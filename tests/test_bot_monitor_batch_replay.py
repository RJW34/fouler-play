from bot_monitor import BotMonitor


def _monitor_without_runtime():
    monitor = BotMonitor.__new__(BotMonitor)
    monitor.batch_results = []
    monitor.batch_losses = []
    monitor.batch_wins_count = 0
    monitor.batch_losses_count = 0
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
