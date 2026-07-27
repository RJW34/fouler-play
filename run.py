import asyncio
import faulthandler
import json
import logging
import traceback
import sys
import ctypes
import os
import subprocess
import time
import uuid
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType

# Load .env so webhook URLs and other config are available to submodules
_dotenv_loaded = False
try:
    from dotenv import load_dotenv
    _default_env_file = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "HERMES"
        / "secrets"
        / "fouler.env"
        if os.name == "nt"
        else Path.home() / ".config" / "deku-devstream" / "secrets" / "fouler.env"
    )
    _dotenv_loaded = load_dotenv(
        Path(os.getenv("FOULER_ENV_FILE", str(_default_env_file))).expanduser()
    )
except ImportError:
    pass  # dotenv not installed; rely on systemd EnvironmentFile

from config import FoulPlayConfig, init_logging, BotModes  # noqa: E402
import constants  # noqa: E402

from teams import load_team, TeamListIterator  # noqa: E402
from fp.run_battle import (  # noqa: E402
    pokemon_battle,
    get_active_battle_count,
    get_resume_pending_count,
    get_resume_battle_ids,
    has_resume_battle,
    prime_resume_battles,
    cleanup_old_logs,
    _current_worker_id,
)
from fp.websocket_client import PSWebsocketClient  # noqa: E402

from data import all_move_json  # noqa: E402
from data import pokedex  # noqa: E402
from data.mods.apply_mods import apply_mods  # noqa: E402

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_STATE_ROOT = Path(
    os.getenv("FOULER_RUNTIME_STATE_ROOT", str(PROJECT_ROOT))
).expanduser().absolute()

try:
    faulthandler.enable(all_threads=True)
except Exception:
    pass

DRAIN_FILE = RUNTIME_STATE_ROOT / "pids" / "drain.request"
PARENT_PID = int(os.getenv("FP_PARENT_PID", "0") or 0)
PARENT_CHECK_SEC = int(os.getenv("FP_PARENT_CHECK_SEC", "5") or 5)
def _strip_env_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = _strip_env_inline_comment(raw).strip().strip('"').strip("'").lower()
    if not value:
        return default
    return value not in ("0", "false", "no", "off")


LOSS_DRAIN_ENV = _strip_env_inline_comment(os.getenv("LOSS_TRIGGERED_DRAIN", "1")).strip().lower()
LOSS_DRAIN_ENABLED = _env_bool("LOSS_TRIGGERED_DRAIN", default=True)


REQUIRED_CONSTANTS = {
    "BASE_POWER": "basePower",
    "CATEGORY": "category",
    "TYPE": "type",
    "MOVES": "moves",
    "ID": "id",
    "PP": "pp",
    "ABILITY": "ability",
    "ITEM": "item",
    "DETAILS": "details",
    "CONDITION": "condition",
    "STATS": "stats",
    "TYPES": "types",
}


def validate_constants():
    missing = []
    mismatched = []
    for name, expected in REQUIRED_CONSTANTS.items():
        if not hasattr(constants, name):
            missing.append(name)
            continue
        if expected is not None:
            actual = getattr(constants, name)
            if actual != expected:
                mismatched.append((name, actual, expected))
    if missing or mismatched:
        if missing:
            logger.critical("Missing required constants: %s", ", ".join(missing))
        for name, actual, expected in mismatched:
            logger.critical(
                "Constant %s mismatch: expected '%s', got '%s'",
                name,
                expected,
                actual,
            )
        raise RuntimeError("Constants validation failed")


def check_dictionaries_are_unmodified(original_pokedex, original_move_json):
    # The bot should not modify the data dictionaries
    # This is a "just-in-case" check to make sure and will stop the bot if it mutates either of them
    if original_move_json != all_move_json:
        logger.critical(
            "Move JSON changed!\nDumping modified version to `modified_moves.json`"
        )
        RUNTIME_STATE_ROOT.mkdir(parents=True, exist_ok=True)
        with (RUNTIME_STATE_ROOT / "modified_moves.json").open("w", encoding="utf-8") as f:
            json.dump(all_move_json, f, indent=4)
        exit(1)
    else:
        logger.debug("Move JSON unmodified!")

    if original_pokedex != pokedex:
        logger.critical(
            "Pokedex JSON changed!\nDumping modified version to `modified_pokedex.json`"
        )
        RUNTIME_STATE_ROOT.mkdir(parents=True, exist_ok=True)
        with (RUNTIME_STATE_ROOT / "modified_pokedex.json").open("w", encoding="utf-8") as f:
            json.dump(pokedex, f, indent=4)
        exit(1)
    else:
        logger.debug("Pokedex JSON unmodified!")


BATTLE_STATS_FILE = RUNTIME_STATE_ROOT / "battle_stats.json"
_DEFAULT_ACCOUNT_SEASON_FILE = (
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    / "HERMES"
    / "authority"
    / "fouler"
    / "account-season.json"
    if os.name == "nt"
    else Path.home()
    / ".config"
    / "deku-devstream"
    / "authority"
    / "fouler"
    / "account-season.json"
)
ACCOUNT_SEASON_FILE = Path(
    os.getenv("FOULER_ACCOUNT_SEASON_PATH", str(_DEFAULT_ACCOUNT_SEASON_FILE))
).expanduser().absolute()

_OPTIONAL_BATTLE_PROVENANCE_ENV = (
    ("FOULER_CHANGE_ID", "change_id"),
    ("FOULER_DEPLOYMENT_ID", "deployment_id"),
    ("FOULER_RUNTIME_LEASE_ID", "runtime_lease_id"),
    ("FOULER_RUNTIME_AUTHORIZATION_SHA256", "runtime_authorization_sha256"),
    ("FOULER_SOURCE_TREE", "source_tree"),
    ("FOULER_RUNTIME_MANIFEST_DIGEST", "runtime_manifest_digest"),
    ("FOULER_DEPLOYMENT_RECEIPT_SHA256", "deployment_receipt_sha256"),
    ("FOULER_PHYSICAL_HOSTNAME", "physical_hostname"),
    ("FOULER_PHYSICAL_HOST_ID_SHA256", "physical_host_id_sha256"),
    ("FOULER_H2H_RUN_ID", "h2h_run_id"),
    ("FOULER_H2H_CELL_ID", "h2h_cell_id"),
    ("FOULER_H2H_ARM", "h2h_arm"),
    ("FOULER_H2H_ROLE", "h2h_role"),
    ("FOULER_H2H_TEAM", "h2h_team"),
    ("FOULER_H2H_ACCOUNT", "h2h_account"),
    ("FOULER_H2H_OPPONENT", "h2h_opponent"),
    ("FOULER_H2H_BASELINE_COMMIT", "h2h_baseline_commit"),
    ("FOULER_H2H_CANDIDATE_PATCH_SHA256", "h2h_candidate_patch_sha256"),
    ("FOULER_H2H_ENGINE_DIGEST", "h2h_engine_digest"),
    ("FOULER_H2H_CHANGE_ID", "h2h_change_id"),
)


def _provenance_env_value(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _git_head_source_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"

    if result.returncode != 0:
        return "unknown"
    return str(result.stdout or "").strip() or "unknown"


def _build_process_battle_provenance() -> dict[str, str]:
    source_commit = _provenance_env_value("FOULER_SOURCE_COMMIT")
    if not source_commit:
        source_commit = _git_head_source_commit()

    provenance = {
        "source_commit": source_commit,
        "session_id": _provenance_env_value("FOULER_SESSION_ID") or str(uuid.uuid4()),
    }
    for env_name, row_key in _OPTIONAL_BATTLE_PROVENANCE_ENV:
        value = _provenance_env_value(env_name)
        if value:
            provenance[row_key] = value
    return provenance


BATTLE_ROW_PROVENANCE = MappingProxyType(_build_process_battle_provenance())


def _normalized_account_id(value) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _active_account_scope(*, require_authority: bool = False) -> tuple[str, str]:
    account = str(getattr(FoulPlayConfig, "username", "") or "").strip()
    try:
        payload = json.loads(ACCOUNT_SEASON_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        if require_authority:
            raise RuntimeError(
                f"protected account-season authority is unavailable: {exc}"
            ) from exc
        return account, ""

    if not isinstance(payload, dict):
        if require_authority:
            raise RuntimeError("protected account-season authority must be a JSON object")
        return account, ""
    schema_version = payload.get("schemaVersion")
    season_account = str(payload.get("account") or "").strip()
    season_id = str(payload.get("seasonId") or "").strip()
    authority_valid = (
        schema_version == "fouler-play-account-season/v1"
        and bool(_normalized_account_id(season_account))
        and bool(season_id)
        and _normalized_account_id(season_account) == _normalized_account_id(account)
    )
    if not authority_valid:
        if require_authority:
            raise RuntimeError(
                "protected account-season authority does not match the configured account"
            )
        return account, ""
    return account, season_id


def _battle_stats_max_entries() -> int:
    raw = os.getenv("BATTLE_STATS_MAX_ENTRIES", "5000").strip()
    try:
        return max(100, int(raw))
    except ValueError:
        return 5000


BATTLE_STATS_MAX_ENTRIES = _battle_stats_max_entries()


class BattleStats:
    """Thread-safe battle statistics tracker with per-team persistence"""
    def __init__(self):
        self.wins = 0
        self.losses = 0
        self.disconnects = 0
        self.battles_run = 0
        self._lock = asyncio.Lock()
        self._battles = self._load_battles()

    def _load_battles(self):
        try:
            if BATTLE_STATS_FILE.exists():
                data = json.loads(BATTLE_STATS_FILE.read_text(encoding="utf-8"))
                battles = data.get("battles", [])
                if not isinstance(battles, list):
                    return []
                account, season_id = _active_account_scope()
                account_id = _normalized_account_id(account)
                tagged_accounts_present = any(
                    _normalized_account_id(row.get("account"))
                    for row in battles
                    if isinstance(row, dict)
                )
                if account_id and tagged_accounts_present:
                    battles = [
                        row
                        for row in battles
                        if isinstance(row, dict)
                        and _normalized_account_id(row.get("account")) == account_id
                    ]
                tagged_seasons_present = any(
                    str(row.get("season_id") or row.get("seasonId") or "").strip()
                    for row in battles
                    if isinstance(row, dict)
                )
                if season_id and tagged_seasons_present:
                    battles = [
                        row
                        for row in battles
                        if str(row.get("season_id") or row.get("seasonId") or "").strip()
                        == season_id
                    ]
                if len(battles) > BATTLE_STATS_MAX_ENTRIES:
                    battles = battles[-BATTLE_STATS_MAX_ENTRIES:]
                return battles
        except Exception as e:
            logger.warning("Failed to load battle_stats.json: %s", e)
        return []

    def _save_battles(self):
        temporary = BATTLE_STATS_FILE.with_name(
            f".{BATTLE_STATS_FILE.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        try:
            data = {"battles": self._battles}
            BATTLE_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, BATTLE_STATS_FILE)
        except Exception as e:
            logger.warning("Failed to save battle_stats.json: %s", e)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _record_battle(self, team_file_name, result, battle_tag=None, rating=None):
        from datetime import datetime, timezone
        account, season_id = _active_account_scope()
        entry = {
            "battle_id": battle_tag or "unknown",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "team_file": team_file_name or "unknown",
            "result": result,
            "replay_id": battle_tag or "",
            "rating": rating,
            "account": account,
            "format": str(getattr(FoulPlayConfig, "pokemon_format", "") or ""),
        }
        if season_id:
            entry["season_id"] = season_id
        entry.update(BATTLE_ROW_PROVENANCE)
        battle_identity = str(entry.get("battle_id") or "").strip().lower()
        account_identity = _normalized_account_id(entry.get("account"))
        existing_index = next(
            (
                index
                for index, row in enumerate(self._battles)
                if isinstance(row, dict)
                and battle_identity
                and battle_identity != "unknown"
                and str(row.get("battle_id") or "").strip().lower() == battle_identity
                and _normalized_account_id(row.get("account")) == account_identity
            ),
            None,
        )
        if existing_index is None:
            self._battles.append(entry)
        else:
            # A resumed websocket can deliver the terminal event more than
            # once. Replace the same account+battle identity atomically rather
            # than emitting a second result row and double-consuming a season.
            self._battles[existing_index] = entry
        if len(self._battles) > BATTLE_STATS_MAX_ENTRIES:
            del self._battles[:-BATTLE_STATS_MAX_ENTRIES]
        self._save_battles()

    async def record_win(self, team_file_name, battle_tag=None, rating=None):
        async with self._lock:
            self.wins += 1
            self.battles_run += 1
            self._record_battle(team_file_name, "win", battle_tag, rating=rating)
            logger.info("Won with team: {}".format(team_file_name))
            logger.info("W: {}\tL: {}".format(self.wins, self.losses))

    async def record_loss(self, team_file_name, battle_tag=None, rating=None):
        async with self._lock:
            self.losses += 1
            self.battles_run += 1
            self._record_battle(team_file_name, "loss", battle_tag, rating=rating)
            logger.info("Lost with team: {}".format(team_file_name))
            logger.info("W: {}\tL: {}".format(self.wins, self.losses))

    async def record_disconnect(self, team_file_name, battle_tag=None, rating=None):
        async with self._lock:
            self.disconnects += 1
            self.losses += 1
            self.battles_run += 1
            self._record_battle(team_file_name, "loss", battle_tag, rating=rating)
            logger.info("Disconnect/timeout loss with team: {}".format(team_file_name))
            logger.info("W: {}\tL: {}\tDC: {}".format(self.wins, self.losses, self.disconnects))

    async def get_battles_run(self):
        async with self._lock:
            return self.battles_run

    async def get_summary(self):
        """Return a snapshot of current stats."""
        async with self._lock:
            return {
                "wins": self.wins,
                "losses": self.losses,
                "disconnects": self.disconnects,
                "battles_run": self.battles_run,
            }

    def get_per_team_stats(self):
        """Compute per-team win/loss/disconnect counts from battle history."""
        team_stats = {}
        for entry in self._battles:
            team = entry.get("team_file", "unknown")
            result = entry.get("result", "unknown")
            if team not in team_stats:
                team_stats[team] = {"wins": 0, "losses": 0, "disconnects": 0, "total": 0}
            team_stats[team]["total"] += 1
            if result == "win":
                team_stats[team]["wins"] += 1
            elif result in {"loss", "disconnect", "timeout", "tie", "draw"}:
                team_stats[team]["losses"] += 1
                if result in {"disconnect", "timeout"}:
                    team_stats[team]["disconnects"] += 1
        return team_stats


async def _forfeit_pending_battle_tags(ps_websocket_client, battle_tags, *, reason: str) -> list[str]:
    """Forfeit and detach pending Showdown rooms that must not be resumed."""
    cleaned = []
    seen = set()
    for tag in list(battle_tags):
        if not tag or tag in seen:
            continue
        seen.add(tag)
        logger.info("Forfeiting pending battle %s (%s)", tag, reason)
        try:
            await ps_websocket_client.forfeit_battle(tag)
        except Exception as e:
            logger.warning("Failed to forfeit pending battle %s: %s", tag, e)
        try:
            await ps_websocket_client.leave_battle(tag)
        except Exception:
            pass
        try:
            ps_websocket_client.unregister_battle(tag)
        except Exception:
            pass
        try:
            ps_websocket_client.pending_battle_messages.pop(tag, None)
        except Exception:
            pass
        try:
            ps_websocket_client.pending_battle_times.pop(tag, None)
        except Exception:
            pass
        cleaned.append(tag)
    if cleaned:
        await asyncio.sleep(1)
    return cleaned


# Keep global reference to prevent GC
_win_handler_ref = None

def setup_windows_handler(loop, shutdown_event, drain_event):
    def handler(dwCtrlType):
        # CTRL_C_EVENT = 0, CTRL_BREAK_EVENT = 1, CTRL_CLOSE_EVENT = 2
        if dwCtrlType in (0, 1, 2):
            try:
                if drain_event.is_set():
                    # Second signal -> force shutdown
                    print(f"[INFO] Shutdown signal received ({dwCtrlType}). Forcing shutdown...")
                    asyncio.run_coroutine_threadsafe(
                        shutdown_event_setter(shutdown_event), loop
                    )
                else:
                    # First signal -> drain (no new battles)
                    print(
                        f"[INFO] Shutdown signal received ({dwCtrlType}). "
                        "Entering drain mode: no new battles will be queued."
                    )
                    asyncio.run_coroutine_threadsafe(
                        shutdown_event_setter(drain_event), loop
                    )
            except Exception as e:
                print(f"[ERROR] Failed to set shutdown/drain event: {e}")
            return True
        return False

    async def shutdown_event_setter(event):
        event.set()

    WINFUNCTYPE = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
    global _win_handler_ref
    _win_handler_ref = WINFUNCTYPE(handler)
    
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleCtrlHandler(_win_handler_ref, True)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    return False
                output = (result.stdout or "") + (result.stderr or "")
                if "No tasks are running" in output:
                    return False
                return str(pid) in output
            except Exception:
                return False
        return False


async def _watch_parent_process(parent_pid: int, shutdown_event: asyncio.Event) -> None:
    if parent_pid <= 0:
        return
    while not shutdown_event.is_set():
        if not _pid_exists(parent_pid):
            logger.warning("Parent process %s not found; shutting down.", parent_pid)
            shutdown_event.set()
            return
        await asyncio.sleep(PARENT_CHECK_SEC)


async def battle_worker(
    worker_id: int,
    ps_websocket_client: PSWebsocketClient,
    stats: BattleStats,
    team_iterator,
    original_pokedex,
    original_move_json,
    use_search_manager: bool,
    shutdown_event: asyncio.Event,
    drain_event: asyncio.Event,
    assigned_team: str = None,
    per_worker_quota: int = 0,
    cached_team_dict=None,
):
    """Worker that continuously runs battles until shutdown or run_count reached"""
    # Set worker context so per-worker log handlers receive only this worker's records
    _current_worker_id.set(worker_id)
    logger.info(f"Battle worker {worker_id} started" + (f" (quota: {per_worker_quota})" if per_worker_quota > 0 else ""))
    worker_battles = 0

    while not shutdown_event.is_set():
        if drain_event.is_set():
            logger.info(f"Worker {worker_id}: Drain mode active, stopping before new battle")
            break
        # Check per-worker quota (authoritative when set â€” each worker
        # plays exactly its share, no global race condition).
        if per_worker_quota > 0 and worker_battles >= per_worker_quota:
            logger.info(f"Worker {worker_id}: Per-worker quota reached ({worker_battles}/{per_worker_quota}), stopping")
            break
        # Global run_count fallback only when per-worker quotas are NOT set
        # (single-worker mode or unlimited).  When quotas are active, the
        # global check races between workers and causes uneven allocation
        # (e.g. 10/9/11 instead of 10/10/10).
        if per_worker_quota <= 0:
            battles_run = await stats.get_battles_run()
            if battles_run >= FoulPlayConfig.run_count:
                logger.info(f"Worker {worker_id}: Run count reached, stopping")
                break

        try:
            search_slot_acquired = False
            resume_ready = False
            start_search = False

            # Determine if a resume battle is available for this worker.
            # Skip resumes when per-worker quotas are active â€” resumed
            # battles would push the worker over its 10-game quota.
            if FoulPlayConfig.bot_mode == BotModes.search_ladder:
                resume_ready = await has_resume_battle(worker_id) if per_worker_quota <= 0 else False
                while (
                    get_active_battle_count() >= FoulPlayConfig.max_concurrent_battles
                    and not resume_ready
                    and not drain_event.is_set()
                ):
                    await asyncio.sleep(5)
                    resume_ready = await has_resume_battle(worker_id)
                if drain_event.is_set():
                    logger.info(f"Worker {worker_id}: Drain mode active, stopping before new battle")
                    break

                # Only acquire a search slot when we need a new ladder battle
                if not use_search_manager and not resume_ready and not drain_event.is_set():
                    await ps_websocket_client.acquire_search_slot(worker_id)
                    search_slot_acquired = True
                    start_search = True

            # Get team for this battle
            team_packed = None
            team_dict = None
            team_file_name = "None"

            if FoulPlayConfig.requires_team():
                # FIX: In search_manager mode, use the cached team_dict that
                # was loaded once before workers started. This ensures all
                # workers share the same team_dict that matches the /utm sent
                # to the server, avoiding the matches=1/6 mismatch.
                if use_search_manager and cached_team_dict is not None:
                    team_dict = cached_team_dict
                    team_file_name = FoulPlayConfig.team_name or "cached"
                    # Don't send /utm again -- it was already sent once
                else:
                    # Priority: assigned_team (fixed per-worker) > team_iterator (cycling) > team_name (single)
                    if assigned_team is not None:
                        team_name = assigned_team
                    elif team_iterator is not None:
                        team_name = team_iterator.get_next_team()
                    else:
                        team_name = FoulPlayConfig.team_name
                    team_packed, team_dict, team_file_name = load_team(team_name)
                    logger.info(f"Team selected: {team_name} -> {team_file_name}")

                    # Only update the server team when starting a new ladder search,
                    # or always when using the global search manager (single-team mode).
                    if (
                        FoulPlayConfig.bot_mode != BotModes.search_ladder
                        or start_search
                    ):
                        await ps_websocket_client.update_team(team_packed)
            else:
                if (
                    FoulPlayConfig.bot_mode != BotModes.search_ladder
                    or start_search
                    or use_search_manager
                ):
                    await ps_websocket_client.update_team("None")

            # Search for a match
            if FoulPlayConfig.bot_mode == BotModes.search_ladder:
                if start_search:
                    if drain_event.is_set():
                        logger.info(f"Worker {worker_id}: Drain mode active, skipping search")
                        if search_slot_acquired:
                            ps_websocket_client.release_search_slot(worker_id, "drain mode")
                        break
                    await ps_websocket_client.search_for_match(FoulPlayConfig.pokemon_format)
                elif drain_event.is_set():
                    logger.info(f"Worker {worker_id}: Drain mode active, skipping search")
                    if search_slot_acquired:
                        ps_websocket_client.release_search_slot(worker_id, "drain mode")
                    break
            else:
                # For challenge modes, only one worker should be active
                if worker_id != 0:
                    logger.info(f"Worker {worker_id}: Challenge mode only supports 1 worker, stopping")
                    break
                if FoulPlayConfig.bot_mode == BotModes.challenge_user:
                    await ps_websocket_client.challenge_user(
                        FoulPlayConfig.user_to_challenge,
                        FoulPlayConfig.pokemon_format,
                    )
                elif FoulPlayConfig.bot_mode == BotModes.accept_challenge:
                    await ps_websocket_client.accept_challenge(
                        FoulPlayConfig.pokemon_format, FoulPlayConfig.room_name
                    )

            # Run the battle
            winner, battle_tag = await pokemon_battle(
                ps_websocket_client,
                FoulPlayConfig.pokemon_format,
                team_dict,
                stop_event=drain_event,
                worker_id=worker_id,
            )

            if battle_tag is None and drain_event.is_set():
                logger.info(f"Worker {worker_id}: Drain mode active, exiting")
                break
            if battle_tag is None:
                logger.info(
                    "Worker %s: no battle was claimed before search timeout; "
                    "retrying without recording a battle result.",
                    worker_id,
                )
                continue

            # Record result â€” ALL outcomes count toward quota.
            # CRITICAL: worker_battles MUST increment even if recording fails,
            # otherwise the per-worker quota never fires and the bot runs forever.
            worker_battles += 1
            lost_battle = False
            try:
                _post_elo = None
                try:
                    from fp.run_battle import _fetch_elo
                    _post_elo, _ = await _fetch_elo(FoulPlayConfig.username)
                except Exception:
                    pass

                if winner == FoulPlayConfig.username:
                    await stats.record_win(team_file_name, battle_tag, rating=_post_elo)
                elif winner is None:
                    await stats.record_disconnect(team_file_name, battle_tag, rating=_post_elo)
                    lost_battle = True
                else:
                    await stats.record_loss(team_file_name, battle_tag, rating=_post_elo)
                    lost_battle = True
            except Exception as rec_err:
                logger.error(f"Worker {worker_id}: Failed to record battle result: {rec_err}")

            if per_worker_quota > 0:
                print(f"[QUOTA] Worker {worker_id}: battle {worker_battles}/{per_worker_quota}", flush=True)

            check_dictionaries_are_unmodified(original_pokedex, original_move_json)

            if lost_battle:
                if LOSS_DRAIN_ENABLED:
                    if not drain_event.is_set():
                        logger.info(
                            "Worker %s: loss detected (%s); entering drain mode.",
                            worker_id,
                            battle_tag or "unknown",
                        )
                        drain_event.set()
                        try:
                            if not DRAIN_FILE.exists():
                                DRAIN_FILE.write_text(
                                    json.dumps(
                                        {
                                            "reason": "loss_triggered",
                                            "worker_id": worker_id,
                                            "battle_tag": battle_tag,
                                            "timestamp": time.time(),
                                        }
                                    )
                                )
                        except Exception as e:
                            logger.warning(
                                "Failed to write loss-triggered drain request: %s",
                                e,
                            )
                    logger.info(
                        "Worker %s: stopping after loss per protocol.",
                        worker_id,
                    )
                    break
                logger.info(
                    "Worker %s: loss detected (%s); loss-triggered drain disabled "
                    "(LOSS_TRIGGERED_DRAIN=%s). Continuing.",
                    worker_id,
                    battle_tag or "unknown",
                    LOSS_DRAIN_ENV,
                )

        except asyncio.CancelledError:
            logger.info(f"Worker {worker_id}: Cancelled")
            break
        except Exception as e:
            logger.error(f"Worker {worker_id} error: {e}")
            logger.error(traceback.format_exc())
            # Brief pause before retrying
            await asyncio.sleep(5)
        finally:
            if search_slot_acquired and ps_websocket_client.owns_search_slot(worker_id):
                # If we still own the slot here, the battle never started; cancel search and release.
                try:
                    await ps_websocket_client.cancel_search()
                except Exception:
                    pass
                ps_websocket_client.release_search_slot(worker_id, "cleanup")

    logger.info(f"Battle worker {worker_id} stopped")


def _battle_worker_quotas(
    *,
    bot_mode: BotModes,
    max_concurrent_battles: int,
    run_count: int,
) -> list[int]:
    """Return a bounded worker plan without zero-quota live workers."""
    if run_count <= 0:
        raise ValueError("run_count must be positive")
    requested_workers = (
        max(1, int(max_concurrent_battles))
        if bot_mode == BotModes.search_ladder
        else 1
    )
    finite_run = run_count <= 999999
    num_workers = min(requested_workers, run_count) if finite_run else requested_workers
    if num_workers == 1:
        return [0]
    if not finite_run:
        return [0] * num_workers
    base, remainder = divmod(run_count, num_workers)
    return [base + (1 if index < remainder else 0) for index in range(num_workers)]


async def run_foul_play(*, offline_eval_authority: object | None = None):
    FoulPlayConfig.configure()
    init_logging(FoulPlayConfig.log_level, FoulPlayConfig.log_to_file)

    if offline_eval_authority is None:
        _active_account_scope(require_authority=True)

    from process_lock import acquire_lock, set_runtime_reservation_outcome

    if not acquire_lock(
        username=FoulPlayConfig.username,
        bot_mode=FoulPlayConfig.bot_mode,
        websocket_uri=FoulPlayConfig.websocket_uri,
        run_count=FoulPlayConfig.run_count,
        max_concurrent_battles=FoulPlayConfig.max_concurrent_battles,
        search_parallelism=FoulPlayConfig.parallelism,
        replay_behavior=FoulPlayConfig.save_replay,
        pokemon_format=FoulPlayConfig.pokemon_format,
        team_name=getattr(
            FoulPlayConfig,
            "team_name",
            FoulPlayConfig.pokemon_format,
        ),
        offline_eval_authority=offline_eval_authority,
    ):
        raise RuntimeError("runtime authority or singleton process lock was not acquired")

    # Prune old logs before they eat the disk
    try:
        cleanup_old_logs()
    except Exception as e:
        logger.warning(f"Log cleanup failed: {e}")

    # Log .env status for debugging
    logger.info(f".env loading: {'success' if _dotenv_loaded else 'skipped/failed (using systemd EnvironmentFile)'}")
    discord_webhook = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")
    if discord_webhook:
        logger.info("Discord battle reporting: ENABLED")
    else:
        logger.warning("Discord battle reporting: DISABLED (DISCORD_BATTLES_WEBHOOK_URL not set)")
    logger.info("Decision policy: %s", FoulPlayConfig.decision_policy)
    if FoulPlayConfig.decision_policy == "hybrid":
        if FoulPlayConfig.openai_api_key:
            logger.info(
                "Hybrid reranker: enabled (model=%s, timeout=%.1fs, top_k=%s)",
                FoulPlayConfig.openai_model,
                FoulPlayConfig.llm_timeout_sec,
                FoulPlayConfig.llm_rerank_top_k,
            )
        else:
            logger.warning(
                "Hybrid policy selected but player API key not set "
                "(OPENAI_API_KEY_PLAYER or OPENAI_API_KEY); "
                "bot will run eval-only until a key is provided."
            )
    if getattr(FoulPlayConfig, "openai_api_key_learner", None):
        logger.info("Learner API key detected (OPENAI_API_KEY_LEARNER).")
    else:
        logger.info("Learner API key not set (optional).")
    
    apply_mods(FoulPlayConfig.pokemon_format)
    validate_constants()

    original_pokedex = deepcopy(pokedex)
    original_move_json = deepcopy(all_move_json)

    ps_websocket_client = await PSWebsocketClient.create(
        FoulPlayConfig.username, FoulPlayConfig.password, FoulPlayConfig.websocket_uri,
        expected_format=FoulPlayConfig.pokemon_format  # CRITICAL FIX: Validate battle format to prevent 9h freeze
    )

    FoulPlayConfig.user_id = await ps_websocket_client.login()

    if FoulPlayConfig.avatar is not None:
        await ps_websocket_client.avatar(FoulPlayConfig.avatar)

    # Start the message dispatcher
    ps_websocket_client.start_dispatcher()

    from fp.run_battle import RESUME_ACTIVE_BATTLES
    if not RESUME_ACTIVE_BATTLES:
        # Clear stale active_battles.json from previous runs so OBS doesn't show dead battles.
        try:
            from streaming.state_store import expected_battle_surfaces, write_active_battles
            write_active_battles(
                {
                    "battles": [],
                    "count": 0,
                    "max_slots": max(FoulPlayConfig.max_concurrent_battles, expected_battle_surfaces()),
                    "updated": "",
                }
            )
            logger.info("Cleared active_battles.json for fresh start")
        except Exception as e:
            logger.warning(f"Failed to clear active_battles.json: {e}")
    else:
        logger.info("Preserving active_battles.json until resume priming completes")

    # Cancel any stale ladder search left running from a previous session.
    # Without this, PS keeps matching us into new games before workers start.
    try:
        await ps_websocket_client.cancel_search()
        logger.info("Cancelled any stale ladder search from previous session")
    except Exception as e:
        logger.warning(f"Failed to cancel stale search: {e}")

    # Handle stale battles from previous session. Only age-valid battles primed
    # from active_battles.json may be resumed; any other pending room is a stale
    # Showdown carry-over and can attach the wrong team_dict to a dead battle.
    await asyncio.sleep(3)  # Let dispatcher receive updatesearch with existing games

    # Prime any in-progress battles so workers can resume instead of re-searching.
    primed_resume_count = 0
    try:
        primed_resume_count = await prime_resume_battles()
    except Exception as e:
        logger.warning(f"Failed to prime resume battles: {e}")

    resumable_tags = await get_resume_battle_ids() if RESUME_ACTIVE_BATTLES else set()
    pending_tags = set(ps_websocket_client.pending_battle_messages.keys())
    stale_tags = sorted(pending_tags - resumable_tags)
    if stale_tags:
        await _forfeit_pending_battle_tags(
            ps_websocket_client,
            stale_tags,
            reason="not backed by current resume truth",
        )

    # Second pass: rooms can arrive between cancel_search and the first cleanup.
    pending_tags_2 = set(ps_websocket_client.pending_battle_messages.keys())
    stale_tags_2 = sorted(pending_tags_2 - resumable_tags)
    if stale_tags_2:
        await _forfeit_pending_battle_tags(
            ps_websocket_client,
            stale_tags_2,
            reason="late stale carry-over",
        )

    kept_count = len(set(ps_websocket_client.pending_battle_messages.keys()) & resumable_tags)
    if kept_count:
        logger.info(
            "Keeping %s primed resumable battle(s) from active_battles.json (primed=%s)",
            kept_count,
            primed_resume_count,
        )

    # Initialize team iterator for both TEAM_LIST (file) and TEAM_NAMES (env var)
    if FoulPlayConfig.team_names is not None:
        team_iterator = TeamListIterator(FoulPlayConfig.team_names)
    elif FoulPlayConfig.team_list is not None:
        team_iterator = TeamListIterator(FoulPlayConfig.team_list)
    else:
        team_iterator = None

    stats = BattleStats()
    shutdown_event = asyncio.Event()
    drain_event = asyncio.Event()
    drain_file_task = None
    try:
        DRAIN_FILE.parent.mkdir(exist_ok=True)
        if DRAIN_FILE.exists():
            DRAIN_FILE.unlink()
            logger.info("Cleared stale drain request file.")
    except Exception as e:
        logger.warning(f"Failed to prepare drain request file: {e}")
    use_search_manager = (
        FoulPlayConfig.bot_mode == BotModes.search_ladder
        and FoulPlayConfig.team_names is None
        and FoulPlayConfig.team_list is None
    )
    if FoulPlayConfig.bot_mode == BotModes.search_ladder and not use_search_manager:
        logger.info(
            "Search manager disabled: per-battle team selection active (team_names/team_list)."
        )

    # FIX: In search_manager mode, send /utm ONCE before workers start and
    # cache the team_dict. This prevents a race where multiple workers each
    # call load_team() (which picks randomly from a directory), send /utm with
    # different teams, and the last /utm wins -- leaving other workers with a
    # team_dict that doesn't match the server's actual team (matches=1/6).
    _search_manager_team_dict = None
    if use_search_manager and FoulPlayConfig.requires_team():
        try:
            _sm_packed, _search_manager_team_dict, _sm_file = load_team(FoulPlayConfig.team_name)
            await ps_websocket_client.update_team(_sm_packed)
            logger.info(
                "Search manager mode: sent /utm once for team '%s' (%s)",
                FoulPlayConfig.team_name,
                _sm_file,
            )
        except Exception as e:
            logger.error("Failed to send initial /utm in search manager mode: %s", e)

    search_task = None
    parent_watch_task = None
    logger.info(f"Max concurrent battles: {FoulPlayConfig.max_concurrent_battles}")

    async def search_manager():
        if not use_search_manager:
            return
        logger.info("Search manager started")
        fmt = FoulPlayConfig.pokemon_format
        while not shutdown_event.is_set() and not drain_event.is_set():
            try:
                active = get_active_battle_count()
                pending = await ps_websocket_client.get_pending_battle_count()
                registered = ps_websocket_client.get_registered_battle_count()
                resume_pending = await get_resume_pending_count()
                in_flight = max(active, registered) + pending + resume_pending
                if in_flight >= FoulPlayConfig.max_concurrent_battles:
                    # Ensure we are not still actively searching once we hit capacity
                    if fmt in ps_websocket_client.active_searches:
                        await ps_websocket_client.cancel_search()
                else:
                    if fmt not in ps_websocket_client.active_searches:
                        await ps_websocket_client.search_for_match(fmt)
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Search manager error: {e}")
                await asyncio.sleep(3)
        if drain_event.is_set():
            # Stop any ongoing searches when draining
            try:
                if fmt in ps_websocket_client.active_searches:
                    await ps_websocket_client.cancel_search()
            except Exception:
                pass
        logger.info("Search manager stopped")

    async def watch_drain_file():
        """Watch for a drain request file and trigger drain mode."""
        while not shutdown_event.is_set() and not drain_event.is_set():
            try:
                if DRAIN_FILE.exists():
                    detail = ""
                    try:
                        detail = DRAIN_FILE.read_text().strip()
                    except Exception:
                        detail = ""
                    logger.info(
                        "Drain request file detected%s.",
                        f" ({detail})" if detail else "",
                    )
                    drain_event.set()
                    try:
                        DRAIN_FILE.unlink()
                    except Exception:
                        pass
                    break
            except Exception as e:
                logger.warning(f"Drain file watcher error: {e}")
            await asyncio.sleep(1)

    per_worker_quotas = _battle_worker_quotas(
        bot_mode=FoulPlayConfig.bot_mode,
        max_concurrent_battles=FoulPlayConfig.max_concurrent_battles,
        run_count=FoulPlayConfig.run_count,
    )
    num_workers = len(per_worker_quotas)

    logger.info(f"Starting {num_workers} battle worker(s)")
    if FoulPlayConfig.team_names:
        for i, team in enumerate(FoulPlayConfig.team_names):
            logger.info(f"  Worker {i} -> {team}")

    # Create and run workers â€” assign fixed teams when team_names are available
    team_names_list = FoulPlayConfig.team_names or []

    if any(per_worker_quotas):
        logger.info(f"Per-worker quotas: {per_worker_quotas} (total={FoulPlayConfig.run_count})")

    workers = [
        asyncio.create_task(
            battle_worker(
                i,
                ps_websocket_client,
                stats,
                team_iterator,
                original_pokedex,
                original_move_json,
                use_search_manager,
                shutdown_event,
                drain_event,
                # FOULER-TEAM-ROTATION-FIX-2026-05-21: per-worker fixed
                # team assignment defeats rotation when num_workers == 1.
                # With one worker and multiple team_names, fall through to
                # `team_iterator` (TeamListIterator) so the single worker
                # cycles through ALL teams instead of locking to team[0].
                # (This was the bug that kept the bot on fat-team-1-stall
                # for 17.5 days starting 2026-05-03.)
                assigned_team=(team_names_list[i % len(team_names_list)]
                               if (team_names_list and num_workers > 1)
                               else None),
                per_worker_quota=per_worker_quotas[i],
                cached_team_dict=_search_manager_team_dict,
            )
        )
        for i in range(num_workers)
    ]

    if use_search_manager:
        search_task = asyncio.create_task(search_manager())

    drain_file_task = asyncio.create_task(watch_drain_file())
    if PARENT_PID > 0:
        parent_watch_task = asyncio.create_task(_watch_parent_process(PARENT_PID, shutdown_event))

    # Setup Windows signal handler
    if sys.platform == "win32":
        try:
            setup_windows_handler(asyncio.get_running_loop(), shutdown_event, drain_event)
            logger.info("Windows shutdown handler registered")
        except Exception as e:
            logger.warning(f"Failed to register Windows shutdown handler: {e}")

    try:
        # Wait for all workers to complete OR shutdown/drain event
        wait_task = asyncio.gather(*workers, return_exceptions=True)
        shutdown_task = asyncio.create_task(shutdown_event.wait())
        drain_task = asyncio.create_task(drain_event.wait())
        
        done, pending = await asyncio.wait(
            [wait_task, shutdown_task, drain_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        
        if shutdown_task in done:
            logger.info("Shutdown event triggered")
        if drain_task in done and not shutdown_task.done():
            logger.info("Drain mode active: waiting for current battles to finish")
            if search_task:
                search_task.cancel()
                try:
                    await search_task
                except asyncio.CancelledError:
                    pass
            if drain_file_task:
                drain_file_task.cancel()
                try:
                    await drain_file_task
                except asyncio.CancelledError:
                    pass
            if parent_watch_task:
                parent_watch_task.cancel()
                try:
                    await parent_watch_task
                except asyncio.CancelledError:
                    pass
            # Allow workers to finish naturally
            await wait_task
            set_runtime_reservation_outcome("aborted")
            return
            
        # Cancel workers
        for worker in workers:
            worker.cancel()
        
        if not wait_task.done():
            wait_task.cancel()
            try:
                await wait_task
            except asyncio.CancelledError:
                pass
        if not shutdown_task.done():
            shutdown_task.cancel()
            try:
                await shutdown_task
            except asyncio.CancelledError:
                pass
        if not drain_task.done():
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass
        if search_task:
            search_task.cancel()
            try:
                await search_task
            except asyncio.CancelledError:
                pass
        if drain_file_task:
            drain_file_task.cancel()
            try:
                await drain_file_task
            except asyncio.CancelledError:
                pass
        if parent_watch_task:
            parent_watch_task.cancel()
            try:
                await parent_watch_task
            except asyncio.CancelledError:
                pass

    except asyncio.CancelledError:
        logger.info("Main task cancelled, shutting down workers")
        shutdown_event.set()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        # Forfeit active battles only on forced shutdown
        if shutdown_event.is_set():
            try:
                active_tags = list(ps_websocket_client.battle_queues.keys())
                if active_tags:
                    logger.info(f"Forfeiting {len(active_tags)} active battles...")
                    for tag in active_tags:
                        try:
                            await ps_websocket_client.forfeit_battle(tag)
                        except Exception as e:
                            logger.error(f"Failed to forfeit {tag}: {e}")
                    
                    # Allow time for messages to flush
                    logger.info("Waiting for forfeits to send...")
                    await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error during forfeit cleanup: {e}")

        # Clean up orphaned battles: cancel any active search, forfeit+leave
        # any pending battles so PS doesn't think we're still in them.
        try:
            await ps_websocket_client.cancel_search()
        except Exception:
            pass
        orphan_tags = list(ps_websocket_client.pending_battle_messages.keys())
        if orphan_tags:
            logger.info(f"Cleaning up {len(orphan_tags)} orphaned pending battle(s): {orphan_tags}")
            for tag in orphan_tags:
                try:
                    await ps_websocket_client.forfeit_battle(tag)
                except Exception:
                    pass
                try:
                    await ps_websocket_client.leave_battle(tag)
                except Exception:
                    pass
            ps_websocket_client.pending_battle_messages.clear()
            ps_websocket_client.pending_battle_times.clear()
            await asyncio.sleep(2)

        if drain_file_task and not drain_file_task.done():
            drain_file_task.cancel()
            try:
                await drain_file_task
            except asyncio.CancelledError:
                pass
        if parent_watch_task and not parent_watch_task.done():
            parent_watch_task.cancel()
            try:
                await parent_watch_task
            except asyncio.CancelledError:
                pass

        await ps_websocket_client.close()

    # ---- ROUND COMPLETE SUMMARY ----
    summary = await stats.get_summary()
    total = summary["battles_run"]
    wins = summary["wins"]
    losses = summary["losses"]
    disconnects = summary["disconnects"]
    win_rate = (wins / total * 100) if total > 0 else 0.0

    print("\n" + "=" * 60)
    print("  ROUND COMPLETE")
    print("=" * 60)
    print(f"  Total battles:  {total}")
    print(f"  Wins:           {wins}")
    print(f"  Losses:         {losses}")
    print(f"  Disconnects:    {disconnects}")
    print(f"  Win rate:       {win_rate:.1f}%")
    print("-" * 60)

    # Per-team breakdown (uses full battle history loaded from disk)
    per_team = stats.get_per_team_stats()
    if per_team:
        print("  Per-team breakdown (this session + history):")
        for team_name, ts in sorted(per_team.items()):
            t_wr = (ts["wins"] / ts["total"] * 100) if ts["total"] > 0 else 0.0
            dc_str = f"  DC:{ts['disconnects']}" if ts["disconnects"] > 0 else ""
            print(f"    {team_name:30s}  W:{ts['wins']}  L:{ts['losses']}{dc_str}  ({t_wr:.0f}% WR, {ts['total']} games)")
    print("-" * 60)
    print("  Evaluation time: review replays, adjust teams, then re-run.")
    print("  The bot will NOT auto-start the next round.")
    print("=" * 60 + "\n")

    logger.info(
        "Round complete: W:%d L:%d DC:%d Total:%d WinRate:%.1f%%",
        wins, losses, disconnects, total, win_rate,
    )
    set_runtime_reservation_outcome(
        "completed" if total >= FoulPlayConfig.run_count else "aborted"
    )


if __name__ == "__main__":
    try:
        asyncio.run(run_foul_play())
    except Exception:
        logger.error(traceback.format_exc())
        raise
