"""Fail-closed authority for one finite autonomous Fouler ladder season.

The season authority is intentionally independent from HERMES.  An
administrator installs one protected JSON document beside the mutable runtime
state and passes its exact SHA-256 to the immutable supervisor.  The document
authorizes a bounded number of fixed-size rounds; it never authorizes source
changes, team redesign, or an unbounded ladder loop.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil

from infrastructure.runtime_paths import paths_overlap

SCHEMA_VERSION = "fouler-play-season-authority/v1"
PROJECT_ID = "fouler-play"
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SEASON_ID_RE = re.compile(r"^season-[0-9a-z][0-9a-z.-]{7,95}$")
NONCE_RE = re.compile(r"^[0-9a-f]{64}$")

AUTHORITY_PATH_ENV = "FOULER_SEASON_AUTHORITY_PATH"
AUTHORITY_SHA256_ENV = "FOULER_SEASON_AUTHORITY_SHA256"
SUPERVISOR_PID_ENV = "FOULER_SEASON_SUPERVISOR_PID"
SUPERVISOR_CREATE_TIME_ENV = "FOULER_SEASON_SUPERVISOR_CREATE_TIME"
SUPERVISOR_NONCE_ENV = "FOULER_SEASON_SUPERVISOR_NONCE"


def _canonical(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"path must be absolute: {candidate}")
    return candidate.resolve(strict=False)


def _normalized_identity(value: object) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def _enum_text(value: object) -> str:
    raw = getattr(value, "name", value)
    text = str(raw or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_json(path: Path, blockers: list[str]) -> dict[str, Any] | None:
    try:
        if path.is_symlink():
            blockers.append("season authority must not be a symlink")
            return None
        details = path.stat()
        if not stat.S_ISREG(details.st_mode):
            blockers.append("season authority is not a regular file")
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError) as exc:
        blockers.append(f"season authority is unavailable or invalid: {exc}")
        return None
    if not isinstance(payload, dict):
        blockers.append("season authority must be a JSON object")
        return None
    return payload


def _git_head(release_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=release_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip().lower() if result.returncode == 0 else ""


def _tracked_git_status(release_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=no"],
            cwd=release_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _validate_paths(
    payload: dict[str, Any],
    *,
    release_root: Path,
    require_existing: bool,
    blockers: list[str],
) -> dict[str, str]:
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        blockers.append("season authority runtime must be an object")
        return {}
    required = (
        "stateRoot",
        "logRoot",
        "cacheRoot",
        "tempRoot",
        "secretEnvFile",
        "accountSeasonPath",
        "eventQueueRoot",
        "controlPath",
    )
    result: dict[str, str] = {}
    for name in required:
        raw = str(runtime.get(name) or "").strip()
        try:
            path = _canonical(raw)
        except (OSError, ValueError) as exc:
            blockers.append(f"runtime.{name} is invalid: {exc}")
            continue
        if paths_overlap(path, release_root):
            blockers.append(f"runtime.{name} overlaps the immutable release")
        if require_existing and name.endswith("Root") and not path.is_dir():
            blockers.append(f"runtime.{name} directory is missing")
        if require_existing and name in {
            "secretEnvFile",
            "accountSeasonPath",
            "controlPath",
        } and not path.is_file():
            blockers.append(f"runtime.{name} file is missing")
        result[name] = str(path)
    return result


def _validate_child_binding(
    *,
    release_root: Path,
    environ: Mapping[str, str],
    blockers: list[str],
) -> dict[str, object]:
    try:
        expected_pid = int(str(environ.get(SUPERVISOR_PID_ENV) or "").strip())
    except ValueError:
        expected_pid = 0
    try:
        expected_create_time = float(
            str(environ.get(SUPERVISOR_CREATE_TIME_ENV) or "").strip()
        )
    except ValueError:
        expected_create_time = 0.0
    nonce = str(environ.get(SUPERVISOR_NONCE_ENV) or "").strip().lower()
    actual_parent = os.getppid()
    if expected_pid <= 0 or actual_parent != expected_pid:
        blockers.append("battle child is not bound to the authorized supervisor PID")
    if expected_create_time <= 0:
        blockers.append("authorized supervisor creation time is missing")
    if not NONCE_RE.fullmatch(nonce):
        blockers.append("authorized supervisor nonce is missing or malformed")

    parent_command: list[str] = []
    parent_cwd = ""
    actual_create_time = 0.0
    if expected_pid > 0:
        try:
            parent = psutil.Process(expected_pid)
            actual_create_time = float(parent.create_time())
            parent_command = [str(part) for part in parent.cmdline()]
            parent_cwd = str(parent.cwd() or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            blockers.append("authorized supervisor process cannot be inspected")
    if (
        expected_create_time > 0
        and actual_create_time > 0
        and abs(actual_create_time - expected_create_time) > 2.0
    ):
        blockers.append("authorized supervisor process creation time does not match")
    if not any(
        Path(part).name.lower() == "season_ladder_supervisor.py"
        for part in parent_command
    ):
        blockers.append("authorized parent is not the season ladder supervisor")
    try:
        if _canonical(parent_cwd) != release_root:
            blockers.append("authorized supervisor working directory is not the immutable release")
    except (OSError, ValueError):
        blockers.append("authorized supervisor working directory is unavailable")

    return {
        "supervisorPid": expected_pid or None,
        "supervisorCreateTime": expected_create_time or None,
        "supervisorNoncePresent": bool(NONCE_RE.fullmatch(nonce)),
    }


def validate_season_authority(
    *,
    authority_path: str | os.PathLike[str] | None,
    expected_sha256: str | None,
    release_root: str | os.PathLike[str],
    requested_account: object | None = None,
    requested_bot_mode: object | None = None,
    requested_websocket_uri: object | None = None,
    requested_pokemon_format: object | None = None,
    requested_team_name: object | None = None,
    requested_run_count: int | None = None,
    requested_max_concurrent_battles: int | None = None,
    requested_search_parallelism: int | None = None,
    requested_replay_behavior: object | None = None,
    require_child_binding: bool = False,
    require_existing_paths: bool = False,
    environ: Mapping[str, str] | None = None,
    hostname: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate one protected finite-season authority document.

    The return shape intentionally mirrors the existing lease guard enough for
    ``process_lock`` to fail closed without giving the finite lease broker a
    false reservation.
    """

    environment = os.environ if environ is None else environ
    blockers: list[str] = []
    try:
        root = _canonical(release_root)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "valid": False,
            "authorityType": "finite-season",
            "blockers": [f"immutable release root is invalid: {exc}"],
        }
    try:
        path = _canonical(str(authority_path or ""))
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "valid": False,
            "authorityType": "finite-season",
            "blockers": [f"season authority path is invalid: {exc}"],
        }
    expected_digest = str(expected_sha256 or "").strip().lower()
    if not SHA256_RE.fullmatch(expected_digest):
        blockers.append("season authority expected SHA-256 is missing or malformed")
    payload = _regular_json(path, blockers)
    actual_digest = ""
    if payload is not None:
        try:
            actual_digest = file_sha256(path)
        except OSError as exc:
            blockers.append(f"season authority SHA-256 cannot be computed: {exc}")
    if expected_digest and actual_digest != expected_digest:
        blockers.append("season authority file SHA-256 does not match the pinned digest")

    public: dict[str, Any] = {}
    if payload is not None:
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            blockers.append("season authority schemaVersion is unsupported")
        if payload.get("projectId") != PROJECT_ID:
            blockers.append("season authority projectId is not fouler-play")
        if payload.get("active") is not True:
            blockers.append("season authority is not active")
        season_id = str(payload.get("seasonId") or "").strip().lower()
        if not SEASON_ID_RE.fullmatch(season_id):
            blockers.append("season authority seasonId is missing or malformed")
        generation = _positive_int(payload.get("generation"))
        if generation is None:
            blockers.append("season authority generation must be positive")
        try:
            pause_epoch = int(payload.get("pauseEpoch"))
        except (TypeError, ValueError):
            pause_epoch = -1
        if pause_epoch < 0:
            blockers.append("season authority pauseEpoch must be non-negative")

        source_commit = str(payload.get("sourceCommit") or "").strip().lower()
        if not GIT_COMMIT_RE.fullmatch(source_commit):
            blockers.append("season authority sourceCommit is malformed")
        configured_release = str(payload.get("releaseRoot") or "").strip()
        try:
            if _canonical(configured_release) != root:
                blockers.append("season authority releaseRoot does not match this release")
        except (OSError, ValueError):
            blockers.append("season authority releaseRoot is invalid")
        if _git_head(root) != source_commit:
            blockers.append("immutable release HEAD does not match season authority sourceCommit")
        tracked_status = _tracked_git_status(root)
        if tracked_status is None:
            blockers.append("immutable release tracked status is unavailable")
        elif tracked_status:
            blockers.append("immutable release contains tracked modifications")

        manifest_path_raw = str(payload.get("releaseManifestPath") or "").strip()
        manifest_digest = str(
            payload.get("releaseManifestSha256") or ""
        ).strip().lower()
        try:
            manifest_path = _canonical(manifest_path_raw)
        except (OSError, ValueError):
            manifest_path = None
            blockers.append("season authority releaseManifestPath is invalid")
        if not SHA256_RE.fullmatch(manifest_digest):
            blockers.append("season authority releaseManifestSha256 is malformed")
        if manifest_path is not None:
            manifest_blockers: list[str] = []
            manifest = _regular_json(manifest_path, manifest_blockers)
            blockers.extend(
                f"release manifest: {item}" for item in manifest_blockers
            )
            if manifest is not None:
                try:
                    actual_manifest_digest = file_sha256(manifest_path)
                except OSError:
                    actual_manifest_digest = ""
                    blockers.append("release manifest SHA-256 cannot be computed")
                if actual_manifest_digest != manifest_digest:
                    blockers.append("release manifest SHA-256 does not match authority")
                if manifest.get("schemaVersion") != "fouler-bootstrap-manifest/v1":
                    blockers.append("release manifest schema is unsupported")
                if manifest.get("projectId") != PROJECT_ID:
                    blockers.append("release manifest projectId does not match")
                if str(manifest.get("sourceCommit") or "").lower() != source_commit:
                    blockers.append("release manifest sourceCommit does not match authority")

        machine = str(payload.get("machine") or "").strip()
        current_host = hostname or socket.gethostname()
        if _normalized_identity(machine) != _normalized_identity(current_host):
            blockers.append("season authority machine does not match this host")
        account = str(payload.get("account") or "").strip()
        if _normalized_identity(account) != _normalized_identity("DekuFoulerFresh"):
            blockers.append(
                "season authority account must equal the owner-locked DekuFoulerFresh identity"
            )
        if requested_account is not None and (
            _normalized_identity(account) != _normalized_identity(requested_account)
        ):
            blockers.append("season authority account does not match the battle runner")

        proof_window = payload.get("proofWindow")
        if not isinstance(proof_window, dict):
            blockers.append("season authority proofWindow must be an object")
            starts_at = expires_at = None
        else:
            starts_at = _parse_utc(proof_window.get("startsAt"))
            expires_at = _parse_utc(proof_window.get("expiresAt"))
            if starts_at is None or expires_at is None or starts_at >= expires_at:
                blockers.append("season authority proofWindow is invalid")
            elif expires_at - starts_at > timedelta(hours=72):
                blockers.append("season authority proofWindow exceeds the 72-hour cap")
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if starts_at is not None and current_time < starts_at:
            blockers.append("season authority proof window has not started")
        if expires_at is not None and current_time >= expires_at:
            blockers.append("season authority proof window is expired")

        limits = payload.get("limits")
        if not isinstance(limits, dict):
            blockers.append("season authority limits must be an object")
            round_size = max_rounds = max_games = None
        else:
            round_size = _positive_int(limits.get("roundSize"))
            max_rounds = _positive_int(limits.get("maxRounds"))
            max_games = _positive_int(limits.get("maxGames"))
            if round_size is None or max_rounds is None or max_games is None:
                blockers.append("season authority limits must be positive")
            elif round_size * max_rounds != max_games:
                blockers.append("season maxGames must equal roundSize multiplied by maxRounds")
            if round_size != 30:
                blockers.append("season roundSize must equal the owner-locked value 30")
            if max_rounds is not None and max_rounds > 4:
                blockers.append("season maxRounds exceeds the owner-approved cap 4")
            if max_games is not None and max_games > 120:
                blockers.append("season maxGames exceeds the owner-approved cap 120")
            if requested_run_count is not None and requested_run_count != round_size:
                blockers.append("battle runner run count does not match the season round size")

        battle = payload.get("battleScope")
        if not isinstance(battle, dict):
            blockers.append("season authority battleScope must be an object")
            battle = {}
        comparisons = (
            ("botMode", _enum_text(requested_bot_mode)),
            ("websocketUri", str(requested_websocket_uri or "").strip()),
            ("pokemonFormat", str(requested_pokemon_format or "").strip()),
            ("teamName", str(requested_team_name or "").strip()),
            ("replayBehavior", _enum_text(requested_replay_behavior)),
        )
        for field, requested in comparisons:
            approved = str(battle.get(field) or "").strip()
            if field in {"botMode", "replayBehavior"}:
                approved = _enum_text(approved)
            if requested and approved != requested:
                blockers.append(f"season authority battleScope.{field} does not match the runner")
        locked_battle_scope = {
            "botMode": "search_ladder",
            "websocketUri": "wss://sim3.psim.us/showdown/websocket",
            "pokemonFormat": "gen9ou",
            "teamName": "gen9/ou/fat-team-2-balance",
            "replayBehavior": "always",
        }
        for field, expected in locked_battle_scope.items():
            actual = str(battle.get(field) or "").strip()
            if field in {"botMode", "replayBehavior"}:
                actual = _enum_text(actual)
            if actual != expected:
                blockers.append(
                    f"season authority battleScope.{field} must equal the owner-locked value"
                )
        approved_concurrency = _positive_int(battle.get("maxConcurrentBattles"))
        approved_parallelism = _positive_int(battle.get("searchParallelism"))
        if approved_concurrency != 3:
            blockers.append("season maxConcurrentBattles must equal the owner-locked value 3")
        if approved_parallelism != 2:
            blockers.append("season searchParallelism must equal the owner-locked value 2")
        if (
            requested_max_concurrent_battles is not None
            and requested_max_concurrent_battles != approved_concurrency
        ):
            blockers.append("battle runner concurrency does not match the season authority")
        if (
            requested_search_parallelism is not None
            and requested_search_parallelism != approved_parallelism
        ):
            blockers.append("battle runner search parallelism does not match the season authority")

        stop_loss = payload.get("stopLoss")
        if not isinstance(stop_loss, dict):
            blockers.append("season authority stopLoss must be an object")
        else:
            try:
                rating_window = int(stop_loss.get("ratingWindow"))
                max_drawdown = float(stop_loss.get("maxRatingDrawdown"))
            except (TypeError, ValueError):
                rating_window = -1
                max_drawdown = -1.0
            if rating_window != 60:
                blockers.append("season stopLoss.ratingWindow must equal 60")
            if max_drawdown != 75.0:
                blockers.append("season stopLoss.maxRatingDrawdown must equal 75")

        grants = payload.get("grants")
        locked_grants = {
            "automaticRoundContinuation": True,
            "sourceChanges": False,
            "teamChanges": False,
            "automaticImprovement": False,
            "publicOutput": False,
        }
        if not isinstance(grants, dict):
            blockers.append("season authority grants must be an object")
        else:
            for field, expected in locked_grants.items():
                if grants.get(field) is not expected:
                    blockers.append(
                        f"season authority grants.{field} violates the owner-locked policy"
                    )

        runtime_paths = _validate_paths(
            payload,
            release_root=root,
            require_existing=require_existing_paths,
            blockers=blockers,
        )
        account_season_path = runtime_paths.get("accountSeasonPath")
        if account_season_path:
            account_season_blockers: list[str] = []
            account_season = _regular_json(
                Path(account_season_path),
                account_season_blockers,
            )
            blockers.extend(
                f"account-season authority: {item}"
                for item in account_season_blockers
            )
            if account_season is not None:
                if account_season.get("schemaVersion") != (
                    "fouler-play-account-season/v1"
                ):
                    blockers.append("account-season authority schemaVersion is unsupported")
                if _normalized_identity(account_season.get("account")) != (
                    _normalized_identity(account)
                ):
                    blockers.append("account-season authority account does not match the season")
                if str(account_season.get("seasonId") or "").strip().lower() != season_id:
                    blockers.append("account-season authority seasonId does not match the season")

        control_path = runtime_paths.get("controlPath")
        if control_path:
            control_blockers: list[str] = []
            control = _regular_json(Path(control_path), control_blockers)
            blockers.extend(
                f"runtime control: {item}" for item in control_blockers
            )
            if control is not None:
                if control.get("schemaVersion") != "devstream-runtime-control/v1":
                    blockers.append("runtime control schemaVersion is unsupported")
                try:
                    control_epoch = int(control.get("pauseEpoch"))
                except (TypeError, ValueError):
                    control_epoch = -1
                if control_epoch != pause_epoch:
                    blockers.append("runtime control pauseEpoch does not match the season")

        binding: dict[str, object] = {}
        if require_child_binding:
            binding = _validate_child_binding(
                release_root=root,
                environ=environment,
                blockers=blockers,
            )
        public = {
            "id": season_id or None,
            "generation": generation,
            "pauseEpoch": pause_epoch if pause_epoch >= 0 else None,
            "sourceCommit": source_commit or None,
            "account": account or None,
            "machine": machine or None,
            "roundSize": round_size,
            "maxRounds": max_rounds,
            "maxGames": max_games,
            "startsAt": starts_at.isoformat() if starts_at else None,
            "expiresAt": expires_at.isoformat() if expires_at else None,
            "sha256": actual_digest or None,
            "runtime": runtime_paths,
            "binding": binding,
        }

    return {
        "ok": not blockers,
        "valid": not blockers,
        "authorityType": "finite-season",
        "authorityPath": str(path),
        "season": public,
        "blockers": blockers,
    }
