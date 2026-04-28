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
DEFAULT_RUN_COUNT = 25
DEFAULT_MAX_CONCURRENT = 2


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_json(command: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=20, check=False)
    except Exception as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, result.stderr.strip() or result.stdout.strip() or f"command failed: {command}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from {command}: {exc}"


def shell_command_for_session(run_count: int, max_concurrent: int) -> list[str]:
    return [sys.executable, "run.py", "--run-count", str(run_count), "--max-concurrent-battles", str(max_concurrent)]


def obs_server_command() -> list[str]:
    return [sys.executable, "streaming/serve_obs_page.py"]


def read_active_battles() -> int:
    path = ROOT / "active_battles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    battles = data.get("battles")
    return len(battles) if isinstance(battles, list) else 0


def build_doctor() -> dict[str, Any]:
    health, error = run_json([sys.executable, "scripts/devstream_health.py"])
    checks = []
    if error:
        checks.append({"name": "health_probe", "ok": False, "error": error})
    else:
        checks.append({"name": "health_probe", "ok": bool(health and health.get("healthy")), "details": health})
    schema = ROOT / "devstream" / "truth" / "elo-proof.schema.json"
    example = ROOT / "devstream" / "truth" / "elo-proof.example.json"
    checks.append({"name": "elo_proof_schema", "ok": schema.exists(), "path": str(schema)})
    checks.append({"name": "elo_proof_example", "ok": example.exists(), "path": str(example)})
    env_present = bool(os.environ.get("PS_USERNAME") or os.environ.get("SHOWDOWN_USER_ID"))
    checks.append({"name": "showdown_identity_env", "ok": env_present, "note": "PS_USERNAME or SHOWDOWN_USER_ID must be available at runtime"})
    active_battles = read_active_battles()
    checks.append({"name": "active_battle_drain", "ok": active_battles == 0, "activeBattleCount": active_battles})
    return {
        "schemaVersion": "fouler-play-devstream-doctor/v1",
        "checkedAt": iso_now(),
        "ready": all(check.get("ok") for check in checks),
        "checks": checks,
        "note": "Read-only doctor; it does not queue battles or start services."
    }


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = build_doctor()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if args.require_ready and not payload["ready"] else 0


def cmd_start(args: argparse.Namespace) -> int:
    commands = {
        "obsHttp": obs_server_command(),
        "battleSession": shell_command_for_session(args.run_count, args.max_concurrent_battles),
    }
    payload = {
        "schemaVersion": "fouler-play-devstream-start-plan/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "commands": commands,
        "bounds": {
            "runCount": args.run_count,
            "maxConcurrentBattles": args.max_concurrent_battles,
            "maxRuntimeMinutes": args.max_runtime_minutes,
            "queueTimeoutSeconds": args.queue_timeout_seconds,
            "turnTimeoutSeconds": args.turn_timeout_seconds
        }
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not args.execute:
        return 0
    raise SystemExit("--execute is intentionally not implemented yet; keep first launch behind an explicit reviewed wrapper")


def cmd_stop(args: argparse.Namespace) -> int:
    active_battles = read_active_battles()
    payload = {
        "schemaVersion": "fouler-play-devstream-stop-plan/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "activeBattleCount": active_battles,
        "recommendedAction": "wait-for-drain" if active_battles and not args.force else "safe-to-stop",
        "force": args.force,
        "note": "This command only plans stop behavior until the reviewed wrapper is implemented."
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not args.execute:
        return 0
    raise SystemExit("--execute is intentionally not implemented yet; use reviewed drain-first wrapper before enabling")


def main() -> int:
    parser = argparse.ArgumentParser(description="fouler-play devstream bounded session planner")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--require-ready", action="store_true")
    start = sub.add_parser("start")
    start.add_argument("--run-count", type=int, default=DEFAULT_RUN_COUNT)
    start.add_argument("--max-concurrent-battles", type=int, default=DEFAULT_MAX_CONCURRENT)
    start.add_argument("--max-runtime-minutes", type=int, default=180)
    start.add_argument("--queue-timeout-seconds", type=int, default=180)
    start.add_argument("--turn-timeout-seconds", type=int, default=90)
    start.add_argument("--execute", action="store_true")
    stop = sub.add_parser("stop")
    stop.add_argument("--force", action="store_true")
    stop.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "stop":
        return cmd_stop(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
