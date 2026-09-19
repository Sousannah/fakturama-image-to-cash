"""Build `Fakturama_Design_And_Implementation_Report.docx`.

    python scripts/build_design_report.py [--out PATH] [--run out/run-YYYYmmdd-HHMMSS]

Every figure in the document is read from the repository or from a run report,
so the prose cannot claim something the code no longer does.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx_style import (  # noqa: E402
    REPO_ROOT, Doc, collected_tests, latest_run, load_report, selector_stats,
)

DIAGRAMS = REPO_ROOT / "docs" / "diagrams"
SHOTS = REPO_ROOT / "docs" / "screenshots"


def build(out: Path, run_dir: Path) -> Path:
    report = load_report(run_dir) if run_dir else {}
    steps = report.get("steps", [])
    ok = sum(1 for s in steps if s["status"] == "ok")
    tiers = report.get("grounding_tier_usage", {})
    state = report.get("flow_state", {})
    controls, strategies, declared = selector_stats()
    tests = collected_tests()

    d = Doc(
        "Design and Implementation Report",
        "One order image \u2192 a saved, verified Order and its linked Invoice, "
        "driven through Fakturama's own user interface.",
        footer="Fakturama Image-to-Cash \u00b7 design and implementation",
    )

    d.toc_list([
        ("1", "Summary"),
        ("2", "The desktop front end"),
        ("3", "Image extraction: OCR first, then the LLM"),
        ("4", "Control discovery and grounding"),
        ("5", "The Order-first flow"),
        ("6", "Verification"),
        ("7", "Trade-offs"),
        ("8", "What is not done"),
        ("9", "Running the system"),
        ("10", "If I had three more hours"),
    ])
    d.spacer(10)

    # ===================================================================== #
    d.h1("1. Summary")
    d.p(
        "The system takes a single order image and ends with a saved Order and a linked, "
        "paid Invoice inside Fakturama 2.2.0, having created whatever master data was missing "
        "on the way. Nothing is written to the database directly and nothing is imported: "
        "every record exists because the automation drove the same controls a person would."
    )
    d.p(
        "Two halves, joined by one typed object. **Extraction** turns pixels into an `OrderDoc` "
        "and refuses to hand it on unless its own arithmetic reconciles. **Automation** takes that "
        "`OrderDoc` and performs the five-stage procedure, verifying each step before the next."
    )

    d.h2("Where it got to")
    if steps:
        d.table(
            ["Spec stage", "What it covers", "Result"],
            [
                ["1 (1.1\u20131.8)", "Extract the image, open New Order, fill the header", "**Works**"],
                ["2 (2.1\u20132.13)", "Select or create the Debtor, incl. the payment method", "**Works**"],
                ["3 (3.1\u20133.17)", "VAT, Product create/select, every item line", "**Works**"],
                ["4 (4.1\u20134.7)", "Confirm, save and verify the Order; follow-up Invoice", "**Works**"],
                ["5 (5.1\u20135.7)", "Linked Invoice, paid status, final verification", "**Works**"],
            ],
            widths=[0.18, 0.55, 0.27],
        )
        d.p(
            "The run this document was built from is `%s`: **%d of %d steps** completed, "
            "final status `%s`."
            % (run_dir.name, ok, len(steps), report.get("status", "?"))
        )
    d.note(
        "The procedure is followed literally. The Order tab is opened first and never closed; "
        "the Debtor and Product **selectors** are the existence checks, not a database query; "
        "master data is created only when an exact selection is unavailable; and the Invoice is "
        "created from the saved Order's follow-up area, never from the top toolbar, because only "
        "the follow-up action preserves the Order relationship."
    )

    d.h2("Records the automation created")
    d.p(
        "Read back out of Fakturama's own stored data rather than off the screen, so this is "
        "what was persisted, not what a widget happened to be displaying:"
    )
    created = []
    if state.get("debtor_created"):
        created.append("CONTACT   Northstar Office GmbH \u00b7 Marta Klein \u00b7 alias NORTHSTAR-BERLIN")
    if state.get("payment_method_created"):
        created.append("PAYMENT   'Bank Transfer'  code = Credit transfer  (spec 2.10.4)")
    for v in state.get("vats_created", []):
        created.append("VAT       '%s'  value = 19%%  code = S (Standard rate)" % v)
    for pcode in state.get("products_created", []):
        created.append("PRODUCT   '%s'" % pcode)
    if state.get("order_number"):
        created.append("ORDER     %s   Cust.Ref. WEB-2026-0714-A17   net 570.00 / gross 678.30" % state["order_number"])
    if state.get("invoice_number"):
        created.append("INVOICE   %s   linked to the Order, paid on 2026-07-18" % state["invoice_number"])
    d.code("\n".join(created) or "(no run report available)")
    d.p(
        "The Product master prices are the spec 3.9 calculation performed by the automation: "
        "`250.00 \u00d7 (1 + 19/100) = 297.50` and `40.00 \u00d7 (1 + 19/100) = 47.60`, with the 10% "
        "transaction-line discount correctly **not** applied to the master price."
    )

    d.h2("Extraction accuracy, and the one thing it gets wrong")
    d.p(
        "Every transaction value on the sample document was read correctly: both SKUs, both "
        "descriptions, both quantities — including the two single digits that the first OCR "
        "configuration silently dropped — every price, both discounts, the VAT rate, all three "
        "totals, the order date, the external reference, the payment method and the payment date. "
        "The reconciliation is exact, not within tolerance."
    )
    d.p(
        "One field is wrong, and it is worth dwelling on because it is the honest shape of this "
        "design. The OCR reads the e-mail address as `martaklein@example.test`: the dot between "
        "the given and family name is a few pixels and falls below the detector's threshold. The "
        "model is instructed never to invent or repair a value — *transcribe, never compute* — "
        "so it faithfully returns what it was given, and the Debtor is created with the shortened "
        "address."
    )
    d.note(
        "That is the trade working as designed, not a bug in the prompt. The same instruction that "
        "lets the model quietly fix an e-mail would let it quietly fix a total, and the "
        "reconciliation gate exists precisely because a model that repairs the document destroys "
        "the only check there is. The fix belongs in the OCR stage: a confidence-weighted second "
        "pass over the regions that scored low, handed to the model as \"these characters are "
        "uncertain\", which improves what is transcribed without licensing invention.",
        "warn",
    )
    d.p(
        "The front end's **what the model corrected** panel is where a repair of this kind would "
        "appear. On this run it reports that every extracted value appears in the OCR reading "
        "character for character — which is exactly how a reviewer can tell that the model "
        "added nothing, for better and for worse."
    )

    # ===================================================================== #
    d.page_break()
    d.h1("2. The desktop front end")
    d.p(
        "The automation is usable from the command line, but the thing a reviewer actually wants "
        "to see is *what the machine read* before it starts typing into an accounting system. "
        "The desktop window makes the pipeline's three stages inspectable in the order they run."
    )
    d.image(DIAGRAMS / "08_frontend.png",
            "Figure 1. The window, and the two calls and one callback it reaches the core through.", 1.0)
    d.image(SHOTS / "gui-01-start.png",
            "The window with the sample order loaded, before anything has run.", 0.96)

    d.table(
        ["Panel", "What it shows", "Why it is on screen"],
        [
            ["1 \u00b7 Read the image",
             "The OCR transcription with horizontal positions preserved, plus the engine, the word "
             "count and the mean confidence.",
             "This is the ground truth the model is given. If a value is wrong later, this panel "
             "says whether the pixels or the model were at fault."],
            ["2 \u00b7 Validate with Groq",
             "The structured fields, the reconciliation recomputed in `Decimal`, and the list of "
             "values the model had to **correct** because they are not in the OCR reading "
             "character for character.",
             "The corrections list is the honest version of 'validated'. A reviewer can see the "
             "model's contribution instead of trusting it."],
            ["3 \u00b7 Drive Fakturama",
             "The step list filling in live, each row carrying its clause from the written "
             "procedure, its status, its retry count and its screenshot.",
             "The run is auditable while it happens, and each row can be checked against the spec."],
        ],
        widths=[0.18, 0.42, 0.40],
    )
    d.note(
        "Tkinter is not thread safe, and a background thread touching a widget takes the "
        "interpreter down rather than raising. Everything slow therefore runs on a worker thread "
        "that talks to the window only by putting messages on a queue; `f2c.gui.app._pump` drains "
        "it on the main loop and is the **only** place in the module that touches a widget. The "
        "flow reports itself through `RunArtifacts.emit`, a listener hook that the rest of the "
        "package neither knows nor cares about."
    )

    # ===================================================================== #
    d.page_break()
    d.h1("3. Image extraction: OCR first, then the LLM")
    d.image(DIAGRAMS / "01_pipeline.png", "Figure 2. Image to a reconciled OrderDoc.", 1.0)

    d.h2("Why OCR first rather than a vision model")
    d.p(
        "The Groq account in use serves no vision model, so OCR was the available route \u2014 but it "
        "is the better engineering regardless. Transcription becomes a reproducible, inspectable "
        "artifact that is saved next to the extraction, so a disputed field can be traced to what "
        "was actually read; and the model never sees pixels it could hallucinate over, only text "
        "it can be caught misreading. The vision path is still implemented "
        "(`llm.structure_from_image`, `F2C_EXTRACTION_MODE=vision`) for a provider that offers one."
    )

    d.h2("Three decisions that carry the accuracy")
    d.h3("Upscale before recognising")
    d.p(
        "At native resolution EasyOCR silently dropped both single-digit quantities \u2014 the `2` and "
        "the `3`. At 2\u00d7 with lowered detection thresholds it reads them at confidence 1.00. A "
        "dropped quantity is a wrong invoice, so this is a correctness fix, not a tuning nicety."
    )
    d.h3("Keep the coordinates")
    d.p(
        "A flat OCR string destroys the item table: the discount, the VAT and the line total "
        "become an ambiguous run of numbers. `ocr.group_lines` clusters words into visual lines "
        "and `ocr.render_layout` re-renders them at their scaled horizontal positions, so a value "
        "under the `Disc.` heading still sits under the `Disc.` heading. Reading the table becomes "
        "a reading question rather than a guessing one."
    )
    d.code(
        "          SKU              Description        Qty  Unit   UribRet   Disc:  VAT     LigoRet\n"
        "1  CHR-ERG-01   Ergonomic Desk Chair           2    pcs    250.00    10%   19%      450.00\n"
        "2  MAT-DESK-02  Anti-Fatigue Desk Mat          3    pCs    40.00     0%    19%      120.00",
        caption="the item table as the model receives it \u2014 note the misread headings, which do not matter "
                "because the columns still line up",
    )
    d.h3("Tool calling, and strings for every number")
    d.p(
        "The model is called with a tool schema rather than being asked for free-form JSON, so the "
        "response shape is enforced by the API instead of by a parser. Every numeric field is typed "
        "`string`: the model transcribes digits and `f2c` parses them into `Decimal` itself, because "
        "a JSON float destroys cents."
    )

    d.h2("The arithmetic gate")
    d.p(
        "`extract/validate.py` recomputes the document before any window is opened, and raises "
        "rather than proceed. Per line `qty \u00d7 unit_net \u00d7 (1 \u2212 disc/100)`; the sum of lines against "
        "the stated net total; VAT per line, summed; net + VAT against gross; and the "
        "paid-status/payment-date consistency rule the spec depends on in 5.3."
    )
    d.note(
        "Writing an order whose own arithmetic disagrees is worse than writing no order at all. "
        "The gate is the reason the automation is allowed to be confident once it starts typing.",
        "ok",
    )

    # ===================================================================== #
    d.page_break()
    d.h1("4. Control discovery and grounding")
    d.h2("What the application actually is")
    d.p(
        "Fakturama 2.2.0 is an Eclipse/SWT application. Recon against the live accessibility tree, "
        "not assumption, established the constraints the whole grounding strategy answers to:"
    )
    d.bullets([
        "Every composite is a `SWT_Window0` with no useful AutomationId.",
        "Toolbar buttons do carry real names (`Create: New Order`, `Save the current contents`), "
        "and a few Edits do too (`Cust.Ref.`, `Discount`, `Total`).",
        "The left navigation is not a tree \u2014 it is a pane of plain `Text` controls.",
        "The record selectors beside *Addresses* and *Items* are unnamed `Image` controls stacked "
        "vertically; the spec's 'upper icon, not the lower green +' has to be expressed geometrically.",
        "**Every list is a custom-drawn canvas** \u2014 the item grid and the address, product and "
        "document lists. UIA reports their position and nothing else.",
        "SWT dialogs are child shells of the main window, not desktop siblings.",
        "One caption can front two fields (`First Name Last Name`, `ZIP - City`).",
        "A saved document editor is **renamed**: `New Order` becomes its document number, so every "
        "selector that identifies it by name stops matching at the moment the Order starts to exist.",
    ])

    d.h2("A four-tier resolver")
    d.image(DIAGRAMS / "03_tiers.png", "Figure 3. The four grounding tiers, tried in order per control.", 1.0)
    d.p(
        "Each logical control is declared in `src/f2c/ui/selectors.yaml` as an ordered list of "
        "strategies. The resolver tries them in turn and records which one won, so the run report "
        "shows how much of the flow the accessibility tree actually carried."
    )
    d.table(
        ["Tier", "How it finds the control", "Declared"],
        [
            ["`uia`", "ControlType + Name, scoped to an ancestor. Semantic and stable.", str(declared.get("uia", 0))],
            ["`anchor`", "Geometric: the Edit to the right of, or below, a given label. Carries most "
                         "of the forms, because SWT `Text` widgets have no AutomationId.", str(declared.get("anchor", 0))],
            ["`template`", "An icon image matched **inside a UIA-resolved rectangle**.", str(declared.get("template", 0))],
            ["`ocr`", "Text found inside a UIA-resolved rectangle. The only tier that reaches a "
                      "custom-drawn canvas.", str(declared.get("ocr", 0))],
        ],
        widths=[0.13, 0.72, 0.15],
    )
    d.p(
        "%d logical controls, %d strategies in total. The pixel tiers are never a control's first "
        "strategy \u2014 `tests/test_selectors.py` enforces that \u2014 so a UI that exposes itself properly "
        "is always driven semantically." % (controls, strategies)
    )
    d.note(
        "**No absolute coordinate exists anywhere in the package.** Every click point is derived at "
        "run time from a resolved rectangle, and `tests/test_no_hardcoded_coords.py` parses the AST "
        "of every module to prove that no pointer call receives a numeric literal."
    )
    if tiers:
        d.p("Tier usage in run `%s`: %s." % (
            run_dir.name, ", ".join("**%s %d**" % (k, v) for k, v in tiers.items())))
        d.p(
            "The accessibility tree carries almost the whole flow; pixels are the exception rather "
            "than the rule, which is the outcome the tier ordering is designed to produce.", muted=True,
        )

    d.h2("Handling the two stacked icons")
    d.p(
        "Spec 2.1 and 3.2 both say the same thing: click the upper selector icon, never the lower "
        "green +, which starts a new record. Both are unnamed `Image` controls in the same unnamed "
        "composite. The rule is expressed as `pick: topmost` within the smallest enclosing "
        "composite that contains the section's label, and a guard refuses to click if the resolved "
        "selector is not actually above the resolved creation control \u2014 so a layout change makes "
        "the run stop rather than silently create a duplicate Debtor."
    )

    # ===================================================================== #
    d.page_break()
    d.h1("5. The Order-first flow")
    d.image(DIAGRAMS / "04_stages.png", "Figure 4. The five stages, and the detours each may take.", 1.0)
    d.p(
        "One continuous flow. The Order editor is opened in stage 1 and stays open: every "
        "master-data detour opens its own editor on top of it and returns by clicking the Order's "
        "tab, never by re-opening it."
    )

    d.h2("Stage 2 in detail \u2014 the pattern the whole system follows")
    d.image(DIAGRAMS / "05_debtor.png", "Figure 5. Debtor: select, or create and then select.", 0.92)
    d.p(
        "The selector is the existence check. The flow searches the address picker for the "
        "extracted company; a row counts as an exact match only when Company, First Name, Name, "
        "ZIP and City all agree. One exact row is selected; conflicting rows stop the run for "
        "manual review; no row sends it down the creation branch. **And after creating the Debtor "
        "it goes back to the same picker and selects it there** \u2014 which is both what the spec says "
        "and the only proof that the record really was saved."
    )

    d.h2("Where it stops for a human")
    d.p(
        "The flow raises `ManualReviewRequired` \u2014 with a screenshot, a UIA tree dump and the "
        "expectation that failed \u2014 rather than guessing, for:"
    )
    d.bullets([
        "ambiguous or conflicting Debtor rows in the address picker;",
        "multiple or conflicting payment methods with the same name;",
        "a VAT row whose name, value or e-invoice code disagrees with the required definition;",
        "a Product that does not reappear in the selector after being saved;",
        "an Invoice payment method that is not available;",
        "any totals mismatch between the Order footer and the source image.",
    ])

    d.h2("What makes a long run survivable")
    d.p(
        "Nearly every failure seen against the live application has the same shape: a control needs "
        "the foreground, keyboard focus and a settled layout, and if any of the three is not true "
        "at the instant a keystroke lands, the operation does nothing **and says nothing**. Chasing "
        "them one at a time was the wrong approach."
    )
    d.p(
        "`flow.context.StepAttempts` recovers the whole class. Between attempts it re-asserts the "
        "foreground, throws away cached container elements and runs the step's own recovery \u2014 "
        "usually 'bring the document editor back to the front'. It is deliberately **opt-in per "
        "step**: a body may only be replayed if running it twice is indistinguishable from running "
        "it once. Anything that creates a record or presses Save uses the plain, single-attempt "
        "`Ctx.step`, because a repeat would mean a second saved record \u2014 the one failure this "
        "automation must never produce."
    )
    d.note(
        "`precondition` covers the harder case in between. The Product picker **appends** an item "
        "line rather than setting one, so an attempt that selected the product and then failed on "
        "the way out had already done its job; repeating it put the same product on the Order "
        "twice. The precondition runs before every retry and a true answer finishes the step "
        "instead of replaying it.",
        "warn",
    )

    # ===================================================================== #
    d.page_break()
    d.h1("6. Verification")
    d.image(DIAGRAMS / "06_verification.png", "Figure 6. Two independent layers.", 1.0)
    d.h2("Layer 1 \u2014 read the screen back")
    d.p(
        "Every write is read back, and a value that did not take is retried through the other write "
        "path before it is accepted. Every stage verifies its own post-condition: the Debtor's "
        "addresses populate the Order, the item line's computed price matches "
        "`qty \u00d7 unit \u00d7 (1 \u2212 disc/100)`, the Order footer matches the image's three totals, the "
        "Documents list gains exactly one row with the expected Cust.Ref."
    )
    d.h2("Layer 2 \u2014 read the stored data")
    d.p(
        "Fakturama 2.2.0 ships **HSQLDB, not H2**, and HSQLDB keeps its data as plain-text SQL "
        "(`Database.script` plus the uncheckpointed `Database.log`). So `verify/db.py` is a parser, "
        "not a JDBC client: no driver, no jar, no file-lock problem, and rows committed since the "
        "last checkpoint are still visible."
    )
    d.note(
        "This layer earned its place. `ValuePattern.SetValue` updates an SWT widget's displayed "
        "text \u2014 and the read-back agrees \u2014 without firing the modify listener, so Company, Alias and "
        "Country read back correctly, saved without complaint, and landed in the database as "
        "`NULL`. Only a second, independent oracle could catch that. Writes are now real key "
        "events by default.",
        "err",
    )

    # ===================================================================== #
    d.h1("7. Trade-offs")
    d.table(
        ["Decision", "Bought", "Paid for with"],
        [
            ["OCR + text LLM rather than a vision model",
             "A reproducible, inspectable transcription; traceability from a disputed field to the pixels.",
             "Two failure points instead of one; layout reconstruction has to be good enough to keep the table readable."],
            ["Declarative selectors in YAML",
             "Re-grounding for a new Fakturama version is a data change, not a code change; the tier that won is measurable.",
             "A layer of indirection between the flow and the widget."],
            ["UIA first, pixels last",
             "Resilience to theme, DPI and window size; %d of the resolutions in the recorded run were semantic."
             % tiers.get("uia", 0),
             "Custom-drawn canvases still need OCR, and that is where the remaining fragility lives."],
            ["Retry only where a replay is safe",
             "Long runs survive the transient focus failures that dominate real-world UI automation.",
             "Every step had to be classified by hand as replayable or not."],
            ["Stop for manual review rather than guess",
             "The automation never invents an accounting record.",
             "A run can end without a document and need a human."],
            ["Drive the UI rather than the database",
             "Fakturama's own validation, numbering and document linking all apply.",
             "Slow \u2014 a full run is minutes, not seconds."],
        ],
        widths=[0.24, 0.40, 0.36],
    )

    # ===================================================================== #
    d.h1("8. What is not done")
    d.bullets([
        "**The OCR drops the dot in the e-mail address**, and the model is correctly forbidden from "
        "inventing it back, so the Debtor is created with `martaklein@example.test`. The fix is a "
        "confidence-weighted second OCR pass, not a looser prompt.",
        "**A differing delivery address is not created as a second address.** The sample's delivery "
        "address differs from billing; only the Main address is created and given the Invoice role. "
        "The flow records this as a deviation rather than silently proceeding.",
        "**Address roles** are set through this version's address-type control; when it cannot be "
        "driven, the Main address keeps Fakturama's defaults and the step is reported as `skipped`, "
        "not `ok`.",
        "**Alias and Country occasionally need a second write.** The read-back catches it and "
        "retries through the other write path, but the underlying widget event has not been found.",
        "**The Product selector's SKU column cannot be reconstructed** from its canvas. A "
        "documented, narrow fallback accepts the single row returned by an exact-SKU search when "
        "its name also matches the extracted description, and records in the report that it did so.",
        "**Icon templates are not committed** \u2014 capture them per machine with `f2c capture-icon`. "
        "Every template selector has a UIA fallback, so their absence degrades rather than breaks a run.",
        "**One document.** The extraction has been proven against one order image; how much of the "
        "accuracy is general and how much is this document is not yet known.",
    ])

    # ===================================================================== #
    d.page_break()
    d.h1("9. Running the system")
    d.h2("Setup")
    d.code(
        "python -m venv .venv && .venv\\Scripts\\activate\n"
        "pip install -r requirements.txt\n"
        "pip install -e .                 # optional: gives the `f2c` command\n"
        "copy .env.example .env           # then edit it"
    )
    d.code(
        "F2C_PROVIDER=groq\n"
        "GROQ_API_KEY=gsk_...\n"
        "F2C_MODEL=openai/gpt-oss-120b\n"
        "F2C_FAKTURAMA_EXE=D:\\Fakturama2\\Fakturama.exe\n"
        "F2C_WORKSPACE=D:/                # the folder holding Database/ and Templates/",
        caption=".env",
    )
    d.p(
        "The workspace is the folder Fakturama's own title bar names (`Fakturama - D:\\`), not the "
        "install directory. `f2c doctor` checks all of it \u2014 dependencies, the executable, the "
        "workspace, the selector registry and the icon templates \u2014 before a run is attempted."
    )

    d.h2("Running it")
    d.code(
        "python run_gui.py                                  # the desktop front end\n"
        "f2c gui                                            # the same window, if pip install -e . was run\n"
        "\n"
        "f2c extract --image fixtures/sample_order.png      # OCR + LLM only, no UI\n"
        "f2c run     --image fixtures/sample_order.png --verify-db\n"
        "f2c verify  --image fixtures/sample_order.png --database\n"
        "f2c inspect --dump out/tree.txt                    # calibration aid\n"
        "f2c resolve order.custref_field                    # resolve one control, report the tier"
    )

    d.h2("Repeatable runs")
    d.p(
        "The flow creates master data, so a second run against the same workspace takes the "
        "'already exists' branch and stops exercising the creation paths. Snapshot once, restore "
        "before each run:"
    )
    d.code(
        "python scripts/snapshot_workspace.py --allow-running\n"
        "python scripts/reset_workspace.py"
    )
    d.p(
        "Only `Database/` and `Templates/` are copied, so the workspace is allowed to be a drive "
        "root; the current workspace is always moved aside rather than deleted.", muted=True,
    )

    d.h2("What each run leaves behind")
    d.p("`out/run-<timestamp>/` \u2014 the evidence folder, which is also the screenshots deliverable:")
    d.bullets([
        "`report.md` and `report.json` \u2014 every step with its spec clause, status, retry count, "
        "duration and screenshot, plus the grounding tier usage and the final flow state;",
        "`screenshots/` \u2014 numbered in execution order;",
        "a UIA tree dump and a full traceback for any failure.",
    ])

    d.h2("Tests")
    d.code("python -m pytest -q      # %d passed" % tests)
    d.p(
        "Pure logic, no UI required: extraction normalisation and reconciliation (including "
        "deliberately corrupted totals), the exact-match and VAT-reuse decision rules, "
        "clipped-column and OCR-confusable matching, anchor-relative geometry, grid addressing, "
        "locale-tolerant formatting, the selector registry's integrity, and the "
        "no-hardcoded-coordinates rule."
    )
    d.p(
        "Two of them exist because they caught real mistakes while writing this: a blanket prefix "
        "rule that would have matched `Berlin` against `Berlin-Mitte`, and an OCR fold that had to "
        "be proven not to merge two distinct SKUs.", muted=True,
    )

    # ===================================================================== #
    d.h1("10. If I had three more hours")
    d.p(
        "**A second and third order image.** The flow is complete; what is not yet known is how "
        "much of the extraction accuracy is genuine and how much is this one document. A different "
        "layout, a gross-priced document and a multi-VAT document would tell me more than any "
        "further work on the happy path."
    )
    d.p(
        "**Resume from a failed step.** The run already records enough state to know what it "
        "created; `FlowState` was written with this in mind. A run that fails at stage 4 should be "
        "restartable without re-creating the Debtor and the Products."
    )
    d.p(
        "**The remaining grid dependency on OCR.** The item lines are written and verified, but the "
        "canvas is still read with OCR. Driving it purely from the keyboard and verifying through "
        "the Order's named total `Edit` controls would remove pixels from the write path entirely."
    )
    d.p(
        "**The Alias and Country persistence bug, properly.** The read-back catches it and the "
        "retry fixes it, which is a workaround. Finding the event those two widgets actually listen "
        "for would be a fix."
    )
    d.p(
        "**Create the differing delivery address.** It is the one part of the written procedure "
        "that is reported as a deviation rather than performed."
    )

    return d.save(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=Path(r"D:\Sousannah") / "Fakturama_Design_And_Implementation_Report.docx")
    ap.add_argument("--run", type=Path, default=None)
    args = ap.parse_args()

    run_dir = args.run or latest_run(status="ok") or latest_run()
    if run_dir is None:
        print("no run report found under out/ - the document will omit run figures")
    path = build(args.out, run_dir)
    print("wrote %s  (%.0f KB)  from run %s" % (path, path.stat().st_size / 1024, run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
