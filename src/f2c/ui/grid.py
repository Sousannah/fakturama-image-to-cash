"""Reading and writing tabular controls.

Two very different situations live here.

`read_uia_rows` handles the *dialog* lists ("Select the address", "Select a
product", Data > VATs, Data > Documents). Those are SWT `Table` widgets backed
by native Win32 list-views, so UIA sees real rows and cells and we can read them
semantically.

`CanvasGrid` handles the *item grid* inside the document editor. In current
Fakturama builds that is a custom-drawn composite (NatTable) - a single
`Canvas` with no accessibility children at all. UIA can tell us where it is but
nothing about what is in it. So we reconstruct the table geometrically from the
control's own rendered rulings (preferred) or from clustered OCR text
(fallback), then address cells by (row, column) in coordinates derived at
runtime from the canvas rectangle.

`ItemGridWriter` prefers keyboard navigation over clicking, because moving with
Tab/arrow keys inside a focused grid does not depend on the geometry being
reconstructed correctly at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from ..logging_setup import get
from . import vision_locator as vl
from .uia import Element, Rect

log = get("ui.grid")


# --------------------------------------------------------------------------- #
# UIA-visible tables (dialog lists)
# --------------------------------------------------------------------------- #
def read_uia_rows(table: Element, max_rows: int = 400) -> List[List[str]]:
    """Return the table's rows as lists of cell strings."""
    rows: List[List[str]] = []
    for child in table.children():
        ct = child.control_type
        if ct not in ("DataItem", "ListItem", "TreeItem", "Custom"):
            continue
        cells = [c.text().strip() for c in child.children() if c.control_type in ("Text", "DataItem", "Edit", "Custom")]
        if not cells:
            cells = [child.text().strip()]
        rows.append([c for c in cells])
        if len(rows) >= max_rows:
            break
    return rows


def read_uia_row_elements(table: Element, max_rows: int = 400) -> List[Element]:
    out = []
    for child in table.children():
        if child.control_type in ("DataItem", "ListItem", "TreeItem", "Custom"):
            out.append(child)
            if len(out) >= max_rows:
                break
    return out


def row_signature(table: Element) -> str:
    """Cheap snapshot used by `wait_stable` to detect a settled result list.

    For a UIA-visible table this is the row text. For a custom-drawn canvas -
    which is what every list in Fakturama 2.2.0 turns out to be - it is a coarse
    hash of the rendered pixels. Hashing pixels rather than OCR-ing on every
    poll matters: OCR takes seconds, and `wait_stable` polls four times a
    second.
    """
    try:
        rows = read_uia_rows(table, max_rows=60)
        if rows:
            return "|".join(" ".join(r) for r in rows)
    except Exception:
        pass
    return region_signature(table)


def region_signature(element: Element, buckets: int = 32) -> str:
    """A coarse perceptual hash of an element's rendered pixels."""
    rect = element.rect
    if rect.is_empty():
        return "?"
    try:
        image = vl.grab(rect).convert("L").resize((buckets, buckets))
        data = image.tobytes()
    except Exception:
        return "?"
    import hashlib

    return hashlib.sha1(data).hexdigest()


def read_rows(table: Element, expected_headers: Sequence[str] = ()) -> List[List[str]]:
    """Rows of a table, whatever kind of control it turns out to be.

    Tries UIA first. When the control is a canvas with no accessibility
    children, reconstructs the table from its own rendered rulings and OCRs the
    cells (`CanvasGrid`). Callers get the same shape either way.
    """
    rows = read_uia_rows(table)
    if rows:
        return rows

    rect = table.rect
    if rect.is_empty():
        return []

    grid = CanvasGrid.build(rect, expected_headers)
    if not grid.is_usable():
        log.debug("canvas at %s could not be reconstructed", rect.as_tuple())
        return []

    out: List[List[str]] = []
    for index in range(len(grid.rows)):
        cells = grid.row_text(index)
        if any(c.strip() for c in cells):
            out.append(cells)
    log.debug("read %d row(s) from canvas %s", len(out), rect.as_tuple())
    return out


def click_canvas_row(table: Element, row_index: int, expected_headers: Sequence[str] = ()) -> bool:
    """Select a row of a custom-drawn list by clicking inside it."""
    grid = CanvasGrid.build(table.rect, expected_headers)
    if not grid.is_usable():
        return False

    populated = [i for i in range(len(grid.rows)) if any(c.strip() for c in grid.row_text(i))]
    if row_index >= len(populated):
        return False

    band = grid.rows[populated[row_index]]
    from .uia import uia

    uia().Click(band.left + min(60, band.width // 4), band.center[1], waitTime=0.1)
    return True


# --------------------------------------------------------------------------- #
# custom-drawn grid
# --------------------------------------------------------------------------- #
@dataclass
class CanvasGrid:
    """A table reconstructed from pixels inside a UIA-resolved rectangle."""

    region: Rect
    columns: List[Rect] = field(default_factory=list)   # column bands (full height)
    rows: List[Rect] = field(default_factory=list)      # row bands (full width)
    headers: List[str] = field(default_factory=list)
    cells: Dict[str, str] = field(default_factory=dict)  # "r,c" -> text

    # --- construction --------------------------------------------------- #
    @classmethod
    def build(cls, region: Rect, expected_headers: Sequence[str] = ()) -> "CanvasGrid":
        grid = cls(region=region, expected_headers=list(expected_headers))
        try:
            col_x, row_y = vl.detect_grid_lines(region)
        except Exception as exc:
            log.debug("grid-line detection unavailable (%s)", exc)
            col_x, row_y = [], []

        if len(col_x) >= 2 and len(row_y) >= 2:
            grid.columns = [
                Rect(col_x[i], region.top, col_x[i + 1], region.bottom)
                for i in range(len(col_x) - 1)
            ]
            grid.rows = [
                Rect(region.left, row_y[i], region.right, row_y[i + 1])
                for i in range(len(row_y) - 1)
            ]
            log.debug("grid from rulings: %d cols x %d rows", len(grid.columns), len(grid.rows))
        else:
            grid._build_from_text(expected_headers)

        grid._read_cells(expected_headers)
        return grid

    def _build_from_text(self, expected_headers: Sequence[str]) -> None:
        """Fallback: derive columns from the header words, rows from text lines."""
        words = vl.ocr_words(self.region)
        if not words:
            return
        lines = vl.group_lines(words)
        if not lines:
            return

        header_line = lines[0]
        if expected_headers:
            wanted = {h.casefold() for h in expected_headers}
            for line in lines:
                joined = {w.text.strip().casefold().rstrip(".") for w in line}
                if len(joined & wanted) >= max(2, len(wanted) // 3):
                    header_line = line
                    break

        boundaries = [self.region.left]
        for a, b in zip(header_line, header_line[1:]):
            boundaries.append((a.rect.right + b.rect.left) // 2)
        boundaries.append(self.region.right)
        self.columns = [
            Rect(boundaries[i], self.region.top, boundaries[i + 1], self.region.bottom)
            for i in range(len(boundaries) - 1)
        ]

        header_bottom = max(w.rect.bottom for w in header_line)
        body = [ln for ln in lines if min(w.rect.top for w in ln) > header_bottom]
        self.rows = [
            Rect(
                self.region.left,
                min(w.rect.top for w in ln) - 2,
                self.region.right,
                max(w.rect.bottom for w in ln) + 2,
            )
            for ln in body
        ]
        self.headers = [w.text.strip() for w in header_line]
        log.debug("grid from OCR: %d cols x %d rows", len(self.columns), len(self.rows))

    def _read_cells(self, expected_headers: Sequence[str]) -> None:
        if not self.columns or not self.rows:
            return
        words = vl.ocr_words(self.region)
        for w in words:
            r = self.row_index_at(w.rect.center[1])
            c = self.column_index_at(w.rect.center[0])
            if r is None or c is None:
                continue
            key = "%d,%d" % (r, c)
            self.cells[key] = (self.cells.get(key, "") + " " + w.text).strip()
        if not self.headers and expected_headers:
            self.headers = list(expected_headers)

    # --- addressing ------------------------------------------------------ #
    def row_index_at(self, y: int) -> Optional[int]:
        for i, r in enumerate(self.rows):
            if r.top <= y <= r.bottom:
                return i
        return None

    def column_index_at(self, x: int) -> Optional[int]:
        for i, c in enumerate(self.columns):
            if c.left <= x <= c.right:
                return i
        return None

    #: The column order the application renders, supplied by the caller. When the
    #: number of detected column bands equals the number of expected headers, the
    #: mapping is positional and OCR of the header row is not consulted at all.
    expected_headers: List[str] = field(default_factory=list)

    def column_index(self, header: str) -> Optional[int]:
        """Index of a named column.

        Positional first. OCR mangles the header row badly - "Qty." is read as
        "Oty:" and "U.Price" as "UPrice" - so matching on the recognised text
        silently failed to find columns that were plainly there. The ruling-line
        detection, on the other hand, gives the column COUNT reliably, and the
        application's column order is fixed and known. When those agree, the
        mapping needs no text at all.
        """
        target = header.strip().casefold().rstrip(".:")

        if self.expected_headers and len(self.columns) == len(self.expected_headers):
            for i, h in enumerate(self.expected_headers):
                if h.strip().casefold().rstrip(".:") == target:
                    return i

        for i, h in enumerate(self.headers):
            if h.strip().casefold().rstrip(".:").startswith(target):
                return i
        return None

    def data_row_rect(self, index: int) -> Optional[Rect]:
        """Rectangle of the index-th DATA row (0 = the first item line).

        The detected bands are not all table rows: the grid control extends
        below the last ruling, so the final band picked up the totals area. The
        rows of this table are a uniform height, so the first band plus a
        multiple of its height is both simpler and more reliable than trusting
        every detected band.
        """
        if not self.rows:
            return None
        first = self.rows[0]
        height = first.height
        if height <= 0:
            return None
        top = first.top + index * height
        bottom = top + height
        if bottom > self.region.bottom:
            return None
        return Rect(self.region.left, top, self.region.right, bottom)

    def data_cell_rect(self, row: int, header: str) -> Optional[Rect]:
        col = self.column_index(header)
        band = self.data_row_rect(row)
        if col is None or band is None:
            return None
        c = self.columns[col]
        return Rect(c.left, band.top, c.right, band.bottom)

    def cell_rect(self, row: int, col: int) -> Rect:
        if not (0 <= row < len(self.rows)):
            raise IndexError("row %d out of range (%d rows)" % (row, len(self.rows)))
        if not (0 <= col < len(self.columns)):
            raise IndexError("column %d out of range (%d columns)" % (col, len(self.columns)))
        r, c = self.rows[row], self.columns[col]
        return Rect(c.left, r.top, c.right, r.bottom)

    def cell_text(self, row: int, col: int) -> str:
        return self.cells.get("%d,%d" % (row, col), "")

    def row_text(self, row: int) -> List[str]:
        return [self.cell_text(row, c) for c in range(len(self.columns))]

    @property
    def shape(self):
        return (len(self.rows), len(self.columns))

    def is_usable(self) -> bool:
        return len(self.rows) > 0 and len(self.columns) > 1


#: Column headings of Data > Documents, used to reconstruct that canvas list.
DOCUMENT_HEADERS = (
    "Icon",
    "Document",
    "Date",
    "Name",
    "Cust.Ref.",
    "State",
    "Total",
    "Printed",
)

#: Header labels of Fakturama 2.2.0's document item table, in the order the
#: application renders them. Read off the live editor, not guessed: the SKU
#: column is "Item No." (not "Item Number"), there is a "Picture" column, and
#: VAT comes BEFORE U.Price.
ITEM_HEADERS = (
    "Pos.",
    "Qty.",
    "Item No.",
    "Picture",
    "Name",
    "Description",
    "VAT",
    "U.Price",
    "Discount",
    "Price",
)


def measure_item_grid(region: Rect, expected_headers: Sequence[str]) -> Optional[CanvasGrid]:
    """Measure the document item table from its own drawn rulings.

    Purpose-built rather than reusing the generic canvas reconstruction, because
    this table has a shape that can be exploited:

    * its column rulings are evenly spaced, so the boundaries can be sanity
      checked against each other instead of trusted blindly;
    * the trailing filler area to the right of the last column is much wider
      than a real column, so it can be recognised and dropped;
    * its rows are a uniform height, so only the header bottom and the row pitch
      are needed - and the pitch survives a row being selected, which the
      rulings themselves do not.

    Header TEXT is never consulted. OCR reads "Qty." as "Oty:" and "U.Price" as
    "UPrice", so the column order is taken from `expected_headers` positionally
    once the boundary count agrees.
    """
    xs, ys = vl.rulings(region, min_ratio=0.35)
    if len(xs) < 2 or not ys:
        log.debug("item grid rulings not found (x=%d, y=%d)", len(xs), len(ys))
        return None

    xs = sorted(set(xs))
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    pitch = sorted(gaps)[len(gaps) // 2] if gaps else 0
    if pitch <= 0:
        return None

    # boundaries -> bands, including the narrow first column left of the first ruling
    edges = [region.left] + xs
    bands = [Rect(a, region.top, b, region.bottom) for a, b in zip(edges, edges[1:])]
    trailing = Rect(xs[-1], region.top, region.right, region.bottom)
    if trailing.width <= pitch * 1.4:
        bands.append(trailing)   # a genuine last column, not filler

    if len(bands) != len(expected_headers):
        log.debug(
            "item grid: %d column bands but %d expected headers",
            len(bands), len(expected_headers),
        )
        return None

    ys = sorted(set(ys))
    header_bottom = ys[0]
    row_gaps = [b - a for a, b in zip(ys, ys[1:]) if (b - a) >= 12]
    row_height = min(row_gaps) if row_gaps else 0
    if row_height <= 0:
        log.debug("item grid: could not establish a row height from %s", ys)
        return None

    grid = CanvasGrid(region=region, expected_headers=list(expected_headers))
    grid.columns = bands
    grid.headers = list(expected_headers)
    grid.rows = [
        Rect(region.left, header_bottom + i * row_height,
             region.right, header_bottom + (i + 1) * row_height)
        for i in range(max(1, (region.bottom - header_bottom) // row_height))
    ]
    log.debug(
        "item grid measured: %d columns (pitch %dpx), rows of %dpx from y=%d",
        len(grid.columns), pitch, row_height, header_bottom,
    )
    return grid


class ItemGridWriter:
    """Reads and writes the document item grid.

    The important discovery behind this class: although the grid itself is a
    custom-drawn canvas with no accessibility children, **clicking a cell makes
    the application create a real SWT editor control for that cell**, and that
    editor DOES appear in the accessibility tree. So the sequence

        click the cell  ->  a new Edit (or ComboBox) appears  ->  drive it

    turns the grid from an OCR problem back into an ordinary UI Automation one.
    Values are typed into a real control and can be read back from it exactly,
    instead of being guessed from pixels.

    Geometry is still needed to know WHERE to click, and that still comes from
    the grid's own ruling lines inside the rectangle UIA reports - so no fixed
    coordinate is involved.
    """

    #: control types the application uses as in-cell editors
    EDITOR_TYPES = ("Edit", "ComboBox", "Custom")

    def __init__(self, resolver, grid_logical_name: str = "order.items_grid",
                 editor_logical_name: str = "order_editor"):
        self.resolver = resolver
        self.grid_logical_name = grid_logical_name
        self.editor_logical_name = editor_logical_name
        self._grid: Optional[CanvasGrid] = None
        self._grid_el: Optional[Element] = None
        self._scope_el: Optional[Element] = None

    # --- the canvas itself ------------------------------------------------ #
    def grid_element(self) -> Element:
        """The canvas, resolved ONCE and then held.

        Re-resolving it per operation is wrong: the selector picks the largest
        childless pane, and as soon as the application creates an in-cell editor
        that pane is no longer childless, so the name starts resolving to a
        different control and the measured geometry is thrown away mid-line.
        """
        if self._grid_el is None or not self._grid_el.exists():
            self._grid_el = self.resolver.resolve(self.grid_logical_name, timeout=10)
            self._scope_el = None
        return self._grid_el

    def invalidate(self) -> None:
        """Forget the canvas and its container, keeping the measured geometry.

        Called before a retried item step runs again. A canvas element resolved
        while the Order was in front keeps answering after the editor has been
        parked off-screen, and it reports a parked rectangle - so every cell
        click computed from it would land nowhere. The measurement itself is
        kept, because the columns and row height do not change; `ensure_geometry`
        re-measures anyway if the freshly resolved canvas turns out to have
        moved.
        """
        self._grid_el = None
        self._scope_el = None

    # --- geometry --------------------------------------------------------- #
    def _region(self) -> Rect:
        rect = self.grid_element().rect
        if rect.is_empty():
            # the canvas was recreated under us - resolve it again once
            self._grid_el = None
            self._scope_el = None
            rect = self.grid_element().rect
        return rect

    def refresh(self, force: bool = True) -> CanvasGrid:
        """(Re)detect the grid's column and row geometry.

        The geometry is CACHED, and a failed detection never replaces a good
        one. That matters because detection reads the grid's own ruling lines,
        and as soon as a row is selected the application paints it solid blue -
        the rulings disappear under the highlight and detection returns nothing.
        Since the columns and row height do not move while a line is being
        filled in, reusing the last good geometry is both safe and necessary.
        """
        region = self._region()

        if not force and self._grid is not None and self._grid.region == region:
            return self._grid

        fresh = measure_item_grid(region, ITEM_HEADERS)
        if fresh is None:
            fresh = CanvasGrid.build(region, ITEM_HEADERS)

        if fresh.is_usable():
            self._grid = fresh
        elif self._grid is not None and self._grid.is_usable():
            # A failed measurement NEVER replaces a good one. Measurement reads
            # the grid's drawn rulings, and those vanish under a selected row's
            # highlight and while an in-cell editor is open - which is exactly
            # when the flow is busy filling a line in.
            log.debug("grid measurement unavailable right now; keeping the cached geometry")
        else:
            self._grid = fresh
        return self._grid

    def ensure_geometry(self) -> CanvasGrid:
        """Geometry, re-measuring only when we genuinely need to.

        A usable measurement is kept as long as the canvas has not actually
        moved. Small differences are ignored, and a failed re-measurement never
        replaces a good one - see `refresh`.
        """
        if self._grid is not None and self._grid.is_usable():
            region = self._region()
            moved = (
                abs(region.left - self._grid.region.left) > 4
                or abs(region.top - self._grid.region.top) > 4
                or abs(region.width - self._grid.region.width) > 8
                or abs(region.height - self._grid.region.height) > 8
            )
            if not moved:
                return self._grid
            log.debug("item grid moved; re-measuring")
        return self.refresh(force=True)

    @property
    def grid(self) -> CanvasGrid:
        if self._grid is None:
            self.refresh()
        return self._grid

    # --- the cell editor -------------------------------------------------- #
    def _editor_scope(self) -> Element:
        """Where an in-cell editor appears.

        The grid's own parent composite, not the whole document editor: the
        editor control is created as a sibling of the canvas, and scanning the
        entire editor subtree on every cell operation made each read take
        several seconds.
        """
        if self._scope_el is None:
            parent = self.grid_element().parent()
            self._scope_el = parent if parent is not None else self.resolver.resolve(
                self.editor_logical_name, timeout=10
            )
        return self._scope_el

    def _candidate_controls(self) -> List[Element]:
        scope = self._editor_scope()
        return [
            e
            for e in scope.descendants(max_depth=6)
            if e.control_type in self.EDITOR_TYPES and not e.rect.is_empty()
        ]

    @staticmethod
    def _key(el: Element) -> str:
        return "%s@%s" % (el.control_type, el.rect.as_tuple())

    def open_cell_editor(self, row: int, header: str, timeout: float = 6.0) -> Optional[Element]:
        """Click a cell and return the editor control the application creates."""
        from .uia import uia
        from .waits import wait_until

        grid = self.ensure_geometry()
        rect = grid.data_cell_rect(row, header)
        if rect is None:
            log.warning("grid shape is %s; expected %d columns",
                        grid.shape, len(ITEM_HEADERS))
            log.warning("cell (row %d, %r) could not be located in the grid", row, header)
            return None

        before = {self._key(e) for e in self._candidate_controls()}
        x, y = rect.center
        uia().MoveTo(x, y, waitTime=0.12)
        uia().Click(x, y, waitTime=0.25)

        def appeared():
            fresh = [e for e in self._candidate_controls() if self._key(e) not in before]
            # the editor is the one sitting over the cell we clicked
            over = [e for e in fresh if e.rect.intersects(rect)]
            return (over or fresh or [None])[0]

        try:
            editor = wait_until(appeared, timeout=timeout, interval=0.2,
                                what="the in-cell editor for %r" % header)
        except Exception:
            log.warning("no in-cell editor appeared for (row %d, %r)", row, header)
            return None
        log.debug("cell editor for %r: %r", header, editor)
        return editor

    def close_editor(self, commit: bool = True) -> None:
        from .uia import uia

        uia().SendKeys("{Enter}" if commit else "{Esc}", waitTime=0.35)

    # --- reading ---------------------------------------------------------- #
    def read_cell(self, row: int, header: str) -> Optional[str]:
        """Exact value of a cell, read from its own editor rather than by OCR."""
        editor = self.open_cell_editor(row, header, timeout=3.0)
        if editor is None:
            # Computed columns (the line Price) are not editable, so no editor is
            # ever created for them. Those are read from the pixels of the exact
            # cell rectangle instead - a small, well-bounded crop.
            return self.ocr_cell(row, header)
        value = editor.text().strip()
        self.close_editor(commit=False)
        return value

    def ocr_cell(self, row: int, header: str) -> Optional[str]:
        """Read a cell that has no editor, by OCR of just that cell."""
        grid = self.ensure_geometry()
        rect = grid.data_cell_rect(row, header)
        if rect is None:
            log.warning("grid shape is %s; expected %d columns",
                        grid.shape, len(ITEM_HEADERS))
            return None
        words = vl.ocr_words(rect.inset(1), upscale=3)
        if not words:
            return None
        text = " ".join(w.text for w in sorted(words, key=lambda w: w.rect.left)).strip()
        log.debug("OCR of read-only cell %r: %r", header, text)
        return text or None

    # --- writing ---------------------------------------------------------- #
    def set_cell(
        self,
        row: int,
        header: str,
        value: str,
        verify: bool = True,
        compare: str = "number",
    ) -> bool:
        """Type a value into one cell and confirm it took."""
        for attempt in range(1, 3):
            if self._write_once(row, header, value):
                if not verify:
                    return True
                if self._confirm(row, header, value, compare):
                    return True
            log.debug("cell %r write attempt %d did not confirm", header, attempt)
        return False

    def _write_once(self, row: int, header: str, value: str) -> bool:
        editor = self.open_cell_editor(row, header)
        if editor is None:
            return False

        if editor.control_type == "ComboBox":
            ok = editor.select_item(str(value))
            self.close_editor(commit=True)
            if not ok:
                log.warning("could not select %r in the %r cell", value, header)
            return ok

        editor.raw.SendKeys("{Ctrl}a", waitTime=0.08)
        editor.raw.SendKeys("{Delete}", waitTime=0.08)
        editor.raw.SendKeys(str(value), waitTime=0.12)
        self.close_editor(commit=True)
        return True

    def _confirm(self, row: int, header: str, value: str, compare: str) -> bool:

        """Read a cell back, allowing for the editor still closing."""
        # Immediately after the commit the editor is still closing; a read that
        # lands in that instant falls through to OCR and returns noise, so the
        # cell gets more than one chance to answer.
        for _ in range(3):
            readback = self.read_cell(row, header)
            if _cell_agrees(readback, value, compare):
                log.debug("cell %r = %r confirmed", header, readback)
                return True
        log.warning("cell %r reads %r after writing %r", header, readback, value)
        return False

    def select_in_cell(self, row: int, header: str, value: str) -> bool:
        """Choose a value in a cell that edits through a dropdown (e.g. VAT).

        The VAT cell edits through a combo, but what UIA reports for it is the
        combo's inner `Edit`, not the combo itself - so the selection is tried
        on the parent first, and falls back to typing the value into the text
        part, which is how a CCombo accepts a choice by name.
        """
        editor = self.open_cell_editor(row, header)
        if editor is None:
            return False

        if editor.control_type == "ComboBox" and editor.select_item(str(value)):
            self.close_editor(commit=True)
            return True

        parent = editor.parent()
        if parent is not None and parent.control_type == "ComboBox":
            if parent.select_item(str(value)):
                self.close_editor(commit=True)
                return True

        editor.raw.SendKeys("{Ctrl}a", waitTime=0.08)
        editor.raw.SendKeys("{Delete}", waitTime=0.08)
        editor.raw.SendKeys(str(value), waitTime=0.15)
        self.close_editor(commit=True)
        return True

    def line_is_present(self, row: int) -> bool:
        """Is there a real item on this line? Checks the Item No. cell."""
        value = self.read_cell(row, "Item No.")
        return bool(value and value.strip())


def _cell_agrees(actual: Optional[str], written: str, compare: str) -> bool:
    """Does what the cell shows represent what we wrote?

    Cells re-render what they were given, so a literal comparison is wrong:
    "250.00" comes back as "USD 250", and a 10% discount comes back as
    "-10.00 %" because the application stores a discount as a negative number.

      ``number``     compare numerically
      ``magnitude``  compare numerically, ignoring the sign (discounts)
      ``text``       compare as text
    """
    if actual is None:
        return False
    if compare == "text":
        return _loose_equal(actual, written)

    from ..flow.formats import parse_amount

    got, want = parse_amount(actual), parse_amount(written)
    if got is None or want is None:
        return _loose_equal(actual, written)
    if compare == "magnitude":
        got, want = abs(got), abs(want)
    return abs(got - want) <= Decimal("0.01")


def _loose_equal(a: str, b: str) -> bool:
    """OCR-tolerant comparison: ignore spaces, currency, and comma/dot."""
    def norm(s: str) -> str:
        s = str(s).strip().casefold()
        for junk in (" ", " ", "eur", "%", "€"):
            s = s.replace(junk, "")
        return s.replace(",", ".")

    return norm(a) == norm(b)
