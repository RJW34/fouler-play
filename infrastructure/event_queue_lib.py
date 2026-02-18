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

import fcntl
import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Callable

QUEUE_FILE = Path(os.getenv(
    "EVENT_QUEUE_FILE",
    "/home/ryan/projects/fouler-play/events_queue.json"
))

LOG_DIR = Path("/home/ryan/projects/fouler-play/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("event_queue_lib")

# Event statuses
STATUS_PENDING = "pending"
STATUS_POSTED = "posted"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"

# Default expiry: 10 minutes
DEFAULT_EXPIRY_SEC = 600
MAX_RETRIES = 3

# Dedup windows per event type (seconds)
DEDUP_WINDOWS = {
    "process_crash": 60,
    "bot_started": 30,
}


def _content_hash(event_type: str, channel: str, content: str) -> str:
    """MD5 hash for deduplication."""
    raw = f"{event_type}:{channel}:{content}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


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


def _with_lock(fn):
    """Execute fn(file_handle) with exclusive flock on queue file."""
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.touch(exist_ok=True)
    with open(QUEUE_FILE, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            return fn(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


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
    """Expire pending events older than max_age_sec. Returns count expired."""
    now = time.time()

    def _do_expire(f):
        events = _read_queue_locked(f)
        count = 0
        for ev in events:
            if ev["status"] == STATUS_PENDING and (now - ev["timestamp"]) > max_age_sec:
                ev["status"] = STATUS_EXPIRED
                count += 1
                logger.info(f"Expired: {ev['event_type']} id={ev['id']} (age={now - ev['timestamp']:.0f}s)")
        if count:
            _write_queue_locked(f, events)
        return count

    return _with_lock(_do_expire)


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
