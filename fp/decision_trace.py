import json
import logging
import os
import time
from datetime import datetime

logger = logging.getLogger(__name__)


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
