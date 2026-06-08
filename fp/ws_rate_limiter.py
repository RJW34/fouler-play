"""
WebSocket send rate limiter for Pokemon Showdown.

PS server throttle is ~10 msg/s. With 3 concurrent workers sharing one WS
connection, uncoordinated sends spike past that limit and trigger 242+
throttle events per session.

This module provides a single async priority queue that serialises all
outbound messages with a minimum 100 ms gap between sends.

Priority tiers (lower number = higher priority):
    PRIORITY_BATTLE_MOVE     = 1   # switch / move commands — time-critical
    PRIORITY_TEAM_SELECT     = 2   # /team selection at battle start
    PRIORITY_TIMER           = 3   # /timer on, /forfeit, /savereplay
    PRIORITY_SEARCH          = 4   # /search, /cancelsearch, /utm
    PRIORITY_CHAT            = 5   # everything else

Usage (inside PSWebsocketClient):
    from fp.ws_rate_limiter import WSSendQueue
    self._send_queue = WSSendQueue()
    self._send_queue.start()
    # replace bare websocket.send() calls with:
    await self._send_queue.enqueue(self.websocket, message, priority=PRIORITY_BATTLE_MOVE)
    # on close:
    await self._send_queue.stop()
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Priority constants ────────────────────────────────────────────────────────

PRIORITY_BATTLE_MOVE = 1   # /choose move|switch + rqid
PRIORITY_TEAM_SELECT = 2   # /team
PRIORITY_TIMER       = 3   # /timer on, /forfeit, /savereplay, /invite
PRIORITY_SEARCH      = 4   # /search, /cancelsearch, /utm, /trn, /join, /leave
PRIORITY_CHAT        = 5   # everything else

# Minimum delay between consecutive sends (seconds)
SEND_INTERVAL = 0.100  # 100 ms  →  max 10 msg/s


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using default %s", name, default)
        return default


DEFAULT_MAX_QUEUE_SIZE = max(1, _env_int("FOULER_WS_SEND_QUEUE_MAX", 500))


def _classify(message: str) -> int:
    """Classify an outbound PS message string into a priority tier."""
    # message format: "room|cmd" or "room|cmd|args…"
    # Extract the command portion (after the first |)
    pipe_idx = message.find("|")
    cmd = message[pipe_idx + 1:] if pipe_idx >= 0 else message

    # Battle move / switch — most time-critical
    if cmd.startswith("/choose ") or cmd.startswith("battle-"):
        return PRIORITY_BATTLE_MOVE

    # Team selection
    if cmd.startswith("/team "):
        return PRIORITY_TEAM_SELECT

    # Timer-related and battle lifecycle
    if any(cmd.startswith(p) for p in (
        "/timer", "/forfeit", "/savereplay", "/invite", "/join "
    )):
        return PRIORITY_TIMER

    # Search / connection management
    if any(cmd.startswith(p) for p in (
        "/search", "/cancelsearch", "/utm", "/trn", "/leave", "/avatar",
        "/challenge", "/accept", "/cmd",
    )):
        return PRIORITY_SEARCH

    return PRIORITY_CHAT


@dataclass(order=True)
class _QueueItem:
    priority: int
    seq: int                    # tiebreak: earlier enqueue wins
    message: str = field(compare=False)
    future: Any    = field(compare=False)  # asyncio.Future — signals completion to caller


class WSSendQueue:
    """
    Single-instance async priority queue for WebSocket sends.

    Thread-safe for use across multiple asyncio workers running in the
    same event loop (which is the fouler-play architecture).
    """

    def __init__(self, send_interval: float = SEND_INTERVAL, max_queue_size: int | None = None):
        self._interval = send_interval
        self._pq: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._max_queue_size = DEFAULT_MAX_QUEUE_SIZE if max_queue_size is None else max(1, max_queue_size)
        self._seq = 0
        self._task: asyncio.Task | None = None
        self._last_send_time: float = 0.0

    def start(self):
        """Start the background sender task. Call once after event loop is running."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._sender_loop())
            logger.info("WSSendQueue: sender task started (interval=%.0fms)", self._interval * 1000)

    async def stop(self):
        """Cancel pending messages and stop the sender."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._cancel_pending_items()
        logger.info("WSSendQueue: sender task stopped")

    async def enqueue(self, websocket, message: str, priority: int | None = None) -> None:
        """
        Queue a message for rate-limited delivery.

        Blocks the caller until the message has been sent (or an exception
        is raised). This preserves the original call-site semantics where
        callers `await send_message(...)` and then continue.

        If the queue already has items ahead, logs a debug notice.
        """
        if priority is None:
            priority = _classify(message)

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()

        self._seq += 1
        item = _QueueItem(priority=priority, seq=self._seq, message=message, future=fut)
        item.websocket = websocket  # type: ignore[attr-defined]

        queue_size = self._pq.qsize()
        if queue_size > 0:
            logger.debug(
                "WSSendQueue: queued (not immediately sent) — priority=%d queue_depth=%d msg=%.80s",
                priority, queue_size, message,
            )

        if queue_size >= self._max_queue_size:
            self._drop_one_queued_item()

        await self._pq.put(item)

        # Wait for the sender to complete this message
        await fut

    def _drop_one_queued_item(self) -> bool:
        """Drop the oldest lowest-priority queued send and fail its waiter."""
        items: list[_QueueItem] = []
        while True:
            try:
                queued: _QueueItem = self._pq.get_nowait()
                self._pq.task_done()
                items.append(queued)
            except asyncio.QueueEmpty:
                break

        if not items:
            return False

        drop_idx = max(
            range(len(items)),
            key=lambda idx: (items[idx].priority, -items[idx].seq),
        )
        dropped = items.pop(drop_idx)
        if not dropped.future.done():
            dropped.future.set_exception(
                RuntimeError(
                    f"WSSendQueue dropped queued message at capacity {self._max_queue_size}: "
                    f"{dropped.message[:80]}"
                )
            )
        for queued in items:
            self._pq.put_nowait(queued)
        logger.warning(
            "WSSendQueue: dropped queued message priority=%d seq=%d at capacity=%d msg=%.80s",
            dropped.priority,
            dropped.seq,
            self._max_queue_size,
            dropped.message,
        )
        return True

    def _cancel_pending_items(self) -> int:
        """Cancel queued sends and balance PriorityQueue task accounting."""
        cancelled = 0
        while True:
            try:
                leftover: _QueueItem = self._pq.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not leftover.future.done():
                leftover.future.cancel()
            self._pq.task_done()
            cancelled += 1
        return cancelled

    async def _sender_loop(self):
        """Drain the priority queue, enforcing minimum inter-send delay."""
        try:
            while True:
                item: _QueueItem = await self._pq.get()
                try:
                    # Enforce minimum delay since last send
                    now = time.monotonic()
                    gap = now - self._last_send_time
                    if gap < self._interval:
                        sleep_ms = (self._interval - gap) * 1000
                        logger.debug("WSSendQueue: throttle sleep %.1fms before send", sleep_ms)
                        await asyncio.sleep(self._interval - gap)

                    ws = getattr(item, "websocket", None)
                    if ws is not None:
                        await ws.send(item.message)
                        self._last_send_time = time.monotonic()
                        logger.debug("WSSendQueue: sent priority=%d msg=%.80s", item.priority, item.message)
                    else:
                        logger.warning("WSSendQueue: no websocket on item, skipping: %.80s", item.message)

                    if not item.future.done():
                        item.future.set_result(None)
                except asyncio.CancelledError:
                    if not item.future.done():
                        item.future.cancel()
                    raise
                except Exception as exc:
                    logger.error("WSSendQueue: send error: %s", exc)
                    if not item.future.done():
                        item.future.set_exception(exc)
                finally:
                    self._pq.task_done()
        except asyncio.CancelledError:
            self._cancel_pending_items()
            raise
