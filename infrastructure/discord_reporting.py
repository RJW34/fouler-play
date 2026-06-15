from __future__ import annotations

import json
import re
from collections import Counter
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
_PS_REPLAY_RE = re.compile(r"https?://replay\.pokemonshowdown\.com/[^\s;,)<>]+")
_BATTLE_ID_RE = re.compile(r"\bbattle-[A-Za-z0-9-]+\b")
_TEAM_FILE_RE = re.compile(r"\b([A-Za-z0-9_-]+\.txt)\b")
_REPORT_FILE_RE = re.compile(r"\b(batch_[A-Za-z0-9._-]+\.md)\b")
_PATH_HINT_RE = re.compile(r"\b([A-Za-z]:\\[^;]+|/[^;]+(?:\.txt|\.md|\.json))\b")
_MAX_FIELD_LEN = 340
_MAX_WHAT_LEN = 420
_MAX_HEADLINE_LEN = 90
_MAX_PROOF_ITEMS = 8
_SECTION_EMOJI = {
    "What happened:": "📝",
    "Why it matters:": "🎯",
    "Proof:": "🔎",
    "Remaining:": "⏭️",
}
_LOSS_WORDS = {"loss", "lost", "forfeit", "forfeited", "timeout", "disconnect", "disconnected", "inactive"}
_WIN_WORDS = {"win", "won"}
_DISCORD_WEBHOOK_URL_RE = re.compile(
    r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/[^\s`)>\]]+",
    re.IGNORECASE,
)
_NAMED_SECRET_RE = re.compile(
    r"\b(token|secret|password|passwd|api[_-]?key|webhook(?:_url)?|authorization)\b"
    r"\s*[:=]\s*[^\s;,)>\]]+",
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(r"\b(?:bot|bearer)\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
_SECRET_TOKEN_WORD_RE = re.compile(r"\bsecret[-_][A-Za-z0-9._-]+\b", re.IGNORECASE)
_LONG_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9._-]{32,})(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9._-]{32,}(?![A-Za-z0-9_-])"
)


def _clean_line(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ")
    text = text.replace("\n", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _truncate(text: object, limit: int = _MAX_FIELD_LEN) -> str:
    cleaned = _clean_line(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _redact_sensitive_text_with_flag(value: object) -> tuple[str, bool]:
    text = "" if value is None else str(value)
    redacted = _DISCORD_WEBHOOK_URL_RE.sub("https://discord.com/api/webhooks/REDACTED", text)
    redacted = _NAMED_SECRET_RE.sub(lambda m: f"{m.group(1)}=REDACTED", redacted)
    redacted = _AUTH_HEADER_RE.sub("AUTH REDACTED", redacted)
    redacted = _SECRET_TOKEN_WORD_RE.sub("REDACTED_SECRET", redacted)
    redacted = _LONG_TOKEN_RE.sub("REDACTED_TOKEN", redacted)
    return redacted, redacted != text


def redact_sensitive_text(value: object) -> str:
    """Redact obvious tokens/secrets before writing dry-run proof artifacts."""
    return _redact_sensitive_text_with_flag(value)[0]


def _safe_report_text(value: object, limit: int = 220) -> tuple[str, bool]:
    redacted, changed = _redact_sensitive_text_with_flag(value)
    return _truncate(redacted, limit), changed


def _short_team_name(team: object) -> str:
    text = _clean_line(team)
    if not text:
        return "unknown"
    text = Path(text).name.replace(".txt", "")
    if text.startswith("fat-team-"):
        parts = text.split("-")
        if len(parts) >= 4:
            return " ".join(parts[2:])
        text = text[len("fat-team-"):]
    return text.replace("-", " ")


def format_elo_delta(before: object, after: object, result: object = "", label: str = "ELO") -> str:
    try:
        after_num_only = int(round(float(after)))
    except Exception:
        after_num_only = None
    try:
        before_num = int(round(float(before)))
        after_num = int(round(float(after)))
    except Exception:
        if after_num_only is not None:
            return f"{label} now {after_num_only}"
        return ""
    delta = after_num - before_num
    result_norm = _normalize_result(result)
    sign = "+" if delta > 0 else ""

    if (result_norm == "loss" and delta > 0) or (result_norm == "win" and delta < 0):
        return f"{label} check needed (cached {before_num}, fetched {after_num}, {sign}{delta} contradicts {result_norm})"

    if delta > 0:
        return f"{label} gained {delta} ({before_num} → {after_num}, +{delta})"
    if delta < 0:
        return f"{label} lost {abs(delta)} ({before_num} → {after_num}, {delta})"
    return f"{label} unchanged ({before_num} → {after_num}, +0)"


def _replay_id_from_reference(value: object, *, public_only: bool) -> str:
    text = _clean_line(value)
    if not text:
        return ""
    match = _PS_REPLAY_RE.search(text)
    if match:
        text = match.group(0)
    if text.startswith("http://") or text.startswith("https://"):
        if not text.startswith("https://replay.pokemonshowdown.com/") and not text.startswith("http://replay.pokemonshowdown.com/"):
            return ""
        text = text.rstrip("/").rsplit("/", 1)[-1]
    text = text.split("?", 1)[0].split("#", 1)[0].removesuffix(".json").strip()
    if text.startswith("battle-"):
        text = text.replace("battle-", "", 1)
    parts = [part for part in text.split("-") if part]
    if len(parts) < 2:
        return ""
    if public_only and len(parts) > 2:
        return ""
    return f"{parts[0]}-{parts[1]}"


def public_replay_id_candidate(value: object) -> str:
    """Return the public Showdown replay id that should be verified before linking."""
    return _replay_id_from_reference(value, public_only=False)


def canonical_replay_url(value: object) -> str:
    """Return a canonical public Showdown replay URL, or empty for private/unresolved refs."""
    replay_id = _replay_id_from_reference(value, public_only=True)
    return f"https://replay.pokemonshowdown.com/{replay_id}" if replay_id else ""


def replay_handoff_fields(
    *,
    battle_tag: object = None,
    replay_url: object = None,
    verified_replay_url: object = None,
) -> dict[str, object]:
    """Preserve replay evidence even when public upload verification lags."""
    replay_id = public_replay_id_candidate(verified_replay_url or replay_url or battle_tag)
    candidate_url = (
        _clean_line(verified_replay_url)
        or _clean_line(replay_url)
        or (f"https://replay.pokemonshowdown.com/{replay_id}" if replay_id else "")
    )
    verified = bool(_clean_line(verified_replay_url))
    if verified:
        status = "public"
    elif replay_id or candidate_url:
        status = "pending-public-upload"
    else:
        status = "absent"
    return {
        "replay_id": replay_id,
        "replay_url": candidate_url or None,
        "replay_status": status,
        "replay_public_verified": verified,
        "raw_replay_url": _clean_line(replay_url) or None,
        "verified_replay_url": _clean_line(verified_replay_url) or None,
    }


def _replay_pending_bit(value: object) -> str:
    replay_id = public_replay_id_candidate(value)
    return f"replay pending public upload {replay_id}" if replay_id else ""


def _payload_declares_replay_public_state(data: dict) -> bool:
    return "replay_status" in data or "replay_public_verified" in data


def _truthy_field(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _clean_line(value).lower() in {"1", "true", "yes", "on", "public", "verified"}


def _payload_replay_is_public(data: dict) -> bool:
    status = _clean_line(data.get("replay_status")).lower()
    if status in {"pending-public-upload", "pending", "absent", "missing"}:
        return False
    if "replay_public_verified" in data:
        return _truthy_field(data.get("replay_public_verified"))
    return status in {
        "public",
        "public-upload-verified",
        "verified",
        "verified-public",
    }


def _extract_replay_bits(text: object) -> list[str]:
    bits: list[str] = []
    seen: set[str] = set()

    for url in _PS_REPLAY_RE.findall(_clean_line(text)):
        canonical = canonical_replay_url(url)
        if not canonical:
            bit = _replay_pending_bit(url)
        else:
            replay_id = canonical.rstrip("/").rsplit("/", 1)[-1]
            label = replay_id.replace("battle-gen9ou-", "").replace("battle-", "")
            if len(label) > 8 and label.isdigit():
                label = label[-8:]
            bit = f"replay {label}: {canonical}"
        if bit not in seen:
            bits.append(bit)
            seen.add(bit)
    return bits


def _extract_replay_proof_bits(text: object, *, public_verified: bool) -> list[str]:
    if public_verified:
        return _extract_replay_bits(text)

    bits: list[str] = []
    seen: set[str] = set()
    for url in _PS_REPLAY_RE.findall(_clean_line(text)):
        bit = _replay_pending_bit(url)
        if bit and bit not in seen:
            bits.append(bit)
            seen.add(bit)
    return bits


def _replay_ref_proof_bits(value: object, *, public_verified: bool) -> list[str]:
    if public_verified:
        return _extract_replay_bits(value)
    bit = _replay_pending_bit(value)
    return [bit] if bit else []


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
        (r"^replay pending public upload (.+)$", lambda m: f"replay pending public upload `{m.group(1)}`"),
        (r"^(\d+) replay link\(s\)$", lambda m: f"`{m.group(1)}` replay link(s)"),
        (r"^batch (.+)$", lambda m: f"batch `{m.group(1)}`"),
        (r"^loss reviews queued=(.+)$", lambda m: f"loss reviews queued=`{m.group(1)}`"),
        (r"^top issue (.+)$", lambda m: f"top issue `{m.group(1).rstrip('…')}`"),
        (r"^coverage (.+)$", lambda m: f"coverage `{m.group(1)}`"),
        (r"^window (.+)$", lambda m: f"window `{m.group(1)}`"),
        (r"^stalled for (.+)$", lambda m: f"stalled for `{m.group(1)}`"),
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


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _positive_turn_count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    turns = _safe_int(value)
    if turns is None or turns <= 0:
        return None
    return turns


def _normalize_result(value: object) -> str:
    text = _clean_line(value).lower()
    if text in {"won", "win", "victory"}:
        return "win"
    if text in {"lost", "loss", "defeat"}:
        return "loss"
    if text in {"tie", "draw"}:
        return "tie"
    return text


def _normalize_batch_outcome(item: object) -> str:
    if isinstance(item, (list, tuple)) and len(item) > 1:
        return _normalize_result(item[1])
    if isinstance(item, dict):
        return _normalize_result(item.get("result"))
    return _normalize_result(item)


def _record_from_batch_results(batch_results: list) -> tuple[int, int, int]:
    wins = sum(1 for item in batch_results if _normalize_batch_outcome(item) == "win")
    losses = sum(1 for item in batch_results if _normalize_batch_outcome(item) == "loss")
    total = len(batch_results)
    return wins, losses, total


def _opponent_from_batch_item(item: object) -> str:
    if isinstance(item, dict):
        return _clean_line(item.get("opponent") or item.get("matchup") or item.get("name"))
    if isinstance(item, (list, tuple)) and item:
        return _clean_line(item[0])
    return ""


def _replay_from_batch_item(item: object) -> str:
    if isinstance(item, dict):
        return _clean_line(item.get("replay") or item.get("replay_url") or item.get("url"))
    if isinstance(item, (list, tuple)) and len(item) > 2:
        return _clean_line(item[2])
    return ""


def _battle_id_from_batch_item(item: object) -> str:
    if isinstance(item, dict):
        return _clean_line(item.get("battle_id") or item.get("battle_tag") or item.get("battle"))
    if isinstance(item, (list, tuple)) and len(item) > 3:
        return _clean_line(item[3])
    return ""


def _batch_public_replay_url(item: object) -> str:
    return canonical_replay_url(_replay_from_batch_item(item))


def _batch_pending_replay_id(item: object) -> str:
    replay = _replay_from_batch_item(item)
    if replay and not canonical_replay_url(replay):
        pending = public_replay_id_candidate(replay)
        if pending:
            return pending
    if not replay:
        battle_id = _battle_id_from_batch_item(item)
        pending = public_replay_id_candidate(battle_id)
        if pending:
            return pending
    return ""


def _first_batch_replay_summary(batch_results: list) -> dict[str, object]:
    for item in batch_results:
        public_url = _batch_public_replay_url(item)
        if public_url:
            return {
                "status": "public",
                "id": public_url.rstrip("/").rsplit("/", 1)[-1],
                "url": public_url,
            }
        pending_id = _batch_pending_replay_id(item)
        if pending_id:
            return {"status": "pending-public-upload", "id": pending_id, "url": ""}
    return {"status": "absent", "id": "", "url": ""}


def _top_loss_pattern(batch_results: list) -> str:
    losses: dict[str, int] = {}
    for item in batch_results:
        if _normalize_batch_outcome(item) == "loss":
            opponent = _opponent_from_batch_item(item) or "opponent"
            losses[opponent] = losses.get(opponent, 0) + 1
    if not losses:
        return "no repeat loss pattern yet"
    opponent, count = max(losses.items(), key=lambda kv: (kv[1], kv[0]))
    if count <= 1:
        return f"losses were split across opponents ({len(losses)} unique)"
    return f"{opponent} caused {count} loss(es) in this window"


def _batch_coverage_line(batch_results: list, analysis_count: object) -> str:
    total = len(batch_results)
    public_replay_count = sum(1 for item in batch_results if _batch_public_replay_url(item))
    pending_replay_count = sum(1 for item in batch_results if _batch_pending_replay_id(item))
    unresolved_count = sum(
        1
        for item in batch_results
        if _replay_from_batch_item(item)
        and not _batch_public_replay_url(item)
        and not _batch_pending_replay_id(item)
    )
    pending = _safe_int(analysis_count) or 0
    reviewed = max(0, min(public_replay_count, public_replay_count - pending))
    parts = [f"public replays {public_replay_count}/{total}"]
    if pending_replay_count:
        parts.append(f"pending public replays {pending_replay_count}")
    if unresolved_count:
        parts.append(f"unresolved replay refs {unresolved_count}")
    parts.extend([f"loss reviews queued {pending}", f"reviewed {reviewed}"])
    return "; ".join(parts)


def _derive_loss_cause(data: dict) -> str:
    for key in ("strategic_issue", "loss_pattern", "performance_change"):
        text = _clean_line(data.get(key, ""))
        if text:
            return text
    result = _normalize_result(data.get("result", ""))
    opponent = _clean_line(data.get("opponent", ""))
    if result == "loss" and opponent:
        return f"loss vs {opponent} needs replay review before the next queue"
    return ""


def _detect_operational_flag(data: dict) -> str:
    notes = " ".join(
        _clean_line(data.get(key, "")).replace("\n", " ")
        for key in (
            "decisive_reason",
            "why_it_matters",
            "performance_change",
            "strategic_issue",
            "what_happened",
            "proof",
        )
    ).lower()
    if any(word in notes for word in ("inactivity", "disconnect", "timed out", "timeout", "reconnect")):
        return "operational"
    return ""


def _status_line(data: dict) -> str:
    result = _normalize_result(data.get("result", ""))
    opponent = _clean_line(data.get("opponent", ""))
    turns = _positive_turn_count(data.get("turns"))
    details: list[str] = []
    if result:
        details.append(result)
    if opponent:
        details.append(f"vs {opponent}")
    if turns is not None:
        details.append(f"{turns} turns")
    return " ".join(details)


def _proof_from_payload(data: dict) -> str:
    proof_bits: list[str] = []
    seen: set[str] = set()

    def add(bit: object) -> None:
        cleaned = _clean_line(bit)
        if cleaned and cleaned not in seen:
            proof_bits.append(cleaned)
            seen.add(cleaned)

    raw_proof = _clean_line(data.get("proof", ""))
    replay_public_verified = _payload_replay_is_public(data) or not _payload_declares_replay_public_state(data)
    for bit in _extract_replay_proof_bits(raw_proof, public_verified=replay_public_verified):
        add(bit)

    battle_id = data.get("battle_id")
    if battle_id:
        add(f"battle {str(battle_id).replace('battle-gen9ou-', '').replace('battle-', '')}")

    replay_url = _clean_line(data.get("replay_url") or data.get("replay") or "")
    for bit in _replay_ref_proof_bits(replay_url, public_verified=replay_public_verified):
        add(bit)
    if replay_url and not _extract_replay_bits(replay_url):
        add(_replay_pending_bit(replay_url))

    status_line = _status_line(data)
    if status_line:
        add(status_line)

    team_file = data.get("team_file")
    if team_file:
        add(f"team {_short_team_name(team_file)}")

    for key in ("report", "report_path", "markdown_report"):
        if data.get(key):
            add(f"report {Path(str(data[key])).name}")

    top_issues = _clean_line(data.get("top_issues", ""))
    if top_issues:
        add(f"top issue {_truncate(top_issues.splitlines()[0], 90)}")

    batch_results = data.get("batch_results")
    if isinstance(batch_results, list) and batch_results:
        wins, losses, total = _record_from_batch_results(batch_results)
        add(f"batch {wins}-{losses}")
        replay_count = sum(1 for item in batch_results if _batch_public_replay_url(item))
        if replay_count:
            add(f"{replay_count} replay link(s)")
        add(f"coverage {_batch_coverage_line(batch_results, data.get('analysis_count'))}")
        for item in batch_results:
            replay_url = _batch_public_replay_url(item)
            if replay_url:
                for bit in _extract_replay_bits(replay_url):
                    add(bit)
            else:
                replay_id = _batch_pending_replay_id(item)
                if replay_id:
                    add(f"replay pending public upload {replay_id}")

    analysis_count = data.get("analysis_count")
    if analysis_count not in (None, ""):
        add(f"loss reviews queued={analysis_count}")

    recent_record = _clean_line(data.get("recent_record", ""))
    if recent_record:
        add(f"window {recent_record}")

    elo_before = data.get("elo_before")
    elo_after = data.get("elo_after")
    elo_delta = format_elo_delta(elo_before, elo_after, data.get("result", ""))
    if elo_delta:
        add(elo_delta)

    source = _clean_line(data.get("source", ""))

    for bit in _extract_named_bits(raw_proof):
        add(bit)
    for bit in _extract_paths(raw_proof):
        add(bit)

    if raw_proof and not proof_bits:
        add(_truncate(raw_proof))

    if source:
        add(f"source={source}")

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
        "Webhook mirror send_discord_notification still runs after the queued contract event.": "full webhook summary should mirror the same signal if it stays enabled",
        "Review the saved full turn review for additional mistakes beyond the lead sequence.": "review the saved turn report if deeper mistakes need inspection",
    }
    remaining = replacements.get(remaining, remaining)
    return _truncate(remaining, 220)


def _headline_from_payload(data: dict) -> str:
    headline = _clean_line(data.get("headline", ""))
    if headline:
        return _truncate(headline, _MAX_HEADLINE_LEN)

    result = _normalize_result(data.get("result", ""))
    opponent = _clean_line(data.get("opponent", ""))
    if result:
        return _truncate(f"battle {result} vs {opponent or 'opponent'}", _MAX_HEADLINE_LEN)

    return "update"


def _subject_matter_summary(data: dict) -> list[str]:
    summary: list[str] = []

    decisive_reason = _clean_line(data.get("decisive_reason", ""))
    if decisive_reason:
        summary.append(decisive_reason)

    strategic_issue = _clean_line(data.get("strategic_issue", ""))
    if strategic_issue:
        summary.append(strategic_issue)

    performance_change = _clean_line(data.get("performance_change", ""))
    if performance_change:
        summary.append(performance_change)

    loss_pattern = _clean_line(data.get("loss_pattern", ""))
    if loss_pattern:
        summary.append(loss_pattern)

    next_battle_action = _clean_line(data.get("next_battle_action", ""))
    if next_battle_action:
        summary.append(f"next battle focus: {next_battle_action}")

    return summary


def _recent_record_summary(data: dict) -> str:
    recent_record = _clean_line(data.get("recent_record", ""))
    if recent_record:
        return recent_record
    wins = _safe_int(data.get("recent_wins"))
    losses = _safe_int(data.get("recent_losses"))
    size = _safe_int(data.get("recent_window_size"))
    if wins is None or losses is None:
        return ""
    if size is None:
        size = wins + losses
    wr = int(round((wins / size) * 100)) if size else 0
    return f"last {size}: {wins}-{losses} ({wr}% WR)"


def _trend_summary(data: dict) -> str:
    trend = _clean_line(data.get("trend", ""))
    if trend:
        return trend
    delta = _safe_int(data.get("recent_delta"))
    if delta is None:
        return ""
    if delta > 0:
        return f"trend improving ({delta:+d} over window)"
    if delta < 0:
        return f"trend slipping ({delta:+d} over window)"
    return "trend flat over window"


def _actionability_line(data: dict) -> str:
    flag = _detect_operational_flag(data)
    if flag == "operational":
        return "this looks like an ops/runtime issue, not a ladder-behavior miss"
    if _clean_line(data.get("code_fix_hint", "")):
        return _clean_line(data.get("code_fix_hint"))
    return ""


def _what_from_payload(data: dict) -> str:
    explicit = _clean_line(data.get("what_happened", ""))
    subject_bits = _subject_matter_summary(data)
    recent_record = _recent_record_summary(data)
    trend = _trend_summary(data)
    actionability = _actionability_line(data)

    result = _normalize_result(data.get("result", ""))
    opponent = _clean_line(data.get("opponent", ""))
    turns = _positive_turn_count(data.get("turns"))
    team_file = data.get("team_file")
    if result:
        battle_line = f"battle finished {result}"
        if opponent:
            battle_line += f" vs {opponent}"
        if team_file:
            battle_line += f" using {_short_team_name(team_file)}"
        if turns is not None:
            battle_line += f" in {turns} turns"
        parts = [battle_line]
        if recent_record:
            parts.append(recent_record)
        if trend:
            parts.append(trend)
        parts.extend(subject_bits)
        if actionability:
            parts.append(actionability)
        return _compact_sentence_parts(parts, 360)

    batch_results = data.get("batch_results")
    if isinstance(batch_results, list) and batch_results:
        wins, losses, total = _record_from_batch_results(batch_results)
        wr = (wins / total * 100) if total else 0
        pattern = _top_loss_pattern(batch_results)
        coverage = _batch_coverage_line(batch_results, data.get("analysis_count"))
        parts = [
            f"{total}-battle window finished at {wins}-{losses} ({wr:.0f}% WR)",
            f"top loss pattern: {pattern}",
        ]
        if recent_record:
            parts.append(recent_record)
        if trend:
            parts.append(trend)
        parts.extend(subject_bits)
        parts.append(coverage)
        if actionability:
            parts.append(actionability)
        return _compact_sentence_parts(parts, 360)

    if data.get("report") or data.get("top_issues"):
        report_name = Path(str(data.get("report") or data.get("report_path") or "report")).name
        raw_top_issues = data.get("top_issues", "")
        if isinstance(raw_top_issues, str):
            top_issue = raw_top_issues.splitlines()[0].strip()
        else:
            top_issue = _clean_line(raw_top_issues)
        parts = []
        if top_issue:
            parts.append(f"lead issue: {_truncate(top_issue, 100)}")
        if recent_record:
            parts.append(recent_record)
        if trend:
            parts.append(trend)
        parts.extend(subject_bits)
        if data.get("window"):
            parts.append(f"window analyzed: {data.get('window')} battles")
        parts.append(f"full batch analysis: {report_name}")
        if actionability:
            parts.append(actionability)
        if not top_issue:
            parts.insert(0, f"batch analysis is ready in {report_name}")
        return _compact_sentence_parts(parts, 360)

    stalled_minutes = _safe_int(data.get("stalled_minutes"))
    if stalled_minutes is not None:
        return _compact_sentence_parts([explicit or "no new battle activity detected", f"stalled for {stalled_minutes} minute(s)"], 220)

    return _truncate(explicit or "pending", 220)


def _why_from_payload(data: dict) -> str:
    explicit = _clean_line(data.get("why_it_matters", ""))
    if explicit and not any(
        phrase in explicit
        for phrase in (
            "queued the outcome for Discord delivery",
            "queued the concise Discord summary",
            "mechanics of posting",
        )
    ) and "operational failure" not in explicit.lower():
        return _truncate(explicit, 220)

    flag = _detect_operational_flag(data)
    if flag == "operational":
        return "operator reports should flag ladder-invisible runtime failures immediately so losses caused by disconnects or inactivity are not mistaken for team or policy problems"

    result = _normalize_result(data.get("result", ""))
    if result:
        if result == "loss":
            return "battle updates should tell us whether this was a real matchup/policy miss or just variance, and what the next ladder-relevant adjustment is"
        return "battle updates should confirm the win condition that worked so we know whether the bot is climbing through repeatable play or variance"
    if data.get("batch_results"):
        return "routine updates should center the win/loss trend, the matchup issue behind it, and the next battle-relevant adjustment instead of recap mechanics"
    if data.get("report") or data.get("top_issues"):
        return "batch analysis only helps if the channel sees the recurring issue, why it hurts results, and where the full breakdown lives"
    return "pending"


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
        _render_section("What happened:", what_happened, limit=_MAX_WHAT_LEN),
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


def _extract_contract_section(message: str, label: str) -> str:
    lines = message.splitlines()
    collected: list[str] = []
    in_section = False
    for line in lines:
        if f"**{label}**" in line:
            in_section = True
            continue
        if in_section and "**" in line and any(required in line for required in _REQUIRED_LABELS):
            break
        if in_section:
            stripped = line.strip()
            if stripped:
                collected.append(stripped.lstrip("- ").strip())
    return _clean_line("; ".join(collected))


def _battle_ids_from_report_text(text: str, limit: int = 8) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(raw: object) -> None:
        candidate = public_replay_id_candidate(raw)
        if candidate and candidate not in seen:
            ids.append(candidate)
            seen.add(candidate)

    for match in re.finditer(r"\b(?:battle-)?gen9ou-[A-Za-z0-9-]+\b", text or ""):
        add(match.group(0))
        if len(ids) >= limit:
            return ids
    for match in re.finditer(r"\bbattle\s+`?(\d{6,})`?", text or "", re.IGNORECASE):
        add(f"gen9ou-{match.group(1)}")
        if len(ids) >= limit:
            return ids
    for url in _PS_REPLAY_RE.findall(text or ""):
        add(url)
        if len(ids) >= limit:
            return ids
    return ids


def _first_replay_summary(*values: object, public_verified: bool | None = None) -> dict[str, object]:
    for value in values:
        pending = public_replay_id_candidate(value)
        if public_verified is False:
            if pending:
                return {"status": "pending-public-upload", "id": pending, "url": ""}
            continue
        canonical = canonical_replay_url(value)
        if canonical:
            return {
                "status": "public",
                "id": canonical.rstrip("/").rsplit("/", 1)[-1],
                "url": canonical,
            }
        if pending:
            return {"status": "pending-public-upload", "id": pending, "url": ""}
    return {"status": "absent", "id": "", "url": ""}


def _short_battle_label(value: object) -> str:
    text = _clean_line(value)
    return text.replace("battle-gen9ou-", "").replace("battle-", "") if text else ""


def _current_battle_state(
    *,
    result: object = "",
    opponent: object = "",
    turns: object = None,
    battle_id: object = "",
    replay: object = None,
    fallback: object = "",
) -> str:
    result_text = _normalize_result(result)
    opponent_text = _clean_line(opponent)
    turn_count = _positive_turn_count(turns)
    battle_label = _short_battle_label(battle_id)
    parts: list[str] = []
    if result_text:
        parts.append(f"battle {result_text}")
    elif fallback:
        parts.append(_clean_line(fallback))
    else:
        parts.append("battle state pending")
    if opponent_text:
        parts.append(f"vs {opponent_text}")
    if turn_count is not None:
        parts.append(f"{turn_count} turns")
    if battle_label:
        parts.append(f"id {battle_label}")
    if isinstance(replay, dict):
        replay_status = _clean_line(replay.get("status"))
        replay_id = _clean_line(replay.get("id"))
        if replay_status == "public" and replay_id:
            parts.append(f"public replay {replay_id}")
        elif replay_status == "pending-public-upload" and replay_id:
            parts.append(f"replay pending {replay_id}")
    return _truncate("; ".join(part for part in parts if part), 220)


def _hermes_next_action(*, ops_signal: object, result: object, next_action: object, missing_fields: Sequence[str] = ()) -> str:
    explicit = _clean_line(next_action)
    if explicit and explicit != "pending":
        return _truncate(explicit, 240)
    if missing_fields:
        return _truncate(f"fill missing report fields before proof handoff: {', '.join(missing_fields)}", 240)
    signal = _clean_line(ops_signal)
    result_text = _normalize_result(result)
    if signal == "operational-loss":
        return "inspect runtime/connectivity/timer failure before queueing the next battle"
    if result_text == "loss":
        return "analyze the replay, isolate the repeatable loss pattern, patch one bounded improvement, then run the next battle"
    if result_text == "win":
        return "record the repeatable win condition, keep the bounded battle cycle running, and refresh proof after the next result"
    return "keep the bounded battle cycle running and refresh proof after the next concrete battle result"


def _proof_readiness(
    *,
    event_type: str = "",
    result: object = "",
    battle_id: object = "",
    proof: object = None,
    analysis: object = None,
    turns: object = None,
    next_action: object = "",
    replay: object = None,
) -> dict[str, object]:
    required = ["battle_id", "proof", "analysis.nextAction"]
    if event_type == "battle_result" or _normalize_result(result):
        required.insert(1, "result")
    missing: list[str] = []
    if not _clean_line(battle_id):
        missing.append("battle_id")
    if "result" in required and not _normalize_result(result):
        missing.append("result")
    if proof in (None, "", [], {}):
        missing.append("proof")
    if analysis in (None, "", [], {}):
        missing.append("analysis")
    if not _clean_line(next_action):
        missing.append("analysis.nextAction")
    replay_status = ""
    if isinstance(replay, dict):
        replay_status = _clean_line(replay.get("status"))
        if replay_status == "pending-public-upload":
            missing.append("replay.url")
        elif replay_status == "public" and not canonical_replay_url(replay.get("url")):
            missing.append("replay.url")
    quality_gaps: list[str] = []
    if (event_type == "battle_result" or _normalize_result(result)) and _positive_turn_count(turns) is None:
        quality_gaps.append("turns")
    status = "proof-ready" if not missing else "proof-needs-fields"
    return {
        "status": status,
        "readyForHermes": status == "proof-ready",
        "classification": "battle-result-proof" if event_type == "battle_result" or _normalize_result(result) else "status-update-proof",
        "missingFields": missing,
        "qualityGaps": quality_gaps,
        "blockers": [
            "replay pending public upload" if field == "replay.url" and replay_status == "pending-public-upload" else f"missing {field}"
            for field in missing
        ],
    }


def _result_and_opponent_from_text(text: str) -> tuple[str, str]:
    match = re.search(
        r"\b(?:battle(?: result| finished)?\s+)?(?P<result>win|won|loss|lost|tie|draw)\s+vs\s+(?P<opponent>[^;`\n]+)",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return "", ""
    opponent = re.sub(r"\s+in\s+\d+\s+turns\b.*$", "", _clean_line(match.group("opponent")), flags=re.IGNORECASE)
    opponent = re.sub(r"\s+battle\s+(?:finished|result)\b.*$", "", opponent, flags=re.IGNORECASE)
    return _normalize_result(match.group("result")), opponent.strip(" .")


def redacted_report_summary(content: str) -> dict[str, object]:
    """Build a compact dry-run summary without printing raw queued content."""
    raw = content or ""
    _, secret_redacted = _redact_sensitive_text_with_flag(raw)
    try:
        data = parse_contract_payload(raw.strip())
    except Exception:
        data = {}

    if data:
        event_class = _clean_line(data.get("event_class") or "PROOF")
        headline, changed = _safe_report_text(_headline_from_payload(data), _MAX_HEADLINE_LEN)
        secret_redacted = secret_redacted or changed
        viewer, changed = _safe_report_text(_what_from_payload(data), 360)
        secret_redacted = secret_redacted or changed
        next_action_source = data.get("next_battle_action") or _remaining_from_payload(data)
        next_action, changed = _safe_report_text(next_action_source, 220)
        secret_redacted = secret_redacted or changed
        result = _normalize_result(data.get("result", ""))
        opponent, changed = _safe_report_text(data.get("opponent", ""), 120)
        secret_redacted = secret_redacted or changed
        battle_ids = _battle_ids_from_report_text(
            " ".join(
                str(data.get(key) or "")
                for key in ("battle_id", "replay_url", "replay", "proof", "headline")
            )
        )
        public_verified = None
        if _payload_declares_replay_public_state(data):
            public_verified = _payload_replay_is_public(data)
        replay = _first_replay_summary(
            data.get("replay_url"), data.get("replay"), data.get("battle_id"), public_verified=public_verified
        )
        batch_results = data.get("batch_results")
        if replay.get("status") == "absent" and isinstance(batch_results, list):
            replay = _first_batch_replay_summary(batch_results)
        ops_signal = "operational-loss" if _detect_operational_flag(data) else ("loss-review" if result == "loss" else "routine")
        why, changed = _safe_report_text(_why_from_payload(data), 240)
        secret_redacted = secret_redacted or changed
        current_state = _current_battle_state(
            result=result,
            opponent=opponent,
            turns=data.get("turns"),
            battle_id=data.get("battle_id"),
            replay=replay,
            fallback=headline,
        )
        next_hermes_action = _hermes_next_action(
            ops_signal=ops_signal,
            result=result,
            next_action=next_action,
        )
        return {
            "eventClass": event_class,
            "headline": headline,
            "result": result,
            "opponent": opponent,
            "battleIds": battle_ids,
            "replay": replay,
            "currentBattleState": current_state,
            "viewerSummary": viewer,
            "whyItMatters": why,
            "opsSignal": ops_signal,
            "nextAction": next_action,
            "nextHermesAction": next_hermes_action,
            "secretLikeContentRedacted": secret_redacted,
        }

    formatted = raw if is_contract_message(raw) else raw.strip()
    header = formatted.splitlines()[0] if formatted.splitlines() else ""
    header_match = re.match(r"^\[(?P<event>[A-Z_]+)\]\s+\*\*(?P<head>.*?)\*\*", header)
    event_class = header_match.group("event") if header_match else ""
    headline_source = header_match.group("head") if header_match else header
    what = _extract_contract_section(formatted, "What happened:")
    remaining = _extract_contract_section(formatted, "Remaining:")
    result, opponent = _result_and_opponent_from_text(" ".join([headline_source, what]))
    headline, changed = _safe_report_text(headline_source, _MAX_HEADLINE_LEN)
    secret_redacted = secret_redacted or changed
    viewer, changed = _safe_report_text(what or headline_source or "pending", 360)
    secret_redacted = secret_redacted or changed
    next_action_match = re.search(r"next battle focus:\s*([^;]+)", what, re.IGNORECASE)
    next_action_source = next_action_match.group(1) if next_action_match else remaining
    next_action, changed = _safe_report_text(next_action_source or "pending", 220)
    secret_redacted = secret_redacted or changed
    battle_ids = _battle_ids_from_report_text(formatted)
    replay = _first_replay_summary(formatted)
    why = _extract_contract_section(formatted, "Why it matters:")
    why, changed = _safe_report_text(why or "pending", 240)
    secret_redacted = secret_redacted or changed
    ops_signal = "operational-loss" if any(
        word in " ".join([headline_source, what]).lower()
        for word in ("inactivity", "disconnect", "timed out", "timeout", "reconnect")
    ) else ("loss-review" if result == "loss" else "routine")
    current_state = _current_battle_state(
        result=result,
        opponent=opponent,
        turns=_structured_turns_from_text(formatted),
        battle_id=f"battle-{battle_ids[0]}" if battle_ids else "",
        replay=replay,
        fallback=what or headline_source,
    )
    next_hermes_action = _hermes_next_action(
        ops_signal=ops_signal,
        result=result,
        next_action=next_action,
    )
    return {
        "eventClass": event_class,
        "headline": headline,
        "result": result,
        "opponent": opponent,
        "battleIds": battle_ids,
        "replay": replay,
        "currentBattleState": current_state,
        "viewerSummary": viewer,
        "whyItMatters": why,
        "opsSignal": ops_signal,
        "nextAction": next_action,
        "nextHermesAction": next_hermes_action,
        "secretLikeContentRedacted": secret_redacted,
    }


def _first_battle_id(*values: object) -> str | None:
    for value in values:
        for battle_id in _battle_ids_from_report_text(str(value or ""), limit=1):
            return f"battle-{battle_id}"
        candidate = public_replay_id_candidate(value)
        if candidate:
            return f"battle-{candidate}"
    return None


def _structured_turns_from_text(text: str) -> int | None:
    for pattern in (r"\bturns\s*[=:]\s*(\d+)\b", r"\bin\s+(\d+)\s+turns\b", r"\b(\d+)\s+turns\b"):
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            turns = _positive_turn_count(match.group(1))
            if turns is not None:
                return turns
    return None


def _structured_proof_items(value: object) -> list[str]:
    cleaned = _clean_line(value)
    items = _split_semicolon_list(cleaned)
    if not items and cleaned and cleaned != "pending":
        items = [cleaned]
    safe_items: list[str] = []
    seen: set[str] = set()
    for item in items[:_MAX_PROOF_ITEMS]:
        safe, _changed = _safe_report_text(item, 220)
        if safe and safe not in seen:
            safe_items.append(safe)
            seen.add(safe)
    return safe_items


def structured_report_fields(content: str, *, event_type: str = "") -> dict[str, object]:
    """Extract safe machine-readable fields from a queued report payload or message."""
    raw = content or ""
    try:
        data = parse_contract_payload(raw.strip())
    except Exception:
        data = {}

    summary = redacted_report_summary(raw)
    result = _normalize_result(data.get("result") if data else summary.get("result"))
    opponent = _clean_line((data.get("opponent") if data else summary.get("opponent")) or "")
    battle_id = _first_battle_id(
        data.get("battle_id") if data else None,
        data.get("replay_url") if data else None,
        data.get("replay") if data else None,
        data.get("proof") if data else None,
        raw,
    )
    turns = _positive_turn_count(data.get("turns")) if data else None
    if turns is None:
        turns = _structured_turns_from_text(raw)

    winner = _clean_line(data.get("winner") if data else "") or None
    loser = _clean_line(data.get("loser") if data else "") or None
    if not winner and not loser and result in {"win", "loss"}:
        bot_label = _clean_line((data.get("player") if data else "") or (data.get("bot") if data else "") or "fouler-play")
        if result == "win":
            winner = bot_label
            loser = opponent or None
        elif result == "loss":
            winner = opponent or None
            loser = bot_label

    proof_source = _proof_from_payload(data) if data else _extract_contract_section(raw, "Proof:")
    proof_items = _structured_proof_items(proof_source)
    replay = summary.get("replay") if isinstance(summary.get("replay"), dict) else {}
    proof = {
        "items": proof_items,
        "battleIds": summary.get("battleIds") if isinstance(summary.get("battleIds"), list) else ([] if battle_id is None else [battle_id.replace("battle-", "", 1)]),
        "replay": replay,
    }
    if data and data.get("source"):
        source, _changed = _safe_report_text(data.get("source"), 120)
        proof["source"] = source

    current_state = _current_battle_state(
        result=result,
        opponent=opponent,
        turns=turns,
        battle_id=battle_id,
        replay=replay,
        fallback=summary.get("viewerSummary"),
    )
    why_it_matters = _clean_line(summary.get("whyItMatters"))
    next_hermes_action = _clean_line(summary.get("nextHermesAction")) or _hermes_next_action(
        ops_signal=summary.get("opsSignal"),
        result=result,
        next_action=summary.get("nextAction"),
    )
    proof_has_signal = bool(
        proof_items
        or proof.get("battleIds")
        or (isinstance(replay, dict) and replay.get("status") != "absent")
    )
    proof_readiness = _proof_readiness(
        event_type=event_type,
        result=result,
        battle_id=battle_id,
        proof=proof if proof_has_signal else None,
        analysis=summary,
        turns=turns,
        next_action=next_hermes_action,
        replay=replay,
    )

    analysis = {
        "eventClass": summary.get("eventClass") or (data.get("event_class") if data else None),
        "headline": summary.get("headline"),
        "result": result or None,
        "opponent": opponent or None,
        "currentBattleState": current_state,
        "viewerSummary": summary.get("viewerSummary"),
        "whyItMatters": why_it_matters,
        "opsSignal": summary.get("opsSignal"),
        "nextAction": summary.get("nextAction"),
        "nextHermesAction": next_hermes_action,
        "proofReadiness": proof_readiness,
    }
    return {
        "battle_id": battle_id,
        "winner": winner,
        "loser": loser,
        "turns": turns,
        "proof": proof if proof_has_signal else None,
        "analysis": analysis if any(value for value in analysis.values()) else None,
        "current_battle_state": current_state,
        "why_it_matters": why_it_matters,
        "next_hermes_action": next_hermes_action,
        "proof_readiness": proof_readiness,
    }


def summarize_items(items: Iterable[str], fallback: str = "none") -> str:
    cleaned = [_clean_line(item) for item in items if _clean_line(item)]
    return "; ".join(cleaned) if cleaned else fallback


def summarize_recent_results(battles: Sequence[dict], *, window: int = 5) -> dict[str, object]:
    recent = list(battles)[-window:]
    wins = sum(1 for battle in recent if _normalize_result(battle.get("result")) == "win")
    losses = sum(1 for battle in recent if _normalize_result(battle.get("result")) == "loss")
    streak_kind = "none"
    streak = 0
    for battle in reversed(recent):
        outcome = _normalize_result(battle.get("result"))
        if outcome not in {"win", "loss"}:
            break
        if streak_kind == "none":
            streak_kind = outcome
        if outcome != streak_kind:
            break
        streak += 1
    return {
        "window_size": len(recent),
        "wins": wins,
        "losses": losses,
        "record": f"last {len(recent)}: {wins}-{losses} ({int(round((wins / len(recent)) * 100)) if recent else 0}% WR)",
        "streak": f"{streak_kind} x{streak}" if streak and streak_kind != "none" else "no streak",
    }


def detect_notable_reason(text: str) -> str:
    cleaned = _clean_line(text).lower()
    if not cleaned:
        return ""
    if any(word in cleaned for word in ("inactive", "disconnect", "timed out", "timeout", "reconnected")):
        return "battle ended through inactivity/disconnect behavior"
    if "forfeit" in cleaned:
        return "battle ended on forfeit"
    if any(word in cleaned for word in ("hazard", "spikes", "stealth rock")):
        return "hazard pressure shaped the result"
    if any(word in cleaned for word in ("sweep", "cleaned", "setup")):
        return "setup sequence decided the endgame"
    return ""


def top_recurring_issue(reasons: Sequence[str]) -> str:
    cleaned = [detect_notable_reason(reason) or _clean_line(reason) for reason in reasons if _clean_line(reason)]
    if not cleaned:
        return ""
    issue, count = Counter(cleaned).most_common(1)[0]
    return issue if count <= 1 else f"{issue} ({count} recent cases)"
