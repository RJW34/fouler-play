#!/usr/bin/env python3
"""Dry-run-first orchestration for Fouler's private local stream rehearsal."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import ipaddress
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OFFLINE_RUNNER = PROJECT_ROOT / "infrastructure" / "offline_eval_runner.py"
OPPONENT_RUNNER = PROJECT_ROOT / "infrastructure" / "local_rehearsal_opponent.py"
OVERLAY_SERVER = PROJECT_ROOT / "streaming" / "serve_obs_page.py"

LOCKED_BATTLE_COUNT = 30
LOCKED_CONCURRENCY = 3
LOCKED_SEARCH_PARALLELISM = 2
LOCKED_FORMAT = "gen9ou"
LOCKED_TEAM_NAMES = (
    "gen9/ou/fat-team-1-stall",
    "gen9/ou/fat-team-2-balance",
    "gen9/ou/fat-team-3-dondozo",
)
LOCKED_TEAM_FILES = tuple(PROJECT_ROOT / "teams" / name for name in LOCKED_TEAM_NAMES)
LOCKED_TEAM_BASENAMES = tuple(path.name for path in LOCKED_TEAM_FILES)
FOULER_USERNAME = "FoulerRehearsal"
OPPONENT_USERNAME = "FoulerLocalOpp"
DEFAULT_SHOWDOWN_WS_URL = "ws://127.0.0.1:8765/showdown/websocket"
DEFAULT_SHOWDOWN_AUTH_URL = "http://127.0.0.1:8765/action.php?"
DEFAULT_OVERLAY_URL = "http://127.0.0.1:8877"
RUNTIME_IMPORTS = ("aiohttp", "requests", "dotenv", "dateutil", "psutil", "poke_engine")
OPPONENT_IMPORTS = ("poke_env",)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        Path(tempfile.gettempdir())
        / "fouler-local-rehearsal"
        / f"run-{stamp}-{os.getpid()}"
    )


@dataclass(frozen=True)
class RehearsalLayout:
    root: Path
    state: Path
    logs: Path
    cache: Path
    temp: Path
    proof: Path
    decision_traces: Path
    empty_env: Path
    battle_stats: Path
    active_battles: Path
    stream_status: Path
    daily_stats: Path
    stability_report: Path
    state_store_failure: Path
    process_lock: Path
    event_queue: Path
    fouler_network_audit: Path
    opponent_network_audit: Path
    showdown_network_audit_dir: Path
    showdown_preload: Path
    showdown_runtime: Path
    opponent_result: Path
    report: Path


def build_layout(root: Path | str) -> RehearsalLayout:
    root_path = Path(root).expanduser()
    if not root_path.is_absolute():
        raise ValueError("rehearsal root must be an absolute path")
    root_path = root_path.resolve(strict=False)
    state = root_path / "state"
    logs = root_path / "logs"
    proof = root_path / "proof"
    return RehearsalLayout(
        root=root_path,
        state=state,
        logs=logs,
        cache=root_path / "cache",
        temp=root_path / "temp",
        proof=proof,
        decision_traces=logs / "decision_traces",
        empty_env=root_path / "rehearsal.env",
        battle_stats=state / "battle_stats.json",
        active_battles=state / "active_battles.json",
        stream_status=state / "stream_status.json",
        daily_stats=state / "daily_stats.json",
        stability_report=state / "stability_report.json",
        state_store_failure=state / "truth" / "state-store-write-failure.json",
        process_lock=state / "pids" / "bot.pid",
        event_queue=state / "events_queue.json",
        fouler_network_audit=proof / "fouler-network-audit.json",
        opponent_network_audit=proof / "opponent-network-audit.json",
        showdown_network_audit_dir=proof / "showdown-network-audits",
        showdown_preload=root_path / "temp" / "showdown-rehearsal-preload.cjs",
        showdown_runtime=root_path / "showdown-runtime",
        opponent_result=proof / "opponent-result.json",
        report=proof / "local-rehearsal-report.json",
    )


def _canonical(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _paths_overlap(first: Path | str, second: Path | str) -> bool:
    first_path = _canonical(first)
    second_path = _canonical(second)
    try:
        first_path.relative_to(second_path)
        return True
    except ValueError:
        pass
    try:
        second_path.relative_to(first_path)
        return True
    except ValueError:
        return False


def production_watch_paths(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    env = os.environ if environ is None else environ
    local_app_data = Path(
        str(env.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    ).expanduser()
    program_data = Path(str(env.get("PROGRAMDATA") or r"C:\ProgramData")).expanduser()
    return (
        PROJECT_ROOT / "battle_stats.json",
        PROJECT_ROOT / "active_battles.json",
        PROJECT_ROOT / "stream_status.json",
        PROJECT_ROOT / "daily_stats.json",
        PROJECT_ROOT / "stability_report.json",
        PROJECT_ROOT / "events_queue.json",
        PROJECT_ROOT / ".bot.pid",
        PROJECT_ROOT / "pids",
        PROJECT_ROOT / "logs",
        PROJECT_ROOT / "replay_analysis",
        local_app_data / "FoulerPlay" / "runtime",
        program_data / "HERMES" / "state" / "fouler",
        program_data / "HERMES" / "logs" / "fouler",
        program_data / "HERMES" / "cache" / "fouler",
    )


def showdown_watch_paths(showdown_dir: Path | str) -> tuple[Path, ...]:
    root = _canonical(showdown_dir)
    if not root.is_dir():
        return (root,)
    return tuple(
        child
        for child in sorted(root.iterdir(), key=lambda path: path.name.casefold())
        if child.name not in {".git", "node_modules"}
    )


def validate_rehearsal_root(
    root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    candidate = _canonical(root)
    forbidden = (PROJECT_ROOT, *production_watch_paths(environ))
    collisions = [str(path) for path in forbidden if _paths_overlap(candidate, path)]
    if collisions:
        raise ValueError(
            "rehearsal root overlaps source or production runtime paths: "
            + ", ".join(collisions)
        )
    return candidate


def _load_loopback_helpers():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from infrastructure.offline_eval_runner import is_loopback_host, validate_loopback_url

    return is_loopback_host, validate_loopback_url


def validate_rehearsal_urls(
    websocket_url: str,
    authentication_url: str,
    overlay_url: str,
) -> dict[str, object]:
    _, validate_loopback_url = _load_loopback_helpers()
    websocket = validate_loopback_url(
        websocket_url,
        label="Showdown websocket URL",
        schemes=frozenset({"ws"}),
    )
    authentication = validate_loopback_url(
        authentication_url,
        label="Showdown authentication URL",
        schemes=frozenset({"http"}),
    )
    overlay = validate_loopback_url(
        overlay_url,
        label="overlay URL",
        schemes=frozenset({"http"}),
    )
    if websocket.port != authentication.port:
        raise ValueError("Showdown websocket and authentication URLs must use the same port")
    websocket_host = str(websocket.hostname or "").strip().lower()
    authentication_host = str(authentication.hostname or "").strip().lower()
    if websocket_host == "localhost":
        websocket_host = "127.0.0.1"
    if authentication_host == "localhost":
        authentication_host = "127.0.0.1"
    try:
        websocket_host = str(ipaddress.ip_address(websocket_host))
        authentication_host = str(ipaddress.ip_address(authentication_host))
    except ValueError:
        pass
    if websocket_host != authentication_host:
        raise ValueError("Showdown websocket and authentication URLs must use one loopback host")
    if websocket_host != "127.0.0.1":
        raise ValueError(
            "Showdown URLs must use localhost or the 127.0.0.1 loopback address"
        )
    if websocket.path != "/showdown/websocket":
        raise ValueError("Showdown websocket URL must use /showdown/websocket")
    if authentication.path != "/action.php":
        raise ValueError("Showdown authentication URL must use /action.php")
    if overlay.path not in ("", "/") or overlay.query or overlay.fragment:
        raise ValueError("overlay URL must contain only a loopback origin")
    if overlay.port == websocket.port:
        raise ValueError("overlay and Showdown must use different loopback ports")
    overlay_host = str(overlay.hostname or "").strip().lower()
    if overlay_host == "localhost":
        overlay_host = "127.0.0.1"
    try:
        overlay_host = str(ipaddress.ip_address(overlay_host))
    except ValueError:
        pass
    if overlay_host != "127.0.0.1":
        raise ValueError(
            "overlay URL must use localhost or the 127.0.0.1 loopback address"
        )
    bind_host = websocket_host
    return {
        "websocket": websocket,
        "authentication": authentication,
        "overlay": overlay,
        "showdownHost": websocket.hostname,
        "showdownPort": websocket.port,
        "showdownBindHost": bind_host,
        "overlayHost": overlay_host,
        "overlayPort": overlay.port,
    }


def _split_command(raw: str) -> list[str]:
    raw = str(raw or "").strip()
    if not raw:
        return []
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return [str(candidate.resolve())]
    return shlex.split(raw, posix=os.name != "nt")


def _python_candidates(*, role: str, explicit: str | None) -> list[list[str]]:
    if explicit:
        return [_split_command(explicit)]
    env_name = "FOULER_RUNTIME_PYTHON" if role == "runtime" else "FOULER_EVAL_PYTHON"
    inherited = os.getenv(env_name, "").strip()
    if inherited:
        return [_split_command(inherited)]

    roots = [PROJECT_ROOT]
    if role == "opponent":
        roots.append(PROJECT_ROOT.parent / "fouler-play")
    names = (".venv",) if role == "runtime" else (".venv-eval", ".venv")
    candidates: list[list[str]] = []
    for root in roots:
        for name in names:
            for relative in (Path("Scripts/python.exe"), Path("bin/python")):
                executable = root / name / relative
                if executable.is_file():
                    candidates.append([str(executable.resolve())])
    candidates.append([sys.executable])
    for name in ("python", "python3"):
        executable = shutil.which(name)
        if executable:
            candidates.append([executable])
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if candidate and key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _probe_python(command: list[str], modules: tuple[str, ...]) -> tuple[bool, dict[str, object]]:
    probe = "; ".join(f"import {module}" for module in modules)
    code = f"{probe}; import json,sys; print(json.dumps({{'executable':sys.executable}}))"
    detail: dict[str, object] = {"command": subprocess.list2cmdline(command)}
    try:
        result = subprocess.run(
            [*command, "-B", "-c", code],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except Exception as exc:
        detail["error"] = str(exc)
        return False, detail
    detail.update(
        {
            "returncode": result.returncode,
            "stdout": (result.stdout or "").strip()[-500:],
            "stderr": (result.stderr or "").strip()[-500:],
        }
    )
    return result.returncode == 0, detail


def resolve_python(
    *,
    role: str,
    explicit: str | None = None,
) -> tuple[list[str] | None, list[dict[str, object]]]:
    modules = RUNTIME_IMPORTS if role == "runtime" else OPPONENT_IMPORTS
    attempts: list[dict[str, object]] = []
    for candidate in _python_candidates(role=role, explicit=explicit):
        ok, detail = _probe_python(candidate, modules)
        attempts.append(detail)
        if ok:
            return candidate, attempts
    return None, attempts


def _port_is_free(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            probe.bind((host, port))
        return True
    except OSError:
        return False


def _check(name: str, ok: bool, detail: object) -> dict[str, object]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def run_doctor(args: argparse.Namespace) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    urls: dict[str, object] | None = None
    layout: RehearsalLayout | None = None
    try:
        urls = validate_rehearsal_urls(
            args.websocket_url,
            args.authentication_url,
            args.overlay_url,
        )
        checks.append(_check("loopback URLs", True, "all origins are explicit loopback URLs"))
    except ValueError as exc:
        checks.append(_check("loopback URLs", False, str(exc)))

    try:
        layout = build_layout(args.rehearsal_root)
        validate_rehearsal_root(layout.root)
        if _paths_overlap(layout.root, args.showdown_dir):
            raise ValueError("rehearsal root overlaps the sibling Pokemon Showdown checkout")
        root_empty = not layout.root.exists() or not any(layout.root.iterdir())
        checks.append(
            _check(
                "isolated rehearsal root",
                root_empty,
                str(layout.root) if root_empty else "root exists and is not empty",
            )
        )
    except (OSError, ValueError) as exc:
        checks.append(_check("isolated rehearsal root", False, str(exc)))

    missing_teams = [str(path) for path in LOCKED_TEAM_FILES if not path.is_file()]
    checks.append(
        _check(
            "owner-locked pilot teams",
            not missing_teams,
            list(LOCKED_TEAM_NAMES) if not missing_teams else missing_teams,
        )
    )

    showdown_dir = _canonical(args.showdown_dir)
    showdown_required = (
        showdown_dir / "pokemon-showdown",
        showdown_dir / "build",
        showdown_dir / "package.json",
        showdown_dir / "node_modules",
        showdown_dir / "node_modules" / "esbuild",
        showdown_dir / "node_modules" / "ts-chacha20",
        showdown_dir / "tools" / "build-utils.js",
    )
    missing_showdown = [str(path) for path in showdown_required if not path.exists()]
    checks.append(
        _check(
            "sibling Pokemon Showdown checkout",
            not missing_showdown,
            str(showdown_dir) if not missing_showdown else missing_showdown,
        )
    )
    node = shutil.which("node")
    checks.append(_check("Node.js", bool(node), node or "node is not on PATH"))

    runtime_python, runtime_attempts = resolve_python(
        role="runtime",
        explicit=args.runtime_python,
    )
    checks.append(
        _check(
            "Fouler runtime Python",
            runtime_python is not None,
            subprocess.list2cmdline(runtime_python) if runtime_python else runtime_attempts,
        )
    )
    opponent_python, opponent_attempts = resolve_python(
        role="opponent",
        explicit=args.opponent_python,
    )
    checks.append(
        _check(
            "poke-env opponent Python",
            opponent_python is not None,
            subprocess.list2cmdline(opponent_python) if opponent_python else opponent_attempts,
        )
    )
    try:
        import psutil  # noqa: F401

        process_audit_ok = True
        process_audit_detail = "psutil available to the rehearsal orchestrator"
    except ImportError as exc:
        process_audit_ok = False
        process_audit_detail = f"invoke this script with a Python containing psutil: {exc}"
    checks.append(_check("process/listener audit", process_audit_ok, process_audit_detail))

    scripts = (OFFLINE_RUNNER, OPPONENT_RUNNER, OVERLAY_SERVER, PROJECT_ROOT / "run.py")
    missing_scripts = [str(path) for path in scripts if not path.is_file()]
    checks.append(_check("rehearsal scripts", not missing_scripts, missing_scripts or "present"))
    if urls:
        showdown_host = str(urls["showdownBindHost"])
        overlay_host = str(urls["overlayHost"])
        checks.append(
            _check(
                "Showdown port free",
                _port_is_free(showdown_host, int(urls["showdownPort"])),
                f"{showdown_host}:{urls['showdownPort']}",
            )
        )
        checks.append(
            _check(
                "overlay port free",
                _port_is_free(overlay_host, int(urls["overlayPort"])),
                f"{overlay_host}:{urls['overlayPort']}",
            )
        )

    plan = {
        "mode": "dry-run",
        "startsProcesses": False,
        "requiresExecute": True,
        "privateLoopbackOnly": True,
        "matchmaking": "search_ladder on managed private Showdown",
        "battles": LOCKED_BATTLE_COUNT,
        "concurrency": LOCKED_CONCURRENCY,
        "searchParallelism": LOCKED_SEARCH_PARALLELISM,
        "teams": list(LOCKED_TEAM_NAMES),
        "expectedDistribution": {name: 10 for name in LOCKED_TEAM_BASENAMES},
        "rehearsalRoot": str(layout.root) if layout else str(args.rehearsal_root),
        "showdownDir": str(showdown_dir),
        "showdownRuntime": str(layout.showdown_runtime) if layout else None,
        "showdownSourceIsReadOnly": True,
        "runtimePython": runtime_python,
        "opponentPython": opponent_python,
    }
    return {
        "schemaVersion": "fouler-play-local-rehearsal-doctor/v1",
        "generatedAt": _utc_now(),
        "ok": all(bool(check["ok"]) for check in checks),
        "checks": checks,
        "plan": plan,
        "resolved": {
            "urls": {
                key: value.geturl() if hasattr(value, "geturl") else value
                for key, value in (urls or {}).items()
            },
            "runtimePython": runtime_python,
            "opponentPython": opponent_python,
        },
    }


def build_rehearsal_env(
    layout: RehearsalLayout,
    *,
    overlay_port: int,
    authentication_url: str,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in list(env):
        upper = key.upper()
        if (
            upper.startswith("OPENAI_")
            or "WEBHOOK" in upper
            or upper in {"TWITCH_TOKEN", "TWITCH_OAUTH_TOKEN", "TWITCH_STREAM_KEY"}
            or (upper.startswith("OBS_") and "PASSWORD" in upper)
        ):
            env[key] = ""
        if upper.startswith("FOULER_RUNTIME_") and upper not in {
            "FOULER_RUNTIME_STATE_ROOT",
            "FOULER_RUNTIME_LOG_ROOT",
            "FOULER_RUNTIME_CACHE_ROOT",
            "FOULER_RUNTIME_TEMP_ROOT",
        }:
            env[key] = ""

    assignments = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": "",
        "TMP": str(layout.temp),
        "TEMP": str(layout.temp),
        "TMPDIR": str(layout.temp),
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "FOULER_ENV_FILE": str(layout.empty_env),
        "FOULER_RUNTIME_PRODUCTION": "0",
        "FOULER_RUNTIME_STATE_ROOT": str(layout.state),
        "FOULER_RUNTIME_LOG_ROOT": str(layout.logs),
        "FOULER_RUNTIME_CACHE_ROOT": str(layout.cache),
        "FOULER_RUNTIME_TEMP_ROOT": str(layout.temp),
        "FOULER_LOG_DIR": str(layout.logs),
        "FOULER_FILE_LOG_LEVEL": "INFO",
        "FOULER_WORKER_LOG_LEVEL": "INFO",
        "DECISION_TRACE_DIR": str(layout.decision_traces),
        "FOULER_PUBLIC_BATTLE_VIEW_PATH": str(
            layout.decision_traces / "latest-public-battle.json"
        ),
        "FOULER_BATTLE_STATS_PATH": str(layout.battle_stats),
        "FOULER_MATCHUP_WEIGHTS_PATH": str(layout.state / "learning" / "matchup_weights.json"),
        "MATCHUP_MEMORY_AB_LOG": str(layout.logs / "matchup_ab_log.jsonl"),
        "FOULER_MOVEPOOL_DATA_PATH": str(layout.state / "learning" / "movepool_data.json"),
        "FOULER_OFFLINE_EVAL": "1",
        "FOULER_OFFLINE_REHEARSAL": "1",
        "FOULER_OFFLINE_EVAL_LABEL": "local-rehearsal",
        "FOULER_NO_SECURITY_LOGIN": "1",
        "FOULER_LOGIN_URI": authentication_url,
        "FOULER_OFFLINE_BATTLE_STATS_FILE": str(layout.battle_stats),
        "FOULER_OFFLINE_ACTIVE_BATTLES_FILE": str(layout.active_battles),
        "FOULER_OFFLINE_STREAM_STATUS_FILE": str(layout.stream_status),
        "FOULER_OFFLINE_DAILY_STATS_FILE": str(layout.daily_stats),
        "FOULER_OFFLINE_STABILITY_REPORT_FILE": str(layout.stability_report),
        "FOULER_OFFLINE_STATE_STORE_FAILURE_FILE": str(layout.state_store_failure),
        "FOULER_OFFLINE_NETWORK_AUDIT_FILE": str(layout.fouler_network_audit),
        "FOULER_PROCESS_LOCK_FILE": str(layout.process_lock),
        "EVENT_QUEUE_FILE": str(layout.event_queue),
        "EVENT_QUEUE_BACKLOG_ARCHIVE_DIR": str(layout.logs / "discord-events"),
        "DEKU_EVENT_QUEUE_ROOT": str(layout.state / "deku-events"),
        "FOULER_ACCOUNT_SEASON_PATH": str(layout.state / "truth" / "account-season.json"),
        "FOULER_BATTLE_RESULT_QUEUE": "0",
        "FOULER_OFFLINE_EVAL_QUEUE_EVENTS": "0",
        "FOULER_STREAM_EVENTS": "0",
        "STREAM_EVENT_URL": "",
        "ENABLE_STREAM_HOOKS": "0",
        "LOSS_TRIGGERED_DRAIN": "0",
        "RESUME_ACTIVE_BATTLES": "0",
        "DECISION_TRACE": "1",
        "MAX_CONCURRENT_BATTLES": str(LOCKED_CONCURRENCY),
        "SEARCH_PARALLELISM": str(LOCKED_SEARCH_PARALLELISM),
        "FP_EXPECTED_DEVSTREAM_BATTLE_SURFACES": str(LOCKED_CONCURRENCY),
        "PS_PASSWORD": "",
        "SHOWDOWN_PASSWORD": "",
        "FOULER_SHOWDOWN_PASSWORD": "",
        "PS_USERNAME": FOULER_USERNAME,
        "SHOWDOWN_ACCOUNTS": FOULER_USERNAME,
        "SHOWDOWN_PROFILE_URL": "",
        "SPECTATOR_USERNAME": "",
        "ENABLE_SPECTATOR_INVITES": "0",
        "FOULER_POST_BATTLE_CHAT_ENABLED": "0",
        "FOULER_POST_BATTLE_PROMO_AUTHORIZED": "0",
        "POST_BATTLE_LIVE_PROMO_MESSAGE": "",
        "DISCORD_BATTLES_WEBHOOK_URL": "",
        "DISCORD_WEBHOOK_URL": "",
        "DISCORD_FEEDBACK_WEBHOOK_URL": "",
        "OPENAI_API_KEY": "",
        "OPENAI_API_KEY_PLAYER": "",
        "OPENAI_API_KEY_LEARNER": "",
        "DEKU_STATE_URL": "",
        "MAGNETON_STATE_URL": "",
        "FOULER_DEVSTREAM_LIVE": "0",
        "FOULER_DEVSTREAM_STATUS_JSON": "",
        "FOULER_DEVSTREAM_STATUS_URL": "",
        "FOULER_OBS_OFFLINE_REHEARSAL": "1",
        "FOULER_OBS_WS_DISABLED": "1",
        "FOULER_OBS_SERVER_ALLOW_DUPLICATE": "1",
        "FOULER_OBS_DEEP_HEALTH_DEFAULT": "0",
        "OBS_SERVER_PORT": str(overlay_port),
        "SHOWDOWN_ELO_POLL_SEC": "0",
        "GHOST_CHECK_INTERVAL_SEC": "0",
        "REPLAY_CHECK_MIN_AGE_SEC": "999999",
    }
    env.update(assignments)
    return env


def build_fouler_command(
    runtime_python: list[str],
    *,
    websocket_url: str,
    search_time_ms: int,
) -> list[str]:
    return [
        *runtime_python,
        "-B",
        str(OFFLINE_RUNNER),
        "run.py",
        "--websocket-uri",
        websocket_url,
        "--ps-username",
        FOULER_USERNAME,
        "--bot-mode",
        "search_ladder",
        "--pokemon-format",
        LOCKED_FORMAT,
        "--team-names",
        ",".join(LOCKED_TEAM_NAMES),
        "--run-count",
        str(LOCKED_BATTLE_COUNT),
        "--max-concurrent-battles",
        str(LOCKED_CONCURRENCY),
        "--search-parallelism",
        str(LOCKED_SEARCH_PARALLELISM),
        "--search-time-ms",
        str(search_time_ms),
        "--save-replay",
        "never",
        "--decision-policy",
        "eval",
        "--log-to-file",
    ]


def build_opponent_command(
    opponent_python: list[str],
    layout: RehearsalLayout,
    *,
    websocket_url: str,
    authentication_url: str,
    baseline: str,
    timeout_seconds: float,
) -> list[str]:
    return [
        *opponent_python,
        "-B",
        str(OPPONENT_RUNNER),
        "--websocket-url",
        websocket_url,
        "--authentication-url",
        authentication_url,
        "--username",
        OPPONENT_USERNAME,
        "--fouler-username",
        FOULER_USERNAME,
        "--team-file",
        str(LOCKED_TEAM_FILES[1]),
        "--result-file",
        str(layout.opponent_result),
        "--network-audit-file",
        str(layout.opponent_network_audit),
        "--baseline",
        baseline,
        "--timeout-seconds",
        str(timeout_seconds),
    ]


def build_overlay_command(runtime_python: list[str]) -> list[str]:
    return [*runtime_python, "-B", str(OVERLAY_SERVER), "--offline-rehearsal"]


def showdown_preload_source() -> str:
    return r'''"use strict";
const dns = require("dns");
const fs = require("fs");
const Module = require("module");
const net = require("net");
const path = require("path");

Error.stackTraceLimit = Math.max(Error.stackTraceLimit || 10, 50);

const auditDir = process.env.FOULER_SHOWDOWN_REHEARSAL_AUDIT_DIR;
const showdownDir = process.env.FOULER_SHOWDOWN_REHEARSAL_DIR;
if (!auditDir || !showdownDir) {
  throw new Error("Showdown rehearsal preload requires isolated audit and checkout paths");
}

const audit = {
  schemaVersion: "fouler-play-showdown-loopback-audit/v1",
  pid: process.pid,
  loopbackOnly: true,
  noFilesystemWrites: true,
  configIntercepted: false,
  skipBuildInjected: false,
  blockedExternalAttempts: [],
};
const auditPath = path.join(auditDir, `showdown-network-${process.pid}.json`);

function writeAudit() {
  try {
    fs.mkdirSync(auditDir, {recursive: true});
    audit.blockedExternalAttemptCount = audit.blockedExternalAttempts.length;
    fs.writeFileSync(auditPath, JSON.stringify(audit, null, 2) + "\n", "utf8");
  } catch (_error) {}
}

function isLoopback(host) {
  let value = String(host || "").trim().toLowerCase();
  if (!value) return true;
  if (value.startsWith("[") && value.endsWith("]")) value = value.slice(1, -1);
  if (value.includes("%")) value = value.split("%", 1)[0];
  if (value === "localhost" || value === "::1") return true;
  if (value.startsWith("::ffff:")) {
    const mapped = value.slice("::ffff:".length);
    return net.isIP(mapped) === 4 && mapped.split(".", 1)[0] === "127";
  }
  return net.isIP(value) === 4 && value.split(".", 1)[0] === "127";
}

function blockedError(operation, host) {
  const stack = new Error().stack || "";
  audit.blockedExternalAttempts.push({
    operation,
    host: String(host || ""),
    stack: stack.split("\n").slice(2, 40),
  });
  writeAudit();
  const error = new Error(`offline rehearsal blocked non-loopback ${operation}: ${host}`);
  error.code = "FOULER_OFFLINE_NETWORK_BLOCKED";
  return error;
}

if (path.basename(process.argv[1] || "").toLowerCase() === "pokemon-showdown" && !process.argv.includes("--skip-build")) {
  process.argv.splice(2, 0, "--skip-build");
  audit.skipBuildInjected = true;
}

const rehearsalConfig = {
  autosavereplays: false,
  backdoor: false,
  bindaddress: process.env.PSBINDADDR || "127.0.0.1",
  crashguard: false,
  disablehotpatchall: true,
  foulerofflinerehearsal: true,
  logchallenges: false,
  logchat: false,
  loginserver: "http://127.0.0.1:1/",
  loguserstats: 0,
  nofswriting: true,
  remoteladder: false,
  repl: false,
  reportbattlejoins: false,
  reportbattles: false,
  reportjoins: false,
  reportjoinsperiod: 0,
  watchconfig: false,
};
const configPath = path.resolve(showdownDir, "config", "config.js").toLowerCase();
const originalLoad = Module._load;
Module._load = function rehearsalModuleLoad(request, parent, isMain) {
  let candidate = "";
  try {
    candidate = path.isAbsolute(request)
      ? path.resolve(request)
      : path.resolve(path.dirname((parent && parent.filename) || showdownDir), request);
  } catch (_error) {}
  if (candidate.toLowerCase() === configPath) {
    audit.configIntercepted = true;
    writeAudit();
    return rehearsalConfig;
  }
  return originalLoad.call(this, request, parent, isMain);
};

const originalConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function rehearsalConnect(...args) {
  const first = args[0];
  let host = null;
  if (typeof first === "number") {
    host = typeof args[1] === "string" ? args[1] : "localhost";
  } else if (first && typeof first === "object" && !first.path) {
    host = first.host || first.hostname || "localhost";
  }
  if (host !== null && !isLoopback(host)) throw blockedError("connect", host);
  return originalConnect.apply(this, args);
};

const originalLookup = dns.lookup;
dns.lookup = function rehearsalLookup(host, ...args) {
  if (isLoopback(host)) return originalLookup.call(this, host, ...args);
  const error = blockedError("dns.lookup", host);
  const callback = args.length && typeof args[args.length - 1] === "function" ? args[args.length - 1] : null;
  if (callback) {
    queueMicrotask(() => callback(error));
    return {};
  }
  throw error;
};
if (dns.promises && dns.promises.lookup) {
  const originalPromiseLookup = dns.promises.lookup.bind(dns.promises);
  dns.promises.lookup = function rehearsalPromiseLookup(host, ...args) {
    if (!isLoopback(host)) return Promise.reject(blockedError("dns.promises.lookup", host));
    return originalPromiseLookup(host, ...args);
  };
}

if (typeof global.fetch === "function") {
  const originalFetch = global.fetch.bind(global);
  global.fetch = function rehearsalFetch(input, init) {
    let host = "";
    try {
      host = new URL(typeof input === "string" ? input : input.url).hostname;
    } catch (_error) {}
    if (host && !isLoopback(host)) return Promise.reject(blockedError("fetch", host));
    return originalFetch(input, init);
  };
}

process.on("exit", writeAudit);
writeAudit();
'''


def write_showdown_preload(layout: RehearsalLayout) -> Path:
    layout.showdown_preload.parent.mkdir(parents=True, exist_ok=True)
    layout.showdown_preload.write_text(showdown_preload_source(), encoding="utf-8")
    return layout.showdown_preload


def node_require_option(path: Path | str) -> str:
    return f'--require="{Path(path).resolve(strict=False).as_posix()}"'


def stage_showdown_runtime(
    layout: RehearsalLayout,
    source_dir: Path | str,
    *,
    timeout_seconds: float,
    base_env: Mapping[str, str] | None = None,
) -> Path:
    source = _canonical(source_dir)
    target = layout.showdown_runtime
    if target.exists():
        raise RuntimeError(f"staged Showdown runtime already exists: {target}")

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {
            name
            for name in names
            if name in {".git", "node_modules", "dist", "logs", "databases", "config.js"}
        }
        if _canonical(directory) == source / "config":
            ignored.update(name for name in names if name == "ladders")
        return ignored

    shutil.copytree(source, target, ignore=ignore)
    patch_records = []
    for relative, marker, replacement in (
        (
            Path("server/ip-tools.ts"),
            "\nvoid IPTools.updateTorRanges();\n",
            "\nif (!Config.foulerofflinerehearsal) void IPTools.updateTorRanges();\n",
        ),
        (
            Path("server/chat-plugins/seasons.ts"),
            "\nrollTimer();\n",
            "\nif (!Config.foulerofflinerehearsal) rollTimer();\n",
        ),
    ):
        staged_source = target / relative
        text = staged_source.read_text(encoding="utf-8")
        marker_count = text.count(marker)
        if marker_count != 1:
            raise RuntimeError(
                "private Showdown staging patch drifted: "
                f"{relative.as_posix()} contains the expected marker {marker_count} times"
            )
        before_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        patched = text.replace(marker, replacement, 1)
        staged_source.write_text(patched, encoding="utf-8")
        patch_records.append(
            {
                "file": relative.as_posix(),
                "purpose": "disable upstream background network side effect in offline rehearsal",
                "beforeSha256": before_sha256,
                "afterSha256": hashlib.sha256(patched.encode("utf-8")).hexdigest(),
            }
        )
    patch_proof = layout.proof / "showdown-staging-patches.json"
    patch_proof.parent.mkdir(parents=True, exist_ok=True)
    patch_proof.write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-showdown-staging-patches/v1",
                "source": str(source),
                "target": str(target),
                "patches": patch_records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    config_file = target / "config" / "config.js"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        "module.exports = {\n"
        "  autosavereplays: false,\n"
        "  backdoor: false,\n"
        "  bindaddress: '127.0.0.1',\n"
        "  crashguard: false,\n"
        "  foulerofflinerehearsal: true,\n"
        "  loginserver: 'http://127.0.0.1:1/',\n"
        "  loguserstats: 0,\n"
        "  nofswriting: true,\n"
        "  remoteladder: false,\n"
        "  repl: false,\n"
        "  reportbattles: false,\n"
        "  watchconfig: false,\n"
        "};\n",
        encoding="utf-8",
    )

    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js disappeared after the rehearsal doctor passed")
    build_env = dict(os.environ if base_env is None else base_env)
    build_env.update(
        {
            "NODE_PATH": str(source / "node_modules"),
            "NODE_OPTIONS": node_require_option(layout.showdown_preload),
            "FOULER_SHOWDOWN_REHEARSAL_DIR": str(target),
            "FOULER_SHOWDOWN_REHEARSAL_AUDIT_DIR": str(
                layout.showdown_network_audit_dir
            ),
            "PSBINDADDR": "127.0.0.1",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "ALL_PROXY": "",
            "NO_PROXY": "127.0.0.1,localhost,::1",
        }
    )
    stdout_path = layout.logs / "showdown-build.stdout.log"
    stderr_path = layout.logs / "showdown-build.stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        result = subprocess.run(
            [node, "build"],
            cwd=str(target),
            env=build_env,
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=max(300.0, timeout_seconds),
            check=False,
        )
    if result.returncode != 0:
        stdout_tail = stdout_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise RuntimeError(
            "private Showdown staging build failed: "
            f"returncode={result.returncode}, stdout={stdout_tail!r}, stderr={stderr_tail!r}"
        )
    required = (
        target / "pokemon-showdown",
        target / "package.json",
        target / "dist" / "server" / "index.js",
        target / "dist" / "sim" / "dex.js",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"private Showdown staging build is incomplete: {missing}")
    return target


def _prepare_layout(layout: RehearsalLayout) -> None:
    validate_rehearsal_root(layout.root)
    if layout.root.exists() and any(layout.root.iterdir()):
        raise RuntimeError(f"rehearsal root must be absent or empty: {layout.root}")
    for directory in (
        layout.root,
        layout.state,
        layout.logs,
        layout.cache,
        layout.temp,
        layout.proof,
        layout.showdown_network_audit_dir,
        layout.decision_traces,
        layout.state / "truth",
        layout.state / "pids",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    layout.empty_env.write_text(
        "# Intentionally empty: local rehearsal inherits no production secrets.\n",
        encoding="utf-8",
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_fingerprint(path: Path) -> dict[str, object]:
    path = _canonical(path)
    if not path.exists():
        return {"kind": "missing"}
    if path.is_file():
        stat = path.stat()
        return {
            "kind": "file",
            "size": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "sha256": _file_digest(path),
        }
    entries: dict[str, object] = {}
    for child in sorted(path.rglob("*"), key=lambda item: str(item).casefold()):
        relative = child.relative_to(path).as_posix()
        try:
            if child.is_dir():
                entries[relative] = {"kind": "directory"}
            elif child.is_file():
                stat = child.stat()
                entries[relative] = {
                    "kind": "file",
                    "size": stat.st_size,
                    "mtimeNs": stat.st_mtime_ns,
                    "sha256": _file_digest(child),
                }
        except OSError as exc:
            entries[relative] = {"kind": "error", "error": type(exc).__name__}
    return {"kind": "directory", "entries": entries}


def snapshot_production_paths(
    paths: Iterable[Path] | None = None,
) -> dict[str, dict[str, object]]:
    selected = production_watch_paths() if paths is None else tuple(paths)
    return {str(_canonical(path)): _path_fingerprint(path) for path in selected}


def _read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_showdown_network_audits(layout: RehearsalLayout) -> list[dict]:
    audits: list[dict] = []
    for path in sorted(layout.showdown_network_audit_dir.glob("showdown-network-*.json")):
        payload = _read_json(path, {})
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["auditFile"] = str(path)
            audits.append(payload)
    return audits


def _start_logged_process(
    name: str,
    command: list[str],
    *,
    env: Mapping[str, str],
    layout: RehearsalLayout,
) -> subprocess.Popen:
    stdout_path = layout.logs / f"{name}.stdout.log"
    stderr_path = layout.logs / f"{name}.stderr.log"
    stdout = stdout_path.open("w", encoding="utf-8")
    stderr = stderr_path.open("w", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        return subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=dict(env),
            stdout=stdout,
            stderr=stderr,
            text=True,
            creationflags=creationflags,
        )
    finally:
        stdout.close()
        stderr.close()


def _read_process_log(layout: RehearsalLayout, name: str) -> str:
    parts = []
    for stream in ("stdout", "stderr"):
        path = layout.logs / f"{name}.{stream}.log"
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _http_json(host: str, port: int, path: str, *, timeout: float = 1.0) -> dict:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise OSError(f"HTTP {response.status} from {path}")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"non-object JSON from {path}")
        return payload
    finally:
        connection.close()


def _wait_for_overlay(host: str, port: int, proc: subprocess.Popen, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"overlay exited during startup with code {proc.returncode}")
        try:
            payload = _http_json(host, port, "/state")
            if payload.get("offline_rehearsal") is True:
                return payload
            last_error = "overlay /state did not identify offline rehearsal mode"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"overlay did not become rehearsal-ready: {last_error}")


def _wait_for_fouler_workers(proc: subprocess.Popen, layout: RehearsalLayout, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    marker = f"Starting {LOCKED_CONCURRENCY} battle worker(s)"
    while time.monotonic() < deadline:
        text = _read_process_log(layout, "fouler")
        if marker in text:
            return text
        if proc.poll() is not None:
            raise RuntimeError(f"Fouler exited before workers started with code {proc.returncode}")
        time.sleep(0.2)
    raise RuntimeError(f"Fouler did not log {marker!r}")


def _capture_process_family(proc: subprocess.Popen | None, identities: set[tuple[int, float]]) -> None:
    if proc is None:
        return
    try:
        import psutil

        parent = psutil.Process(proc.pid)
        family = [parent, *parent.children(recursive=True)]
    except Exception:
        family = []
    for process in family:
        try:
            identities.add((process.pid, process.create_time()))
        except Exception:
            pass


def _identity_alive(identity: tuple[int, float]) -> bool:
    pid, created = identity
    try:
        import psutil

        process = psutil.Process(pid)
        return process.is_running() and abs(process.create_time() - created) < 0.01
    except Exception:
        return False


def _listener_rows(port: int) -> list[dict[str, object]]:
    import psutil

    rows: list[dict[str, object]] = []
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status != psutil.CONN_LISTEN or not connection.laddr:
            continue
        if int(connection.laddr.port) != int(port):
            continue
        rows.append(
            {
                "pid": connection.pid,
                "host": connection.laddr.ip,
                "port": connection.laddr.port,
            }
        )
    return rows


def _assert_private_listener(port: int) -> list[dict[str, object]]:
    is_loopback_host, _ = _load_loopback_helpers()
    rows = _listener_rows(port)
    if not rows:
        raise RuntimeError(f"no listener ownership evidence found for Showdown port {port}")
    public = [row for row in rows if not is_loopback_host(row.get("host"))]
    if public:
        raise RuntimeError(f"Showdown listener is not loopback-only: {public}")
    return rows


def _sample_external_connections(
    identities: set[tuple[int, float]],
    findings: set[tuple[int, str, int]],
) -> None:
    is_loopback_host, _ = _load_loopback_helpers()
    try:
        import psutil

        for pid, created in list(identities):
            try:
                process = psutil.Process(pid)
                if abs(process.create_time() - created) >= 0.01:
                    continue
                for connection in process.net_connections(kind="inet"):
                    if not connection.raddr:
                        continue
                    host = str(connection.raddr.ip)
                    if not is_loopback_host(host):
                        findings.add((pid, host, int(connection.raddr.port)))
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
    except Exception:
        return


def _sample_active_file(layout: RehearsalLayout, evidence: dict[str, object]) -> None:
    payload = _read_json(layout.active_battles, {})
    if not isinstance(payload, dict):
        return
    battles = payload.get("battles") if isinstance(payload.get("battles"), list) else []
    count = len(battles)
    evidence["filePeakActiveBattles"] = max(int(evidence["filePeakActiveBattles"]), count)
    ids = sorted(str(row.get("id")) for row in battles if isinstance(row, dict) and row.get("id"))
    evidence["fileObservedBattleIds"].update(ids)
    transition = {"count": count, "battleIds": ids}
    transitions = evidence["activeTransitions"]
    if not transitions or transitions[-1] != transition:
        transitions.append(transition)


def _sample_overlay(
    host: str,
    port: int,
    evidence: dict[str, object],
) -> None:
    payload = _http_json(host, port, "/state", timeout=1.0)
    if payload.get("offline_rehearsal") is not True:
        raise RuntimeError("overlay left offline rehearsal mode")
    battles = payload.get("battles") if isinstance(payload.get("battles"), list) else []
    evidence["overlaySamples"] = int(evidence["overlaySamples"]) + 1
    evidence["overlayPeakActiveBattles"] = max(
        int(evidence["overlayPeakActiveBattles"]),
        len(battles),
    )
    slot_to_id: dict[int, str] = {}
    for row in battles:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        battle_id = str(row["id"])
        evidence["overlayObservedBattleIds"].add(battle_id)
        try:
            slot = int(row.get("slot"))
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= LOCKED_CONCURRENCY:
            slot_to_id[slot] = battle_id
    for slot, battle_id in slot_to_id.items():
        slot_payload = _http_json(host, port, f"/slot/{slot}/state", timeout=1.0)
        battle_lab = slot_payload.get("battle_lab")
        if isinstance(battle_lab, dict) and isinstance(battle_lab.get("battle_view"), dict):
            evidence["overlayDecisionBattleIds"].add(battle_id)
            evidence["overlayDecisionSlots"].add(slot)


def _trace_battle_ids(layout: RehearsalLayout) -> set[str]:
    battle_ids: set[str] = set()
    for path in layout.decision_traces.glob("battle-*_turn*.json"):
        payload = _read_json(path, {})
        if isinstance(payload, dict) and payload.get("battle_tag"):
            battle_ids.add(str(payload["battle_tag"]))
    return battle_ids


def _replay_artifacts(layout: RehearsalLayout) -> list[str]:
    paths = (layout.state / "replay_analysis", layout.root / "replay_analysis")
    artifacts: list[str] = []
    for root in paths:
        if root.is_dir():
            artifacts.extend(str(path) for path in root.rglob("*") if path.is_file())
    return artifacts


def verify_rehearsal(evidence: Mapping[str, object]) -> list[str]:
    blockers: list[str] = []
    stats = evidence.get("battleStats")
    rows = stats.get("battles") if isinstance(stats, dict) else None
    if not isinstance(rows, list):
        blockers.append("battle_stats.json does not contain a battles list")
        rows = []
    if len(rows) != LOCKED_BATTLE_COUNT:
        blockers.append(f"battle total is {len(rows)}, expected {LOCKED_BATTLE_COUNT}")
    results = [str(row.get("result") or "") for row in rows if isinstance(row, dict)]
    if len(results) != len(rows) or any(result not in {"win", "loss"} for result in results):
        blockers.append("Fouler battle rows are not all decisive win/loss records")
    battle_ids = [str(row.get("battle_id") or "") for row in rows if isinstance(row, dict)]
    if any(not battle_id or battle_id == "unknown" for battle_id in battle_ids):
        blockers.append("one or more Fouler battle rows lacks a real battle id")
    if len(set(battle_ids)) != len(battle_ids):
        blockers.append("Fouler battle ids are not unique")
    distribution = Counter(
        str(row.get("team_file") or "") for row in rows if isinstance(row, dict)
    )
    expected_distribution = Counter({name: 10 for name in LOCKED_TEAM_BASENAMES})
    if distribution != expected_distribution:
        blockers.append(
            f"team distribution is {dict(distribution)}, expected {dict(expected_distribution)}"
        )

    opponent = evidence.get("opponentResult")
    if not isinstance(opponent, dict) or opponent.get("ok") is not True:
        blockers.append("private opponent did not report a successful locked ladder run")
    else:
        expected_values = {
            "requestedBattles": LOCKED_BATTLE_COUNT,
            "finishedBattles": LOCKED_BATTLE_COUNT,
            "decisiveBattles": LOCKED_BATTLE_COUNT,
            "ties": 0,
            "maxConcurrentBattles": LOCKED_CONCURRENCY,
            "observedPeakActiveBattles": LOCKED_CONCURRENCY,
        }
        for key, expected in expected_values.items():
            if opponent.get(key) != expected:
                blockers.append(f"opponent {key}={opponent.get(key)!r}, expected {expected!r}")

    for key in ("filePeakActiveBattles", "overlayPeakActiveBattles"):
        if evidence.get(key) != LOCKED_CONCURRENCY:
            blockers.append(f"{key}={evidence.get(key)!r}, expected {LOCKED_CONCURRENCY}")
    if int(evidence.get("overlaySamples") or 0) <= 0:
        blockers.append("overlay rehearsal state was not sampled")
    if set(evidence.get("overlayDecisionSlots") or []) != set(range(1, LOCKED_CONCURRENCY + 1)):
        blockers.append("overlay did not consume a real decision state on all three slots")

    expected_ids = set(battle_ids)
    for key in ("fileObservedBattleIds", "overlayObservedBattleIds", "traceBattleIds"):
        observed = set(evidence.get(key) or [])
        missing = sorted(expected_ids - observed)
        if missing:
            blockers.append(f"{key} missed {len(missing)} completed battle id(s)")

    final_active = evidence.get("finalActiveBattles")
    final_rows = final_active.get("battles") if isinstance(final_active, dict) else None
    if not isinstance(final_rows, list) or final_rows:
        blockers.append("active_battles.json did not end empty")

    for key in ("foulerNetworkAudit", "opponentNetworkAudit"):
        audit = evidence.get(key)
        if not isinstance(audit, dict):
            blockers.append(f"{key} is missing")
        elif audit.get("blockedExternalAttemptCount") != 0:
            blockers.append(f"{key} recorded an external network attempt")
    showdown_audits = evidence.get("showdownNetworkAudits")
    if not isinstance(showdown_audits, list) or not showdown_audits:
        blockers.append("managed Showdown network/no-write audit is missing")
    else:
        if not any(audit.get("configIntercepted") is True for audit in showdown_audits):
            blockers.append("managed Showdown did not prove rehearsal config interception")
        if not any(audit.get("skipBuildInjected") is True for audit in showdown_audits):
            blockers.append("managed Showdown did not prove --skip-build injection")
        for audit in showdown_audits:
            if audit.get("loopbackOnly") is not True or audit.get("noFilesystemWrites") is not True:
                blockers.append("managed Showdown audit did not preserve loopback/no-write mode")
                break
            if audit.get("blockedExternalAttemptCount") != 0:
                blockers.append("managed Showdown attempted a non-loopback network operation")
                break
    fouler_audit = evidence.get("foulerNetworkAudit")
    suppressed = fouler_audit.get("suppressedOfflineOperations") if isinstance(fouler_audit, dict) else {}
    if not isinstance(suppressed, dict):
        suppressed = {}
    for operation in ("battle-chat", "replay-upload-command", "public-elo-probe"):
        if int(suppressed.get(operation) or 0) < LOCKED_BATTLE_COUNT:
            blockers.append(f"offline guard did not prove suppression of {operation} for all battles")

    if evidence.get("externalConnections"):
        blockers.append("an owned rehearsal process opened a non-loopback connection")
    if evidence.get("replayArtifacts"):
        blockers.append("replay artifacts were written despite offline replay suppression")
    if evidence.get("orphanProcessIdentities"):
        blockers.append("one or more rehearsal child processes remained alive")
    if evidence.get("listenersAfterCleanup"):
        blockers.append("a rehearsal listener remained open after cleanup")
    if evidence.get("productionPathChanges"):
        blockers.append("one or more production runtime paths changed during rehearsal")
    if evidence.get("foulerReturnCode") != 0:
        blockers.append(f"Fouler exited with code {evidence.get('foulerReturnCode')!r}")
    if evidence.get("opponentReturnCode") != 0:
        blockers.append(f"opponent exited with code {evidence.get('opponentReturnCode')!r}")

    log_text = str(evidence.get("foulerLogText") or "")
    required_log_markers = (
        "Starting 3 battle worker(s)",
        "Per-worker quotas: [10, 10, 10]",
        *tuple(f"Worker {index} -> {team}" for index, team in enumerate(LOCKED_TEAM_NAMES)),
    )
    for marker in required_log_markers:
        if marker not in log_text:
            blockers.append(f"Fouler worker log is missing {marker!r}")
    return blockers


@contextmanager
def _temporary_environment(updates: Mapping[str, str]):
    previous = {key: os.environ.get(key) for key in updates}
    try:
        os.environ.update({key: str(value) for key, value in updates.items()})
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _terminate(proc: subprocess.Popen | None, *, reason: str) -> dict[str, object]:
    if proc is None:
        return {"reason": reason, "method": "not-started"}
    from infrastructure.offline_eval import _terminate_process_tree

    return _terminate_process_tree(proc, reason=reason)


def _terminate_safely(proc: subprocess.Popen | None, *, reason: str) -> dict[str, object]:
    try:
        return _terminate(proc, reason=reason)
    except Exception as exc:
        detail: dict[str, object] = {
            "reason": reason,
            "method": "termination-error",
            "error": f"{type(exc).__name__}: {exc}",
        }
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
                detail["fallback"] = "kill"
            except Exception as fallback_exc:
                detail["fallbackError"] = f"{type(fallback_exc).__name__}: {fallback_exc}"
        return detail


def _wait_ports_closed(ports: Iterable[tuple[str, int]], timeout: float = 15.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    remaining: list[dict] = []
    while time.monotonic() < deadline:
        remaining = []
        for host, port in ports:
            try:
                with socket.create_connection((host, port), timeout=0.2):
                    remaining.append({"host": host, "port": port})
            except OSError:
                pass
        if not remaining:
            return []
        time.sleep(0.2)
    return remaining


def execute_rehearsal(args: argparse.Namespace, doctor: dict[str, object]) -> int:
    if not doctor.get("ok"):
        raise RuntimeError("doctor failed; execute is refused")
    resolved = doctor.get("resolved") if isinstance(doctor.get("resolved"), dict) else {}
    runtime_python = resolved.get("runtimePython")
    opponent_python = resolved.get("opponentPython")
    if not isinstance(runtime_python, list) or not isinstance(opponent_python, list):
        raise RuntimeError("doctor did not resolve both Python runtimes")
    urls = validate_rehearsal_urls(
        args.websocket_url,
        args.authentication_url,
        args.overlay_url,
    )
    layout = build_layout(args.rehearsal_root)
    _prepare_layout(layout)
    write_showdown_preload(layout)

    showdown_dir = _canonical(args.showdown_dir)
    watched_paths = (*production_watch_paths(), *showdown_watch_paths(showdown_dir))
    production_before = snapshot_production_paths(watched_paths)
    env = build_rehearsal_env(
        layout,
        overlay_port=int(urls["overlayPort"]),
        authentication_url=args.authentication_url,
    )
    staged_showdown_dir = stage_showdown_runtime(
        layout,
        showdown_dir,
        timeout_seconds=args.startup_timeout_seconds,
        base_env=env,
    )
    fouler_command = build_fouler_command(
        runtime_python,
        websocket_url=args.websocket_url,
        search_time_ms=args.search_time_ms,
    )
    opponent_command = build_opponent_command(
        opponent_python,
        layout,
        websocket_url=args.websocket_url,
        authentication_url=args.authentication_url,
        baseline=args.baseline,
        timeout_seconds=args.overall_timeout_seconds,
    )
    overlay_command = build_overlay_command(runtime_python)
    (layout.proof / "plan.json").write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-local-rehearsal-plan/v1",
                "generatedAt": _utc_now(),
                "root": str(layout.root),
                "foulerCommand": fouler_command,
                "opponentCommand": opponent_command,
                "overlayCommand": overlay_command,
                "lockedTeams": list(LOCKED_TEAM_NAMES),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    server_proc: subprocess.Popen | None = None
    overlay_proc: subprocess.Popen | None = None
    fouler_proc: subprocess.Popen | None = None
    opponent_proc: subprocess.Popen | None = None
    identities: set[tuple[int, float]] = set()
    external_connections: set[tuple[int, str, int]] = set()
    terminations: list[dict[str, object]] = []
    execution_error: dict[str, str] | None = None
    evidence: dict[str, object] = {
        "filePeakActiveBattles": 0,
        "overlayPeakActiveBattles": 0,
        "overlaySamples": 0,
        "fileObservedBattleIds": set(),
        "overlayObservedBattleIds": set(),
        "overlayDecisionBattleIds": set(),
        "overlayDecisionSlots": set(),
        "activeTransitions": [],
    }
    showdown_host = str(urls["showdownBindHost"])
    showdown_port = int(urls["showdownPort"])
    overlay_host = str(urls["overlayHost"])
    overlay_port = int(urls["overlayPort"])
    try:
        from infrastructure import offline_eval

        offline_eval.RESULTS_DIR = layout.logs
        with _temporary_environment(
            {
                **env,
                "POKEMON_SHOWDOWN_DIR": str(staged_showdown_dir),
                "PSBINDADDR": showdown_host,
                "NODE_OPTIONS": node_require_option(layout.showdown_preload),
                "NODE_PATH": str(showdown_dir / "node_modules"),
                "FOULER_SHOWDOWN_REHEARSAL_DIR": str(staged_showdown_dir),
                "FOULER_SHOWDOWN_REHEARSAL_AUDIT_DIR": str(
                    layout.showdown_network_audit_dir
                ),
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "ALL_PROXY": "",
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "EVAL_SHOWDOWN_ADOPT_EXISTING": "",
                "EVAL_SHOWDOWN_START_TIMEOUT_SECONDS": str(args.startup_timeout_seconds),
            }
        ):
            server_proc = offline_eval.start_managed_showdown_server(showdown_port)
        if server_proc is None:
            raise RuntimeError("managed Showdown unexpectedly adopted an existing listener")
        _capture_process_family(server_proc, identities)
        evidence["showdownListeners"] = _assert_private_listener(showdown_port)

        overlay_proc = _start_logged_process(
            "overlay",
            overlay_command,
            env=env,
            layout=layout,
        )
        _capture_process_family(overlay_proc, identities)
        evidence["initialOverlayState"] = _wait_for_overlay(
            overlay_host,
            overlay_port,
            overlay_proc,
            args.startup_timeout_seconds,
        )

        fouler_proc = _start_logged_process(
            "fouler",
            fouler_command,
            env=env,
            layout=layout,
        )
        _capture_process_family(fouler_proc, identities)
        _wait_for_fouler_workers(
            fouler_proc,
            layout,
            args.startup_timeout_seconds,
        )

        opponent_proc = _start_logged_process(
            "opponent",
            opponent_command,
            env=env,
            layout=layout,
        )
        _capture_process_family(opponent_proc, identities)

        deadline = time.monotonic() + args.overall_timeout_seconds
        next_overlay_sample = 0.0
        next_connection_sample = 0.0
        while True:
            now = time.monotonic()
            for proc in (server_proc, overlay_proc, fouler_proc, opponent_proc):
                _capture_process_family(proc, identities)
            _sample_active_file(layout, evidence)
            if now >= next_overlay_sample:
                _sample_overlay(overlay_host, overlay_port, evidence)
                next_overlay_sample = now + 0.5
            if now >= next_connection_sample:
                _sample_external_connections(identities, external_connections)
                next_connection_sample = now + 1.0

            if server_proc.poll() is not None:
                raise RuntimeError(f"managed Showdown exited early with code {server_proc.returncode}")
            if overlay_proc.poll() is not None:
                raise RuntimeError(f"overlay exited early with code {overlay_proc.returncode}")
            if fouler_proc.poll() is not None and opponent_proc.poll() is not None:
                break
            if now >= deadline:
                raise TimeoutError("local rehearsal exceeded its overall timeout")
            time.sleep(0.1)
        _sample_active_file(layout, evidence)
        with contextlib.suppress(Exception):
            _sample_overlay(overlay_host, overlay_port, evidence)
    except BaseException as exc:
        execution_error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        for proc in (server_proc, overlay_proc, fouler_proc, opponent_proc):
            _capture_process_family(proc, identities)
        terminations.append(_terminate_safely(opponent_proc, reason="local-rehearsal-cleanup-opponent"))
        terminations.append(_terminate_safely(fouler_proc, reason="local-rehearsal-cleanup-fouler"))
        terminations.append(_terminate_safely(overlay_proc, reason="local-rehearsal-cleanup-overlay"))
        terminations.append(_terminate_safely(server_proc, reason="local-rehearsal-cleanup-showdown"))

    listeners_after = _wait_ports_closed(
        ((showdown_host, showdown_port), (overlay_host, overlay_port))
    )
    orphan_identities = [identity for identity in sorted(identities) if _identity_alive(identity)]
    production_after = snapshot_production_paths(watched_paths)
    production_changes = [
        path
        for path in sorted(set(production_before) | set(production_after))
        if production_before.get(path) != production_after.get(path)
    ]

    evidence.update(
        {
            "battleStats": _read_json(layout.battle_stats, {}),
            "opponentResult": _read_json(layout.opponent_result, {}),
            "foulerNetworkAudit": _read_json(layout.fouler_network_audit, {}),
            "opponentNetworkAudit": _read_json(layout.opponent_network_audit, {}),
            "showdownNetworkAudits": _read_showdown_network_audits(layout),
            "finalActiveBattles": _read_json(layout.active_battles, {}),
            "traceBattleIds": _trace_battle_ids(layout),
            "replayArtifacts": _replay_artifacts(layout),
            "externalConnections": sorted(external_connections),
            "orphanProcessIdentities": orphan_identities,
            "listenersAfterCleanup": listeners_after,
            "productionPathChanges": production_changes,
            "foulerReturnCode": fouler_proc.poll() if fouler_proc else None,
            "opponentReturnCode": opponent_proc.poll() if opponent_proc else None,
            "foulerLogText": _read_process_log(layout, "fouler"),
        }
    )
    blockers = verify_rehearsal(evidence)
    if execution_error:
        blockers.insert(0, f"execution failed: {execution_error['type']}: {execution_error['message']}")

    serializable_evidence = {
        key: sorted(value) if isinstance(value, set) else value
        for key, value in evidence.items()
        if key != "foulerLogText"
    }
    report = {
        "schemaVersion": "fouler-play-local-rehearsal-report/v1",
        "generatedAt": _utc_now(),
        "ok": not blockers,
        "privateLoopbackOnly": True,
        "publicMatchmaking": False,
        "publicChat": False,
        "publicReplayUpload": False,
        "publicEloProbes": False,
        "root": str(layout.root),
        "locked": {
            "battles": LOCKED_BATTLE_COUNT,
            "concurrency": LOCKED_CONCURRENCY,
            "searchParallelism": LOCKED_SEARCH_PARALLELISM,
            "teams": list(LOCKED_TEAM_NAMES),
        },
        "blockers": blockers,
        "executionError": execution_error,
        "evidence": serializable_evidence,
        "terminations": terminations,
        "productionFingerprints": {
            "before": production_before,
            "after": production_after,
        },
    }
    layout.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": report["ok"], "report": str(layout.report), "blockers": blockers}, indent=2))
    return 0 if report["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Doctor and optionally execute the locked 30-battle private Fouler rehearsal. "
            "No service or battle starts without --execute."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--doctor", action="store_true", help="run checks only (the default)")
    mode.add_argument("--execute", action="store_true", help="start the loopback-only rehearsal")
    parser.add_argument("--json", action="store_true", help="print doctor output as JSON")
    parser.add_argument("--rehearsal-root", type=Path, default=_default_root())
    parser.add_argument("--websocket-url", default=DEFAULT_SHOWDOWN_WS_URL)
    parser.add_argument("--authentication-url", default=DEFAULT_SHOWDOWN_AUTH_URL)
    parser.add_argument("--overlay-url", default=DEFAULT_OVERLAY_URL)
    parser.add_argument(
        "--showdown-dir",
        type=Path,
        default=PROJECT_ROOT.parent / "pokemon-showdown",
    )
    parser.add_argument("--runtime-python")
    parser.add_argument("--opponent-python")
    parser.add_argument("--baseline", choices=("simple", "maxbp", "random"), default="simple")
    parser.add_argument("--search-time-ms", type=int, default=100)
    parser.add_argument("--startup-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--overall-timeout-seconds", type=float, default=7200.0)
    args = parser.parse_args(argv)
    if args.search_time_ms <= 0:
        parser.error("--search-time-ms must be positive")
    if args.startup_timeout_seconds <= 0 or args.overall_timeout_seconds <= 0:
        parser.error("timeouts must be positive")
    return args


def _print_doctor(doctor: Mapping[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(doctor, indent=2, sort_keys=True))
        return
    print("Local rehearsal doctor: " + ("READY" if doctor.get("ok") else "BLOCKED"))
    for check in doctor.get("checks", []):
        if not isinstance(check, dict):
            continue
        marker = "OK" if check.get("ok") else "BLOCKED"
        print(f"[{marker}] {check.get('name')}: {check.get('detail')}")
    print("No server, overlay, opponent, or battle process was started.")
    if doctor.get("ok"):
        print("Use --execute with the same arguments to run the locked loopback rehearsal.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    doctor = run_doctor(args)
    if not args.execute:
        _print_doctor(doctor, as_json=args.json)
        return 0 if doctor.get("ok") else 1
    if not doctor.get("ok"):
        _print_doctor(doctor, as_json=args.json)
        return 2
    try:
        return execute_rehearsal(args, doctor)
    except Exception as exc:
        print(f"[local-rehearsal] execute refused or failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
