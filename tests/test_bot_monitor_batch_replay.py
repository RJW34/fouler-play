import asyncio
import time
from collections import OrderedDict

import pytest

from bot_monitor import BotMonitor


def _monitor_without_runtime():
    monitor = BotMonitor.__new__(BotMonitor)
    monitor.batch_results = []
    monitor.batch_losses = []
    monitor.batch_wins_count = 0
    monitor.batch_losses_count = 0
    monitor.BATCH_SIZE = 3
    monitor.finished_battles = OrderedDict()
    monitor.finished_battle_times = OrderedDict()
    monitor.replay_flush_grace_sec = 20.0
    monitor._batch_flush_task = None
    monitor._analysis_tasks = set()
    monitor.max_analysis_tasks = 50
    return monitor


def test_batch_result_retains_battle_id_until_replay_url_arrives():
    monitor = _monitor_without_runtime()

    monitor.record_batch_result(
        "WaitingReplay",
        "won",
        battle_id="battle-gen9ou-333-privatehash",
    )

    assert monitor.batch_results == [
        ("WaitingReplay", "won", None, "battle-gen9ou-333-privatehash")
    ]
    assert BotMonitor._batch_result_parts(monitor.batch_results[0]) == (
        "WaitingReplay",
        "won",
        None,
        "battle-gen9ou-333-privatehash",
    )
    assert monitor.batch_wins_count == 1
    assert monitor.batch_losses_count == 0


def test_batch_result_parts_accepts_legacy_three_tuple():
    assert BotMonitor._batch_result_parts(("Opponent", "lost", None)) == (
        "Opponent",
        "lost",
        None,
        None,
    )


def test_batch_flush_waits_for_remaining_finished_battle_replays():
    monitor = _monitor_without_runtime()

    for idx, opponent in enumerate(("A", "B", "C"), start=1):
        battle_id = f"battle-gen9ou-{idx}"
        result = "lost" if opponent == "B" else "won"
        monitor._track_finished_battle(battle_id, opponent, result)
        monitor.record_batch_result(opponent, result, battle_id=battle_id)

    assert monitor._batch_ready_to_flush() is False

    monitor.batch_results[0] = (
        "A",
        "won",
        "https://replay.pokemonshowdown.com/gen9ou-1",
        "battle-gen9ou-1",
    )
    monitor.finished_battles.pop("battle-gen9ou-1")
    monitor.finished_battle_times.pop("battle-gen9ou-1")

    assert monitor._batch_ready_to_flush() is False

    monitor.batch_results[1] = (
        "B",
        "lost",
        "https://replay.pokemonshowdown.com/gen9ou-2",
        "battle-gen9ou-2",
    )
    monitor.finished_battles.pop("battle-gen9ou-2")
    monitor.finished_battle_times.pop("battle-gen9ou-2")
    monitor.batch_results[2] = (
        "C",
        "won",
        "https://replay.pokemonshowdown.com/gen9ou-3",
        "battle-gen9ou-3",
    )
    monitor.finished_battles.pop("battle-gen9ou-3")
    monitor.finished_battle_times.pop("battle-gen9ou-3")

    assert monitor._batch_ready_to_flush() is True


def test_batch_flush_grace_allows_pending_replay_summary():
    monitor = _monitor_without_runtime()
    monitor.replay_flush_grace_sec = 5.0

    for idx, opponent in enumerate(("A", "B", "C"), start=1):
        battle_id = f"battle-gen9ou-{idx}"
        monitor._track_finished_battle(battle_id, opponent, "won")
        monitor.finished_battle_times[battle_id] = time.monotonic() - 6.0
        monitor.record_batch_result(opponent, "won", battle_id=battle_id)

    assert monitor._batch_ready_to_flush() is True


@pytest.mark.asyncio
async def test_loss_analysis_task_tracker_discards_completed_tasks():
    monitor = _monitor_without_runtime()

    async def done():
        return None

    task = monitor._track_analysis_task(done())
    assert task in monitor._analysis_tasks

    await task
    await asyncio.sleep(0)

    assert monitor._analysis_tasks == set()
