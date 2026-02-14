#!/usr/bin/env python3
"""
Simple HTTP server to serve OBS battle display on Windows.

Provides:
- /obs (battle layout)
- /overlay (stats overlay)
- /ws (real-time state updates)
- /event (bot event hook-ins)
- /battles, /status, /state (JSON APIs)

Design goal: single source of truth from JSON files,
with WebSocket broadcasting to OBS.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
import re
import atexit
from datetime import datetime
from aiohttp import web
import aiohttp
from pathlib import Path
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency fallback
    load_dotenv = None

# Ensure repo root is on sys.path so "streaming" is importable when run as a script
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from streaming import state_store
from streaming.hybrid_dashboard import register_dashboard_routes

# Load .env if present so OBS WebSocket settings are available
if load_dotenv:
    load_dotenv()

PORT = int(os.getenv("OBS_SERVER_PORT", "8777"))
STREAMING_DIR = Path(__file__).parent
OBS_WS_HOST = os.getenv("OBS_WS_HOST", "localhost")
OBS_WS_PORT = int(os.getenv("OBS_WS_PORT", "4455"))
OBS_WS_PASSWORD = os.getenv("OBS_WS_PASSWORD", "")
OBS_BATTLE_SOURCES = [
    name.strip()
    for name in os.getenv("OBS_BATTLE_SOURCES", "").split(",")
    if name.strip()
]
OBS_IDLE_URL = os.getenv("OBS_IDLE_URL", f"http://localhost:{PORT}/idle")
OBS_FORCE_REFRESH = os.getenv("OBS_FORCE_REFRESH", "1").strip().lower() not in ("0", "false", "no", "off")
OBS_REFRESH_PAUSE_MS = int(os.getenv("OBS_REFRESH_PAUSE_MS", "120"))
OBS_SYNC_INTERVAL_SEC = int(os.getenv("OBS_SYNC_INTERVAL_SEC", "5"))
SHOWDOWN_PROFILE_URL = os.getenv("SHOWDOWN_PROFILE_URL", "").strip()
SHOWDOWN_USER_ID = os.getenv("SHOWDOWN_USER_ID", "").strip()
SHOWDOWN_ACCOUNTS = [
    acc.strip() for acc in os.getenv("SHOWDOWN_ACCOUNTS", "").split(",") if acc.strip()
]
SHOWDOWN_FORMAT = os.getenv("PS_FORMAT", "gen9ou").strip().lower()
ELO_REFRESH_COOLDOWN_SEC = int(os.getenv("SHOWDOWN_ELO_COOLDOWN_SEC", "5"))
ELO_EVENT_RETRY_SEC = int(os.getenv("SHOWDOWN_ELO_EVENT_RETRY_SEC", "8"))
ELO_POLL_INTERVAL_SEC = int(os.getenv("SHOWDOWN_ELO_POLL_SEC", "60"))
PARENT_PID = int(os.getenv("FP_PARENT_PID", "0") or 0)
PARENT_CHECK_SEC = int(os.getenv("FP_PARENT_CHECK_SEC", "5") or 5)
REPLAY_CHECK_TTL_SEC = int(os.getenv("REPLAY_CHECK_TTL_SEC", "60"))
REPLAY_CHECK_MIN_AGE_SEC = int(os.getenv("REPLAY_CHECK_MIN_AGE_SEC", "180"))
REPLAY_CHECK_TIMEOUT_SEC = int(os.getenv("REPLAY_CHECK_TIMEOUT_SEC", "4"))
REPLAY_CACHE_MAX_ENTRIES = max(100, int(os.getenv("REPLAY_CACHE_MAX_ENTRIES", "4000")))
REPLAY_CACHE_RETENTION_SEC = max(REPLAY_CHECK_TTL_SEC * 5, 300)

ws_clients: set[web.WebSocketResponse] = set()
_obs_client = None
_obs_update_lock = asyncio.Lock()
_last_obs_ids: dict[int, str | None] = {}
_last_obs_urls: dict[int, str | None] = {}
_last_obs_updates: dict[int, float] = {}
_last_obs_status: dict[int, str] = {}
_obs_sources: list[str] = []
_ladder_cache = {"accounts": {}, "updated": 0.0}
_ladder_lock = asyncio.Lock()
_last_stats = {"wins": None, "losses": None}
_last_elo_refresh_ts = 0.0
_last_elo_event_ts = 0.0
_elo_refresh_task = None
_elo_retry_task = None
_replay_cache: dict[str, dict[str, float | bool]] = {}

PID_FILE = ROOT_DIR / ".pids" / "obs_server.pid"


def _write_pid_file() -> None:
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "pid": os.getpid(),
            "name": "obs_server",
            "started_at": time.time(),
            "command": " ".join(sys.argv),
        }
        PID_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _cleanup_pid_file() -> None:
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except Exception:
        pass


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    return False
                output = (result.stdout or "") + (result.stderr or "")
                if "No tasks are running" in output:
                    return False
                return str(pid) in output
            except Exception:
                return False
        return False

try:
    from streaming.obs_websocket import ObsWebsocketClient
    _obs_client = ObsWebsocketClient(
        OBS_WS_HOST,
        OBS_WS_PORT,
        OBS_WS_PASSWORD,
    )
except Exception as e:
    print(f"[OBS-WS] Disabled (failed to init): {e}")
    _obs_client = None


def build_state_payload() -> dict:
    status = _apply_ladder_status(state_store.read_status())
    daily = state_store.read_daily_stats()
    status["today_wins"] = daily.get("wins", 0)
    status["today_losses"] = daily.get("losses", 0)
    battles_data = state_store.read_active_battles()
    battles = battles_data.get("battles", [])
    
    # Update status field based on active battles
    if battles:
        status["status"] = "Active"
        opponent = battles[0].get("opponent", "Opponent") if battles else "Opponent"
        status["battle_info"] = f"vs {opponent}"
    else:
        status["status"] = "Searching"
        status["battle_info"] = "Searching..."
    
    # Add accounts_elo to status (so overlay.html receives it via payload.status)
    accounts_elo = {}
    if _ladder_cache.get("accounts"):
        accounts_elo = dict(_ladder_cache["accounts"])
    status["accounts_elo"] = accounts_elo
    
    return {
        "status": status,
        "battles": battles,
        "count": battles_data.get("count", len(battles)),
        "max_slots": battles_data.get("max_slots"),
        "updated": battles_data.get("updated"),
        "accounts_elo": accounts_elo,  # Also keep at top-level for /state endpoint compat
    }


async def broadcast(event_type: str, payload: dict) -> None:
    if not ws_clients:
        if _obs_client:
            await maybe_update_obs_sources(payload)
        return
    message = json.dumps({
        "type": event_type,
        "payload": payload,
        "timestamp": time.time(),
    })
    disconnected = set()
    for ws in ws_clients:
        try:
            await ws.send_str(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        ws_clients.discard(ws)

    if _obs_client:
        await maybe_update_obs_sources(payload)


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    ws_clients.add(ws)
    await ws.send_str(json.dumps({
        "type": "INIT",
        "payload": build_state_payload(),
        "timestamp": time.time(),
    }))

    try:
        async for _ in ws:
            pass
    finally:
        ws_clients.discard(ws)

    return ws


async def handle_event(request: web.Request) -> web.Response:
    """Event hook for bot processes (battle start/end, stats update)."""
    data = {}
    try:
        data = await request.json()
    except Exception:
        # We don't strictly require a body, this endpoint is just a trigger.
        data = {}

    # Refresh ladder ELO on battle completion or stats update
    event_type = data.get("type", "UNKNOWN")
    payload = data.get("payload", {})
    asyncio.create_task(_process_event_update(event_type, payload))
    return web.json_response({"ok": True})


async def _merge_deku_battles(payload: dict) -> dict:
    """Merge DEKU's active battles into the payload for OBS updates."""
    try:
        deku_url = os.getenv("DEKU_STATE_URL", "http://192.168.1.40:8777/state")
        async with aiohttp.ClientSession() as sess:
            async with sess.get(deku_url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    deku_data = await resp.json()
                    deku_battles = deku_data.get("battles", [])
                    for b in deku_battles:
                        b["slot"] = 2
                    local_battles = payload.get("battles", [])
                    for b in local_battles:
                        b.setdefault("slot", 1)
                    payload["battles"] = local_battles + deku_battles
    except Exception:
        pass
    return payload


async def _process_event_update(event_type: str, payload: dict) -> None:
    try:
        print(f"[EVENT] Received event: {event_type}")
        if payload:
            print(f"[EVENT] Payload: {payload}")
        
        await maybe_refresh_elo_from_event(event_type, payload)
        state = build_state_payload()
        await broadcast("STATE_UPDATE", state)
        
        # Update OBS sources immediately when battle events come in (event-based, not polling)
        if event_type in ("BATTLE_START", "BATTLE_END"):
            print(f"[EVENT] {event_type} detected - triggering OBS update")
            if _obs_client:
                state = await _merge_deku_battles(state)
                await maybe_update_obs_sources(state)
            else:
                print(f"[EVENT] FAIL: No OBS client available for {event_type} update")
    except Exception as e:
        print(f"[EVENT] FAIL: Error processing event {event_type}: {e}")
        import traceback
        traceback.print_exc()


def _build_direct_battle_url(bid: str) -> str:
    # Use direct URL without ~~showdown to avoid "visit showdown directly" frame check.
    # OBS browser sources load as top-level pages so X-Frame-Options doesn't apply.
    # Keep spectator hash intact - it's required for spectator access.
    # Without the hash, PS redirects to homepage instead of showing the battle.
    ts = int(time.time())
    return f"https://play.pokemonshowdown.com/{bid}?r={ts}"


def _build_slot_map(battles: list[dict]) -> dict[int, dict]:
    slot_map: dict[int, dict] = {}
    for idx, battle in enumerate(battles):
        try:
            slot = int(battle.get("slot") or (idx + 1))
        except (TypeError, ValueError):
            slot = idx + 1
        slot_map[slot] = battle
    return slot_map


def _sort_slot_names(names: list[str]) -> list[str]:
    def _slot_key(name: str) -> tuple[int, str]:
        match = re.search(r"(\d+)", name)
        if match:
            return (int(match.group(1)), name.lower())
        return (999, name.lower())

    return sorted(names, key=_slot_key)


def _cache_bust(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}r={int(time.time() * 1000)}"


def _normalize_showdown_id(username: str) -> str:
    if not username:
        return ""
    return re.sub(r"[^a-z0-9]+", "", username.lower())


def _normalize_replay_id(battle_id: str) -> str:
    if not battle_id:
        return ""
    if battle_id.startswith("battle-"):
        return battle_id.replace("battle-", "", 1)
    return battle_id


def _parse_started_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _resolve_showdown_profile_url() -> str:
    if SHOWDOWN_PROFILE_URL:
        return SHOWDOWN_PROFILE_URL
    user_id = SHOWDOWN_USER_ID
    if not user_id:
        user_id = _normalize_showdown_id(os.getenv("PS_USERNAME", ""))
    if not user_id:
        return ""
    return f"https://pokemonshowdown.com/users/{user_id}"


def _resolve_showdown_user_id() -> str:
    user_id = SHOWDOWN_USER_ID
    if not user_id:
        user_id = _normalize_showdown_id(os.getenv("PS_USERNAME", ""))
    return user_id


def _extract_elo_from_profile(html: str, fmt: str) -> int | None:
    if not html:
        return None
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    if not fmt:
        fmt = "gen9ou"
    pattern = re.compile(rf"{re.escape(fmt)}\s+(\d+)", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


async def fetch_showdown_elo(user_id: str | None = None) -> int | None:
    # Prefer JSON user API (more reliable than HTML scraping).
    if not user_id:
        user_id = _resolve_showdown_user_id()
    if user_id:
        api_url = f"https://pokemonshowdown.com/users/{user_id}.json"
        timeout = aiohttp.ClientTimeout(total=6)
        headers = {"User-Agent": "FoulerPlayOBS/1.0"}
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = None
                        try:
                            data = await resp.json()
                        except Exception:
                            try:
                                text = await resp.text()
                                data = json.loads(text)
                            except Exception:
                                data = None
                        if isinstance(data, dict):
                            ratings = data.get("ratings", {})
                            if isinstance(ratings, dict):
                                entry = None
                                if SHOWDOWN_FORMAT in ratings:
                                    entry = ratings.get(SHOWDOWN_FORMAT)
                                else:
                                    # Fallback: normalize format key and try to match.
                                    fmt_norm = re.sub(r"[^a-z0-9]+", "", SHOWDOWN_FORMAT.lower())
                                    for key, value in ratings.items():
                                        if not isinstance(key, str):
                                            continue
                                        key_norm = re.sub(r"[^a-z0-9]+", "", key.lower())
                                        if key_norm == fmt_norm or key_norm.endswith(fmt_norm):
                                            entry = value
                                            break
                                if isinstance(entry, dict):
                                    for field in ("elo", "rating", "r"):
                                        elo = entry.get(field)
                                        if isinstance(elo, (int, float)):
                                            return int(elo)
        except Exception:
            pass

    # Fallback to HTML scraping if JSON API didn't work
    if not user_id:
        user_id = _resolve_showdown_user_id()
    if not user_id:
        url = _resolve_showdown_profile_url()
        if not url:
            return None
    else:
        url = f"https://pokemonshowdown.com/users/{user_id}"
    timeout = aiohttp.ClientTimeout(total=6)
    headers = {"User-Agent": "FoulerPlayOBS/1.0"}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                html = await resp.text()
        return _extract_elo_from_profile(html, SHOWDOWN_FORMAT)
    except Exception:
        return None


def _prune_replay_cache(now: float) -> None:
    stale_ids = [
        replay_id
        for replay_id, payload in _replay_cache.items()
        if (now - float(payload.get("checked", 0.0))) > REPLAY_CACHE_RETENTION_SEC
    ]
    for replay_id in stale_ids:
        _replay_cache.pop(replay_id, None)

    overflow = len(_replay_cache) - REPLAY_CACHE_MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(
            _replay_cache.items(),
            key=lambda item: float(item[1].get("checked", 0.0)),
        )[:overflow]
        for replay_id, _ in oldest:
            _replay_cache.pop(replay_id, None)


async def _replay_exists(replay_id: str) -> bool:
    if not replay_id:
        return False
    now = time.time()
    _prune_replay_cache(now)
    cached = _replay_cache.get(replay_id)
    if cached and (now - float(cached.get("checked", 0.0))) < REPLAY_CHECK_TTL_SEC:
        return bool(cached.get("exists", False))

    url = f"https://replay.pokemonshowdown.com/{replay_id}.json"
    timeout = aiohttp.ClientTimeout(total=REPLAY_CHECK_TIMEOUT_SEC)
    headers = {"User-Agent": "FoulerPlayOBS/1.0"}
    exists = False
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    exists = True
                elif resp.status in (404, 410):
                    exists = False
                else:
                    exists = False
    except Exception:
        exists = False

    _replay_cache[replay_id] = {"exists": exists, "checked": now}
    _prune_replay_cache(now)
    return exists


async def _init_elo_cache() -> None:
    try:
        accounts = SHOWDOWN_ACCOUNTS if SHOWDOWN_ACCOUNTS else [_resolve_showdown_user_id()]
        accounts = [acc for acc in accounts if acc]
        if not accounts:
            return
        
        async with _ladder_lock:
            for acc in accounts:
                elo = await fetch_showdown_elo(user_id=acc)
                if elo is not None:
                    _ladder_cache["accounts"][acc] = elo
            _ladder_cache["updated"] = time.time()
    except Exception:
        pass


async def _refresh_elo(force: bool = False) -> bool:
    """Fetch ELO from Showdown and update cache. Returns True on success."""
    global _last_elo_refresh_ts
    now = time.time()
    if not force and ELO_REFRESH_COOLDOWN_SEC > 0 and (now - _last_elo_refresh_ts) < ELO_REFRESH_COOLDOWN_SEC:
        return False

    _last_elo_refresh_ts = now
    accounts = SHOWDOWN_ACCOUNTS if SHOWDOWN_ACCOUNTS else [_resolve_showdown_user_id()]
    accounts = [acc for acc in accounts if acc]
    if not accounts:
        return False
    
    updated_any = False
    async with _ladder_lock:
        for acc in accounts:
            elo = await fetch_showdown_elo(user_id=acc)
            if elo is not None:
                _ladder_cache["accounts"][acc] = elo
                updated_any = True
        if updated_any:
            _ladder_cache["updated"] = time.time()
    return updated_any


async def _run_elo_refresh_task(*, force: bool, delay: int = 0) -> None:
    try:
        if delay > 0:
            await asyncio.sleep(max(0, delay))
        refreshed = await _refresh_elo(force=force)
        if refreshed:
            await broadcast("STATE_UPDATE", build_state_payload())
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def _schedule_elo_refresh(*, force: bool, delay: int = 0) -> None:
    global _elo_refresh_task, _elo_retry_task
    if delay <= 0:
        # Immediate event refresh: keep at most one in-flight to avoid piling up.
        if _elo_refresh_task and not _elo_refresh_task.done():
            return
        _elo_refresh_task = asyncio.create_task(_run_elo_refresh_task(force=force, delay=0))
        return

    if delay <= 0:
        return
    if _elo_retry_task and not _elo_retry_task.done():
        _elo_retry_task.cancel()
    _elo_retry_task = asyncio.create_task(_run_elo_refresh_task(force=force, delay=delay))


async def _filter_finished_battles(battles: list[dict]) -> list[dict]:
    """Filter out finished battles (replay exists). Removed stale-battle filter - most PS battles are 10-30min."""
    if not battles:
        return battles
    filtered: list[dict] = []
    now = time.time()
    
    for battle in battles:
        battle_id = battle.get("id")
        if not battle_id:
            continue
        
        started = _parse_started_iso(battle.get("started"))
        
        # Skip replay check for very recent battles to avoid false positives
        if started and REPLAY_CHECK_MIN_AGE_SEC > 0:
            age = now - started.timestamp()
            if age < REPLAY_CHECK_MIN_AGE_SEC:
                filtered.append(battle)
                continue
        
        # Check if replay exists (battle finished)
        replay_id = _normalize_replay_id(battle_id)
        if await _replay_exists(replay_id):
            # Replay exists -> battle is finished; drop from OBS updates.
            continue
        
        filtered.append(battle)
    return filtered


async def maybe_refresh_elo_from_event(event_type: str, payload: dict) -> None:
    global _last_elo_event_ts
    trigger = False
    if event_type == "BATTLE_END":
        trigger = True
    elif event_type == "STATS_UPDATE":
        wins = payload.get("wins") if isinstance(payload, dict) else None
        losses = payload.get("losses") if isinstance(payload, dict) else None
        if wins is not None or losses is not None:
            if wins != _last_stats.get("wins") or losses != _last_stats.get("losses"):
                trigger = True
            _last_stats["wins"] = wins
            _last_stats["losses"] = losses

    if not trigger:
        return

    now = time.time()
    if ELO_REFRESH_COOLDOWN_SEC > 0 and (now - _last_elo_event_ts) < ELO_REFRESH_COOLDOWN_SEC:
        return
    _last_elo_event_ts = now

    _schedule_elo_refresh(force=True, delay=0)
    if event_type == "BATTLE_END" and ELO_EVENT_RETRY_SEC > 0:
        _schedule_elo_refresh(force=True, delay=ELO_EVENT_RETRY_SEC)


def _apply_ladder_status(status: dict) -> dict:
    merged = dict(status)
    accounts = _ladder_cache.get("accounts", {})
    
    # Backward compat: if only one account, set top-level "elo" field
    if accounts:
        # Prefer SHOWDOWN_USER_ID if set, else use first account
        primary_user = _resolve_showdown_user_id()
        if primary_user and primary_user in accounts:
            merged["elo"] = accounts[primary_user]
        else:
            # Fallback: use first account's ELO
            merged["elo"] = list(accounts.values())[0]
        merged["elo_source"] = "showdown"
        merged["elo_updated"] = _ladder_cache.get("updated")
    
    return merged


def _is_overlay_source(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("overlay", "stats", "hud"))


def _is_battle_source(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("battle", "slot", "worker", "showdown"))


async def ensure_obs_sources() -> None:
    global _obs_sources
    if not _obs_client or _obs_sources:
        return
    inputs = await _obs_client.get_input_list("browser_source")
    if not inputs:
        return
    names = [item.get("inputName", "") for item in inputs if item.get("inputName")]
    if not names:
        return
    # If OBS_BATTLE_SOURCES was provided but didn't resolve, fall back to auto-detect
    if OBS_BATTLE_SOURCES:
        resolved = [n for n in OBS_BATTLE_SOURCES if n in names]
        if len(resolved) == len(OBS_BATTLE_SOURCES):
            _obs_sources = resolved
            return

    candidates = [n for n in names if _is_battle_source(n) and not _is_overlay_source(n)]
    if len(candidates) < 3:
        candidates = [n for n in names if not _is_overlay_source(n)]
    if candidates:
        ordered = _sort_slot_names(candidates)
        _obs_sources = ordered[:3] if len(ordered) >= 3 else ordered


async def maybe_update_obs_sources(payload: dict) -> None:
    print(f"[OBS-UPDATE] maybe_update_obs_sources() called")
    
    if not _obs_client:
        print(f"[OBS-UPDATE] FAIL: No OBS client (_obs_client is None)")
        return
    
    obs_connected = False
    try:
        obs_connected = not _obs_client.is_closed()
    except Exception as e:
        print(f"[OBS-UPDATE] FAIL: Failed to check OBS client status: {e}")
    
    print(f"[OBS-UPDATE] OBS client connected: {obs_connected}")
    
    if not _obs_sources:
        await ensure_obs_sources()
    if not _obs_sources:
        print(f"[OBS-UPDATE] FAIL: No OBS sources configured")
        return
    
    # Trust active_battles.json as the single source of truth.
    # The bot adds battles when they start and removes them when they finish.
    # No replay-checking or second-guessing needed.
    battles = payload.get("battles") or []
    print(f"[OBS-UPDATE] Battles in payload: {len(battles)}")
    for b in battles:
        print(f"[OBS-UPDATE]   - {b.get('id')} (slot {b.get('slot')}, opponent: {b.get('opponent')})")

    slot_map = _build_slot_map(battles)
    print(f"[OBS-UPDATE] Slot map: {dict((k, v.get('id')) for k, v in slot_map.items())}")
    print(f"[OBS-UPDATE] OBS sources: {_obs_sources}")
    
    async with _obs_update_lock:
        for idx, source_name in enumerate(_obs_sources, start=1):
            battle = slot_map.get(idx)
            desired_id = battle.get("id") if battle else None
            previous_id = _last_obs_ids.get(idx)
            
            print(f"[OBS-UPDATE] Slot {idx} ({source_name}): previous={previous_id}, desired={desired_id}")
            
            if previous_id == desired_id:
                print(f"[OBS-UPDATE] Slot {idx}: No change, skipping")
                continue
            
            if desired_id:
                if OBS_FORCE_REFRESH:
                    # Force a clean load between battles to avoid stale CEF state.
                    print(f"[OBS-UPDATE] Slot {idx}: Force refresh to idle page")
                    await _obs_client.set_browser_source_url(source_name, _cache_bust(OBS_IDLE_URL))
                    if OBS_REFRESH_PAUSE_MS > 0:
                        await asyncio.sleep(OBS_REFRESH_PAUSE_MS / 1000)
                url = _build_direct_battle_url(desired_id)
                print(f"[OBS-UPDATE] Slot {idx}: Setting to battle {desired_id}")
            else:
                url = OBS_IDLE_URL
                print(f"[OBS-UPDATE] Slot {idx}: Setting to idle page")
            
            ok = await _obs_client.set_browser_source_url(source_name, url)
            _last_obs_urls[idx] = url
            _last_obs_updates[idx] = time.time()
            _last_obs_status[idx] = "ok" if ok else "fail"
            
            if ok:
                print(f"[OBS-UPDATE] OK: Slot {idx}: Successfully updated to {url}")
                _last_obs_ids[idx] = desired_id
            else:
                print(f"[OBS-UPDATE] FAIL: Slot {idx}: Failed to update to {url}")
                # Clear tracked id so periodic sync retries this slot
                _last_obs_ids.pop(idx, None)


async def handle_obs(request: web.Request) -> web.FileResponse:
    return web.FileResponse(str(STREAMING_DIR / "obs_battles.html"))


async def handle_overlay(request: web.Request) -> web.FileResponse:
    return web.FileResponse(str(STREAMING_DIR / "overlay.html"))


async def handle_idle(request: web.Request) -> web.FileResponse:
    return web.FileResponse(str(STREAMING_DIR / "obs_idle.html"))


async def handle_debug(request: web.Request) -> web.FileResponse:
    return web.FileResponse(str(STREAMING_DIR / "obs_debug.html"))


async def handle_battles(request: web.Request) -> web.Response:
    return web.json_response(state_store.read_active_battles())


async def handle_status(request: web.Request) -> web.Response:
    status = _apply_ladder_status(state_store.read_status())
    battles_data = state_store.read_active_battles()
    battles = battles_data.get("battles", [])
    status["active_battles"] = [b.get("id") for b in battles]
    # Build battle_info from actual battles (more reliable than stale status file)
    if battles:
        status["battle_info"] = ", ".join(f"vs {b.get('opponent', 'Unknown')}" for b in battles)
    # Add daily totals
    daily = state_store.read_daily_stats()
    status["today_wins"] = daily.get("wins", 0)
    status["today_losses"] = daily.get("losses", 0)
    return web.json_response(status)


async def handle_state(request: web.Request) -> web.Response:
    return web.json_response(build_state_payload())


DEKU_STATE_URL = os.getenv("DEKU_STATE_URL", "http://192.168.1.40:8777/state")

async def handle_deku_state(request: web.Request) -> web.Response:
    """Proxy DEKU's state endpoint to avoid CORS issues in OBS browser."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DEKU_STATE_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return web.json_response(data)
    except Exception:
        return web.json_response({"error": "deku offline"}, status=502)


def build_debug_payload() -> dict:
    payload = build_state_payload()
    battles = payload.get("battles") or []
    slot_map = _build_slot_map(battles)
    max_slots = payload.get("max_slots") or max(3, len(_obs_sources))
    expected = {}
    for i in range(1, max_slots + 1):
        battle = slot_map.get(i)
        expected[i] = battle.get("id") if battle else None

    obs_connected = False
    obs_client_status = "None"
    try:
        if _obs_client:
            obs_connected = not _obs_client.is_closed()
            obs_client_status = "connected" if obs_connected else "disconnected"
        else:
            obs_client_status = "None"
    except Exception as e:
        obs_client_status = f"error: {e}"

    # Include current battles from active_battles.json
    battles_data = state_store.read_active_battles()
    current_battles = battles_data.get("battles", [])

    return {
        "updated": time.time(),
        "expected": expected,
        "current_battles": current_battles,
        "battles_file_path": str(state_store.ACTIVE_BATTLES_PATH),
        "ladder": {
            "accounts": dict(_ladder_cache.get("accounts", {})),
            "elo_updated": _ladder_cache.get("updated"),
            "last_refresh_ts": _last_elo_refresh_ts,
            "last_event_ts": _last_elo_event_ts,
            "refresh_in_flight": bool(_elo_refresh_task and not _elo_refresh_task.done()),
            "retry_in_flight": bool(_elo_retry_task and not _elo_retry_task.done()),
        },
        "obs": {
            "client_status": obs_client_status,
            "connected": obs_connected,
            "host": OBS_WS_HOST,
            "port": OBS_WS_PORT,
            "sources": list(_obs_sources),
            "last_ids": dict(_last_obs_ids),
            "last_urls": dict(_last_obs_urls),
            "last_updates": dict(_last_obs_updates),
            "last_status": dict(_last_obs_status),
            "sync_interval_sec": OBS_SYNC_INTERVAL_SEC,
            "force_refresh": OBS_FORCE_REFRESH,
        },
    }


async def handle_battles_file(request: web.Request) -> web.Response:
    return web.json_response(state_store.read_active_battles())


async def handle_debug_state(request: web.Request) -> web.Response:
    return web.json_response(build_debug_payload())


async def poll_files(app: web.Application) -> None:
    """Fallback polling to broadcast state if files change."""
    last_status_mtime = None
    last_battles_mtime = None
    last_obs_sync = 0.0
    last_elo_poll = 0.0

    while True:
        await asyncio.sleep(2)

        if PARENT_PID > 0 and not _pid_exists(PARENT_PID):
            print(f"[SERVER] Parent process {PARENT_PID} not found; shutting down.")
            os._exit(0)

        status_mtime = (
            state_store.STREAM_STATUS_PATH.stat().st_mtime
            if state_store.STREAM_STATUS_PATH.exists()
            else None
        )
        battles_mtime = (
            state_store.ACTIVE_BATTLES_PATH.stat().st_mtime
            if state_store.ACTIVE_BATTLES_PATH.exists()
            else None
        )

        if status_mtime and status_mtime != last_status_mtime:
            print(f"[POLL] Status file changed (mtime: {status_mtime})")
            last_status_mtime = status_mtime
            await broadcast("STATE_UPDATE", build_state_payload())

        if battles_mtime and battles_mtime != last_battles_mtime:
            print(f"[POLL] Battles file changed (mtime: {battles_mtime})")
            last_battles_mtime = battles_mtime
            await broadcast("STATE_UPDATE", build_state_payload())

        # Periodic OBS sync so a failed update doesn't leave a slot stale.
        # Also poll DEKU's state for cross-machine battle display in slot 2.
        if _obs_client and OBS_SYNC_INTERVAL_SEC > 0:
            now = time.time()
            if (now - last_obs_sync) >= OBS_SYNC_INTERVAL_SEC:
                print(f"[POLL] Running periodic OBS sync (interval: {OBS_SYNC_INTERVAL_SEC}s)")
                last_obs_sync = now
                local_payload = build_state_payload()
                local_payload = await _merge_deku_battles(local_payload)
                await maybe_update_obs_sources(local_payload)

        # Periodic ELO refresh in case no events fire (e.g., after restart).
        if ELO_POLL_INTERVAL_SEC > 0:
            now = time.time()
            if (now - last_elo_poll) >= ELO_POLL_INTERVAL_SEC:
                last_elo_poll = now
                try:
                    refreshed = await _refresh_elo(force=True)
                    if refreshed:
                        await broadcast("STATE_UPDATE", build_state_payload())
                except Exception:
                    pass


async def start_background_tasks(app: web.Application) -> None:
    app["poller"] = asyncio.create_task(poll_files(app))
    # Initialize ELO cache and broadcast once ready
    async def init_and_broadcast_elo():
        await _init_elo_cache()
        await broadcast("STATE_UPDATE", build_state_payload())
    app["elo_init"] = asyncio.create_task(init_and_broadcast_elo())
    if _obs_client:
        app["obs_init"] = asyncio.create_task(maybe_update_obs_sources(build_state_payload()))


async def cleanup_background_tasks(app: web.Application) -> None:
    poller = app.get("poller")
    if poller:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller
    obs_init = app.get("obs_init")
    if obs_init:
        obs_init.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await obs_init
    elo_init = app.get("elo_init")
    if elo_init:
        elo_init.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await elo_init
    refresh = _elo_refresh_task
    if refresh:
        refresh.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh
    retry = _elo_retry_task
    if retry:
        retry.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retry


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/ws", handle_ws)
    app.router.add_post("/event", handle_event)
    app.router.add_get("/obs", handle_obs)
    app.router.add_get("/overlay", handle_overlay)
    app.router.add_get("/idle", handle_idle)
    app.router.add_get("/debug", handle_debug)
    app.router.add_get("/battles", handle_battles)
    app.router.add_get("/status", handle_status)
    app.router.add_get("/state", handle_state)
    app.router.add_get("/deku-state", handle_deku_state)
    app.router.add_get("/debug_state", handle_debug_state)
    app.router.add_get("/obs-debug", handle_debug_state)  # Alias for debug_state
    app.router.add_get("/active_battles.json", handle_battles_file)
    register_dashboard_routes(app)
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    return app


if __name__ == "__main__":
    _write_pid_file()
    atexit.register(_cleanup_pid_file)
    print(f"[SERVER] Fouler Play OBS Server starting on port {PORT}")
    print(f"[SERVER] Serving files from: {STREAMING_DIR}")
    print()
    print("  OBS Browser Source URLs:")
    print(f"    Battle Display (legacy iframes): http://localhost:{PORT}/obs")
    print(f"    Stats Overlay:  http://localhost:{PORT}/overlay")
    if OBS_BATTLE_SOURCES:
        print()
        print("  OBS Direct Sources (recommended):")
        print(f"    OBS WebSocket: ws://{OBS_WS_HOST}:{OBS_WS_PORT}")
        print(f"    Battle Sources: {', '.join(OBS_BATTLE_SOURCES)}")
    print()
    print("  API Endpoints:")
    print("    GET  /battles - Active battle list")
    print("    GET  /status  - Bot status")
    print("    GET  /state   - Combined status + battles")
    print("    GET  /debug   - OBS debug overlay")
    print("    GET  /debug_state - OBS debug JSON")
    print("    GET  /obs-debug   - OBS diagnostics (client status, sources, battles)")
    print("    GET  /ws      - Real-time updates")
    print("    POST /event   - Bot event hook")
    print()
    print("[SERVER] Waiting for requests...")

    web.run_app(create_app(), host="0.0.0.0", port=PORT)
