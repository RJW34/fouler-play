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
        )
    )

    assert result is None
    assert calls == [
        ("gen9ou-2626011055", False),
        ("gen9ou-2626011055", False),
    ]


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
