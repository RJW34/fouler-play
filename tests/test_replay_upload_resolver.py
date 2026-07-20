import asyncio

from fp import run_battle
from fp import websocket_client as websocket_client_module
from fp.websocket_client import PSWebsocketClient


def test_resolve_public_replay_url_retries_with_cache_bypass(monkeypatch):
    calls = []
    sleeps = []

    async def fake_replay_exists(replay_id, *, use_cache=True):
        calls.append((replay_id, use_cache))
        return len(calls) == 2

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(run_battle, "_replay_exists", fake_replay_exists)
    monkeypatch.setattr(run_battle.asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        run_battle.resolve_public_replay_url(
            battle_tag="battle-gen9ou-2626011055-privatehash",
            replay_url="https://replay.pokemonshowdown.com/gen9ou-2626011055",
            max_attempts=3,
            delay_seconds=0.25,
        )
    )

    assert result == "https://replay.pokemonshowdown.com/gen9ou-2626011055"
    assert calls == [
        ("gen9ou-2626011055", False),
        ("gen9ou-2626011055", False),
    ]
    assert sleeps == [0.25]


def test_resolve_public_replay_url_stops_after_bounded_attempts(monkeypatch):
    calls = []

    async def fake_replay_exists(replay_id, *, use_cache=True):
        calls.append((replay_id, use_cache))
        return False

    monkeypatch.setattr(run_battle, "_replay_exists", fake_replay_exists)

    result = asyncio.run(
        run_battle.resolve_public_replay_url(
            battle_tag="battle-gen9ou-2626011055-privatehash",
            replay_url=None,
            max_attempts=2,
            delay_seconds=0,
            allow_battle_tag_fallback=True,
        )
    )

    assert result is None
    # Probes the FULL id. This assertion previously required the truncated id,
    # which is the URL Showdown 404s on -- the resolver was being asserted to
    # spend its bounded attempts on an address that could never resolve.
    assert calls == [
        ("gen9ou-2626011055-privatehash", False),
        ("gen9ou-2626011055-privatehash", False),
    ]


def test_resolve_public_replay_url_skips_unsaved_battle_ids(monkeypatch):
    calls = []

    async def fake_replay_exists(replay_id, *, use_cache=True):
        calls.append((replay_id, use_cache))
        return True

    monkeypatch.setattr(run_battle, "_replay_exists", fake_replay_exists)

    result = asyncio.run(
        run_battle.resolve_public_replay_url(
            battle_tag="battle-gen9ou-2626011055-privatehash",
            replay_url=None,
            max_attempts=2,
            delay_seconds=0,
        )
    )

    assert result is None
    assert calls == []


def test_save_replay_json_for_evidence_awaits_local_save(monkeypatch):
    calls = []

    async def fake_save(replay_id):
        calls.append(replay_id)
        return {"id": replay_id}

    monkeypatch.setattr(run_battle, "_save_replay_json_locally", fake_save)

    result = asyncio.run(
        run_battle._save_replay_json_for_evidence(
            "gen9ou-2626011055",
            attempts=2,
            delay_seconds=0,
            timeout_seconds=1,
        )
    )

    assert result is True
    assert calls == ["gen9ou-2626011055"]


def test_save_replay_json_for_evidence_retries_missing_json(monkeypatch):
    calls = []
    sleeps = []

    async def fake_save(replay_id):
        calls.append(replay_id)
        return None if len(calls) == 1 else {"id": replay_id}

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(run_battle, "_save_replay_json_locally", fake_save)
    monkeypatch.setattr(run_battle.asyncio, "sleep", fake_sleep)

    result = asyncio.run(
        run_battle._save_replay_json_for_evidence(
            "gen9ou-2626011055",
            attempts=2,
            delay_seconds=0.25,
            timeout_seconds=1,
        )
    )

    assert result is True
    assert calls == ["gen9ou-2626011055", "gen9ou-2626011055"]
    assert sleeps == [0.25]


def test_save_replay_json_for_evidence_returns_false_after_timeout(monkeypatch):
    calls = []

    async def fake_save(replay_id):
        calls.append(replay_id)
        raise asyncio.TimeoutError

    monkeypatch.setattr(run_battle, "_save_replay_json_locally", fake_save)

    result = asyncio.run(
        run_battle._save_replay_json_for_evidence(
            "gen9ou-2626011055",
            attempts=1,
            delay_seconds=0,
            timeout_seconds=1,
        )
    )

    assert result is False
    assert calls == ["gen9ou-2626011055"]


def test_replay_handoff_absent_without_saved_replay_url():
    fields = run_battle.replay_handoff_fields(
        battle_tag="battle-gen9ou-2626011055-privatehash",
        replay_url=None,
        verified_replay_url=None,
    )

    assert fields["replay_id"] is None
    assert fields["replay_url"] is None
    assert fields["replay_status"] == "absent"
    assert fields["replay_public_verified"] is False


def test_replay_handoff_marks_requested_missing_url_as_pending():
    fields = run_battle.replay_handoff_fields(
        battle_tag="battle-gen9ou-2626011055-privatehash",
        replay_url=None,
        verified_replay_url=None,
        save_replay_requested=True,
    )

    # The room suffix is retained. It is part of the id Showdown serves the
    # replay under, and dropping it produced a URL that 404s forever.
    assert fields["replay_id"] == "gen9ou-2626011055-privatehash"
    assert fields["replay_url"] is None
    assert fields["replay_status"] == "pending-public-upload"
    assert fields["replay_public_verified"] is False


def test_replay_handoff_keeps_suffixed_replay_unverified_until_probed():
    """A suffixed replay is still UNVERIFIED until something probes it.

    What changed is the id, not the verification rule. Previously the room
    suffix both truncated the id and permanently forced "pending", so the
    resolver spent its one attempt on a URL that could only 404. Now the full id
    is carried -- so the probe can succeed -- while publicness still comes from
    the probe, never from the shape of the id.
    """
    fields = run_battle.replay_handoff_fields(
        battle_tag="battle-gen9ou-2626011055-privatehash",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2626011055-privatehash",
        verified_replay_url=None,
    )

    assert fields["replay_id"] == "gen9ou-2626011055-privatehash"
    assert fields["raw_replay_url"] == "https://replay.pokemonshowdown.com/gen9ou-2626011055-privatehash"
    # Unverified: no probe has confirmed it resolves.
    assert fields["replay_status"] == "pending-public-upload"
    assert fields["replay_public_verified"] is False


def test_battle_result_queue_disabled_by_default_for_offline_eval(monkeypatch):
    monkeypatch.setenv("FOULER_OFFLINE_EVAL", "1")
    monkeypatch.delenv("FOULER_BATTLE_RESULT_QUEUE", raising=False)
    monkeypatch.delenv("FOULER_OFFLINE_EVAL_QUEUE_EVENTS", raising=False)

    assert run_battle.battle_result_event_queue_enabled() is False


def test_battle_result_queue_can_be_enabled_for_offline_eval_proof(monkeypatch):
    monkeypatch.setenv("FOULER_OFFLINE_EVAL", "1")
    monkeypatch.delenv("FOULER_BATTLE_RESULT_QUEUE", raising=False)
    monkeypatch.setenv("FOULER_OFFLINE_EVAL_QUEUE_EVENTS", "1")

    assert run_battle.battle_result_event_queue_enabled() is True


def test_battle_result_queue_explicit_disable_wins(monkeypatch):
    monkeypatch.setenv("FOULER_OFFLINE_EVAL", "0")
    monkeypatch.setenv("FOULER_BATTLE_RESULT_QUEUE", "0")

    assert run_battle.battle_result_event_queue_enabled() is False


def test_save_replay_retries_transient_upload_failure(monkeypatch):
    class Response:
        def __init__(self, status_code, text=""):
            self.status_code = status_code
            self.text = text

    posts = []

    def fake_post(url, data, timeout):
        posts.append((url, data, timeout))
        return Response(503, "busy") if len(posts) == 1 else Response(200)

    async def scenario():
        client = PSWebsocketClient()
        client.global_queue = asyncio.Queue()

        async def fake_send_message(room, message_list):
            assert room == "battle-gen9ou-2626011055-privatehash"
            assert message_list == ["/savereplay"]

        async def fake_receive_battle_message(battle_tag):
            assert battle_tag == "battle-gen9ou-2626011055-privatehash"
            return (
                ">battle-gen9ou-2626011055-privatehash\n"
                '|queryresponse|savereplay|{"id":"gen9ou-2626011055","log":"|win|Bot"}'
            )

        client.send_message = fake_send_message
        client.receive_battle_message = fake_receive_battle_message

        monkeypatch.setattr(websocket_client_module, "REPLAY_UPLOAD_ATTEMPTS", 2)
        monkeypatch.setattr(websocket_client_module, "REPLAY_UPLOAD_RETRY_DELAY_SEC", 0)
        monkeypatch.setattr(websocket_client_module.requests, "post", fake_post)

        return await client.save_replay("battle-gen9ou-2626011055-privatehash")

    replay_url = asyncio.run(scenario())

    assert replay_url == "https://replay.pokemonshowdown.com/gen9ou-2626011055"
    assert len(posts) == 2
    assert posts[0][1]["id"] == "gen9ou-2626011055"
    assert posts[0][1]["log"] == "|win|Bot"
