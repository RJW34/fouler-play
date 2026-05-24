#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devstream_runtime_checks import recent_showdown_credential_failure

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming import state_store

DEFAULT_RUN_COUNT = 25
DEFAULT_MAX_CONCURRENT = 3
PID_DIR = ROOT / ".pids"
OBS_PID_FILE = PID_DIR / "devstream_obs_http.pid"
BATTLE_PID_FILE = PID_DIR / "devstream_battle_session.pid"
DRAIN_FILE = PID_DIR / "drain.request"
ENV_FILES = [ROOT / ".env", ROOT / ".env.deku"]


def runtime_python() -> str:
    """Prefer the repo-local virtualenv expected by the devstream contract."""
    if os.name == "nt":
        candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def prepare_runtime_env(env: dict[str, str]) -> dict[str, str]:
    if os.name == "nt":
        bin_dir = ROOT / ".venv" / "Scripts"
    else:
        bin_dir = ROOT / ".venv" / "bin"
    if bin_dir.exists():
        env = dict(env)
        env.setdefault("VIRTUAL_ENV", str(ROOT / ".venv"))
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


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
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    return env


def env_value(env: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return default


def shell_command_for_session(run_count: int, max_concurrent: int, env: dict[str, str] | None = None) -> list[str]:
    env = env or load_env_files()
    username = env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID")
    command = [
        runtime_python(),
        "run.py",
        "--websocket-uri",
        env_value(env, "PS_WEBSOCKET_URI", default="wss://sim3.psim.us/showdown/websocket"),
        "--ps-username",
        username,
        "--bot-mode",
        env_value(env, "PS_BOT_MODE", default="search_ladder"),
        "--pokemon-format",
        env_value(env, "PS_FORMAT", "POKEMON_FORMAT", default="gen9ou"),
        "--run-count",
        str(run_count),
        "--max-concurrent-battles",
        str(max_concurrent),
        "--save-replay",
        env_value(env, "SAVE_REPLAY", default="always"),
        "--log-to-file",
    ]
    avatar = env_value(env, "PS_AVATAR")
    if avatar:
        command.extend(["--ps-avatar", avatar])
    team_names = env_value(env, "TEAM_NAMES")
    team_list = env_value(env, "TEAM_LIST")
    team_name = env_value(env, "TEAM_NAME")
    if team_names:
        command.extend(["--team-names", team_names])
    elif team_list:
        command.extend(["--team-list", team_list])
    elif team_name:
        command.extend(["--team-name", team_name])
    spectator = env_value(env, "SPECTATOR_USERNAME")
    if spectator:
        command.extend(["--spectator-username", spectator])
    return command


def showdown_password_required(env: dict[str, str]) -> bool:
    mode = env_value(env, "PS_BOT_MODE", default="search_ladder")
    return mode == "search_ladder"


def obs_server_command() -> list[str]:
    return [runtime_python(), "streaming/serve_obs_page.py"]


def read_active_battles() -> int:
    path = ROOT / "active_battles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    battles = data.get("battles")
    return len(battles) if isinstance(battles, list) else 0


def pid_alive(path: Path) -> tuple[bool, int | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = int(data.get("pid") or 0)
    except Exception:
        return False, None
    if pid <= 0:
        return False, pid
    try:
        os.kill(pid, 0)
        return True, pid
    except OSError:
        return False, pid


def write_pid(path: Path, proc: subprocess.Popen[Any], command: list[str]) -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": proc.pid,
                "command": command,
                "startedAt": iso_now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def secure_env_files(*, execute: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in ENV_FILES:
        if not path.exists():
            continue
        mode = path.stat().st_mode & 0o777
        item: dict[str, Any] = {"path": str(path), "mode": oct(mode), "changed": False}
        if execute and os.name != "nt" and mode != 0o600:
            path.chmod(0o600)
            item["changed"] = True
            mode = path.stat().st_mode & 0o777
            item["mode"] = oct(mode)
        item["ok"] = os.name == "nt" or mode == 0o600
        items.append(item)
    return items


def start_process(command: list[str], pid_file: Path, env: dict[str, str]) -> dict[str, Any]:
    alive, pid = pid_alive(pid_file)
    if alive:
        return {"pidFile": str(pid_file), "alreadyRunning": True, "pid": pid, "command": command}
    PID_DIR.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "logs" / f"{pid_file.stem}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(command, cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
    write_pid(pid_file, proc, command)
    return {"pidFile": str(pid_file), "pid": proc.pid, "log": str(log_path), "command": command}


def obs_http_env(env: dict[str, str]) -> dict[str, str]:
    obs_env = dict(env)
    # The devstream start command exits after spawning child processes; the
    # OBS HTTP surface is intentionally stopped later via its PID file.
    obs_env["FP_PARENT_PID"] = "0"
    return obs_env


def detached_child_env(env: dict[str, str]) -> dict[str, str]:
    child_env = dict(env)
    # The bounded devstream launcher is a one-shot supervisor that records PID
    # files and exits. Runtime children must survive that exit so OBS can keep
    # showing active battles during unattended rehearsal/live-build audits.
    child_env["FP_PARENT_PID"] = "0"
    return child_env


def terminate_pid_file(path: Path, *, force: bool = False) -> dict[str, Any]:
    alive, pid = pid_alive(path)
    item: dict[str, Any] = {"pidFile": str(path), "pid": pid, "wasRunning": alive}
    if not alive or pid is None:
        return item
    try:
        os.kill(pid, signal.SIGTERM)
        item["sent"] = "SIGTERM"
    except OSError as exc:
        item["error"] = str(exc)
        return item
    for _ in range(30):
        still_alive, _ = pid_alive(path)
        if not still_alive:
            break
        time.sleep(1)
    still_alive, _ = pid_alive(path)
    if force and still_alive:
        try:
            os.kill(pid, signal.SIGKILL)
            item["forced"] = True
        except OSError as exc:
            item["forceError"] = str(exc)
    return item


def wait_for_drain(max_wait_seconds: int) -> dict[str, Any]:
    started = time.time()
    while time.time() - started < max_wait_seconds:
        count = read_active_battles()
        if count <= 0:
            return {"drained": True, "activeBattleCount": 0, "waitedSeconds": round(time.time() - started, 3)}
        time.sleep(5)
    return {
        "drained": False,
        "activeBattleCount": read_active_battles(),
        "waitedSeconds": round(time.time() - started, 3),
    }


def build_doctor() -> dict[str, Any]:
    env = prepare_runtime_env(load_env_files())
    health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
    checks = []
    if error:
        checks.append({"name": "health_probe", "ok": False, "error": error})
    else:
        checks.append({"name": "health_probe", "ok": bool(health and health.get("healthy")), "details": health})
    schema = ROOT / "devstream" / "truth" / "elo-proof.schema.json"
    example = ROOT / "devstream" / "truth" / "elo-proof.example.json"
    login_check = ROOT / "scripts" / "showdown_login_check.py"
    checks.append({"name": "elo_proof_schema", "ok": schema.exists(), "path": str(schema)})
    checks.append({"name": "elo_proof_example", "ok": example.exists(), "path": str(example)})
    checks.append({"name": "showdown_login_check_tool", "ok": login_check.exists(), "path": str(login_check)})
    env_present = bool(env.get("PS_USERNAME") or env.get("SHOWDOWN_USER_ID"))
    checks.append({"name": "showdown_identity_env", "ok": env_present, "note": "PS_USERNAME or SHOWDOWN_USER_ID must be available at runtime"})
    password_present = bool(env.get("PS_PASSWORD"))
    password_required = showdown_password_required(env)
    checks.append({
        "name": "showdown_password_env",
        "ok": password_present or not password_required,
        "required": password_required,
        "note": "PS_PASSWORD is required for registered Showdown ladder sessions; the value is never emitted.",
    })
    credential_failure = recent_showdown_credential_failure(ROOT)
    checks.append({
        "name": "showdown_recent_credential_failure",
        "ok": not credential_failure.get("found"),
        "details": credential_failure,
    })
    env_modes = secure_env_files(execute=False)
    checks.append({"name": "env_file_permissions", "ok": all(item.get("ok") for item in env_modes), "details": env_modes})
    checks.append({
        "name": "run_command_materializes",
        "ok": env_present,
        "command": shell_command_for_session(DEFAULT_RUN_COUNT, DEFAULT_MAX_CONCURRENT, env) if env_present else [],
    })
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
    env = prepare_runtime_env(load_env_files())
    commands = {
        "obsHttp": obs_server_command(),
        "battleSession": shell_command_for_session(args.run_count, args.max_concurrent_battles, env),
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
        },
        "envFilePermissions": secure_env_files(execute=args.execute),
    }
    if not args.execute:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if not env_value(env, "PS_USERNAME", "SHOWDOWN_USER_ID"):
        payload["error"] = "PS_USERNAME or SHOWDOWN_USER_ID is required"
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    if showdown_password_required(env) and not env_value(env, "PS_PASSWORD"):
        payload["error"] = "PS_PASSWORD is required for registered Showdown ladder sessions"
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    credential_failure = recent_showdown_credential_failure(ROOT)
    if credential_failure.get("found"):
        payload["credentialFailure"] = credential_failure
        payload["blockedTruth"] = state_store.write_runtime_blocked_status(
            code=str(credential_failure.get("code") or "showdown_credential_blocked"),
            summary=str(
                credential_failure.get("summary")
                or "Showdown login failed; credential was rejected."
            ),
        )
        env["FP_PARENT_PID"] = "0"
        payload["started"] = {
            "obsHttp": start_process(commands["obsHttp"], OBS_PID_FILE, obs_http_env(env)),
            "battleSession": {
                "skipped": True,
                "reason": "recent Showdown credential failure is unresolved",
            },
        }
        time.sleep(1)
        health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
        payload["postHealth"] = health if health is not None else {"error": error}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    env.setdefault("LOSS_TRIGGERED_DRAIN", "1")
    env.setdefault("BATTLE_STATS_MAX_ENTRIES", "5000")
    env["FP_PARENT_PID"] = "0"
    payload["started"] = {
        "obsHttp": start_process(commands["obsHttp"], OBS_PID_FILE, obs_http_env(env)),
        "battleSession": start_process(commands["battleSession"], BATTLE_PID_FILE, detached_child_env(env)),
    }
    time.sleep(2)
    health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
    payload["postHealth"] = health if health is not None else {"error": error}
    credential_failure = (
        (health or {})
        .get("credentials", {})
        .get("recentShowdownFailure", {})
        if isinstance(health, dict)
        else {}
    )
    if isinstance(credential_failure, dict) and credential_failure.get("found"):
        payload["blockedTruth"] = state_store.write_runtime_blocked_status(
            code=str(credential_failure.get("code") or "showdown_credential_blocked"),
            summary=str(
                credential_failure.get("summary")
                or "Showdown login failed; credential was rejected."
            ),
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    active_battles = read_active_battles()
    payload = {
        "schemaVersion": "fouler-play-devstream-stop-plan/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "activeBattleCount": active_battles,
        "recommendedAction": "wait-for-drain" if active_battles and not args.force else "safe-to-stop",
        "force": args.force,
        "note": "Drain-first stop. Without --force, active battles are allowed to finish before processes are terminated."
    }
    if not args.execute:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    PID_DIR.mkdir(parents=True, exist_ok=True)
    DRAIN_FILE.write_text(iso_now() + "\n", encoding="utf-8")
    payload["drain"] = wait_for_drain(args.max_wait_seconds)
    if payload["drain"].get("drained") or args.force:
        payload["terminated"] = {
            "battleSession": terminate_pid_file(BATTLE_PID_FILE, force=args.force),
            "obsHttp": terminate_pid_file(OBS_PID_FILE, force=args.force),
        }
    else:
        payload["error"] = "active battles did not drain before timeout"
    health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
    payload["postHealth"] = health if health is not None else {"error": error}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if "error" not in payload else 1


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
    stop.add_argument("--max-wait-seconds", type=int, default=1800)
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
