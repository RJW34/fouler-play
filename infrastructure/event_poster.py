#!/usr/bin/env python3
"""Validate Fouler observations and atomically hand them to DEKU's local outbox.

This process has no network-delivery authority and owns no chat credentials.
DEKU's separately managed relay is the only component allowed to transport the
observation files written here.
"""

import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
import argparse
import copy
from pathlib import Path
from typing import Any, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure import event_queue_lib
from infrastructure.event_queue_lib import (
    get_pending_events,
    mark_posted,
    mark_failed,
    expire_old_events,
    quarantine_stale_battle_results,
    archive_stale_failed_events,
    cleanup_queue,
    queue_stats,
    queue_health_summary,
)
from infrastructure.gen9_validation import Gen9Validator
from infrastructure.discord_reporting import (
    _extract_contract_section,
    why_adds_no_information,
    why_is_constant_copy,
    canonical_replay_url,
    format_payload_or_message,
    is_generic_why_text,
    public_replay_id_candidate,
    redacted_report_summary,
    structured_report_fields,
)
from infrastructure.runtime_paths import (
    resolve_runtime_paths,
    validate_external_runtime_path,
)

# Configuration
POLL_INTERVAL = float(os.getenv("EVENT_POSTER_POLL_SEC", "2"))
CLEANUP_INTERVAL = 300  # Cleanup every 5 minutes
REPLAY_RESOLVE_ATTEMPTS = max(1, int(os.getenv("EVENT_POSTER_REPLAY_RESOLVE_ATTEMPTS", "1")))
REPLAY_RESOLVE_DELAY_SEC = max(0.0, float(os.getenv("EVENT_POSTER_REPLAY_RESOLVE_DELAY_SEC", "0")))
REPLAY_RESOLVE_TIMEOUT_SEC = max(0.1, float(os.getenv("EVENT_POSTER_REPLAY_RESOLVE_TIMEOUT_SEC", "3")))
_RUNTIME_PATHS = resolve_runtime_paths(PROJECT_ROOT)
RUNTIME_STATE_ROOT = _RUNTIME_PATHS.state_root
PID_DIR = RUNTIME_STATE_ROOT / "pids"
BOT_MAIN_PID_FILE = PID_DIR / "bot_main.pid"
BATTLE_STATS_FILE = RUNTIME_STATE_ROOT / "battle_stats.json"
TRUTH_DIR = RUNTIME_STATE_ROOT / "truth"
DISCORD_REPORTING_PROOF = TRUTH_DIR / "discord-reporting.json"
DISCORD_DELIVERY_PROOF = TRUTH_DIR / "discord-delivery.json"
DISCORD_DOCTOR_PROOF = TRUTH_DIR / "discord-reporting-doctor.json"
DEKU_EVENT_QUEUE_ROOT = validate_external_runtime_path(
    os.getenv("DEKU_EVENT_QUEUE_ROOT", str(RUNTIME_STATE_ROOT / "deku-events")),
    release_root=PROJECT_ROOT,
    label="DEKU event queue root",
)
# Read-only compatibility for existing proof tooling. Production reporting no
# longer buffers or emits cycle digests; every battle result goes to DEKU.
BATTLE_DIGEST_STATE = validate_external_runtime_path(
    os.getenv(
        "FOULER_BATTLE_DIGEST_STATE",
        str(TRUTH_DIR / "battle-report-digest-state.json"),
    ),
    release_root=PROJECT_ROOT,
    label="legacy battle digest state",
)
BATTLE_DIGEST_SIZE = max(1, int(os.getenv("FOULER_CYCLE_DIGEST_BATTLE_COUNT", "30")))
BATTLE_DIGEST_MAX_AGE_SEC = max(
    60,
    int(os.getenv("FOULER_BATTLE_DIGEST_MAX_AGE_SEC", "900")),
)
BATTLE_DIGEST_REPORTED_ID_LIMIT = max(
    BATTLE_DIGEST_SIZE * 2,
    int(os.getenv("FOULER_BATTLE_DIGEST_REPORTED_ID_LIMIT", "500")),
)
EXPIRY_SEC = max(
    60,
    int(os.getenv("EVENT_POSTER_EXPIRY_SEC", str(event_queue_lib.DEFAULT_EXPIRY_SEC))),
)
BATTLE_ID_RE = re.compile(r"\b(?:battle-)?gen9ou-[A-Za-z0-9-]+\b|battle `([^`]+)`")
ELO_TRANSITION_RE = re.compile(
    r"\bELO\s+(?:gained\s+\d+|lost\s+\d+|unchanged)\s+"
    r"\((?P<before>\d+)\s*(?:->|\u2192)\s*(?P<after>\d+),\s*(?P<delta>[+-]?\d+)\)",
    re.IGNORECASE,
)
PENDING_REPLAY_ID_RE = re.compile(
    r"replay\s+pending\s+public\s+upload\s+`?((?:battle-)?gen9ou-[A-Za-z0-9-]+)`?",
    re.IGNORECASE,
)
REPLAY_STATUS_PENDING_RE = re.compile(
    r"replay_status\s*=\s*pending[-_\s]+public[-_\s]+upload",
    re.IGNORECASE,
)
GEN9_VALIDATED_EVENT_TYPE_MARKERS = (
    "analysis",
    "report",
    "autoresearch",
    "deep_dive",
    "summary",
    "battle_result",
    "battle-summary",
    "proof",
    "post_packet",
)
GEN9_VALIDATED_STRUCTURED_FIELDS = (
    "analysis",
    "proof",
    "current_battle_state",
    "next_hermes_action",
    "decisive_reason",
    "battle_id",
    "winner",
    "loser",
)
GEN9_VALIDATED_CONTENT_MARKERS = (
    "[proof]",
    "why it matters",
    "next hermes action",
    "showdown",
    "gen 9",
    "gen9",
    "pokemon",
    "matchup",
    "ability",
    "item",
    "move",
    "tera",
    "hazard",
)
REPORT_QUALITY_BANNED_PHRASES = (
    "battle updates should",
    "operator-facing battle posts should",
    "was converted once the bot secured the favorable endgame",
    "closed the endgame before the bot stabilized the board",
    "keep watching whether this line keeps converting",
    "review the replay before the next queue and tag whether this was policy, matchup, or ops",
)
RECENT_RECORD_RE = re.compile(
    r"\blast\s+(?P<count>\d+)\s*:\s*(?P<wins>\d+)\s*-\s*(?P<losses>\d+)\s*\(\s*(?P<wr>\d+)%\s*WR\s*\)",
    re.IGNORECASE,
)
ROUTINE_LOCAL_ONLY_EVENT_TYPES = {
    "autoresearch_summary",
    "autoresearch_deep_dive",
    "batch_analyzed",
    "batch_complete",
    "batch_report",
    "mission_alert",
    "pipeline_report",
}

# Logging is configured only by the CLI entry point. Importers such as the
# cycle reporter must not create files inside an immutable release.
RUNTIME_LOG_ROOT = _RUNTIME_PATHS.log_root
LOG_FILE = validate_external_runtime_path(
    os.getenv("EVENT_POSTER_LOG", str(RUNTIME_LOG_ROOT / "event_poster.log")),
    release_root=PROJECT_ROOT,
    label="event poster log",
)
DEKU_CATEGORY_BY_CHANNEL = {
    "battles": "fouler-play",
    "feedback": "fouler-play",
    "project": "fouler-play",
    "workspace": "devstream",
}
logger = logging.getLogger("event_poster")
logger.addHandler(logging.NullHandler())


def _configure_cli_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )

# Graceful shutdown
_running = True


def _signal_handler(signum, frame):
    global _running
    logger.info(f"Received signal {signum}, shutting down...")
    _running = False


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _cycle_id() -> str:
    return os.getenv("DEVSTREAM_CYCLE_ID") or os.getenv("FOULER_PLAY_CYCLE_ID") or f"discord-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _extract_battle_ids_from_text(text: str, limit: int = 20) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for match in BATTLE_ID_RE.finditer(text or ""):
        raw = match.group(1) or match.group(0)
        if raw and raw.isdigit():
            raw = f"gen9ou-{raw}"
        if raw.startswith("battle-"):
            raw = raw.replace("battle-", "", 1)
        if raw not in seen:
            ids.append(raw)
            seen.add(raw)
        if len(ids) >= limit:
            break
    return ids


def _read_queue_events() -> list[dict[str, Any]]:
    try:
        from infrastructure.event_queue_lib import read_queue

        events = read_queue()
        return [event for event in events if isinstance(event, dict)]
    except Exception as exc:
        logger.debug("Failed to read queue for proof: %s", exc)
        return []


def _queue_summary(events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    events = events if events is not None else _read_queue_events()
    stats = queue_stats()
    health = queue_health_summary(events)
    return {
        **stats,
        "pendingEventTypes": health["pendingEventTypes"],
        "pendingAgeBuckets": health["pendingAgeBuckets"],
        "pendingPlaceholderFieldCounts": health["pendingPlaceholderFieldCounts"],
        "pendingBattleResultStructuredFields": health["pendingBattleResultStructuredFields"],
        "pendingBattleResults": health["pendingBattleResults"],
        "stalePendingBacklog": health["stalePendingBacklog"],
        "stalePendingBattleResults": health["stalePendingBattleResults"],
        "freshPendingBacklog": health["freshPendingBacklog"],
        "freshPendingBattleResults": health["freshPendingBattleResults"],
        "staleAfterSeconds": health["staleAfterSeconds"],
        "failedEventTypes": health["failedEventTypes"],
        "expiredEventTypes": health["expiredEventTypes"],
        "statusCounts": health["statusCounts"],
        "pendingBacklog": health["pendingBacklog"],
        "oldestPendingAgeSeconds": health["oldestPendingAgeSeconds"],
        "oldestPendingEventId": health["oldestPendingEventId"],
        "deliveryFailures": health["deliveryFailures"],
        "retryingDeliveries": health["retryingDeliveries"],
        "expiredDeliveries": health["expiredDeliveries"],
        "dnsFailures": health["dnsFailures"],
        "webhookFailures": health["webhookFailures"],
        "failureTypes": health["failureTypes"],
        "healthStatus": health["status"],
        "ready": health["ready"],
        "health": health,
    }


def _latest_battle_result_summary(
    *,
    current_event: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    if isinstance(current_event, dict) and current_event.get("event_type") == "battle_result":
        candidates.append(current_event)
    for event in events if events is not None else _read_queue_events():
        if isinstance(event, dict) and event.get("event_type") == "battle_result":
            candidates.append(event)
    if not candidates:
        return None

    def event_ts(event: dict[str, Any]) -> float:
        try:
            return float(event.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0.0

    latest = max(candidates, key=event_ts)
    content = str(latest.get("content") or "")
    structured = structured_report_fields(content, event_type="battle_result") if content else _event_structured_fields(latest)
    return {
        "eventId": latest.get("id"),
        "eventStatus": latest.get("status"),
        "eventTimestamp": latest.get("timestamp"),
        "channel": latest.get("channel"),
        "battle_id": latest.get("battle_id") or structured.get("battle_id"),
        "result": latest.get("result") or (structured.get("analysis") or {}).get("result"),
        "winner": latest.get("winner") or structured.get("winner"),
        "loser": latest.get("loser") or structured.get("loser"),
        "turns": latest.get("turns") if latest.get("turns") is not None else structured.get("turns"),
        "proof": structured.get("proof"),
        "analysis": structured.get("analysis"),
        "reportSummary": (
            redacted_report_summary(str(latest.get("content") or ""))
            if latest.get("content") else {}
        ),
    }


def _proof_report_paths() -> dict[str, str]:
    return {
        "discordReporting": _relative(DISCORD_REPORTING_PROOF),
        "discordDelivery": _relative(DISCORD_DELIVERY_PROOF),
        "discordDoctor": _relative(DISCORD_DOCTOR_PROOF),
        "discordBacklogArchive": _relative(TRUTH_DIR / "discord-backlog-archive.json"),
        "eventPosterLog": _relative(LOG_FILE),
        "queueFile": _relative(event_queue_lib.QUEUE_FILE),
    }


def _deku_category(destination_alias: str) -> str:
    return DEKU_CATEGORY_BY_CHANNEL.get(str(destination_alias or "").strip(), "fouler-play")


def _transport_summary(destination_alias: str) -> dict[str, Any]:
    pending = DEKU_EVENT_QUEUE_ROOT / "pending"
    return {
        "type": "deku_event_queue",
        "configured": pending.is_dir(),
        "source": str(pending),
        "category": _deku_category(destination_alias),
        "networkDeliveryOwnedByProject": False,
        "authority": "none",
        "credentialMaterialIncluded": False,
    }


def _read_deku_relay_status() -> dict[str, Any]:
    path = DEKU_EVENT_QUEUE_ROOT / "status.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _deku_event_queue_status(destination_alias: str) -> dict[str, Any]:
    pending = DEKU_EVENT_QUEUE_ROOT / "pending"
    relay = _read_deku_relay_status()
    return {
        "ready": bool(pending.is_dir() and relay.get("ok")),
        "transport": "deku_event_queue",
        "category": _deku_category(destination_alias),
        "queueRoot": str(DEKU_EVENT_QUEUE_ROOT),
        "pendingDirectoryExists": pending.is_dir(),
        "relayCheckedAtUtc": relay.get("checkedAtUtc"),
        "relayOk": relay.get("ok"),
        "relayPendingAfterRun": relay.get("pendingAfterRun"),
        "credentialMaterialIncluded": False,
    }


def _deku_event_proof(event: dict[str, Any]) -> list[str]:
    proof = [str(Path(event_queue_lib.QUEUE_FILE))]
    proof.extend(
        str(item).strip()
        for item in event.get("evidence_refs") or []
        if str(item).strip()
    )
    replay_url = str(event.get("replay_url") or "").strip()
    if replay_url.startswith(("https://", "http://")):
        proof.append(replay_url)
    return list(dict.fromkeys(proof))


def _recommended_next_action(event: dict[str, Any], report: dict[str, Any]) -> str:
    explicit = str(event.get("recommended_next_action") or "").strip()
    if explicit:
        return explicit[:500]
    inferred = str(report.get("nextAction") or "").strip()
    if inferred and inferred.lower() not in {"none", "pending"}:
        return inferred[:500]
    return "Review the referenced local evidence during the next DEKU planning pass."


def _observation_dedup_key(event: dict[str, Any], event_type: str, local_event_id: str) -> str:
    battle_key = _battle_observation_key(event) if event_type == "battle_result" else ""
    if battle_key:
        return f"fouler-play:battle-result:{battle_key}"[:240]
    explicit = str(event.get("dedup_key") or "").strip()
    if explicit:
        return explicit[:240]
    digest = hashlib.sha256(
        f"{event_type}:{local_event_id}".encode("utf-8")
    ).hexdigest()
    return f"fouler-play:{event_type}:{digest}"[:240]


def _battle_observation_key(event: dict[str, Any]) -> str:
    value = event.get("battle_id")
    if not value and isinstance(event.get("proof"), dict):
        battle_ids = event["proof"].get("battleIds") or []
        value = battle_ids[0] if battle_ids else None
    text = str(value or "").strip().lower()
    public_id = public_replay_id_candidate(text)
    if public_id:
        text = public_id.lower()
    elif text.startswith("battle-"):
        text = text.removeprefix("battle-")
    return re.sub(r"[^a-z0-9._-]+", "-", text).strip("-.")[:120]


def _deku_project_event_id(event: dict[str, Any], event_type: str, local_event_id: str) -> str:
    battle_key = _battle_observation_key(event) if event_type == "battle_result" else ""
    if battle_key:
        return f"fouler-battle-result-{battle_key}"[:120]
    return ("fouler-%s" % local_event_id)[:120]


def _deku_project_event(event: dict[str, Any], content: str) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "status_update").strip() or "status_update"
    destination_alias = str(event.get("channel") or "battles")
    local_event_id = str(event.get("id") or "").strip()
    status = "warn" if any(marker in event_type.lower() for marker in ("error", "failed", "alert", "stop")) else "done"
    report_summary = redacted_report_summary(content)
    recommended_next_action = _recommended_next_action(event, report_summary)
    report_summary = {
        key: value
        for key, value in report_summary.items()
        if key not in {"nextHermesAction", "actionRequired", "command", "instruction"}
    }
    report_summary["recommendedNextAction"] = recommended_next_action
    evidence_refs = _deku_event_proof(event)
    payload = {
        "schemaVersion": "deku-project-event/v1",
        "id": _deku_project_event_id(event, event_type, local_event_id),
        "source": "fouler-play.event-poster",
        "kind": "observation",
        "authority": "none",
        "producer": "fouler-play",
        "eventType": event_type,
        "dedupKey": _observation_dedup_key(event, event_type, local_event_id),
        "status": status,
        "severity": "warning" if status == "warn" else "info",
        "title": "Fouler Play: %s" % event_type.replace("_", " "),
        "summary": str(report_summary.get("viewerSummary") or report_summary.get("headline") or event_type)[:600],
        "proof": evidence_refs,
        "evidenceRefs": evidence_refs,
        "recommendedNextAction": recommended_next_action,
        "category": _deku_category(destination_alias),
        "payload": {
            "localEventId": local_event_id,
            "destinationAlias": destination_alias,
            "reportSummary": report_summary,
            "sessionId": str(event.get("session_id") or "").strip() or None,
            "cycleId": str(event.get("cycle_id") or "").strip() or None,
            "edgeState": str(event.get("edge_state") or "").strip() or None,
        },
    }
    return payload


def _queue_deku_project_event(event: dict[str, Any], content: str) -> dict[str, Any]:
    payload = _deku_project_event(event, content)
    pending = DEKU_EVENT_QUEUE_ROOT / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", payload["id"]).strip("-.")[:140] or "fouler-event"
    destination = pending / (safe_id + ".json")
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "status": "failed",
                "transport": "deku_event_queue",
                "destinationAlias": event.get("channel"),
                "blockers": ["existing DEKU queue event is unreadable: %s" % type(exc).__name__],
                "errorCode": "deku_event_queue_collision",
            }
        if existing.get("id") != payload["id"]:
            return {
                "ok": False,
                "status": "failed",
                "transport": "deku_event_queue",
                "destinationAlias": event.get("channel"),
                "blockers": ["existing DEKU queue event has a different id"],
                "errorCode": "deku_event_queue_collision",
            }
        return {
            "ok": True,
            "status": "queued",
            "transport": "deku_event_queue",
            "destinationAlias": event.get("channel"),
            "category": payload["category"],
            "eventId": payload["id"],
            "alreadyQueued": True,
            "blockers": [],
        }
    temporary = pending / (".%s.%s.%s.tmp" % (safe_id, os.getpid(), time.time_ns()))
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "ok": True,
        "status": "queued",
        "transport": "deku_event_queue",
        "destinationAlias": event.get("channel"),
        "category": payload["category"],
        "eventId": payload["id"],
        "alreadyQueued": False,
        "blockers": [],
    }


def _empty_battle_digest_state() -> dict[str, Any]:
    return {
        "schemaVersion": "fouler-play-battle-digest-state/v1",
        "updatedAtUtc": _iso_now(),
        "pendingBattles": [],
        "reportedEventIds": [],
        "reportedCycleIds": [],
        "lastDigest": None,
    }


def _read_battle_digest_state() -> dict[str, Any]:
    if not BATTLE_DIGEST_STATE.exists():
        return _empty_battle_digest_state()
    try:
        payload = json.loads(BATTLE_DIGEST_STATE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"battle digest state is unreadable: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("battle digest state is not an object")
    if payload.get("schemaVersion") != "fouler-play-battle-digest-state/v1":
        raise RuntimeError("battle digest state schema is unsupported")
    if not isinstance(payload.get("pendingBattles"), list):
        raise RuntimeError("battle digest pendingBattles is not a list")
    if not isinstance(payload.get("reportedEventIds"), list):
        raise RuntimeError("battle digest reportedEventIds is not a list")
    if "reportedCycleIds" not in payload:
        payload["reportedCycleIds"] = []
    if not isinstance(payload.get("reportedCycleIds"), list):
        raise RuntimeError("battle digest reportedCycleIds is not a list")
    return payload


def _write_battle_digest_state(state: dict[str, Any]) -> None:
    state = dict(state)
    state["updatedAtUtc"] = _iso_now()
    BATTLE_DIGEST_STATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = BATTLE_DIGEST_STATE.with_name(
        f".{BATTLE_DIGEST_STATE.name}.{os.getpid()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, BATTLE_DIGEST_STATE)
    finally:
        temporary.unlink(missing_ok=True)


def _clean_digest_text(value: object, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _battle_digest_entry(event: dict[str, Any], *, now: float) -> dict[str, Any]:
    content = str(event.get("content") or "")
    summary = redacted_report_summary(content)
    analysis = event.get("analysis") if isinstance(event.get("analysis"), dict) else {}
    proof = event.get("proof") if isinstance(event.get("proof"), dict) else {}
    replay = proof.get("replay") if isinstance(proof.get("replay"), dict) else {}
    battle_ids = proof.get("battleIds") if isinstance(proof.get("battleIds"), list) else []
    battle_id = _clean_digest_text(
        event.get("battle_id")
        or (battle_ids[0] if battle_ids else None)
        or (summary.get("battleIds") or [None])[0],
        160,
    )
    result = _clean_digest_text(event.get("result") or analysis.get("result") or summary.get("result"), 20).lower()
    if result == "draw":
        result = "tie"
    if result not in {"win", "loss", "tie"}:
        result = "unknown"
    replay_url = (
        canonical_replay_url(event.get("replay_url"))
        or canonical_replay_url(replay.get("url"))
        or canonical_replay_url(summary.get("replay", {}).get("url") if isinstance(summary.get("replay"), dict) else None)
    )
    try:
        turns = int(event.get("turns")) if event.get("turns") is not None else None
    except (TypeError, ValueError):
        turns = None
    entry = {
        "localEventId": _clean_digest_text(event.get("id"), 160),
        "battleId": battle_id or None,
        "result": result,
        "opponent": _clean_digest_text(analysis.get("opponent") or summary.get("opponent"), 80) or None,
        "turns": turns if turns is not None and turns > 0 else None,
        "replayUrl": replay_url or None,
        "eventTimestamp": event.get("timestamp"),
        "bufferedAtEpoch": now,
        "sessionId": _clean_digest_text(event.get("session_id"), 160) or None,
        "cycleId": _clean_digest_text(event.get("cycle_id"), 220) or None,
        "sessionExpectedBattles": event.get("session_expected_battles"),
    }
    elo = ELO_TRANSITION_RE.search(content)
    if elo:
        entry["elo"] = {
            "before": int(elo.group("before")),
            "after": int(elo.group("after")),
            "delta": int(elo.group("delta")),
        }
    return entry


def _battle_digest_event(entries: list[dict[str, Any]]) -> dict[str, Any]:
    local_ids = [str(entry.get("localEventId") or "") for entry in entries]
    session_id = str(entries[0].get("sessionId") or "").strip()
    cycle_id = str(entries[0].get("cycleId") or "").strip()
    digest_identity = cycle_id or session_id or "|".join(local_ids)
    digest_key = hashlib.sha256(digest_identity.encode("utf-8")).hexdigest()[:20]
    result_counts = {
        result: sum(1 for entry in entries if entry.get("result") == result)
        for result in ("win", "loss", "tie", "unknown")
    }
    total_turns = sum(
        int(entry["turns"])
        for entry in entries
        if isinstance(entry.get("turns"), int)
    )
    elo_entries = [entry["elo"] for entry in entries if isinstance(entry.get("elo"), dict)]
    elo_start = elo_entries[0].get("before") if elo_entries else None
    elo_end = elo_entries[-1].get("after") if elo_entries else None
    elo_delta = elo_end - elo_start if isinstance(elo_start, int) and isinstance(elo_end, int) else None
    latest_replay = next(
        (str(entry.get("replayUrl")) for entry in reversed(entries) if entry.get("replayUrl")),
        None,
    )
    first_battle_id = entries[0].get("battleId")
    latest_battle_id = entries[-1].get("battleId")
    latest_opponent = entries[-1].get("opponent")
    digest = {
        "battleCount": len(entries),
        "wins": result_counts["win"],
        "losses": result_counts["loss"],
        "ties": result_counts["tie"],
        "unknown": result_counts["unknown"],
        "totalTurns": total_turns,
        "eloStart": elo_start,
        "eloEnd": elo_end,
        "eloDelta": elo_delta,
        "firstBattleId": first_battle_id,
        "latestBattleId": latest_battle_id,
        "latestReplayUrl": latest_replay,
        "sessionId": session_id or None,
        "cycleId": cycle_id or None,
    }
    parts = [
        "Fouler ladder digest: "
        f"{len(entries)} battles ({digest['wins']}W-{digest['losses']}L-{digest['ties']}T)"
    ]
    if total_turns:
        parts[0] += f", {total_turns} turns"
    parts[0] += "."
    if elo_start is not None and elo_end is not None:
        sign = "+" if elo_delta is not None and elo_delta >= 0 else ""
        parts.append(f"ELO {elo_start} -> {elo_end} ({sign}{elo_delta}).")
    if first_battle_id or latest_battle_id:
        parts.append(f"Range {first_battle_id or 'unknown'} to {latest_battle_id or 'unknown'}.")
    if latest_opponent:
        parts.append(f"Latest opponent: {latest_opponent}.")
    if latest_replay:
        parts.append(f"Latest replay: {latest_replay}")
    return {
        "id": f"cycle-digest-{digest_key}",
        "event_type": "fouler-cycle-digest",
        "channel": "battles",
        "content": " ".join(parts),
        "replay_url": latest_replay,
        "session_id": session_id or None,
        "cycle_id": cycle_id or None,
        "dedup_key": f"fouler-play:cycle-digest:{cycle_id or session_id or digest_key}",
        "recommended_next_action": "Review the bounded session evidence before authorizing another cycle.",
        "digest": digest,
    }


def _battle_digest_failure(exc: BaseException) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "failed",
        "transport": "deku_event_digest_buffer",
        "destinationAlias": "battles",
        "blockers": [f"battle digest state failed: {type(exc).__name__}"],
        "errorCode": "battle_digest_state_failed",
        "flushed": False,
    }


def _flush_battle_digest_if_due(*, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    try:
        state = _read_battle_digest_state()
        pending = [item for item in state["pendingBattles"] if isinstance(item, dict)]
        if not pending:
            return {
                "ok": True,
                "status": "idle",
                "transport": "deku_event_digest_buffer",
                "destinationAlias": "battles",
                "blockers": [],
                "flushed": False,
            }
        first_session = str(pending[0].get("sessionId") or "").strip()
        first_cycle = str(pending[0].get("cycleId") or "").strip()
        batch = []
        for item in pending:
            if str(item.get("cycleId") or "").strip() != first_cycle:
                break
            batch.append(item)
        expected_count = next(
            (
                int(item.get("sessionExpectedBattles"))
                for item in batch
                if str(item.get("sessionExpectedBattles") or "").isdigit()
                and int(item.get("sessionExpectedBattles")) > 0
            ),
            BATTLE_DIGEST_SIZE,
        )
        first_buffered = float(batch[0].get("bufferedAtEpoch") or now)
        count_due = len(batch) >= expected_count
        age_due = (now - first_buffered) >= BATTLE_DIGEST_MAX_AGE_SEC
        session_changed_due = len(batch) < len(pending)
        if not count_due and not age_due and not session_changed_due:
            return {
                "ok": True,
                "status": "digested",
                "transport": "deku_event_digest_buffer",
                "destinationAlias": "battles",
                "blockers": [],
                "flushed": False,
                "digestPendingCount": len(batch),
                "sessionId": first_session or None,
                "cycleId": first_cycle or None,
                "expectedBattleCount": expected_count,
            }
        digest_event = _battle_digest_event(batch)
        result = _queue_deku_project_event(digest_event, digest_event["content"])
        if not result.get("ok"):
            return {**result, "flushed": False, "digestEvent": digest_event}
        flushed_ids = [str(item.get("localEventId") or "") for item in batch]
        state["pendingBattles"] = pending[len(batch):]
        reported_ids = [str(item) for item in state["reportedEventIds"] if item]
        reported_ids.extend(flushed_ids)
        state["reportedEventIds"] = reported_ids[-BATTLE_DIGEST_REPORTED_ID_LIMIT:]
        reported_cycles = [str(item) for item in state["reportedCycleIds"] if item]
        if first_cycle:
            reported_cycles.append(first_cycle)
        state["reportedCycleIds"] = list(dict.fromkeys(reported_cycles))[-100:]
        state["lastDigest"] = {
            "digestId": digest_event["id"],
            "queuedEventId": result.get("eventId"),
            "queuedAtUtc": _iso_now(),
            "battleCount": len(batch),
            "sessionId": first_session or None,
            "cycleId": first_cycle or None,
            "firstBattleId": batch[0].get("battleId"),
            "latestBattleId": batch[-1].get("battleId"),
        }
        _write_battle_digest_state(state)
        return {**result, "flushed": True, "digestEvent": digest_event}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error("Battle digest flush failed: %s", exc)
        return _battle_digest_failure(exc)


def _buffer_battle_result(event: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    try:
        state = _read_battle_digest_state()
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            raise ValueError("battle result event is missing id")
        reported_ids = {str(item) for item in state["reportedEventIds"] if item}
        session_id = str(event.get("session_id") or "").strip()
        cycle_id = str(event.get("cycle_id") or "").strip()
        reported_cycles = {str(item) for item in state["reportedCycleIds"] if item}
        if cycle_id and cycle_id in reported_cycles:
            return {
                "ok": True,
                "status": "retained-local-after-session-digest",
                "transport": "deku_event_digest_buffer",
                "destinationAlias": "battles",
                "blockers": [],
                "flushed": False,
                "alreadyDigested": True,
                "sessionId": session_id,
                "cycleId": cycle_id,
            }
        pending_ids = {
            str(item.get("localEventId") or "")
            for item in state["pendingBattles"]
            if isinstance(item, dict)
        }
        if event_id not in reported_ids and event_id not in pending_ids:
            state["pendingBattles"].append(_battle_digest_entry(event, now=now))
            _write_battle_digest_state(state)
        result = _flush_battle_digest_if_due(now=now)
        if result.get("ok"):
            already_digested = event_id in reported_ids or event_id in pending_ids
            result["alreadyDigested"] = already_digested
            if event_id in reported_ids and not result.get("flushed"):
                result["status"] = "digested"
        return result
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.error("Battle digest buffer failed: %s", exc)
        return _battle_digest_failure(exc)


def _clean_status_text(value: object) -> str:
    return str(value or "").lstrip("\ufeff").strip()


def _generic_event_report_summary(event: dict[str, Any]) -> dict[str, Any]:
    content = _clean_status_text(event.get("content"))
    headline = content.splitlines()[0].strip() if content.splitlines() else str(event.get("event_type") or "status")
    return {
        "eventType": str(event.get("event_type") or "unknown"),
        "headline": headline[:180],
        "viewerSummary": headline[:180],
        "currentState": content[:500],
        "whyItMatters": "Non-battle status event; battle and replay fields are intentionally not inferred.",
        "nextHermesAction": str(event.get("next_hermes_action") or "Use the linked source report or repair queue for the next action."),
        "secretLikeContentRedacted": False,
    }


def _generic_event_analysis(event: dict[str, Any]) -> dict[str, Any]:
    summary = _generic_event_report_summary(event)
    return {
        "eventClass": "status_update",
        "headline": summary.get("headline"),
        "viewerSummary": summary.get("viewerSummary"),
        "currentState": summary.get("currentState"),
        "whyItMatters": summary.get("whyItMatters"),
        "nextHermesAction": summary.get("nextHermesAction"),
        "proofReadiness": {
            "status": "proof-ready",
            "classification": "status-update-proof",
            "readyForHermes": True,
            "missingFields": [],
            "qualityGaps": [],
            "blockers": [],
        },
    }


def write_delivery_proof(
    *,
    status: str,
    event: dict[str, Any] | None = None,
    destination_alias: str | None = None,
    dry_run: bool = False,
    blockers: list[str] | None = None,
    http_status: int | None = None,
    retry_after: str | None = None,
    error_code: str | None = None,
    transport: str | None = None,
) -> dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    destination_alias = destination_alias or str(event.get("channel") or "unknown")
    event_type = str(event.get("event_type") or "")
    is_battle_result = event_type == "battle_result"
    battle_ids = _extract_battle_ids_from_text(str(event.get("content") or "")) if is_battle_result else []
    queue_events = _read_queue_events()
    report_summary = (
        redacted_report_summary(str(event.get("content") or ""))
        if event and is_battle_result
        else _generic_event_report_summary(event) if event else {}
    )
    structured_fields = _event_structured_fields(event)
    payload = {
        "schemaVersion": "fouler-play-discord-delivery/v1",
        "attemptedAtUtc": _iso_now(),
        "cycleId": _cycle_id(),
        "status": status,
        "dryRun": dry_run,
        "eventId": event.get("id"),
        "eventType": event.get("event_type"),
        "destinationAlias": destination_alias,
        "transport": transport,
        "battleIds": battle_ids,
        "battle_id": structured_fields.get("battle_id"),
        "winner": structured_fields.get("winner"),
        "loser": structured_fields.get("loser"),
        "turns": structured_fields.get("turns"),
        "proof": structured_fields.get("proof"),
        "analysis": structured_fields.get("analysis"),
        "httpStatus": http_status,
        "retryAfter": retry_after,
        "errorCode": error_code,
        "blockers": blockers or [],
        "queue": _queue_summary(),
        "latestBattleResult": _latest_battle_result_summary(
            current_event=event,
            events=queue_events,
        ),
        "reportSummary": report_summary,
        "reportPaths": _proof_report_paths(),
        "secretValuesPrinted": False,
    }
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    DISCORD_DELIVERY_PROOF.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_reporting_proof(status=status, event=event, delivery_payload=payload, blockers=blockers or [])
    return payload


def write_reporting_proof(
    *,
    status: str,
    event: dict[str, Any] | None = None,
    delivery_payload: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    event_type = str(event.get("event_type") or "")
    is_battle_result = event_type == "battle_result"
    destination_alias = str(event.get("channel") or (delivery_payload or {}).get("destinationAlias") or "unknown")
    structured_fields = _event_structured_fields(event)
    latest_battle_result = (
        (delivery_payload or {}).get("latestBattleResult")
        or _latest_battle_result_summary(current_event=event)
    )
    payload = {
        "schemaVersion": "fouler-play-discord-reporting/v1",
        "generatedAtUtc": _iso_now(),
        "cycleId": (delivery_payload or {}).get("cycleId") or _cycle_id(),
        "status": status,
        "destinationAlias": destination_alias,
        "transport": _transport_summary(destination_alias),
        "queue": _queue_summary(),
        "battleIds": ((delivery_payload or {}).get("battleIds") or _extract_battle_ids_from_text(str(event.get("content") or ""))) if is_battle_result else [],
        "battle_id": ((delivery_payload or {}).get("battle_id") or structured_fields.get("battle_id")) if is_battle_result else None,
        "winner": ((delivery_payload or {}).get("winner") or structured_fields.get("winner")) if is_battle_result else None,
        "loser": ((delivery_payload or {}).get("loser") or structured_fields.get("loser")) if is_battle_result else None,
        "turns": ((delivery_payload or {}).get("turns") or structured_fields.get("turns")) if is_battle_result else None,
        "proof": (delivery_payload or {}).get("proof") or structured_fields.get("proof"),
        "analysis": (delivery_payload or {}).get("analysis") or structured_fields.get("analysis"),
        "latestBattleResult": latest_battle_result,
        "reportSummary": (delivery_payload or {}).get("reportSummary")
        or (
            redacted_report_summary(str(event.get("content") or ""))
            if event and is_battle_result
            else _generic_event_report_summary(event) if event else {}
        ),
        "blockers": blockers or [],
        "reportPaths": _proof_report_paths(),
        "secretValuesPrinted": False,
    }
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    DISCORD_REPORTING_PROOF.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _event_structured_fields(event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("event_type") or "")
    if event_type != "battle_result":
        return {
            "battle_id": None,
            "winner": None,
            "loser": None,
            "turns": None,
            "proof": None,
            "analysis": _generic_event_analysis(event),
        }
    content = str(event.get("content") or "")
    extracted = structured_report_fields(content, event_type=event_type) if content else {}
    return {
        "battle_id": event.get("battle_id") or extracted.get("battle_id"),
        "winner": event.get("winner") or extracted.get("winner"),
        "loser": event.get("loser") or extracted.get("loser"),
        "turns": event.get("turns") if event.get("turns") is not None else extracted.get("turns"),
        "proof": event.get("proof") or extracted.get("proof"),
        "analysis": event.get("analysis") or extracted.get("analysis"),
    }


def _normalized_replay_status(value: object) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[-_\s]+", "-", text)


def _is_pending_replay_status(value: object) -> bool:
    return _normalized_replay_status(value) in {"pending", "pending-public-upload"}


def _content_has_pending_replay_status(content: str) -> bool:
    normalized = re.sub(r"[-_\s]+", "-", str(content or "").lower())
    return (
        "replay-pending-public-upload" in normalized
        or "replay-status=pending-public-upload" in normalized
    )


def _replay_id_from_pending_text(content: str) -> str:
    match = PENDING_REPLAY_ID_RE.search(content or "")
    if not match:
        return ""
    return public_replay_id_candidate(match.group(1))


def _pending_battle_result_replay_id(event: dict[str, Any]) -> str:
    """Return a replay id only when the battle_result event is explicitly pending."""
    if str(event.get("event_type") or "") != "battle_result":
        return ""

    proof = event.get("proof") if isinstance(event.get("proof"), dict) else {}
    replay = proof.get("replay") if isinstance(proof.get("replay"), dict) else {}
    content = str(event.get("content") or "")

    pending = any(
        _is_pending_replay_status(value)
        for value in (
            event.get("replay_status"),
            replay.get("status"),
        )
    ) or _content_has_pending_replay_status(content)
    if not pending:
        return ""

    for value in (
        event.get("replay_id"),
        replay.get("id"),
        replay.get("url"),
        event.get("verified_replay_url"),
        event.get("replay_url"),
        event.get("raw_replay_url"),
        _replay_id_from_pending_text(content),
        event.get("battle_id"),
    ):
        replay_id = public_replay_id_candidate(value)
        if replay_id:
            return replay_id
    return ""


def _replay_json_is_live(replay_id: str) -> bool:
    """Check Showdown's public replay JSON endpoint with a fixed timeout."""
    if not replay_id:
        return False
    import urllib.error
    import urllib.request

    url = f"https://replay.pokemonshowdown.com/{replay_id}.json"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FoulerPlay/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=REPLAY_RESOLVE_TIMEOUT_SEC) as resp:
            return getattr(resp, "status", 200) == 200
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 410):
            logger.debug("Replay JSON probe returned HTTP %s for %s", exc.code, replay_id)
        return False
    except Exception as exc:
        logger.debug("Replay JSON probe failed for %s: %s", replay_id, exc)
        return False


def _resolve_public_replay_url_from_pending_id(replay_id: str) -> str:
    attempts = max(1, REPLAY_RESOLVE_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        if _replay_json_is_live(replay_id):
            return canonical_replay_url(replay_id) or f"https://replay.pokemonshowdown.com/{replay_id}"
        if attempt < attempts and REPLAY_RESOLVE_DELAY_SEC > 0:
            time.sleep(REPLAY_RESOLVE_DELAY_SEC)
    logger.info(
        "Battle result replay still pending-public-upload for %s after %d poster resolver attempt(s)",
        replay_id,
        attempts,
    )
    return ""


def _public_replay_content_line(replay_id: str, replay_url: str) -> str:
    return f"replay `{replay_id}`: {replay_url}"


def _inject_public_replay_line(content: str, replay_id: str, replay_url: str) -> str:
    public_line = f"- {_public_replay_content_line(replay_id, replay_url)}"
    remaining_idx = content.find("**Remaining:**")
    if remaining_idx >= 0:
        insert_at = content.rfind("\n", 0, remaining_idx)
        if insert_at >= 0:
            return content[:insert_at].rstrip() + f"\n{public_line}\n\n" + content[insert_at:].lstrip("\n")
    return content.rstrip() + f"\n{public_line}"


def _upgrade_replay_content(content: str, replay_id: str, replay_url: str) -> str:
    public_bit = _public_replay_content_line(replay_id, replay_url)
    pending_pattern = re.compile(
        rf"replay\s+pending\s+public\s+upload\s+`?{re.escape(replay_id)}`?",
        re.IGNORECASE,
    )
    upgraded, replacements = pending_pattern.subn(public_bit, content or "")
    upgraded = REPLAY_STATUS_PENDING_RE.sub("replay_status=public", upgraded)
    upgraded = re.sub(
        r"replay_public_verified\s*=\s*(?:false|0|no)",
        "replay_public_verified=True",
        upgraded,
        flags=re.IGNORECASE,
    )
    if replacements == 0 and replay_url not in upgraded:
        upgraded = _inject_public_replay_line(upgraded, replay_id, replay_url)
    return upgraded


def _upgrade_replay_items(items: object, replay_id: str, replay_url: str) -> list[str]:
    public_item = f"replay {replay_id}: {replay_url}"
    if not isinstance(items, list):
        return [public_item]
    upgraded: list[str] = []
    replaced = False
    for item in items:
        text = str(item)
        normalized = re.sub(r"[-_\s`]+", "-", text.lower())
        if f"replay-pending-public-upload-{replay_id.lower()}" in normalized:
            upgraded.append(public_item)
            replaced = True
        else:
            upgraded.append(text)
    if not replaced and public_item not in upgraded:
        upgraded.append(public_item)
    return upgraded


def _replace_replay_state_text(value: object, replay_id: str) -> object:
    if not isinstance(value, str):
        return value
    return value.replace(
        f"replay pending {replay_id}",
        f"public replay {replay_id}",
    ).replace(
        f"replay pending-public-upload {replay_id}",
        f"public replay {replay_id}",
    )


def prepare_battle_result_replay_for_post(event: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a pending battle_result replay to a public URL if Showdown JSON is live."""
    replay_id = _pending_battle_result_replay_id(event)
    if not replay_id:
        return event

    replay_url = _resolve_public_replay_url_from_pending_id(replay_id)
    if not replay_url:
        return event

    upgraded = copy.deepcopy(event)
    upgraded["content"] = _upgrade_replay_content(str(upgraded.get("content") or ""), replay_id, replay_url)
    upgraded["replay_id"] = replay_id
    upgraded["replay_url"] = replay_url
    upgraded["verified_replay_url"] = replay_url
    upgraded["replay_status"] = "public"
    upgraded["replay_public_verified"] = True

    proof = upgraded.get("proof") if isinstance(upgraded.get("proof"), dict) else {}
    if proof:
        proof["items"] = _upgrade_replay_items(proof.get("items"), replay_id, replay_url)
        proof["replay"] = {"status": "public", "id": replay_id, "url": replay_url}
        upgraded["proof"] = proof

    analysis = upgraded.get("analysis") if isinstance(upgraded.get("analysis"), dict) else {}
    if analysis:
        analysis["currentBattleState"] = _replace_replay_state_text(analysis.get("currentBattleState"), replay_id)
        upgraded["analysis"] = analysis
    upgraded["current_battle_state"] = _replace_replay_state_text(upgraded.get("current_battle_state"), replay_id)

    logger.info(
        "Upgraded pending battle_result replay before Discord post: event=%s replay=%s",
        upgraded.get("id"),
        replay_id,
    )
    return upgraded


def _event_timestamp(event: dict[str, Any], default: float) -> float:
    try:
        return float(event.get("timestamp") or default)
    except (TypeError, ValueError):
        return default


def resolve_pending_battle_result_replays_before_expiry(max_age_sec: int = EXPIRY_SEC) -> int:
    """Resolve pending battle-result replays before stale backlog quarantine runs."""
    now = time.time()
    upgrades: dict[str, dict[str, Any]] = {}
    for event in get_pending_events():
        if str(event.get("event_type") or "") != "battle_result":
            continue
        if now - _event_timestamp(event, now) <= max_age_sec:
            continue
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        upgraded = prepare_battle_result_replay_for_post(event)
        if upgraded == event:
            continue
        upgraded = copy.deepcopy(upgraded)
        upgraded["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        upgraded["replay_resolved_from_stale_backlog"] = True
        upgrades[event_id] = upgraded

    if not upgrades:
        return 0

    def _do_update(f):
        events = event_queue_lib._read_queue_locked(f)
        changed = 0
        for idx, event in enumerate(events):
            if not isinstance(event, dict) or event.get("status") != event_queue_lib.STATUS_PENDING:
                continue
            upgraded = upgrades.get(str(event.get("id") or ""))
            if not upgraded:
                continue
            merged = {**event, **upgraded}
            merged["status"] = event.get("status")
            merged["retry_count"] = event.get("retry_count", merged.get("retry_count", 0))
            events[idx] = merged
            changed += 1
        if changed:
            event_queue_lib._write_queue_locked(f, events)
        return changed

    changed = event_queue_lib._with_lock(_do_update)
    if changed:
        logger.info("Resolved %s pending battle_result replay(s) before stale quarantine", changed)
    return changed


def discord_config_status() -> dict[str, Any]:
    """Return the credential-free project-side handoff configuration."""

    pending = DEKU_EVENT_QUEUE_ROOT / "pending"
    return {
        "primaryTransport": {
            "type": "deku_event_queue",
            "configured": pending.is_dir(),
            "source": str(pending),
            "category": _deku_category("battles"),
            "kind": "observation",
            "authority": "none",
            "credentialMaterialIncluded": False,
        },
        "projectNetworkSenderEnabled": False,
        "projectCredentialDiscoveryEnabled": False,
        "relayOwner": "DEKU",
    }


# ── Precondition Functions ──────────────────────────────────────────

def bot_is_alive() -> bool:
    """Check if bot_main process is running via PID file + os.kill(pid, 0)."""
    try:
        if not BOT_MAIN_PID_FILE.exists():
            logger.debug("bot_is_alive: no PID file")
            return False
        data = json.loads(BOT_MAIN_PID_FILE.read_text())
        pid = data.get("pid")
        if not pid:
            return False
        os.kill(pid, 0)  # Check if process exists
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception as e:
        logger.debug(f"bot_is_alive error: {e}")
        return False


def bot_is_dead() -> bool:
    """Inverse of bot_is_alive."""
    return not bot_is_alive()


def battle_exists_in_stats() -> bool:
    """Check if battle_stats.json exists and has battles."""
    try:
        if not BATTLE_STATS_FILE.exists():
            return False
        data = json.loads(BATTLE_STATS_FILE.read_text())
        return len(data.get("battles", [])) > 0
    except Exception:
        return False


def always_true() -> bool:
    """No precondition — always post."""
    return True


# Map of precondition names to functions
PRECONDITION_MAP = {
    "bot_is_alive": bot_is_alive,
    "bot_is_dead": bot_is_dead,
    "battle_exists_in_stats": battle_exists_in_stats,
    "always_true": always_true,
    None: always_true,
}


def check_precondition(event: dict) -> bool:
    """Check if an event's precondition is met."""
    fn_name = event.get("precondition_check")
    fn = PRECONDITION_MAP.get(fn_name, always_true)
    try:
        result = fn()
        logger.debug(f"Precondition '{fn_name}' for {event['id']}: {result}")
        return result
    except Exception as e:
        logger.error(f"Precondition '{fn_name}' error: {e}")
        return False


# ── Discord Posting ─────────────────────────────────────────────────

def _stringify_for_validation(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def event_requires_gen9_validation(event: dict) -> bool:
    """Return True when an event carries Pokemon strategy/proof text."""
    event_type = str(event.get("event_type") or "").lower()
    if any(marker in event_type for marker in GEN9_VALIDATED_EVENT_TYPE_MARKERS):
        return True

    if any(event.get(field) not in (None, "", [], {}) for field in GEN9_VALIDATED_STRUCTURED_FIELDS):
        return True

    content = str(event.get("content") or "").lower()
    return any(marker in content for marker in GEN9_VALIDATED_CONTENT_MARKERS)


def _event_validation_text(event: dict) -> str:
    parts = [_stringify_for_validation(event.get("content"))]
    for field in GEN9_VALIDATED_STRUCTURED_FIELDS:
        if event.get(field) not in (None, "", [], {}):
            parts.append(_stringify_for_validation(event.get(field)))
    return "\n".join(part for part in parts if part)


def validate_event_content(event: dict) -> Tuple[bool, str]:
    """
    Validate event content for hallucinations/inaccuracies.
    Returns: (is_valid, error_reason)
    """
    if not event_requires_gen9_validation(event):
        return True, ""

    content = _event_validation_text(event)

    # Validate Gen 9 mechanics
    validator = Gen9Validator()
    is_valid, errors, warnings = validator.validate_analysis(content)
    
    if not is_valid:
        error_msg = "; ".join(errors)
        logger.error("Validation FAILED for %s: %s", event.get("id", "unknown"), error_msg)
        return False, error_msg
    
    if warnings:
        for warning in warnings:
            logger.warning("Validation warning for %s: %s", event.get("id", "unknown"), warning)

    quality_findings = report_quality_findings(event)
    if quality_findings:
        return False, "report quality failed: " + "; ".join(quality_findings[:5])

    return True, ""


def _recent_why_texts(limit: int = 8) -> list[str]:
    """Best-effort: the whyItMatters of the most recent queued battle events.

    Fails OPEN. If the queue cannot be read, the constant-copy detector simply
    sees no history and reports nothing; a reporting gate must never be able to
    block the transport because of its own I/O error.
    """
    try:
        queue_file = Path(os.getenv("EVENT_QUEUE_FILE", str(PROJECT_ROOT / "events_queue.json")))
        events = json.loads(queue_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(events, list):
        return []
    whys: list[str] = []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") != "battle_result":
            continue
        summary = redacted_report_summary(str(event.get("content") or ""))
        why = str(summary.get("whyItMatters") or "").strip()
        if why:
            whys.append(why)
        if len(whys) >= limit:
            break
    return whys


def report_quality_findings(event: dict[str, Any]) -> list[str]:
    """Return transport-blocking report quality findings for Discord-bound events."""

    event_type = str(event.get("event_type") or "")
    content = str(event.get("content") or "")
    if event_type not in {"battle_result", "performance_alert"} and "[PROOF]" not in content:
        return []

    lowered = content.lower()
    findings: list[str] = []
    for phrase in REPORT_QUALITY_BANNED_PHRASES:
        if phrase in lowered:
            findings.append(f"banned_phrase:{phrase}")

    summary = redacted_report_summary(content)
    why = str(summary.get("whyItMatters") or "")
    # An ABSENT why is fine and is now the normal case for wins: the field is
    # omitted rather than filled with prose. These checks only fire on a why
    # that is present but says nothing.
    if is_generic_why_text(why):
        findings.append("generic_why")
    elif why:
        # Structural checks. The phrase blocklist above was silenced once by a
        # rewording (it still lists "battle updates should" while the runtime had
        # moved to a different constant tail), so these test the properties we
        # actually care about rather than a list of strings someone noticed.
        what = _extract_contract_section(content, "What happened:")
        if why_adds_no_information(why, what):
            findings.append("why_restates_what_happened")
        # why_is_constant_copy is deliberately NOT blocking. The surviving why is
        # a CLASSIFICATION drawn from a closed set of three causes (forfeit /
        # inactivity-disconnect / replay-not-public), so it legitimately repeats
        # whenever the same cause recurs. Measured against the live corpus it
        # would have blocked 32 of 38 real loss reports. A repeated valid
        # classification is not a defect; it stays available for offline scanning
        # of the free-prose fields, which is where boilerplate actually hides.

    structured = structured_report_fields(content, event_type=event_type)
    readiness = structured.get("proof_readiness")
    if isinstance(readiness, dict) and readiness.get("status") != "proof-ready":
        missing = ",".join(str(item) for item in readiness.get("missingFields") or [])
        findings.append(f"proof_not_ready:{missing or readiness.get('status')}")

    result = str(structured.get("result") or "").lower()
    if result == "tie" and any(token in lowered for token in ("timeout", "timed out", "inactive", "disconnect")):
        findings.append("timeout_reported_as_tie")

    for match in RECENT_RECORD_RE.finditer(content):
        count = int(match.group("count"))
        wins = int(match.group("wins"))
        losses = int(match.group("losses"))
        wr = int(match.group("wr"))
        if wins + losses != count:
            findings.append(f"recent_record_count_mismatch:last{count}!={wins + losses}")
            continue
        expected_wr = int(round((wins / count) * 100)) if count else 0
        if wr != expected_wr:
            findings.append(f"recent_record_winrate_mismatch:{wr}!={expected_wr}")

    return findings


def write_deku_observation(event: dict) -> dict[str, Any]:
    """Validate an observation and write only to DEKU's local atomic outbox."""

    event = dict(event)
    channel = event["channel"]
    content = format_payload_or_message(str(event.get("content") or ""))
    event["content"] = content

    # Validate before posting
    is_valid, error_reason = validate_event_content(event)
    if not is_valid:
        logger.error("Blocking observation %s: %s", event["id"], error_reason)
        return {
            "ok": False,
            "status": "failed",
            "destinationAlias": channel,
            "blockers": [f"content validation failed: {error_reason}"],
            "errorCode": "validation_failed",
        }

    event_type = str(event.get("event_type") or "")
    if event_type == "battle_result":
        return _queue_deku_project_event(event, content)
    if event_type in ROUTINE_LOCAL_ONLY_EVENT_TYPES:
        return {
            "ok": True,
            "status": "retained-local",
            "transport": "local_event_queue",
            "destinationAlias": channel,
            "blockers": [],
            "outboxWritten": False,
        }
    return _queue_deku_project_event(event, content)


def post_to_discord(event: dict) -> dict[str, Any]:
    """Compatibility alias; performs no network operation."""

    return write_deku_observation(event)


class RetiredDirectTransportError(RuntimeError):
    """Raised when stale code asks Fouler to perform network delivery."""


def retired_direct_transport(*_args: object, **_kwargs: object) -> None:
    raise RetiredDirectTransportError(
        "Fouler has observation authority only; DEKU owns all network delivery"
    )


# ── Main Loop ───────────────────────────────────────────────────────

def process_one_event(dry_run: bool = False) -> bool:
    """Process the oldest pending event. Returns True if an event was processed."""
    if not dry_run:
        resolve_pending_battle_result_replays_before_expiry(EXPIRY_SEC)
        quarantined = quarantine_stale_battle_results(EXPIRY_SEC)
        if quarantined:
            logger.warning(
                "Archived and quarantined %s stale battle_result event(s); live transport withheld",
                quarantined,
            )
            write_delivery_proof(
                status="blocked",
                event=None,
                destination_alias="unknown",
                dry_run=False,
                blockers=[
                    f"archived and quarantined {quarantined} stale battle_result event(s) before transport",
                    "late live Discord posting is withheld; durable local proof was written instead",
                ],
                error_code="stale_battle_result_quarantined",
            )
            return False
        archived_failed = archive_stale_failed_events(EXPIRY_SEC)
        if archived_failed:
            logger.warning(
                "Archived %s stale terminal Discord failure event(s); live transport withheld",
                archived_failed,
            )
            write_delivery_proof(
                status="blocked",
                event=None,
                destination_alias="unknown",
                dry_run=False,
                blockers=[
                    f"archived {archived_failed} stale terminal Discord failure event(s)",
                    "live Discord transport is withheld until the next fresh queue pass",
                ],
                error_code="stale_failed_events_archived",
            )
            return False
        expired = expire_old_events(EXPIRY_SEC)
        if expired:
            logger.warning("Archived and expired %s stale Discord event(s); live transport withheld", expired)
            write_delivery_proof(
                status="blocked",
                event=None,
                destination_alias="unknown",
                dry_run=False,
                blockers=[
                    f"archived {expired} stale Discord event(s) before transport",
                    "live Discord transport is withheld until the next fresh queue pass",
                ],
                error_code="stale_backlog_archived",
            )
            return False

    pending = get_pending_events()
    if not pending:
        write_delivery_proof(
            status="idle",
            event=None,
            destination_alias="unknown",
            dry_run=dry_run,
            blockers=["no pending Discord events"],
            error_code="no_pending_events",
        )
        return False

    # Process oldest first (FIFO)
    event = pending[0]
    event_id = event["id"]
    event_type = event["event_type"]

    if dry_run:
        is_valid, error_reason = validate_event_content(event)
        validation_blockers = [] if is_valid else [f"content validation failed: {error_reason}"]
        write_delivery_proof(
            status="dry-run" if is_valid else "blocked",
            event=event,
            destination_alias=str(event.get("channel") or "unknown"),
            dry_run=True,
            blockers=validation_blockers,
            error_code=None if is_valid else "validation_failed",
        )
        logger.info("Dry-run proof written for %s id=%s", event_type, event_id)
        return True

    # Check precondition
    if not check_precondition(event):
        logger.debug(f"Precondition not met for {event_type} id={event_id}, skipping")
        write_delivery_proof(
            status="blocked",
            event=event,
            destination_alias=str(event.get("channel") or "unknown"),
            blockers=[f"precondition not met: {event.get('precondition_check') or 'always_true'}"],
            error_code="precondition_not_met",
        )
        return False

    # Commit the observation to the project-local DEKU handoff.
    event = prepare_battle_result_replay_for_post(event)
    result = write_deku_observation(event)
    if result.get("ok"):
        mark_posted(event_id)
    else:
        mark_failed(event_id, str(result.get("errorCode") or result.get("status") or "post_failed"))

    write_delivery_proof(
        status=str(result.get("status") or "failed"),
        event=event,
        destination_alias=str(result.get("destinationAlias") or event.get("channel") or "unknown"),
        blockers=[str(item) for item in result.get("blockers") or []],
        http_status=result.get("httpStatus"),
        retry_after=result.get("retryAfter"),
        error_code=result.get("errorCode"),
        transport=result.get("transport"),
    )

    return True


def process_pending_events(
    *,
    max_events: int = 50,
    required_event_id: str | None = None,
) -> dict[str, Any]:
    """Advance the local journal into DEKU's outbox with a finite bound."""

    limit = max(1, min(int(max_events), 200))
    required_id = str(required_event_id or "").strip()
    attempts: list[dict[str, Any]] = []

    for _ in range(limit):
        pending_before = get_pending_events()
        if not pending_before:
            break
        current_id = str(pending_before[0].get("id") or "")
        retry_before = int(pending_before[0].get("retry_count") or 0)
        process_one_event()

        queue_after = event_queue_lib.read_queue()
        current_after = next(
            (event for event in queue_after if str(event.get("id") or "") == current_id),
            None,
        )
        status_after = str((current_after or {}).get("status") or "missing")
        retry_after = int((current_after or {}).get("retry_count") or 0)
        pending_after = [
            event for event in queue_after if event.get("status") == event_queue_lib.STATUS_PENDING
        ]
        attempts.append(
            {
                "eventId": current_id,
                "status": status_after,
                "retryCount": retry_after,
            }
        )

        progressed = (
            status_after != event_queue_lib.STATUS_PENDING
            or retry_after > retry_before
            or len(pending_after) < len(pending_before)
        )
        if not progressed:
            break

    final_queue = event_queue_lib.read_queue()
    required = next(
        (event for event in final_queue if str(event.get("id") or "") == required_id),
        None,
    ) if required_id else None
    required_status = str((required or {}).get("status") or ("missing" if required_id else "not-requested"))
    pending_remaining = sum(
        1 for event in final_queue if event.get("status") == event_queue_lib.STATUS_PENDING
    )
    return {
        "ok": required_status == event_queue_lib.STATUS_POSTED if required_id else pending_remaining == 0,
        "processed": len(attempts),
        "attempts": attempts,
        "requiredEventId": required_id or None,
        "requiredStatus": required_status,
        "pendingRemaining": pending_remaining,
        "networkDeliveryOwnedByProject": False,
    }


def main_loop():
    """Poll the local journal and write eligible DEKU observations."""
    logger.info("DEKU observation handoff starting")
    logger.info(f"Poll interval: {POLL_INTERVAL}s, Expiry: {EXPIRY_SEC}s")

    last_cleanup = time.time()
    last_stats_log = time.time()

    while _running:
        try:
            # Process one event at a time
            processed = process_one_event()

            # Periodic cleanup
            now = time.time()
            if now - last_cleanup > CLEANUP_INTERVAL:
                removed = cleanup_queue(keep_last=200)
                if removed:
                    logger.info(f"Cleaned up {removed} old events")
                last_cleanup = now

            # Periodic stats logging (every 60s)
            if now - last_stats_log > 60:
                stats = queue_stats()
                if stats["pending"] > 0:
                    logger.info(f"Queue stats: {stats}")
                last_stats_log = now

            # Sleep between polls (shorter if we just processed something)
            if not processed:
                time.sleep(POLL_INTERVAL)
            else:
                time.sleep(0.5)  # Brief pause between consecutive posts

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(5)  # Back off on errors

    logger.info("DEKU observation handoff stopped")


def build_doctor_payload() -> dict[str, Any]:
    config = discord_config_status()
    stats = _queue_summary()
    primary_status = _deku_event_queue_status("battles")
    transport_ready = bool(primary_status.get("ready"))
    return {
        "schemaVersion": "fouler-play-discord-poster-doctor/v1",
        "checkedAt": _iso_now(),
        "cycleId": _cycle_id(),
        "ready": transport_ready and bool(stats.get("ready")),
        "transportReady": transport_ready,
        "primaryTransportStatus": primary_status,
        "config": config,
        "queue": stats,
        "reportPaths": _proof_report_paths(),
        "queueFile": _relative(Path(os.getenv("EVENT_QUEUE_FILE", str(PROJECT_ROOT / "events_queue.json")))),
        "logFile": _relative(LOG_FILE),
        "secretValuesPrinted": False,
        "note": "Read-only doctor; it checks the local durable DEKU outbox and relay status without network delivery.",
    }


def main() -> int:
    _configure_cli_logging()
    parser = argparse.ArgumentParser(description="Write Fouler observations to the local DEKU outbox")
    parser.add_argument("--doctor", action="store_true", help="print read-only local queue/outbox readiness")
    parser.add_argument("--once", action="store_true", help="process at most one event and exit")
    parser.add_argument("--dry-run", action="store_true", help="write redacted local proof for the oldest pending event")
    parser.add_argument(
        "--archive-terminal-failures",
        action="store_true",
        help="archive stale failed queue events as local proof without handoff",
    )
    parser.add_argument("--require-ready", action="store_true", help="with --doctor, exit non-zero if no transport is configured")
    args = parser.parse_args()
    if args.doctor:
        payload = build_doctor_payload()
        TRUTH_DIR.mkdir(parents=True, exist_ok=True)
        DISCORD_DOCTOR_PROOF.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        doctor_blockers: list[str] = []
        if not payload.get("transportReady"):
            doctor_blockers.append("durable DEKU event queue or relay is unavailable")
        if not (payload.get("queue") or {}).get("ready", True):
            doctor_blockers.extend(str(item) for item in ((payload.get("queue") or {}).get("health") or {}).get("blockers") or [])
        write_reporting_proof(
            status="ready" if payload["ready"] else "blocked",
            blockers=doctor_blockers,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if args.require_ready and not payload["ready"] else 0
    if args.archive_terminal_failures:
        archived_failed = archive_stale_failed_events(EXPIRY_SEC)
        status = "archived" if archived_failed else "idle"
        blockers = (
            [
                f"archived {archived_failed} stale terminal Discord failure event(s)",
                "no Discord transport was attempted",
            ]
            if archived_failed
            else ["no stale terminal Discord failure events"]
        )
        write_delivery_proof(
            status=status,
            event=None,
            destination_alias="unknown",
            dry_run=False,
            blockers=blockers,
            error_code="stale_failed_events_archived" if archived_failed else "no_stale_failed_events",
        )
        payload = {
            "schemaVersion": "fouler-play-discord-terminal-failure-archive/v1",
            "checkedAt": _iso_now(),
            "archivedFailedEvents": archived_failed,
            "queue": _queue_summary(),
            "reportPaths": _proof_report_paths(),
            "secretValuesPrinted": False,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.dry_run:
        return 0 if process_one_event(dry_run=True) else 1
    if args.once:
        return 0 if process_one_event() else 1
    main_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
