import json
import logging
import os
import time
import hashlib
from datetime import datetime

logger = logging.getLogger(__name__)


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


def write_decision_trace(trace: dict, base_dir: str | None = None) -> str | None:
    if not trace:
        return None
    target_dir = base_dir or os.getenv("DECISION_TRACE_DIR", "logs/decision_traces")
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
        return path
    except Exception as e:
        logger.debug(f"Decision trace write failed: {e}")
        return None
