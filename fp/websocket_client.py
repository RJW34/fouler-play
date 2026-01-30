import asyncio
import websockets
import requests
import json
import time

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
        self.websocket = await websockets.connect(self.address)
        self.login_uri = (
            "https://play.pokemonshowdown.com/api/login"
            if password
            else "https://play.pokemonshowdown.com/action.php?"
        )
        return self

    async def join_room(self, room_name):
        message = "/join {}".format(room_name)
        await self.send_message("", [message])
        logger.debug("Joined room '{}'".format(room_name))

    async def receive_message(self):
        message = await self.websocket.recv()
        logger.debug("Received message from websocket: {}".format(message))
        return message

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
            if msg_split[1] == "queryresponse":
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
        await self.websocket.close()

    async def get_id_and_challstr(self):
        while True:
            message = await self.receive_message()
            split_message = message.split("|")
            if split_message[1] == "challstr":
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

        while True:
            msg = await self.receive_message()
            if battle_tag in msg and "deinit" in msg:
                return

    async def save_replay(self, battle_tag):
        message = ["/savereplay"]
        await self.send_message(battle_tag, message)
        
        # Wait for the queryresponse|savereplay message
        timeout = 10  # seconds
        start_time = time.time()
        while time.time() - start_time < timeout:
            msg = await self.receive_message()
            
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
        
        logger.warning(f"No replay URL received for {battle_tag}")
        return None
