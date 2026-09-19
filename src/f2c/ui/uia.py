"""Thin, testable wrapper over the `uiautomation` client.

Why a wrapper and not raw `uiautomation`:

* SWT/Eclipse controls expose almost no `AutomationId` and frequently an empty
  `Name`. The value of a `Text` widget is often only reachable through
  `LegacyIAccessiblePattern`, not `ValuePattern`. `text_of()` below tries every
  route in order.
* Everything downstream (resolver, grid, flow) works against `Element`, which
  is a plain object with a `rect`. That makes the geometry logic unit-testable
  without a live application - see tests/test_geometry.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator, List, Optional

from ..logging_setup import get

log = get("ui.uia")

#: Pause between moving the cursor onto a control and pressing the button, and
#: after the press. SWT image "buttons" swallow a click that arrives in the same
#: instant as the cursor.
CLICK_HOVER_SETTLE = 0.12
CLICK_SETTLE = 0.12

#: A control at or left/above this coordinate is *parked*, not displayed.
#:
#: Eclipse/SWT does not destroy the widgets of an editor that is no longer the
#: active tab - it moves the whole composite to around (-32000, -32000) and
#: leaves it fully present in the accessibility tree. Every one of those
#: controls still answers, still accepts SendKeys, and still reports a value,
#: so an automation that does not check where a control IS will drive the wrong
#: editor and see nothing happen. That is exactly how "VAT 19% could not be
#: selected in the Product editor" was produced: the combo being driven was at
#: x=-31464, in a Product editor that had been parked behind the Order.
PARKED_COORDINATE = -10000

#: Sentinel appended by the combo walk when it lands on the wanted entry.
_FOUND = "<found>"

_uia = None


def uia():
    """Lazy import so the package can be used (and tested) without Windows deps."""
    global _uia
    if _uia is None:
        import uiautomation as _m  # type: ignore

        _m.SetGlobalSearchTimeout(0)  # we do our own condition-based waiting
        _uia = _m
    return _uia


def normalise_control_type(raw: str) -> str:
    """"ButtonControl" -> "Button"; "TabItemControl" -> "TabItem".

    `uiautomation` exposes names with a trailing "Control". Selector files use
    the short form, which is also what inspect.exe displays.
    """
    name = (raw or "").strip()
    if name.endswith("ControlType"):
        name = name[: -len("ControlType")]
    if name.endswith("Control"):
        name = name[: -len("Control")]
    return name


# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self):
        return (self.left + self.width // 2, self.top + self.height // 2)

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def is_parked(self) -> bool:
        """Is this rectangle off in SWT's parking lot rather than on screen?

        A maximized Eclipse shell legitimately reports a slightly negative
        origin (-9, -9), so the threshold is deliberately far out - see
        PARKED_COORDINATE.
        """
        return self.left <= PARKED_COORDINATE or self.top <= PARKED_COORDINATE

    def is_onscreen(self) -> bool:
        return not self.is_empty() and not self.is_parked()

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom

    def contains(self, other: "Rect") -> bool:
        return (
            self.left <= other.left
            and self.top <= other.top
            and self.right >= other.right
            and self.bottom >= other.bottom
        )

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right < other.left
            or other.right < self.left
            or self.bottom < other.top
            or other.bottom < self.top
        )

    def vertical_overlap(self, other: "Rect") -> int:
        return max(0, min(self.bottom, other.bottom) - max(self.top, other.top))

    def offset(self, dx: int, dy: int) -> "Rect":
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def inset(self, n: int) -> "Rect":
        return Rect(self.left + n, self.top + n, self.right - n, self.bottom - n)

    def as_tuple(self):
        return (self.left, self.top, self.right, self.bottom)


EMPTY_RECT = Rect(0, 0, 0, 0)


# --------------------------------------------------------------------------- #
# element
# --------------------------------------------------------------------------- #
class Element:
    """A resolved UIA control."""

    def __init__(self, control: Any, logical_name: str = "", tier: str = "uia"):
        self._c = control
        self.logical_name = logical_name
        self.tier = tier

    # --- identity ---------------------------------------------------------
    @property
    def raw(self) -> Any:
        return self._c

    @property
    def name(self) -> str:
        try:
            return self._c.Name or ""
        except Exception:
            return ""

    @property
    def control_type(self) -> str:
        """Short name: `uiautomation` reports "ButtonControl", we use "Button"."""
        try:
            return normalise_control_type(self._c.ControlTypeName)
        except Exception:
            return ""

    @property
    def class_name(self) -> str:
        try:
            return self._c.ClassName or ""
        except Exception:
            return ""

    @property
    def automation_id(self) -> str:
        try:
            return self._c.AutomationId or ""
        except Exception:
            return ""

    @property
    def rect(self) -> Rect:
        try:
            r = self._c.BoundingRectangle
            return Rect(int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            return EMPTY_RECT

    @property
    def enabled(self) -> bool:
        try:
            return bool(self._c.IsEnabled)
        except Exception:
            return False

    @property
    def offscreen(self) -> bool:
        try:
            return bool(self._c.IsOffscreen)
        except Exception:
            return True

    @property
    def visible(self) -> bool:
        return not self.offscreen and not self.rect.is_empty()

    @property
    def on_screen(self) -> bool:
        """Is this control actually displayed, rather than parked by SWT?

        `IsOffscreen` is not enough on its own: SWT reports it as False for the
        widgets of a parked editor, which is why the geometry has to be checked
        as well.
        """
        return self.rect.is_onscreen()

    def exists(self) -> bool:
        try:
            return bool(self._c.Exists(0, 0))
        except Exception:
            return False

    def __repr__(self) -> str:
        return "<%s %r cls=%r rect=%s tier=%s>" % (
            self.control_type,
            self.name[:40],
            self.class_name[:24],
            self.rect.as_tuple(),
            self.tier,
        )

    # --- tree -------------------------------------------------------------
    def children(self) -> List["Element"]:
        try:
            return [Element(c) for c in self._c.GetChildren()]
        except Exception:
            return []

    def parent(self) -> Optional["Element"]:
        try:
            p = self._c.GetParentControl()
            return Element(p) if p else None
        except Exception:
            return None

    def descendants(self, max_depth: int = 14) -> Iterator["Element"]:
        """Depth-first walk. `max_depth` guards against pathological trees."""
        stack = [(self, 0)]
        while stack:
            node, depth = stack.pop()
            if depth >= max_depth:
                continue
            for child in node.children():
                yield child
                stack.append((child, depth + 1))

    def find(
        self,
        predicate: Callable[["Element"], bool],
        max_depth: int = 14,
    ) -> List["Element"]:
        return [e for e in self.descendants(max_depth) if predicate(e)]

    # --- text -------------------------------------------------------------
    def text(self) -> str:
        """Read a control's value, trying every route SWT might expose."""
        # 1. ValuePattern  (most native Win32 Edit / Combo controls)
        try:
            vp = self._c.GetValuePattern()
            if vp is not None:
                v = vp.Value
                if v:
                    return v
        except Exception:
            pass
        # 2. LegacyIAccessible  (SWT's usual route)
        try:
            lp = self._c.GetLegacyIAccessiblePattern()
            if lp is not None:
                v = lp.Value
                if v:
                    return v
        except Exception:
            pass
        # 3. TextPattern (rich text / styled controls)
        try:
            tp = self._c.GetTextPattern()
            if tp is not None:
                v = tp.DocumentRange.GetText(4096)
                if v:
                    return v
        except Exception:
            pass
        # 4. Name  (labels, buttons, list items)
        return self.name

    def value_text(self) -> str:
        """The control's VALUE, or "" - never its label.

        `text()` ends with `self.name`, which is right for a label or a button
        and quietly wrong for an empty input: SWT names its Text widgets after
        their caption, so a blank "Account" box reports the string "Account",
        an untouched "Item Number" box reports "Item Number", and a write that
        did land looks like a write that did not.

        That mattered twice. Verification retried perfectly good writes through
        the programmatic path - the one that updates the display without telling
        the application - and the Account field, which the spec says to leave
        blank, was repeatedly "cleared" although it was empty all along.
        """
        for getter in (
            lambda: self._c.GetValuePattern().Value,
            lambda: self._c.GetLegacyIAccessiblePattern().Value,
            lambda: self._c.GetTextPattern().DocumentRange.GetText(4096),
        ):
            try:
                value = getter()
            except Exception:
                continue
            if value:
                return value
        return ""

    def set_text(self, value: str, verify: bool = True, method: str = "keys") -> None:
        """Write a value and read it back.

        `method`:
          ``keys``  keystrokes, finished with Tab. **The default.**
          ``value`` ValuePattern.SetValue, for controls that honour it.

        Keystrokes are the default because of what `ValuePattern.SetValue` does
        to SWT: it updates the widget's displayed text, and the readback agrees,
        but it never fires the modify listener, so the application model is not
        told. Two ways that showed up here, both silent:

        * the date field reverted to today the moment focus moved away;
        * the Company, Alias and Country fields of a new Debtor read back
          correctly, saved without complaint, and landed in the database as
          NULL.

        A record that is wrong in the database but right on screen is the worst
        failure mode this automation can have, so the slower path that sends
        real key events is the one taken by default.
        """
        value = "" if value is None else str(value)
        written = False

        if method == "value":
            try:
                pattern = self._c.GetValuePattern()
                if pattern is not None and not pattern.IsReadOnly:
                    pattern.SetValue(value)
                    written = True
            except Exception:
                pass

        if not written:
            self.focus()
            self._c.SendKeys("{Ctrl}a", waitTime=0.04)
            self._c.SendKeys("{Delete}", waitTime=0.04)
            if value:
                # curly braces are SendKeys metacharacters
                self._c.SendKeys(_escape_sendkeys(value), waitTime=0.04)
            # No trailing Tab. Real key events already fire SWT's ModifyListener
            # as the text changes, so no commit key is needed - and a multi-line
            # Text swallows Tab as *content*: the Company field was saved as
            # "Northstar Office GmbH	", which then matched nothing.
            # (The segmented date widget is the exception and sends its own Tab;
            # see flow/context.py set_date.)

        if verify:
            got = self.value_text().strip()
            if got.replace(" ", " ") != value.strip():
                log.debug(
                    "set_text readback mismatch on %r: wrote %r read %r",
                    self.logical_name or self.name,
                    value,
                    got,
                )

    # --- interaction ------------------------------------------------------
    def click(self, dx_ratio: float = 0.5, dy_ratio: float = 0.5) -> None:
        """Move, settle, then click.

        The move-then-pause matters for SWT's image "buttons": clicking in the
        same instant the cursor arrives is frequently swallowed, and the only
        visible effect is the tooltip appearing. Moving first, letting the
        widget register the hover, and only then pressing gives a reliable
        activation.
        """
        r = self.rect
        if r.is_empty():
            raise RuntimeError("cannot click %r: empty rectangle" % self)
        x = r.left + int(r.width * dx_ratio)
        y = r.top + int(r.height * dy_ratio)
        module = uia()
        module.MoveTo(x, y, waitTime=CLICK_HOVER_SETTLE)
        module.Click(x, y, waitTime=CLICK_SETTLE)

    def double_click(self) -> None:
        x, y = self.rect.center
        uia().DoubleClick(x, y, waitTime=0.05)

    def invoke(self) -> None:
        """Activate the control.

        Order matters. `DoDefaultAction` on a control that has no default action
        - which is the case for the SWT `Static` images Fakturama uses as its
        record-selector icons - returns successfully without doing anything, so
        it is only used when the element actually advertises an action. Anything
        else falls through to a real click on the resolved rectangle.
        """
        try:
            pattern = self._c.GetInvokePattern()
            if pattern is not None:
                pattern.Invoke()
                return
        except Exception:
            pass

        try:
            pattern = self._c.GetLegacyIAccessiblePattern()
            if pattern is not None and (pattern.DefaultAction or "").strip():
                pattern.DoDefaultAction()
                return
        except Exception:
            pass

        self.click()

    def focus(self) -> None:
        try:
            self._c.SetFocus()
        except Exception:
            self.click()

    def send_keys(self, keys: str, wait: float = 0.05) -> None:
        self._c.SendKeys(keys, waitTime=wait)

    def top_level(self) -> "Element":
        """Walk up to the owning top-level window."""
        node = self
        for _ in range(24):
            if node.control_type == "Window":
                return node
            parent = node.parent()
            if parent is None:
                break
            node = parent
        return node

    def select_item(self, value: str) -> bool:
        """Choose `value` in a combo, by really selecting it.

        Only two mechanisms are used, and both genuinely move the widget's
        selected index:

        1. `SelectionItemPattern.Select()` on a child that is already exposed
           as a selectable item;
        2. walking the list with Home/Down, which is what a keyboard user does
           and which fires the widget's own selection-changed event.

        Everything else was tried and removed, because all of it produced the
        same failure: the control DISPLAYS the requested value, reading it back
        agrees, and the application saves the previous selection anyway.

        * `ValuePattern.SetValue` - writes the text, no selection.
        * Type-ahead into an editable combo - writes the text, no selection.
        * Clicking the entry in the drop-down popup - looked the most convincing
          of the three. The Product editor showed "VAT 19%" right up to the
          moment of saving, and the product was still stored against "Tax-free".
          Only reading the database afterwards exposed it.
        """
        import time as _time

        target = value.strip().casefold()

        def settled_text() -> str:
            """What the control shows, once it has stopped changing.

            Reading immediately after a keystroke races the widget: the walk
            below asked what was displayed and got the PREVIOUS entry back, so
            it stepped straight past the entry it wanted and ran to the end of
            the list. The list genuinely contained "Credit transfer" as its
            second entry; the automation reported that the dropdown did not
            offer it.
            """
            previous = None
            for _ in range(8):
                current = self.text().strip()
                if current == previous:
                    return current
                previous = current
                _time.sleep(0.05)
            return previous or ""

        def matches(shown: str) -> bool:
            shown = shown.casefold()
            return bool(shown) and (
                shown == target or shown.startswith(target) or target in shown
            )

        def shows_target() -> bool:
            return matches(settled_text())

        # 1. an item the control already exposes as selectable
        for child in self.children():
            name = (child.name or "").strip().casefold()
            if name and (name == target or name.startswith(target)):
                try:
                    child.raw.GetSelectionItemPattern().Select()
                    if shows_target():
                        return True
                except Exception:
                    pass

        # 2. walk the list with the keyboard
        def walk() -> List[str]:
            """Step through the entries, returning the ones actually seen."""
            visited: List[str] = []
            self._c.SendKeys("{Esc}", waitTime=0.12)   # close any open drop-down
            self._c.SendKeys("{Home}", waitTime=0.3)
            previous = None
            unchanged = 0
            for _ in range(100):
                shown = settled_text()
                if shown not in visited:
                    visited.append(shown)
                if matches(shown):
                    _time.sleep(0.15)
                    visited.append(_FOUND)
                    return visited
                folded = shown.casefold()
                if folded == previous:
                    # the value may simply not have refreshed yet, so give it a
                    # couple of chances before concluding we are at the end
                    unchanged += 1
                    if unchanged >= 3:
                        break
                else:
                    unchanged = 0
                previous = folded
                self._c.SendKeys("{Down}", waitTime=0.12)
            return visited

        seen: List[str] = []
        try:
            self.focus()
            _time.sleep(0.25)                  # let the control take focus
            seen = walk()
            if _FOUND in seen:
                return True

            # A walk that only ever saw ONE value did not move the control at
            # all, which means the keys never reached it: SetFocus reports
            # success on these SWT combos without actually giving them the
            # keyboard. A real click does. (The click opens the drop-down; the
            # Esc at the top of the walk closes it again and the focus stays.)
            if len(seen) <= 1:
                log.debug("%r did not respond to the keyboard; clicking it first", self)
                self.click()
                _time.sleep(0.35)
                seen = walk()
                if _FOUND in seen:
                    return True
        except Exception as exc:
            log.debug("keyboard walk failed on %r: %s", self, exc)
        seen = [v for v in seen if v != _FOUND]

        # Say what the control DID offer. "not offered by the dropdown" without
        # the list is a dead end for whoever reads the report: the entry was in
        # fact there every time this message has been produced.
        log.warning(
            "could not genuinely select %r in %r; the walk saw: %s",
            value, self, ", ".join(repr(v) for v in seen[:20]) or "<nothing>",
        )
        return False

    def toggle_on(self) -> None:
        try:
            tp = self._c.GetTogglePattern()
            if tp is not None and tp.ToggleState != 1:
                tp.Toggle()
                return
        except Exception:
            pass
        if not self.is_checked():
            self.click()

    def toggle_off(self) -> None:
        try:
            tp = self._c.GetTogglePattern()
            if tp is not None and tp.ToggleState == 1:
                tp.Toggle()
                return
        except Exception:
            pass
        if self.is_checked():
            self.click()

    def is_checked(self) -> bool:
        try:
            tp = self._c.GetTogglePattern()
            if tp is not None:
                return tp.ToggleState == 1
        except Exception:
            pass
        try:
            sp = self._c.GetSelectionItemPattern()
            if sp is not None:
                return bool(sp.IsSelected)
        except Exception:
            pass
        return False

    def is_selected(self) -> bool:
        try:
            return bool(self._c.GetSelectionItemPattern().IsSelected)
        except Exception:
            return False

    def select(self) -> None:
        try:
            self._c.GetSelectionItemPattern().Select()
        except Exception:
            self.click()


def _escape_sendkeys(text: str) -> str:
    out = []
    for ch in text:
        if ch in "{}":
            out.append("{%s}" % ch)
        else:
            out.append(ch)
    return "".join(out)


def _escape(text: str) -> str:  # public alias used by flow modules
    return _escape_sendkeys(text)


# --------------------------------------------------------------------------- #
# roots
# --------------------------------------------------------------------------- #
def desktop() -> Element:
    return Element(uia().GetRootControl(), "desktop")


def windows_of_process(pid: int) -> List[Element]:
    out = []
    for w in desktop().children():
        try:
            if w.raw.ProcessId == pid:
                out.append(w)
        except Exception:
            continue
    return out


def top_windows(name_contains: str = "") -> List[Element]:
    res = []
    for w in desktop().children():
        if not name_contains or name_contains.casefold() in w.name.casefold():
            res.append(w)
    return res


def dump_tree(el: Element, max_depth: int = 8, _depth: int = 0) -> str:
    """Human-readable subtree dump - written next to every failure screenshot."""
    pad = "  " * _depth
    line = "%s%s | name=%r | class=%r | id=%r | rect=%s" % (
        pad,
        el.control_type,
        el.name[:60],
        el.class_name[:30],
        el.automation_id[:24],
        el.rect.as_tuple(),
    )
    if _depth >= max_depth:
        return line
    parts = [line]
    for child in el.children():
        parts.append(dump_tree(child, max_depth, _depth + 1))
    return "\n".join(parts)
