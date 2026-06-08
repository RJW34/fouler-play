import asyncio

from fp.websocket_client import (
    _append_bounded_pending_message,
    _public_replay_id_from_ref,
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
