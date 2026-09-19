"""The control-discovery engine: four grounding tiers, declared in YAML.

A logical control (say ``order.custref_field``) maps to an *ordered* list of
strategies. The resolver tries each in turn and returns the first hit, logging
which tier won. Tier statistics are written into the run artifacts, which is the
quickest way to see how brittle a given screen actually is.

Tiers
-----
1. ``uia``      semantic: ControlType + Name / ClassName, scoped to an ancestor.
2. ``anchor``   geometric: the Edit immediately right of the label "Cust.Ref.".
                This carries most of Fakturama's form, because SWT Text widgets
                have neither an AutomationId nor a Name.
3. ``template`` an icon image, matched *inside a UIA-resolved rectangle*.
                Disambiguates the upper record-selector icon from the lower
                green plus (spec 2.1 / 3.2) via ``pick: topmost``.
4. ``ocr``      text found inside a UIA-resolved rectangle. The only tier that
                can reach a custom-drawn canvas such as the item grid.

plus ``hotkey``, a keyboard equivalent used as a last-resort fallback for
commands that have one (Save, Close).

No strategy may contain an absolute screen coordinate. ``tests/test_no_hardcoded
_coords.py`` enforces that mechanically.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from ..config import SETTINGS
from ..errors import LocatorError
from ..logging_setup import get
from . import vision_locator as vl
from .uia import Element, Rect, desktop
from .waits import wait_until

log = get("ui.resolver")

SELECTORS_FILE = Path(__file__).with_name("selectors.yaml")

DIRECTIONS = ("right", "left", "below", "above")

#: Ceiling for resolving a *container*. See `Resolver._scope`.
#:
#: A compromise with two failure modes either side of it. Too long, and one
#: poll of the caller's own wait loop is spent entirely inside this one, so a
#: dialog that is merely slow never gets a second look. Too short, and a walk
#: that is legitimately slow never finishes: once the Order carries two filled
#: item lines, walking the shell's accessibility tree takes the better part of
#: ten seconds, and a three-second ceiling made the left navigation panel
#: "disappear" - which surfaced, confusingly, as the Documents list not opening.
SCOPE_TIMEOUT = 8.0

#: How long a resolved container may be reused before it is looked up again.
#:
#: Worth more than it looks: each lookup is a full subtree walk costing seconds,
#: and a single step resolves several controls inside the same container. The
#: cache is only safe because every hit is re-checked for being on screen - a
#: container that has since been parked is never served from it.
SCOPE_CACHE_SECONDS = 5.0


class VirtualElement(Element):
    """Not a real control - a keyboard command dressed up as one."""

    def __init__(self, keys: str, logical_name: str, sender: Callable[[str], None]):
        self.keys = keys
        self.logical_name = logical_name
        self.tier = "hotkey"
        self._sender = sender
        self._c = None

    @property
    def name(self) -> str:
        return self.logical_name

    @property
    def control_type(self) -> str:
        return "Hotkey"

    @property
    def class_name(self) -> str:
        return ""

    @property
    def automation_id(self) -> str:
        return ""

    @property
    def rect(self) -> Rect:
        return Rect(0, 0, 0, 0)

    @property
    def enabled(self) -> bool:
        return True

    @property
    def offscreen(self) -> bool:
        return False

    def exists(self) -> bool:
        return True

    def click(self, dx_ratio: float = 0.5, dy_ratio: float = 0.5) -> None:
        self._sender(self.keys)

    def invoke(self) -> None:
        self._sender(self.keys)

    def __repr__(self) -> str:
        return "<Hotkey %s keys=%r>" % (self.logical_name, self.keys)


@dataclass
class RectElement(Element):
    """A rectangle discovered by pixels (template / OCR) rather than by UIA."""

    def __init__(self, rect: Rect, logical_name: str, tier: str, label: str = "", score: float = 0.0):
        self._rect = rect
        self.logical_name = logical_name
        self.tier = tier
        self._label = label
        self.score = score
        self._c = None

    @property
    def name(self) -> str:
        return self._label

    @property
    def control_type(self) -> str:
        return "Region"

    @property
    def class_name(self) -> str:
        return ""

    @property
    def automation_id(self) -> str:
        return ""

    @property
    def rect(self) -> Rect:
        return self._rect

    @property
    def enabled(self) -> bool:
        return True

    @property
    def offscreen(self) -> bool:
        return False

    def exists(self) -> bool:
        return not self._rect.is_empty()

    def text(self) -> str:
        return self._label

    def children(self):
        return []

    def parent(self):
        return None

    def focus(self) -> None:
        self.click()

    def __repr__(self) -> str:
        return "<Region %r rect=%s tier=%s score=%.2f>" % (
            self._label[:30],
            self._rect.as_tuple(),
            self.tier,
            self.score,
        )


@dataclass
class TierStats:
    counts: Dict[str, int] = field(default_factory=dict)

    def record(self, tier: str) -> None:
        self.counts[tier] = self.counts.get(tier, 0) + 1

    def as_dict(self) -> Dict[str, int]:
        return dict(sorted(self.counts.items(), key=lambda kv: -kv[1]))


class Resolver:
    """Resolves logical names to elements, using the declared strategy list."""

    def __init__(self, app_window_provider: Callable[[], Element], selectors_file: Path = None):
        self._app = app_window_provider
        self.selectors: Dict[str, List[Dict[str, Any]]] = load_selectors(
            selectors_file or SELECTORS_FILE
        )
        self.stats = TierStats()
        self._scope_cache: Dict[str, Any] = {}
        self._scope_cache_ts = 0.0
        #: Values the flow discovers at run time and selectors may refer to as
        #: "{order_number}". Needed because Fakturama RENAMES a document editor
        #: when it is saved - "New Order" becomes "PO000001" - so every selector
        #: that identifies the editor by name stops matching at the exact moment
        #: the document starts existing. The number is read from the form in
        #: step 1.4 and put here; see `Ctx.remember`.
        self.values: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def resolve(self, logical_name: str, timeout: Optional[float] = None) -> Element:
        """Wait for, and return, the element. Raises LocatorError on failure."""
        timeout = SETTINGS.default_timeout if timeout is None else timeout
        tried: List[str] = []

        def attempt():
            tried.clear()
            for strategy in self._strategies(logical_name):
                tier = strategy.get("tier", "uia")
                try:
                    el = self._apply(logical_name, tier, strategy)
                except Exception as exc:
                    tried.append("%s(error: %s)" % (tier, exc))
                    continue
                if el is not None:
                    self.stats.record(tier)
                    log.debug("resolved %s via %s -> %r", logical_name, tier, el)
                    return el
                tried.append(tier)
            return None

        try:
            return wait_until(attempt, timeout=timeout, what="control %r" % logical_name)
        except Exception:
            raise LocatorError(logical_name, tried)

    def try_resolve(self, logical_name: str, timeout: float = 1.5) -> Optional[Element]:
        """Non-raising variant, for optional controls and existence checks."""
        try:
            return self.resolve(logical_name, timeout=timeout)
        except LocatorError:
            return None

    def exists(self, logical_name: str, timeout: float = 1.5) -> bool:
        return self.try_resolve(logical_name, timeout=timeout) is not None

    def rect_of(self, logical_name: str, timeout: Optional[float] = None) -> Rect:
        return self.resolve(logical_name, timeout=timeout).rect

    def invalidate_scopes(self) -> None:
        self._scope_cache.clear()

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _strategies(self, logical_name: str) -> List[Dict[str, Any]]:
        if logical_name not in self.selectors:
            raise LocatorError(logical_name, ["<not declared in selectors.yaml>"])
        return [
            filled
            for filled in (self._fill(s) for s in self.selectors[logical_name])
            if filled is not None
        ]

    def _fill(self, strategy: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Substitute "{name}" placeholders, or drop the strategy.

        Dropping matters: a strategy asking for `name: "{order_number}"` before
        the order number is known would become `name: ""`, which matches every
        unnamed pane in the shell. A strategy whose value is not yet known is
        simply not attempted.
        """
        out = dict(strategy)
        for key, value in strategy.items():
            if not isinstance(value, str) or "{" not in value:
                continue
            try:
                filled = value.format(**self.values)
            except (KeyError, IndexError):
                return None
            if not filled.strip():
                return None
            out[key] = filled
        return out

    def _scope(self, name: Optional[str]) -> Element:
        """Resolve a container. `None`/`app` = the main window, `desktop` = root."""
        if name in (None, "", "app"):
            return self._app()
        if name == "desktop":
            return desktop()

        now = time.monotonic()
        if now - self._scope_cache_ts > SCOPE_CACHE_SECONDS:
            self._scope_cache.clear()
            self._scope_cache_ts = now
        cached = self._scope_cache.get(name)
        if cached is not None and cached.on_screen:
            # A container that has since been parked (an editor that is no
            # longer the active tab) must not keep serving its children - see
            # uia.PARKED_COORDINATE.
            return cached
        self._scope_cache.pop(name, None)

        # A SHORT timeout, deliberately. Resolving a scope happens inside the
        # caller's own `wait_until` loop, so a full-length wait here would spend
        # the entire budget inside a single poll: the run that could not find
        # 'dlg.product.table' timed out "after 20.0s (1 polls)" - it never got a
        # second look at a dialog that was merely slow. Failing fast here lets
        # the outer loop poll, which is where the waiting is supposed to happen.
        el = self.resolve(name, timeout=SCOPE_TIMEOUT)
        self._scope_cache[name] = el
        return el

    def _apply(self, logical_name: str, tier: str, spec: Dict[str, Any]) -> Optional[Element]:
        handler = getattr(self, "_tier_" + tier, None)
        if handler is None:
            raise ValueError("unknown grounding tier %r" % tier)
        return handler(logical_name, spec)

    # --- tier 1: semantic UIA ----------------------------------------- #
    def _tier_uia(self, logical_name: str, spec: Dict[str, Any]) -> Optional[Element]:
        scope = self._scope(spec.get("scope"))
        allow_parked = bool(spec.get("allow_parked", False))
        matches = [
            e
            for e in scope.descendants(max_depth=int(spec.get("depth", 14)))
            if _uia_matches(e, spec) and (allow_parked or e.on_screen)
        ]
        if not matches:
            return None
        matches = _order(matches, spec.get("pick", "first"))
        index = int(spec.get("index", 0))
        if index >= len(matches):
            return None
        el = matches[index]
        el.logical_name, el.tier = logical_name, "uia"
        return el

    # --- tier 2: anchor-relative --------------------------------------- #
    def _tier_anchor(self, logical_name: str, spec: Dict[str, Any]) -> Optional[Element]:
        scope = self._scope(spec.get("scope"))
        anchor_text = str(spec["anchor_text"])
        direction = spec.get("direction", "right")
        if direction not in DIRECTIONS:
            raise ValueError("bad direction %r" % direction)

        nodes = list(scope.descendants(max_depth=int(spec.get("depth", 14))))
        anchors = [
            e
            for e in nodes
            if _text_matches(e.name, anchor_text, spec.get("anchor_exact", False))
            and e.on_screen
        ]
        if not anchors:
            return None
        anchor = anchors[int(spec.get("anchor_index", 0))] if len(anchors) > int(
            spec.get("anchor_index", 0)
        ) else anchors[0]

        candidates = [
            e
            for e in nodes
            if _uia_matches(e, spec, ignore_name=True) and e.on_screen
        ]
        ordered = ordered_in_direction(anchor.rect, [c.rect for c in candidates], direction)
        index = int(spec.get("match_index", 0))
        if index >= len(ordered):
            return None
        el = candidates[ordered[index]]
        el.logical_name, el.tier = logical_name, "anchor"
        return el

    # --- tier 3: icon template match, scoped --------------------------- #
    def _tier_template(self, logical_name: str, spec: Dict[str, Any]) -> Optional[Element]:
        scope = self._scope(spec.get("scope"))
        region = scope.rect
        if not region.is_onscreen():
            return None
        image = SETTINGS.assets_dir / spec["image"] if not Path(spec["image"]).is_absolute() else Path(spec["image"])
        if not image.exists():
            raise FileNotFoundError(
                "icon template %s is missing - run `f2c capture-icon` to record it" % image
            )
        matches = vl.match_template(
            image, region, threshold=float(spec.get("threshold", SETTINGS.template_threshold))
        )
        best = vl.pick_match(matches, spec.get("pick", "best"))
        if best is None:
            return None
        return RectElement(best.rect, logical_name, "template", spec["image"], best.score)

    # --- tier 4: OCR inside a scoped rect ------------------------------ #
    def _tier_ocr(self, logical_name: str, spec: Dict[str, Any]) -> Optional[Element]:
        scope = self._scope(spec.get("scope"))
        region = scope.rect
        if not region.is_onscreen():
            return None
        hits = vl.find_text(region, str(spec["text"]), exact=bool(spec.get("exact", False)))
        if not hits:
            return None
        pick = spec.get("pick", "first")
        if pick == "topmost":
            hit = min(hits, key=lambda w: w.rect.top)
        elif pick == "bottommost":
            hit = max(hits, key=lambda w: w.rect.top)
        else:
            hit = hits[0]
        rect = hit.rect
        if spec.get("direction"):
            rect = _shift(rect, spec["direction"], int(spec.get("gap", 8)))
        return RectElement(rect, logical_name, "ocr", hit.text, hit.conf / 100.0)

    # --- keyboard equivalent ------------------------------------------- #
    def _tier_hotkey(self, logical_name: str, spec: Dict[str, Any]) -> Optional[Element]:
        keys = str(spec["keys"])

        def send(k: str) -> None:
            self._app().send_keys(k)

        return VirtualElement(keys, logical_name, send)


# --------------------------------------------------------------------------- #
# matching helpers (pure functions - unit tested without a live app)
# --------------------------------------------------------------------------- #
def _text_matches(actual: str, expected: str, exact: bool) -> bool:
    a, e = (actual or "").strip().casefold(), expected.strip().casefold()
    # Fakturama labels often carry a trailing colon or an access-key ampersand
    a = a.rstrip(":").replace("&", "")
    e = e.rstrip(":").replace("&", "")
    return a == e if exact else (e in a)


def _has_descendant_text(el: Element, needle: str, max_depth: int = 3) -> bool:
    """True when a descendant label carries `needle`.

    Eclipse/SWT groups the fields of a section into an unnamed `Pane` whose only
    identity is the `Text` label inside it - "Addresses", "Items". This is how a
    selector says "the composite that holds the Addresses label" without
    resorting to an index.
    """
    target = needle.strip().casefold()
    for child in el.descendants(max_depth=max_depth):
        if child.control_type in ("Text", "Label", "Group") and target in (
            child.name or ""
        ).strip().casefold():
            return True
    return False


#: children a canvas is allowed to own while still counting as "leaf"
_CHROME_TYPES = ("ScrollBar", "Thumb")


def _has_content_children(el: Element) -> bool:
    return any(c.control_type not in _CHROME_TYPES for c in el.children())


def _uia_matches(el: Element, spec: Dict[str, Any], ignore_name: bool = False) -> bool:
    want_type = spec.get("type")
    if want_type and el.control_type.casefold() != str(want_type).casefold():
        return False
    if "contains_text" in spec and not _has_descendant_text(
        el, str(spec["contains_text"]), int(spec.get("contains_depth", 3))
    ):
        return False
    if spec.get("leaf") and _has_content_children(el):
        # A custom-drawn canvas (NatTable) exposes no accessibility children for
        # its content. It may still own scrollbars, so those do not count.
        return False
    if not ignore_name:
        if "name" in spec and not _text_matches(el.name, str(spec["name"]), True):
            return False
        if "name_contains" in spec and not _text_matches(el.name, str(spec["name_contains"]), False):
            return False
        # A loose `name_contains` can catch a control that merely shares a word.
        # "Invoice" matched the ORDER editor's own "Invoice address" tab, and
        # the flow then clicked that instead of the follow-up action - so a
        # strategy may name the words that disqualify a match.
        if "name_excludes" in spec:
            excludes = spec["name_excludes"]
            if isinstance(excludes, str):
                excludes = [excludes]
            if any(_text_matches(el.name, str(x), False) for x in excludes):
                return False
    if "class_name" in spec and str(spec["class_name"]).casefold() not in el.class_name.casefold():
        return False
    if "automation_id" in spec and str(spec["automation_id"]) != el.automation_id:
        return False
    if spec.get("enabled_only", True) and not el.enabled:
        return False
    if spec.get("visible_only", True) and not el.visible:
        return False
    return True


def _order(elements: List[Element], pick: str) -> List[Element]:
    if pick == "topmost":
        return sorted(elements, key=lambda e: (e.rect.top, e.rect.left))
    if pick == "bottommost":
        return sorted(elements, key=lambda e: (-e.rect.top, e.rect.left))
    if pick == "leftmost":
        return sorted(elements, key=lambda e: (e.rect.left, e.rect.top))
    if pick == "rightmost":
        return sorted(elements, key=lambda e: (-e.rect.left, e.rect.top))
    if pick == "largest":
        return sorted(elements, key=lambda e: -e.rect.area)
    if pick == "smallest":
        # the innermost composite that still satisfies the match - this is how
        # "the little strip holding the Addresses label and its two icons" is
        # expressed, as opposed to the whole section that also contains it
        return sorted(
            [e for e in elements if not e.rect.is_empty()], key=lambda e: e.rect.area
        )
    return elements  # document order


def ordered_in_direction(anchor: Rect, candidates: List[Rect], direction: str) -> List[int]:
    """Candidate indices lying in `direction`, nearest first.

    "Right of" means: starts at or after the anchor's right edge *and* shares
    vertical space with it. Requiring the overlap is what stops the resolver
    picking a field from the row below when a label happens to be short.

    A list rather than a single hit, because Fakturama labels two fields with
    one caption: "First Name Last Name" fronts two Edits, and "ZIP - City"
    fronts two more. `match_index` in the selector picks which one.
    """
    scored = []
    for i, c in enumerate(candidates):
        if c.is_empty():
            continue
        if direction == "right":
            if c.left < anchor.right - 2 or anchor.vertical_overlap(c) <= 0:
                continue
            score = (c.left - anchor.right, abs(c.center[1] - anchor.center[1]))
        elif direction == "left":
            if c.right > anchor.left + 2 or anchor.vertical_overlap(c) <= 0:
                continue
            score = (anchor.left - c.right, abs(c.center[1] - anchor.center[1]))
        elif direction == "below":
            if c.top < anchor.bottom - 2:
                continue
            horizontal = min(c.right, anchor.right) - max(c.left, anchor.left)
            if horizontal <= 0:
                continue
            score = (c.top - anchor.bottom, abs(c.center[0] - anchor.center[0]))
        else:  # above
            if c.bottom > anchor.top + 2:
                continue
            horizontal = min(c.right, anchor.right) - max(c.left, anchor.left)
            if horizontal <= 0:
                continue
            score = (anchor.top - c.bottom, abs(c.center[0] - anchor.center[0]))
        scored.append((score, i))
    scored.sort()
    return [i for _, i in scored]


def nearest_in_direction(anchor: Rect, candidates: List[Rect], direction: str) -> Optional[int]:
    """The single nearest candidate in `direction`, or None."""
    ordered = ordered_in_direction(anchor, candidates, direction)
    return ordered[0] if ordered else None


def _shift(rect: Rect, direction: str, gap: int) -> Rect:
    w, h = max(rect.width, 40), max(rect.height, 16)
    if direction == "right":
        return Rect(rect.right + gap, rect.top, rect.right + gap + w, rect.bottom)
    if direction == "left":
        return Rect(rect.left - gap - w, rect.top, rect.left - gap, rect.bottom)
    if direction == "below":
        return Rect(rect.left, rect.bottom + gap, rect.right, rect.bottom + gap + h)
    return Rect(rect.left, rect.top - gap - h, rect.right, rect.top - gap)


# --------------------------------------------------------------------------- #
# selector file
# --------------------------------------------------------------------------- #
def load_selectors(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue  # YAML anchors / notes
        if not isinstance(value, list):
            raise ValueError("selector %r must be a list of strategies" % key)
        for strategy in value:
            if not isinstance(strategy, dict) or "tier" not in strategy:
                raise ValueError("selector %r has a strategy without a tier: %r" % (key, strategy))
            if strategy["tier"] not in ("uia", "anchor", "template", "ocr", "hotkey"):
                raise ValueError("selector %r has unknown tier %r" % (key, strategy["tier"]))
        out[key] = value
    return out
