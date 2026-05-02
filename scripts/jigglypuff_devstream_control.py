#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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


def remote_command(
    action: str,
    *,
    execute: bool = False,
    run_count: int = 10,
    max_concurrent_battles: int = 1,
    obs_only: bool = False,
    timeout: int = 90,
) -> dict[str, Any]:
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
    parsed = parse_json_object(result.get("stdout", ""))
    result["json"] = parsed
    if parsed is None:
        result["stdoutTail"] = str(result.get("stdout") or "")[-3000:]
    return result


def mirror_status(payload: dict[str, Any] | None, *, action: str, raw: dict[str, Any]) -> dict[str, Any]:
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
            },
        }
    else:
        mirrored = dict(payload)
        mirrored["mirroredAt"] = iso_now()
        mirrored["remote"] = REMOTE
        mirrored["action"] = action
    write_json(TRUTH_DIR / "jigglypuff-runtime.json", mirrored)
    return mirrored


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
    payload = mirror_status(result.get("json"), action="status", raw=result)
    return (0 if payload.get("ok") or payload.get("status") in {"ready-idle", "running"} else 1, payload)


def action_mutating(action: str, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if not args.execute:
        payload = planned(action, args)
        return 0, payload
    result = remote_command(
        action,
        execute=True,
        run_count=getattr(args, "run_count", 10),
        max_concurrent_battles=getattr(args, "max_concurrent_battles", 1),
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
    parser = argparse.ArgumentParser(description="Control fouler-play on JIGGLYPUFF through DEKU's Tailscale SSH path.")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--timeout", type=int, default=45)

    bootstrap = sub.add_parser("bootstrap")
    bootstrap.add_argument("--execute", action="store_true")
    bootstrap.add_argument("--timeout", type=int, default=1200)

    start = sub.add_parser("start")
    start.add_argument("--execute", action="store_true")
    start.add_argument("--run-count", type=int, default=10)
    start.add_argument("--max-concurrent-battles", type=int, default=1)
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
