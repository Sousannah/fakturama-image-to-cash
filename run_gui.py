#!/usr/bin/env python
"""Open the desktop front end without installing the package.

    python run_gui.py
    python run_gui.py fixtures/sample_order.png

Equivalent to `f2c gui` once `pip install -e .` has been run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from f2c.gui.app import main  # noqa: E402

if __name__ == "__main__":
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    def opt(name):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None

    main(
        positional[0] if positional else None,
        auto_run="--run" in sys.argv,
        geometry_file=opt("--geometry-out"),
        report=opt("--report"),
        select=opt("--select"),
    )
