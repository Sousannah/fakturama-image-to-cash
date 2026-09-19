"""The brief's hard constraint, enforced as a test.

"without relying on hardcoded coordinates or a fixed UI layout"

Every click coordinate in this package must be derived at runtime from a
rectangle that UIA reported, or from a template/OCR match *inside* such a
rectangle. This test fails if any module calls a mouse function with literal
numbers, which is the only way a fixed coordinate could get in.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "f2c"

#: functions that move or click the physical pointer
POINTER_CALLS = {"Click", "DoubleClick", "RightClick", "MoveTo", "DragDrop", "PressMouse"}

PY_FILES = sorted(SRC.rglob("*.py"))


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name:
                yield name, node


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_no_literal_pointer_coordinates(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name, node in _calls(tree):
        if name not in POINTER_CALLS:
            continue
        literals = [
            a
            for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, (int, float))
        ]
        assert not literals, (
            "%s:%d calls %s with literal coordinates %s - every coordinate must be "
            "derived from a resolved rectangle"
            % (path.name, node.lineno, name, [a.value for a in literals])
        )


def test_screen_size_is_never_queried():
    """Reading the screen size would imply a layout assumption."""
    banned = ("GetSystemMetrics", "screensize", "GetScreenSize", "virtual_screen")
    offenders = []
    for path in PY_FILES:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append("%s: %s" % (path.name, token))
    assert not offenders, offenders


def test_window_is_never_moved_or_resized_to_a_fixed_size():
    banned = ("MoveWindow", "SetWindowPos", "resize_to", "set_window_size")
    offenders = []
    for path in PY_FILES:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append("%s: %s" % (path.name, token))
    assert not offenders, offenders
