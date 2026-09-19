"""The desktop front end: image in, Order and Invoice out, with the evidence shown.

Three panels, in the order the pipeline actually runs:

1. **OCR** - Python OCR reads the pixels. The panel shows the transcription
   exactly as the model will see it, with its position preserved, plus the
   engine, the word count and the mean confidence.
2. **Groq validation** - the transcription goes to a Groq text model under a
   tool schema, and the result is reconciled in `Decimal`. The panel shows the
   structured fields, the arithmetic checks, and - the interesting part - which
   values the model had to *correct*, i.e. the ones that do not appear
   literally in the OCR reading.
3. **Fakturama** - the five-stage UI flow runs against the live application and
   the step list fills in as it goes, each row carrying its spec reference so a
   step can be checked against the written procedure.

Everything slow happens on a worker thread; the thread talks to the UI only by
putting messages on a queue that the main loop drains. Tkinter is not thread
safe, and a background thread touching a widget crashes the interpreter rather
than raising - so there is exactly one rule in this file: **widgets are only
ever touched from `_pump`.**
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..config import SETTINGS
from . import theme
from .widgets import Card, Chip, Metric, MonoPanel, StageItem

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_IMAGE = REPO_ROOT / "fixtures" / "sample_order.png"

STAGES = (
    (1, "Read the image", "Python OCR - EasyOCR / Tesseract"),
    (2, "Validate with Groq", "structure, correct and reconcile"),
    (3, "Drive Fakturama", "Order -> linked Invoice, verified"),
)


# --------------------------------------------------------------------------- #
def _norm(s: Any) -> str:
    """Comparison form used to ask 'did the OCR actually read this?'."""
    import re

    return re.sub(r"[\s,]+", "", str(s)).casefold()


class App:
    def __init__(
        self,
        image: Optional[Path] = None,
        auto_run: bool = False,
        geometry_file: Optional[Path] = None,
    ):
        self.geometry_file = Path(geometry_file) if geometry_file else None
        self.root = tk.Tk()
        self.root.title("Fakturama Image-to-Cash")
        self.root.minsize(1080, 680)
        self._fit_to_screen(1460, 860)
        self.fonts = theme.apply(self.root)

        self.q: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None

        self.image_path: Optional[Path] = None
        self.ocr_result = None          # f2c.extract.ocr.OcrResult
        self.ocr_layout: str = ""
        self.raw: Optional[Dict[str, Any]] = None
        self.doc = None                 # f2c.models.OrderDoc
        self.run_dir: Optional[Path] = None
        self.shots: Dict[str, str] = {}   # step index -> screenshot path
        self._preview_img = None          # keep a reference or Tk drops it
        self._thumb_img = None

        self._build()
        self._pump()
        if self.geometry_file is not None:
            # Tk exposes no child controls to UI Automation, so a script that
            # wants to drive this window cannot find its buttons the way the
            # rest of this project finds Fakturama's. Publishing the screen
            # rectangles lets `scripts/capture_demo.py` click them for real -
            # a physical click on a real button, not a shortcut past the UI.
            self.root.after(700, self._write_geometry)
            self.root.after(1000, self._state_tick)

        chosen = Path(image) if image else (DEFAULT_IMAGE if DEFAULT_IMAGE.exists() else None)
        if chosen:
            self._set_image(Path(chosen))
        if auto_run and chosen:
            self.root.after(400, self.run_all)

    def _fit_to_screen(self, width: int, height: int) -> None:
        """Size the window to the preferred size, but never past the desktop.

        The work area is what is left after the taskbar. A window taller than
        the screen hides its own footer - and, on a recorded run, the part of
        the footer that says what the automation is currently doing.
        """
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(width, screen_w - 40)
        height = min(height, screen_h - 90)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        self.root.geometry("%dx%d+%d+%d" % (width, height, x, y))

    # ------------------------------------------------------------------ #
    # layout
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        self._build_header()
        self._build_statusbar()

        body = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, minsize=330)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)

    def _build_header(self) -> None:
        head = ttk.Frame(self.root, style="Header.TFrame", padding=(18, 12))
        head.pack(fill="x")

        left = ttk.Frame(head, style="Header.TFrame")
        left.pack(side="left")
        ttk.Label(left, text="Fakturama Image-to-Cash", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="one order image  →  OCR  →  Groq validation  →  saved, verified Order + linked Invoice",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(head, style="Header.TFrame")
        right.pack(side="right")
        for caption, value in (
            ("OCR", (SETTINGS.ocr_engine or "auto")),
            ("LLM", "%s / %s" % (SETTINGS.provider, SETTINGS.model)),
        ):
            box = tk.Frame(right, bg=theme.NAVY_SOFT, padx=10, pady=4)
            box.pack(side="left", padx=(8, 0))
            tk.Label(
                box, text=caption, bg=theme.NAVY_SOFT, fg="#8FAAC6",
                font=(self.fonts.family, 7, "bold"),
            ).pack(anchor="w")
            tk.Label(
                box, text=value, bg=theme.NAVY_SOFT, fg="#FFFFFF",
                font=(self.fonts.family, 9, "bold"),
            ).pack(anchor="w")

    # -- left column ---------------------------------------------------- #
    def _build_left(self, parent) -> None:
        col = ttk.Frame(parent)
        col.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        col.rowconfigure(0, weight=1)
        col.columnconfigure(0, weight=1)

        card = Card(col, title="Source image", fonts=self.fonts)
        card.grid_in(row=0, column=0, sticky="nsew")

        # Packed bottom-up on purpose. The controls must always get their space;
        # only the image preview is allowed to take whatever is left. Packing
        # top-down let a tall thumbnail push the buttons off the card, and an
        # unmapped Tk widget reports a 1x1 rectangle rather than complaining.
        steps = ttk.Frame(card.body, style="Card.TFrame")
        steps.pack(side="bottom", fill="x", pady=(8, 0))

        self.run_button = ttk.Button(
            card.body, text="Run the whole pipeline",
            style="Accent.TButton", command=self.run_all,
        )
        self.run_button.pack(side="bottom", fill="x")

        ttk.Separator(card.body, orient="horizontal").pack(side="bottom", fill="x", pady=12)

        self.stage_items: Dict[int, StageItem] = {}
        for number, name, hint in reversed(STAGES):
            item = StageItem(card.body, number, name, hint, self.fonts)
            item.pack(side="bottom", fill="x", pady=(4, 0))
            self.stage_items[number] = item

        ttk.Label(card.body, text="PIPELINE", style="CardMuted.TLabel").pack(
            side="bottom", anchor="w", pady=(0, 2)
        )
        ttk.Separator(card.body, orient="horizontal").pack(side="bottom", fill="x", pady=12)

        ttk.Button(
            card.body, text="Choose an order image…",
            style="Ghost.TButton", command=self.choose_image,
        ).pack(side="bottom", fill="x")

        self.image_label = ttk.Label(card.body, text="—", style="CardMuted.TLabel")
        self.image_label.pack(side="bottom", anchor="w", pady=(8, 8))

        self.preview = tk.Label(
            card.body, bg=theme.SURFACE_ALT, bd=1, relief="flat",
            text="no image selected", fg=theme.MUTED, font=self.fonts.small,
        )
        self.preview.pack(side="top", fill="both", expand=True)
        self.btn_ocr = ttk.Button(steps, text="1 · OCR", style="Ghost.TButton", command=self.run_ocr)
        self.btn_llm = ttk.Button(steps, text="2 · Groq", style="Ghost.TButton", command=self.run_llm)
        self.btn_ui = ttk.Button(steps, text="3 · Fakturama", style="Ghost.TButton", command=self.run_ui)
        for i, b in enumerate((self.btn_ocr, self.btn_llm, self.btn_ui)):
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 5, 0))
            steps.columnconfigure(i, weight=1)
        self.btn_llm.state(["disabled"])
        self.btn_ui.state(["disabled"])

    # -- right column --------------------------------------------------- #
    def _build_right(self, parent) -> None:
        col = ttk.Frame(parent)
        col.grid(row=0, column=1, sticky="nsew")
        col.rowconfigure(1, weight=1)
        col.columnconfigure(0, weight=1)

        tabs = ttk.Frame(col)
        tabs.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.tab_buttons: Dict[int, tk.Label] = {}
        for number, name, _ in STAGES:
            lab = tk.Label(
                tabs, text="  %d   %s  " % (number, name),
                bg=theme.PAGE, fg=theme.MUTED,
                font=(self.fonts.family, 10, "bold"), padx=6, pady=7, cursor="hand2",
            )
            lab.pack(side="left", padx=(0, 4))
            lab.bind("<Button-1>", lambda _e, n=number: self.show_panel(n))
            self.tab_buttons[number] = lab

        self.panel_host = ttk.Frame(col)
        self.panel_host.grid(row=1, column=0, sticky="nsew")
        self.panel_host.rowconfigure(0, weight=1)
        self.panel_host.columnconfigure(0, weight=1)

        self.panels = {
            1: self._panel_ocr(self.panel_host),
            2: self._panel_llm(self.panel_host),
            3: self._panel_ui(self.panel_host),
        }
        self.show_panel(1)

    def _panel_ocr(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        card = Card(
            frame, title="OCR transcription",
            subtitle="what the Python OCR engine actually read, positions preserved",
            fonts=self.fonts,
        )
        card.grid_in(row=0, column=0, sticky="nsew")

        strip = ttk.Frame(card.body, style="Card.TFrame")
        strip.pack(fill="x", pady=(0, 12))
        self.m_engine = Metric(strip, "engine", "—", self.fonts)
        self.m_words = Metric(strip, "words read", "—", self.fonts)
        self.m_conf = Metric(strip, "mean confidence", "—", self.fonts)
        self.m_low = Metric(strip, "below 0.55", "—", self.fonts)
        for i, m in enumerate((self.m_engine, self.m_words, self.m_conf, self.m_low)):
            m.grid(row=0, column=i, sticky="w", padx=(0 if i == 0 else 28, 0))

        ttk.Label(
            card.body,
            text="The image is upscaled 2× before recognition - at native size the single-digit "
                 "quantities were dropped silently, and a dropped quantity is a wrong invoice.",
            style="CardMuted.TLabel", wraplength=780, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self.ocr_panel = MonoPanel(card.body, self.fonts, wrap="none", height=22)
        self.ocr_panel.pack(fill="both", expand=True)
        return frame

    def _panel_llm(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)

        left = Card(
            frame, title="Structured fields",
            subtitle="Groq tool call → typed OrderDoc", fonts=self.fonts,
        )
        left.grid_in(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.fields = ttk.Treeview(
            left.body, columns=("value",), show="tree headings",
            style="Steps.Treeview", selectmode="browse",
        )
        self.fields.heading("#0", text="FIELD", anchor="w")
        self.fields.heading("value", text="VALUE", anchor="w")
        self.fields.column("#0", width=210, stretch=False)
        self.fields.column("value", width=300, anchor="w")
        fvs = ttk.Scrollbar(left.body, orient="vertical", command=self.fields.yview)
        self.fields.configure(yscrollcommand=fvs.set)
        fvs.pack(side="right", fill="y")
        self.fields.pack(fill="both", expand=True)
        self.fields.tag_configure("group", font=(self.fonts.family, 9, "bold"))
        self.fields.tag_configure("fixed", foreground=theme.WARN)

        right = Card(
            frame, title="Validation",
            subtitle="arithmetic in Decimal, not floats", fonts=self.fonts,
        )
        right.grid_in(row=0, column=1, sticky="nsew")
        self.checks = MonoPanel(right.body, self.fonts, wrap="word", height=26)
        self.checks.pack(fill="both", expand=True)
        return frame

    def _panel_ui(self, parent) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=6)
        frame.columnconfigure(1, weight=4)

        left = Card(
            frame, title="Fakturama automation",
            subtitle="every row carries its spec reference", fonts=self.fonts,
        )
        left.grid_in(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.steps = ttk.Treeview(
            left.body, columns=("spec", "step", "status", "s"),
            show="headings", style="Steps.Treeview", selectmode="browse",
        )
        for col, text, width, anchor, stretch in (
            ("spec", "SPEC", 52, "w", False),
            ("step", "STEP", 200, "w", True),
            ("status", "STATUS", 78, "w", False),
            ("s", "SEC", 42, "e", False),
        ):
            self.steps.heading(col, text=text, anchor=anchor)
            self.steps.column(col, width=width, anchor=anchor, stretch=stretch)
        svs = ttk.Scrollbar(left.body, orient="vertical", command=self.steps.yview)
        self.steps.configure(yscrollcommand=svs.set)
        svs.pack(side="right", fill="y")
        self.steps.pack(fill="both", expand=True)
        self.steps.bind("<<TreeviewSelect>>", self._on_step_selected)

        for status, (fg, bg) in theme.STATUS_COLOURS.items():
            self.steps.tag_configure(status, foreground=fg)
        self.steps.tag_configure("stage", font=(self.fonts.family, 9, "bold"),
                                 background=theme.SURFACE_ALT, foreground=theme.NAVY)

        right = Card(frame, title="Step evidence", subtitle="screenshot + detail", fonts=self.fonts)
        right.grid_in(row=0, column=1, sticky="nsew")

        self.shot_label = tk.Label(
            right.body, bg=theme.SURFACE_ALT, fg=theme.MUTED,
            text="select a step to see its screenshot",
            font=self.fonts.small, height=14,
        )
        self.shot_label.pack(fill="both", expand=True)

        self.step_detail = MonoPanel(right.body, self.fonts, wrap="word", height=7)
        self.step_detail.pack(fill="x", pady=(10, 0))

        self.open_button = ttk.Button(
            right.body, text="Open the run folder", style="Ghost.TButton",
            command=self.open_run_dir,
        )
        self.open_button.pack(fill="x", pady=(10, 0))
        self.open_button.state(["disabled"])
        return frame

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(18, 6, 18, 10))
        bar.pack(side="bottom", fill="x")
        self.status = ttk.Label(bar, text="Ready.", style="Muted.TLabel")
        self.status.pack(side="left")
        self.progress = ttk.Progressbar(
            bar, mode="determinate", length=230, style="Stage.Horizontal.TProgressbar",
        )
        self.progress.pack(side="right")

    # ------------------------------------------------------------------ #
    # navigation / small UI helpers
    # ------------------------------------------------------------------ #
    def show_panel(self, number: int) -> None:
        for n, panel in self.panels.items():
            panel.grid_forget()
            lab = self.tab_buttons[n]
            if n == number:
                lab.configure(bg=theme.SURFACE, fg=theme.NAVY)
            else:
                lab.configure(bg=theme.PAGE, fg=theme.MUTED)
        self.panels[number].grid(row=0, column=0, sticky="nsew")

    def say(self, message: str) -> None:
        self.status.configure(text=message)

    def _busy(self, busy: bool) -> None:
        state = ["disabled"] if busy else ["!disabled"]
        self.run_button.state(state)
        self.btn_ocr.state(state)
        if not busy:
            self.btn_llm.state(["!disabled"] if self.ocr_layout else ["disabled"])
            self.btn_ui.state(["!disabled"] if self.doc is not None else ["disabled"])
        else:
            self.btn_llm.state(["disabled"])
            self.btn_ui.state(["disabled"])
        if busy:
            self.progress.configure(mode="indeterminate")
            self.progress.start(14)
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")

    # ------------------------------------------------------------------ #
    # image
    # ------------------------------------------------------------------ #
    def choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an order image",
            initialdir=str(DEFAULT_IMAGE.parent if DEFAULT_IMAGE.exists() else REPO_ROOT),
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("All files", "*.*")],
        )
        if path:
            self._set_image(Path(path))

    def _set_image(self, path: Path) -> None:
        self.image_path = path
        self.image_label.configure(text="%s  ·  %.0f KB" % (path.name, path.stat().st_size / 1024))
        self._show_thumbnail(path)

        # a new image invalidates everything downstream
        self.ocr_result = None
        self.ocr_layout = ""
        self.raw = None
        self.doc = None
        self.ocr_panel.clear()
        self.checks.clear()
        self.fields.delete(*self.fields.get_children())
        self.steps.delete(*self.steps.get_children())
        for item in self.stage_items.values():
            item.set_status("idle")
        self.btn_llm.state(["disabled"])
        self.btn_ui.state(["disabled"])
        self.show_panel(1)
        self.say("%s loaded. Run the pipeline when ready." % path.name)
        # The first fit happens before the window is laid out, so the preview
        # still reports its minimum size; fit it again once it has a real one.
        self.root.after(350, lambda: self._show_thumbnail(path))

    def _show_thumbnail(self, path: Path) -> None:
        try:
            from PIL import Image, ImageTk

            img = Image.open(path)
            self.root.update_idletasks()
            box = (
                max(200, self.preview.winfo_width() - 8),
                max(200, self.preview.winfo_height() - 8),
            )
            img.thumbnail(box)
            self._thumb_img = ImageTk.PhotoImage(img)
            self.preview.configure(image=self._thumb_img, text="", width=0, height=0)
        except Exception as exc:
            self.preview.configure(image="", text="preview unavailable\n%s" % exc)

    # ------------------------------------------------------------------ #
    # running
    # ------------------------------------------------------------------ #
    def _start(self, target, *args) -> bool:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Busy", "A stage is already running.")
            return False
        if self.image_path is None:
            messagebox.showwarning("No image", "Choose an order image first.")
            return False
        self._busy(True)
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()
        return True

    def run_ocr(self) -> None:
        self._start(self._work_ocr, False)

    def run_llm(self) -> None:
        self._start(self._work_llm, False)

    def run_ui(self) -> None:
        self._start(self._work_ui)

    def run_all(self) -> None:
        self._start(self._work_all)

    # -- worker bodies (NO widget access below this line) ---------------- #
    def _post(self, kind: str, payload: Any = None) -> None:
        self.q.put((kind, payload))

    def _work_all(self) -> None:
        if self._work_ocr(chain=True) and self._work_llm(chain=True):
            self._work_ui()

    def _work_ocr(self, chain: bool = False) -> bool:
        from ..extract.ocr import low_confidence_words, read_image

        self._post("stage", (1, "running", "reading the image…"))
        self._post("status", "Stage 1 - running OCR over %s" % self.image_path.name)
        try:
            result = read_image(self.image_path, engine=SETTINGS.ocr_engine)
        except Exception as exc:
            self._post("stage", (1, "failed", str(exc)[:60]))
            self._post("error", ("OCR", exc, traceback.format_exc()))
            return False

        self._post(
            "ocr-done",
            {
                "engine": result.engine,
                "words": len(result.words),
                "confidence": result.mean_confidence(),
                "low": len(low_confidence_words(result)),
                "layout": result.layout_text(),
                "plain": result.plain_text(),
                "result": result,
            },
        )
        if not chain:
            self._post("idle", None)
        return True

    def _work_llm(self, chain: bool = False) -> bool:
        from ..extract import llm, pipeline
        from ..extract.validate import reconcile

        self._post("stage", (2, "running", "%s / %s" % (SETTINGS.provider, SETTINGS.model)))
        self._post("status", "Stage 2 - structuring the transcription with %s" % SETTINGS.model)
        try:
            raw = llm.structure(self.ocr_layout)
            doc = pipeline.normalise(raw)
            problems = reconcile(doc, strict=False)
        except Exception as exc:
            self._post("stage", (2, "failed", str(exc)[:60]))
            self._post("error", ("Groq validation", exc, traceback.format_exc()))
            return False

        self._post("llm-done", {"raw": raw, "doc": doc, "problems": problems})
        if problems:
            # Reconciliation failing is a stop, not a warning: writing an order
            # whose own arithmetic disagrees is worse than writing none.
            self._post("stage", (2, "failed", "%d reconciliation problem(s)" % len(problems)))
            return False
        if not chain:
            self._post("idle", None)
        return True

    def _work_ui(self) -> None:
        from ..flow.orchestrator import run_flow

        self._post("stage", (3, "running", "driving the live application"))
        self._post("status", "Stage 3 - driving Fakturama")
        try:
            art = run_flow(self.doc, listener=lambda kind, payload: self._post(kind, payload))
            report = json.loads((art.dir / "report.json").read_text(encoding="utf-8"))
            self._post("flow-report", report)
        except Exception as exc:
            self._post("stage", (3, "failed", str(exc)[:60]))
            self._post("error", ("Fakturama automation", exc, traceback.format_exc()))
        self._post("idle", None)

    # ------------------------------------------------------------------ #
    # the only place widgets are touched
    # ------------------------------------------------------------------ #
    def _pump(self) -> None:
        handled = False
        try:
            while True:
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
                handled = True
        except queue.Empty:
            pass
        if handled:
            self._write_state()
        self.root.after(60, self._pump)

    def _handle(self, kind: str, payload: Any) -> None:
        if kind == "status":
            self.say(payload)
        elif kind == "stage":
            number, status, hint = payload
            self.stage_items[number].set_status(status, hint)
            if status == "running":
                self.show_panel(number)
        elif kind == "idle":
            self._busy(False)
        elif kind == "error":
            self._on_error(*payload)
        elif kind == "ocr-done":
            self._on_ocr(payload)
        elif kind == "llm-done":
            self._on_llm(payload)
        elif kind == "run-start":
            self._on_run_start(payload)
        elif kind == "step-begin":
            self._on_step(payload, begin=True)
        elif kind == "step-end":
            self._on_step(payload, begin=False)
        elif kind == "run-end":
            self._on_run_end(payload)
        elif kind == "flow-report":
            self._on_flow_report(payload)

    # -- stage 1 --------------------------------------------------------- #
    def _on_ocr(self, data: Dict[str, Any]) -> None:
        self.ocr_result = data["result"]
        self.ocr_layout = data["layout"]
        self.ocr_plain = data["plain"]

        self.m_engine.set(data["engine"])
        self.m_words.set(str(data["words"]))
        conf = data["confidence"]
        self.m_conf.set("%.2f" % conf, theme.OK if conf >= 0.8 else theme.WARN)
        self.m_low.set(str(data["low"]), theme.OK if data["low"] == 0 else theme.WARN)

        self.ocr_panel.set(data["layout"])
        self.stage_items[1].set_status(
            "ok", "%d words, mean confidence %.2f" % (data["words"], conf)
        )
        self.btn_llm.state(["!disabled"])
        self.say("Stage 1 complete - %d words read by %s." % (data["words"], data["engine"]))

    # -- stage 2 --------------------------------------------------------- #
    def _on_llm(self, data: Dict[str, Any]) -> None:
        doc = data["doc"]
        self.doc = doc
        self.raw = data["raw"]
        problems: List[str] = data["problems"]

        corrections = self._corrections(doc)
        self._fill_fields(doc, corrections)
        self._fill_checks(doc, problems, corrections)

        if problems:
            self.stage_items[2].set_status("failed", "%d reconciliation problem(s)" % len(problems))
            self.say("Stage 2 - the extraction does not reconcile; the UI run is blocked.")
            self.btn_ui.state(["disabled"])
        else:
            self.stage_items[2].set_status(
                "ok",
                "reconciled✓  %d correction(s)" % len(corrections) if corrections else "reconciled✓",
            )
            self.btn_ui.state(["!disabled"])
            self.say("Stage 2 complete - every total reconciles. Ready to drive Fakturama.")

    def _corrections(self, doc) -> Dict[str, str]:
        """Fields whose final value is not literally in the OCR reading.

        This is the honest way to show what the LLM contributed. The OCR read
        the sample's e-mail without its dot; the model returned it correctly,
        and that difference is exactly what a reviewer should be shown rather
        than a flat 'validated' badge.
        """
        hay = _norm(getattr(self, "ocr_plain", "") + self.ocr_layout)
        out: Dict[str, str] = {}
        candidates = [
            ("external_ref", doc.external_ref),
            ("company", doc.company),
            ("contact", doc.contact_name),
            ("alias", doc.alias),
            ("email", doc.email),
            ("phone", doc.phone),
            ("billing.street", doc.billing.street),
            ("billing.zip", doc.billing.zip),
            ("delivery.street", doc.delivery.street),
            ("delivery.zip", doc.delivery.zip),
            ("payment_method", doc.payment_method),
            ("net_total", doc.net_total),
            ("vat_total", doc.vat_total),
            ("gross_total", doc.gross_total),
        ]
        for item in doc.items:
            candidates += [
                ("item %s sku" % item.sku, item.sku),
                ("item %s description" % item.sku, item.description),
                ("item %s qty" % item.sku, item.qty),
                ("item %s unit net" % item.sku, item.unit_net),
            ]
        for label, value in candidates:
            if value in (None, "") or _norm(value) in hay:
                continue
            out[label] = str(value)
        return out

    def _fill_fields(self, doc, corrections: Dict[str, str]) -> None:
        self.fields.delete(*self.fields.get_children())

        def group(name: str) -> str:
            return self.fields.insert("", "end", text=name, open=True, tags=("group",))

        def row(parent: str, label: str, value: Any, key: str = "") -> None:
            fixed = (key or label) in corrections
            self.fields.insert(
                parent, "end",
                text="   " + label,
                values=(("%s   ● corrected" % value) if fixed else value,),
                tags=("fixed",) if fixed else (),
            )

        g = group("Order")
        row(g, "Date", doc.order_date)
        row(g, "Cust.Ref.", doc.external_ref, "external_ref")
        row(g, "Currency", doc.currency)

        g = group("Debtor")
        row(g, "Company", doc.company, "company")
        row(g, "Contact", doc.contact_name, "contact")
        row(g, "Alias", doc.alias, "alias")
        row(g, "E-mail", doc.email, "email")
        row(g, "Telephone", doc.phone, "phone")

        g = group("Addresses")
        row(g, "Billing", "%s, %s %s, %s" % (
            doc.billing.street, doc.billing.zip, doc.billing.city, doc.billing.country))
        row(g, "Delivery", "%s, %s %s, %s%s" % (
            doc.delivery.street, doc.delivery.zip, doc.delivery.city, doc.delivery.country,
            "  (same as billing)" if doc.delivery_same_as_billing else ""))

        g = group("Payment")
        row(g, "Method", doc.payment_method, "payment_method")
        row(g, "Fakturama code", doc.payment_code)
        row(g, "Paid status", doc.paid_status)
        row(g, "Payment date", doc.payment_date or "—")

        g = group("Items")
        for i, it in enumerate(doc.items, 1):
            node = self.fields.insert(
                g, "end", text="   %d. %s" % (i, it.sku), open=True,
                values=("%s  ×%s @ %s  −%s%%  VAT %s%%  = %s"
                        % (it.description, it.qty, it.unit_net, it.discount_pct,
                           it.vat_pct, it.computed_line_net),),
            )
            self.fields.insert(
                node, "end", text="      product gross (3.9)",
                values=("%s × (1 + %s/100) = %s"
                        % (it.unit_net, it.vat_pct, it.product_gross_price),),
            )
            self.fields.insert(
                node, "end", text="      VAT record (3.4)", values=(it.vat_name,),
            )

        g = group("Totals")
        row(g, "Total Net", doc.net_total, "net_total")
        row(g, "VAT", doc.vat_total, "vat_total")
        row(g, "Total", doc.gross_total, "gross_total")

    def _fill_checks(self, doc, problems: List[str], corrections: Dict[str, str]) -> None:
        from ..models import money

        self.checks.clear()
        p = self.checks

        p.append("RECONCILIATION\n", "head")
        p.append("Recomputed from the extracted values, in Decimal.\n\n", "dim")

        for it in doc.items:
            ok = money(it.computed_line_net) == money(it.line_net)
            p.append("  %s  " % ("✓" if ok else "✗"), "ok" if ok else "err")
            p.append("line %s: %s × %s × (1 − %s/100) = %s   image says %s\n"
                     % (it.sku, it.qty, it.unit_net, it.discount_pct,
                        it.computed_line_net, money(it.line_net)))

        net = money(sum((i.computed_line_net for i in doc.items), Decimal(0)))
        vat = money(sum((i.computed_line_net * i.vat_pct / Decimal(100) for i in doc.items), Decimal(0)))
        for label, computed, stated in (
            ("net total", net, money(doc.net_total)),
            ("VAT total", vat, money(doc.vat_total)),
            ("gross total", money(net + vat), money(doc.gross_total)),
        ):
            ok = abs(computed - stated) <= Decimal("0.01")
            p.append("  %s  " % ("✓" if ok else "✗"), "ok" if ok else "err")
            p.append("%s: computed %s   image says %s\n" % (label, computed, stated))

        paid_ok = (doc.is_paid and doc.payment_date is not None) or (
            not doc.is_paid and doc.payment_date is None)
        p.append("  %s  " % ("✓" if paid_ok else "✗"), "ok" if paid_ok else "err")
        p.append("paid status %s is consistent with the payment date\n" % doc.paid_status)

        code_ok = bool(doc.payment_code)
        p.append("  %s  " % ("✓" if code_ok else "✗"), "ok" if code_ok else "err")
        p.append("payment method %r maps to %r (spec 2.10.4)\n" % (doc.payment_method, doc.payment_code))

        p.append("\nWHAT THE MODEL CORRECTED\n", "head")
        if corrections:
            p.append("These values are not in the OCR reading character for character,\n"
                     "so the model repaired them. Each is worth a glance.\n\n", "dim")
            for label, value in corrections.items():
                p.append("  ● ", "warn")
                p.append("%-22s %s\n" % (label, value))
        else:
            p.append("\n  ", "")
            p.append("✓", "ok")
            p.append("  every extracted value appears literally in the OCR reading.\n")

        p.append("\nVERDICT\n", "head")
        if problems:
            for prob in problems:
                p.append("  ✗ ", "err")
                p.append("%s\n" % prob)
            p.append("\nThe run is blocked. Writing an order whose own arithmetic\n"
                     "disagrees is worse than writing none.\n", "err")
        else:
            p.append("\n  ✓ ", "ok")
            p.append("The extraction reconciles exactly. Fakturama may be driven.\n")

    # -- stage 3 --------------------------------------------------------- #
    def _on_run_start(self, payload: Dict[str, Any]) -> None:
        self.run_dir = Path(payload["dir"])
        self.shots.clear()
        self.steps.delete(*self.steps.get_children())
        self.open_button.state(["!disabled"])
        self._stage_header("Stage 0 - launch")
        self.say("Run folder: %s" % self.run_dir)

    _STAGE_TITLES = {
        "0": "Stage 0 - launch and prerequisites",
        "1": "Stage 1 - order header (spec 1.x)",
        "2": "Stage 2 - debtor (spec 2.x)",
        "3": "Stage 3 - products and item lines (spec 3.x)",
        "4": "Stage 4 - complete and save the order (spec 4.x)",
        "5": "Stage 5 - linked invoice (spec 5.x)",
    }

    def _stage_header(self, text: str) -> None:
        self.steps.insert("", "end", values=("", text, "", ""), tags=("stage",))

    def _on_step(self, rec: Dict[str, Any], begin: bool) -> None:
        iid = "step-%d" % rec["index"]
        spec = str(rec["spec"])
        stage = spec.split(".")[0]

        if begin:
            if stage != getattr(self, "_current_stage", None) and stage in self._STAGE_TITLES:
                self._current_stage = stage
                self._stage_header(self._STAGE_TITLES[stage])
            self.steps.insert(
                "", "end", iid=iid,
                values=(spec, rec["title"], "running…", ""),
                tags=("running",),
            )
            self.steps.see(iid)
            self.say("[%s] %s" % (spec, rec["title"]))
            return

        if not self.steps.exists(iid):
            return
        status = rec["status"]
        label = status
        if rec.get("attempts", 1) > 1:
            label = "%s (%d tries)" % (status, rec["attempts"])
        self.steps.item(
            iid,
            values=(spec, rec["title"], label, "%.1f" % rec["seconds"]),
            tags=(status,),
        )
        if rec.get("screenshot") and self.run_dir is not None:
            self.shots[iid] = str(self.run_dir / "screenshots" / rec["screenshot"])
        self.steps.set(iid, "step", rec["title"])
        self._step_details = getattr(self, "_step_details", {})
        self._step_details[iid] = rec
        self.steps.see(iid)

    def _on_step_selected(self, _event=None) -> None:
        sel = self.steps.selection()
        if not sel:
            return
        iid = sel[0]
        rec = getattr(self, "_step_details", {}).get(iid)
        self.step_detail.clear()
        if rec:
            self.step_detail.append("[%s] %s\n" % (rec["spec"], rec["title"]), "head")
            self.step_detail.append(
                "%s in %.2fs, %d attempt(s)\n\n"
                % (rec["status"], rec["seconds"], rec.get("attempts", 1)), "dim")
            if rec.get("detail"):
                self.step_detail.append("%s\n" % rec["detail"])
            for note in rec.get("recovered", []):
                self.step_detail.append("\n↺ %s\n" % note, "warn")
        self._show_shot(self.shots.get(iid))

    def _show_shot(self, path: Optional[str]) -> None:
        if not path or not Path(path).exists():
            self.shot_label.configure(image="", text="no screenshot for this step")
            self._preview_img = None
            return
        try:
            from PIL import Image, ImageTk

            img = Image.open(path)
            img.thumbnail((self.shot_label.winfo_width() or 420, 320))
            self._preview_img = ImageTk.PhotoImage(img)
            self.shot_label.configure(image=self._preview_img, text="")
        except Exception as exc:
            self.shot_label.configure(image="", text="could not show the screenshot: %s" % exc)

    def _on_run_end(self, payload: Dict[str, Any]) -> None:
        status = payload["status"]
        nice = {"ok": "ok", "stopped-early": "ok"}.get(status, status)
        self.stage_items[3].set_status(nice, "run %s" % status)
        self.say("Run finished: %s  ·  %s" % (status, payload["dir"]))

    def _on_flow_report(self, report: Dict[str, Any]) -> None:
        steps = report.get("steps", [])
        done = sum(1 for s in steps if s["status"] == "ok")
        tiers = report.get("grounding_tier_usage", {})
        self.step_detail.clear()
        self.step_detail.append("RUN REPORT\n", "head")
        self.step_detail.append("status      %s\n" % report["status"],
                                "ok" if report["status"] == "ok" else "err")
        self.step_detail.append("steps       %d of %d ok\n" % (done, len(steps)))
        self.step_detail.append("grounding   %s\n" % (
            ", ".join("%s %d" % (k, v) for k, v in tiers.items()) or "—"))
        state = report.get("flow_state", {})
        if state.get("order_number") or state.get("invoice_number"):
            self.step_detail.append(
                "documents   Order %s / Invoice %s\n"
                % (state.get("order_number") or "?", state.get("invoice_number") or "?"))

    def load_report(self, report_path: Path) -> None:
        """Show a finished run's step list without repeating the run.

        Useful on its own - a run takes twenty minutes and its report is the
        thing anyone actually wants to look at afterwards - and it is what makes
        the stage-3 screenshots reproducible without creating a second Order.
        """
        report_path = Path(report_path)
        if report_path.is_dir():
            report_path = report_path / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))

        self._on_run_start({"dir": str(report_path.parent)})
        for step in report.get("steps", []):
            self._on_step(step, begin=True)
            self._on_step(step, begin=False)
        self._on_run_end({"status": report.get("status", "?"), "dir": str(report_path.parent)})
        self._on_flow_report(report)

        source = report.get("source") or {}
        for number, status, hint in (
            (1, "ok", "from the saved run"),
            (2, "ok", "from the saved run"),
            (3, "ok" if report.get("status") == "ok" else report.get("status", "?"),
             "run %s" % report.get("status", "?")),
        ):
            self.stage_items[number].set_status(status, hint)
        self.show_panel(3)
        self.say("Loaded %s  ·  %s" % (report_path.parent.name, report.get("status", "?")))
        if source.get("external_ref"):
            self.say("Loaded %s  ·  %s  ·  Cust.Ref. %s"
                     % (report_path.parent.name, report.get("status", "?"), source["external_ref"]))

    def select_step(self, spec: str) -> None:
        """Select the first step with this spec reference. Used by the demo capture."""
        for iid, rec in getattr(self, "_step_details", {}).items():
            if str(rec.get("spec")) == spec:
                self.steps.selection_set(iid)
                self.steps.see(iid)
                self._on_step_selected()
                return

    def open_run_dir(self) -> None:
        if self.run_dir is None:
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(self.run_dir)])
            else:  # pragma: no cover
                subprocess.Popen(["xdg-open", str(self.run_dir)])
        except Exception as exc:
            messagebox.showerror("Could not open", str(exc))

    # ------------------------------------------------------------------ #
    def _on_error(self, stage: str, exc: BaseException, tb: str) -> None:
        self._busy(False)
        self.say("%s failed: %s" % (stage, exc))
        messagebox.showerror("%s failed" % stage, "%s\n\n%s" % (exc, tb[-1500:]))

    def _state_tick(self) -> None:
        """Republish the state once a second while a demo driver is watching.

        `_pump` only publishes when a message arrives, and the last message of
        a stage can land before the worker thread has actually exited - so a
        watcher would see `busy` stuck true with nothing left to change it.
        """
        self._write_state()
        self.root.after(1000, self._state_tick)

    def _write_state(self) -> None:
        """Publish what the window is doing, for `scripts/capture_demo.py`.

        Only written when a geometry file was asked for, so an ordinary run of
        the front end touches nothing outside `out/run-*`.
        """
        if self.geometry_file is None:
            return
        path = self.geometry_file.with_name(self.geometry_file.stem + "-state.json")
        try:
            path.write_text(json.dumps({
                "busy": self.busy(),
                "ocr_done": bool(self.ocr_layout),
                "llm_done": self.doc is not None,
                "run_dir": str(self.run_dir) if self.run_dir else "",
                "stages": {n: item.badge.cget("text") for n, item in self.stage_items.items()},
            }), encoding="utf-8")
        except Exception:
            pass

    def _write_geometry(self, attempt: int = 1) -> None:
        self.root.update_idletasks()
        # Until the window has actually been mapped and laid out, every widget
        # reports 1x1. Publishing that would send the demo driver's clicks to
        # the top-left corner, so wait for real sizes.
        if self.run_button.winfo_width() <= 1 and attempt < 40:
            self.root.after(250, lambda: self._write_geometry(attempt + 1))
            return
        widgets = {
            "choose": None,
            "run_all": self.run_button,
            "ocr": self.btn_ocr,
            "llm": self.btn_llm,
            "ui": self.btn_ui,
        }
        out = {"window": self.root.winfo_id()}
        for name, widget in widgets.items():
            if widget is None:
                continue
            out[name] = [
                widget.winfo_rootx(), widget.winfo_rooty(),
                widget.winfo_width(), widget.winfo_height(),
            ]
        try:
            self.geometry_file.parent.mkdir(parents=True, exist_ok=True)
            self.geometry_file.write_text(json.dumps(out, indent=2), encoding="utf-8")
        except Exception as exc:
            self.say("could not write the geometry file: %s" % exc)

    def busy(self) -> bool:
        """True while a stage is running - the demo driver waits on this."""
        return self.worker is not None and self.worker.is_alive()

    def run(self) -> None:
        self.root.mainloop()


def main(
    image: Optional[str] = None,
    auto_run: bool = False,
    geometry_file: Optional[str] = None,
    report: Optional[str] = None,
    select: Optional[str] = None,
) -> None:
    from ..logging_setup import setup

    setup()
    app = App(
        Path(image) if image else None,
        auto_run=auto_run,
        geometry_file=Path(geometry_file) if geometry_file else None,
    )
    if report:
        app.root.after(500, lambda: app.load_report(Path(report)))
        if select:
            app.root.after(1200, lambda: app.select_step(select))
    app.run()


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else None)
