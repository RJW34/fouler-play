#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devstream_runtime_lease import RUNTIME_LEASE_PATH_ENV, validate_runtime_lease

TRUTH_DIR = ROOT / "devstream" / "truth"
ENV_FILES = [ROOT / ".env", ROOT / ".env.deku"]
JIGGLYPUFF_RUNTIME_START_PURPOSE = "jigglypuff-runtime-start"
JIGGLYPUFF_RUNTIME_STOP_PURPOSE = "jigglypuff-runtime-stop"
JIGGLYPUFF_TAILNET_HOST = "jigglypuff.tail4859dd.ts.net"
JIGGLYPUFF_LAN_HOST = "JIGGLYPUFF"
JIGGLYPUFF_DIRECT_IP = "192.168.1.126"
DEFAULT_OBS_HTTP = f"http://{JIGGLYPUFF_DIRECT_IP}:8777"
DEFAULT_WORKER_HTTP = f"http://{JIGGLYPUFF_DIRECT_IP}:8791"
TAILNET_OBS_HTTP = f"http://{JIGGLYPUFF_TAILNET_HOST}:8777"
TAILNET_WORKER_HTTP = f"http://{JIGGLYPUFF_TAILNET_HOST}:8791"
STATUS_WORKER_TIMEOUT_SECONDS = int(os.environ.get("FOULER_JIGGLYPUFF_STATUS_WORKER_TIMEOUT_SECONDS", "6"))
STATUS_SSH_TIMEOUT_SECONDS = int(os.environ.get("FOULER_JIGGLYPUFF_STATUS_SSH_TIMEOUT_SECONDS", "45"))
TAILNET_REMOTE = f"Ryanj@{JIGGLYPUFF_TAILNET_HOST}"
LAN_REMOTE = f"Ryanj@{JIGGLYPUFF_LAN_HOST}"
DIRECT_REMOTE = f"Ryanj@{JIGGLYPUFF_DIRECT_IP}"
REMOTE = os.environ.get("FOULER_JIGGLYPUFF_SSH", TAILNET_REMOTE)
REMOTE_SCRIPT = os.environ.get(
    "FOULER_JIGGLYPUFF_SCRIPT",
    r"D:\Projects\fouler-play\scripts\fouler_jigglypuff_runtime.ps1",
)
OBS_HTTP = os.environ.get("FOULER_JIGGLYPUFF_OBS_HTTP", DEFAULT_OBS_HTTP).rstrip("/")
WORKER_HTTP = os.environ.get("FOULER_JIGGLYPUFF_WORKER_HTTP", DEFAULT_WORKER_HTTP).rstrip("/")
WORKER_SECRET_ENV = Path.home() / ".config" / "deku-devstream" / "secrets" / "jigglypuff-worker.env"


def endpoint_candidates(primary: str, fallback_env: str, defaults: list[str]) -> list[str]:
    raw = [primary, *fallback_env.replace(";", ",").split(","), *defaults]
    candidates: list[str] = []
    seen: set[str] = set()
    for item in raw:
        url = str(item or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        candidates.append(url)
    return candidates


def remote_candidates(primary: str, fallback_env: str, defaults: list[str]) -> list[str]:
    raw = [primary, *fallback_env.replace(";", ",").split(","), *defaults]
    candidates: list[str] = []
    seen: set[str] = set()
    for item in raw:
        remote = str(item or "").strip()
        if not remote or remote in seen:
            continue
        seen.add(remote)
        candidates.append(remote)
    return candidates


OBS_HTTP_CANDIDATES = endpoint_candidates(
    OBS_HTTP,
    os.environ.get("FOULER_JIGGLYPUFF_OBS_HTTP_FALLBACKS", ""),
    [DEFAULT_OBS_HTTP, TAILNET_OBS_HTTP],
)
WORKER_HTTP_CANDIDATES = endpoint_candidates(
    WORKER_HTTP,
    os.environ.get("FOULER_JIGGLYPUFF_WORKER_HTTP_FALLBACKS", ""),
    [DEFAULT_WORKER_HTTP, TAILNET_WORKER_HTTP],
)
SSH_REMOTE_CANDIDATES = remote_candidates(
    REMOTE,
    os.environ.get("FOULER_JIGGLYPUFF_SSH_FALLBACKS", ""),
    [] if os.environ.get("FOULER_JIGGLYPUFF_SSH") else [LAN_REMOTE, DIRECT_REMOTE],
)

_LAST_LIVE_STATE_URL: str | None = None
_LAST_LIVE_HEALTH_URL: str | None = None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def strip_env_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def unquote_env_value(value: str) -> str:
    value = strip_env_inline_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env_files() -> dict[str, str]:
    env = dict(os.environ)
    for path in ENV_FILES:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = unquote_env_value(value.strip())
            if key and key not in env:
                env[key] = value
    return env


def env_value(env: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return default


def redact_text(value: Any, *, limit: int = 2000) -> str:
    text = str(value or "")[-limit:]
    return re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)(\s*[=:]\s*)([^\s'\";,]+)",
        r"\1\2[redacted]",
        text,
    )


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def worker_token() -> str:
    env = load_env(WORKER_SECRET_ENV)
    return os.environ.get("DEKU_JIGGLYPUFF_WORKER_TOKEN") or env.get("DEKU_JIGGLYPUFF_WORKER_TOKEN") or ""


def run(command: list[str], *, timeout: int = 60) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {
            "command": command,
            "returnCode": None,
            "stdout": "",
            "stderr": str(exc),
            "ok": False,
        }
    return {
        "command": command,
        "returnCode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "ok": result.returncode == 0,
    }


def ssh_candidates_for(action: str, *, execute: bool = False) -> list[str]:
    if action == "status" and not execute:
        return SSH_REMOTE_CANDIDATES
    return [REMOTE]


def run_ssh_with_fallbacks(
    *,
    action: str,
    execute: bool,
    powershell_args: list[str],
    timeout: int,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None
    for remote in ssh_candidates_for(action, execute=execute):
        result = run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", remote, *powershell_args],
            timeout=timeout,
        )
        result["remote"] = remote
        parsed = parse_json_object(result.get("stdout", ""))
        result["json"] = parsed
        attempts.append({
            "remote": remote,
            "returnCode": result.get("returnCode"),
            "ok": bool(result.get("ok")),
            "stderrTail": redact_text(result.get("stderr"), limit=1000),
            "stdoutTail": redact_text(result.get("stdout"), limit=1000) if parsed is None else "",
        })
        last_result = result
        if parsed is not None or result.get("returnCode") == 0:
            break
    if last_result is None:
        last_result = {
            "command": [],
            "returnCode": None,
            "stdout": "",
            "stderr": "no SSH candidates configured",
            "ok": False,
            "remote": REMOTE,
            "json": None,
        }
    last_result["sshAttempts"] = attempts
    return last_result


def worker_request(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 20,
    require_token: bool = False,
) -> dict[str, Any]:
    token = worker_token()
    if require_token and not token:
        return {"ok": False, "status": None, "error": "resident worker token is missing", "url": f"{WORKER_HTTP}{path}"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-DEKU-Worker-Token"] = token
    attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None
    for base_url in WORKER_HTTP_CANDIDATES:
        url = f"{base_url}{path}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                payload = parsed if isinstance(parsed, dict) else {"ok": False, "error": "resident worker returned non-object JSON"}
                payload.setdefault("ok", 200 <= response.status < 300)
                result = {
                    "ok": bool(payload.get("ok")),
                    "returnCode": 0,
                    "json": payload,
                    "workerStatus": response.status,
                    "workerUrl": url,
                    "workerAttempts": attempts,
                    "stdout": "",
                    "stderr": "",
                }
                return result
        except urllib.error.HTTPError as exc:
            try:
                parsed = json.loads(exc.read().decode("utf-8"))
            except Exception:
                parsed = {"ok": False, "error": str(exc)}
            last_result = {
                "ok": False,
                "returnCode": None,
                "json": parsed if isinstance(parsed, dict) else None,
                "workerStatus": exc.code,
                "workerUrl": url,
                "stdout": "",
                "stderr": str(exc),
            }
        except Exception as exc:
            last_result = {
                "ok": False,
                "returnCode": None,
                "json": None,
                "workerStatus": None,
                "workerUrl": url,
                "stdout": "",
                "stderr": str(exc),
            }
        attempts.append({
            "workerUrl": url,
            "workerStatus": last_result.get("workerStatus") if last_result else None,
            "stderrTail": redact_text(last_result.get("stderr") if last_result else "", limit=1000),
        })
    result = last_result or {
        "ok": False,
        "returnCode": None,
        "json": None,
        "workerStatus": None,
        "workerUrl": f"{WORKER_HTTP}{path}",
        "stdout": "",
        "stderr": "no resident worker endpoint candidates configured",
    }
    result["workerAttempts"] = attempts
    return result


def resident_command(
    action: str,
    *,
    execute: bool = False,
    run_count: int = 0,
    max_concurrent_battles: int = 3,
    max_cycles: int = 0,
    runtime_lease: str | None = None,
    obs_only: bool = False,
    enable_auto_improve: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
    if action == "status":
        return worker_request("/fouler/status", timeout=min(timeout, STATUS_WORKER_TIMEOUT_SECONDS))
    body = {"execute": bool(execute)}
    if action == "start":
        body.update({
            "runCount": run_count,
            "maxConcurrentBattles": max_concurrent_battles,
            "maxCycles": max_cycles,
            "runtimeLease": runtime_lease,
            "obsOnly": obs_only,
            "autoImprove": enable_auto_improve,
        })
    return worker_request(
        f"/fouler/{action}",
        method="POST",
        body=body,
        timeout=timeout,
        require_token=execute,
    )


def remote_command(
    action: str,
    *,
    execute: bool = False,
    run_count: int = 0,
    max_concurrent_battles: int = 3,
    max_cycles: int = 0,
    runtime_lease: str | None = None,
    obs_only: bool = False,
    enable_auto_improve: bool = False,
    no_remote_write: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
    if no_remote_write and action == "status":
        resident = {
            "attempted": False,
            "skippedNoWrite": True,
            "workerStatus": None,
            "workerUrl": "",
            "workerAttempts": [],
            "stderr": "",
        }
    else:
        resident = resident_command(
            action,
            execute=execute,
            run_count=run_count,
            max_concurrent_battles=max_concurrent_battles,
            max_cycles=max_cycles,
            runtime_lease=runtime_lease,
            obs_only=obs_only,
            enable_auto_improve=enable_auto_improve,
            timeout=min(timeout, 180),
        )
        if resident.get("json") and resident.get("workerStatus") != 404:
            resident["transport"] = "resident-worker-http"
            return resident

    powershell_args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        REMOTE_SCRIPT,
        "-Command",
        action,
    ]
    if action == "status" and no_remote_write:
        powershell_args.append("-NoWrite")
    if action in {"start"}:
        powershell_args.extend([
            "-RunCount",
            str(run_count),
            "-MaxConcurrentBattles",
            str(max_concurrent_battles),
            "-MaxCycles",
            str(max_cycles),
        ])
        if runtime_lease:
            powershell_args.extend(["-RuntimeLease", runtime_lease])
        if enable_auto_improve:
            powershell_args.append("-AutoImprove")
    if obs_only:
        powershell_args.append("-ObsOnly")
    if execute:
        powershell_args.append("-Execute")
    ssh_timeout = min(timeout, STATUS_SSH_TIMEOUT_SECONDS) if action == "status" else timeout
    result = run_ssh_with_fallbacks(
        action=action,
        execute=execute,
        powershell_args=powershell_args,
        timeout=ssh_timeout,
    )
    result["remoteStatusWriteSkipped"] = bool(action == "status" and no_remote_write)
    result["residentWorker"] = {
        "attempted": not bool(resident.get("skippedNoWrite")),
        "skippedNoWrite": bool(resident.get("skippedNoWrite")),
        "workerStatus": resident.get("workerStatus"),
        "workerUrl": resident.get("workerUrl"),
        "workerAttempts": resident.get("workerAttempts") if isinstance(resident.get("workerAttempts"), list) else [],
        "stderrTail": redact_text(resident.get("stderr"), limit=1000),
    }
    if result.get("json") is None:
        result["stdoutTail"] = redact_text(result.get("stdout"), limit=3000)
    return result


def fetch_public_runtime_json(path: str, *, timeout: float = 4.0) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    last_error = ""
    for base_url in OBS_HTTP_CANDIDATES:
        url = f"{base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                if isinstance(parsed, dict):
                    return {
                        "ok": True,
                        "url": url,
                        "statusCode": response.status,
                        "json": parsed,
                        "attempts": attempts,
                    }
                last_error = "public runtime returned non-object JSON"
        except urllib.error.HTTPError as exc:
            last_error = str(exc)
            attempts.append({"url": url, "statusCode": exc.code, "error": str(exc)})
            continue
        except Exception as exc:
            last_error = str(exc)
            attempts.append({"url": url, "statusCode": None, "error": str(exc)})
            continue
        attempts.append({"url": url, "statusCode": None, "error": last_error})
    return {"ok": False, "url": f"{OBS_HTTP}{path}", "statusCode": None, "json": None, "attempts": attempts, "error": last_error}


def fetch_live_state(timeout: float = 4.0) -> dict[str, Any] | None:
    global _LAST_LIVE_STATE_URL
    result = fetch_public_runtime_json("/state", timeout=timeout)
    _LAST_LIVE_STATE_URL = str(result.get("url") or f"{OBS_HTTP}/state")
    parsed = result.get("json")
    return parsed if isinstance(parsed, dict) else None


def fetch_live_health(timeout: float = 4.0) -> dict[str, Any] | None:
    global _LAST_LIVE_HEALTH_URL
    result = fetch_public_runtime_json("/health?deep=1", timeout=timeout)
    _LAST_LIVE_HEALTH_URL = str(result.get("url") or f"{OBS_HTTP}/health?deep=1")
    parsed = result.get("json")
    return parsed if isinstance(parsed, dict) else None


def live_battle_summary(state: dict[str, Any] | None, health: dict[str, Any] | None) -> dict[str, Any]:
    battles = state.get("battles") if isinstance(state, dict) and isinstance(state.get("battles"), list) else []
    count = len(battles)
    if isinstance(state, dict):
        try:
            count = int(state.get("count") or count)
        except (TypeError, ValueError):
            count = len(battles)
    if count == 0 and isinstance(health, dict):
        try:
            count = int(health.get("activeBattleCount") or 0)
        except (TypeError, ValueError):
            count = 0
    return {
        "activeBattleCount": max(0, count),
        "battles": battles,
        "stateUpdated": state.get("updated") if isinstance(state, dict) else None,
        "stateUrl": _LAST_LIVE_STATE_URL or f"{OBS_HTTP}/state",
        "healthUrl": _LAST_LIVE_HEALTH_URL or f"{OBS_HTTP}/health?deep=1",
    }


def synthesize_public_runtime_status(
    *,
    action: str,
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    state = fetch_live_state()
    health = fetch_live_health()
    live = live_battle_summary(state, health)
    if int(live["activeBattleCount"]) <= 0:
        return None
    health_readiness = health.get("readiness") if isinstance(health, dict) and isinstance(health.get("readiness"), dict) else {}
    health_blockers = health.get("blockers") if isinstance(health, dict) and isinstance(health.get("blockers"), list) else []
    return {
        "schemaVersion": "fouler-play-jigglypuff-runtime-mirror/v1",
        "checkedAt": iso_now(),
        "machine": "JIGGLYPUFF",
        "ok": False,
        "healthy": False,
        "running": True,
        "readyForLiveFocus": False,
        "status": "degraded-live",
        "action": action,
        "remote": raw.get("remote") or REMOTE,
        "activeBattleCount": live["activeBattleCount"],
        "liveBattles": live["battles"],
        "readiness": {
            "streamReady": bool(health_readiness.get("streamReady", True)),
            "runtimeReady": False,
            "controlPlaneReady": False,
            "proofHandoffReady": False,
        },
        "blockers": [
            "JIGGLYPUFF control-plane JSON unavailable while public runtime shows "
            f"{live['activeBattleCount']} active battle(s); HERMES must drain/adopt live runtime before claiming ready"
        ],
        "warnings": [
            "status synthesized from public /state or /health?deep=1 because resident worker/SSH status JSON was unavailable"
        ],
        "publicRuntime": {
            "stateUrl": live["stateUrl"],
            "healthUrl": live["healthUrl"],
            "stateUpdated": live["stateUpdated"],
            "healthStatus": health.get("status") if isinstance(health, dict) else None,
            "healthHealthy": health.get("healthy") if isinstance(health, dict) else None,
            "healthBlockerCount": len(health_blockers),
        },
        "raw": {
            "returnCode": raw.get("returnCode"),
            "stderrTail": redact_text(raw.get("stderr")),
            "stdoutTail": redact_text(raw.get("stdout")),
            "residentWorker": raw.get("residentWorker") if isinstance(raw.get("residentWorker"), dict) else {},
        },
    }


def mirror_status(
    payload: dict[str, Any] | None,
    *,
    action: str,
    raw: dict[str, Any],
    write_mirror: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        mirrored = synthesize_public_runtime_status(action=action, raw=raw)
        if mirrored is None:
            mirrored = {
                "schemaVersion": "fouler-play-jigglypuff-runtime-mirror/v1",
                "checkedAt": iso_now(),
                "machine": "JIGGLYPUFF",
                "ok": False,
                "healthy": False,
                "running": False,
                "status": "blocked",
                "action": action,
                "blockers": ["JIGGLYPUFF fouler runtime did not return JSON"],
                "remote": raw.get("remote") or REMOTE,
                "raw": {
                    "returnCode": raw.get("returnCode"),
                    "stderrTail": redact_text(raw.get("stderr")),
                    "stdoutTail": redact_text(raw.get("stdout")),
                    "residentWorker": raw.get("residentWorker") if isinstance(raw.get("residentWorker"), dict) else {},
                },
            }
    else:
        mirrored = dict(payload)
        mirrored["mirroredAt"] = iso_now()
        mirrored["remote"] = raw.get("remote") or REMOTE
        mirrored["action"] = action
    if not write_mirror:
        mirrored["mirrorSkipped"] = True
        mirrored["remoteStatusWriteSkipped"] = bool(raw.get("remoteStatusWriteSkipped"))
        mirrored["liveStateMirror"] = {
            "ok": True,
            "url": _LAST_LIVE_STATE_URL or f"{OBS_HTTP}/state",
            "activeBattlesMirrored": False,
            "skipped": True,
            "reason": "read-only status mode",
            "observedAt": iso_now(),
        }
        return mirrored
    mirrored["liveStateMirror"] = mirror_live_state()
    write_json(TRUTH_DIR / "jigglypuff-runtime.json", mirrored)
    return mirrored


def mirror_live_state() -> dict[str, Any]:
    observed_at = iso_now()
    state = fetch_live_state()
    if not isinstance(state, dict):
        return {
            "ok": False,
            "url": _LAST_LIVE_STATE_URL or f"{OBS_HTTP}/state",
            "activeBattlesMirrored": False,
            "observedAt": observed_at,
        }
    battles = state.get("battles")
    if not isinstance(battles, list):
        battles = []
    payload = {
        "battles": battles,
        "count": int(state.get("count") or len(battles)),
        "max_slots": int(state.get("max_slots") or max(len(battles), 3)),
        "updated": state.get("updated") or iso_now(),
        "observedAt": observed_at,
    }
    write_json(ROOT / "active_battles.json", payload)
    return {
        "ok": True,
        "url": _LAST_LIVE_STATE_URL or f"{OBS_HTTP}/state",
        "activeBattlesMirrored": True,
        "battleCount": payload["count"],
        "updated": payload["updated"],
        "observedAt": observed_at,
    }


def planned(action: str, args: argparse.Namespace) -> dict[str, Any]:
    payload = {
        "schemaVersion": "fouler-play-jigglypuff-control-plan/v1",
        "checkedAt": iso_now(),
        "machine": "JIGGLYPUFF",
        "remote": REMOTE,
        "remoteCandidates": SSH_REMOTE_CANDIDATES,
        "remoteScript": REMOTE_SCRIPT,
        "action": action,
        "execute": False,
        "planned": True,
        "message": "Pass --execute to mutate the JIGGLYPUFF fouler-play runtime.",
    }
    if action == "start":
        payload["bounds"] = {
            "runCount": args.run_count,
            "maxConcurrentBattles": args.max_concurrent_battles,
            "maxCycles": args.max_cycles,
            "obsOnly": bool(args.obs_only),
            "autoImprove": bool(getattr(args, "enable_auto_improve", False)),
        }
        payload["runtimeLease"] = {
            "requiredForExecute": True,
            "path": str(getattr(args, "runtime_lease", "") or f"${RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json"),
        }
    elif action == "stop":
        payload["runtimeLease"] = {
            "requiredForExecute": True,
            "purpose": JIGGLYPUFF_RUNTIME_STOP_PURPOSE,
            "path": str(getattr(args, "runtime_lease", "") or f"${RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json"),
        }
    return payload


def runtime_lease_guard_for_action(action: str, args: argparse.Namespace) -> dict[str, Any]:
    env = load_env_files()
    purpose = JIGGLYPUFF_RUNTIME_STOP_PURPOSE if action == "stop" else JIGGLYPUFF_RUNTIME_START_PURPOSE
    return validate_runtime_lease(
        purpose=purpose,
        lease_path=getattr(args, "runtime_lease", None),
        requested_run_count=getattr(args, "run_count", 1),
        requested_max_cycles=getattr(args, "max_cycles", 1),
        requested_max_concurrent_battles=getattr(args, "max_concurrent_battles", 1),
        requested_account=env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID") or None,
        require_run_count=True,
        require_max_cycles=True,
        require_max_concurrent_battles=True,
        require_replay_behavior=True,
    )


def start_runtime_lease_guard(args: argparse.Namespace) -> dict[str, Any]:
    return runtime_lease_guard_for_action("start", args)


def runtime_lease_blocked_payload(action: str, args: argparse.Namespace, guard: dict[str, Any]) -> dict[str, Any]:
    blockers = guard.get("blockers") if isinstance(guard.get("blockers"), list) else []
    return {
        "schemaVersion": "fouler-play-jigglypuff-control-plan/v1",
        "checkedAt": iso_now(),
        "machine": "JIGGLYPUFF",
        "remote": REMOTE,
        "remoteCandidates": SSH_REMOTE_CANDIDATES,
        "remoteScript": REMOTE_SCRIPT,
        "action": action,
        "execute": bool(getattr(args, "execute", False)),
        "blocked": True,
        "status": "blocked-runtime-lease",
        "runtimeLease": guard,
        "blockers": blockers or ["runtime lease/proof window is required"],
        "message": f"JIGGLYPUFF {action} --execute requires a current proof-window runtime lease with finite run and cycle bounds.",
    }


def action_status(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    result = remote_command(
        "status",
        timeout=args.timeout,
        no_remote_write=not bool(getattr(args, "mirror", True)),
    )
    payload = mirror_status(result.get("json"), action="status", raw=result, write_mirror=getattr(args, "mirror", True))
    return (0 if payload.get("ok") or payload.get("status") in {"ready-idle", "running"} else 1, payload)


def action_mutating(action: str, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.execute:
        payload = planned(action, args)
        return 0, payload
    if action in {"start", "stop"}:
        guard = runtime_lease_guard_for_action(action, args)
        if not guard.get("ok"):
            return 2, runtime_lease_blocked_payload(action, args, guard)
    result = remote_command(
        action,
        execute=True,
        run_count=getattr(args, "run_count", 0),
        max_concurrent_battles=getattr(args, "max_concurrent_battles", 3),
        max_cycles=getattr(args, "max_cycles", 0),
        runtime_lease=getattr(args, "runtime_lease", None),
        obs_only=getattr(args, "obs_only", False),
        enable_auto_improve=getattr(args, "enable_auto_improve", False),
        timeout=args.timeout,
    )
    payload = mirror_status(result.get("json"), action=action, raw=result)
    payload.setdefault("control", {})
    payload["control"] = {
        "returnCode": result.get("returnCode"),
        "stderrTail": redact_text(result.get("stderr")),
    }
    return (0 if result.get("returnCode") == 0 and payload.get("status") != "blocked" else 1, payload)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Control fouler-play on JIGGLYPUFF through DEKU's Tailscale SSH path.",
        epilog=(
            "Direct-IP fallback is available through env overrides: FOULER_JIGGLYPUFF_SSH, "
            "FOULER_JIGGLYPUFF_OBS_HTTP, and FOULER_JIGGLYPUFF_WORKER_HTTP."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--timeout", type=int, default=45)
    status.add_argument(
        "--read-only",
        "--no-mirror",
        dest="mirror",
        action="store_false",
        default=True,
        help="Fetch status without writing devstream truth or active battle mirror files.",
    )

    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--execute", action="store_true")
    bootstrap.add_argument("--timeout", type=int, default=1200)

    start = sub.add_parser("start")
    start.add_argument("--execute", action="store_true")
    start.add_argument("--run-count", type=int, default=0)
    start.add_argument("--max-concurrent-battles", type=int, default=3)
    start.add_argument("--max-cycles", type=int, default=0)
    start.add_argument(
        "--runtime-lease",
        help=f"Path to proof-window runtime lease JSON. Default: {RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json.",
    )
    start.add_argument("--obs-only", action="store_true")
    start.add_argument("--enable-auto-improve", action="store_true")
    start.add_argument("--timeout", type=int, default=180)

    stop = sub.add_parser("stop")
    stop.add_argument("--execute", action="store_true")
    stop.add_argument(
        "--runtime-lease",
        help=f"Path to proof-window runtime lease JSON. Default: {RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json.",
    )
    stop.add_argument("--timeout", type=int, default=120)

    login = sub.add_parser("login-proof")
    login.add_argument("--execute", action="store_true")
    login.add_argument("--timeout", type=int, default=90)

    args = parser.parse_args()
    if args.command == "status":
        code, payload = action_status(args)
    else:
        code, payload = action_mutating(args.command, args)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
