#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
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

from infrastructure.runtime_lease import RuntimeLeaseBusy, acquire_runtime_lease
from streaming import state_store

DEFAULT_RUN_COUNT = 1000000
DEFAULT_MAX_CONCURRENT = 3
PID_DIR = ROOT / ".pids"
OBS_PID_FILE = PID_DIR / "devstream_obs_http.pid"
BATTLE_PID_FILE = PID_DIR / "devstream_battle_session.pid"
DRAIN_FILE = PID_DIR / "drain.request"
SUPERVISOR_PID_FILE = PID_DIR / "devstream_battle_supervisor.pid"
SUPERVISOR_STOP_FILE = PID_DIR / "supervisor.stop"
SUPERVISOR_STATUS_FILE = ROOT / "devstream" / "truth" / "supervisor-status.json"
STALE_BATTLE_BACKUP_DIR = ROOT / "devstream" / "truth" / "stale-active-battles-backups"
ENV_FILES = [ROOT / ".env", ROOT / ".env.deku"]
BOT_LOCK_PID_FILE = ROOT / ".bot.pid"
AUTO_IMPROVE_SENTINEL = "FOULER_PLAY_ENABLE_AUTO_IMPROVE"
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def env_flag_enabled(env: dict[str, str], name: str) -> bool:
    return str(env.get(name, "")).strip().lower() in TRUTHY_ENV_VALUES


def supervisor_auto_improve_enabled(args: argparse.Namespace, env: dict[str, str] | None = None) -> tuple[bool, str]:
    if getattr(args, "skip_improve", False):
        return False, "--skip-improve"
    if getattr(args, "enable_auto_improve", False):
        return True, "--enable-auto-improve"
    env = env if env is not None else load_env_files()
    if env_flag_enabled(env, AUTO_IMPROVE_SENTINEL):
        return True, f"{AUTO_IMPROVE_SENTINEL}=1"
    return False, f"missing --enable-auto-improve or {AUTO_IMPROVE_SENTINEL}=1"


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


def strip_env_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
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


def supervisor_command(
    run_count: int,
    max_concurrent: int,
    queue_timeout_seconds: int,
    sleep_seconds: int,
    *,
    enable_auto_improve: bool = False,
) -> list[str]:
    command = [
        runtime_python(),
        "scripts/devstream_session.py",
        "supervise",
        "--run-count",
        str(run_count),
        "--max-concurrent-battles",
        str(max_concurrent),
        "--queue-timeout-seconds",
        str(queue_timeout_seconds),
        "--sleep-seconds",
        str(sleep_seconds),
    ]
    if enable_auto_improve:
        command.append("--enable-auto-improve")
    return command


def showdown_password_required(env: dict[str, str]) -> bool:
    mode = env_value(env, "PS_BOT_MODE", default="search_ladder")
    return mode == "search_ladder"


def obs_server_command() -> list[str]:
    return [runtime_python(), "streaming/serve_obs_page.py"]


def obs_http_ready(timeout_seconds: float = 1.5) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:8777/health", timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def adopted_process_ready(pid_file: Path, command: list[str]) -> bool:
    expected_tokens = _pid_file_expected_tokens(pid_file, {"command": command})
    if "serve_obs_page.py" in expected_tokens:
        return obs_http_ready()
    return True


def is_obs_http_command(command: list[str]) -> bool:
    return "serve_obs_page.py" in _command_expected_tokens(command)


def battle_supervisor_task_command(args: argparse.Namespace) -> list[str]:
    installer = ROOT / "scripts" / "install_battle_supervisor_task.ps1"
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.exists(powershell):
        powershell = "powershell.exe"
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer),
        "-Apply",
        "-Start",
        "-RunCount",
        str(args.run_count),
        "-MaxConcurrentBattles",
        str(args.max_concurrent_battles),
        "-QueueTimeoutSeconds",
        str(args.queue_timeout_seconds),
        "-SleepSeconds",
        str(args.supervisor_sleep_seconds),
    ]
    if getattr(args, "enable_auto_improve", False):
        command.append("-AutoImprove")
    return command


def start_supervisor_runtime(args: argparse.Namespace, command: list[str], env: dict[str, str]) -> dict[str, Any]:
    installer = ROOT / "scripts" / "install_battle_supervisor_task.ps1"
    if os.name != "nt" or not installer.exists():
        return start_process(command, SUPERVISOR_PID_FILE, detached_child_env(env))
    task_command = battle_supervisor_task_command(args)
    started = time.time()
    result = subprocess.run(
        task_command,
        cwd=str(ROOT),
        env=prepare_runtime_env(env),
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    payload: dict[str, Any] = {
        "command": task_command,
        "code": result.returncode,
        "ok": result.returncode == 0,
        "stdoutTail": tail_text(result.stdout),
        "stderrTail": tail_text(result.stderr),
        "durationSeconds": round(time.time() - started, 3),
    }
    try:
        parsed = json.loads(result.stdout)
        if isinstance(parsed, dict):
            payload["taskStatus"] = parsed
    except json.JSONDecodeError:
        pass
    return payload


def supervisor_child_python() -> str:
    return runtime_python()


def read_active_battles() -> int:
    path = ROOT / "active_battles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    battles = data.get("battles")
    return len(battles) if isinstance(battles, list) else 0


def active_battles_path() -> Path:
    return ROOT / "active_battles.json"


def active_battles_age_seconds() -> float | None:
    path = active_battles_path()
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def read_pid(path: Path) -> int | None:
    payload = read_pid_payload(path)
    if isinstance(payload, dict):
        try:
            return int(payload.get("pid") or 0) or None
        except (TypeError, ValueError):
            return None
    try:
        return int(str(payload or "").strip())
    except (TypeError, ValueError):
        return None


def read_pid_payload(path: Path) -> dict[str, Any] | str | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        return raw
    except json.JSONDecodeError:
        return None


def write_pid_value(path: Path, pid: int, command: list[str], **extra: Any) -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "command": command,
                "startedAt": iso_now(),
                **extra,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _parse_started_at(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _command_expected_tokens(command: object) -> list[str]:
    if isinstance(command, str):
        command_parts = command.split()
    elif isinstance(command, list):
        command_parts = [str(item) for item in command]
    else:
        command_parts = []
    tokens = [
        Path(part).name.lower()
        for part in command_parts
        if str(part).lower().endswith((".py", ".bat", ".ps1", ".sh"))
    ]
    for index, part in enumerate(command_parts):
        if Path(part).name.lower() != "devstream_session.py":
            continue
        if index + 1 < len(command_parts):
            subcommand = command_parts[index + 1].strip().lower()
            if subcommand and not subcommand.startswith("-"):
                tokens.append(subcommand)
    return tokens


def _pid_file_expected_tokens(path: Path, payload: dict[str, Any] | str | None) -> list[str]:
    if isinstance(payload, dict):
        tokens = _command_expected_tokens(payload.get("command") or [])
        if tokens:
            return tokens
    if path == BOT_LOCK_PID_FILE or path == BATTLE_PID_FILE:
        return ["run.py"]
    if path == OBS_PID_FILE:
        return ["serve_obs_page.py"]
    return []


def _process_snapshot(pid: int) -> dict[str, Any] | None:
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        status = proc.status() if hasattr(proc, "status") else ""
        if status == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
            return None
        return {
            "running": bool(proc.is_running()),
            "cmdline": [str(item) for item in proc.cmdline()],
            "cwd": proc.cwd(),
            "createTime": float(proc.create_time()),
        }
    except Exception:
        return None


def _find_existing_process(command: list[str]) -> int | None:
    expected_tokens = _command_expected_tokens(command)
    if not expected_tokens:
        return None
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid", "cmdline", "cwd", "status"]):
            try:
                if int(proc.info.get("pid") or 0) == os.getpid():
                    continue
                if proc.info.get("status") == getattr(psutil, "STATUS_ZOMBIE", "zombie"):
                    continue
                cwd = proc.info.get("cwd")
                if not cwd or os.path.abspath(str(cwd)) != os.path.abspath(ROOT):
                    continue
                command_line = " ".join(str(item) for item in (proc.info.get("cmdline") or [])).lower()
                if all(token in command_line for token in expected_tokens):
                    return int(proc.info["pid"])
            except Exception:
                continue
    except Exception:
        return None
    return None


def _pid_matches_expected_process(path: Path, pid: int, payload: dict[str, Any] | str | None) -> bool:
    snapshot = _process_snapshot(pid)
    if not snapshot or not snapshot.get("running"):
        return False
    expected_tokens = _pid_file_expected_tokens(path, payload)
    command = " ".join(snapshot.get("cmdline") or []).lower()
    if expected_tokens and not any(token in command for token in expected_tokens):
        return False
    if expected_tokens:
        cwd = snapshot.get("cwd")
        if cwd and os.path.abspath(str(cwd)) != os.path.abspath(ROOT):
            return False
    if isinstance(payload, dict):
        started_at = _parse_started_at(payload.get("startedAt") or payload.get("started_at"))
        create_time = snapshot.get("createTime")
        if started_at is not None and create_time is not None and float(create_time) < started_at - 2:
            return False
    return True


def pid_alive(path: Path) -> tuple[bool, int | None]:
    payload = read_pid_payload(path)
    pid = read_pid(path)
    if pid is None or pid <= 0:
        return False, pid
    try:
        os.kill(pid, 0)
        return _pid_matches_expected_process(path, pid, payload), pid
    except PermissionError:
        return _pid_matches_expected_process(path, pid, payload), pid
    except OSError:
        # Windows can raise for signal 0 even when psutil can still inspect a
        # valid process. Prefer the richer process snapshot before treating the
        # pid file as stale, otherwise legacy bare PID files can spawn duplicates.
        return _pid_matches_expected_process(path, pid, payload), pid


def write_pid(path: Path, proc: subprocess.Popen[Any], command: list[str]) -> None:
    write_pid_value(path, proc.pid, command)


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
    existing_pid = _find_existing_process(command)
    stale_existing: dict[str, Any] | None = None
    if existing_pid is not None and adopted_process_ready(pid_file, command):
        write_pid_value(
            pid_file,
            existing_pid,
            command,
            adoptedExistingProcess=True,
            previousPid=pid,
        )
        return {
            "pidFile": str(pid_file),
            "alreadyRunning": True,
            "pid": existing_pid,
            "command": command,
            "adoptedExistingProcess": True,
            "previousPid": pid,
        }
    elif existing_pid is not None:
        stale_existing = {
            "pid": existing_pid,
            "reason": "matching process existed but readiness probe failed",
        }
        if is_obs_http_command(command):
            stale_existing["termination"] = terminate_process_pid(
                existing_pid,
                force=True,
                reason="OBS HTTP process matched command but /health was unavailable before restart",
            )
        try:
            pid_file.unlink()
        except OSError:
            pass
    PID_DIR.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / "logs" / f"{pid_file.stem}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    proc = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    write_pid(pid_file, proc, command)
    payload = {"pidFile": str(pid_file), "pid": proc.pid, "log": str(log_path), "command": command}
    if stale_existing is not None:
        payload["staleExistingProcess"] = stale_existing
    return payload


def process_age_seconds(pid: int) -> float | None:
    try:
        import psutil  # type: ignore

        return max(0.0, time.time() - psutil.Process(pid).create_time())
    except Exception:
        return None


def battle_pid_files() -> list[Path]:
    return [BOT_LOCK_PID_FILE, BATTLE_PID_FILE]


def supervisor_alive() -> tuple[bool, int | None]:
    return pid_alive(SUPERVISOR_PID_FILE)


def existing_battle_runner_start_result(command: list[str]) -> dict[str, Any] | None:
    runners = []
    for pid_file in battle_pid_files():
        alive, pid = pid_alive(pid_file)
        if alive and pid is not None:
            runners.append({
                "pidFile": str(pid_file),
                "pid": pid,
            })
    if not runners:
        return None
    adopted = None
    if runners[0]["pidFile"] != str(BATTLE_PID_FILE):
        write_pid_value(
            BATTLE_PID_FILE,
            int(runners[0]["pid"]),
            command,
            adoptedExistingProcess=True,
            adoptedFrom=runners[0]["pidFile"],
        )
        adopted = {
            "pidFile": str(BATTLE_PID_FILE),
            "pid": runners[0]["pid"],
            "adoptedFrom": runners[0]["pidFile"],
        }
    return {
        "alreadyRunning": True,
        "pid": runners[0]["pid"],
        "pidFile": runners[0]["pidFile"],
        "knownRunners": runners,
        "adoptedPidFile": adopted,
        "command": command,
        "reason": "existing battle runner is alive; not spawning duplicate runner",
    }


def any_battle_runner_alive() -> bool:
    return any(pid_alive(path)[0] for path in battle_pid_files())


def clear_stale_active_battles(
    *,
    execute: bool,
    stale_after_seconds: int,
    force: bool = False,
    clear_reason: str = "stale active battle truth had no live battle runner",
) -> dict[str, Any]:
    active_count = read_active_battles()
    age = active_battles_age_seconds()
    runner_alive = any_battle_runner_alive()
    path = active_battles_path()
    payload: dict[str, Any] = {
        "activeBattleCount": active_count,
        "ageSeconds": round(age, 3) if age is not None else None,
        "battleRunnerAlive": runner_alive,
        "execute": execute,
        "staleAfterSeconds": stale_after_seconds,
        "force": force,
        "cleared": False,
    }
    if active_count <= 0:
        payload["reason"] = "no active battle truth to clear"
        return payload
    if runner_alive and not force:
        payload["reason"] = "battle runner is alive; preserving active battle truth"
        return payload
    if not force and (age is None or age < stale_after_seconds):
        payload["reason"] = "active battle truth is not stale enough to clear"
        return payload
    if not execute:
        payload["reason"] = "dry run; stale active battle truth cleanup planned only"
        return payload

    STALE_BATTLE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = STALE_BATTLE_BACKUP_DIR / f"active_battles-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    if path.exists():
        shutil.copy2(path, backup)
        payload["backupPath"] = str(backup)
    replacement = {
        "battles": [],
        "count": 0,
        "max_slots": DEFAULT_MAX_CONCURRENT,
        "updated": iso_now(),
        "clearedBy": "HERMES devstream_session stop" if force else "HERMES devstream_session start",
        "clearReason": clear_reason,
        "previousBattleCount": active_count,
    }
    try:
        path.write_text(json.dumps(replacement, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except PermissionError:
        path.chmod(0o666)
        path.write_text(json.dumps(replacement, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        payload["permissionRepair"] = "chmod 666 before rewrite"
    payload["cleared"] = True
    payload["reason"] = "active battle truth cleared after forced stop" if force else "stale active battle truth cleared before bounded session start"
    return payload


def recover_stale_battle_runtime(*, execute: bool, stale_after_seconds: int) -> dict[str, Any]:
    """Replace only idle, stale battle runners through the HERMES start path.

    A live battle is never interrupted. This only clears singleton runners that
    have produced no active battle proof beyond the queue timeout, which is the
    same condition reported as a runtime blocker by devstream_health.py.
    """
    active_battles = read_active_battles()
    payload: dict[str, Any] = {
        "activeBattleCount": active_battles,
        "execute": execute,
        "staleAfterSeconds": stale_after_seconds,
        "candidates": [],
        "actions": [],
        "recovered": False,
    }
    if active_battles > 0:
        payload["reason"] = "active battles are present; not replacing runner"
        return payload

    seen: set[int] = set()
    for pid_file in battle_pid_files():
        alive, pid = pid_alive(pid_file)
        item: dict[str, Any] = {
            "pidFile": str(pid_file),
            "pid": pid,
            "alive": alive,
        }
        if pid and pid not in seen:
            seen.add(pid)
            age = process_age_seconds(pid)
            if age is None and pid_file.exists():
                age = max(0.0, time.time() - pid_file.stat().st_mtime)
            item["ageSeconds"] = round(age, 3) if age is not None else None
            item["stale"] = bool(alive and age is not None and age >= stale_after_seconds)
        else:
            item["stale"] = False
        payload["candidates"].append(item)

    stale_alive = [item for item in payload["candidates"] if item.get("alive") and item.get("stale")]
    live_young = [item for item in payload["candidates"] if item.get("alive") and not item.get("stale")]
    if live_young:
        payload["reason"] = "battle runner is alive but not stale enough to replace"
        return payload
    if not stale_alive:
        payload["reason"] = "no stale live battle runner found"
        return payload
    if not execute:
        payload["reason"] = "dry run; stale runner replacement planned only"
        return payload

    PID_DIR.mkdir(parents=True, exist_ok=True)
    DRAIN_FILE.write_text(iso_now() + "\n", encoding="utf-8")
    for pid_file in battle_pid_files():
        action = terminate_pid_file(pid_file, force=True)
        if read_pid(pid_file) is not None:
            alive, _ = pid_alive(pid_file)
            if not alive:
                try:
                    pid_file.unlink()
                    action["pidFileRemoved"] = True
                except OSError as exc:
                    action["pidFileRemoveError"] = str(exc)
        payload["actions"].append(action)
    payload["recovered"] = True
    payload["reason"] = "stale idle battle runner replaced before bounded session start"
    return payload


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
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            item["forced"] = True
        except OSError as exc:
            item["forceError"] = str(exc)
    return item


def terminate_process_pid(pid: int, *, force: bool = False, reason: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {"pid": pid, "wasRunning": False}
    if reason:
        item["reason"] = reason
    if pid <= 0:
        return item
    try:
        os.kill(pid, 0)
        item["wasRunning"] = True
    except OSError:
        return item
    try:
        os.kill(pid, signal.SIGTERM)
        item["sent"] = "SIGTERM"
    except OSError as exc:
        item["error"] = str(exc)
        return item
    for _ in range(15):
        try:
            os.kill(pid, 0)
        except OSError:
            item["stopped"] = True
            return item
        time.sleep(0.2)
    if force:
        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            item["forced"] = True
        except OSError as exc:
            item["forceError"] = str(exc)
    return item


def terminate_battle_runners(*, force: bool = False) -> dict[str, Any]:
    return {
        path.name: terminate_pid_file(path, force=force)
        for path in battle_pid_files()
    }


def tail_text(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def run_supervisor_command(command: list[str], *, timeout: int) -> dict[str, Any]:
    started = time.time()
    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            env=prepare_runtime_env(load_env_files()),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "returnCode": result.returncode,
            "stdoutTail": tail_text(result.stdout),
            "stderrTail": tail_text(result.stderr),
            "durationSeconds": round(time.time() - started, 3),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "returnCode": None,
            "timedOut": True,
            "stdoutTail": tail_text(exc.stdout or ""),
            "stderrTail": tail_text(exc.stderr or ""),
            "durationSeconds": round(time.time() - started, 3),
        }
    except Exception as exc:
        return {
            "command": command,
            "returnCode": None,
            "error": f"{type(exc).__name__}: {exc}",
            "durationSeconds": round(time.time() - started, 3),
        }


def write_supervisor_status(payload: dict[str, Any]) -> None:
    SUPERVISOR_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUPERVISOR_STATUS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_supervisor_cycle(args: argparse.Namespace, cycle_index: int) -> dict[str, Any]:
    active_count = read_active_battles()
    battle_runner_alive = any_battle_runner_alive()
    payload: dict[str, Any] = {
        "schemaVersion": "fouler-play-battle-supervisor-cycle/v1",
        "checkedAt": iso_now(),
        "cycleIndex": cycle_index,
        "activeBattleCount": active_count,
        "battleRunnerAlive": battle_runner_alive,
        "actions": [],
    }
    if active_count > 0 and not battle_runner_alive:
        stale_clear = clear_stale_active_battles(
            execute=True,
            stale_after_seconds=max(int(args.queue_timeout_seconds), 60),
            clear_reason="stale active battle truth blocked three-slot supervisor restart",
        )
        payload["staleActiveBattleClear"] = stale_clear
        if stale_clear.get("cleared"):
            active_count = read_active_battles()
            battle_runner_alive = any_battle_runner_alive()
            payload["activeBattleCountAfterClear"] = active_count
            payload["battleRunnerAliveAfterClear"] = battle_runner_alive

    if active_count > 0 or battle_runner_alive:
        payload["state"] = "battle-cycle-in-flight"
        payload["nextAction"] = "wait for active battle runner/drain before proof refresh"
        return payload

    payload["state"] = "idle-restoring-runtime"
    py = supervisor_child_python()
    payload["actions"].append(
        run_supervisor_command(
            [py, "pipeline.py", "autoresearch", "-n", str(getattr(args, "autoresearch_count", 30)), "--no-discord"],
            timeout=getattr(args, "proof_timeout_seconds", 300),
        )
    )
    payload["actions"].append(
        run_supervisor_command(
            [py, "scripts/devstream_cycle_report.py", "--write"],
            timeout=getattr(args, "proof_timeout_seconds", 300),
        )
    )

    # --- Self-improvement loop (explicit opt-in only) ------------------------
    # Recursive improvement is disabled by default while the architecture is
    # corrected. It may only run when the supervisor CLI flag or env sentinel is
    # present; --skip-improve remains an explicit override.
    improve_enabled, improve_reason = supervisor_auto_improve_enabled(args)
    payload["autoImprove"] = {
        "enabled": improve_enabled,
        "reason": improve_reason,
        "sentinel": AUTO_IMPROVE_SENTINEL,
    }
    if improve_enabled:
        improve_command = [py, "infrastructure/improve_agent.py", "--enable-auto-improve"]
        payload["actions"].append(
            run_supervisor_command(
                improve_command,
                timeout=getattr(args, "improve_timeout_seconds", 240),
            )
        )
        payload["actions"].append(
            run_supervisor_command(
                [py, "infrastructure/elo_watchdog.py"],
                timeout=getattr(args, "proof_timeout_seconds", 300),
            )
        )
    payload["actions"].append(
        run_supervisor_command(
            [
                py,
                "scripts/devstream_session.py",
                "start",
                "--run-count",
                str(args.run_count),
                "--max-concurrent-battles",
                str(args.max_concurrent_battles),
                "--queue-timeout-seconds",
                str(args.queue_timeout_seconds),
                "--execute",
            ],
            timeout=getattr(args, "start_timeout_seconds", 60),
        )
    )
    payload["battleRunnerAliveAfter"] = any_battle_runner_alive()
    payload["activeBattleCountAfter"] = read_active_battles()
    payload["nextAction"] = "monitor bounded battle cycle, then refresh proof and restart if idle"
    return payload


def cmd_supervise(args: argparse.Namespace) -> int:
    if SUPERVISOR_STOP_FILE.exists():
        SUPERVISOR_STOP_FILE.unlink()
    try:
        acquire_runtime_lease(holder="devstream_session supervise")
    except RuntimeLeaseBusy as exc:
        payload = {
            "schemaVersion": "fouler-play-battle-supervisor/v1",
            "startedAt": iso_now(),
            "pid": os.getpid(),
            "state": "blocked-runtime-lease",
            "runtimeLease": str(exc),
            "secretValuesPrinted": False,
        }
        write_supervisor_status(payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 3
    cycle_index = 0
    payload: dict[str, Any] = {
        "schemaVersion": "fouler-play-battle-supervisor/v1",
        "startedAt": iso_now(),
        "pid": os.getpid(),
        "state": "starting",
        "cycles": [],
        "bounds": {
            "runCount": args.run_count,
            "maxConcurrentBattles": args.max_concurrent_battles,
            "queueTimeoutSeconds": args.queue_timeout_seconds,
            "sleepSeconds": args.sleep_seconds,
            "maxCycles": args.max_cycles,
        },
        "stopFile": str(SUPERVISOR_STOP_FILE),
        "secretValuesPrinted": False,
    }
    write_pid_value(
        SUPERVISOR_PID_FILE,
        os.getpid(),
        supervisor_command(
            args.run_count,
            args.max_concurrent_battles,
            args.queue_timeout_seconds,
            args.sleep_seconds,
            enable_auto_improve=getattr(args, "enable_auto_improve", False),
        ),
    )
    try:
        while True:
            if SUPERVISOR_STOP_FILE.exists():
                payload["state"] = "stopping"
                payload["stopReason"] = "stop file present"
                write_supervisor_status(payload)
                return 0
            cycle_index += 1
            cycle = run_supervisor_cycle(args, cycle_index)
            payload["state"] = cycle.get("state", "unknown")
            payload["lastHeartbeatAt"] = iso_now()
            payload["lastCycle"] = cycle
            payload["cycles"] = (payload.get("cycles") or [])[-9:] + [cycle]
            write_supervisor_status(payload)
            if args.max_cycles and cycle_index >= args.max_cycles:
                payload["state"] = "completed-max-cycles"
                payload["completedAt"] = iso_now()
                write_supervisor_status(payload)
                return 0
            time.sleep(args.sleep_seconds)
    except KeyboardInterrupt:
        payload["state"] = "interrupted"
        payload["interruptedAt"] = iso_now()
        write_supervisor_status(payload)
        return 130
    finally:
        pid = None
        try:
            raw = SUPERVISOR_PID_FILE.read_text(encoding="utf-8").strip()
            parsed = json.loads(raw) if raw.startswith("{") else {"pid": int(raw)}
            pid = int(parsed.get("pid"))
        except Exception:
            pass
        if pid == os.getpid():
            try:
                SUPERVISOR_PID_FILE.unlink()
            except OSError:
                pass


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
        readiness = health.get("readiness") if isinstance(health, dict) and isinstance(health.get("readiness"), dict) else {}
        proof_handoff_ready = bool(readiness.get("proofHandoffReady"))
        checks.append({
            "name": "health_probe",
            "ok": bool(health and (health.get("healthy") or proof_handoff_ready)),
            "details": health,
            **(
                {
                    "acceptedMode": "proof-handoff",
                    "runtimeRestoration": "runtime is not live-ready; start only through HERMES after readiness gate allows project starts",
                }
                if health and not health.get("healthy") and proof_handoff_ready
                else {}
            ),
        })
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
    supervisor_running, supervisor_pid = supervisor_alive()
    checks.append({
        "name": "battle_supervisor",
        "ok": supervisor_running,
        "pid": supervisor_pid,
        "statusPath": str(SUPERVISOR_STATUS_FILE),
        "note": "HERMES persistent battle supervisor must be alive for unattended devstream operation.",
    })
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
        "battleSupervisor": supervisor_command(
            args.run_count,
            args.max_concurrent_battles,
            args.queue_timeout_seconds,
            args.supervisor_sleep_seconds,
            enable_auto_improve=getattr(args, "enable_auto_improve", False),
        ),
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
    if args.continuous:
        env["FP_PARENT_PID"] = "0"
        env["LOSS_TRIGGERED_DRAIN"] = "0"
        env["BATTLE_STATS_MAX_ENTRIES"] = env_value(env, "BATTLE_STATS_MAX_ENTRIES", default="5000")
        try:
            SUPERVISOR_STOP_FILE.unlink()
        except OSError:
            pass
        payload["started"] = {
            "obsHttp": start_process(commands["obsHttp"], OBS_PID_FILE, obs_http_env(env)),
            "battleSupervisor": start_supervisor_runtime(args, commands["battleSupervisor"], env),
            "battleSession": {
                "skipped": True,
                "reason": "persistent supervisor owns bounded battle session starts",
            },
        }
        time.sleep(2)
        health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
        payload["postHealth"] = health if health is not None else {"error": error}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    env["LOSS_TRIGGERED_DRAIN"] = "0"
    env["BATTLE_STATS_MAX_ENTRIES"] = env_value(env, "BATTLE_STATS_MAX_ENTRIES", default="5000")
    env["FP_PARENT_PID"] = "0"
    payload["staleActiveBattleCleanup"] = clear_stale_active_battles(
        execute=True,
        stale_after_seconds=args.queue_timeout_seconds,
    )
    payload["staleBattleRuntimeRecovery"] = recover_stale_battle_runtime(
        execute=args.replace_stale_runner,
        stale_after_seconds=args.queue_timeout_seconds,
    )
    existing_battle_runner = existing_battle_runner_start_result(commands["battleSession"])
    payload["started"] = {
        "obsHttp": start_process(commands["obsHttp"], OBS_PID_FILE, obs_http_env(env)),
        "battleSession": existing_battle_runner
        or start_process(commands["battleSession"], BATTLE_PID_FILE, detached_child_env(env)),
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
    SUPERVISOR_STOP_FILE.write_text(iso_now() + "\n", encoding="utf-8")
    payload["drain"] = wait_for_drain(args.max_wait_seconds)
    if payload["drain"].get("drained") or args.force:
        payload["terminated"] = {
            "battleSupervisor": terminate_pid_file(SUPERVISOR_PID_FILE, force=args.force),
            "battleSession": terminate_battle_runners(force=args.force),
            "obsHttp": terminate_pid_file(OBS_PID_FILE, force=args.force),
        }
        if args.force:
            payload["forcedActiveBattleCleanup"] = clear_stale_active_battles(
                execute=True,
                stale_after_seconds=0,
                force=True,
                clear_reason="forced devstream stop terminated the battle runner; stale active battle truth must not stay public",
            )
    else:
        payload["error"] = "active battles did not drain before timeout"
    health, error = run_json([runtime_python(), "scripts/devstream_health.py"])
    payload["postHealth"] = health if health is not None else {"error": error}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if "error" not in payload else 1


def cmd_drain(args: argparse.Namespace) -> int:
    active_battles = read_active_battles()
    runner_alive = any_battle_runner_alive()
    payload = {
        "schemaVersion": "fouler-play-devstream-drain-plan/v1",
        "checkedAt": iso_now(),
        "dryRun": not args.execute,
        "activeBattleCount": active_battles,
        "battleRunnerAlive": runner_alive,
        "drainFile": str(DRAIN_FILE),
        "reason": args.reason,
        "note": "Drain-only control: finish current battle, queue no new battle from the old process, and let the supervisor restart from current code.",
    }
    if args.execute:
        PID_DIR.mkdir(parents=True, exist_ok=True)
        DRAIN_FILE.write_text(f"{iso_now()} {args.reason}\n", encoding="utf-8")
        payload["written"] = True
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


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
    start.add_argument("--continuous", action="store_true")
    start.add_argument("--supervisor-sleep-seconds", type=int, default=15)
    start.add_argument("--replace-stale-runner", action=argparse.BooleanOptionalAction, default=True)
    start.add_argument(
        "--enable-auto-improve",
        action="store_true",
        help=f"Allow the recursive improve_agent + elo_watchdog path. Alternative: {AUTO_IMPROVE_SENTINEL}=1.",
    )
    start.add_argument("--execute", action="store_true")
    supervise = sub.add_parser("supervise")
    supervise.add_argument("--run-count", type=int, default=DEFAULT_RUN_COUNT)
    supervise.add_argument("--max-concurrent-battles", type=int, default=DEFAULT_MAX_CONCURRENT)
    supervise.add_argument("--queue-timeout-seconds", type=int, default=180)
    supervise.add_argument("--sleep-seconds", type=int, default=15)
    supervise.add_argument("--max-cycles", type=int, default=0)
    supervise.add_argument("--autoresearch-count", type=int, default=30)
    supervise.add_argument("--proof-timeout-seconds", type=int, default=300)
    supervise.add_argument("--start-timeout-seconds", type=int, default=60)
    supervise.add_argument("--improve-timeout-seconds", type=int, default=240)
    supervise.add_argument(
        "--enable-auto-improve",
        action="store_true",
        help=f"Allow the recursive improve_agent + elo_watchdog path. Alternative: {AUTO_IMPROVE_SENTINEL}=1.",
    )
    supervise.add_argument("--skip-improve", action="store_true",
                           help="Disable the self-improvement loop (improve_agent + elo_watchdog).")
    stop = sub.add_parser("stop")
    stop.add_argument("--force", action="store_true")
    stop.add_argument("--max-wait-seconds", type=int, default=1800)
    stop.add_argument("--execute", action="store_true")
    drain = sub.add_parser("drain")
    drain.add_argument("--execute", action="store_true")
    drain.add_argument("--reason", default="HERMES requested graceful code-refresh drain")
    args = parser.parse_args()
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "supervise":
        return cmd_supervise(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "drain":
        return cmd_drain(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
