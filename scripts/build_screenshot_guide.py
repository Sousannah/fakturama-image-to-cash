"""Build `Fakturama_Annotated_Screenshots.docx` - the walk-through deliverable.

    python scripts/build_screenshot_guide.py [--run out/run-YYYYmmdd-HHMMSS]

Part A is the desktop front end (input and the two extraction screens); Part B
is the Fakturama run, one page per captured step, in execution order; Part C is
the final verification.

The run screenshots are read from the run's own `report.json`, so the document
always matches the run it was built from. The annotations - the ribbon text and
the highlight boxes - are declared in `ANNOTATIONS` below, keyed by spec clause.
Boxes are fractions of the image, so they survive a different screen resolution.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from annotate import annotate  # noqa: E402
from docx_style import REPO_ROOT, Doc, latest_run, load_report  # noqa: E402

SHOTS = REPO_ROOT / "docs" / "screenshots"
#: The annotated figures are a deliverable in their own right, so they are
#: written into the repository rather than only into the run folder.
BUILD = REPO_ROOT / "docs" / "screenshots" / "run"


# --------------------------------------------------------------------------- #
# What to say about each captured step, and where to point.
# (x0, y0, x1, y1, label) are fractions of the screenshot.
# --------------------------------------------------------------------------- #
ANNOTATIONS: Dict[str, dict] = {
    "0.0": {
        "note": "The workspace before the run: no debtor, no products, no documents.",
    },
    "2.10.6": {
        "note": "Spec 2.10.3-2.10.6. Name and Description are the exact extracted method; the "
                "payment code follows the 2.10.4 mapping; the day and discount fields are zeroed "
                "and 'Set as standard' is not touched.",
        "boxes": [
            (0.205, 0.155, 0.700, 0.310, "Name and Description = the exact extracted method"),
            (0.205, 0.320, 0.700, 0.500, "code Credit transfer (2.10.4); everything else zero"),
        ],
    },
    "3.6": {
        "note": "Spec 3.6. Name and Description are 'VAT' plus the extracted percentage, the value "
                "is that percentage, and the e-invoice code stays S (Standard rate).",
        "boxes": [
            (0.205, 0.150, 0.700, 0.470, "VAT 19%, value 19%, code S (Standard rate)"),
        ],
    },
    "1.3": {
        "note": "Spec 1.3. The New Order editor, opened from the top toolbar. It is opened first "
                "and stays open for the whole run - every master-data detour comes back to this tab.",
        "boxes": [
            (0.355, 0.125, 0.425, 0.147, "the Order tab, which never closes"),
        ],
    },
    "1.7": {
        "note": "Spec 1.4-1.7. The proposed No. is read and left alone, the Date and Cust.Ref. come "
                "from the image, the price mode is Net and VAT stays 'With VAT'.",
        "boxes": [
            (0.205, 0.154, 0.730, 0.188, "No. read, never written (1.4) - Date (1.5) - price mode Net (1.7)"),
            (0.205, 0.224, 0.505, 0.255, "External Reference (1.6)"),
            (0.528, 0.290, 0.730, 0.320, "VAT stays 'With VAT' (1.7)"),
        ],
    },
    "2.1-2.3": {
        "note": "Spec 2.1-2.3. The Order's own address picker is the existence check. A row counts "
                "as an exact match only when Company, First Name, Name, ZIP and City all agree; "
                "conflicting rows stop the run, and no row sends it to the creation branch.",
        "boxes": [
            (0.226, 0.276, 0.248, 0.334, "the UPPER selector icon, never the lower green +"),
        ],
    },
    "2.8": {
        "note": "Spec 2.7-2.8. The billing address from the image, on Addresses > Main address.",
    },
    "2.11": {
        "note": "Spec 2.9-2.11. Alias, 0% discount and Net on Miscellaneous; the exact payment "
                "method on Payment; then one save.",
    },
    "2.12": {
        "note": "Spec 2.12-2.13. Back in the still-open Order, the picker is re-opened and the new "
                "Debtor selected there - which is what proves the record was really saved.",
    },
    "2.4": {
        "note": "Spec 2.4 / 2.13. The Invoice and Delivery addresses populated from the selected "
                "Debtor, checked against the source image before the run moves on to the products.",
        "boxes": [
            (0.205, 0.255, 0.510, 0.375, "populated from the selected Debtor"),
        ],
    },
    "3.2-3.3": {
        "note": "Spec 3.2-3.3. The Product picker, searched on the exact SKU. When no exact row "
                "appears the run cancels out, creates the Product, and comes back to this dialog.",
    },
    "3.10": {
        "note": "Spec 3.8-3.10. Item Number is the SKU and both Name and Description are the item "
                "description. Price (gross) is the 3.9 calculation - and the transaction line's "
                "discount is deliberately NOT applied to a master price.",
        "boxes": [
            (0.275, 0.178, 0.660, 0.232, "Item Number = SKU, Name = description (3.8)"),
            (0.222, 0.404, 0.365, 0.432, "250.00 x (1 + 19/100) = 297.50 (3.9)"),
            (0.222, 0.435, 0.470, 0.520, "cost price 0.00, the exact VAT, Stock 0.00 (3.10)"),
            (0.355, 0.125, 0.420, 0.147, "the Order is still open behind this editor"),
        ],
    },
    "3.12": {
        "note": "Spec 3.12. The newly saved Product, selected from the same picker in the same "
                "still-open Order. If it did not appear here, the run would stop for review.",
    },
    "4.1": {
        "note": "Spec 4.1-4.3. Both item lines complete, and the footer totals about to be checked "
                "against the image's own net, VAT and gross.",
        "boxes": [
            (0.203, 0.365, 0.845, 0.428, "qty, unit price, VAT and discount per line (3.13-3.16)"),
            (0.770, 0.445, 0.992, 0.590, "Total Net / VAT / Total"),
        ],
    },
    "4.4": {
        "note": "Spec 4.4. One click on the toolbar Save. This step is deliberately not replayable: "
                "a repeat would mean a second saved record.",
    },
    "4.5": {
        "note": "Spec 4.5. Exactly one Order row, carrying the extracted Cust.Ref., in the open "
                "state, with the expected total - and the follow-up area the next step will use.",
        "boxes": [
            (0.203, 0.365, 0.845, 0.428, "both lines: qty x unit x (1 - disc/100)"),
            (0.770, 0.445, 0.992, 0.590, "570.00 / 108.30 / 678.30 - the image's own totals"),
            (0.735, 0.248, 0.992, 0.332, "Create a follow-up document (4.6), never the toolbar"),
            (0.299, 0.678, 0.992, 0.710, "one Order row: PO000001, open, WEB-2026-0714-A17, 678.30"),
        ],
    },
    "4.6": {
        "note": "Spec 4.6-4.7. The Invoice is created from the saved Order's own follow-up area. "
                "The toolbar Invoice button is deliberately not used, because only the follow-up "
                "action preserves the Order relationship.",
        "boxes": [
            (0.203, 0.155, 0.760, 0.200, "the linked New Invoice editor, opened from the Order"),
        ],
    },
    "5.1": {
        "note": "Spec 5.1. Cust.Ref., both addresses, the Order Date, the VAT mode, the item lines "
                "and the totals were all copied from the Order. The proposed Invoice No., Invoice "
                "Date and Service date are left exactly as generated.",
        "boxes": [
            (0.203, 0.222, 0.510, 0.260, "Cust.Ref. carried over from the Order"),
            (0.512, 0.270, 0.745, 0.330, "Order Date preserved; the link is intact"),
        ],
    },
    "5.3": {
        "note": "Spec 5.2-5.3. The payment method matches the image; because the extracted status "
                "is PAID, 'paid' is set with the extracted payment date and the full invoice total. "
                "Had the status not been PAID, the flag would have been left clear and no date "
                "invented.",
        "boxes": [
            (0.203, 0.533, 0.445, 0.568, "paid - Bank Transfer - 18 Jul 2026 - value 678.30"),
        ],
    },
    "5.4": {
        "note": "Spec 5.4. One save.",
    },
    "5.5": {
        "note": "Spec 5.5. The final state: the Invoice row with its total and paid state, and the "
                "source Order still open with the same Cust.Ref. and the same total. The list is a "
                "custom-drawn canvas read by OCR, so rows are matched by document number with "
                "confusable glyphs folded - it renders INV000001 and OCR returns INVOOOOO1.",
        "boxes": [
            (0.203, 0.533, 0.445, 0.568, "the persisted paid state, date and value"),
            (0.206, 0.618, 0.297, 0.710, "both documents under one transaction"),
            (0.299, 0.678, 0.992, 0.732, "INV000001 paid and PO000001 open - same Cust.Ref., same total"),
        ],
    },
    "99": {
        "note": "The workspace at the end of the run.",
    },
}


GUI_PAGES = [
    ("gui-01-start.png", "The front end with the order image loaded",
     "The only input the system gets. The left column is the pipeline; nothing has run yet."),
    ("gui-02-ocr.png", "Stage 1 \u00b7 what the OCR actually read",
     "The transcription with its horizontal positions preserved, so the item table is still a "
     "table. The strip above it gives the engine, the word count and the mean confidence - this "
     "is the ground truth the language model is given."),
    ("gui-03-groq.png", "Stage 2 \u00b7 the Groq validation",
     "Left: the structured fields the model returned, typed and reconciled. Right: the arithmetic "
     "recomputed in Decimal, and the values the model had to correct because they are not in the "
     "OCR reading character for character."),
    ("gui-04-running.png", "Stage 3 · the automation in control of Fakturama",
     "A frame from the screen recording, mid-run. The Order PO000001 is open and stays open; "
     "the Product picker has been re-opened after the creation detour and now offers CHR-ERG-01 "
     "at the gross price the automation calculated — 250.00 × (1 + 19/100) = 297.50 — "
     "against the VAT 19% rate it created a few steps earlier. The front end is behind this "
     "window, filling in its step list."),
    ("gui-05-done.png", "The finished run",
     "Every stage green, every step ok, and the final verification step selected — its "
     "evidence screenshot shows the Documents list carrying both the paid Invoice and the "
     "still-open source Order, with the same Cust.Ref. and the same total."),
]


# --------------------------------------------------------------------------- #
def build(out: Path, run_dir: Path) -> Path:
    report = load_report(run_dir)
    steps = report.get("steps", [])
    state = report.get("flow_state", {})
    shots_dir = run_dir / "screenshots"

    if BUILD.exists():
        shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    d = Doc(
        "Annotated Screenshots",
        "Every screen the automation passed through, in order, with the clause of the "
        "written procedure it satisfies.",
        footer="Fakturama Image-to-Cash \u00b7 annotated screenshots",
    )

    d.p(
        "This document is generated from run `%s` (status **%s**, %d steps). "
        "Part A is the desktop front end: the input image and the two extraction screens. "
        "Part B is the Fakturama run itself, one figure per captured step, in execution order. "
        "Part C is the final verification."
        % (run_dir.name, report.get("status", "?"), len(steps))
    )
    d.note(
        "The captions name the spec clause each screen satisfies, so a reviewer can check the "
        "screen against the procedure rather than against a description of it."
    )

    # ===================================================================== #
    d.h1("Part A \u00b7 The desktop front end")
    d.h2("A.0 \u2014 the input")
    d.image(REPO_ROOT / "fixtures" / "sample_order.png",
            "The one input: a single order image. Nothing else is supplied.", 0.62)
    d.p(
        "Everything that follows \u2014 the customer, the two products, the VAT rate, the payment "
        "method, the Order and the Invoice \u2014 comes from this picture."
    )

    for n, (name, title, caption) in enumerate(GUI_PAGES, start=1):
        d.page_break()
        d.h2("A.%d \u2014 %s" % (n, title))
        d.p(caption)
        d.image(SHOTS / name, "Figure A.%d. %s" % (n, title), 0.98)

    # ===================================================================== #
    # Landscape from here: these are full-screen 16:9 captures, and on a
    # portrait page the annotation labels are too small to read.
    d.orientation(landscape=True)
    d.h1("Part B \u00b7 The Fakturama run, step by step")

    figure = 0
    for step in steps:
        shot = step.get("screenshot")
        if not shot or not (shots_dir / shot).exists():
            continue
        figure += 1
        spec = str(step["spec"])
        meta = ANNOTATIONS.get(spec, {})
        note = meta.get("note", "")
        boxes = meta.get("boxes", [])

        dst = BUILD / ("B%02d-%s.jpg" % (figure, Path(shot).stem))
        annotate(
            shots_dir / shot, dst,
            index=figure, spec=spec if spec not in ("00", "99") else "",
            title=step["title"], note=note, boxes=boxes, status=step["status"],
        )

        d.h2("B.%d \u2014 [%s] %s" % (figure, spec, step["title"]))
        if note:
            d.p(note)
        if step.get("detail"):
            d.p("**Recorded:** %s" % step["detail"], muted=True)
        if step.get("attempts", 1) > 1:
            d.note(
                "This step needed %d attempts. The replay harness recovered it in place and the "
                "run continued; the retry count is kept in the report so a passing run that "
                "limped is still visible." % step["attempts"],
                "warn",
            )
        d.image(dst, "Figure B.%d \u00b7 spec %s \u00b7 %s" % (figure, spec, step["status"]), 0.85)
        if step is not steps[-1]:
            d.page_break()

    # ===================================================================== #
    d.orientation(landscape=False)
    d.h1("Part C \u00b7 What was actually saved")
    d.p("Read back from Fakturama's own stored data, not from the screen:")
    lines = []
    if state.get("payment_method_created"):
        lines.append("PAYMENT   Bank Transfer            code = Credit transfer   (spec 2.10.4)")
    for v in state.get("vats_created", []):
        lines.append("VAT       %-24s value = 19%%   code = S (Standard rate)" % v)
    if state.get("debtor_created"):
        lines.append("CONTACT   Northstar Office GmbH    Marta Klein   alias NORTHSTAR-BERLIN")
    for p in state.get("products_created", []):
        lines.append("PRODUCT   %-24s created from the Order's picker" % p)
    if state.get("order_number"):
        lines.append("ORDER     %-24s Cust.Ref. WEB-2026-0714-A17   net 570.00 / gross 678.30"
                     % state["order_number"])
    if state.get("invoice_number"):
        lines.append("INVOICE   %-24s linked to the Order, paid 2026-07-18, value 678.30"
                     % state["invoice_number"])
    d.code("\n".join(lines) or "(no flow state recorded)")

    d.h2("Step index")
    rows = [
        [str(s["index"]), str(s["spec"]), s["title"], s["status"],
         str(s.get("attempts", 1)), "%.1f" % s["seconds"]]
        for s in steps
    ]
    d.table(["#", "Spec", "Step", "Status", "Tries", "s"], rows,
            widths=[0.05, 0.09, 0.58, 0.14, 0.07, 0.07], font_size=8)

    return d.save(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path(r"D:\Sousannah") / "Fakturama_Annotated_Screenshots.docx")
    ap.add_argument("--run", type=Path, default=None)
    args = ap.parse_args()

    run_dir = args.run or latest_run(status="ok") or latest_run()
    if run_dir is None:
        print("no run found under out/")
        return 2
    path = build(args.out, run_dir)
    print("wrote %s  (%.0f KB)  from run %s" % (path, path.stat().st_size / 1024, run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
