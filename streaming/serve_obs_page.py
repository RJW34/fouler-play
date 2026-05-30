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
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from streaming import state_store
from streaming.hybrid_dashboard import register_dashboard_routes
from devstream_runtime_checks import recent_showdown_credential_failure

HERMES_OBS_ENV_KEYS = {
    "OBS_WS_PASSWORD",
    "OBS_WEBSOCKET_PASSWORD",
    "HERMES_OBS_WEBSOCKET_PASSWORD",
    "OBS_WS_HOST",
    "OBS_WEBSOCKET_HOST",
    "HERMES_OBS_WEBSOCKET_HOST",
    "OBS_WS_PORT",
    "OBS_WEBSOCKET_PORT",
    "HERMES_OBS_WEBSOCKET_PORT",
}


def _valid_env_value(value: str | None) -> bool:
    if value is None:
        return False
    text = value.strip().strip('"').strip("'")
    if not text or text.lower() in {"missing", "present", "[redacted]"}:
        return False
    return not (text.startswith("<") and text.endswith(">"))


def _load_hermes_obs_env() -> None:
    secret_root = os.getenv("APPDATA")
    if not secret_root:
        return
    secret_file = Path(secret_root) / "hermes-devstream" / "secrets.env"
    try:
        lines = secret_file.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in HERMES_OBS_ENV_KEYS:
            continue
        value = value.strip().strip('"').strip("'")
        if _valid_env_value(value):
            os.environ[key] = value


# Load .env first, then the HERMES secret/config file so persistent OBS
# WebSocket settings win over stale repo-local defaults without printing values.
if load_dotenv:
    load_dotenv()
_load_hermes_obs_env()

PORT = int(os.getenv("OBS_SERVER_PORT", "8777"))
STREAMING_DIR = Path(__file__).parent
OBS_WS_HOST = (
    os.getenv("OBS_WS_HOST")
    or os.getenv("OBS_WEBSOCKET_HOST")
    or os.getenv("HERMES_OBS_WEBSOCKET_HOST")
    or "localhost"
)
OBS_WS_PORT = int(
    os.getenv("OBS_WS_PORT")
    or os.getenv("OBS_WEBSOCKET_PORT")
    or os.getenv("HERMES_OBS_WEBSOCKET_PORT")
    or "4455"
)
OBS_WS_PASSWORD = (
    os.getenv("OBS_WS_PASSWORD")
    or os.getenv("OBS_WEBSOCKET_PASSWORD")
    or os.getenv("HERMES_OBS_WEBSOCKET_PASSWORD")
    or ""
)
OBS_BATTLE_SOURCES = [
    name.strip()
    for name in os.getenv("OBS_BATTLE_SOURCES", "").split(",")
    if name.strip()
]
OBS_IDLE_URL = os.getenv("OBS_IDLE_URL", f"http://localhost:{PORT}/idle")
OBS_FORCE_REFRESH = os.getenv("OBS_FORCE_REFRESH", "1").strip().lower() not in ("0", "false", "no", "off")
OBS_REFRESH_PAUSE_MS = int(os.getenv("OBS_REFRESH_PAUSE_MS", "120"))
OBS_SYNC_INTERVAL_SEC = int(os.getenv("OBS_SYNC_INTERVAL_SEC", "5"))
OBS_STALE_BATTLE_SEC = int(os.getenv("OBS_STALE_BATTLE_SEC", "600"))  # 10min: force idle if same battle
HEALTH_PROBE_TIMEOUT_SEC = float(os.getenv("FOULER_HEALTH_PROBE_TIMEOUT_SEC", "20") or "20")
DEEP_HEALTH_DEFAULT = os.getenv("FOULER_OBS_DEEP_HEALTH_DEFAULT", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
GHOST_BATTLE_MAX_AGE_SEC = int(os.getenv("GHOST_BATTLE_MAX_AGE_SEC", "1800"))  # 30min: hard ghost removal (stall games can run 20+ min)
GHOST_CHECK_INTERVAL_SEC = int(os.getenv("GHOST_CHECK_INTERVAL_SEC", "0"))  # Disabled: bot owns active_battles.json lifecycle
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
REPLAY_CHECK_TTL_SEC = int(os.getenv("REPLAY_CHECK_TTL_SEC", "30"))
REPLAY_CHECK_MIN_AGE_SEC = int(os.getenv("REPLAY_CHECK_MIN_AGE_SEC", "60"))
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
_emerald_brain_state: dict = {"status": "initializing", "objective": None, "last_action": None, "location": None, "title": "INITIALIZING", "subtitle": "Awaiting connection to Emerald ROM", "status_text": "INITIALIZING"}
_firered_brain_state: dict = {"status": "initializing", "objective": None, "last_action": None, "location": None, "title": "INITIALIZING", "subtitle": "Awaiting connection to Fire Red ROM", "status_text": "INITIALIZING"}

PID_FILE = ROOT_DIR / ".pids" / "obs_server.pid"
PROCESS_SCAN_TIMEOUT_SEC = float(os.getenv("FOULER_OBS_PROCESS_SCAN_TIMEOUT_SEC", "4") or "4")
OBS_SERVER_SCRIPT_NAME = "serve_obs_page.py"
OBS_SERVER_SCRIPT_TOKEN = "streaming/serve_obs_page.py"


def _read_pid_file() -> dict:
    try:
        if not PID_FILE.exists():
            return {}
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalized_path_text(value: object) -> str:
    return str(value or "").replace("\\", "/").lower()


def _same_repo_obs_command(command_line: object) -> bool:
    normalized = _normalized_path_text(command_line)
    repo = _normalized_path_text(ROOT_DIR)
    if OBS_SERVER_SCRIPT_NAME not in normalized:
        return False
    if _is_obs_server_launcher_wrapper(normalized):
        return False
    return repo in normalized


def _is_obs_server_launcher_wrapper(normalized_command: str) -> bool:
    return (
        "cmd.exe" in normalized_command
        and "/d /c" in normalized_command
        and OBS_SERVER_SCRIPT_NAME in normalized_command
        and (">>" in normalized_command or "1>>" in normalized_command or "2>>" in normalized_command)
    )


def _collect_process_rows() -> list[dict]:
    """Return a small process table used to guard against duplicate OBS servers."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Process | "
                        "Select-Object ProcessId,ParentProcessId,CommandLine | "
                        "ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=PROCESS_SCAN_TIMEOUT_SEC,
                check=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return []
            parsed = json.loads(result.stdout)
            if isinstance(parsed, dict):
                parsed = [parsed]
            return [
                {
                    "pid": _safe_int(row.get("ProcessId")),
                    "ppid": _safe_int(row.get("ParentProcessId")),
                    "command": row.get("CommandLine") or "",
                }
                for row in parsed
                if isinstance(row, dict)
            ]

        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            capture_output=True,
            text=True,
            timeout=PROCESS_SCAN_TIMEOUT_SEC,
            check=False,
        )
        if result.returncode != 0:
            return []
        rows: list[dict] = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            rows.append({
                "pid": _safe_int(parts[0]),
                "ppid": _safe_int(parts[1]),
                "command": parts[2],
            })
        return rows
    except Exception:
        return []


def _current_process_family(rows: list[dict]) -> set[int]:
    current = os.getpid()
    family = {current}
    parent = os.getppid()
    if parent > 0:
        family.add(parent)
    if PARENT_PID > 0:
        family.add(PARENT_PID)

    parent_by_pid = {
        _safe_int(row.get("pid")): _safe_int(row.get("ppid"))
        for row in rows
        if _safe_int(row.get("pid")) > 0
    }
    probe = current
    for _ in range(20):
        probe = parent_by_pid.get(probe, 0)
        if probe <= 0 or probe in family:
            break
        family.add(probe)
    return family


def _find_duplicate_obs_servers(rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else _collect_process_rows()
    current_family = _current_process_family(rows)
    duplicates: list[dict] = []
    for row in rows:
        pid = _safe_int(row.get("pid"))
        command = row.get("command") or ""
        if pid <= 0 or pid in current_family:
            continue
        if _same_repo_obs_command(command):
            duplicates.append({
                "pid": pid,
                "ppid": _safe_int(row.get("ppid")),
                "command": str(command)[:500],
            })
    return duplicates


def _command_for_pid(rows: list[dict], pid: int) -> str:
    for row in rows:
        if _safe_int(row.get("pid")) == pid:
            return str(row.get("command") or "")
    return ""


def _build_singleton_status(rows: list[dict] | None = None) -> dict:
    pid_data = _read_pid_file()
    pid = _safe_int(pid_data.get("pid"))
    duplicates = _find_duplicate_obs_servers(rows)
    return {
        "pid": os.getpid(),
        "pidFile": str(PID_FILE),
        "pidFilePid": pid or None,
        "pidFileAlive": _pid_exists(pid) if pid else False,
        "duplicateCount": len(duplicates),
        "duplicates": duplicates,
    }


def _acquire_singleton_or_exit() -> None:
    """Prevent watchdog/scheduler drift from creating multiple OBS surface servers."""
    if os.getenv("FOULER_OBS_SERVER_ALLOW_DUPLICATE", "").strip().lower() in ("1", "true", "yes"):
        _write_pid_file()
        return

    pid_data = _read_pid_file()
    existing_pid = _safe_int(pid_data.get("pid"))
    rows = _collect_process_rows()
    if existing_pid and existing_pid != os.getpid():
        if _pid_exists(existing_pid):
            command = _command_for_pid(rows, existing_pid)
            if not _same_repo_obs_command(command):
                print(
                    f"[SERVER] Removing stale Fouler OBS pid file; pid {existing_pid} is alive but is not this repo's OBS server.",
                    file=sys.stderr,
                )
                _cleanup_pid_file()
            else:
                print(
                    f"[SERVER] Existing Fouler OBS server pid {existing_pid} is alive; refusing duplicate start.",
                    file=sys.stderr,
                )
                sys.exit(78)
        else:
            _cleanup_pid_file()

    duplicates = _find_duplicate_obs_servers(rows)
    if duplicates:
        print(
            "[SERVER] Existing Fouler OBS server process found; refusing duplicate start: "
            + json.dumps({"duplicates": duplicates[:5]}, sort_keys=True),
            file=sys.stderr,
        )
        sys.exit(78)

    _write_pid_file()

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
    credential_failure = recent_showdown_credential_failure(ROOT_DIR)
    
    # Update status field based on active battles
    if credential_failure.get("found"):
        summary = credential_failure.get("summary") or "Showdown login failed; credential was rejected."
        status["status"] = "Credential blocked"
        status["battle_info"] = summary
        status["streaming"] = False
        status["runtime_blocked"] = True
        status["blocker_code"] = credential_failure.get("code")
        status["blocker_summary"] = summary
        status["next_fix"] = "Refresh Showdown credentials and rerun the login proof."
    elif battles:
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
        "runtime_blocked": bool(status.get("runtime_blocked")),
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
    """Merge DEKU's active battles into the payload for OBS updates.

    Only attempts the fetch when DEKU_STATE_URL is explicitly set in the
    environment.  Without it, the 3-second timeout fires every sync cycle
    and blocks source updates, causing visible flicker in OBS.
    """
    deku_url = os.getenv("DEKU_STATE_URL", "")
    if not deku_url:
        return payload
    try:
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
    # OBS browser sources should be logged into the spectator account
    # (SPECTATOR_USERNAME in .env) so they can view any battle, with or
    # without a spectator hash.  The bot invites the spectator to each battle.
    return f"https://play.pokemonshowdown.com/{bid}"


def _build_public_slot_source_url(slot: int, battle_id: str | None = None) -> str:
    url = f"http://localhost:{PORT}/slot/{slot}?slot_idle=public"
    if battle_id:
        url += f"&battle_id={battle_id}"
    return url


def _build_slot_map(battles: list[dict]) -> dict[int, dict]:
    slot_map: dict[int, dict] = {}
    for idx, battle in enumerate(battles):
        try:
            slot = int(battle.get("slot") or (idx + 1))
        except (TypeError, ValueError):
            slot = idx + 1
        slot_map[slot] = battle
    return slot_map


def _battle_for_slot(slot_num: int) -> dict | None:
    state = build_state_payload()
    battles = state.get("battles") or []
    if not isinstance(battles, list):
        return None
    return _build_slot_map(battles).get(slot_num)


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


async def _cleanup_ghost_battles() -> None:
    """Remove finished/stale battles from active_battles.json.

    Uses two strategies:
    1. Replay existence check: if a replay exists on Showdown, the battle is over.
    2. Hard age cutoff: any battle older than GHOST_BATTLE_MAX_AGE_SEC is removed.

    Writes cleaned data back to active_battles.json so OBS transitions to idle.
    """
    battles_data = state_store.read_active_battles()
    battles = battles_data.get("battles", [])
    if not battles:
        return

    # Strategy 1: replay-based check (filters out battles with existing replays)
    filtered = await _filter_finished_battles(battles)

    # Strategy 2: hard age cutoff
    now = time.time()
    age_filtered = []
    for battle in filtered:
        started = _parse_started_iso(battle.get("started"))
        if started:
            age = now - started.timestamp()
            if age > GHOST_BATTLE_MAX_AGE_SEC:
                print(f"[GHOST-CLEANUP] Battle {battle.get('id')} exceeded max age ({age:.0f}s > {GHOST_BATTLE_MAX_AGE_SEC}s)")
                continue
        age_filtered.append(battle)

    if len(age_filtered) < len(battles):
        filtered_ids = {b.get("id") for b in age_filtered}
        removed_ids = [b.get("id") for b in battles if b.get("id") not in filtered_ids]
        print(f"[GHOST-CLEANUP] Removing {len(removed_ids)} ghost battle(s): {removed_ids}")
        battles_data["battles"] = age_filtered
        battles_data["count"] = len(age_filtered)
        battles_data["updated"] = datetime.now().isoformat()
        state_store.write_active_battles(battles_data)


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
            url = _build_public_slot_source_url(idx, desired_id)
            
            print(f"[OBS-UPDATE] Slot {idx} ({source_name}): previous={previous_id}, desired={desired_id}")

            # Force stale battles to idle — if the same battle has been
            # displayed for too long, the Showdown page is likely showing
            # an empty post-game lobby.  Reset to the "SCANNING..." idle page.
            if previous_id and previous_id == desired_id and OBS_STALE_BATTLE_SEC > 0:
                last_update = _last_obs_updates.get(idx, 0)
                age = time.time() - last_update if last_update else 0
                if age > OBS_STALE_BATTLE_SEC:
                    print(f"[OBS-UPDATE] Slot {idx}: Battle stale ({age:.0f}s > {OBS_STALE_BATTLE_SEC}s), forcing idle")
                    await _obs_client.set_browser_source_url(source_name, url)
                    _last_obs_ids[idx] = None
                    _last_obs_urls[idx] = url
                    _last_obs_updates[idx] = time.time()
                    continue

            if previous_id == desired_id and _last_obs_urls.get(idx) == url:
                print(f"[OBS-UPDATE] Slot {idx}: No change, skipping")
                continue
            
            # Two-step CEF transition for ALL URL changes:
            # current → about:blank → 500ms pause → new URL
            # Forces CEF to fully unload the old page, preventing green
            # glitch bars on any transition (battle→idle, idle→battle, battle→battle).
            current_url = _last_obs_urls.get(idx)
            if current_url and current_url != "about:blank":
                await _obs_client.set_browser_source_url(source_name, "about:blank")
                await asyncio.sleep(0.5)

            if desired_id:
                print(f"[OBS-UPDATE] Slot {idx}: Loading public slot battle surface for battle {desired_id}")
            else:
                print(f"[OBS-UPDATE] Slot {idx}: Keeping public slot ready surface")

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


def _public_surface_health_payload(singleton_status: dict, probe_failure: dict | None = None) -> tuple[dict, int]:
    """Return OBS HTTP liveness when the deeper devstream probe is unavailable."""
    try:
        state = build_state_payload()
    except Exception as exc:
        return (
            {
                "schemaVersion": "fouler-obs-health/v1",
                "projectId": "fouler-play",
                "status": "degraded",
                "healthy": False,
                "running": False,
                "readyForLiveFocus": False,
                "readiness": {
                    "streamReady": False,
                    "runtimeReady": False,
                    "proofHandoffReady": False,
                },
                "blockers": [f"OBS public surface state is unreadable: {type(exc).__name__}"],
                "devstreamHealthProbe": probe_failure or {"ok": False, "method": "fallback"},
                "obsServerSingleton": singleton_status,
            },
            503,
        )

    battles = state.get("battles") if isinstance(state.get("battles"), list) else []
    status = state.get("status") if isinstance(state.get("status"), dict) else {}
    runtime_blocked = bool(state.get("runtime_blocked") or status.get("runtime_blocked"))
    blockers: list[str] = []
    if runtime_blocked:
        summary = status.get("blocker_summary") or status.get("status") or "runtime blocked"
        code = status.get("blocker_code")
        suffix = f" ({code})" if code else ""
        blockers.append(f"{summary}{suffix}")

    active_battle_count = len(battles)
    stream_ready = not blockers
    payload = {
        "schemaVersion": "fouler-obs-health/v1",
        "projectId": "fouler-play",
        "status": "running" if active_battle_count else ("ready" if stream_ready else "blocked"),
        "healthy": stream_ready,
        "running": True,
        "readyForLiveFocus": bool(stream_ready and active_battle_count),
        "readiness": {
            "streamReady": stream_ready,
            "runtimeReady": bool(stream_ready and active_battle_count),
            "proofHandoffReady": False,
        },
        "activeBattleCount": active_battle_count,
        "proofBlockers": [
            "devstream health probe unavailable; HTTP surface liveness cannot certify completed proof handoff"
        ],
        "blockers": blockers,
        "warnings": ["devstream health probe failed; using OBS public surface liveness fallback"],
        "devstreamHealthProbe": probe_failure or {"ok": False, "method": "fallback"},
        "obsServerSingleton": singleton_status,
    }
    return payload, 503 if blockers else 200


def _build_devstream_health_payload() -> dict:
    from devstream_health import build_payload

    return build_payload(check_http=False)


def _probe_failure_payload(*, method: str, error: str = "", return_code: int | None = None) -> dict:
    payload = {
        "ok": False,
        "method": method,
        "error": str(error or "").strip()[:1000],
    }
    if return_code is not None:
        payload["returnCode"] = return_code
    return payload


async def _load_devstream_health_payload(singleton_status: dict) -> tuple[dict, int]:
    try:
        payload = await asyncio.to_thread(_build_devstream_health_payload)
        payload["obsServerSingleton"] = singleton_status
        payload["devstreamHealthProbe"] = {"ok": True, "method": "in-process"}
        return payload, 200
    except Exception as exc:
        direct_failure = _probe_failure_payload(method="in-process", error=f"{type(exc).__name__}: {exc}")

    script = ROOT_DIR / "scripts" / "devstream_health.py"
    if script.exists():
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(script), "--skip-http"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                timeout=HEALTH_PROBE_TIMEOUT_SEC,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                payload = json.loads(result.stdout)
                payload["obsServerSingleton"] = singleton_status
                payload["devstreamHealthProbe"] = {"ok": True, "method": "subprocess"}
                return payload, 200
            failure = _probe_failure_payload(
                method="subprocess",
                return_code=result.returncode,
                error=result.stderr or direct_failure["error"],
            )
            return _public_surface_health_payload(singleton_status, failure)
        except Exception as exc:
            failure = _probe_failure_payload(method="subprocess", error=f"{type(exc).__name__}: {exc}")
            return _public_surface_health_payload(singleton_status, failure)

    return _public_surface_health_payload(singleton_status, direct_failure)


async def handle_health(request: web.Request) -> web.Response:
    singleton_status = _build_singleton_status()
    deep_requested = DEEP_HEALTH_DEFAULT or request.query.get("deep", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if deep_requested:
        payload, status_code = await _load_devstream_health_payload(singleton_status)
    else:
        payload, status_code = _public_surface_health_payload(
            singleton_status,
            {
                "ok": None,
                "method": "skipped",
                "reason": "deep devstream proof health is available at /health?deep=1",
            },
        )
    if singleton_status.get("duplicateCount"):
        payload["status"] = "degraded"
        payload["healthy"] = False
        payload["readyForLiveFocus"] = False
        payload["error"] = "duplicate OBS surface server processes detected"
        blockers = payload.setdefault("blockers", [])
        if isinstance(blockers, list):
            blockers.append("duplicate OBS surface server processes detected")
        return web.json_response(payload, status=503)
    return web.json_response(payload, status=status_code)


async def handle_status(request: web.Request) -> web.Response:
    status = _apply_ladder_status(state_store.read_status())
    battles_data = state_store.read_active_battles()
    battles = battles_data.get("battles", [])
    status["active_battles"] = [b.get("id") for b in battles]
    # Build battle_info from actual battles (more reliable than stale status file)
    if battles:
        status["status"] = "Active"
        status["battle_info"] = ", ".join(f"vs {b.get('opponent', 'Unknown')}" for b in battles)
    else:
        status["status"] = "Searching"
        status["battle_info"] = "Searching..."
    # Add daily totals
    daily = state_store.read_daily_stats()
    status["today_wins"] = daily.get("wins", 0)
    status["today_losses"] = daily.get("losses", 0)
    return web.json_response(status)


async def handle_state(request: web.Request) -> web.Response:
    return web.json_response(build_state_payload())


DEKU_STATE_URL = os.getenv("DEKU_STATE_URL", "http://127.0.0.1:8777/state")
MAGNETON_STATE_URL = os.getenv("MAGNETON_STATE_URL", "http://jigglypuff.tail4859dd.ts.net:8777/state")

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
    last_ghost_check = 0.0

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

        # Periodic ghost battle cleanup: verify battles are still alive
        # using replay existence check and hard age cutoff.
        if GHOST_CHECK_INTERVAL_SEC > 0:
            now = time.time()
            if (now - last_ghost_check) >= GHOST_CHECK_INTERVAL_SEC:
                last_ghost_check = now
                try:
                    await _cleanup_ghost_battles()
                except Exception as e:
                    print(f"[GHOST-CLEANUP] Error: {e}")


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




# ── Grid Panel Handlers ──

async def handle_fouler_stats(request: web.Request) -> web.FileResponse:
    """Serve the Fouler Stats panel HTML."""
    return web.FileResponse(str(STREAMING_DIR / "fouler_stats.html"))


async def handle_emerald_brain(request: web.Request) -> web.FileResponse:
    """Serve the Emerald AI Brain panel HTML."""
    return web.FileResponse(str(STREAMING_DIR / "emerald_brain.html"))


async def handle_emerald_brain_state(request: web.Request) -> web.Response:
    """Return current emerald brain state as JSON."""
    return web.json_response(_emerald_brain_state)


async def handle_emerald_update(request: web.Request) -> web.Response:
    """POST endpoint to update Emerald AI brain state."""
    global _emerald_brain_state
    try:
        data = await request.json()
        if isinstance(data, dict):
            for key in ("status", "objective", "last_action", "location", "title", "subtitle", "status_text", "progress"):
                if key in data:
                    _emerald_brain_state[key] = data[key]
            _emerald_brain_state["updated"] = time.time()
        return web.json_response({"ok": True, "state": _emerald_brain_state})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_firered_brain(request: web.Request) -> web.FileResponse:
    """Serve the Fire Red AI Brain panel HTML."""
    return web.FileResponse(str(STREAMING_DIR / "firered_brain.html"))


async def handle_firered_brain_state(request: web.Request) -> web.Response:
    """Return current firered brain state as JSON."""
    return web.json_response(_firered_brain_state)


async def handle_firered_update(request: web.Request) -> web.Response:
    """POST endpoint to update Fire Red AI brain state."""
    global _firered_brain_state
    try:
        data = await request.json()
        if isinstance(data, dict):
            for key in ("status", "objective", "last_action", "location", "title", "subtitle", "status_text", "progress"):
                if key in data:
                    _firered_brain_state[key] = data[key]
            _firered_brain_state["updated"] = __import__('time').time()
        return web.json_response({"ok": True, "state": _firered_brain_state})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_magneton_state(request: web.Request) -> web.Response:
    """Proxy MAGNETON state endpoint to avoid CORS issues in OBS browser sources."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(MAGNETON_STATE_URL, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return web.json_response(data)
    except Exception:
        return web.json_response({"error": "magneton offline"}, status=502)



BATTLE_SLOT_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Battle Slot {slot}</title>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;700;900&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:100vw;height:100vh;overflow:hidden;background:#0f0f1b;
font-family:'Outfit',system-ui,sans-serif;color:#eaeaea}}
#scanning{{position:absolute;top:0;left:0;width:100%;height:100%;
display:flex;flex-direction:column;align-items:center;justify-content:center;
z-index:2;transition:opacity 0.3s}}
#battle-frame{{position:absolute;top:0;left:0;width:100%;height:100%;
border:none;z-index:1}}
.hidden{{opacity:0;pointer-events:none}}
.sonar{{width:120px;height:120px;position:relative;margin-bottom:24px}}
.sonar-circle{{position:absolute;top:50%;left:50%;
transform:translate(-50%,-50%);border:1.5px solid #08d9d6;
border-radius:50%;opacity:0;animation:sonar-ping 4s infinite linear}}
.sonar-circle:nth-child(2){{animation-delay:1s}}
.sonar-circle:nth-child(3){{animation-delay:2s}}
.sonar-circle:nth-child(4){{animation-delay:3s}}
@keyframes sonar-ping{{0%{{width:0;height:0;opacity:1}}100%{{width:100%;height:100%;opacity:0}}}}
.sonar-scanner{{position:absolute;top:0;left:0;width:100%;height:100%;
background:conic-gradient(from 0deg,transparent 0deg,#08d9d6 360deg);
opacity:0.1;border-radius:50%;animation:spin 4s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.scanning-text{{font-size:13px;font-weight:800;letter-spacing:3px;text-transform:uppercase;
color:#08d9d6;text-shadow:0 0 12px rgba(8,217,214,0.4);animation:pulse 2s ease-in-out infinite}}
.scanning-sub{{margin-top:8px;font-size:10px;letter-spacing:2px;text-transform:uppercase;
color:rgba(8,217,214,0.4)}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.5}}}}
</style></head><body>
<div id="scanning">
  <div class="sonar">
    <div class="sonar-scanner"></div>
    <div class="sonar-circle"></div>
    <div class="sonar-circle"></div>
    <div class="sonar-circle"></div>
    <div class="sonar-circle"></div>
  </div>
  <div class="scanning-text">MATCHMAKING</div>
  <div class="scanning-sub">RANKED BATTLE FEED {slot}</div>
</div>
<iframe id="battle-frame" class="hidden"></iframe>
<script>
(function(){{
  var SLOT={slot};
  var STATE_URL='/slot/'+SLOT+'/state';
  var POLL_MS=3000;
  var activeBid=null;
  var scanning=document.getElementById('scanning');
  var frame=document.getElementById('battle-frame');

  function slotOf(b,i){{return b.slot!=null?parseInt(b.slot):(i+1);}}

  function showBattle(bid, battleUrl){{
    var url=(battleUrl||('https://play.pokemonshowdown.com/'+bid))+'?r='+Date.now();
    // Pokemon Showdown intentionally refuses battle display inside an iframe.
    // OBS browser sources should leave this local polling page and load the
    // battle URL as the top-level page.
    window.location.replace(url);
  }}

  function showScanning(){{
    frame.classList.add('hidden');
    frame.src='about:blank';
    scanning.classList.remove('hidden');
  }}

  function poll(){{
    fetch(STATE_URL+'?t='+Date.now())
      .then(function(r){{return r.json();}})
      .then(function(d){{
        var battleId=d.battle_id||null;
        var battleUrl=d.url||null;
        if(battleId){{
          if(battleId!==activeBid){{
            activeBid=battleId;
            showBattle(battleId,battleUrl);
          }}
        }} else {{
          if(activeBid!==null){{
            activeBid=null;
            showScanning();
          }}
        }}
      }})
      .catch(function(){{}});
  }}
  poll();
  setInterval(poll,POLL_MS);
}})();
</script>
</body></html>"""


BATTLE_REDIRECT_HTML = BATTLE_SLOT_HTML  # deprecated alias



async def handle_slot_state(request: web.Request) -> web.Response:
    """Return JSON state for a specific battle slot.

    Used by the client-side redirect page to poll for battle changes/endings.
    Returns: {"slot": N, "battle_id": "...", "url": "..."} or
             {"slot": N, "battle_id": null, "url": null}
    """
    slot_str = request.match_info.get("slot", "1")
    try:
        slot_num = int(slot_str)
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid slot"}, status=400)

    if slot_num < 1 or slot_num > 9:
        return web.json_response({"error": "invalid slot"}, status=400)

    battle = _battle_for_slot(slot_num)

    if battle:
        battle_id = battle.get("id")
        url = _build_direct_battle_url(battle_id) if battle_id else None
        return web.json_response({
            "slot": slot_num,
            "battle_id": battle_id,
            "url": url,
        })

    return web.json_response({
        "slot": slot_num,
        "battle_id": None,
        "url": None,
    })


async def handle_battle_slot(request: web.Request) -> web.Response:
    """Battle slot OBS browser source.

    Always serves the self-managing BATTLE_SLOT_HTML page: shows SCANNING
    animation by default, polls /slot/N/state, and redirects to PS
    via window.location.replace() when a battle is active for this slot.

    obs-battle-sync pins all slots to /slot/N every 5 min so OBS returns to
    SCANNING automatically after battles end (no permanent-stick bug).
    """
    slot_str = request.match_info.get("slot", "1")
    try:
        slot_num = int(slot_str)
    except (TypeError, ValueError):
        return web.Response(text="Invalid slot", status=400)

    if slot_num < 1 or slot_num > 9:
        return web.Response(text="Invalid slot", status=400)

    return web.Response(
        text=BATTLE_SLOT_HTML.format(slot=slot_num),
        content_type="text/html",
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/health", handle_health)
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
    app.router.add_get("/fouler-stats", handle_fouler_stats)
    app.router.add_get("/emerald-brain", handle_emerald_brain)
    app.router.add_get("/emerald-brain-state", handle_emerald_brain_state)
    app.router.add_post("/emerald-update", handle_emerald_update)
    app.router.add_get("/firered-brain", handle_firered_brain)
    app.router.add_get("/firered-brain-state", handle_firered_brain_state)
    app.router.add_post("/firered-update", handle_firered_update)
    app.router.add_get("/magneton-state", handle_magneton_state)
    app.router.add_get("/slot/{slot}/state", handle_slot_state)
    app.router.add_get("/slot/{slot}", handle_battle_slot)
    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    return app


if __name__ == "__main__":
    _acquire_singleton_or_exit()
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
