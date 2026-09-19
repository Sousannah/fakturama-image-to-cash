"""Draw `docs/diagrams/07_write_value.png` - how one value is written and proved.

    python scripts/make_write_diagram.py

This is the loop behind every field the automation fills. It exists because of
the bug that shaped the whole write path: `ValuePattern.SetValue` updates an SWT
widget's displayed text, and the read-back agrees, but no modify listener fires -
so the value looks correct on screen and is stored as NULL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "diagrams" / "07_write_value.png"

BLUE_F, BLUE_E = "#DAE8F7", "#2E7DD1"
GREEN_F, GREEN_E = "#DCEEE0", "#2E7D4F"
AMBER_F, AMBER_E = "#FBEDD7", "#C98A1E"
RED_F, RED_E = "#FBE3E3", "#C32B2B"
INK, MUTED, ARROW = "#16202C", "#697A8D", "#66788A"

STEPS = [
    ("1", "Bring Fakturama\nto the front", "a physical click lands on\nwhatever is on top"),
    ("2", "Resolve the field", "four tiers, no fixed\ncoordinate anywhere"),
    ("3", "Select all, then type\nwith real key events", "not SetValue — see below"),
    ("4", "Read the field back", "through whichever pattern\nthe widget actually exposes"),
]


def box(ax, x, y, w, h, fill, edge, lw=1.6, radius=0.018, dashed=False):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.008,rounding_size=%s" % radius,
        facecolor=fill, edgecolor=edge, linewidth=lw,
        linestyle="--" if dashed else "-"))


def arrow(ax, start, end, colour=ARROW, style="-|>", dashed=False, rad=0.0):
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle=style, mutation_scale=14, color=colour,
        linewidth=1.5, linestyle="--" if dashed else "-",
        connectionstyle="arc3,rad=%s" % rad, shrinkA=2, shrinkB=2))


def main() -> int:
    fig, ax = plt.subplots(figsize=(15, 6.6), dpi=118)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(0.012, 0.955, "WRITING ONE VALUE", fontsize=10.5,
            fontweight="bold", color=BLUE_E)
    ax.text(0.012, 0.912,
            "Every field the automation fills goes through this. A write is not "
            "finished until it has been read back.",
            fontsize=11, color=MUTED)

    # --- the four steps --------------------------------------------------- #
    width, height, y = 0.212, 0.30, 0.50
    xs = [0.030, 0.278, 0.526, 0.774]
    for (n, title, note), x in zip(STEPS, xs):
        box(ax, x, y, width, height, BLUE_F, BLUE_E)
        ax.text(x + 0.018, y + height - 0.055, n, fontsize=13,
                fontweight="bold", color=BLUE_E, va="center")
        ax.text(x + width / 2, y + height * 0.60, title, fontsize=11.5,
                fontweight="bold", color=INK, ha="center", va="center",
                linespacing=1.4)
        ax.text(x + width / 2, y + height * 0.20, note, fontsize=9,
                color=MUTED, ha="center", va="center", linespacing=1.4)

    for x in xs[:-1]:
        arrow(ax, (x + width, y + height / 2), (x + width + 0.036, y + height / 2))

    # --- the decision ----------------------------------------------------- #
    # Three outcomes, right to left, each sized to its own label so the text
    # stays inside its box and the arrows do not run through it.
    outcomes = [
        (0.774, 0.212, GREEN_F, GREEN_E, "#17864A", "matches  →  accept"),
        (0.512, 0.232, AMBER_F, AMBER_E, "#B4700B", "no match  →  retry the other way"),
        (0.230, 0.252, RED_F, RED_E, "#C32B2B", "still no match  →  fail the run loudly"),
    ]
    for x, w, fill, edge, colour, label in outcomes:
        box(ax, x, 0.215, w, 0.12, fill, edge)
        ax.text(x + w / 2, 0.275, label, fontsize=10.5, fontweight="bold",
                color=colour, ha="center", va="center")

    arrow(ax, (0.880, y - 0.004), (0.880, 0.340), GREEN_E)
    arrow(ax, (0.770, 0.275), (0.748, 0.275), AMBER_E)
    arrow(ax, (0.508, 0.275), (0.486, 0.275), RED_E)

    # --- the reason -------------------------------------------------------- #
    box(ax, 0.030, 0.035, 0.956, 0.135, "#F7F9FC", "#C9D5E2", lw=1.3,
        radius=0.014, dashed=True)
    ax.text(0.052, 0.128,
            "Why real key events, and why the read-back is not optional",
            fontsize=10, fontweight="bold", color=INK, va="center")
    ax.text(0.052, 0.074,
            "ValuePattern.SetValue updates an SWT widget's displayed text and the read-back agrees — "
            "but no modify listener fires. Company, Alias and Country read back correctly, saved without\n"
            "complaint, and landed in the database as NULL. That is why the database oracle exists: only a "
            "second, independent check could have caught it.",
            fontsize=9.5, color=MUTED, va="center", linespacing=1.5)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print("wrote %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
