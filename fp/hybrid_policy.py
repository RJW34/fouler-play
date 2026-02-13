import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

import constants
from fp.helpers import normalize_name

logger = logging.getLogger(__name__)

_RERANK_RATE_LIMIT_UNTIL = 0.0
HYBRID_RATE_LIMIT_BACKOFF_SEC = max(
    15,
    int(os.getenv("HYBRID_RATE_LIMIT_BACKOFF_SEC", "120")),
)
HYBRID_CLEAR_BEST_RATIO = max(
    1.05,
    float(os.getenv("HYBRID_CLEAR_BEST_RATIO", "1.35")),
)
HYBRID_CLEAR_BEST_DELTA = max(
    0.0,
    float(os.getenv("HYBRID_CLEAR_BEST_DELTA", "0.15")),
)


SYSTEM_PROMPT = (
    "You are assisting a Pokemon Showdown battle bot.\n"
    "Pick exactly one candidate decision from the provided indexed list.\n"
    "Do not invent moves, switches, tera tags, or any action not listed.\n"
    "Optimize for win probability this turn while avoiding unnecessary risk when scores are close.\n"
    "Respond ONLY as JSON with keys: choice_index (integer) and reason (string)."
)


@dataclass
class HybridRerankResult:
    decision: str | None
    metadata: dict[str, Any]


def _candidate_decisions(candidates: list[dict[str, Any]] | None) -> list[str]:
    if not candidates:
        return []
    out: list[str] = []
    for item in candidates:
        decision = item.get("decision") if isinstance(item, dict) else None
        if decision is None:
            continue
        out.append(str(decision))
    return out


def _build_metadata(
    *,
    status: str,
    reason: str,
    engine_choice: str,
    candidates: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    decisions = _candidate_decisions(candidates)
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "engine_choice": str(engine_choice or ""),
        "selected_decision": str(engine_choice or ""),
        "override": False,
        "candidates": decisions,
        "candidate_count": len(decisions),
    }
    payload.update(extra)
    return payload


def _extract_json_dict(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _to_hp_percent(pkmn) -> float | None:
    if pkmn is None or getattr(pkmn, "max_hp", 0) <= 0:
        return None
    return round((pkmn.hp / pkmn.max_hp) * 100, 1)


def _compact_active_state(pkmn) -> dict[str, Any]:
    if pkmn is None:
        return {}
    return {
        "name": pkmn.name,
        "hp_percent": _to_hp_percent(pkmn),
        "status": pkmn.status,
        "types": list(pkmn.types) if pkmn.types else [],
        "item": pkmn.item,
        "ability": pkmn.ability,
        "boosts": dict(getattr(pkmn, "boosts", {}) or {}),
        "volatile_statuses": list(getattr(pkmn, "volatile_statuses", []) or []),
        "terastallized": bool(getattr(pkmn, "terastallized", False)),
        "tera_type": getattr(pkmn, "tera_type", None),
    }


def _alive_reserve_summary(battler) -> list[dict[str, Any]]:
    reserve = getattr(battler, "reserve", []) or []
    out = []
    for pkmn in reserve:
        if pkmn.hp <= 0:
            continue
        out.append(
            {
                "name": pkmn.name,
                "hp_percent": _to_hp_percent(pkmn),
                "status": pkmn.status,
                "types": list(pkmn.types) if pkmn.types else [],
            }
        )
    return out


def build_rerank_candidates(
    engine_choice: str,
    trace: dict[str, Any] | None,
    top_k: int,
) -> list[dict[str, Any]]:
    if not trace:
        return []
    raw_scores = trace.get("eval_scores_raw")
    if not isinstance(raw_scores, dict):
        return []

    scored: list[tuple[str, float]] = []
    for move, score in raw_scores.items():
        try:
            scored.append((str(move), float(score)))
        except (TypeError, ValueError):
            continue

    if not scored:
        return []

    scored.sort(key=lambda item: item[1], reverse=True)

    top_k = max(2, int(top_k))
    selected = scored[:top_k]
    seen = {move for move, _ in selected}

    if engine_choice and engine_choice not in seen:
        engine_score = None
        for move, score in scored:
            if move == engine_choice:
                engine_score = score
                break
        if engine_score is None:
            engine_score = selected[-1][1] if selected else 0.0
        selected.append((engine_choice, engine_score))

    return [
        {"index": idx, "decision": decision, "engine_score": round(score, 6)}
        for idx, (decision, score) in enumerate(selected, start=1)
    ]


def _compact_battle_context(battle) -> dict[str, Any]:
    user = getattr(battle, "user", None)
    opponent = getattr(battle, "opponent", None)

    return {
        "format": getattr(battle, "pokemon_format", None),
        "turn": getattr(battle, "turn", None),
        "force_switch": bool(getattr(battle, "force_switch", False)),
        "time_remaining": getattr(battle, "time_remaining", None),
        "weather": getattr(battle, "weather", None),
        "weather_turns": getattr(battle, "weather_turns_remaining", None),
        "field": getattr(battle, "field", None),
        "field_turns": getattr(battle, "field_turns_remaining", None),
        "trick_room": bool(getattr(battle, "trick_room", False)),
        "trick_room_turns": getattr(battle, "trick_room_turns_remaining", None),
        "our_active": _compact_active_state(getattr(user, "active", None)),
        "opponent_active": _compact_active_state(getattr(opponent, "active", None)),
        "our_reserve_alive": _alive_reserve_summary(user),
        "opponent_reserve_alive": _alive_reserve_summary(opponent),
        "our_side_conditions": dict(getattr(user, "side_conditions", {}) or {}),
        "opponent_side_conditions": dict(
            getattr(opponent, "side_conditions", {}) or {}
        ),
    }


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, dict):
                text_parts.append(str(part.get("text", "")))
        return "".join(text_parts)
    return str(content)


def _is_clear_best_engine_turn(trace: dict[str, Any] | None) -> bool:
    if not trace:
        return False
    raw_scores = trace.get("eval_scores_raw")
    if not isinstance(raw_scores, dict):
        return False

    scored: list[float] = []
    for score in raw_scores.values():
        try:
            scored.append(float(score))
        except (TypeError, ValueError):
            continue

    if len(scored) < 2:
        return False

    scored.sort(reverse=True)
    top = scored[0]
    second = scored[1]
    delta = top - second
    ratio = float("inf") if second <= 0 else (top / second)
    return delta >= HYBRID_CLEAR_BEST_DELTA or ratio >= HYBRID_CLEAR_BEST_RATIO


async def run_hybrid_rerank(
    *,
    battle,
    engine_choice: str,
    trace: dict[str, Any] | None,
    api_key: str,
    model: str,
    api_base: str,
    timeout_sec: float,
    top_k: int,
) -> HybridRerankResult:
    global _RERANK_RATE_LIMIT_UNTIL

    pre_candidates = build_rerank_candidates(engine_choice, trace, top_k=top_k)
    now = time.time()

    if now < _RERANK_RATE_LIMIT_UNTIL:
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="skipped",
                reason="rate_limited_backoff",
                engine_choice=engine_choice,
                candidates=pre_candidates,
                retry_in_sec=max(1, int(_RERANK_RATE_LIMIT_UNTIL - now)),
            ),
        )

    if not api_key:
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="skipped",
                reason="missing_api_key",
                engine_choice=engine_choice,
                candidates=pre_candidates,
            ),
        )

    if not trace or trace.get("decision_mode") != "eval":
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="skipped",
                reason="non_eval_decision",
                engine_choice=engine_choice,
                candidates=pre_candidates,
            ),
        )

    if _is_clear_best_engine_turn(trace):
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="skipped",
                reason="clear_best_engine",
                engine_choice=engine_choice,
                candidates=pre_candidates,
            ),
        )

    if getattr(battle, "time_remaining", None) is not None and battle.time_remaining < 20:
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="skipped",
                reason="time_pressure",
                engine_choice=engine_choice,
                candidates=pre_candidates,
            ),
        )

    candidates = pre_candidates
    if len(candidates) < 2:
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="skipped",
                reason="insufficient_candidates",
                engine_choice=engine_choice,
                candidates=candidates,
            ),
        )

    context_payload = {
        "battle": _compact_battle_context(battle),
        "engine_choice": engine_choice,
        "candidates": candidates,
        "note": (
            "Pick one candidate index only. "
            "A candidate may be a move, a move with -tera/-mega suffix, or 'switch <pokemon>'."
        ),
    }

    user_prompt = (
        "Rerank these engine candidates.\n"
        "Return strict JSON only: {\"choice_index\": <int>, \"reason\": \"<short>\"}\n\n"
        f"{json.dumps(context_payload, ensure_ascii=True)}"
    )

    request_payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 120,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    endpoint = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    started = time.time()
    try:
        timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout_sec)))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint,
                json=request_payload,
                headers=headers,
            ) as resp:
                elapsed_ms = int((time.time() - started) * 1000)
                body_text = await resp.text()
                if resp.status != 200:
                    if resp.status == 429:
                        _RERANK_RATE_LIMIT_UNTIL = time.time() + HYBRID_RATE_LIMIT_BACKOFF_SEC
                        return HybridRerankResult(
                            decision=None,
                            metadata=_build_metadata(
                                status="skipped",
                                reason="rate_limited",
                                engine_choice=engine_choice,
                                candidates=candidates,
                                latency_ms=elapsed_ms,
                                retry_in_sec=HYBRID_RATE_LIMIT_BACKOFF_SEC,
                            ),
                        )
                    logger.warning(
                        "Hybrid rerank API returned status %s: %s",
                        resp.status,
                        body_text[:300],
                    )
                    return HybridRerankResult(
                        decision=None,
                        metadata=_build_metadata(
                            status="error",
                            reason=f"http_{resp.status}",
                            engine_choice=engine_choice,
                            candidates=candidates,
                            latency_ms=elapsed_ms,
                        ),
                    )
                try:
                    data = json.loads(body_text)
                except json.JSONDecodeError:
                    return HybridRerankResult(
                        decision=None,
                        metadata=_build_metadata(
                            status="error",
                            reason="invalid_json_response",
                            engine_choice=engine_choice,
                            candidates=candidates,
                            latency_ms=elapsed_ms,
                        ),
                    )
    except Exception as e:
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="error",
                reason=f"request_exception:{e}",
                engine_choice=engine_choice,
                candidates=candidates,
            ),
        )

    content = _extract_message_content(data)
    parsed = _extract_json_dict(content)
    if not parsed:
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="error",
                reason="unparseable_model_output",
                engine_choice=engine_choice,
                candidates=candidates,
            ),
        )

    choice_index = parsed.get("choice_index")
    if isinstance(choice_index, str) and choice_index.isdigit():
        choice_index = int(choice_index)
    if not isinstance(choice_index, int):
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="error",
                reason="missing_choice_index",
                engine_choice=engine_choice,
                candidates=candidates,
            ),
        )
    if choice_index < 1 or choice_index > len(candidates):
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="error",
                reason="choice_index_out_of_range",
                engine_choice=engine_choice,
                candidates=candidates,
            ),
        )

    selected = candidates[choice_index - 1]["decision"]
    selected_norm = normalize_name(selected.replace(constants.SWITCH_STRING, ""))
    if selected.startswith("switch ") and not selected_norm:
        return HybridRerankResult(
            decision=None,
            metadata=_build_metadata(
                status="error",
                reason="invalid_switch_selection",
                engine_choice=engine_choice,
                candidates=candidates,
            ),
        )

    reason = str(parsed.get("reason", ""))[:240]
    return HybridRerankResult(
        decision=selected,
        metadata=_build_metadata(
            status="applied",
            reason=reason,
            engine_choice=engine_choice,
            candidates=candidates,
            model=model,
            selected_index=choice_index,
            selected_decision=selected,
            override=(selected != engine_choice),
        ),
    )
