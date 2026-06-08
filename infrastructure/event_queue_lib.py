#!/usr/bin/env python3
"""
Event Queue Library for Fouler Play Discord Notifications

Thread-safe event queuing with file-based locking, deduplication,
and precondition support. All Discord messages flow through this queue.

Usage:
    from infrastructure.event_queue_lib import queue_event, read_queue, mark_posted, mark_failed
    
    queue_event("batch_complete", "battles", "📊 Batch Report...", 
                precondition_check_fn="bot_is_alive", dedup_window_sec=10)
"""

import errno
import hashlib
import json
import logging
import os
import re
from collections import Counter
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Callable

from infrastructure.discord_reporting import format_payload_or_message, public_replay_id_candidate, structured_report_fields

# Cross-platform file locking
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _positive_int_env(name: str, default: int) -> int:
    try:
        parsed = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


QUEUE_FILE = Path(os.getenv(
    "EVENT_QUEUE_FILE",
    str(PROJECT_ROOT / "events_queue.json")
))

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
TRUTH_DIR = PROJECT_ROOT / "devstream" / "truth"
BACKLOG_ARCHIVE_DIR = Path(os.getenv("EVENT_QUEUE_BACKLOG_ARCHIVE_DIR", str(LOG_DIR / "discord-events")))
BACKLOG_ARCHIVE_LATEST = TRUTH_DIR / "discord-backlog-archive.json"
BACKLOG_ARCHIVE_KEEP_LAST = _positive_int_env("EVENT_QUEUE_BACKLOG_ARCHIVE_KEEP_LAST", 200)
BACKLOG_ARCHIVE_MAX_BYTES = _positive_int_env("EVENT_QUEUE_BACKLOG_ARCHIVE_MAX_BYTES", 1_000_000)

logger = logging.getLogger("event_queue_lib")

# Event statuses
STATUS_PENDING = "pending"
STATUS_POSTED = "posted"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

# Default expiry: 10 minutes
DEFAULT_EXPIRY_SEC = 600
MAX_RETRIES = 3
BATTLE_RESULT_EVENT_TYPE = "battle_result"
QUEUE_LOCK_RETRY_ATTEMPTS = int(os.getenv("EVENT_QUEUE_LOCK_RETRY_ATTEMPTS", "8"))
QUEUE_LOCK_RETRY_DELAY_SEC = float(os.getenv("EVENT_QUEUE_LOCK_RETRY_DELAY_SEC", "0.05"))

DNS_ERROR_MARKERS = (
    "dns",
    "gaierror",
    "getaddrinfo",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
)
WEBHOOK_ERROR_MARKERS = (
    "webhook",
    "urlerror",
    "network",
    "timed out",
    "timeout",
    "connection refused",
    "connection reset",
)
PLACEHOLDER_FIELD_PATTERNS = {
    "falseTurns": re.compile(r"\bfalse\s+turns\b", re.IGNORECASE),
    "noneTurns": re.compile(r"\b(?:none|null)\s+turns\b", re.IGNORECASE),
}

# Dedup windows per event type (seconds)
DEDUP_WINDOWS = {
    "process_crash": 60,
    "bot_started": 30,
}


def _content_hash(event_type: str, channel: str, content: str) -> str:
    """MD5 hash for deduplication."""
    raw = f"{event_type}:{channel}:{content}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _battle_result_key(fields: dict) -> str:
    """Stable battle key for updating pending battle_result events in place."""
    value = fields.get("battle_id")
    if not value and isinstance(fields.get("proof"), dict):
        battle_ids = fields["proof"].get("battleIds") or []
        value = battle_ids[0] if battle_ids else None
    text = str(value or "").strip().lower()
    public_id = public_replay_id_candidate(text)
    if public_id:
        return public_id.lower()
    if text.startswith("battle-"):
        text = text.replace("battle-", "", 1)
    return text


def _read_queue_locked(f) -> list:
    """Read queue from an already-locked file handle."""
    f.seek(0)
    raw = f.read().strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Corrupt queue file, resetting")
        return []


def _write_queue_locked(f, events: list):
    """Write queue to an already-locked file handle."""
    f.seek(0)
    f.truncate()
    json.dump(events, f, indent=2)
    f.flush()


def _utc_stamp(now: float | None = None) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(time.time() if now is None else now))


def _iso_utc(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _queue_hash(events: list[dict]) -> str:
    safe_projection = [
        {
            "id": str(event.get("id") or ""),
            "status": str(event.get("status") or ""),
            "event_type": str(event.get("event_type") or ""),
            "timestamp": event.get("timestamp"),
            "retry_count": event.get("retry_count"),
        }
        for event in events
        if isinstance(event, dict)
    ]
    encoded = json.dumps(safe_projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_id_hash(event_id: object) -> str:
    return hashlib.sha256(str(event_id or "").encode("utf-8")).hexdigest()[:16]


def _battle_ids_for_archive(event: dict) -> list[str]:
    ids: list[str] = []
    direct_id = event.get("battle_id")
    if direct_id:
        ids.append(str(direct_id))
    proof = event.get("proof") if isinstance(event.get("proof"), dict) else {}
    for battle_id in proof.get("battleIds") or []:
        if battle_id:
            ids.append(str(battle_id))
    if not ids:
        extracted = structured_report_fields(str(event.get("content") or ""), event_type=str(event.get("event_type") or ""))
        extracted_id = extracted.get("battle_id")
        if extracted_id:
            ids.append(str(extracted_id))
        extracted_proof = extracted.get("proof") if isinstance(extracted.get("proof"), dict) else {}
        for battle_id in extracted_proof.get("battleIds") or []:
            if battle_id:
                ids.append(str(battle_id))
    deduped: list[str] = []
    for battle_id in ids:
        if battle_id not in deduped:
            deduped.append(battle_id[:120])
    return deduped


def _archive_event_summary(event: dict, *, now: float) -> dict[str, object]:
    content = str(event.get("content") or "")
    proof = event.get("proof") if isinstance(event.get("proof"), dict) else {}
    replay = proof.get("replay") if isinstance(proof.get("replay"), dict) else {}
    replay_status = event.get("replay_status") or replay.get("status")
    replay_id = public_replay_id_candidate(
        event.get("replay_id")
        or replay.get("id")
        or replay.get("url")
        or event.get("verified_replay_url")
        or event.get("replay_url")
        or event.get("raw_replay_url")
        or event.get("battle_id")
    )
    proof_readiness = event.get("proof_readiness") if isinstance(event.get("proof_readiness"), dict) else {}
    return {
        "eventIdHash": _event_id_hash(event.get("id")),
        "eventType": str(event.get("event_type") or "unknown"),
        "statusBeforeArchive": str(event.get("status") or "unknown"),
        "channelAlias": str(event.get("channel") or "unknown")[:80],
        "timestamp": event.get("timestamp"),
        "ageSeconds": round(max(0.0, now - _event_timestamp(event, now)), 3),
        "battleIds": _battle_ids_for_archive(event),
        "replayStatus": str(replay_status)[:80] if replay_status else None,
        "publicReplayId": replay_id or None,
        "proofReadinessStatus": str(proof_readiness.get("status") or "unknown"),
        "hasStructuredProof": isinstance(event.get("proof"), dict) and bool(event.get("proof")),
        "hasStructuredAnalysis": isinstance(event.get("analysis"), dict) and bool(event.get("analysis")),
        "hasNextHermesAction": bool(event.get("next_hermes_action") or proof_readiness.get("nextHermesAction")),
        "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None,
    }


def _prune_backlog_archives(keep_last: int | None = None, *, protected: Path | None = None) -> int:
    """Keep only the newest timestamped backlog archives."""
    if keep_last is None:
        keep_last = BACKLOG_ARCHIVE_KEEP_LAST
    keep_last = max(1, int(keep_last or 1))
    protected_path = protected.resolve() if protected is not None else None
    try:
        archives = sorted(
            (path for path in BACKLOG_ARCHIVE_DIR.glob("backlog-archive-*.json") if path.is_file()),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
    except OSError:
        return 0
    remove_count = max(0, len(archives) - keep_last)
    stale = []
    for path in archives:
        if len(stale) >= remove_count:
            break
        if protected_path is not None and path.resolve() == protected_path:
            continue
        stale.append(path)
    removed = 0
    for path in stale:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Failed to prune backlog archive %s: %s", path, exc)
    return removed


def _backlog_archive_text(payload: dict[str, object], max_bytes: int | None = None) -> str:
    """Render a bounded archive payload, truncating only per-event summaries."""
    max_bytes = max(4096, int(max_bytes if max_bytes is not None else BACKLOG_ARCHIVE_MAX_BYTES))
    event_summaries = list(payload.get("events") or [])

    def _render(summary_count: int, *, truncated: bool) -> str:
        payload["events"] = event_summaries[:summary_count]
        payload["archiveMaxBytes"] = max_bytes
        payload["archiveByteGuard"] = "per-event-summaries-truncated" if truncated else "within-limit"
        payload["archivedEventSummaryCount"] = summary_count
        payload["omittedArchivedEventSummaryCount"] = max(0, len(event_summaries) - summary_count)
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    text = _render(len(event_summaries), truncated=False)
    if len(text.encode("utf-8")) <= max_bytes:
        return text

    low = 0
    high = len(event_summaries)
    best_text = _render(0, truncated=True)
    while low <= high:
        mid = (low + high) // 2
        candidate = _render(mid, truncated=True)
        if len(candidate.encode("utf-8")) <= max_bytes:
            best_text = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best_text


def _write_backlog_archive(events: list[dict], stale_events: list[dict], *, now: float, max_age_sec: int, reason: str) -> dict[str, object]:
    event_types: Counter[str] = Counter(str(event.get("event_type") or "unknown") for event in stale_events)
    stale_ids = {id(event) for event in stale_events}
    remaining_pending = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("status") == STATUS_PENDING
        and id(event) not in stale_ids
    ]
    remaining_pending_types: Counter[str] = Counter(
        str(event.get("event_type") or "unknown") for event in remaining_pending
    )
    oldest = min((_event_timestamp(event, now) for event in stale_events), default=None)
    newest = max((_event_timestamp(event, now) for event in stale_events), default=None)
    event_summaries = [_archive_event_summary(event, now=now) for event in stale_events]
    payload: dict[str, object] = {
        "schemaVersion": "fouler-play-discord-backlog-archive/v1",
        "archivedAt": _iso_utc(now),
        "reason": reason,
        "maxAgeSeconds": max_age_sec,
        "queueFile": str(QUEUE_FILE),
        "queueHashBeforeArchive": _queue_hash(events),
        "archivedEventCount": len(stale_events),
        "archivedEventTypes": dict(sorted(event_types.items())),
        "archivedBattleResultCount": event_types.get("battle_result", 0),
        "remainingPendingEventCount": len(remaining_pending),
        "remainingPendingEventTypes": dict(sorted(remaining_pending_types.items())),
        "remainingFreshPendingEventCount": len(remaining_pending),
        "remainingFreshBattleResultCount": remaining_pending_types.get("battle_result", 0),
        "oldestArchivedTimestamp": _iso_utc(oldest) if oldest is not None else None,
        "newestArchivedTimestamp": _iso_utc(newest) if newest is not None else None,
        "events": event_summaries,
        "archivalDisposition": "stale-pending-events-expired-locally-not-sent",
        "liveDiscordMessagesSent": False,
        "prunedArchiveCount": 0,
        "secretValuesPrinted": False,
        "nextHermesAction": "Treat this as local stale-proof archive only; transport only fresh events after the queue is clean.",
    }
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    BACKLOG_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = BACKLOG_ARCHIVE_DIR / f"backlog-archive-{_utc_stamp(now)}.json"
    payload["archivePath"] = str(archive_path)
    payload["latestPath"] = str(BACKLOG_ARCHIVE_LATEST)
    archive_text = _backlog_archive_text(payload)
    archive_path.write_text(archive_text, encoding="utf-8")
    BACKLOG_ARCHIVE_LATEST.write_text(archive_text, encoding="utf-8")
    payload["prunedArchiveCount"] = _prune_backlog_archives(protected=archive_path)
    if payload["prunedArchiveCount"]:
        payload["events"] = event_summaries
        archive_text = _backlog_archive_text(payload)
        archive_path.write_text(archive_text, encoding="utf-8")
        BACKLOG_ARCHIVE_LATEST.write_text(archive_text, encoding="utf-8")
    return payload


def _is_transient_lock_error(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "errno", None) in {errno.EACCES, errno.EAGAIN}


def _with_lock(fn):
    """Execute fn(file_handle) with exclusive lock on queue file (cross-platform)."""
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    attempts = max(1, QUEUE_LOCK_RETRY_ATTEMPTS)
    for attempt in range(attempts):
        try:
            QUEUE_FILE.touch(exist_ok=True)
            with open(QUEUE_FILE, "r+") as f:
                if sys.platform == "win32":
                    # Windows: lock first byte
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        return fn(f)
                    finally:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        return fn(f)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
        except OSError as exc:
            if not _is_transient_lock_error(exc) or attempt == attempts - 1:
                raise
            time.sleep(QUEUE_LOCK_RETRY_DELAY_SEC * (attempt + 1))
    raise RuntimeError("unreachable queue lock retry state")


def queue_event(
    event_type: str,
    channel: str,
    content: str,
    precondition_check_fn: Optional[str] = None,
    dedup_window_sec: Optional[int] = None,
    suppress_embeds: bool = False,
) -> Optional[str]:
    """
    Queue a Discord event for posting.
    
    Args:
        event_type: Type identifier (batch_complete, process_crash, etc.)
        channel: Discord channel target - channel ID or webhook alias ("battles", "project", "feedback")
        content: Message content
        precondition_check_fn: Name of precondition function the poster must check before posting
        dedup_window_sec: Seconds within which duplicate content is rejected (None = use default per type)
        suppress_embeds: If True, poster will set Discord suppress_embeds flag
    
    Returns:
        Event ID if queued, None if deduplicated
    """
    if dedup_window_sec is None:
        dedup_window_sec = DEDUP_WINDOWS.get(event_type, 10)

    structured_fields = structured_report_fields(content, event_type=event_type)
    content = format_payload_or_message(content)
    if not structured_fields.get("analysis"):
        structured_fields = structured_report_fields(content, event_type=event_type)
    content_md5 = _content_hash(event_type, channel, content)
    now = time.time()

    def _do_queue(f):
        events = _read_queue_locked(f)

        # Dedup check: reject if same hash exists within window
        for ev in events:
            if (ev.get("content_hash") == content_md5
                    and ev["status"] in (STATUS_PENDING, STATUS_POSTED)
                    and (now - ev["timestamp"]) < dedup_window_sec):
                logger.info(f"Dedup rejected: {event_type} (hash={content_md5[:8]})")
                return None

        battle_key = _battle_result_key(structured_fields) if event_type == "battle_result" else ""
        if battle_key:
            for ev in events:
                if (
                    isinstance(ev, dict)
                    and ev.get("event_type") == "battle_result"
                    and ev.get("status") == STATUS_PENDING
                    and _battle_result_key(ev) == battle_key
                ):
                    ev.update(
                        {
                            "channel": channel,
                            "content": content,
                            "content_hash": content_md5,
                            "precondition_check": precondition_check_fn,
                            "suppress_embeds": suppress_embeds,
                            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "update_count": int(ev.get("update_count") or 0) + 1,
                            **structured_fields,
                        }
                    )
                    _write_queue_locked(f, events)
                    logger.info(
                        "Updated pending battle_result id=%s battle=%s with newer replay/proof fields",
                        ev.get("id"),
                        battle_key,
                    )
                    return ev.get("id")

        event_id = str(uuid.uuid4())[:12]
        event = {
            "id": event_id,
            "timestamp": now,
            "event_type": event_type,
            "channel": channel,
            "content": content,
            "content_hash": content_md5,
            "precondition_check": precondition_check_fn,
            "suppress_embeds": suppress_embeds,
            "status": STATUS_PENDING,
            "retry_count": 0,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "posted_at": None,
            "last_error": None,
            **structured_fields,
        }

        events.append(event)
        _write_queue_locked(f, events)
        logger.info(f"Queued: {event_type} id={event_id} channel={channel}")
        return event_id

    return _with_lock(_do_queue)


def read_queue(status_filter: Optional[str] = None) -> list:
    """Read current queue state. Optionally filter by status."""
    def _do_read(f):
        events = _read_queue_locked(f)
        if status_filter:
            return [e for e in events if e["status"] == status_filter]
        return events

    return _with_lock(_do_read)


def get_pending_events() -> list:
    """Get all pending events, ordered by timestamp (oldest first)."""
    return read_queue(status_filter=STATUS_PENDING)


def mark_posted(event_id: str) -> bool:
    """Mark an event as successfully posted."""
    def _do_mark(f):
        events = _read_queue_locked(f)
        for ev in events:
            if ev["id"] == event_id:
                ev["status"] = STATUS_POSTED
                ev["posted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _write_queue_locked(f, events)
                logger.info(f"Posted: {ev['event_type']} id={event_id}")
                return True
        return False

    return _with_lock(_do_mark)


def mark_failed(event_id: str, error: str = "") -> bool:
    """Increment retry count. If max retries reached, mark as failed."""
    def _do_fail(f):
        events = _read_queue_locked(f)
        for ev in events:
            if ev["id"] == event_id:
                ev["retry_count"] += 1
                ev["last_error"] = error[:500]
                if ev["retry_count"] >= MAX_RETRIES:
                    ev["status"] = STATUS_FAILED
                    logger.warning(f"Failed permanently: {ev['event_type']} id={event_id} after {MAX_RETRIES} retries")
                else:
                    logger.warning(f"Retry {ev['retry_count']}/{MAX_RETRIES}: {ev['event_type']} id={event_id}: {error[:100]}")
                _write_queue_locked(f, events)
                return True
        return False

    return _with_lock(_do_fail)


def expire_old_events(max_age_sec: int = DEFAULT_EXPIRY_SEC) -> int:
    """Archive then expire pending non-battle events older than max_age_sec. Returns count expired."""
    now = time.time()

    def _do_expire(f):
        events = _read_queue_locked(f)
        stale_events = [
            ev
            for ev in events
            if isinstance(ev, dict)
            and ev.get("status") == STATUS_PENDING
            and ev.get("event_type") != BATTLE_RESULT_EVENT_TYPE
            and (now - _event_timestamp(ev, now)) > max_age_sec
        ]
        if not stale_events:
            return 0
        _write_backlog_archive(
            events,
            stale_events,
            now=now,
            max_age_sec=max_age_sec,
            reason="pending-discord-event-expired-before-transport",
        )
        stale_event_ids = {id(ev) for ev in stale_events}
        for ev in stale_events:
            if ev["status"] == STATUS_PENDING:
                ev["status"] = STATUS_EXPIRED
                ev["expired_at"] = _iso_utc(now)
                ev["expired_reason"] = "pending-discord-event-expired-before-transport"
                logger.info(
                    "Archived and expired stale Discord event: %s id_hash=%s (age=%.0fs)",
                    ev.get("event_type"),
                    _event_id_hash(ev.get("id")),
                    now - _event_timestamp(ev, now),
                )
        compacted_events = [
            ev
            for ev in events
            if not (
                isinstance(ev, dict)
                and id(ev) in stale_event_ids
                and ev.get("status") == STATUS_EXPIRED
            )
        ]
        _write_queue_locked(f, compacted_events)
        return len(stale_events)

    return _with_lock(_do_expire)


def quarantine_stale_battle_results(max_age_sec: int = DEFAULT_EXPIRY_SEC) -> int:
    """Archive and remove stale pending battle_result events from the live transport queue."""
    now = time.time()
    reason = "stale-battle-result-quarantined-before-live-transport"

    def _do_quarantine(f):
        events = _read_queue_locked(f)
        stale_events = [
            ev
            for ev in events
            if isinstance(ev, dict)
            and ev.get("status") == STATUS_PENDING
            and ev.get("event_type") == BATTLE_RESULT_EVENT_TYPE
            and (now - _event_timestamp(ev, now)) > max_age_sec
        ]
        if not stale_events:
            return 0
        _write_backlog_archive(
            events,
            stale_events,
            now=now,
            max_age_sec=max_age_sec,
            reason=reason,
        )
        stale_event_ids = {id(ev) for ev in stale_events}
        for ev in stale_events:
            if ev["status"] == STATUS_PENDING:
                ev["status"] = STATUS_EXPIRED
                ev["expired_at"] = _iso_utc(now)
                ev["expired_reason"] = reason
                ev["archival_disposition"] = "local-proof-quarantine-not-sent"
                logger.info(
                    "Archived and quarantined stale battle_result event: id_hash=%s (age=%.0fs)",
                    _event_id_hash(ev.get("id")),
                    now - _event_timestamp(ev, now),
                )
        compacted_events = [
            ev
            for ev in events
            if not (
                isinstance(ev, dict)
                and id(ev) in stale_event_ids
                and ev.get("status") == STATUS_EXPIRED
            )
        ]
        _write_queue_locked(f, compacted_events)
        return len(stale_events)

    return _with_lock(_do_quarantine)


def cleanup_queue(keep_last: int = 200) -> int:
    """Remove old posted/failed/expired events, keeping last N."""
    def _do_cleanup(f):
        events = _read_queue_locked(f)
        # Keep all pending, plus last N of completed
        pending = [e for e in events if e["status"] == STATUS_PENDING]
        completed = [e for e in events if e["status"] != STATUS_PENDING]
        trimmed = completed[-keep_last:] if len(completed) > keep_last else completed
        removed = len(completed) - len(trimmed)
        if removed > 0:
            _write_queue_locked(f, pending + trimmed)
            logger.info(f"Cleanup: removed {removed} old events")
        return removed

    return _with_lock(_do_cleanup)


def queue_stats() -> dict:
    """Get queue statistics."""
    events = read_queue()
    stats = {
        "total": len(events),
        "pending": sum(1 for e in events if e["status"] == STATUS_PENDING),
        "posted": sum(1 for e in events if e["status"] == STATUS_POSTED),
        "failed": sum(1 for e in events if e["status"] == STATUS_FAILED),
        "expired": sum(1 for e in events if e["status"] == STATUS_EXPIRED),
    }
    return stats


def classify_delivery_error(error: object) -> str:
    """Normalize queue delivery errors into reportable health classes."""
    text = str(error or "").strip().lower()
    if not text:
        return "unknown"
    if any(marker in text for marker in DNS_ERROR_MARKERS):
        return "dns_failure"
    if "rate_limited" in text or "rate-limited" in text or "429" in text:
        return "rate_limited"
    if "http" in text:
        return "webhook_http_error"
    if any(marker in text for marker in WEBHOOK_ERROR_MARKERS):
        return "webhook_network_error"
    if "openclaw" in text:
        return "openclaw_error"
    if "validation" in text:
        return "validation_failed"
    if "precondition" in text:
        return "precondition_not_met"
    return "unknown"


def _event_timestamp(event: dict, default: float) -> float:
    try:
        return float(event.get("timestamp") or default)
    except (TypeError, ValueError):
        return default


def _pending_age_buckets(pending: list[dict], now: float) -> dict[str, int]:
    buckets = {
        "lt5m": 0,
        "m5to60": 0,
        "h1to24": 0,
        "d1to3": 0,
        "gt3d": 0,
    }
    for event in pending:
        age = max(0.0, now - _event_timestamp(event, now))
        if age < 300:
            buckets["lt5m"] += 1
        elif age < 3600:
            buckets["m5to60"] += 1
        elif age < 86400:
            buckets["h1to24"] += 1
        elif age < 259200:
            buckets["d1to3"] += 1
        else:
            buckets["gt3d"] += 1
    return buckets


def _pending_freshness_counts(pending: list[dict], now: float, stale_after_sec: int = DEFAULT_EXPIRY_SEC) -> dict[str, int]:
    stale = [
        event
        for event in pending
        if now - _event_timestamp(event, now) > stale_after_sec
    ]
    fresh = [
        event
        for event in pending
        if now - _event_timestamp(event, now) <= stale_after_sec
    ]
    return {
        "stalePendingBacklog": len(stale),
        "stalePendingBattleResults": sum(1 for event in stale if str(event.get("event_type") or "") == "battle_result"),
        "freshPendingBacklog": len(fresh),
        "freshPendingBattleResults": sum(1 for event in fresh if str(event.get("event_type") or "") == "battle_result"),
        "staleAfterSeconds": stale_after_sec,
    }


def _placeholder_field_counts(events: list[dict]) -> dict[str, int]:
    counts = Counter()
    for event in events:
        content = str(event.get("content") or "")
        for label, pattern in PLACEHOLDER_FIELD_PATTERNS.items():
            if pattern.search(content):
                counts[label] += 1
    return dict(sorted(counts.items()))


def _structured_field_counts(events: list[dict]) -> dict[str, int]:
    counts = Counter()
    for event in events:
        if str(event.get("event_type") or "") != "battle_result":
            continue
        fields = {
            "battle_id": event.get("battle_id"),
            "winner": event.get("winner"),
            "loser": event.get("loser"),
            "turns": event.get("turns"),
            "proof": event.get("proof"),
            "analysis": event.get("analysis"),
            "current_battle_state": event.get("current_battle_state"),
            "why_it_matters": event.get("why_it_matters"),
            "next_hermes_action": event.get("next_hermes_action"),
            "proof_readiness": event.get("proof_readiness"),
        }
        if any(value in (None, "", [], {}) for value in fields.values()):
            extracted = structured_report_fields(str(event.get("content") or ""), event_type="battle_result")
            for key, value in extracted.items():
                fields[key] = fields.get(key) or value
        for key, value in fields.items():
            if value not in (None, "", [], {}):
                counts[key] += 1
    return dict(sorted(counts.items()))


def _backlog_classification(
    *,
    available: bool,
    status: str,
    pending_count: int,
    pending_battle_results: int,
    delivery_failures: int,
    dns_failures: int,
    webhook_failures: int,
    oldest_pending_age: float | None,
) -> dict[str, object]:
    if not available:
        return {
            "status": "unavailable",
            "severity": "hard-blocker",
            "whyItMatters": "HERMES cannot prove Discord reporting while the event queue is unreadable or unavailable.",
            "nextHermesAction": "restore queue readability before treating fouler-play reporting as proof-ready",
            "blocking": True,
        }
    if dns_failures:
        return {
            "status": "dns-failed",
            "severity": "stream-safety-blocker",
            "whyItMatters": "Discord proof cannot leave the machine because DNS resolution failed.",
            "nextHermesAction": "repair DNS/network resolution, then rerun the Discord poster doctor or dry-run proof",
            "blocking": True,
        }
    if webhook_failures or delivery_failures:
        return {
            "status": "delivery-failed",
            "severity": "stream-safety-blocker",
            "whyItMatters": "Discord proof exists locally but failed during transport.",
            "nextHermesAction": "classify failed queue events, repair webhook/OpenClaw transport, then retry or archive failures",
            "blocking": True,
        }
    if pending_count:
        age = int(oldest_pending_age or 0)
        severity = "reliability-blocker" if age >= 900 or pending_battle_results else "quality-gap"
        if age >= DEFAULT_EXPIRY_SEC:
            next_action = "archive stale Discord backlog locally before any live transport; transport only fresh/new events"
        else:
            next_action = "drain the Discord event queue in dry-run or approved transport mode and refresh proof-status.json"
        return {
            "status": "backlogged",
            "severity": severity,
            "whyItMatters": (
                f"{pending_count} Discord event(s) are still pending, including "
                f"{pending_battle_results} battle_result event(s)."
            ),
            "nextHermesAction": next_action,
            "blocking": True,
        }
    return {
        "status": status,
        "severity": "clear",
        "whyItMatters": "Discord reporting has no pending or failed queue events blocking proof handoff.",
        "nextHermesAction": "keep monitoring the queue and attach the next completed battle proof to the devstream cycle",
        "blocking": False,
    }


def _queue_proof_readiness(
    *,
    available: bool,
    pending_battle_results: int,
    structured_counts: dict[str, int],
    pending_count: int,
    delivery_failures: int,
    classification: dict[str, object],
) -> dict[str, object]:
    required = ["battle_id", "proof", "analysis", "next_hermes_action", "proof_readiness"]
    missing_counts = {
        field: max(0, pending_battle_results - int(structured_counts.get(field) or 0))
        for field in required
        if pending_battle_results and int(structured_counts.get(field) or 0) < pending_battle_results
    }
    machine_actionable = (
        min([pending_battle_results] + [int(structured_counts.get(field) or 0) for field in required])
        if pending_battle_results
        else 0
    )
    local_proof_classified = (
        bool(pending_battle_results)
        and machine_actionable == pending_battle_results
        and not missing_counts
        and available
    )
    local_proof_status = (
        "classified-redacted-local-proof" if local_proof_classified else "needs-classification"
    )
    status = "ready"
    if not available:
        status = "queue-unavailable"
    elif delivery_failures:
        status = "delivery-failed"
    elif pending_count:
        status = "queue-backlogged"
    elif missing_counts:
        status = "needs-structured-fields"
    return {
        "status": status,
        "readyForProofHandoff": status == "ready",
        "localProofStatus": local_proof_status,
        "localProofClassified": local_proof_classified,
        "readyForLocalProofHandoff": local_proof_classified and delivery_failures == 0,
        "pendingBattleResults": pending_battle_results,
        "machineActionablePendingBattleResults": machine_actionable,
        "missingStructuredFieldCounts": missing_counts,
        "nextHermesAction": classification.get("nextHermesAction"),
        "blockers": [] if status == "ready" or local_proof_classified else [str(classification.get("whyItMatters") or status)],
    }


def queue_health_summary(events: list | None = None, *, now: float | None = None, available: bool = True) -> dict:
    """Return explicit Discord queue health fields for proof/readiness reports."""
    events = events if events is not None else read_queue()
    now = time.time() if now is None else now
    typed_events = [event for event in events if isinstance(event, dict)]
    pending = [event for event in typed_events if event.get("status") == STATUS_PENDING]
    failed = [event for event in typed_events if event.get("status") == STATUS_FAILED]
    expired = [event for event in typed_events if event.get("status") == STATUS_EXPIRED]

    def retry_count(event: dict) -> int:
        try:
            return int(event.get("retry_count") or 0)
        except (TypeError, ValueError):
            return 0

    retrying = [
        event
        for event in pending
        if retry_count(event) > 0 or event.get("last_error")
    ]

    event_types: Counter[str] = Counter(str(event.get("event_type") or "unknown") for event in pending)
    failed_event_types: Counter[str] = Counter(str(event.get("event_type") or "unknown") for event in failed)
    expired_event_types: Counter[str] = Counter(str(event.get("event_type") or "unknown") for event in expired)
    status_counts: Counter[str] = Counter(str(event.get("status") or "unknown") for event in typed_events)
    failure_types: Counter[str] = Counter(
        classify_delivery_error(
            event.get("last_error")
            or event.get("errorCode")
            or event.get("error_code")
            or event.get("error")
        )
        for event in failed + retrying
    )

    oldest_pending = min(pending, key=lambda event: _event_timestamp(event, now), default=None)
    oldest_timestamp = _event_timestamp(oldest_pending, now) if oldest_pending else None
    delivery_failures = len(failed)
    dns_failures = failure_types.get("dns_failure", 0)
    webhook_failures = sum(
        count
        for error_type, count in failure_types.items()
        if error_type in {"webhook_http_error", "webhook_network_error", "rate_limited"}
    )

    blockers: list[str] = []
    if pending:
        blockers.append(f"pending Discord delivery backlog: {len(pending)} event(s)")
    if delivery_failures:
        blockers.append(f"Discord delivery failures: {delivery_failures} event(s)")
    if dns_failures:
        blockers.append(f"Discord DNS failures: {dns_failures} event(s)")
    if webhook_failures:
        blockers.append(f"Discord webhook failures: {webhook_failures} event(s)")

    if not available:
        status = "unavailable"
    elif dns_failures:
        status = "dns-failed"
    elif webhook_failures or delivery_failures:
        status = "delivery-failed"
    elif pending:
        status = "backlogged"
    else:
        status = "ready"

    pending_battle_results = event_types.get("battle_result", 0)
    structured_counts = _structured_field_counts(pending)
    oldest_pending_age = round(now - oldest_timestamp, 3) if oldest_timestamp is not None else None
    freshness_counts = _pending_freshness_counts(pending, now)
    classification = _backlog_classification(
        available=available,
        status=status,
        pending_count=len(pending),
        pending_battle_results=pending_battle_results,
        delivery_failures=delivery_failures,
        dns_failures=dns_failures,
        webhook_failures=webhook_failures,
        oldest_pending_age=oldest_pending_age,
    )
    proof_readiness = _queue_proof_readiness(
        available=available,
        pending_battle_results=pending_battle_results,
        structured_counts=structured_counts,
        pending_count=len(pending),
        delivery_failures=delivery_failures,
        classification=classification,
    )

    return {
        "available": available,
        "ready": available and not blockers,
        "status": status,
        "pendingBacklog": len(pending),
        "pendingBattleResults": pending_battle_results,
        **freshness_counts,
        "pendingEventTypes": dict(sorted(event_types.items())),
        "pendingAgeBuckets": _pending_age_buckets(pending, now),
        "pendingPlaceholderFieldCounts": _placeholder_field_counts(pending),
        "pendingBattleResultStructuredFields": structured_counts,
        "failedEventTypes": dict(sorted(failed_event_types.items())),
        "expiredEventTypes": dict(sorted(expired_event_types.items())),
        "statusCounts": dict(sorted(status_counts.items())),
        "oldestPendingAgeSeconds": oldest_pending_age,
        "oldestPendingEventId": oldest_pending.get("id") if oldest_pending else None,
        "deliveryFailures": delivery_failures,
        "retryingDeliveries": len(retrying),
        "expiredDeliveries": len(expired),
        "dnsFailures": dns_failures,
        "webhookFailures": webhook_failures,
        "failureTypes": dict(sorted(failure_types.items())),
        "backlogClassification": classification,
        "proofReadiness": proof_readiness,
        "nextHermesAction": classification.get("nextHermesAction"),
        "blockers": blockers,
    }
