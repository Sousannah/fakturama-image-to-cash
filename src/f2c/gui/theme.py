"""Colours, fonts and ttk styling for the desktop front end.

Kept in one place so the three stage panels look like one application rather
than three. The palette is taken from the sample order document itself - deep
navy headers on a light page - so a screenshot of the tool sits next to a
screenshot of the source image without clashing.
"""
from __future__ import annotations

import tkinter.font as tkfont
from tkinter import ttk

# --------------------------------------------------------------------------- #
# palette
# --------------------------------------------------------------------------- #
NAVY = "#0E2338"          # header bar, titles
NAVY_SOFT = "#1B3A5C"     # header subtitle, secondary navy
ACCENT = "#2E7DD1"        # primary action, active stage
ACCENT_DARK = "#1F62A8"
PAGE = "#EDF1F7"          # window background
SURFACE = "#FFFFFF"       # cards
SURFACE_ALT = "#F6F8FB"   # inset areas, monospace panels
BORDER = "#D5DEE9"
TEXT = "#16202C"
MUTED = "#697A8D"

OK = "#17864A"
OK_BG = "#E4F4EA"
WARN = "#B4700B"
WARN_BG = "#FDF1DC"
ERR = "#C32B2B"
ERR_BG = "#FBE7E7"
IDLE = "#7D8CA0"
IDLE_BG = "#EDF1F7"

#: status -> (foreground, background). Shared by the stage badges and the
#: per-step chips in the automation panel so one vocabulary of colour covers
#: the whole window.
STATUS_COLOURS = {
    "idle": (IDLE, IDLE_BG),
    "running": (ACCENT_DARK, "#E3EFFB"),
    "ok": (OK, OK_BG),
    "skipped": (MUTED, IDLE_BG),
    "manual-review": (WARN, WARN_BG),
    "failed": (ERR, ERR_BG),
}


def status_colours(status: str):
    return STATUS_COLOURS.get(status, STATUS_COLOURS["idle"])


# --------------------------------------------------------------------------- #
# fonts
# --------------------------------------------------------------------------- #
class Fonts:
    """Resolved at runtime because a font family that is missing silently
    falls back to something ugly; we pick the first family that exists."""

    def __init__(self, root):
        families = set(tkfont.families(root))

        def pick(*names, default="TkDefaultFont"):
            for n in names:
                if n in families:
                    return n
            return default

        ui = pick("Segoe UI", "Inter", "Helvetica Neue", "Arial")
        mono = pick("Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New")

        self.family = ui
        self.mono_family = mono

        self.title = (ui, 17, "bold")
        self.subtitle = (ui, 9)
        self.h1 = (ui, 13, "bold")
        self.h2 = (ui, 10, "bold")
        self.body = (ui, 10)
        self.small = (ui, 9)
        self.tiny = (ui, 8)
        self.button = (ui, 10, "bold")
        self.mono = (mono, 9)
        self.mono_small = (mono, 8)
        self.number = (ui, 18, "bold")


# --------------------------------------------------------------------------- #
def apply(root) -> Fonts:
    """Style the ttk widgets and return the resolved fonts."""
    fonts = Fonts(root)
    style = ttk.Style(root)
    # 'clam' is the only built-in theme that honours background colours on
    # every platform; the native themes ignore most of what is set below.
    try:
        style.theme_use("clam")
    except Exception:  # pragma: no cover
        pass

    root.configure(bg=PAGE)

    style.configure("TFrame", background=PAGE)
    style.configure("Card.TFrame", background=SURFACE)
    style.configure("Inset.TFrame", background=SURFACE_ALT)
    style.configure("Header.TFrame", background=NAVY)

    style.configure("TLabel", background=PAGE, foreground=TEXT, font=fonts.body)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT, font=fonts.body)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=fonts.small)
    style.configure("CardH1.TLabel", background=SURFACE, foreground=NAVY, font=fonts.h1)
    style.configure("CardH2.TLabel", background=SURFACE, foreground=NAVY, font=fonts.h2)
    style.configure("Inset.TLabel", background=SURFACE_ALT, foreground=TEXT, font=fonts.body)
    style.configure("InsetMuted.TLabel", background=SURFACE_ALT, foreground=MUTED, font=fonts.small)
    style.configure("HeaderTitle.TLabel", background=NAVY, foreground="#FFFFFF", font=fonts.title)
    style.configure("HeaderSub.TLabel", background=NAVY, foreground="#9FB6CE", font=fonts.subtitle)
    style.configure("Muted.TLabel", background=PAGE, foreground=MUTED, font=fonts.small)

    style.configure(
        "Accent.TButton",
        background=ACCENT, foreground="#FFFFFF", font=fonts.button,
        borderwidth=0, focuscolor=ACCENT, padding=(16, 9),
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", ACCENT_DARK), ("active", ACCENT_DARK), ("disabled", "#B9C7D6")],
        foreground=[("disabled", "#F2F6FA")],
    )
    style.configure(
        "Ghost.TButton",
        background=SURFACE, foreground=NAVY, font=fonts.body,
        borderwidth=1, bordercolor=BORDER, focuscolor=SURFACE, padding=(12, 7),
    )
    style.map(
        "Ghost.TButton",
        background=[("pressed", "#E6EDF6"), ("active", "#F2F6FB"), ("disabled", SURFACE_ALT)],
        foreground=[("disabled", "#A9B6C4")],
    )

    style.configure(
        "Stage.Horizontal.TProgressbar",
        troughcolor=BORDER, background=ACCENT, borderwidth=0,
        lightcolor=ACCENT, darkcolor=ACCENT,
    )

    style.configure(
        "Steps.Treeview",
        background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
        rowheight=25, borderwidth=0, font=fonts.small,
    )
    style.configure(
        "Steps.Treeview.Heading",
        background=SURFACE_ALT, foreground=MUTED, font=fonts.tiny,
        relief="flat", borderwidth=0, padding=(6, 5),
    )
    style.map("Steps.Treeview.Heading", background=[("active", SURFACE_ALT)])
    style.map(
        "Steps.Treeview",
        background=[("selected", "#DCEAF8")],
        foreground=[("selected", TEXT)],
    )

    style.configure("TSeparator", background=BORDER)
    style.configure(
        "Vertical.TScrollbar",
        background=BORDER, troughcolor=SURFACE_ALT, borderwidth=0,
        arrowsize=11, arrowcolor=MUTED,
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=BORDER, troughcolor=SURFACE_ALT, borderwidth=0,
        arrowsize=11, arrowcolor=MUTED,
    )
    return fonts
