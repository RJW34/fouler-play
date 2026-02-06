#!/usr/bin/env python3
"""
Minimal OBS WebSocket v5 client for updating Browser Source URLs.

Uses the built-in OBS WebSocket server (OBS 28+). Intended for lightweight
automation without additional dependencies.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import uuid
from typing import Any

import websockets

logger = logging.getLogger(__name__)


class ObsWebsocketClient:
    def __init__(self, host: str, port: int, password: str | None = None, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password or ""
        self.timeout = timeout
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._lock = asyncio.Lock()

    def _is_closed(self) -> bool:
        if self._ws is None:
            return True
        closed_attr = getattr(self._ws, "closed", None)
        if closed_attr is not None:
            try:
                return bool(closed_attr)
            except Exception:
                pass
        state = getattr(self._ws, "state", None)
        if state is not None:
            try:
                state_name = getattr(state, "name", str(state))
                if state_name in ("CLOSED", "CLOSING"):
                    return True
            except Exception:
                pass
        close_code = getattr(self._ws, "close_code", None)
        if close_code is not None:
            return True
        return False

    def is_closed(self) -> bool:
        return self._is_closed()

    async def connect(self) -> None:
        if self._ws and not self._is_closed():
            return

        uri = f"ws://{self.host}:{self.port}"
        logger.info("[OBS-WS] Connecting to %s", uri)
        self._ws = await websockets.connect(uri, open_timeout=self.timeout)

        hello = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
        hello_msg = json.loads(hello)
        if hello_msg.get("op") != 0:
            raise RuntimeError("Unexpected OBS WebSocket hello")

        auth = None
        auth_info = hello_msg.get("d", {}).get("authentication")
        if auth_info:
            if not self.password:
                raise RuntimeError("OBS WebSocket requires a password but none was provided")
            salt = auth_info.get("salt", "")
            challenge = auth_info.get("challenge", "")
            secret = base64.b64encode(hashlib.sha256((self.password + salt).encode()).digest()).decode()
            auth = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()

        identify = {
            "op": 1,
            "d": {
                "rpcVersion": 1,
                "authentication": auth,
            },
        }
        await self._ws.send(json.dumps(identify))

        identified = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
        identified_msg = json.loads(identified)
        if identified_msg.get("op") != 2:
            raise RuntimeError("OBS WebSocket identify failed")

        logger.info("[OBS-WS] Connected and identified")

    async def disconnect(self) -> None:
        if self._ws and not self._is_closed():
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    async def _send_request(self, request_type: str, request_data: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            await self.connect()
            if not self._ws or self._is_closed():
                raise RuntimeError("OBS WebSocket not connected")

            request_id = str(uuid.uuid4())
            payload = {
                "op": 6,
                "d": {
                    "requestType": request_type,
                    "requestId": request_id,
                    "requestData": request_data,
                },
            }
            await self._ws.send(json.dumps(payload))

            # Wait for matching response
            while True:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout)
                msg = json.loads(raw)
                if msg.get("op") != 7:
                    # Ignore events or other messages
                    continue
                data = msg.get("d", {})
                if data.get("requestId") != request_id:
                    continue
                return data

    async def set_browser_source_url(self, input_name: str, url: str) -> bool:
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._send_request(
                    "SetInputSettings",
                    {
                        "inputName": input_name,
                        "inputSettings": {"url": url},
                        "overlay": True,
                    },
                )
                status = resp.get("requestStatus", {})
                ok = bool(status.get("result", False))
                if not ok:
                    logger.warning("[OBS-WS] SetInputSettings failed for %s: %s", input_name, status)
                return ok
            except Exception as e:
                last_err = e
                logger.warning("[OBS-WS] Failed to set URL for %s (attempt %s): %s", input_name, attempt + 1, e)
                # Drop connection to allow reconnect next time
                await self.disconnect()
        if last_err:
            logger.warning("[OBS-WS] Giving up on setting URL for %s: %s", input_name, last_err)
        return False

    async def get_input_list(self, input_kind: str | None = None) -> list[dict[str, Any]]:
        try:
            payload: dict[str, Any] = {}
            if input_kind:
                payload["inputKind"] = input_kind
            resp = await self._send_request("GetInputList", payload)
            data = resp.get("responseData", {})
            return data.get("inputs", []) or []
        except Exception as e:
            logger.warning("[OBS-WS] GetInputList failed: %s", e)
            await self.disconnect()
            return []

    async def press_input_properties_button(self, input_name: str, property_name: str) -> bool:
        try:
            resp = await self._send_request(
                "PressInputPropertiesButton",
                {
                    "inputName": input_name,
                    "propertyName": property_name,
                },
            )
            status = resp.get("requestStatus", {})
            ok = bool(status.get("result", False))
            if not ok:
                logger.warning(
                    "[OBS-WS] PressInputPropertiesButton failed for %s (%s): %s",
                    input_name,
                    property_name,
                    status,
                )
            return ok
        except Exception as e:
            logger.warning(
                "[OBS-WS] PressInputPropertiesButton error for %s (%s): %s",
                input_name,
                property_name,
                e,
            )
            await self.disconnect()
            return False
