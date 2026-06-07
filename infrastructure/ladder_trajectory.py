"""Source-owned ladder trajectory helpers for improvement-loop status."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BATTLE_STATS_PATH = PROJECT_ROOT / "battle_stats.json"
TARGET_ELO = 1700
FLOOR_ELO = 1000
RECENT_WINDOW = 10


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _rating_after(row: dict) -> tuple[float | None, str]:
    for key in ("elo_after",):
        value = _as_float(row.get(key))
        if value is not None:
            return value, "authoritative_elo"
    for key in ("rating_after", "ratingAfter", "rating", "elo"):
        value = _as_float(row.get(key))
        if value is not None:
            return value, "fallback_rating"
    return None, "missing"


def _battle_id(row: dict) -> str:
    return str(
        row.get("battle_tag")
        or row.get("battle_id")
        or row.get("id")
        or row.get("replay_id")
        or ""
    )


def load_battles(path: Path = BATTLE_STATS_PATH) -> list[dict]:
    """Load battle rows from battle_stats.json, returning [] on absent data."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = raw.get("battles", [])
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def rated_points(battles: list[dict]) -> list[dict]:
    """Return chronological rated battle points with normalized ELO fields."""

    points: list[dict] = []
    for row in battles:
        elo, source = _rating_after(row)
        if elo is None:
            continue
        points.append(
            {
                "index": len(points),
                "battle_id": _battle_id(row),
                "timestamp": row.get("timestamp") or row.get("time") or "",
                "result": row.get("result") or row.get("outcome") or "",
                "elo": elo,
                "elo_source": source,
                "elo_delta": _as_float(row.get("elo_delta")),
            }
        )
    return points


def _recent_slope(points: list[dict], recent_window: int) -> float | None:
    recent = points[-max(2, int(recent_window)) :]
    if len(recent) < 2:
        return None
    return (recent[-1]["elo"] - recent[0]["elo"]) / (len(recent) - 1)


def trajectory_from_battles(
    battles: list[dict],
    *,
    target: int = TARGET_ELO,
    floor: int = FLOOR_ELO,
    recent_window: int = RECENT_WINDOW,
) -> dict:
    points = rated_points(battles)
    if not points:
        return {
            "current_elo": None,
            "peak_elo": None,
            "recent_slope_per_game": None,
            "games_to_target_at_rate": None,
            "progress_fraction_1000_to_target": 0.0,
            "rated_games": 0,
            "target": target,
            "remaining_to_target": None,
            "at_or_above_target": False,
            "recent_points": [],
            "authoritative_elo_games": 0,
            "fallback_rating_games": 0,
        }

    current = points[-1]["elo"]
    peak = max(point["elo"] for point in points)
    slope = _recent_slope(points, recent_window)
    remaining = max(0.0, float(target) - current)
    progress = (current - floor) / (target - floor) if target > floor else 0.0
    progress = min(1.0, max(0.0, progress))
    games_to_target = None
    if current >= target:
        games_to_target = 0
    elif slope is not None and slope > 0:
        games_to_target = int(math.ceil(remaining / slope))

    return {
        "current_elo": current,
        "peak_elo": peak,
        "recent_slope_per_game": slope,
        "games_to_target_at_rate": games_to_target,
        "progress_fraction_1000_to_target": progress,
        "rated_games": len(points),
        "authoritative_elo_games": sum(1 for point in points if point.get("elo_source") == "authoritative_elo"),
        "fallback_rating_games": sum(1 for point in points if point.get("elo_source") == "fallback_rating"),
        "target": target,
        "remaining_to_target": remaining,
        "at_or_above_target": current >= target,
        "recent_points": points[-max(1, int(recent_window)) :],
    }


def trajectory(
    path: Path = BATTLE_STATS_PATH,
    *,
    target: int = TARGET_ELO,
    floor: int = FLOOR_ELO,
    recent_window: int = RECENT_WINDOW,
) -> dict:
    return trajectory_from_battles(
        load_battles(path),
        target=target,
        floor=floor,
        recent_window=recent_window,
    )


def proof(path: Path = BATTLE_STATS_PATH, *, recent_window: int = RECENT_WINDOW) -> dict:
    """Return the compact proof payload behind trajectory()."""

    battles = load_battles(path)
    traj = trajectory_from_battles(battles, recent_window=recent_window)
    return {
        "battle_stats_path": str(path),
        "rated_games": traj["rated_games"],
        "current_elo": traj["current_elo"],
        "peak_elo": traj["peak_elo"],
        "recent_slope_per_game": traj["recent_slope_per_game"],
        "authoritative_elo_games": traj["authoritative_elo_games"],
        "fallback_rating_games": traj["fallback_rating_games"],
        "recent_points": traj["recent_points"],
    }
