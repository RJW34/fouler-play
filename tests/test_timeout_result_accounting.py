import json

import pytest

import run
import fp.run_battle as run_battle
from fp.run_battle import (
    _battle_result_from_evidence,
    _operational_loss_stream_payload,
    _post_battle_to_discord,
    _queue_operational_loss_battle_result,
)


def test_no_winner_terminal_battle_counts_as_loss():
    assert (
        _battle_result_from_evidence(
            None,
            "LEBOTJAMESXD00N",
            opponent_name="KantoFriedChansey",
        )
        == "loss"
    )


def test_ladder_tie_counts_as_loss_for_mission_accounting():
    assert (
        _battle_result_from_evidence(
            "tie",
            "LEBOTJAMESXD00N",
            opponent_name="KantoFriedChansey",
        )
        == "loss"
    )


def test_our_winner_counts_as_win():
    assert (
        _battle_result_from_evidence(
            "LEBOTJAMESXD00N",
            "LEBOTJAMESXD00N",
            opponent_name="KantoFriedChansey",
        )
        == "win"
    )


def test_opponent_winner_counts_as_loss():
    assert (
        _battle_result_from_evidence(
            "KantoFriedChansey",
            "LEBOTJAMESXD00N",
            opponent_name="KantoFriedChansey",
        )
        == "loss"
    )


@pytest.mark.asyncio
async def test_disconnect_persists_as_loss_not_neutral(tmp_path, monkeypatch):
    stats_path = tmp_path / "battle_stats.json"
    monkeypatch.setattr(run, "BATTLE_STATS_FILE", stats_path)
    stats = run.BattleStats()

    await stats.record_disconnect(
        "gen9/ou/fat-team-1-stall",
        "battle-gen9ou-timeout",
        rating=1444,
    )

    summary = await stats.get_summary()
    assert summary["wins"] == 0
    assert summary["losses"] == 1
    assert summary["disconnects"] == 1
    assert summary["battles_run"] == 1

    persisted = json.loads(stats_path.read_text(encoding="utf-8"))
    assert persisted["battles"][-1]["result"] == "loss"
    assert persisted["battles"][-1]["battle_id"] == "battle-gen9ou-timeout"


@pytest.mark.asyncio
async def test_battle_stats_stamp_active_account_and_season(tmp_path, monkeypatch):
    stats_path = tmp_path / "battle_stats.json"
    season_path = tmp_path / "account-season.json"
    season_path.write_text(
        json.dumps(
            {
                "account": "DekuFoulerLab",
                "seasonId": "dekufoulerlab-gen9ou-20260710",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(run, "BATTLE_STATS_FILE", stats_path)
    monkeypatch.setattr(run, "ACCOUNT_SEASON_FILE", season_path)
    monkeypatch.setattr(run.FoulPlayConfig, "username", "DekuFoulerLab", raising=False)
    monkeypatch.setattr(run.FoulPlayConfig, "pokemon_format", "gen9ou", raising=False)
    stats = run.BattleStats()

    await stats.record_win("fat-team-2-balance", "battle-gen9ou-current", rating=1045)

    row = json.loads(stats_path.read_text(encoding="utf-8"))["battles"][0]
    assert row["account"] == "DekuFoulerLab"
    assert row["season_id"] == "dekufoulerlab-gen9ou-20260710"
    assert row["format"] == "gen9ou"


def test_battle_stats_load_only_active_tagged_account_season(tmp_path, monkeypatch):
    stats_path = tmp_path / "battle_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "battles": [
                    {"battle_id": "old-account", "account": "thepeakmons"},
                    {
                        "battle_id": "old-season",
                        "account": "DekuFoulerLab",
                        "season_id": "dekufoulerlab-gen9ou-20260709",
                    },
                    {
                        "battle_id": "current",
                        "account": "DekuFoulerLab",
                        "season_id": "dekufoulerlab-gen9ou-20260710",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    season_path = tmp_path / "account-season.json"
    season_path.write_text(
        json.dumps(
            {
                "account": "DekuFoulerLab",
                "seasonId": "dekufoulerlab-gen9ou-20260710",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(run, "BATTLE_STATS_FILE", stats_path)
    monkeypatch.setattr(run, "ACCOUNT_SEASON_FILE", season_path)
    monkeypatch.setattr(run.FoulPlayConfig, "username", "DekuFoulerLab", raising=False)

    stats = run.BattleStats()

    assert [row["battle_id"] for row in stats._battles] == ["current"]


def test_legacy_disconnect_rows_count_as_team_losses():
    stats = run.BattleStats()
    stats._battles = [
        {
            "battle_id": "battle-gen9ou-old-timeout",
            "team_file": "gen9/ou/fat-team-2-pivot",
            "result": "disconnect",
        }
    ]

    team_stats = stats.get_per_team_stats()["gen9/ou/fat-team-2-pivot"]
    assert team_stats["wins"] == 0
    assert team_stats["losses"] == 1
    assert team_stats["disconnects"] == 1
    assert team_stats["total"] == 1


def test_legacy_tie_rows_count_as_team_losses():
    stats = run.BattleStats()
    stats._battles = [
        {
            "battle_id": "battle-gen9ou-old-tie",
            "team_file": "gen9/ou/fat-team-2-pivot",
            "result": "tie",
        }
    ]

    team_stats = stats.get_per_team_stats()["gen9/ou/fat-team-2-pivot"]
    assert team_stats["wins"] == 0
    assert team_stats["losses"] == 1
    assert team_stats["disconnects"] == 0
    assert team_stats["total"] == 1


def test_timeout_stream_event_carries_loss_result_not_tie():
    payload = _operational_loss_stream_payload(
        "battle-gen9ou-timeout",
        reason="message_timeout_disconnect",
        ended=123.0,
        elapsed_seconds=300.12,
        timeout_strikes=3,
    )

    assert payload["winner"] is None
    assert payload["result"] == "loss"
    assert payload["terminalResult"] == "loss"
    assert payload["operationalLoss"] is True
    assert payload["timeoutStrikes"] == 3


def test_timeout_queues_loss_battle_result_for_discord(monkeypatch):
    queued = {}

    def fake_queue_event(event_type, channel, payload, dedup_window_sec=None):
        queued["event_type"] = event_type
        queued["channel"] = channel
        queued["payload"] = payload
        queued["dedup_window_sec"] = dedup_window_sec

    monkeypatch.setattr(run_battle, "battle_result_event_queue_enabled", lambda: True)
    monkeypatch.setattr(run_battle, "queue_event", fake_queue_event)

    assert _queue_operational_loss_battle_result(
        "battle-gen9ou-timeout",
        opponent_name="TimerOpponent",
        team_name="gen9/ou/fat-team-1-stall",
        turns=17,
        reason="message_timeout_disconnect",
        elapsed_seconds=240.0,
        timeout_strikes=3,
    ) is True

    assert queued["event_type"] == "battle_result"
    payload = json.loads(queued["payload"])
    assert payload["result"] == "loss"
    assert payload["battle_id"] == "battle-gen9ou-timeout"
    assert payload["opponent"] == "TimerOpponent"
    assert payload["operational_loss"] is True
    assert payload["timeout_reason"] == "message_timeout_disconnect"
    assert "ended loss" in queued["payload"]
    assert "result=loss" in payload["proof"]


@pytest.mark.asyncio
async def test_structured_event_queue_owns_battle_discord_transport(monkeypatch):
    called = {"http": False}

    class _UnexpectedSession:
        def __init__(self, *args, **kwargs):
            called["http"] = True
            raise AssertionError("legacy direct Discord transport should not be used")

    async def fake_fetch_elo(username):
        return 1483.0, None

    async def fake_replay_exists(replay_id):
        return False

    monkeypatch.setenv("FOULER_BATTLE_RESULT_QUEUE", "1")
    monkeypatch.setenv("DISCORD_BATTLES_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")
    monkeypatch.setattr(run_battle, "_fetch_elo", fake_fetch_elo)
    monkeypatch.setattr(run_battle, "_replay_exists", fake_replay_exists)
    monkeypatch.setattr(run_battle.aiohttp, "ClientSession", _UnexpectedSession)

    elo_after = await _post_battle_to_discord(
        battle_tag="battle-gen9ou-structured",
        winner="LEBOTJAMESXD00N",
        opponent_name="QueueOpponent",
        our_player_name="LEBOTJAMESXD00N",
    )

    assert elo_after == 1483.0
    assert called["http"] is False


@pytest.mark.asyncio
async def test_disabled_event_queue_still_cannot_fall_back_to_direct_webhook(monkeypatch):
    called = {"http": False}

    class _UnexpectedSession:
        def __init__(self, *args, **kwargs):
            called["http"] = True
            raise AssertionError("direct Discord webhook transport is forbidden")

    async def fake_fetch_elo(username):
        return 1483.0, None

    async def fake_replay_exists(replay_id):
        return False

    monkeypatch.setenv("FOULER_BATTLE_RESULT_QUEUE", "0")
    monkeypatch.setenv("DISCORD_BATTLES_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")
    monkeypatch.setattr(run_battle, "_fetch_elo", fake_fetch_elo)
    monkeypatch.setattr(run_battle, "_replay_exists", fake_replay_exists)
    monkeypatch.setattr(run_battle.aiohttp, "ClientSession", _UnexpectedSession)

    elo_after = await _post_battle_to_discord(
        battle_tag="battle-gen9ou-queue-disabled",
        winner="LEBOTJAMESXD00N",
        opponent_name="QueueDisabledOpponent",
        our_player_name="LEBOTJAMESXD00N",
    )

    assert elo_after == 1483.0
    assert called["http"] is False
