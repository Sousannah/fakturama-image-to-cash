"""Structuring step: OCR text (or an image) -> the record_order tool payload.

Two providers, one interface:

* ``groq``      an OpenAI-compatible chat-completions endpoint. Used with a text
                model over the OCR transcription. This is the default here,
                because the Groq account in use serves no vision model.
* ``anthropic`` Claude, which can take the image directly and skip OCR.

Both are driven with *tool calling* rather than free-form JSON, so the response
shape is enforced by the API instead of by a parser.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import SETTINGS
from ..errors import ExtractionError
from ..logging_setup import get
from .schema import ORDER_TOOL, SYSTEM_PROMPT

log = get("extract.llm")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

OCR_PREAMBLE = (
    "Below is an OCR transcription of a sales-order document. Horizontal "
    "positions are preserved, so values line up under their column headings.\n"
    "The OCR is imperfect: a character may be misread, and a very short cell "
    "may be missing entirely. Transcribe what is there; never invent a value "
    "and never compute one from the others. If a required value is genuinely "
    "absent from the transcription, return an empty string for it.\n"
    "\n--- BEGIN OCR ---\n%s\n--- END OCR ---\n"
)


# --------------------------------------------------------------------------- #
def structure(
    ocr_text: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Turn an OCR transcription into the record_order payload."""
    provider = (provider or SETTINGS.provider).lower()
    if provider == "groq":
        return _groq_tool_call(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": OCR_PREAMBLE % ocr_text},
            ],
            model=model or SETTINGS.model,
            max_retries=max_retries,
        )
    if provider == "anthropic":
        return _anthropic_call(
            content=[{"type": "text", "text": OCR_PREAMBLE % ocr_text}],
            model=model or SETTINGS.model,
            max_retries=max_retries,
        )
    raise ExtractionError("unknown provider %r (use groq or anthropic)" % provider)


def structure_from_image(
    image_path: Path,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Vision path: hand the image straight to a multimodal model."""
    provider = (provider or SETTINGS.provider).lower()
    data = base64.standard_b64encode(Path(image_path).read_bytes()).decode("ascii")
    media = _MEDIA.get(Path(image_path).suffix.lower()) or (
        mimetypes.guess_type(str(image_path))[0] or "image/png"
    )

    if provider == "anthropic":
        return _anthropic_call(
            content=[
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media, "data": data},
                },
                {"type": "text", "text": "Transcribe every field of this sales order."},
            ],
            model=model or SETTINGS.model,
            max_retries=max_retries,
        )
    if provider == "groq":
        return _groq_tool_call(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:%s;base64,%s" % (media, data)},
                        },
                        {"type": "text", "text": "Transcribe every field of this sales order."},
                    ],
                },
            ],
            model=model or SETTINGS.model,
            max_retries=max_retries,
        )
    raise ExtractionError("unknown provider %r" % provider)


# --------------------------------------------------------------------------- #
# Groq (OpenAI-compatible)
# --------------------------------------------------------------------------- #
def _groq_tools() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": ORDER_TOOL["name"],
                "description": ORDER_TOOL["description"],
                "parameters": ORDER_TOOL["input_schema"],
            },
        }
    ]


def _groq_tool_call(messages, model: str, max_retries: int) -> Dict[str, Any]:
    import requests

    if not SETTINGS.groq_api_key:
        raise ExtractionError("GROQ_API_KEY is not set (see .env.example)")

    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 8192,
        "messages": messages,
        "tools": _groq_tools(),
        "tool_choice": {"type": "function", "function": {"name": ORDER_TOOL["name"]}},
    }
    headers = {
        "Authorization": "Bearer %s" % SETTINGS.groq_api_key,
        "Content-Type": "application/json",
    }

    last = None
    for attempt in range(1, max_retries + 1):
        log.info("structuring with groq/%s (attempt %d)", model, attempt)
        try:
            response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=180)
        except Exception as exc:
            last = exc
            log.warning("groq request failed: %s", exc)
            continue

        if response.status_code != 200:
            last = ExtractionError("groq HTTP %s: %s" % (response.status_code, response.text[:400]))
            log.warning("%s", last)
            # a bad request will not fix itself on retry
            if response.status_code in (400, 401, 403, 404, 422):
                raise last
            continue

        body = response.json()
        try:
            message = body["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            if calls:
                arguments = calls[0]["function"]["arguments"]
                return json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            # some models answer with JSON in the content instead of a tool call
            content = message.get("content") or ""
            parsed = _loads_loose(content)
            if parsed is not None:
                log.debug("model answered with content rather than a tool call")
                return parsed
            last = ExtractionError("groq returned neither a tool call nor JSON content")
        except Exception as exc:
            last = exc
        log.warning("could not read the groq response: %s", last)

    raise ExtractionError("groq structuring failed after %d attempts: %s" % (max_retries, last))


def _loads_loose(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON recovery from a chat response."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #
def _anthropic_call(content, model: str, max_retries: int) -> Dict[str, Any]:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("pip install anthropic to use the anthropic provider") from exc
    if not SETTINGS.anthropic_api_key:
        raise ExtractionError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key)
    last = None
    for attempt in range(1, max_retries + 1):
        log.info("structuring with anthropic/%s (attempt %d)", model, attempt)
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=4096,
                temperature=0,
                system=SYSTEM_PROMPT,
                tools=[ORDER_TOOL],
                tool_choice={"type": "tool", "name": ORDER_TOOL["name"]},
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            last = exc
            log.warning("anthropic call failed: %s", exc)
            continue
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use" and block.name == ORDER_TOOL["name"]:
                return dict(block.input)
        last = ExtractionError("no record_order tool call in the response")
    raise ExtractionError("anthropic structuring failed: %s" % last)
