#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRUTH_DIR = ROOT / "devstream" / "truth"
REMOTE = os.environ.get("FOULER_JIGGLYPUFF_SSH", "Ryanj@jigglypuff.tail4859dd.ts.net")
REMOTE_SCRIPT = os.environ.get(
    "FOULER_JIGGLYPUFF_SCRIPT",
    r"D:\Projects\fouler-play\scripts\fouler_jigglypuff_runtime.ps1",
)
OBS_HTTP = os.environ.get("FOULER_JIGGLYPUFF_OBS_HTTP", "http://jigglypuff.tail4859dd.ts.net:8777").rstrip("/")
WORKER_HTTP = os.environ.get("FOULER_JIGGLYPUFF_WORKER_HTTP", "http://jigglypuff.tail4859dd.ts.net:8791").rstrip("/")
WORKER_SECRET_ENV = Path.home() / ".config" / "deku-devstream" / "secrets" / "jigglypuff-worker.env"


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
    request = urllib.request.Request(f"{WORKER_HTTP}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            payload = parsed if isinstance(parsed, dict) else {"ok": False, "error": "resident worker returned non-object JSON"}
            payload.setdefault("ok", 200 <= response.status < 300)
            return {"ok": bool(payload.get("ok")), "returnCode": 0, "json": payload, "workerStatus": response.status, "workerUrl": f"{WORKER_HTTP}{path}", "stdout": "", "stderr": ""}
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
        except Exception:
            parsed = {"ok": False, "error": str(exc)}
        return {"ok": False, "returnCode": None, "json": parsed if isinstance(parsed, dict) else None, "workerStatus": exc.code, "workerUrl": f"{WORKER_HTTP}{path}", "stdout": "", "stderr": str(exc)}
    except Exception as exc:
        return {"ok": False, "returnCode": None, "json": None, "workerStatus": None, "workerUrl": f"{WORKER_HTTP}{path}", "stdout": "", "stderr": str(exc)}


def resident_command(
    action: str,
    *,
    execute: bool = False,
    run_count: int = 10,
    max_concurrent_battles: int = 3,
    obs_only: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
    if action == "status":
        return worker_request("/fouler/status", timeout=timeout)
    body = {"execute": bool(execute)}
    if action == "start":
        body.update({"runCount": run_count, "maxConcurrentBattles": max_concurrent_battles, "obsOnly": obs_only})
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
    run_count: int = 10,
    max_concurrent_battles: int = 3,
    obs_only: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
    resident = resident_command(
        action,
        execute=execute,
        run_count=run_count,
        max_concurrent_battles=max_concurrent_battles,
        obs_only=obs_only,
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
    if action in {"start"}:
        powershell_args.extend(["-RunCount", str(run_count), "-MaxConcurrentBattles", str(max_concurrent_battles)])
    if obs_only:
        powershell_args.append("-ObsOnly")
    if execute:
        powershell_args.append("-Execute")
    result = run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", REMOTE, *powershell_args],
        timeout=timeout,
    )
    result["residentWorker"] = {
        "attempted": True,
        "workerStatus": resident.get("workerStatus"),
        "workerUrl": resident.get("workerUrl"),
        "stderrTail": str(resident.get("stderr") or "")[-1000:],
    }
    parsed = parse_json_object(result.get("stdout", ""))
    result["json"] = parsed
    if parsed is None:
        result["stdoutTail"] = str(result.get("stdout") or "")[-3000:]
    return result


def mirror_status(
    payload: dict[str, Any] | None,
    *,
    action: str,
    raw: dict[str, Any],
    write_mirror: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
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
            "remote": REMOTE,
            "raw": {
                "returnCode": raw.get("returnCode"),
                "stderrTail": str(raw.get("stderr") or "")[-2000:],
                "stdoutTail": str(raw.get("stdout") or "")[-2000:],
                "residentWorker": raw.get("residentWorker") if isinstance(raw.get("residentWorker"), dict) else {},
            },
        }
    else:
        mirrored = dict(payload)
        mirrored["mirroredAt"] = iso_now()
        mirrored["remote"] = REMOTE
        mirrored["action"] = action
    if not write_mirror:
        mirrored["mirrorSkipped"] = True
        mirrored["liveStateMirror"] = {
            "ok": True,
            "url": f"{OBS_HTTP}/state",
            "activeBattlesMirrored": False,
            "skipped": True,
            "reason": "read-only status mode",
            "observedAt": iso_now(),
        }
        return mirrored
    mirrored["liveStateMirror"] = mirror_live_state()
    write_json(TRUTH_DIR / "jigglypuff-runtime.json", mirrored)
    return mirrored


def fetch_live_state(timeout: float = 4.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{OBS_HTTP}/state", timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def mirror_live_state() -> dict[str, Any]:
    observed_at = iso_now()
    state = fetch_live_state()
    if not isinstance(state, dict):
        return {
            "ok": False,
            "url": f"{OBS_HTTP}/state",
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
        "url": f"{OBS_HTTP}/state",
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
            "obsOnly": bool(args.obs_only),
        }
    return payload


def action_status(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    result = remote_command("status", timeout=args.timeout)
    payload = mirror_status(result.get("json"), action="status", raw=result, write_mirror=getattr(args, "mirror", True))
    return (0 if payload.get("ok") or payload.get("status") in {"ready-idle", "running"} else 1, payload)


def action_mutating(action: str, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.execute:
        payload = planned(action, args)
        return 0, payload
    result = remote_command(
        action,
        execute=True,
        run_count=getattr(args, "run_count", 10),
        max_concurrent_battles=getattr(args, "max_concurrent_battles", 3),
        obs_only=getattr(args, "obs_only", False),
        timeout=args.timeout,
    )
    payload = mirror_status(result.get("json"), action=action, raw=result)
    payload.setdefault("control", {})
    payload["control"] = {
        "returnCode": result.get("returnCode"),
        "stderrTail": str(result.get("stderr") or "")[-2000:],
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
    start.add_argument("--run-count", type=int, default=10)
    start.add_argument("--max-concurrent-battles", type=int, default=3)
    start.add_argument("--obs-only", action="store_true")
    start.add_argument("--timeout", type=int, default=180)

    stop = sub.add_parser("stop")
    stop.add_argument("--execute", action="store_true")
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
