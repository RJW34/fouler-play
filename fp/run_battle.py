import json
import os
import asyncio
import contextvars
import faulthandler
import time
from collections import OrderedDict
from copy import deepcopy
import logging
from logging.handlers import RotatingFileHandler
import re
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
from datetime import datetime

from data import all_move_json
from data.pkmn_sets import RandomBattleTeamDatasets, TeamDatasets
from data.pkmn_sets import SmogonSets
from infrastructure.runtime_paths import resolve_runtime_paths

try:
    faulthandler.enable(all_threads=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Startup log cleanup
# ---------------------------------------------------------------------------
# Limits for how many files to keep per category.
LOG_KEEP_BATTLE_FILES = int(os.getenv("LOG_KEEP_BATTLE_FILES", "60"))
LOG_KEEP_TRACE_FILES = int(os.getenv("LOG_KEEP_TRACE_FILES", "500"))
LOG_KEEP_STDOUT_FILES = int(os.getenv("LOG_KEEP_STDOUT_FILES", "3"))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_PATHS = resolve_runtime_paths(PROJECT_ROOT)
RUNTIME_STATE_ROOT = _RUNTIME_PATHS.state_root
RUNTIME_LOG_ROOT = _RUNTIME_PATHS.log_root
BATTLE_STATS_PATH = _RUNTIME_PATHS.battle_stats_path
_battle_stats_enrichment_lock = asyncio.Lock()
_deku_event_drain_lock = asyncio.Lock()
_battle_stats_authoritative_facts: dict[str, dict[str, object]] = {}


def cleanup_old_logs(log_dir: str | None = None, trace_dir: str | None = None):
    """Prune old battle logs, rotated backups, phantom logs, decision traces,
    and stdout logs on startup.  Keeps the most recent files by mtime."""
    _log = logging.getLogger(__name__)
    log_dir = log_dir or str(RUNTIME_LOG_ROOT)
    trace_dir = trace_dir or os.path.join(log_dir, "decision_traces")
    os.makedirs(log_dir, exist_ok=True)
    removed = 0

    # --- 1. Phantom _None.log files (always delete all â€” they're from dead rooms) ---
    for fname in os.listdir(log_dir):
        if "_None.log" in fname:
            try:
                os.remove(os.path.join(log_dir, fname))
                removed += 1
            except OSError:
                pass

    # --- 2. Battle log files and their rotated backups ---
    # Collect battle-*.log* (but not worker_*_init.log or init.log)
    battle_logs = []
    for fname in os.listdir(log_dir):
        if not fname.startswith("battle-"):
            continue
        if "_None.log" in fname:
            continue  # already handled above
        path = os.path.join(log_dir, fname)
        try:
            battle_logs.append((os.path.getmtime(path), path))
        except OSError:
            pass

    # Group by base name (strip .1/.2/.3 suffix) so we prune whole families
    base_names: dict[str, list[str]] = {}
    for _mtime, path in battle_logs:
        fname = os.path.basename(path)
        base = re.sub(r"\.log(\.\d+)?$", ".log", fname)
        base_names.setdefault(base, []).append(path)

    # Sort base names by newest file in each family, keep most recent N
    family_newest = []
    for base, paths in base_names.items():
        newest = max(os.path.getmtime(p) for p in paths)
        family_newest.append((newest, base, paths))
    family_newest.sort(reverse=True)

    for _newest, _base, paths in family_newest[LOG_KEEP_BATTLE_FILES:]:
        for p in paths:
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass

    # --- 3. Decision trace JSON files ---
    if os.path.isdir(trace_dir):
        traces = []
        for fname in os.listdir(trace_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(trace_dir, fname)
            try:
                traces.append((os.path.getmtime(path), path))
            except OSError:
                pass
        traces.sort(reverse=True)
        for _mtime, path in traces[LOG_KEEP_TRACE_FILES:]:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass

    # --- 4. Old stdout batch logs ---
    stdout_logs = []
    for fname in os.listdir(log_dir):
        if "stdout" in fname and fname.endswith(".log"):
            path = os.path.join(log_dir, fname)
            try:
                stdout_logs.append((os.path.getmtime(path), path))
            except OSError:
                pass
    stdout_logs.sort(reverse=True)
    for _mtime, path in stdout_logs[LOG_KEEP_STDOUT_FILES:]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass

    if removed:
        _log.info(f"Log cleanup: removed {removed} old files")
import constants
from constants import BattleType
from config import FoulPlayConfig, SaveReplay, configured_log_level
from fp.battle import LastUsedMove, Pokemon, Battle
from fp.battle_modifier import async_update_battle, process_battle_updates
from fp.helpers import normalize_name
from fp.search.main import find_best_move
from fp.decision_trace import write_decision_trace, build_trace_base
from fp.movepool_tracker import get_threat_category, ThreatCategory
from fp.opponent_model import OPPONENT_MODEL
from fp.hybrid_policy import run_hybrid_rerank
from fp.helpers import type_effectiveness_modifier
from fp.battle_decision import StrategicDecisionLayer, clear_battle_strategy
from fp.devstream_chat import post_battle_messages

from fp.websocket_client import PSWebsocketClient
from streaming.state_store import (
    expected_battle_surfaces,
    write_active_battles,
    read_active_battles,
    write_status,
    update_daily_stats,
)
from fp.team_analysis import analyze_team
from fp.playstyle_config import PlaystyleConfig, Playstyle, HAZARD_MOVES, PIVOT_MOVES, RECOVERY_MOVES
from fp.gameplan_integration import generate_and_store_gameplan, get_gameplan, clear_gameplan
from constants_pkg.strategy import SETUP_MOVES
from infrastructure.event_queue_lib import queue_event
from infrastructure.event_poster import process_pending_events
from infrastructure.discord_reporting import (
    build_contract_payload,
    canonical_replay_url,
    format_elo_delta,
    public_replay_id_candidate,
    recent_results_safety_alert,
    summarize_recent_results_with_current,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using default %s", name, default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using default %s", name, default)
        return default


# Blacklist for dead battles (forcibly terminated due to timeout)
# Prevents re-claiming the same stuck battle immediately after termination
_dead_battle_blacklist: "OrderedDict[str, float]" = OrderedDict()

# Active battles tracking for stream overlay
# battle_id -> {"opponent": str, "started": datetime, "worker_id": int | None}
_active_battles = {}
_battles_lock = asyncio.Lock()
_last_active_battles_write = 0.0
_last_active_battles_payload = None

# Spectator invite tracking: the /invite for a battle must be sent exactly
# once, and always BEFORE the battle is published as active to the OBS
# router/feed — a Browser Source that loads a private room before its
# spectator is invited renders only the battlefield backdrop.
_spectator_invites_sent: set[str] = set()

_ENV_FALSE_VALUES = ("0", "false", "no", "off")


def spectator_invites_enabled() -> bool:
    """True when the spectator should be invited to each battle.

    ENABLE_SPECTATOR_INVITES honors explicit false values ("0", "false",
    "no", "off", any case); every other value — including unset or empty —
    enables invites as long as SPECTATOR_USERNAME is configured.
    """
    if not getattr(FoulPlayConfig, "spectator_username", None):
        return False
    raw = os.getenv("ENABLE_SPECTATOR_INVITES", "")
    return raw.strip().lower() not in _ENV_FALSE_VALUES


async def ensure_spectator_invited(ps_websocket_client, battle_tag) -> bool:
    """Send the spectator /invite for ``battle_tag`` exactly once.

    Callers must invoke this BEFORE the battle is published as active
    (``update_active_battles_file``) so the OBS Browser Source never
    navigates to a private room the spectator cannot yet view.  Returns
    True only when this call sent the invite.
    """
    if not spectator_invites_enabled():
        return False
    if not battle_tag or battle_tag in _spectator_invites_sent:
        return False
    # Reserve before awaiting so a concurrent caller cannot double-send.
    _spectator_invites_sent.add(battle_tag)
    try:
        logger.info(f"Inviting spectator: {FoulPlayConfig.spectator_username}")
        await ps_websocket_client.send_message(
            battle_tag, [f"/invite {FoulPlayConfig.spectator_username}"]
        )
    except Exception:
        # Release the reservation so a later fallback call can retry.
        _spectator_invites_sent.discard(battle_tag)
        raise
    return True

# Battle message timeout tuning (seconds)
MESSAGE_TIMEOUT_SEC = int(os.getenv("BATTLE_MESSAGE_TIMEOUT_SEC", "120"))
STALE_STRIKES = int(os.getenv("BATTLE_STALE_STRIKES", "2"))
# After this many consecutive timeout strikes, declare the battle a disconnect
# and return winner=None so it counts toward the quota. Default: 5 strikes
# (= 5 * 120s = 10 minutes of silence).
DISCONNECT_STRIKES = int(os.getenv("BATTLE_DISCONNECT_STRIKES", "5"))
STALE_DISPLAY_GRACE_SEC = int(os.getenv("BATTLE_STALE_DISPLAY_GRACE_SEC", "900"))
GHOST_BATTLE_MAX_AGE_SEC = int(os.getenv("GHOST_BATTLE_MAX_AGE_SEC", "1800"))  # 30min: hard ghost removal (stall games can run 20+ min)
# Throttle active_battles.json writes to avoid excessive disk churn.
ACTIVE_BATTLES_WRITE_INTERVAL_SEC = float(os.getenv("ACTIVE_BATTLES_WRITE_INTERVAL_SEC", "1.0"))
# How often (seconds) the battle loop refreshes active_battles.json heartbeat.
ACTIVE_BATTLES_HEARTBEAT_SEC = float(os.getenv("ACTIVE_BATTLES_HEARTBEAT_SEC", "30.0"))
# Hard cap for move selection (seconds). If exceeded, use fallback move.
DECISION_TIMEOUT_SEC = int(os.getenv("DECISION_TIMEOUT_SEC", "25"))
TRACE_DECISIONS = os.getenv("DECISION_TRACE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
RESUME_ACTIVE_BATTLES = os.getenv("RESUME_ACTIVE_BATTLES", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
RESUME_MAX_AGE_SEC = int(os.getenv("RESUME_MAX_AGE_SEC", "900"))
RESUME_JOIN_TIMEOUT_SEC = int(os.getenv("RESUME_JOIN_TIMEOUT_SEC", "10"))
# Cap how many times a single in-flight resume entry may time out before we
# stop re-queuing it. A battle whose Showdown room is dead but never emits a
# close/finish message (a hard-killed prior client orphan) would otherwise
# time out -> requeue -> re-claim -> time out forever, pinning a worker off
# the ladder until the server inactivity timer fires (~1-2 min). After the
# cap we forfeit + blacklist the orphan so the worker returns to laddering.
RESUME_MAX_TIMEOUT_REQUEUES = int(os.getenv("RESUME_MAX_TIMEOUT_REQUEUES", "2"))
SEARCH_WAIT_TIMEOUT_SEC = int(os.getenv("SEARCH_WAIT_TIMEOUT_SEC", "120"))
REPLAY_CHECK_TTL_SEC = int(os.getenv("REPLAY_CHECK_TTL_SEC", "60"))
REPLAY_CHECK_MIN_AGE_SEC = int(os.getenv("REPLAY_CHECK_MIN_AGE_SEC", "180"))
REPLAY_CHECK_TIMEOUT_SEC = int(os.getenv("REPLAY_CHECK_TIMEOUT_SEC", "4"))
REPLAY_CACHE_MAX_ENTRIES = max(100, int(os.getenv("REPLAY_CACHE_MAX_ENTRIES", "4000")))
REPLAY_CACHE_RETENTION_SEC = max(REPLAY_CHECK_TTL_SEC * 5, 300)
REPLAY_UPLOAD_RESOLVE_ATTEMPTS = max(1, _env_int("REPLAY_UPLOAD_RESOLVE_ATTEMPTS", 4))
REPLAY_UPLOAD_RESOLVE_DELAY_SEC = max(0.0, _env_float("REPLAY_UPLOAD_RESOLVE_DELAY_SEC", 1.0))
REPLAY_JSON_SAVE_ATTEMPTS = max(1, _env_int("REPLAY_JSON_SAVE_ATTEMPTS", 2))
REPLAY_JSON_SAVE_DELAY_SEC = max(0.0, _env_float("REPLAY_JSON_SAVE_DELAY_SEC", 1.0))
REPLAY_JSON_SAVE_TIMEOUT_SEC = max(1.0, _env_float("REPLAY_JSON_SAVE_TIMEOUT_SEC", 12.0))
DEAD_BATTLE_BLACKLIST_MAX = max(100, int(os.getenv("DEAD_BATTLE_BLACKLIST_MAX", "2000")))

# Hard battle timeout (seconds). 0 disables forced battle termination.
BATTLE_HARD_TIMEOUT_SEC = int(os.getenv("BATTLE_HARD_TIMEOUT_SEC", "0"))

# Prevents heartbeat from re-registering battles that already finished.
# Uses OrderedDict for FIFO eviction (set.pop() was evicting arbitrarily).
_concluded_battles: OrderedDict[str, float] = OrderedDict()
_CONCLUDED_BATTLES_MAX = 200

# --- Per-worker logging ---
# ContextVar tracks which worker (and battle) the current coroutine belongs to.
# Each worker gets its own RotatingFileHandler so log files don't clobber each other.
_current_worker_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_worker_id", default=None
)
_worker_handlers: dict[int, RotatingFileHandler] = {}


class _WorkerFilter(logging.Filter):
    """Only accept records from the matching worker coroutine."""

    def __init__(self, worker_id: int):
        super().__init__()
        self.worker_id = worker_id

    def filter(self, record):
        return _current_worker_id.get(None) == self.worker_id


class _InitOnlyFilter(logging.Filter):
    """Only accept records that have no worker context (init/shared messages)."""

    def filter(self, record):
        return _current_worker_id.get(None) is None


_shared_handler_filtered = False
_WINDOWS_UNSAFE_LOG_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _get_or_create_worker_handler(worker_id: int) -> RotatingFileHandler:
    """Return the RotatingFileHandler for *worker_id*, creating one if needed."""
    global _shared_handler_filtered
    if worker_id in _worker_handlers:
        return _worker_handlers[worker_id]
    log_dir = os.getenv("FOULER_LOG_DIR", str(RUNTIME_LOG_ROOT))
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, f"worker_{worker_id}_init.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    worker_log_level = configured_log_level("FOULER_WORKER_LOG_LEVEL")
    handler.setLevel(worker_log_level)
    from config import CustomFormatter
    handler.setFormatter(CustomFormatter())
    handler.addFilter(_WorkerFilter(worker_id))
    logging.getLogger().addHandler(handler)
    _worker_handlers[worker_id] = handler
    # Add init-only filter to the shared handler so it stops duplicating
    # worker output into init.log. Only needs to happen once.
    if not _shared_handler_filtered and FoulPlayConfig.file_log_handler:
        FoulPlayConfig.file_log_handler.addFilter(_InitOnlyFilter())
        _shared_handler_filtered = True
    logger.info("Created per-worker log handler for worker %d", worker_id)
    return handler


def _safe_log_filename_part(value: object, *, fallback: str = "unknown", max_length: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    safe = _WINDOWS_UNSAFE_LOG_FILENAME_RE.sub("_", text)
    safe = re.sub(r"_+", "_", safe).strip(" ._")
    if not safe:
        safe = fallback
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip(" ._") or fallback
    return safe


def _worker_battle_log_path(worker_id: int, battle_tag: str, opponent_name: str) -> str:
    safe_battle_tag = _safe_log_filename_part(battle_tag, fallback=f"worker_{worker_id}")
    safe_opponent_name = _safe_log_filename_part(opponent_name)
    log_dir = os.getenv("FOULER_LOG_DIR", str(RUNTIME_LOG_ROOT))
    return os.path.join(log_dir, f"{safe_battle_tag}_{safe_opponent_name}.log")


def _rollover_worker_handler(worker_id: int, battle_tag: str, opponent_name: str):
    """Switch worker's log handler to a new battle-specific file."""
    handler = _get_or_create_worker_handler(worker_id)
    handler.baseFilename = _worker_battle_log_path(worker_id, battle_tag, opponent_name)
    # doRollover() renames the current file to .1 and opens a fresh file
    # with the new baseFilename â€” exactly like the original do_rollover().
    handler.doRollover()

# Battle chat defaults
OPENING_CHAT_MESSAGE = "hf"

# Resume queue for in-progress battles (populated on startup from active_battles.json)
_resume_lock = asyncio.Lock()
_resume_by_worker: dict[int, list[dict]] = {}
_resume_queue: list[dict] = []
_replay_cache: dict[str, dict[str, float | bool]] = {}

# Per-battle ELO cache: battle_tag -> pre-battle ELO value
# Used to compute ELO delta for Discord reports
_elo_before_cache: dict[str, float] = {}


def _blacklist_battle_tag(battle_tag: str) -> None:
    if not battle_tag:
        return
    _dead_battle_blacklist[battle_tag] = time.time()
    _dead_battle_blacklist.move_to_end(battle_tag)
    while len(_dead_battle_blacklist) > DEAD_BATTLE_BLACKLIST_MAX:
        _dead_battle_blacklist.popitem(last=False)


def _prune_replay_cache(now: float) -> None:
    stale_replays = [
        replay_id
        for replay_id, payload in _replay_cache.items()
        if (now - float(payload.get("checked", 0.0))) > REPLAY_CACHE_RETENTION_SEC
    ]
    for replay_id in stale_replays:
        _replay_cache.pop(replay_id, None)

    overflow = len(_replay_cache) - REPLAY_CACHE_MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(
            _replay_cache.items(),
            key=lambda item: float(item[1].get("checked", 0.0)),
        )[:overflow]
        for replay_id, _ in oldest:
            _replay_cache.pop(replay_id, None)


def _parse_started_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


async def _send_battle_chat(ps_websocket_client, battle_tag: str, messages: list[str]) -> None:
    for i, message in enumerate(messages):
        if not message:
            continue
        if i > 0:
            await asyncio.sleep(0.6)  # Avoid Showdown message throttle
        logger.info("Sending battle chat in %s: %s", battle_tag, message)
        await ps_websocket_client.send_message(battle_tag, [message])


def _normalize_username(name: str) -> str:
    """Normalize a Showdown username for comparison (strip non-alnum, lowercase)."""
    import re
    return re.sub(r'[^a-z0-9]', '', name.lower()) if name else ""


def _showdown_account_identities(*extra_names: object) -> set[str]:
    """Return account names that should be treated as our bot in this process."""
    accounts: set[str] = set()
    raw_values: list[object] = [getattr(FoulPlayConfig, "username", None)]
    raw_values.extend(extra_names)
    raw_values.append(os.getenv("SHOWDOWN_ACCOUNTS", ""))

    for raw in raw_values:
        if raw is None:
            continue
        for part in str(raw).split(","):
            normalized = _normalize_username(part.strip())
            if normalized:
                accounts.add(normalized)
    return accounts


def _is_our_showdown_account(name: object, *extra_names: object) -> bool:
    normalized = _normalize_username(str(name) if name is not None else "")
    return bool(normalized and normalized in _showdown_account_identities(*extra_names))


def _battle_result_from_evidence(
    winner: object,
    our_player_name: object = None,
    *,
    opponent_name: object = None,
    elo_delta: object = None,
) -> str:
    """Return win/loss from the ladder battle winner.

    Ladder/profile ELO movement is display/proof context only because it can
    reflect a stale cache, another concurrent battle, or a mis-attributed raw
    rating segment. Keep the terminal Showdown winner as the result source; the
    optional ``elo_delta`` parameter remains for call-site compatibility but is
    intentionally not allowed to relabel an explicit winner.
    """
    if winner is None:
        # A ladder battle that reaches our terminal handler without a winner is
        # a timeout/disconnect from our side, not a competitive draw.
        return "loss"
    if str(winner).lower() in {"tie", "draw"}:
        # In unattended ladder play a tie/draw is a non-win mission outcome and
        # can also be how operational timeouts surface downstream. Counting it
        # as neutral hides regressions in ELO trend, loss-streak safety valves,
        # and Discord reporting.
        return "loss"
    if _is_our_showdown_account(winner, our_player_name):
        return "win"
    winner_norm = _normalize_username(str(winner) if winner is not None else "")
    opponent_norm = _normalize_username(str(opponent_name) if opponent_name is not None else "")
    if opponent_norm and winner_norm == opponent_norm:
        return "loss"
    if opponent_norm and winner_norm:
        return "win"
    return "loss"


def _operational_loss_stream_payload(
    battle_tag: str,
    *,
    reason: str,
    ended: float | None = None,
    elapsed_seconds: float | None = None,
    timeout_strikes: int | None = None,
) -> dict:
    """BATTLE_END payload for bot-side timeout/disconnect losses.

    ``winner=None`` is still the raw Showdown evidence, but downstream consumers
    must not infer a tie from no winner. Carry the normalized result explicitly.
    """

    payload = {
        "id": battle_tag,
        "winner": None,
        "result": "loss",
        "terminalResult": "loss",
        "reason": reason,
        "operationalLoss": True,
        "ended": time.time() if ended is None else ended,
    }
    if elapsed_seconds is not None:
        payload["elapsedSeconds"] = round(float(elapsed_seconds), 3)
    if timeout_strikes is not None:
        payload["timeoutStrikes"] = int(timeout_strikes)
    return payload


def _queue_operational_loss_battle_result(
    battle_tag: str,
    *,
    opponent_name: str | None,
    team_name: str | None = None,
    turns: int | None = None,
    reason: str,
    elapsed_seconds: float | None = None,
    timeout_strikes: int | None = None,
) -> str | None:
    """Queue Discord proof for a bot-side timeout/disconnect loss."""

    if not battle_result_event_queue_enabled():
        logger.info("Skipping operational-loss battle_result for %s: queue disabled", battle_tag)
        return None
    opponent = opponent_name or "unknown opponent"
    reason_label = str(reason or "timeout").replace("_", " ")
    elapsed_note = ""
    if elapsed_seconds is not None:
        elapsed_note = f"; elapsed={float(elapsed_seconds):.0f}s"
    strikes_note = ""
    if timeout_strikes is not None:
        strikes_note = f"; timeout_strikes={int(timeout_strikes)}"
    decisive_reason = (
        "Loss came from inactivity/disconnect behavior, so this looks operational "
        "before it looks strategic."
    )
    event_id = queue_event(
        "battle_result",
        "battles",
        build_contract_payload(
            "PROOF",
            f"battle result loss vs {opponent}",
            f"Battle {battle_tag} ended loss against {opponent}.",
            (
                "Timeouts, hard time limits, and disconnects count as losses for "
                "ladder learning and operator reports."
            ),
            (
                f"battle_id={battle_tag}; result=loss; reason={reason_label}; "
                f"team_file={team_name or 'unknown'}; opponent={opponent}; "
                f"turns={turns}{elapsed_note}{strikes_note}"
            ),
            "Review reconnect/timer handling before treating this as a strategic team loss.",
            source="fp.run_battle",
            battle_id=battle_tag,
            result="loss",
            team_file=team_name or "unknown",
            opponent=opponent,
            turns=turns,
            decisive_reason=decisive_reason,
            next_battle_action="Review reconnect / timer handling before blaming the team.",
            operational_loss=True,
            timeout_reason=reason,
            elapsed_seconds=elapsed_seconds,
            timeout_strikes=timeout_strikes,
        ),
        dedup_window_sec=5,
    )
    return event_id


# Authoritative per-battle rating transition emitted by Showdown to the battle
# room at the end of a RATED game. The live wire format wraps both the player
# name and the new rating in HTML, e.g.
#   |raw|<username ...>npctypebeat</username>'s rating: 1105 &rarr; <strong>1133</strong><br />(+28 for winning)
# CRUCIALLY, Showdown sends a separate rating line for BOTH players. We must
# parse the line for OUR account only -- grabbing the first/opponent line gives
# the wrong sign (e.g. reporting the winner's +24 on our LOSS).
#
# We must capture THIS, not the lagging shared ladder-API aggregate, because
# with concurrent battles the API value is moved by other games between this
# battle's before/after snapshots (collapsing the delta to ~+/-1).
#
# Each per-player segment looks like: NAME's rating: OLD <arrow> [<tags>] NEW
# where NAME may itself be HTML-wrapped (e.g. <username ...>npctypebeat</username>).
# We match the "rating: OLD -> NEW" part here, then attribute it to a player by
# inspecting the text that precedes the match (see parse_rating_transition).
_RATING_TRANSITION_RE = re.compile(
    r"rating:\s*(\d+)\s*(?:&rarr;|&#8594;|\u2192|->|&gt;)\s*(?:<[^>]+>\s*)*(\d+)",
    re.IGNORECASE,
)


def parse_rating_transition(
    msg: str, our_username: str | None = None
) -> tuple[int, int, int] | None:
    """Parse a Showdown |raw| end-of-battle rating line for OUR account.

    Showdown emits one rating line per player, e.g.::

        |raw|<username ...>npctypebeat</username>'s rating: 1221 &rarr; <strong>1197</strong><br />(-24 for losing)

    When *our_username* is provided we return the transition belonging to the
    segment that names our account, so a LOSS is never mis-reported with the
    winning opponent's positive delta. Each transition is attributed to the
    player whose (HTML-stripped) name appears in the text segment immediately
    preceding that ``rating:`` token. If *our_username* is None we fall back to
    the first rating transition found (legacy single-player behaviour).

    Returns (old, new, delta) where delta = new - old, or None if no matching
    rating transition is present. ``delta`` is the TRUE per-battle rating change
    computed by Showdown's authoritative engine.
    """
    if not msg or "rating:" not in msg:
        return None

    want = _normalize_username(our_username) if our_username else None
    fallback: tuple[int, int, int] | None = None
    prev_end = 0

    for match in _RATING_TRANSITION_RE.finditer(msg):
        try:
            old = int(match.group(1))
            new = int(match.group(2))
        except (TypeError, ValueError):
            prev_end = match.end()
            continue
        transition = (old, new, new - old)
        if fallback is None:
            fallback = transition
        if want is None:
            return transition
        # The player name lives in the text between the previous segment's end
        # and this "rating:" token. Strip HTML and normalize, then check whether
        # our account name is named there.
        segment = msg[prev_end:match.start()]
        segment_names = _normalize_username(re.sub(r"<[^>]+>", " ", segment))
        if want and want in segment_names:
            return transition
        prev_end = match.end()

    # No segment matched our username. When a username filter was requested we
    # refuse to guess (return None so the caller falls back to the ladder API,
    # rather than reporting the opponent's delta). Only use the first-found
    # fallback when no username filter was requested.
    return None if want is not None else fallback


def _normalize_replay_id(battle_id: str) -> str:
    """Convert a battle tag to a public replay ID.
    
    Battle tags come in two forms:
      - battle-gen9ou-2535182938          (public)
      - battle-gen9ou-2535189406-HASH     (private hash appended)
    
    Public replay IDs are always: format-number (e.g. gen9ou-2535182938)
    The private hash (4th segment) must be stripped or the URL 404s.
    """
    if not battle_id:
        return ""
    tag = battle_id
    if tag.startswith("battle-"):
        tag = tag.replace("battle-", "", 1)
    # Strip private hash: format is "gen9ou-NUMBER" or "gen9ou-NUMBER-PRIVATEHASH"
    # Keep only first two segments (format + number)
    parts = tag.split("-")
    if len(parts) >= 3:
        # parts[0] = "gen9ou", parts[1] = number, parts[2+] = private hash
        tag = f"{parts[0]}-{parts[1]}"
    return tag


async def _replay_exists(replay_id: str, *, use_cache: bool = True) -> bool:
    if not replay_id:
        return False
    now = time.time()
    _prune_replay_cache(now)
    cached = _replay_cache.get(replay_id)
    if use_cache and cached and (now - float(cached.get("checked", 0.0))) < REPLAY_CHECK_TTL_SEC:
        return bool(cached.get("exists", False))
    url = f"https://replay.pokemonshowdown.com/{replay_id}.json"
    timeout = aiohttp.ClientTimeout(total=REPLAY_CHECK_TIMEOUT_SEC)
    headers = {"User-Agent": "FoulerPlay/1.0"}
    exists = False
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    exists = True
                elif resp.status in (404, 410):
                    exists = False
                else:
                    exists = False
    except Exception:
        exists = False
    _replay_cache[replay_id] = {"exists": exists, "checked": now}
    _prune_replay_cache(now)
    return exists


async def resolve_public_replay_url(
    *,
    battle_tag: str | None,
    replay_url: str | None,
    max_attempts: int | None = None,
    delay_seconds: float | None = None,
    allow_battle_tag_fallback: bool = False,
) -> str | None:
    """Poll for a just-uploaded public replay URL without relying on later events."""
    replay_source = replay_url or (battle_tag if allow_battle_tag_fallback else None)
    replay_id = public_replay_id_candidate(replay_source)
    if not replay_id:
        return None

    attempts = max(1, REPLAY_UPLOAD_RESOLVE_ATTEMPTS if max_attempts is None else max_attempts)
    delay = max(0.0, REPLAY_UPLOAD_RESOLVE_DELAY_SEC if delay_seconds is None else delay_seconds)
    for attempt in range(1, attempts + 1):
        # Force refresh so a cached 404 from the first upload check does not
        # suppress the bounded retry window.
        if await _replay_exists(replay_id, use_cache=False):
            return f"https://replay.pokemonshowdown.com/{replay_id}"
        if attempt < attempts and delay > 0:
            await asyncio.sleep(delay)
    logger.info(
        "Replay public upload still pending for %s after %d resolver attempt(s)",
        replay_id,
        attempts,
    )
    return None


async def _fetch_elo(username: str, fmt: str = "gen9ou") -> tuple:
    """Fetch current ELO and GXE from Pokemon Showdown ladder API.
    Returns (elo, gxe) or (None, None) on failure."""
    try:
        url = f"https://pokemonshowdown.com/users/{_normalize_username(username)}.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return (None, None)
                data = await resp.json(content_type=None)
                if 'ratings' in data and fmt in data['ratings']:
                    rating = data['ratings'][fmt]
                    return (rating.get('elo'), rating.get('gxe'))
    except Exception:
        pass
    return (None, None)


async def _fetch_glicko(username: str, fmt: str = "gen9ou") -> tuple:
    """Fetch (rpr, rprd, gxe) from the canonical ladder JSON API.

    rpr  = Glicko-1 rating, rprd = its deviation. ELO is only a meaningful progress
    signal once rprd < 50 (well-established rating); below that the ladder is still
    placing the account and single-battle ELO swings are noise. Used by the ELO
    watchdog to avoid reverting on placement-period jitter.
    Returns (None, None, None) on failure.
    """
    try:
        url = f"https://pokemonshowdown.com/users/{_normalize_username(username)}.json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return (None, None, None)
                data = await resp.json(content_type=None)
                if 'ratings' in data and fmt in data['ratings']:
                    rating = data['ratings'][fmt]
                    def _f(v):
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            return None
                    return (_f(rating.get('rpr')), _f(rating.get('rprd')), _f(rating.get('gxe')))
    except Exception:
        pass
    return (None, None, None)



async def _save_replay_json_locally(replay_id: str) -> dict | None:
    """Fetch replay JSON from Pokemon Showdown and save it locally.
    
    This runs immediately after a battle ends so replays don't expire.
    Saves to replay_analysis/{replay_id}.json
    """
    if not replay_id:
        return None
    # Normalize the replay ID (strip battle- prefix, keep hash variants)
    clean_id = replay_id
    if clean_id.startswith("battle-"):
        clean_id = clean_id[len("battle-"):]
    
    # Check if already saved
    replay_dir = RUNTIME_STATE_ROOT / "replay_analysis"
    replay_dir.mkdir(parents=True, exist_ok=True)
    local_path = replay_dir / f"{clean_id}.json"
    if local_path.exists():
        logger.debug(f"Replay already saved locally: {clean_id}.json")
        try:
            import json as _json
            with open(local_path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            pass
    
    # Fetch from Pokemon Showdown
    url = f"https://replay.pokemonshowdown.com/{clean_id}.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    # Save locally
                    import json as _json
                    local_path.write_text(
                        _json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    logger.info(f"Saved replay JSON locally: {clean_id}.json")
                    return data
                else:
                    logger.debug(f"Replay not yet available on PS: {clean_id} (HTTP {resp.status})")
    except asyncio.TimeoutError:
        logger.debug(f"Timed out fetching replay JSON: {clean_id}")
    except Exception as e:
        logger.debug(f"Failed to save replay JSON for {clean_id}: {e}")
    return None


async def _save_replay_json_for_evidence(
    replay_id: str | None,
    *,
    attempts: int | None = None,
    delay_seconds: float | None = None,
    timeout_seconds: float | None = None,
) -> bool:
    """Persist replay JSON before battle teardown so autoresearch has proof."""
    if not replay_id:
        return False
    max_attempts = max(1, attempts or REPLAY_JSON_SAVE_ATTEMPTS)
    delay = REPLAY_JSON_SAVE_DELAY_SEC if delay_seconds is None else max(0.0, delay_seconds)
    timeout = REPLAY_JSON_SAVE_TIMEOUT_SEC if timeout_seconds is None else max(0.1, timeout_seconds)
    for attempt in range(max_attempts):
        try:
            data = await asyncio.wait_for(
                _save_replay_json_locally(replay_id),
                timeout=timeout,
            )
            if data:
                return True
        except asyncio.TimeoutError:
            logger.debug("Timed out saving replay JSON evidence: %s", replay_id)
        except Exception as exc:
            logger.debug("Failed replay JSON evidence save for %s: %s", replay_id, exc)
        if attempt + 1 < max_attempts and delay:
            await asyncio.sleep(delay)
    logger.warning("Replay JSON evidence was not saved locally: %s", replay_id)
    return False

async def _post_battle_to_discord(
    battle_tag: str,
    winner: str | None,
    opponent_name: str,
    replay_url: str | None = None,
    team_name: str | None = None,
    our_player_name: str | None = None,
    elo_before: float | None = None,
    turn_count: int | None = None,
    rating_delta: tuple[int, int, int] | None = None,
) -> float | None:
    """Post battle result to Discord webhook using the Lucario reporting format.

    Format:
        âš”ï¸ WIN vs Opponent (1050 â†’ 1065 ELO)
        ðŸ† Team: fat-team-a | Turns: 42
        ðŸ”— <https://replay.pokemonshowdown.com/gen9ou-XXXXX>

    Args:
        battle_tag: Battle ID
        winner: Winner's username ("tie"/"draw" counts as a mission loss in ladder play;
            None for an operational loss)
        opponent_name: Opponent's username
        replay_url: Replay URL (if available)
        team_name: Team name used (if applicable)
        our_player_name: Our actual player name in this battle
        elo_before: ELO before battle (for delta display)
        turn_count: Number of turns the battle lasted
        rating_delta: Authoritative per-battle (old, new, delta) parsed from
            Showdown's end-of-battle |raw| rating line. When present this is
            the TRUE rating change and overrides the lagging ladder-API value.
    """
    # Determine if we won. SHOWDOWN_ACCOUNTS is only an alias list; the active
    # runtime account from CLI/config must remain authoritative, but a terminal
    # winner that is not the known opponent is also our account when config is
    # stale.
    initial_result_key = _battle_result_from_evidence(
        winner,
        our_player_name,
        opponent_name=opponent_name,
    )
    parsed_is_win = initial_result_key == "win"

    winner_norm = _normalize_username(str(winner) if winner is not None else "")
    opponent_norm = _normalize_username(str(opponent_name) if opponent_name is not None else "")
    if (
        parsed_is_win
        and winner_norm
        and winner_norm != opponent_norm
        and not _is_our_showdown_account(winner, our_player_name)
    ):
        our_player_name = str(winner)
    elif not our_player_name:
        our_player_name = winner if parsed_is_win else FoulPlayConfig.username

    ps_username = our_player_name or FoulPlayConfig.username
    authoritative_elo_delta: int | None = None

    # --- AUTHORITATIVE per-battle rating (preferred) ---
    # If Showdown sent us the end-of-battle |raw| rating transition for this
    # game, use it verbatim. This is the real per-game delta (typically +/-8..30)
    # and is immune to the concurrent-battle ladder-API lag that collapsed the
    # old elo_after - elo_before computation to ~+/-1.
    if rating_delta is not None:
        rd_old, rd_new, rd_delta = rating_delta
        elo_before = float(rd_old)
        elo_after = float(rd_new)
        authoritative_elo_delta = int(rd_delta)
        gxe = None
        logger.info(
            "Using authoritative |raw| rating for %s: %d -> %d (%+d)",
            battle_tag, rd_old, rd_new, rd_delta,
        )
    else:
        # Fallback: poll the shared ladder API (lagging aggregate). Only used
        # when no |raw| rating arrived (e.g. an unrated battle).
        elo_after, gxe = await _fetch_elo(ps_username)

        # FOULER-ELO-PROPAGATION-RETRY-2026-05-20: PS profile API has a cache lag; if we got the
        # same value as elo_before, retry with backoff to give the rating
        # update time to propagate. If all retries return the same value,
        # set elo_after = None so the formatter shows no ELO info rather
        # than fabricating a +0 delta for a real win/loss.
        if (
            elo_after is not None
            and elo_before is not None
            and abs(float(elo_after) - float(elo_before)) < 0.01
        ):
            for _retry_delay in (5, 10, 15):
                try:
                    await asyncio.sleep(_retry_delay)
                except Exception:
                    pass
                _retry_elo, _retry_gxe = await _fetch_elo(ps_username)
                if (
                    _retry_elo is not None
                    and abs(float(_retry_elo) - float(elo_before)) >= 0.01
                ):
                    elo_after = _retry_elo
                    if _retry_gxe is not None:
                        gxe = _retry_gxe
                    break
            else:
                # All retries returned the same value as elo_before.
                # Honest-fail: report no ELO info instead of fake "+0".
                logger.info(
                    "ELO update did not propagate within 30s for %s; "
                    "marking elo_after=None (was %s, before=%s).",
                    ps_username, elo_after, elo_before,
                )
                elo_after = None

    # --- Line 1: Result header ---
    if authoritative_elo_delta is None and elo_after is not None and elo_before is not None:
        authoritative_elo_delta = int(round(float(elo_after) - float(elo_before)))
    result_key = _battle_result_from_evidence(
        winner,
        our_player_name,
        opponent_name=opponent_name,
    )
    if authoritative_elo_delta:
        contradicts_result = (
            (result_key == "win" and authoritative_elo_delta < 0)
            or (result_key == "loss" and authoritative_elo_delta > 0)
        )
        if contradicts_result:
            logger.warning(
                "Rating delta contradicts terminal winner for %s: result=%s delta=%+d",
                battle_tag,
                result_key,
                authoritative_elo_delta,
            )

    if result_key == "tie":
        result_word = "TIE"
        emoji = "ðŸ¤"
    elif result_key == "win":
        result_word = "WIN"
        emoji = "âš”ï¸"
    else:
        result_word = "LOSS"
        emoji = "ðŸ’€"

    # ELO delta
    if elo_after is not None and elo_before is not None:
        elo_str = format_elo_delta(
            elo_before,
            elo_after,
            result_word.lower(),
            rating_delta=authoritative_elo_delta,
        )
    elif elo_after is not None:
        elo_str = f"ELO now {elo_after:.0f}"
    else:
        elo_str = ""

    line1 = f"{emoji} **{result_word}** vs {opponent_name}"
    if elo_str:
        line1 += f" ({elo_str})"

    # --- Line 2: Team + turns ---
    team_display = ""
    if team_name and team_name != "gen9ou":
        # Shorten long team names for display
        short_name = team_name.split("/")[-1] if "/" in team_name else team_name
        team_display = f"Team: {short_name}"
    turn_display = f"Turns: {turn_count}" if turn_count else ""
    line2_parts = [p for p in [team_display, turn_display] if p]
    line2 = ("ðŸ† " + " | ".join(line2_parts)) if line2_parts else ""

    # --- Line 3: Replay link ---
    replay_line = ""
    replay_ref = replay_url or battle_tag
    replay_id = public_replay_id_candidate(replay_ref)
    if replay_id and await _replay_exists(replay_id):
        replay_line = f"ðŸ”— <https://replay.pokemonshowdown.com/{replay_id}>"
    elif replay_url:
        replay_line = "ðŸ”— Replay pending public upload"

    # Assemble message
    lines = [line1]
    if line2:
        lines.append(line2)
    if replay_line:
        lines.append(replay_line)
    message = "\n".join(lines)

    if battle_result_event_queue_enabled():
        logger.info(
            "Structured event queue owns the DEKU Discord report for %s",
            battle_tag,
        )
    else:
        logger.warning(
            "Battle result queue is disabled for %s; withholding Discord delivery instead of "
            "bypassing the DEKU authority path",
            battle_tag,
        )
    return elo_after


async def _enrich_battle_stats_rating_once(
    battle_tag: str,
    *,
    elo_before: float | None,
    elo_after: float | None,
    rating_delta: int | None,
    result_key: str | None = None,
    winner: str | None = None,
    opponent_name: str | None = None,
    replay_url: str | None = None,
    path: Path | None = None,
) -> bool:
    """Fill battle_stats rows with authoritative result/ELO/reporting data.

    run.py owns the append path and keeps an in-memory battle list. Its next
    save can overwrite fields this async helper previously added to disk, so
    each enrichment pass reapplies all authoritative facts captured by this
    process, not only the just-finished battle.
    """
    if not battle_tag or (elo_after is None and not result_key):
        return False
    stats_path = path or BATTLE_STATS_PATH
    async with _battle_stats_enrichment_lock:
        if not stats_path.exists():
            return False
        try:
            data = json.loads(stats_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Could not read battle_stats for rating enrichment: %s", exc)
            return False

        if isinstance(data, dict):
            battles = data.get("battles")
        elif isinstance(data, list):
            battles = data
        else:
            battles = None
        if not isinstance(battles, list):
            return False

        fact = _battle_stats_authoritative_facts.setdefault(battle_tag, {})
        if result_key:
            fact["result"] = str(result_key)
        if elo_before is not None:
            fact["elo_before"] = float(elo_before)
        if elo_after is not None:
            fact["elo_after"] = float(elo_after)
        if rating_delta is not None:
            fact["rating_delta"] = int(rating_delta)
            fact["rating_source"] = "showdown_raw"
        elif elo_after is not None:
            fact.setdefault("rating_source", "ladder_api")
        if winner:
            fact["winner"] = str(winner)
        if opponent_name and opponent_name != "Unknown":
            fact["opponent"] = str(opponent_name)
        if replay_url:
            canonical_url = canonical_replay_url(replay_url)
            if canonical_url:
                fact["replay_url"] = canonical_url
                fact["replay_status"] = "public"
            else:
                pending_id = public_replay_id_candidate(replay_url)
                if pending_id:
                    fact["public_replay_id"] = pending_id
                    fact["replay_status"] = "pending-public-upload"

        target = None
        known_ids = set(_battle_stats_authoritative_facts)
        for entry in battles:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("battle_id") or entry.get("replay_id") or "")
            entry_replay = str(entry.get("replay_id") or "")
            matched_id = entry_id if entry_id in known_ids else entry_replay
            entry_fact = _battle_stats_authoritative_facts.get(matched_id)
            if entry_fact:
                fact_result = entry_fact.get("result")
                if fact_result in {"win", "loss", "tie", "disconnect"}:
                    entry["result"] = fact_result
                if entry_fact.get("elo_after") is not None:
                    entry["rating"] = float(entry_fact["elo_after"])
                    entry["elo_after"] = float(entry_fact["elo_after"])
                if entry_fact.get("elo_before") is not None:
                    entry["elo_before"] = float(entry_fact["elo_before"])
                if entry_fact.get("rating_delta") is not None:
                    entry["rating_delta"] = int(entry_fact["rating_delta"])
                    entry["rating_source"] = str(entry_fact.get("rating_source") or "showdown_raw")
                elif entry_fact.get("rating_source") and entry.get("rating_source") is None:
                    entry["rating_source"] = str(entry_fact["rating_source"])
                if entry.get("battle_tag") is None:
                    entry["battle_tag"] = matched_id
                if entry_fact.get("winner"):
                    entry["winner"] = str(entry_fact["winner"])
                if entry_fact.get("opponent"):
                    entry["opponent"] = str(entry_fact["opponent"])
                if entry_fact.get("replay_url"):
                    entry["replay_url"] = str(entry_fact["replay_url"])
                if entry_fact.get("public_replay_id"):
                    entry["public_replay_id"] = str(entry_fact["public_replay_id"])
                if entry_fact.get("replay_status"):
                    entry["replay_status"] = str(entry_fact["replay_status"])
            if entry_id == battle_tag or entry_replay == battle_tag:
                target = entry
        if target is None:
            return False

        try:
            stats_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("Could not write enriched battle_stats rating: %s", exc)
            return False
    logger.info(
        "Enriched battle_stats result/rating/reporting for %s: result=%s after=%s delta=%s",
        battle_tag,
        result_key,
        elo_after,
        rating_delta,
    )
    return True


async def _enrich_battle_stats_rating_after_record(
    battle_tag: str,
    *,
    elo_before: float | None,
    elo_after: float | None,
    rating_delta: int | None,
    result_key: str | None = None,
    winner: str | None = None,
    opponent_name: str | None = None,
    replay_url: str | None = None,
    attempts: int = 30,
    delay_seconds: float = 0.5,
) -> bool:
    """Wait for run.py to append the stats row, then add exact reporting fields."""
    for attempt in range(max(1, attempts)):
        if await _enrich_battle_stats_rating_once(
            battle_tag,
            elo_before=elo_before,
            elo_after=elo_after,
            rating_delta=rating_delta,
            result_key=result_key,
            winner=winner,
            opponent_name=opponent_name,
            replay_url=replay_url,
        ):
            return True
        if attempt + 1 < attempts:
            await asyncio.sleep(delay_seconds)
    logger.info("battle_stats row not found for ELO enrichment: %s", battle_tag)
    return False


def _schedule_battle_stats_rating_enrichment(
    battle_tag: str,
    *,
    elo_before: float | None,
    elo_after: float | None,
    rating_delta: int | None,
    result_key: str | None = None,
    winner: str | None = None,
    opponent_name: str | None = None,
    replay_url: str | None = None,
) -> None:
    if not battle_tag or (elo_after is None and not result_key):
        return
    try:
        asyncio.create_task(
            _enrich_battle_stats_rating_after_record(
                battle_tag,
                elo_before=elo_before,
                elo_after=elo_after,
                rating_delta=rating_delta,
                result_key=result_key,
                winner=winner,
                opponent_name=opponent_name,
                replay_url=replay_url,
            )
        )
    except RuntimeError:
        logger.debug("No running loop for battle_stats ELO enrichment: %s", battle_tag)


async def prime_resume_battles() -> int:
    """Load in-progress battles from active_battles.json so workers can resume them."""
    if not RESUME_ACTIVE_BATTLES:
        return 0

    data = read_active_battles()
    battles = data.get("battles", [])
    if not battles:
        return 0

    count = 0
    skipped = 0
    now = datetime.now()
    async with _resume_lock:
        _resume_by_worker.clear()
        _resume_queue.clear()
        for battle in battles:
            battle_id = battle.get("id")
            if not battle_id:
                continue
            clean_id = re.sub(r"[^a-zA-Z0-9-]", "", battle_id)
            if not clean_id:
                continue
            opponent = battle.get("opponent")
            if opponent == "Unknown":
                opponent = None
            worker_id = battle.get("worker_id")

            started = _parse_started_ts(battle.get("started"))
            if started and RESUME_MAX_AGE_SEC > 0:
                age = (now - started).total_seconds()
                if age > RESUME_MAX_AGE_SEC:
                    skipped += 1
                    continue

            entry = {
                "id": clean_id,
                "opponent": opponent,
                "worker_id": worker_id,
                "started": started,
            }
            if worker_id is not None:
                try:
                    wid = int(worker_id)
                except (TypeError, ValueError):
                    wid = None
                if wid is not None:
                    _resume_by_worker.setdefault(wid, []).append(entry)
                else:
                    _resume_queue.append(entry)
            else:
                _resume_queue.append(entry)

            count += 1

    if count or skipped:
        # Clear any stale active_battles.json entries on startup; resumes
        # will be re-registered once confirmed with live messages.
        await update_active_battles_file()
        logger.info(
            f"Primed {count} resumable battle(s) from active_battles.json"
            + (f" (skipped {skipped} stale)" if skipped else "")
        )
    return count


async def has_resume_battle(worker_id: int | None = None) -> bool:
    async with _resume_lock:
        if worker_id is not None and _resume_by_worker.get(worker_id):
            return True
        return bool(_resume_queue)


async def get_resume_pending_count() -> int:
    async with _resume_lock:
        return sum(len(v) for v in _resume_by_worker.values()) + len(_resume_queue)


async def get_resume_battle_ids() -> set[str]:
    async with _resume_lock:
        ids: set[str] = set()
        for entry in _resume_queue:
            battle_id = entry.get("id")
            if battle_id:
                ids.add(str(battle_id))
        for entries in _resume_by_worker.values():
            for entry in entries:
                battle_id = entry.get("id")
                if battle_id:
                    ids.add(str(battle_id))
        return ids


def _resume_message_indicates_active(msg: str) -> bool:
    if "|request|" in msg:
        return True
    for token in ("|turn|", "|move|", "|switch|", "|drag|", "|replace|", "|inactive|"):
        if token in msg:
            return True
    return False


async def _requeue_resume_entry(resume_entry: dict, reason: str) -> None:
    battle_id = resume_entry.get("id")
    if not battle_id:
        return
    worker_id = resume_entry.get("worker_id")
    async with _resume_lock:
        if worker_id is not None:
            try:
                wid = int(worker_id)
            except (TypeError, ValueError):
                wid = None
            if wid is not None:
                _resume_by_worker.setdefault(wid, []).append(resume_entry)
            else:
                _resume_queue.append(resume_entry)
        else:
            _resume_queue.append(resume_entry)
    logger.info("Requeued resume battle %s (%s)", battle_id, reason)


async def _attempt_resume_battle(
    ps_websocket_client: PSWebsocketClient,
    battle_tag: str,
    opponent_hint: str | None = None,
) -> tuple[str, str | None, str]:
    """Join a battle room and confirm it is active before resuming."""
    try:
        await ps_websocket_client.register_battle(battle_tag)
    except Exception:
        pass
    try:
        await ps_websocket_client.join_room(battle_tag)
    except Exception:
        pass

    buffered: list[str] = []
    opponent_name = opponent_hint
    deadline = time.time() + RESUME_JOIN_TIMEOUT_SEC if RESUME_JOIN_TIMEOUT_SEC > 0 else None

    while True:
        try:
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                msg = await asyncio.wait_for(
                    ps_websocket_client.receive_battle_message(battle_tag),
                    timeout=remaining,
                )
            else:
                msg = await ps_websocket_client.receive_battle_message(battle_tag)
        except asyncio.TimeoutError:
            logger.warning("Resume timeout waiting for battle messages: %s", battle_tag)
            try:
                ps_websocket_client.unregister_battle(battle_tag)
            except Exception:
                pass
            if battle_tag in _active_battles:
                _log_battle_removal(battle_tag, "resume_timeout")
                del _active_battles[battle_tag]
                await update_active_battles_file()
            return battle_tag, opponent_name, "timeout"

        buffered.append(msg)

        if battle_room_closed(battle_tag, msg) or battle_is_finished(battle_tag, msg):
            logger.info("Resume drop: battle already closed/finished %s", battle_tag)
            # Blacklist to prevent search loop from re-claiming this dead battle
            _blacklist_battle_tag(battle_tag)
            logger.info(f"Blacklisted resumed-but-closed battle: {battle_tag} (blacklist size: {len(_dead_battle_blacklist)})")
            try:
                ps_websocket_client.unregister_battle(battle_tag)
            except Exception:
                pass
            try:
                await ps_websocket_client.leave_battle(battle_tag)
            except Exception:
                pass
            if battle_tag in _active_battles:
                _log_battle_removal(battle_tag, "resume_closed")
                del _active_battles[battle_tag]
                await update_active_battles_file()
            return battle_tag, opponent_name, "closed"

        if not opponent_name:
            opponent_name = _extract_opponent_from_message(
                msg,
                getattr(ps_websocket_client, "username", None),
            )

        if _resume_message_indicates_active(msg):
            queue = ps_websocket_client.battle_queues.get(battle_tag)
            if queue:
                for buffered_msg in buffered:
                    queue.put_nowait(buffered_msg)

            # Re-assert the spectator invite before re-publishing the battle
            # as active. The exactly-once guard makes this a no-op when this
            # process already invited; after a restart it restores spectator
            # access without double-sending within one runtime.
            try:
                await ensure_spectator_invited(ps_websocket_client, battle_tag)
            except Exception:
                pass

            info = _active_battles.get(battle_tag, {})
            info["status"] = "active"
            info.pop("resume_pending", None)
            _active_battles[battle_tag] = info
            await update_active_battles_file()
            logger.info("Resume confirmed active: %s", battle_tag)
            return battle_tag, opponent_name, "ok"

    return battle_tag, opponent_name, "timeout"


async def _claim_resume_battle(worker_id: int | None = None) -> dict | None:
    async with _resume_lock:
        if worker_id is not None:
            per_worker = _resume_by_worker.get(worker_id)
            if per_worker:
                return per_worker.pop(0)
        if _resume_queue:
            return _resume_queue.pop(0)
    return None


def get_active_battle_count():
    return sum(
        1 for info in _active_battles.values()
        if info.get("status", "active") == "active"
    )


def _log_battle_removal(battle_tag: str, reason: str):
    """Log every removal from _active_battles so we can trace ghost disappearances."""
    remaining = [bid for bid in _active_battles if bid != battle_tag]
    logger.info(
        "TRACKING: removed %s (reason: %s) | remaining: %d entries %s",
        battle_tag, reason, len(remaining), remaining,
    )
    # Mark as concluded so heartbeat never re-registers it
    _concluded_battles[battle_tag] = time.time()
    _concluded_battles.move_to_end(battle_tag)
    while len(_concluded_battles) > _CONCLUDED_BATTLES_MAX:
        _concluded_battles.popitem(last=False)  # FIFO: evict oldest, not arbitrary


def replay_handoff_fields(
    *,
    battle_tag: str | None,
    replay_url: str | None,
    verified_replay_url: str | None = None,
    save_replay_requested: bool = False,
) -> dict[str, object]:
    """Preserve replay evidence even when public upload verification lags."""

    verified_url = canonical_replay_url(verified_replay_url) or None
    candidate_url = verified_url or canonical_replay_url(replay_url) or None
    replay_id = public_replay_id_candidate(
        verified_url
        or replay_url
        or (battle_tag if save_replay_requested else None)
    ) or None
    verified = bool(verified_url)
    if verified:
        status = "public"
    elif replay_id or candidate_url:
        status = "pending-public-upload"
    else:
        status = "absent"
    return {
        "replay_id": replay_id,
        "replay_url": candidate_url,
        "replay_status": status,
        "replay_public_verified": verified,
        "raw_replay_url": replay_url,
        "verified_replay_url": verified_replay_url,
    }


def battle_result_event_queue_enabled() -> bool:
    """Return False for offline eval runs unless queue proof is explicitly requested."""
    raw = os.getenv("FOULER_BATTLE_RESULT_QUEUE", "").strip().lower()
    if raw:
        return raw not in {"0", "false", "no", "off"}

    offline_eval = os.getenv("FOULER_OFFLINE_EVAL", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not offline_eval:
        return True

    override = os.getenv("FOULER_OFFLINE_EVAL_QUEUE_EVENTS", "").strip().lower()
    return override in {"1", "true", "yes", "on"}


async def _handoff_battle_result_to_deku(event_id: str | None) -> bool:
    """Move one queued battle result into DEKU's outbox without network authority."""

    if not event_id:
        logger.error("Battle result was not journaled; DEKU handoff cannot run")
        return False
    async with _deku_event_drain_lock:
        result = await asyncio.to_thread(
            process_pending_events,
            max_events=50,
            required_event_id=event_id,
        )
    if not result.get("ok"):
        logger.error(
            "DEKU handoff failed for battle event %s: status=%s pending=%s",
            event_id,
            result.get("requiredStatus"),
            result.get("pendingRemaining"),
        )
        return False
    logger.info("Queued battle result %s for the DEKU relay", event_id)
    return True


async def update_active_battles_file():
    """Write active battles to JSON file for stream overlay integration.

    Slot assignment priority:
    1) Worker-based slot (worker_id + 1) when available.
    2) Oldest-first order for any battles without worker_id.
    """
    async with _battles_lock:
        # Drop stale entries after grace period to avoid permanent ghosts
        if STALE_DISPLAY_GRACE_SEC > 0:
            cutoff = time.time() - STALE_DISPLAY_GRACE_SEC
            stale_tags = [
                bid for bid, info in _active_battles.items()
                if info.get("status") == "stale"
                and info.get("stale_since") is not None
                and info.get("stale_since") < cutoff
            ]
            for bid in stale_tags:
                _log_battle_removal(bid, f"stale_grace_expired ({STALE_DISPLAY_GRACE_SEC}s)")
                _active_battles.pop(bid, None)

        # Hard age-based ghost cleanup: remove ANY battle older than max age,
        # regardless of status. Catches entries that slip through normal
        # finalization (hung websocket, missed |win| message, etc.).
        # Marking as concluded prevents the heartbeat from re-registering.
        if GHOST_BATTLE_MAX_AGE_SEC > 0:
            now_gc = time.time()
            ghost_tags = [
                bid for bid, info in _active_battles.items()
                if isinstance(info.get("started"), datetime)
                and (now_gc - info["started"].timestamp()) > GHOST_BATTLE_MAX_AGE_SEC
            ]
            for bid in ghost_tags:
                _log_battle_removal(bid, f"ghost_max_age ({GHOST_BATTLE_MAX_AGE_SEC}s)")
                _active_battles.pop(bid, None)

        battles = []
        for bid, info in _active_battles.items():
            # Clean ID - ensure no whitespace or hidden characters
            clean_id = re.sub(r'[^a-zA-Z0-9-]', '', bid)
            # Use short battle ID format - works for both local and cross-machine spectator viewing
            url = f"https://play.pokemonshowdown.com/{clean_id}"
            started = info.get("started")
            worker_id = info.get("worker_id")
            status = info.get("status", "active")
            battles.append({
                "id": clean_id,
                "opponent": info.get("opponent", "Unknown"),
                "url": url,
                "started": started.isoformat() if started else None,
                "worker_id": worker_id,
                "_sort_key": started or datetime.min,  # For sorting
                "status": status,
                "players": [FoulPlayConfig.username, info.get("opponent", "Unknown")],
            })

        # Sort by start time (oldest first) for consistent fallback ordering
        battles.sort(key=lambda b: b["_sort_key"])

        # Assign slot numbers (prefer worker mapping)
        used_slots = set()
        for battle in battles:
            worker_id = battle.get("worker_id")
            if worker_id is None:
                continue
            try:
                slot = int(worker_id) + 1  # 1-indexed slots
            except (TypeError, ValueError):
                continue
            if slot <= 0:
                continue
            battle["slot"] = slot
            used_slots.add(slot)

        next_slot = 1
        for battle in battles:
            if "slot" in battle:
                continue
            while next_slot in used_slots:
                next_slot += 1
            battle["slot"] = next_slot
            used_slots.add(next_slot)
            next_slot += 1

        # Remove sort key before writing
        for battle in battles:
            battle.pop("_sort_key", None)

        data = {
            "battles": battles,
            "count": len(battles),
            "max_slots": max(FoulPlayConfig.max_concurrent_battles, expected_battle_surfaces()),
            "updated": datetime.now().isoformat(),
        }

        now = time.time()
        payload_key = json.dumps(data, sort_keys=True)
        global _last_active_battles_write, _last_active_battles_payload
        if (
            payload_key == _last_active_battles_payload
            and (now - _last_active_battles_write) < ACTIVE_BATTLES_WRITE_INTERVAL_SEC
        ):
            return

        try:
            write_active_battles(data)
            _last_active_battles_write = now
            _last_active_battles_payload = payload_key
            logger.debug(f"Updated active_battles.json: {len(battles)} battles")
        except Exception as e:
            logger.error(f"Failed to write active_battles.json: {e}")

def _validated_loopback_stream_event_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        return ""
    return url


async def send_stream_event(event_type, payload):
    """Send a real-time event signal to the stream server."""
    stream_events = os.getenv("FOULER_STREAM_EVENTS", "").strip().lower()
    if stream_events in {"0", "false", "no", "off"}:
        logger.debug("Stream event %s skipped: FOULER_STREAM_EVENTS=%s", event_type, stream_events)
        return {"skipped": True, "reason": "stream-events-disabled"}
    if os.getenv("FOULER_OFFLINE_EVAL", "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.debug("Stream event %s skipped for offline eval", event_type)
        return {"skipped": True, "reason": "offline-eval"}
    configured_url = os.getenv("STREAM_EVENT_URL", "http://localhost:8777/event").strip()
    if not configured_url:
        logger.debug("Stream event %s skipped: STREAM_EVENT_URL is empty", event_type)
        return {"skipped": True, "reason": "stream-event-url-empty"}
    url = _validated_loopback_stream_event_url(configured_url)
    if not url:
        logger.warning("Stream event %s skipped: STREAM_EVENT_URL must be loopback HTTP(S)", event_type)
        return {"skipped": True, "reason": "stream-event-url-not-loopback"}
    for attempt in range(3):  # Try 3 times: initial + 2 retries
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json={"type": event_type, "payload": payload}) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        logger.warning(f"Stream event {event_type} returned status {resp.status}")
        except asyncio.TimeoutError:
            if attempt < 2:
                logger.debug(f"Stream event {event_type} timeout (attempt {attempt+1}/3), retrying...")
                await asyncio.sleep(1)
            else:
                logger.error(f"Stream event {event_type} failed after 3 attempts (timeout)")
        except aiohttp.ClientConnectorError as e:
            if attempt < 2:
                logger.debug(f"Stream server not available for {event_type} (attempt {attempt+1}/3): {e}")
                await asyncio.sleep(1)
            else:
                logger.error(f"Stream server unreachable for {event_type} after 3 attempts: {e}")
        except Exception as e:
            if attempt < 2:
                logger.debug(f"Stream event {event_type} failed (attempt {attempt+1}/3), retrying: {e}")
                await asyncio.sleep(1)
            else:
                # Final failure after retries
                logger.error(f"Stream event {event_type} failed after 3 attempts: {e}")


def format_decision(battle, decision):
    # Formats a decision for communication with Pokemon-Showdown
    # If the move can be used as a Z-Move, it will be

    # Final safety: if force_switch is active, the decision MUST be a switch
    if battle.force_switch and not decision.startswith(constants.SWITCH_STRING + " "):
        logger.error(
            "format_decision called with move '%s' during force_switch! "
            "This should have been caught earlier.",
            decision,
        )
        # Pick any alive reserve Pokemon
        for pkmn in battle.user.reserve:
            if pkmn.hp > 0:
                decision = "{} {}".format(constants.SWITCH_STRING, pkmn.name)
                logger.warning("Emergency switch override to: %s", pkmn.name)
                break

    if decision.startswith(constants.SWITCH_STRING + " "):
        switch_pokemon = decision.split("switch ")[-1]
        for pkmn in battle.user.reserve:
            if pkmn.name == switch_pokemon:
                message = "/switch {}".format(pkmn.index)
                break
        else:
            raise ValueError("Tried to switch to: {}".format(switch_pokemon))
    else:
        tera = False
        mega = False
        if decision.endswith("-tera"):
            decision = decision.replace("-tera", "")
            tera = True
        elif decision.endswith("-mega"):
            decision = decision.replace("-mega", "")
            mega = True
        message = "/choose move {}".format(decision)

        if battle.user.active and battle.user.active.can_mega_evo and mega:
            message = "{} {}".format(message, constants.MEGA)
        elif battle.user.active and battle.user.active.can_ultra_burst:
            message = "{} {}".format(message, constants.ULTRA_BURST)

        # only dynamax on last pokemon
        if battle.user.active and battle.user.active.can_dynamax and all(
            p.hp == 0 for p in battle.user.reserve
        ):
            message = "{} {}".format(message, constants.DYNAMAX)

        if tera:
            if battle.user.active.can_terastallize:
                message = "{} {}".format(message, constants.TERASTALLIZE)
            else:
                logger.warning("Tera requested but unavailable; sending move without Tera")

        move_obj = battle.user.active.get_move(decision) if battle.user.active else None
        if move_obj and move_obj.can_z:
            message = "{} {}".format(message, constants.ZMOVE)

    return [message, str(battle.rqid)]


def battle_is_finished(battle_tag, msg):
    return (
        msg.startswith(">{}".format(battle_tag))
        and (constants.WIN_STRING in msg or constants.TIE_STRING in msg)
        and constants.CHAT_STRING not in msg
    )

def battle_room_closed(battle_tag, msg):
    """Return True if the battle room closed without a win/tie (deinit/expire/noinit)."""
    if not msg.startswith(">{}".format(battle_tag)):
        return False
    return any(token in msg for token in ["|deinit|", "|expire|", "|noinit|"])


def extract_battle_factory_tier_from_msg(msg):
    start = msg.find("Battle Factory Tier: ") + len("Battle Factory Tier: ")
    end = msg.find("</b>", start)
    tier_name = msg[start:end]

    return normalize_name(tier_name)


async def async_pick_move(battle):
    battle_copy = deepcopy(battle)
    setattr(battle_copy, "_isolation_copy", True)
    if not battle_copy.team_preview:
        try:
            battle_copy.user.update_from_request_json(battle_copy.request_json)
        except Exception as e:
            logger.warning(f"Failed to update battle copy from request_json: {e}")

    loop = asyncio.get_event_loop()
    best_move = None
    trace = None
    trace_reason = None
    try:
        # Run move search in the default executor, but enforce a hard timeout.
        # This prevents rare hangs from stalling the battle loop indefinitely.
        future = loop.run_in_executor(None, find_best_move, battle_copy)
        timeout = DECISION_TIMEOUT_SEC
        try:
            opp = battle_copy.opponent.active
            if opp is not None:
                boosts = getattr(opp, "boosts", {}) or {}
                if boosts.get(constants.ATTACK, 0) > 0 or boosts.get(constants.SPECIAL_ATTACK, 0) > 0:
                    timeout = max(timeout, int(DECISION_TIMEOUT_SEC * 1.5))
        except Exception:
            pass
        # BACKSTOP: find_best_move now self-limits to a clock-aware budget, but
        # asyncio.wait_for cannot stop the underlying executor thread, so a single
        # misbehaving decision can keep a thread busy. Bind this outer timeout to
        # the remaining SIDE CLOCK so the fallback move is ALWAYS submitted well
        # before the inactivity forfeit -- even if the inner budget is bypassed.
        tr = battle_copy.time_remaining
        if tr is not None:
            clock_cap = max(2, int(tr) - 6)
            timeout = min(timeout, clock_cap)
            if tr < 30:
                timeout = min(timeout, max(3, int(tr * 0.5)))
        else:
            # Unknown clock -> stay conservative, never the full 25-37s.
            timeout = min(timeout, 12)
        timeout = max(2, timeout)
        if timeout > 0:
            best_move = await asyncio.wait_for(future, timeout=timeout)
        else:
            best_move = await future
        if isinstance(best_move, tuple) and len(best_move) == 2:
            best_move, trace = best_move
    except asyncio.TimeoutError:
        logger.warning(
            "Decision timeout after %ss (clock-bound) - using fallback move.",
            timeout,
        )
        best_move = _fallback_decision(battle_copy)
        trace_reason = "timeout"
    except Exception as e:
        logger.error(f"MCTS error: {e}", exc_info=True)
        logger.debug("Falling back to safe move selection")
        best_move = _fallback_decision(battle_copy)
        trace_reason = "error"

    if not best_move:
        best_move = _fallback_decision(battle_copy)
        trace_reason = trace_reason or "fallback"

    # === STRATEGIC LAYER INTEGRATION ===
    # Log archetype detection (full move selection integration in next phase)
    try:
        strategic_layer = StrategicDecisionLayer()
        battle_tag = battle_copy.battle_tag
        
        # Convert Pokemon objects to dicts for archetype analyzer
        def pokemon_to_dict(pokemon):
            if pokemon is None:
                return None
            # Extract move names - ensure all are strings
            move_names = []
            if pokemon.moves:
                for move in pokemon.moves:
                    try:
                        if isinstance(move, str):
                            move_names.append(move.strip() if move else "")
                        elif hasattr(move, 'name'):
                            move_names.append(str(move.name).strip() if move.name else "")
                        else:
                            move_str = str(move).strip() if move else ""
                            # Try to extract move name from __repr__ or similar
                            if move_str.startswith("<") and ">" in move_str:
                                # Likely a Move object repr, skip
                                continue
                            move_names.append(move_str)
                    except Exception as e:
                        logger.debug(f"Failed to extract move: {e}")
                        continue
            
            return {
                "name": pokemon.name,
                "species": pokemon.name,  # Pokemon.name is already normalized species name
                "types": list(pokemon.types) if pokemon.types else [],
                "hp": pokemon.hp,
                "max_hp": pokemon.max_hp,
                "moves": [m for m in move_names if m],  # Filter out empty strings
                "ability": pokemon.ability or "unknown",
                "item": pokemon.item or "unknown",
            }
        
        team_data = []
        if battle_copy.user.active:
            team_data.append(pokemon_to_dict(battle_copy.user.active))
        for p in battle_copy.user.reserve:
            if p is not None:
                team_data.append(pokemon_to_dict(p))
        
        # Initialize strategic layer for this battle
        archetype, gameplan = strategic_layer.initialize_for_battle(battle_tag, team_data)
        
        # Log archetype for analysis
        logger.info(f"[STRATEGIC] Archetype={archetype.archetype}, Confidence={archetype.confidence:.2f}")
        logger.info(f"[STRATEGIC] Win Condition: {archetype.primary_win_condition}")
        
        if trace is not None:
            trace["strategic"] = {
                "archetype": archetype.archetype,
                "confidence": archetype.confidence,
                "win_condition": archetype.primary_win_condition,
                "engine_choice": best_move,
            }
    except Exception as e:
        logger.warning(f"Strategic layer initialization failed: {e}")
        if trace is not None:
            trace["strategic"] = {
                "status": "error",
                "reason": str(e),
            }

    # Optional hybrid rerank: engine proposes candidates, LLM reranks among them.
    if FoulPlayConfig.decision_policy == "hybrid" and best_move:
        try:
            engine_move = best_move
            hybrid_result = await run_hybrid_rerank(
                battle=battle_copy,
                engine_choice=engine_move,
                trace=trace,
                api_key=FoulPlayConfig.openai_api_key or "",
                model=FoulPlayConfig.openai_model,
                api_base=FoulPlayConfig.openai_api_base,
                timeout_sec=FoulPlayConfig.llm_timeout_sec,
                top_k=FoulPlayConfig.llm_rerank_top_k,
            )

            if hybrid_result.decision and hybrid_result.decision != engine_move:
                logger.info(
                    "Hybrid rerank override: %s -> %s",
                    engine_move,
                    hybrid_result.decision,
                )
                best_move = hybrid_result.decision

            hybrid_meta = (
                dict(hybrid_result.metadata)
                if isinstance(hybrid_result.metadata, dict)
                else {}
            )
            if hybrid_meta:
                hybrid_meta.setdefault("engine_choice", engine_move)
                hybrid_meta.setdefault("selected_decision", best_move)
                hybrid_meta.setdefault(
                    "override",
                    bool(
                        hybrid_result.decision
                        and hybrid_result.decision != engine_move
                    ),
                )

            if TRACE_DECISIONS and trace is None and hybrid_result.metadata:
                trace = build_trace_base(battle_copy, reason=trace_reason or "hybrid")
                trace["choice"] = best_move
            if trace is not None and hybrid_meta:
                trace["hybrid"] = hybrid_meta
        except Exception as e:
            logger.warning(f"Hybrid rerank failed; using engine choice: {e}")
            if TRACE_DECISIONS and trace is not None:
                trace["hybrid"] = {
                    "status": "error",
                    "reason": f"exception:{e}",
                }

    # Safety check: if force_switch is active but MCTS returned a move, override with a switch
    if battle.force_switch and not best_move.startswith(constants.SWITCH_STRING + " "):
        logger.warning(
            "force_switch is active but MCTS returned move '%s' - forcing a switch",
            best_move,
        )
        best_move = _get_best_switch(battle_copy)
        if trace is not None:
            trace["choice_override"] = "force_switch"
            trace["choice"] = best_move

    if TRACE_DECISIONS and trace is None:
        trace = build_trace_base(battle_copy, reason=trace_reason or "fallback")
        trace["choice"] = best_move
    if TRACE_DECISIONS and trace is not None and trace_reason in {"timeout", "error", "fallback"}:
        trace["fallback"] = {
            "policy": "request_legal_emergency_score",
            "reason": trace_reason,
            "truth_source": (
                "showdown_request_legal_options"
                if _request_legal_move_ids(battle_copy)
                else "battle_state_last_resort"
            ),
        }

    _action_str = best_move.removesuffix("-tera").removesuffix("-mega")
    battle.user.last_selected_move = LastUsedMove(
        battle.user.active.name
        if battle.user.active
        else (battle_copy.user.active.name if battle_copy.user.active else ""),
        _action_str,
        battle.turn,
    )
    # Record action in rolling history for repetition detection (keep last 10)
    battle.user.action_history.append(_action_str)
    if len(battle.user.action_history) > 10:
        battle.user.action_history = battle.user.action_history[-10:]
    formatted = format_decision(battle_copy, best_move)
    if TRACE_DECISIONS and trace is not None:
        trace["formatted_choice"] = formatted
        write_decision_trace(trace)
    return formatted


def _get_best_switch(battle, legal_switch_slots: set[int] | None = None):
    """Pick the best available switch-in when forced to switch."""
    alive_reserves = [
        p
        for p in battle.user.reserve
        if p.hp > 0
        and (
            not legal_switch_slots
            or getattr(p, "index", None) in legal_switch_slots
        )
    ]
    if alive_reserves:
        opponent = battle.opponent.active
        threat_category = None
        if opponent is not None:
            try:
                threat_category = get_threat_category(opponent.name)
            except Exception:
                threat_category = None

        def score_switch(pkmn):
            hp_ratio = pkmn.hp / max(pkmn.max_hp, 1)
            score = hp_ratio
            if opponent is None:
                return score

            # Defensive matchup: how well do we take their STABs?
            opp_types = opponent.types if opponent.types else []
            if opp_types:
                worst = max(
                    type_effectiveness_modifier(t, pkmn.types) for t in opp_types
                )
                score += (2.0 - min(worst, 2.0))  # lower damage -> higher score

            # Offensive matchup: do our STABs hit them?
            our_types = pkmn.types if pkmn.types else []
            if our_types:
                best_off = max(
                    type_effectiveness_modifier(t, opponent.types) for t in our_types
                )
                score += best_off

            # Bulk preference based on observed threat category
            if threat_category == ThreatCategory.PHYSICAL_ONLY:
                score += pkmn.stats[constants.DEFENSE] / 200.0
            elif threat_category == ThreatCategory.SPECIAL_ONLY:
                score += pkmn.stats[constants.SPECIAL_DEFENSE] / 200.0
            else:
                score += (pkmn.stats[constants.DEFENSE] + pkmn.stats[constants.SPECIAL_DEFENSE]) / 400.0

            return score

        best = max(alive_reserves, key=score_switch)
        logger.info("Force-switch fallback: switching to %s", best.name)
        return "{} {}".format(constants.SWITCH_STRING, best.name)
    # Should never reach here if there are alive reserves, but just in case
    raise ValueError("No alive Pokemon to switch to during force_switch")


def _fallback_decision(battle):
    """Pick a cheap legal emergency decision if the normal engine fails."""
    try:
        if battle.force_switch:
            return _get_best_switch(battle, _request_legal_switch_slots(battle))

        legal_moves = _request_legal_move_ids(battle)
        if legal_moves:
            scored = [
                (_score_fallback_move(battle, move_id), index, move_id)
                for index, move_id in enumerate(legal_moves)
            ]
            scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
            return scored[0][2]

        if battle.user and battle.user.active:
            available_moves = [
                mv.name
                for mv in battle.user.active.moves
                if not getattr(mv, "disabled", False) and getattr(mv, "current_pp", 1) > 0
            ]
            if available_moves:
                scored = [
                    (_score_fallback_move(battle, move_name), index, move_name)
                    for index, move_name in enumerate(available_moves)
                ]
                scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                return scored[0][2]
            alive_reserves = [p for p in battle.user.reserve if p.hp > 0]
            if alive_reserves:
                return _get_best_switch(battle)
    except Exception as e:
        logger.warning(f"Fallback decision failed: {e}")

    # Last resort: splash (no-op). Only used if we truly have nothing else.
    return constants.DO_NOTHING_MOVE


def _request_legal_move_ids(battle) -> list[str]:
    request = getattr(battle, "request_json", None) or {}
    active = request.get(constants.ACTIVE, [])
    if not active:
        return []

    legal: list[str] = []
    for move in active[0].get(constants.MOVES, []):
        if move.get(constants.DISABLED, False):
            continue
        if move.get(constants.PP, 1) == 0:
            continue
        move_id = move.get(constants.ID) or normalize_name(move.get("move", ""))
        if not move_id:
            continue
        if move_id == constants.HIDDEN_POWER:
            move_id = normalize_name(move.get("move", move_id))
        legal.append(move_id)
    return legal


def _request_legal_switch_slots(battle) -> set[int]:
    request = getattr(battle, "request_json", None) or {}
    side = request.get(constants.SIDE, {})
    side_pokemon = side.get(constants.POKEMON, [])
    legal: set[int] = set()
    for index, pkmn in enumerate(side_pokemon, start=1):
        if pkmn.get(constants.ACTIVE, False):
            continue
        condition = str(pkmn.get(constants.CONDITION, "")).lower()
        if "fnt" in condition:
            continue
        legal.add(index)
    return legal


def _pokemon_has_type(pokemon, move_type: str) -> bool:
    if not pokemon or not move_type:
        return False
    try:
        return bool(pokemon.has_type(move_type))
    except AttributeError:
        return normalize_name(move_type) in {
            normalize_name(t) for t in (getattr(pokemon, "types", []) or [])
        }


def _pokemon_hp_ratio(pokemon) -> float:
    if pokemon is None:
        return 1.0
    try:
        return max(0.0, min(1.0, float(pokemon.hp) / max(float(pokemon.max_hp), 1.0)))
    except (TypeError, ValueError):
        return 1.0


def _score_fallback_move(battle, move_id: str) -> float:
    move_norm = normalize_name(move_id)
    move_data = all_move_json.get(move_norm, {})
    score = 1.0
    user_active = getattr(getattr(battle, "user", None), "active", None)
    opponent = getattr(getattr(battle, "opponent", None), "active", None)

    category = move_data.get(constants.CATEGORY)
    base_power = float(move_data.get(constants.BASE_POWER) or 0)
    move_type = normalize_name(move_data.get(constants.TYPE, ""))
    if category in {constants.PHYSICAL, constants.SPECIAL} and base_power > 0:
        effectiveness = 1.0
        if opponent is not None and getattr(opponent, "types", None):
            effectiveness = type_effectiveness_modifier(move_type, opponent.types)
        if effectiveness <= 0:
            return 0.01
        stab = 1.5 if _pokemon_has_type(user_active, move_type) else 1.0
        score += (base_power * effectiveness * stab) / 100.0
        if effectiveness < 1:
            score *= 0.6
        elif effectiveness > 1:
            score *= 1.25
    else:
        recovery_moves = {normalize_name(m) for m in RECOVERY_MOVES}
        hazard_moves = {normalize_name(m) for m in HAZARD_MOVES}
        pivot_moves = {normalize_name(m) for m in PIVOT_MOVES}
        setup_moves = {normalize_name(m) for m in SETUP_MOVES}

        if move_norm in recovery_moves:
            hp_missing = 1.0 - _pokemon_hp_ratio(user_active)
            score += max(0.0, hp_missing) * 1.4
        elif move_norm in hazard_moves:
            score += 0.35
        elif move_norm in pivot_moves:
            score += 0.45
        elif move_norm in setup_moves:
            score += 0.15
        else:
            score += 0.2

    if move_norm in {normalize_name(m) for m in PIVOT_MOVES}:
        score += 0.35
    return score


def _choose_first_request_move_id(battle) -> str | None:
    legal_moves = _request_legal_move_ids(battle)
    return legal_moves[0] if legal_moves else None


def _choose_first_request_switch_slot(battle) -> int | None:
    legal_slots = sorted(_request_legal_switch_slots(battle))
    return legal_slots[0] if legal_slots else None


def _request_indicates_trapped(battle) -> bool:
    request = getattr(battle, "request_json", None) or {}
    active = request.get(constants.ACTIVE, [])
    if not active:
        return bool(getattr(getattr(battle, "user", None), "trapped", False))
    trapped = bool(active[0].get(constants.TRAPPED, False))
    maybe_trapped = bool(active[0].get(constants.MAYBE_TRAPPED, False))
    return trapped or maybe_trapped


def _build_recovery_choice_from_request(
    battle, error_message: str = ""
) -> list[str] | None:
    """
    Build a legal immediate retry command after Showdown rejects a choice.
    """
    rqid = getattr(battle, "rqid", None)
    if rqid is None:
        return None

    request = getattr(battle, "request_json", None) or {}
    trapped = _request_indicates_trapped(battle)
    force_switch = bool(request.get(constants.FORCE_SWITCH, False) or getattr(battle, "force_switch", False))

    reason = (error_message or "").lower()
    prefer_move = ("can't switch" in reason) or ("trapped" in reason) or trapped
    prefer_switch = ("can't move" in reason) or ("must switch" in reason) or force_switch

    if force_switch:
        prefer_switch = True
        prefer_move = False
    elif trapped:
        prefer_move = True
        prefer_switch = False

    if prefer_move:
        order = ("move", "switch")
    elif prefer_switch:
        order = ("switch", "move")
    else:
        order = ("move", "switch")

    for choice_type in order:
        if choice_type == "move":
            move_id = _choose_first_request_move_id(battle)
            if move_id:
                return [f"/choose move {move_id}", str(rqid)]
        elif choice_type == "switch":
            if trapped and not force_switch:
                continue
            switch_slot = _choose_first_request_switch_slot(battle)
            if switch_slot is not None:
                return [f"/switch {switch_slot}", str(rqid)]

    return None


def _is_invalid_choice_message(msg: str) -> bool:
    return "|error|[Invalid choice]" in (msg or "")


def _first_alive_team_preview_slot(battle) -> int:
    reserve = list(getattr(getattr(battle, "user", None), "reserve", []) or [])
    for fallback_index, pkmn in enumerate(reserve, start=1):
        try:
            if getattr(pkmn, "hp", 1) <= 0:
                continue
        except Exception:
            pass
        try:
            index = int(getattr(pkmn, "index"))
            if index > 0:
                return index
        except Exception:
            return fallback_index
    return 1


def _team_preview_message_for_slot(battle, slot: int) -> list[str]:
    reserve = list(getattr(getattr(battle, "user", None), "reserve", []) or [])
    size_of_team = max(len(reserve), slot)
    team_list_indexes = list(range(1, size_of_team + 1))
    if slot in team_list_indexes:
        team_list_indexes.remove(slot)
    else:
        slot = team_list_indexes[0] if team_list_indexes else 1
        team_list_indexes = [idx for idx in team_list_indexes if idx != slot]
    order = "{}{}".format(slot, "".join(str(x) for x in team_list_indexes))
    return ["/team {}|{}".format(order, battle.rqid)]


def _initialize_standard_battle_datasets(pokemon_battle_type: str, unique_pkmn_names: set[str]) -> None:
    try:
        logger.info(
            "Initializing Smogon/team datasets for %s (%d pokemon)",
            pokemon_battle_type,
            len(unique_pkmn_names),
        )
        SmogonSets.initialize(
            FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
        )
        TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)
        logger.info("Battle dataset initialization complete")
    except BaseException:
        logger.exception(
            "Battle dataset initialization failed; continuing without set priors"
        )


async def handle_team_preview(battle, ps_websocket_client):
    _preview_start = time.time()
    lead_pick = None
    try:
        team_plan = analyze_team(battle.user.team_dict) if battle.user.team_dict else None
        if team_plan:
            playstyle = team_plan.playstyle
        else:
            playstyle = PlaystyleConfig.get_team_playstyle(FoulPlayConfig.team_name or "")

        hazard_moves_norm = {normalize_name(m) for m in HAZARD_MOVES}
        pivot_moves_norm = {normalize_name(m) for m in PIVOT_MOVES}
        setup_moves_norm = {normalize_name(m) for m in SETUP_MOVES}

        def score_lead(pkmn):
            score = 0.0
            moves = {m.name for m in pkmn.moves}
            if pkmn.name in (team_plan.hazard_setters if team_plan else set()) or moves & hazard_moves_norm:
                score += 2.0
            if moves & pivot_moves_norm:
                score += 0.8
            if moves & setup_moves_norm and playstyle == Playstyle.HYPER_OFFENSE:
                score += 1.0
            # Speed bonus
            score += pkmn.stats[constants.SPEED] / 200.0

            # Matchup vs opponent roster (type-based)
            if battle.opponent.reserve:
                matchup_scores = []
                for opp in battle.opponent.reserve:
                    if opp is None:
                        continue
                    try:
                        worst = max(
                            type_effectiveness_modifier(t, pkmn.types) for t in opp.types
                        )
                        best_off = max(
                            type_effectiveness_modifier(t, opp.types) for t in pkmn.types
                        )
                        matchup_scores.append((2.0 - min(worst, 2.0)) + best_off)
                    except Exception:
                        continue
                if matchup_scores:
                    score += sum(matchup_scores) / len(matchup_scores)
            return score

        candidates = [p for p in battle.user.reserve if p.hp > 0]
        if candidates:
            scored = [(p, score_lead(p)) for p in candidates]
            best, _best_score = max(scored, key=lambda x: x[1])
            lead_pick = best
    except Exception as e:
        logger.warning(f"Lead heuristic failed: {e}")

    if lead_pick is not None:
        try:
            lead_slot = int(lead_pick.index)
        except Exception:
            lead_slot = _first_alive_team_preview_slot(battle)
    else:
        lead_slot = _first_alive_team_preview_slot(battle)

    logger.info(
        "Team preview selected lead slot %s in %s without pre-turn search",
        lead_slot,
        battle.battle_tag,
    )

    # FIX: Before sending /team, check if team preview has already ended.
    # The server's inactivity timer may have auto-selected a team while we
    # were computing the lead pick. Two checks:
    # 1. Time-based: PS team preview timer is 90-150s. If we've been in preview
    #    for >85s, skip sending /team (it will be rejected anyway).
    # 2. Queue-based: yield to event loop so any pending WS messages arrive,
    #    then drain queue looking for START_STRING or |turn|.
    battle_tag = battle.battle_tag
    _elapsed = time.time() - _preview_start
    team_preview_expired = False

    # Check 1: time-based guard (PS timer is typically 90-150s)
    TEAM_PREVIEW_SAFE_WINDOW_SEC = 85
    if _elapsed > TEAM_PREVIEW_SAFE_WINDOW_SEC:
        team_preview_expired = True
        logger.warning(
            "Team preview took %.1fs in %s (>%ds safe window). "
            "Skipping /team to avoid inactivity forfeit.",
            _elapsed, battle_tag, TEAM_PREVIEW_SAFE_WINDOW_SEC,
        )

    # Check 2: queue-drain (yield first so WS messages can arrive)
    if not team_preview_expired:
        await asyncio.sleep(0)  # yield to event loop â€” let pending WS messages queue up
        queue = ps_websocket_client.battle_queues.get(battle_tag)
        drained_msgs = []
        if queue:
            while not queue.empty():
                try:
                    peeked = queue.get_nowait()
                    drained_msgs.append(peeked)
                    if constants.START_STRING in peeked or "|turn|" in peeked:
                        team_preview_expired = True
                        logger.warning(
                            "Team preview expired before /team was sent in %s "
                            "(server auto-selected). Skipping /team command.",
                            battle_tag,
                        )
                        break
                except asyncio.QueueEmpty:
                    break
            # Put drained messages back so the main battle loop can process them
            for m in drained_msgs:
                queue.put_nowait(m)

    if team_preview_expired:
        logger.info("Team preview auto-resolved for %s; proceeding to battle loop", battle_tag)
        return

    try:
        pkmn_name = battle.user.reserve[lead_slot - 1].name
        battle.user.last_selected_move = LastUsedMove(
            "teampreview", "switch {}".format(pkmn_name), battle.turn
        )
    except Exception:
        logger.debug("Could not record team preview lead slot %s", lead_slot)

    message = _team_preview_message_for_slot(battle, lead_slot)
    await ps_websocket_client.send_message(battle.battle_tag, message)


async def get_battle_tag_and_opponent(
    ps_websocket_client: PSWebsocketClient,
    stop_event: asyncio.Event | None = None,
    worker_id: int | None = None,
):
    """Wait for a battle to start.

    Returns: (battle_tag, opponent_name, resume_mode, resume_started)
    Uses atomic claim_pending_battle() to avoid race conditions when multiple
    workers are waiting for battles concurrently.
    """
    def _release_search(reason: str):
        if worker_id is not None:
            ps_websocket_client.release_search_slot(worker_id, reason)

    if RESUME_ACTIVE_BATTLES:
        while True:
            resume_entry = await _claim_resume_battle(worker_id)
            if not resume_entry:
                break
            battle_tag = resume_entry.get("id")
            opponent_name = resume_entry.get("opponent")
            if not battle_tag:
                continue
            # Skip resuming if a replay already exists (battle is finished).
            replay_id = _normalize_replay_id(battle_tag)
            started = resume_entry.get("started")
            if started and REPLAY_CHECK_MIN_AGE_SEC > 0:
                try:
                    age = (datetime.now() - started).total_seconds()
                except Exception:
                    age = None
            else:
                age = None
            if age is None or age >= REPLAY_CHECK_MIN_AGE_SEC:
                try:
                    if await _replay_exists(replay_id):
                        logger.info(
                            "Skipping resume for %s: replay exists (finished battle)",
                            battle_tag,
                        )
                        continue
                except Exception:
                    # If replay check fails, fall back to resume attempt.
                    pass
            logger.info(
                "Resuming battle %s%s",
                battle_tag,
                f" (worker {worker_id})" if worker_id is not None else "",
            )
            resumed = await _attempt_resume_battle(
                ps_websocket_client, battle_tag, opponent_hint=opponent_name
            )
            if resumed:
                battle_tag, opponent_name, status = resumed
                if status == "ok":
                    _release_search("resume confirmed")
                    return battle_tag, opponent_name, True, resume_entry.get("started")
                if status == "timeout":
                    resume_entry["_timeout_requeues"] = (
                        int(resume_entry.get("_timeout_requeues", 0)) + 1
                    )
                    if (
                        RESUME_MAX_TIMEOUT_REQUEUES > 0
                        and resume_entry["_timeout_requeues"] >= RESUME_MAX_TIMEOUT_REQUEUES
                    ):
                        # Dead-but-silent orphan: stop re-queuing so this worker
                        # returns to the ladder. Forfeit + blacklist so neither
                        # this loop nor the search loop re-claims the dead room.
                        logger.warning(
                            "Resume orphan %s timed out %s time(s); forfeiting and "
                            "dropping so worker %s can ladder.",
                            battle_tag,
                            resume_entry["_timeout_requeues"],
                            worker_id,
                        )
                        try:
                            await ps_websocket_client.forfeit_battle(battle_tag)
                        except Exception:
                            pass
                        try:
                            await ps_websocket_client.leave_battle(battle_tag)
                        except Exception:
                            pass
                        try:
                            _blacklist_battle_tag(battle_tag)
                        except Exception:
                            pass
                        if battle_tag in _active_battles:
                            _log_battle_removal(battle_tag, "resume_orphan_dropped")
                            del _active_battles[battle_tag]
                            await update_active_battles_file()
                        # do NOT requeue -> fall through to ladder search
                    else:
                        await _requeue_resume_entry(resume_entry, "timeout")
                # If battle is closed/finished, drop and continue to next entry.

    battle_tag_pattern = re.compile(r'^>(battle-[a-z0-9-]+)')
    search_wait_started = time.time()

    while True:
        if stop_event is not None and stop_event.is_set():
            _release_search("stopped")
            return None, None, False, None
        if SEARCH_WAIT_TIMEOUT_SEC > 0 and (time.time() - search_wait_started) >= SEARCH_WAIT_TIMEOUT_SEC:
            logger.warning(
                "Timed out waiting %ss for worker %s ladder search; cancelling and retrying.",
                SEARCH_WAIT_TIMEOUT_SEC,
                worker_id,
            )
            try:
                await ps_websocket_client.cancel_search()
            except Exception:
                pass
            _release_search("search wait timeout")
            return None, None, False, None
        # First try to atomically claim a pending battle (prevents race conditions)
        battle_tag, pending_msgs = await ps_websocket_client.claim_pending_battle(worker_id)
        if battle_tag and pending_msgs:
            # Check if this battle is blacklisted (dead/stuck battle)
            if battle_tag in _dead_battle_blacklist:
                logger.warning(f"Skipping blacklisted dead battle: {battle_tag}")
                # Unregister and leave the room so PS stops sending messages
                ps_websocket_client.unregister_battle(battle_tag)
                try:
                    await ps_websocket_client.leave_battle(battle_tag)
                except Exception:
                    pass
                continue
            _release_search("battle claimed")
            # Battle already claimed and registered - extract opponent name
            for msg in pending_msgs:
                opponent_name = _extract_opponent_from_message(
                    msg,
                    getattr(ps_websocket_client, "username", None),
                )
                if opponent_name:
                    logger.info("Claimed pending battle {} against: {}".format(battle_tag, opponent_name))
                    return battle_tag, opponent_name, False, None
            # If we couldn't find the opponent yet, return now and let
            # start_battle_common pick it up from the battle queue.
            return battle_tag, None, False, None

        # No pending battles - check global queue for new battle notifications
        try:
            msg = await asyncio.wait_for(ps_websocket_client.global_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            continue

        first_line = msg.split("\n")[0]

        # Check for battle tag in message (this shouldn't happen often with dispatcher,
        # but handle edge case where message arrives before dispatcher routes it)
        match = battle_tag_pattern.match(first_line)
        if match:
            battle_tag = match.group(1)

            # Check if this battle is blacklisted (dead/stuck battle)
            if battle_tag in _dead_battle_blacklist:
                logger.warning(f"Skipping blacklisted dead battle from message: {battle_tag}")
                continue

            # Register this battle immediately to prevent other workers from grabbing it
            await ps_websocket_client.register_battle(battle_tag)
            _release_search("battle claimed")

            opponent_name = _extract_opponent_from_message(
                msg,
                getattr(ps_websocket_client, "username", None),
            )
            if opponent_name:
                logger.info("Initialized {} against: {}".format(battle_tag, opponent_name))
                return battle_tag, opponent_name, False, None
            # If opponent not found yet, return now; start_battle_common will
            # parse player lines from the battle queue.
            return battle_tag, None, False, None


def _extract_opponent_from_message(msg, *our_names):
    """Extract opponent name from a battle message. Returns None if not found."""
    def _is_ours(player_name: str) -> bool:
        return _is_our_showdown_account(player_name, *our_names)

    # Try |title| format first (comes earliest in battle init)
    for line in msg.split("\n"):
        if "|title|" in line:
            # Format: |title|Player1 vs. Player2
            parts = line.split("|")
            if len(parts) >= 3:
                title = parts[2]
                if " vs. " in title:
                    players = title.split(" vs. ")
                    for player in players:
                        player = player.strip()
                        if player and not _is_ours(player):
                            return player
    
    # Try |player| format
    for line in msg.split("\n"):
        if "|player|" in line:
            parts = line.split("|")
            if len(parts) >= 4:
                player_name = parts[3]
                if player_name and not _is_ours(player_name):
                    return player_name

    # Fallback: vs. format anywhere in message
    for line in msg.split("\n"):
        if " vs. " not in line:
            continue
        text = line.split("|")[-1].strip() if "|" in line else line.strip()
        if " vs. " not in text:
            continue
        for player in text.split(" vs. "):
            player = player.strip()
            if player and not _is_ours(player):
                return player

    return None


def _message_has_request_or_turn(msg: str) -> bool:
    return "|request|" in msg or "|turn|" in msg


def _extract_log_lines(msg: str, battle_tag: str | None = None) -> list[str]:
    lines = []
    for line in msg.split("\n"):
        if not line:
            continue
        if battle_tag and line.startswith(f">{battle_tag}"):
            continue
        if line.startswith("|request|"):
            continue
        lines.append(line)
    return lines


async def start_battle_common(
    ps_websocket_client: PSWebsocketClient,
    pokemon_battle_type,
    stop_event: asyncio.Event | None = None,
    worker_id: int | None = None,
):
    battle_tag, opponent_name, resume_mode, resume_started = await get_battle_tag_and_opponent(
        ps_websocket_client,
        stop_event=stop_event,
        worker_id=worker_id,
    )
    if battle_tag is None:
        return None, None

    async def _register_active_battle():
        async with _battles_lock:
            if worker_id is not None:
                for existing_tag, info in list(_active_battles.items()):
                    if info.get("worker_id") == worker_id and existing_tag != battle_tag:
                        _log_battle_removal(existing_tag, f"replaced_by_worker_{worker_id}_new_battle_{battle_tag}")
                        _active_battles.pop(existing_tag, None)
            existing = _active_battles.get(battle_tag, {})
            if resume_started and not existing.get("started"):
                existing["started"] = resume_started
            else:
                existing.setdefault("started", datetime.now())
            existing["opponent"] = opponent_name or existing.get("opponent", "Unknown")
            existing["worker_id"] = worker_id
            existing["status"] = "active"
            existing.pop("resume_pending", None)
            _active_battles[battle_tag] = existing
        await update_active_battles_file()

    # Invite the spectator BEFORE the battle is published to the router/feed
    # so the OBS Browser Source only ever navigates to a room its spectator
    # session can already view. A failed invite must not abort the battle;
    # the fallback in start_battle retries it.
    try:
        await ensure_spectator_invited(ps_websocket_client, battle_tag)
    except Exception:
        logger.warning(
            "Spectator invite failed for %s; will retry after battle init",
            battle_tag,
            exc_info=True,
        )

    # Register battle as soon as we have the tag so OBS can attach immediately.
    # This avoids a "searching" slot while start_battle waits on early messages.
    await _register_active_battle()

    # Battle is already registered atomically in get_battle_tag_and_opponent()

    if FoulPlayConfig.log_to_file:
        if worker_id is not None:
            # Per-worker handler: each worker logs to its own file
            _rollover_worker_handler(worker_id, battle_tag, opponent_name)
        else:
            # Fallback: single-worker mode uses the shared handler
            FoulPlayConfig.file_log_handler.do_rollover(
                "{}_{}.log".format(battle_tag, opponent_name)
            )

    battle = Battle(battle_tag)
    battle.worker_id = worker_id
    battle.resume_pending = resume_mode
    battle.resume_started = resume_started
    if opponent_name and opponent_name != "Unknown":
        battle.opponent.account_name = opponent_name
    battle.pokemon_format = pokemon_battle_type
    battle.generation = pokemon_battle_type[:4]

    # wait until the opponent's identifier is received. This will be `p1` or `p2`.
    #
    # e.g.
    # '>battle-gen9randombattle-44733
    # |player|p1|OpponentName|2|'
    while True:
        msg = await ps_websocket_client.receive_battle_message(battle_tag)

        # If the battle room was closed before we could initialize, bail out.
        if battle_room_closed(battle_tag, msg):
            logger.warning(f"Battle room closed before init: {battle_tag}")
            # Blacklist to prevent immediate re-claim from buffered messages
            _blacklist_battle_tag(battle_tag)
            logger.info(f"Blacklisted closed-before-init battle: {battle_tag} (blacklist size: {len(_dead_battle_blacklist)})")
            try:
                ps_websocket_client.unregister_battle(battle_tag)
            except Exception:
                pass
            try:
                await ps_websocket_client.leave_battle(battle_tag)
            except Exception:
                pass
            removed = False
            async with _battles_lock:
                if battle_tag in _active_battles:
                    _log_battle_removal(battle_tag, "room_closed_before_init")
                    del _active_battles[battle_tag]
                    removed = True
            if removed:
                await update_active_battles_file()
            return None, None

        # If we don't know the opponent yet, try to infer from player lines.
        if not battle.opponent.account_name:
            inferred = _extract_opponent_from_message(
                msg,
                getattr(ps_websocket_client, "username", None),
            )
            if inferred:
                battle.opponent.account_name = inferred

        if "|player|" in msg:
            # Get list of our known accounts (normalized for Showdown comparison)
            showdown_accounts = _showdown_account_identities(
                getattr(ps_websocket_client, "username", None)
            )
            
            for line in msg.split("\n"):
                if "|player|" not in line:
                    continue
                parts = line.split("|")
                if len(parts) < 4:
                    continue
                player_slot = parts[2]
                player_name = parts[3]
                
                # Check if this player is one of our accounts
                if _normalize_username(player_name) in showdown_accounts:
                    # This is us!
                    battle.user.account_name = player_name
                    battle.user.name = player_slot
                    battle.opponent.name = constants.ID_LOOKUP[player_slot]
                else:
                    # This is the opponent
                    if not battle.opponent.account_name:
                        battle.opponent.account_name = player_name
                    if battle.opponent.account_name and player_name.lower() == battle.opponent.account_name.lower():
                        battle.opponent.name = player_slot
                        if not battle.user.name:
                            battle.user.name = constants.ID_LOOKUP[battle.opponent.name]
            
            if battle.opponent.name and battle.user.name:
                break

    return battle, msg


def _try_parse_request_from_message(msg: str, battle: Battle) -> bool:
    for line in msg.split("\n"):
        if "|request|" in line:
            parts = line.split("|request|")
            if len(parts) > 1 and parts[1].strip():
                try:
                    user_json = json.loads(parts[1].strip("'"))
                except json.JSONDecodeError:
                    continue
                battle.request_json = user_json
                battle.user.initialize_first_turn_user_from_json(user_json)
                battle.rqid = user_json[constants.RQID]
                return True
    return False


async def get_first_request_json(
    ps_websocket_client: PSWebsocketClient, battle: Battle, initial_msg: str | None = None
):
    if initial_msg and _try_parse_request_from_message(initial_msg, battle):
        return
    while True:
        msg = await ps_websocket_client.receive_battle_message(battle.battle_tag)
        if _try_parse_request_from_message(msg, battle):
            return


async def start_random_battle(
    ps_websocket_client: PSWebsocketClient,
    pokemon_battle_type,
    stop_event: asyncio.Event | None = None,
    worker_id: int | None = None,
):
    battle, msg = await start_battle_common(
        ps_websocket_client,
        pokemon_battle_type,
        stop_event=stop_event,
        worker_id=worker_id,
    )
    if battle is None:
        return None
    resume_mode = getattr(battle, "resume_pending", False)
    battle.battle_type = BattleType.RANDOM_BATTLE
    RandomBattleTeamDatasets.initialize(battle.generation)

    if not resume_mode:
        await _send_battle_chat(ps_websocket_client, battle.battle_tag, [OPENING_CHAT_MESSAGE])

    while True:
        if constants.START_STRING in msg:
            battle.started = True

            # hold onto some messages to apply after we get the request JSON
            # omit the bot's switch-in message because we won't need that
            # parsing the request JSON will set the bot's active pkmn
            battle.msg_list = [
                m
                for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                if not (m.startswith("|switch|{}".format(battle.user.name)))
            ]
            break
        if resume_mode and _message_has_request_or_turn(msg):
            # Resumed mid-battle; skip waiting for |start| and process available log lines.
            battle.started = True
            battle.msg_list = _extract_log_lines(msg, battle.battle_tag)
            break
        msg = await ps_websocket_client.receive_battle_message(battle.battle_tag)

    await get_first_request_json(ps_websocket_client, battle, initial_msg=msg)

    # apply the messages that were held onto
    process_battle_updates(battle)

    best_move = await async_pick_move(battle)
    await ps_websocket_client.send_message(battle.battle_tag, best_move)

    return battle


async def start_standard_battle(
    ps_websocket_client: PSWebsocketClient,
    pokemon_battle_type,
    team_dict,
    stop_event: asyncio.Event | None = None,
    worker_id: int | None = None,
):
    battle, msg = await start_battle_common(
        ps_websocket_client,
        pokemon_battle_type,
        stop_event=stop_event,
        worker_id=worker_id,
    )
    if battle is None:
        return None
    resume_mode = getattr(battle, "resume_pending", False)
    # FIX: For resumed battles, the server's actual team may not match the
    # team_dict loaded by this worker (e.g., worker 1 resumes a battle that
    # worker 0 originally started with a different team). Setting a wrong
    # team_dict causes matches=1/6 which disables all team knowledge for the
    # entire battle. Safer to skip team_dict for resumes and let the bot use
    # server-provided stats instead.
    if resume_mode:
        logger.info(
            "Resumed battle %s: skipping team_dict assignment "
            "(server team may differ from loaded team)",
            battle.battle_tag,
        )
        battle.user.team_dict = None
    else:
        battle.user.team_dict = team_dict
    if "battlefactory" in pokemon_battle_type:
        battle.battle_type = BattleType.BATTLE_FACTORY
    else:
        battle.battle_type = BattleType.STANDARD_BATTLE

    if not resume_mode:
        await _send_battle_chat(ps_websocket_client, battle.battle_tag, [OPENING_CHAT_MESSAGE])

    if battle.generation in constants.NO_TEAM_PREVIEW_GENS:
        while True:
            if constants.START_STRING in msg:
                battle.started = True

                # hold onto some messages to apply after we get the request JSON
                # omit the bot's switch-in message because we won't need that
                # parsing the request JSON will set the bot's active pkmn
                battle.msg_list = [
                    m
                    for m in msg.split(constants.START_STRING)[1].strip().split("\n")
                    if not (m.startswith("|switch|{}".format(battle.user.name)))
                ]
                break
            if resume_mode and _message_has_request_or_turn(msg):
                battle.started = True
                battle.msg_list = _extract_log_lines(msg, battle.battle_tag)
                break
            msg = await ps_websocket_client.receive_battle_message(battle.battle_tag)

        await get_first_request_json(ps_websocket_client, battle, initial_msg=msg)

        unique_pkmn_names = set(
            [p.name for p in battle.user.reserve] + [battle.user.active.name]
        )
        _initialize_standard_battle_datasets(pokemon_battle_type, unique_pkmn_names)

        # apply the messages that were held onto
        process_battle_updates(battle)

        best_move = await async_pick_move(battle)
        await ps_websocket_client.send_message(battle.battle_tag, best_move)

    else:
        if resume_mode and _message_has_request_or_turn(msg):
            # Resumed after team preview; skip waiting for |teampreview| and continue.
            battle.started = True
            await get_first_request_json(ps_websocket_client, battle, initial_msg=msg)

            unique_pkmn_names = set(
                p.name for p in [battle.user.active] + battle.user.reserve if p
            )
            _initialize_standard_battle_datasets(pokemon_battle_type, unique_pkmn_names)

            battle.msg_list = _extract_log_lines(msg, battle.battle_tag)
            if battle.msg_list:
                process_battle_updates(battle)
            return battle

        while constants.START_TEAM_PREVIEW not in msg:
            msg = await ps_websocket_client.receive_battle_message(battle.battle_tag)

        preview_string_lines = msg.split(constants.START_TEAM_PREVIEW)[-1].split("\n")

        opponent_pokemon = []
        for line in preview_string_lines:
            if not line:
                continue

            split_line = line.split("|")
            if (
                len(split_line) > 3
                and split_line[1] == constants.TEAM_PREVIEW_POKE
                and split_line[2].strip() == battle.opponent.name
            ):
                opponent_pokemon.append(split_line[3])

        await get_first_request_json(ps_websocket_client, battle, initial_msg=msg)
        battle.initialize_team_preview(opponent_pokemon, pokemon_battle_type)
        battle.during_team_preview()

        unique_pkmn_names = set(
            p.name for p in battle.opponent.reserve + battle.user.reserve
        )

        if battle.battle_type == BattleType.BATTLE_FACTORY:
            battle.battle_type = BattleType.BATTLE_FACTORY
            tier_name = extract_battle_factory_tier_from_msg(msg)
            logger.info("Battle Factory Tier: {}".format(tier_name))
            TeamDatasets.initialize(
                pokemon_battle_type,
                unique_pkmn_names,
                battle_factory_tier_name=tier_name,
            )
        else:
            battle.battle_type = BattleType.STANDARD_BATTLE
            _initialize_standard_battle_datasets(pokemon_battle_type, unique_pkmn_names)

        await handle_team_preview(battle, ps_websocket_client)

    return battle


async def start_battle(
    ps_websocket_client,
    pokemon_battle_type,
    team_dict,
    stop_event: asyncio.Event | None = None,
    worker_id: int | None = None,
):
    if "random" in pokemon_battle_type:
        battle = await start_random_battle(
            ps_websocket_client,
            pokemon_battle_type,
            stop_event=stop_event,
            worker_id=worker_id,
        )
    else:
        battle = await start_standard_battle(
            ps_websocket_client,
            pokemon_battle_type,
            team_dict,
            stop_event=stop_event,
            worker_id=worker_id,
        )

    if battle is None:
        return None

    await ps_websocket_client.send_message(battle.battle_tag, ["/timer on"])

    # Fallback only: the invite is normally sent (exactly once) before the
    # battle was published in start_battle_common. This retries solely when
    # that earlier attempt failed and released its reservation.
    await ensure_spectator_invited(ps_websocket_client, battle.battle_tag)

    return battle


async def _finalize_battle_runtime(
    ps_websocket_client: PSWebsocketClient,
    battle_tag: str,
    *,
    send_end_event: bool,
) -> None:
    # Save replay before leaving so the OBS ghost cleanup can detect
    # finished battles via the replay API.  Without this, replays are
    # never uploaded and the ghost check always returns 404.
    try:
        await ps_websocket_client.send_message(battle_tag, ["/savereplay"])
        await asyncio.sleep(0.5)  # brief pause for PS to process
    except Exception:
        pass

    try:
        await ps_websocket_client.leave_battle(battle_tag)
    except Exception:
        pass

    try:
        ps_websocket_client.unregister_battle(battle_tag)
    except Exception:
        pass

    removed = False
    async with _battles_lock:
        if battle_tag in _active_battles:
            _log_battle_removal(battle_tag, "finalize_battle_runtime")
            del _active_battles[battle_tag]
            removed = True
    if removed:
        await update_active_battles_file()

    if send_end_event:
        try:
            await send_stream_event(
                "BATTLE_END",
                _operational_loss_stream_payload(
                    battle_tag,
                    reason="finalize_without_terminal_result",
                ),
            )
        except Exception:
            pass


async def pokemon_battle(
    ps_websocket_client,
    pokemon_battle_type,
    team_dict,
    stop_event: asyncio.Event | None = None,
    worker_id: int | None = None,
):
    """Run a single battle to completion. Returns (winner, battle_tag)."""
    # Set worker context for per-worker log filtering
    if worker_id is not None:
        _current_worker_id.set(worker_id)
        _get_or_create_worker_handler(worker_id)

    battle = await start_battle(
        ps_websocket_client,
        pokemon_battle_type,
        team_dict,
        stop_event=stop_event,
        worker_id=worker_id,
    )
    if battle is None:
        return None, None
    battle_tag = battle.battle_tag
    opponent_name = battle.opponent.account_name if battle.opponent else "Unknown"

    # Signal battle start instantly
    await send_stream_event("BATTLE_START", {
        "id": battle_tag,
        "opponent": opponent_name,
        "format": pokemon_battle_type,
        "started": time.time(),
        "worker_id": worker_id,
        "slot": (worker_id + 1) if worker_id is not None else None,
    })

    # Generate pre-battle gameplan for strategic decision-making
    gameplan = generate_and_store_gameplan(battle_tag, battle)
    if gameplan:
        # Store gameplan in battle object for access by decision layer
        battle.gameplan = gameplan
        logger.info(f"ðŸŽ® GAMEPLAN GENERATED: {gameplan.our_strategy}")
        logger.info(f"ðŸ“Œ OUR WIN CONDITION: {gameplan.win_condition}")
        logger.info(f"âš”ï¸ OPPONENT WIN CONDITION: {gameplan.opponent_win_condition}")
        logger.info(f"ðŸ”„ KEY PIVOTS: {', '.join(gameplan.key_pivot_triggers)}")
        logger.info(f"ðŸ’¡ BACKUP PLAN: {gameplan.backup_plan or 'None'}")
    else:
        battle.gameplan = None
        logger.warning(f"Failed to generate gameplan for {battle_tag}")

    timeout_strikes = 0
    message_timeout = MESSAGE_TIMEOUT_SEC
    battle_start_time = time.time()
    battle_end_event_sent = False
    last_heartbeat = time.time()

    # Cache pre-battle ELO for delta display in Discord reports
    try:
        _pre_battle_username = (
            getattr(getattr(battle, "user", None), "account_name", None)
            or getattr(ps_websocket_client, "username", None)
            or FoulPlayConfig.username
        )
        _pre_elo, _ = await _fetch_elo(_pre_battle_username)
        if _pre_elo is not None:
            _elo_before_cache[battle_tag] = _pre_elo
            logger.debug(f"Pre-battle ELO cached: {_pre_elo:.0f} for {battle_tag}")
    except Exception:
        pass

    try:
        while True:
            # Heartbeat: periodically verify tracking + refresh file
            now_hb = time.time()
            if now_hb - last_heartbeat >= ACTIVE_BATTLES_HEARTBEAT_SEC:
                last_heartbeat = now_hb
                needs_reregister = False
                async with _battles_lock:
                    if battle_tag not in _active_battles:
                        # Don't re-register battles that already concluded
                        if battle_tag in _concluded_battles:
                            logger.debug(
                                "TRACKING: %s missing but already concluded, skipping re-register",
                                battle_tag,
                            )
                        else:
                            logger.warning(
                                "TRACKING: %s missing from _active_battles! Re-registering (worker=%s, opp=%s)",
                                battle_tag, worker_id, opponent_name,
                            )
                            _active_battles[battle_tag] = {
                                "opponent": opponent_name,
                                "started": datetime.fromtimestamp(battle_start_time),
                                "worker_id": worker_id,
                                "status": "active",
                            }
                            needs_reregister = True
                if needs_reregister:
                    await update_active_battles_file()
                    logger.info("TRACKING: re-registered %s successfully", battle_tag)
                else:
                    # Force a write to keep the file fresh for OBS
                    global _last_active_battles_payload
                    _last_active_battles_payload = None  # Bypass dedup
                    await update_active_battles_file()

            # Hard timeout safety: forcibly end battles that run too long
            if BATTLE_HARD_TIMEOUT_SEC > 0:
                elapsed = time.time() - battle_start_time
                if elapsed > BATTLE_HARD_TIMEOUT_SEC:
                    logger.error(
                        f"Battle {battle_tag} exceeded hard timeout "
                        f"({elapsed:.0f}s > {BATTLE_HARD_TIMEOUT_SEC}s) - forcibly terminating"
                    )
                    _blacklist_battle_tag(battle_tag)
                    logger.info(
                        "Added %s to dead battle blacklist (size: %s)",
                        battle_tag,
                        len(_dead_battle_blacklist),
                    )
                    operational_event_id = _queue_operational_loss_battle_result(
                        battle_tag,
                        opponent_name=opponent_name,
                        team_name=getattr(FoulPlayConfig, "team_name", None),
                        turns=getattr(battle, "turn", None),
                        reason="hard_timeout",
                        elapsed_seconds=elapsed,
                    )
                    await _handoff_battle_result_to_deku(operational_event_id)
                    await send_stream_event(
                        "BATTLE_END",
                        _operational_loss_stream_payload(
                            battle_tag,
                            reason="hard_timeout",
                            elapsed_seconds=elapsed,
                        ),
                    )
                    battle_end_event_sent = True
                    return None, battle_tag

            try:
                msg = await asyncio.wait_for(
                    ps_websocket_client.receive_battle_message(battle_tag),
                    timeout=message_timeout,
                )
                if not msg.startswith(f">{battle_tag}"):
                    logger.warning(
                        "Battle message tag mismatch: expected %s, got %s",
                        battle_tag,
                        msg.split("\n")[0] if msg else "<empty>",
                    )
                timeout_strikes = 0
                # If we had marked this battle stale, promote it back to active once messages resume.
                needs_update = False
                async with _battles_lock:
                    info = _active_battles.get(battle_tag)
                    if info and info.get("status") == "stale":
                        info["status"] = "active"
                        info.pop("stale_since", None)
                        needs_update = True
                if needs_update:
                    await update_active_battles_file()
            except asyncio.TimeoutError:
                timeout_strikes += 1
                logger.warning(
                    f"No messages for {message_timeout}s in {battle_tag} "
                    f"(strike {timeout_strikes}/{STALE_STRIKES})."
                )
                # Try to ensure connection/rejoin before giving up
                try:
                    await ps_websocket_client.ensure_connection()
                    await ps_websocket_client.join_room(battle_tag)
                except Exception:
                    pass
                if timeout_strikes < STALE_STRIKES:
                    continue

                # After DISCONNECT_STRIKES consecutive timeouts, declare disconnected
                if timeout_strikes >= DISCONNECT_STRIKES:
                    logger.error(
                        f"Battle {battle_tag} unresponsive for {timeout_strikes} strikes "
                        f"({timeout_strikes * message_timeout}s) - declaring disconnect"
                    )
                    _blacklist_battle_tag(battle_tag)
                    operational_event_id = _queue_operational_loss_battle_result(
                        battle_tag,
                        opponent_name=opponent_name,
                        team_name=getattr(FoulPlayConfig, "team_name", None),
                        turns=getattr(battle, "turn", None),
                        reason="message_timeout_disconnect",
                        elapsed_seconds=time.time() - battle_start_time,
                        timeout_strikes=timeout_strikes,
                    )
                    await _handoff_battle_result_to_deku(operational_event_id)
                    await send_stream_event(
                        "BATTLE_END",
                        _operational_loss_stream_payload(
                            battle_tag,
                            reason="message_timeout_disconnect",
                            elapsed_seconds=time.time() - battle_start_time,
                            timeout_strikes=timeout_strikes,
                        ),
                    )
                    battle_end_event_sent = True
                    return None, battle_tag

                # Mark stale but keep the battle visible/attached so OBS doesn't drop it.
                needs_update = False
                async with _battles_lock:
                    info = _active_battles.get(battle_tag)
                    if info and info.get("status") != "stale":
                        info["status"] = "stale"
                        info["stale_since"] = time.time()
                        needs_update = True
                if needs_update:
                    await update_active_battles_file()
                    logger.warning(f"Battle {battle_tag} marked stale; waiting for updates.")
                continue

            if _is_invalid_choice_message(msg):
                lower_msg = msg.lower()
                if "not your turn" in lower_msg:
                    logger.debug("Ignoring stale invalid choice in %s: not our turn", battle_tag)
                    continue
                if "nothing to choose" in lower_msg:
                    # Server has no pending request for us. This typically means
                    # the server's inactivity timer auto-selected a move/switch
                    # while we were still computing. Safe to skip -- the server
                    # will send a new |request| when it needs our next command.
                    logger.info(
                        "Ignoring 'nothing to choose' in %s: server already advanced",
                        battle_tag,
                    )
                    continue
                if "team preview" in lower_msg:
                    # Bot tried to send /team but team preview already ended.
                    # The server auto-picked a team order. Nothing to retry --
                    # wait for the next |request| from the main battle loop.
                    logger.info(
                        "Ignoring team preview invalid choice in %s: "
                        "server auto-selected team order",
                        battle_tag,
                    )
                    continue

                retry_choice = _build_recovery_choice_from_request(battle, error_message=msg)
                if retry_choice:
                    logger.warning(
                        "Invalid choice in %s; retrying with legal fallback %s",
                        battle_tag,
                        retry_choice[0],
                    )
                    await ps_websocket_client.send_message(battle_tag, retry_choice)
                    continue
                logger.warning(
                    "Invalid choice in %s but no legal fallback could be built from request_json",
                    battle_tag,
                )

            if battle_is_finished(battle_tag, msg):
                if constants.WIN_STRING in msg:
                    winner = msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
                elif constants.TIE_STRING in msg:
                    winner = "tie"
                else:
                    winner = None
                logger.info("Battle finished: %s Winner: %s", battle_tag, winner)

                # Capture Showdown's AUTHORITATIVE per-battle rating change.
                # The |raw| rating-transition line ("X's rating: 1234 &rarr; 1250")
                # may already be in this |win| batch, or arrive in the next few
                # room messages. Briefly drain remaining room messages here so we
                # don't miss it before reporting. This replaces the lagging
                # shared-ladder-API delta (which collapsed to ~+/-1 under
                # concurrent battles).
                #
                # Showdown emits a rating line for BOTH players, so we must scope
                # the parse to our own account name -- otherwise a LOSS gets the
                # winning opponent's positive delta.
                _our_name = (
                    (battle.user.account_name if battle.user and battle.user.account_name else None)
                    or getattr(ps_websocket_client, "username", None)
                    or FoulPlayConfig.username
                )
                _rating_delta = parse_rating_transition(msg, _our_name)
                if _rating_delta is None:
                    _rating_deadline = time.time() + 5.0
                    while time.time() < _rating_deadline:
                        try:
                            _post_win_msg = await asyncio.wait_for(
                                ps_websocket_client.receive_battle_message(battle_tag),
                                timeout=1.0,
                            )
                        except asyncio.TimeoutError:
                            continue
                        except ValueError:
                            break
                        _rating_delta = parse_rating_transition(_post_win_msg, _our_name)
                        if _rating_delta is not None:
                            break
                        if "deinit" in _post_win_msg:
                            break
                if _rating_delta is not None:
                    logger.info(
                        "Captured authoritative rating transition for %s: %d -> %d (%+d)",
                        battle_tag, _rating_delta[0], _rating_delta[1], _rating_delta[2],
                    )
                else:
                    logger.info(
                        "No |raw| rating transition seen for %s; "
                        "falling back to ladder-API ELO.",
                        battle_tag,
                    )
                await _send_battle_chat(ps_websocket_client, battle_tag, post_battle_messages())

                # Save replay and capture URL if configured
                replay_url = None

                # Check the terminal winner directly; this also handles stale
                # account config when the winner is not the known opponent.
                we_won = _battle_result_from_evidence(
                    winner,
                    battle.user.account_name if battle.user else None,
                    opponent_name=opponent_name,
                ) == "win"

                save_replay_requested = (
                    FoulPlayConfig.save_replay == SaveReplay.always
                    or (
                        FoulPlayConfig.save_replay == SaveReplay.on_loss and not we_won
                    )
                    or (
                        FoulPlayConfig.save_replay == SaveReplay.on_win and we_won
                    )
                )
                if save_replay_requested:
                    replay_url = await ps_websocket_client.save_replay(battle_tag)

                # Post battle result to Discord
                team_name = (
                    FoulPlayConfig.team_name
                    if hasattr(FoulPlayConfig, "team_name")
                    else None
                )
                our_player_name = (
                    battle.user.account_name
                    if battle.user and battle.user.account_name
                    else getattr(ps_websocket_client, "username", None)
                )
                # Get pre-battle ELO for delta display (fetched now = post-battle)
                # We fetch ELO inside _post_battle_to_discord; pass pre-battle value
                # Note: elo_before should ideally be cached at battle start; here we
                # pass None and rely on post-only display if not available.
                battle_turn_count = getattr(battle, "turn", None)

                # Retrieve pre-battle ELO for delta display
                _elo_before_val = _elo_before_cache.pop(battle_tag, None)
                _discord_replay_url = await resolve_public_replay_url(
                    battle_tag=battle_tag,
                    replay_url=replay_url,
                    max_attempts=None if (replay_url or save_replay_requested) else 1,
                    delay_seconds=None if (replay_url or save_replay_requested) else 0,
                    allow_battle_tag_fallback=save_replay_requested,
                )
                # Save replay JSON locally immediately (before PS expires it).
                # If save_replay() missed the response but the upload succeeded,
                # the battle tag still recovers the public replay id here.
                _replay_save_id = public_replay_id_candidate(
                    _discord_replay_url
                    or replay_url
                    or (battle_tag if save_replay_requested else None)
                )
                if _replay_save_id:
                    await _save_replay_json_for_evidence(_replay_save_id)

                _replay_handoff = replay_handoff_fields(
                    battle_tag=battle_tag,
                    replay_url=replay_url,
                    verified_replay_url=_discord_replay_url,
                    save_replay_requested=save_replay_requested,
                )
                _queue_replay_url = _replay_handoff.get("replay_url")
                _queue_replay_status = str(_replay_handoff.get("replay_status") or "absent")

                elo_after = await _post_battle_to_discord(
                    battle_tag=battle_tag,
                    winner=winner,
                    opponent_name=opponent_name,
                    replay_url=_discord_replay_url or replay_url,
                    team_name=team_name,
                    our_player_name=our_player_name,
                    turn_count=battle_turn_count,
                    elo_before=_elo_before_val,
                    rating_delta=_rating_delta,
                )

                _elo_before_final = None
                _elo_after_final = None
                _elo_delta_final = None
                if _rating_delta is not None:
                    _elo_before_final = float(_rating_delta[0])
                    _elo_after_final = float(_rating_delta[1])
                    _elo_delta_final = int(_rating_delta[2])
                else:
                    _elo_after_final = elo_after
                    if _elo_before_val is not None:
                        _elo_before_final = float(_elo_before_val)
                    if _elo_after_final is not None and _elo_before_final is not None:
                        _elo_delta_final = int(round(float(_elo_after_final) - float(_elo_before_final)))

                # Cleanup battle queue to prevent buildup over time.
                timeout = 5
                start = time.time()
                while time.time() - start < timeout:
                    try:
                        msg = await asyncio.wait_for(
                            ps_websocket_client.receive_battle_message(battle_tag),
                            timeout=1.0,
                        )
                        if "deinit" in msg:
                            break
                    except asyncio.TimeoutError:
                        continue
                    except ValueError:
                        break

                # Remove from active battles tracking so stream status immediately
                # shows "Searching" when this was the final battle.
                removed = False
                async with _battles_lock:
                    if battle_tag in _active_battles:
                        _log_battle_removal(battle_tag, f"battle_finished (winner={winner})")
                        del _active_battles[battle_tag]
                        removed = True
                if removed:
                    await update_active_battles_file()

                # Update stream overlay stats
                try:
                    _result_key_stats = _battle_result_from_evidence(
                        winner,
                        getattr(getattr(battle, "user", None), "account_name", None),
                        opponent_name=opponent_name,
                    )
                    is_win = _result_key_stats == "win"

                    if _result_key_stats in {"win", "loss"}:
                        update_daily_stats(
                            wins_delta=1 if is_win else 0,
                            losses_delta=0 if is_win else 1,
                        )
                    daily = __import__(
                        "streaming.state_store", fromlist=["read_daily_stats"]
                    ).read_daily_stats()
                    async with _battles_lock:
                        battle_count = len(_active_battles)
                    write_status(
                        {
                            "wins": daily.get("wins", 0),
                            "losses": daily.get("losses", 0),
                            "today_wins": daily.get("wins", 0),
                            "today_losses": daily.get("losses", 0),
                            "status": "Searching" if battle_count == 0 else "Battling",
                            "battle_info": (
                                f"vs {opponent_name}"
                                if not is_win and opponent_name
                                else "Searching..."
                            ),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to update stream status: {e}")

                try:
                    _terminal_result = _battle_result_from_evidence(
                        winner,
                        getattr(getattr(battle, "user", None), "account_name", None),
                        opponent_name=opponent_name,
                    )
                except Exception:
                    _terminal_result = "loss" if winner is None else None
                _battle_end_payload = {
                    "id": battle_tag,
                    "winner": winner,
                    "ended": time.time(),
                }
                if _terminal_result:
                    _battle_end_payload["result"] = _terminal_result
                    _battle_end_payload["terminalResult"] = _terminal_result
                if winner is None and _terminal_result == "loss":
                    _battle_end_payload["operationalLoss"] = True
                    _battle_end_payload["reason"] = "terminal_without_winner"
                await send_stream_event("BATTLE_END", _battle_end_payload)
                battle_end_event_sent = True

                if not _replay_handoff.get("replay_public_verified"):
                    _late_replay_url = await resolve_public_replay_url(
                        battle_tag=battle_tag,
                        replay_url=replay_url,
                        max_attempts=1,
                        delay_seconds=0,
                        allow_battle_tag_fallback=save_replay_requested,
                    )
                    if _late_replay_url:
                        _discord_replay_url = _late_replay_url
                        _replay_handoff = replay_handoff_fields(
                            battle_tag=battle_tag,
                            replay_url=replay_url,
                            verified_replay_url=_discord_replay_url,
                            save_replay_requested=save_replay_requested,
                        )
                        _queue_replay_url = _replay_handoff.get("replay_url")
                        _queue_replay_status = str(_replay_handoff.get("replay_status") or "absent")

                # Queue battle result event for Discord event poster. Offline eval
                # disables this by default because it intentionally uses
                # --save-replay never and has no Discord transport to drain it.
                if not battle_result_event_queue_enabled():
                    logger.info("Skipping battle_result queue event for %s: queue disabled", battle_tag)
                else:
                    try:
                        _result_str = _battle_result_from_evidence(
                            winner,
                            getattr(getattr(battle, "user", None), "account_name", None),
                            opponent_name=opponent_name,
                        )
                        _team_name_ev = (
                            FoulPlayConfig.team_name
                            if hasattr(FoulPlayConfig, "team_name")
                            else "unknown"
                        )
                        _turn_count_ev = getattr(battle, "turn", None)
                        _recent_summary = ""
                        _recent_wins_ev = None
                        _recent_losses_ev = None
                        _recent_window_size_ev = None
                        _recent_streak_ev = ""
                        _stats_battles_for_alert = []
                        _current_result_row = {
                            "battle_id": battle_tag,
                            "result": _result_str,
                            "opponent": opponent_name,
                            "turns": _turn_count_ev,
                        }
                        try:
                            _stats_battles = []
                            if BATTLE_STATS_PATH.exists():
                                _stats_data = json.loads(BATTLE_STATS_PATH.read_text(encoding="utf-8"))
                                _stats_battles = [
                                    _b for _b in _stats_data.get("battles", [])
                                    if isinstance(_b, dict)
                                ]
                            _stats_battles_for_alert = _stats_battles
                            _recent_result_summary = summarize_recent_results_with_current(
                                _stats_battles,
                                _current_result_row,
                                window=5,
                            )
                            _recent_summary = str(_recent_result_summary.get("record") or "")
                            _recent_wins_ev = _recent_result_summary.get("wins")
                            _recent_losses_ev = _recent_result_summary.get("losses")
                            _recent_window_size_ev = _recent_result_summary.get("window_size")
                            _recent_streak_ev = str(_recent_result_summary.get("streak") or "")
                        except Exception as _recent_err:
                            logger.debug("Failed to summarize recent battle window: %s", _recent_err)
                            _recent_summary = ""

                        _message_text = str(msg or "").lower()
                        _replay_is_public = bool(_replay_handoff.get("replay_public_verified")) or bool(
                            canonical_replay_url(_queue_replay_url)
                        )
                        _opponent_label = opponent_name or "unknown opponent"
                        _battle_ref = battle_tag.replace("battle-", "", 1) if battle_tag else "latest battle"
                        _what_parts = [f"battle finished {_result_str} vs {_opponent_label}"]
                        if _team_name_ev:
                            _what_parts.append(f"using {_team_name_ev}")
                        if _turn_count_ev:
                            _what_parts.append(f"in {_turn_count_ev} turns")
                        _what_happened = " ".join(_what_parts)

                        _decisive_reason = ""
                        if _result_str == "loss":
                            if "forfeit" in _message_text:
                                _decisive_reason = "Battle ended on forfeit; classify who forfeited from replay before treating it as gameplay signal."
                            elif "inactive" in _message_text or "disconnect" in _message_text or "timeout" in _message_text:
                                _decisive_reason = "Loss came from inactivity/disconnect behavior, so this is operational until reconnect and timer logs are cleared."
                            elif not _replay_is_public:
                                _decisive_reason = "Replay is not public yet, so the loss is recorded without a claimed strategic cause."
                        elif _result_str == "win":
                            _decisive_reason = ""

                        _next_action = ""
                        if _result_str == "loss":
                            if "inactive" in _message_text or "disconnect" in _message_text or "timeout" in _message_text:
                                _next_action = f"Inspect reconnect/timer logs for {_battle_ref} before treating the {_opponent_label} loss as team or policy signal."
                            elif not _replay_is_public:
                                _next_action = f"Resolve the replay for {_battle_ref} vs {_opponent_label} before assigning a strategic failure tag."
                            else:
                                _next_action = f"Review {_battle_ref} vs {_opponent_label} and classify one cause before the next improve cycle."
                        elif _result_str == "win":
                            _next_action = ""

                        _why_it_matters_ev = (
                            f"{_result_str} vs {_opponent_label} is ladder evidence"
                        )
                        if _recent_summary:
                            _why_it_matters_ev += f"; {_recent_summary}"
                        if _result_str == "loss" and _decisive_reason:
                            _why_it_matters_ev += f"; {_decisive_reason}"
                        elif _result_str == "loss":
                            _why_it_matters_ev += "; recorded as ladder evidence only (improve loop parked: no replay-review servicer; needs a discriminating offline gate)"
                        elif _result_str == "win":
                            _why_it_matters_ev += "; keep proof focused on repeatable win conditions, not flavor text"

                        _battle_result_event_id = queue_event(
                            "battle_result",
                            "battles",
                            build_contract_payload(
                                "PROOF",
                                f"battle result {_result_str} vs {opponent_name}",
                                _what_happened,
                                _why_it_matters_ev,
                                f"battle_id={battle_tag}; result={_result_str}; team_file={_team_name_ev or 'unknown'}; opponent={opponent_name}; turns={_turn_count_ev}; replay={_queue_replay_url or ''}; replay_status={_queue_replay_status}",
                                "Append replay or ladder delta if more context lands after posting.",
                                source="fp.run_battle",
                                battle_id=battle_tag,
                                result=_result_str,
                                team_file=_team_name_ev or "unknown",
                                opponent=opponent_name,
                                turns=_turn_count_ev,
                                replay_url=_queue_replay_url,
                                replay_id=_replay_handoff.get("replay_id"),
                                replay_status=_queue_replay_status,
                                replay_public_verified=_replay_handoff.get("replay_public_verified"),
                                raw_replay_url=_replay_handoff.get("raw_replay_url"),
                                elo_before=_elo_before_final,
                                elo_after=_elo_after_final,
                                rating_delta=_elo_delta_final,
                                recent_record=_recent_summary,
                                recent_wins=_recent_wins_ev,
                                recent_losses=_recent_losses_ev,
                                recent_window_size=_recent_window_size_ev,
                                recent_streak=_recent_streak_ev,
                                decisive_reason=_decisive_reason,
                                next_battle_action=_next_action,
                            ),
                            dedup_window_sec=5,
                        )
                        _safety_alert = recent_results_safety_alert(
                            _stats_battles_for_alert,
                            _current_result_row,
                            short_window=5,
                            trend_window=20,
                            loss_streak_threshold=5,
                            low_win_rate_threshold=0.45,
                            min_decisive_for_rate=10,
                        )
                        if _safety_alert:
                            _short_record_text = str(_safety_alert.get("short_record") or "unknown")
                            if not _short_record_text.lower().startswith("last "):
                                _short_record_text = f"last 5 {_short_record_text}"
                            queue_event(
                                "performance_alert",
                                "battles",
                                build_contract_payload(
                                    "STAGNATION",
                                    str(_safety_alert.get("headline") or "Fouler recent-results safety alert"),
                                    (
                                        f"{_safety_alert.get('summary')}; "
                                        f"{_short_record_text}"
                                    ),
                                    (
                                        "A long loss streak or sub-threshold recent win rate means the ladder bot may be malfunctioning "
                                        "or no longer improving; HERMES should open a bounded repair lane before more autonomous batches."
                                    ),
                                    (
                                        f"battle_id={battle_tag}; trigger={_safety_alert.get('trigger')}; "
                                        f"trend={_safety_alert.get('trend_record')}; streak={_safety_alert.get('streak')}; "
                                        f"decisive={_safety_alert.get('decisive')}"
                                    ),
                                    "Review recent replays, runtime logs, and team/policy changes before launching another improve cycle.",
                                    source="fp.run_battle",
                                    battle_id=battle_tag,
                                    recent_record=str(_safety_alert.get("short_record") or ""),
                                    trend=str(_safety_alert.get("trend_record") or ""),
                                    performance_change=str(_safety_alert.get("summary") or ""),
                                    code_fix_hint="create a repair ticket with recent replay evidence; do not treat the batch as healthy proof",
                                    next_battle_action="pause or bound the next autonomous improve cycle until the loss pattern is classified",
                                ),
                                dedup_window_sec=900,
                            )
                        await _handoff_battle_result_to_deku(_battle_result_event_id)
                    except Exception as _qe_err:
                        logger.warning(f"Failed to queue battle_result event: {_qe_err}")

                _schedule_battle_stats_rating_enrichment(
                    battle_tag,
                    elo_before=_elo_before_final,
                    elo_after=_elo_after_final,
                    rating_delta=_elo_delta_final,
                    result_key=_battle_result_from_evidence(
                        winner,
                        getattr(getattr(battle, "user", None), "account_name", None),
                        opponent_name=opponent_name,
                    ),
                    winner=winner,
                    opponent_name=opponent_name,
                    replay_url=_queue_replay_url or _discord_replay_url or replay_url,
                )

                return winner, battle_tag

            if battle_room_closed(battle_tag, msg):
                logger.warning(f"Battle room closed without win/tie: {battle_tag}")
                await send_stream_event(
                    "BATTLE_END",
                    _operational_loss_stream_payload(
                        battle_tag,
                        reason="room_closed_without_winner",
                    ),
                )
                battle_end_event_sent = True
                return None, battle_tag

            action_required = await async_update_battle(battle, msg)
            try:
                OPPONENT_MODEL.observe(battle)
            except Exception as e:
                logger.debug(f"Opponent model update failed: {e}")

            # Send turn update for real-time OBS updates
            if action_required and "|turn|" in msg:
                try:
                    turn_num = battle.turn if hasattr(battle, "turn") else None
                    our_active = (
                        battle.user.active.name
                        if battle.user and battle.user.active
                        else None
                    )
                    opp_active = (
                        battle.opponent.active.name
                        if battle.opponent and battle.opponent.active
                        else None
                    )
                    await send_stream_event(
                        "TURN_UPDATE",
                        {
                            "id": battle_tag,
                            "turn": turn_num,
                            "our_pokemon": our_active,
                            "opponent_pokemon": opp_active,
                            "timestamp": time.time(),
                        },
                    )
                except Exception as e:
                    logger.debug(f"Failed to send turn update event: {e}")

            if action_required and not battle.wait:
                # Capture the rqid BEFORE the potentially slow move computation.
                # If the server advances while we compute (timer auto-pick),
                # a new |request| will update battle.rqid. We detect that
                # stale-rqid scenario and skip sending the outdated command.
                pre_pick_rqid = battle.rqid

                best_move = await async_pick_move(battle)

                # Guard: if new messages arrived during move computation that
                # updated the rqid, our command is stale. Skip it to avoid
                # "Invalid choice" / "nothing to choose" errors.
                if battle.rqid != pre_pick_rqid:
                    logger.info(
                        "Skipping stale command in %s: rqid changed %s -> %s "
                        "during move computation (server advanced)",
                        battle_tag,
                        pre_pick_rqid,
                        battle.rqid,
                    )
                    continue

                await ps_websocket_client.send_message(battle_tag, best_move)
    except Exception:
        logger.exception("Unhandled exception in battle loop for %s", battle_tag)
        raise
    finally:
        # Clean up gameplan and strategic cache from memory (memory leak fix)
        _elo_before_cache.pop(battle_tag, None)
        clear_gameplan(battle_tag)
        clear_battle_strategy(battle_tag)
        await _finalize_battle_runtime(
            ps_websocket_client,
            battle_tag,
            send_end_event=not battle_end_event_sent,
        )
