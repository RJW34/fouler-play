#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import runpy
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRUTH_DIR = ROOT / "devstream" / "truth"
LEASE_PATH = TRUTH_DIR / "runtime-lease.json"
LATEST_PATH = TRUTH_DIR / "obs-server-launch.json"
EVENTS_PATH = TRUTH_DIR / "obs-server-launch.jsonl"


def _record(
    phase: str,
    *,
    exit_code: int | None = None,
    error: str | None = None,
    publish_latest: bool = True,
) -> None:
    payload = {
        "schemaVersion": "fouler-obs-launch/v1",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "processId": os.getpid(),
        "phase": phase,
        "foreground": True,
        "lifecycleOwner": "windows-service",
        "exitCode": exit_code,
    }
    if error:
        payload["error"] = error[:1000]
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    with EVENTS_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    if not publish_latest:
        return
    temporary = LATEST_PATH.with_name(f"{LATEST_PATH.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, LATEST_PATH)


def _runtime_account() -> str:
    try:
        lease = json.loads(LEASE_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    candidates = [
        lease.get("account"),
        lease.get("psUsername"),
        lease.get("showdownAccount"),
        (lease.get("battleScope") or {}).get("account") if isinstance(lease.get("battleScope"), dict) else None,
        (lease.get("battleScope") or {}).get("psUsername") if isinstance(lease.get("battleScope"), dict) else None,
    ]
    return next((str(value).strip() for value in candidates if str(value or "").strip()), "")


def _configure_environment() -> None:
    defaults = {
        "FP_PARENT_PID": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "BOT_LOG_TO_FILE": "1",
        "OBS_SERVER_PORT": "8777",
        "OBS_SYNC_INTERVAL_SEC": "0",
        "FOULER_OBS_WS_DISABLED": "1",
        "PS_FORMAT": "gen9ou",
        "FOULER_RUNTIME_LEASE_PATH": str(LEASE_PATH),
        "FOULER_OBS_LIFECYCLE_OWNER": "windows-service",
    }
    os.environ.update(defaults)
    account = _runtime_account()
    if account:
        for name in ("PS_USERNAME", "SHOWDOWN_USER_ID", "SHOWDOWN_ACCOUNTS", "FOULER_ACTIVE_ACCOUNT"):
            os.environ[name] = account


def _ignore_service_console_signal(signum: int, _frame: object) -> None:
    try:
        signal_name = signal.Signals(signum).name
    except ValueError:
        signal_name = str(signum)
    _record(
        "service-console-signal-ignored",
        error=f"ignored {signal_name}",
        publish_latest=False,
    )


def _install_service_console_signal_handlers() -> None:
    if os.name != "nt":
        return
    for name in ("SIGINT", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, _ignore_service_console_signal)


def main() -> int:
    os.chdir(ROOT)
    _configure_environment()
    _install_service_console_signal_handlers()
    _record("service-entrypoint-started")
    try:
        runpy.run_path(str(ROOT / "streaming" / "serve_obs_page.py"), run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        _record("python-exited", exit_code=code)
        return code
    except BaseException as exc:
        _record("python-exited", exit_code=1, error=f"{type(exc).__name__}: {exc}")
        raise
    _record("python-exited", exit_code=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
