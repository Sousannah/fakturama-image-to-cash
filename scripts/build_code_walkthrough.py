"""Build `Fakturama_Code_Walkthrough.docx` - a file-by-file tour of the package.

    python scripts/build_code_walkthrough.py [--out PATH] [--run out/run-...]

Line counts, module lists and run figures are read from the repository at build
time, so a module that is added or removed shows up here without anyone
remembering to update prose.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docx_style import (  # noqa: E402
    REPO_ROOT, Doc, collected_tests, latest_run, load_report, loc, selector_stats,
)

DIAGRAMS = REPO_ROOT / "docs" / "diagrams"
SHOTS = REPO_ROOT / "docs" / "screenshots"


def _lines(rel: str) -> str:
    try:
        return "%d lines" % loc(rel)
    except Exception:
        return "\u2014"


def build(out: Path, run_dir: Path) -> Path:
    report = load_report(run_dir) if run_dir else {}
    steps = report.get("steps", [])
    tiers = report.get("grounding_tier_usage", {})
    state = report.get("flow_state", {})
    controls, strategies, declared = selector_stats()
    tests = collected_tests()

    d = Doc(
        "Code Walkthrough",
        "What every file in the package does, why it is shaped that way, and "
        "which live-application failure taught it that shape.",
        footer="Fakturama Image-to-Cash \u00b7 code walkthrough",
    )

    d.toc_list([
        ("1", "What this project does"),
        ("2", "How the parts fit together"),
        ("3", "The core files"),
        ("4", "Reading the image \u2014 src/f2c/extract/"),
        ("5", "The desktop front end \u2014 src/f2c/gui/"),
        ("6", "Driving the screen \u2014 src/f2c/ui/"),
        ("7", "The business steps \u2014 src/f2c/flow/"),
        ("8", "Checking the work \u2014 src/f2c/verify/"),
        ("9", "Supporting files"),
        ("10", "One complete run, traced through the code"),
        ("11", "The bugs that shaped the code"),
    ])
    d.spacer(10)

    # ===================================================================== #
    d.h1("1. What this project does")
    d.h2("The job in one sentence")
    d.p(
        "Given one picture of a sales order, produce a saved Order and a linked, paid Invoice "
        "inside Fakturama \u2014 creating the customer, the payment method, the VAT rate and the "
        "products if they do not already exist \u2014 by driving the application's real user interface, "
        "and verify every step before taking the next one."
    )
    d.h2("The whole pipeline in one picture")
    d.image(DIAGRAMS / "01_pipeline.png", "Image \u2192 OCR \u2192 Groq \u2192 a typed, reconciled OrderDoc.", 1.0)
    d.image(DIAGRAMS / "02_architecture.png", "The layers, and what each one is allowed to talk to.", 0.95)

    # ===================================================================== #
    d.h1("2. How the parts fit together")
    d.p(
        "Five packages, each with one job, and a strict direction of dependency: `flow` talks to "
        "`ui`, never the other way round; `ui` knows about widgets and nothing about orders; "
        "`extract` knows about orders and nothing about widgets; `gui` knows about both but is "
        "known by neither."
    )
    d.h2("Folder map")
    d.table(
        ["Path", "Job", "Size"],
        [
            ["`src/f2c/models.py`", "The typed shape of an order. All money is `Decimal`.", _lines("src/f2c/models.py")],
            ["`src/f2c/extract/`", "Image \u2192 `OrderDoc`. OCR, the LLM tool call, and the arithmetic gate.", _lines("src/f2c/extract")],
            ["`src/f2c/gui/`", "The desktop window: upload, OCR, validation, live run.", _lines("src/f2c/gui")],
            ["`src/f2c/ui/`", "Finding and operating Fakturama's controls. Knows no business rules.", _lines("src/f2c/ui")],
            ["`src/f2c/flow/`", "The five stages of the written procedure, one module each.", _lines("src/f2c/flow")],
            ["`src/f2c/verify/`", "Two independent oracles: the screen, and the stored data.", _lines("src/f2c/verify")],
            ["`src/f2c/artifacts.py`", "The evidence folder: steps, screenshots, reports, the live listener.", _lines("src/f2c/artifacts.py")],
            ["`src/f2c/cli.py`", "`run`, `extract`, `verify`, `gui`, `inspect`, `resolve`, `doctor`.", _lines("src/f2c/cli.py")],
            ["`tests/`", "%d tests. No Fakturama required." % tests, _lines("tests")],
        ],
        widths=[0.26, 0.58, 0.16],
    )

    # ===================================================================== #
    d.page_break()
    d.h1("3. The core files")

    d.h2("models.py \u2014 the shape of an order")
    d.p(
        "`OrderDoc` is the one object the two halves of the system share. Extraction produces it; "
        "the flow consumes it; nothing else crosses that line."
    )
    d.p("Three derived properties encode arithmetic the spec defines, so it is written once:")
    d.bullets([
        "`Item.computed_line_net` \u2014 spec 3.16: `qty \u00d7 unit_net \u00d7 (1 \u2212 discount/100)`.",
        "`Item.product_gross_price` \u2014 spec 3.9: `unit_net \u00d7 (1 + VAT/100)`, two decimal places, "
        "half-up. The line discount is deliberately **not** applied: this is the product master "
        "price, not a transaction price.",
        "`Item.vat_name` \u2014 spec 3.4/3.6: the string `VAT 19%`, which is both what is searched for "
        "and what is created.",
    ])
    d.note(
        "`money()` quantises to two places with `ROUND_HALF_UP` \u2014 commercial rounding, not Python's "
        "default banker's rounding. Floats appear nowhere in this package."
    )

    d.h2("errors.py \u2014 the different kinds of failure")
    d.p(
        "The hierarchy exists to separate 'try again or try another route' from 'stop and fetch a "
        "human'. `LocatorError` and `TimeoutError_` are the first kind. `ManualReviewRequired` is "
        "the second, and it carries a context dictionary \u2014 the rows it saw, the value it expected \u2014 "
        "that lands in the run report. `ReconciliationError` is raised before any window opens."
    )

    d.h2("config.py, logging_setup.py")
    d.p(
        "`Settings` is one frozen read of the environment: provider, model, OCR engine, the "
        "Fakturama executable, the workspace, and the timing ceilings. Every timeout in the package "
        "is a **ceiling on a condition**, never a sleep. `logging_setup` gives `rich` output when it "
        "is installed and plain output when it is not."
    )

    d.h2("artifacts.py \u2014 the evidence folder")
    d.p(
        "`RunArtifacts` opens `out/run-<timestamp>/` and records a `StepRecord` per step: the spec "
        "clause, the title, the status, how long it took, **how many attempts it needed**, and the "
        "screenshot. It writes `report.json` and a `report.md` whose screenshot column makes it "
        "readable on its own."
    )
    d.p(
        "It also carries an optional `listener`. That is the hook the desktop window uses to fill "
        "its step list while the run is still going; the rest of the package neither knows nor cares "
        "that a front end exists, and a listener that raises is logged and ignored rather than being "
        "allowed to take a run down."
    )
    d.code(
        "def emit(self, kind, payload):\n"
        "    if self.listener is None:\n"
        "        return\n"
        "    try:\n"
        "        self.listener(kind, payload)\n"
        "    except Exception as exc:   # a broken front end must not fail the run\n"
        "        log.debug('run listener raised on %s: %s', kind, exc)",
        caption="artifacts.py",
    )

    # ===================================================================== #
    d.page_break()
    d.h1("4. Reading the image \u2014 src/f2c/extract/")

    d.h2("ocr.py \u2014 turning pixels into positioned words")
    d.p(
        "EasyOCR is the primary engine, Tesseract the fallback, and the choice is per-run "
        "(`F2C_OCR_ENGINE=auto|easyocr|tesseract`). Both produce the same `OcrWord` objects, so "
        "nothing downstream knows which one ran."
    )
    d.h3("What it does, step by step")
    d.numbers([
        "**Upscale 2\u00d7 with LANCZOS** and lower the detection thresholds. At native size EasyOCR "
        "silently dropped both single-digit quantities; at 2\u00d7 it reads them at confidence 1.00.",
        "**Divide every coordinate back down** by the upscale factor, so positions are in the "
        "original image's frame and can be compared to anything else.",
        "**Cluster words into visual lines** (`group_lines`) by vertical proximity, with the "
        "tolerance scaled to the word's own height rather than fixed.",
        "**Re-render at scaled horizontal positions** (`render_layout`) to about 110 characters "
        "wide, so the item table still looks like a table.",
        "**Expose the confidence** \u2014 `mean_confidence()` and `low_confidence_words()` \u2014 which is "
        "what the desktop window's statistics strip shows.",
    ])
    d.p(
        "`cross_check()` is the reverse direction, used when a vision model did the extraction: it "
        "asserts that every value in the `OrderDoc` literally appears in the OCR text, and reports "
        "the ones that do not."
    )

    d.h2("schema.py \u2014 the form the model must fill in")
    d.p(
        "`ORDER_TOOL` is a JSON schema handed to the model as a **tool definition**, so the response "
        "shape is enforced by the API rather than by a parser. Every numeric field is typed "
        "`string`, on purpose: the model transcribes digits, and `pipeline._num` parses them into "
        "`Decimal`. A JSON float would silently destroy cents."
    )
    d.p(
        "`SYSTEM_PROMPT` says the thing that matters most: transcribe, never compute. If a printed "
        "total is wrong, the model must report the wrong total \u2014 because the reconciliation step is "
        "what is supposed to notice, and a model that quietly 'fixes' the document destroys the "
        "only check that exists.", muted=True,
    )

    d.h2("llm.py \u2014 asking the model")
    d.p(
        "Two providers behind one interface. `groq` posts to the OpenAI-compatible "
        "chat-completions endpoint with `tools` and a forced `tool_choice`; `anthropic` uses the "
        "Messages API with the same tool. Both have a text path (`structure`) and an image path "
        "(`structure_from_image`); the default here is Groq over OCR text, because the Groq account "
        "in use serves no vision model."
    )
    d.bullets([
        "`temperature=0`, and a retry loop that does **not** retry a 4xx \u2014 a bad request will not "
        "fix itself.",
        "`_loads_loose` recovers JSON from the message content, because some models answer with "
        "JSON in `content` instead of a tool call.",
        "The raw tool payload is cached under `out/extraction-cache/<image-hash>.json`, next to "
        "`<hash>.ocr.txt`, so a disputed field can be traced back to what was actually read.",
    ])

    d.h2("validate.py \u2014 the arithmetic gate")
    d.p("Runs before any window opens, entirely in `Decimal`, and raises rather than proceed:")
    d.code(
        "per line   qty x unit_net x (1 - disc/100)  ==  the printed line total   (exact)\n"
        "sum        sum(lines)                       ==  the printed net total    (1 cent)\n"
        "VAT        sum(line x vat/100)              ==  the printed VAT total    (1 cent)\n"
        "gross      net + VAT                        ==  the printed gross total  (1 cent)\n"
        "payment    PAID  <=>  a payment date exists                (spec 5.3)\n"
        "mapping    the payment method has a Fakturama code          (spec 2.10.4)"
    )
    d.p(
        "Lines are exact; the totals get one cent of slack because some source documents round per "
        "line and some round on the sum.", muted=True,
    )

    d.h2("pipeline.py \u2014 tying extraction together")
    d.p(
        "`extract_order()` is the public entry point: cache lookup by image hash, the configured "
        "mode (`ocr` or `vision`), normalisation, the optional OCR cross-check, then reconciliation. "
        "`_num` is the quiet workhorse \u2014 it strips currency symbols, percent signs and non-breaking "
        "spaces, and resolves `1.234,56` against `1,234.56` by comparing where the last separator is."
    )

    # ===================================================================== #
    d.page_break()
    d.h1("5. The desktop front end \u2014 src/f2c/gui/")
    d.p(
        "Three files, and one rule that shapes all of them. Tkinter is not thread safe: a "
        "background thread touching a widget takes the interpreter down rather than raising. So "
        "everything slow runs on a worker thread that communicates **only** by putting messages on "
        "a `queue.Queue`, and `App._pump` \u2014 scheduled every 60 ms on the main loop \u2014 is the single "
        "place in the module that touches a widget."
    )
    d.image(DIAGRAMS / "08_frontend.png",
            "The window, and the narrow surface it uses to reach the core.", 1.0)
    d.image(SHOTS / "gui-02-ocr.png",
            "Panel 1: the OCR transcription, with its positions preserved.", 0.96)

    d.table(
        ["File", "What is in it", "Size"],
        [
            ["`theme.py`", "The palette, the resolved fonts and every ttk style. One vocabulary of "
                           "colour \u2014 the status colours used by the stage badges are the same ones "
                           "the step rows use.", _lines("src/f2c/gui/theme.py")],
            ["`widgets.py`", "The composites Tkinter does not have: `Card`, `Chip`, `StageItem`, "
                             "`MonoPanel`, `Metric`.", _lines("src/f2c/gui/widgets.py")],
            ["`app.py`", "The window, the three panels, the worker bodies and `_pump`.", _lines("src/f2c/gui/app.py")],
        ],
        widths=[0.16, 0.68, 0.16],
    )

    d.h2("The three panels")
    d.numbers([
        "**Read the image.** `extract.ocr.read_image` on the worker thread; the panel shows the "
        "layout-preserving transcription plus engine, word count, mean confidence and how many "
        "words fell below 0.55.",
        "**Validate with Groq.** `llm.structure` \u2192 `pipeline.normalise` \u2192 "
        "`validate.reconcile(strict=False)`. The panel shows the structured fields as a tree, the "
        "reconciliation recomputed line by line, and the corrections list.",
        "**Drive Fakturama.** `flow.orchestrator.run_flow(..., listener=...)`. Every "
        "`step-begin` / `step-end` / `screenshot` message becomes a row, and selecting a row shows "
        "that step's screenshot and detail.",
    ])

    d.h2("The corrections list, and why it is not a badge")
    d.p(
        "`App._corrections` normalises the whole OCR reading into one string and asks, of every "
        "extracted value, whether it appears there character for character. The ones that do not "
        "are what the model contributed \u2014 and they are exactly what a reviewer should be shown."
    )
    d.p(
        "On the sample document the list comes back **empty**, and that is informative rather than "
        "dull. The OCR reads the e-mail as `martaklein@example.test` — the dot between the given "
        "and family name is a few pixels and falls below the detector's threshold — and the "
        "model, told never to invent or repair a value, returns exactly that. The empty list is "
        "how a reviewer can see that the model added nothing at all: no silent fix to the e-mail, "
        "and equally no silent fix to a total.", muted=True,
    )
    d.image(SHOTS / "gui-03-groq.png", "Panel 2: fields, reconciliation, and what the model corrected.", 0.96)

    # ===================================================================== #
    d.page_break()
    d.h1("6. Driving the screen \u2014 src/f2c/ui/")

    d.h2("The four-tier approach to finding a control")
    d.image(DIAGRAMS / "03_tiers.png", "Tried in order; the winning tier is recorded per resolution.", 1.0)

    d.h2("selectors.yaml \u2014 the address book of controls")
    d.p(
        "%d logical control names, %d strategies. Re-grounding for a new Fakturama version is a "
        "data change, not a code change, and the file is the single place where 'what the control "
        "is called' lives." % (controls, strategies)
    )
    d.code(
        "order.custref_field:                     # spec 1.6  (this Edit is named)\n"
        "  - {tier: uia, type: Edit, name: \"Cust.Ref.\", scope: order_editor}\n"
        "  - {tier: anchor, anchor_text: \"Cust.Ref.\", direction: right, type: Edit, scope: order_editor}\n"
        "\n"
        "order.address_select_icon:               # spec 2.1 - the UPPER icon\n"
        "  - {tier: uia, type: Image, scope: order.addresses_group, pick: topmost}\n"
        "  - {tier: template, image: \"icons/select_contact.png\", scope: order.addresses_group,\n"
        "     pick: topmost, threshold: 0.86}",
        caption="two entries from selectors.yaml",
    )
    d.p(
        "Scopes are themselves logical names, so a control can be declared relative to a container "
        "that is itself resolved by four tiers. `tests/test_selectors.py` proves that every name the "
        "flow uses exists, that every scope resolves, and that a pixel tier is never a control's "
        "first strategy."
    )

    d.h2("resolver.py \u2014 the engine that does the finding")
    d.h3("Special abilities it grew, each for a real reason")
    d.bullets([
        "**`{order_number}` placeholders.** Fakturama renames a document editor when it is saved: "
        "`New Order` becomes `%s`. Every selector that identifies the editor by name therefore "
        "stops matching at the exact moment the Order starts to exist. `Ctx.remember` puts the "
        "number \u2014 read from the form in step 1.4 \u2014 into `Resolver.values`, and a strategy whose "
        "placeholder is not yet known is **dropped** rather than matched as an empty name."
        % (state.get("order_number") or "PO000001"),
        "**A scope cache with an on-screen check.** Resolving a container is expensive, so it is "
        "cached \u2014 but a container that has since been parked (an editor that is no longer the "
        "active tab) must not keep serving its children, or writes go into a window nobody can see.",
        "**A short timeout when resolving a scope.** A scope is resolved inside the caller's own "
        "polling loop, so a full-length wait there would spend the entire budget inside a single "
        "poll. One run timed out 'after 20.0s (1 polls)' \u2014 it never got a second look at a dialog "
        "that was merely slow.",
        "**`pick:`** \u2014 `topmost`, `bottommost`, `largest`, `smallest`. This is how the spec's "
        "'upper icon, not the lower green +' is expressed without a coordinate.",
        "**Tier statistics.** Every resolution records which tier won, and the counts go into the "
        "run report: %s." % (", ".join("%s %d" % kv for kv in tiers.items()) or "\u2014"),
    ])

    d.h2("uia.py \u2014 the wrapper around Windows automation")
    d.h3("The most important thing in this file")
    d.p(
        "`PARKED_COORDINATE`. Eclipse does not destroy the widgets of a background editor \u2014 it "
        "moves them off-screen, to a large negative coordinate, and they keep answering UIA "
        "queries perfectly happily. An automation that does not know this will read from and type "
        "into an editor that is not on screen, and every write will silently do nothing."
    )
    d.p(
        "So `Element.on_screen` treats a parked rectangle as not-on-screen, and the resolver "
        "refuses to return a parked control unless a strategy explicitly asks for one. That single "
        "rule is what makes `Ctx._activate` able to *prove* an editor came to the front: resolving "
        "anything inside it is the proof."
    )
    d.p(
        "`text_of()` is the other workhorse. An SWT `Text` widget's value is often reachable only "
        "through `LegacyIAccessiblePattern`, not `ValuePattern`, so it tries every route in order.",
        muted=True,
    )

    d.h2("waits.py \u2014 never sleep, always wait for a condition")
    d.p(
        "There is no `sleep(2)` anywhere in the package. `wait_until` polls a predicate against a "
        "ceiling; `wait_stable` waits for a list to return the same signature twice, which is how "
        "'wait for the results to stabilise' in spec 2.2 is implemented; `wait_gone` waits for a "
        "dialog to close."
    )

    d.h2("vision_locator.py \u2014 pixels, but only inside a known box")
    d.p(
        "Every function takes a `Rect` that came from a UIA element and returns `Rect`s in screen "
        "coordinates. Nothing here knows a constant screen position: the search region is always "
        "resolved at run time from the accessibility tree, and offsets are always relative to it. "
        "That is what satisfies 'no hardcoded coordinates, no fixed UI layout' while still being "
        "able to reach a canvas with no accessibility children."
    )

    d.h2("grid.py \u2014 reading and writing table-like controls")
    d.p("Two very different situations share this file.")
    d.bullets([
        "`read_uia_rows` handles the **dialog** lists, which are SWT `Table` widgets backed by "
        "native Win32 list-views. UIA sees real rows and cells, so they are read semantically.",
        "`CanvasGrid` handles the **item grid** in the document editor, which is a custom-drawn "
        "NatTable: a single `Canvas` with no accessibility children. The table is reconstructed "
        "geometrically from the control's own rendered rulings (preferred) or from clustered OCR "
        "text (fallback), and cells are addressed by `(row, column)` in coordinates derived at run "
        "time from the canvas rectangle.",
    ])
    d.note(
        "The way through the canvas is that **clicking a cell makes the application create a real "
        "editor control for it**, which does appear in the accessibility tree. So every value in an "
        "item line is typed into a genuine SWT control and read back from that same control \u2014 OCR "
        "is used to find the cell, never to write it or to check it.",
        "ok",
    )

    d.h2("app.py, dpi.py")
    d.p(
        "`FakturamaApp` launches or attaches, and identifies the window by **process plus shell "
        "class**, not by title \u2014 a File Explorer window showing a folder called "
        "`fakturama-image-to-cash` matched a title check and produced a memorably confusing "
        "failure. `normalise_window` restores a minimized window before grounding, because a "
        "minimized window reports an empty rectangle for every control. `dpi.py` sets per-monitor "
        "DPI awareness before the first UIA call, or every rectangle is silently scaled."
    )

    # ===================================================================== #
    d.page_break()
    d.h1("7. The business steps \u2014 src/f2c/flow/")
    d.image(DIAGRAMS / "04_stages.png", "The stages and their detours.", 1.0)

    d.h2("context.py \u2014 the shared toolbox")
    d.p(
        "`Ctx` holds the document, the application, the resolver and the artifacts, and exposes the "
        "small vocabulary a stage is allowed to use: `el`, `maybe`, `click`, `type_into`, `select`, "
        "`expect`, `expect_contains`, `stop_for_review`, `activate_order`, `open_editor`, `save`. "
        "Keeping the verbs here rather than letting stages talk to the resolver directly is what "
        "makes every interaction logged, screenshotted and \u2014 where it writes \u2014 read back."
    )
    d.h3("`step` versus `retried_step`")
    d.p(
        "`step` runs a body once. `retried_step` runs it until it works. The distinction is a "
        "safety property, not a convenience: a body may only be replayed if running it twice is "
        "indistinguishable from running it once. Setting a field, choosing from a dropdown, opening "
        "a dialog that is checked for first, reading a value back \u2014 all replayable. Anything that "
        "**creates a record or presses Save** uses `step` and gets exactly one attempt."
    )
    d.h3("`precondition`")
    d.p(
        "For the harder case: a body whose goal may already have been reached by the attempt that "
        "failed. The Product picker **appends** an item line rather than setting one, so an attempt "
        "that selected the product and then failed on the way out had already done its job; "
        "replaying it put the same product on the Order twice. The check runs before every retry, "
        "never before the first attempt, and a true answer finishes the step."
    )
    d.h3("`_activate` \u2014 and why it proves rather than assumes")
    d.p(
        "Every master-data detour opens an editor on top of the Order, and the spec is explicit "
        "that the Order tab stays open and is returned to. `_activate` clicks the tab and then "
        "**resolves something inside the editor** as proof it arrived. Because the resolver refuses "
        "parked controls, a tab click that the platform swallowed shows up here immediately, "
        "instead of as a silent write into an invisible editor."
    )

    d.h2("formats.py \u2014 speaking the application's language")
    d.p(
        "Fakturama renders dates and decimals according to the JVM locale, which is not knowable "
        "ahead of time. Rather than assume, the field is **read before it is written** and its "
        "existing content is used to pick the format. That is why `order.date_field` is read first."
    )

    d.h2("dialogs.py \u2014 the two pickers, and the matching rules")
    d.p(
        "Type the query, wait for the list to stop changing, read the rows, decide. The deciding is "
        "where the care is."
    )
    d.h3("Three matching rules, each added because of a real failure")
    d.bullets([
        "**A cell may be clipped by its column.** A list column that is too narrow renders "
        "`Northstar Office G\u2026`. A clipped cell is allowed to match a prefix of the expected value, "
        "but only when it is actually shorter \u2014 never as a blanket prefix rule.",
        "**`Berlin` must not match `Berlin-Mitte`.** The blanket prefix rule that would have "
        "allowed it is the reason the previous point is worded so carefully, and there is a test "
        "named after it.",
        "**OCR confusables are folded.** `0`/`O`, `1`/`l`/`I`, `5`/`S` are folded before comparing "
        "a canvas-read cell \u2014 and a test proves the fold does not merge two distinct SKUs.",
    ])

    d.h2("order_header.py \u2014 Stage 1")
    d.p(
        "Opens the New Order, reads the proposed number and hands it to `Ctx.remember` (this is "
        "where `{order_number}` comes from), sets the Date and the Cust.Ref., sets the price mode "
        "to Net and leaves VAT as With VAT. The proposed No. is read and never written."
    )

    d.h2("debtor.py \u2014 Stage 2, the most involved one")
    d.image(DIAGRAMS / "05_debtor.png", "Select, or create and then select.", 0.90)
    d.p(
        "The selector is the existence check. An exact match needs Company, First Name, Name, ZIP "
        "and City to agree. One exact row is selected; conflicting rows stop the run; no row goes "
        "to the creation branch \u2014 which ends by **going back to the same picker and selecting the "
        "new Debtor there**, because that is both what the spec says and the only proof the record "
        "was really saved."
    )

    d.h2("prerequisites.py \u2014 Stage 0, and an honest deviation")
    d.p(
        "The spec creates missing master data while the document editor is open. This build's "
        "dropdowns are filled once, when the editor opens, and a record created afterwards is "
        "simply not offered \u2014 verified directly: after creating `Bank Transfer`, the Debtor's "
        "Payment dropdown still listed only the pre-existing entry, and a Product created after "
        "`VAT 19%` existed still saved against `Tax-free`."
    )
    d.p(
        "So the same checks, with the same matching and the same manual-review gates, run **first**. "
        "Nothing is created that the spec would not have created; only the moment differs, and the "
        "run report records that a prerequisite was made here rather than mid-flow.", muted=True,
    )

    d.h2("payment.py and vat.py \u2014 creating supporting records")
    d.p(
        "Both follow the same shape: search the exact name, reuse one unambiguous exact row, stop "
        "for review on a conflict, create on absence. `payment.py` applies the spec 2.10.4 mapping "
        "(`Bank Transfer \u2192 Credit transfer`); `vat.py` only reuses a row when the name, the value "
        "**and** the e-invoice code `S (Standard rate)` all agree."
    )

    d.h2("product.py and items.py \u2014 Stage 3")
    d.p(
        "`product.py` runs the selection branch per item in source order, creates the Product with "
        "the spec 3.9 gross price when the SKU is absent, and returns to the Order to select it. "
        "`items.py` then completes the line: quantity, unit price, VAT, discount, and a check that "
        "the line price equals `qty \u00d7 unit \u00d7 (1 \u2212 disc/100)`."
    )

    d.h2("finalize_order.py and invoice.py \u2014 Stages 4 and 5")
    d.p(
        "Stage 4 confirms the totals against the image, saves once, verifies the row in "
        "Data \u203a Documents, and opens the Invoice **from the Order's follow-up area** \u2014 never from "
        "the top toolbar, because only the follow-up action preserves the Order relationship. That "
        "step begins by bringing the Order back to the front, since verifying in Data \u203a Documents "
        "leaves the Documents list covering the editor."
    )
    d.p(
        "Stage 5 confirms the Invoice was populated from the Order, sets the payment method, and \u2014 "
        "only when the extracted status is PAID \u2014 marks it paid with the extracted date and the "
        "full total. If the status is not PAID it leaves the flag clear and invents nothing."
    )

    d.h2("orchestrator.py \u2014 the conductor")
    d.p(
        "Launches or attaches, builds the `Ctx`, runs the six stage functions in order, and "
        "guarantees that a report is written whatever happens \u2014 `ManualReviewRequired` becomes "
        "status `manual-review`, any other exception becomes `failed`, and both still get the "
        "closing screenshot, the flow state and the tier statistics."
    )

    # ===================================================================== #
    d.page_break()
    d.h1("8. Checking the work \u2014 src/f2c/verify/")
    d.image(DIAGRAMS / "06_verification.png", "Two oracles that can disagree with each other.", 1.0)
    d.h2("ui_readback.py \u2014 reading the screen back")
    d.p(
        "Used inside the flow, where each stage verifies its own post-condition, and available "
        "standalone through `f2c verify`, which re-opens Data \u203a Documents and confirms a completed "
        "run without repeating it."
    )
    d.h2("db.py \u2014 reading Fakturama's own data files")
    d.p(
        "Fakturama 2.2.0 ships **HSQLDB 2.7.4**, not H2. HSQLDB keeps MEMORY tables as plain-text "
        "SQL in `Database.script` and everything committed since the last checkpoint as plain-text "
        "SQL in `Database.log`. So the oracle is a parser, not a driver: no JDBC jar, no JPype, no "
        "exclusive-lock problem, and rows that have not yet been checkpointed are still visible."
    )
    d.note(
        "This is the layer that proved the `NULL` company bug. The screen read back correctly and "
        "the save reported success; the stored row had `NULL`. Only an independent oracle could "
        "have caught it.",
        "err",
    )

    # ===================================================================== #
    d.h1("9. Supporting files")
    d.h2("scripts/")
    d.bullets([
        "`snapshot_workspace.py` / `reset_workspace.py` \u2014 make runs repeatable. Only `Database/` "
        "and `Templates/` are copied, so the workspace may be a drive root, and the current "
        "workspace is always moved aside rather than deleted.",
        "`record_screen.py` \u2014 records the demo to mp4 using `mss` and OpenCV, both of which are "
        "already dependencies. The encoder runs on its own thread with a bounded queue, so a slow "
        "encode drops frames rather than stretching the wall-clock time of what is being recorded.",
        "`docx_style.py`, `build_design_report.py`, `build_code_walkthrough.py`, "
        "`build_screenshot_guide.py` \u2014 the Word deliverables are generated from the repository and "
        "from a run report, so the prose cannot claim something the code no longer does.",
    ])
    d.h2("tests/ — %d tests, no Fakturama required" % tests)
    d.table(
        ["File", "What it protects"],
        [
            ["`test_extraction.py`", "Normalisation and reconciliation, including deliberately corrupted totals."],
            ["`test_matching.py`", "Exact-match and VAT-reuse decisions; clipped columns; OCR confusables."],
            ["`test_geometry.py`", "Anchor-relative resolution and grid addressing, with no live application."],
            ["`test_formats.py`", "Locale-tolerant date and decimal formatting."],
            ["`test_selectors.py`", "Registry integrity: every name used exists, every scope resolves, pixels are never first."],
            ["`test_retry.py`", "The replay harness: attempt counts, preconditions, and that a non-replayable step is never repeated."],
            ["`test_no_hardcoded_coords.py`", "Parses the AST of every module to prove no pointer call receives a numeric literal."],
        ],
        widths=[0.3, 0.7],
    )

    # ===================================================================== #
    d.page_break()
    d.h1("10. One complete run, traced through the code")
    if steps:
        trace = [
            ["`cli.run` / `gui.App._work_ui`", "reads `.env`, extracts, calls `run_flow`"],
            ["`extract.pipeline.extract_order`", "OCR \u2192 Groq tool call \u2192 `OrderDoc` \u2192 reconcile"],
            ["`flow.orchestrator.run_flow`", "launches Fakturama, builds `Ctx`, runs the stages"],
            ["`flow.prerequisites`", "creates `Bank Transfer` and `VAT 19%` before any editor opens"],
            ["`flow.order_header`", "New Order; reads `%s`; Date, Cust.Ref., Net, With VAT"
             % (state.get("order_number") or "the proposed number")],
            ["`flow.debtor`", "picker \u2192 absent \u2192 New Debtor \u2192 save \u2192 back to the picker \u2192 selected"],
            ["`flow.product` \u00d7 2", "picker \u2192 absent \u2192 New product (gross 297.50 / 47.60) \u2192 save \u2192 selected"],
            ["`flow.items` \u00d7 2", "qty, unit price, VAT, discount; line price verified"],
            ["`flow.finalize_order`", "totals confirmed, saved once, verified in Data \u203a Documents"],
            ["`flow.invoice`", "follow-up Invoice, payment method, paid + date + value, saved, verified"],
            ["`artifacts.RunArtifacts.finish`", "`report.md`, `report.json`, screenshots, tier statistics"],
        ]
        d.table(["Where", "What happens"], trace, widths=[0.36, 0.64])
        d.h2("What the run produced")
        d.code(
            "status              %s\n"
            "steps               %d, of which %d ok\n"
            "grounding tiers     %s\n"
            "order / invoice     %s / %s"
            % (
                report.get("status", "?"),
                len(steps),
                sum(1 for s in steps if s["status"] == "ok"),
                ", ".join("%s %d" % kv for kv in tiers.items()) or "\u2014",
                state.get("order_number") or "?",
                state.get("invoice_number") or "?",
            )
        )

    # ===================================================================== #
    d.h1("11. The bugs that shaped the code")
    d.p(
        "The first one shaped the entire write path. Every value the automation types goes through "
        "the loop below, and a write is not considered finished until it has been read back."
    )
    d.image(DIAGRAMS / "07_write_value.png",
            "Writing one value: bring the application forward, resolve, type with real keys, read back.",
            1.0)
    d.p("Each of these was found against the live application, and each is commented at its fix site.")
    d.table(
        ["Symptom", "Cause", "What changed"],
        [
            ["Company, Alias and Country read back correctly, saved without complaint, and stored as `NULL`.",
             "`ValuePattern.SetValue` updates an SWT widget's text without firing its modify listener.",
             "Writes are real key events by default; the database oracle exists to catch the class."],
            ["Company saved as `\"Northstar Office GmbH\\t\"`.",
             "A commit `{Tab}` is inserted as content by multi-line fields.",
             "The generic commit key was removed \u2014 real keystrokes already fire the listener."],
            ["`Jul 14, 2026` typed into the date field became `Sep 20, 0026`.",
             "The date control is segmented and re-parses on every keystroke.",
             "A continuous digit run is typed from the first segment, in the widget's own order."],
            ["Clicks on the selector icons did nothing at all.",
             "SWT image buttons swallow a press that arrives in the same instant as the cursor.",
             "The click moves, settles, then presses; dialog opening retries."],
            ["The automation drove File Explorer.",
             "A window-title match \u2014 the Explorer tab was titled `fakturama-image-to-cash`.",
             "Identification is by process plus shell class."],
            ["Every control reported an empty rectangle.",
             "A minimized window.",
             "`normalise_window` restores before grounding."],
            ["Writes went into an editor that was not on screen, silently.",
             "Eclipse parks a background editor's widgets off-screen; they keep answering UIA.",
             "`PARKED_COORDINATE`; the resolver refuses parked controls; `_activate` proves arrival."],
            ["A run timed out `after 20.0s (1 polls)` on a dialog that was merely slow.",
             "A full-length scope resolution inside the caller's own polling loop.",
             "Scope resolution fails fast so the outer loop can poll."],
            ["The same product appeared on the Order twice.",
             "The picker appends a line; a retry after a late failure appended a second one.",
             "`precondition` on the replay harness."],
            ["Step 4.6 could not find the follow-up area.",
             "4.5 verifies in Data \u203a Documents, which leaves that list in front of the editor \u2014 and "
             "the saved editor has been renamed from `New Order` to its document number.",
             "4.6 activates the Order first; `order_editor` carries the `{order_number}` strategy."],
        ],
        widths=[0.30, 0.36, 0.34],
        font_size=8.5,
    )

    return d.save(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    # Default into the repository: the documents are deliverables, so they
    # belong with the code they are generated from. Use --out for anywhere else.
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "Fakturama_Code_Walkthrough.docx")
    ap.add_argument("--run", type=Path, default=None)
    args = ap.parse_args()

    run_dir = args.run or latest_run(status="ok") or latest_run()
    path = build(args.out, run_dir)
    print("wrote %s  (%.0f KB)  from run %s" % (path, path.stat().st_size / 1024, run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
