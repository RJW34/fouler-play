#!/usr/bin/env python3
"""
Event Poster Service for Fouler Play

Systemd service that polls the event queue and posts to Discord one at a time.
Checks preconditions before posting, handles retries, and expires stale events.

Run as: python3 /home/ryan/projects/fouler-play/infrastructure/event_poster.py
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.event_queue_lib import (
    get_pending_events,
    mark_posted,
    mark_failed,
    expire_old_events,
    cleanup_queue,
    queue_stats,
)
from infrastructure.gen9_validation import Gen9Validator

# Configuration
POLL_INTERVAL = float(os.getenv("EVENT_POSTER_POLL_SEC", "2"))
EXPIRY_SEC = int(os.getenv("EVENT_POSTER_EXPIRY_SEC", "600"))  # 10 min
CLEANUP_INTERVAL = 300  # Cleanup every 5 minutes
PID_DIR = PROJECT_ROOT / ".pids"
BOT_MAIN_PID_FILE = PID_DIR / "bot_main.pid"
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"

# Logging
LOG_FILE = Path(os.getenv(
    "EVENT_POSTER_LOG",
    "/home/ryan/projects/fouler-play/logs/event_poster.log"
))
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("event_poster")

# Graceful shutdown
_running = True


def _signal_handler(signum, frame):
    global _running
    logger.info(f"Received signal {signum}, shutting down...")
    _running = False


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


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

def validate_event_content(event: dict) -> Tuple[bool, str]:
    """
    Validate event content for hallucinations/inaccuracies.
    Returns: (is_valid, error_reason)
    """
    event_type = event.get("event_type", "")
    content = event.get("content", "")
    
    # Only validate analysis reports (not other event types)
    if "analysis" not in event_type.lower() and "report" not in event_type.lower():
        return True, ""
    
    # Validate Gen 9 mechanics
    validator = Gen9Validator()
    is_valid, errors, warnings = validator.validate_analysis(content)
    
    if not is_valid:
        error_msg = "; ".join(errors)
        logger.error(f"Validation FAILED for {event['id']}: {error_msg}")
        return False, error_msg
    
    if warnings:
        for warning in warnings:
            logger.warning(f"Validation warning for {event['id']}: {warning}")
    
    return True, ""


def post_to_discord(event: dict) -> bool:
    """Post event to Discord via OpenClaw CLI. Returns True on success."""
    channel = event["channel"]
    content = event["content"]
    suppress = event.get("suppress_embeds", False)

    # Validate before posting
    is_valid, error_reason = validate_event_content(event)
    if not is_valid:
        logger.error(f"Blocking post: {event['id']} - {error_reason}")
        return False

    try:
        # Build openclaw command
        cmd = [
            "openclaw", "message", "send",
            "--target", channel,
            "--channel", "discord",
            "--message", content,
        ]

        logger.info(f"Posting {event['event_type']} id={event['id']} to {channel}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info(f"Posted successfully: {event['event_type']} id={event['id']}")
            return True
        else:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error(f"Post failed (rc={result.returncode}): {error[:200]}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"Post timed out: {event['event_type']} id={event['id']}")
        return False
    except Exception as e:
        logger.error(f"Post error: {e}")
        return False


# ── Main Loop ───────────────────────────────────────────────────────

def process_one_event() -> bool:
    """Process the oldest pending event. Returns True if an event was processed."""
    pending = get_pending_events()
    if not pending:
        return False

    # Process oldest first (FIFO)
    event = pending[0]
    event_id = event["id"]
    event_type = event["event_type"]

    # Check precondition
    if not check_precondition(event):
        logger.debug(f"Precondition not met for {event_type} id={event_id}, skipping")
        return False

    # Post to Discord
    success = post_to_discord(event)

    if success:
        mark_posted(event_id)
    else:
        mark_failed(event_id, "post_failed")

    return True


def main_loop():
    """Main service loop: poll, process, expire, cleanup."""
    logger.info("Event poster service starting")
    logger.info(f"Poll interval: {POLL_INTERVAL}s, Expiry: {EXPIRY_SEC}s")

    last_cleanup = time.time()
    last_stats_log = time.time()

    while _running:
        try:
            # Process one event at a time
            processed = process_one_event()

            # Expire old events
            expired = expire_old_events(EXPIRY_SEC)
            if expired:
                logger.info(f"Expired {expired} stale events")

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

    logger.info("Event poster service stopped")


if __name__ == "__main__":
    main_loop()
