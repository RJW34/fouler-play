import asyncio

from fp.ws_rate_limiter import PRIORITY_CHAT, WSSendQueue


class BlockingWebSocket:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, message):
        self.started.set()
        await self.release.wait()


def test_stop_cancels_inflight_and_pending_sends_without_join_hang():
    async def scenario():
        queue = WSSendQueue(send_interval=0)
        ws = BlockingWebSocket()
        queue.start()

        first = asyncio.create_task(queue.enqueue(ws, "|/avatar 1", priority=PRIORITY_CHAT))
        await asyncio.wait_for(ws.started.wait(), timeout=1)

        second = asyncio.create_task(queue.enqueue(ws, "|/avatar 2", priority=PRIORITY_CHAT))
        while queue._pq.qsize() < 1:
            await asyncio.sleep(0)

        await queue.stop()
        results = await asyncio.wait_for(
            asyncio.gather(first, second, return_exceptions=True),
            timeout=1,
        )

        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert queue._pq.empty()
        await asyncio.wait_for(queue._pq.join(), timeout=1)

    asyncio.run(scenario())
