import asyncio
import json
import logging
import traceback
from copy import deepcopy

from config import FoulPlayConfig, init_logging, BotModes

from teams import load_team, TeamListIterator
from fp.run_battle import pokemon_battle
from fp.websocket_client import PSWebsocketClient

from data import all_move_json
from data import pokedex
from data.mods.apply_mods import apply_mods

logger = logging.getLogger(__name__)

# Maximum concurrent battles
MAX_CONCURRENT_BATTLES = 4


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


class BattleStats:
    """Thread-safe battle statistics tracker"""
    def __init__(self):
        self.wins = 0
        self.losses = 0
        self.battles_run = 0
        self._lock = asyncio.Lock()

    async def record_win(self, team_file_name):
        async with self._lock:
            self.wins += 1
            self.battles_run += 1
            logger.info("Won with team: {}".format(team_file_name))
            logger.info("W: {}\tL: {}".format(self.wins, self.losses))

    async def record_loss(self, team_file_name):
        async with self._lock:
            self.losses += 1
            self.battles_run += 1
            logger.info("Lost with team: {}".format(team_file_name))
            logger.info("W: {}\tL: {}".format(self.wins, self.losses))

    async def get_battles_run(self):
        async with self._lock:
            return self.battles_run


async def battle_worker(
    worker_id: int,
    ps_websocket_client: PSWebsocketClient,
    stats: BattleStats,
    team_iterator,
    original_pokedex,
    original_move_json,
    shutdown_event: asyncio.Event
):
    """Worker that continuously runs battles until shutdown or run_count reached"""
    logger.info(f"Battle worker {worker_id} started")

    while not shutdown_event.is_set():
        # Check if we've hit run_count
        battles_run = await stats.get_battles_run()
        if battles_run >= FoulPlayConfig.run_count:
            logger.info(f"Worker {worker_id}: Run count reached, stopping")
            break

        try:
            # Get team for this battle
            team_packed = None
            team_dict = None
            team_file_name = "None"

            if FoulPlayConfig.requires_team():
                team_name = (
                    team_iterator.get_next_team()
                    if team_iterator is not None
                    else FoulPlayConfig.team_name
                )
                team_packed, team_dict, team_file_name = load_team(team_name)
                await ps_websocket_client.update_team(team_packed)
            else:
                await ps_websocket_client.update_team("None")

            # Search for a match
            if FoulPlayConfig.bot_mode == BotModes.search_ladder:
                await ps_websocket_client.search_for_match(FoulPlayConfig.pokemon_format)
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
                ps_websocket_client, FoulPlayConfig.pokemon_format, team_dict
            )

            # Record result
            if winner == FoulPlayConfig.username:
                await stats.record_win(team_file_name)
            else:
                await stats.record_loss(team_file_name)

            check_dictionaries_are_unmodified(original_pokedex, original_move_json)

        except asyncio.CancelledError:
            logger.info(f"Worker {worker_id}: Cancelled")
            break
        except Exception as e:
            logger.error(f"Worker {worker_id} error: {e}")
            logger.error(traceback.format_exc())
            # Brief pause before retrying
            await asyncio.sleep(5)

    logger.info(f"Battle worker {worker_id} stopped")


async def run_foul_play():
    FoulPlayConfig.configure()
    init_logging(FoulPlayConfig.log_level, FoulPlayConfig.log_to_file)
    apply_mods(FoulPlayConfig.pokemon_format)

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

    team_iterator = (
        None
        if FoulPlayConfig.team_list is None
        else TeamListIterator(FoulPlayConfig.team_list)
    )

    stats = BattleStats()
    shutdown_event = asyncio.Event()

    # Determine number of workers
    # For ladder mode, use MAX_CONCURRENT_BATTLES
    # For challenge modes, use 1 (can only have one pending challenge at a time)
    if FoulPlayConfig.bot_mode == BotModes.search_ladder:
        num_workers = MAX_CONCURRENT_BATTLES
    else:
        num_workers = 1

    logger.info(f"Starting {num_workers} battle worker(s)")

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
                shutdown_event
            )
        )
        for i in range(num_workers)
    ]

    try:
        # Wait for all workers to complete
        await asyncio.gather(*workers, return_exceptions=True)
    except asyncio.CancelledError:
        logger.info("Main task cancelled, shutting down workers")
        shutdown_event.set()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
    finally:
        await ps_websocket_client.close()

    logger.info(f"Final stats: W: {stats.wins}\tL: {stats.losses}")


if __name__ == "__main__":
    try:
        asyncio.run(run_foul_play())
    except Exception:
        logger.error(traceback.format_exc())
        raise
