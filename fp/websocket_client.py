import asyncio
import websockets
import requests
import json
import time
import re

import logging

logger = logging.getLogger(__name__)


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
    async def create(cls, username, password, address):
        self = PSWebsocketClient()
        self.username = username
        self.password = password
        self.address = address
        self.websocket = await websockets.connect(
            self.address,
            ping_interval=20,  # Send ping every 20s to keep connection alive
            ping_timeout=20,   # Wait 20s for pong before considering connection dead
            close_timeout=10,  # Wait 10s for close handshake
        )
        self.login_uri = (
            "https://play.pokemonshowdown.com/api/login"
            if password
            else "https://play.pokemonshowdown.com/action.php?"
        )
        # Message routing for concurrent battles
        self.battle_queues = {}  # battle_tag -> asyncio.Queue
        self.pending_battle_messages = {}  # battle_tag -> list of msgs (pre-registration buffer)
        self.global_queue = asyncio.Queue()  # for non-battle messages (login, search, etc.)
        self.dispatcher_task = None
        self._dispatcher_running = False
        return self

    def start_dispatcher(self):
        """Start the background message dispatcher"""
        if not self._dispatcher_running:
            self._dispatcher_running = True
            self.dispatcher_task = asyncio.create_task(self._message_dispatcher())
            logger.info("Message dispatcher started")

    def stop_dispatcher(self):
        """Stop the background message dispatcher"""
        self._dispatcher_running = False
        if self.dispatcher_task:
            self.dispatcher_task.cancel()
            self.dispatcher_task = None

    async def _message_dispatcher(self):
        """Background task that routes incoming messages to correct queues"""
        battle_tag_pattern = re.compile(r'^>(battle-[a-z0-9]+-\d+)')

        while self._dispatcher_running:
            try:
                msg = await self.websocket.recv()
                logger.debug("Dispatcher received: {}".format(msg[:100] if len(msg) > 100 else msg))

                # Check if this is a battle-specific message
                first_line = msg.split("\n")[0]
                match = battle_tag_pattern.match(first_line)

                if match:
                    battle_tag = match.group(1)
                    if battle_tag in self.battle_queues:
                        await self.battle_queues[battle_tag].put(msg)
                        logger.debug(f"Routed message to battle {battle_tag}")
                    else:
                        # Battle not registered yet - buffer messages until registration
                        if battle_tag not in self.pending_battle_messages:
                            self.pending_battle_messages[battle_tag] = []
                        self.pending_battle_messages[battle_tag].append(msg)
                        logger.debug(f"Battle {battle_tag} not registered, buffered ({len(self.pending_battle_messages[battle_tag])} msgs)")
                else:
                    # Non-battle message (login responses, search updates, etc.)
                    await self.global_queue.put(msg)

            except websockets.exceptions.ConnectionClosed:
                logger.error("WebSocket connection closed")
                self._dispatcher_running = False
                break
            except asyncio.CancelledError:
                logger.info("Dispatcher cancelled")
                break
            except Exception as e:
                logger.error(f"Dispatcher error: {e}")
                await asyncio.sleep(0.1)

    def register_battle(self, battle_tag):
        """Register a new battle and create its message queue, flushing any buffered messages"""
        if battle_tag not in self.battle_queues:
            self.battle_queues[battle_tag] = asyncio.Queue()
            # Flush any messages that arrived before registration
            if battle_tag in self.pending_battle_messages:
                buffered = self.pending_battle_messages.pop(battle_tag)
                for msg in buffered:
                    self.battle_queues[battle_tag].put_nowait(msg)
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

    async def receive_message(self):
        """Receive from global queue (for non-battle messages)

        If dispatcher is not running, reads directly from websocket.
        If dispatcher is running, reads from global queue.
        """
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
        logger.debug("Sending message to websocket: {}".format(message))
        await self.websocket.send(message)
        self.last_message = message

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

    async def close(self):
        self.stop_dispatcher()
        await self.websocket.close()

    async def get_id_and_challstr(self):
        while True:
            message = await self.receive_message()
            split_message = message.split("|")
            if len(split_message) > 2 and split_message[1] == "challstr":
                return split_message[2], split_message[3]

    async def login(self):
        logger.info("Logging in...")
        client_id, challstr = await self.get_id_and_challstr()

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

    async def challenge_user(self, user_to_challenge, battle_format):
        logger.info("Challenging {}...".format(user_to_challenge))
        message = ["/challenge {},{}".format(user_to_challenge, battle_format)]
        await self.send_message("", message)
        self.last_challenge_time = time.time()

    async def accept_challenge(self, battle_format, room_name):
        if room_name is not None:
            await self.join_room(room_name)

        logger.info("Waiting for a {} challenge".format(battle_format))
        username = None
        while username is None:
            msg = await self.receive_message()
            split_msg = msg.split("|")
            if (
                len(split_msg) == 9
                and split_msg[1] == "pm"
                and split_msg[3].strip().replace("!", "").replace("‽", "")
                == self.username
                and split_msg[4].startswith("/challenge")
                and split_msg[5] == battle_format
            ):
                username = split_msg[2].strip()

        message = ["/accept " + username]
        await self.send_message("", message)

    async def search_for_match(self, battle_format):
        logger.info("Searching for ranked {} match".format(battle_format))
        message = ["/search {}".format(battle_format)]
        await self.send_message("", message)

    async def leave_battle(self, battle_tag):
        message = ["/leave {}".format(battle_tag)]
        await self.send_message("", message)

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
                    import re
                    replay_match = re.search(r'https://replay\.pokemonshowdown\.com/([\w-]+)', msg)
                    if replay_match:
                        replay_url = f"https://replay.pokemonshowdown.com/{replay_match.group(1)}"
                        logger.info(f"Replay saved: {replay_url}")
                        return replay_url

                # Handle queryresponse|savereplay|{JSON} format
                if "|queryresponse|savereplay|" in msg:
                    try:
                        json_str = msg.split("|queryresponse|savereplay|", 1)[1]
                        replay_data = json.loads(json_str)

                        # Upload the replay to create the public URL
                        upload_url = "https://play.pokemonshowdown.com/~~showdown/action.php"
                        post_data = {
                            "act": "uploadreplay",
                            "log": replay_data.get("log", ""),
                            "id": replay_data.get("id", battle_tag),
                        }

                        resp = requests.post(upload_url, data=post_data, timeout=15)

                        if resp.status_code == 200:
                            # Response should contain the replay URL or ID
                            replay_id = replay_data.get("id", battle_tag)
                            replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
                            logger.info(f"Replay saved: {replay_url}")
                            return replay_url
                        else:
                            logger.warning(f"Replay upload failed with status {resp.status_code}: {resp.text[:200]}")
                            # Still return the URL - replay might exist anyway
                            replay_id = replay_data.get("id", battle_tag)
                            replay_url = f"https://replay.pokemonshowdown.com/{replay_id}"
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
