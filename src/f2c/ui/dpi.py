"""Per-monitor DPI awareness.

Without this, every BoundingRectangle we read is silently scaled on a
high-DPI display and every derived click lands in the wrong place. It must run
before the first UIA call.
"""
from __future__ import annotations

import ctypes

from ..logging_setup import get

log = get("ui.dpi")

_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def make_process_dpi_aware() -> str:
    """Best-effort; returns the mode that was applied."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(_PER_MONITOR_AWARE_V2)
        return "per-monitor-v2"
    except Exception:
        pass
    try:  # Windows 8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:  # Vista+
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception as exc:
        log.warning("could not set DPI awareness: %s", exc)
        return "none"
