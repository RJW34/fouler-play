#!/usr/bin/env python3
"""
Hybrid decision dashboard data + route helpers.

This module is intentionally read-only with respect to gameplay state. It
parses decision traces, merges stream state files, and serves sanitized
dashboard payloads.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

from streaming import state_store

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_DIR = Path(os.getenv("DECISION_TRACE_DIR", "logs/decision_traces"))
if not DEFAULT_TRACE_DIR.is_absolute():
    DEFAULT_TRACE_DIR = ROOT_DIR / DEFAULT_TRACE_DIR

DEFAULT_DASHBOARD_HTML = Path(__file__).resolve().parent / "hybrid_dashboard.html"
DEFAULT_OVERLAY_HTML = Path(__file__).resolve().parent / "hybrid_overlay.html"

DEFAULT_SCAN_INTERVAL_SEC = 1.0
MAX_TURNS_LIMIT = 200
DEFAULT_TURNS_LIMIT = 50
TIMELINE_LIMIT = 20
MAX_CANDIDATES = 8

ALLOWED_DECISION_MODES = {
    "eval",
    "endgame",
    "forced_line",
    "fallback",
    "error",
}
ALLOWED_HYBRID_STATUS = {
    "applied",
    "skipped",
    "error",
    "unavailable",
}
ALLOWED_POLICY = {"eval", "hybrid"}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_ID_CHARS = re.compile(r"[^a-zA-Z0-9_.:-]")
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}\b", flags=re.IGNORECASE),
    re.compile(r"\bOPENAI_API_KEY\b", flags=re.IGNORECASE),
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_secrets(text: str) -> str:
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[redacted]", out)
    return out


def _sanitize_text(value: Any, *, max_len: int = 240, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value)
    text = _CONTROL_CHARS.sub("", text).strip()
    text = _redact_secrets(text)
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text if text else fallback


def _sanitize_id(value: Any, *, max_len: int = 128, fallback: str = "") -> str:
    text = _sanitize_text(value, max_len=max_len, fallback=fallback)
    text = _SAFE_ID_CHARS.sub("", text)
    return text if text else fallback


def _safe_int(
    value: Any,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and num < minimum:
        num = minimum
    if maximum is not None and num > maximum:
        num = maximum
    return num


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 1)


def _parse_iso_timestamp(value: Any, *, fallback_epoch: float | None = None) -> tuple[str, float]:
    fallback_iso = datetime.fromtimestamp(
        fallback_epoch if fallback_epoch is not None else time.time(),
        tz=timezone.utc,
    ).isoformat()
    if not isinstance(value, str) or not value.strip():
        return fallback_iso, fallback_epoch if fallback_epoch is not None else time.time()

    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        epoch = dt.timestamp()
        return dt.isoformat(), epoch
    except Exception:
        return fallback_iso, fallback_epoch if fallback_epoch is not None else time.time()


def _sanitize_policy(value: Any) -> str:
    policy = _sanitize_text(value, max_len=32, fallback="").lower()
    if policy in ALLOWED_POLICY:
        return policy
    return "eval"


def _sanitize_decision_mode(value: Any) -> str:
    mode = _sanitize_text(value, max_len=32, fallback="").lower()
    if mode in ALLOWED_DECISION_MODES:
        return mode
    return "unknown"


def _sanitize_hybrid_status(value: Any) -> str:
    status = _sanitize_text(value, max_len=32, fallback="").lower()
    if status in ALLOWED_HYBRID_STATUS:
        return status
    return "unavailable"


def _candidate_list_from_eval_scores(payload: dict[str, Any]) -> list[str]:
    scores = payload.get("eval_scores_raw")
    if not isinstance(scores, dict):
        return []
    scored: list[tuple[str, float]] = []
    for raw_choice, raw_score in scores.items():
        choice = _sanitize_text(raw_choice, max_len=80, fallback="")
        if not choice:
            continue
        scored.append((choice, _safe_float(raw_score, default=0.0)))
    scored.sort(key=lambda item: item[1], reverse=True)
    ordered = [choice for choice, _ in scored[:MAX_CANDIDATES]]
    return ordered


def _candidate_list_from_hybrid(hybrid: dict[str, Any]) -> list[str]:
    raw_candidates = hybrid.get("candidates")
    if not isinstance(raw_candidates, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_candidates:
        if isinstance(item, dict):
            raw_decision = item.get("decision")
        else:
            raw_decision = item
        decision = _sanitize_text(raw_decision, max_len=80, fallback="")
        if not decision or decision in seen:
            continue
        seen.add(decision)
        out.append(decision)
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def parse_trace_turn(
    payload: dict[str, Any],
    *,
    source_name: str,
    fallback_epoch: float,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    battle_id = _sanitize_id(payload.get("battle_tag"), max_len=96, fallback="")
    turn = _safe_int(payload.get("turn"), default=-1, minimum=-1, maximum=10000)
    if not battle_id:
        return None

    timestamp_iso, timestamp_epoch = _parse_iso_timestamp(
        payload.get("timestamp"), fallback_epoch=fallback_epoch
    )

    decision_mode = _sanitize_decision_mode(payload.get("decision_mode"))
    hybrid_payload = payload.get("hybrid") if isinstance(payload.get("hybrid"), dict) else {}
    hybrid_status = _sanitize_hybrid_status(hybrid_payload.get("status"))

    selected_choice = _sanitize_text(
        hybrid_payload.get("selected_decision"),
        max_len=80,
        fallback="",
    )
    if not selected_choice:
        selected_choice = _sanitize_text(payload.get("choice"), max_len=80, fallback="-")

    candidate_list = _candidate_list_from_hybrid(hybrid_payload)
    if not candidate_list:
        candidate_list = _candidate_list_from_eval_scores(payload)

    engine_choice = _sanitize_text(
        hybrid_payload.get("engine_choice"),
        max_len=80,
        fallback="",
    )
    if not engine_choice and candidate_list:
        engine_choice = candidate_list[0]
    if not engine_choice:
        engine_choice = selected_choice

    override_flag = bool(hybrid_payload.get("override", False))
    if (
        not override_flag
        and engine_choice
        and selected_choice
        and engine_choice != selected_choice
        and hybrid_status in {"applied", "error"}
    ):
        override_flag = True

    reason = _sanitize_text(hybrid_payload.get("reason"), max_len=180, fallback="")
    if not reason:
        reason = _sanitize_text(payload.get("reason"), max_len=180, fallback="")

    formatted = payload.get("formatted_choice")
    if isinstance(formatted, list) and formatted:
        formatted_choice = _sanitize_text(formatted[0], max_len=120, fallback="")
    else:
        formatted_choice = _sanitize_text(formatted, max_len=120, fallback="")

    return {
        "trace_id": _sanitize_id(source_name, max_len=160, fallback="trace"),
        "battle_id": battle_id,
        "turn": turn if turn >= 0 else None,
        "timestamp": timestamp_iso,
        "sort_ts": timestamp_epoch,
        "decision_mode": decision_mode,
        "engine_choice": engine_choice or "-",
        "candidate_list": candidate_list,
        "selected_choice": selected_choice or "-",
        "override": override_flag,
        "hybrid_status": hybrid_status,
        "reason": reason,
        "formatted_choice": formatted_choice,
        "choice_override": _sanitize_text(payload.get("choice_override"), max_len=64, fallback=""),
    }


def parse_trace_file(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except Exception:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except Exception:
        return None
    return parse_trace_turn(payload, source_name=path.name, fallback_epoch=stat.st_mtime)


@dataclass
class _TraceFileEntry:
    signature: tuple[int, int]
    parsed_turn: dict[str, Any] | None
    parse_error: bool


class DecisionTraceCache:
    def __init__(self, trace_dir: Path, *, scan_interval_sec: float = DEFAULT_SCAN_INTERVAL_SEC):
        self.trace_dir = trace_dir
        self.scan_interval_sec = max(0.2, float(scan_interval_sec))
        self._entries: dict[Path, _TraceFileEntry] = {}
        self._turns: list[dict[str, Any]] = []
        self._parse_errors = 0
        self._last_scan_epoch = 0.0
        self._last_dir_signature: tuple[int, int] | None = None
        self._lock = threading.Lock()

    @property
    def total_turns(self) -> int:
        return len(self._turns)

    def _scan_needed(self, now: float, *, force: bool) -> bool:
        if force:
            return True
        if self._last_scan_epoch <= 0:
            return True
        return (now - self._last_scan_epoch) >= self.scan_interval_sec

    def refresh(self, *, force: bool = False) -> None:
        now = time.time()
        if not self._scan_needed(now, force=force):
            return
        with self._lock:
            now = time.time()
            if not self._scan_needed(now, force=force):
                return
            self._refresh_locked(now)

    def _refresh_locked(self, now: float) -> None:
        if not self.trace_dir.exists():
            self._entries = {}
            self._turns = []
            self._parse_errors = 0
            self._last_scan_epoch = now
            self._last_dir_signature = None
            return

        try:
            dir_stat = self.trace_dir.stat()
            dir_signature = (int(dir_stat.st_mtime_ns), int(dir_stat.st_size))
        except Exception:
            dir_signature = None

        # Fast path: no directory-level changes since last scan.
        if (
            dir_signature is not None
            and self._last_dir_signature == dir_signature
            and self._entries
        ):
            self._last_scan_epoch = now
            return

        current_signatures: dict[Path, tuple[int, int]] = {}
        for path in self.trace_dir.glob("*.json"):
            try:
                stat = path.stat()
            except Exception:
                continue
            current_signatures[path] = (int(stat.st_mtime_ns), int(stat.st_size))

        existing_paths = set(self._entries)
        current_paths = set(current_signatures)
        for removed_path in existing_paths - current_paths:
            self._entries.pop(removed_path, None)

        for path, signature in current_signatures.items():
            cached = self._entries.get(path)
            if cached and cached.signature == signature:
                continue
            parsed_turn = parse_trace_file(path)
            self._entries[path] = _TraceFileEntry(
                signature=signature,
                parsed_turn=parsed_turn,
                parse_error=parsed_turn is None,
            )

        turns: list[dict[str, Any]] = []
        parse_errors = 0
        for entry in self._entries.values():
            if entry.parse_error:
                parse_errors += 1
            if entry.parsed_turn is not None:
                turns.append(entry.parsed_turn)
        turns.sort(
            key=lambda turn: (
                _safe_float(turn.get("sort_ts"), default=0.0),
                _safe_int(turn.get("turn"), default=-1),
            ),
            reverse=True,
        )

        self._turns = turns
        self._parse_errors = parse_errors
        self._last_scan_epoch = now
        self._last_dir_signature = dir_signature

    def get_turns(self, *, limit: int) -> list[dict[str, Any]]:
        self.refresh()
        safe_limit = _safe_int(
            limit,
            default=DEFAULT_TURNS_LIMIT,
            minimum=1,
            maximum=MAX_TURNS_LIMIT,
        )
        return [dict(turn) for turn in self._turns[:safe_limit]]

    def get_all_turns(self) -> list[dict[str, Any]]:
        self.refresh()
        return [dict(turn) for turn in self._turns]

    def health(self) -> dict[str, Any]:
        self.refresh()
        return {
            "available": bool(self._turns),
            "trace_count": len(self._turns),
            "parse_errors": self._parse_errors,
            "last_scan_epoch": round(self._last_scan_epoch, 3),
            "trace_dir": str(self.trace_dir),
        }


class DashboardDataProvider:
    def __init__(
        self,
        *,
        trace_dir: Path | str = DEFAULT_TRACE_DIR,
        state_module=state_store,
        scan_interval_sec: float = DEFAULT_SCAN_INTERVAL_SEC,
    ):
        trace_path = Path(trace_dir)
        if not trace_path.is_absolute():
            trace_path = ROOT_DIR / trace_path
        self.state_module = state_module
        self.trace_cache = DecisionTraceCache(
            trace_path,
            scan_interval_sec=scan_interval_sec,
        )

    def _read_status(self) -> dict[str, Any]:
        try:
            status = self.state_module.read_status()
            return status if isinstance(status, dict) else {}
        except Exception:
            return {}

    def _read_daily(self) -> dict[str, Any]:
        try:
            daily = self.state_module.read_daily_stats()
            return daily if isinstance(daily, dict) else {}
        except Exception:
            return {}

    def _read_battles_raw(self) -> dict[str, Any]:
        try:
            battles = self.state_module.read_active_battles()
            return battles if isinstance(battles, dict) else {}
        except Exception:
            return {}

    def _infer_policy(self, turns: list[dict[str, Any]]) -> str:
        if any(turn.get("hybrid_status") != "unavailable" for turn in turns):
            return "hybrid"
        return _sanitize_policy(os.getenv("DECISION_POLICY", "eval"))

    def _build_battle_payload(self, turns: list[dict[str, Any]]) -> dict[str, Any]:
        battle_turn_map: dict[str, int] = {}
        for turn in turns:
            bid = _sanitize_id(turn.get("battle_id"), max_len=96, fallback="")
            if not bid:
                continue
            turn_num = _safe_int(turn.get("turn"), default=-1, minimum=-1, maximum=10000)
            if bid not in battle_turn_map and turn_num >= 0:
                battle_turn_map[bid] = turn_num

        raw = self._read_battles_raw()
        battles_raw = raw.get("battles")
        if not isinstance(battles_raw, list):
            battles_raw = []

        battles: list[dict[str, Any]] = []
        for item in battles_raw:
            if not isinstance(item, dict):
                continue
            battle_id = _sanitize_id(item.get("id"), max_len=96, fallback="")
            if not battle_id:
                continue
            battles.append(
                {
                    "id": battle_id,
                    "opponent": _sanitize_text(item.get("opponent"), max_len=80, fallback="Unknown"),
                    "status": _sanitize_text(item.get("status"), max_len=32, fallback="active").lower(),
                    "slot": _safe_int(item.get("slot"), default=0, minimum=0, maximum=32) or None,
                    "worker_id": _safe_int(item.get("worker_id"), default=-1, minimum=-1, maximum=128),
                    "started": _sanitize_text(item.get("started"), max_len=48, fallback=""),
                    "url": _sanitize_text(item.get("url"), max_len=180, fallback=""),
                    "current_turn": battle_turn_map.get(battle_id),
                }
            )

        updated = _sanitize_text(raw.get("updated"), max_len=48, fallback=_utc_now_iso())
        return {
            "battles": battles,
            "count": _safe_int(raw.get("count"), default=len(battles), minimum=0, maximum=9999),
            "updated": updated,
            "max_slots": _safe_int(raw.get("max_slots"), default=0, minimum=0, maximum=64) or None,
        }

    @staticmethod
    def _placeholder_latest() -> dict[str, Any]:
        return {
            "battle_id": "-",
            "turn": None,
            "timestamp": None,
            "decision_mode": "unknown",
            "engine_choice": "-",
            "candidate_list": [],
            "hybrid_selected_choice": "-",
            "override": False,
            "hybrid_status": "unavailable",
            "reason": "no_trace_data",
        }

    @staticmethod
    def _public_turn(turn: dict[str, Any]) -> dict[str, Any]:
        return {
            "trace_id": _sanitize_id(turn.get("trace_id"), max_len=160, fallback="trace"),
            "battle_id": _sanitize_id(turn.get("battle_id"), max_len=96, fallback="-"),
            "turn": _safe_int(turn.get("turn"), default=-1, minimum=-1, maximum=10000),
            "timestamp": _sanitize_text(turn.get("timestamp"), max_len=48, fallback=None),
            "decision_mode": _sanitize_decision_mode(turn.get("decision_mode")),
            "engine_choice": _sanitize_text(turn.get("engine_choice"), max_len=80, fallback="-"),
            "candidate_list": [
                _sanitize_text(candidate, max_len=80, fallback="")
                for candidate in (turn.get("candidate_list") or [])
                if _sanitize_text(candidate, max_len=80, fallback="")
            ][:MAX_CANDIDATES],
            "selected_choice": _sanitize_text(turn.get("selected_choice"), max_len=80, fallback="-"),
            "override": bool(turn.get("override", False)),
            "hybrid_status": _sanitize_hybrid_status(turn.get("hybrid_status")),
            "reason": _sanitize_text(turn.get("reason"), max_len=180, fallback=""),
            "choice_override": _sanitize_text(turn.get("choice_override"), max_len=64, fallback=""),
            "formatted_choice": _sanitize_text(turn.get("formatted_choice"), max_len=120, fallback=""),
        }

    @staticmethod
    def _build_recent_trend(turns: list[dict[str, Any]]) -> dict[str, Any]:
        window = turns[:TIMELINE_LIMIT]
        if not window:
            return {
                "window_size": 0,
                "latest_override_rate": 0.0,
                "previous_override_rate": 0.0,
                "direction": "flat",
                "delta": 0.0,
            }

        latest = window[:10]
        previous = window[10:20]

        def _override_rate(items: list[dict[str, Any]]) -> float:
            hybrid_count = sum(
                1 for item in items if _sanitize_hybrid_status(item.get("hybrid_status")) != "unavailable"
            )
            override_count = sum(1 for item in items if bool(item.get("override", False)))
            if hybrid_count <= 0:
                return 0.0
            return round(override_count / hybrid_count, 3)

        latest_rate = _override_rate(latest)
        previous_rate = _override_rate(previous)
        delta = round(latest_rate - previous_rate, 3)
        if delta > 0.05:
            direction = "up"
        elif delta < -0.05:
            direction = "down"
        else:
            direction = "flat"
        return {
            "window_size": len(window),
            "latest_override_rate": latest_rate,
            "previous_override_rate": previous_rate,
            "direction": direction,
            "delta": delta,
        }

    def get_turns_payload(self, *, limit: int = DEFAULT_TURNS_LIMIT) -> dict[str, Any]:
        safe_limit = _safe_int(
            limit,
            default=DEFAULT_TURNS_LIMIT,
            minimum=1,
            maximum=MAX_TURNS_LIMIT,
        )
        turns = self.trace_cache.get_turns(limit=safe_limit)
        return {
            "turns": [self._public_turn(turn) for turn in turns],
            "limit": safe_limit,
            "total_available": self.trace_cache.total_turns,
            "updated": _utc_now_iso(),
            "trace_health": self.trace_cache.health(),
        }

    def get_battles_payload(self) -> dict[str, Any]:
        turns = self.trace_cache.get_all_turns()
        battles = self._build_battle_payload(turns)
        return {
            **battles,
            "decision_policy": self._infer_policy(turns),
            "trace_health": self.trace_cache.health(),
        }

    def get_state_payload(self) -> dict[str, Any]:
        turns = self.trace_cache.get_all_turns()
        public_turns = [self._public_turn(turn) for turn in turns]
        timeline = public_turns[:TIMELINE_LIMIT]
        latest = timeline[0] if timeline else None

        status = self._read_status()
        daily = self._read_daily()
        battles_payload = self._build_battle_payload(turns)
        policy = self._infer_policy(turns)

        wins = _safe_int(
            daily.get("wins", status.get("wins", 0)),
            default=0,
            minimum=0,
            maximum=999999,
        )
        losses = _safe_int(
            daily.get("losses", status.get("losses", 0)),
            default=0,
            minimum=0,
            maximum=999999,
        )
        battle_count = wins + losses

        hybrid_turns = [
            turn for turn in public_turns if _sanitize_hybrid_status(turn.get("hybrid_status")) != "unavailable"
        ]
        override_turns = [turn for turn in hybrid_turns if bool(turn.get("override", False))]
        override_rate = _safe_percent(len(override_turns), len(hybrid_turns))

        override_patterns = Counter(
            f"{turn.get('engine_choice', '-') } -> {turn.get('selected_choice', '-')}"
            for turn in override_turns
        )
        skip_reasons = Counter(
            _sanitize_text(turn.get("reason"), max_len=80, fallback="unknown")
            for turn in public_turns
            if turn.get("hybrid_status") == "skipped"
        )

        latest_payload = self._placeholder_latest()
        if latest:
            latest_payload = {
                "battle_id": latest.get("battle_id", "-"),
                "turn": latest.get("turn"),
                "timestamp": latest.get("timestamp"),
                "decision_mode": latest.get("decision_mode", "unknown"),
                "engine_choice": latest.get("engine_choice", "-"),
                "candidate_list": latest.get("candidate_list", []),
                "hybrid_selected_choice": latest.get("selected_choice", "-"),
                "override": bool(latest.get("override", False)),
                "hybrid_status": latest.get("hybrid_status", "unavailable"),
                "reason": latest.get("reason") or "none",
            }

        return {
            "updated": _utc_now_iso(),
            "decision_policy": policy,
            "active_battles": battles_payload.get("battles", []),
            "active_battle_count": battles_payload.get("count", 0),
            "max_slots": battles_payload.get("max_slots") or 3,
            "elo": status.get("elo", "---"),
            "latest_decision": latest_payload,
            "timeline": timeline,
            "stats": {
                "wins": wins,
                "losses": losses,
                "battle_count": battle_count,
                "override_rate": override_rate,
                "hybrid_turn_count": len(hybrid_turns),
                "override_turn_count": len(override_turns),
            },
            "learning": {
                "top_override_patterns": [
                    {"pattern": pattern, "count": count}
                    for pattern, count in override_patterns.most_common(5)
                ],
                "top_skip_reasons": [
                    {"reason": reason, "count": count}
                    for reason, count in skip_reasons.most_common(5)
                ],
                "recent_trend": self._build_recent_trend(public_turns),
            },
            "status": {
                "status_text": _sanitize_text(status.get("status"), max_len=64, fallback="Unknown"),
                "battle_info": _sanitize_text(status.get("battle_info"), max_len=160, fallback=""),
            },
            "trace_health": self.trace_cache.health(),
        }


_default_provider: DashboardDataProvider | None = None


def get_default_provider() -> DashboardDataProvider:
    global _default_provider
    if _default_provider is not None:
        return _default_provider

    scan_interval = _safe_float(
        os.getenv("DASHBOARD_TRACE_SCAN_INTERVAL_SEC", DEFAULT_SCAN_INTERVAL_SEC),
        default=DEFAULT_SCAN_INTERVAL_SEC,
    )
    _default_provider = DashboardDataProvider(
        trace_dir=DEFAULT_TRACE_DIR,
        scan_interval_sec=scan_interval,
    )
    return _default_provider


def _add_get_if_missing(app: web.Application, route: str, handler) -> None:
    try:
        app.router.add_get(route, handler)
    except RuntimeError:
        # Route already exists. Keep server behavior intact.
        pass


def register_dashboard_routes(
    app: web.Application,
    *,
    provider: DashboardDataProvider | None = None,
    dashboard_html: Path | str = DEFAULT_DASHBOARD_HTML,
    overlay_html: Path | str = DEFAULT_OVERLAY_HTML,
) -> None:
    data_provider = provider or get_default_provider()
    dashboard_file = Path(dashboard_html)
    overlay_file = Path(overlay_html)

    async def handle_dashboard_state(_request: web.Request) -> web.Response:
        return web.json_response(data_provider.get_state_payload())

    async def handle_dashboard_turns(request: web.Request) -> web.Response:
        raw_limit = request.query.get("limit", str(DEFAULT_TURNS_LIMIT))
        limit = _safe_int(
            raw_limit,
            default=DEFAULT_TURNS_LIMIT,
            minimum=1,
            maximum=MAX_TURNS_LIMIT,
        )
        return web.json_response(data_provider.get_turns_payload(limit=limit))

    async def handle_dashboard_battles(_request: web.Request) -> web.Response:
        return web.json_response(data_provider.get_battles_payload())

    async def handle_dashboard_page(_request: web.Request) -> web.Response:
        target = dashboard_file if dashboard_file.exists() else DEFAULT_DASHBOARD_HTML
        return web.FileResponse(str(target))

    async def handle_overlay_page(_request: web.Request) -> web.Response:
        target = overlay_file if overlay_file.exists() else DEFAULT_OVERLAY_HTML
        return web.FileResponse(str(target))

    _add_get_if_missing(app, "/api/dashboard/state", handle_dashboard_state)
    _add_get_if_missing(app, "/api/dashboard/turns", handle_dashboard_turns)
    _add_get_if_missing(app, "/api/dashboard/battles", handle_dashboard_battles)
    _add_get_if_missing(app, "/dashboard/hybrid", handle_dashboard_page)
    _add_get_if_missing(app, "/overlay/hybrid", handle_overlay_page)
