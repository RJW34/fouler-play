import asyncio
import json
import logging
import traceback
import sys
import ctypes
import os
import subprocess
import time
from copy import deepcopy
from pathlib import Path

# Load .env so webhook URLs and other config are available to submodules
_dotenv_loaded = False
try:
    from dotenv import load_dotenv
    _dotenv_loaded = load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # dotenv not installed; rely on systemd EnvironmentFile

from config import FoulPlayConfig, init_logging, BotModes
import constants

from teams import load_team, TeamListIterator
from fp.run_battle import (
    pokemon_battle,
    get_active_battle_count,
    get_resume_pending_count,
    has_resume_battle,
    prime_resume_battles,
)
from fp.websocket_client import PSWebsocketClient

from data import all_move_json
from data import pokedex
from data.mods.apply_mods import apply_mods

logger = logging.getLogger(__name__)

DRAIN_FILE = Path(__file__).resolve().parent / ".pids" / "drain.request"
PARENT_PID = int(os.getenv("FP_PARENT_PID", "0") or 0)
PARENT_CHECK_SEC = int(os.getenv("FP_PARENT_CHECK_SEC", "5") or 5)
LOSS_DRAIN_ENV = os.getenv("LOSS_TRIGGERED_DRAIN", "1").strip().lower()
LOSS_DRAIN_ENABLED = LOSS_DRAIN_ENV not in ("0", "false", "no", "off")


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
        with open("modified_moves.json", "w") as f:
            json.dump(all_move_json, f, indent=4)
        exit(1)
    else:
        logger.debug("Move JSON unmodified!")

    if original_pokedex != pokedex:
        logger.critical(
            "Pokedex JSON changed!\nDumping modified version to `modified_pokedex.json`"
        )
        with open("modified_pokedex.json", "w") as f:
            json.dump(pokedex, f, indent=4)
        exit(1)
    else:
        logger.debug("Pokedex JSON unmodified!")


BATTLE_STATS_FILE = Path(__file__).resolve().parent / "battle_stats.json"


class BattleStats:
    """Thread-safe battle statistics tracker with per-team persistence"""
    def __init__(self):
        self.wins = 0
        self.losses = 0
        self.battles_run = 0
        self._lock = asyncio.Lock()
        self._battles = self._load_battles()

    def _load_battles(self):
        try:
            if BATTLE_STATS_FILE.exists():
                data = json.loads(BATTLE_STATS_FILE.read_text(encoding="utf-8"))
                return data.get("battles", [])
        except Exception as e:
            logger.warning("Failed to load battle_stats.json: %s", e)
        return []

    def _save_battles(self):
        try:
            data = {"battles": self._battles}
            BATTLE_STATS_FILE.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save battle_stats.json: %s", e)

    def _record_battle(self, team_file_name, result, battle_tag=None):
        from datetime import datetime, timezone
        entry = {
            "battle_id": battle_tag or "unknown",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "team_file": team_file_name or "unknown",
            "result": result,
            "replay_id": battle_tag or "",
        }
        self._battles.append(entry)
        self._save_battles()

    async def record_win(self, team_file_name, battle_tag=None):
        async with self._lock:
            self.wins += 1
            self.battles_run += 1
            self._record_battle(team_file_name, "win", battle_tag)
            logger.info("Won with team: {}".format(team_file_name))
            logger.info("W: {}\tL: {}".format(self.wins, self.losses))

    async def record_loss(self, team_file_name, battle_tag=None):
        async with self._lock:
            self.losses += 1
            self.battles_run += 1
            self._record_battle(team_file_name, "loss", battle_tag)
            logger.info("Lost with team: {}".format(team_file_name))
            logger.info("W: {}\tL: {}".format(self.wins, self.losses))

    async def get_battles_run(self):
        async with self._lock:
            return self.battles_run


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
    drain_event: asyncio.Event
):
    """Worker that continuously runs battles until shutdown or run_count reached"""
    logger.info(f"Battle worker {worker_id} started")

    while not shutdown_event.is_set():
        if drain_event.is_set():
            logger.info(f"Worker {worker_id}: Drain mode active, stopping before new battle")
            break
        # Check if we've hit run_count
        battles_run = await stats.get_battles_run()
        if battles_run >= FoulPlayConfig.run_count:
            logger.info(f"Worker {worker_id}: Run count reached, stopping")
            break

        try:
            search_slot_acquired = False
            resume_ready = False
            start_search = False

            # Determine if a resume battle is available for this worker
            if FoulPlayConfig.bot_mode == BotModes.search_ladder:
                resume_ready = await has_resume_battle(worker_id)
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
                # Priority: team_iterator (cycling team_names/team_list) > team_name (single)
                if team_iterator is not None:
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
                    or use_search_manager
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

            # Record result
            lost_battle = False
            if winner == FoulPlayConfig.username:
                await stats.record_win(team_file_name, battle_tag)
            elif winner is None:
                logger.info(
                    "Worker %s: battle ended without winner (tag=%s)",
                    worker_id,
                    battle_tag,
                )
            else:
                await stats.record_loss(team_file_name, battle_tag)
                lost_battle = True

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


async def run_foul_play():
    FoulPlayConfig.configure()
    init_logging(FoulPlayConfig.log_level, FoulPlayConfig.log_to_file)
    
    # Log .env status for debugging
    logger.info(f".env loading: {'success' if _dotenv_loaded else 'skipped/failed (using systemd EnvironmentFile)'}")
    discord_webhook = os.getenv("DISCORD_BATTLES_WEBHOOK_URL")
    if discord_webhook:
        logger.info("Discord battle reporting: ENABLED")
    else:
        logger.warning("Discord battle reporting: DISABLED (DISCORD_BATTLES_WEBHOOK_URL not set)")
    
    apply_mods(FoulPlayConfig.pokemon_format)
    validate_constants()

    original_pokedex = deepcopy(pokedex)
    original_move_json = deepcopy(all_move_json)

    ps_websocket_client = await PSWebsocketClient.create(
        FoulPlayConfig.username, FoulPlayConfig.password, FoulPlayConfig.websocket_uri
    )

    FoulPlayConfig.user_id = await ps_websocket_client.login()

    if FoulPlayConfig.avatar is not None:
        await ps_websocket_client.avatar(FoulPlayConfig.avatar)

    # Start the message dispatcher
    ps_websocket_client.start_dispatcher()

    # Prime any in-progress battles so workers can resume instead of re-searching.
    try:
        await prime_resume_battles()
    except Exception as e:
        logger.warning(f"Failed to prime resume battles: {e}")

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

    # Determine number of workers
    # For ladder mode, use MAX_CONCURRENT_BATTLES
    # For challenge modes, use 1 (can only have one pending challenge at a time)
    if FoulPlayConfig.bot_mode == BotModes.search_ladder:
        num_workers = FoulPlayConfig.max_concurrent_battles
    else:
        num_workers = 1

    logger.info(f"Starting {num_workers} battle worker(s)")
    if FoulPlayConfig.team_names:
        for i, team in enumerate(FoulPlayConfig.team_names):
            logger.info(f"  Worker {i} -> {team}")

    # Create and run workers
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
                drain_event
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

    logger.info(f"Final stats: W: {stats.wins}\tL: {stats.losses}")


if __name__ == "__main__":
    # Prevent duplicate bot instances
    try:
        from process_lock import acquire_lock, release_lock
        if not acquire_lock(username=FoulPlayConfig.pokemon_showdown_username if hasattr(FoulPlayConfig, 'pokemon_showdown_username') else "unknown"):
            logger.error("Another bot instance is already running. Exiting.")
            sys.exit(1)
    except ImportError:
        pass  # process_lock not available, continue without it

    try:
        asyncio.run(run_foul_play())
    except Exception:
        logger.error(traceback.format_exc())
        raise
