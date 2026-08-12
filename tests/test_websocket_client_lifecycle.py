import asyncio

from fp.websocket_client import PSWebsocketClient


class FakeSendQueue:
    def __init__(self):
        self.stopped = False

    async def enqueue(self, websocket, message, priority=None):
        await websocket.send(message)

    async def stop(self):
        self.stopped = True


class FakeWebSocket:
    def __init__(self, *, closed=False, recv_messages=None):
        self.closed = closed
        self.close_count = 0
        self.sent = []
        self.recv_messages = list(recv_messages or [])

    async def close(self):
        self.close_count += 1
        self.closed = True

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        if not self.recv_messages:
            await asyncio.sleep(30)
        return self.recv_messages.pop(0)


def _new_client_for_lifecycle_tests():
    client = PSWebsocketClient()
    client.username = "bot"
    client.password = None
    client.address = "ws://example.invalid"
    client.expected_format = "gen9ou"
    client.global_queue = asyncio.Queue(maxsize=10)
    client.battle_queues = {}
    client.pending_battle_messages = {}
    client.pending_battle_times = {}
    client.pending_battle_owners = {}
    client.dispatcher_task = None
    client._dispatcher_running = False
    client._dispatcher_started = False
    client._reconnect_task = None
    client._pending_lock = asyncio.Lock()
    client._reconnect_lock = asyncio.Lock()
    client._search_lock = asyncio.Lock()
    client._search_owner = None
    client._search_owner_since = None
    client.active_searches = set()
    client._recently_finished = {}
    client._send_queue = FakeSendQueue()
    return client


def test_reconnect_clears_stale_pending_owner_state_and_drains_queues():
    async def scenario():
        client = _new_client_for_lifecycle_tests()
        old_ws = FakeWebSocket(closed=True)
        new_ws = FakeWebSocket(closed=False)
        client.websocket = old_ws
        client.active_searches = {"gen9ou"}

        await client.global_queue.put("|updatesearch|gen9ou")
        battle_queue = asyncio.Queue(maxsize=10)
        await battle_queue.put(">battle-gen9ou-1\n|turn|1")
        client.battle_queues["battle-gen9ou-1"] = battle_queue
        client.pending_battle_messages["battle-gen9ou-stale"] = [
            ">battle-gen9ou-stale\n|request|{}"
        ]
        client.pending_battle_times["battle-gen9ou-stale"] = 1.0
        client.pending_battle_owners["battle-gen9ou-stale"] = 7

        async def fake_connect():
            client.websocket = new_ws

        async def fake_login():
            return client.username

        client._connect_websocket = fake_connect
        client.login = fake_login

        await client.reconnect()

        assert old_ws.close_count == 1
        assert client.websocket is new_ws
        assert client.global_queue.empty()
        assert battle_queue.empty()
        assert client.pending_battle_messages == {}
        assert client.pending_battle_times == {}
        assert client.pending_battle_owners == {}
        assert client.active_searches == set()
        assert new_ws.sent == ["|/join battle-gen9ou-1"]

    asyncio.run(scenario())


def test_close_awaits_background_tasks_and_clears_receive_state():
    async def scenario():
        client = _new_client_for_lifecycle_tests()
        ws = FakeWebSocket(closed=False)
        client.websocket = ws
        client._dispatcher_running = True
        dispatcher_task = asyncio.create_task(asyncio.sleep(30))
        reconnect_task = asyncio.create_task(asyncio.sleep(30))
        client.dispatcher_task = dispatcher_task
        client._reconnect_task = reconnect_task
        client.active_searches = {"gen9ou"}
        client._search_owner = 3
        client._search_owner_since = 123.0
        client._recently_finished = {"battle-gen9ou-1": 123.0}
        await client.global_queue.put("|challstr|1|abc")
        battle_queue = asyncio.Queue(maxsize=10)
        await battle_queue.put(">battle-gen9ou-1\n|deinit")
        client.battle_queues["battle-gen9ou-1"] = battle_queue
        client.pending_battle_messages["battle-gen9ou-2"] = [">battle-gen9ou-2"]
        client.pending_battle_times["battle-gen9ou-2"] = 123.0
        client.pending_battle_owners["battle-gen9ou-2"] = 4

        await client.close()

        assert client._send_queue.stopped
        assert dispatcher_task.done()
        assert reconnect_task.done()
        assert client.dispatcher_task is None
        assert client._reconnect_task is None
        assert ws.close_count == 1
        assert client.websocket is None
        assert client.global_queue.empty()
        assert client.battle_queues == {}
        assert client.pending_battle_messages == {}
        assert client.pending_battle_times == {}
        assert client.pending_battle_owners == {}
        assert client._recently_finished == {}
        assert client.active_searches == set()
        assert client._search_owner is None
        assert client._search_owner_since is None

    asyncio.run(scenario())


def test_no_security_login_uses_local_trn_and_waits_for_confirmation(monkeypatch):
    async def scenario():
        client = _new_client_for_lifecycle_tests()
        client.username = "foulerEvalBot"
        client.websocket = FakeWebSocket(
            recv_messages=[
                "|challstr|1|abc",
                "|updateuser|FoulerEvalBot|1|0",
            ]
        )
        monkeypatch.setenv("FOULER_NO_SECURITY_LOGIN", "1")

        user_id = await client.login()

        assert user_id == "foulerevalbot"
        assert client.websocket.sent == ["|/trn foulerEvalBot"]

    asyncio.run(scenario())


def test_accept_challenge_matches_showdown_user_ids_case_insensitively():
    async def scenario():
        client = _new_client_for_lifecycle_tests()
        client.username = "foulerEvalBot"
        client.websocket = FakeWebSocket()
        await client.global_queue.put(
            "|pm| evalBaseline| FoulerEvalBot|/challenge|gen9ou|fat-team|0|"
        )
        client._dispatcher_running = True

        await client.accept_challenge("gen9ou", room_name=None, timeout=1)

        assert client.websocket.sent == ["|/accept evalBaseline"]

    asyncio.run(scenario())
