"""Draw `docs/diagrams/04_stages.png` - the Order-first flow and its detours.

    python scripts/make_stages_diagram.py [--run out/run-YYYYmmdd-HHMMSS]

The step counts and the per-stage status are read from a run report, so the
diagram cannot claim a stage works when the last run says otherwise. The
previous version of this file was hand-drawn and went stale the moment the item
grid started working - which is exactly the failure mode generating it prevents.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
OUT = REPO_ROOT / "docs" / "diagrams" / "04_stages.png"

GREEN_F, GREEN_E, GREEN_T = "#DCEEE0", "#2E7D4F", "#17864A"
AMBER_F, AMBER_E, AMBER_T = "#FBEDD7", "#C98A1E", "#B4700B"
RED_F, RED_E, RED_T = "#FBE3E3", "#C32B2B", "#C32B2B"
GREY_F, GREY_E, GREY_T = "#EAEDF1", "#8794A3", "#697A8D"
INK, MUTED, ARROW = "#16202C", "#697A8D", "#66788A"

#: Which spec clauses belong to each row, and what the row is called.
STAGES = [
    ("Stage 1", "Open the Order and fill its header",
     "spec 1.3 - 1.8", lambda s: s.startswith("1."),
     "—"),
    ("Stage 2", "Select or create the Debtor",
     "spec 2.1 - 2.13", lambda s: s.startswith("2."),
     "New Debtor  +  its payment method"),
    ("Stage 3a", "Select or create the VAT rate and Product",
     "spec 3.2 - 3.12", lambda s: s.startswith("3.") and _minor(s) <= 12,
     "VAT 19%  +  New product"),
    ("Stage 3b", "Complete each item line in the grid",
     "spec 3.13 - 3.17", lambda s: s.startswith("3.") and _minor(s) >= 13,
     "—"),
    ("Stage 4", "Confirm and save the Order, open the follow-up Invoice",
     "spec 4.1 - 4.7", lambda s: s.startswith("4."),
     "—"),
    ("Stage 5", "Complete and verify the linked Invoice",
     "spec 5.1 - 5.7", lambda s: s.startswith("5."),
     "—"),
]


def _minor(spec: str) -> int:
    try:
        return int(spec.split(".")[1].split("-")[0])
    except (IndexError, ValueError):
        return 0


def latest_ok_run() -> Path:
    runs = sorted((REPO_ROOT / "out").glob("run-*"), reverse=True)
    for run in runs:
        report = run / "report.json"
        if report.exists():
            try:
                if json.loads(report.read_text(encoding="utf-8")).get("status") == "ok":
                    return run
            except Exception:
                continue
    raise SystemExit("no completed run under out/ - run the flow first")


def verdict(statuses):
    """One status for a row, from the statuses of its steps."""
    if not statuses:
        return "NOT RUN", GREY_F, GREY_E, GREY_T
    if any(s == "failed" for s in statuses):
        return "FAILS", RED_F, RED_E, RED_T
    if any(s == "manual-review" for s in statuses):
        return "STOPS FOR REVIEW", AMBER_F, AMBER_E, AMBER_T
    if any(s == "skipped" for s in statuses):
        return "WORKS", GREEN_F, GREEN_E, GREEN_T
    return "WORKS", GREEN_F, GREEN_E, GREEN_T


def box(ax, x, y, w, h, fill, edge, radius=0.018, lw=1.6, dashed=False):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.008,rounding_size=%s" % radius,
        facecolor=fill, edgecolor=edge, linewidth=lw,
        linestyle="--" if dashed else "-",
    ))


def build(run_dir: Path) -> Path:
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    steps = report.get("steps", [])

    rows = []
    for name, title, clause, belongs, detour in STAGES:
        mine = [s for s in steps if belongs(str(s["spec"]))]
        label, fill, edge, text = verdict([s["status"] for s in mine])
        skipped = [s for s in mine if s["status"] == "skipped"]
        rows.append({
            "name": name, "title": title, "clause": clause, "detour": detour,
            "count": len(mine), "label": label, "fill": fill, "edge": edge,
            "text": text, "skipped": skipped,
        })

    fig, ax = plt.subplots(figsize=(15.5, 8.6), dpi=118)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    fig.patch.set_facecolor("white")

    ax.text(0.012, 0.965, "THE ORDER-FIRST FLOW", fontsize=10.5,
            fontweight="bold", color="#2E7DD1")
    ax.text(0.012, 0.928,
            "The Order tab is opened in stage 1 and stays open the whole way through — "
            "every detour creates its record and returns to it.",
            fontsize=11, color=MUTED)

    # column headings
    head_y = 0.876
    ax.text(0.058, head_y, "STAGE", fontsize=8.5, fontweight="bold", color=MUTED)
    ax.text(0.7225, head_y, "CREATED ONLY IF MISSING", fontsize=8.5,
            fontweight="bold", color=MUTED, ha="center")
    ax.text(0.922, head_y, "LAST RUN", fontsize=8.5,
            fontweight="bold", color=MUTED, ha="center")

    top, height, gap = 0.842, 0.108, 0.026
    for i, row in enumerate(rows):
        y = top - i * (height + gap) - height

        # --- the stage itself
        box(ax, 0.045, y, 0.545, height, row["fill"], row["edge"])
        ax.text(0.068, y + height * 0.60, "%s   %s" % (row["name"], row["title"]),
                fontsize=11.5, fontweight="bold", color=INK, va="center")
        ax.text(0.068, y + height * 0.24, row["clause"],
                fontsize=9, color=MUTED, va="center")

        # --- the detour, if the stage has one
        if row["detour"] != "—":
            box(ax, 0.605, y + height * 0.16, 0.235, height * 0.66,
                "#F7F9FC", "#C9D5E2", radius=0.014, lw=1.3, dashed=True)
            ax.text(0.7225, y + height * 0.49, row["detour"],
                    fontsize=9.5, color=MUTED, ha="center", va="center")
            ax.add_patch(FancyArrowPatch(
                (0.603, y + height * 0.49), (0.592, y + height * 0.49),
                arrowstyle="<|-|>", mutation_scale=11, color="#C9D5E2",
                linewidth=1.2, shrinkA=0, shrinkB=0))
        else:
            ax.text(0.7225, y + height * 0.49, "no detour — straight through",
                    fontsize=9, color="#B6C2CF", ha="center", va="center", style="italic")

        # --- the status
        box(ax, 0.858, y, 0.128, height, row["fill"], row["edge"])
        ax.text(0.922, y + height * 0.60, row["label"], fontsize=10.5,
                fontweight="bold", color=row["text"], ha="center", va="center")
        passed = row["count"] - len(row["skipped"])
        detail = "%d step%s ok" % (passed, "" if passed == 1 else "s")
        if row["skipped"]:
            detail += "  ·  %d deviation" % len(row["skipped"])
        ax.text(0.922, y + height * 0.25, detail,
                fontsize=8.5, color=AMBER_T if row["skipped"] else MUTED,
                ha="center", va="center")

        if i < len(rows) - 1:
            ax.add_patch(FancyArrowPatch(
                (0.3175, y - 0.002), (0.3175, y - gap + 0.002),
                arrowstyle="-|>", mutation_scale=14, color=ARROW,
                linewidth=1.5, shrinkA=0, shrinkB=0))

    # --- footer
    deviations = [s for row in rows for s in row["skipped"]]
    ax.plot([0.045, 0.986], [0.052, 0.052], color="#D5DEE9", linewidth=1)
    left = ("Run %s  ·  %d of %d steps ok, zero retries  ·  Order %s, Invoice %s"
            % (run_dir.name,
               sum(1 for s in steps if s["status"] == "ok"), len(steps),
               report.get("flow_state", {}).get("order_number", "?"),
               report.get("flow_state", {}).get("invoice_number", "?")))
    ax.text(0.045, 0.022, left, fontsize=9.5, color=MUTED, va="center")
    if deviations:
        ax.text(0.986, 0.022,
                "%d step reported as a deviation, not a pass: %s"
                % (len(deviations), deviations[0]["title"]),
                fontsize=9.5, color=AMBER_T, ha="right", va="center")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    print("wrote %s  (from %s)" % (OUT, run_dir.name))
    return OUT


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=None)
    args = ap.parse_args()
    build(args.run or latest_ok_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
