"""Command line entry point.

    f2c gui                                     <- the desktop front end
    f2c run          --image fixtures/sample_order.png
    f2c extract      --image fixtures/sample_order.png
    f2c verify       --image fixtures/sample_order.png
    f2c inspect      --dump out/tree.txt
    f2c capture-icon --name select_contact --scope order_editor
    f2c doctor
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .config import SETTINGS
from .logging_setup import setup

app = typer.Typer(add_completion=False, help="Fakturama Image-to-Cash automation")
console = Console()

DEFAULT_IMAGE = Path("fixtures/sample_order.png")


def _boot(verbose: bool) -> None:
    setup(logging.DEBUG if verbose else logging.INFO)


# --------------------------------------------------------------------------- #
@app.command()
def gui(
    image: Optional[Path] = typer.Option(None, "--image", "-i", help="pre-load this image"),
    run: bool = typer.Option(False, "--run", help="start the pipeline as soon as the window opens"),
    report: Optional[Path] = typer.Option(None, "--report", help="open a finished run instead"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Open the desktop front end: upload an image, watch OCR, the Groq
    validation and the Fakturama run, in one window.

    With --report, show a finished run's step list without repeating it.
    """
    _boot(verbose)
    from .gui.app import main as gui_main

    gui_main(
        str(image) if image else None,
        auto_run=run,
        report=str(report) if report else None,
    )


# --------------------------------------------------------------------------- #
@app.command()
def extract(
    image: Path = typer.Option(DEFAULT_IMAGE, "--image", "-i", help="order image"),
    from_json: Optional[Path] = typer.Option(None, "--from-json", help="reuse a saved extraction"),
    no_cache: bool = typer.Option(False, "--no-cache", help="ignore the extraction cache"),
    ocr_check: bool = typer.Option(False, "--ocr-check", help="cross-check with OCR"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="write the OrderDoc as JSON"),
    lenient: bool = typer.Option(False, "--lenient", help="report reconciliation problems but continue"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Image -> validated OrderDoc. Touches no UI."""
    _boot(verbose)
    from .extract import extract_order
    from .extract.validate import summary

    doc = extract_order(
        image,
        use_cache=not no_cache,
        ocr_check=ocr_check,
        strict=not lenient,
        from_json=from_json,
    )
    console.print(summary(doc))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        console.print("[green]wrote[/green] %s" % out)


# --------------------------------------------------------------------------- #
@app.command()
def run(
    image: Path = typer.Option(DEFAULT_IMAGE, "--image", "-i"),
    from_json: Optional[Path] = typer.Option(None, "--from-json"),
    dry_run: bool = typer.Option(False, "--dry-run", help="resolve controls but never write"),
    no_launch: bool = typer.Option(False, "--no-launch", help="attach to a running instance only"),
    stop_after: Optional[str] = typer.Option(None, "--stop-after", help="stage id: 1..5"),
    verify_db: bool = typer.Option(False, "--verify-db", help="also verify against Fakturama's stored data"),
    ocr_check: bool = typer.Option(False, "--ocr-check"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """The full Order-first flow: image -> saved, verified Order + linked Invoice."""
    _boot(verbose)
    from .extract import extract_order
    from .flow.orchestrator import run_flow

    doc = extract_order(image, ocr_check=ocr_check, from_json=from_json)
    art = run_flow(
        doc,
        dry_run=dry_run,
        launch=not no_launch,
        stop_after=stop_after,
        verify_db=verify_db,
    )
    report = json.loads((art.dir / "report.json").read_text(encoding="utf-8"))
    status = report["status"]
    console.print("\n[bold]status:[/bold] %s" % status)
    console.print("artifacts: %s" % art.dir)
    raise typer.Exit(0 if status in ("ok", "stopped-early") else 1)


# --------------------------------------------------------------------------- #
@app.command()
def verify(
    image: Path = typer.Option(DEFAULT_IMAGE, "--image", "-i"),
    from_json: Optional[Path] = typer.Option(None, "--from-json"),
    database: bool = typer.Option(False, "--database", help="also read Fakturama's stored data"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Verify an already-completed run without repeating it."""
    _boot(verbose)
    from .extract import extract_order
    from .verify.ui_readback import verify_documents

    doc = extract_order(image, from_json=from_json)
    result = verify_documents(doc)
    console.print_json(data=result)

    if database:
        from .verify.db import verify_against_database

        try:
            console.print_json(data=verify_against_database(doc))
        except Exception as exc:
            console.print("[yellow]database verification unavailable:[/yellow] %s" % exc)

    raise typer.Exit(0 if result["ok"] else 1)


# --------------------------------------------------------------------------- #
@app.command("inspect")
def inspect_tree(
    dump: Path = typer.Option(Path("out/tree.txt"), "--dump", "-d"),
    depth: int = typer.Option(10, "--depth"),
    scope: Optional[str] = typer.Option(None, "--scope", help="a logical scope from selectors.yaml"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Dump the live UIA tree - the calibration tool for selectors.yaml."""
    _boot(verbose)
    from .ui.app import FakturamaApp
    from .ui.resolver import Resolver
    from .ui.uia import dump_tree

    fak = FakturamaApp()
    fak.attach()
    root = fak.window()
    if scope:
        root = Resolver(fak.window).resolve(scope, timeout=10)

    text = dump_tree(root, max_depth=depth)
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_text(text, encoding="utf-8")
    console.print("wrote %d lines to %s" % (len(text.splitlines()), dump))


# --------------------------------------------------------------------------- #
@app.command("resolve")
def resolve_one(
    name: str = typer.Argument(..., help="logical control name from selectors.yaml"),
    shoot: bool = typer.Option(True, "--shoot/--no-shoot", help="save a screenshot of the hit"),
    verbose: bool = typer.Option(True, "--verbose", "-v"),
):
    """Resolve a single control and report which tier won. Calibration aid."""
    _boot(verbose)
    from .ui.app import FakturamaApp
    from .ui.resolver import Resolver
    from .ui.vision_locator import save_screenshot

    fak = FakturamaApp()
    fak.attach()
    fak.normalise_window()
    resolver = Resolver(fak.window)
    el = resolver.resolve(name, timeout=10)
    console.print("[green]resolved[/green] %s -> %r" % (name, el))
    console.print("tier usage: %s" % resolver.stats.as_dict())
    if shoot:
        out = SETTINGS.out_dir / "resolve" / ("%s.png" % name.replace(".", "_"))
        save_screenshot(out, el.rect.inset(-6))
        console.print("screenshot: %s" % out)


# --------------------------------------------------------------------------- #
@app.command("capture-icon")
def capture_icon(
    name: str = typer.Option(..., "--name", help="icon file name, without .png"),
    scope: str = typer.Option("order_editor", "--scope"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Save a screenshot of a scope so an icon template can be cropped from it.

    Template assets are the only binary part of the grounding strategy, so they
    are captured from the machine that will run the automation rather than
    shipped blind.
    """
    _boot(verbose)
    from .ui.app import FakturamaApp
    from .ui.resolver import Resolver
    from .ui.vision_locator import save_screenshot

    fak = FakturamaApp()
    fak.attach()
    fak.normalise_window()
    el = Resolver(fak.window).resolve(scope, timeout=10)
    out = SETTINGS.assets_dir / "icons" / ("_capture_%s.png" % name)
    save_screenshot(out, el.rect)
    console.print(
        "wrote %s\nCrop the icon from it and save as assets/icons/%s.png" % (out, name)
    )


# --------------------------------------------------------------------------- #
@app.command()
def doctor(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Check the environment: dependencies, executable, workspace, selectors."""
    _boot(verbose)
    ok = True

    console.print("[bold]python[/bold]           %s" % sys.version.split()[0])

    for module, why in (
        ("pydantic", "models"),
        ("yaml", "selector registry"),
        ("uiautomation", "UI automation (required to drive Fakturama)"),
        ("comtypes", "UIA patterns"),
        ("win32api", "window management"),
        ("PIL", "screenshots"),
        ("mss", "fast screenshots"),
        ("cv2", "icon template matching"),
        ("numpy", "grid detection"),
        ("requests", "Groq chat-completions"),
        ("easyocr", "OCR - the primary engine"),
        ("tkinter", "the desktop front end"),
        ("anthropic", "the Anthropic provider (optional)"),
        ("pytesseract", "OCR fallback (optional)"),
        ("docx", "generating the Word deliverables (optional)"),
    ):
        try:
            __import__(module)
            console.print("[green]  ok  [/green] %-14s %s" % (module, why))
        except Exception:
            optional = "optional" in why
            console.print(
                "%s %-14s %s" % ("[yellow]  --  [/yellow]" if optional else "[red] MISS [/red]", module, why)
            )
            if not optional and module in ("uiautomation", "pydantic", "yaml", "requests"):
                ok = False

    exe = SETTINGS.fakturama_exe
    console.print(
        "[bold]executable[/bold]       %s" % (exe if exe else "<F2C_FAKTURAMA_EXE not set>")
    )
    if exe and not Path(exe).exists():
        console.print("[red]  the configured executable does not exist[/red]")
        ok = False

    ws = SETTINGS.workspace
    console.print("[bold]workspace[/bold]        %s%s" % (ws, "" if Path(ws).exists() else "  (missing)"))

    try:
        from .verify.db import find_database

        console.print("[bold]database[/bold]         %s" % (find_database() or "<not found>"))
    except Exception as exc:
        console.print("[bold]database[/bold]         <%s>" % exc)

    try:
        from .ui.resolver import SELECTORS_FILE, load_selectors

        sel = load_selectors(SELECTORS_FILE)
        tiers = {}
        for strategies in sel.values():
            for s in strategies:
                tiers[s["tier"]] = tiers.get(s["tier"], 0) + 1
        console.print(
            "[bold]selectors[/bold]        %d logical controls, %d strategies %s"
            % (len(sel), sum(tiers.values()), tiers)
        )
    except Exception as exc:
        console.print("[red]selectors.yaml is invalid: %s[/red]" % exc)
        ok = False

    icons = sorted((SETTINGS.assets_dir / "icons").glob("*.png"))
    console.print(
        "[bold]icon templates[/bold]   %s"
        % (", ".join(p.stem for p in icons) if icons else "<none - run f2c capture-icon>")
    )

    console.print("\n[bold]%s[/bold]" % ("ready" if ok else "not ready - see the entries above"))
    raise typer.Exit(0 if ok else 1)


if __name__ == "__main__":
    app()
