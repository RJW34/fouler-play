from collections import OrderedDict

import bot_monitor


def test_finished_replay_handoff_waiters_expire_without_dropping_fresh_pending():
    monitor = bot_monitor.BotMonitor.__new__(bot_monitor.BotMonitor)
    monitor.finished_battles = OrderedDict(
        {
            "battle-gen9ou-111": ("OldOpponent", "won"),
            "battle-gen9ou-222": ("FreshOpponent", "lost"),
        }
    )
    monitor.finished_battle_times = OrderedDict(
        {
            "battle-gen9ou-111": 10.0,
            "battle-gen9ou-222": 95.0,
        }
    )
    monitor.finished_replay_pending_max_age_sec = 60

    expired = monitor._expire_finished_replay_handoffs(now=100.0)

    assert expired == ["battle-gen9ou-111"]
    assert "battle-gen9ou-111" not in monitor.finished_battles
    assert "battle-gen9ou-111" not in monitor.finished_battle_times
    assert monitor.finished_battles["battle-gen9ou-222"] == ("FreshOpponent", "lost")
