import asyncio
import os
import websockets
import requests
import json
import time
import re

import logging

from fp.ws_rate_limiter import WSSendQueue, _classify

logger = logging.getLogger(__name__)


def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using default %s", name, default)
        return default


def _env_float(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("Invalid %s value; using default %s", name, default)
        return default


MAX_BATTLE_QUEUE_MESSAGES = max(1, _env_int("FOULER_BATTLE_QUEUE_MAX", 200))
MAX_PENDING_BATTLE_MESSAGES = max(1, _env_int("FOULER_PENDING_BATTLE_MESSAGES_MAX", 80))
MAX_GLOBAL_QUEUE_MESSAGES = max(1, _env_int("FOULER_GLOBAL_QUEUE_MAX", 500))
REPLAY_UPLOAD_ATTEMPTS = max(1, _env_int("FOULER_REPLAY_UPLOAD_ATTEMPTS", 3))
REPLAY_UPLOAD_RETRY_DELAY_SEC = max(0.0, _env_float("FOULER_REPLAY_UPLOAD_RETRY_DELAY_SEC", 1.0))


def _pending_message_drop_index(messages):
    """Prefer dropping non-request battle updates from pre-registration buffers."""
    for idx, message in enumerate(messages):
        if "|request|" not in message:
            return idx
    return 0


def _append_bounded_pending_message(messages, message, limit=MAX_PENDING_BATTLE_MESSAGES):
    """Append a pending battle message, evicting old low-value entries at capacity."""
    dropped = None
    while len(messages) >= limit:
        drop_idx = _pending_message_drop_index(messages)
        dropped = messages.pop(drop_idx)
    messages.append(message)
    return dropped


def _to_showdown_id(value):
    """Normalize a Showdown display name to its protocol userid."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _strip_showdown_identity(value):
    return str(value or "").strip().lstrip("!‽+%@&#~ ")


def _parse_updatesearch_formats(payload):
    """Return the set of format ids the account is actively searching.

    Modern Showdown sends the |updatesearch| payload as JSON, e.g.
        {"searching": ["gen9ou"], "games": {"battle-...": "[Gen 9] OU"}}
    where `searching` is empty once a search has been consumed by a match.
    Older / local (--no-security) servers may instead send a bare CSV of
    format ids. This parser handles both and returns an empty set for the
    "not searching" case (empty payload, empty list, or malformed JSON).

    Correct parsing is load-bearing: the ladder search manager uses
    `active_searches` to decide when to STOP searching / cancel the queue
    once max_concurrent_battles is reached. If this always returned an empty
    set (the pre-fix behaviour of splitting JSON on ","), the bot could never
    detect that it was still searching, never cancelled at capacity, and got
    matched into more battles than its workers could claim -> inactivity
    forfeits.
    """
    payload = (payload or "").strip()
    if not payload:
        return set()
    if payload[0] in "{[":
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return set()
        if isinstance(data, dict):
            searching = data.get("searching")
        elif isinstance(data, list):
            searching = data
        else:
            searching = None
        if not searching:
            return set()
        return {str(f) for f in searching if f}
    # Legacy CSV fallback: comma-separated format ids.
    return {f for f in payload.split(",") if f}


def _put_bounded_nowait(queue, message, label):
    """Put without allowing inactive consumers to grow queues forever."""
    dropped = None
    if queue.full():
        try:
            dropped = queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            dropped = None
    queue.put_nowait(message)
    if dropped is not None:
        logger.warning("Dropped oldest %s message to keep queue bounded", label)
    return dropped


def _drain_queue(queue):
    """Drain an asyncio.Queue and mark discarded items complete."""
    drained = 0
    while True:
        try:
            queue.get_nowait()
            drained += 1
            try:
                queue.task_done()
            except (AttributeError, ValueError):
                pass
        except asyncio.QueueEmpty:
            break
    return drained


def _public_replay_id_from_ref(value):
    """Return the public replay id portion for a battle tag or replay URL."""
    if not value:
        return ""
    ref = str(value).strip()
    if "/" in ref:
        ref = ref.rsplit("/", 1)[-1]
    if ref.endswith(".json"):
        ref = ref[:-5]
    if ref.startswith("battle-"):
        ref = ref.replace("battle-", "", 1)
    parts = ref.split("-")
    if len(parts) >= 2 and parts[1].isdigit():
        return f"{parts[0]}-{parts[1]}"
    return ref


def _replay_ref_matches_battle(replay_ref, battle_tag):
    """Reject savereplay responses that belong to another active battle."""
    expected = _public_replay_id_from_ref(battle_tag)
    actual = _public_replay_id_from_ref(replay_ref)
    return bool(expected and actual and expected == actual)


class LoginError(Exception):
    pass


class SaveReplayError(Exception):
    pass


class PSWebsocketClient:
    websocket = None
    address = None
    login_uri = None
    username = None
    password = None
    last_message = None
    last_challenge_time = 0

    @classmethod
    async def create(cls, username, password, address, expected_format=None):
        self = PSWebsocketClient()
        self.username = username
        self.password = password
        self.address = address
        self.websocket = None
        self.expected_format = expected_format  # CRITICAL: validate claimed battles match this format
        # FOULER_LOGIN_URI lets the offline eval harness point the login/assertion
        # request at a LOCAL pokemon-showdown server (--no-security), e.g.
        # http://localhost:8765/action.php?  . Production leaves it unset and uses
        # the public Showdown login server exactly as before.
        _login_override = os.getenv("FOULER_LOGIN_URI")
        if _login_override:
            self.login_uri = _login_override
        else:
            self.login_uri = (
                "https://play.pokemonshowdown.com/api/login"
                if password
                else "https://play.pokemonshowdown.com/action.php?"
            )
        # Message routing for concurrent battles
        self.battle_queues = {}  # battle_tag -> asyncio.Queue
        self.pending_battle_messages = {}  # battle_tag -> list of msgs (pre-registration buffer)
        self.pending_battle_times = {}  # battle_tag -> first-seen timestamp
        self.pending_battle_owners = {}  # battle_tag -> worker_id that initiated search (or None)
        self.global_queue = asyncio.Queue(maxsize=MAX_GLOBAL_QUEUE_MESSAGES)  # for non-battle messages (login, search, etc.)
        self.dispatcher_task = None
        self._dispatcher_running = False
        self._reconnect_task = None  # Track reconnect task to prevent duplicates
        self._dispatcher_started = False
        self._pending_lock = asyncio.Lock()  # Lock for atomic battle claiming
        self._reconnect_lock = asyncio.Lock()
        self._search_lock = asyncio.Lock()
        self._search_owner = None
        self._search_owner_since = None
        self.active_searches = set()
        self._recently_finished = {}  # battle_tag -> unregister timestamp
        # Rate limiter: serialises all outbound WS messages with 100ms minimum gap
        # to prevent PS rate throttle hits when 3 workers share one connection.
        # NOTE: Must be started BEFORE _connect_websocket() because the login
        # handshake calls send_message() → enqueue() → awaits fut, and the sender
        # task must already be running to drain the queue and resolve that future.
        self._send_queue = WSSendQueue()
        self._send_queue.start()
        await self._connect_websocket()
        return self

    def start_dispatcher(self):
        """Start the background message dispatcher and rate-limited send queue"""
        self._dispatcher_started = True
        if not self._dispatcher_running:
            self._dispatcher_running = True
            self.dispatcher_task = asyncio.create_task(self._message_dispatcher())
            logger.info("Message dispatcher started")
        # Always ensure the send queue is running
        self._send_queue.start()

    def stop_dispatcher(self):
        """Stop the background message dispatcher (send queue stays alive until close())"""
        self._dispatcher_running = False
        if self.dispatcher_task:
            self.dispatcher_task.cancel()
            self.dispatcher_task = None

    async def _stop_dispatcher_task(self):
        task = self.dispatcher_task
        self.stop_dispatcher()
        if task and task is not asyncio.current_task() and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _is_closed(self):
        if self.websocket is None:
            return True

        closed_attr = getattr(self.websocket, "closed", None)
        if closed_attr is not None:
            try:
                return bool(closed_attr)
            except Exception:
                pass

        state = getattr(self.websocket, "state", None)
        if state is not None:
            try:
                state_name = getattr(state, "name", str(state))
                if state_name in ("CLOSED", "CLOSING"):
                    return True
            except Exception:
                pass

        close_code = getattr(self.websocket, "close_code", None)
        if close_code is not None:
            return True

        return False

    async def _connect_websocket(self):
        self.websocket = await websockets.connect(
            self.address,
            ping_interval=20,  # Send ping every 20s to keep connection alive
            ping_timeout=20,   # Wait 20s for pong before considering connection dead
            close_timeout=10,  # Wait 10s for close handshake
        )

    async def ensure_connection(self):
        if self._is_closed():
            await self.reconnect()

    async def reconnect(self):
        async with self._reconnect_lock:
            if not self._is_closed():
                return

            logger.warning("WebSocket disconnected. Reconnecting...")
            await self._stop_dispatcher_task()

            if self.websocket is not None:
                try:
                    await self.websocket.close()
                except Exception:
                    pass

            await self._connect_websocket()

            # Clear any stale global messages from the old connection
            try:
                _drain_queue(self.global_queue)
            except Exception:
                pass

            # Pending battles are from the old connection; drop them
            self.pending_battle_messages.clear()
            self.pending_battle_times.clear()
            self.pending_battle_owners.clear()
            # Clear active search state; it will repopulate from updatesearch
            self.active_searches = set()

            try:
                await self.login()
            except Exception as e:
                logger.error(f"Reconnect login failed: {e}")
                raise

            # Rejoin any active battle rooms to resume updates after reconnect.
            # This is critical for long-running battles; otherwise the bot can stall.
            if self.battle_queues:
                # Clear stale queued messages
                for queue in self.battle_queues.values():
                    try:
                        _drain_queue(queue)
                    except Exception:
                        pass

                for battle_tag in list(self.battle_queues.keys()):
                    try:
                        await self.websocket.send("|/join {}".format(battle_tag))
                        logger.info(f"Rejoined battle room: {battle_tag}")
                    except Exception as e:
                        logger.warning(f"Failed to rejoin {battle_tag}: {e}")

            if self._dispatcher_started:
                self.start_dispatcher()

    async def _auto_reconnect(self):
        """Auto-reconnect with exponential backoff when dispatcher detects disconnection."""
        backoff = 1
        max_backoff = 60
        max_attempts = 10
        for attempt in range(1, max_attempts + 1):
            logger.info(f"Auto-reconnect attempt {attempt}/{max_attempts} in {backoff}s...")
            await asyncio.sleep(backoff)
            try:
                await self.reconnect()
                logger.info(f"Auto-reconnect succeeded on attempt {attempt}")
                return
            except Exception as e:
                logger.error(f"Auto-reconnect attempt {attempt} failed: {e}")
                backoff = min(backoff * 2, max_backoff)
        logger.error(f"Auto-reconnect failed after {max_attempts} attempts. Giving up.")

    async def _message_dispatcher(self):
        """Background task that routes incoming messages to correct queues"""
        battle_tag_pattern = re.compile(r'^>(battle-[a-z0-9-]+)')

        while self._dispatcher_running:
            try:
                msg = await self.websocket.recv()
                logger.debug("Dispatcher received: {}".format(msg[:100] if len(msg) > 100 else msg))

                # Check if this is a battle-specific message
                first_line = msg.split("\n")[0]

                # Track ladder search state from updatesearch messages.
                # Modern Showdown: |updatesearch|{"searching":[...],"games":{...}}
                # Legacy/local:    |updatesearch|gen9ou,gen9randombattle
                # NOTE: the payload is JSON and MUST NOT be split on "," (that
                # produced garbage tokens, so active_searches never matched the
                # format id and the search manager could never cancel at
                # capacity -> inactivity-forfeit doom loop).
                if first_line.startswith("|updatesearch|"):
                    payload = first_line[len("|updatesearch|"):]
                    self.active_searches = _parse_updatesearch_formats(payload)

                match = battle_tag_pattern.match(first_line)

                if match:
                    # Use the full battle room id (including any suffix)
                    battle_tag = match.group(1)
                    routed = False
                    buffered_count = None
                    async with self._pending_lock:
                        self._purge_stale_pending()
                        queue = self.battle_queues.get(battle_tag)
                        if queue is not None:
                            _put_bounded_nowait(queue, msg, f"battle {battle_tag}")
                            routed = True
                        elif battle_tag in self._recently_finished:
                            # Stray message from a battle we just finished — discard
                            pass
                        else:
                            # Battle not registered yet - buffer messages until registration
                            if battle_tag not in self.pending_battle_messages:
                                self.pending_battle_messages[battle_tag] = []
                                self.pending_battle_times[battle_tag] = time.time()
                                # Capture the current search owner (if any) so the
                                # same worker that started the search claims this battle.
                                self.pending_battle_owners[battle_tag] = self._search_owner
                            dropped = _append_bounded_pending_message(
                                self.pending_battle_messages[battle_tag],
                                msg,
                            )
                            if dropped is not None:
                                logger.warning(
                                    "Dropped oldest non-critical pending message for %s",
                                    battle_tag,
                                )
                            buffered_count = len(self.pending_battle_messages[battle_tag])
                    if routed:
                        logger.debug(f"Routed message to battle {battle_tag}")
                    elif buffered_count is not None:
                        logger.debug(
                            f"Battle {battle_tag} not registered, buffered ({buffered_count} msgs)"
                        )
                else:
                    # Non-battle message (login responses, search updates, etc.)
                    _put_bounded_nowait(self.global_queue, msg, "global")

            except websockets.exceptions.ConnectionClosed:
                logger.error("WebSocket connection closed in dispatcher")
                self._dispatcher_running = False
                # Fire off reconnection as a separate task (can't call reconnect from within dispatcher
                # since reconnect() cancels the dispatcher task)
                # Guard: only spawn reconnect if none already running
                if self._reconnect_task is None or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(self._auto_reconnect())
                break
            except asyncio.CancelledError:
                logger.info("Dispatcher cancelled")
                break
            except Exception as e:
                logger.error(f"Dispatcher error: {e}")
                await asyncio.sleep(0.1)


    async def register_battle(self, battle_tag):
        """Register a new battle and create its message queue, flushing any buffered messages.

        Uses lock to prevent race conditions with claim_pending_battle().
        """
        async with self._pending_lock:
            if battle_tag not in self.battle_queues:
                self.battle_queues[battle_tag] = asyncio.Queue(maxsize=MAX_BATTLE_QUEUE_MESSAGES)
                # Flush any messages that arrived before registration
                if battle_tag in self.pending_battle_messages:
                    buffered = self.pending_battle_messages.pop(battle_tag)
                    self.pending_battle_times.pop(battle_tag, None)
                    self.pending_battle_owners.pop(battle_tag, None)
                    for msg in buffered:
                        _put_bounded_nowait(self.battle_queues[battle_tag], msg, f"battle {battle_tag}")
                    logger.info(f"Registered battle queue: {battle_tag} (flushed {len(buffered)} buffered messages)")
                else:
                    logger.info(f"Registered battle queue: {battle_tag}")
            return self.battle_queues[battle_tag]

    def get_pending_battle_tags(self):
        """Return list of battle tags that have buffered messages but aren't registered yet"""
        return list(self.pending_battle_messages.keys())

    def peek_pending_messages(self, battle_tag):
        """Get buffered messages for an unregistered battle without consuming them"""
        return self.pending_battle_messages.get(battle_tag, [])

    def unregister_battle(self, battle_tag):
        """Remove a battle's message queue"""
        if battle_tag in self.battle_queues:
            del self.battle_queues[battle_tag]
            logger.info(f"Unregistered battle queue: {battle_tag}")
        # Mark as recently finished so the dispatcher won't re-buffer
        # stray messages (like |deinit) as a new pending battle.
        self._recently_finished[battle_tag] = time.time()
        # Purge entries older than 60s to avoid unbounded growth.
        cutoff = time.time() - 60
        self._recently_finished = {
            t: ts for t, ts in self._recently_finished.items() if ts > cutoff
        }

    def get_registered_battle_count(self):
        """Return count of registered battle queues."""
        return len(self.battle_queues)

    def _purge_stale_pending(self, max_age_seconds: int = 120) -> int:
        """Remove pending battles that have been waiting too long.

        Returns number of stale entries removed.
        """
        if not self.pending_battle_times:
            return 0
        now = time.time()
        stale = [
            tag for tag, ts in self.pending_battle_times.items()
            if (now - ts) > max_age_seconds
        ]
        for tag in stale:
            self.pending_battle_times.pop(tag, None)
            self.pending_battle_messages.pop(tag, None)
            self.pending_battle_owners.pop(tag, None)
        if stale:
            logger.info(f"Purged {len(stale)} stale pending battle(s)")
        return len(stale)

    async def get_pending_battle_count(self):
        """Return count of pending battles with buffered messages."""
        async with self._pending_lock:
            self._purge_stale_pending()
            return len(self.pending_battle_messages)

    async def claim_pending_battle(self, worker_id: int | None = None):
        """Atomically claim a pending battle, returning (battle_tag, messages) or (None, None).

        If worker_id is provided, only battles initiated by that worker (or with
        no recorded owner) are claimable. This keeps per-worker team assignments
        aligned to the search that created the battle.
        
        CRITICAL: Validates battle format matches expected_format to prevent
        claiming gen9randombattle when searching gen9ou (root cause of 9h freeze).
        """
        async with self._pending_lock:
            if not self.pending_battle_messages:
                return None, None

            for battle_tag in list(self.pending_battle_messages.keys()):
                owner = self.pending_battle_owners.get(battle_tag)
                if worker_id is not None and owner is not None and owner != worker_id:
                    continue

                # CRITICAL FIX: Validate format matches expected format
                if self.expected_format:
                    # Extract format from battle_tag (e.g., "battle-gen9ou-2539622417" -> "gen9ou")
                    match = re.match(r'battle-([a-z0-9]+)-', battle_tag)
                    if match:
                        battle_format = match.group(1)
                        if battle_format != self.expected_format:
                            # Reject this battle, it's wrong format
                            logger.warning(
                                f"Format mismatch: battle {battle_tag} is '{battle_format}', "
                                f"but we're searching '{self.expected_format}'. Rejecting (prevents 9h freeze)."
                            )
                            self.pending_battle_messages.pop(battle_tag, None)
                            self.pending_battle_times.pop(battle_tag, None)
                            self.pending_battle_owners.pop(battle_tag, None)
                            continue
                    else:
                        logger.warning(f"Could not parse format from battle_tag: {battle_tag}")

                # Drop stale pending battles to avoid blocking capacity
                ts = self.pending_battle_times.get(battle_tag)
                if ts is not None and (time.time() - ts) > 120:
                    self.pending_battle_messages.pop(battle_tag, None)
                    self.pending_battle_times.pop(battle_tag, None)
                    self.pending_battle_owners.pop(battle_tag, None)
                    continue

                messages = self.pending_battle_messages.pop(battle_tag)
                self.pending_battle_times.pop(battle_tag, None)
                self.pending_battle_owners.pop(battle_tag, None)

                # Register the battle queue immediately
                self.battle_queues[battle_tag] = asyncio.Queue(maxsize=MAX_BATTLE_QUEUE_MESSAGES)
                for msg in messages:
                    _put_bounded_nowait(self.battle_queues[battle_tag], msg, f"battle {battle_tag}")

                logger.info(f"Claimed battle {battle_tag} (flushed {len(messages)} buffered messages)")
                return battle_tag, messages

            return None, None

    async def receive_message(self):
        """Receive from global queue (for non-battle messages)

        If dispatcher is not running, reads directly from websocket.
        If dispatcher is running, reads from global queue.
        """
        await self.ensure_connection()
        if self._dispatcher_running:
            message = await self.global_queue.get()
            logger.debug("Received from global queue: {}".format(message[:100] if len(message) > 100 else message))
        else:
            # Dispatcher not started yet, read directly from websocket
            message = await self.websocket.recv()
            logger.debug("Received from websocket (pre-dispatcher): {}".format(message[:100] if len(message) > 100 else message))
        return message

    async def receive_battle_message(self, battle_tag):
        """Receive a message for a specific battle"""
        if battle_tag not in self.battle_queues:
            raise ValueError(f"Battle {battle_tag} not registered")
        message = await self.battle_queues[battle_tag].get()
        logger.debug(f"Received for battle {battle_tag}: {message[:100] if len(message) > 100 else message}")
        return message

    async def join_room(self, room_name):
        message = "/join {}".format(room_name)
        await self.send_message("", [message])
        logger.debug("Joined room '{}'".format(room_name))

    async def send_message(self, room, message_list):
        message = room + "|" + "|".join(message_list)
        priority = _classify(message)
        logger.debug("Sending message to websocket (priority=%d): %s", priority, message)
        if message.endswith("|gg") or "ttv/" in message:
            logger.info("SIGNOFF wire: %s", message)
        last_error = None
        for attempt in range(2):
            try:
                await self.ensure_connection()
                # Route through rate limiter (100ms min gap, priority queue)
                await self._send_queue.enqueue(self.websocket, message, priority=priority)
                self.last_message = message
                return
            except websockets.exceptions.ConnectionClosed as e:
                last_error = e
                logger.warning(f"WebSocket closed during send (attempt {attempt + 1}/2): {e}")
                await self.reconnect()
        if last_error:
            raise last_error

    async def avatar(self, avatar):
        await self.send_message("", ["/avatar {}".format(avatar)])
        await self.send_message("", ["/cmd userdetails {}".format(self.username)])
        while True:
            # Wait for the query response and check the avatar
            # |queryresponse|QUERYTYPE|JSON
            msg = await self.receive_message()
            msg_split = msg.split("|")
            if len(msg_split) > 1 and msg_split[1] == "queryresponse":
                user_details = json.loads(msg_split[3])
                if user_details["avatar"] == avatar:
                    logger.info("Avatar set to {}".format(avatar))
                else:
                    logger.warning(
                        "Could not set avatar to {}, avatar is {}".format(
                            avatar, user_details["avatar"]
                        )
                    )
                break

    def _clear_receive_state(self):
        global_queue = getattr(self, "global_queue", None)
        if global_queue is not None:
            _drain_queue(global_queue)

        for queue in list(getattr(self, "battle_queues", {}).values()):
            _drain_queue(queue)
        getattr(self, "battle_queues", {}).clear()
        getattr(self, "pending_battle_messages", {}).clear()
        getattr(self, "pending_battle_times", {}).clear()
        getattr(self, "pending_battle_owners", {}).clear()
        getattr(self, "_recently_finished", {}).clear()
        self.active_searches = set()
        self._search_owner = None
        self._search_owner_since = None

    async def close(self):
        await self._stop_dispatcher_task()
        # Stop the rate-limited send queue (drains/cancels pending items)
        await self._send_queue.stop()
        await self._cancel_reconnect_task()
        if self.websocket is not None:
            try:
                await self.websocket.close()
            finally:
                self.websocket = None
        self._clear_receive_state()

    async def get_id_and_challstr(self):
        while True:
            message = await self.receive_message()
            split_message = message.split("|")
            if len(split_message) > 2 and split_message[1] == "challstr":
                return split_message[2], split_message[3]

    async def login(self):
        logger.info("Logging in...")
        client_id, challstr = await self.get_id_and_challstr()

        # Local --no-security showdown server (offline eval harness): no assertion
        # is required; sending `/trn user` logs in as an unregistered local user.
        if os.getenv("FOULER_NO_SECURITY_LOGIN", "").lower() in {"1", "true", "yes", "on"}:
            message = ["/trn " + self.username]
            logger.info("Logging in via --no-security local server (no assertion)")
            await self.send_message("", message)
            deadline = time.time() + 10
            expected_id = _to_showdown_id(self.username)
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise LoginError(
                        "Timed out waiting for no-security login confirmation "
                        f"for {self.username}"
                    )
                msg = await asyncio.wait_for(
                    self.receive_message(),
                    timeout=min(remaining, 2.0),
                )
                split_msg = msg.split("|")
                if len(split_msg) > 2 and split_msg[1] == "nametaken":
                    raise LoginError(
                        "No-security login rejected for "
                        f"{self.username}: {split_msg[-1]}"
                    )
                if (
                    len(split_msg) > 3
                    and split_msg[1] == "updateuser"
                    and _to_showdown_id(split_msg[2]) == expected_id
                    and split_msg[3] == "1"
                ):
                    return expected_id

        guest_login = self.password is None

        if guest_login:
            response = requests.post(
                self.login_uri,
                data={
                    "act": "getassertion",
                    "userid": self.username,
                    "challstr": "|".join([client_id, challstr]),
                },
            )
        else:
            response = requests.post(
                self.login_uri,
                data={
                    "name": self.username,
                    "pass": self.password,
                    "challstr": "|".join([client_id, challstr]),
                },
            )

        if response.status_code != 200:
            logger.error(
                "Could not get assertion\nDetails:\n{}".format(response.content)
            )
            raise LoginError("Could not get assertion")

        if guest_login:
            assertion = response.text
        else:
            response_json = json.loads(response.text[1:])
            if "actionsuccess" not in response_json:
                logger.error("Login Unsuccessful: {}".format(response_json))
                raise LoginError("Could not log-in: {}".format(response_json))
            assertion = response_json.get("assertion")

        message = ["/trn " + self.username + ",0," + assertion]
        logger.info("Successfully logged in")
        await self.send_message("", message)
        await asyncio.sleep(3)
        return self.username if guest_login else response_json["curuser"]["userid"]

    async def update_team(self, team):
        await self.send_message("", ["/utm {}".format(team)])

    async def acquire_search_slot(self, worker_id: int):
        """Serialize ladder searches so team selection stays aligned to the worker."""
        await self._search_lock.acquire()
        self._search_owner = worker_id
        self._search_owner_since = time.time()
        logger.info(f"Worker {worker_id} acquired search slot")

    def release_search_slot(self, worker_id: int, reason: str | None = None):
        """Release the search slot if owned by this worker."""
        if self._search_owner == worker_id and self._search_lock.locked():
            self._search_owner = None
            self._search_owner_since = None
            self._search_lock.release()
            if reason:
                logger.info(f"Worker {worker_id} released search slot ({reason})")
            else:
                logger.info(f"Worker {worker_id} released search slot")

    def owns_search_slot(self, worker_id: int) -> bool:
        return self._search_owner == worker_id

    async def challenge_user(self, user_to_challenge, battle_format):
        logger.info("Challenging {}...".format(user_to_challenge))
        message = ["/challenge {},{}".format(user_to_challenge, battle_format)]
        await self.send_message("", message)
        self.last_challenge_time = time.time()

    async def accept_challenge(self, battle_format, room_name, timeout=30):
        if room_name is not None:
            await self.join_room(room_name)

        logger.info("Waiting for a {} challenge (timeout={}s)".format(battle_format, timeout))
        username = None
        deadline = time.time() + timeout
        while username is None:
            remaining = deadline - time.time()
            if remaining <= 0:
                logger.warning("accept_challenge timed out after {}s".format(timeout))
                raise asyncio.TimeoutError("No challenge received within {}s".format(timeout))
            try:
                msg = await asyncio.wait_for(self.receive_message(), timeout=min(remaining, 5.0))
            except asyncio.TimeoutError:
                continue
            split_msg = msg.split("|")
            if (
                len(split_msg) == 9
                and split_msg[1] == "pm"
                and _to_showdown_id(_strip_showdown_identity(split_msg[3]))
                == _to_showdown_id(self.username)
                and split_msg[4].startswith("/challenge")
                and split_msg[5] == battle_format
            ):
                username = _strip_showdown_identity(split_msg[2])

        message = ["/accept " + username]
        await self.send_message("", message)

    async def search_for_match(self, battle_format):
        logger.info("Searching for ranked {} match".format(battle_format))
        message = ["/search {}".format(battle_format)]
        await self.send_message("", message)

    async def cancel_search(self):
        logger.info("Cancelling active ladder search")
        message = ["/cancelsearch"]
        await self.send_message("", message)
        # Optimistically clear local state; server will confirm via updatesearch
        self.active_searches.clear()

    async def leave_battle(self, battle_tag):
        # Owner directive 2026-07-31: sign off every battle with gg + the channel plug.
        try:
            if battle_tag and str(battle_tag).startswith("battle-"):
                # Showdown's per-connection throttle silently drops rapid-fire chats
                # (verified live: /leave landed, both chats vanished). Space them out.
                await self.send_message(battle_tag, ["gg"])
                await asyncio.sleep(0.5)
                await self.send_message(battle_tag, ["ttv/thepeakmos"])
                await asyncio.sleep(0.7)
        except Exception:
            pass
        message = ["/leave {}".format(battle_tag)]
        await self.send_message("", message)

    async def forfeit_battle(self, battle_tag):
        logger.info(f"Forfeiting {battle_tag}...")
        message = ["/forfeit"]
        await self.send_message(battle_tag, message)

        # Wait for deinit confirmation from battle queue
        timeout = 10
        start = time.time()
        while time.time() - start < timeout:
            try:
                msg = await asyncio.wait_for(
                    self.receive_battle_message(battle_tag),
                    timeout=1.0
                )
                if "deinit" in msg:
                    self.unregister_battle(battle_tag)
                    return
            except asyncio.TimeoutError:
                continue
            except ValueError:
                # Battle already unregistered
                return

        # Timeout - just unregister anyway
        self.unregister_battle(battle_tag)

    async def save_replay(self, battle_tag):
        message = ["/savereplay"]
        await self.send_message(battle_tag, message)

        # Wait for the queryresponse|savereplay message
        timeout = 10  # seconds
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # Check battle queue first, then global queue
                msg = None
                try:
                    msg = await asyncio.wait_for(
                        self.receive_battle_message(battle_tag),
                        timeout=0.5
                    )
                except (asyncio.TimeoutError, ValueError):
                    try:
                        msg = await asyncio.wait_for(
                            self.global_queue.get(),
                            timeout=0.5
                        )
                    except asyncio.TimeoutError:
                        continue

                if msg is None:
                    continue

                # Check for direct replay URL (some server versions)
                if "replay.pokemonshowdown.com" in msg:
                    replay_match = re.search(r'https://replay\.pokemonshowdown\.com/([\w-]+)', msg)
                    if replay_match:
                        replay_id = replay_match.group(1)
                        if not _replay_ref_matches_battle(replay_id, battle_tag):
                            logger.warning(
                                "Ignoring savereplay URL for %s because it belongs to %s",
                                battle_tag,
                                replay_id,
                            )
                            continue
                        replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
                        logger.info(f"Replay saved: {replay_url}")
                        return replay_url

                # Handle queryresponse|savereplay|{JSON} format
                if "|queryresponse|savereplay|" in msg:
                    try:
                        json_str = msg.split("|queryresponse|savereplay|", 1)[1]
                        replay_data = json.loads(json_str)
                        replay_id = replay_data.get("id", battle_tag)
                        if not _replay_ref_matches_battle(replay_id, battle_tag):
                            logger.warning(
                                "Ignoring savereplay response for %s because replay id was %s",
                                battle_tag,
                                replay_id,
                            )
                            continue

                        # Upload the replay to create the public URL. Keep this
                        # bounded because battle finalization should never loop
                        # forever waiting on replay.pokemonshowdown.com.
                        upload_url = "https://play.pokemonshowdown.com/~~showdown/action.php"
                        post_data = {
                            "act": "uploadreplay",
                            "log": replay_data.get("log", ""),
                            "id": replay_id,
                        }

                        replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
                        last_upload_error = None
                        for attempt in range(1, REPLAY_UPLOAD_ATTEMPTS + 1):
                            try:
                                resp = requests.post(upload_url, data=post_data, timeout=15)
                                if resp.status_code == 200:
                                    logger.info(f"Replay saved: {replay_url}")
                                    return replay_url
                                last_upload_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                            except requests.RequestException as e:
                                last_upload_error = str(e)

                            if attempt < REPLAY_UPLOAD_ATTEMPTS:
                                logger.warning(
                                    "Replay upload attempt %d/%d failed for %s: %s",
                                    attempt,
                                    REPLAY_UPLOAD_ATTEMPTS,
                                    replay_id,
                                    last_upload_error,
                                )
                                if REPLAY_UPLOAD_RETRY_DELAY_SEC > 0:
                                    await asyncio.sleep(REPLAY_UPLOAD_RETRY_DELAY_SEC)

                        logger.warning(
                            "Replay upload failed after %d attempt(s) for %s: %s",
                            REPLAY_UPLOAD_ATTEMPTS,
                            replay_id,
                            last_upload_error,
                        )
                        # Still return the candidate URL so downstream proof
                        # keeps pending-public-upload semantics.
                        logger.info(f"Replay URL (upload may have failed): {replay_url}")
                        return replay_url

                    except (json.JSONDecodeError, KeyError, IndexError) as e:
                        logger.warning(f"Failed to parse savereplay response: {e}")
                        continue
            except Exception as e:
                logger.warning(f"Error in save_replay: {e}")
                continue

        logger.warning(f"No replay URL received for {battle_tag}")
        return None

    async def _cancel_reconnect_task(self):
        task = self._reconnect_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None
