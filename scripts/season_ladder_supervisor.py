#!/usr/bin/env python3
"""Run one protected finite Fouler season across fixed 30-game boundaries.

This is operational continuity, not autonomous code or team improvement.  It
launches the exact immutable ``run.py`` repeatedly only while the protected
season budget, pause epoch, identity, source, and stop-loss gates remain valid.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psutil
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from infrastructure.season_runtime_authority import (  # noqa: E402
    AUTHORITY_PATH_ENV,
    AUTHORITY_SHA256_ENV,
    SUPERVISOR_CREATE_TIME_ENV,
    SUPERVISOR_NONCE_ENV,
    SUPERVISOR_PID_ENV,
    validate_season_authority,
)

LOG = logging.getLogger("fouler-season-supervisor")
STATE_SCHEMA = "fouler-play-season-state/v1"
CONTROL_SCHEMA = "devstream-runtime-control/v1"
SHUTDOWN_REQUESTED = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def configure_logging(log_root: Path) -> None:
    log_root.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
    formatter.converter = time.gmtime
    file_handler = logging.handlers.RotatingFileHandler(
        log_root / "season_supervisor.log",
        maxBytes=8 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    LOG.setLevel(logging.INFO)
    LOG.addHandler(file_handler)
    LOG.addHandler(stream_handler)
    LOG.propagate = False


def _signal_handler(signum: int, _frame: object) -> None:
    global SHUTDOWN_REQUESTED
    SHUTDOWN_REQUESTED = True
    LOG.warning("shutdown requested by signal %s; draining the active round", signum)


def _claim_lock(lock_path: Path) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "createTime": psutil.Process(os.getpid()).create_time(),
        "releaseRoot": str(ROOT),
        "startedAt": utc_now(),
    }
    while True:
        try:
            descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\n").encode())
            os.fsync(descriptor)
            return descriptor
        except FileExistsError:
            try:
                current = read_json(lock_path)
                process = psutil.Process(int(current.get("pid") or 0))
                same_creation = abs(
                    process.create_time() - float(current.get("createTime") or 0)
                ) <= 2.0
                same_command = any(
                    Path(part).name.lower() == "season_ladder_supervisor.py"
                    for part in process.cmdline()
                )
                same_root = Path(process.cwd()).resolve() == ROOT.resolve()
                if same_creation and same_command and same_root:
                    raise RuntimeError(
                        f"season supervisor is already active as PID {process.pid}"
                    )
            except (OSError, ValueError, TypeError, json.JSONDecodeError, psutil.Error):
                pass
            lock_path.unlink(missing_ok=True)


def _release_lock(lock_path: Path, descriptor: int | None) -> None:
    if descriptor is not None:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        current = read_json(lock_path)
        if int(current.get("pid") or 0) == os.getpid():
            lock_path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass


def _authority(
    path: Path,
    digest: str,
    *,
    require_existing_paths: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    guard = validate_season_authority(
        authority_path=path,
        expected_sha256=digest,
        release_root=ROOT,
        require_existing_paths=require_existing_paths,
    )
    if not guard.get("ok"):
        blockers = "; ".join(str(item) for item in guard.get("blockers") or [])
        raise RuntimeError(f"finite season authority rejected: {blockers}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise TypeError("finite season authority is not an object")
    return payload, guard


def _control_decision(payload: dict[str, Any]) -> tuple[bool, str]:
    runtime = payload["runtime"]
    path = Path(runtime["controlPath"])
    try:
        control = read_json(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, f"protected runtime control is unavailable: {exc}"
    if not isinstance(control, dict) or control.get("schemaVersion") != CONTROL_SCHEMA:
        return False, "protected runtime control schema is invalid"
    try:
        control_epoch = int(control.get("pauseEpoch"))
        authority_epoch = int(payload.get("pauseEpoch"))
    except (TypeError, ValueError):
        return False, "protected runtime control pauseEpoch is invalid"
    if control_epoch != authority_epoch:
        return False, "protected runtime control pauseEpoch does not match the season"
    state = str(control.get("state") or "").strip().upper()
    if state != "RUNNING":
        return False, f"protected runtime control state is {state or 'missing'}"
    return True, "running"


def _state_path(payload: dict[str, Any]) -> Path:
    return Path(payload["runtime"]["stateRoot"]) / "seasons" / (
        str(payload["seasonId"]) + ".json"
    )


def _load_or_initialize_state(payload: dict[str, Any], authority_sha256: str) -> dict[str, Any]:
    path = _state_path(payload)
    if path.exists():
        state = read_json(path)
        if not isinstance(state, dict):
            raise RuntimeError("season state must be a JSON object")
        expected = {
            "schemaVersion": STATE_SCHEMA,
            "seasonId": payload["seasonId"],
            "sourceCommit": payload["sourceCommit"],
            "authoritySha256": authority_sha256,
            "pauseEpoch": payload["pauseEpoch"],
        }
        mismatches = [
            name
            for name, value in expected.items()
            if state.get(name) != value
        ]
        if mismatches:
            raise RuntimeError(
                "season state identity mismatch: " + ", ".join(mismatches)
            )
        return state
    state = {
        "schemaVersion": STATE_SCHEMA,
        "seasonId": payload["seasonId"],
        "sourceCommit": payload["sourceCommit"],
        "authoritySha256": authority_sha256,
        "pauseEpoch": payload["pauseEpoch"],
        "roundsCompleted": 0,
        "gamesCompleted": 0,
        "startedAt": utc_now(),
        "updatedAt": utc_now(),
        "status": "ready",
        "lastRound": None,
        "lastRatingProof": None,
        "blockers": [],
    }
    atomic_write_json(path, state)
    return state


def _battle_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = read_json(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []
    rows = payload.get("battles") if isinstance(payload, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def _round_rows(
    battle_stats_path: Path,
    session_id: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows = [
        row
        for row in _battle_rows(battle_stats_path)
        if str(row.get("session_id") or "") == session_id
    ]
    identities: dict[str, int] = {}
    blockers: list[str] = []
    for row in rows:
        battle_id = str(row.get("battle_id") or "").strip().lower()
        result = str(row.get("result") or "").strip().lower()
        if not battle_id or battle_id == "unknown":
            blockers.append("round contains a missing battle identity")
        else:
            identities[battle_id] = identities.get(battle_id, 0) + 1
        if result not in {"win", "loss"}:
            blockers.append(f"battle {battle_id or 'unknown'} has an invalid result")
    duplicates = sorted(key for key, count in identities.items() if count > 1)
    if duplicates:
        blockers.append(
            "round contains duplicate battle identities: " + ", ".join(duplicates[:5])
        )
    return rows, blockers


def _public_rating(account: str, pokemon_format: str) -> dict[str, Any]:
    userid = "".join(char.lower() for char in account if char.isalnum())
    request = Request(
        f"https://pokemonshowdown.com/users/{userid}.json",
        headers={"User-Agent": "fouler-play-season-supervisor/1"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, HTTPError, URLError, ValueError, TypeError) as exc:
        return {"ok": False, "blocker": f"public rating source unavailable: {exc}"}
    if not isinstance(payload, dict) or not payload.get("registered"):
        return {"ok": False, "blocker": "public rating identity is not registered"}
    if str(payload.get("userid") or "").strip().lower() != userid:
        return {"ok": False, "blocker": "public rating identity does not match account"}
    ratings = payload.get("ratings") if isinstance(payload.get("ratings"), dict) else {}
    rating = ratings.get(pokemon_format) if isinstance(ratings.get(pokemon_format), dict) else {}
    try:
        value = float(rating.get("rpr") or rating.get("elo"))
    except (TypeError, ValueError):
        return {"ok": False, "blocker": "public rating value is unavailable for format"}
    return {
        "ok": True,
        "account": account,
        "userid": userid,
        "format": pokemon_format,
        "rating": value,
        "observedAt": utc_now(),
    }


def _rating_drawdown(rows: list[dict[str, Any]], window: int) -> dict[str, Any]:
    ratings: list[float] = []
    for row in rows[-window:]:
        try:
            ratings.append(float(row.get("rating")))
        except (TypeError, ValueError):
            continue
    peak: float | None = None
    max_drawdown = 0.0
    for value in ratings:
        peak = value if peak is None else max(peak, value)
        max_drawdown = max(max_drawdown, peak - value)
    return {
        "window": window,
        "ratedBattles": len(ratings),
        "maxDrawdown": max_drawdown,
        "firstRating": ratings[0] if ratings else None,
        "lastRating": ratings[-1] if ratings else None,
    }


def _secret_environment(secret_path: Path) -> dict[str, str]:
    values = dotenv_values(secret_path)
    return {
        str(name): str(value)
        for name, value in values.items()
        if name and value is not None
    }


def _child_environment(
    payload: dict[str, Any],
    *,
    authority_path: Path,
    authority_sha256: str,
    session_id: str,
    nonce: str,
) -> dict[str, str]:
    runtime = payload["runtime"]
    environment = dict(os.environ)
    environment.update(_secret_environment(Path(runtime["secretEnvFile"])))
    for name in list(environment):
        if (
            name.startswith("FOULER_RUNTIME_LEASE_")
            or name.startswith("FOULER_IMPROVE_")
        ):
            environment.pop(name, None)
    for name in (
        "FOULER_RUNTIME_LEASE_PATH",
        "FOULER_RUNTIME_LEASE_ID",
        "FOULER_RUNTIME_AUTHORIZATION_SHA256",
        "DISCORD_BATTLES_WEBHOOK_URL",
        "FOULER_AUTO_IMPROVE_MAX_CYCLES",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "FOULER_RUNTIME_PRODUCTION": "1",
            "FOULER_RUNTIME_STATE_ROOT": str(runtime["stateRoot"]),
            "FOULER_RUNTIME_LOG_ROOT": str(runtime["logRoot"]),
            "FOULER_RUNTIME_CACHE_ROOT": str(runtime["cacheRoot"]),
            "FOULER_RUNTIME_TEMP_ROOT": str(runtime["tempRoot"]),
            "FOULER_ENV_FILE": str(runtime["secretEnvFile"]),
            "FOULER_ACCOUNT_SEASON_PATH": str(runtime["accountSeasonPath"]),
            "DEKU_EVENT_QUEUE_ROOT": str(runtime["eventQueueRoot"]),
            "FOULER_BATTLE_RESULT_QUEUE": "1",
            "FOULER_PLAY_ENABLE_AUTO_IMPROVE": "0",
            "FOULER_PLAY_ENABLE_AUTO_PUSH": "0",
            "AUTO_START_OBS_SERVER": "0",
            "FOULER_SOURCE_COMMIT": str(payload["sourceCommit"]),
            "FOULER_SESSION_ID": session_id,
            "FOULER_PLAY_CYCLE_ID": session_id,
            AUTHORITY_PATH_ENV: str(authority_path),
            AUTHORITY_SHA256_ENV: authority_sha256,
            SUPERVISOR_PID_ENV: str(os.getpid()),
            SUPERVISOR_CREATE_TIME_ENV: str(
                psutil.Process(os.getpid()).create_time()
            ),
            SUPERVISOR_NONCE_ENV: nonce,
        }
    )
    return environment


def _runner_command(payload: dict[str, Any]) -> list[str]:
    battle = payload["battleScope"]
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError(f"immutable release Python is missing: {python}")
    return [
        str(python),
        "-u",
        str(ROOT / "run.py"),
        "--websocket-uri",
        str(battle["websocketUri"]),
        "--ps-username",
        str(payload["account"]),
        "--bot-mode",
        str(battle["botMode"]),
        "--pokemon-format",
        str(battle["pokemonFormat"]),
        "--team-name",
        str(battle["teamName"]),
        "--run-count",
        str(payload["limits"]["roundSize"]),
        "--max-concurrent-battles",
        str(battle["maxConcurrentBattles"]),
        "--search-parallelism",
        str(battle["searchParallelism"]),
        "--save-replay",
        str(battle["replayBehavior"]),
        "--log-to-file",
    ]


def _drain_child(child: subprocess.Popen[bytes], state_root: Path, reason: str) -> None:
    drain = state_root / "pids" / "drain.request"
    drain.parent.mkdir(parents=True, exist_ok=True)
    try:
        drain.write_text(reason + "\n", encoding="utf-8")
    except OSError as exc:
        LOG.error("failed to write drain request: %s", exc)
    deadline = time.monotonic() + 900
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(2)
    if child.poll() is None:
        LOG.error("child did not drain within 900 seconds; leaving it intact")


def run_season(authority_path: Path, authority_sha256: str) -> int:
    payload, _guard = _authority(
        authority_path,
        authority_sha256,
        require_existing_paths=True,
    )
    runtime = payload["runtime"]
    state_root = Path(runtime["stateRoot"])
    configure_logging(Path(runtime["logRoot"]))
    lock_path = state_root / "pids" / "season-supervisor.lock"
    descriptor: int | None = None
    nonce = uuid.uuid4().hex + uuid.uuid4().hex
    state_path = _state_path(payload)
    try:
        descriptor = _claim_lock(lock_path)
        state = _load_or_initialize_state(payload, authority_sha256)
        LOG.info(
            "admitted finite season %s at commit %s (%s/%s games consumed)",
            payload["seasonId"],
            payload["sourceCommit"],
            state["gamesCompleted"],
            payload["limits"]["maxGames"],
        )
        while True:
            payload, _guard = _authority(
                authority_path,
                authority_sha256,
                require_existing_paths=True,
            )
            state = _load_or_initialize_state(payload, authority_sha256)
            running, control_reason = _control_decision(payload)
            if not running or SHUTDOWN_REQUESTED:
                state.update(
                    {
                        "status": "paused",
                        "updatedAt": utc_now(),
                        "blockers": [
                            "shutdown requested" if SHUTDOWN_REQUESTED else control_reason
                        ],
                    }
                )
                atomic_write_json(state_path, state)
                return 0

            limits = payload["limits"]
            if (
                int(state["roundsCompleted"]) >= int(limits["maxRounds"])
                or int(state["gamesCompleted"]) >= int(limits["maxGames"])
            ):
                state.update(
                    {
                        "status": "complete",
                        "completedAt": utc_now(),
                        "updatedAt": utc_now(),
                        "blockers": [],
                    }
                )
                atomic_write_json(state_path, state)
                LOG.info("finite season budget completed exactly; no further round authorized")
                return 0

            round_number = int(state["roundsCompleted"]) + 1
            round_size = int(limits["roundSize"])
            remaining = int(limits["maxGames"]) - int(state["gamesCompleted"])
            if remaining < round_size:
                raise RuntimeError("remaining season game budget is smaller than one fixed round")
            session_id = f"{payload['seasonId']}-round-{round_number:02d}"
            child_env = _child_environment(
                payload,
                authority_path=authority_path,
                authority_sha256=authority_sha256,
                session_id=session_id,
                nonce=nonce,
            )
            log_path = Path(runtime["logRoot"]) / f"{session_id}.log"
            state.update(
                {
                    "status": "running",
                    "updatedAt": utc_now(),
                    "activeRound": round_number,
                    "activeSessionId": session_id,
                    "blockers": [],
                }
            )
            atomic_write_json(state_path, state)
            LOG.info("starting authorized round %s/%s", round_number, limits["maxRounds"])
            with log_path.open("ab", buffering=0) as log_handle:
                child = subprocess.Popen(
                    _runner_command(payload),
                    cwd=ROOT,
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                while child.poll() is None:
                    current_payload, _ = _authority(
                        authority_path,
                        authority_sha256,
                        require_existing_paths=True,
                    )
                    still_running, reason = _control_decision(current_payload)
                    if SHUTDOWN_REQUESTED or not still_running:
                        _drain_child(
                            child,
                            state_root,
                            "supervisor shutdown"
                            if SHUTDOWN_REQUESTED
                            else reason,
                        )
                        break
                    time.sleep(5)
                child_exit = child.poll()
            if child_exit is None:
                state.update(
                    {
                        "status": "quiescing",
                        "updatedAt": utc_now(),
                        "blockers": ["active battle child did not complete its drain"],
                    }
                )
                atomic_write_json(state_path, state)
                return 3

            battle_stats_path = state_root / "battle_stats.json"
            round_rows, integrity_blockers = _round_rows(
                battle_stats_path,
                session_id,
            )
            if child_exit != 0:
                integrity_blockers.append(f"battle child exited with code {child_exit}")
            if len(round_rows) != round_size:
                integrity_blockers.append(
                    f"round produced {len(round_rows)} unique result rows; expected {round_size}"
                )
            if integrity_blockers:
                state.update(
                    {
                        "status": "blocked",
                        "updatedAt": utc_now(),
                        "lastRound": {
                            "round": round_number,
                            "sessionId": session_id,
                            "childExitCode": child_exit,
                            "resultRows": len(round_rows),
                            "finishedAt": utc_now(),
                        },
                        "blockers": integrity_blockers,
                    }
                )
                atomic_write_json(state_path, state)
                LOG.error("round failed closed: %s", "; ".join(integrity_blockers))
                return 4

            all_season_rows = [
                row
                for row in _battle_rows(battle_stats_path)
                if str(row.get("session_id") or "").startswith(
                    str(payload["seasonId"]) + "-round-"
                )
            ]
            stop_loss = payload.get("stopLoss") or {}
            drawdown = _rating_drawdown(
                all_season_rows,
                int(stop_loss.get("ratingWindow") or 60),
            )
            rating_proof = _public_rating(
                str(payload["account"]),
                str(payload["battleScope"]["pokemonFormat"]),
            )
            boundary_blockers: list[str] = []
            if not rating_proof.get("ok"):
                boundary_blockers.append(str(rating_proof.get("blocker")))
            try:
                max_drawdown = float(stop_loss.get("maxRatingDrawdown"))
            except (TypeError, ValueError):
                max_drawdown = -1.0
            if max_drawdown <= 0:
                boundary_blockers.append("stop-loss maxRatingDrawdown is invalid")
            elif float(drawdown["maxDrawdown"]) >= max_drawdown:
                boundary_blockers.append(
                    "rating drawdown stop-loss breached: "
                    f"{drawdown['maxDrawdown']:.1f} >= {max_drawdown:.1f}"
                )

            state["roundsCompleted"] = round_number
            state["gamesCompleted"] = int(state["gamesCompleted"]) + round_size
            state["lastRound"] = {
                "round": round_number,
                "sessionId": session_id,
                "childExitCode": child_exit,
                "resultRows": len(round_rows),
                "wins": sum(1 for row in round_rows if row.get("result") == "win"),
                "losses": sum(1 for row in round_rows if row.get("result") == "loss"),
                "finishedAt": utc_now(),
            }
            state["lastRatingProof"] = rating_proof
            state["ratingDrawdown"] = drawdown
            state["updatedAt"] = utc_now()
            state["activeRound"] = None
            state["activeSessionId"] = None
            if boundary_blockers:
                state["status"] = "blocked"
                state["blockers"] = boundary_blockers
                atomic_write_json(state_path, state)
                LOG.error("season boundary blocked: %s", "; ".join(boundary_blockers))
                return 5
            state["status"] = "boundary-clear"
            state["blockers"] = []
            atomic_write_json(state_path, state)
            LOG.info(
                "round %s complete with %s results; boundary gates clear",
                round_number,
                len(round_rows),
            )
    finally:
        _release_lock(lock_path, descriptor)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--authority-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.authority.is_absolute():
        raise SystemExit("--authority must be an absolute path")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", args.authority_sha256):
        raise SystemExit("--authority-sha256 must be exactly 64 hexadecimal characters")
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        value = getattr(signal, name, None)
        if value is not None:
            signal.signal(value, _signal_handler)
    try:
        return run_season(
            args.authority.resolve(strict=False),
            args.authority_sha256.lower(),
        )
    except Exception:
        if not LOG.handlers:
            logging.basicConfig(level=logging.ERROR)
        LOG.exception("finite season supervisor failed closed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
