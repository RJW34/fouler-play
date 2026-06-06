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
import re
import signal
import shutil
import socket
import subprocess
import sys
import time
import argparse
from pathlib import Path
from typing import Any, Tuple

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
    queue_health_summary,
)
from infrastructure.gen9_validation import Gen9Validator
from infrastructure.discord_reporting import redacted_report_summary, structured_report_fields

# Configuration
POLL_INTERVAL = float(os.getenv("EVENT_POSTER_POLL_SEC", "2"))
EXPIRY_SEC = int(os.getenv("EVENT_POSTER_EXPIRY_SEC", "600"))  # 10 min
CLEANUP_INTERVAL = 300  # Cleanup every 5 minutes
PID_DIR = PROJECT_ROOT / ".pids"
BOT_MAIN_PID_FILE = PID_DIR / "bot_main.pid"
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
TRUTH_DIR = PROJECT_ROOT / "devstream" / "truth"
DISCORD_REPORTING_PROOF = TRUTH_DIR / "discord-reporting.json"
DISCORD_DELIVERY_PROOF = TRUTH_DIR / "discord-delivery.json"
DISCORD_DOCTOR_PROOF = TRUTH_DIR / "discord-reporting-doctor.json"
BATTLE_ID_RE = re.compile(r"\b(?:battle-)?gen9ou-[A-Za-z0-9-]+\b|battle `([^`]+)`")
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

# Logging
LOG_FILE = Path(os.getenv(
    "EVENT_POSTER_LOG",
    str(PROJECT_ROOT / "logs" / "event_poster.log")
))
ENV_FILES = (
    PROJECT_ROOT / ".env",
    Path.home() / "hermes" / ".env",
    Path("/home/ryan/projects/polymarket-copytrade/.env"),
)
WEBHOOK_ENV_BY_CHANNEL = {
    "battles": ("DISCORD_BATTLES_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"),
    "feedback": ("DISCORD_FEEDBACK_WEBHOOK_URL", "DISCORD_WEBHOOK_URL"),
    "project": ("DISCORD_WEBHOOK_URL", "DISCORD_BATTLES_WEBHOOK_URL"),
    "workspace": ("DISCORD_WEBHOOK_URL", "DISCORD_BATTLES_WEBHOOK_URL"),
}
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


def load_env_chain() -> list[str]:
    """Load project/HERMES env files without overwriting real service env."""
    loaded: list[str] = []
    for env_file in ENV_FILES:
        try:
            if not env_file.exists():
                continue
            loaded.append(str(env_file))
            for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except Exception as exc:
            logger.debug("Failed to load env file %s: %s", env_file, exc)
    return loaded


def _redact_url(value: str) -> str:
    if not value:
        return ""
    if "discord" in value and "/api/webhooks/" in value:
        prefix = value.split("/api/webhooks/", 1)[0]
        return f"{prefix}/api/webhooks/REDACTED"
    return "<configured>"


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


def _proof_report_paths() -> dict[str, str]:
    return {
        "discordReporting": _relative(DISCORD_REPORTING_PROOF),
        "discordDelivery": _relative(DISCORD_DELIVERY_PROOF),
        "discordDoctor": _relative(DISCORD_DOCTOR_PROOF),
        "discordBacklogArchive": _relative(TRUTH_DIR / "discord-backlog-archive.json"),
        "eventPosterLog": _relative(LOG_FILE),
        "queueFile": _relative(Path(os.getenv("EVENT_QUEUE_FILE", str(PROJECT_ROOT / "events_queue.json")))),
    }


def _transport_summary(destination_alias: str) -> dict[str, Any]:
    load_env_chain()
    webhook_url, webhook_source = resolve_webhook_url(destination_alias)
    return {
        "type": "webhook" if webhook_url else "openclaw" if shutil.which("openclaw") else "unconfigured",
        "configured": bool(webhook_url or shutil.which("openclaw")),
        "source": webhook_source if webhook_url else None,
        "redactedUrl": _redact_url(webhook_url) if webhook_url else "",
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
) -> dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    destination_alias = destination_alias or str(event.get("channel") or "unknown")
    battle_ids = _extract_battle_ids_from_text(str(event.get("content") or ""))
    report_summary = redacted_report_summary(str(event.get("content") or "")) if event else {}
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
    destination_alias = str(event.get("channel") or (delivery_payload or {}).get("destinationAlias") or "unknown")
    structured_fields = _event_structured_fields(event)
    payload = {
        "schemaVersion": "fouler-play-discord-reporting/v1",
        "generatedAtUtc": _iso_now(),
        "cycleId": (delivery_payload or {}).get("cycleId") or _cycle_id(),
        "status": status,
        "destinationAlias": destination_alias,
        "transport": _transport_summary(destination_alias),
        "queue": _queue_summary(),
        "battleIds": (delivery_payload or {}).get("battleIds") or _extract_battle_ids_from_text(str(event.get("content") or "")),
        "battle_id": (delivery_payload or {}).get("battle_id") or structured_fields.get("battle_id"),
        "winner": (delivery_payload or {}).get("winner") or structured_fields.get("winner"),
        "loser": (delivery_payload or {}).get("loser") or structured_fields.get("loser"),
        "turns": (delivery_payload or {}).get("turns") or structured_fields.get("turns"),
        "proof": (delivery_payload or {}).get("proof") or structured_fields.get("proof"),
        "analysis": (delivery_payload or {}).get("analysis") or structured_fields.get("analysis"),
        "reportSummary": (delivery_payload or {}).get("reportSummary")
        or (redacted_report_summary(str(event.get("content") or "")) if event else {}),
        "blockers": blockers or [],
        "reportPaths": _proof_report_paths(),
        "secretValuesPrinted": False,
    }
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    DISCORD_REPORTING_PROOF.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _event_structured_fields(event: dict[str, Any]) -> dict[str, Any]:
    content = str(event.get("content") or "")
    extracted = structured_report_fields(content, event_type=str(event.get("event_type") or "")) if content else {}
    return {
        "battle_id": event.get("battle_id") or extracted.get("battle_id"),
        "winner": event.get("winner") or extracted.get("winner"),
        "loser": event.get("loser") or extracted.get("loser"),
        "turns": event.get("turns") if event.get("turns") is not None else extracted.get("turns"),
        "proof": event.get("proof") or extracted.get("proof"),
        "analysis": event.get("analysis") or extracted.get("analysis"),
    }


def resolve_webhook_url(channel: str) -> tuple[str, str]:
    """Resolve a channel alias to a webhook URL and the env var that supplied it."""
    keys = WEBHOOK_ENV_BY_CHANNEL.get(channel, ("DISCORD_WEBHOOK_URL",))
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value, key
    return os.getenv("DISCORD_WEBHOOK_URL", "").strip(), "DISCORD_WEBHOOK_URL"


def discord_config_status() -> dict[str, Any]:
    loaded = load_env_chain()
    aliases = {}
    for alias in ("battles", "feedback", "project", "workspace"):
        url, key = resolve_webhook_url(alias)
        aliases[alias] = {"configured": bool(url), "source": key if url else None, "redactedUrl": _redact_url(url)}
    return {
        "loadedEnvFiles": loaded,
        "aliases": aliases,
        "anyWebhookConfigured": any(item["configured"] for item in aliases.values()),
        "openclawAvailable": shutil.which("openclaw") is not None,
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
    
    return True, ""


def post_to_discord(event: dict) -> dict[str, Any]:
    """Post event to Discord via webhook (or OpenClaw CLI fallback)."""
    channel = event["channel"]
    content = event["content"]
    suppress = event.get("suppress_embeds", False)

    # Validate before posting
    is_valid, error_reason = validate_event_content(event)
    if not is_valid:
        logger.error(f"Blocking post: {event['id']} - {error_reason}")
        return {
            "ok": False,
            "status": "failed",
            "destinationAlias": channel,
            "blockers": [f"content validation failed: {error_reason}"],
            "errorCode": "validation_failed",
        }

    load_env_chain()
    webhook_url, webhook_source = resolve_webhook_url(channel)

    if webhook_url:
        logger.debug("Resolved Discord channel %s via %s", channel, webhook_source)
        return _post_via_webhook(event, webhook_url, content, suppress)
    else:
        return _post_via_cli(event, channel, content)


def _is_dns_exception(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return True
    text = str(reason or exc).lower()
    return any(
        marker in text
        for marker in (
            "gaierror",
            "getaddrinfo",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
        )
    )


def _post_via_webhook(event, webhook_url, content, suppress=False):
    """Post to Discord via webhook URL."""
    import urllib.request
    import urllib.error

    try:
        payload = {"content": content[:2000]}
        if suppress:
            payload["flags"] = 4

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "DiscordBot (https://github.com/fouler-play, 1.0)"},
            method="POST",
        )

        logger.info(f"Posting {event['event_type']} id={event['id']} via webhook")
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.status
            if status in (200, 204):
                logger.info(f"Posted successfully: {event['event_type']} id={event['id']} (HTTP {status})")
                return {
                    "ok": True,
                    "status": "posted",
                    "destinationAlias": event.get("channel"),
                    "httpStatus": status,
                    "blockers": [],
                }
            else:
                body = resp.read().decode("utf-8", errors="replace")[:200]
                logger.error(f"Webhook returned HTTP {status}: {body}")
                return {
                    "ok": False,
                    "status": "failed",
                    "destinationAlias": event.get("channel"),
                    "httpStatus": status,
                    "blockers": [f"webhook returned HTTP {status}"],
                    "errorCode": "webhook_http_error",
                }

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200] if e.fp else ""
        if e.code == 429:
            retry_after = e.headers.get("Retry-After") or e.headers.get("retry-after") or "unknown"
            logger.error(f"Webhook rate limited; retry-after={retry_after}: {body}")
            return {
                "ok": False,
                "status": "rate-limited",
                "destinationAlias": event.get("channel"),
                "httpStatus": e.code,
                "retryAfter": retry_after,
                "blockers": [f"Discord webhook rate limited; retry-after={retry_after}"],
                "errorCode": "rate_limited",
            }
        else:
            logger.error(f"Webhook HTTP error {e.code}: {body}")
        return {
            "ok": False,
            "status": "failed",
            "destinationAlias": event.get("channel"),
            "httpStatus": e.code,
            "blockers": [f"webhook HTTP error {e.code}"],
            "errorCode": "webhook_http_error",
        }
    except urllib.error.URLError as e:
        if _is_dns_exception(e):
            logger.error("Webhook DNS failure: %s", e)
            return {
                "ok": False,
                "status": "failed",
                "destinationAlias": event.get("channel"),
                "blockers": ["webhook DNS resolution failed"],
                "errorCode": "dns_failure",
            }
        logger.error(f"Webhook URL error: {e}")
        return {
            "ok": False,
            "status": "failed",
            "destinationAlias": event.get("channel"),
            "blockers": [f"webhook network error: {type(e.reason).__name__ if getattr(e, 'reason', None) else type(e).__name__}"],
            "errorCode": "webhook_network_error",
        }
    except Exception as e:
        if _is_dns_exception(e):
            logger.error("Webhook DNS failure: %s", e)
            return {
                "ok": False,
                "status": "failed",
                "destinationAlias": event.get("channel"),
                "blockers": ["webhook DNS resolution failed"],
                "errorCode": "dns_failure",
            }
        logger.error(f"Webhook error: {e}")
        return {
            "ok": False,
            "status": "failed",
            "destinationAlias": event.get("channel"),
            "blockers": [f"webhook error: {type(e).__name__}"],
            "errorCode": "webhook_error",
        }


def _post_via_cli(event, channel, content):
    """Fallback: post via OpenClaw CLI (for non-Docker environments)."""
    try:
        cmd = [
            "openclaw", "message", "send",
            "--target", channel,
            "--channel", "discord",
            "--message", content,
        ]
        logger.info(f"Posting {event['event_type']} id={event['id']} via CLI to {channel}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            logger.info(f"Posted successfully: {event['event_type']} id={event['id']}")
            return {
                "ok": True,
                "status": "posted",
                "destinationAlias": channel,
                "blockers": [],
            }
        else:
            error = result.stderr.strip() or result.stdout.strip()
            logger.error(f"Post failed (rc={result.returncode}): {error[:200]}")
            return {
                "ok": False,
                "status": "failed",
                "destinationAlias": channel,
                "blockers": [f"OpenClaw post failed with rc={result.returncode}"],
                "errorCode": "openclaw_failed",
            }
    except subprocess.TimeoutExpired:
        logger.error(f"Post timed out: {event['event_type']} id={event['id']}")
        return {
            "ok": False,
            "status": "failed",
            "destinationAlias": channel,
            "blockers": ["OpenClaw post timed out"],
            "errorCode": "openclaw_timeout",
        }
    except Exception as e:
        logger.error(f"Post error: {e}")
        return {
            "ok": False,
            "status": "failed",
            "destinationAlias": channel,
            "blockers": [f"OpenClaw post error: {type(e).__name__}"],
            "errorCode": "openclaw_error",
        }


# ── Main Loop ───────────────────────────────────────────────────────

def archive_stale_backlog() -> int:
    """Archive/expire stale pending events without posting them to Discord."""
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
    return expired


def process_one_event_result(dry_run: bool = False, *, expire_stale: bool = True) -> dict[str, Any]:
    """Process the oldest pending event and return a structured one-step result."""
    if not dry_run and expire_stale:
        expired = archive_stale_backlog()
        if expired:
            return {
                "processed": False,
                "action": "archived-stale",
                "status": "blocked",
                "expired": expired,
                "errorCode": "stale_backlog_archived",
            }

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
        return {
            "processed": False,
            "action": "idle",
            "status": "idle",
            "errorCode": "no_pending_events",
        }

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
        return {
            "processed": True,
            "action": "dry-run",
            "status": "dry-run" if is_valid else "blocked",
            "eventId": event_id,
            "eventType": event_type,
            "errorCode": None if is_valid else "validation_failed",
        }

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
        return {
            "processed": False,
            "action": "blocked",
            "status": "blocked",
            "eventId": event_id,
            "eventType": event_type,
            "errorCode": "precondition_not_met",
        }

    # Post to Discord
    result = post_to_discord(event)
    write_delivery_proof(
        status=str(result.get("status") or "failed"),
        event=event,
        destination_alias=str(result.get("destinationAlias") or event.get("channel") or "unknown"),
        blockers=[str(item) for item in result.get("blockers") or []],
        http_status=result.get("httpStatus"),
        retry_after=result.get("retryAfter"),
        error_code=result.get("errorCode"),
    )

    if result.get("ok"):
        mark_posted(event_id)
        action = "posted"
    else:
        mark_failed(event_id, str(result.get("errorCode") or result.get("status") or "post_failed"))
        action = "rate-limited" if result.get("errorCode") == "rate_limited" else "failed"

    return {
        "processed": True,
        "action": action,
        "status": str(result.get("status") or ("posted" if result.get("ok") else "failed")),
        "eventId": event_id,
        "eventType": event_type,
        "httpStatus": result.get("httpStatus"),
        "retryAfter": result.get("retryAfter"),
        "errorCode": result.get("errorCode"),
    }


def process_one_event(dry_run: bool = False) -> bool:
    """Process the oldest pending event. Returns True if an event was processed."""
    return bool(process_one_event_result(dry_run=dry_run).get("processed"))


def drain_events(*, max_events: int = 10, dry_run: bool = False, sleep_sec: float = 0.5) -> dict[str, Any]:
    """Archive stale backlog, then process up to max_events fresh pending events."""
    max_events = max(0, int(max_events))
    summary: dict[str, Any] = {
        "schemaVersion": "fouler-play-discord-drain/v1",
        "startedAtUtc": _iso_now(),
        "completedAtUtc": None,
        "dryRun": dry_run,
        "maxEvents": max_events,
        "archivedStaleEvents": 0,
        "attemptedEvents": 0,
        "postedEvents": 0,
        "failedEvents": 0,
        "blocked": False,
        "stopReason": None,
        "lastResult": None,
        "secretValuesPrinted": False,
    }

    if not dry_run:
        summary["archivedStaleEvents"] = archive_stale_backlog()

    if max_events == 0:
        summary["stopReason"] = "max_events_zero"
        summary["completedAtUtc"] = _iso_now()
        summary["queue"] = _queue_summary()
        return summary

    for index in range(max_events):
        result = process_one_event_result(dry_run=dry_run, expire_stale=False)
        summary["lastResult"] = result
        action = str(result.get("action") or "")

        if action == "idle":
            summary["stopReason"] = "queue_empty"
            break
        if action == "blocked":
            summary["blocked"] = True
            summary["stopReason"] = str(result.get("errorCode") or "blocked")
            break
        if action == "dry-run":
            summary["attemptedEvents"] += 1
            summary["stopReason"] = "dry_run_complete"
            break

        if result.get("processed"):
            summary["attemptedEvents"] += 1
            if action == "posted":
                summary["postedEvents"] += 1
            else:
                summary["failedEvents"] += 1
                summary["blocked"] = True
                summary["stopReason"] = str(result.get("errorCode") or action or "post_failed")
                break

        if index < max_events - 1 and sleep_sec > 0:
            time.sleep(sleep_sec)
    else:
        summary["stopReason"] = "max_events_reached"

    summary["completedAtUtc"] = _iso_now()
    summary["queue"] = _queue_summary()
    return summary


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


def build_doctor_payload() -> dict[str, Any]:
    config = discord_config_status()
    stats = _queue_summary()
    transport_ready = bool(config["anyWebhookConfigured"] or config["openclawAvailable"])
    return {
        "schemaVersion": "fouler-play-discord-poster-doctor/v1",
        "checkedAt": _iso_now(),
        "cycleId": _cycle_id(),
        "ready": transport_ready and bool(stats.get("ready")),
        "transportReady": transport_ready,
        "config": config,
        "queue": stats,
        "reportPaths": _proof_report_paths(),
        "queueFile": _relative(Path(os.getenv("EVENT_QUEUE_FILE", str(PROJECT_ROOT / "events_queue.json")))),
        "logFile": _relative(LOG_FILE),
        "secretValuesPrinted": False,
        "note": "Read-only doctor; it does not post to Discord.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Post queued fouler-play Discord events")
    parser.add_argument("--doctor", action="store_true", help="print read-only Discord queue/poster readiness")
    parser.add_argument("--once", action="store_true", help="process at most one event and exit")
    parser.add_argument("--dry-run", action="store_true", help="write redacted Discord proof for the oldest pending event without posting")
    parser.add_argument("--archive-stale", action="store_true", help="archive stale pending events locally without live Discord posting")
    parser.add_argument("--drain", action="store_true", help="archive stale events, then transport up to --max-events fresh events")
    parser.add_argument("--max-events", type=int, default=10, help="maximum fresh events to transport with --drain")
    parser.add_argument("--require-ready", action="store_true", help="with --doctor, exit non-zero if no transport is configured")
    args = parser.parse_args()
    if args.doctor:
        payload = build_doctor_payload()
        TRUTH_DIR.mkdir(parents=True, exist_ok=True)
        DISCORD_DOCTOR_PROOF.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        doctor_blockers: list[str] = []
        if not payload.get("transportReady"):
            doctor_blockers.append("no Discord webhook or OpenClaw transport configured")
        if not (payload.get("queue") or {}).get("ready", True):
            doctor_blockers.extend(str(item) for item in ((payload.get("queue") or {}).get("health") or {}).get("blockers") or [])
        write_reporting_proof(
            status="ready" if payload["ready"] else "blocked",
            blockers=doctor_blockers,
        )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if args.require_ready and not payload["ready"] else 0
    load_env_chain()
    if args.archive_stale:
        payload = drain_events(max_events=0)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if args.drain:
        payload = drain_events(max_events=args.max_events, dry_run=args.dry_run)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if payload.get("blocked") else 0
    if args.dry_run:
        return 0 if process_one_event(dry_run=True) else 1
    if args.once:
        return 0 if process_one_event() else 1
    main_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
