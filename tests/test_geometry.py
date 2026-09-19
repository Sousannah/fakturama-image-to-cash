"""Anchor-relative grounding and grid reconstruction, tested without a live app.

These are the two pieces of geometry the automation depends on, so they are
exercised against synthetic rectangles rather than only against Fakturama.
"""
from __future__ import annotations

from f2c.ui.grid import CanvasGrid
from f2c.ui.resolver import nearest_in_direction
from f2c.ui.uia import Rect


def test_rect_basics():
    r = Rect(10, 20, 110, 60)
    assert r.width == 100 and r.height == 40
    assert r.center == (60, 40)
    assert r.contains_point(50, 30)
    assert not r.contains_point(200, 30)
    assert r.area == 4000


def test_vertical_overlap():
    a = Rect(0, 10, 50, 30)
    b = Rect(60, 20, 120, 40)
    assert a.vertical_overlap(b) == 10
    assert a.vertical_overlap(Rect(60, 100, 120, 140)) == 0


def test_nearest_right_picks_the_field_on_the_same_row():
    label = Rect(10, 100, 90, 120)
    candidates = [
        Rect(100, 300, 300, 320),   # far below - wrong row
        Rect(100, 100, 300, 120),   # same row - correct
        Rect(400, 100, 600, 120),   # same row but further away
    ]
    assert nearest_in_direction(label, candidates, "right") == 1


def test_nearest_right_ignores_fields_without_vertical_overlap():
    label = Rect(10, 100, 90, 120)
    candidates = [Rect(100, 300, 300, 320)]
    assert nearest_in_direction(label, candidates, "right") is None


def test_nearest_right_ignores_fields_to_the_left():
    label = Rect(200, 100, 280, 120)
    candidates = [Rect(10, 100, 100, 120)]
    assert nearest_in_direction(label, candidates, "right") is None


def test_nearest_below_requires_horizontal_overlap():
    label = Rect(10, 100, 90, 120)
    assert nearest_in_direction(label, [Rect(10, 130, 90, 150)], "below") == 0
    assert nearest_in_direction(label, [Rect(500, 130, 590, 150)], "below") is None


def test_nearest_left_and_above():
    anchor = Rect(200, 200, 280, 220)
    assert nearest_in_direction(anchor, [Rect(100, 200, 180, 220)], "left") == 0
    assert nearest_in_direction(anchor, [Rect(200, 150, 280, 180)], "above") == 0


# --- grid addressing -------------------------------------------------------- #
def _synthetic_grid() -> CanvasGrid:
    """A 3-column, 2-row table at an arbitrary screen position."""
    region = Rect(500, 400, 800, 480)
    grid = CanvasGrid(region=region)
    grid.columns = [
        Rect(500, 400, 600, 480),
        Rect(600, 400, 700, 480),
        Rect(700, 400, 800, 480),
    ]
    grid.rows = [Rect(500, 420, 800, 450), Rect(500, 450, 800, 480)]
    grid.headers = ["Qty.", "U.Price", "Price"]
    grid.cells = {"0,0": "2", "0,1": "250.00", "0,2": "450.00"}
    return grid


def test_grid_cell_rect_is_relative_to_the_region():
    grid = _synthetic_grid()
    assert grid.cell_rect(0, 1) == Rect(600, 420, 700, 450)
    assert grid.cell_rect(1, 2) == Rect(700, 450, 800, 480)


def test_grid_column_lookup_by_header():
    grid = _synthetic_grid()
    assert grid.column_index("Qty.") == 0
    assert grid.column_index("Qty") == 0        # trailing dot tolerated
    assert grid.column_index("U.Price") == 1
    assert grid.column_index("Nonsense") is None


def test_grid_reports_shape_and_usability():
    grid = _synthetic_grid()
    assert grid.shape == (2, 3)
    assert grid.is_usable()
    assert not CanvasGrid(region=Rect(0, 0, 10, 10)).is_usable()


def test_grid_cell_text_and_row_text():
    grid = _synthetic_grid()
    assert grid.cell_text(0, 2) == "450.00"
    assert grid.row_text(0) == ["2", "250.00", "450.00"]
    assert grid.row_text(1) == ["", "", ""]


def test_grid_indexes_by_coordinate():
    grid = _synthetic_grid()
    assert grid.column_index_at(650) == 1
    assert grid.row_index_at(460) == 1
    assert grid.row_index_at(10) is None
