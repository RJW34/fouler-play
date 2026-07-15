import json
import logging
import os
import time
import hashlib
from datetime import datetime
from pathlib import Path

from infrastructure.runtime_paths import (
    resolve_runtime_paths,
    validate_external_runtime_path,
)

logger = logging.getLogger(__name__)
PUBLIC_BATTLE_VIEW_FILENAME = "latest-public-battle.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_PATHS = resolve_runtime_paths(PROJECT_ROOT)


def _live_request_evidence(battle):
    request = getattr(battle, "request_json", None)
    if not isinstance(request, dict):
        return None

    active_requests = request.get("active")
    if not isinstance(active_requests, list):
        active_requests = []
    side = request.get("side") if isinstance(request.get("side"), dict) else {}
    side_pokemon = side.get("pokemon") if isinstance(side.get("pokemon"), list) else []
    force_switch = request.get("forceSwitch") if "forceSwitch" in request else getattr(battle, "force_switch", False)
    trapped = bool(
        any(isinstance(active, dict) and active.get("trapped") for active in active_requests)
        or getattr(getattr(battle, "user", None), "trapped", False)
    )

    legal_moves = []
    for active_slot, active in enumerate(active_requests):
        if not isinstance(active, dict):
            continue
        moves = active.get("moves") if isinstance(active.get("moves"), list) else []
        for move in moves:
            if not isinstance(move, dict) or move.get("disabled") is True:
                continue
            move_id = move.get("id") or move.get("move")
            if not move_id:
                continue
            legal_moves.append({
                "activeSlot": active_slot,
                "id": str(move_id),
                "target": move.get("target"),
            })

    legal_switches = []
    if force_switch or not trapped:
        for slot, mon in enumerate(side_pokemon):
            if not isinstance(mon, dict) or mon.get("active") is True:
                continue
            condition = str(mon.get("condition") or "")
            if condition.startswith("0 fnt"):
                continue
            legal_switches.append({
                "slot": slot,
                "details": mon.get("details"),
                "condition": condition,
            })

    redacted = {
        "rqid": request.get("rqid") or getattr(battle, "rqid", None),
        "wait": bool(request.get("wait") or getattr(battle, "wait", False)),
        "forceSwitch": force_switch,
        "trapped": trapped,
        "legalMoves": legal_moves,
        "legalSwitches": legal_switches,
    }
    request_hash = hashlib.sha256(
        json.dumps(redacted, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    redacted["requestHash"] = request_hash
    redacted["legalOptionsSource"] = "showdown-request"
    redacted["candidateSetBounded"] = bool(legal_moves or legal_switches)
    return redacted


def _make_json_safe(value):
    if isinstance(value, dict):
        return {str(k): _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(v) for v in value]
    if isinstance(value, set):
        return [_make_json_safe(v) for v in sorted(value)]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def build_trace_base(battle, reason: str | None = None):
    trace = {
        "battle_tag": getattr(battle, "battle_tag", None),
        "worker_id": getattr(battle, "worker_id", None),
        "turn": getattr(battle, "turn", None),
        "format": getattr(battle, "pokemon_format", None),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "reason": reason or "",
    }
    if hasattr(battle, "snapshot"):
        try:
            trace["snapshot"] = battle.snapshot()
        except Exception as e:
            logger.debug(f"Decision trace snapshot failed: {e}")
            trace["snapshot"] = {"error": "snapshot_failed"}
    request_evidence = _live_request_evidence(battle)
    if request_evidence:
        trace["showdownRequest"] = request_evidence
        trace["legalOptions"] = {
            "source": "showdown-request",
            "requestHash": request_evidence["requestHash"],
            "rqid": request_evidence.get("rqid"),
            "legalMoves": request_evidence.get("legalMoves", []),
            "legalSwitches": request_evidence.get("legalSwitches", []),
            "forceSwitch": request_evidence.get("forceSwitch"),
            "trapped": request_evidence.get("trapped"),
            "candidateSetBounded": request_evidence.get("candidateSetBounded"),
        }
    return trace


def validate_trace_schema(trace: dict) -> bool:
    required = {"battle_tag", "turn", "timestamp", "snapshot", "choice"}
    return required.issubset(set(trace.keys()))


def _public_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else round(number, 2)


def _public_pokemon(raw):
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip().lower()
    if not name:
        return None
    tera = raw.get("tera") if isinstance(raw.get("tera"), dict) else {}
    tera_active = bool(tera.get("active"))
    hp = _public_number(raw.get("hp"))
    max_hp = _public_number(raw.get("max_hp"))
    hp_percent = None
    if hp is not None and max_hp is not None and max_hp > 0:
        hp_percent = round(max(0, min(100, (hp / max_hp) * 100)), 1)
    elif bool(raw.get("fainted")):
        hp_percent = 0
    boosts = raw.get("boosts") if isinstance(raw.get("boosts"), dict) else {}
    public_boosts = {}
    for stat, value in boosts.items():
        number = _public_number(value)
        if number not in (None, 0):
            public_boosts[str(stat)[:16]] = number
    return {
        "name": name[:80],
        "hp_percent": hp_percent,
        "fainted": bool(raw.get("fainted")),
        "status": str(raw.get("status") or "").strip().lower()[:12] or None,
        "types": [str(value).strip().lower()[:16] for value in (raw.get("types") or [])[:2] if value],
        "tera": {
            "active": tera_active,
            "type": (
                str(tera.get("type") or "").strip().lower()[:16] or None
                if tera_active
                else None
            ),
        },
        "boosts": public_boosts,
    }


def _public_side(raw):
    if not isinstance(raw, dict):
        return None
    active = _public_pokemon(raw.get("active"))
    reserve = [
        pokemon
        for pokemon in (_public_pokemon(value) for value in (raw.get("reserve") or [])[:5])
        if pokemon is not None
    ]
    conditions = raw.get("side_conditions") if isinstance(raw.get("side_conditions"), dict) else {}
    public_conditions = {}
    for name, value in conditions.items():
        number = _public_number(value)
        if number not in (None, 0):
            public_conditions[str(name).strip().lower()[:24]] = number
    return {
        "account": str(raw.get("account") or "").strip()[:40] or None,
        "active": active,
        "reserve": reserve,
        "side_conditions": public_conditions,
    }


def build_public_battle_view(trace: dict):
    if not isinstance(trace, dict):
        return None
    snapshot = trace.get("snapshot") if isinstance(trace.get("snapshot"), dict) else {}
    user = _public_side(snapshot.get("user"))
    opponent = _public_side(snapshot.get("opponent"))
    battle_id = str(trace.get("battle_tag") or snapshot.get("battle_tag") or "").strip()
    if not battle_id or user is None or opponent is None:
        return None
    return {
        "schema": "fouler-public-battle-view/v1",
        # Kept only in the local file so the HTTP surface can reject a stale
        # snapshot. The API removes this private room identifier.
        "battle_id": battle_id,
        "updated_at": str(trace.get("timestamp") or ""),
        "turn": _public_number(trace.get("turn") or snapshot.get("turn")),
        "format": str(trace.get("format") or "gen9ou").strip().lower()[:24],
        "weather": str(snapshot.get("weather") or "").strip().lower()[:24] or None,
        "field": str(snapshot.get("field") or "").strip().lower()[:24] or None,
        "trick_room": bool(snapshot.get("trick_room")),
        "user": user,
        "opponent": opponent,
    }


def _write_public_battle_view(payload: dict, target_dir: str) -> None:
    path = os.path.join(target_dir, PUBLIC_BATTLE_VIEW_FILENAME)
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def write_decision_trace(trace: dict, base_dir: str | None = None) -> str | None:
    if not trace:
        return None
    target_dir = validate_external_runtime_path(
        base_dir or _RUNTIME_PATHS.decision_trace_root,
        release_root=PROJECT_ROOT,
        label="decision trace directory",
    )
    try:
        os.makedirs(target_dir, exist_ok=True)
        tag = trace.get("battle_tag", "battle")
        turn = trace.get("turn", "x")
        stamp = int(time.time() * 1000)
        filename = f"{tag}_turn{turn}_{stamp}.json"
        path = os.path.join(target_dir, filename)
        safe_trace = _make_json_safe(trace)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(safe_trace, f, indent=2, sort_keys=True)
        public_view = build_public_battle_view(safe_trace)
        if public_view:
            try:
                _write_public_battle_view(public_view, target_dir)
            except Exception as e:
                logger.debug(f"Public battle view write failed: {e}")
        return path
    except Exception as e:
        logger.debug(f"Decision trace write failed: {e}")
        return None
