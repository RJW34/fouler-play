"""Rate-limited, live-aware Pokemon Showdown battle chat policy."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import urllib.request


logger = logging.getLogger(__name__)


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on", "live", "active"}


POST_BATTLE_GG_MESSAGE = os.getenv("POST_BATTLE_GG_MESSAGE", "gg").strip() or "gg"
POST_BATTLE_LIVE_PROMO_MESSAGE = os.getenv(
    "POST_BATTLE_LIVE_PROMO_MESSAGE", "twitch.tv/thepeakmos"
).strip()
POST_BATTLE_CHAT_ENABLED = _env_bool("FOULER_POST_BATTLE_CHAT_ENABLED")
# This extra, newly named gate prevents stale launchers that set the historical
# live-state variables from re-enabling promotional chat by accident.
POST_BATTLE_PROMO_AUTHORIZED = _env_bool("FOULER_POST_BATTLE_PROMO_AUTHORIZED")
POST_BATTLE_CHAT_COOLDOWN_BATTLES = int(
    os.getenv("FOULER_POST_BATTLE_CHAT_COOLDOWN_BATTLES", "12")
)
STREAM_STATUS_JSON = os.getenv("FOULER_DEVSTREAM_STATUS_JSON", "").strip()
STREAM_STATUS_URL = os.getenv("FOULER_DEVSTREAM_STATUS_URL", "").strip()
_post_battle_chat_calls = 0


def _load_json_file(path_text: str) -> dict | None:
    if not path_text:
        return None
    try:
        payload = json.loads(Path(path_text).expanduser().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("Could not read devstream status JSON %s: %s", path_text, exc)
        return None


def _load_json_url(url: str) -> dict | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError) as exc:
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
    """Return true only when a configured stream-truth source says live."""
    file_signal = _extract_stream_active(
        _load_json_file(os.getenv("FOULER_DEVSTREAM_STATUS_JSON", STREAM_STATUS_JSON).strip())
    )
    if file_signal is not None:
        return file_signal
    url_signal = _extract_stream_active(
        _load_json_url(os.getenv("FOULER_DEVSTREAM_STATUS_URL", STREAM_STATUS_URL).strip())
    )
    return bool(url_signal) if url_signal is not None else False


def post_battle_messages() -> list[str]:
    """Return rate-limited chat, adding the promo only when both gates pass.

    The live-only behavior is retained for compatibility with the established
    devstream workflow. Production leaves the authorization gate disabled until
    the platform explicitly approves promotional battle chat.
    """
    global _post_battle_chat_calls
    if not POST_BATTLE_CHAT_ENABLED:
        return []
    _post_battle_chat_calls += 1
    if (
        POST_BATTLE_CHAT_COOLDOWN_BATTLES > 1
        and (_post_battle_chat_calls - 1) % POST_BATTLE_CHAT_COOLDOWN_BATTLES
    ):
        return []
    messages = [POST_BATTLE_GG_MESSAGE]
    if (
        POST_BATTLE_PROMO_AUTHORIZED
        and POST_BATTLE_LIVE_PROMO_MESSAGE
        and devstream_is_live()
    ):
        messages.append(POST_BATTLE_LIVE_PROMO_MESSAGE)
    return messages
