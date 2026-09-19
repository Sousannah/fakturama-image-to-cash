"""Run artifacts: screenshots, step log, tier stats, failure dumps.

Every run gets its own directory under `out/`. The screenshots are numbered in
execution order, which is what makes them usable as the "annotated screenshots"
deliverable without any manual curation.
"""
from __future__ import annotations

import json
import re
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import SETTINGS
from .logging_setup import get

log = get("artifacts")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60]


@dataclass
class StepRecord:
    index: int
    spec_ref: str
    title: str
    status: str = "running"       # running | ok | skipped | failed | manual-review
    started: float = field(default_factory=time.monotonic)
    duration: float = 0.0
    detail: str = ""
    screenshot: Optional[str] = None
    #: How many times the step body ran. >1 means a transient failure was
    #: recovered by the retry harness rather than ending the run; the run
    #: report keeps the count so a "passing" run that limped is still visible.
    attempts: int = 1
    #: One line per recovered failure, in order.
    recovered: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out = {
            "index": self.index,
            "spec": self.spec_ref,
            "title": self.title,
            "status": self.status,
            "seconds": round(self.duration, 2),
            "detail": self.detail,
            "screenshot": self.screenshot,
        }
        if self.attempts > 1:
            out["attempts"] = self.attempts
            out["recovered"] = self.recovered
        return out


class RunArtifacts:
    """The evidence folder for one run.

    `listener` is how a front end watches a run happen. The desktop UI passes
    one in so the step list fills while the flow is still going; nothing else
    in the package knows or cares that it exists, and a listener that raises is
    ignored rather than being allowed to take a run down.
    """

    def __init__(self, label: str = "run", listener: Optional[Callable[[str, Any], None]] = None):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = SETTINGS.out_dir / ("%s-%s" % (label, stamp))
        self.shots = self.dir / "screenshots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.steps: List[StepRecord] = []
        self._n = 0
        self.started = datetime.now()
        self.extra: Dict[str, Any] = {}
        self.listener = listener
        log.info("run artifacts -> %s", self.dir)
        self.emit("run-start", {"dir": str(self.dir)})

    def emit(self, kind: str, payload: Any) -> None:
        if self.listener is None:
            return
        try:
            self.listener(kind, payload)
        except Exception as exc:  # a broken front end must not fail the run
            log.debug("run listener raised on %s: %s", kind, exc)

    # ------------------------------------------------------------------ #
    def begin(self, spec_ref: str, title: str) -> StepRecord:
        self._n += 1
        rec = StepRecord(index=self._n, spec_ref=spec_ref, title=title)
        self.steps.append(rec)
        log.info("[%s] %s", spec_ref, title)
        self.emit("step-begin", rec.as_dict())
        return rec

    def end(self, rec: StepRecord, status: str = "ok", detail: str = "") -> None:
        rec.status = status
        rec.detail = detail
        rec.duration = time.monotonic() - rec.started
        if detail:
            log.info("    %s (%s)", detail, status)
        self.emit("step-end", rec.as_dict())

    # ------------------------------------------------------------------ #
    def screenshot(self, name: str, region=None, rec: Optional[StepRecord] = None) -> Optional[Path]:
        try:
            from .ui.vision_locator import save_screenshot

            path = self.shots / ("%02d-%s.png" % (len(list(self.shots.glob("*.png"))) + 1, _slug(name)))
            save_screenshot(path, region)
            if rec is not None:
                rec.screenshot = path.name
            self.emit("screenshot", {"path": str(path), "name": name})
            return path
        except Exception as exc:
            log.debug("screenshot %r failed: %s", name, exc)
            return None

    def dump_tree(self, name: str, element) -> Optional[Path]:
        try:
            from .ui.uia import dump_tree

            path = self.dir / ("tree-%s.txt" % _slug(name))
            path.write_text(dump_tree(element), encoding="utf-8")
            return path
        except Exception as exc:
            log.debug("tree dump %r failed: %s", name, exc)
            return None

    def write_json(self, name: str, data: Any) -> Path:
        path = self.dir / name
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        return path

    def record_failure(self, exc: BaseException, element=None, name: str = "failure") -> None:
        (self.dir / ("%s-traceback.txt" % _slug(name))).write_text(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            encoding="utf-8",
        )
        self.screenshot(name + "-screen")
        if element is not None:
            self.dump_tree(name, element)

    # ------------------------------------------------------------------ #
    def finish(self, status: str, tier_stats: Optional[Dict[str, int]] = None) -> Path:
        report = {
            "status": status,
            "started": self.started.isoformat(timespec="seconds"),
            "finished": datetime.now().isoformat(timespec="seconds"),
            "grounding_tier_usage": tier_stats or {},
            "steps": [s.as_dict() for s in self.steps],
        }
        report.update(self.extra)
        path = self.write_json("report.json", report)
        self._write_markdown(report)
        log.info("report -> %s", path)
        self.emit("run-end", {"status": status, "dir": str(self.dir), "report": str(path)})
        return path

    def _write_markdown(self, report: Dict[str, Any]) -> None:
        lines = [
            "# Run report",
            "",
            "**Status:** %s  " % report["status"],
            "**Started:** %s  " % report["started"],
            "**Finished:** %s" % report["finished"],
            "",
            "## Grounding tier usage",
            "",
        ]
        tiers = report.get("grounding_tier_usage") or {}
        if tiers:
            lines += ["| tier | resolutions |", "|---|---|"]
            lines += ["| %s | %d |" % (k, v) for k, v in tiers.items()]
        else:
            lines.append("_no controls were resolved_")
        lines += [
            "", "## Steps", "",
            "| # | spec | step | status | tries | s | screenshot |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in report["steps"]:
            lines.append(
                "| %d | %s | %s | %s | %s | %s | %s |"
                % (
                    s["index"],
                    s["spec"],
                    s["title"],
                    s["status"],
                    s.get("attempts", 1),
                    s["seconds"],
                    ("![](screenshots/%s)" % s["screenshot"]) if s["screenshot"] else "",
                )
            )

        retried = [s for s in report["steps"] if s.get("attempts", 1) > 1]
        if retried:
            lines += [
                "", "## Recovered transient failures", "",
                "These steps did not pass first time. Each was retried in place and",
                "went on to pass, so the run continued.", "",
            ]
            for s in retried:
                lines.append("* **[%s] %s** - %d attempts" % (s["spec"], s["title"], s["attempts"]))
                for note in s.get("recovered", []):
                    lines.append("  * %s" % note)
        (self.dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
