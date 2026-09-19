"""Static guarantees about the selector registry.

These are the tests that keep the grounding strategy honest as the YAML grows:
no absolute coordinates, no dangling logical names, no unreachable scopes.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from f2c.ui.resolver import SELECTORS_FILE, load_selectors

SRC = Path(__file__).resolve().parents[1] / "src" / "f2c"
FLOW = SRC / "flow"

BUILT_IN_SCOPES = {"app", "desktop", None, ""}


@pytest.fixture(scope="module")
def selectors():
    return load_selectors(SELECTORS_FILE)


def test_registry_loads_and_is_not_trivial(selectors):
    assert len(selectors) > 50


def test_every_strategy_has_a_known_tier(selectors):
    allowed = {"uia", "anchor", "template", "ocr", "hotkey"}
    for name, strategies in selectors.items():
        for s in strategies:
            assert s["tier"] in allowed, "%s uses tier %r" % (name, s["tier"])


def test_every_scope_reference_resolves(selectors):
    for name, strategies in selectors.items():
        for s in strategies:
            scope = s.get("scope")
            if scope in BUILT_IN_SCOPES:
                continue
            assert scope in selectors, "%s references undeclared scope %r" % (name, scope)


def test_anchor_strategies_declare_a_direction_and_text(selectors):
    for name, strategies in selectors.items():
        for s in strategies:
            if s["tier"] == "anchor":
                assert "anchor_text" in s, "%s: anchor without anchor_text" % name
                assert s.get("direction", "right") in ("right", "left", "below", "above")


def test_template_strategies_point_at_an_icon_file(selectors):
    for name, strategies in selectors.items():
        for s in strategies:
            if s["tier"] == "template":
                assert s["image"].endswith(".png"), name
                assert s["image"].startswith("icons/"), name


def test_no_selector_contains_an_absolute_coordinate(selectors):
    """The brief forbids hardcoded coordinates; enforce it mechanically."""
    banned = {"x", "y", "left", "top", "right", "bottom", "coordinate", "coords", "point"}
    for name, strategies in selectors.items():
        for s in strategies:
            for key in s:
                assert key not in banned, "%s declares a raw coordinate key %r" % (name, key)


def test_first_strategy_of_each_control_prefers_a_semantic_tier(selectors):
    """Pixels are a fallback, never the first thing tried, except for the two
    icon-only controls the spec itself describes visually."""
    pixel_first_allowed = {
        "order.address_select_icon",
        "order.address_new_icon",
        "order.item_select_icon",
        "order.item_new_icon",
        "payment.list_new",
        "vat.list_new",
    }
    for name, strategies in selectors.items():
        if name in pixel_first_allowed:
            continue
        assert strategies[0]["tier"] in ("uia", "anchor", "hotkey"), (
            "%s starts with a pixel tier" % name
        )


# --------------------------------------------------------------------------- #
LOGICAL_RE = re.compile(
    r"""(?:resolve|try_resolve|el|maybe|click|type_into|read|choose|check|
         wait_for|wait_gone|rect_of|exists)\(\s*["']([a-z][a-z0-9_.]*)["']""",
    re.VERBOSE,
)


def _referenced_names():
    names = set()
    for path in list(FLOW.glob("*.py")) + [SRC / "ui" / "grid.py", SRC / "verify" / "ui_readback.py"]:
        text = path.read_text(encoding="utf-8")
        for match in LOGICAL_RE.finditer(text):
            names.add(match.group(1))
    return names


def test_every_logical_name_used_in_the_flow_is_declared(selectors):
    missing = sorted(n for n in _referenced_names() if n not in selectors)
    assert not missing, "not declared in selectors.yaml: %s" % missing


def test_the_flow_actually_uses_the_registry():
    """Guards against a stage quietly hardcoding a lookup."""
    assert len(_referenced_names()) > 40


# --------------------------------------------------------------------------- #
# name_excludes: a loose name match must not catch a control that merely shares
# a word. "Invoice" matched the ORDER editor's own "Invoice address" tab, and
# the flow clicked that instead of the follow-up Invoice action.
# --------------------------------------------------------------------------- #
def test_name_excludes_rejects_a_control_that_only_shares_a_word():
    from f2c.ui.resolver import _uia_matches

    class _El:
        control_type = "TabItem"
        class_name = ""
        automation_id = ""
        enabled = True
        visible = True

        def __init__(self, name):
            self.name = name

        def children(self):
            return []

    spec = {"type": "TabItem", "name_contains": "Invoice", "name_excludes": ["address"]}
    assert _uia_matches(_El("New Invoice"), spec)
    assert not _uia_matches(_El("Invoice address"), spec)


def test_the_loose_editor_selectors_all_exclude_the_address_tabs(selectors):
    """Both document editors and both of their tabs carry the guard."""
    for name in ("order_editor", "invoice_editor", "order.tab", "invoice.tab"):
        loose = [
            s for s in selectors[name]
            if "name_contains" in s and s["name_contains"] in ("Order", "Invoice")
        ]
        assert loose, "%s lost its loose fallback" % name
        for s in loose:
            assert "address" in [x.casefold() for x in s.get("name_excludes", [])], name
