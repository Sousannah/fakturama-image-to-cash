"""Console + file logging. Rich if available, plain otherwise."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

_CONFIGURED = False


def setup(level: int = logging.INFO, logfile: Optional[Path] = None) -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger("f2c")
    if not _CONFIGURED:
        root.setLevel(logging.DEBUG)
        try:
            from rich.logging import RichHandler

            handler = RichHandler(rich_tracebacks=True, show_path=False)
            handler.setFormatter(logging.Formatter("%(message)s", datefmt="%H:%M:%S"))
        except Exception:  # pragma: no cover
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
            )
        handler.setLevel(level)
        root.addHandler(handler)
        _CONFIGURED = True

    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
        )
        root.addHandler(fh)
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger("f2c." + name)
