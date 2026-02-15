import json
import os
import asyncio
import contextvars
import time
from collections import OrderedDict
from copy import deepcopy
import logging
from logging.handlers import RotatingFileHandler
import re
import aiohttp
from datetime import datetime

from data.pkmn_sets import RandomBattleTeamDatasets, TeamDatasets
from data.pkmn_sets import SmogonSets

# ---------------------------------------------------------------------------
# Startup log cleanup
# ---------------------------------------------------------------------------
# Limits for how many files to keep per category.
LOG_KEEP_BATTLE_FILES = int(os.getenv("LOG_KEEP_BATTLE_FILES", "60"))
LOG_KEEP_TRACE_FILES = int(os.getenv("LOG_KEEP_TRACE_FILES", "500"))
LOG_KEEP_STDOUT_FILES = int(os.getenv("LOG_KEEP_STDOUT_FILES", "3"))


def cleanup_old_logs(log_dir: str = "logs", trace_dir: str | None = None):
    """Prune old battle logs, rotated backups, phantom logs, decision traces,
    and stdout logs on startup.  Keeps the most recent files by mtime."""
    _log = logging.getLogger(__name__)
    trace_dir = trace_dir or os.path.join(log_dir, "decision_traces")
    removed = 0

    # --- 1. Phantom _None.log files (always delete all — they're from dead rooms) ---
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
from config import FoulPlayConfig, SaveReplay
from fp.battle import LastUsedMove, Pokemon, Battle
from fp.battle_modifier import async_update_battle, process_battle_updates
from fp.helpers import normalize_name
from fp.search.main import find_best_move
from fp.decision_trace import write_decision_trace, build_trace_base
from fp.movepool_tracker import get_threat_category, ThreatCategory
from fp.opponent_model import OPPONENT_MODEL
from fp.hybrid_policy import run_hybrid_rerank
from fp.helpers import type_effectiveness_modifier

from fp.websocket_client import PSWebsocketClient
from streaming.state_store import write_active_battles, read_active_battles, write_status, update_daily_stats
from fp.team_analysis import analyze_team
from fp.playstyle_config import PlaystyleConfig, Playstyle, HAZARD_MOVES, PIVOT_MOVES
from fp.gameplan_integration import generate_and_store_gameplan, get_gameplan, clear_gameplan
from constants_pkg.strategy import SETUP_MOVES

logger = logging.getLogger(__name__)

# Blacklist for dead battles (forcibly terminated due to timeout)
# Prevents re-claiming the same stuck battle immediately after termination
_dead_battle_blacklist: "OrderedDict[str, float]" = OrderedDict()

# Active battles tracking for stream overlay
# battle_id -> {"opponent": str, "started": datetime, "worker_id": int | None}
_active_battles = {}
_battles_lock = asyncio.Lock()
_last_active_battles_write = 0.0
_last_active_battles_payload = None

# Battle message timeout tuning (seconds)
MESSAGE_TIMEOUT_SEC = int(os.getenv("BATTLE_MESSAGE_TIMEOUT_SEC", "120"))
STALE_STRIKES = int(os.getenv("BATTLE_STALE_STRIKES", "2"))
STALE_DISPLAY_GRACE_SEC = int(os.getenv("BATTLE_STALE_DISPLAY_GRACE_SEC", "900"))
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
REPLAY_CHECK_TTL_SEC = int(os.getenv("REPLAY_CHECK_TTL_SEC", "60"))
REPLAY_CHECK_MIN_AGE_SEC = int(os.getenv("REPLAY_CHECK_MIN_AGE_SEC", "180"))
REPLAY_CHECK_TIMEOUT_SEC = int(os.getenv("REPLAY_CHECK_TIMEOUT_SEC", "4"))
REPLAY_CACHE_MAX_ENTRIES = max(100, int(os.getenv("REPLAY_CACHE_MAX_ENTRIES", "4000")))
REPLAY_CACHE_RETENTION_SEC = max(REPLAY_CHECK_TTL_SEC * 5, 300)
DEAD_BATTLE_BLACKLIST_MAX = max(100, int(os.getenv("DEAD_BATTLE_BLACKLIST_MAX", "2000")))

# Hard battle timeout (seconds). 0 disables forced battle termination.
BATTLE_HARD_TIMEOUT_SEC = int(os.getenv("BATTLE_HARD_TIMEOUT_SEC", "0"))

# Prevents heartbeat from re-registering battles that already finished.
# Capped at 200 entries to avoid unbounded growth.
_concluded_battles: set[str] = set()
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


def _get_or_create_worker_handler(worker_id: int) -> RotatingFileHandler:
    """Return the RotatingFileHandler for *worker_id*, creating one if needed."""
    global _shared_handler_filtered
    if worker_id in _worker_handlers:
        return _worker_handlers[worker_id]
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, f"worker_{worker_id}_init.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
    )
    handler.setLevel(logging.DEBUG)
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


def _rollover_worker_handler(worker_id: int, battle_tag: str, opponent_name: str):
    """Switch worker's log handler to a new battle-specific file."""
    handler = _get_or_create_worker_handler(worker_id)
    new_name = f"{battle_tag}_{opponent_name}.log".replace("/", "_")
    handler.baseFilename = os.path.join("logs", new_name)
    # doRollover() renames the current file to .1 and opens a fresh file
    # with the new baseFilename — exactly like the original do_rollover().
    handler.doRollover()

# Battle chat defaults
OPENING_CHAT_MESSAGE = "hf"
POST_BATTLE_MESSAGES = ["gg", "ttv/thepeakmos"]

# Resume queue for in-progress battles (populated on startup from active_battles.json)
_resume_lock = asyncio.Lock()
_resume_by_worker: dict[int, list[dict]] = {}
_resume_queue: list[dict] = []
_replay_cache: dict[str, dict[str, float | bool]] = {}


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


async def _replay_exists(replay_id: str) -> bool:
    if not replay_id:
        return False
    now = time.time()
    _prune_replay_cache(now)
    cached = _replay_cache.get(replay_id)
    if cached and (now - float(cached.get("checked", 0.0))) < REPLAY_CHECK_TTL_SEC:
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


async def _post_battle_to_discord(
    battle_tag: str,
    winner: str | None,
    opponent_name: str,
    replay_url: str | None = None,
    team_name: str | None = None,
    our_player_name: str | None = None,
) -> None:
    """Post battle result to Discord webhook.
    
    Args:
        battle_tag: Battle ID
        winner: Winner's username (None for tie/forfeit)
        opponent_name: Opponent's username
        replay_url: Replay URL (if available)
        team_name: Team name used (if applicable)
        our_player_name: Our actual player name in this battle (e.g., "ALL CHUNG" or "BugInTheCode")
    """
    webhook_url = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")
    if not webhook_url:
        logger.debug("DISCORD_BATTLES_WEBHOOK_URL not configured, skipping Discord post")
        return
    
    # Get bot display name from environment (e.g., "💥 BAKUGO" or "🪲 DEKU")
    bot_display_name = os.getenv("BOT_DISPLAY_NAME", "").strip()
    if not bot_display_name:
        bot_display_name = FoulPlayConfig.username
    
    # Determine if we won by checking if winner matches any of our known accounts
    # Normalize both sides (Showdown strips spaces/special chars)
    showdown_accounts = os.getenv("SHOWDOWN_ACCOUNTS", FoulPlayConfig.username).strip().lower().split(",")
    showdown_accounts = [_normalize_username(acc) for acc in showdown_accounts if acc.strip()]
    
    is_win = winner and _normalize_username(winner) in showdown_accounts
    is_tie = winner is None or winner == "tie"
    
    # Format emoji based on result
    if is_tie:
        emoji = "🤝"
    elif is_win:
        emoji = "✅"
    else:
        emoji = "💀"
    
    # Build message
    result_text = "won" if is_win else "lost" if not is_tie else "tied"
    
    # Use the actual player name from the battle for attribution
    # If not provided, determine from winner or fall back to FoulPlayConfig.username
    if not our_player_name:
        our_player_name = winner if is_win else FoulPlayConfig.username
    
    # Build embed title showing the actual matchup
    message = f"{emoji} **{bot_display_name}** {result_text} vs **{opponent_name}**"
    
    # Add team info if available
    if team_name and team_name != "gen9ou":  # Skip if it's just the default format name
        message += f" (Team: {team_name})"
    
    # Add replay link if available
    if replay_url:
        # Normalize replay URL to strip any spectator hashes
        # Extract replay ID from URL
        replay_id = replay_url.split("/")[-1]
        # Strip hash if present (format: gen9ou-NUMBER or gen9ou-NUMBER-HASH)
        replay_id = _normalize_replay_id(replay_id)
        normalized_url = f"https://replay.pokemonshowdown.com/{replay_id}"
        
        # Wrap non-loss replays in <> to suppress Discord embed
        if is_win or is_tie:
            message += f"\n<{normalized_url}>"
        else:
            message += f"\n{normalized_url}"
    else:
        # Construct replay URL from battle_tag and verify it exists
        replay_id = _normalize_replay_id(battle_tag)
        if replay_id:
            constructed_url = f"https://replay.pokemonshowdown.com/{replay_id}"
            if await _replay_exists(replay_id):
                if is_win or is_tie:
                    message += f"\n<{constructed_url}>"
                else:
                    message += f"\n{constructed_url}"
                # else: replay doesn't exist (not uploaded), skip link
    
    # Fetch and append current ELO
    ps_username = our_player_name or FoulPlayConfig.username
    elo, gxe = await _fetch_elo(ps_username)
    if elo is not None:
        elo_line = f"📊 **ELO: {elo:.0f}**"
        if gxe is not None:
            elo_line += f" ({gxe:.1f}% GXE)"
        message += f"\n{elo_line}"

    # Send to Discord
    try:
        async with aiohttp.ClientSession() as session:
            payload = {"content": message}
            async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 204:
                    logger.info(f"Posted battle result to Discord: {result_text} vs {opponent_name}")
                else:
                    logger.warning(f"Discord webhook returned status {resp.status}")
    except asyncio.TimeoutError:
        logger.warning("Discord webhook post timed out")
    except Exception as e:
        logger.warning(f"Failed to post to Discord webhook: {e}")


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
            opponent_name = _extract_opponent_from_message(msg)

        if _resume_message_indicates_active(msg):
            queue = ps_websocket_client.battle_queues.get(battle_tag)
            if queue:
                for buffered_msg in buffered:
                    queue.put_nowait(buffered_msg)

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
    _concluded_battles.add(battle_tag)
    if len(_concluded_battles) > _CONCLUDED_BATTLES_MAX:
        # Evict oldest (arbitrary since set is unordered, but prevents unbounded growth)
        _concluded_battles.pop()


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
            "max_slots": FoulPlayConfig.max_concurrent_battles,
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

async def send_stream_event(event_type, payload):
    """Send a real-time event signal to the stream server."""
    url = "http://localhost:8777/event"
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
        if battle_copy.time_remaining is not None and battle_copy.time_remaining < 30:
            timeout = min(timeout, max(5, int(timeout * 0.6)))
        if timeout > 0:
            best_move = await asyncio.wait_for(future, timeout=timeout)
        else:
            best_move = await future
        if isinstance(best_move, tuple) and len(best_move) == 2:
            best_move, trace = best_move
    except asyncio.TimeoutError:
        logger.warning(
            "Decision timeout after %ss - using fallback move.",
            DECISION_TIMEOUT_SEC,
        )
        best_move = _fallback_decision(battle_copy)
        trace_reason = "timeout"
    except Exception as e:
        logger.error(f"MCTS error: {e}")
        logger.debug("Falling back to safe move selection")
        best_move = _fallback_decision(battle_copy)
        trace_reason = "error"

    if not best_move:
        best_move = _fallback_decision(battle_copy)
        trace_reason = trace_reason or "fallback"

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

    battle.user.last_selected_move = LastUsedMove(
        battle.user.active.name
        if battle.user.active
        else (battle_copy.user.active.name if battle_copy.user.active else ""),
        best_move.removesuffix("-tera").removesuffix("-mega"),
        battle.turn,
    )
    formatted = format_decision(battle_copy, best_move)
    if TRACE_DECISIONS and trace is not None:
        trace["formatted_choice"] = formatted
        write_decision_trace(trace)
    return formatted


def _get_best_switch(battle):
    """Pick the best available switch-in when forced to switch."""
    alive_reserves = [p for p in battle.user.reserve if p.hp > 0]
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
    """Pick a safe fallback decision if MCTS fails or returns no move."""
    try:
        if battle.force_switch:
            return _get_best_switch(battle)

        request = battle.request_json or {}
        active = request.get(constants.ACTIVE, [])
        if active:
            moves = active[0].get(constants.MOVES, [])
            for move in moves:
                if move.get(constants.DISABLED, False):
                    continue
                move_id = move.get(constants.ID) or normalize_name(move.get("move", ""))
                if not move_id:
                    continue
                if move_id == constants.HIDDEN_POWER:
                    move_id = normalize_name(move.get("move", move_id))
                return move_id

        if battle.user and battle.user.active:
            for mv in battle.user.active.moves:
                if not mv.disabled:
                    return mv.name
            alive_reserves = [p for p in battle.user.reserve if p.hp > 0]
            if alive_reserves:
                return "{} {}".format(constants.SWITCH_STRING, alive_reserves[0].name)
    except Exception as e:
        logger.warning(f"Fallback decision failed: {e}")

    # Last resort: splash (no-op). Only used if we truly have nothing else.
    return constants.DO_NOTHING_MOVE


def _choose_first_request_move_id(battle) -> str | None:
    request = getattr(battle, "request_json", None) or {}
    active = request.get(constants.ACTIVE, [])
    if not active:
        return None

    moves = active[0].get(constants.MOVES, [])
    for move in moves:
        if move.get(constants.DISABLED, False):
            continue
        if move.get(constants.PP, 1) == 0:
            continue
        move_id = move.get(constants.ID) or normalize_name(move.get("move", ""))
        if not move_id:
            continue
        if move_id == constants.HIDDEN_POWER:
            move_id = normalize_name(move.get("move", move_id))
        return move_id
    return None


def _choose_first_request_switch_slot(battle) -> int | None:
    request = getattr(battle, "request_json", None) or {}
    side = request.get(constants.SIDE, {})
    side_pokemon = side.get(constants.POKEMON, [])
    for index, pkmn in enumerate(side_pokemon, start=1):
        if pkmn.get(constants.ACTIVE, False):
            continue
        condition = str(pkmn.get(constants.CONDITION, "")).lower()
        if "fnt" in condition:
            continue
        return index
    return None


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


async def handle_team_preview(battle, ps_websocket_client):
    battle_copy = deepcopy(battle)
    battle_copy.user.active = Pokemon.get_dummy()
    battle_copy.opponent.active = Pokemon.get_dummy()
    battle_copy.team_preview = True

    # Try heuristic lead selection first (more stable than MCTS in team preview)
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
        best_move = [f"/switch {lead_pick.index}", str(battle.rqid)]
    else:
        best_move = await async_pick_move(battle_copy)

    # because we copied the battle before sending it in, we need to update the last selected move here
    pkmn_name = battle.user.reserve[int(best_move[0].split()[1]) - 1].name
    battle.user.last_selected_move = LastUsedMove(
        "teampreview", "switch {}".format(pkmn_name), battle.turn
    )

    size_of_team = len(battle.user.reserve) + 1
    team_list_indexes = list(range(1, size_of_team))
    choice_digit = int(best_move[0].split()[-1])

    team_list_indexes.remove(choice_digit)
    message = [
        "/team {}{}|{}".format(
            choice_digit, "".join(str(x) for x in team_list_indexes), battle.rqid
        )
    ]

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
                    await _requeue_resume_entry(resume_entry, "timeout")
                # If battle is closed/finished, drop and continue to next entry.

    battle_tag_pattern = re.compile(r'^>(battle-[a-z0-9-]+)')

    while True:
        if stop_event is not None and stop_event.is_set():
            _release_search("stopped")
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
                opponent_name = _extract_opponent_from_message(msg)
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

            opponent_name = _extract_opponent_from_message(msg)
            if opponent_name:
                logger.info("Initialized {} against: {}".format(battle_tag, opponent_name))
                return battle_tag, opponent_name, False, None
            # If opponent not found yet, return now; start_battle_common will
            # parse player lines from the battle queue.
            return battle_tag, None, False, None


def _extract_opponent_from_message(msg):
    """Extract opponent name from a battle message. Returns None if not found."""
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
                        if player.lower() != FoulPlayConfig.username.lower():
                            return player
    
    # Try |player| format
    for line in msg.split("\n"):
        if "|player|" in line:
            parts = line.split("|")
            if len(parts) >= 4:
                player_name = parts[3]
                if player_name.lower() != FoulPlayConfig.username.lower():
                    return player_name

    # Fallback: vs. format anywhere in message
    if "vs." in msg:
        opponent_name = msg.split("vs.")[1].split("|")[0].strip()
        if opponent_name.lower() != FoulPlayConfig.username.lower():
            return opponent_name

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
            inferred = _extract_opponent_from_message(msg)
            if inferred:
                battle.opponent.account_name = inferred

        if "|player|" in msg:
            # Get list of our known accounts (normalized for Showdown comparison)
            showdown_accounts = os.getenv("SHOWDOWN_ACCOUNTS", FoulPlayConfig.username).strip().lower().split(",")
            showdown_accounts = [_normalize_username(acc) for acc in showdown_accounts if acc.strip()]
            
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
        SmogonSets.initialize(
            FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
        )
        TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)

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
            SmogonSets.initialize(
                FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
            )
            TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)

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
            SmogonSets.initialize(
                FoulPlayConfig.smogon_stats or pokemon_battle_type, unique_pkmn_names
            )
            TeamDatasets.initialize(pokemon_battle_type, unique_pkmn_names)

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

    if FoulPlayConfig.spectator_username:
        logger.info(f"Inviting spectator: {FoulPlayConfig.spectator_username}")
        await ps_websocket_client.send_message(battle.battle_tag, [f"/invite {FoulPlayConfig.spectator_username}"])

    return battle


async def _finalize_battle_runtime(
    ps_websocket_client: PSWebsocketClient,
    battle_tag: str,
    *,
    send_end_event: bool,
) -> None:
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
                {
                    "id": battle_tag,
                    "winner": None,
                    "ended": time.time(),
                },
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
        logger.info(f"🎮 GAMEPLAN GENERATED: {gameplan.our_strategy}")
        logger.info(f"📌 OUR WIN CONDITION: {gameplan.win_condition}")
        logger.info(f"⚔️ OPPONENT WIN CONDITION: {gameplan.opponent_win_condition}")
        logger.info(f"🔄 KEY PIVOTS: {', '.join(gameplan.key_pivot_triggers)}")
        logger.info(f"💡 BACKUP PLAN: {gameplan.backup_plan or 'None'}")
    else:
        battle.gameplan = None
        logger.warning(f"Failed to generate gameplan for {battle_tag}")

    timeout_strikes = 0
    message_timeout = MESSAGE_TIMEOUT_SEC
    battle_start_time = time.time()
    battle_end_event_sent = False
    last_heartbeat = time.time()

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
                    await send_stream_event(
                        "BATTLE_END",
                        {
                            "id": battle_tag,
                            "winner": None,
                            "ended": time.time(),
                        },
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
                winner = (
                    msg.split(constants.WIN_STRING)[-1].split("\n")[0].strip()
                    if constants.WIN_STRING in msg
                    else None
                )
                logger.info("Battle finished: %s Winner: %s", battle_tag, winner)
                await _send_battle_chat(ps_websocket_client, battle_tag, POST_BATTLE_MESSAGES)

                # Save replay and capture URL if configured
                replay_url = None

                # Check if winner is one of our accounts (normalize for Showdown's format)
                showdown_accounts = os.getenv(
                    "SHOWDOWN_ACCOUNTS", FoulPlayConfig.username
                ).strip().lower().split(",")
                showdown_accounts = [
                    _normalize_username(acc) for acc in showdown_accounts if acc.strip()
                ]
                we_won = winner and _normalize_username(winner) in showdown_accounts

                if (
                    FoulPlayConfig.save_replay == SaveReplay.always
                    or (
                        FoulPlayConfig.save_replay == SaveReplay.on_loss and not we_won
                    )
                    or (
                        FoulPlayConfig.save_replay == SaveReplay.on_win and we_won
                    )
                ):
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
                    else None
                )
                await _post_battle_to_discord(
                    battle_tag=battle_tag,
                    winner=winner,
                    opponent_name=opponent_name,
                    replay_url=replay_url,
                    team_name=team_name,
                    our_player_name=our_player_name,
                )

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
                    showdown_accounts_stats = os.getenv(
                        "SHOWDOWN_ACCOUNTS", FoulPlayConfig.username
                    ).strip().lower().split(",")
                    showdown_accounts_stats = [
                        _normalize_username(acc)
                        for acc in showdown_accounts_stats
                        if acc.strip()
                    ]
                    is_win = winner and _normalize_username(winner) in showdown_accounts_stats

                    if winner and winner != "None":
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
                                f"vs {winner}"
                                if not is_win and winner
                                else "Searching..."
                            ),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to update stream status: {e}")

                await send_stream_event(
                    "BATTLE_END",
                    {
                        "id": battle_tag,
                        "winner": winner,
                        "ended": time.time(),
                    },
                )
                battle_end_event_sent = True
                return winner, battle_tag

            if battle_room_closed(battle_tag, msg):
                logger.warning(f"Battle room closed without win/tie: {battle_tag}")
                await send_stream_event(
                    "BATTLE_END",
                    {
                        "id": battle_tag,
                        "winner": None,
                        "ended": time.time(),
                    },
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
                best_move = await async_pick_move(battle)
                await ps_websocket_client.send_message(battle_tag, best_move)
    except Exception:
        logger.exception("Unhandled exception in battle loop for %s", battle_tag)
        raise
    finally:
        # Clean up gameplan from memory
        clear_gameplan(battle_tag)
        await _finalize_battle_runtime(
            ps_websocket_client,
            battle_tag,
            send_end_event=not battle_end_event_sent,
        )
