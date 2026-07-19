"""Spectator invite contract.

The OBS Browser Sources view battles through an invited spectator account,
so the runtime must:

1. send the /invite BEFORE the battle is published as active to the
   router/feed (a Browser Source that loads a private room ahead of its
   invite renders only the battlefield backdrop),
2. send it exactly once per battle, and
3. honor explicit false values for ENABLE_SPECTATOR_INVITES — a nonempty
   string like "0" or "false" must not count as truthy.
"""

import asyncio

import pytest

import fp.run_battle as run_battle
from config import FoulPlayConfig

SPECTATOR = "SpectatorAcct"
BATTLE_TAG = "battle-gen9ou-424242"


class RecordingClient:
    """Fake PSWebsocketClient recording events into a shared ordered log."""

    def __init__(self, events, username="OurBot", fail_invites=0):
        self.events = events
        self.username = username
        self.battle_queues = {}
        self._fail_invites = fail_invites

    async def send_message(self, room, messages):
        message = messages[0] if messages else ""
        if message.startswith("/invite"):
            if self._fail_invites > 0:
                self._fail_invites -= 1
                raise ConnectionError("simulated send failure")
            self.events.append(("invite", room, message))
        else:
            self.events.append(("send", room, message))

    async def register_battle(self, battle_tag):
        self.events.append(("register", battle_tag, None))

    async def join_room(self, battle_tag):
        self.events.append(("join", battle_tag, None))

    async def receive_battle_message(self, battle_tag):
        # One message that both breaks start_battle_common's init loop
        # (both player slots resolve) and reads as active to the resume
        # path (contains |turn|).
        return (
            f">{battle_tag}\n"
            "|player|p1|OurBot|2|\n"
            "|player|p2|RivalPlayer|1|\n"
            "|turn|5"
        )


@pytest.fixture(autouse=True)
def _spectator_config(monkeypatch):
    monkeypatch.setattr(
        FoulPlayConfig, "spectator_username", SPECTATOR, raising=False
    )
    monkeypatch.setattr(FoulPlayConfig, "log_to_file", False, raising=False)
    monkeypatch.delenv("ENABLE_SPECTATOR_INVITES", raising=False)
    run_battle._spectator_invites_sent.clear()
    run_battle._active_battles.clear()
    yield
    run_battle._spectator_invites_sent.clear()
    run_battle._active_battles.clear()


def _publish_recorder(events):
    async def _record():
        events.append(("publish", None, None))

    return _record


# ---------------------------------------------------------------------------
# ENABLE_SPECTATOR_INVITES parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw", ["0", "false", "no", "off", "FALSE", "Off", " 0 ", "NO"]
)
def test_explicit_false_disables_invites(monkeypatch, raw):
    monkeypatch.setenv("ENABLE_SPECTATOR_INVITES", raw)
    assert run_battle.spectator_invites_enabled() is False

    events = []
    client = RecordingClient(events)
    sent = asyncio.run(run_battle.ensure_spectator_invited(client, BATTLE_TAG))
    assert sent is False
    assert events == []


@pytest.mark.parametrize("raw", [None, "", "1", "true", "yes", "on", "weird"])
def test_unset_empty_or_truthy_enables_invites(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("ENABLE_SPECTATOR_INVITES", raising=False)
    else:
        monkeypatch.setenv("ENABLE_SPECTATOR_INVITES", raw)
    assert run_battle.spectator_invites_enabled() is True


def test_no_spectator_username_disables_invites(monkeypatch):
    monkeypatch.setattr(FoulPlayConfig, "spectator_username", "", raising=False)
    assert run_battle.spectator_invites_enabled() is False

    events = []
    client = RecordingClient(events)
    sent = asyncio.run(run_battle.ensure_spectator_invited(client, BATTLE_TAG))
    assert sent is False
    assert events == []


# ---------------------------------------------------------------------------
# Exactly-once behavior
# ---------------------------------------------------------------------------


def test_invite_sent_exactly_once_per_battle():
    events = []
    client = RecordingClient(events)

    async def scenario():
        first = await run_battle.ensure_spectator_invited(client, BATTLE_TAG)
        second = await run_battle.ensure_spectator_invited(client, BATTLE_TAG)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False
    invites = [e for e in events if e[0] == "invite"]
    assert invites == [("invite", BATTLE_TAG, f"/invite {SPECTATOR}")]


def test_concurrent_calls_send_single_invite():
    events = []
    client = RecordingClient(events)

    async def scenario():
        return await asyncio.gather(
            run_battle.ensure_spectator_invited(client, BATTLE_TAG),
            run_battle.ensure_spectator_invited(client, BATTLE_TAG),
            run_battle.ensure_spectator_invited(client, BATTLE_TAG),
        )

    results = asyncio.run(scenario())
    assert sum(1 for r in results if r) == 1
    assert len([e for e in events if e[0] == "invite"]) == 1


def test_failed_invite_releases_reservation_for_retry():
    events = []
    client = RecordingClient(events, fail_invites=1)

    async def scenario():
        with pytest.raises(ConnectionError):
            await run_battle.ensure_spectator_invited(client, BATTLE_TAG)
        # The fallback call site retries and must succeed.
        return await run_battle.ensure_spectator_invited(client, BATTLE_TAG)

    retried = asyncio.run(scenario())
    assert retried is True
    assert len([e for e in events if e[0] == "invite"]) == 1


def test_distinct_battles_each_get_one_invite():
    events = []
    client = RecordingClient(events)

    async def scenario():
        await run_battle.ensure_spectator_invited(client, "battle-gen9ou-1")
        await run_battle.ensure_spectator_invited(client, "battle-gen9ou-2")

    asyncio.run(scenario())
    assert [e[1] for e in events if e[0] == "invite"] == [
        "battle-gen9ou-1",
        "battle-gen9ou-2",
    ]


# ---------------------------------------------------------------------------
# Ordering: invite precedes the router/feed publish
# ---------------------------------------------------------------------------


def test_start_battle_common_invites_before_publish(monkeypatch):
    events = []
    client = RecordingClient(events)

    async def fake_get_tag(*args, **kwargs):
        return BATTLE_TAG, "RivalPlayer", False, None

    monkeypatch.setattr(run_battle, "get_battle_tag_and_opponent", fake_get_tag)
    monkeypatch.setattr(
        run_battle, "update_active_battles_file", _publish_recorder(events)
    )

    battle, msg = asyncio.run(
        run_battle.start_battle_common(client, "gen9ou", worker_id=0)
    )
    assert battle is not None
    assert battle.battle_tag == BATTLE_TAG

    kinds = [e[0] for e in events]
    assert "invite" in kinds, "spectator invite was never sent"
    assert "publish" in kinds, "battle was never published to the feed"
    assert kinds.index("invite") < kinds.index("publish"), (
        "the /invite must be sent before the battle is published as active"
    )


def test_resume_invites_before_republish(monkeypatch):
    events = []
    client = RecordingClient(events)

    monkeypatch.setattr(
        run_battle, "update_active_battles_file", _publish_recorder(events)
    )

    tag, opponent, status = asyncio.run(
        run_battle._attempt_resume_battle(client, BATTLE_TAG)
    )
    assert status == "ok"

    kinds = [e[0] for e in events]
    assert "invite" in kinds
    assert "publish" in kinds
    assert kinds.index("invite") < kinds.index("publish")


def test_start_battle_common_proceeds_when_invite_fails(monkeypatch):
    events = []
    client = RecordingClient(events, fail_invites=1)

    async def fake_get_tag(*args, **kwargs):
        return BATTLE_TAG, "RivalPlayer", False, None

    monkeypatch.setattr(run_battle, "get_battle_tag_and_opponent", fake_get_tag)
    monkeypatch.setattr(
        run_battle, "update_active_battles_file", _publish_recorder(events)
    )

    battle, msg = asyncio.run(
        run_battle.start_battle_common(client, "gen9ou", worker_id=0)
    )
    assert battle is not None, "a failed invite must not abort the battle"
    assert ("publish", None, None) in events
    # The reservation was released, so the fallback can still invite later.
    assert BATTLE_TAG not in run_battle._spectator_invites_sent
