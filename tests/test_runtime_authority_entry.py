import pytest

import process_lock
import run
from config import SaveReplay


@pytest.mark.asyncio
async def test_guard_exception_is_fatal_before_websocket_connection(monkeypatch):
    monkeypatch.setattr(run.FoulPlayConfig, "configure", lambda: None)
    monkeypatch.setattr(run.FoulPlayConfig, "log_level", "INFO", raising=False)
    monkeypatch.setattr(run.FoulPlayConfig, "log_to_file", False, raising=False)
    monkeypatch.setattr(run.FoulPlayConfig, "username", "bot", raising=False)
    monkeypatch.setattr(
        run.FoulPlayConfig,
        "bot_mode",
        run.BotModes.search_ladder,
        raising=False,
    )
    monkeypatch.setattr(
        run.FoulPlayConfig,
        "websocket_uri",
        "wss://sim3.psim.us/showdown/websocket",
        raising=False,
    )
    monkeypatch.setattr(run.FoulPlayConfig, "run_count", 1, raising=False)
    monkeypatch.setattr(
        run.FoulPlayConfig,
        "save_replay",
        SaveReplay.always,
        raising=False,
    )
    monkeypatch.setattr(
        run.FoulPlayConfig,
        "max_concurrent_battles",
        3,
        raising=False,
    )
    monkeypatch.setattr(run.FoulPlayConfig, "parallelism", 2, raising=False)
    monkeypatch.setattr(
        run,
        "_active_account_scope",
        lambda **_kwargs: ("bot", "test-season"),
    )
    monkeypatch.setattr(run, "init_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        process_lock,
        "acquire_lock",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("guard exploded")),
    )
    network_attempts = []

    async def unexpected_connection(*_args, **_kwargs):
        network_attempts.append(True)
        raise AssertionError("WebSocket connection must not be attempted")

    monkeypatch.setattr(run.PSWebsocketClient, "create", unexpected_connection)

    with pytest.raises(RuntimeError, match="guard exploded"):
        await run.run_foul_play()

    assert network_attempts == []


def test_worker_plan_never_creates_zero_quota_workers_for_short_run():
    assert run._battle_worker_quotas(
        bot_mode=run.BotModes.search_ladder,
        max_concurrent_battles=3,
        run_count=1,
    ) == [0]
    assert run._battle_worker_quotas(
        bot_mode=run.BotModes.search_ladder,
        max_concurrent_battles=3,
        run_count=2,
    ) == [1, 1]


def test_worker_plan_distributes_finite_run_exactly():
    quotas = run._battle_worker_quotas(
        bot_mode=run.BotModes.search_ladder,
        max_concurrent_battles=3,
        run_count=30,
    )

    assert quotas == [10, 10, 10]
    assert sum(quotas) == 30


def test_worker_plan_rejects_nonpositive_run_count():
    with pytest.raises(ValueError, match="run_count must be positive"):
        run._battle_worker_quotas(
            bot_mode=run.BotModes.search_ladder,
            max_concurrent_battles=3,
            run_count=0,
        )
