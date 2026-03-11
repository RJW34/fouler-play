from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Sequence

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
_PS_REPLAY_RE = re.compile(r"https?://replay\.pokemonshowdown\.com/[^\s;,)]+")
_BATTLE_ID_RE = re.compile(r"\bbattle-[A-Za-z0-9-]+\b")
_TEAM_FILE_RE = re.compile(r"\b([A-Za-z0-9_-]+\.txt)\b")
_REPORT_FILE_RE = re.compile(r"\b(batch_[A-Za-z0-9._-]+\.md)\b")
_PATH_HINT_RE = re.compile(r"\b([A-Za-z]:\\[^;]+|/[^;]+(?:\.txt|\.md|\.json))\b")
_MAX_FIELD_LEN = 340
_MAX_HEADLINE_LEN = 90
_MAX_PROOF_ITEMS = 4
_SECTION_EMOJI = {
    "What happened:": "📝",
    "Why it matters:": "🎯",
    "Proof:": "🔎",
    "Remaining:": "⏭️",
}


def _clean_line(value: object) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def _truncate(text: object, limit: int = _MAX_FIELD_LEN) -> str:
    cleaned = _clean_line(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _short_team_name(team: object) -> str:
    text = _clean_line(team)
    if not text:
        return "unknown"
    text = text.replace(".txt", "")
    if text.startswith("fat-team-"):
        text = text[len("fat-team-"):]
    return text.replace("-", " ")


def _format_delta(before: object, after: object, label: str = "ELO") -> str:
    try:
        before_num = int(before)
        after_num = int(after)
    except Exception:
        return ""
    delta = after_num - before_num
    sign = "+" if delta > 0 else ""
    return f"{label} {before_num} → {after_num} ({sign}{delta})"


def _extract_replay_bits(text: str) -> list[str]:
    bits: list[str] = []
    seen: set[str] = set()
    for url in _PS_REPLAY_RE.findall(text):
        replay_id = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".json")
        label = replay_id.replace("battle-gen9ou-", "").replace("battle-", "")
        if len(label) > 8 and label.isdigit():
            label = label[-8:]
        bit = f"replay {label}: {url.removesuffix('.json')}"
        if bit not in seen:
            bits.append(bit)
            seen.add(bit)
    return bits


def _extract_named_bits(text: str) -> list[str]:
    bits: list[str] = []
    seen: set[str] = set()

    for battle_id in _BATTLE_ID_RE.findall(text):
        bit = f"battle {battle_id.replace('battle-gen9ou-', '').replace('battle-', '')}"
        if bit not in seen:
            bits.append(bit)
            seen.add(bit)

    for team_file in _TEAM_FILE_RE.findall(text):
        bit = f"team {_short_team_name(team_file)}"
        if bit not in seen:
            bits.append(bit)
            seen.add(bit)

    for report_name in _REPORT_FILE_RE.findall(text):
        bit = f"report {report_name}"
        if bit not in seen:
            bits.append(bit)
            seen.add(bit)

    return bits


def _extract_paths(text: str) -> list[str]:
    bits: list[str] = []
    seen: set[str] = set()
    for raw in _PATH_HINT_RE.findall(text):
        path = raw.strip().rstrip('.,)')
        name = Path(path).name
        bit = f"artifact {name}"
        if bit not in seen:
            bits.append(bit)
            seen.add(bit)
    return bits


def _compact_sentence_parts(parts: Sequence[str], limit: int = _MAX_FIELD_LEN) -> str:
    cleaned = [_clean_line(p) for p in parts if _clean_line(p)]
    if not cleaned:
        return "pending"
    joined = "; ".join(cleaned)
    if len(joined) <= limit:
        return joined
    trimmed: list[str] = []
    length = 0
    for part in cleaned:
        extra = len(part) if not trimmed else len(part) + 2
        if length + extra > max(0, limit - 2):
            break
        trimmed.append(part)
        length += extra
    if not trimmed:
        return _truncate(joined, limit)
    suffix = "" if len(trimmed) == len(cleaned) else "; …"
    return "; ".join(trimmed) + suffix


def _split_semicolon_list(text: str) -> list[str]:
    cleaned = _clean_line(text)
    if not cleaned or cleaned == "pending":
        return []
    parts = [part.strip() for part in cleaned.split(";")]
    return [part for part in parts if part]


def _stylize_proof_item(item: str) -> str:
    cleaned = _clean_line(item)
    if not cleaned:
        return "pending"

    patterns = [
        (r"^source=(.+)$", lambda m: f"source `{m.group(1)}`"),
        (r"^team (.+)$", lambda m: f"team `{m.group(1)}`"),
        (r"^report (.+)$", lambda m: f"report `{m.group(1)}`"),
        (r"^artifact (.+)$", lambda m: f"artifact `{m.group(1)}`"),
        (r"^battle (.+)$", lambda m: f"battle `{m.group(1)}`"),
        (r"^replay ([^:]+):\s+(.+)$", lambda m: f"replay `{m.group(1)}`: {m.group(2)}"),
        (r"^(\d+) replay link\(s\)$", lambda m: f"`{m.group(1)}` replay link(s)"),
        (r"^batch (.+)$", lambda m: f"batch `{m.group(1)}`"),
        (r"^loss reviews queued=(.+)$", lambda m: f"loss reviews queued=`{m.group(1)}`"),
        (r"^top issue (.+)$", lambda m: f"top issue `{m.group(1)}`"),
        (r"^ELO (.+)$", lambda m: f"ELO `{m.group(1)}`"),
    ]
    for pattern, formatter in patterns:
        match = re.match(pattern, cleaned)
        if match:
            return formatter(match)
    return cleaned


def _render_section(label: str, value: object, *, bulletize: bool = False, limit: int = _MAX_FIELD_LEN) -> str:
    cleaned = _truncate(value, limit) or "pending"
    emoji = _SECTION_EMOJI.get(label, "•")
    lines = [f"{emoji} **{label}**"]

    if bulletize:
        items = _split_semicolon_list(cleaned)
        if items:
            if label == "Proof:":
                lines.extend(f"- {_stylize_proof_item(item)}" for item in items)
            else:
                lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- pending")
    else:
        lines.append(cleaned)

    return "\n".join(lines)


def _proof_from_payload(data: dict) -> str:
    proof_bits: list[str] = []
    seen: set[str] = set()

    def add(bit: object) -> None:
        cleaned = _clean_line(bit)
        if cleaned and cleaned not in seen:
            proof_bits.append(cleaned)
            seen.add(cleaned)

    raw_proof = _clean_line(data.get("proof", ""))
    for bit in _extract_replay_bits(raw_proof):
        add(bit)

    battle_id = data.get("battle_id")
    if battle_id:
        add(f"battle {str(battle_id).replace('battle-gen9ou-', '').replace('battle-', '')}")

    result = _clean_line(data.get("result", ""))
    opponent = _clean_line(data.get("opponent", ""))
    turns = data.get("turns")
    if result or opponent or turns not in (None, ""):
        details = []
        if result:
            details.append(result)
        if opponent:
            details.append(f"vs {opponent}")
        if turns not in (None, ""):
            details.append(f"{turns} turns")
        add(" ".join(details))

    team_file = data.get("team_file")
    if team_file:
        add(f"team {_short_team_name(team_file)}")

    for key in ("report", "report_path"):
        if data.get(key):
            add(f"report {Path(str(data[key])).name}")

    top_issues = _clean_line(data.get("top_issues", ""))
    if top_issues:
        first_issue = top_issues.split("\n", 1)[0]
        add(f"top issue {_truncate(first_issue, 90)}")

    batch_results = data.get("batch_results")
    if isinstance(batch_results, list) and batch_results:
        wins = sum(1 for item in batch_results if len(item) > 1 and item[1] == "won")
        losses = sum(1 for item in batch_results if len(item) > 1 and item[1] == "lost")
        add(f"batch {wins}-{losses}")
        replay_count = sum(1 for item in batch_results if len(item) > 2 and item[2])
        if replay_count:
            add(f"{replay_count} replay link(s)")

    analysis_count = data.get("analysis_count")
    if analysis_count not in (None, ""):
        add(f"loss reviews queued={analysis_count}")

    elo_before = data.get("elo_before")
    elo_after = data.get("elo_after")
    elo_delta = _format_delta(elo_before, elo_after)
    if elo_delta:
        add(elo_delta)

    source = _clean_line(data.get("source", ""))
    if source:
        add(f"source={source}")

    for bit in _extract_named_bits(raw_proof):
        add(bit)
    for bit in _extract_paths(raw_proof):
        add(bit)

    if raw_proof and not proof_bits:
        add(_truncate(raw_proof))

    if len(proof_bits) > _MAX_PROOF_ITEMS:
        proof_bits = proof_bits[:_MAX_PROOF_ITEMS]
        proof_bits.append("…")

    return _compact_sentence_parts(proof_bits)


def _remaining_from_payload(data: dict) -> str:
    remaining = _clean_line(data.get("remaining", ""))
    if not remaining:
        return "pending"
    replacements = {
        "Poster can append replay/ELO context if available before or after posting this result.": "append replay or ladder delta if it becomes available",
        "Webhook mirror send_discord_notification still runs after the queued contract event.": "webhook summary should follow with the richer batch breakdown",
        "Review the saved full turn review for additional mistakes beyond the lead sequence.": "review the saved turn report if deeper mistakes need inspection",
    }
    remaining = replacements.get(remaining, remaining)
    return _truncate(remaining, 220)


def _headline_from_payload(data: dict) -> str:
    headline = _clean_line(data.get("headline", ""))
    if headline:
        return _truncate(headline, _MAX_HEADLINE_LEN)

    result = _clean_line(data.get("result", ""))
    opponent = _clean_line(data.get("opponent", ""))
    if result:
        return _truncate(f"battle {result} vs {opponent or 'opponent'}", _MAX_HEADLINE_LEN)

    return "update"


def _what_from_payload(data: dict) -> str:
    explicit = _clean_line(data.get("what_happened", ""))

    result = _clean_line(data.get("result", ""))
    opponent = _clean_line(data.get("opponent", ""))
    turns = data.get("turns")
    team_file = data.get("team_file")
    if result:
        parts = [f"battle finished {result}"]
        if opponent:
            parts.append(f"vs {opponent}")
        if team_file:
            parts.append(f"using {_short_team_name(team_file)}")
        if turns not in (None, ""):
            parts.append(f"in {turns} turns")
        return _compact_sentence_parts([" ".join(parts)])

    batch_results = data.get("batch_results")
    if isinstance(batch_results, list) and batch_results:
        wins = sum(1 for item in batch_results if len(item) > 1 and item[1] == "won")
        losses = sum(1 for item in batch_results if len(item) > 1 and item[1] == "lost")
        return f"bot completed a battle batch at {wins}-{losses} and queued the live summary"

    return _truncate(explicit or "pending", 220)


def _why_from_payload(data: dict) -> str:
    explicit = _clean_line(data.get("why_it_matters", ""))

    if data.get("result"):
        return "battle outcomes are only useful in Discord if the proof is scannable without decoding raw payloads"
    if data.get("batch_results"):
        return "batch reporting should show outcomes and follow-up work in one quick scan"
    if data.get("report") or data.get("top_issues"):
        return "batch analysis only helps if the channel gets a compact summary with clear proof"
    return _truncate(explicit or "pending", 220)


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
        f"[{event}] **{_truncate(header, _MAX_HEADLINE_LEN)}**",
        "",
        _render_section("What happened:", what_happened, limit=220),
        "",
        _render_section("Why it matters:", why_it_matters, limit=220),
        "",
        _render_section("Proof:", proof, bulletize=True),
        "",
        _render_section("Remaining:", remaining, bulletize=True, limit=220),
    ]
    return "\n".join(parts)


def is_contract_message(message: str) -> bool:
    if not message:
        return False
    stripped = message.strip()
    header = stripped.splitlines()[0] if stripped else ""
    match = _HEADER_RE.match(header.replace("**", ""))
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
        _headline_from_payload(data),
        _what_from_payload(data),
        _why_from_payload(data),
        _proof_from_payload(data),
        _remaining_from_payload(data),
    )


def summarize_items(items: Iterable[str], fallback: str = "none") -> str:
    cleaned = [_clean_line(item) for item in items if _clean_line(item)]
    return "; ".join(cleaned) if cleaned else fallback
