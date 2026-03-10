from __future__ import annotations

import json
import re
from typing import Iterable

VALID_EVENT_CLASSES = {
    "PROGRESSION",
    "STAGNATION",
    "RECOVERY_ATTEMPT",
    "RECOVERY_RESULT",
    "RUNTIME_DRIFT",
    "REPORTING_CORRECTION",
    "CODE_FIX",
    "PROOF",
    "ESCALATION",
}

_HEADER_RE = re.compile(r"^\[(?P<event>[A-Z_]+)\]\s+.+")
_REQUIRED_LABELS = (
    "What happened:",
    "Why it matters:",
    "Proof:",
    "Remaining:",
)


def _clean_line(value: object) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def build_contract_message(
    event_class: str,
    headline: str,
    what_happened: object,
    why_it_matters: object,
    proof: object,
    remaining: object,
) -> str:
    event = _clean_line(event_class).upper()
    if event not in VALID_EVENT_CLASSES:
        raise ValueError(f"Unsupported event class: {event_class}")

    header = _clean_line(headline)
    if not header:
        raise ValueError("Headline is required")

    parts = [
        f"[{event}] {header}",
        f"What happened: {_clean_line(what_happened) or 'pending'}",
        f"Why it matters: {_clean_line(why_it_matters) or 'pending'}",
        f"Proof: {_clean_line(proof) or 'pending'}",
        f"Remaining: {_clean_line(remaining) or 'pending'}",
    ]
    return "\n".join(parts)


def is_contract_message(message: str) -> bool:
    if not message:
        return False
    stripped = message.strip()
    header = stripped.splitlines()[0] if stripped else ""
    match = _HEADER_RE.match(header)
    if not match:
        return False
    if match.group("event") not in VALID_EVENT_CLASSES:
        return False
    return all(label in stripped for label in _REQUIRED_LABELS)


def build_contract_payload(
    event_class: str,
    headline: str,
    what_happened: object,
    why_it_matters: object,
    proof: object,
    remaining: object,
    **extra: object,
) -> str:
    payload = {
        "event_class": _clean_line(event_class).upper(),
        "headline": _clean_line(headline),
        "what_happened": _clean_line(what_happened),
        "why_it_matters": _clean_line(why_it_matters),
        "proof": _clean_line(proof),
        "remaining": _clean_line(remaining),
    }
    for key, value in extra.items():
        payload[key] = value
    return json.dumps(payload, ensure_ascii=False)


def parse_contract_payload(payload: str) -> dict:
    data = json.loads(payload)
    event_class = data.get("event_class", "")
    return {
        "event_class": event_class,
        "headline": data.get("headline", ""),
        "what_happened": data.get("what_happened", ""),
        "why_it_matters": data.get("why_it_matters", ""),
        "proof": data.get("proof", ""),
        "remaining": data.get("remaining", ""),
        **{k: v for k, v in data.items() if k not in {"event_class", "headline", "what_happened", "why_it_matters", "proof", "remaining"}},
    }


def format_payload_or_message(content: str) -> str:
    stripped = (content or "").strip()
    if not stripped:
        return stripped
    if is_contract_message(stripped):
        return stripped
    try:
        data = parse_contract_payload(stripped)
    except Exception:
        return stripped
    return build_contract_message(
        data.get("event_class", "PROGRESSION"),
        data.get("headline", "update"),
        data.get("what_happened", "pending"),
        data.get("why_it_matters", "pending"),
        data.get("proof", "pending"),
        data.get("remaining", "pending"),
    )


def summarize_items(items: Iterable[str], fallback: str = "none") -> str:
    cleaned = [_clean_line(item) for item in items if _clean_line(item)]
    return "; ".join(cleaned) if cleaned else fallback
