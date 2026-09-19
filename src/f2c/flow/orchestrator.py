"""The state machine that runs the five stages in one continuous Order-first flow."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from ..artifacts import RunArtifacts
from ..config import SETTINGS
from ..errors import ManualReviewRequired
from ..logging_setup import get
from ..models import OrderDoc
from ..ui.app import FakturamaApp
from ..ui.resolver import Resolver
from . import debtor, finalize_order, invoice, items, order_header, prerequisites
from .context import Ctx

log = get("flow.orchestrator")

STAGES = (
    ("0", "master-data prerequisites", prerequisites.run),
    ("1", "order header", order_header.run),
    ("2", "debtor", debtor.run),
    ("3", "products and item lines", items.run),
    ("4", "complete and save the order", finalize_order.run),
    ("5", "linked invoice", invoice.run),
)


def run_flow(
    doc: OrderDoc,
    dry_run: bool = False,
    launch: bool = True,
    stop_after: Optional[str] = None,
    verify_db: bool = False,
    listener: Optional[Callable[[str, Any], None]] = None,
) -> RunArtifacts:
    """Run the five stages.

    `listener` receives ``(kind, payload)`` as the run happens - the desktop UI
    uses it to fill its step list live. See `artifacts.RunArtifacts.emit`.
    """
    art = RunArtifacts(label="run", listener=listener)
    art.extra["source"] = doc.model_dump(mode="json")

    app = FakturamaApp()
    startup = art.begin("0.1", "Launch or attach to Fakturama")
    try:
        window = app.launch() if launch else app.attach()
        app.normalise_window()
        art.end(startup, "ok", "main window %r" % window.name)
    except Exception as exc:
        art.end(startup, "failed", str(exc))
        art.record_failure(exc, None, "startup")
        art.finish("failed-to-start")
        raise

    resolver = Resolver(app.window)
    ctx = Ctx(doc=doc, app=app, resolver=resolver, art=art, dry_run=dry_run)
    art.screenshot("00-before")

    status = "ok"
    try:
        for stage_id, title, fn in STAGES:
            log.info("=== stage %s: %s ===", stage_id, title)
            fn(ctx)
            if stop_after and stage_id == stop_after:
                log.info("stopping after stage %s as requested", stage_id)
                status = "stopped-early"
                break
    except ManualReviewRequired as exc:
        log.error("MANUAL REVIEW REQUIRED: %s", exc)
        log.error("context: %s", exc.context)
        status = "manual-review"
    except Exception as exc:
        log.exception("flow failed: %s", exc)
        status = "failed"
    finally:
        art.screenshot("99-after")
        art.extra["flow_state"] = ctx.state.as_dict()
        if verify_db and status in ("ok", "stopped-early"):
            _run_db_verification(ctx, art)
        art.finish(status, resolver.stats.as_dict())

    return art


def _run_db_verification(ctx: Ctx, art: RunArtifacts) -> None:
    from ..verify.db import verify_against_database

    rec = art.begin("V", "Verify the persisted records in Fakturama's database")
    try:
        result = verify_against_database(ctx.doc)
        art.extra["db_verification"] = result
        art.end(rec, "ok" if result.get("ok") else "failed", result.get("summary", ""))
    except Exception as exc:
        art.end(rec, "skipped", "database verification unavailable: %s" % exc)
