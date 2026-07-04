"""Devstream-aware Pokemon Showdown chat policy."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import urllib.request


logger = logging.getLogger(__name__)

POST_BATTLE_GG_MESSAGE = os.getenv("POST_BATTLE_GG_MESSAGE", "gg").strip() or "gg"
POST_BATTLE_LIVE_PROMO_MESSAGE = os.getenv("POST_BATTLE_LIVE_PROMO_MESSAGE", "twitch.tv/thepeakmos").strip()
POST_BATTLE_MESSAGES = [POST_BATTLE_GG_MESSAGE, POST_BATTLE_LIVE_PROMO_MESSAGE]
STREAM_STATUS_JSON = os.getenv("FOULER_DEVSTREAM_STATUS_JSON", "").strip()
STREAM_STATUS_URL = os.getenv("FOULER_DEVSTREAM_STATUS_URL", "").strip()
POST_BATTLE_CHAT_ENABLED = os.getenv("FOULER_POST_BATTLE_CHAT_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
POST_BATTLE_CHAT_COOLDOWN_BATTLES = int(os.getenv("FOULER_POST_BATTLE_CHAT_COOLDOWN_BATTLES", "12"))
_post_battle_chat_calls = 0


def _env_bool(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on", "live", "active"}:
        return True
    if value in {"0", "false", "no", "n", "off", "offline", "inactive"}:
        return False
    return None


def _load_json_file(path_text: str) -> dict | None:
    if not path_text:
        return None
    try:
        path = Path(path_text).expanduser()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.debug("Could not read devstream status JSON %s: %s", path_text, exc)
        return None


def _load_json_url(url: str) -> dict | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.debug("Could not fetch devstream status URL %s: %s", url, exc)
        return None


def _extract_stream_active(payload: dict | None) -> bool | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("streamActive")
    if isinstance(direct, bool):
        return direct
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if isinstance(summary.get("streamActive"), bool):
        return summary["streamActive"]
    obs = payload.get("obs") if isinstance(payload.get("obs"), dict) else {}
    if isinstance(obs.get("streamActive"), bool):
        return obs["streamActive"]
    nested_obs = obs.get("obs") if isinstance(obs.get("obs"), dict) else {}
    if isinstance(nested_obs.get("streamActive"), bool):
        return nested_obs["streamActive"]
    obs_runtime = payload.get("obsRuntime") if isinstance(payload.get("obsRuntime"), dict) else {}
    runtime_obs = obs_runtime.get("obs") if isinstance(obs_runtime.get("obs"), dict) else {}
    if isinstance(runtime_obs.get("streamActive"), bool):
        return runtime_obs["streamActive"]
    metrics = payload.get("metrics")
    if isinstance(metrics, list):
        for item in metrics:
            if not isinstance(item, dict) or item.get("name") != "deku_obs_stream_active":
                continue
            value = item.get("value")
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
    return None


def devstream_is_live() -> bool:
    """Return True only when a trusted live signal says OBS is streaming.

    Missing or unreadable signals intentionally fall back to offline so the bot
    never advertises the Twitch handle during private/off-stream test battles.
    """
    explicit = _env_bool("FOULER_DEVSTREAM_LIVE")
    if explicit is not None:
        return explicit
    file_signal = _extract_stream_active(
        _load_json_file(os.getenv("FOULER_DEVSTREAM_STATUS_JSON", STREAM_STATUS_JSON).strip())
    )
    if file_signal is not None:
        return file_signal
    url_signal = _extract_stream_active(_load_json_url(os.getenv("FOULER_DEVSTREAM_STATUS_URL", STREAM_STATUS_URL).strip()))
    if url_signal is not None:
        return url_signal
    return False


def post_battle_messages() -> list[str]:
    global _post_battle_chat_calls
    if not POST_BATTLE_CHAT_ENABLED:
        return []
    _post_battle_chat_calls += 1
    if POST_BATTLE_CHAT_COOLDOWN_BATTLES > 1 and (_post_battle_chat_calls - 1) % POST_BATTLE_CHAT_COOLDOWN_BATTLES:
        return []
    messages = [POST_BATTLE_GG_MESSAGE]
    if devstream_is_live() and POST_BATTLE_LIVE_PROMO_MESSAGE:
        messages.append(POST_BATTLE_LIVE_PROMO_MESSAGE)
    return messages
