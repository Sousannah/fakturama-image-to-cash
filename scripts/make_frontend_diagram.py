"""Draw `docs/diagrams/08_frontend.png` - how the desktop window sits on the core.

    python scripts/make_frontend_diagram.py

Matches the visual language of the other diagrams: rounded boxes, a muted
palette, one idea per row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "diagrams" / "08_frontend.png"

BLUE_F, BLUE_E = "#DAE8F7", "#2E7DD1"
GREEN_F, GREEN_E = "#DCEEE0", "#2E7D4F"
GREY_F, GREY_E = "#EAEDF1", "#8794A3"
AMBER_F, AMBER_E = "#FBEDD7", "#C98A1E"
ARROW = "#66788A"


def box(ax, x, y, w, h, text, fill, edge, bold=False, size=10.5):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
        facecolor=fill, edgecolor=edge, linewidth=1.6,
    ))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=size, fontweight="bold" if bold else "normal", color="#16202C",
            linespacing=1.45)


def arrow(ax, start, end, style="-|>", dashed=False):
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle=style, mutation_scale=15,
        color=ARROW, linewidth=1.5, linestyle="--" if dashed else "-",
        shrinkA=2, shrinkB=2,
    ))


def main() -> int:
    fig, ax = plt.subplots(figsize=(14, 7.4), dpi=120)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(0.012, 0.955, "THE DESKTOP FRONT END", fontsize=10,
            fontweight="bold", color="#2E7DD1")
    ax.text(0.012, 0.915, "f2c.gui - three panels over the same core the CLI drives",
            fontsize=10.5, color="#697A8D")

    # --- the window ------------------------------------------------------- #
    ax.add_patch(FancyBboxPatch(
        (0.03, 0.44), 0.94, 0.42, boxstyle="round,pad=0.008,rounding_size=0.015",
        facecolor="#F7F9FC", edgecolor="#C9D5E2", linewidth=1.4,
    ))
    ax.text(0.045, 0.825, "run_gui.py  /  f2c gui", fontsize=9.5,
            fontweight="bold", color="#0E2338")

    box(ax, 0.055, 0.53, 0.26, 0.23,
        "1  Read the image\n\nthe OCR transcription,\npositions preserved\nengine / words / confidence",
        BLUE_F, BLUE_E)
    box(ax, 0.372, 0.53, 0.26, 0.23,
        "2  Validate with Groq\n\nthe typed fields, the\nreconciliation in Decimal,\nand what the model corrected",
        BLUE_F, BLUE_E)
    box(ax, 0.689, 0.53, 0.26, 0.23,
        "3  Drive Fakturama\n\nthe step list, live -\nspec clause, status, tries,\nscreenshot per step",
        BLUE_F, BLUE_E)

    arrow(ax, (0.318, 0.645), (0.369, 0.645))
    arrow(ax, (0.635, 0.645), (0.686, 0.645))

    # --- the boundary ------------------------------------------------------ #
    ax.plot([0.03, 0.97], [0.40, 0.40], color="#C9D5E2", linewidth=1.2, linestyle=(0, (5, 4)))
    ax.text(0.97, 0.418, "the window reaches the core through two calls and one callback",
            ha="right", fontsize=9, color="#8794A3", style="italic")

    # --- the core ---------------------------------------------------------- #
    box(ax, 0.055, 0.20, 0.26, 0.13,
        "extract.ocr.read_image\nextract.llm.structure\nextract.validate.reconcile",
        GREEN_F, GREEN_E, size=9.5)
    box(ax, 0.372, 0.20, 0.26, 0.13,
        "flow.orchestrator.run_flow\n(doc, listener=...)",
        GREEN_F, GREEN_E, size=9.5)
    box(ax, 0.689, 0.20, 0.26, 0.13,
        "artifacts.RunArtifacts.emit\nstep-begin / step-end /\nscreenshot / run-end",
        AMBER_F, AMBER_E, size=9.5)

    arrow(ax, (0.185, 0.53), (0.185, 0.335))
    arrow(ax, (0.502, 0.53), (0.502, 0.335))
    arrow(ax, (0.819, 0.335), (0.819, 0.53))

    box(ax, 0.372, 0.045, 0.26, 0.095, "Fakturama 2.2.0", GREY_F, GREY_E, bold=True)
    arrow(ax, (0.502, 0.195), (0.502, 0.145))

    ax.text(0.055, 0.115,
            "Everything slow runs on a worker thread.\n"
            "It reaches the window only by putting a\n"
            "message on a queue.",
            fontsize=9, color="#697A8D", va="top", linespacing=1.5)
    ax.text(0.689, 0.115,
            "App._pump drains that queue on the main\n"
            "loop and is the only place in the module\n"
            "that touches a widget.",
            fontsize=9, color="#697A8D", va="top", linespacing=1.5)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
