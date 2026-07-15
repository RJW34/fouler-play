"""External runtime path policy for an immutable Fouler release.

This module is intentionally side-effect free. Installers provision the writable
roots; Python runtime code only resolves and validates them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class RuntimePathError(RuntimeError):
    """Raised when writable runtime paths are not safe for immutable execution."""


@dataclass(frozen=True)
class RuntimePaths:
    release_root: Path
    state_root: Path
    log_root: Path
    cache_root: Path
    temp_root: Path
    decision_trace_root: Path
    battle_stats_path: Path
    matchup_weights_path: Path
    matchup_ab_log_path: Path
    movepool_data_path: Path
    production: bool


def _canonical(path: str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise RuntimePathError(f"runtime path must be absolute: {candidate}")
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def paths_overlap(first: Path, second: Path) -> bool:
    """Return True when either canonical path contains the other."""
    first = _canonical(first)
    second = _canonical(second)
    try:
        second.relative_to(first)
        return True
    except ValueError:
        pass
    try:
        first.relative_to(second)
        return True
    except ValueError:
        return False


def _looks_like_windows_release(release_root: Path) -> bool:
    parts = [part.casefold() for part in release_root.parts]
    try:
        releases_index = parts.index("releases")
    except ValueError:
        return False
    return parts[releases_index + 1 : releases_index + 2] == ["fouler-play"]


def is_production_runtime(
    release_root: Path = PROJECT_ROOT,
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> bool:
    environment = os.environ if environ is None else environ
    platform = os.name if platform_name is None else platform_name
    inferred = (
        platform == "nt" and _looks_like_windows_release(_canonical(release_root))
    ) or any(
        str(environment.get(name) or "").strip()
        for name in (
            "FOULER_RUNTIME_LEASE_PATH",
            "FOULER_DEPLOYMENT_ID",
            "FOULER_RUNTIME_AUTHORIZATION_SHA256",
        )
    )
    explicit = str(environment.get("FOULER_RUNTIME_PRODUCTION") or "").strip().lower()
    if explicit:
        if explicit in _TRUE_VALUES:
            return True
        if explicit in _FALSE_VALUES:
            # A release/lease-bound runtime cannot downgrade path validation.
            return inferred
        raise RuntimePathError("FOULER_RUNTIME_PRODUCTION must be a boolean value")
    return inferred


def _default_roots(
    *,
    environment: Mapping[str, str],
    platform_name: str,
    production: bool,
) -> tuple[Path, Path, Path, Path]:
    if platform_name == "nt" and production:
        program_data = _canonical(
            str(environment.get("PROGRAMDATA") or r"C:\ProgramData").strip()
        )
        return (
            program_data / "HERMES" / "state" / "fouler",
            program_data / "HERMES" / "logs" / "fouler",
            program_data / "HERMES" / "cache" / "fouler",
            program_data / "HERMES" / "state" / "fouler" / "tmp",
        )

    if platform_name == "nt":
        local_app_data = _canonical(
            str(
                environment.get("LOCALAPPDATA")
                or (Path.home() / "AppData" / "Local")
            ).strip()
        )
        base = local_app_data / "FoulerPlay" / "runtime"
        return (base / "state", base / "logs", base / "cache", base / "temp")

    state_home = str(environment.get("XDG_STATE_HOME") or "").strip()
    cache_home = str(environment.get("XDG_CACHE_HOME") or "").strip()
    home = Path.home()
    state_base = _canonical(state_home) if state_home else _canonical(home / ".local" / "state")
    cache_base = _canonical(cache_home) if cache_home else _canonical(home / ".cache")
    state_root = state_base / "fouler-play"
    return (
        state_root,
        state_root / "logs",
        cache_base / "fouler-play",
        state_root / "tmp",
    )


def _configured_path(
    environment: Mapping[str, str],
    name: str,
    default: Path,
) -> Path:
    raw = str(environment.get(name) or "").strip()
    return _canonical(raw) if raw else _canonical(default)


def _require_external(path: Path, release_root: Path, *, label: str) -> None:
    if paths_overlap(path, release_root):
        raise RuntimePathError(
            f"{label} overlaps immutable release: {path} <-> {release_root}"
        )


def validate_external_runtime_path(
    path: str | os.PathLike[str],
    *,
    release_root: Path = PROJECT_ROOT,
    label: str = "runtime path",
) -> Path:
    """Canonicalize one writable leaf and reject release overlap."""
    release = _canonical(release_root)
    candidate = _canonical(path)
    _require_external(candidate, release, label=label)
    return candidate


def resolve_runtime_paths(
    release_root: Path = PROJECT_ROOT,
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    require_existing: bool | None = None,
) -> RuntimePaths:
    """Resolve writable paths and fail closed for an immutable production run."""
    environment = os.environ if environ is None else environ
    platform = os.name if platform_name is None else platform_name
    release = _canonical(release_root)
    production = is_production_runtime(
        release,
        environ=environment,
        platform_name=platform,
    )
    defaults = _default_roots(
        environment=environment,
        platform_name=platform,
        production=production,
    )
    state_root = _configured_path(environment, "FOULER_RUNTIME_STATE_ROOT", defaults[0])
    log_root = _configured_path(environment, "FOULER_RUNTIME_LOG_ROOT", defaults[1])
    cache_root = _configured_path(environment, "FOULER_RUNTIME_CACHE_ROOT", defaults[2])
    temp_root = _configured_path(environment, "FOULER_RUNTIME_TEMP_ROOT", defaults[3])

    roots = {
        "runtime state root": state_root,
        "runtime log root": log_root,
        "runtime cache root": cache_root,
        "runtime temp root": temp_root,
    }
    for label, path in roots.items():
        _require_external(path, release, label=label)

    if require_existing is None:
        require_existing = production
    if require_existing:
        missing = [f"{label}: {path}" for label, path in roots.items() if not path.is_dir()]
        if missing:
            raise RuntimePathError(
                "required runtime directories are missing: " + "; ".join(missing)
            )

    decision_trace_root = _configured_path(
        environment,
        "DECISION_TRACE_DIR",
        log_root / "decision_traces",
    )
    battle_stats_path = _configured_path(
        environment,
        "FOULER_BATTLE_STATS_PATH",
        state_root / "battle_stats.json",
    )
    matchup_weights_path = _configured_path(
        environment,
        "FOULER_MATCHUP_WEIGHTS_PATH",
        state_root / "learning" / "matchup_weights.json",
    )
    matchup_ab_log_path = _configured_path(
        environment,
        "MATCHUP_MEMORY_AB_LOG",
        log_root / "matchup_ab_log.jsonl",
    )
    movepool_data_path = _configured_path(
        environment,
        "FOULER_MOVEPOOL_DATA_PATH",
        state_root / "learning" / "movepool_data.json",
    )
    leaves = {
        "decision trace root": decision_trace_root,
        "battle stats path": battle_stats_path,
        "matchup weights path": matchup_weights_path,
        "matchup A/B log path": matchup_ab_log_path,
        "movepool data path": movepool_data_path,
    }
    for label, path in leaves.items():
        _require_external(path, release, label=label)

    return RuntimePaths(
        release_root=release,
        state_root=state_root,
        log_root=log_root,
        cache_root=cache_root,
        temp_root=temp_root,
        decision_trace_root=decision_trace_root,
        battle_stats_path=battle_stats_path,
        matchup_weights_path=matchup_weights_path,
        matchup_ab_log_path=matchup_ab_log_path,
        movepool_data_path=movepool_data_path,
        production=production,
    )
