#!/usr/bin/env python3
"""Run Fouler through run.py with offline-eval-only battle stats storage.

The normal entry point writes to the live root battle_stats.json. Offline eval
uses the same run.py decision path, but its synthetic local battles must stay out
of live ladder evidence and autoresearch inputs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import sys
import threading
import traceback
from pathlib import Path
from types import ModuleType
from typing import Mapping
from urllib.parse import SplitResult, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = PROJECT_ROOT / "eval_results" / "offline"
_LOOPBACK_SCHEMES = frozenset({"http", "https", "ws", "wss"})


def is_loopback_host(value: object) -> bool:
    """Return True only for localhost or a literal loopback IP address."""
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return False
    host = str(value or "").strip().rstrip(".").lower()
    if host == "localhost":
        return True
    if "%" in host:
        host = host.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def validate_loopback_url(
    value: object,
    *,
    label: str = "URL",
    schemes: frozenset[str] = _LOOPBACK_SCHEMES,
) -> SplitResult:
    """Parse a URL and reject credentials, non-loopback hosts, or bad schemes."""
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid: {exc}") from exc
    if parsed.scheme.lower() not in schemes:
        raise ValueError(f"{label} must use one of: {', '.join(sorted(schemes))}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain URL credentials")
    if not parsed.hostname or not is_loopback_host(parsed.hostname):
        raise ValueError(f"{label} must use a loopback host")
    if port is None:
        raise ValueError(f"{label} must include an explicit port")
    return parsed


class LoopbackNetworkGuard:
    """Process-local socket guard used only by the offline runner."""

    def __init__(self, audit_path: Path | None = None):
        self.audit_path = audit_path
        self.blocked: list[dict[str, str]] = []
        self.suppressed: dict[str, int] = {}
        self._lock = threading.Lock()
        self._installed = False

    def record_blocked(self, operation: str, host: object) -> None:
        with self._lock:
            self.blocked.append(
                {
                    "operation": str(operation),
                    "host": str(host or ""),
                }
            )

    def record_suppressed(self, operation: str, count: int = 1) -> None:
        with self._lock:
            self.suppressed[operation] = self.suppressed.get(operation, 0) + count

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "schemaVersion": "fouler-play-loopback-network-audit/v1",
                "loopbackOnly": True,
                "blockedExternalAttemptCount": len(self.blocked),
                "blockedExternalAttempts": list(self.blocked),
                "suppressedOfflineOperations": dict(sorted(self.suppressed.items())),
            }

    def write(self) -> None:
        if self.audit_path is None:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.write_text(
            json.dumps(self.snapshot(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _address_host(address: object) -> object:
    if isinstance(address, tuple) and address:
        return address[0]
    return address


def install_loopback_network_guard(
    *,
    audit_path: Path | None = None,
) -> LoopbackNetworkGuard:
    """Block non-loopback DNS and socket connects in this process only."""
    guard = LoopbackNetworkGuard(audit_path=audit_path)
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and not is_loopback_host(host):
            guard.record_blocked("getaddrinfo", host)
            raise OSError(f"offline rehearsal blocked non-loopback DNS lookup: {host}")
        results = original_getaddrinfo(host, *args, **kwargs)
        if host is not None:
            resolved_hosts = [_address_host(result[4]) for result in results]
            non_loopback = [value for value in resolved_hosts if not is_loopback_host(value)]
            if non_loopback:
                guard.record_blocked("getaddrinfo-result", non_loopback[0])
                raise OSError(
                    "offline rehearsal blocked non-loopback DNS result: "
                    f"{non_loopback[0]}"
                )
        return results

    def guarded_connect(sock, address):
        host = _address_host(address)
        if not is_loopback_host(host):
            guard.record_blocked("connect", host)
            raise OSError(f"offline rehearsal blocked non-loopback connect: {host}")
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        host = _address_host(address)
        if not is_loopback_host(host):
            guard.record_blocked("connect_ex", host)
            raise OSError(f"offline rehearsal blocked non-loopback connect: {host}")
        return original_connect_ex(sock, address)

    def guarded_create_connection(address, *args, **kwargs):
        host = _address_host(address)
        if not is_loopback_host(host):
            guard.record_blocked("create_connection", host)
            raise OSError(f"offline rehearsal blocked non-loopback connect: {host}")
        return original_create_connection(address, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.create_connection = guarded_create_connection
    guard._installed = True
    return guard


def configure_offline_runtime_guards(
    run_battle_module: ModuleType,
    websocket_client_class: type,
    *,
    audit: LoopbackNetworkGuard,
) -> None:
    """Disable public-only side effects while retaining real battle execution."""

    async def no_elo(*_args, **_kwargs):
        audit.record_suppressed("public-elo-probe")
        return None, None

    async def no_glicko(*_args, **_kwargs):
        audit.record_suppressed("public-glicko-probe")
        return None, None, None

    async def no_replay_bool(*_args, **_kwargs):
        audit.record_suppressed("public-replay-probe")
        return False

    async def no_replay_value(*_args, **_kwargs):
        audit.record_suppressed("public-replay-fetch")
        return None

    async def no_replay_saved(*_args, **_kwargs):
        audit.record_suppressed("public-replay-fetch")
        return False

    async def no_chat(*_args, **_kwargs):
        audit.record_suppressed("battle-chat")

    async def no_discord(*_args, **_kwargs):
        audit.record_suppressed("discord-report")
        return None

    original_send_message = websocket_client_class.send_message

    async def guarded_send_message(client, room_name, message_list):
        messages = list(message_list or [])
        allowed = []
        for message in messages:
            text = str(message or "").strip()
            if text.lower() == "/savereplay":
                audit.record_suppressed("replay-upload-command")
                continue
            if room_name and text and not text.startswith("/"):
                audit.record_suppressed("battle-chat")
                continue
            allowed.append(message)
        if not allowed:
            return None
        return await original_send_message(client, room_name, allowed)

    run_battle_module._fetch_elo = no_elo
    run_battle_module._fetch_glicko = no_glicko
    run_battle_module._replay_exists = no_replay_bool
    run_battle_module.resolve_public_replay_url = no_replay_value
    run_battle_module._save_replay_json_locally = no_replay_value
    run_battle_module._save_replay_json_for_evidence = no_replay_saved
    run_battle_module._send_battle_chat = no_chat
    run_battle_module._post_battle_to_discord = no_discord
    run_battle_module.post_battle_messages = lambda: []
    websocket_client_class.send_message = guarded_send_message


def _strip_run_py_sentinel(argv: list[str]) -> list[str]:
    if argv and Path(argv[0]).name.lower() == "run.py":
        return argv[1:]
    return argv


def offline_battle_stats_path(
    *,
    root: Path = PROJECT_ROOT,
    env: Mapping[str, str] | None = None,
) -> Path:
    env = os.environ if env is None else env
    raw = str(env.get("FOULER_OFFLINE_BATTLE_STATS_FILE") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        return path
    label = str(env.get("FOULER_OFFLINE_EVAL_LABEL") or "eval").strip() or "eval"
    return root / "eval_results" / "offline" / f"{label}-battle_stats.json"


def _offline_state_path(
    *,
    root: Path,
    env: Mapping[str, str],
    env_name: str,
    default_name: str,
) -> Path:
    raw = str(env.get(env_name) or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        return path
    label = str(env.get("FOULER_OFFLINE_EVAL_LABEL") or "eval").strip() or "eval"
    return root / "eval_results" / "offline" / f"{label}-{default_name}"


def configure_state_store_module(
    state_store_module: ModuleType,
    *,
    root: Path = PROJECT_ROOT,
    env: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    env = os.environ if env is None else env
    paths = {
        "activeBattles": _offline_state_path(
            root=root,
            env=env,
            env_name="FOULER_OFFLINE_ACTIVE_BATTLES_FILE",
            default_name="active_battles.json",
        ),
        "streamStatus": _offline_state_path(
            root=root,
            env=env,
            env_name="FOULER_OFFLINE_STREAM_STATUS_FILE",
            default_name="stream_status.json",
        ),
        "dailyStats": _offline_state_path(
            root=root,
            env=env,
            env_name="FOULER_OFFLINE_DAILY_STATS_FILE",
            default_name="daily_stats.json",
        ),
        "stabilityReport": _offline_state_path(
            root=root,
            env=env,
            env_name="FOULER_OFFLINE_STABILITY_REPORT_FILE",
            default_name="stability_report.json",
        ),
        "stateStoreFailure": _offline_state_path(
            root=root,
            env=env,
            env_name="FOULER_OFFLINE_STATE_STORE_FAILURE_FILE",
            default_name="state-store-write-failure.json",
        ),
    }
    state_store_module.ACTIVE_BATTLES_PATH = paths["activeBattles"]
    state_store_module.STREAM_STATUS_PATH = paths["streamStatus"]
    state_store_module.DAILY_STATS_PATH = paths["dailyStats"]
    state_store_module.STABILITY_REPORT_PATH = paths["stabilityReport"]
    state_store_module.STATE_STORE_WRITE_FAILURE_PATH = paths["stateStoreFailure"]
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    return paths


def configure_run_module(
    run_module: ModuleType,
    argv: list[str],
    *,
    root: Path = PROJECT_ROOT,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, list[str]]:
    stats_path = offline_battle_stats_path(root=root, env=env)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    run_module.BATTLE_STATS_FILE = stats_path
    return stats_path, ["run.py", *_strip_run_py_sentinel(argv)]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    os.environ["FOULER_OFFLINE_EVAL"] = "1"
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    rehearsal_mode = os.getenv("FOULER_OFFLINE_REHEARSAL", "").strip() == "1"
    audit: LoopbackNetworkGuard | None = None
    if rehearsal_mode:
        raw_audit_path = os.getenv("FOULER_OFFLINE_NETWORK_AUDIT_FILE", "").strip()
        if not raw_audit_path:
            raise RuntimeError(
                "FOULER_OFFLINE_NETWORK_AUDIT_FILE is required in offline rehearsal mode"
            )
        audit_path = Path(raw_audit_path).expanduser()
        if not audit_path.is_absolute():
            raise RuntimeError(
                "FOULER_OFFLINE_NETWORK_AUDIT_FILE must be an absolute path"
            )
        audit = install_loopback_network_guard(audit_path=audit_path)

    run_module: ModuleType | None = None
    try:
        import run as run_module
        from streaming import state_store

        if audit is not None:
            from fp import run_battle as run_battle_module
            from fp.websocket_client import PSWebsocketClient

            configure_offline_runtime_guards(
                run_battle_module,
                PSWebsocketClient,
                audit=audit,
            )

        stats_path, run_argv = configure_run_module(run_module, argv)
        state_paths = configure_state_store_module(state_store)
        sys.argv = run_argv
        print(
            f"[offline-eval-runner] battle stats redirected to {stats_path}",
            file=sys.stderr,
        )
        print(
            "[offline-eval-runner] state store redirected to "
            + ", ".join(f"{name}={path}" for name, path in state_paths.items()),
            file=sys.stderr,
        )

        from process_lock import _OFFLINE_EVAL_AUTHORITY

        asyncio.run(
            run_module.run_foul_play(
                offline_eval_authority=_OFFLINE_EVAL_AUTHORITY,
            )
        )
    except Exception:
        if run_module is not None and hasattr(run_module, "logger"):
            run_module.logger.error(traceback.format_exc())
        raise
    finally:
        if audit is not None:
            audit.write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
