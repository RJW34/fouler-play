from __future__ import annotations

import asyncio

import pytest

import run


class _DummyStats:
    def __init__(self) -> None:
        self.recorded = False

    async def get_battles_run(self) -> int:
        return 0

    async def record_win(self, *args, **kwargs) -> None:
        self.recorded = True
        raise AssertionError("no-battle retries must not record wins")

    async def record_loss(self, *args, **kwargs) -> None:
        self.recorded = True
        raise AssertionError("no-battle retries must not record losses")

    async def record_disconnect(self, *args, **kwargs) -> None:
        self.recorded = True
        raise AssertionError("no-battle retries must not record disconnects")


class _DummyShowdownClient:
    def __init__(self) -> None:
        self.owner = False
        self.cancelled = 0
        self.released = []
        self.searches = 0

    async def acquire_search_slot(self, worker_id: int) -> None:
        self.owner = True

    async def search_for_match(self, fmt: str) -> None:
        self.searches += 1

    async def update_team(self, team: str) -> None:
        return None

    async def cancel_search(self) -> None:
        self.cancelled += 1

    def owns_search_slot(self, worker_id: int) -> bool:
        return self.owner

    def release_search_slot(self, worker_id: int, reason: str) -> None:
        self.owner = False
        self.released.append((worker_id, reason))


def test_loss_drain_bool_parser_ignores_inline_comments(monkeypatch) -> None:
    monkeypatch.setenv("LOSS_TRIGGERED_DRAIN", "0  # disable early-stop for devstream runs")

    assert run._env_bool("LOSS_TRIGGERED_DRAIN", default=True) is False


@pytest.mark.asyncio
async def test_battle_worker_retries_empty_search_without_recording(monkeypatch) -> None:
    shutdown_event = asyncio.Event()
    drain_event = asyncio.Event()
    stats = _DummyStats()
    client = _DummyShowdownClient()

    monkeypatch.setattr(run.FoulPlayConfig, "bot_mode", run.BotModes.search_ladder, raising=False)
    monkeypatch.setattr(run.FoulPlayConfig, "pokemon_format", "gen9ou", raising=False)
    monkeypatch.setattr(run.FoulPlayConfig, "run_count", 1000000, raising=False)
    monkeypatch.setattr(run.FoulPlayConfig, "max_concurrent_battles", 3, raising=False)
    monkeypatch.setattr(run.FoulPlayConfig, "requires_team", lambda: False)
    monkeypatch.setattr(run, "has_resume_battle", lambda worker_id: asyncio.sleep(0, result=False))
    monkeypatch.setattr(run, "get_active_battle_count", lambda: 0)
    monkeypatch.setattr(run, "check_dictionaries_are_unmodified", lambda *args: None)

    async def fake_pokemon_battle(*args, **kwargs):
        shutdown_event.set()
        return None, None

    monkeypatch.setattr(run, "pokemon_battle", fake_pokemon_battle)

    await run.battle_worker(
        0,
        client,
        stats,
        team_iterator=None,
        original_pokedex={},
        original_move_json={},
        use_search_manager=False,
        shutdown_event=shutdown_event,
        drain_event=drain_event,
    )

    assert stats.recorded is False
    assert client.searches == 1
    assert client.released == [(0, "cleanup")]
