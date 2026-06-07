import asyncio

from fp.websocket_client import (
    PSWebsocketClient,
    _append_bounded_pending_message,
    _public_replay_id_from_ref,
    _put_bounded_nowait,
    _replay_ref_matches_battle,
)
from fp.ws_rate_limiter import (
    PRIORITY_BATTLE_MOVE,
    PRIORITY_CHAT,
    WSSendQueue,
    _QueueItem,
)


def test_public_replay_id_normalizes_battle_tags_private_hashes_and_urls():
    assert _public_replay_id_from_ref("battle-gen9ou-2620946620-stxlkin") == "gen9ou-2620946620"
    assert _public_replay_id_from_ref("https://replay.pokemonshowdown.com/gen9ou-2620946620") == "gen9ou-2620946620"
    assert _public_replay_id_from_ref("https://replay.pokemonshowdown.com/gen9ou-2620946620.json") == "gen9ou-2620946620"


def test_replay_guard_rejects_cross_linked_active_battle_replays():
    assert _replay_ref_matches_battle("gen9ou-2620946620", "battle-gen9ou-2620946620-stxlkin")
    assert not _replay_ref_matches_battle("gen9ou-2620945433", "battle-gen9ou-2620946620-stxlkin")


def test_global_websocket_queue_drops_oldest_when_capacity_is_reached():
    async def run():
        client = object.__new__(PSWebsocketClient)
        client.global_queue = asyncio.Queue(maxsize=2)
        client._dropped_global_messages = 0

        await client._enqueue_global_message("old")
        await client._enqueue_global_message("middle")
        await client._enqueue_global_message("new")

        assert client._dropped_global_messages == 1
        assert client.global_queue.qsize() == 2
        assert client.global_queue.get_nowait() == "middle"
        assert client.global_queue.get_nowait() == "new"

    asyncio.run(run())


def test_pending_battle_buffer_keeps_request_when_bounded():
    messages = [
        ">battle-gen9ou-1\n|turn|1",
        ">battle-gen9ou-1\n|request|{\"active\":true}",
    ]

    dropped = _append_bounded_pending_message(
        messages,
        ">battle-gen9ou-1\n|turn|2",
        limit=2,
    )

    assert dropped == ">battle-gen9ou-1\n|turn|1"
    assert len(messages) == 2
    assert any("|request|" in message for message in messages)
    assert messages[-1].endswith("|turn|2")


def test_battle_queue_eviction_preserves_critical_messages():
    queue = asyncio.Queue(maxsize=2)
    old_chat = ">battle-gen9ou-1\n|chat|opponent|hello"
    request = ">battle-gen9ou-1\n|request|{\"active\":true}"
    next_turn = ">battle-gen9ou-1\n|turn|2"

    queue.put_nowait(old_chat)
    queue.put_nowait(request)

    dropped = _put_bounded_nowait(queue, next_turn, "battle battle-gen9ou-1")

    remaining = [queue.get_nowait(), queue.get_nowait()]
    assert dropped == old_chat
    assert request in remaining
    assert next_turn in remaining


def test_battle_queue_rejects_low_priority_arrival_before_critical_messages():
    queue = asyncio.Queue(maxsize=2)
    request = ">battle-gen9ou-1\n|request|{\"active\":true}"
    next_turn = ">battle-gen9ou-1\n|turn|2"
    incoming_chat = ">battle-gen9ou-1\n|chat|opponent|hello"

    queue.put_nowait(request)
    queue.put_nowait(next_turn)

    dropped = _put_bounded_nowait(queue, incoming_chat, "battle battle-gen9ou-1")

    remaining = [queue.get_nowait(), queue.get_nowait()]
    assert dropped == incoming_chat
    assert request in remaining
    assert next_turn in remaining
    assert incoming_chat not in remaining


def test_battle_queue_eviction_preserves_replay_and_rating_messages():
    queue = asyncio.Queue(maxsize=2)
    old_chat = ">battle-gen9ou-1\n|chat|opponent|hello"
    replay = ">battle-gen9ou-1\n|queryresponse|savereplay|{\"id\":\"gen9ou-2626011055\"}"
    rating = ">battle-gen9ou-1\n|raw|Enzo RSETIS's rating: 1226 -> 1249"

    queue.put_nowait(old_chat)
    queue.put_nowait(replay)

    dropped = _put_bounded_nowait(queue, rating, "battle battle-gen9ou-1")

    remaining = [queue.get_nowait(), queue.get_nowait()]
    assert dropped == old_chat
    assert replay in remaining
    assert rating in remaining


def test_battle_queue_rejects_noise_before_replay_public_url():
    queue = asyncio.Queue(maxsize=2)
    replay_url = ">battle-gen9ou-1\n|raw|https://replay.pokemonshowdown.com/gen9ou-2626011055"
    ladder = "|queryresponse|ladder|{\"formatid\":\"gen9ou\",\"acre\":1249}"
    incoming_chat = ">battle-gen9ou-1\n|chat|opponent|hello"

    queue.put_nowait(replay_url)
    queue.put_nowait(ladder)

    dropped = _put_bounded_nowait(queue, incoming_chat, "battle battle-gen9ou-1")

    remaining = [queue.get_nowait(), queue.get_nowait()]
    assert dropped == incoming_chat
    assert replay_url in remaining
    assert ladder in remaining


def test_ws_send_queue_drops_oldest_lowest_priority_at_capacity():
    async def scenario():
        queue = WSSendQueue(max_queue_size=2)
        loop = asyncio.get_running_loop()
        battle_future = loop.create_future()
        old_chat_future = loop.create_future()
        new_chat_future = loop.create_future()
        battle = _QueueItem(PRIORITY_BATTLE_MOVE, 1, "battle-gen9ou-1|/choose move 1", battle_future)
        old_chat = _QueueItem(PRIORITY_CHAT, 2, "|/avatar 1", old_chat_future)
        new_chat = _QueueItem(PRIORITY_CHAT, 3, "|/avatar 2", new_chat_future)
        for item in (battle, old_chat, new_chat):
            item.websocket = object()
            queue._pq.put_nowait(item)

        assert queue._drop_one_queued_item()

        remaining_messages = []
        while not queue._pq.empty():
            item = queue._pq.get_nowait()
            queue._pq.task_done()
            remaining_messages.append(item.message)

        assert "battle-gen9ou-1|/choose move 1" in remaining_messages
        assert "|/avatar 2" in remaining_messages
        assert old_chat_future.done()
        assert isinstance(old_chat_future.exception(), RuntimeError)
        assert not battle_future.done()
        assert not new_chat_future.done()

    asyncio.run(scenario())


def test_ws_send_queue_rejects_low_priority_arrival_before_battle_move():
    async def scenario():
        queue = WSSendQueue(max_queue_size=2)
        loop = asyncio.get_running_loop()
        battle_future = loop.create_future()
        timer_future = loop.create_future()
        chat_future = loop.create_future()
        battle = _QueueItem(PRIORITY_BATTLE_MOVE, 1, "battle-gen9ou-1|/choose move 1", battle_future)
        timer = _QueueItem(3, 2, "battle-gen9ou-1|/timer on", timer_future)
        chat = _QueueItem(PRIORITY_CHAT, 3, "|/avatar 2", chat_future)
        for item in (battle, timer):
            item.websocket = object()
            queue._pq.put_nowait(item)

        assert not queue._drop_for_incoming_item(chat)

        remaining_messages = []
        while not queue._pq.empty():
            item = queue._pq.get_nowait()
            queue._pq.task_done()
            remaining_messages.append(item.message)

        assert "battle-gen9ou-1|/choose move 1" in remaining_messages
        assert "battle-gen9ou-1|/timer on" in remaining_messages
        assert chat_future.done()
        assert isinstance(chat_future.exception(), RuntimeError)
        assert not battle_future.done()
        assert not timer_future.done()

    asyncio.run(scenario())
