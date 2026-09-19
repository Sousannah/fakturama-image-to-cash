"""Runtime configuration, read from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # optional
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]


def _p(value: Optional[str]) -> Optional[Path]:
    return Path(value) if value else None


#: Default model per provider, used when F2C_MODEL is unset.
DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "anthropic": "claude-opus-5",
}


def _default_model() -> str:
    explicit = os.getenv("F2C_MODEL")
    if explicit:
        return explicit
    return DEFAULT_MODELS.get(os.getenv("F2C_PROVIDER", "groq").lower(), "openai/gpt-oss-120b")


@dataclass
class Settings:
    # extraction
    provider: str = field(default_factory=lambda: os.getenv("F2C_PROVIDER", "groq").lower())
    groq_api_key: Optional[str] = field(default_factory=lambda: os.getenv("GROQ_API_KEY"))
    anthropic_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY")
    )
    model: str = field(default_factory=_default_model)

    #: "ocr" reads the image with Python OCR and structures the text with a
    #: text model; "vision" hands the image straight to a multimodal model.
    extraction_mode: str = field(
        default_factory=lambda: os.getenv("F2C_EXTRACTION_MODE", "ocr").lower()
    )
    ocr_engine: str = field(default_factory=lambda: os.getenv("F2C_OCR_ENGINE", "auto").lower())

    # application under automation
    fakturama_exe: Optional[Path] = field(
        default_factory=lambda: _p(os.getenv("F2C_FAKTURAMA_EXE"))
    )
    workspace: Optional[Path] = field(
        default_factory=lambda: _p(os.getenv("F2C_WORKSPACE"))
        or (Path.home() / "Fakturama2")
    )
    h2_jar: Optional[Path] = field(default_factory=lambda: _p(os.getenv("F2C_H2_JAR")))

    # timing (seconds) - every wait is condition-based, these are only ceilings
    default_timeout: float = 20.0
    app_start_timeout: float = 120.0
    poll_interval: float = 0.25
    list_stable_polls: int = 2

    #: How many times a *replayable* step may run before the run gives up, and
    #: how long to let the UI settle between those attempts. See
    #: `flow.context.Ctx.retried_step` for which steps qualify.
    step_attempts: int = 3
    step_settle: float = 0.8

    # grounding
    template_threshold: float = 0.88

    # artifacts
    out_dir: Path = field(default_factory=lambda: REPO_ROOT / "out")
    assets_dir: Path = field(default_factory=lambda: REPO_ROOT / "assets")


SETTINGS = Settings()
