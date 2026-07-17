#!/usr/bin/env python3
from __future__ import annotations

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

from scripts.devstream_runtime_lease import (  # noqa: E402
    lease_environment,
    runtime_lease_path,
    validate_runtime_lease,
)


def _runtime_state_root() -> Path:
    configured = os.getenv("FOULER_RUNTIME_STATE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().absolute()
    if os.name == "nt":
        return (
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / "HERMES"
            / "state"
            / "fouler"
        )
    return ROOT / "devstream" / "runtime-state"


TRUTH_DIR = _runtime_state_root() / "truth"
LEASE_PATH = runtime_lease_path()
LATEST_PATH = TRUTH_DIR / "obs-server-launch.json"
EVENTS_PATH = TRUTH_DIR / "obs-server-launch.jsonl"
LOOPBACK_HOST = "127.0.0.1"
LOOPBACK_PORT = 8777
WINDOWS_RELEASE_RE = re.compile(
    r"^D:\\Releases\\fouler-play\\[0-9a-f]{40}$"
)
WINDOWS_EXTERNAL_PATHS = {
    "FOULER_RUNTIME_LEASE_PATH": Path(
        r"C:\ProgramData\HERMES\authority\fouler\runtime-lease.json"
    ),
    "FOULER_CONTROLLER_TRUST_STORE_PATH": Path(
        r"C:\ProgramData\HERMES\authority\fouler\controller-keys.json"
    ),
    "FOULER_RUNTIME_STATE_ROOT": Path(r"C:\ProgramData\HERMES\state\fouler"),
    "FOULER_RUNTIME_LOG_ROOT": Path(r"C:\ProgramData\HERMES\logs\fouler"),
    "FOULER_RUNTIME_CACHE_ROOT": Path(r"C:\ProgramData\HERMES\cache\fouler"),
    "FOULER_RUNTIME_TEMP_ROOT": Path(r"C:\ProgramData\HERMES\state\fouler\tmp"),
}
SENSITIVE_NAME_RE = re.compile(
    r"(?:PASSWORD|SECRET|TOKEN|WEBHOOK|OAUTH|STREAM_KEY)", re.IGNORECASE
)


def _redact_sensitive_values(value: str) -> str:
    redacted = value
    for name, secret in os.environ.items():
        if SENSITIVE_NAME_RE.search(name) and secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


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
        payload["error"] = _redact_sensitive_values(error)[:1000]
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    with EVENTS_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    if not publish_latest:
        return
    temporary = LATEST_PATH.with_name(f"{LATEST_PATH.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, LATEST_PATH)


def _validated_runtime() -> dict:
    validation = validate_runtime_lease(
        purpose="jigglypuff-runtime-start",
        lease_path=LEASE_PATH,
        require_deployment_receipt=True,
        verify_deployment_checkout=True,
    )
    if not validation.get("ok"):
        blockers = validation.get("blockers")
        detail = "; ".join(str(item) for item in blockers) if isinstance(blockers, list) else "validation failed"
        raise RuntimeError(f"OBS service runtime authority is invalid: {detail}")
    return validation


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    return (
        left_resolved == right_resolved
        or left_resolved in right_resolved.parents
        or right_resolved in left_resolved.parents
    )


def _assert_service_runtime_layout() -> None:
    if os.name != "nt":
        return
    root_text = str(ROOT)
    if not WINDOWS_RELEASE_RE.fullmatch(root_text):
        raise RuntimeError("OBS service root is not D:\\Releases\\fouler-play\\<40-char commit>")
    expected_python = (ROOT / ".venv" / "Scripts" / "python.exe").resolve(strict=True)
    if Path(sys.executable).resolve(strict=True) != expected_python:
        raise RuntimeError("OBS service is not running from the pinned release venv Python")
    required_external_paths = {
        name: Path(os.environ.get(name, "")) for name in WINDOWS_EXTERNAL_PATHS
    }
    for name, path in required_external_paths.items():
        if not str(path) or not path.is_absolute():
            raise RuntimeError(f"{name} must be an absolute external path")
        if _paths_overlap(path, ROOT):
            raise RuntimeError(f"{name} must not overlap the immutable release")
        if os.path.normcase(str(path.resolve(strict=False))) != os.path.normcase(
            str(WINDOWS_EXTERNAL_PATHS[name].resolve(strict=False))
        ):
            raise RuntimeError(f"{name} must equal the canonical protected path")
    configured_host = os.environ.get("OBS_SERVER_HOST", LOOPBACK_HOST).strip()
    configured_port = os.environ.get("OBS_SERVER_PORT", str(LOOPBACK_PORT)).strip()
    if configured_host != LOOPBACK_HOST or configured_port != str(LOOPBACK_PORT):
        raise RuntimeError("OBS service bind must equal 127.0.0.1:8777")


def _configure_environment(
    environment: MutableMapping[str, str] | None = None,
) -> None:
    _assert_service_runtime_layout()
    validation = _validated_runtime()
    summary = validation.get("lease") if isinstance(validation.get("lease"), dict) else {}
    account = str(summary.get("account") or "").strip()
    if not account:
        raise RuntimeError("validated OBS service runtime lease has no Showdown account")
    target = os.environ if environment is None else environment
    state_root = _runtime_state_root()
    defaults = {
        "FP_PARENT_PID": "0",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "BOT_LOG_TO_FILE": "1",
        "OBS_SYNC_INTERVAL_SEC": "0",
        "FOULER_RUNTIME_LEASE_PATH": str(LEASE_PATH),
        "FOULER_RUNTIME_STATE_ROOT": str(state_root),
        "FOULER_RUNTIME_LOG_ROOT": str(state_root / "logs"),
        "FOULER_OBS_LIFECYCLE_OWNER": "windows-service",
    }
    if os.name == "nt":
        defaults.update(
            {name: str(path) for name, path in WINDOWS_EXTERNAL_PATHS.items()}
        )
    for name, value in defaults.items():
        target.setdefault(name, value)
    # JIGGLYPUFF only serves the browser-source HTTP surface. OBS control and
    # credentials belong exclusively to the RWLEGION broadcast node.
    target["FOULER_OBS_WS_DISABLED"] = "1"
    target["OBS_SERVER_HOST"] = LOOPBACK_HOST
    target["OBS_SERVER_PORT"] = str(LOOPBACK_PORT)
    target["PS_FORMAT"] = "gen9ou"
    target.update(lease_environment(validation))
    for name in ("PS_USERNAME", "SHOWDOWN_USER_ID", "SHOWDOWN_ACCOUNTS", "FOULER_ACTIVE_ACCOUNT"):
        target[name] = account


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


def _run_public_surface() -> None:
    from aiohttp import web

    original_run_app = web.run_app

    def run_loopback(app: object, *args: object, **kwargs: object) -> object:
        kwargs["host"] = LOOPBACK_HOST
        kwargs["port"] = LOOPBACK_PORT
        return original_run_app(app, *args, **kwargs)

    web.run_app = run_loopback
    try:
        runpy.run_path(str(ROOT / "streaming" / "serve_obs_page.py"), run_name="__main__")
    finally:
        web.run_app = original_run_app


def main() -> int:
    os.chdir(ROOT)
    _install_service_console_signal_handlers()
    try:
        _configure_environment()
    except Exception as exc:
        _record("runtime-authority-blocked", exit_code=2, error=f"{type(exc).__name__}: {exc}")
        return 2
    _record("service-entrypoint-started")
    try:
        _run_public_surface()
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
