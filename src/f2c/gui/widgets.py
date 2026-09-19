"""Small composite widgets shared by the stage panels.

Tkinter has no card, no chip and no stage list, so they are assembled here once
rather than three times in `app.py`.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from . import theme


class Card(ttk.Frame):
    """A white panel with a hairline border and an optional title row.

    The border is a 1px background frame rather than a relief, because ttk's
    reliefs render as a grey bevel under the 'clam' theme.
    """

    def __init__(self, master, title: str = "", subtitle: str = "", fonts=None, pad: int = 14):
        self._outer = tk.Frame(master, bg=theme.BORDER)
        super().__init__(self._outer, style="Card.TFrame", padding=pad)
        super().pack(fill="both", expand=True, padx=1, pady=1)
        self.fonts = fonts
        self.body = self

        if title:
            head = ttk.Frame(self, style="Card.TFrame")
            head.pack(fill="x", pady=(0, 10))
            ttk.Label(head, text=title, style="CardH1.TLabel").pack(side="left")
            if subtitle:
                ttk.Label(head, text=subtitle, style="CardMuted.TLabel").pack(
                    side="right", pady=(4, 0)
                )

    # the outer frame is what the caller places
    def place_in(self, **kwargs) -> "Card":
        self._outer.pack(**kwargs)
        return self

    def grid_in(self, **kwargs) -> "Card":
        self._outer.grid(**kwargs)
        return self


class Chip(tk.Label):
    """A small rounded-looking status pill. Colour comes from the status."""

    def __init__(self, master, text: str = "idle", status: str = "idle", fonts=None, width: int = 0):
        fg, bg = theme.status_colours(status)
        super().__init__(
            master, text=text, bg=bg, fg=fg,
            font=(fonts.family if fonts else "Segoe UI", 8, "bold"),
            padx=8, pady=2, bd=0,
        )
        if width:
            self.configure(width=width)

    def set(self, text: str, status: str) -> None:
        fg, bg = theme.status_colours(status)
        self.configure(text=text, bg=bg, fg=fg)


class StageItem(tk.Frame):
    """One row of the left-hand pipeline list: number, name, status chip.

    The number badge doubles as the progress indicator - it fills with the
    accent colour while the stage runs and with green once it is done, so the
    left column alone tells you where the run is.
    """

    def __init__(self, master, number: int, name: str, hint: str, fonts):
        super().__init__(master, bg=theme.SURFACE)
        self.fonts = fonts

        self.badge = tk.Label(
            self, text=str(number), bg=theme.IDLE_BG, fg=theme.IDLE,
            font=(fonts.family, 10, "bold"), width=3, height=1, bd=0,
        )
        self.badge.pack(side="left", padx=(0, 10), pady=6)

        text = tk.Frame(self, bg=theme.SURFACE)
        text.pack(side="left", fill="x", expand=True, pady=6)
        self.name = tk.Label(
            text, text=name, bg=theme.SURFACE, fg=theme.MUTED,
            font=(fonts.family, 10, "bold"), anchor="w",
        )
        self.name.pack(fill="x")
        self.hint = tk.Label(
            text, text=hint, bg=theme.SURFACE, fg=theme.MUTED,
            font=(fonts.family, 8), anchor="w",
        )
        self.hint.pack(fill="x")

    def set_status(self, status: str, hint: Optional[str] = None) -> None:
        fg, bg = theme.status_colours(status)
        self.badge.configure(bg=bg, fg=fg)
        self.name.configure(fg=theme.NAVY if status != "idle" else theme.MUTED)
        if hint is not None:
            self.hint.configure(text=hint, fg=fg if status != "idle" else theme.MUTED)


class MonoPanel(tk.Frame):
    """A read-only monospace text area with scrollbars.

    Used for the OCR transcription, where the horizontal positions carry
    meaning - so it does not wrap, and it gets a horizontal scrollbar.
    """

    def __init__(self, master, fonts, wrap: str = "none", height: int = 10):
        super().__init__(master, bg=theme.BORDER)
        inner = tk.Frame(self, bg=theme.SURFACE_ALT)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self.text = tk.Text(
            inner, wrap=wrap, height=height, bd=0, padx=10, pady=8,
            bg=theme.SURFACE_ALT, fg=theme.TEXT, font=fonts.mono,
            insertwidth=0, highlightthickness=0, relief="flat",
        )
        vs = ttk.Scrollbar(inner, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")

        if wrap == "none":
            hs = ttk.Scrollbar(inner, orient="horizontal", command=self.text.xview)
            self.text.configure(xscrollcommand=hs.set)
            hs.pack(side="bottom", fill="x")

        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(state="disabled")

        self.text.tag_configure("dim", foreground=theme.MUTED)
        self.text.tag_configure("ok", foreground=theme.OK)
        self.text.tag_configure("warn", foreground=theme.WARN)
        self.text.tag_configure("err", foreground=theme.ERR)
        self.text.tag_configure("key", foreground=theme.ACCENT_DARK)
        self.text.tag_configure("head", foreground=theme.NAVY, font=(fonts.mono_family, 9, "bold"))

    def set(self, content: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.configure(state="disabled")

    def append(self, content: str, tag: str = "") -> None:
        self.text.configure(state="normal")
        self.text.insert("end", content, tag or ())
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.set("")


class Metric(tk.Frame):
    """A big number with a caption underneath - the OCR statistics strip."""

    def __init__(self, master, caption: str, value: str, fonts, colour: str = theme.NAVY):
        super().__init__(master, bg=theme.SURFACE)
        self.value = tk.Label(
            self, text=value, bg=theme.SURFACE, fg=colour,
            font=(fonts.family, 16, "bold"), anchor="w",
        )
        self.value.pack(anchor="w")
        tk.Label(
            self, text=caption.upper(), bg=theme.SURFACE, fg=theme.MUTED,
            font=(fonts.family, 7, "bold"), anchor="w",
        ).pack(anchor="w")

    def set(self, value: str, colour: Optional[str] = None) -> None:
        self.value.configure(text=value)
        if colour:
            self.value.configure(fg=colour)
