#!/usr/bin/env python3
"""Serve Fouler's loopback OBS surfaces from one finite-season authority."""

from __future__ import annotations

import argparse
import json
import os
import re
import runpy
import signal
import sys
from collections.abc import MutableMapping
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.season_runtime_authority import (  # noqa: E402
    AUTHORITY_PATH_ENV,
    AUTHORITY_SHA256_ENV,
    validate_season_authority,
)

LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 8777
SENSITIVE_NAME_RE = re.compile(
    r"(?:PASSWORD|SECRET|TOKEN|WEBHOOK|OAUTH|STREAM_KEY)", re.IGNORECASE
)


def _redact_sensitive_values(value: str) -> str:
    redacted = value
    for name, secret in os.environ.items():
        if SENSITIVE_NAME_RE.search(name) and secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def configure_environment(
    *,
    authority_path: str,
    authority_sha256: str,
    environment: MutableMapping[str, str] | None = None,
    hostname: str | None = None,
) -> dict:
    """Validate the exact season/release and configure the read-only HTTP surface."""
    validation = validate_season_authority(
        authority_path=authority_path,
        expected_sha256=authority_sha256,
        release_root=ROOT,
        require_child_binding=False,
        require_existing_paths=True,
        environ=os.environ if environment is None else environment,
        hostname=hostname,
    )
    if not validation.get("ok"):
        blockers = validation.get("blockers")
        detail = (
            "; ".join(str(item) for item in blockers)
            if isinstance(blockers, list)
            else "validation failed"
        )
        raise RuntimeError(f"OBS finite-season authority is invalid: {detail}")

    season = validation.get("season")
    if not isinstance(season, dict):
        raise RuntimeError("OBS finite-season authority omitted its public season summary")
    runtime = season.get("runtime")
    if not isinstance(runtime, dict):
        raise RuntimeError("OBS finite-season authority omitted runtime paths")
    account = str(season.get("account") or "").strip()
    if not account:
        raise RuntimeError("OBS finite-season authority omitted the Showdown account")

    target = os.environ if environment is None else environment
    state_root = Path(str(runtime["stateRoot"]))
    log_root = Path(str(runtime["logRoot"]))
    cache_root = Path(str(runtime["cacheRoot"]))
    temp_root = Path(str(runtime["tempRoot"]))
    target.update(
        {
            AUTHORITY_PATH_ENV: str(Path(authority_path).resolve(strict=False)),
            AUTHORITY_SHA256_ENV: authority_sha256.lower(),
            "FOULER_RUNTIME_STATE_ROOT": str(state_root),
            "FOULER_RUNTIME_LOG_ROOT": str(log_root),
            "FOULER_RUNTIME_CACHE_ROOT": str(cache_root),
            "FOULER_RUNTIME_TEMP_ROOT": str(temp_root),
            "FOULER_ACCOUNT_SEASON_PATH": str(runtime["accountSeasonPath"]),
            "DEKU_EVENT_QUEUE_ROOT": str(runtime["eventQueueRoot"]),
            "FOULER_LOG_DIR": str(log_root),
            "DECISION_TRACE_DIR": str(log_root / "decision_traces"),
            "TEMP": str(temp_root),
            "TMP": str(temp_root),
            "FP_PARENT_PID": "0",
            "BOT_LOG_TO_FILE": "1",
            "OBS_SYNC_INTERVAL_SEC": "0",
            "FOULER_OBS_LIFECYCLE_OWNER": "windows-task",
            "FOULER_OBS_AUTHORITY_MANAGED": "1",
            "FOULER_OBS_WS_DISABLED": "1",
            "OBS_SERVER_HOST": LOOPBACK_HOST,
            "OBS_SERVER_PORT": str(LOOPBACK_PORT),
            "PS_FORMAT": "gen9ou",
            "PS_USERNAME": account,
            "SHOWDOWN_USER_ID": account,
            "SHOWDOWN_ACCOUNTS": account,
            "FOULER_ACTIVE_ACCOUNT": account,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return validation


def _truth_paths(validation: dict) -> tuple[Path, Path]:
    season = validation["season"]
    state_root = Path(str(season["runtime"]["stateRoot"]))
    truth = state_root / "truth"
    return truth / "obs-server-launch.json", truth / "obs-server-launch.jsonl"


def _record(
    validation: dict,
    phase: str,
    *,
    exit_code: int | None = None,
    error: str | None = None,
    publish_latest: bool = True,
) -> None:
    latest, events = _truth_paths(validation)
    payload = {
        "schemaVersion": "fouler-season-obs-launch/v1",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "processId": os.getpid(),
        "phase": phase,
        "foreground": True,
        "lifecycleOwner": "windows-task",
        "seasonId": validation["season"].get("id"),
        "sourceCommit": validation["season"].get("sourceCommit"),
        "exitCode": exit_code,
    }
    if error:
        payload["error"] = _redact_sensitive_values(error)[:1000]
    latest.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    with events.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    if publish_latest:
        temporary = latest.with_name(f".{latest.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, latest)


def _install_console_signal_handlers(validation: dict) -> None:
    if os.name != "nt":
        return

    def ignore(signum: int, _frame: object) -> None:
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        _record(
            validation,
            "task-console-signal-ignored",
            error=f"ignored {signal_name}",
            publish_latest=False,
        )

    for name in ("SIGINT", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, ignore)


def _run_public_surface() -> None:
    from aiohttp import web

    original_run_app = web.run_app

    def run_loopback(app: object, *args: object, **kwargs: object) -> object:
        kwargs["host"] = LOOPBACK_HOST
        kwargs["port"] = LOOPBACK_PORT
        return original_run_app(app, *args, **kwargs)

    web.run_app = run_loopback
    try:
        runpy.run_path(
            str(ROOT / "streaming" / "serve_obs_page.py"),
            run_name="__main__",
        )
    finally:
        web.run_app = original_run_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--authority",
        default=os.getenv(AUTHORITY_PATH_ENV, ""),
    )
    parser.add_argument(
        "--authority-sha256",
        default=os.getenv(AUTHORITY_SHA256_ENV, ""),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    try:
        validation = configure_environment(
            authority_path=args.authority,
            authority_sha256=args.authority_sha256,
        )
    except Exception as exc:
        print(
            _redact_sensitive_values(
                f"finite-season OBS admission failed: {type(exc).__name__}: {exc}"
            ),
            file=sys.stderr,
        )
        return 2
    if args.check:
        print(
            json.dumps(
                {
                    "schemaVersion": "fouler-season-obs-check/v1",
                    "ok": True,
                    "seasonId": validation["season"].get("id"),
                    "sourceCommit": validation["season"].get("sourceCommit"),
                    "account": validation["season"].get("account"),
                    "host": LOOPBACK_HOST,
                    "port": LOOPBACK_PORT,
                    "publicOutputChanged": False,
                    "startStreaming": False,
                },
                indent=2,
            )
        )
        return 0

    os.chdir(ROOT)
    _install_console_signal_handlers(validation)
    _record(validation, "task-entrypoint-started")
    try:
        _run_public_surface()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        _record(validation, "python-exited", exit_code=code)
        return code
    except BaseException as exc:
        _record(
            validation,
            "python-exited",
            exit_code=1,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    _record(validation, "python-exited", exit_code=0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
