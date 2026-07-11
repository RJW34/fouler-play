#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import devstream_health

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT_JSON = ROOT / "devstream" / "truth" / "cycle-report.json"
OUTPUT_MD = ROOT / "devstream" / "truth" / "cycle-report.md"
OUTPUT_COMPLETION = ROOT / "devstream" / "truth" / "completion.json"
OUTPUT_PROOF_STATUS = ROOT / "devstream" / "truth" / "proof-status.json"
OUTPUT_ELO_PROOF = ROOT / "devstream" / "truth" / "latest-elo-proof.json"
ACCOUNT_SEASON_FILE = ROOT / "devstream" / "truth" / "account-season.json"
DISCORD_REPORTING = ROOT / "devstream" / "truth" / "discord-reporting.json"
DISCORD_DELIVERY = ROOT / "devstream" / "truth" / "discord-delivery.json"
IDLE_RUNTIME_BLOCKER = "fouler-play battle runner is idle; OBS HTTP alone is not active battle proof"
TERMINAL_BATTLE_RESULTS = {"win", "loss", "tie", "draw", "forfeit", "timeout", "ended", "error"}
UNKNOWN_ACCOUNT = "unknown"
ELO_TARGET_RATING = 1700
ELO_SUSTAIN_MINIMUM_GAMES = 30
ELO_SUSTAIN_MINIMUM_GAMES_PER_TEAM = 10
ELO_SUSTAIN_MAX_DRAWDOWN = 75
ELO_SUSTAIN_MINIMUM_WIN_RATE = 0.5
ELO_REQUIRED_TEAMS = ("fat-team-1-stall", "fat-team-2-pivot", "fat-team-3-dondozo")
SHOWDOWN_PROFILE_TIMEOUT_SECONDS = 5.0


def current_source_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def refresh_discord_proof_preview() -> dict[str, Any]:
    """Write a fresh local Discord proof preview without posting or draining."""
    queue_file = ROOT / "events_queue.json"
    try:
        from infrastructure import event_poster, event_queue_lib

        events = event_queue_lib.read_queue()
        if not isinstance(events, list):
            events = []
        pending = [event for event in events if isinstance(event, dict) and event.get("status") == "pending"]
        pending.sort(key=lambda event: _safe_count(event.get("timestamp")))
        if pending:
            event = pending[0]
            payload = event_poster.write_delivery_proof(
                status="dry-run",
                event=event,
                destination_alias=str(event.get("channel") or "unknown"),
                dry_run=True,
                blockers=[],
            )
            return {
                "refreshed": True,
                "status": "dry-run",
                "eventId": event.get("id"),
                "eventType": event.get("event_type"),
                "pendingBacklog": (payload.get("queue") or {}).get("pending"),
                "pendingBattleResults": (payload.get("queue") or {}).get("pendingBattleResults"),
                "deliveryProof": str(DISCORD_DELIVERY.relative_to(ROOT)),
                "reportingProof": str(DISCORD_REPORTING.relative_to(ROOT)),
                "secretValuesPrinted": bool(payload.get("secretValuesPrinted")),
                "note": "local proof preview only; queue events remain pending until approved transport or explicit archival",
            }
        payload = event_poster.write_delivery_proof(
            status="idle",
            event=None,
            destination_alias="unknown",
            dry_run=True,
            blockers=["no pending Discord events"],
            error_code="no_pending_events",
        )
        return {
            "refreshed": True,
            "status": "idle",
            "pendingBacklog": (payload.get("queue") or {}).get("pending"),
            "pendingBattleResults": (payload.get("queue") or {}).get("pendingBattleResults"),
            "deliveryProof": str(DISCORD_DELIVERY.relative_to(ROOT)),
            "reportingProof": str(DISCORD_REPORTING.relative_to(ROOT)),
            "secretValuesPrinted": bool(payload.get("secretValuesPrinted")),
            "note": "no pending Discord events were available for preview",
        }
    except Exception as exc:
        return {
            "refreshed": False,
            "status": "failed",
            "queueFile": str(queue_file),
            "error": f"{type(exc).__name__}: {exc}",
            "note": "cycle report continued without refreshing local Discord proof preview",
        }


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _clean_account(value: Any) -> str:
    return str(value or "").strip()


def _first_csv_value(value: Any) -> str:
    text = _clean_account(value)
    if not text:
        return ""
    return next((item.strip() for item in text.split(",") if item.strip()), "")


def _dotenv_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    prefix = f"{key}="
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line[len(prefix):].split("#", 1)[0].strip().strip('"').strip("'")
        return value
    return ""


def account_from_runtime_lease(path: Path | None = None) -> tuple[str, str]:
    lease_path = path or ROOT / "devstream" / "truth" / "runtime-lease.json"
    lease = read_json(lease_path)
    if not isinstance(lease, dict):
        return "", ""
    for value in (
        lease.get("account"),
        lease.get("showdownUserId"),
        (lease.get("battleScope") or {}).get("account") if isinstance(lease.get("battleScope"), dict) else "",
        (lease.get("battleScope") or {}).get("showdownUserId") if isinstance(lease.get("battleScope"), dict) else "",
    ):
        account = _first_csv_value(value)
        if account:
            return account, str(lease_path.relative_to(ROOT)).replace("\\", "/") if lease_path.is_relative_to(ROOT) else str(lease_path)
    return "", ""


def resolve_showdown_account(explicit: str | None = None) -> dict[str, Any]:
    explicit_account = _first_csv_value(explicit)
    if explicit_account:
        return {"showdownUserId": explicit_account, "authoritySource": "cli", "accountAuthorityReady": True}

    lease_account, lease_source = account_from_runtime_lease()
    if lease_account:
        return {
            "showdownUserId": lease_account,
            "authoritySource": lease_source,
            "accountAuthorityReady": True,
        }

    for env_key in ("FOULER_ACTIVE_ACCOUNT", "PS_USERNAME", "SHOWDOWN_ACCOUNTS"):
        account = _first_csv_value(os.getenv(env_key, ""))
        if account:
            return {"showdownUserId": account, "authoritySource": f"env:{env_key}", "accountAuthorityReady": True}

    env_path = ROOT / ".env"
    for env_key in ("PS_USERNAME", "SHOWDOWN_ACCOUNTS"):
        account = _first_csv_value(_dotenv_value(env_path, env_key))
        if account:
            return {
                "showdownUserId": account,
                "authoritySource": f".env:{env_key}",
                "accountAuthorityReady": True,
            }

    return {
        "showdownUserId": UNKNOWN_ACCOUNT,
        "authoritySource": "unresolved",
        "accountAuthorityReady": False,
    }


def showdown_user_id(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _profile_number(value: object) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    rounded = round(parsed, 2)
    return int(rounded) if rounded.is_integer() else rounded


def fetch_showdown_profile_rating(
    account: str,
    fmt: str = "gen9ou",
    *,
    timeout: float = SHOWDOWN_PROFILE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Read the live Pokemon Showdown profile rating without mutating runtime state."""

    user_id = showdown_user_id(account)
    checked_at = iso_now()
    if not user_id or user_id == UNKNOWN_ACCOUNT:
        return {
            "status": "unresolved-account",
            "checkedAtUtc": checked_at,
            "showdownUserId": user_id or UNKNOWN_ACCOUNT,
            "format": fmt,
            "rating": None,
            "source": "pokemonshowdown-user-api",
            "noRuntimeActions": True,
        }

    url = f"https://pokemonshowdown.com/users/{user_id}.json"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "fouler-play-devstream-proof/1.0",
        },
    )
    http_status: int | None = None
    http_warning: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            http_status = int(getattr(response, "status", None) or response.getcode())
            raw = response.read(65536)
    except urllib.error.HTTPError as exc:
        # Pokemon Showdown can return a useful user JSON body with a non-2xx
        # status for unrated/unregistered account pages. The body is the proof.
        http_status = int(exc.code)
        http_warning = f"{type(exc).__name__}: HTTP Error {exc.code}: {exc.reason}"
        raw = exc.read(65536)
    except (OSError, urllib.error.URLError) as exc:
        return {
            "status": "fetch-failed",
            "checkedAtUtc": checked_at,
            "showdownUserId": user_id,
            "format": fmt,
            "rating": None,
            "source": url,
            "error": f"{type(exc).__name__}: {exc}",
            "noRuntimeActions": True,
        }
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return {
            "status": "fetch-failed",
            "checkedAtUtc": checked_at,
            "showdownUserId": user_id,
            "format": fmt,
            "rating": None,
            "source": url,
            "httpStatus": http_status,
            "error": f"{type(exc).__name__}: {exc}",
            "httpWarning": http_warning,
            "noRuntimeActions": True,
        }

    ratings = payload.get("ratings") if isinstance(payload, dict) else {}
    format_rating = ratings.get(fmt) if isinstance(ratings, dict) else None
    if not isinstance(format_rating, dict):
        return {
            "status": "format-missing",
            "checkedAtUtc": checked_at,
            "showdownUserId": str(payload.get("userid") or user_id) if isinstance(payload, dict) else user_id,
            "format": fmt,
            "rating": None,
            "source": url,
            "httpStatus": http_status,
            "httpWarning": http_warning,
            "noRuntimeActions": True,
        }

    rating = _profile_number(format_rating.get("elo"))
    return {
        "status": "fetched" if rating is not None else "rating-missing",
        "checkedAtUtc": checked_at,
        "showdownUserId": str(payload.get("userid") or user_id) if isinstance(payload, dict) else user_id,
        "format": fmt,
        "rating": rating,
        "gxe": _profile_number(format_rating.get("gxe")),
        "rpr": _profile_number(format_rating.get("rpr")),
        "rprd": _profile_number(format_rating.get("rprd")),
        "wins": format_rating.get("w"),
        "losses": format_rating.get("l"),
        "source": url,
        "httpStatus": http_status,
        "httpWarning": http_warning,
        "noRuntimeActions": True,
    }


def file_meta(path: Path) -> dict[str, Any]:
    exists = path.exists()
    age = time.time() - path.stat().st_mtime if exists else None
    try:
        rel_path = str(path.relative_to(ROOT))
    except ValueError:
        rel_path = str(path)
    return {
        "path": rel_path,
        "exists": exists,
        "updatedAt": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat() if exists else None,
        "ageSeconds": round(age, 3) if age is not None else None,
    }


def active_battle_stale_after_seconds() -> int | None:
    for spec in devstream_health.TRUTH_FILES:
        if spec.get("path") == "active_battles.json":
            try:
                return int(spec.get("staleAfterSeconds") or 0) or None
            except (TypeError, ValueError):
                return None
    return None


def active_battle_telemetry_status(path: Path) -> dict[str, Any]:
    meta = file_meta(path)
    stale_after = active_battle_stale_after_seconds()
    age = meta.get("ageSeconds")
    stale = bool(meta["exists"] and stale_after and age is not None and float(age) > stale_after)
    return {
        **meta,
        "staleAfterSeconds": stale_after,
        "stale": stale,
    }


def summarize_active_battles(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "battleCount": 0,
            "battleIds": [],
            "classification": "missing-active-battle-telemetry",
            "isCompletedProof": False,
        }
    battles = payload.get("battles") if isinstance(payload.get("battles"), list) else []
    return {
        "battleCount": len(battles),
        "battleIds": [str(item.get("battle_id") or item.get("id") or "") for item in battles[:5] if isinstance(item, dict)],
        "classification": "active-battle-telemetry" if battles else "empty-active-battle-telemetry",
        "isCompletedProof": False,
        "proofNote": (
            "active battle telemetry shows battles in progress; it is not completed cycle proof"
            if battles
            else "no active battle telemetry is present"
        ),
    }


def _battle_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("battle_id") or payload.get("id") or "").strip()


def terminal_battle_ids(stats: Any) -> set[str]:
    battles = stats.get("battles") if isinstance(stats, dict) else stats
    if not isinstance(battles, list):
        return set()
    ids: set[str] = set()
    for item in battles:
        if not isinstance(item, dict):
            continue
        battle_id = _battle_id(item)
        result = str(item.get("result") or item.get("status") or item.get("outcome") or "").lower()
        if battle_id and result in TERMINAL_BATTLE_RESULTS:
            ids.add(battle_id)
    return ids


def reconcile_active_battles(summary: dict[str, Any], stats: Any) -> dict[str, Any]:
    battle_ids = [str(item) for item in summary.get("battleIds") or [] if str(item)]
    ghosts = [battle_id for battle_id in battle_ids if battle_id in terminal_battle_ids(stats)]
    if not ghosts:
        return {
            **summary,
            "rawBattleCount": summary.get("battleCount", 0),
            "ghostBattleCount": 0,
            "ghostBattleIds": [],
        }
    ghost_set = set(ghosts)
    live_ids = [battle_id for battle_id in battle_ids if battle_id not in ghost_set]
    return {
        **summary,
        "rawBattleCount": summary.get("battleCount", len(battle_ids)),
        "battleCount": len(live_ids),
        "battleIds": live_ids,
        "ghostBattleCount": len(ghosts),
        "ghostBattleIds": ghosts,
        "classification": "ghost-active-battle-telemetry" if not live_ids else "mixed-active-and-ghost-battle-telemetry",
        "proofNote": "terminal battle_stats evidence exists for ghost active_battles id(s); ghosts are not live proof",
    }


def summarize_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    wins = payload.get("wins")
    losses = payload.get("losses")
    total = None
    if isinstance(wins, int) and isinstance(losses, int):
        total = wins + losses
    return {
        "status": payload.get("status"),
        "elo": payload.get("elo") or payload.get("rating"),
        "wins": wins,
        "losses": losses,
        "games": total,
        "updated": payload.get("updated") or payload.get("updated_at"),
    }


def normalize_result(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"win", "won"}:
        return "win"
    if text in {"tie", "draw"}:
        return "draw"
    if text in {"loss", "lost", "forfeit", "timeout", "timed out", "disconnect", "disconnected", "inactive", "ended", "error"}:
        return "loss"
    if any(marker in text for marker in ("timeout", "disconnect", "inactive")):
        return "loss"
    return "unknown"


def rating_after(row: dict[str, Any]) -> int | None:
    for key in ("ratingAfter", "rating_after", "rating", "eloAfter", "elo_after", "elo"):
        value = row.get(key)
        if value is None:
            continue
        try:
            rating = int(float(value))
        except (TypeError, ValueError):
            continue
        if rating > 0:
            return rating
    return None


def normalize_team(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    leaf = text.rstrip("/").split("/")[-1]
    if "." in leaf:
        leaf = leaf.rsplit(".", 1)[0]
    return leaf


def battle_timestamp(row: dict[str, Any]) -> str | None:
    for key in ("timestamp", "endedAt", "ended_at", "createdAt", "created_at", "time"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def parse_battle_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def battle_order_key(row: dict[str, Any]) -> tuple[datetime, str]:
    parsed = parse_battle_timestamp(row.get("timestamp"))
    return (
        parsed or datetime.max.replace(tzinfo=timezone.utc),
        normalized_battle_id(row.get("battleId")),
    )


def replay_url(row: dict[str, Any], battle_id: str) -> str:
    for key in ("replayUrl", "replay_url", "replay"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    normalized = battle_id.removeprefix("battle-")
    return f"https://replay.pokemonshowdown.com/{normalized}" if normalized and normalized != "unknown" else ""


def decision_trace_value(row: dict[str, Any]) -> tuple[str, str]:
    for key in ("decisionTracePath", "decision_trace_path", "decisionTrace"):
        value = str(row.get(key) or "").strip()
        if value:
            return "decisionTracePath", value
    for key in ("decisionTraceUrl", "decision_trace_url"):
        value = str(row.get(key) or "").strip()
        if value:
            return "decisionTraceUrl", value
    return "", ""


def replay_proof_present(value: object) -> bool:
    text = str(value or "").strip()
    if not re.fullmatch(r"https?://replay\.pokemonshowdown\.com/[A-Za-z0-9][A-Za-z0-9-]*", text):
        return False
    replay_id = text.rstrip("/").rsplit("/", 1)[-1].lower()
    return replay_id not in {"unknown", "none", "null"}


def normalized_battle_id(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("battle-"):
        text = text[len("battle-"):]
    return text


def replay_matches_battle_id(replay_url: object, battle_id: object) -> bool:
    replay = str(replay_url or "").strip()
    normalized = normalized_battle_id(battle_id)
    if not replay or not normalized:
        return False
    replay_id = replay.rstrip("/").rsplit("/", 1)[-1].lower()
    return replay_id == normalized


def decision_trace_proof_present(row: dict[str, Any]) -> bool:
    return bool(decision_trace_proof_id(row))


def decision_trace_proof_id(row: dict[str, Any]) -> str:
    for key in ("decisionTracePath", "decision_trace_path", "decisionTrace", "decisionTraceUrl", "decision_trace_url"):
        value = str(row.get(key) or "").strip()
        if value and value.lower() not in {"unknown", "none", "null"}:
            return value.replace("\\", "/").rstrip("/").lower()
    return ""


def active_account_season(account: str, path: Path | None = None) -> str:
    payload = read_json(path or ACCOUNT_SEASON_FILE)
    if not isinstance(payload, dict):
        return ""
    if showdown_user_id(payload.get("account")) != showdown_user_id(account):
        return ""
    return str(payload.get("seasonId") or "").strip()


def completed_battle_rows(
    stats: Any,
    *,
    account: str | None = None,
    season_id: str | None = None,
) -> list[dict[str, Any]]:
    battles = stats.get("battles") if isinstance(stats, dict) else stats
    if not isinstance(battles, list):
        return []
    account_id = showdown_user_id(account)
    tagged_accounts_present = any(
        showdown_user_id(row.get("account"))
        for row in battles
        if isinstance(row, dict)
    )
    if account_id and account_id != UNKNOWN_ACCOUNT and tagged_accounts_present:
        battles = [
            row
            for row in battles
            if isinstance(row, dict)
            and showdown_user_id(row.get("account")) == account_id
        ]
    tagged_seasons_present = any(
        str(row.get("season_id") or row.get("seasonId") or "").strip()
        for row in battles
        if isinstance(row, dict)
    )
    if season_id and tagged_seasons_present:
        battles = [
            row
            for row in battles
            if isinstance(row, dict)
            and str(row.get("season_id") or row.get("seasonId") or "").strip()
            == season_id
        ]
    completed: list[dict[str, Any]] = []
    for row in battles:
        if not isinstance(row, dict):
            continue
        result = normalize_result(row.get("result") or row.get("status") or row.get("outcome"))
        if result not in {"win", "loss", "draw"}:
            continue
        battle_id = str(row.get("battleId") or row.get("battle_id") or row.get("id") or "unknown").strip() or "unknown"
        team = normalize_team(row.get("teamFile") or row.get("team_file") or row.get("teamName") or row.get("team"))
        proof_row = {
            "battleId": battle_id,
            "result": result,
            "replayUrl": replay_url(row, battle_id),
            "opponent": str(row.get("opponent") or row.get("opponentName") or ""),
            "opponentRating": row.get("opponentRating") or row.get("opponent_rating"),
            "ratingBefore": row.get("ratingBefore") or row.get("rating_before"),
            "ratingAfter": rating_after(row),
            "teamFile": team,
            "timestamp": battle_timestamp(row),
            "failureClasses": row.get("failureClasses") if isinstance(row.get("failureClasses"), list) else [],
        }
        trace_key, trace_value = decision_trace_value(row)
        if trace_key:
            proof_row[trace_key] = trace_value
        completed.append(proof_row)
    completed.sort(key=battle_order_key)
    return completed


def max_drawdown(ratings: list[int]) -> float | None:
    if not ratings:
        return None
    peak = ratings[0]
    drawdown = 0
    for rating in ratings:
        if rating > peak:
            peak = rating
        drawdown = max(drawdown, peak - rating)
    return float(drawdown)


def drawdown_summary(games: list[dict[str, Any]]) -> dict[str, Any]:
    if not games:
        return {
            "ratedGames": 0,
            "maxDrawdown": None,
            "peakRating": None,
            "peakBattleId": None,
            "troughRating": None,
            "troughBattleId": None,
        }

    peak = games[0]
    drawdown_peak = games[0]
    trough_after_peak = games[0]
    max_seen = 0
    for game in games:
        rating = int(game["ratingAfter"])
        if rating > int(peak["ratingAfter"]):
            peak = game
        drawdown = int(peak["ratingAfter"]) - rating
        if drawdown > max_seen:
            max_seen = drawdown
            drawdown_peak = peak
            trough_after_peak = game

    return {
        "ratedGames": len(games),
        "maxDrawdown": float(max_seen),
        "peakRating": int(drawdown_peak["ratingAfter"]),
        "peakBattleId": str(drawdown_peak.get("battleId") or ""),
        "troughRating": int(trough_after_peak["ratingAfter"]),
        "troughBattleId": str(trough_after_peak.get("battleId") or ""),
    }


def artifact_path_if_present(meta: object) -> str:
    if not isinstance(meta, dict):
        return ""
    if meta.get("exists") is False:
        return ""
    return str(meta.get("path") or "").strip().replace("\\", "/")


def rel_output_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def build_elo_analysis_evidence(cycle: dict[str, Any]) -> dict[str, Any]:
    autoresearch = cycle.get("autoresearch") if isinstance(cycle.get("autoresearch"), dict) else {}
    return {
        "generatedAtUtc": cycle.get("generatedAt"),
        "autoresearchJsonPath": artifact_path_if_present(autoresearch.get("json")),
        "autoresearchReportPath": artifact_path_if_present(autoresearch.get("report")),
        "decisionTraceReviewPath": rel_output_path(OUTPUT_PROOF_STATUS),
        "topIssue": None,
        "reviewedBattleCount": None,
        "lossesAnalyzed": None,
    }


def build_elo_proof_payload(
    stats: Any,
    cycle: dict[str, Any],
    *,
    account: str | None = None,
    autoresearch: Any = None,
    live_profile: dict[str, Any] | None = None,
    fetch_live_profile: bool = False,
) -> dict[str, Any]:
    autoresearch_payload = autoresearch if isinstance(autoresearch, dict) else {}
    regression = (
        autoresearch_payload.get("regression")
        if isinstance(autoresearch_payload.get("regression"), dict)
        else {}
    )
    improvement = positive_improvement_signal(autoresearch_payload, regression)
    account_authority = resolve_showdown_account(account)
    showdown_user = account_authority["showdownUserId"]
    season_id = active_account_season(showdown_user)
    games = completed_battle_rows(stats, account=showdown_user, season_id=season_id)
    missing_battle_timestamp_games = [
        game for game in games if parse_battle_timestamp(game.get("timestamp")) is None
    ]
    out_of_order_battle_timestamp_games: list[dict[str, Any]] = []
    previous_timestamp: datetime | None = None
    for game in games:
        parsed_timestamp = parse_battle_timestamp(game.get("timestamp"))
        if parsed_timestamp is None:
            continue
        if previous_timestamp is not None and parsed_timestamp < previous_timestamp:
            out_of_order_battle_timestamp_games.append(game)
        previous_timestamp = parsed_timestamp
    chronological_battle_order_complete = (
        not missing_battle_timestamp_games
        and not out_of_order_battle_timestamp_games
    )
    rated_games = [game for game in games if isinstance(game.get("ratingAfter"), int)]
    first_target_index = next(
        (index for index, game in enumerate(rated_games) if int(game["ratingAfter"]) >= ELO_TARGET_RATING),
        None,
    )
    pre_target_games = rated_games[:first_target_index] if first_target_index is not None else rated_games
    pre_target_drawdown = drawdown_summary(pre_target_games)
    max_pre_target_drawdown = pre_target_drawdown["maxDrawdown"]
    pre_target_drawdown_within_limit = (
        max_pre_target_drawdown is None
        or max_pre_target_drawdown <= ELO_SUSTAIN_MAX_DRAWDOWN
    )
    sustain_games = rated_games[first_target_index:] if first_target_index is not None else []
    games_at_or_above = [game for game in sustain_games if int(game["ratingAfter"]) >= ELO_TARGET_RATING]
    below_floor_after_first_target = len(sustain_games) - len(games_at_or_above)
    team_coverage = {team: 0 for team in ELO_REQUIRED_TEAMS}
    for game in games_at_or_above:
        team = normalize_team(game.get("teamFile")).lower()
        if team in team_coverage:
            team_coverage[team] += 1
    missing_sustain_replay_games = [
        game for game in games_at_or_above if not replay_proof_present(game.get("replayUrl"))
    ]
    mismatched_sustain_replay_games = [
        game
        for game in games_at_or_above
        if replay_proof_present(game.get("replayUrl"))
        and not replay_matches_battle_id(game.get("replayUrl"), game.get("battleId"))
    ]
    sustain_battle_ids = [normalized_battle_id(game.get("battleId")) for game in games_at_or_above]
    missing_sustain_battle_id_games = [
        game
        for game, battle_id in zip(games_at_or_above, sustain_battle_ids)
        if not battle_id or battle_id in {"unknown", "none", "null"}
    ]
    duplicate_sustain_battle_ids = sorted(
        battle_id
        for battle_id in set(sustain_battle_ids)
        if battle_id and sustain_battle_ids.count(battle_id) > 1
    )
    sustain_replay_ids = [
        str(game.get("replayUrl") or "").strip().rstrip("/").rsplit("/", 1)[-1].lower()
        for game in games_at_or_above
        if replay_proof_present(game.get("replayUrl"))
    ]
    duplicate_sustain_replay_ids = sorted(
        replay_id
        for replay_id in set(sustain_replay_ids)
        if replay_id and sustain_replay_ids.count(replay_id) > 1
    )
    unknown_sustain_team_games = [
        game
        for game in games_at_or_above
        if normalize_team(game.get("teamFile")).lower() not in team_coverage
    ]
    missing_decision_trace_games = [
        game for game in games_at_or_above if not decision_trace_proof_present(game)
    ]
    sustain_decision_trace_ids = [
        decision_trace_proof_id(game)
        for game in games_at_or_above
        if decision_trace_proof_present(game)
    ]
    duplicate_decision_trace_proofs = sorted(
        trace_id
        for trace_id in set(sustain_decision_trace_ids)
        if trace_id and sustain_decision_trace_ids.count(trace_id) > 1
    )
    sustain_wins = sum(1 for game in games_at_or_above if game["result"] == "win")
    sustain_losses = sum(1 for game in games_at_or_above if game["result"] == "loss")
    sustain_win_rate = sustain_wins / len(games_at_or_above) if games_at_or_above else None
    sustain_ratings = [int(game["ratingAfter"]) for game in sustain_games if isinstance(game.get("ratingAfter"), int)]
    sustain_drawdown = max_drawdown(sustain_ratings)
    latest_game = games[-1] if games else {}
    final_rating = int(rated_games[-1]["ratingAfter"]) if rated_games else None
    sustain_evidence_shape_complete = (
        bool(sustain_games)
        and not missing_sustain_replay_games
        and not mismatched_sustain_replay_games
        and not missing_sustain_battle_id_games
        and not duplicate_sustain_battle_ids
        and not duplicate_sustain_replay_ids
        and not unknown_sustain_team_games
        and not missing_decision_trace_games
        and not duplicate_decision_trace_proofs
        and chronological_battle_order_complete
        and pre_target_drawdown_within_limit
    )
    sustained_target = (
        len(games_at_or_above) >= ELO_SUSTAIN_MINIMUM_GAMES
        and below_floor_after_first_target == 0
        and all(count >= ELO_SUSTAIN_MINIMUM_GAMES_PER_TEAM for count in team_coverage.values())
        and final_rating is not None
        and final_rating >= ELO_TARGET_RATING
        and sustain_win_rate is not None
        and sustain_win_rate >= ELO_SUSTAIN_MINIMUM_WIN_RATE
        and (sustain_drawdown is None or sustain_drawdown <= ELO_SUSTAIN_MAX_DRAWDOWN)
        and sustain_evidence_shape_complete
    )
    timestamps = [str(game["timestamp"]) for game in games if game.get("timestamp")]
    source_commit = current_source_commit()
    if isinstance(live_profile, dict):
        live_profile_payload = live_profile
    elif fetch_live_profile:
        live_profile_payload = fetch_showdown_profile_rating(showdown_user, "gen9ou")
    else:
        live_profile_payload = {
            "status": "not-fetched",
            "checkedAtUtc": None,
            "showdownUserId": showdown_user,
            "format": "gen9ou",
            "rating": None,
            "source": "disabled-for-offline-build",
            "noRuntimeActions": True,
        }
    live_profile_rating = _profile_number(live_profile_payload.get("rating"))
    current_rating = live_profile_rating if live_profile_rating is not None else final_rating
    analysis_evidence = build_elo_analysis_evidence(cycle)
    analysis_evidence_complete = all(
        str(analysis_evidence.get(key) or "").strip()
        for key in ("autoresearchJsonPath", "autoresearchReportPath", "decisionTraceReviewPath")
    )
    latest_battle_learning_verified = bool(
        autoresearch_payload
        and analysis_evidence_complete
        and not autoresearch_has_unsupported_claims(autoresearch_payload)
    )
    return {
        "schemaVersion": "fouler-play-elo-proof/v1",
        "format": "gen9ou",
        "checkedAtUtc": cycle.get("generatedAt") or iso_now(),
        "sourceCommit": source_commit,
        "account": {
            "showdownUserId": showdown_user,
            "authoritySource": account_authority["authoritySource"],
            "authorityReady": account_authority["accountAuthorityReady"],
            "seasonId": season_id or None,
            "ratingSource": (
                "battle_stats.json + pokemonshowdown-user-api"
                if live_profile_rating is not None else "battle_stats.json"
            ),
        },
        "target": {
            "ratingFloor": ELO_TARGET_RATING,
            "minimumCompletedGames": ELO_SUSTAIN_MINIMUM_GAMES,
            "sustainMinimumGames": ELO_SUSTAIN_MINIMUM_GAMES,
            "sustainMinimumGamesPerTeam": ELO_SUSTAIN_MINIMUM_GAMES_PER_TEAM,
            "maximumSustainDrawdown": ELO_SUSTAIN_MAX_DRAWDOWN,
            "maximumPreTargetDrawdown": ELO_SUSTAIN_MAX_DRAWDOWN,
            "minimumSustainWinRate": ELO_SUSTAIN_MINIMUM_WIN_RATE,
            "requiredTeams": list(ELO_REQUIRED_TEAMS),
            "opponentBand": "prefer 1700+ when opponent rating is known",
            "noCherryPicking": True,
            "uninterruptedPostTargetFloorRequired": True,
        },
        "session": {
            "startedAt": timestamps[0] if timestamps else None,
            "endedAt": timestamps[-1] if timestamps else None,
            "runCountTarget": ELO_SUSTAIN_MINIMUM_GAMES,
            "maxConcurrentBattles": None,
        },
        "games": games,
        "analysis": analysis_evidence,
        "liveProfile": live_profile_payload,
        "summary": {
            "completedGames": len(games),
            "latestBattleId": latest_game.get("battleId"),
            "latestBattleAt": latest_game.get("timestamp"),
            "latestBattleLearningVerified": latest_battle_learning_verified,
            "performanceImprovementVerified": bool(improvement["ok"]),
            "performanceTrendStatus": improvement["trend"],
            "improvementSignalStatus": improvement["status"],
            "improvementSignal": improvement,
            "winRate": autoresearch_payload.get("win_rate"),
            "ratingDelta": improvement["ratingDelta"],
            "winRateDelta": improvement["winRateDelta"],
            "wins": sum(1 for game in games if game["result"] == "win"),
            "losses": sum(1 for game in games if game["result"] == "loss"),
            "peakRating": max((int(game["ratingAfter"]) for game in rated_games), default=None),
            "finalRating": final_rating,
            "currentRating": current_rating,
            "currentRatingSource": "pokemonshowdown-user-api" if live_profile_rating is not None else "battle_stats.json",
            "liveProfileRating": live_profile_rating,
            "liveProfileCheckedAtUtc": live_profile_payload.get("checkedAtUtc"),
            "passesTarget": any(int(game["ratingAfter"]) >= ELO_TARGET_RATING for game in rated_games),
            "sustainedTarget": sustained_target,
            "sustainWindowGames": len(sustain_games),
            "gamesAtOrAboveFloor": len(games_at_or_above),
            "belowFloorAfterFirstTarget": below_floor_after_first_target,
            "maxSustainDrawdown": sustain_drawdown,
            "preTargetRatedGames": pre_target_drawdown["ratedGames"],
            "maxPreTargetDrawdown": max_pre_target_drawdown,
            "preTargetDrawdownPeakRating": pre_target_drawdown["peakRating"],
            "preTargetDrawdownPeakBattleId": pre_target_drawdown["peakBattleId"],
            "preTargetDrawdownTroughRating": pre_target_drawdown["troughRating"],
            "preTargetDrawdownTroughBattleId": pre_target_drawdown["troughBattleId"],
            "preTargetDrawdownWithinLimit": pre_target_drawdown_within_limit,
            "minimumSustainWinRate": ELO_SUSTAIN_MINIMUM_WIN_RATE,
            "sustainWinRate": sustain_win_rate,
            "teamCoverage": team_coverage,
            "sustainReplayProofCount": len(games_at_or_above) - len(missing_sustain_replay_games),
            "missingSustainReplayCount": len(missing_sustain_replay_games),
            "missingSustainReplayBattleIds": [
                str(game.get("battleId") or "") for game in missing_sustain_replay_games[:10]
            ],
            "mismatchedSustainReplayCount": len(mismatched_sustain_replay_games),
            "mismatchedSustainReplayBattleIds": [
                str(game.get("battleId") or "") for game in mismatched_sustain_replay_games[:10]
            ],
            "missingSustainBattleIdCount": len(missing_sustain_battle_id_games),
            "duplicateSustainBattleIdCount": len(duplicate_sustain_battle_ids),
            "duplicateSustainBattleIds": duplicate_sustain_battle_ids[:10],
            "duplicateSustainReplayIdCount": len(duplicate_sustain_replay_ids),
            "duplicateSustainReplayIds": duplicate_sustain_replay_ids[:10],
            "unknownSustainTeamCount": len(unknown_sustain_team_games),
            "unknownSustainTeamBattleIds": [
                str(game.get("battleId") or "") for game in unknown_sustain_team_games[:10]
            ],
            "decisionTraceProofCount": len(games_at_or_above) - len(missing_decision_trace_games),
            "missingDecisionTraceCount": len(missing_decision_trace_games),
            "missingDecisionTraceBattleIds": [
                str(game.get("battleId") or "") for game in missing_decision_trace_games[:10]
            ],
            "duplicateDecisionTraceProofCount": len(duplicate_decision_trace_proofs),
            "duplicateDecisionTraceProofs": duplicate_decision_trace_proofs[:10],
            "missingBattleTimestampCount": len(missing_battle_timestamp_games),
            "missingBattleTimestampBattleIds": [
                str(game.get("battleId") or "") for game in missing_battle_timestamp_games[:10]
            ],
            "outOfOrderBattleTimestampCount": len(out_of_order_battle_timestamp_games),
            "outOfOrderBattleTimestampBattleIds": [
                str(game.get("battleId") or "") for game in out_of_order_battle_timestamp_games[:10]
            ],
            "chronologicalBattleOrderComplete": chronological_battle_order_complete,
            "analysisEvidenceComplete": analysis_evidence_complete,
            "sustainEvidenceShapeComplete": sustain_evidence_shape_complete,
            "sustainProofComplete": sustained_target and analysis_evidence_complete,
        },
        "source": {
            "battleStatsPath": "battle_stats.json",
            "cycleReportPath": rel_output_path(OUTPUT_JSON),
            "generatedBy": "scripts/devstream_cycle_report.py",
            "sourceCommit": source_commit,
            "noRuntimeActions": True,
        },
    }


def summarize_discord_delivery(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "status": "missing",
            "pending": None,
            "pendingBattleResults": None,
            "pendingEventTypes": {},
            "pendingAgeBuckets": {},
            "pendingPlaceholderFieldCounts": {},
            "pendingBattleResultStructuredFields": {},
            "stalePendingBacklog": None,
            "stalePendingBattleResults": None,
            "freshPendingBacklog": None,
            "freshPendingBattleResults": None,
            "staleAfterSeconds": None,
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": None,
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "dnsFailures": None,
            "webhookFailures": None,
            "healthStatus": "missing",
            "battle_id": None,
            "winner": None,
            "loser": None,
            "turns": None,
            "proof": None,
            "analysis": None,
            "currentBattleState": None,
            "whyItMatters": None,
            "nextHermesAction": None,
            "proofReadiness": None,
            "blockers": ["Discord delivery proof is missing"],
            "secretValuesPrinted": False,
        }
    queue = payload.get("queue") if isinstance(payload.get("queue"), dict) else {}
    return {
        "status": payload.get("status") or "unknown",
        "cycleId": payload.get("cycleId"),
        "destinationAlias": payload.get("destinationAlias"),
        "battleIds": payload.get("battleIds") if isinstance(payload.get("battleIds"), list) else [],
        "pending": queue.get("pending"),
        "pendingBattleResults": queue.get("pendingBattleResults"),
        "pendingEventTypes": queue.get("pendingEventTypes") if isinstance(queue.get("pendingEventTypes"), dict) else {},
        "pendingAgeBuckets": queue.get("pendingAgeBuckets") if isinstance(queue.get("pendingAgeBuckets"), dict) else {},
        "pendingPlaceholderFieldCounts": (
            queue.get("pendingPlaceholderFieldCounts")
            if isinstance(queue.get("pendingPlaceholderFieldCounts"), dict)
            else {}
        ),
        "pendingBattleResultStructuredFields": (
            queue.get("pendingBattleResultStructuredFields")
            if isinstance(queue.get("pendingBattleResultStructuredFields"), dict)
            else {}
        ),
        "stalePendingBacklog": queue.get("stalePendingBacklog"),
        "stalePendingBattleResults": queue.get("stalePendingBattleResults"),
        "freshPendingBacklog": queue.get("freshPendingBacklog"),
        "freshPendingBattleResults": queue.get("freshPendingBattleResults"),
        "staleAfterSeconds": queue.get("staleAfterSeconds"),
        "oldestPendingAgeSeconds": queue.get("oldestPendingAgeSeconds"),
        "deliveryFailures": queue.get("deliveryFailures"),
        "failedEventTypes": queue.get("failedEventTypes") if isinstance(queue.get("failedEventTypes"), dict) else {},
        "expiredEventTypes": queue.get("expiredEventTypes") if isinstance(queue.get("expiredEventTypes"), dict) else {},
        "statusCounts": queue.get("statusCounts") if isinstance(queue.get("statusCounts"), dict) else {},
        "dnsFailures": queue.get("dnsFailures"),
        "webhookFailures": queue.get("webhookFailures"),
        "failureTypes": queue.get("failureTypes") if isinstance(queue.get("failureTypes"), dict) else {},
        "healthStatus": queue.get("healthStatus") or (queue.get("health") or {}).get("status"),
        "errorCode": payload.get("errorCode"),
        "battle_id": payload.get("battle_id"),
        "winner": payload.get("winner"),
        "loser": payload.get("loser"),
        "turns": payload.get("turns"),
        "proof": payload.get("proof") if isinstance(payload.get("proof"), dict) else None,
        "analysis": payload.get("analysis") if isinstance(payload.get("analysis"), dict) else None,
        "currentBattleState": (
            (payload.get("analysis") or {}).get("currentBattleState")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "whyItMatters": (
            (payload.get("analysis") or {}).get("whyItMatters")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "nextHermesAction": (
            (payload.get("analysis") or {}).get("nextHermesAction")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "proofReadiness": (
            (payload.get("analysis") or {}).get("proofReadiness")
            if isinstance(payload.get("analysis"), dict)
            else None
        ),
        "reportSummary": payload.get("reportSummary") if isinstance(payload.get("reportSummary"), dict) else {},
        "blockers": [str(item) for item in payload.get("blockers") or []],
        "reportPaths": payload.get("reportPaths") if isinstance(payload.get("reportPaths"), dict) else {},
        "secretValuesPrinted": bool(payload.get("secretValuesPrinted")),
    }


def summarize_queue_backlog() -> dict[str, Any]:
    try:
        from infrastructure import event_queue_lib

        queue_file = ROOT / "events_queue.json"
        if queue_file.exists():
            events = json.loads(queue_file.read_text(encoding="utf-8", errors="replace") or "[]")
            if not isinstance(events, list):
                raise ValueError("event queue root is not a list")
        else:
            events = []
    except Exception as exc:
        health = {
            "available": False,
            "ready": True,
            "status": "unavailable",
            "pendingBacklog": None,
            "pendingAgeBuckets": {},
            "pendingPlaceholderFieldCounts": {},
            "pendingBattleResultStructuredFields": {},
            "stalePendingBacklog": None,
            "stalePendingBattleResults": None,
            "freshPendingBacklog": None,
            "freshPendingBattleResults": None,
            "staleAfterSeconds": None,
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": None,
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "dnsFailures": None,
            "webhookFailures": None,
            "failureTypes": {},
            "blockers": [],
        }
        return {
            "available": False,
            "pending": None,
            "pendingBattleResults": None,
            "pendingEventTypes": {},
            "pendingAgeBuckets": {},
            "pendingPlaceholderFieldCounts": {},
            "pendingBattleResultStructuredFields": {},
            "pendingBacklog": None,
            "failedEventTypes": {},
            "expiredEventTypes": {},
            "statusCounts": {},
            "oldestPendingAgeSeconds": None,
            "deliveryFailures": None,
            "dnsFailures": None,
            "webhookFailures": None,
            "failureTypes": {},
            "backlogClassification": {
                "status": "unavailable",
                "severity": "hard-blocker",
                "whyItMatters": "HERMES cannot inspect Discord backlog because the queue could not be read.",
                "nextHermesAction": "repair queue readability before proof handoff",
                "blocking": True,
            },
            "proofReadiness": {
                "status": "queue-unavailable",
                "readyForProofHandoff": False,
                "pendingBattleResults": None,
                "machineActionablePendingBattleResults": 0,
                "missingStructuredFieldCounts": {},
                "nextHermesAction": "repair queue readability before proof handoff",
                "blockers": [f"event queue could not be read: {exc}"],
            },
            "nextHermesAction": "repair queue readability before proof handoff",
            "healthStatus": "unavailable",
            "ready": True,
            "health": health,
            "blockers": [f"event queue could not be read: {exc}"],
        }

    pending = [event for event in events if isinstance(event, dict) and event.get("status") == "pending"]
    event_types: dict[str, int] = {}
    for event in pending:
        event_type = str(event.get("event_type") or "unknown")
        event_types[event_type] = event_types.get(event_type, 0) + 1
    health = event_queue_lib.queue_health_summary(events)
    return {
        "available": True,
        "total": len(events),
        "pending": len(pending),
        "pendingBattleResults": event_types.get("battle_result", 0),
        "pendingEventTypes": event_types,
        "pendingAgeBuckets": health.get("pendingAgeBuckets", {}),
        "pendingPlaceholderFieldCounts": health.get("pendingPlaceholderFieldCounts", {}),
        "pendingBattleResultStructuredFields": health.get("pendingBattleResultStructuredFields", {}),
        "stalePendingBacklog": health.get("stalePendingBacklog"),
        "stalePendingBattleResults": health.get("stalePendingBattleResults"),
        "freshPendingBacklog": health.get("freshPendingBacklog"),
        "freshPendingBattleResults": health.get("freshPendingBattleResults"),
        "staleAfterSeconds": health.get("staleAfterSeconds"),
        "pendingBacklog": health.get("pendingBacklog"),
        "failedEventTypes": health.get("failedEventTypes", {}),
        "expiredEventTypes": health.get("expiredEventTypes", {}),
        "statusCounts": health.get("statusCounts", {}),
        "oldestPendingAgeSeconds": health.get("oldestPendingAgeSeconds"),
        "oldestPendingEventId": health.get("oldestPendingEventId"),
        "deliveryFailures": health.get("deliveryFailures"),
        "retryingDeliveries": health.get("retryingDeliveries"),
        "expiredDeliveries": health.get("expiredDeliveries"),
        "dnsFailures": health.get("dnsFailures"),
        "webhookFailures": health.get("webhookFailures"),
        "failureTypes": health.get("failureTypes", {}),
        "backlogClassification": health.get("backlogClassification", {}),
        "proofReadiness": health.get("proofReadiness", {}),
        "nextHermesAction": health.get("nextHermesAction"),
        "healthStatus": health.get("status"),
        "ready": health.get("ready"),
        "health": health,
        "blockers": [],
    }


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def summarize_unconsumed_battles(stats: Any, autoresearch: Any) -> dict[str, Any]:
    battles = stats.get("battles") if isinstance(stats, dict) and isinstance(stats.get("battles"), list) else []
    autoresearch = autoresearch if isinstance(autoresearch, dict) else {}
    batch = autoresearch.get("batch") if isinstance(autoresearch.get("batch"), dict) else {}
    end_battle_id = batch.get("end_battle_id") or batch.get("endBattleId")
    end_timestamp = _parse_time(batch.get("end_timestamp") or batch.get("endTimestamp"))
    unconsumed: list[dict[str, Any]] = []
    seen_end_id = not end_battle_id
    for battle in battles:
        if not isinstance(battle, dict):
            continue
        battle_id = battle.get("battle_id") or battle.get("id")
        battle_time = _parse_time(battle.get("timestamp") or battle.get("created_at"))
        if end_timestamp and battle_time and battle_time > end_timestamp:
            unconsumed.append(battle)
            continue
        if end_battle_id and battle_id == end_battle_id:
            seen_end_id = True
            continue
        if end_battle_id and seen_end_id:
            unconsumed.append(battle)
    losses = [battle for battle in unconsumed if str(battle.get("result") or "").lower() in {"loss", "lost"}]
    return {
        "latestAnalyzedBattleId": end_battle_id,
        "totalBattles": len(battles),
        "unconsumedCount": len(unconsumed),
        "unconsumedLosses": len(losses),
        "battleIds": [str(battle.get("battle_id") or battle.get("id") or "") for battle in unconsumed[:10]],
        "lossBattleIds": [str(battle.get("battle_id") or battle.get("id") or "") for battle in losses[:10]],
    }


def completed_cycle_evidence_available(
    *,
    active: dict[str, Any],
    unconsumed: dict[str, Any],
    report_exists: bool,
    autoresearch: Any | None = None,
) -> bool:
    autoresearch_payload = autoresearch if isinstance(autoresearch, dict) else {}
    regression = (
        autoresearch_payload.get("regression")
        if isinstance(autoresearch_payload.get("regression"), dict)
        else {}
    )
    return (
        _safe_count(active.get("battleCount")) == 0
        and bool(unconsumed.get("latestAnalyzedBattleId"))
        and _safe_count(unconsumed.get("totalBattles")) > 0
        and _safe_count(unconsumed.get("unconsumedCount")) == 0
        and report_exists
        and not autoresearch_has_unsupported_claims(autoresearch)
        and autoresearch_evidence_integrity(autoresearch).get("present") is True
        and positive_improvement_signal(autoresearch_payload, regression).get("ok") is True
    )


def autoresearch_evidence_integrity(payload: Any) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    integrity = payload.get("evidence_integrity") if isinstance(payload.get("evidence_integrity"), dict) else {}
    unsupported = integrity.get("claims_without_evidence") if isinstance(integrity.get("claims_without_evidence"), list) else []
    return {
        "present": bool(integrity),
        "lossCount": integrity.get("loss_count"),
        "lossesWithReplayJson": integrity.get("losses_with_replay_json"),
        "lossesWithDecisionTrace": integrity.get("losses_with_decision_trace"),
        "claimsWithoutEvidenceCount": len(unsupported),
        "claimsWithoutEvidence": unsupported[:10],
        "blocksCompletionProof": bool(unsupported),
    }


def positive_improvement_signal(autoresearch: dict[str, Any], regression: dict[str, Any]) -> dict[str, Any]:
    trend = str(regression.get("status") or autoresearch.get("performance_trend_status") or "").strip().lower()
    rating_delta = regression.get("rating_delta") or regression.get("ratingDelta")
    try:
        numeric_rating_delta = float(rating_delta)
    except (TypeError, ValueError):
        numeric_rating_delta = None
    win_rate_delta = regression.get("win_rate_delta") or regression.get("winRateDelta")
    try:
        numeric_win_rate_delta = float(win_rate_delta)
    except (TypeError, ValueError):
        numeric_win_rate_delta = None
    ok = (
        trend in {"improving", "better", "reduced"}
        or (numeric_rating_delta is not None and numeric_rating_delta > 0)
        or (numeric_win_rate_delta is not None and numeric_win_rate_delta > 0)
    )
    return {
        "ok": ok,
        "status": "positive" if ok else "missing-or-nonpositive",
        "trend": trend or "unknown",
        "ratingDelta": rating_delta,
        "winRateDelta": win_rate_delta,
    }


def autoresearch_has_unsupported_claims(payload: Any) -> bool:
    return bool(autoresearch_evidence_integrity(payload).get("blocksCompletionProof"))


def summarize_health(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "healthy": False,
            "status": "unknown",
            "readiness": {},
            "blockers": ["devstream health probe did not return a payload"],
        }
    return {
        "healthy": bool(payload.get("healthy")),
        "status": payload.get("status"),
        "readyForLiveFocus": bool(payload.get("readyForLiveFocus")),
        "running": bool(payload.get("running")),
        "activeBattleCount": payload.get("activeBattleCount"),
        "readiness": payload.get("readiness") if isinstance(payload.get("readiness"), dict) else {},
        "devstreamReporting": payload.get("devstreamReporting") if isinstance(payload.get("devstreamReporting"), dict) else {},
        "runtimeOwnership": payload.get("runtimeOwnership") if isinstance(payload.get("runtimeOwnership"), dict) else {},
        "blockers": [str(item) for item in payload.get("blockers") or []],
    }


def build_completion_payload(cycle: dict[str, Any], autoresearch: Any) -> dict[str, Any]:
    autoresearch = autoresearch if isinstance(autoresearch, dict) else {}
    batch = autoresearch.get("batch") if isinstance(autoresearch.get("batch"), dict) else {}
    regression = autoresearch.get("regression") if isinstance(autoresearch.get("regression"), dict) else {}
    trend = regression.get("status") or "unknown"
    report = cycle.get("autoresearch", {}).get("report", {}) if isinstance(cycle.get("autoresearch"), dict) else {}
    report_exists = bool(report.get("exists"))
    active_battles = int((cycle.get("activeBattles") or {}).get("battleCount") or 0)
    pending_delivery = int((cycle.get("queueBacklog") or {}).get("pending") or 0)
    local_discord_proof = bool(cycle.get("discordBacklogClassifiedForLocalHandoff"))
    unconsumed_count = int((cycle.get("unconsumedBattles") or {}).get("unconsumedCount") or 0)
    blockers = list(cycle.get("blockers") or [])
    warnings = list(cycle.get("warnings") or [])
    integrity = autoresearch_evidence_integrity(autoresearch)
    improvement = positive_improvement_signal(autoresearch, regression)
    if active_battles:
        active_msg = "active battles are still present; completion proof is not final"
        if active_msg not in blockers:
            blockers.append(active_msg)
    if not integrity["present"]:
        blockers.append("autoresearch evidence_integrity is missing; replay/loss-analysis proof is required")
    if integrity["blocksCompletionProof"]:
        blockers.append(
            f"autoresearch has {integrity['claimsWithoutEvidenceCount']} unsupported mechanics/strategy claim(s); completion proof is not final"
        )
    if not improvement["ok"]:
        blockers.append(
            "performance improvement signal is missing or nonpositive; HERMES must not accept this cycle as progress"
        )
    if (
        pending_delivery
        and not local_discord_proof
        and not any(str(item).startswith("pending Discord delivery remains") for item in blockers)
    ):
        blockers.append(f"pending Discord delivery remains: {pending_delivery} event(s)")
    if unconsumed_count and not any(str(item).startswith("unconsumed battles remain") for item in blockers):
        blockers.append(f"unconsumed battles remain after latest autoresearch batch: {unconsumed_count} battle(s)")
    if not report_exists:
        warnings.append("autoresearch markdown report was not available for completion proof")
    active_improvement_verified = bool(
        active_battles == 0
        and report_exists
        and not blockers
        and integrity["present"]
        and not integrity["blocksCompletionProof"]
        and improvement["ok"]
    )
    return {
        "schemaVersion": "fouler-play-devstream-completion/v1",
        "projectId": "fouler-play",
        "proofKind": "completed-cycle-proof",
        "checkedAtUtc": cycle["generatedAt"],
        "status": "cycle-proof-current" if not blockers else "cycle-proof-blocked",
        "latestBattleId": batch.get("end_battle_id") or batch.get("endBattleId"),
        "latestBattleAt": batch.get("end_timestamp") or batch.get("endTimestamp"),
        "battleCount": batch.get("size") or autoresearch.get("window_size"),
        "latestBattleLearningVerified": bool(autoresearch and report_exists and not blockers),
        "evidenceIntegrity": integrity,
        "performanceImprovementVerified": bool(improvement["ok"]),
        "performanceTrendStatus": improvement["trend"],
        "improvementSignalStatus": improvement["status"],
        "improvementSignal": improvement,
        "winRate": autoresearch.get("win_rate"),
        "finalRating": (cycle.get("streamStatus") or {}).get("elo"),
        "ratingDelta": regression.get("rating_delta") or regression.get("ratingDelta"),
        "activeImprovementVerified": active_improvement_verified,
        "activeBattleTelemetryPresent": active_battles > 0,
        "activeBattleTelemetryIsCompletionProof": False,
        "reportPaths": {
            "cycleReport": str(OUTPUT_JSON.relative_to(ROOT)),
            "cycleMarkdown": str(OUTPUT_MD.relative_to(ROOT)),
            "autoresearchJson": (cycle.get("autoresearch") or {}).get("json", {}).get("path"),
            "autoresearchMarkdown": report.get("path"),
        },
        "blockers": blockers,
        "warnings": warnings,
        "nextActions": [
            "Continue bounded battle batches and keep completion.json fresh after each cycle proof."
        ],
    }


def _safe_count(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _limited_strings(items: object, limit: int = 8) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item) for item in items[:limit]]


def build_proof_status_payload(cycle: dict[str, Any], completion: dict[str, Any]) -> dict[str, Any]:
    active = cycle.get("activeBattles") if isinstance(cycle.get("activeBattles"), dict) else {}
    queue = cycle.get("queueBacklog") if isinstance(cycle.get("queueBacklog"), dict) else {}
    delivery = cycle.get("discordDelivery") if isinstance(cycle.get("discordDelivery"), dict) else {}
    active_count = _safe_count(active.get("battleCount"))
    pending = _safe_count(queue.get("pending"))
    completion_ready = completion.get("status") == "cycle-proof-current"
    improvement_ready = bool(completion.get("performanceImprovementVerified"))
    local_discord_proof = bool(cycle.get("discordBacklogClassifiedForLocalHandoff"))

    if active_count:
        status = "active-telemetry-not-final-proof"
    elif pending and not local_discord_proof:
        status = "discord-backlog-blocked"
    elif pending and local_discord_proof and completion_ready and improvement_ready:
        status = "local-discord-proof-classified"
    elif completion_ready and improvement_ready:
        status = "proof-ready"
    else:
        status = "blocked"

    return {
        "schemaVersion": "fouler-play-proof-status/v1",
        "projectId": "fouler-play",
        "generatedAt": cycle["generatedAt"],
        "status": status,
        "readyForProofHandoff": status in {"proof-ready", "local-discord-proof-classified"},
        "secretValuesPrinted": False,
        "activeBattleTelemetry": {
            "classification": active.get("classification") or "unknown",
            "battleCount": active_count,
            "rawBattleCount": _safe_count(active.get("rawBattleCount")),
            "ghostBattleCount": _safe_count(active.get("ghostBattleCount")),
            "battleIds": _limited_strings(active.get("battleIds"), 5),
            "ghostBattleIds": _limited_strings(active.get("ghostBattleIds"), 5),
            "isCompletedProof": False,
            "note": active.get("proofNote") or "active battle telemetry is separate from completed proof",
        },
        "completedCycleProof": {
            "classification": completion.get("proofKind") or "completed-cycle-proof",
            "status": completion.get("status"),
            "latestBattleId": completion.get("latestBattleId"),
            "latestBattleAt": completion.get("latestBattleAt"),
            "latestBattleLearningVerified": bool(completion.get("latestBattleLearningVerified")),
            "performanceImprovementVerified": bool(completion.get("performanceImprovementVerified")),
            "performanceTrendStatus": completion.get("performanceTrendStatus"),
            "improvementSignalStatus": completion.get("improvementSignalStatus"),
            "isCurrent": completion_ready,
        },
        "discordBacklog": {
            "healthStatus": queue.get("healthStatus"),
            "pending": pending,
            "pendingBattleResults": queue.get("pendingBattleResults"),
            "backlogClassification": (
                queue.get("backlogClassification")
                if isinstance(queue.get("backlogClassification"), dict)
                else {}
            ),
            "pendingEventTypes": queue.get("pendingEventTypes") if isinstance(queue.get("pendingEventTypes"), dict) else {},
            "pendingAgeBuckets": queue.get("pendingAgeBuckets") if isinstance(queue.get("pendingAgeBuckets"), dict) else {},
            "pendingPlaceholderFieldCounts": (
                queue.get("pendingPlaceholderFieldCounts")
                if isinstance(queue.get("pendingPlaceholderFieldCounts"), dict)
                else {}
            ),
            "pendingBattleResultStructuredFields": (
                queue.get("pendingBattleResultStructuredFields")
                if isinstance(queue.get("pendingBattleResultStructuredFields"), dict)
                else {}
            ),
            "stalePendingBacklog": queue.get("stalePendingBacklog"),
            "stalePendingBattleResults": queue.get("stalePendingBattleResults"),
            "freshPendingBacklog": queue.get("freshPendingBacklog"),
            "freshPendingBattleResults": queue.get("freshPendingBattleResults"),
            "staleAfterSeconds": queue.get("staleAfterSeconds"),
            "oldestPendingAgeSeconds": queue.get("oldestPendingAgeSeconds"),
            "deliveryFailures": queue.get("deliveryFailures"),
            "dnsFailures": queue.get("dnsFailures"),
            "webhookFailures": queue.get("webhookFailures"),
            "proofReadiness": (
                queue.get("proofReadiness")
                if isinstance(queue.get("proofReadiness"), dict)
                else {}
            ),
            "nextHermesAction": queue.get("nextHermesAction"),
        },
        "discordDeliveryProof": {
            "status": delivery.get("status"),
            "cycleId": delivery.get("cycleId"),
            "battle_id": delivery.get("battle_id"),
            "winner": delivery.get("winner"),
            "loser": delivery.get("loser"),
            "turns": delivery.get("turns"),
            "proof": delivery.get("proof") if isinstance(delivery.get("proof"), dict) else None,
            "analysis": delivery.get("analysis") if isinstance(delivery.get("analysis"), dict) else None,
            "currentBattleState": delivery.get("currentBattleState"),
            "whyItMatters": delivery.get("whyItMatters"),
            "nextHermesAction": delivery.get("nextHermesAction"),
            "proofReadiness": delivery.get("proofReadiness"),
            "secretValuesPrinted": bool(delivery.get("secretValuesPrinted")),
        },
        "nextHermesAction": cycle.get("nextHermesAction"),
        "blockers": _limited_strings(cycle.get("blockers"), 12),
        "warnings": _limited_strings(cycle.get("warnings"), 12),
        "artifactPaths": {
            "proofStatus": str(OUTPUT_PROOF_STATUS.relative_to(ROOT)),
            "cycleReport": str(OUTPUT_JSON.relative_to(ROOT)),
            "cycleMarkdown": str(OUTPUT_MD.relative_to(ROOT)),
            "completion": str(OUTPUT_COMPLETION.relative_to(ROOT)),
            "discordReporting": str(DISCORD_REPORTING.relative_to(ROOT)),
            "discordDelivery": str(DISCORD_DELIVERY.relative_to(ROOT)),
            "eventQueue": "events_queue.json",
        },
    }


def build_handoff_action(
    *,
    active: dict[str, Any],
    queue: dict[str, Any],
    delivery: dict[str, Any],
    health: dict[str, Any],
    unconsumed: dict[str, Any],
) -> dict[str, Any]:
    active_count = _safe_count(active.get("battleCount"))
    delivery_action = delivery.get("nextHermesAction")
    queue_action = queue.get("nextHermesAction")
    health_reporting = health.get("devstreamReporting") if isinstance(health.get("devstreamReporting"), dict) else {}
    if active_count:
        state = f"{active_count} active battle(s) in flight; telemetry is useful but not completed proof"
    elif unconsumed.get("unconsumedCount"):
        state = f"{unconsumed.get('unconsumedCount')} completed battle(s) still need autoresearch consumption"
    else:
        state = "no active battle telemetry; rely on latest completed cycle proof"
    backlog = queue.get("backlogClassification") if isinstance(queue.get("backlogClassification"), dict) else {}
    local_discord_ready = local_discord_proof_classified(queue, delivery)
    if backlog.get("blocking") and queue_action and not local_discord_ready:
        next_action = str(queue_action)
    elif unconsumed.get("unconsumedLosses"):
        next_action = "run loss analysis/autoresearch on unconsumed losses before claiming learning progress"
    elif queue_action:
        next_action = (
            "transport Discord backlog when approved; local redacted proof is classified for rehearsal handoff"
            if local_discord_ready
            else str(queue_action)
        )
    elif delivery_action:
        next_action = str(delivery_action)
    else:
        next_action = str(health_reporting.get("nextHermesAction") or "run one bounded battle cycle, drain proof, and refresh reports")
    why = (
        "Discord delivery remains pending, but every queued battle_result has redacted local proof fields for HERMES handoff."
        if local_discord_ready
        else backlog.get("whyItMatters")
        or health_reporting.get("whyItMatters")
        or "HERMES needs clean battle, analysis, and Discord proof before fouler-play can be stream-ready."
    )
    return {
        "currentBattleState": state,
        "whyItMatters": str(why),
        "nextHermesAction": next_action,
        "backlogClassification": backlog,
        "proofReadiness": queue.get("proofReadiness") if isinstance(queue.get("proofReadiness"), dict) else {},
    }


def _delivery_proof_ready(delivery: dict[str, Any]) -> bool:
    proof_readiness = delivery.get("proofReadiness") if isinstance(delivery.get("proofReadiness"), dict) else {}
    return bool(proof_readiness.get("readyForHermes") or proof_readiness.get("status") == "proof-ready")


def local_discord_proof_classified(queue: dict[str, Any], delivery: dict[str, Any]) -> bool:
    proof_readiness = queue.get("proofReadiness") if isinstance(queue.get("proofReadiness"), dict) else {}
    return (
        _safe_count(queue.get("pending")) > 0
        and bool(proof_readiness.get("readyForLocalProofHandoff"))
        and delivery.get("status") == "dry-run"
        and _delivery_proof_ready(delivery)
        and not bool(delivery.get("secretValuesPrinted"))
        and not _safe_count(queue.get("deliveryFailures"))
        and not _safe_count(queue.get("dnsFailures"))
        and not _safe_count(queue.get("webhookFailures"))
    )


def _is_idle_runtime_blocker(value: object) -> bool:
    return IDLE_RUNTIME_BLOCKER in str(value)


def build_payload() -> dict[str, Any]:
    active_path = ROOT / "active_battles.json"
    stream_path = ROOT / "stream_status.json"
    daily_path = ROOT / "daily_stats.json"
    stats_path = ROOT / "battle_stats.json"
    autoresearch_json = ROOT / "replay_analysis" / "autoresearch_latest.json"
    autoresearch_md = ROOT / "replay_analysis" / "reports" / "autoresearch_latest.md"
    active = read_json(active_path)
    stream = read_json(stream_path)
    daily = read_json(daily_path)
    stats = read_json(stats_path)
    discord_reporting = read_json(DISCORD_REPORTING)
    discord_delivery = read_json(DISCORD_DELIVERY)
    queue_backlog = summarize_queue_backlog()
    delivery = summarize_discord_delivery(discord_delivery)
    unconsumed = summarize_unconsumed_battles(stats, read_json(autoresearch_json))
    report_exists = autoresearch_md.exists()
    active_telemetry = active_battle_telemetry_status(active_path)
    active_summary = reconcile_active_battles(summarize_active_battles(active), stats)
    active_summary.update({
        "stale": active_telemetry["stale"],
        "ageSeconds": active_telemetry["ageSeconds"],
        "staleAfterSeconds": active_telemetry["staleAfterSeconds"],
    })
    completed_cycle_available = completed_cycle_evidence_available(
        active=active_summary,
        unconsumed=unconsumed,
        report_exists=report_exists,
        autoresearch=read_json(autoresearch_json),
    )
    discord_backlog_classified = local_discord_proof_classified(queue_backlog, delivery)
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        health = summarize_health(devstream_health.build_payload(check_http=True))
    except Exception as exc:
        health = summarize_health(None)
        health["blockers"] = [f"devstream health probe failed: {exc}"]
    if active_summary.get("ghostBattleCount"):
        warnings.append(
            "active_battles.json contains terminal battle id(s) already present in battle_stats.json; "
            f"not counting ghost battle telemetry as live proof: {', '.join(active_summary.get('ghostBattleIds', [])[:5])}"
        )
    if active_summary["battleCount"]:
        warnings.append("active battles are still present; cycle report is not a final handoff yet")
    runtime_ownership = health.get("runtimeOwnership") if isinstance(health.get("runtimeOwnership"), dict) else {}
    battle_runner_count = int(runtime_ownership.get("battleRunnerCount") or 0)
    if active_summary.get("stale") and battle_runner_count == 0:
        stale_msg = (
            "active_battles.json is stale and no battle runner owns the runtime; "
            "clear/adopt runtime state before proof handoff"
        )
        if not any("active_battles.json is stale" in str(item) for item in blockers):
            blockers.append(stale_msg)
    if queue_backlog["blockers"] and not discord_backlog_classified:
        blockers.extend(queue_backlog["blockers"])
    elif queue_backlog["blockers"] and discord_backlog_classified:
        warnings.append("Discord queue backlog is locally classified with redacted dry-run proof; transport remains pending.")
    if queue_backlog.get("pending") and not discord_backlog_classified:
        blockers.append(
            f"pending Discord delivery remains: {queue_backlog['pending']} event(s), "
            f"{queue_backlog.get('pendingBattleResults') or 0} battle_result event(s)"
        )
    elif queue_backlog.get("pending") and discord_backlog_classified:
        warnings.append(
            f"pending Discord delivery remains locally classified: {queue_backlog['pending']} event(s), "
            f"{queue_backlog.get('pendingBattleResults') or 0} battle_result event(s)"
        )
    if queue_backlog.get("deliveryFailures"):
        blockers.append(f"Discord queue has {queue_backlog['deliveryFailures']} failed delivery event(s)")
    if queue_backlog.get("dnsFailures"):
        blockers.append(f"Discord queue has {queue_backlog['dnsFailures']} DNS failure(s)")
    if queue_backlog.get("webhookFailures"):
        blockers.append(f"Discord queue has {queue_backlog['webhookFailures']} webhook failure(s)")
    if delivery["status"] in {"missing", "failed", "rate-limited", "blocked"}:
        blockers.append(f"Discord delivery proof status is {delivery['status']}")
    if delivery.get("dnsFailures"):
        blockers.append(f"Discord delivery proof reports {delivery['dnsFailures']} DNS failure(s)")
    if delivery.get("webhookFailures"):
        blockers.append(f"Discord delivery proof reports {delivery['webhookFailures']} webhook failure(s)")
    if delivery.get("secretValuesPrinted"):
        blockers.append("Discord proof reports secretValuesPrinted=true")
    if unconsumed["unconsumedCount"]:
        blockers.append(f"unconsumed battles remain after latest autoresearch batch: {unconsumed['unconsumedCount']} battle(s)")
    if unconsumed["unconsumedLosses"]:
        blockers.append(f"loss-learning is blocked until {unconsumed['unconsumedLosses']} unconsumed loss battle(s) are analyzed")
    if not health["healthy"]:
        health_blockers = health["blockers"] or ["devstream health is not ready"]
        for blocker in health_blockers:
            if _is_idle_runtime_blocker(blocker) and completed_cycle_available:
                warnings.append(
                    "runtime is idle after completed cycle proof; plan restoration only after readiness gate allows project starts"
                )
            elif blocker not in blockers:
                blockers.append(blocker)
    if not stream_path.exists() and not daily_path.exists() and not stats_path.exists():
        warnings.append("no battle/stat truth files exist yet; run a bounded session before treating this as performance proof")
    if not report_exists:
        warnings.append("autoresearch report is missing; DEKU should run replay analysis before claiming learning progress")
    handoff_action = build_handoff_action(
        active=active_summary,
        queue=queue_backlog,
        delivery=delivery,
        health=health,
        unconsumed=unconsumed,
    )
    return {
        "schemaVersion": "fouler-play-cycle-report/v1",
        "projectId": "fouler-play",
        "generatedAt": iso_now(),
        "readyForHandoff": (
            not blockers
            and active_summary["battleCount"] == 0
            and (not queue_backlog.get("pending") or discord_backlog_classified)
        ),
        "completedCycleEvidenceAvailable": completed_cycle_available,
        "discordBacklogClassifiedForLocalHandoff": discord_backlog_classified,
        "currentBattleState": handoff_action["currentBattleState"],
        "whyItMatters": handoff_action["whyItMatters"],
        "nextHermesAction": handoff_action["nextHermesAction"],
        "backlogClassification": handoff_action["backlogClassification"],
        "proofReadiness": handoff_action["proofReadiness"],
        "blockers": blockers,
        "warnings": warnings,
        "health": health,
        "activeBattles": active_summary,
        "queueBacklog": queue_backlog,
        "discordDelivery": delivery,
        "discordReporting": {
            "json": file_meta(DISCORD_REPORTING),
            "status": discord_reporting.get("status") if isinstance(discord_reporting, dict) else "missing",
            "secretValuesPrinted": bool(discord_reporting.get("secretValuesPrinted")) if isinstance(discord_reporting, dict) else False,
        },
        "unconsumedBattles": unconsumed,
        "streamStatus": summarize_record(stream),
        "dailyStats": summarize_record(daily),
        "battleStatsShape": list(stats.keys())[:20] if isinstance(stats, dict) else [],
        "autoresearch": {
            "json": file_meta(autoresearch_json),
            "report": file_meta(autoresearch_md),
        },
        "truthFiles": {
            "activeBattles": file_meta(active_path),
            "streamStatus": file_meta(stream_path),
            "dailyStats": file_meta(daily_path),
            "battleStats": file_meta(stats_path),
            "discordReporting": file_meta(DISCORD_REPORTING),
            "discordDelivery": file_meta(DISCORD_DELIVERY),
            "proofStatus": file_meta(OUTPUT_PROOF_STATUS),
        },
        "operatorNote": "A fouler-play devstream cycle should run a bounded battle batch, stop cleanly, write this report, then let DEKU analyze replay/decision evidence before the next batch.",
    }


def _inline_counts(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "none"
    return ", ".join(f"{key}={value[key]}" for key in sorted(value))


def write_markdown(payload: dict[str, Any]) -> None:
    lines = [
        "# fouler-play Cycle Report",
        "",
        f"- Generated: `{payload['generatedAt']}`",
        f"- Ready for handoff: `{payload['readyForHandoff']}`",
        f"- Active battles: `{payload['activeBattles']['battleCount']}`",
        f"- Active telemetry class: `{payload['activeBattles'].get('classification')}`",
        f"- Pending Discord delivery: `{payload['queueBacklog'].get('pending')}`",
        f"- Pending battle_result events: `{payload['queueBacklog'].get('pendingBattleResults')}`",
        f"- Pending event classes: `{_inline_counts(payload['queueBacklog'].get('pendingEventTypes'))}`",
        f"- Pending age buckets: `{_inline_counts(payload['queueBacklog'].get('pendingAgeBuckets'))}`",
        f"- Pending placeholder fields: `{_inline_counts(payload['queueBacklog'].get('pendingPlaceholderFieldCounts'))}`",
        f"- Pending battle_result structured fields: `{_inline_counts(payload['queueBacklog'].get('pendingBattleResultStructuredFields'))}`",
        f"- Oldest pending Discord age seconds: `{payload['queueBacklog'].get('oldestPendingAgeSeconds')}`",
        f"- Discord queue health: `{payload['queueBacklog'].get('healthStatus')}`",
        f"- Discord delivery failures: `{payload['queueBacklog'].get('deliveryFailures')}`",
        f"- Discord DNS/webhook failures: `{payload['queueBacklog'].get('dnsFailures')}` / `{payload['queueBacklog'].get('webhookFailures')}`",
        f"- Discord delivery proof: `{payload['discordDelivery'].get('status')}`",
        f"- Current battle state: `{payload.get('currentBattleState')}`",
        f"- Why it matters: `{payload.get('whyItMatters')}`",
        f"- Next HERMES action: `{payload.get('nextHermesAction')}`",
        f"- Proof readiness: `{(payload.get('proofReadiness') or {}).get('status')}`",
        f"- Unconsumed battles: `{payload['unconsumedBattles'].get('unconsumedCount')}`",
        f"- Unconsumed losses: `{payload['unconsumedBattles'].get('unconsumedLosses')}`",
        f"- Stream ELO: `{payload['streamStatus'].get('elo') or 'unknown'}`",
        f"- Daily record: `{payload['dailyStats'].get('wins') or 0}-{payload['dailyStats'].get('losses') or 0}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = payload.get("blockers") or []
    lines.extend(f"- {item}" for item in blockers) if blockers else lines.append("- none")
    lines.extend([
        "",
        "## Warnings",
        "",
    ])
    warnings = payload.get("warnings") or []
    lines.extend(f"- {item}" for item in warnings) if warnings else lines.append("- none")
    lines.extend(["", payload["operatorNote"], ""])
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def write_completion(payload: dict[str, Any], autoresearch: Any) -> dict[str, Any]:
    completion = build_completion_payload(payload, autoresearch)
    OUTPUT_COMPLETION.write_text(json.dumps(completion, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return completion


def write_proof_status(payload: dict[str, Any], completion: dict[str, Any]) -> dict[str, Any]:
    proof_status = build_proof_status_payload(payload, completion)
    OUTPUT_PROOF_STATUS.write_text(json.dumps(proof_status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return proof_status


def write_elo_proof(
    payload: dict[str, Any],
    stats: Any,
    *,
    account: str | None = None,
    autoresearch: Any = None,
) -> dict[str, Any]:
    proof = build_elo_proof_payload(
        stats, payload, account=account, autoresearch=autoresearch, fetch_live_profile=True
    )
    OUTPUT_ELO_PROOF.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description="Write fouler-play bounded devstream cycle handoff report.")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--account", default=None, help="Showdown account id to stamp into generated ELO proof")
    args = parser.parse_args()
    discord_proof_refresh = refresh_discord_proof_preview() if args.write else None
    payload = build_payload()
    if discord_proof_refresh:
        payload["discordProofRefresh"] = discord_proof_refresh
    if args.write:
        OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        stats = read_json(ROOT / "battle_stats.json")
        autoresearch = read_json(ROOT / "replay_analysis" / "autoresearch_latest.json")
        completion = write_completion(payload, autoresearch)
        write_proof_status(payload, completion)
        write_elo_proof(payload, stats, account=args.account, autoresearch=autoresearch)
        payload.setdefault("truthFiles", {})
        payload["truthFiles"]["completion"] = file_meta(OUTPUT_COMPLETION)
        payload["truthFiles"]["proofStatus"] = file_meta(OUTPUT_PROOF_STATUS)
        payload["truthFiles"]["latestEloProof"] = file_meta(OUTPUT_ELO_PROOF)
        payload["written"] = [str(OUTPUT_JSON), str(OUTPUT_MD), str(OUTPUT_COMPLETION), str(OUTPUT_PROOF_STATUS), str(OUTPUT_ELO_PROOF)]
        OUTPUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_markdown(payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
