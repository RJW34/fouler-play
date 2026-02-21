import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

import constants
from constants import (
    BattleType,
    # Unaware
    OFFENSIVE_STAT_BOOST_MOVES,
    POKEMON_COMMONLY_UNAWARE,
    UNAWARE_BOOST_PENALTY,
    # Status-boosted abilities (Guts, Marvel Scale, etc.)
    POKEMON_STATUS_BACKFIRES,
    STATUS_INFLICTING_MOVES,
    PURE_STATUS_MOVES,
    # Poison Heal
    POKEMON_COMMONLY_POISON_HEAL,
    TOXIC_POISON_MOVES,
    POISON,
    TOXIC,
    BURN,
    PARALYZED,
    # Status threat detection
    NON_VOLATILE_STATUSES,
    STATUS,
    # Type-absorbing abilities
    POKEMON_COMMONLY_WATER_IMMUNE,
    WATER_TYPE_MOVES,
    POKEMON_COMMONLY_ELECTRIC_IMMUNE,
    ELECTRIC_TYPE_MOVES,
    POKEMON_COMMONLY_FLASH_FIRE,
    FIRE_TYPE_MOVES,
    POKEMON_COMMONLY_LEVITATE,
    GROUND_TYPE_MOVES,
    # Magic Bounce
    POKEMON_COMMONLY_MAGIC_BOUNCE,
    MAGIC_BOUNCE_REFLECTED_MOVES,
    # Good as Gold
    POKEMON_COMMONLY_GOOD_AS_GOLD,
    # Competitive/Defiant
    POKEMON_STAT_DROP_BACKFIRES,
    STAT_LOWERING_MOVES,
    # Penalty values
    ABILITY_PENALTY_SEVERE,
    ABILITY_PENALTY_MEDIUM,
    ABILITY_PENALTY_LIGHT,
    ABILITY_BOOST_LIGHT,
    ABILITY_BOOST_MEDIUM,
    # Mold Breaker
    MOLD_BREAKER_ABILITIES,
    MOLD_BREAKER_BYPASSED_ABILITIES,
    # Focus Sash
    POKEMON_COMMONLY_FOCUS_SASH,
    MULTI_HIT_MOVES,
    PRIORITY_MOVES,
    # Setup vs Phazers
    PHAZING_MOVES,
    SETUP_MOVES,
    # Substitute
    STATUS_ONLY_MOVES,
    INFILTRATOR_BYPASS,
    # Contact Moves
    CONTACT_MOVES,
    POKEMON_COMMONLY_IRON_BARBS,
    POKEMON_COMMONLY_ROUGH_SKIN,
    POKEMON_COMMONLY_ROCKY_HELMET,
    # NEW: Dynamic move sets from moves.json
    SOUND_MOVES,
    POWDER_MOVES,
    BULLET_MOVES,
    WIND_MOVES,
    GRASS_TYPE_MOVES,
    DARK_TYPE_MOVES,
    SELF_STAT_DROP_MOVES,
    ALL_STATUS_MOVES,
    # NEW: Dynamic Pokemon sets from pokedex.json
    POKEMON_COMMONLY_CONTRARY,
    POKEMON_COMMONLY_SAP_SIPPER,
    POKEMON_COMMONLY_STURDY,
    POKEMON_COMMONLY_DISGUISE,
    POKEMON_COMMONLY_SOUNDPROOF,
    POKEMON_COMMONLY_BULLETPROOF,
    POKEMON_COMMONLY_OVERCOAT,
    POKEMON_COMMONLY_EARTH_EATER,
    POKEMON_COMMONLY_JUSTIFIED,
    POKEMON_COMMONLY_STEAM_ENGINE,
    POKEMON_COMMONLY_WIND_RIDER,
    POKEMON_COMMONLY_WELL_BAKED_BODY,
    POKEMON_COMMONLY_STAT_DROP_IMMUNE,
    POKEMON_COMMONLY_MIRROR_ARMOR,
    POKEMON_COMMONLY_FLUFFY,
    POKEMON_COMMONLY_DRY_SKIN,
    POKEMON_WITH_PRANKSTER,
    POKEMON_COMMONLY_AIR_BALLOON,
    POKEMON_COMMONLY_QUEENLY_MAJESTY,
    POKEMON_COMMONLY_DAZZLING,
    POKEMON_COMMONLY_ARMOR_TAIL,
    # NEW: Weather/Terrain constants
    WEATHER_RAIN,
    WEATHER_SUN,
    WEATHER_EXTREME_RAIN,
    WEATHER_EXTREME_SUN,
    TERRAIN_PSYCHIC,
    DAMAGING_GROUND_MOVES,
    UNGROUNDED_ABILITIES,
    UNGROUNDED_ITEMS,
    UNGROUNDED_TYPES,
    POKEMON_COMMONLY_SUPREME_OVERLORD,
    # PHASE 1.1: Positive Boosts Expansion
    PROTECT_MOVES,
    BOOST_CHOICE_LOCKED_RESIST,
    BOOST_CHOICE_LOCKED_IMMUNE,
    BOOST_PROTECT_PUNISH,
    BOOST_OPPONENT_STATUSED,
    BOOST_LOW_HP_PRIORITY,
    PRE_ORB_STATUS_THREAT_MIN_PROB,
    BOOST_PRE_ORB_PROTECT,
    PENALTY_PRE_ORB_NONPROTECT,
    BOOST_PRE_ORB_PROTECT_KNOCK,
    PENALTY_PRE_ORB_NONPROTECT_KNOCK,
    BOOST_PRE_ORB_SWITCH_KNOCK,
    ITEM_REMOVAL_MOVES,
    # PHASE 1.2: Trick Room Awareness
    SPEED_BOOSTING_MOVES,
    TRICK_ROOM_MOVES,
    PENALTY_SPEED_BOOST_IN_TR,
    BOOST_SETUP_SLOW_IN_TR,
    # PHASE 1.3: Screens Awareness
    SCREEN_BREAKING_MOVES,
    SCREEN_SETTING_MOVES,
    BOOST_SETUP_VS_SCREENS,
    BOOST_SCREEN_BREAKER,
    # PHASE 1.4: Weather/Terrain Synergies
    POKEMON_WITH_SWIFT_SWIM,
    POKEMON_WITH_SAND_RUSH,
    POKEMON_WITH_SLUSH_RUSH,
    POKEMON_WITH_CHLOROPHYLL,
    SWIFT_SWIM_ABILITIES,
    SAND_RUSH_ABILITIES,
    SLUSH_RUSH_ABILITIES,
    CHLOROPHYLL_ABILITIES,
    WEATHER_SAND,
    WEATHER_SNOW,
    BOOST_TERRAIN_MOVE,
    BOOST_WEATHER_SPEED_ADVANTAGE,
    TERRAIN_ELECTRIC_BOOSTS,
    TERRAIN_GRASSY_BOOSTS,
    TERRAIN_PSYCHIC_BOOSTS,
    GRASSY_TERRAIN_WEAKENED,
    # PHASE 2.1: Switch Evaluation
    POKEMON_WITH_INTIMIDATE_COMMON,
    INTIMIDATE_ABILITIES,
    PENALTY_SWITCH_INTO_HAZARDS_PER_LAYER,
    PENALTY_SWITCH_INTIMIDATE_VS_DEFIANT,
    PENALTY_SWITCH_LOW_HP,
    PENALTY_SWITCH_VERY_LOW_HP,
    PENALTY_SWITCH_LOW_HP_VS_BOOSTED,
    PENALTY_SWITCH_WEAK_TO_OPPONENT,
    BOOST_SWITCH_RESISTS_STAB,
    BOOST_SWITCH_HAS_RECOVERY,
    BOOST_SWITCH_COUNTERS,
    BOOST_SWITCH_UNAWARE_VS_SETUP,
    POKEMON_RECOVERY_MOVES,
    # PHASE 2.2: Entry Hazard Calculus
    HAZARD_REMOVAL_MOVES,
    HAZARD_SETTING_MOVES,
    POKEMON_SR_4X_WEAK,
    POKEMON_COMMONLY_HDB,
    BOOST_SET_HAZARDS_NO_HAZARDS,
    BOOST_SET_HAZARDS_SR_WEAK_OPP,
    PENALTY_SET_HAZARDS_ALREADY_UP,
    BOOST_REMOVE_HAZARDS_HEAVY,
    PENALTY_REMOVE_HAZARDS_NONE,
    PENALTY_HAZARDS_VS_REMOVAL,
    PENALTY_HAZARD_VS_ACTIVE_SWEEPER,
    # PHASE 2.3: Tera Prediction
    DEFENSIVE_TERA_TYPES,
    OFFENSIVE_TERA_TYPES,
    BOOST_COVERAGE_VS_LIKELY_TERA,
    BOOST_STAY_FOR_TERA,
    # PHASE 3.1: Win Condition Awareness
    PENALTY_RISKY_WITH_WINCON,
    BOOST_SAFE_WITH_WINCON,
    PENALTY_SACK_ONLY_CHECK,
    BOOST_REVENGE_KILL_THREAT,
    RISKY_MOVES,
    SAFE_MOVES,
    # PHASE 3.2: PP Tracking
    BOOST_STALL_NO_RECOVERY_PP,
    BOOST_DEFENSIVE_LOW_PP,
    # PHASE 3.3: Momentum Tracking
    MOMENTUM_STRONG_POSITIVE,
    MOMENTUM_POSITIVE,
    MOMENTUM_NEGATIVE,
    MOMENTUM_STRONG_NEGATIVE,
    BOOST_AGGRESSIVE_STRONG_MOMENTUM,
    BOOST_PRESSURE_MOMENTUM,
    BOOST_PIVOT_NEGATIVE_MOMENTUM,
    BOOST_HIGHRISK_DESPERATE,
    AGGRESSIVE_MOVES,
    # PHASE 4.1: Endgame Solver
    ENDGAME_MAX_POKEMON,
    # Proactive sweep prevention (2026-02-08)
    PENALTY_STAY_VS_SETUP_SWEEPER,
    PENALTY_PASSIVE_VS_BOOSTED,
    BOOST_SWITCH_VS_BOOSTED,
    BOOST_PHAZE_VS_BOOSTED,
    BOOST_REVENGE_VS_BOOSTED,
)
from fp.battle import Battle
from fp.decision_trace import build_trace_base
from config import FoulPlayConfig
from .standard_battles import prepare_battles
from .random_battles import prepare_random_battles

from poke_engine import monte_carlo_tree_search
from fp.search.eval import evaluate_position, _opponent_best_damage as _eval_opponent_best_damage
from fp.search.forced_lines import detect_forced_line
from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.search.speed_order import assess_speed_order
from fp.playstyle_config import PlaystyleConfig, Playstyle, HAZARD_MOVES
from fp.helpers import normalize_name, type_effectiveness_modifier
from data.pkmn_sets import ITEM_STRING, EFFECTIVENESS, SmogonSets, TeamDatasets
from fp.team_analysis import analyze_team, TeamAnalysis, REMOVAL_MOVES, SCREEN_MOVES
from fp.playstyle_config import RECOVERY_MOVES, PIVOT_MOVES
from constants_pkg.strategy import SETUP_MOVES, PRIORITY_MOVES
from data import all_move_json
from fp.movepool_tracker import get_threat_category, ThreatCategory
from fp.search.move_validators import filter_blocked_moves
from fp.opponent_model import OPPONENT_MODEL
from sweep_fix import smart_sweep_prevention

logger = logging.getLogger(__name__)
PIVOT_MOVES_NORM = {normalize_name(m) for m in PIVOT_MOVES}
RECOVERY_MOVES_NORM = {normalize_name(m) for m in RECOVERY_MOVES}
SETUP_MOVES_NORM = {normalize_name(m) for m in SETUP_MOVES}
HAZARD_SETTING_MOVES_NORM = {normalize_name(m) for m in HAZARD_SETTING_MOVES}
STALL_SURVIVAL_MOVES_NORM = {
    "protect",
    "detect",
    "kingsshield",
    "spikyshield",
    "banefulbunker",
    "silktrap",
    "obstruct",
    "endure",
}

# =============================================================================
# HOT-RELOAD: pick up eval.py / forced_lines.py changes without restarting
# =============================================================================
_reload_lock = threading.Lock()


def _maybe_hot_reload():
    signal = Path(__file__).resolve().parent.parent.parent / ".reload"
    if not signal.exists():
        return
    with _reload_lock:
        if not signal.exists():
            return
        try:
            import importlib
            import fp.search.eval
            import fp.search.forced_lines
            importlib.reload(fp.search.eval)
            importlib.reload(fp.search.forced_lines)
            globals()['evaluate_position'] = fp.search.eval.evaluate_position
            globals()['_eval_opponent_best_damage'] = fp.search.eval._opponent_best_damage
            globals()['detect_forced_line'] = fp.search.forced_lines.detect_forced_line
            signal.unlink()
            logger.info("HOT RELOAD: eval.py + forced_lines.py reloaded successfully")
        except Exception as e:
            logger.error(f"HOT RELOAD FAILED (keeping old code): {e}")
            try:
                signal.unlink()
            except Exception:
                pass


# =============================================================================
# DECISION PROFILES (variance control)
# =============================================================================


class DecisionProfile(Enum):
    DEFAULT = "default"
    LOW = "low"
    HIGH = "high"


# Keep post-eval heuristics from drifting too far from core eval intent.
# 0.0 = raw eval only, 1.0 = full heuristic stack.
HEURISTIC_BLEND_ALPHA = 0.35
MCTS_BLEND_ENABLED = str(os.getenv("MCTS_BLEND_ENABLED", "1")).lower() not in {
    "0",
    "false",
    "no",
    "off",
}
MCTS_BLEND_BASE_ALPHA = max(
    0.0,
    min(1.0, float(os.getenv("MCTS_BLEND_BASE_ALPHA", "0.55"))),
)
MCTS_BLEND_HIGH_ALPHA = max(
    MCTS_BLEND_BASE_ALPHA,
    min(1.0, float(os.getenv("MCTS_BLEND_HIGH_ALPHA", "0.70"))),
)
MCTS_BLEND_MIN_TIME_MS = max(200, int(os.getenv("MCTS_BLEND_MIN_TIME_MS", "1800")))
MCTS_BLEND_MIN_PER_SAMPLE_MS = max(
    120, int(os.getenv("MCTS_BLEND_MIN_PER_SAMPLE_MS", "350"))
)
MCTS_BLEND_MAX_SAMPLES = max(1, int(os.getenv("MCTS_BLEND_MAX_SAMPLES", "2")))
MCTS_BLEND_UNCERTAIN_RATIO = max(
    1.01, float(os.getenv("MCTS_BLEND_UNCERTAIN_RATIO", "1.28"))
)
MCTS_BLEND_CLEAR_PROGRESS_RATIO = max(
    1.10, float(os.getenv("MCTS_BLEND_CLEAR_PROGRESS_RATIO", "1.45"))
)
MCTS_BLEND_MIN_EVAL_REL = max(
    0.0, min(0.5, float(os.getenv("MCTS_BLEND_MIN_EVAL_REL", "0.06")))
)
MCTS_BLEND_MIN_PROGRESS_REL = max(
    0.0, min(0.5, float(os.getenv("MCTS_BLEND_MIN_PROGRESS_REL", "0.03")))
)
MCTS_BLEND_MIN_KEEP = max(2, int(os.getenv("MCTS_BLEND_MIN_KEEP", "3")))
MCTS_BLEND_MAX_KEEP = max(
    MCTS_BLEND_MIN_KEEP, int(os.getenv("MCTS_BLEND_MAX_KEEP", "6"))
)
MCTS_BLEND_ANCHOR_BASE_RATIO = max(
    0.40, min(0.98, float(os.getenv("MCTS_BLEND_ANCHOR_BASE_RATIO", "0.72")))
)
MCTS_BLEND_ANCHOR_LOW_RATIO = max(
    MCTS_BLEND_ANCHOR_BASE_RATIO,
    min(0.99, float(os.getenv("MCTS_BLEND_ANCHOR_LOW_RATIO", "0.80"))),
)
MCTS_BLEND_ANCHOR_THREAT_RATIO = max(
    MCTS_BLEND_ANCHOR_LOW_RATIO,
    min(0.995, float(os.getenv("MCTS_BLEND_ANCHOR_THREAT_RATIO", "0.88"))),
)


def _stability_blend_policy(
    pre_policy: dict[str, float],
    post_policy: dict[str, float],
    alpha: float = HEURISTIC_BLEND_ALPHA,
) -> dict[str, float]:
    """Blend raw eval with heuristic-adjusted policy to reduce over-correction."""
    alpha = max(0.0, min(1.0, float(alpha)))
    if alpha <= 0.0:
        return dict(pre_policy)
    if alpha >= 1.0:
        return dict(post_policy)

    merged: dict[str, float] = {}
    all_moves = set(pre_policy) | set(post_policy)
    for move in all_moves:
        try:
            pre = float(pre_policy.get(move, 0.0))
        except (TypeError, ValueError):
            pre = 0.0
        try:
            post = float(post_policy.get(move, 0.0))
        except (TypeError, ValueError):
            post = 0.0

        # Keep weights non-negative for downstream thresholding.
        pre = max(0.0, pre)
        post = max(0.0, post)
        merged[move] = ((1.0 - alpha) * pre) + (alpha * post)

    return merged


def _normalize_policy_weights(policy: dict[str, float]) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    total = 0.0
    for move, weight in (policy or {}).items():
        try:
            w = float(weight)
        except (TypeError, ValueError):
            continue
        if w <= 0.0:
            continue
        cleaned[str(move)] = w
        total += w
    if total <= 0.0:
        return {}
    return {move: weight / total for move, weight in cleaned.items()}


def _top_policy_entries(
    policy: dict[str, float],
    limit: int = 5,
) -> list[dict[str, float | str]]:
    entries = sorted((policy or {}).items(), key=lambda x: x[1], reverse=True)[:limit]
    return [
        {"move": move, "weight": round(float(weight), 6)}
        for move, weight in entries
    ]


def _blend_eval_mcts_policy(
    eval_policy: dict[str, float],
    mcts_policy: dict[str, float],
    *,
    alpha: float,
) -> dict[str, float]:
    """
    Blend normalized eval policy with normalized MCTS policy.
    alpha=0 -> eval only, alpha=1 -> MCTS only.
    """
    alpha = max(0.0, min(1.0, float(alpha)))
    eval_norm = _normalize_policy_weights(eval_policy)
    mcts_norm = _normalize_policy_weights(mcts_policy)
    if not eval_norm:
        return dict(mcts_norm)
    if not mcts_norm:
        return dict(eval_norm)

    blended: dict[str, float] = {}
    all_moves = set(eval_norm) | set(mcts_norm)
    for move in all_moves:
        blended[move] = ((1.0 - alpha) * eval_norm.get(move, 0.0)) + (
            alpha * mcts_norm.get(move, 0.0)
        )
    return _normalize_policy_weights(blended)


def _should_activate_mcts_blend(
    *,
    battle: Battle,
    ability_state: "OpponentAbilityState | None",
    eval_policy: dict[str, float],
    decision_profile: DecisionProfile,
) -> tuple[bool, list[str]]:
    if not MCTS_BLEND_ENABLED:
        return False, ["disabled"]
    if battle is None or getattr(battle, "force_switch", False):
        return False, ["force_switch_or_no_battle"]

    normalized = _normalize_policy_weights(eval_policy)
    ranked = sorted(normalized.items(), key=lambda x: x[1], reverse=True)
    if len(ranked) < 2:
        return False, ["insufficient_eval_options"]

    top_w = float(ranked[0][1])
    second_w = float(ranked[1][1])
    ratio = float("inf") if second_w <= 0 else (top_w / second_w)
    top_move = str(ranked[0][0])

    reasons: list[str] = []

    uncertainty_cutoff = MCTS_BLEND_UNCERTAIN_RATIO
    if decision_profile == DecisionProfile.HIGH:
        uncertainty_cutoff += 0.08
    elif decision_profile == DecisionProfile.LOW:
        uncertainty_cutoff -= 0.05
    uncertainty_cutoff = max(1.02, uncertainty_cutoff)
    if ratio <= uncertainty_cutoff:
        reasons.append("uncertain_eval")

    early_turn = int(getattr(battle, "turn", 0) or 0) <= 3
    if early_turn and ratio <= (uncertainty_cutoff + 0.12):
        reasons.append("early_turn")

    boost_level = 0
    if ability_state is not None:
        boost_level = max(
            int(getattr(ability_state, "opponent_attack_boost", 0) or 0),
            int(getattr(ability_state, "opponent_spa_boost", 0) or 0),
        )
        if bool(getattr(ability_state, "opponent_active_is_threat", False)) and (
            boost_level >= 2 or ratio <= (uncertainty_cutoff + 0.15)
        ):
            reasons.append("active_threat")
        if boost_level >= 2 or (
            boost_level >= 1 and ratio <= uncertainty_cutoff
        ):
            reasons.append("opponent_boosted")
        if bool(getattr(ability_state, "ko_line_available", False)):
            reasons.append("ko_line_present")

    # If eval already has a clear direct progress line and no immediate danger,
    # skip blend to avoid MCTS over-correcting into defensive loops.
    top_is_progress = _is_meaningful_progress_move(
        top_move,
        battle,
        ability_state,
        include_status_pivots=False,
    )
    active_threat = bool(
        ability_state is not None
        and getattr(ability_state, "opponent_active_is_threat", False)
    )
    if (
        top_is_progress
        and ratio >= MCTS_BLEND_CLEAR_PROGRESS_RATIO
        and boost_level < 2
        and not active_threat
    ):
        return False, ["clear_eval_progress"]

    # If eval already has a very clear best line, only invoke MCTS when we are
    # in genuinely dangerous states (boosted threat). This avoids over-correcting
    # obvious progress turns.
    if ratio >= 1.7 and boost_level < 2:
        reasons = [r for r in reasons if r in {"opponent_boosted"}]

    return (len(reasons) > 0), reasons


def _run_mcts_policy_pass(
    sampled_battles: list[tuple[Battle, float]],
    *,
    per_sample_ms: int,
    max_samples: int,
    legal_moves: set[str] | None = None,
) -> tuple[dict[str, float], dict]:
    """
    Run bounded MCTS on up to max_samples sampled states and aggregate visit policy.
    """
    selected_samples = sorted(
        list(sampled_battles or []), key=lambda x: float(x[1]), reverse=True
    )[: max(1, int(max_samples))]
    aggregated: dict[str, float] = {}
    meta = {
        "samples_attempted": len(selected_samples),
        "samples_succeeded": 0,
        "samples_failed": 0,
        "total_visits": 0,
        "per_sample_ms": int(per_sample_ms),
    }
    legal_norm_to_move: dict[str, str] = {}
    if legal_moves:
        for lm in legal_moves:
            legal_norm_to_move[normalize_name(lm)] = lm

    # Timeout per sample: MCTS search time + 5s buffer for state conversion
    timeout_sec = (int(per_sample_ms) / 1000.0) + 5.0

    for idx, (sample_battle, sample_weight) in enumerate(selected_samples):
        try:
            state = battle_to_poke_engine_state(sample_battle)
            # Run MCTS with timeout to prevent hangs on pathological states
            with ThreadPoolExecutor(max_workers=1) as mcts_executor:
                future = mcts_executor.submit(
                    monte_carlo_tree_search, state, int(per_sample_ms)
                )
                try:
                    result = future.result(timeout=timeout_sec)
                except (FuturesTimeoutError, TimeoutError):
                    meta["samples_failed"] += 1
                    logger.warning(
                        "MCTS sample %s timed out after %.1fs (budget=%sms), skipping",
                        idx,
                        timeout_sec,
                        per_sample_ms,
                    )
                    continue
            total_visits = int(getattr(result, "total_visits", 0) or 0)
            if total_visits <= 0:
                meta["samples_failed"] += 1
                continue

            meta["samples_succeeded"] += 1
            meta["total_visits"] += total_visits

            for option in getattr(result, "side_one", []) or []:
                move_choice = str(getattr(option, "move_choice", "") or "")
                if not move_choice:
                    continue
                if legal_moves and move_choice not in legal_moves:
                    mapped = legal_norm_to_move.get(normalize_name(move_choice))
                    if mapped:
                        move_choice = mapped
                    else:
                        continue
                visits = float(getattr(option, "visits", 0) or 0.0)
                if visits <= 0:
                    continue
                contribution = float(sample_weight) * (visits / float(total_visits))
                aggregated[move_choice] = aggregated.get(move_choice, 0.0) + contribution
        except Exception as e:
            meta["samples_failed"] += 1
            logger.warning(
                "MCTS sample %s failed (budget=%sms): %s",
                idx,
                per_sample_ms,
                e,
            )

    return _normalize_policy_weights(aggregated), meta


def _choose_from_weighted_policy(
    policy: dict[str, float],
    *,
    decision_profile: DecisionProfile,
) -> str | None:
    ranked = sorted((policy or {}).items(), key=lambda x: x[1], reverse=True)
    if not ranked:
        return None

    if decision_profile == DecisionProfile.LOW:
        return ranked[0][0]

    if len(ranked) >= 2:
        top, second = ranked[0][1], ranked[1][1]
        dominance_ratio = 1.6 if decision_profile == DecisionProfile.HIGH else 1.4
        if second > 0 and (top / second) >= dominance_ratio:
            return ranked[0][0]

    considered_threshold = 0.85 if decision_profile == DecisionProfile.HIGH else 0.90
    best_weight = ranked[0][1]
    considered = [item for item in ranked if item[1] >= best_weight * considered_threshold]
    if not considered:
        return ranked[0][0]

    return random.choices(considered, weights=[w for _, w in considered], k=1)[0][0]


def _build_mcts_legal_move_set(
    eval_policy: dict[str, float],
    *,
    battle: Battle | None,
    ability_state: "OpponentAbilityState | None",
) -> set[str]:
    """
    Restrict MCTS blend choices to eval-credible lines.
    This prevents low-value tails from dominating after blend.
    """
    normalized = _normalize_policy_weights(eval_policy)
    ranked = sorted(normalized.items(), key=lambda x: x[1], reverse=True)
    if not ranked:
        return set()

    top_weight = max(float(ranked[0][1]), 1e-9)
    selected: list[tuple[str, float]] = []

    for idx, (move, weight) in enumerate(ranked):
        rel = float(weight) / top_weight
        include = idx < MCTS_BLEND_MIN_KEEP or rel >= MCTS_BLEND_MIN_EVAL_REL
        if (
            not include
            and rel >= MCTS_BLEND_MIN_PROGRESS_REL
            and _is_meaningful_progress_move(
                move,
                battle,
                ability_state,
                include_status_pivots=False,
            )
        ):
            include = True
        if include:
            selected.append((move, float(weight)))

    if not selected:
        selected = ranked[: MCTS_BLEND_MIN_KEEP]

    selected.sort(key=lambda x: x[1], reverse=True)
    limited = selected[: MCTS_BLEND_MAX_KEEP]
    return {move for move, _ in limited}


def _move_name_and_norm(move: str) -> tuple[str, str]:
    move_name = move.split(":")[-1] if ":" in move else move
    return move_name, normalize_name(move_name)


def _is_status_setup_move(move: str) -> bool:
    if not move or move.startswith("switch "):
        return False
    move_name, move_norm = _move_name_and_norm(move)
    move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
    return (
        move_data.get(constants.CATEGORY) == constants.STATUS
        and move_norm in SETUP_MOVES_NORM
    )


def _resolve_mcts_anchor_ratio(
    *,
    decision_profile: DecisionProfile,
    ability_state: "OpponentAbilityState | None",
) -> float:
    ratio = MCTS_BLEND_ANCHOR_BASE_RATIO
    if decision_profile == DecisionProfile.LOW:
        ratio = max(ratio, MCTS_BLEND_ANCHOR_LOW_RATIO)

    boost_level = 0
    active_threat = False
    if ability_state is not None:
        active_threat = bool(getattr(ability_state, "opponent_active_is_threat", False))
        boost_level = max(
            int(getattr(ability_state, "opponent_attack_boost", 0) or 0),
            int(getattr(ability_state, "opponent_spa_boost", 0) or 0),
        )

    if active_threat or boost_level >= 1:
        ratio = max(ratio, MCTS_BLEND_ANCHOR_THREAT_RATIO)
    return ratio


def _apply_mcts_eval_anchor_choice_guard(
    *,
    battle: Battle | None,
    ability_state: "OpponentAbilityState | None",
    eval_policy: dict[str, float],
    final_policy: dict[str, float],
    eval_choice: str | None,
    proposed_choice: str | None,
    decision_profile: DecisionProfile,
) -> tuple[str | None, dict]:
    """
    Keep MCTS blend from overriding a clearly credible eval line with a lower-trust
    fallback (switch churn or setup status) in volatile states.
    """
    metadata = {
        "applied": False,
        "reason": None,
        "eval_choice": eval_choice,
        "proposed_choice": proposed_choice,
    }
    if (
        battle is None
        or not eval_choice
        or not proposed_choice
        or eval_choice == proposed_choice
        or getattr(battle, "force_switch", False)
    ):
        return proposed_choice, metadata

    eval_norm = _normalize_policy_weights(eval_policy)
    final_norm = _normalize_policy_weights(final_policy)
    eval_weight = float(eval_norm.get(eval_choice, 0.0))
    if eval_weight <= 0.0:
        return proposed_choice, metadata

    ranked_eval = sorted(eval_norm.items(), key=lambda x: x[1], reverse=True)
    second_weight = 0.0
    for move, weight in ranked_eval:
        if move != eval_choice:
            second_weight = float(weight)
            break
    eval_ratio = float("inf") if second_weight <= 0.0 else (eval_weight / second_weight)

    incoming_pressure = None
    try:
        incoming_pressure = float(_eval_opponent_best_damage(battle))
    except Exception:
        incoming_pressure = None

    eval_move_name, eval_move_norm = _move_name_and_norm(eval_choice)
    eval_move_data = all_move_json.get(eval_move_norm, all_move_json.get(eval_move_name, {}))
    proposed_move_name, proposed_move_norm = _move_name_and_norm(proposed_choice)
    proposed_move_data = all_move_json.get(
        proposed_move_norm, all_move_json.get(proposed_move_name, {})
    )

    boost_level = 0
    active_threat = False
    if ability_state is not None:
        active_threat = bool(getattr(ability_state, "opponent_active_is_threat", False))
        boost_level = max(
            int(getattr(ability_state, "opponent_attack_boost", 0) or 0),
            int(getattr(ability_state, "opponent_spa_boost", 0) or 0),
        )

    eval_is_progress = _is_meaningful_progress_move(
        eval_choice,
        battle,
        ability_state,
        include_status_pivots=False,
        incoming_pressure=incoming_pressure,
    )
    # Under immediate threat, hazard setup is too often fake progress.
    if active_threat and eval_move_norm in HAZARD_SETTING_MOVES_NORM:
        eval_is_progress = False

    proposed_is_progress = _is_meaningful_progress_move(
        proposed_choice,
        battle,
        ability_state,
        include_status_pivots=False,
        incoming_pressure=incoming_pressure,
    )
    proposed_is_switch = proposed_choice.startswith("switch ")
    proposed_is_setup = _is_status_setup_move(proposed_choice)
    proposed_is_status = (
        proposed_move_data.get(constants.CATEGORY) == constants.STATUS and not proposed_is_setup
    )
    proposed_is_nonprogress = (not proposed_is_progress) or proposed_is_status

    user_active = getattr(getattr(battle, "user", None), "active", None)
    our_hp_ratio = 1.0
    if user_active is not None:
        our_hp_ratio = getattr(user_active, "hp", 1) / max(getattr(user_active, "max_hp", 1), 1)
    unaware_hold = _is_unaware_like(user_active) and active_threat and our_hp_ratio >= 0.40

    if active_threat and boost_level >= 1 and proposed_is_setup:
        metadata["applied"] = True
        metadata["reason"] = "boosted_threat_block_setup_override"
        return eval_choice, metadata

    if (
        active_threat
        and proposed_move_norm in HAZARD_SETTING_MOVES_NORM
        and eval_move_norm not in HAZARD_SETTING_MOVES_NORM
        and eval_is_progress
    ):
        metadata["applied"] = True
        metadata["reason"] = "boosted_threat_block_hazard_override"
        return eval_choice, metadata

    if unaware_hold and proposed_is_switch and eval_is_progress:
        metadata["applied"] = True
        metadata["reason"] = "unaware_hold_keep_progress_line"
        return eval_choice, metadata

    if proposed_is_setup and eval_is_progress and not eval_choice.startswith("switch "):
        # Outside explicit boosted-threat states, avoid flipping strong direct
        # progress lines into setup clicks under meaningful pressure.
        if incoming_pressure is not None and incoming_pressure >= max(0.18, our_hp_ratio * 0.42):
            metadata["applied"] = True
            metadata["reason"] = "block_setup_override_under_pressure"
            return eval_choice, metadata
        if eval_ratio >= 1.08 and decision_profile == DecisionProfile.LOW:
            metadata["applied"] = True
            metadata["reason"] = "block_setup_override_over_progress"
            return eval_choice, metadata

    survival = _assess_immediate_survival_risk(battle)
    if (
        bool(survival.get("risk", False))
        and proposed_is_switch
        and not eval_choice.startswith("switch ")
    ):
        metadata["applied"] = True
        metadata["reason"] = "survival_risk_prefer_switch"
        return proposed_choice, metadata

    confidence_floor = 1.20
    if decision_profile == DecisionProfile.LOW:
        confidence_floor = 1.22
    elif decision_profile == DecisionProfile.HIGH:
        confidence_floor = 1.30
    if active_threat:
        confidence_floor = min(confidence_floor, 1.15)
    if boost_level >= 2:
        confidence_floor = min(confidence_floor, 1.05)
    eval_confident = eval_ratio >= confidence_floor

    anchor_ratio = _resolve_mcts_anchor_ratio(
        decision_profile=decision_profile,
        ability_state=ability_state,
    )
    eval_floor = eval_weight * anchor_ratio
    final_eval_weight = float(final_norm.get(eval_choice, 0.0))

    if (
        eval_is_progress
        and eval_confident
        and final_eval_weight < eval_floor
        and (proposed_is_switch or proposed_is_setup or proposed_is_nonprogress)
    ):
        metadata["applied"] = True
        metadata["reason"] = "eval_anchor_progress_guard"
        metadata["eval_ratio"] = round(eval_ratio, 4)
        metadata["eval_weight"] = round(eval_weight, 6)
        metadata["final_eval_weight"] = round(final_eval_weight, 6)
        metadata["eval_floor"] = round(eval_floor, 6)
        return eval_choice, metadata

    return proposed_choice, metadata


def _apply_penalty_guard(
    base_policy: dict[str, float],
    penalized_policy: dict[str, float],
) -> dict[str, float]:
    """
    Keep only downward adjustments from a penalty pass.
    """
    guarded: dict[str, float] = {}
    for move, weight in (base_policy or {}).items():
        current = max(0.0, float(weight))
        adjusted = float(penalized_policy.get(move, current)) if penalized_policy else current
        guarded[move] = min(current, max(0.0, adjusted))
    return guarded


def _resolve_playstyle(team_plan: TeamAnalysis | None = None) -> Playstyle:
    """Resolve playstyle from config or team analysis (auto mode)."""
    try:
        if FoulPlayConfig.playstyle == "auto":
            if team_plan is not None:
                return team_plan.playstyle
            return PlaystyleConfig.get_team_playstyle(FoulPlayConfig.team_name or "")
        return Playstyle(FoulPlayConfig.playstyle)
    except Exception:
        return Playstyle.BALANCE


def _select_decision_profile(battle: Battle, playstyle: Playstyle) -> DecisionProfile:
    """Use low-variance profile by default in gen9ou; HO gets high variance."""
    if battle.pokemon_format != "gen9ou":
        return DecisionProfile.DEFAULT
    if playstyle == Playstyle.HYPER_OFFENSE:
        return DecisionProfile.HIGH
    return DecisionProfile.LOW


# =============================================================================
# ABILITY DETECTION
# =============================================================================


@dataclass
class OpponentAbilityState:
    """Tracks what abilities the opponent's active Pokemon has or likely has."""

    # === EXISTING FIELDS ===
    has_unaware: bool = False
    has_guts_like: bool = False  # Guts, Marvel Scale, Quick Feet
    has_poison_heal: bool = False
    has_purifying_salt: bool = False
    has_water_immunity: bool = False  # Water Absorb, Storm Drain, Dry Skin
    has_electric_immunity: bool = False  # Volt Absorb, Lightning Rod, Motor Drive
    has_flash_fire: bool = False
    has_levitate: bool = False
    has_magic_bounce: bool = False
    has_good_as_gold: bool = False  # Blocks ALL status moves
    has_competitive_defiant: bool = False  # Competitive or Defiant
    has_focus_sash: bool = False  # Focus Sash item
    has_phazing: bool = False  # Has revealed a phazing move
    has_substitute: bool = False  # Currently behind a Substitute
    has_contact_punish: bool = False  # Iron Barbs, Rough Skin, or Rocky Helmet
    has_status: bool = False  # Already has a status condition
    is_choice_locked: bool = False  # Opponent is choice-locked into a move
    choice_locked_move: str = ""  # The move they're locked into
    pokemon_name: str = ""
    ability_known: bool = False
    ability_name: str = ""
    at_full_hp: bool = True  # Whether opponent is at full HP (for Sash)

    # === NEW PHASE 2: HIGH PRIORITY ABILITIES ===
    has_contrary: bool = False  # Stat changes reversed (our drops = their boosts!)
    has_sap_sipper: bool = False  # Immune to Grass, +1 Atk
    has_sturdy: bool = False  # Survives any hit at full HP with 1 HP
    has_disguise: bool = False  # Mimikyu's first-hit immunity
    disguise_broken: bool = False  # Whether Disguise has been busted

    # === NEW PHASE 3: MOVE CATEGORY IMMUNITIES ===
    has_soundproof: bool = False  # Immune to sound-based moves
    has_bulletproof: bool = False  # Immune to ball/bullet moves
    has_overcoat: bool = False  # Immune to powder moves + weather
    is_grass_type: bool = False  # Natural powder immunity

    # === NEW PHASE 4-5: TYPE/STAT INTERACTIONS ===
    has_earth_eater: bool = False  # Immune to Ground, heals instead
    has_justified: bool = False  # Dark moves give +1 Atk
    has_steam_engine: bool = False  # Fire/Water give +6 Spe!
    has_wind_rider: bool = False  # Wind moves give +1 Atk
    has_well_baked_body: bool = False  # Fire moves give +2 Def

    # === NEW PHASE 6: STAT IMMUNITY/REFLECTION ===
    has_clear_body: bool = False  # Immune to stat drops (Clear Body/White Smoke/Full Metal Body)
    has_mirror_armor: bool = False  # Reflects stat drops back
    has_supreme_overlord: bool = False  # Atk/SpA boosts per fainted ally

    # === NEW PHASE 7: SPECIAL INTERACTIONS ===
    our_has_prankster: bool = False  # OUR Pokemon has Prankster
    our_has_mold_breaker: bool = False  # OUR Pokemon has Mold Breaker/Teravolt/Turboblaze
    opponent_is_dark_type: bool = False  # Opponent is Dark type (Prankster fails)
    has_air_balloon: bool = False  # Has Air Balloon (Ground immunity until popped)
    has_fluffy: bool = False  # Takes 2x Fire damage (opportunity!)
    has_dry_skin_fire_weak: bool = False  # Takes 1.25x Fire damage (opportunity!)
    has_queenly_majesty: bool = False  # Blocks priority moves
    has_dazzling: bool = False  # Blocks priority moves
    has_armor_tail: bool = False  # Blocks priority moves
    has_priority_block: bool = False  # Any priority-blocking ability

    # === NEW PHASE 8: WEATHER/TERRAIN CONTEXT ===
    weather: str = ""  # Current weather condition
    terrain: str = ""  # Current terrain condition
    opponent_is_grounded: bool = True  # Whether opponent is affected by terrain/ground moves

    # === PHASE 1.1: POSITIVE BOOSTS EXPANSION ===
    opponent_used_protect: bool = False  # Opponent used Protect last turn
    opponent_hp_percent: float = 1.0  # Opponent's HP percentage (for low HP priority)
    opponent_used_setup: bool = False  # Opponent used a setup move last turn
    opponent_used_damaging_move: bool = False  # Opponent used a damaging move last turn

    # === PHASE 1.2: TRICK ROOM AWARENESS ===
    trick_room_active: bool = False  # Trick Room is active
    trick_room_turns: int = 0  # Turns remaining on Trick Room
    our_is_slow: bool = False  # Our active Pokemon is slow (base speed < 60)
    our_speed_stat: int = 0  # Our current speed stat

    # === OUR BOOST STATE (ANTI-REDUNDANT SETUP) ===
    our_attack_boost: int = 0  # Replay: gen9ou-2532366515 turn 55 (Swords Dance at +4)
    our_spa_boost: int = 0  # Replay: gen9ou-2532366515 turn 55 (Swords Dance at +4)

    # === OPPONENT BOOST STATE (PROACTIVE SWEEP PREVENTION 2026-02-08) ===
    opponent_attack_boost: int = 0  # Opponent's Attack boost stage
    opponent_spa_boost: int = 0  # Opponent's Special Attack boost stage
    opponent_speed_boost: int = 0  # Opponent's Speed boost stage
    opponent_has_offensive_boost: bool = False  # Opponent has +1 or more Atk/SpA

    # === PHASE 1.3: SCREENS AWARENESS ===
    opponent_has_reflect: bool = False  # Opponent has Reflect up
    opponent_has_light_screen: bool = False  # Opponent has Light Screen up
    opponent_has_aurora_veil: bool = False  # Opponent has Aurora Veil up

    # === PHASE 1.4: WEATHER/TERRAIN SYNERGIES ===
    we_have_weather_speed: bool = False  # We have Swift Swim/Sand Rush/etc in matching weather
    terrain_type: str = ""  # electricterrain, grassyterrain, psychicterrain, mistyterrain
    we_are_grounded: bool = True  # Whether we are grounded (for terrain effects)

    # === PHASE 2.1: SWITCH EVALUATION ===
    our_hazard_layers: int = 0  # Total effective hazard layers on our side
    our_sr_up: bool = False  # Stealth Rock on our side
    our_spikes_layers: int = 0  # Spikes layers on our side
    opponent_has_defiant_pokemon: bool = False  # Opponent team has Defiant/Competitive

    # === PHASE 2.2: ENTRY HAZARD CALCULUS ===
    opponent_sr_up: bool = False  # Stealth Rock on opponent's side
    opponent_spikes_layers: int = 0  # Spikes layers on opponent's side
    opponent_hazard_layers: int = 0  # Effective hazard pressure on opponent side
    opponent_has_sr_weak: bool = False  # Opponent has SR-weak Pokemon in back
    opponent_has_hazard_removal: bool = False  # Opponent has revealed Defog/Spin
    opponent_active_has_hazard_removal: bool = False  # Active foe can remove hazards now
    opponent_active_removal_is_spin: bool = False  # Active foe has Rapid Spin / Mortal Spin
    opponent_has_salt_cure: bool = False  # Active foe has revealed Salt Cure
    opponent_alive_count: int = 6  # Number of opponent Pokemon still alive
    our_has_alive_spinblocker: bool = False  # We have at least one living Ghost type
    our_active_is_spinblocker: bool = False  # Our active is a Ghost type

    # === PHASE 2.3: TERA PREDICTION ===
    opponent_has_terastallized: bool = False  # Opponent has already used Tera
    we_have_terastallized: bool = False  # We have already used Tera
    opponent_tera_type: str = ""  # Opponent's Tera type if known
    our_tera_type: str = ""  # Our active Pokemon's Tera type

    # === PHASE 3.1: WIN CONDITION AWARENESS ===
    our_active_is_wincon: bool = False  # Our active is a win condition
    opponent_active_is_threat: bool = False  # Opponent active is a major threat
    ko_line_available: bool = False  # We have a simple KO line this turn
    ko_line_turns: int = 0  # 1 or 2 when a KO line is detected
    ko_line_move: str = ""  # Suggested KO move

    # === PHASE 3.2: PP TRACKING ===
    opponent_recovery_pp_exhausted: bool = False  # Opponent recovery PP likely exhausted
    opponent_low_stab_pp: bool = False  # Opponent STAB PP low

    # === HEALING WASTE AVOIDANCE ===
    our_hp_percent: float = 1.0  # Our active HP percentage
    our_active_is_salt_cured: bool = False  # Our active is under Salt Cure volatile

    # === PHASE 3.3: MOMENTUM TRACKING ===
    momentum: float = 0.0  # Positive = we have momentum
    momentum_level: str = "neutral"  # strong_positive, positive, neutral, negative, strong_negative
    our_alive_count: int = 6  # Number of our Pokemon still alive


def _check_ability_or_pokemon(
    ability: str | None,
    pokemon_name: str,
    base_name: str | None,
    ability_names: set[str],
    pokemon_set: set[str],
) -> bool:
    """
    Check if a Pokemon has a specific ability, either known or inferred.

    Args:
        ability: The known ability (or None if unknown)
        pokemon_name: The Pokemon's name
        base_name: The Pokemon's base name (for forme variations)
        ability_names: Set of ability names to check against
        pokemon_set: Set of Pokemon that commonly have these abilities
    """
    if ability:
        return ability in ability_names

    # If ability is unknown, check if Pokemon commonly has it
    return pokemon_name in pokemon_set or (base_name and base_name in pokemon_set)


def calculate_momentum(battle: Battle) -> tuple[float, str]:
    """
    Calculate momentum score for the battle.
    Returns (momentum_score, momentum_level).
    Positive = we have advantage, Negative = opponent has advantage.
    """
    momentum = 0.0

    # HP advantage (sum of HP percentages)
    our_pokemon = [battle.user.active] + battle.user.reserve if battle.user.active else battle.user.reserve
    opp_pokemon = [battle.opponent.active] + battle.opponent.reserve if battle.opponent.active else battle.opponent.reserve

    our_hp = sum(p.hp / max(p.max_hp, 1) for p in our_pokemon if p and p.hp > 0)
    opp_hp = sum(p.hp / max(p.max_hp, 1) for p in opp_pokemon if p and p.hp > 0)
    momentum += (our_hp - opp_hp) * 0.5

    # Pokemon count advantage
    our_alive = sum(1 for p in our_pokemon if p and p.hp > 0)
    opp_alive = sum(1 for p in opp_pokemon if p and p.hp > 0)
    momentum += (our_alive - opp_alive) * 1.5

    # Hazard advantage (hazards on opponent = good for us)
    our_hazards = (
        (2 if battle.user.side_conditions.get(constants.STEALTH_ROCK, 0) > 0 else 0)
        + battle.user.side_conditions.get(constants.SPIKES, 0)
    )
    opp_hazards = (
        (2 if battle.opponent.side_conditions.get(constants.STEALTH_ROCK, 0) > 0 else 0)
        + battle.opponent.side_conditions.get(constants.SPIKES, 0)
    )
    momentum += (opp_hazards - our_hazards) * 0.5

    # Categorize momentum level
    if momentum >= MOMENTUM_STRONG_POSITIVE:
        level = "strong_positive"
    elif momentum >= MOMENTUM_POSITIVE:
        level = "positive"
    elif momentum <= MOMENTUM_STRONG_NEGATIVE:
        level = "strong_negative"
    elif momentum <= MOMENTUM_NEGATIVE:
        level = "negative"
    else:
        level = "neutral"

    return momentum, level


def _estimate_board_advantage(
    battle: Battle | None,
    ability_state: "OpponentAbilityState | None" = None,
) -> tuple[float, bool]:
    """
    Estimate whether we are ahead enough to favor conversion (direct progress)
    over defensive shuffling.
    """
    if battle is None:
        return 0.0, False

    our_team = ([battle.user.active] if battle.user.active else []) + list(
        getattr(battle.user, "reserve", []) or []
    )
    opp_team = ([battle.opponent.active] if battle.opponent.active else []) + list(
        getattr(battle.opponent, "reserve", []) or []
    )

    our_alive = sum(1 for p in our_team if p is not None and getattr(p, "hp", 0) > 0)
    opp_alive = sum(1 for p in opp_team if p is not None and getattr(p, "hp", 0) > 0)
    our_hp_total = sum(
        getattr(p, "hp", 0) / max(getattr(p, "max_hp", 1), 1)
        for p in our_team
        if p is not None and getattr(p, "hp", 0) > 0
    )
    opp_hp_total = sum(
        getattr(p, "hp", 0) / max(getattr(p, "max_hp", 1), 1)
        for p in opp_team
        if p is not None and getattr(p, "hp", 0) > 0
    )

    score = 0.0
    score += (our_alive - opp_alive) * 1.10
    score += (our_hp_total - opp_hp_total) * 0.32

    if ability_state is not None:
        score += max(-2.5, min(2.5, float(getattr(ability_state, "momentum", 0.0) or 0.0))) * 0.20
        score += min(3.0, float(getattr(ability_state, "opponent_hazard_layers", 0.0) or 0.0)) * 0.14
        score -= min(3.0, float(getattr(ability_state, "our_hazard_layers", 0.0) or 0.0)) * 0.10

    # Conservative threshold: avoid forcing "conversion mode" in marginal spots.
    return score, (score >= 0.75)


def is_likely_wincon(pokemon, battle: Battle) -> bool:
    """Check if a Pokemon is likely a win condition based on its characteristics."""
    if pokemon is None or pokemon.hp <= 0:
        return False

    score = 0

    # Has setup move
    pokemon_moves = [m.name if hasattr(m, "name") else str(m) for m in getattr(pokemon, "moves", [])]
    for move in pokemon_moves:
        if move in SETUP_MOVES:
            score += 3
            break

    # High speed (potential sweeper)
    speed = pokemon.stats.get(constants.SPEED, 50) if hasattr(pokemon, "stats") else 50
    if speed > 100:
        score += 2

    # High offensive stats
    atk = pokemon.stats.get(constants.ATTACK, 50) if hasattr(pokemon, "stats") else 50
    spa = pokemon.stats.get(constants.SPECIAL_ATTACK, 50) if hasattr(pokemon, "stats") else 50
    if atk > 120 or spa > 120:
        score += 1

    # Already has boosts (but discount vs Unaware opponents)
    boosts = getattr(pokemon, "boosts", {}) or {}
    if boosts.get(constants.ATTACK, 0) > 0 or boosts.get(constants.SPECIAL_ATTACK, 0) > 0:
        opp_active = getattr(getattr(battle, "opponent", None), "active", None)
        opp_ability = normalize_name(getattr(opp_active, "ability", "") or "")
        opp_name = normalize_name(getattr(opp_active, "name", "") or "")
        if opp_ability != "unaware" and opp_name not in POKEMON_COMMONLY_UNAWARE:
            score += 2

    return score >= 4


def _hazard_move_can_progress(move_norm: str, battle: Battle | None) -> bool:
    """Return True when a hazard-setting move can still add new board value."""
    if battle is None:
        return True
    opp_side = getattr(getattr(battle, "opponent", None), "side_conditions", {}) or {}
    if move_norm == "spikes":
        return int(opp_side.get(constants.SPIKES, 0) or 0) < 3
    if move_norm == "toxicspikes":
        return int(opp_side.get(constants.TOXIC_SPIKES, 0) or 0) < 2
    if move_norm == "stealthrock":
        return int(opp_side.get(constants.STEALTH_ROCK, 0) or 0) <= 0
    if move_norm == "stickyweb":
        return int(opp_side.get(constants.STICKY_WEB, 0) or 0) <= 0
    return True


def detect_odd_move(
    battle: Battle,
    move_choice: str,
    ability_state: OpponentAbilityState | None = None,
) -> list[str]:
    """Return a list of odd/wasted-turn heuristics triggered by the move."""
    if not move_choice:
        return []
    move_name = move_choice.split(":")[-1] if ":" in move_choice else move_choice
    move_norm = normalize_name(move_name)
    oddities = []
    last_move_raw = getattr(getattr(battle, "user", None), "last_selected_move", None)
    last_move = getattr(last_move_raw, "move", "") if last_move_raw is not None else ""
    last_move_norm = normalize_name(last_move)
    hazard_setting_norm = {normalize_name(m) for m in HAZARD_SETTING_MOVES}
    hazard_removal_norm = {normalize_name(m) for m in HAZARD_REMOVAL_MOVES}
    recovery_moves_norm = {normalize_name(m) for m in RECOVERY_MOVES}
    status_moves_norm = {normalize_name(m) for m in ALL_STATUS_MOVES}
    status_inflicting_norm = {normalize_name(m) for m in STATUS_INFLICTING_MOVES}

    if ability_state is not None:
        # Hazard removal with no hazards
        if move_norm in hazard_removal_norm and ability_state.our_hazard_layers <= 0:
            oddities.append("waste_turn:remove_hazards_no_hazards")
            if last_move_norm == move_norm:
                oddities.append("waste_turn:repeat_hazard_removal")

        # Full-HP recovery
        if move_norm in recovery_moves_norm and ability_state.our_hp_percent >= 0.95:
            oddities.append("waste_turn:full_hp_recovery")

        # Status moves into known immunities/blocks
        if move_norm in status_moves_norm:
            if ability_state.has_good_as_gold:
                oddities.append("waste_turn:status_blocked_good_as_gold")
            if ability_state.has_magic_bounce and move_norm in {
                normalize_name(m) for m in MAGIC_BOUNCE_REFLECTED_MOVES
            }:
                oddities.append("waste_turn:status_reflected_magic_bounce")
            if ability_state.has_poison_heal and move_norm in {
                normalize_name(m) for m in TOXIC_POISON_MOVES
            }:
                oddities.append("waste_turn:status_into_poison_heal")
            if ability_state.has_purifying_salt and move_norm in status_inflicting_norm:
                oddities.append("waste_turn:status_into_purifying_salt")

    # Future Sight/Doom Desire already pending on opponent side
    if move_norm in {normalize_name(constants.FUTURE_SIGHT), "doomdesire"}:
        try:
            if battle.opponent.future_sight[0] > 0:
                oddities.append("waste_turn:future_sight_already_pending")
        except Exception:
            pass

    # Hazard setup with no incremental value (maxed/already active).
    if move_norm in hazard_setting_norm and not _hazard_move_can_progress(move_norm, battle):
        oddities.append("waste_turn:hazard_no_progress")

    # Repeating a non-damaging move on consecutive turns
    move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
    if move_data.get(constants.CATEGORY) == constants.STATUS:
        if last_move_norm == move_norm:
            # Repeating hazards can be correct while layers are still progressing.
            if move_norm in hazard_setting_norm and _hazard_move_can_progress(move_norm, battle):
                return oddities
            oddities.append("waste_turn:repeat_status_move")

    # ── Destiny Bond awareness ──
    # If the opponent has revealed Destiny Bond and is likely to use it
    # (low HP), penalize KO-ing with a valuable Pokemon.
    opponent = getattr(battle, "opponent", None)
    opp_active = getattr(opponent, "active", None) if opponent else None
    if (
        opp_active is not None
        and move_data.get(constants.CATEGORY) in {constants.PHYSICAL, constants.SPECIAL}
        and not move_choice.startswith("switch ")
    ):
        opp_moves = getattr(opp_active, "moves", []) or []
        opp_move_names = {
            normalize_name(m.name if hasattr(m, "name") else str(m))
            for m in opp_moves
        }
        if "destinybond" in opp_move_names:
            opp_hp = getattr(opp_active, "hp", 1)
            opp_max = max(getattr(opp_active, "max_hp", 1), 1)
            if opp_hp / opp_max <= 0.40:
                oddities.append("risk:destiny_bond_likely")

    # ── Setup moves with no stat-using attack ──
    # Calm Mind / Nasty Plot are useless when the only damaging moves
    # are fixed-damage (Seismic Toss) or don't use the boosted stat.
    SPA_SETUP = {"calmmind", "nastyplot", "quiverdance", "tailglow", "geomancy", "torchsong"}
    ATK_SETUP = {"swordsdance", "dragondance", "bellydrum", "howl", "honeclaws", "victorydance"}
    if move_norm in SPA_SETUP or move_norm in ATK_SETUP:
        boosted_category = constants.SPECIAL if move_norm in SPA_SETUP else constants.PHYSICAL
        our_active = getattr(getattr(battle, "user", None), "active", None)
        if our_active is not None:
            our_moves = getattr(our_active, "moves", []) or []
            has_stat_using_attack = False
            for m in our_moves:
                mn = normalize_name(m.name if hasattr(m, "name") else str(m))
                md = all_move_json.get(mn, {})
                if md.get(constants.CATEGORY) == boosted_category:
                    if not _is_fixed_damage_attack(mn, md):
                        has_stat_using_attack = True
                        break
            if not has_stat_using_attack:
                oddities.append("waste_turn:setup_no_stat_attack")

    return oddities


def _log_oddities(choice: str, oddities: list[str]):
    if oddities:
        logger.warning(f"Odd move detected: {choice} -> {', '.join(oddities)}")


def apply_oddity_penalties(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """Penalize choices that are known low-value oddities in the current state."""
    if battle is None:
        return policy

    adjusted = dict(policy)
    for move, weight in policy.items():
        if weight <= 0:
            continue
        oddities = detect_odd_move(battle, move, ability_state)
        if not oddities:
            continue

        penalty = 1.0
        for odd in oddities:
            if odd == "waste_turn:repeat_status_move":
                penalty = min(penalty, 0.30)
            elif odd in {
                "waste_turn:remove_hazards_no_hazards",
                "waste_turn:repeat_hazard_removal",
                "waste_turn:full_hp_recovery",
                "waste_turn:future_sight_already_pending",
                "waste_turn:hazard_no_progress",
            }:
                penalty = min(penalty, 0.25)
            elif odd in {
                "waste_turn:status_blocked_good_as_gold",
                "waste_turn:status_reflected_magic_bounce",
                "waste_turn:status_into_poison_heal",
                "waste_turn:status_into_purifying_salt",
            }:
                penalty = min(penalty, 0.10)
            elif odd == "risk:destiny_bond_likely":
                penalty = min(penalty, 0.15)
            elif odd == "waste_turn:setup_no_stat_attack":
                penalty = min(penalty, 0.15)
            else:
                penalty = min(penalty, 0.50)

        new_weight = weight * penalty
        adjusted[move] = new_weight
        if trace_events is not None:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "oddity",
                    "move": move,
                    "reason": ",".join(oddities),
                    "before": weight,
                    "after": new_weight,
                }
            )
    return adjusted


def find_ko_line(battle: Battle) -> dict | None:
    """Detect a simple 1-2 turn KO line for the current active matchup."""
    if battle is None or battle.user.active is None or battle.opponent.active is None:
        return None

    our = battle.user.active
    opp = battle.opponent.active
    move_names = [
        m.name if hasattr(m, "name") else str(m) for m in getattr(our, "moves", [])
    ]
    if not move_names:
        return None

    try:
        from fp.search.endgame import estimate_damage
    except Exception:
        return None

    speed_assessment = assess_speed_order(battle)
    guaranteed_move_first = speed_assessment.guaranteed_move_first

    best_one_shot = None
    for move_name in move_names:
        damage = estimate_damage(our, opp, move_name)
        if damage >= 1.0 and (guaranteed_move_first or move_name in PRIORITY_MOVES):
            if best_one_shot is None or damage > best_one_shot["damage"]:
                best_one_shot = {
                    "turns": 1,
                    "move": move_name,
                    "damage": round(damage, 3),
                    "outspeed": guaranteed_move_first,
                }
    if best_one_shot:
        return best_one_shot

    best_two_shot = None
    if guaranteed_move_first:
        for move_name in move_names:
            damage = estimate_damage(our, opp, move_name)
            if damage >= 0.5:
                if best_two_shot is None or damage > best_two_shot["damage"]:
                    best_two_shot = {
                        "turns": 2,
                        "move": move_name,
                        "damage": round(damage, 3),
                        "outspeed": guaranteed_move_first,
                    }
    return best_two_shot


def detect_opponent_abilities(battle: Battle) -> OpponentAbilityState:
    """
    Analyze the opponent's active Pokemon to detect relevant abilities.

    Returns an OpponentAbilityState with flags for each ability category.
    """
    state = OpponentAbilityState()

    if battle.opponent.active is None:
        return state

    opponent = battle.opponent.active
    ability = opponent.ability
    name = opponent.name
    base_name = getattr(opponent, "base_name", None)
    item = getattr(opponent, "item", None)

    state.pokemon_name = name
    state.ability_known = bool(ability)
    state.ability_name = ability or "unknown"

    # Check if our Pokemon has Mold Breaker (ignores defensive abilities)
    our_has_mold_breaker = False
    our_has_prankster = False
    if battle.user.active is not None:
        our_ability = battle.user.active.ability
        if our_ability:
            if our_ability in MOLD_BREAKER_ABILITIES:
                our_has_mold_breaker = True
            if our_ability == "prankster":
                our_has_prankster = True

    state.our_has_mold_breaker = our_has_mold_breaker
    state.our_has_prankster = our_has_prankster

    # Track our current offensive boosts (anti-redundant setup)
    if battle.user.active is not None:
        boosts = getattr(battle.user.active, "boosts", {}) or {}
        state.our_attack_boost = int(boosts.get(constants.ATTACK, 0) or 0)
        state.our_spa_boost = int(boosts.get(constants.SPECIAL_ATTACK, 0) or 0)

    # Track opponent's offensive boosts (PROACTIVE SWEEP PREVENTION 2026-02-08)
    opp_boosts = getattr(opponent, "boosts", {}) or {}
    state.opponent_attack_boost = int(opp_boosts.get(constants.ATTACK, 0) or 0)
    state.opponent_spa_boost = int(opp_boosts.get(constants.SPECIAL_ATTACK, 0) or 0)
    state.opponent_speed_boost = int(opp_boosts.get(constants.SPEED, 0) or 0)
    state.opponent_has_offensive_boost = (
        state.opponent_attack_boost > 0 or state.opponent_spa_boost > 0
    )

    # === WEATHER AND TERRAIN DETECTION ===
    state.weather = getattr(battle, "weather", "") or ""
    state.terrain = getattr(battle, "field", "") or ""

    # === OPPONENT TYPE DETECTION ===
    opponent_types = getattr(opponent, "types", []) or []
    state.opponent_is_dark_type = "dark" in opponent_types
    state.is_grass_type = "grass" in opponent_types

    # === GROUNDED CHECK ===
    # Pokemon is grounded unless: Flying type, Levitate, Air Balloon, or Magnet Rise
    state.opponent_is_grounded = True
    if "flying" in opponent_types:
        state.opponent_is_grounded = False
    elif ability and ability in UNGROUNDED_ABILITIES:
        state.opponent_is_grounded = False
    elif item and item in UNGROUNDED_ITEMS:
        state.opponent_is_grounded = False
    # Check volatile statuses for Magnet Rise, Telekinesis
    volatile_statuses = getattr(opponent, "volatile_statuses", []) or []
    if "magnetrise" in volatile_statuses or "telekinesis" in volatile_statuses:
        state.opponent_is_grounded = False

    # === EXISTING ABILITY DETECTION ===

    # Unaware
    state.has_unaware = _check_ability_or_pokemon(
        ability, name, base_name, {"unaware"}, POKEMON_COMMONLY_UNAWARE
    )

    # Guts-like abilities (status boosts them)
    state.has_guts_like = _check_ability_or_pokemon(
        ability,
        name,
        base_name,
        {"guts", "marvelscale", "quickfeet"},
        POKEMON_STATUS_BACKFIRES,
    )

    # Poison Heal
    state.has_poison_heal = _check_ability_or_pokemon(
        ability, name, base_name, {"poisonheal"}, POKEMON_COMMONLY_POISON_HEAL
    )

    # Purifying Salt: status immunity + ghost damage reduction.
    # Garganacl almost always leverages this for long-game progress.
    state.has_purifying_salt = _check_ability_or_pokemon(
        ability, name, base_name, {"purifyingsalt"}, {"garganacl"}
    )

    # Type immunities - Mold Breaker bypasses these
    if not our_has_mold_breaker:
        # Water immunity
        state.has_water_immunity = _check_ability_or_pokemon(
            ability,
            name,
            base_name,
            {"waterabsorb", "stormdrain", "dryskin"},
            POKEMON_COMMONLY_WATER_IMMUNE,
        )

        # Electric immunity
        state.has_electric_immunity = _check_ability_or_pokemon(
            ability,
            name,
            base_name,
            {"voltabsorb", "lightningrod", "motordrive"},
            POKEMON_COMMONLY_ELECTRIC_IMMUNE,
        )

        # Flash Fire
        state.has_flash_fire = _check_ability_or_pokemon(
            ability, name, base_name, {"flashfire"}, POKEMON_COMMONLY_FLASH_FIRE
        )

        # Levitate
        state.has_levitate = _check_ability_or_pokemon(
            ability, name, base_name, {"levitate"}, POKEMON_COMMONLY_LEVITATE
        )

        # NEW: Sap Sipper (Grass immunity + Atk boost)
        state.has_sap_sipper = _check_ability_or_pokemon(
            ability, name, base_name, {"sapsipper"}, POKEMON_COMMONLY_SAP_SIPPER
        )

        # NEW: Earth Eater (Ground immunity + heal)
        state.has_earth_eater = _check_ability_or_pokemon(
            ability, name, base_name, {"eartheater"}, POKEMON_COMMONLY_EARTH_EATER
        )

        # NEW: Soundproof (Sound move immunity)
        state.has_soundproof = _check_ability_or_pokemon(
            ability, name, base_name, {"soundproof"}, POKEMON_COMMONLY_SOUNDPROOF
        )

        # NEW: Bulletproof (Bullet/ball move immunity)
        state.has_bulletproof = _check_ability_or_pokemon(
            ability, name, base_name, {"bulletproof"}, POKEMON_COMMONLY_BULLETPROOF
        )

        # NEW: Overcoat (Powder move immunity)
        state.has_overcoat = _check_ability_or_pokemon(
            ability, name, base_name, {"overcoat"}, POKEMON_COMMONLY_OVERCOAT
        )


        # NEW: Sturdy (Sash-like effect at full HP)
        state.has_sturdy = _check_ability_or_pokemon(
            ability, name, base_name, {"sturdy"}, POKEMON_COMMONLY_STURDY
        )

        # NEW: Fluffy (2x Fire damage - opportunity!)
        state.has_fluffy = _check_ability_or_pokemon(
            ability, name, base_name, {"fluffy"}, POKEMON_COMMONLY_FLUFFY
        )

        # NEW: Dry Skin fire weakness (1.25x Fire - opportunity!)
        # Note: Dry Skin also gives water immunity, handled above
        if _check_ability_or_pokemon(
            ability, name, base_name, {"dryskin"}, POKEMON_COMMONLY_DRY_SKIN
        ):
            state.has_dry_skin_fire_weak = True

        # NEW: Wind Rider (Wind move immunity + Atk boost)
        state.has_wind_rider = _check_ability_or_pokemon(
            ability, name, base_name, {"windrider"}, POKEMON_COMMONLY_WIND_RIDER
        )

        # NEW: Well-Baked Body (Fire immunity + Def boost)
        state.has_well_baked_body = _check_ability_or_pokemon(
            ability, name, base_name, {"wellbakedbody"}, POKEMON_COMMONLY_WELL_BAKED_BODY
        )
    else:
        logger.info(
            f"Our Pokemon has Mold Breaker - ignoring type-immunity abilities"
        )

    # Magic Bounce - NOT affected by Mold Breaker for reflected moves
    state.has_magic_bounce = _check_ability_or_pokemon(
        ability, name, base_name, {"magicbounce"}, POKEMON_COMMONLY_MAGIC_BOUNCE
    )

    # Good as Gold - Blocks ALL status moves (not affected by Mold Breaker)
    state.has_good_as_gold = _check_ability_or_pokemon(
        ability, name, base_name, {"goodasgold"}, POKEMON_COMMONLY_GOOD_AS_GOLD
    )

    # Priority-blocking abilities (not bypassed by Mold Breaker)
    state.has_queenly_majesty = _check_ability_or_pokemon(
        ability, name, base_name, {"queenlymajesty"}, POKEMON_COMMONLY_QUEENLY_MAJESTY
    )
    state.has_dazzling = _check_ability_or_pokemon(
        ability, name, base_name, {"dazzling"}, POKEMON_COMMONLY_DAZZLING
    )
    state.has_armor_tail = _check_ability_or_pokemon(
        ability, name, base_name, {"armortail"}, POKEMON_COMMONLY_ARMOR_TAIL
    )
    state.has_priority_block = (
        state.has_queenly_majesty or state.has_dazzling or state.has_armor_tail
    )

    # Competitive/Defiant
    state.has_competitive_defiant = _check_ability_or_pokemon(
        ability,
        name,
        base_name,
        {"competitive", "defiant"},
        POKEMON_STAT_DROP_BACKFIRES,
    )

    # === NEW ABILITY DETECTION (NOT BYPASSED BY MOLD BREAKER) ===

    # Contrary - stat changes reversed (affects their own stats, not our attack)
    state.has_contrary = _check_ability_or_pokemon(
        ability, name, base_name, {"contrary"}, POKEMON_COMMONLY_CONTRARY
    )

    # Disguise - Mimikyu's first-hit immunity (form change, not damage prevention)
    state.has_disguise = _check_ability_or_pokemon(
        ability, name, base_name, {"disguise"}, POKEMON_COMMONLY_DISGUISE
    )
    # Check if Disguise has been busted (Mimikyu-Busted forme)
    if state.has_disguise:
        if "busted" in name.lower() or "disguisebroken" in volatile_statuses:
            state.disguise_broken = True

    # Mirror Armor - reflects stat drops (different mechanic than damage prevention)
    state.has_mirror_armor = _check_ability_or_pokemon(
        ability, name, base_name, {"mirrorarmor"}, POKEMON_COMMONLY_MIRROR_ARMOR
    )

    # Supreme Overlord - Kingambit (gets stronger as allies faint)
    state.has_supreme_overlord = _check_ability_or_pokemon(
        ability, name, base_name, {"supremeoverlord"}, POKEMON_COMMONLY_SUPREME_OVERLORD
    )

    # Clear Body / White Smoke / Full Metal Body - stat drop immunity
    state.has_clear_body = _check_ability_or_pokemon(
        ability, name, base_name,
        {"clearbody", "whitesmoke", "fullmetalbody"},
        POKEMON_COMMONLY_STAT_DROP_IMMUNE
    )

    # Justified - Dark moves boost their Attack
    state.has_justified = _check_ability_or_pokemon(
        ability, name, base_name, {"justified"}, POKEMON_COMMONLY_JUSTIFIED
    )

    # Steam Engine - Fire/Water moves give +6 Speed!
    state.has_steam_engine = _check_ability_or_pokemon(
        ability, name, base_name, {"steamengine"}, POKEMON_COMMONLY_STEAM_ENGINE
    )

    # === FOCUS SASH DETECTION ===
    if item == "focussash":
        state.has_focus_sash = True
    elif item in (None, "unknownitem", "") and name in POKEMON_COMMONLY_FOCUS_SASH:
        state.has_focus_sash = True

    # Check if opponent is at full HP (Sash/Sturdy only works at full HP)
    state.at_full_hp = opponent.hp == opponent.max_hp

    # === AIR BALLOON DETECTION ===
    if item == "airballoon":
        state.has_air_balloon = True
    elif item in (None, "unknownitem", "") and name in POKEMON_COMMONLY_AIR_BALLOON:
        state.has_air_balloon = True

    # === PHAZING DETECTION ===
    for move in opponent.moves:
        move_name = move.name if hasattr(move, "name") else str(move)
        move_norm = normalize_name(move_name)
        if move_name in PHAZING_MOVES:
            state.has_phazing = True
            break
        if move_norm == "saltcure":
            state.opponent_has_salt_cure = True

    # === SUBSTITUTE DETECTION ===
    if "substitute" in volatile_statuses:
        state.has_substitute = True

    # === CONTACT PUNISHMENT DETECTION ===
    if ability and ability in {"ironbarbs", "roughskin"}:
        state.has_contact_punish = True
    elif item and item == "rockyhelmet":
        state.has_contact_punish = True
    elif (
        name in POKEMON_COMMONLY_IRON_BARBS
        or name in POKEMON_COMMONLY_ROUGH_SKIN
        or name in POKEMON_COMMONLY_ROCKY_HELMET
    ):
        state.has_contact_punish = True

    # === STATUS DETECTION ===
    opponent_status = getattr(opponent, "status", None)
    if opponent_status is not None:
        state.has_status = True

    # === CHOICE-LOCK DETECTION ===
    opp_battler = battle.opponent
    opp_item = getattr(opponent, "item", None)
    if opp_item in ("choiceband", "choicescarf", "choicespecs"):
        last_move = opp_battler.last_used_move
        if (
            last_move
            and last_move.pokemon_name == opponent.name
            and last_move.move
            and last_move.move not in ("trick", "switcheroo")
        ):
            state.is_choice_locked = True
            state.choice_locked_move = last_move.move

    # =========================================================================
    # PHASE 1.1: POSITIVE BOOSTS EXPANSION
    # =========================================================================

    # Detect if opponent used Protect last turn
    opp_last_move = opp_battler.last_used_move
    if opp_last_move and opp_last_move.move:
        if opp_last_move.move in PROTECT_MOVES:
            state.opponent_used_protect = True
        if opp_last_move.move in SETUP_MOVES:
            state.opponent_used_setup = True
        move_key = normalize_name(opp_last_move.move)
        move_data = all_move_json.get(move_key, {})
        move_category = move_data.get(constants.CATEGORY)
        if move_category and move_category != constants.STATUS:
            state.opponent_used_damaging_move = True

    # Track opponent HP percentage
    if opponent.max_hp > 0:
        state.opponent_hp_percent = opponent.hp / opponent.max_hp

    # Track our HP percentage (for avoiding wasteful recovery)
    if battle.user.active is not None and battle.user.active.max_hp > 0:
        state.our_hp_percent = battle.user.active.hp / battle.user.active.max_hp
        user_volatiles = getattr(battle.user.active, "volatile_statuses", []) or []
        state.our_active_is_salt_cured = any(
            "saltcure" in normalize_name(v) for v in user_volatiles
        )

    # =========================================================================
    # PHASE 1.2: TRICK ROOM AWARENESS
    # =========================================================================

    # Trick Room detection
    trick_room = getattr(battle, "trick_room", False)
    if trick_room:
        state.trick_room_active = True
        state.trick_room_turns = getattr(battle, "trick_room_turns_remaining", 0)

    # Our speed detection (for slow Pokemon under TR)
    if battle.user.active is not None:
        our_pokemon = battle.user.active
        our_speed = getattr(our_pokemon, "speed", 0) or our_pokemon.stats.get(constants.SPEED, 0) if hasattr(our_pokemon, "stats") else 0
        if our_speed == 0:
            # Try base stats
            our_speed = getattr(our_pokemon, "base_speed", 50)
        state.our_speed_stat = our_speed
        # Consider "slow" if base speed < 60 (typical for TR abusers)
        state.our_is_slow = our_speed < 60

    # =========================================================================
    # PHASE 1.3: SCREENS AWARENESS
    # =========================================================================

    # Check opponent's side conditions for screens
    opp_side = getattr(battle.opponent, "side_conditions", {})
    if opp_side:
        state.opponent_has_reflect = opp_side.get(constants.REFLECT, 0) > 0
        state.opponent_has_light_screen = opp_side.get(constants.LIGHT_SCREEN, 0) > 0
        state.opponent_has_aurora_veil = opp_side.get(constants.AURORA_VEIL, 0) > 0

    # =========================================================================
    # PHASE 1.4: WEATHER/TERRAIN SYNERGIES
    # =========================================================================

    # Check if we have a weather-speed ability in matching weather
    if battle.user.active is not None:
        our_pokemon = battle.user.active
        our_ability = getattr(our_pokemon, "ability", None)
        our_name = our_pokemon.name

        # Swift Swim in Rain
        if state.weather in WEATHER_RAIN or state.weather == "raindance":
            if our_ability in SWIFT_SWIM_ABILITIES or our_name in POKEMON_WITH_SWIFT_SWIM:
                state.we_have_weather_speed = True

        # Sand Rush in Sand
        if state.weather in WEATHER_SAND or state.weather == "sandstorm":
            if our_ability in SAND_RUSH_ABILITIES or our_name in POKEMON_WITH_SAND_RUSH:
                state.we_have_weather_speed = True

        # Slush Rush in Snow/Hail
        if state.weather in WEATHER_SNOW or state.weather in ("snow", "hail"):
            if our_ability in SLUSH_RUSH_ABILITIES or our_name in POKEMON_WITH_SLUSH_RUSH:
                state.we_have_weather_speed = True

        # Chlorophyll in Sun
        if state.weather in WEATHER_SUN or state.weather == "sunnyday":
            if our_ability in CHLOROPHYLL_ABILITIES or our_name in POKEMON_WITH_CHLOROPHYLL:
                state.we_have_weather_speed = True

        # Check if we're grounded
        our_types = getattr(our_pokemon, "types", []) or []
        state.we_are_grounded = True
        if "flying" in our_types:
            state.we_are_grounded = False
        elif our_ability and our_ability in UNGROUNDED_ABILITIES:
            state.we_are_grounded = False
        elif hasattr(our_pokemon, "item") and our_pokemon.item in UNGROUNDED_ITEMS:
            state.we_are_grounded = False

    # Store terrain type for move boosts
    state.terrain_type = state.terrain

    # =========================================================================
    # PHASE 2.1: SWITCH EVALUATION
    # =========================================================================

    # Count hazard layers on our side
    our_side = getattr(battle.user, "side_conditions", {})
    if our_side:
        state.our_sr_up = our_side.get(constants.STEALTH_ROCK, 0) > 0
        state.our_spikes_layers = our_side.get(constants.SPIKES, 0)
        # Effective hazard layers (SR counts as ~2 layers worth of damage)
        state.our_hazard_layers = (2 if state.our_sr_up else 0) + state.our_spikes_layers

    # Check if opponent team has Defiant/Competitive (for Intimidate awareness)
    for opp_pokemon in battle.opponent.reserve + ([battle.opponent.active] if battle.opponent.active else []):
        if opp_pokemon is None or opp_pokemon.hp <= 0:
            continue
        opp_ability = getattr(opp_pokemon, "ability", None)
        opp_name = opp_pokemon.name
        if opp_ability in {"defiant", "competitive"}:
            state.opponent_has_defiant_pokemon = True
            break
        if opp_name in POKEMON_STAT_DROP_BACKFIRES:
            state.opponent_has_defiant_pokemon = True
            break

    # =========================================================================
    # PHASE 2.2: ENTRY HAZARD CALCULUS
    # =========================================================================

    # Check opponent's side conditions
    if opp_side:
        state.opponent_sr_up = opp_side.get(constants.STEALTH_ROCK, 0) > 0
        state.opponent_spikes_layers = opp_side.get(constants.SPIKES, 0)
        opp_toxic_spikes = int(opp_side.get(constants.TOXIC_SPIKES, 0) or 0)
        opp_sticky_web = int(opp_side.get(constants.STICKY_WEB, 0) or 0)
        state.opponent_hazard_layers = (
            (2 if state.opponent_sr_up else 0)
            + int(state.opponent_spikes_layers or 0)
            + (1 if opp_toxic_spikes > 0 else 0)
            + (1 if opp_sticky_web > 0 else 0)
        )

    # Count opponent's alive Pokemon
    alive_count = 0
    has_sr_weak = False
    has_hazard_removal = False
    hazard_removal_norm = {normalize_name(m) for m in HAZARD_REMOVAL_MOVES}

    for opp_pokemon in battle.opponent.reserve + ([battle.opponent.active] if battle.opponent.active else []):
        if opp_pokemon is None:
            continue
        if opp_pokemon.hp > 0:
            alive_count += 1

        # Check for SR-weak Pokemon (not wearing HDB)
        opp_name = opp_pokemon.name
        opp_item = getattr(opp_pokemon, "item", None)
        if opp_name in POKEMON_SR_4X_WEAK:
            if opp_item != "heavydutyboots" and opp_name not in POKEMON_COMMONLY_HDB:
                has_sr_weak = True

        # Check for hazard removal moves
        for move in getattr(opp_pokemon, "moves", []):
            move_name = normalize_name(move.name if hasattr(move, "name") else str(move))
            if move_name in hazard_removal_norm:
                has_hazard_removal = True

    state.opponent_alive_count = alive_count
    state.opponent_has_sr_weak = has_sr_weak
    state.opponent_has_hazard_removal = has_hazard_removal

    # Active removal pressure (important for hazard-preservation play).
    active_removal_is_spin = False
    if opponent is not None:
        for move in getattr(opponent, "moves", []) or []:
            move_name = normalize_name(move.name if hasattr(move, "name") else str(move))
            if move_name in hazard_removal_norm:
                state.opponent_active_has_hazard_removal = True
                if move_name in {"rapidspin", "mortalspin"}:
                    active_removal_is_spin = True
                break
    state.opponent_active_removal_is_spin = active_removal_is_spin

    # Ghost types can block Rapid Spin / Mortal Spin hazard removal.
    def _is_alive_ghost(pkmn) -> bool:
        if pkmn is None or getattr(pkmn, "hp", 0) <= 0:
            return False
        pkmn_types = [normalize_name(t) for t in (getattr(pkmn, "types", []) or [])]
        return "ghost" in pkmn_types

    our_team = ([battle.user.active] if battle.user.active is not None else []) + list(
        getattr(battle.user, "reserve", []) or []
    )
    state.our_has_alive_spinblocker = any(_is_alive_ghost(p) for p in our_team)
    state.our_active_is_spinblocker = _is_alive_ghost(battle.user.active)

    # =========================================================================
    # PHASE 2.3: TERA PREDICTION
    # =========================================================================

    # Check if opponent has Terastallized
    if opponent:
        opp_tera = getattr(opponent, "terastallized", False)
        state.opponent_has_terastallized = bool(opp_tera)
        if opp_tera:
            state.opponent_tera_type = getattr(opponent, "tera_type", "") or ""

    # Check if we have Terastallized
    if battle.user.active is not None:
        our_tera = getattr(battle.user.active, "terastallized", False)
        state.we_have_terastallized = bool(our_tera)
        state.our_tera_type = getattr(battle.user.active, "tera_type", "") or ""

    # Check whole battle for Tera usage
    if not state.opponent_has_terastallized:
        # Check if any opponent Pokemon shows terastallized
        for opp_pokemon in battle.opponent.reserve:
            if opp_pokemon and getattr(opp_pokemon, "terastallized", False):
                state.opponent_has_terastallized = True
                break

    # =========================================================================
    # PHASE 3.1: WIN CONDITION AWARENESS
    # =========================================================================

    # Check if our active Pokemon is a win condition
    if battle.user.active is not None:
        state.our_active_is_wincon = is_likely_wincon(battle.user.active, battle)

    # Check if opponent's active is a major threat (has setup boosts or high power)
    if opponent:
        opp_boosts = getattr(opponent, "boosts", {}) or {}
        atk_boost = opp_boosts.get(constants.ATTACK, 0)
        spa_boost = opp_boosts.get(constants.SPECIAL_ATTACK, 0)
        spe_boost = opp_boosts.get(constants.SPEED, 0)
        atk_stat = opponent.stats.get(constants.ATTACK, 0) if hasattr(opponent, "stats") else 0
        spa_stat = opponent.stats.get(constants.SPECIAL_ATTACK, 0) if hasattr(opponent, "stats") else 0
        spe_stat = opponent.stats.get(constants.SPEED, 0) if hasattr(opponent, "stats") else 0

        has_attack_boost = atk_boost >= 2 or spa_boost >= 2
        has_attack_boost |= (atk_boost >= 1 and atk_stat >= 100)
        has_attack_boost |= (spa_boost >= 1 and spa_stat >= 100)
        has_speed_threat = spe_boost >= 1 and spe_stat >= 90

        # Check if opponent's known moves can actually hit our active Pokemon.
        # If ALL revealed moves are type-immune, the opponent isn't a real threat
        # (e.g. Skarmory Body Press vs Ghost-type Pecharunt).
        our_types = []
        if battle.user.active is not None:
            our_types = getattr(battle.user.active, "types", []) or []
        all_moves_immune = False
        if our_types and hasattr(opponent, "moves") and opponent.moves:
            revealed_damaging = []
            for opp_move in opponent.moves:
                opp_move_name = opp_move.name if hasattr(opp_move, "name") else str(opp_move)
                opp_move_data = all_move_json.get(normalize_name(opp_move_name), {})
                opp_bp = float(opp_move_data.get(constants.BASE_POWER, 0) or 0)
                if opp_bp > 0 or normalize_name(opp_move_name) in {"seismictoss", "nightshade", "superfang", "naturesmadness", "finalgambit"}:
                    revealed_damaging.append(opp_move_data)
            if revealed_damaging:
                can_hit = False
                for md in revealed_damaging:
                    move_type = md.get(constants.TYPE, "typeless")
                    eff = type_effectiveness_modifier(move_type, our_types)
                    if eff > 0:
                        can_hit = True
                        break
                if not can_hit:
                    all_moves_immune = True

        if has_attack_boost or has_speed_threat:
            if not all_moves_immune:
                state.opponent_active_is_threat = True
            # else: opponent has boosts but can't hit us — not a real threat
        elif is_likely_wincon(opponent, battle):
            if not all_moves_immune:
                state.opponent_active_is_threat = True

    # =========================================================================
    # PHASE 3.2: PP TRACKING
    # =========================================================================

    # Estimate opponent recovery PP status
    recovery_moves = []
    for move in getattr(opponent, "moves", []):
        move_name = move.name if hasattr(move, "name") else str(move)
        if move_name in POKEMON_RECOVERY_MOVES:
            recovery_moves.append(move_name)

    if recovery_moves:
        exhausted = True
        for move_name in recovery_moves:
            min_pp, max_pp = opponent.estimate_pp_remaining(move_name)
            if max_pp is None:
                # Unknown PP data - be conservative
                exhausted = False
                break
            if max_pp > 0:
                exhausted = False
                break
        state.opponent_recovery_pp_exhausted = exhausted

    # Estimate if opponent's STAB PP is low
    stab_moves = []
    for move in getattr(opponent, "moves", []):
        move_name = move.name if hasattr(move, "name") else str(move)
        move_data = all_move_json.get(move_name)
        if move_data is None and move_name.startswith(constants.HIDDEN_POWER):
            move_data = all_move_json.get(constants.HIDDEN_POWER)
        if move_data is None:
            continue
        move_type = move_data.get(constants.TYPE)
        if move_type and move_type in opponent_types:
            stab_moves.append(move_name)

    if stab_moves:
        low_stab = True
        for move_name in stab_moves:
            _min_pp, max_pp = opponent.estimate_pp_remaining(move_name)
            if max_pp is None:
                low_stab = False
                break
            if max_pp > 2:
                low_stab = False
                break
        state.opponent_low_stab_pp = low_stab

    # =========================================================================
    # PHASE 3.3: MOMENTUM TRACKING
    # =========================================================================

    state.momentum, state.momentum_level = calculate_momentum(battle)

    # Count our alive Pokemon
    our_pokemon = [battle.user.active] + battle.user.reserve if battle.user.active else battle.user.reserve
    state.our_alive_count = sum(1 for p in our_pokemon if p and p.hp > 0)

    return state


# =============================================================================
# PENALTY APPLICATION
# =============================================================================


def apply_ko_line_bias(
    policy: dict[str, float],
    ability_state: OpponentAbilityState,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """If a KO line exists, strongly prefer damaging actions and the KO move."""
    if not ability_state.ko_line_available:
        return policy

    adjusted = {}
    ko_move = ability_state.ko_line_move
    turns = ability_state.ko_line_turns
    for move, weight in policy.items():
        move_name = move.split(":")[-1] if ":" in move else move
        move_data = all_move_json.get(move_name, {})
        cat = move_data.get(constants.CATEGORY)

        new_weight = weight
        if move_name == ko_move:
            new_weight *= 1.2
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "boost",
                        "source": "ko_line",
                        "move": move,
                        "reason": "ko_line_move",
                        "before": weight,
                        "after": new_weight,
                    }
                )
        elif turns == 1:
            if move_name in HAZARD_REMOVAL_MOVES or move_name in HAZARD_SETTING_MOVES:
                new_weight *= 0.05
            elif move_name in RECOVERY_MOVES:
                new_weight *= 0.1
            elif cat == constants.STATUS:
                new_weight *= 0.3
        elif turns == 2:
            if move_name in HAZARD_REMOVAL_MOVES or move_name in HAZARD_SETTING_MOVES:
                new_weight *= 0.2
            elif move_name in RECOVERY_MOVES:
                new_weight *= 0.4

        adjusted[move] = new_weight
    return adjusted


def apply_heuristic_bias(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """Inject a small heuristic score into the MCTS policy."""
    if battle is None or ability_state is None:
        return policy

    adjusted = {}
    for move, weight in policy.items():
        move_name = move.split(":")[-1] if ":" in move else move
        move_data = all_move_json.get(move_name, {})
        cat = move_data.get(constants.CATEGORY)
        heuristic = 0.0

        # Finish low HP targets
        if cat in {constants.PHYSICAL, constants.SPECIAL} and ability_state.opponent_hp_percent <= 0.35:
            heuristic += 0.2

        # Recover when truly low (but not if boosts are wasted vs Unaware)
        if move_name in RECOVERY_MOVES and ability_state.our_hp_percent <= 0.4:
            if not (ability_state.has_unaware and (
                ability_state.our_attack_boost > 0 or ability_state.our_spa_boost > 0)):
                heuristic += 0.2

        # Hazard tempo when opponent has many Pokemon
        if move_name in HAZARD_SETTING_MOVES and ability_state.opponent_alive_count >= 4:
            heuristic += 0.1

        # Pivot when momentum is negative
        if move_name in PIVOT_MOVES_NORM and ability_state.momentum_level in {
            "negative",
            "strong_negative",
        }:
            heuristic += 0.1

        multiplier = 1.0 + (heuristic * 0.05)
        new_weight = weight * multiplier
        adjusted[move] = new_weight

        if trace_events is not None and abs(multiplier - 1.0) > 1e-6:
            trace_events.append(
                {
                    "type": "heuristic",
                    "source": "heuristic_bias",
                    "move": move,
                    "reason": "heuristic_adjustment",
                    "before": weight,
                    "after": new_weight,
                    "score": round(heuristic, 3),
                }
            )

    return adjusted


def get_opponent_status_threats(opponent_pokemon, our_pokemon=None, movepool_data=None):
    """
    Returns set of status types the opponent can inflict ON OUR POKEMON.
    Checks revealed moves + potential moves from movepool.
    
    GENERAL approach: checks ALL status-inflicting moves, not just specific ones.
    
    TYPE IMMUNITY CHECKS:
    - Ground immune to Electric moves (Thunder Wave can't hit)
    - Grass immune to powder moves (Spore, Sleep Powder, Stun Spore)
    - Electric can't be paralyzed
    - Fire can't be burned
    - Ice can't be frozen
    - Poison/Steel can't be poisoned
    
    Args:
        opponent_pokemon: Opponent's active Pokemon
        our_pokemon: Our active Pokemon (for type immunity checks)
        movepool_data: Movepool data for checking potential moves
    
    Returns: set of status codes like {'brn', 'par', 'slp', 'frz', 'psn', 'tox'}
    """
    if not opponent_pokemon:
        return set()
    
    # Get our types for immunity checks
    our_types = []
    if our_pokemon:
        our_types = [t.lower() for t in (getattr(our_pokemon, "types", []) or [])]
    
    threats = set()
    
    def can_status_affect_us(move_data, status_code):
        """Check if a status move can actually affect our Pokemon."""
        if not our_pokemon or not our_types:
            # Can't verify immunity, assume vulnerable
            return True
        
        # TYPE IMMUNITY: Move type effectiveness check
        # Ground immune to Electric (Thunder Wave), etc.
        move_type = move_data.get(constants.TYPE, "").lower()
        if move_type:
            from fp.helpers import type_effectiveness_modifier
            if type_effectiveness_modifier(move_type, our_types) == 0:
                # Move can't hit us at all (type immunity)
                return False
        
        # POWDER IMMUNITY: Grass types immune to powder moves
        if "grass" in our_types:
            flags = move_data.get("flags", {})
            if flags.get("powder"):
                return False
        
        # STATUS-TYPE IMMUNITY: Certain types can't receive certain statuses
        # Electric types can't be paralyzed
        if status_code == "par" and "electric" in our_types:
            return False
        
        # Fire types can't be burned
        if status_code == "brn" and "fire" in our_types:
            return False
        
        # Ice types can't be frozen
        if status_code == "frz" and "ice" in our_types:
            return False
        
        # Poison and Steel types can't be poisoned
        if status_code in {"psn", "tox"} and ("poison" in our_types or "steel" in our_types):
            return False
        
        return True
    
    # Check revealed moves
    revealed_moves = getattr(opponent_pokemon, "moves", []) or []
    for mv in revealed_moves:
        move_name = normalize_name(mv.name if hasattr(mv, "name") else str(mv))
        move_data = all_move_json.get(move_name, {})
        
        # Direct status effect (e.g., Thunder Wave -> par, Will-O-Wisp -> brn)
        if STATUS in move_data and move_data[STATUS] in NON_VOLATILE_STATUSES:
            status_code = move_data[STATUS]
            if can_status_affect_us(move_data, status_code):
                threats.add(status_code)
        
        # Secondary status effect (e.g., Scald 30% burn, Nuzzle 100% par)
        secondary = move_data.get("secondary", {})
        if isinstance(secondary, dict) and "status" in secondary:
            status_code = secondary["status"]
            if status_code in NON_VOLATILE_STATUSES:
                if can_status_affect_us(move_data, status_code):
                    threats.add(status_code)
    
    # Check potential moves from movepool (what opponent COULD have)
    if movepool_data:
        opponent_name = normalize_name(opponent_pokemon.name)
        opp_data = movepool_data.get(opponent_name, {})
        
        # Check all move categories (status, physical, special)
        # Physical/special moves can have secondary status (Scald, Nuzzle, etc.)
        all_potential_moves = (
            opp_data.get('status_moves', []) +
            opp_data.get('physical_moves', []) +
            opp_data.get('special_moves', [])
        )
        
        for move_name in all_potential_moves:
            move_norm = normalize_name(move_name)
            move_data = all_move_json.get(move_norm, {})
            
            # Check for status effects
            if STATUS in move_data and move_data[STATUS] in NON_VOLATILE_STATUSES:
                status_code = move_data[STATUS]
                if can_status_affect_us(move_data, status_code):
                    threats.add(status_code)
            
            secondary = move_data.get("secondary", {})
            if isinstance(secondary, dict) and "status" in secondary:
                status_code = secondary["status"]
                if status_code in NON_VOLATILE_STATUSES:
                    if can_status_affect_us(move_data, status_code):
                        threats.add(status_code)
    
    return threats


def is_status_threatening_for_ability(status_codes, our_pokemon):
    """
    Returns (is_threatened, ability_name, reason) tuple.
    
    GENERAL check for ALL ability-status interactions (not just Poison Heal vs WoW).
    
    Checks if any of the given status codes would be BAD for our Pokemon's ability.
    
    Args:
        status_codes: set of status codes like {'brn', 'par', 'slp'}
        our_pokemon: our active Pokemon object
    
    Returns:
        (bool, str, str): (is_threatened, ability_name, reason)
    """
    if not status_codes:
        return (False, None, None)
    
    ability_norm = normalize_name(getattr(our_pokemon, 'ability', '') or '')
    our_name_norm = normalize_name(our_pokemon.name)
    our_base_norm = normalize_name(getattr(our_pokemon, 'base_name', '') or our_pokemon.name)
    
    # =========================================================================
    # POISON HEAL: Wants PSN/TOX, threatened by BRN/PAR/SLP/FRZ
    # =========================================================================
    is_poison_heal = (
        ability_norm == 'poisonheal'
        or our_name_norm in POKEMON_COMMONLY_POISON_HEAL
        or our_base_norm in POKEMON_COMMONLY_POISON_HEAL
    )
    
    if is_poison_heal:
        # Any non-poison status ruins Poison Heal (no healing)
        harmful_statuses = status_codes - {'psn', 'tox'}
        if harmful_statuses:
            status_str = ', '.join(sorted(harmful_statuses))
            return (True, 'Poison Heal', status_str)
    
    # =========================================================================
    # TOXIC BOOST: Wants PSN/TOX, threatened by other status
    # =========================================================================
    is_toxic_boost = ability_norm == 'toxicboost'
    
    if is_toxic_boost:
        harmful_statuses = status_codes - {'psn', 'tox'}
        if harmful_statuses:
            status_str = ', '.join(sorted(harmful_statuses))
            return (True, 'Toxic Boost', status_str)
    
    # =========================================================================
    # GUTS: Wants status (boosts Attack), but BRN halves Attack (bad!)
    # Also SLP/FRZ prevent action (very bad!)
    # =========================================================================
    is_guts = ability_norm == 'guts'
    
    if is_guts:
        # Burn negates Guts boost, Sleep/Freeze prevent action
        harmful = status_codes & {'brn', 'slp', 'frz'}
        if harmful:
            status_str = ', '.join(sorted(harmful))
            return (True, 'Guts', status_str)
    
    # =========================================================================
    # MARVEL SCALE: Wants status (Def boost), but SLP/FRZ prevent action
    # =========================================================================
    is_marvel_scale = ability_norm == 'marvelscale'
    
    if is_marvel_scale:
        harmful = status_codes & {'slp', 'frz'}
        if harmful:
            status_str = ', '.join(sorted(harmful))
            return (True, 'Marvel Scale', status_str)
    
    # =========================================================================
    # QUICK FEET: Wants status (Speed boost), but:
    #   - PAR reduces speed (bad!)
    #   - SLP/FRZ prevent action
    # =========================================================================
    is_quick_feet = ability_norm == 'quickfeet'
    
    if is_quick_feet:
        # Paralysis negates speed boost, Sleep/Freeze prevent action
        harmful = status_codes & {'par', 'slp', 'frz'}
        if harmful:
            status_str = ', '.join(sorted(harmful))
            return (True, 'Quick Feet', status_str)
    
    # =========================================================================
    # Future extensibility: add more ability checks here
    # - Synchronize: reflects status back (might want different logic)
    # - Magic Guard: immune to status damage (not threatened)
    # - Natural Cure: shrugs off status on switch (less threatened)
    # =========================================================================
    
    return (False, None, None)


def apply_ability_penalties(
    final_policy: dict[str, float],
    ability_state: OpponentAbilityState,
    trace_events: list[dict] | None = None,
    battle: Battle | None = None,
) -> dict[str, float]:
    """
    Apply penalties to moves based on opponent's abilities.

    This function handles all ability-based move penalties in one pass.
    """
    # Quick exit if no problematic abilities/states detected
    if not any(
        [
            # Existing checks
            ability_state.has_unaware,
            ability_state.has_guts_like,
            ability_state.has_poison_heal,
            ability_state.has_purifying_salt,
            ability_state.has_water_immunity,
            ability_state.has_electric_immunity,
            ability_state.has_flash_fire,
            ability_state.has_levitate,
            ability_state.has_magic_bounce,
            ability_state.has_competitive_defiant,
            ability_state.has_focus_sash and ability_state.at_full_hp,
            ability_state.has_phazing,
            ability_state.has_substitute,
            ability_state.has_contact_punish,
            ability_state.has_status,
            ability_state.is_choice_locked,
            # NEW checks
            ability_state.has_contrary,
            ability_state.has_sap_sipper,
            ability_state.has_sturdy and ability_state.at_full_hp,
            ability_state.has_disguise and not ability_state.disguise_broken,
            ability_state.has_soundproof,
            ability_state.has_bulletproof,
            ability_state.has_overcoat,
            ability_state.is_grass_type,  # Powder immunity
            ability_state.has_earth_eater,
            ability_state.has_justified,
            ability_state.has_steam_engine,
            ability_state.has_wind_rider,
            ability_state.has_well_baked_body,
            ability_state.has_clear_body,
            ability_state.has_mirror_armor,
            ability_state.has_supreme_overlord,
            ability_state.our_has_prankster and ability_state.opponent_is_dark_type,
            ability_state.has_air_balloon,
            ability_state.has_fluffy,
            ability_state.has_dry_skin_fire_weak,
            # Weather/Terrain
            ability_state.weather in WEATHER_EXTREME_RAIN,
            ability_state.weather in WEATHER_EXTREME_SUN,
            ability_state.weather in WEATHER_RAIN,
            ability_state.weather in WEATHER_SUN,
            ability_state.terrain in TERRAIN_PSYCHIC,
              # PHASE 1.1: Positive Boosts
              ability_state.opponent_used_protect,
              ability_state.opponent_used_setup,
              ability_state.opponent_hp_percent < 0.25,
              ability_state.opponent_used_damaging_move and ability_state.our_hp_percent <= 0.5,
            # PHASE 1.2: Trick Room
            ability_state.trick_room_active,
            # PHASE 1.3: Screens
            ability_state.opponent_has_reflect,
            ability_state.opponent_has_light_screen,
            ability_state.opponent_has_aurora_veil,
            # PHASE 1.4: Weather/Terrain Synergies
            ability_state.we_have_weather_speed,
            ability_state.terrain_type != "",
            # PHASE 2.2: Entry Hazard Calculus
            ability_state.opponent_alive_count >= 4,  # SR valuable
            ability_state.opponent_has_sr_weak,
            ability_state.opponent_sr_up,  # Don't double-set
            ability_state.our_hazard_layers >= 2,  # Need removal
            ability_state.our_hazard_layers == 0,  # Avoid useless hazard removal
            ability_state.opponent_has_hazard_removal,
            # PHASE 2.3: Tera Prediction
            not ability_state.opponent_has_terastallized,
            # PHASE 3.1: Win Condition Awareness
            ability_state.our_active_is_wincon,
            ability_state.opponent_active_is_threat,
            # PHASE 3.2: PP Tracking
            ability_state.opponent_recovery_pp_exhausted,
            ability_state.opponent_low_stab_pp,
            # Healing waste avoidance
            ability_state.our_hp_percent >= 0.95,
            # PHASE 3.3: Momentum Tracking
            ability_state.momentum_level != "neutral",
            # Proactive sweep prevention (2026-02-08)
            ability_state.opponent_has_offensive_boost,
        ]
    ):
        return final_policy

    penalized_policy = {}
    penalties_applied = []
    boosts_applied = []

    recovery_moves_norm = {normalize_name(m) for m in RECOVERY_MOVES}

    # Compute opponent offensive boost level for setup-denial logic
    _opp = battle.opponent.active if battle is not None and battle.opponent else None
    _opp_boosts = getattr(_opp, "boosts", {}) or {} if _opp else {}
    opp_offensive_boost_level = max(
        int(_opp_boosts.get("atk", 0) or 0),
        int(_opp_boosts.get("spa", 0) or 0),
    )

    for move, weight in final_policy.items():
        # Extract the move name from the move choice format
        # Move choices can be "move:swordsdance" or just "swordsdance"
        move_name = move.split(":")[-1] if ":" in move else move
        move_norm = normalize_name(move_name)
        base_move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
        move_type_norm = normalize_name(base_move_data.get(constants.TYPE, ""))
        move_category = base_move_data.get(constants.CATEGORY)

        penalty = 1.0  # No penalty by default
        reason = None

        # =====================================================================
        # EXISTING PENALTIES
        # =====================================================================

        # Already has status: NEVER use pure status moves (they will fail)
        if ability_state.has_status and move_name in PURE_STATUS_MOVES:
            penalty = min(penalty, 0.01)  # Near-zero: move literally fails
            reason = "Opponent already has a status condition (move will fail)"

        # Unaware: penalize offensive stat boosting moves
        if ability_state.has_unaware and move_name in OFFENSIVE_STAT_BOOST_MOVES:
            penalty = min(penalty, UNAWARE_BOOST_PENALTY)
            reason = "Unaware (stat boosts ignored)"

        # Guts/Marvel Scale/Quick Feet: penalize status moves
        if ability_state.has_guts_like and move_name in STATUS_INFLICTING_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Guts/Marvel Scale (status boosts them)"

        # Poison Heal: penalize Toxic/poison moves specifically
        if ability_state.has_poison_heal and move_name in TOXIC_POISON_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Poison Heal (poison heals them)"

        # Purifying Salt: status immunity + ghost damage reduction.
        if ability_state.has_purifying_salt and move_norm in {
            normalize_name(m) for m in STATUS_INFLICTING_MOVES
        }:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Purifying Salt (status blocked)"
        if (
            ability_state.has_purifying_salt
            and move_category in {constants.PHYSICAL, constants.SPECIAL}
            and move_type_norm == "ghost"
        ):
            penalty = min(penalty, 0.60)
            reason = "Purifying Salt (Ghost damage halved)"

        # Water immunity: penalize water moves
        if ability_state.has_water_immunity and move_name in WATER_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Water Absorb/Storm Drain (water heals/boosts)"

        # Electric immunity: penalize electric moves
        if ability_state.has_electric_immunity and move_name in ELECTRIC_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Volt Absorb/Lightning Rod (electric heals/boosts)"

        # Flash Fire: penalize fire moves (unless we have Mold Breaker)
        if ability_state.has_flash_fire and move_name in FIRE_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Flash Fire (fire boosts their fire moves)"

        # Levitate: penalize ground moves
        if ability_state.has_levitate and move_name in DAMAGING_GROUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Levitate (ground immunity)"

        # Magic Bounce: penalize status moves that get reflected
        if ability_state.has_magic_bounce and move_name in MAGIC_BOUNCE_REFLECTED_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Magic Bounce (moves reflect back)"

        # Good as Gold: NEVER use status moves (they're completely blocked)
        if ability_state.has_good_as_gold and move_name in ALL_STATUS_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Good as Gold (status moves blocked)"

        # Competitive/Defiant: penalize stat-lowering moves
        if (
            ability_state.has_competitive_defiant
            and move_name in STAT_LOWERING_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Competitive/Defiant (stat drops boost them)"

        # Focus Sash at full HP: boost multi-hit moves
        if ability_state.has_focus_sash and ability_state.at_full_hp:
            if move_name in MULTI_HIT_MOVES:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Focus Sash (multi-hit breaks it)"

        # Setup vs Phazers: penalize setup moves if opponent has phazing
        if ability_state.has_phazing and move_name in SETUP_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Phazer detected (setup boosts will be wasted)"

        # Redundant offensive setup: penalize boosting when already heavily boosted
        # Replay: gen9ou-2532366515 turn 55 (Gliscor used Swords Dance at +4 vs low-HP Kingambit)
        if move_name in OFFENSIVE_STAT_BOOST_MOVES:
            max_off_boost = max(ability_state.our_attack_boost, ability_state.our_spa_boost)
            if max_off_boost >= 6:
                penalty = min(penalty, ABILITY_PENALTY_SEVERE)
                reason = "Offensive boost already maxed (+6)"
            elif max_off_boost >= 4:
                penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
                reason = "Offensive boost already high (+4)"

        # Low HP vs recent damage: avoid setup/hazards when a KO line is likely.
        # Replay: gen9ou-2532408383 turn 25 (Blissey used Stealth Rock into Fiery Dance KO)
        # Replay: gen9ou-2532408309-1jnoidrrww9a72mn55er7s1nk8u4q2ipw turn 26
        # (Blissey used Calm Mind into Garganacl Earthquake KO)
        if (
            ability_state.opponent_used_damaging_move
            and ability_state.our_hp_percent <= 0.5
            and (move_name in SETUP_MOVES or move_name in HAZARD_SETTING_MOVES)
        ):
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Low HP vs recent damage (avoid setup/hazards)"

        # Substitute: penalize status-only moves that fail against sub
        if ability_state.has_substitute and move_name in STATUS_ONLY_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Substitute up (status moves fail)"

        # Choice-lock exploitation: boost setup moves when opponent is locked
        if ability_state.is_choice_locked and move_name in SETUP_MOVES:
            if not ability_state.has_phazing:
                if penalty >= 1.0:
                    penalty = ABILITY_BOOST_LIGHT
                    reason = f"Choice-locked into {ability_state.choice_locked_move} (free setup opportunity)"

        # Contact punishment: penalize contact moves
        # Harsher when our HP is low enough that recoil would KO us
        if ability_state.has_contact_punish and move_name in CONTACT_MOVES:
            our_hp = getattr(ability_state, "our_hp_percent", 1.0)
            if our_hp <= 0.17:
                # At ≤17% HP, Rocky Helmet recoil (1/6 ≈ 16.7%) would KO us
                penalty = min(penalty, ABILITY_PENALTY_SEVERE)
                reason = "Contact move at critical HP (recoil KO)"
            else:
                penalty = min(penalty, ABILITY_PENALTY_LIGHT)
                reason = "Contact punishment (Iron Barbs/Rough Skin/Rocky Helmet)"

        # =====================================================================
        # NEW PHASE 2: HIGH PRIORITY ABILITIES
        # =====================================================================

        # Contrary: OUR stat-lowering moves become THEIR boosts!
        # This is different from Competitive/Defiant - it's even worse
        if ability_state.has_contrary and move_name in STAT_LOWERING_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Contrary (our stat drops become their boosts!)"

        # Sap Sipper: Grass moves immune + boost their Attack
        if ability_state.has_sap_sipper and move_name in GRASS_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Sap Sipper (Grass immune, boosts their Atk)"

        # Sturdy at full HP: boost multi-hit moves (like Focus Sash)
        if ability_state.has_sturdy and ability_state.at_full_hp:
            if move_name in MULTI_HIT_MOVES:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Sturdy (multi-hit breaks it)"

        # Disguise not broken: boost multi-hit moves
        if ability_state.has_disguise and not ability_state.disguise_broken:
            if move_name in MULTI_HIT_MOVES:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Disguise (multi-hit breaks + damages)"

        # =====================================================================
        # NEW PHASE 3: MOVE CATEGORY IMMUNITIES
        # =====================================================================

        # Soundproof: Sound-based moves are immune
        if ability_state.has_soundproof and move_name in SOUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Soundproof (sound moves immune)"

        # Bulletproof: Ball/Bullet moves are immune
        if ability_state.has_bulletproof and move_name in BULLET_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Bulletproof (bullet moves immune)"

        # Overcoat OR Grass type: Powder moves are immune
        if (ability_state.has_overcoat or ability_state.is_grass_type) and move_name in POWDER_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            if ability_state.is_grass_type:
                reason = "Grass type (powder moves immune)"
            else:
                reason = "Overcoat (powder moves immune)"

        # =====================================================================
        # NEW PHASE 4-5: TYPE/STAT INTERACTIONS
        # =====================================================================

        # Earth Eater: Ground moves heal them
        if ability_state.has_earth_eater and move_name in DAMAGING_GROUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Earth Eater (Ground heals them)"

        # Justified: Dark moves give +1 Atk
        if ability_state.has_justified and move_name in DARK_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Justified (Dark gives +1 Atk)"

        # Steam Engine: Fire/Water moves give +6 Speed!
        if ability_state.has_steam_engine:
            if move_name in FIRE_TYPE_MOVES or move_name in WATER_TYPE_MOVES:
                penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
                reason = "Steam Engine (Fire/Water gives +6 Spe!)"

        # Wind Rider: Wind moves give +1 Atk instead of damage
        if ability_state.has_wind_rider and move_name in WIND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Wind Rider (wind moves boost their Atk)"

        # Well-Baked Body: Fire moves give +2 Def instead of damage
        if ability_state.has_well_baked_body and move_name in FIRE_TYPE_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Well-Baked Body (Fire immune, boosts their Def)"

        # =====================================================================
        # NEW PHASE 6: STAT DROP IMMUNITY/REFLECTION
        # =====================================================================

        # Clear Body / White Smoke / Full Metal Body: stat drops don't work
        if ability_state.has_clear_body and move_name in STAT_LOWERING_MOVES:
            # Only penalize moves that ONLY lower stats (like Charm, Scary Face)
            # Damaging moves with secondary stat drops are still useful
            if move_name in STATUS_ONLY_MOVES or move_name in {
                "tickle", "charm", "featherdance", "faketears", "metalsound",
                "scaryface", "stringshot", "tearfullook", "captivate", "confide",
                "nobleroar", "partingshot", "memento"
            }:
                penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
                reason = "Clear Body (stat drops blocked)"

        # Mirror Armor: stat drops reflect back to us!
        if ability_state.has_mirror_armor and move_name in STAT_LOWERING_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_MEDIUM)
            reason = "Mirror Armor (stat drops reflect back!)"

        # Supreme Overlord: Be careful of Sucker Punch? 
        # For now, we mainly want to avoid giving it free turns if it's the last mon.
        if ability_state.has_supreme_overlord:
             # Just a marker for now, hard to penalize specific moves without knowing move pool
             # But if we use status moves, we risk attacks.
             pass

        # =====================================================================
        # NEW PHASE 7: SPECIAL INTERACTIONS
        # =====================================================================

        # Prankster vs Dark type: status moves fail completely
        if (
            ability_state.our_has_prankster
            and ability_state.opponent_is_dark_type
            and move_name in ALL_STATUS_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Dark type immune to Prankster status moves"

        # Air Balloon: Ground moves fail (until balloon pops)
        if ability_state.has_air_balloon and move_name in DAMAGING_GROUND_MOVES:
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Air Balloon (Ground immune until popped)"

        # Fluffy: Fire moves deal 2x damage - OPPORTUNITY!
        if ability_state.has_fluffy and move_name in FIRE_TYPE_MOVES:
            # Don't boost if they also have Flash Fire (shouldn't happen but safety)
            if not ability_state.has_flash_fire:
                penalty = max(penalty, ABILITY_BOOST_MEDIUM)
                reason = "Fluffy (Fire deals 2x - opportunity!)"

        # Dry Skin: Fire moves deal 1.25x damage - opportunity
        if ability_state.has_dry_skin_fire_weak and move_name in FIRE_TYPE_MOVES:
            # Don't boost if they also have Flash Fire
            if not ability_state.has_flash_fire:
                penalty = max(penalty, ABILITY_BOOST_LIGHT)
                reason = "Dry Skin (Fire deals 1.25x - opportunity)"

        # =====================================================================
        # NEW PHASE 8: WEATHER/TERRAIN
        # =====================================================================

        # Primordial Sea: Fire moves fail completely
        if ability_state.weather in WEATHER_EXTREME_RAIN and move_name in FIRE_TYPE_MOVES:
            penalty = min(penalty, 0.01)  # Near-zero: move fails
            reason = "Primordial Sea (Fire moves fail)"

        # Desolate Land: Water moves fail completely
        if ability_state.weather in WEATHER_EXTREME_SUN and move_name in WATER_TYPE_MOVES:
            penalty = min(penalty, 0.01)  # Near-zero: move fails
            reason = "Desolate Land (Water moves fail)"

        # Rain Dance: Fire -50%, Water already handled by MCTS but slight boost
        if ability_state.weather in WEATHER_RAIN and ability_state.weather not in WEATHER_EXTREME_RAIN:
            if move_name in FIRE_TYPE_MOVES:
                penalty = min(penalty, ABILITY_PENALTY_LIGHT)
                reason = "Rain (Fire -50%)"

        # Sunny Day: Water -50%, Fire already handled by MCTS but slight boost
        if ability_state.weather in WEATHER_SUN and ability_state.weather not in WEATHER_EXTREME_SUN:
            if move_name in WATER_TYPE_MOVES:
                penalty = min(penalty, ABILITY_PENALTY_LIGHT)
                reason = "Sun (Water -50%)"

        # Psychic Terrain: Priority moves blocked on grounded opponents
        if (
            ability_state.terrain in TERRAIN_PSYCHIC
            and ability_state.opponent_is_grounded
            and move_name in PRIORITY_MOVES
        ):
            penalty = min(penalty, ABILITY_PENALTY_SEVERE)
            reason = "Psychic Terrain (priority blocked on grounded)"

        # =====================================================================
        # PHASE 1.1: POSITIVE BOOSTS EXPANSION
        # =====================================================================

        # Boost setup when opponent used Protect last turn (free turn)
        if ability_state.opponent_used_protect and move_name in SETUP_MOVES:
            if penalty >= 1.0:
                penalty = BOOST_PROTECT_PUNISH
                reason = "Opponent used Protect (free setup turn)"

        # =================================================================
        # PROACTIVE SWEEP PREVENTION (2026-02-08)
        # Smart sweep prevention: consider counterplay options
        # =================================================================
        penalty, reason = smart_sweep_prevention(
            penalty=penalty,
            reason=reason,
            move=move,
            move_name=move_name,
            ability_state=ability_state,
            battle=battle,
            PENALTY_PASSIVE_VS_BOOSTED=PENALTY_PASSIVE_VS_BOOSTED,
            BOOST_SWITCH_VS_BOOSTED=BOOST_SWITCH_VS_BOOSTED,
            BOOST_PHAZE_VS_BOOSTED=BOOST_PHAZE_VS_BOOSTED,
            BOOST_REVENGE_VS_BOOSTED=BOOST_REVENGE_VS_BOOSTED,
            SETUP_MOVES=SETUP_MOVES,
            STATUS_ONLY_MOVES=STATUS_ONLY_MOVES,
            PHAZING_MOVES=PHAZING_MOVES,
            PRIORITY_MOVES=PRIORITY_MOVES,
        )

        # Anti-setup punishment: boost aggressive replies after opponent sets up
        if ability_state.opponent_used_setup:
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            base_power = move_data.get(constants.BASE_POWER, 0)
            if move_name in AGGRESSIVE_MOVES or move_name in PRIORITY_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_PRESSURE_MOMENTUM)
                    reason = "Punish setup (aggressive response)"
            elif move_category in {constants.PHYSICAL, constants.SPECIAL} and base_power >= 80:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_PRESSURE_MOMENTUM)
                    reason = "Punish setup (hard hit)"

        # Boost setup when opponent is already statused (they're weakened)
        if ability_state.has_status and move_name in SETUP_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_OPPONENT_STATUSED)
                reason = "Opponent is statused (weakened, good setup opportunity)"

        # Boost priority moves when opponent is at low HP (<25%)
        if ability_state.opponent_hp_percent < 0.25 and move_name in PRIORITY_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_LOW_HP_PRIORITY)
                reason = f"Opponent at {int(ability_state.opponent_hp_percent * 100)}% HP (priority to finish)"

        # Enhanced choice-lock exploitation (check type matchup)
        # This extends the existing choice-lock detection with resist/immune checks
        if ability_state.is_choice_locked and move_name in SETUP_MOVES:
            if not ability_state.has_phazing:
                # Note: Full type immunity/resistance check would require battle context
                # For now, we apply the base boost; future: check actual matchup
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_CHOICE_LOCKED_RESIST)
                    reason = f"Choice-locked into {ability_state.choice_locked_move} (setup opportunity)"

        # =====================================================================
        # PHASE 1.2: TRICK ROOM AWARENESS
        # =====================================================================

        if ability_state.trick_room_active:
            # Penalize speed-boosting moves under Trick Room (they hurt us)
            if move_name in SPEED_BOOSTING_MOVES:
                penalty = min(penalty, PENALTY_SPEED_BOOST_IN_TR)
                reason = "Trick Room active (speed boosts are bad)"

            # Boost setup moves for slow Pokemon under Trick Room
            if ability_state.our_is_slow and move_name in SETUP_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SETUP_SLOW_IN_TR)
                    reason = "Slow Pokemon under Trick Room (great setup opportunity)"

        # =====================================================================
        # PHASE 1.3: SCREENS AWARENESS
        # =====================================================================

        has_any_screen = (
            ability_state.opponent_has_reflect
            or ability_state.opponent_has_light_screen
            or ability_state.opponent_has_aurora_veil
        )

        if has_any_screen:
            # Boost setup moves when screens reduce damage to us
            if move_name in SETUP_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SETUP_VS_SCREENS)
                    reason = "Screens active (safe to set up)"

            # Boost screen-breaking moves
            if move_name in SCREEN_BREAKING_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SCREEN_BREAKER)
                    reason = "Screens active (screen breaker valuable)"

        # =====================================================================
        # PHASE 1.4: WEATHER/TERRAIN SYNERGIES
        # =====================================================================

        # Boost attacks when we have weather-based speed advantage
        if ability_state.we_have_weather_speed:
            # Get move data to check if it's a damaging move
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            if move_category in {constants.PHYSICAL, constants.SPECIAL}:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_WEATHER_SPEED_ADVANTAGE)
                    reason = "Weather speed advantage (we outspeed)"

        # Terrain-boosted moves (for grounded Pokemon)
        if ability_state.we_are_grounded and ability_state.terrain_type:
            move_data = all_move_json.get(move_name, {})
            move_type = move_data.get(constants.TYPE, "")

            # Electric Terrain boosts Electric moves
            if ability_state.terrain_type == "electricterrain" and move_type == "electric":
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_TERRAIN_MOVE)
                    reason = "Electric Terrain (Electric moves boosted)"

            # Grassy Terrain boosts Grass moves
            if ability_state.terrain_type == "grassyterrain" and move_type == "grass":
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_TERRAIN_MOVE)
                    reason = "Grassy Terrain (Grass moves boosted)"

            # Psychic Terrain boosts Psychic moves
            if ability_state.terrain_type == "psychicterrain" and move_type == "psychic":
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_TERRAIN_MOVE)
                    reason = "Psychic Terrain (Psychic moves boosted)"

        # Grassy Terrain weakens Earthquake/Bulldoze (opponent benefits)
        if ability_state.terrain_type == "grassyterrain" and ability_state.opponent_is_grounded:
            if move_name in GRASSY_TERRAIN_WEAKENED:
                penalty = min(penalty, ABILITY_PENALTY_LIGHT)
                reason = "Grassy Terrain (Ground moves weakened)"

        # =====================================================================
        # PHASE 2.2: ENTRY HAZARD CALCULUS
        # =====================================================================

        # Boost Stealth Rock when not up and opponent has multiple Pokemon
        if move_name == "stealthrock":
            if not ability_state.opponent_sr_up:
                if ability_state.opponent_alive_count >= 4:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_SET_HAZARDS_NO_HAZARDS)
                        reason = f"No SR up, {ability_state.opponent_alive_count} opponent Pokemon alive"
                # Extra boost if opponent has SR-weak Pokemon
                if ability_state.opponent_has_sr_weak:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_SET_HAZARDS_SR_WEAK_OPP)
                        reason = "Opponent has SR-weak Pokemon"
            else:
                # Heavily penalize setting SR when already up
                penalty = min(penalty, PENALTY_SET_HAZARDS_ALREADY_UP)
                reason = "Stealth Rock already up"

        # Boost/penalize Spikes
        if move_name == "spikes":
            if ability_state.opponent_spikes_layers < 3:
                if ability_state.opponent_alive_count >= 3:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_SET_HAZARDS_NO_HAZARDS * 0.9)  # Slightly less than SR
                        reason = f"Spikes valuable ({ability_state.opponent_spikes_layers}/3 layers)"
            else:
                penalty = min(penalty, PENALTY_SET_HAZARDS_ALREADY_UP)
                reason = "Max Spikes layers already up"

        # Penalize hazard moves if opponent has revealed hazard removal
        if move_name in HAZARD_SETTING_MOVES and ability_state.opponent_has_hazard_removal:
            if penalty >= 0.5:  # Don't double-penalize
                dynamic_penalty = PENALTY_HAZARDS_VS_REMOVAL
                # If we currently have no hazards up, we still want to establish
                # baseline chip before over-respecting future removal.
                if getattr(ability_state, "opponent_hazard_layers", 0) <= 0:
                    dynamic_penalty = max(dynamic_penalty, 0.88)
                    reason = "Opponent has hazard removal (set baseline hazards first)"
                # If the active foe can remove hazards now, lean toward pressure.
                elif getattr(ability_state, "opponent_active_has_hazard_removal", False):
                    dynamic_penalty = min(dynamic_penalty, 0.60)
                    reason = "Active hazard remover present (pressure before restacking)"
                else:
                    reason = "Opponent has hazard removal"
                penalty = min(penalty, dynamic_penalty)

        # Boost Defog/Rapid Spin when we have heavy hazards
        if move_name in HAZARD_REMOVAL_MOVES:
            if ability_state.our_hazard_layers == 0:
                penalty = min(penalty, PENALTY_REMOVE_HAZARDS_NONE)
                reason = "No hazards to remove"
            elif ability_state.our_hazard_layers >= 2:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_REMOVE_HAZARDS_HEAVY)
                    reason = f"Heavy hazards on our side ({ability_state.our_hazard_layers} layers)"

        # === SETUP DENIAL: Don't stack hazards vs active boosted sweeper ===
        # If opponent has >= 2 offensive boosts, non-damaging moves (hazards, status, setup)
        # give them free turns to keep boosting. Heavily penalize.
        if (
            opp_offensive_boost_level >= 2
            and move_category not in {"Physical", "Special"}
        ):
            penalty = min(penalty, PENALTY_HAZARD_VS_ACTIVE_SWEEPER)
            reason = "opp_boosted_dont_stack_hazards"

        # =====================================================================
        # PHASE 2.3: TERA PREDICTION
        # =====================================================================

        # If opponent hasn't Terastallized yet, boost coverage moves
        # (they might Tera to change type matchup)
        if not ability_state.opponent_has_terastallized:
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            move_type = move_data.get(constants.TYPE, "")

            # Boost coverage moves (non-STAB attacking moves)
            # This is a heuristic - if we have coverage, it might hit their Tera type
            if move_category in {constants.PHYSICAL, constants.SPECIAL}:
                # Check if it's likely a coverage move (not same type as our Pokemon)
                # We can't easily check our types here, so we boost neutral coverage slightly
                if move_type in OFFENSIVE_TERA_TYPES:
                    # Small boost to coverage that hits common Tera types
                    if penalty >= 1.0 and penalty < 1.2:
                        penalty = max(penalty, BOOST_COVERAGE_VS_LIKELY_TERA)
                        reason = "Coverage vs potential Tera"

        # =====================================================================
        # PHASE 3.1: WIN CONDITION AWARENESS
        # =====================================================================

        # If our active is a win condition, be more conservative
        if ability_state.our_active_is_wincon:
            # Penalize risky moves (self-destruct, etc.)
            if move_name in RISKY_MOVES:
                penalty = min(penalty, PENALTY_RISKY_WITH_WINCON)
                reason = "Win condition (avoid risky plays)"

            # Boost safe moves (pivots, protect)
            if move_name in SAFE_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_SAFE_WITH_WINCON)
                    reason = "Win condition (prefer safe plays)"

        # If opponent's active is a boosted threat, boost revenge killing
        if ability_state.opponent_active_is_threat:
            move_data = all_move_json.get(move_name, {})
            move_category = move_data.get(constants.CATEGORY, "")
            # Boost priority moves and strong attacks
            if move_name in PRIORITY_MOVES or move_category in {constants.PHYSICAL, constants.SPECIAL}:
                base_power = move_data.get(constants.BASE_POWER, 0)
                if move_name in PRIORITY_MOVES or base_power >= 80:
                    if penalty >= 1.0:
                        penalty = max(penalty, BOOST_REVENGE_KILL_THREAT)
                        reason = "Opponent threat active (revenge kill)"

        # =====================================================================
        # PHASE 3.2: PP TRACKING
        # =====================================================================

        # If opponent's recovery PP is exhausted, favor safe/stall plays
        if ability_state.opponent_recovery_pp_exhausted and move_name in SAFE_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_STALL_NO_RECOVERY_PP)
                reason = "Opponent recovery PP exhausted (stall)"

        # If opponent's STAB PP is low, favor defensive positioning
        if ability_state.opponent_low_stab_pp and move_name in SAFE_MOVES:
            if penalty >= 1.0:
                penalty = max(penalty, BOOST_DEFENSIVE_LOW_PP)
                reason = "Opponent STAB low PP (defensive)"

        # Avoid wasting recovery at full HP, especially into a boosted threat
        if move_name in recovery_moves_norm and ability_state.our_hp_percent >= 0.95:
            penalty = min(penalty, 0.25)
            reason = "Full HP (avoid wasted recovery)"
            if ability_state.opponent_active_is_threat:
                penalty = min(penalty, 0.1)
                reason = "Full HP vs boosted threat (don't waste turn)"

        # =====================================================================
        # GENERAL ABILITY-STATUS THREAT AWARENESS (ABSO ROOT CAUSE FIX)
        # 
        # Many abilities have critical interactions with status conditions:
        #   - POISON HEAL: Wants PSN/TOX, crippled by BRN/PAR/SLP/FRZ
        #   - TOXIC BOOST: Wants PSN/TOX, crippled by other status
        #   - GUTS: Wants status (Atk boost), but BRN halves Attack (bad!)
        #   - MARVEL SCALE: Wants status (Def boost), but SLP/FRZ prevent action
        #   - QUICK FEET: Wants status (Speed boost), but PAR slows (bad!)
        # 
        # GENERAL PRINCIPLE: Check opponent's ACTUAL status-inflicting capabilities
        # (revealed moves + movepool data), not hardcoded species lists.
        # 
        # This replaces the WoW-specific hardcoded fix from commit 236e62c.
        # =====================================================================
        
        # This check only applies to non-switch moves (when we're active)
        if not move.startswith("switch ") and battle is not None:
            our_active = getattr(battle.user, "active", None) if hasattr(battle, "user") else None
            opponent_active = getattr(battle.opponent, "active", None) if hasattr(battle, "opponent") else None
            
            if our_active is not None and opponent_active is not None:
                our_status = getattr(our_active, "status", None)
                
                # Only apply penalty if we're NOT already statused
                # (if already statused, threat is irrelevant)
                if our_status is None:
                    # Get opponent's status threats (GENERAL check - all status types)
                    # Now includes TYPE IMMUNITY checks (Ground vs Thunder Wave, etc.)
                    opponent_threats = get_opponent_status_threats(
                        opponent_active,
                        our_pokemon=our_active,
                        movepool_data=TeamDatasets.movepool_data
                    )
                    
                    # Check if those threats are harmful for our ability
                    is_threatened, ability_name, threat_details = is_status_threatening_for_ability(
                        opponent_threats,
                        our_active
                    )
                    
                    if is_threatened:
                        move_data = all_move_json.get(move_name, {})
                        move_category = move_data.get(constants.CATEGORY, "")
                        base_power = move_data.get(constants.BASE_POWER, 0)
                        
                        # Heavily penalize status/setup moves
                        # These waste a turn and give opponent a free chance to status us
                        if move_name in SETUP_MOVES or move_category == constants.STATUS:
                            penalty = min(penalty, PENALTY_PASSIVE_VS_BOOSTED)
                            reason = f"{ability_name} vs status threat ({threat_details}) - avoid passive"
                        
                        # Moderately penalize weak attacks
                        # Should either switch or attack decisively
                        elif move_category in {constants.PHYSICAL, constants.SPECIAL} and base_power < 70:
                            penalty = min(penalty, 0.7)
                            reason = f"{ability_name} vs status threat - prefer strong move or switch"

        # =====================================================================
        # PHASE 3.3: MOMENTUM TRACKING
        # =====================================================================

        # Strong positive momentum: boost aggressive/setup moves
        if ability_state.momentum_level == "strong_positive":
            if move_name in AGGRESSIVE_MOVES or move_name in SETUP_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_AGGRESSIVE_STRONG_MOMENTUM)
                    reason = "Strong momentum (push advantage)"

        # Positive momentum: boost pressure moves
        elif ability_state.momentum_level == "positive":
            move_data = all_move_json.get(move_name, {})
            if move_data.get(constants.CATEGORY) in {constants.PHYSICAL, constants.SPECIAL}:
                if penalty >= 1.0 and penalty < 1.2:
                    penalty = max(penalty, BOOST_PRESSURE_MOMENTUM)
                    reason = "Positive momentum (maintain pressure)"

        # Negative momentum: boost pivots and safe switches
        elif ability_state.momentum_level == "negative":
            if move_name in SAFE_MOVES or move_name in PIVOT_MOVES_NORM:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_PIVOT_NEGATIVE_MOMENTUM)
                    reason = "Negative momentum (regroup with pivot)"

        # Strong negative momentum: boost high-risk/high-reward plays
        elif ability_state.momentum_level == "strong_negative":
            # When desperate, risky plays become more acceptable
            if move_name in SETUP_MOVES or move_name in RISKY_MOVES:
                if penalty >= 1.0:
                    penalty = max(penalty, BOOST_HIGHRISK_DESPERATE)
                    reason = "Desperate (high-risk play)"

        # =====================================================================
        # APPLY PENALTY
        # =====================================================================

        new_weight = weight * penalty
        penalized_policy[move] = new_weight

        if penalty < 1.0:
            penalties_applied.append((move, weight, new_weight, reason))
        elif penalty > 1.0:
            boosts_applied.append((move, weight, new_weight, reason))

    # Log penalties/boosts with reduced noise by grouping by reason
    def _log_grouped(entries, label):
        grouped = {}
        for move, old_weight, new_weight, reason in entries:
            grouped.setdefault(reason, []).append((move, old_weight, new_weight))

        for reason, items in grouped.items():
            if len(items) <= 3:
                for move, old_weight, new_weight in items:
                    logger.info(
                        f"{label} ({reason}) on {move}: {old_weight:.3f} -> {new_weight:.3f}"
                    )
            else:
                preview = ", ".join(
                    f"{m} {ow:.3f}->{nw:.3f}" for m, ow, nw in items[:3]
                )
                logger.info(
                    f"{label} ({reason}) on {len(items)} moves: {preview} (+{len(items)-3} more)"
                )

    _log_grouped(penalties_applied, "Ability penalty")
    _log_grouped(boosts_applied, "Ability BOOST")

    if trace_events is not None:
        for move, old_weight, new_weight, reason in penalties_applied:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "ability",
                    "move": move,
                    "reason": reason,
                    "before": old_weight,
                    "after": new_weight,
                }
            )
        for move, old_weight, new_weight, reason in boosts_applied:
            trace_events.append(
                {
                    "type": "boost",
                    "source": "ability",
                    "move": move,
                    "reason": reason,
                    "before": old_weight,
                    "after": new_weight,
                }
            )

    return penalized_policy


def apply_switch_penalties(
    policy: dict[str, float],
    battle: Battle,
    ability_state: OpponentAbilityState,
    playstyle: Playstyle | None = None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """
    Apply penalties and boosts to switch decisions based on:
    - Hazard damage on switch-in
    - Intimidate vs Defiant/Competitive
    - Type matchups
    - HP of switch target
    - Recovery moves available
    - Playstyle modifiers (FAT/STALL teams have reduced penalties)
    """
    if battle is None or battle.user.active is None:
        return policy

    # Get playstyle-specific switch penalty multiplier
    switch_penalty_mult = 1.0
    if playstyle is not None:
        cfg = PlaystyleConfig.get_config(playstyle)
        switch_penalty_mult = cfg.get("switch_penalty_multiplier", 1.0)

    adjusted_policy = {}
    penalties_applied = []
    boosts_applied = []
    active_hp_ratio = getattr(battle.user.active, "hp", 1) / max(
        getattr(battle.user.active, "max_hp", 1), 1
    )
    turn_number = int(getattr(battle, "turn", 0) or 0)
    last_selected_raw = str(
        getattr(getattr(battle.user, "last_selected_move", None), "move", "") or ""
    ).lower()
    switched_last_turn = turn_number > 1 and last_selected_raw.startswith("switch ")

    opponent = battle.opponent.active
    opponent_types = getattr(opponent, "types", []) if opponent else []
    opp_boosts = getattr(opponent, "boosts", {}) or {} if opponent else {}
    opponent_offensive_boost_level = max(
        int(opp_boosts.get(constants.ATTACK, 0) or 0),
        int(opp_boosts.get(constants.SPECIAL_ATTACK, 0) or 0),
    ) if opponent else 0

    # If we are guaranteed to move second and have a pivot move available,
    # prefer slow-pivot lines over hard switching where practical.
    can_slow_pivot_now = False
    if (
        battle.user.active is not None
        and not getattr(battle, "force_switch", False)
        and opponent_offensive_boost_level < 2
        and active_hp_ratio >= 0.40
    ):
        active_moves = getattr(battle.user.active, "moves", []) or []
        has_pivot_available = False
        for mv in active_moves:
            mv_name = normalize_name(mv.name if hasattr(mv, "name") else str(mv))
            mv_disabled = bool(getattr(mv, "disabled", False))
            mv_pp = getattr(mv, "current_pp", 1)
            if mv_name in PIVOT_MOVES_NORM and not mv_disabled and mv_pp != 0:
                has_pivot_available = True
                break
        if has_pivot_available:
            try:
                speed_assessment = assess_speed_order(battle)
                can_slow_pivot_now = bool(
                    getattr(speed_assessment, "guaranteed_move_second", False)
                )
            except Exception:
                can_slow_pivot_now = False

    # Build a map of our reserve Pokemon for quick lookup
    reserve_map = {}
    for pkmn in battle.user.reserve:
        if pkmn and pkmn.hp > 0:
            reserve_map[pkmn.name] = pkmn
            # Also map normalized name
            reserve_map[normalize_name(pkmn.name)] = pkmn

    for move, weight in policy.items():
        if weight <= 0:
            adjusted_policy[move] = weight
            continue

        # Only process switch moves
        if not move.startswith("switch "):
            adjusted_policy[move] = weight
            continue

        # Extract switch target name
        switch_target_name = move.split("switch ", 1)[1].strip()
        target_pkmn = reserve_map.get(switch_target_name) or reserve_map.get(normalize_name(switch_target_name))

        if target_pkmn is None:
            adjusted_policy[move] = weight
            continue

        multiplier = 1.0
        reasons = []
        critical_penalty = False
        target_moves = getattr(target_pkmn, "moves", []) or []
        target_move_names = [
            m.name if hasattr(m, "name") else str(m) for m in target_moves
        ]
        target_move_names_norm = {normalize_name(m) for m in target_move_names}
        target_types_norm = {
            normalize_name(t) for t in (getattr(target_pkmn, "types", []) or [])
        }
        target_hp_ratio = target_pkmn.hp / max(target_pkmn.max_hp, 1)
        target_has_recovery = bool(target_move_names_norm & {normalize_name(m) for m in POKEMON_RECOVERY_MOVES})
        target_has_regenerator = normalize_name(getattr(target_pkmn, "ability", None) or "") == "regenerator"

        # === TARGET STATUS RELIABILITY PENALTY ===
        target_status = getattr(target_pkmn, "status", None)
        if target_status == constants.FROZEN:
            multiplier *= 0.25
            reasons.append("frozen target (unreliable switch-in)")
            critical_penalty = True
        elif target_status == constants.SLEEP:
            has_sleep_talk = "sleeptalk" in target_move_names_norm
            rest_turns = int(getattr(target_pkmn, "rest_turns", 0) or 0)
            if has_sleep_talk and rest_turns > 0:
                multiplier *= 0.78
                reasons.append("asleep (Sleep Talk available)")
            else:
                multiplier *= 0.35
                reasons.append("asleep without Sleep Talk line")
                critical_penalty = True

        # === SALT CURE MATCHUP SAFETY ===
        # Revealed Salt Cure makes Water/Steel pivots far worse in long games:
        # they take 25%/turn chip and get forced into passive recovery loops.
        if getattr(ability_state, "opponent_has_salt_cure", False):
            salted_types = {"water", "steel"}
            if target_types_norm & salted_types:
                salt_mult = 0.64
                if target_has_recovery or target_has_regenerator:
                    salt_mult = 0.76
                if target_hp_ratio <= 0.55:
                    salt_mult = min(salt_mult, 0.70)
                multiplier *= salt_mult
                reasons.append("Salt Cure risk (Water/Steel chip)")
                if not (target_has_recovery or target_has_regenerator):
                    critical_penalty = True
            elif getattr(ability_state, "our_active_is_salt_cured", False):
                # If we're currently salted, switching to a safer non-Water/Steel
                # target can reset chip and preserve long-term board health.
                multiplier *= 1.08
                reasons.append("reset active Salt Cure chip")

        # === HAZARD DAMAGE PENALTY ===
        if ability_state.our_hazard_layers > 0:
            # Calculate approximate hazard damage
            hazard_damage = 0.0
            target_types = getattr(target_pkmn, "types", []) or []
            target_item = getattr(target_pkmn, "item", None)

            # Skip if Heavy-Duty Boots
            if target_item != "heavydutyboots":
                # Stealth Rock damage
                if ability_state.our_sr_up:
                    sr_mult = type_effectiveness_modifier("rock", target_types)
                    hazard_damage += 0.125 * sr_mult  # 6.25% to 50%

                # Spikes damage (only if grounded)
                is_grounded = "flying" not in target_types
                if hasattr(target_pkmn, "ability") and target_pkmn.ability in UNGROUNDED_ABILITIES:
                    is_grounded = False
                if is_grounded and ability_state.our_spikes_layers > 0:
                    spikes_damage = [0, 0.125, 0.167, 0.25][min(ability_state.our_spikes_layers, 3)]
                    hazard_damage += spikes_damage

                # Apply penalty based on hazard damage
                if hazard_damage > 0:
                    # Scale penalty: 12.5% damage = ~8% penalty, 50% damage = ~32% penalty
                    hp_ratio = target_pkmn.hp / max(target_pkmn.max_hp, 1)
                    effective_damage = min(hazard_damage, hp_ratio)  # Can't take more than current HP
                    damage_penalty = PENALTY_SWITCH_INTO_HAZARDS_PER_LAYER ** (effective_damage * 8)
                    multiplier *= damage_penalty
                    reasons.append(f"hazards ({int(hazard_damage * 100)}% damage)")

        # === INTIMIDATE VS DEFIANT/COMPETITIVE PENALTY ===
        if ability_state.opponent_has_defiant_pokemon:
            # Check if switch target has Intimidate
            target_ability = getattr(target_pkmn, "ability", None)
            if target_ability in INTIMIDATE_ABILITIES or target_pkmn.name in POKEMON_WITH_INTIMIDATE_COMMON:
                multiplier *= PENALTY_SWITCH_INTIMIDATE_VS_DEFIANT
                reasons.append("Intimidate vs Defiant/Competitive")

        # === LOW HP PENALTY ===
        hp_ratio = target_hp_ratio
        if hp_ratio < 0.15:
            # Tier 1: Near-certain death — critical penalty, cannot be overridden by type boosts
            multiplier *= PENALTY_SWITCH_VERY_LOW_HP
            reasons.append(f"very low HP ({int(hp_ratio * 100)}%) - near-certain death")
            critical_penalty = True
        elif hp_ratio < 0.25:
            # Tier 2: Standard low HP penalty
            multiplier *= PENALTY_SWITCH_LOW_HP
            reasons.append(f"low HP ({int(hp_ratio * 100)}%)")
        elif hp_ratio < 0.40 and opponent_offensive_boost_level >= 2:
            # Tier 3: Below 40% HP into a highly boosted sweeper — likely KO
            multiplier *= PENALTY_SWITCH_LOW_HP_VS_BOOSTED
            reasons.append(f"low HP ({int(hp_ratio * 100)}%) vs +{opponent_offensive_boost_level} boosted sweeper")
            critical_penalty = True

        # === TYPE WEAKNESS PENALTY ===
        if opponent and opponent_types:
            target_types = getattr(target_pkmn, "types", []) or []
            if target_types:
                # Check if target is weak to opponent's STAB
                for opp_type in opponent_types:
                    effectiveness = type_effectiveness_modifier(opp_type, target_types)
                    if effectiveness >= 2.0:
                        multiplier *= PENALTY_SWITCH_WEAK_TO_OPPONENT
                        reasons.append(f"weak to {opp_type}")
                        if opponent_offensive_boost_level >= 2:
                            multiplier *= 0.55
                            reasons.append(
                                f"weak to +{opponent_offensive_boost_level} boosted STAB"
                            )
                            critical_penalty = True
                        elif getattr(ability_state, "opponent_has_offensive_boost", False):
                            multiplier *= 0.75
                            reasons.append("weak to boosted STAB")
                            critical_penalty = True
                        break

        # === TYPE RESISTANCE BOOST ===
        if opponent and opponent_types:
            target_types = getattr(target_pkmn, "types", []) or []
            if target_types:
                # Check if target resists opponent's STAB
                resists_both = True
                for opp_type in opponent_types:
                    effectiveness = type_effectiveness_modifier(opp_type, target_types)
                    if effectiveness >= 1.0:
                        resists_both = False
                        break
                if resists_both and len(opponent_types) > 0:
                    resist_boost = BOOST_SWITCH_RESISTS_STAB
                    if (
                        not getattr(ability_state, "opponent_has_offensive_boost", False)
                        and active_hp_ratio >= 0.55
                    ):
                        # Keep defensive switching available, but avoid overvaluing
                        # it when our current board is still healthy.
                        resist_boost = 1.0 + (BOOST_SWITCH_RESISTS_STAB - 1.0) * 0.5
                    multiplier *= resist_boost
                    reasons.append("resists opponent STAB")

        # === UNAWARE VS SETUP BOOST (ENHANCED 2026-02-08) ===
        if opponent is not None:
            opp_boosts = getattr(opponent, "boosts", {}) or {}
            has_offensive_boost = (
                opp_boosts.get(constants.ATTACK, 0) > 0
                or opp_boosts.get(constants.SPECIAL_ATTACK, 0) > 0
            )
            if has_offensive_boost:
                boost_level = max(opp_boosts.get(constants.ATTACK, 0), opp_boosts.get(constants.SPECIAL_ATTACK, 0))
                
                target_ability = getattr(target_pkmn, "ability", None)
                target_ability_norm = normalize_name(target_ability) if target_ability else None
                target_name_norm = normalize_name(target_pkmn.name)
                target_base_norm = normalize_name(getattr(target_pkmn, "base_name", "") or target_pkmn.name)
                
                # CRITICAL: Unaware walls boosted sweepers perfectly
                if (
                    target_ability_norm == "unaware"
                    or target_name_norm in POKEMON_COMMONLY_UNAWARE
                    or target_base_norm in POKEMON_COMMONLY_UNAWARE
                ):
                    # Scale boost with threat level
                    unaware_boost = BOOST_SWITCH_UNAWARE_VS_SETUP + (boost_level - 1) * 0.15
                    multiplier *= unaware_boost
                    reasons.append(f"Unaware vs +{boost_level} boosted (PERFECT WALL)")
                
                # CRITICAL: Phazing resets all boosts
                elif any(
                    normalize_name(m) in PHAZING_MOVES
                    or normalize_name(m) in {"haze", "clearsmog"}
                    for m in target_move_names
                ):
                    normalized_moves = {normalize_name(m) for m in target_move_names}
                    has_fast_reset = bool(normalized_moves & {"haze", "clearsmog"})
                    opp_speed_boost = opp_boosts.get(constants.SPEED, 0)
                    target_hp_ratio = target_pkmn.hp / max(target_pkmn.max_hp, 1)

                    # Whirlwind/Roar are negative priority. If opponent already has speed boosts,
                    # blindly switching to a low-HP phazer often hands over another KO.
                    if has_fast_reset:
                        phaze_boost = BOOST_SWITCH_COUNTERS + (boost_level - 1) * 0.1
                        multiplier *= phaze_boost
                        reasons.append(f"Haze/Clear Smog vs +{boost_level} boosted (RESET THREAT)")
                    elif opp_speed_boost > 0:
                        cautious_mult = 1.05 if target_hp_ratio >= 0.70 else 0.95
                        multiplier *= cautious_mult
                        reasons.append(
                            f"Phaze user vs +{opp_speed_boost} Spe threat (low-priority risk)"
                        )
                    else:
                        phaze_boost = BOOST_SWITCH_COUNTERS + (boost_level - 1) * 0.1
                        multiplier *= phaze_boost
                        reasons.append(f"Phaze vs +{boost_level} boosted (RESET THREAT)")
                
                # HIGH PRIORITY: Priority moves can revenge kill
                elif any(m in PRIORITY_MOVES for m in target_move_names):
                    multiplier *= BOOST_SWITCH_COUNTERS
                    reasons.append(f"Priority vs +{boost_level} boosted (revenge kill)")
                
                # DEFENSIVE: Type resistance helps survive
                else:
                    target_types = getattr(target_pkmn, "types", []) or []
                    if target_types and opponent_types:
                        # Check if target resists opponent's STAB
                        resists_count = 0
                        for opp_type in opponent_types:
                            effectiveness = type_effectiveness_modifier(opp_type, target_types)
                            if effectiveness <= 0.5:
                                resists_count += 1
                        
                        # If we resist their STAB, we can at least survive to check them
                        if resists_count > 0:
                            resist_boost = 1.3 + (boost_level - 1) * 0.1
                            multiplier *= resist_boost
                            reasons.append(f"Resists STAB vs +{boost_level} boosted (defensive check)")

        # === RECOVERY MOVE BOOST ===
        has_recovery = False
        for move_name in target_move_names_norm:
            if move_name in POKEMON_RECOVERY_MOVES:
                has_recovery = True
                break
        if has_recovery and (
            hp_ratio <= 0.75
            or getattr(ability_state, "opponent_has_offensive_boost", False)
            or active_hp_ratio <= 0.45
        ):
            recovery_mult = BOOST_SWITCH_HAS_RECOVERY
            if getattr(ability_state, "opponent_has_offensive_boost", False):
                has_direct_reset = bool(
                    target_move_names_norm & (set(PHAZING_MOVES) | {"haze", "clearsmog"})
                )
                target_ability_norm = normalize_name(getattr(target_pkmn, "ability", None) or "")
                target_name_norm = normalize_name(target_pkmn.name)
                target_base_norm = normalize_name(
                    getattr(target_pkmn, "base_name", "") or target_pkmn.name
                )
                has_unaware_like = (
                    target_ability_norm == "unaware"
                    or target_name_norm in POKEMON_COMMONLY_UNAWARE
                    or target_base_norm in POKEMON_COMMONLY_UNAWARE
                )
                if has_direct_reset or has_unaware_like:
                    recovery_mult = min(recovery_mult, 1.08)
                    reasons.append("has recovery (limited vs boosted threat)")
                else:
                    recovery_mult = min(recovery_mult, 0.94)
                    reasons.append("has recovery (de-emphasized vs boosted threat)")
                critical_penalty = True
            else:
                reasons.append("has recovery")
            multiplier *= recovery_mult

        # === SLOW PIVOT PREFERENCE ===
        if can_slow_pivot_now:
            multiplier *= 0.80
            reasons.append("prefer slow pivot (U-turn/Chilly Reception)")

        # === POISON HEAL STATUS VULNERABILITY ===
        # Poison Heal mons (Gliscor, Breloom) are ruined if burned before Toxic Orb activates
        # Status-backfire mons (Guts, Marvel Scale) WANT to be statused
        if opponent is not None:
            target_ability = getattr(target_pkmn, "ability", None)
            target_status = getattr(target_pkmn, "status", None)
            target_name_norm = normalize_name(target_pkmn.name)
            target_base_norm = normalize_name(getattr(target_pkmn, "base_name", "") or target_pkmn.name)
            
            # Check if target is a Poison Heal mon
            is_poison_heal = (
                (target_ability and normalize_name(target_ability) == "poisonheal")
                or target_name_norm in POKEMON_COMMONLY_POISON_HEAL
                or target_base_norm in POKEMON_COMMONLY_POISON_HEAL
            )
            
            # Check if target is a status-backfire mon (Guts, Marvel Scale, Quick Feet)
            is_status_backfire = (
                target_name_norm in POKEMON_STATUS_BACKFIRES
                or target_base_norm in POKEMON_STATUS_BACKFIRES
            )
            
            # Get opponent's known moves
            opponent_moves = getattr(opponent, "moves", [])
            opponent_move_names = [
                m.name if hasattr(m, "name") else str(m) for m in opponent_moves
            ]
            
            # Check for status-inflicting moves
            has_burn_move = any(
                move_name in STATUS_INFLICTING_MOVES and move_name in {"willowisp", "scald", "searingshot", "inferno", "sacredfire", "burningjealousy"}
                for move_name in opponent_move_names
            )
            has_paralyze_move = any(
                move_name in STATUS_INFLICTING_MOVES and move_name in {"thunderwave", "stunspore", "glare", "nuzzle", "zapcannon", "bodyslam"}
                for move_name in opponent_move_names
            )
            has_status_move = has_burn_move or has_paralyze_move
            
            # PENALTY: Poison Heal mon switching into burn move (if not already poisoned)
            if is_poison_heal and has_burn_move and target_status not in (POISON, TOXIC):
                # This is catastrophic - burning a Poison Heal mon ruins it permanently
                multiplier *= PENALTY_SWITCH_WEAK_TO_OPPONENT  # Use existing severe penalty constant
                reasons.append("Poison Heal vs burn move")
            
            # BOOST: Status-backfire mon (Guts/Marvel Scale) switching into status move
            # These mons WANT to be statused (if not already statused)
            if is_status_backfire and has_status_move and target_status is None:
                multiplier *= BOOST_SWITCH_COUNTERS  # Use existing counter boost constant
                reasons.append("Guts/Marvel Scale vs status move")

        # === PLAYSTYLE ADJUSTMENT ===
        # For penalties (multiplier < 1.0), apply playstyle modulation
        # FAT/STALL teams get less harsh penalties (0.6x = 60% of base penalty)
        # HYPER_OFFENSE teams get harsher penalties (1.4x = 140% of base penalty)
        if multiplier < 1.0 and switch_penalty_mult != 1.0 and not critical_penalty:
            # Convert penalty to "distance from 1.0"
            penalty_magnitude = 1.0 - multiplier
            # Adjust penalty magnitude by playstyle
            adjusted_penalty_magnitude = penalty_magnitude * switch_penalty_mult
            # Convert back to multiplier
            multiplier = 1.0 - adjusted_penalty_magnitude
            if switch_penalty_mult != 1.0:
                reasons.append(f"playstyle {switch_penalty_mult:.1f}x")
        elif multiplier < 1.0 and switch_penalty_mult != 1.0 and critical_penalty:
            reasons.append("critical penalty (skip playstyle softening)")

        # Repeated switch chains usually lose board progress. Penalize all switch
        # options after we just switched, unless we are forced or too low to stay.
        if (
            switched_last_turn
            and not getattr(battle, "force_switch", False)
            and active_hp_ratio >= 0.40
        ):
            chain_mult = 0.72 if ability_state.opponent_has_offensive_boost else 0.82
            multiplier *= chain_mult
            reasons.append(f"break switch chain ({chain_mult:.2f}x)")

        # Keep switch boosts bounded so stacked heuristics cannot drown out
        # obvious progress lines from eval (main source of switch spam).
        if multiplier > 1.0:
            max_switch_boost = 1.25
            if getattr(ability_state, "opponent_has_offensive_boost", False):
                max_switch_boost = 1.40
            if multiplier > max_switch_boost:
                multiplier = max_switch_boost
                reasons.append(f"cap switch boost ({max_switch_boost:.2f}x)")

        # Apply multiplier
        new_weight = weight * multiplier
        adjusted_policy[move] = new_weight

        if multiplier < 1.0:
            penalties_applied.append((move, weight, new_weight, ", ".join(reasons)))
        elif multiplier > 1.0:
            boosts_applied.append((move, weight, new_weight, ", ".join(reasons)))

    # Log switch adjustments
    for move, old_w, new_w, reason in penalties_applied:
        logger.info(f"Switch penalty ({reason}): {move} {old_w:.3f} -> {new_w:.3f}")
    for move, old_w, new_w, reason in boosts_applied:
        logger.info(f"Switch BOOST ({reason}): {move} {old_w:.3f} -> {new_w:.3f}")

    if trace_events is not None:
        for move, old_w, new_w, reason in penalties_applied:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "switch",
                    "move": move,
                    "reason": reason,
                    "before": old_w,
                    "after": new_w,
                }
            )
        for move, old_w, new_w, reason in boosts_applied:
            trace_events.append(
                {
                    "type": "boost",
                    "source": "switch",
                    "move": move,
                    "reason": reason,
                    "before": old_w,
                    "after": new_w,
                }
            )

    return adjusted_policy


def _estimate_immediate_heal_fraction(move_norm: str, move_data: dict) -> float:
    """Estimate immediate HP recovered this turn as a fraction of max HP."""
    heal = move_data.get("heal")
    if isinstance(heal, (list, tuple)) and len(heal) == 2:
        num, den = heal
        try:
            value = float(num) / float(den)
            return max(0.0, min(value, 1.0))
        except Exception:
            pass

    # Weather-conditional or implicit half-heal moves often omit explicit fractions.
    if move_norm in {
        "recover",
        "softboiled",
        "roost",
        "slackoff",
        "milkdrink",
        "healorder",
        "moonlight",
        "morningsun",
        "synthesis",
        "shoreup",
    }:
        return 0.50
    return 0.0


def _is_fixed_damage_attack(move_norm: str, move_data: dict) -> bool:
    """True for fixed-damage attacks that can progress despite 0 base power."""
    if move_norm in {"superfang", "ruination", "naturesmadness", "naturefury"}:
        return True
    fixed_damage = move_data.get("damage")
    if isinstance(fixed_damage, (int, float)) and fixed_damage > 0:
        return True
    if isinstance(fixed_damage, str) and normalize_name(fixed_damage) == "level":
        return True
    return False


def _is_stabilizing_recovery_line(
    move_norm: str,
    move_data: dict,
    our_hp_ratio: float,
    incoming_pressure: float | None,
) -> bool:
    """
    Return True when an immediate recovery move is likely to improve our net HP
    after a typical incoming hit this turn.
    """
    if incoming_pressure is None:
        return False
    if move_norm == "wish":
        return False  # delayed recovery; treat as passive under immediate pressure

    heal_fraction = _estimate_immediate_heal_fraction(move_norm, move_data)
    if heal_fraction <= 0:
        return False

    # If expected incoming damage nearly cancels the heal, this is not stabilizing.
    if incoming_pressure >= heal_fraction * 0.95:
        return False

    post_heal = min(1.0, our_hp_ratio + heal_fraction)
    post_trade = post_heal - incoming_pressure
    required_gain = max(0.06, heal_fraction * 0.20)
    return post_trade >= our_hp_ratio + required_gain


def _is_ghost_type(pokemon) -> bool:
    if pokemon is None:
        return False
    pkmn_types = [normalize_name(t) for t in (getattr(pokemon, "types", []) or [])]
    return "ghost" in pkmn_types


def _is_unaware_like(pokemon) -> bool:
    if pokemon is None:
        return False
    ability_norm = normalize_name(getattr(pokemon, "ability", None) or "")
    if ability_norm == "unaware":
        return True
    name_norm = normalize_name(getattr(pokemon, "name", "") or "")
    base_norm = normalize_name(getattr(pokemon, "base_name", "") or "")
    return (
        name_norm in POKEMON_COMMONLY_UNAWARE
        or base_norm in POKEMON_COMMONLY_UNAWARE
    )


def _find_switch_target(battle: Battle | None, move: str):
    if battle is None or not move.startswith("switch "):
        return None
    target_name = normalize_name(move.split("switch ", 1)[1])
    for pkmn in getattr(getattr(battle, "user", None), "reserve", []) or []:
        candidate_name = normalize_name(getattr(pkmn, "name", ""))
        if candidate_name == target_name:
            return pkmn
    return None


def _assess_immediate_survival_risk(battle: Battle | None) -> dict[str, float | bool]:
    """
    Estimate whether staying in is likely to get KOed before we can act.
    Returns a compact dict used by both eval and MCTS-anchor safety rails.
    """
    if battle is None or getattr(battle, "user", None) is None or getattr(battle, "opponent", None) is None:
        return {"risk": False, "incoming": 0.0, "our_hp_ratio": 1.0, "move_second": False, "uncertain_speed": False}
    if battle.user.active is None or battle.opponent.active is None:
        return {"risk": False, "incoming": 0.0, "our_hp_ratio": 1.0, "move_second": False, "uncertain_speed": False}

    our_active = battle.user.active
    our_hp_ratio = getattr(our_active, "hp", 1) / max(getattr(our_active, "max_hp", 1), 1)
    incoming = 0.0
    try:
        incoming = float(_eval_opponent_best_damage(battle))
    except Exception:
        incoming = 0.0

    move_second = False
    uncertain_speed = False
    try:
        speed = assess_speed_order(battle)
        move_second = bool(getattr(speed, "guaranteed_move_second", False))
        uncertain_speed = bool(getattr(speed, "uncertain", False))
    except Exception:
        move_second = False
        uncertain_speed = True

    # Lethal-or-near-lethal pressure while we likely move second.
    # For uncertain speed, require clear overkill to avoid overreacting.
    lethal_if_second = incoming >= max(our_hp_ratio * 0.92, 0.35)
    lethal_if_uncertain = incoming >= max(our_hp_ratio * 1.05, 0.50)
    risk = (move_second and lethal_if_second) or (uncertain_speed and lethal_if_uncertain)
    return {
        "risk": bool(risk),
        "incoming": float(incoming),
        "our_hp_ratio": float(our_hp_ratio),
        "move_second": bool(move_second),
        "uncertain_speed": bool(uncertain_speed),
    }


def _is_non_switch_stall_survival_line(
    move: str,
    battle: Battle | None,
    *,
    incoming_pressure: float | None = None,
) -> bool:
    if move.startswith("switch "):
        return False
    move_name, move_norm = _move_name_and_norm(move)
    if move_norm in STALL_SURVIVAL_MOVES_NORM:
        return True
    if move_norm in RECOVERY_MOVES_NORM:
        move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
        our_active = getattr(getattr(battle, "user", None), "active", None)
        our_hp_ratio = 1.0
        if our_active is not None:
            our_hp_ratio = getattr(our_active, "hp", 1) / max(getattr(our_active, "max_hp", 1), 1)
        return _is_stabilizing_recovery_line(
            move_norm,
            move_data,
            our_hp_ratio,
            incoming_pressure,
        )
    return False


def _is_meaningful_progress_move(
    move: str,
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    *,
    include_status_pivots: bool = True,
    incoming_pressure: float | None = None,
) -> bool:
    """
    Decide whether a non-switch move produces immediate, defensible progress.

    Progress here means one of:
    - Direct board interaction (damage/phaze/haze/fixed damage),
    - A hazard layer that still adds value right now,
    - A stabilizing recovery line (net HP-positive under pressure),
    - Optional status pivots (Parting Shot / Chilly Reception) when enabled.
    """
    if move.startswith("switch "):
        return False

    move_name = move.split(":")[-1] if ":" in move else move
    move_norm = normalize_name(move_name)
    move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
    category = move_data.get(constants.CATEGORY)
    reset_moves = set(PHAZING_MOVES) | {"haze", "clearsmog"}

    if move_norm in reset_moves:
        return True

    if category in {constants.PHYSICAL, constants.SPECIAL}:
        base_power = float(move_data.get(constants.BASE_POWER, 0) or 0)
        return (
            base_power >= 55
            or move_norm in PRIORITY_MOVES
            or move_norm in {"knockoff", "trick", "switcheroo"}
            or _is_fixed_damage_attack(move_norm, move_data)
        )

    if include_status_pivots and move_norm in PIVOT_MOVES_NORM and category == constants.STATUS:
        return True

    if move_norm in HAZARD_SETTING_MOVES:
        if not _hazard_move_can_progress(move_norm, battle):
            return False
        # If active hazard removal is staring at us, restacking is usually not
        # immediate progress; pressure/removal control should come first.
        if (
            ability_state is not None
            and getattr(ability_state, "opponent_active_has_hazard_removal", False)
            and getattr(ability_state, "opponent_hazard_layers", 0) > 0
        ):
            return False
        return True

    if move_norm in RECOVERY_MOVES_NORM:
        our_active = getattr(getattr(battle, "user", None), "active", None)
        our_hp_ratio = 1.0
        if our_active is not None:
            our_hp_ratio = getattr(our_active, "hp", 1) / max(
                getattr(our_active, "max_hp", 1), 1
            )
        return _is_stabilizing_recovery_line(
            move_norm,
            move_data,
            our_hp_ratio,
            incoming_pressure,
        )

    return False


def apply_hazard_maintenance_bias(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """
    Preserve meaningful hazard pressure when the active opponent can remove hazards.

    Core behavior:
    - Prefer pressuring the remover now over passive loops.
    - Prefer spinblocking switches against Rapid Spin / Mortal Spin.
    - Avoid re-stacking hazards into immediate removal turns.
    """
    if battle is None or ability_state is None or getattr(battle, "force_switch", False):
        return policy
    if getattr(ability_state, "opponent_hazard_layers", 0) <= 0:
        return policy
    if not getattr(ability_state, "opponent_active_has_hazard_removal", False):
        return policy

    hazard_layers = max(1, int(getattr(ability_state, "opponent_hazard_layers", 0) or 0))
    urgency = min(1.0, 0.30 + hazard_layers * 0.14)
    spinner_active = bool(getattr(ability_state, "opponent_active_removal_is_spin", False))

    best_pressure_weight = 0.0
    for move, weight in policy.items():
        if weight <= 0:
            continue
        if _is_meaningful_progress_move(
            move,
            battle,
            ability_state,
            include_status_pivots=False,
        ):
            best_pressure_weight = max(best_pressure_weight, weight)

    adjusted = dict(policy)
    for move, weight in policy.items():
        if weight <= 0:
            continue

        move_name = move.split(":")[-1] if ":" in move else move
        move_norm = normalize_name(move_name)
        move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
        category = move_data.get(constants.CATEGORY)
        new_weight = weight
        reason = None

        if move.startswith("switch "):
            target = _find_switch_target(battle, move)
            target_is_spinblocker = _is_ghost_type(target)

            if (
                spinner_active
                and target_is_spinblocker
                and getattr(target, "hp", 0) > 0
            ):
                multiplier = 1.10 + 0.22 * urgency
                new_weight = weight * multiplier
                reason = "hazard_preserve_spinblock"
            elif best_pressure_weight > 0:
                cap = best_pressure_weight * (0.96 if spinner_active else 0.92)
                if new_weight > cap:
                    new_weight = cap
                    reason = "hazard_preserve_cap_switch"
        else:
            if category in {constants.PHYSICAL, constants.SPECIAL}:
                base_power = float(move_data.get(constants.BASE_POWER, 0) or 0)
                if (
                    base_power >= 70
                    or move_norm in {"knockoff"}
                    or _is_fixed_damage_attack(move_norm, move_data)
                ):
                    multiplier = 1.08 + 0.20 * urgency
                    new_weight = weight * multiplier
                    reason = "hazard_preserve_pressure"
            elif move_norm in HAZARD_SETTING_MOVES:
                if _hazard_move_can_progress(move_norm, battle):
                    new_weight = weight * 0.72
                    reason = "hazard_preserve_delay_restack"
                else:
                    new_weight = weight * 0.35
                    reason = "hazard_preserve_no_progress"
            elif move_norm in RECOVERY_MOVES_NORM or move_norm in SETUP_MOVES:
                if best_pressure_weight > 0:
                    cap = best_pressure_weight * 0.80
                    if new_weight > cap:
                        new_weight = cap
                        reason = "hazard_preserve_avoid_passive"
            elif move_norm in PIVOT_MOVES_NORM and category == constants.STATUS:
                if best_pressure_weight > 0:
                    cap = best_pressure_weight * 0.84
                    if new_weight > cap:
                        new_weight = cap
                        reason = "hazard_preserve_avoid_pivot_loop"

        adjusted[move] = new_weight
        if trace_events is not None and reason is not None and abs(new_weight - weight) > 1e-9:
            trace_events.append(
                {
                    "type": "penalty" if new_weight < weight else "boost",
                    "source": "hazard_maintenance",
                    "move": move,
                    "reason": reason,
                    "before": weight,
                    "after": new_weight,
                }
            )

    return adjusted


def apply_threat_switch_bias(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """
    Threat-aware cleanup after other penalty layers.

    Goals:
    - Suppress passive status lines (hazards/recovery) while facing a boosted threat.
    - Preserve and often prioritize direct reset lines (phaze/haze).
    - If no switch exists, force proactive attacks over passive play.
    """
    if battle is None or ability_state is None or not ability_state.opponent_active_is_threat:
        return policy

    # Extra caution scales with current offensive boost level.
    boost_level = max(
        1,
        int(getattr(ability_state, "opponent_attack_boost", 0) or 0),
        int(getattr(ability_state, "opponent_spa_boost", 0) or 0),
    )
    force_switch = bool(getattr(battle, "force_switch", False))
    turn_number = int(getattr(battle, "turn", 0) or 0)
    user_battler = getattr(battle, "user", None)
    last_selected_raw = str(
        getattr(getattr(user_battler, "last_selected_move", None), "move", "") or ""
    ).lower()
    last_used_raw = str(
        getattr(getattr(user_battler, "last_used_move", None), "move", "") or ""
    ).lower()
    switched_last_turn = (
        turn_number > 1
        and (last_selected_raw.startswith("switch ") or last_used_raw.startswith("switch "))
    )

    reset_moves = set(PHAZING_MOVES) | {"haze", "clearsmog"}
    recovery_moves_norm = {normalize_name(m) for m in RECOVERY_MOVES}

    best_switch_weight = 0.0
    best_attack_weight = 0.0
    best_strong_attack_weight = 0.0
    best_reset_weight = 0.0
    for move, weight in policy.items():
        if weight <= 0:
            continue
        if move.startswith("switch "):
            best_switch_weight = max(best_switch_weight, weight)
            continue
        move_name = move.split(":")[-1] if ":" in move else move
        move_norm = normalize_name(move_name)
        move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
        category = move_data.get(constants.CATEGORY)
        if move_norm in reset_moves:
            best_reset_weight = max(best_reset_weight, weight)
        elif category in {constants.PHYSICAL, constants.SPECIAL}:
            best_attack_weight = max(best_attack_weight, weight)
            base_power = float(move_data.get(constants.BASE_POWER, 0) or 0)
            if (
                base_power >= 75
                or move_norm in PRIORITY_MOVES
                or _is_fixed_damage_attack(move_norm, move_data)
            ):
                best_strong_attack_weight = max(best_strong_attack_weight, weight)

    anchor_weight = max(best_switch_weight, best_attack_weight, best_reset_weight)
    if anchor_weight <= 0:
        return policy
    best_progress_weight = max(best_reset_weight, best_strong_attack_weight)
    has_viable_progress_line = best_progress_weight >= anchor_weight * 0.15

    our_active = getattr(getattr(battle, "user", None), "active", None)
    our_hp_ratio = 1.0
    if our_active is not None:
        our_hp_ratio = getattr(our_active, "hp", 1) / max(getattr(our_active, "max_hp", 1), 1)
    our_is_unaware = _is_unaware_like(our_active)
    healthy_enough_for_reset = our_hp_ratio >= 0.35
    incoming_pressure = None
    try:
        incoming_pressure = float(_eval_opponent_best_damage(battle))
    except Exception:
        incoming_pressure = None
    unaware_hold_mode = (
        our_is_unaware
        and boost_level >= 1
        and not force_switch
        and our_hp_ratio >= 0.30
    )

    # Detect when we have NO offensive answer to the opponent.
    # If every attacking move is weight-0 (type-immune) or absent, status moves
    # like Toxic are our only way to make progress — don't suppress them.
    no_offensive_answer = best_attack_weight <= 0 and best_reset_weight <= 0

    adjusted = {}
    for move, weight in policy.items():
        # Hard-blocked moves (type immunity, etc.) must stay at zero.
        if weight <= 0:
            adjusted[move] = weight
            continue
        move_name = move.split(":")[-1] if ":" in move else move
        move_norm = normalize_name(move_name)
        move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
        cat = move_data.get(constants.CATEGORY)
        is_switch = move.startswith("switch ")
        is_reset = move_norm in reset_moves
        is_damaging = cat in {constants.PHYSICAL, constants.SPECIAL}
        is_status = cat == constants.STATUS
        base_power = float(move_data.get(constants.BASE_POWER, 0) or 0)
        is_fixed_damage = _is_fixed_damage_attack(move_norm, move_data)
        is_strong_attack = is_damaging and (
            base_power >= 75 or move_norm in PRIORITY_MOVES or is_fixed_damage
        )
        new_weight = weight
        reason = None

        # If we can reset boosts now, do not get trapped in endless switching.
        if is_reset:
            if healthy_enough_for_reset:
                # Ensure reset lines compete directly with switch options.
                target = anchor_weight * (1.08 + min(0.04 * (boost_level - 1), 0.16))
                if target > new_weight:
                    new_weight = target
                    reason = "boosted_threat_prefer_reset"
            elif best_switch_weight > 0:
                # Keep reset lines visible but don't overforce when we're too low.
                floor = best_switch_weight * 0.70
                if floor > new_weight:
                    new_weight = floor
                    reason = "boosted_threat_keep_reset_option"

        elif is_status and not is_switch:
            # Passive moves are usually bad while a boosted threat is active.
            # EXCEPTION: when we have no offensive answer (all attacks type-immune),
            # status moves like Toxic are our only way to make progress.
            if no_offensive_answer and move_norm in {"toxic", "willowisp", "thunderwave", "toxicspikes"}:
                reason = "no_offensive_answer_status_exempt"
                status_cap = float("inf")  # skip capping — this is our only progress
            elif move_norm == "partingshot":
                status_cap = anchor_weight * (0.62 if best_switch_weight > 0 else 0.52)
            elif move_norm in recovery_moves_norm:
                stabilizing_recovery = _is_stabilizing_recovery_line(
                    move_norm,
                    move_data,
                    our_hp_ratio,
                    incoming_pressure,
                )
                if stabilizing_recovery:
                    # If recovery is projected to net HP this turn, keep it live.
                    status_cap = anchor_weight * (0.92 if best_switch_weight > 0 else 0.84)
                elif our_hp_ratio <= 0.28:
                    status_cap = anchor_weight * (0.80 if best_switch_weight > 0 else 0.70)
                elif our_hp_ratio <= 0.45:
                    status_cap = anchor_weight * (0.58 if best_switch_weight > 0 else 0.45)
                else:
                    status_cap = anchor_weight * (0.40 if best_switch_weight > 0 else 0.28)
            else:
                status_cap = anchor_weight * (0.45 if best_switch_weight > 0 else 0.30)
            if has_viable_progress_line:
                # Keep hazards/recovery below direct progress options under pressure.
                if move_norm in recovery_moves_norm:
                    stabilizing_recovery = _is_stabilizing_recovery_line(
                        move_norm,
                        move_data,
                        our_hp_ratio,
                        incoming_pressure,
                    )
                    if stabilizing_recovery:
                        recovery_progress_cap = 1.05
                    else:
                        recovery_progress_cap = 0.82 if our_hp_ratio <= 0.45 else 0.70
                    status_cap = min(status_cap, best_progress_weight * recovery_progress_cap)
                elif move_norm == "partingshot":
                    status_cap = min(status_cap, best_progress_weight * 0.88)
                else:
                    status_cap = min(status_cap, best_progress_weight * 0.85)
            if new_weight > status_cap:
                new_weight = status_cap
                reason = "boosted_threat_suppress_passive"

        elif is_switch:
            if force_switch:
                pass
            elif healthy_enough_for_reset and best_reset_weight > 0:
                # Prevent switch-looping when a healthy reset move exists now.
                cap = max(best_reset_weight * 0.95, best_attack_weight * 1.05)
                if cap > 0 and new_weight > cap:
                    new_weight = cap
                    reason = "boosted_threat_avoid_switch_loop"
            elif has_viable_progress_line and our_hp_ratio >= 0.40 and boost_level >= 1:
                if switched_last_turn:
                    # Break back-to-back switch chains when we can make progress now.
                    cap = best_progress_weight * (0.85 if boost_level >= 2 else 0.90)
                    if new_weight > cap:
                        new_weight = cap
                        reason = "boosted_threat_break_switch_chain"
                else:
                    # Keep switch options close to proactive lines instead of dominating.
                    cap = best_progress_weight * (0.95 if boost_level >= 2 else 0.98)
                    if new_weight > cap:
                        new_weight = cap
                        reason = "boosted_threat_keep_progress_live"

        elif is_damaging and best_switch_weight <= 0:
            # Last-mon / trapped states: attack instead of hazards/recover spam.
            attack_floor = anchor_weight * 0.90
            if move_norm in PRIORITY_MOVES:
                attack_floor = anchor_weight
            if attack_floor > new_weight:
                new_weight = attack_floor
                reason = "boosted_threat_no_switch_attack_now"
        elif is_strong_attack and best_switch_weight > 0 and boost_level >= 1 and not force_switch:
            # Keep at least one meaningful attacking line alive when switches dominate.
            floor = best_switch_weight * (0.92 if boost_level == 1 else 0.96)
            if switched_last_turn:
                floor = best_switch_weight * (0.96 if boost_level == 1 else 1.02)
            if floor > new_weight:
                new_weight = floor
                reason = "boosted_threat_keep_attack_live"
        elif is_strong_attack and switched_last_turn and has_viable_progress_line and not force_switch:
            # When we switched last turn and are still under threat, keep attacking
            # lines competitive so we do not oscillate forever.
            floor = best_progress_weight * 1.05
            if floor > new_weight:
                new_weight = floor
                reason = "boosted_threat_force_progress_attack"

        # Unaware hold mode:
        # If we pivoted to an Unaware wall against a boosted threat, avoid
        # immediately switching back out unless the line is truly unsafe.
        if unaware_hold_mode:
            if (
                is_switch
                and has_viable_progress_line
                and our_hp_ratio >= 0.45
            ):
                hold_cap = best_progress_weight * (0.78 if switched_last_turn else 0.86)
                if new_weight > hold_cap:
                    new_weight = hold_cap
                    reason = "unaware_hold_avoid_immediate_switch"
            elif (is_reset or is_strong_attack) and best_switch_weight > 0:
                hold_floor = best_switch_weight * (0.98 if switched_last_turn else 0.90)
                if hold_floor > new_weight:
                    new_weight = hold_floor
                    reason = "unaware_hold_keep_progress"
            elif is_status and move_norm in recovery_moves_norm and best_switch_weight > 0:
                stabilizing_recovery = _is_stabilizing_recovery_line(
                    move_norm,
                    move_data,
                    our_hp_ratio,
                    incoming_pressure,
                )
                if stabilizing_recovery and our_hp_ratio <= 0.72:
                    hold_floor = best_switch_weight * 0.92
                    if hold_floor > new_weight:
                        new_weight = hold_floor
                        reason = "unaware_hold_stabilize_and_stay"

        if reason is not None and trace_events is not None:
            trace_events.append(
                {
                    "type": "penalty" if new_weight < weight else "boost",
                    "source": "threat_switch",
                    "move": move,
                    "reason": reason,
                    "before": weight,
                    "after": new_weight,
                }
            )
        adjusted[move] = new_weight
    return adjusted


def apply_switch_chain_progress_bias(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """
    Prevent long switch loops when we just switched and have a viable progress line.

    This is intentionally format-agnostic and applies outside explicit boosted-threat
    scenarios because non-boosted pivot wars can still stall bot progress.
    """
    if battle is None or getattr(battle, "force_switch", False):
        return policy

    turn_number = int(getattr(battle, "turn", 0) or 0)
    if turn_number <= 1:
        return policy

    user = getattr(battle, "user", None)
    last_selected_raw = str(
        getattr(getattr(user, "last_selected_move", None), "move", "") or ""
    ).lower()
    last_used_raw = str(
        getattr(getattr(user, "last_used_move", None), "move", "") or ""
    ).lower()
    switched_last_turn = (
        last_selected_raw.startswith("switch ") or last_used_raw.startswith("switch ")
    )
    if not switched_last_turn:
        return policy

    our_active = getattr(user, "active", None)
    our_hp_ratio = 1.0
    if our_active is not None:
        our_hp_ratio = getattr(our_active, "hp", 1) / max(getattr(our_active, "max_hp", 1), 1)

    boosted_threat = bool(
        ability_state is not None and getattr(ability_state, "opponent_has_offensive_boost", False)
    )
    unaware_hold = bool(
        boosted_threat and _is_unaware_like(our_active) and our_hp_ratio >= 0.40
    )
    if boosted_threat and our_hp_ratio < 0.35:
        # If we're very low into a boosted threat, preserve emergency switching.
        return policy

    best_switch_weight = 0.0
    best_progress_move = None
    best_progress_weight = 0.0
    best_fallback_non_switch_move = None
    best_fallback_non_switch_weight = 0.0

    for move, weight in policy.items():
        if weight <= 0:
            continue
        if move.startswith("switch "):
            best_switch_weight = max(best_switch_weight, weight)
            continue

        is_progress = _is_meaningful_progress_move(
            move,
            battle,
            ability_state,
            include_status_pivots=not boosted_threat,
        )
        if is_progress and weight > best_progress_weight:
            best_progress_weight = weight
            best_progress_move = move

        if weight > best_fallback_non_switch_weight:
            best_fallback_non_switch_weight = weight
            best_fallback_non_switch_move = move

    if best_switch_weight <= 0:
        return policy

    # Prefer progress moves, but fall back to any non-switch if that's all we have.
    if best_progress_move is None:
        best_progress_move = best_fallback_non_switch_move
        best_progress_weight = best_fallback_non_switch_weight

    if not best_progress_move or best_progress_weight <= 0:
        return policy

    ratio = best_progress_weight / best_switch_weight
    min_ratio = 0.30 if boosted_threat else 0.20
    if unaware_hold:
        min_ratio = min(min_ratio, 0.12)
    if ratio < min_ratio:
        return policy

    adjusted = dict(policy)
    if unaware_hold:
        switch_cap = best_progress_weight * 0.84
    else:
        switch_cap = best_progress_weight * (0.92 if boosted_threat else 0.98)
    progress_floor = switch_cap * 1.03
    if progress_floor > adjusted[best_progress_move]:
        old = adjusted[best_progress_move]
        adjusted[best_progress_move] = progress_floor
        if trace_events is not None:
            trace_events.append(
                {
                    "type": "boost",
                    "source": "switch_chain",
                    "move": best_progress_move,
                    "reason": "progress_after_switch_chain",
                    "before": old,
                    "after": adjusted[best_progress_move],
                }
            )

    for move, weight in policy.items():
        if not move.startswith("switch ") or weight <= 0:
            continue
        if weight > switch_cap:
            adjusted[move] = switch_cap
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "penalty",
                        "source": "switch_chain",
                        "move": move,
                        "reason": "cap_switch_after_switch_chain",
                        "before": weight,
                        "after": switch_cap,
                    }
                )

    return adjusted


def apply_conversion_progress_bias(
    policy: dict[str, float],
    battle: Battle | None,
    ability_state: OpponentAbilityState | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """
    When we are materially ahead, prefer converting advantage with direct progress
    instead of extra passive/switch cycles.
    """
    if battle is None or getattr(battle, "force_switch", False):
        return policy
    if battle.user.active is None:
        return policy

    advantage_score, ahead = _estimate_board_advantage(battle, ability_state)
    if not ahead:
        return policy

    our_active = battle.user.active
    our_hp_ratio = getattr(our_active, "hp", 1) / max(getattr(our_active, "max_hp", 1), 1)
    opp_boost_level = 0
    if ability_state is not None:
        opp_boost_level = max(
            int(getattr(ability_state, "opponent_attack_boost", 0) or 0),
            int(getattr(ability_state, "opponent_spa_boost", 0) or 0),
        )
    if opp_boost_level >= 2 and our_hp_ratio < 0.55:
        # Do not force conversion when the board is volatile and we are low.
        return policy

    incoming_pressure = None
    try:
        incoming_pressure = float(_eval_opponent_best_damage(battle))
    except Exception:
        incoming_pressure = None

    turn_number = int(getattr(battle, "turn", 0) or 0)
    last_selected_raw = str(
        getattr(getattr(getattr(battle, "user", None), "last_selected_move", None), "move", "") or ""
    ).lower()
    switched_last_turn = turn_number > 1 and last_selected_raw.startswith("switch ")

    best_progress_weight = 0.0
    best_switch_weight = 0.0
    for move, weight in policy.items():
        if weight <= 0:
            continue
        if move.startswith("switch "):
            best_switch_weight = max(best_switch_weight, weight)
            continue
        if _is_meaningful_progress_move(
            move,
            battle,
            ability_state,
            include_status_pivots=False,
            incoming_pressure=incoming_pressure,
        ):
            best_progress_weight = max(best_progress_weight, weight)

    if best_progress_weight <= 0:
        return policy

    switch_cap = best_progress_weight * (0.88 if advantage_score >= 1.35 else 0.94)
    if switched_last_turn:
        switch_cap *= 0.93
    if incoming_pressure is not None and incoming_pressure >= max(0.42, our_hp_ratio * 0.75):
        # Keep emergency switching mostly intact under meaningful immediate pressure.
        switch_cap = max(switch_cap, best_switch_weight * 0.95)

    adjusted = dict(policy)
    for move, weight in policy.items():
        if weight <= 0:
            continue

        move_name = move.split(":")[-1] if ":" in move else move
        move_norm = normalize_name(move_name)
        move_data = all_move_json.get(move_norm, all_move_json.get(move_name, {}))
        cat = move_data.get(constants.CATEGORY)

        new_weight = weight
        reason = None

        if move.startswith("switch ") and our_hp_ratio >= 0.35:
            if weight > switch_cap:
                new_weight = switch_cap
                reason = "ahead_convert_cap_switch"
        elif cat == constants.STATUS:
            if move_norm in RECOVERY_MOVES_NORM:
                stabilizing = _is_stabilizing_recovery_line(
                    move_norm,
                    move_data,
                    our_hp_ratio,
                    incoming_pressure,
                )
                if not (stabilizing and our_hp_ratio <= 0.58):
                    status_cap = best_progress_weight * (
                        0.78 if our_hp_ratio <= 0.45 else 0.66
                    )
                    if weight > status_cap:
                        new_weight = status_cap
                        reason = "ahead_convert_cap_recovery"
            elif move_norm in HAZARD_SETTING_MOVES and _hazard_move_can_progress(move_norm, battle):
                early_hazard_turn = turn_number <= 4 and (
                    ability_state is None
                    or (
                        int(getattr(ability_state, "opponent_hazard_layers", 0) or 0) <= 0
                        and int(getattr(ability_state, "opponent_alive_count", 6) or 6) >= 4
                    )
                )
                hazard_cap = best_progress_weight * (0.92 if early_hazard_turn else 0.80)
                if weight > hazard_cap:
                    new_weight = hazard_cap
                    reason = "ahead_convert_cap_hazard"
            elif move_norm in PIVOT_MOVES_NORM:
                pivot_cap = best_progress_weight * 0.90
                if weight > pivot_cap:
                    new_weight = pivot_cap
                    reason = "ahead_convert_cap_pivot"
            else:
                passive_cap = best_progress_weight * 0.72
                if weight > passive_cap:
                    new_weight = passive_cap
                    reason = "ahead_convert_cap_passive"

        adjusted[move] = new_weight
        if reason is not None and trace_events is not None and abs(new_weight - weight) > 1e-9:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "conversion",
                    "move": move,
                    "reason": reason,
                    "before": weight,
                    "after": new_weight,
                }
            )

    return adjusted


def _get_item_probability(pkmn, item_name: str) -> float:
    """Estimate probability that pkmn is holding item_name."""
    if pkmn is None:
        return 0.0

    normalized_item = normalize_name(item_name)
    known_item = normalize_name(pkmn.item) if pkmn.item else ""

    # If item is known, return certainty
    if pkmn.item not in (None, "", "unknownitem", "unknown", "none", constants.UNKNOWN_ITEM):
        return 1.0 if known_item == normalized_item else 0.0

    # If item is explicitly impossible, return 0
    if hasattr(pkmn, "impossible_items") and normalized_item in pkmn.impossible_items:
        return 0.0

    try:
        raw = SmogonSets.get_raw_pkmn_sets_from_pkmn_name(pkmn.name, pkmn.base_name)
    except Exception:
        return 0.0

    for item, prob in raw.get(ITEM_STRING, []):
        if normalize_name(item) == normalized_item:
            return prob
    return 0.0


def _get_status_threat_probability(pkmn) -> float:
    """Estimate probability that opponent has a status-inflicting move."""
    if pkmn is None:
        return 0.0

    # If a status move is revealed, treat as certain.
    revealed = getattr(pkmn, "moves", []) or []
    for mv in revealed:
        name = mv.name if hasattr(mv, "name") else str(mv)
        if normalize_name(name) in STATUS_INFLICTING_MOVES:
            return 1.0

    # Fall back to move-set distributions from TeamDatasets (replay moves).
    try:
        movesets = (
            TeamDatasets.raw_pkmn_moves.get(pkmn.name)
            or TeamDatasets.raw_pkmn_moves.get(getattr(pkmn, "base_name", pkmn.name))
        )
    except Exception:
        movesets = None

    if not movesets:
        return 0.0

    total = sum(ms.count for ms in movesets)
    if total <= 0:
        return 0.0

    status_count = 0
    for moveset in movesets:
        if any(normalize_name(mv) in STATUS_INFLICTING_MOVES for mv in moveset.moves):
            status_count += moveset.count

    return status_count / total


def _get_item_removal_threat_probability(pkmn) -> float:
    """Estimate probability that opponent has Knock Off or another item-removal move."""
    if pkmn is None:
        return 0.0

    # If an item-removal move is revealed, treat as certain.
    revealed = getattr(pkmn, "moves", []) or []
    for mv in revealed:
        name = mv.name if hasattr(mv, "name") else str(mv)
        if normalize_name(name) in ITEM_REMOVAL_MOVES:
            return 1.0

    # Fall back to move-set distributions from TeamDatasets.
    try:
        movesets = (
            TeamDatasets.raw_pkmn_moves.get(pkmn.name)
            or TeamDatasets.raw_pkmn_moves.get(getattr(pkmn, "base_name", pkmn.name))
        )
    except Exception:
        movesets = None

    if not movesets:
        # Knock Off is extremely common in gen9ou — default to moderate threat
        return 0.5

    total = sum(ms.count for ms in movesets)
    if total <= 0:
        return 0.5

    knock_count = 0
    for moveset in movesets:
        if any(normalize_name(mv) in ITEM_REMOVAL_MOVES for mv in moveset.moves):
            knock_count += moveset.count

    return knock_count / total


def apply_pre_orb_status_safety(
    policy: dict[str, float],
    battle: Battle | None,
    trace_events: list[dict] | None = None,
) -> dict[str, float]:
    """Protect Toxic Orb activation for Poison Heal users (pre-poison).

    Handles two threat types:
    - Status moves (burn/para/sleep before orb activates) — moderate concern
    - Knock Off / item removal (orb gone forever) — catastrophic concern
    """
    if battle is None or battle.user.active is None or battle.opponent.active is None:
        return policy

    our = battle.user.active
    if our.hp <= 0:
        return policy
    if our.status in (POISON, TOXIC):
        # Already poisoned — orb has done its job, item loss doesn't matter
        return policy
    if our.status:
        # Already statused by something else; orb protection is moot.
        return policy

    our_name = normalize_name(our.name)
    ability_norm = normalize_name(our.ability or "")
    if ability_norm and ability_norm != "poisonheal":
        return policy
    if not ability_norm and our_name not in POKEMON_COMMONLY_POISON_HEAL:
        return policy

    item_norm = normalize_name(our.item or "")
    if item_norm and item_norm != "toxicorb":
        return policy
    orb_prob = 1.0 if item_norm == "toxicorb" else _get_item_probability(our, "toxicorb")
    if orb_prob <= 0:
        return policy

    # Evaluate both threat types
    status_threat = _get_status_threat_probability(battle.opponent.active)
    knock_threat = _get_item_removal_threat_probability(battle.opponent.active)

    # Use the stronger threat channel to determine adjustments
    if knock_threat >= status_threat and knock_threat >= PRE_ORB_STATUS_THREAT_MIN_PROB:
        threat = min(knock_threat, 1.0)
        protect_boost = 1.0 + (BOOST_PRE_ORB_PROTECT_KNOCK - 1.0) * threat
        nonprotect_penalty = 1.0 - (1.0 - PENALTY_PRE_ORB_NONPROTECT_KNOCK) * threat
        switch_boost = 1.0 + (BOOST_PRE_ORB_SWITCH_KNOCK - 1.0) * threat
        threat_source = f"knock_off(prob={knock_threat:.2f})"
    elif status_threat >= PRE_ORB_STATUS_THREAT_MIN_PROB:
        threat = min(status_threat, 1.0)
        protect_boost = 1.0 + (BOOST_PRE_ORB_PROTECT - 1.0) * threat
        nonprotect_penalty = 1.0 - (1.0 - PENALTY_PRE_ORB_NONPROTECT) * threat
        switch_boost = 1.0  # no switch boost for status threats
        threat_source = f"status(prob={status_threat:.2f})"
    else:
        return policy

    adjusted = dict(policy)
    changed = False
    for move, weight in policy.items():
        if weight <= 0:
            adjusted[move] = weight
            continue
        move_name = move.split(":")[-1] if ":" in move else move
        if move_name.endswith("-tera"):
            move_name = move_name[:-5]
        move_norm = normalize_name(move_name)

        if move_norm in PROTECT_MOVES:
            new_weight = weight * protect_boost
            adjusted[move] = new_weight
            changed = True
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "boost",
                        "source": "pre_orb_safety",
                        "move": move,
                        "reason": f"pre_orb_safety({threat_source})",
                        "before": weight,
                        "after": new_weight,
                    }
                )
            continue

        if move.startswith("switch "):
            if switch_boost > 1.0:
                new_weight = weight * switch_boost
                adjusted[move] = new_weight
                changed = True
                if trace_events is not None:
                    trace_events.append(
                        {
                            "type": "boost",
                            "source": "pre_orb_safety",
                            "move": move,
                            "reason": f"pre_orb_switch({threat_source})",
                            "before": weight,
                            "after": new_weight,
                        }
                    )
            continue

        new_weight = weight * nonprotect_penalty
        adjusted[move] = new_weight
        changed = True
        if trace_events is not None:
            trace_events.append(
                {
                    "type": "penalty",
                    "source": "pre_orb_safety",
                    "move": move,
                    "reason": f"pre_orb_safety({threat_source})",
                    "before": weight,
                    "after": new_weight,
                }
            )

    if changed:
        logger.info(
            f"Pre-orb safety [{threat_source}]: "
            f"protect_boost={protect_boost:.2f}, nonprotect_penalty={nonprotect_penalty:.2f}"
            + (f", switch_boost={switch_boost:.2f}" if switch_boost > 1.0 else "")
        )

    return adjusted


def _get_switch_likelihood(our_active, target) -> float:
    """Estimate likelihood that target switches into our_active."""
    if our_active is None or target is None:
        return 0.0
    try:
        raw = SmogonSets.get_raw_pkmn_sets_from_pkmn_name(
            our_active.name, getattr(our_active, "base_name", our_active.name)
        )
    except Exception:
        return 0.0

    matchups = raw.get(EFFECTIVENESS, {})
    if not matchups:
        return 0.0
    return float(
        matchups.get(target.name, matchups.get(getattr(target, "base_name", ""), 0.0))
    )


def _get_hazard_pressure(battle: Battle) -> float:
    """Return a simple hazard-pressure signal on opponent side."""
    if battle is None:
        return 0.0
    hazards_present = any(
        [
            battle.opponent.side_conditions[constants.STEALTH_ROCK] > 0,
            battle.opponent.side_conditions[constants.SPIKES] > 0,
            battle.opponent.side_conditions[constants.TOXIC_SPIKES] > 0,
            battle.opponent.side_conditions[constants.STICKY_WEB] > 0,
        ]
    )
    if hazards_present:
        return 1.0

    if battle.user.active is not None:
        hazard_moves_norm = {normalize_name(m) for m in HAZARD_MOVES}
        if any(normalize_name(m.name) in hazard_moves_norm for m in battle.user.active.moves):
            return 0.5
    return 0.0


def apply_preemptive_item_punish(
    policy: dict[str, float],
    battle: Battle | None,
    playstyle: Playstyle,
    decision_profile: DecisionProfile,
) -> dict[str, float]:
    """
    Preemptive Knock Off bonus for high-value switch-ins (gen9ou only).
    Focuses on:
      - Gliscor Toxic Orb denial (pre-poison)
      - Heavy-Duty Boots removal when hazards matter
    """
    if battle is None or battle.pokemon_format != "gen9ou":
        return policy
    if battle.battle_type != BattleType.STANDARD_BATTLE:
        return policy
    if battle.force_switch:
        return policy
    if battle.user.active is None or battle.opponent.active is None:
        return policy
    if "knockoff" not in policy:
        return policy

    knock_weight = policy["knockoff"]
    if knock_weight <= 0:
        return policy

    best_weight = max(policy.values())
    gate_threshold = 0.90 if decision_profile == DecisionProfile.LOW else 0.85
    if knock_weight < best_weight * gate_threshold:
        return policy

    playstyle_cfg = PlaystyleConfig.get_config(playstyle)
    chip_scale = max(0.7, min(1.3, playstyle_cfg.get("chip_damage_multiplier", 1.0)))
    hazard_scale = max(0.7, min(1.3, playstyle_cfg.get("hazard_value_multiplier", 1.0)))

    preemptive_scores = []

    # 1) Gliscor Toxic Orb denial (only if not already poisoned)
    gliscor = next(
        (p for p in battle.opponent.reserve if p.name == "gliscor" and p.hp > 0),
        None,
    )
    if gliscor and gliscor.status not in (POISON, TOXIC):
        # If ability known and not poison heal, skip denial logic
        if not gliscor.ability or gliscor.ability == "poisonheal":
            toxic_orb_prob = _get_item_probability(gliscor, "toxicorb")
            switch_likelihood = _get_switch_likelihood(battle.user.active, gliscor)
            gliscor_score = toxic_orb_prob * switch_likelihood * chip_scale
            if gliscor_score > 0:
                preemptive_scores.append(("gliscor-toxic-orb", gliscor_score))

    # 2) Heavy-Duty Boots removal when hazards matter
    hazard_pressure = _get_hazard_pressure(battle)
    if hazard_pressure > 0:
        best_boots_score = 0.0
        best_boots_target = None
        for p in battle.opponent.reserve:
            if p.hp <= 0:
                continue
            boots_prob = _get_item_probability(p, "heavydutyboots")
            if boots_prob <= 0:
                continue
            switch_likelihood = _get_switch_likelihood(battle.user.active, p)
            score = boots_prob * switch_likelihood * hazard_pressure * hazard_scale
            if score > best_boots_score:
                best_boots_score = score
                best_boots_target = p.name
        if best_boots_score > 0 and best_boots_target:
            preemptive_scores.append(
                (f"boots-removal({best_boots_target})", best_boots_score)
            )

    if not preemptive_scores:
        return policy

    # Pick the strongest preemptive signal
    reason, best_score = max(preemptive_scores, key=lambda x: x[1])

    max_bonus = 0.12 if decision_profile == DecisionProfile.LOW else 0.22
    bonus = max_bonus * min(best_score, 1.0)
    if bonus <= 0:
        return policy

    policy = dict(policy)
    policy["knockoff"] = knock_weight * (1.0 + bonus)
    logger.info(
        f"Preemptive Knock Off bonus ({reason}): {knock_weight:.3f} -> "
        f"{policy['knockoff']:.3f} (bonus={bonus:.3f})"
    )
    return policy


def apply_team_strategy_bias(
    policy: dict[str, float],
    battle: Battle | None,
    team_plan: TeamAnalysis | None,
    playstyle: Playstyle,
) -> dict[str, float]:
    """Apply playstyle- and team-plan-based move biases."""
    if battle is None or team_plan is None:
        return policy
    if battle.user.active is None:
        return policy

    cfg = PlaystyleConfig.get_config(playstyle)
    hazard_mult = cfg.get("hazard_value_multiplier", 1.0)
    recovery_mult = cfg.get("recovery_value_multiplier", 1.0)
    setup_mult = cfg.get("setup_value_multiplier", 1.0)
    pivot_mult = cfg.get("pivot_value_multiplier", 1.0)
    damage_mult = cfg.get("damage_value_multiplier", 1.0)
    chip_mult = cfg.get("chip_damage_multiplier", 1.0)

    # Hazard state
    hazards_on_opp = any(
        [
            battle.opponent.side_conditions[constants.STEALTH_ROCK] > 0,
            battle.opponent.side_conditions[constants.SPIKES] > 0,
            battle.opponent.side_conditions[constants.TOXIC_SPIKES] > 0,
            battle.opponent.side_conditions[constants.STICKY_WEB] > 0,
        ]
    )
    hazards_on_us = any(
        [
            battle.user.side_conditions[constants.STEALTH_ROCK] > 0,
            battle.user.side_conditions[constants.SPIKES] > 0,
            battle.user.side_conditions[constants.TOXIC_SPIKES] > 0,
            battle.user.side_conditions[constants.STICKY_WEB] > 0,
        ]
    )

    opponent_has_gholdengo = any(
        p.name == "gholdengo"
        for p in battle.opponent.reserve + ([battle.opponent.active] if battle.opponent.active else [])
        if p is not None and p.hp > 0
    )
    our_has_gholdengo = "gholdengo" in team_plan.hazard_setters or any(
        p.name == "gholdengo" for p in battle.user.reserve + ([battle.user.active] if battle.user.active else [])
        if p is not None
    )

    hazard_moves_norm = {normalize_name(m) for m in HAZARD_MOVES}
    removal_moves_norm = {normalize_name(m) for m in REMOVAL_MOVES}
    screen_moves_norm = {normalize_name(m) for m in SCREEN_MOVES}
    recovery_moves_norm = {normalize_name(m) for m in RECOVERY_MOVES}
    setup_moves_norm = {normalize_name(m) for m in SETUP_MOVES}
    pivot_moves_norm = {normalize_name(m) for m in PIVOT_MOVES}

    active_name = battle.user.active.name if battle.user.active else ""
    active_is_wincon = active_name in team_plan.wincons
    opponent = battle.opponent.active
    switch_tendency = 0.0
    if battle.opponent is not None:
        opp_name = getattr(battle.opponent, "account_name", None) or getattr(battle.opponent, "name", None)
        if opp_name:
            switch_tendency = OPPONENT_MODEL.get_switch_tendency(opp_name)
    threat_category = None
    if opponent is not None:
        try:
            threat_category = get_threat_category(opponent.name)
        except Exception:
            threat_category = None
    opp_boosts = getattr(opponent, "boosts", {}) or {} if opponent is not None else {}
    opp_offensive_boost = max(
        int(opp_boosts.get(constants.ATTACK, 0) or 0),
        int(opp_boosts.get(constants.SPECIAL_ATTACK, 0) or 0),
    )
    # When the opponent is already boosted, avoid late-stage style multipliers
    # re-inflating passive lines that earlier threat layers suppressed.
    suppress_passive_style_boosts = opp_offensive_boost >= 1

    adjusted = {}
    for move, weight in policy.items():
        if weight <= 0:
            adjusted[move] = weight
            continue

        new_weight = weight
        is_switch = move.startswith("switch ")
        base_move = move.split()[-1] if is_switch else move
        move_name = normalize_name(base_move)

        # Early-game hazard urgency for FAT/STALL teams
        # Human stall players set rocks in the first 1-3 turns almost always
        is_early_game = hasattr(battle, 'turn') and isinstance(battle.turn, int) and battle.turn <= 3
        if move_name == "stealthrock" and is_early_game and not hazards_on_opp:
            if playstyle in (Playstyle.FAT, Playstyle.STALL):
                # Strong boost: rocks are the foundation of chip-based strategies
                early_hazard_boost = 3.0 if battle.turn <= 1 else 2.5 if battle.turn <= 2 else 2.0
                new_weight *= early_hazard_boost
            elif playstyle in (Playstyle.BALANCE, Playstyle.BULKY_OFFENSE):
                # Moderate boost for non-HO teams
                new_weight *= 1.8

        # Apply style multipliers
        if move_name in hazard_moves_norm:
            if suppress_passive_style_boosts:
                new_weight *= 0.60
            else:
                if not hazards_on_opp or our_has_gholdengo:
                    new_weight *= hazard_mult * chip_mult
                if switch_tendency > 0.5:
                    new_weight *= 1.0 + min((switch_tendency - 0.5) * 0.3, 0.2)
        if move_name in screen_moves_norm:
            if playstyle == Playstyle.HYPER_OFFENSE:
                new_weight *= 1.3
        if move_name in removal_moves_norm:
            if hazards_on_us:
                new_weight *= hazard_mult
            if opponent_has_gholdengo and move_name in {"defog", "mortalspin"}:
                new_weight *= 0.5
            if opponent_has_gholdengo and move_name == "rapidspin":
                new_weight *= 0.7
        if move_name in recovery_moves_norm:
            if suppress_passive_style_boosts:
                active = battle.user.active
                hp_ratio = 1.0
                if active is not None and active.max_hp > 0:
                    hp_ratio = active.hp / active.max_hp
                # Keep emergency heals available at very low HP, but otherwise
                # avoid over-prioritizing recovery into boosted threats.
                if hp_ratio <= 0.25:
                    new_weight *= 0.95
                else:
                    new_weight *= 0.55
            else:
                new_weight *= recovery_mult
            # FAT/STALL: tempo-aware recovery
            # Recover aggressively when threatened, but don't waste free turns healing
            if playstyle in (Playstyle.FAT, Playstyle.STALL) and not suppress_passive_style_boosts:
                active = battle.user.active
                if active and active.max_hp > 0:
                    hp_ratio = active.hp / active.max_hp
                    # Estimate if we're threatened (opponent can 2HKO)
                    _threatened = False
                    if opponent is not None:
                        opp_moves_list = getattr(opponent, "moves", []) or []
                        for om in opp_moves_list:
                            om_name = om.name if hasattr(om, "name") else str(om)
                            om_data = all_move_json.get(om_name, {})
                            bp = om_data.get(constants.BASE_POWER, 0)
                            if bp > 0:
                                _threatened = True
                                break
                        if not _threatened and opponent.types:
                            _threatened = True  # assume they have attacks
                    if hp_ratio <= 0.4:
                        if _threatened:
                            new_weight *= 2.5  # Heal: critically low AND threatened
                        else:
                            new_weight *= 1.3  # Low but safe: mild recovery urge
                    elif hp_ratio <= 0.6:
                        if _threatened:
                            new_weight *= 1.8  # Moderate heal when threatened
                        else:
                            new_weight *= 1.1  # Barely boost if safe
        if move_name in setup_moves_norm:
            if suppress_passive_style_boosts:
                new_weight *= 0.55
            else:
                new_weight *= setup_mult
                if active_is_wincon:
                    new_weight *= 1.1
        if move_name in pivot_moves_norm:
            if suppress_passive_style_boosts:
                new_weight *= min(pivot_mult, 1.0)
            else:
                new_weight *= pivot_mult

        # Damage move bias
        if move_name in all_move_json:
            cat = all_move_json[move_name].get(constants.CATEGORY)
            if cat in {constants.PHYSICAL, constants.SPECIAL}:
                new_weight *= damage_mult

        # Wincon preservation: discourage switching a healthy wincon without force
        if active_is_wincon and move.startswith("switch "):
            hp_ratio = battle.user.active.hp / max(battle.user.active.max_hp, 1)
            if hp_ratio >= 0.5:
                new_weight *= 0.9

        # Matchup-aware switching (even when not forced)
        if is_switch and opponent is not None:
            target_name = normalize_name(move.split("switch ")[-1])
            target = None
            for p in battle.user.reserve:
                if p.name == target_name:
                    target = p
                    break
            if target is not None:
                score = 0.0
                hp_ratio = target.hp / max(target.max_hp, 1)
                score += hp_ratio
                try:
                    worst = max(
                        type_effectiveness_modifier(t, target.types)
                        for t in opponent.types
                    )
                    score += (2.0 - min(worst, 2.0))
                except Exception:
                    pass
                try:
                    best_off = max(
                        type_effectiveness_modifier(t, opponent.types)
                        for t in target.types
                    )
                    score += best_off
                except Exception:
                    pass
                if threat_category == ThreatCategory.PHYSICAL_ONLY:
                    score += target.stats[constants.DEFENSE] / 200.0
                elif threat_category == ThreatCategory.SPECIAL_ONLY:
                    score += target.stats[constants.SPECIAL_DEFENSE] / 200.0
                else:
                    score += (
                        target.stats[constants.DEFENSE]
                        + target.stats[constants.SPECIAL_DEFENSE]
                    ) / 400.0
                # translate score into a gentle multiplier
                new_weight *= 1.0 + min(max(score - 1.5, -0.3), 0.5) * 0.1

        adjusted[move] = new_weight

    return adjusted


def select_move_from_eval_scores(
    eval_scores: dict[str, float],
    ability_state: OpponentAbilityState | None = None,
    battle: Battle | None = None,
    playstyle: Playstyle | None = None,
    decision_profile: DecisionProfile = DecisionProfile.DEFAULT,
    trace: dict | None = None,
) -> str:
    """Select a move from a policy (MCTS or eval), applying penalty layers."""
    trace_events = []
    pre_penalty_scores = dict(eval_scores)

    blended_policy = dict(eval_scores)

    blended_policy = apply_heuristic_bias(
        blended_policy,
        battle,
        ability_state,
        trace_events=trace_events,
    )

    # Hard filter: remove moves that are blocked by opponent abilities
    if ability_state:
        blended_policy = filter_blocked_moves(
            blended_policy,
            ability_state,
            battle=battle,
            trace_events=trace_events,
        )

    # Apply ability-based penalties before sorting
    if ability_state:
        blended_policy = apply_ability_penalties(
            blended_policy,
            ability_state,
            trace_events=trace_events,
            battle=battle,
        )

    # Apply switch-specific penalties (Phase 2.1)
    if ability_state and battle is not None:
        blended_policy = apply_switch_penalties(
            blended_policy,
            battle,
            ability_state,
            playstyle=playstyle,
            trace_events=trace_events,
        )

    # KO-line bias: punish non-damaging moves if a KO line is available
    if ability_state:
        blended_policy = apply_ko_line_bias(
            blended_policy,
            ability_state,
            trace_events=trace_events,
        )

    blended_policy = apply_threat_switch_bias(
        blended_policy,
        battle,
        ability_state,
        trace_events=trace_events,
    )
    blended_policy = apply_hazard_maintenance_bias(
        blended_policy,
        battle,
        ability_state,
        trace_events=trace_events,
    )

    # Pre-orb status safety: protect Toxic Orb activation (Poison Heal)
    if battle is not None:
        blended_policy = apply_pre_orb_status_safety(
            blended_policy,
            battle,
            trace_events=trace_events,
        )

    # Preemptive item-denial bonus (gen9ou only)
    if battle is not None and playstyle is not None:
        blended_policy = apply_preemptive_item_punish(
            blended_policy,
            battle,
            playstyle,
            decision_profile,
        )

    # Team strategy / playstyle bias
    if battle is not None and playstyle is not None:
        team_plan = getattr(battle.user, "team_plan", None)
        blended_policy = apply_team_strategy_bias(
            blended_policy,
            battle,
            team_plan,
            playstyle,
        )
        # NOTE: Removed duplicate apply_threat_switch_bias() call here.
        # Threat safety is already enforced at line 5369 (FIRST PASS).
        # Re-applying here after team_strategy_bias causes override ping-pong
        # where threat logic contradicts earlier decisions. Single-pass is correct.
        # See FOULER_PLAY_MAINTENANCE.md Phase 1.
        blended_policy = apply_hazard_maintenance_bias(
            blended_policy,
            battle,
            ability_state,
            trace_events=trace_events,
        )

    # Final cleanup: down-weight explicitly odd/waste-turn choices.
    blended_policy = apply_oddity_penalties(
        blended_policy,
        battle,
        ability_state,
        trace_events=trace_events,
    )

    # Hard legality filter: never keep switch options when trapped.
    # This prevents illegal /switch submissions in partial-trap turns.
    if battle is not None and not getattr(battle, "force_switch", False):
        trapped = bool(getattr(getattr(battle, "user", None), "trapped", False))
        request = getattr(battle, "request_json", None) or {}
        active = request.get(constants.ACTIVE, []) if isinstance(request, dict) else []
        if active:
            trapped = trapped or bool(
                active[0].get(constants.TRAPPED, False)
                or active[0].get(constants.MAYBE_TRAPPED, False)
            )

        if trapped:
            filtered_policy = {
                move: weight
                for move, weight in blended_policy.items()
                if not move.startswith("switch ")
            }
            if filtered_policy:
                removed = len(blended_policy) - len(filtered_policy)
                if removed > 0:
                    logger.info(
                        "Switch legality filter: trapped state removed %s switch option(s)",
                        removed,
                    )
                    if trace_events is not None:
                        trace_events.append(
                            {
                                "type": "override",
                                "source": "legality_filter",
                                "move": "switch *",
                                "reason": "trapped_state",
                                "before": removed,
                                "after": 0,
                            }
                        )
                blended_policy = filtered_policy

    sorted_policy = sorted(blended_policy.items(), key=lambda x: x[1], reverse=True)

    if trace is not None:
        top_moves = []
        for move, weight in sorted_policy[:5]:
            top_moves.append(
                {
                    "move": move,
                    "eval_weight": weight,
                    "pre_penalty_score": pre_penalty_scores.get(move, 0.0),
                }
            )
        trace["eval"] = {
            "top_moves": top_moves,
            "policy_pre_penalty": pre_penalty_scores,
            "policy_post_penalty": blended_policy,
            "events": trace_events,
        }

    logger.info("All Choices (post-eval, post-penalty):")
    for i, (move, weight) in enumerate(sorted_policy):
        logger.info(f"\t{round(weight * 100, 3)}%: {move}")

    # Immediate survival override:
    # If we are likely to be KOed before acting, favor the best available switch
    # over a close non-switch line (unless the line is itself a viable stall/survival move).
    if (
        battle is not None
        and sorted_policy
        and not getattr(battle, "force_switch", False)
        and not sorted_policy[0][0].startswith("switch ")
    ):
        survival = _assess_immediate_survival_risk(battle)
        if bool(survival.get("risk", False)):
            top_move, top_weight = sorted_policy[0]
            incoming_pressure = None
            try:
                incoming_pressure = float(survival.get("incoming", 0.0))
            except Exception:
                incoming_pressure = None
            if not _is_non_switch_stall_survival_line(
                top_move,
                battle,
                incoming_pressure=incoming_pressure,
            ):
                best_switch = next(
                    ((m, w) for m, w in sorted_policy if m.startswith("switch ") and w > 0),
                    None,
                )
                if best_switch is not None:
                    # Require at least a minimally competitive switch score so we
                    # do not hard-force obviously bad sacks.
                    preserve_ratio = 0.62
                    if best_switch[1] >= top_weight * preserve_ratio:
                        if trace_events is not None:
                            trace_events.append(
                                {
                                    "type": "override",
                                    "source": "survival_preserve",
                                    "move": best_switch[0],
                                    "reason": "immediate_ko_risk_prefer_switch",
                                    "before": top_weight,
                                    "after": best_switch[1],
                                }
                            )
                        logger.info(
                            "Survival override: choosing %s over %s "
                            "(incoming=%.3f hp=%.3f)",
                            best_switch[0],
                            top_move,
                            float(survival.get("incoming", 0.0)),
                            float(survival.get("our_hp_ratio", 1.0)),
                        )
                        return best_switch[0]

    # If a switch is only marginally above the best progress line, prefer the
    # non-switch option to reduce oscillation and preserve tempo.
    if (
        battle is not None
        and sorted_policy
        and not getattr(battle, "force_switch", False)
        and sorted_policy[0][0].startswith("switch ")
    ):
        best_move, best_weight = sorted_policy[0]
        best_non_switch = next(
            ((m, w) for m, w in sorted_policy if not m.startswith("switch ") and w > 0),
            None,
        )
        if best_non_switch is not None:
            tie_ratio = 0.96
            turn_number = int(getattr(battle, "turn", 0) or 0)
            last_selected_raw = str(
                getattr(getattr(getattr(battle, "user", None), "last_selected_move", None), "move", "") or ""
            ).lower()
            advantage_score, ahead = _estimate_board_advantage(battle, ability_state)
            if ahead:
                tie_ratio = min(tie_ratio, 0.88 if advantage_score < 1.35 else 0.84)
            if turn_number > 1 and last_selected_raw.startswith("switch "):
                tie_ratio = min(tie_ratio, 0.84 if ahead else 0.90)
            if best_non_switch[1] >= best_weight * tie_ratio:
                if trace_events is not None:
                    trace_events.append(
                        {
                            "type": "override",
                            "source": "switch_tie_break",
                            "move": best_non_switch[0],
                            "reason": "prefer_non_switch_when_close",
                            "before": best_weight,
                            "after": best_non_switch[1],
                        }
                    )
                logger.info(
                    f"Switch tie-break: choosing {best_non_switch[0]} over {best_move} "
                    f"(ratio={best_non_switch[1] / best_weight:.2f})"
                )
                return best_non_switch[0]

    # Deterministic selection when one move is clearly dominant
    if len(sorted_policy) >= 2:
        best_weight = sorted_policy[0][1]
        second_weight = sorted_policy[1][1]
        dominance_ratio = 1.5
        if decision_profile == DecisionProfile.LOW:
            dominance_ratio = 1.2
        elif decision_profile == DecisionProfile.HIGH:
            dominance_ratio = 1.6

        if second_weight > 0 and best_weight / second_weight >= dominance_ratio:
            logger.info(f"Clear best move: {sorted_policy[0][0]} ({best_weight/second_weight:.1f}x better)")
            return sorted_policy[0][0]

    # Consider moves within a threshold of the best
    highest_percentage = sorted_policy[0][1]
    considered_threshold = 0.90
    if decision_profile == DecisionProfile.LOW:
        considered_threshold = 0.95
    elif decision_profile == DecisionProfile.HIGH:
        considered_threshold = 0.85

    considered = [i for i in sorted_policy if i[1] >= highest_percentage * considered_threshold]
    if (
        battle is not None
        and ability_state is not None
        and ability_state.opponent_active_is_threat
        and not getattr(battle, "force_switch", False)
        and considered
        and all(move.startswith("switch ") for move, _ in considered)
    ):
        progress_pick = None
        for move, weight in sorted_policy:
            if move.startswith("switch "):
                continue
            if _is_meaningful_progress_move(
                move,
                battle,
                ability_state,
                include_status_pivots=False,
            ):
                progress_pick = (move, weight)
                break
        if progress_pick is not None:
            considered.append(progress_pick)
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "override",
                        "source": "threat_considered",
                        "move": progress_pick[0],
                        "reason": "inject_progress_when_considered_all_switches",
                        "before": progress_pick[1],
                        "after": progress_pick[1],
                    }
                )
            logger.info(
                "Threat override: injecting progress move %s into considered pool",
                progress_pick[0],
            )
    logger.info(f"Considered Choices ({int(considered_threshold * 100)}% threshold):")
    for i, policy in enumerate(considered):
        logger.info(f"\t{round(policy[1] * 100, 3)}%: {policy[0]}")

    # If we switched last turn, prefer a considered progress move
    # (damaging or direct reset) over another random switch.
    if battle is not None and not getattr(battle, "force_switch", False):
        turn_number = int(getattr(battle, "turn", 0) or 0)
        last_selected_raw = str(
            getattr(getattr(getattr(battle, "user", None), "last_selected_move", None), "move", "") or ""
        ).lower()
        if turn_number > 1 and last_selected_raw.startswith("switch "):
            progress_choices = []
            for move, weight in considered:
                if move.startswith("switch "):
                    continue
                if _is_meaningful_progress_move(
                    move,
                    battle,
                    ability_state,
                    include_status_pivots=True,
                ):
                    progress_choices.append((move, weight))
            if progress_choices:
                progress_pick = max(progress_choices, key=lambda x: x[1])
                if trace_events is not None:
                    trace_events.append(
                        {
                            "type": "override",
                            "source": "switch_chain",
                            "move": progress_pick[0],
                            "reason": "prefer_progress_after_switch",
                            "before": progress_pick[1],
                            "after": progress_pick[1],
                        }
                    )
                logger.info(f"Switch-chain override: choosing progress move {progress_pick[0]}")
                return progress_pick[0]

    # Conversion lock: if we are ahead and already have the best non-switch line,
    # do not re-randomize into a close defensive switch.
    if battle is not None and considered and not getattr(battle, "force_switch", False):
        advantage_score, ahead = _estimate_board_advantage(battle, ability_state)
        if ahead:
            best_move, best_weight = sorted_policy[0]
            if not best_move.startswith("switch "):
                best_switch = next(
                    ((m, w) for m, w in sorted_policy if m.startswith("switch ") and w > 0),
                    None,
                )
                if best_switch is not None:
                    keep_ratio = 0.94 if advantage_score < 1.35 else 0.90
                    if best_weight >= best_switch[1] * keep_ratio:
                        if trace_events is not None:
                            trace_events.append(
                                {
                                    "type": "override",
                                    "source": "conversion_lock",
                                    "move": best_move,
                                    "reason": "ahead_keep_progress_over_close_switch",
                                    "before": best_switch[1],
                                    "after": best_weight,
                                }
                            )
                        return best_move

    # Low profile should be deterministic to reduce variance/regression noise
    # during ladder grinding and make behavior easier to debug.
    if decision_profile == DecisionProfile.LOW and considered:
        deterministic = max(considered, key=lambda x: x[1])[0]
        return deterministic

    choice = random.choices(considered, weights=[p[1] for p in considered])[0]
    return choice[0]


def _get_time_pressure_level(battle):
    """Returns time pressure level: 0=none, 1=moderate (<60s), 2=critical (<30s), 3=emergency (<15s)"""
    if battle.time_remaining is None:
        return 0
    if battle.time_remaining <= 15:
        return 3
    if battle.time_remaining <= 30:
        return 2
    if battle.time_remaining <= 60:
        return 1
    return 0


def search_time_num_battles_randombattles(battle):
    revealed_pkmn = len(battle.opponent.reserve)
    if battle.opponent.active is not None:
        revealed_pkmn += 1

    opponent_active_num_moves = len(battle.opponent.active.moves)
    pressure = _get_time_pressure_level(battle)

    # Emergency: minimal search
    if pressure >= 3:
        return max(FoulPlayConfig.parallelism, 1), int(FoulPlayConfig.search_time_ms // 4)

    # Critical: reduced search
    if pressure >= 2:
        return max(FoulPlayConfig.parallelism, 1), int(FoulPlayConfig.search_time_ms // 2)

    # Early battle with unknown moves: search many battles shallowly
    if (
        revealed_pkmn <= 3
        and battle.opponent.active.hp > 0
        and opponent_active_num_moves == 0
    ):
        num_battles_multiplier = 2 if pressure >= 1 else 4
        return max(FoulPlayConfig.parallelism, 1) * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms // 2
        )

    else:
        num_battles_multiplier = 1 if pressure >= 1 else 2
        return max(FoulPlayConfig.parallelism, 1) * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )


def search_time_num_battles_standard_battle(battle):
    opponent_active_num_moves = len(battle.opponent.active.moves)
    pressure = _get_time_pressure_level(battle)

    # Emergency: minimal search
    if pressure >= 3:
        return max(FoulPlayConfig.parallelism, 1), int(FoulPlayConfig.search_time_ms // 4)

    # Critical: reduced search
    if pressure >= 2:
        return max(FoulPlayConfig.parallelism, 1), int(FoulPlayConfig.search_time_ms // 2)

    if (
        battle.team_preview
        or (battle.opponent.active.hp > 0 and opponent_active_num_moves == 0)
        or opponent_active_num_moves < 3
    ):
        num_battles_multiplier = 1 if pressure >= 1 else 2
        return max(FoulPlayConfig.parallelism, 1) * num_battles_multiplier, int(
            FoulPlayConfig.search_time_ms
        )
    else:
        return max(FoulPlayConfig.parallelism, 1), FoulPlayConfig.search_time_ms




# Maximum total time budget for a single decision (seconds)
# Pokemon Showdown gives ~150s total per game, or ~45s per turn with timer on
# We need to leave margin for network latency, state processing, etc.
MAX_DECISION_TIME_SECONDS = 20  # Reduced from 30 to avoid timeout losses
# When in time pressure (<60s remaining), use a much tighter budget
MAX_DECISION_TIME_PRESSURE_SECONDS = 6  # Reduced from 8 for more safety margin


def _get_fallback_move(battle: Battle) -> str:
    """
    Emergency fallback: pick the first available move without MCTS.
    Used when MCTS times out to avoid forfeiting the turn.
    """
    # If force_switch is active, we MUST switch - don't try moves
    if battle.force_switch:
        for pkmn in battle.user.reserve:
            if pkmn.hp > 0:
                logger.warning(f"Timeout fallback (force_switch): switching to {pkmn.name}")
                return f"switch {pkmn.name}"
        logger.error("Timeout fallback: force_switch active but no alive reserves!")
        return "switch 1"  # Last resort, server will reject but better than a move

    if battle.user.active is not None:
        best_move = None
        best_score = -1.0
        opponent = battle.opponent.active
        for move in battle.user.active.moves:
            if hasattr(move, "disabled") and move.disabled:
                continue
            if hasattr(move, "current_pp") and move.current_pp <= 0:
                continue
            move_name = move.name if hasattr(move, "name") else str(move)
            score = 0.0
            if move_name in all_move_json and opponent is not None:
                mv = all_move_json[move_name]
                if mv.get(constants.CATEGORY) in {constants.PHYSICAL, constants.SPECIAL}:
                    move_type = mv.get(constants.TYPE)
                    base_power = mv.get(constants.BASE_POWER, 60)
                    if move_type and opponent.types:
                        eff = type_effectiveness_modifier(move_type, opponent.types)
                    else:
                        eff = 1.0
                    stab = 1.0
                    if move_type and battle.user.active.has_type(move_type):
                        stab = 1.5
                    score = eff * stab * base_power
            if score > best_score:
                best_score = score
                best_move = move_name

        if best_move:
            logger.warning(f"Timeout fallback: selecting {best_move}")
            return best_move

    # If no moves available, try switching
    for pkmn in battle.user.reserve:
        if pkmn.hp > 0:
            logger.warning(f"Timeout fallback: switching to {pkmn.name}")
            return f"switch {pkmn.name}"

    logger.error("Timeout fallback: no moves or switches available!")
    return "splash"


def find_best_move(battle: Battle) -> tuple[str, dict]:
    _maybe_hot_reload()
    start_time = time.time()
    if not getattr(battle, "_isolation_copy", False):
        battle = deepcopy(battle)
    trace = build_trace_base(battle)
    trace["battle_type"] = (
        battle.battle_type.name if hasattr(battle.battle_type, "name") else str(battle.battle_type)
    )
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    # Determine time budget
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60
    time_budget = (
        MAX_DECISION_TIME_PRESSURE_SECONDS
        if in_time_pressure
        else MAX_DECISION_TIME_SECONDS
    )

    if in_time_pressure:
        logger.warning(
            f"TIME PRESSURE: {battle.time_remaining}s remaining, "
            f"budget={time_budget}s"
        )

    # Detect opponent's abilities before we start sampling
    # (sampling may change the ability, so check the original battle state)
    ability_state = detect_opponent_abilities(battle)
    trace["ability_state"] = asdict(ability_state)

    # Log detected abilities that will affect move selection
    detected_abilities = []
    # Existing detections
    if ability_state.has_unaware:
        detected_abilities.append("Unaware")
    if ability_state.has_guts_like:
        detected_abilities.append("Guts/Marvel Scale/Quick Feet")
    if ability_state.has_poison_heal:
        detected_abilities.append("Poison Heal")
    if ability_state.has_purifying_salt:
        detected_abilities.append("Purifying Salt")
    if ability_state.has_water_immunity:
        detected_abilities.append("Water Absorb/Storm Drain")
    if ability_state.has_electric_immunity:
        detected_abilities.append("Volt Absorb/Lightning Rod")
    if ability_state.has_flash_fire:
        detected_abilities.append("Flash Fire")
    if ability_state.has_levitate:
        detected_abilities.append("Levitate")
    if ability_state.has_magic_bounce:
        detected_abilities.append("Magic Bounce")
    if ability_state.has_good_as_gold:
        detected_abilities.append("Good as Gold")
    if ability_state.has_competitive_defiant:
        detected_abilities.append("Competitive/Defiant")
    if ability_state.has_focus_sash and ability_state.at_full_hp:
        detected_abilities.append("Focus Sash (full HP)")
    if ability_state.has_phazing:
        detected_abilities.append("Phazer")
    if ability_state.has_substitute:
        detected_abilities.append("Substitute")
    if ability_state.has_contact_punish:
        detected_abilities.append("Contact Punishment")
    if ability_state.has_status:
        detected_abilities.append("Already statused")
    if ability_state.is_choice_locked:
        detected_abilities.append(f"Choice-locked into {ability_state.choice_locked_move}")
    # NEW: Phase 2 - High Priority
    if ability_state.has_contrary:
        detected_abilities.append("Contrary")
    if ability_state.has_sap_sipper:
        detected_abilities.append("Sap Sipper")
    if ability_state.has_sturdy and ability_state.at_full_hp:
        detected_abilities.append("Sturdy (full HP)")
    if ability_state.has_disguise and not ability_state.disguise_broken:
        detected_abilities.append("Disguise (intact)")
    # NEW: Phase 3 - Move Category Immunities
    if ability_state.has_soundproof:
        detected_abilities.append("Soundproof")
    if ability_state.has_bulletproof:
        detected_abilities.append("Bulletproof")
    if ability_state.has_overcoat:
        detected_abilities.append("Overcoat")
    if ability_state.is_grass_type:
        detected_abilities.append("Grass type (powder immune)")
    # NEW: Phase 4-5 - Type/Stat Interactions
    if ability_state.has_earth_eater:
        detected_abilities.append("Earth Eater")
    if ability_state.has_justified:
        detected_abilities.append("Justified")
    if ability_state.has_steam_engine:
        detected_abilities.append("Steam Engine")
    if ability_state.has_wind_rider:
        detected_abilities.append("Wind Rider")
    if ability_state.has_well_baked_body:
        detected_abilities.append("Well-Baked Body")
    # NEW: Phase 6 - Stat Immunity/Reflection
    if ability_state.has_clear_body:
        detected_abilities.append("Clear Body/White Smoke")
    if ability_state.has_mirror_armor:
        detected_abilities.append("Mirror Armor")
    if ability_state.has_supreme_overlord:
        detected_abilities.append("Supreme Overlord")
    # NEW: Phase 7 - Special Interactions
    if ability_state.our_has_prankster and ability_state.opponent_is_dark_type:
        detected_abilities.append("Dark type (Prankster blocked)")
    if ability_state.our_has_mold_breaker:
        detected_abilities.append("(We have Mold Breaker)")
    if ability_state.has_air_balloon:
        detected_abilities.append("Air Balloon")
    if ability_state.has_fluffy:
        detected_abilities.append("Fluffy (Fire 2x)")
    if ability_state.has_dry_skin_fire_weak:
        detected_abilities.append("Dry Skin (Fire 1.25x)")
    if ability_state.has_priority_block:
        detected_abilities.append("Priority blocked (Queenly Majesty/Dazzling/Armor Tail)")
    # NEW: Phase 8 - Weather/Terrain
    if ability_state.weather:
        detected_abilities.append(f"Weather: {ability_state.weather}")
    if ability_state.terrain:
        detected_abilities.append(f"Terrain: {ability_state.terrain}")
    # PHASE 1.1: Positive Boosts
    if ability_state.opponent_used_protect:
        detected_abilities.append("Opponent used Protect (free turn)")
    if ability_state.opponent_used_setup:
        detected_abilities.append("Opponent used setup move")
    if ability_state.opponent_used_damaging_move:
        detected_abilities.append("Opponent used damaging move last turn")
    if ability_state.opponent_hp_percent < 0.25:
        detected_abilities.append(f"Low HP ({int(ability_state.opponent_hp_percent * 100)}%)")
    # PHASE 1.2: Trick Room
    if ability_state.trick_room_active:
        detected_abilities.append(f"Trick Room active ({ability_state.trick_room_turns} turns)")
        if ability_state.our_is_slow:
            detected_abilities.append("(We're slow - TR advantage)")
    # PHASE 1.3: Screens
    screens = []
    if ability_state.opponent_has_reflect:
        screens.append("Reflect")
    if ability_state.opponent_has_light_screen:
        screens.append("Light Screen")
    if ability_state.opponent_has_aurora_veil:
        screens.append("Aurora Veil")
    if screens:
        detected_abilities.append(f"Screens: {', '.join(screens)}")
    # PHASE 1.4: Weather/Terrain Synergies
    if ability_state.we_have_weather_speed:
        detected_abilities.append("(We have weather speed boost)")
    # PHASE 2.1: Switch Evaluation
    if ability_state.our_hazard_layers > 0:
        detected_abilities.append(f"Hazards on our side ({ability_state.our_hazard_layers} layers)")
    if ability_state.opponent_has_defiant_pokemon:
        detected_abilities.append("Opponent has Defiant/Competitive Pokemon")
    # PHASE 2.2: Entry Hazard Calculus
    if ability_state.opponent_sr_up:
        detected_abilities.append("Opponent SR up")
    if ability_state.opponent_has_sr_weak:
        detected_abilities.append("Opponent has SR-weak Pokemon")
    if ability_state.opponent_has_hazard_removal:
        detected_abilities.append("Opponent has hazard removal")
    if ability_state.opponent_has_salt_cure:
        detected_abilities.append("Opponent has Salt Cure")
    # PHASE 2.3: Tera Prediction
    if ability_state.opponent_has_terastallized:
        detected_abilities.append(f"Opponent Terastallized ({ability_state.opponent_tera_type})")
    elif not ability_state.opponent_has_terastallized:
        detected_abilities.append("Opponent can still Tera")
    # PHASE 3.1: Win Condition Awareness
    if ability_state.our_active_is_wincon:
        detected_abilities.append("(Our active is win condition)")
    if ability_state.opponent_active_is_threat:
        detected_abilities.append("Opponent active is THREAT")
    # PHASE 3.3: Momentum Tracking
    if ability_state.momentum_level != "neutral":
        detected_abilities.append(f"Momentum: {ability_state.momentum_level} ({ability_state.momentum:.1f})")
    if ability_state.our_active_is_salt_cured:
        detected_abilities.append("(Our active is Salt Cured)")

    if detected_abilities:
        logger.info(
            f"Opponent's {ability_state.pokemon_name} "
            f"(ability: {ability_state.ability_name}) - "
            f"detected: {', '.join(detected_abilities)}"
        )
    trace["detected_abilities"] = detected_abilities

    ko_line = find_ko_line(battle)
    if ko_line:
        ability_state.ko_line_available = True
        ability_state.ko_line_turns = int(ko_line.get("turns", 0))
        ability_state.ko_line_move = ko_line.get("move", "")
        trace["ko_line"] = ko_line

    # Team analysis (for playstyle + strategy bias)
    team_plan = getattr(battle.user, "team_plan", None)
    if team_plan is None and battle.user.team_dict:
        try:
            team_plan = analyze_team(battle.user.team_dict)
            battle.user.team_plan = team_plan
        except Exception as e:
            logger.warning(f"Failed to analyze team: {e}")
            team_plan = None

    # Resolve playstyle + decision profile (gen9ou only)
    playstyle = _resolve_playstyle(team_plan)
    decision_profile = _select_decision_profile(battle, playstyle)
    trace["playstyle"] = playstyle.value if hasattr(playstyle, "value") else str(playstyle)
    trace["decision_profile"] = (
        decision_profile.value if hasattr(decision_profile, "value") else str(decision_profile)
    )
    if battle.pokemon_format == "gen9ou":
        logger.info(
            f"Decision profile: {decision_profile.value} (playstyle: {playstyle.value})"
        )

    # =========================================================================
    # PHASE 4.1: ENDGAME SOLVER
    # =========================================================================
    # Try to solve simple endgames deterministically before MCTS
    try:
        from fp.search.endgame import is_endgame, solve_endgame

        if is_endgame(battle, ENDGAME_MAX_POKEMON):
            solution = solve_endgame(battle)
            if solution and solution.is_deterministic and solution.best_move:
                logger.info(
                    f"ENDGAME SOLVED: {solution.best_move} "
                    f"(outcome: {solution.expected_outcome:.1%}, {solution.explanation})"
                )
                trace["decision_mode"] = "endgame"
                trace["endgame"] = {
                    "best_move": solution.best_move,
                    "expected_outcome": solution.expected_outcome,
                    "explanation": solution.explanation,
                    "deterministic": True,
                }
                trace["choice"] = solution.best_move
                return solution.best_move, trace
            elif solution:
                logger.info(
                    f"Endgame analysis: {solution.explanation} "
                    f"(outcome: {solution.expected_outcome:.1%}, not deterministic)"
                )
    except Exception as e:
        logger.debug(f"Endgame solver error: {e}")

    # =========================================================================
    # FORCED LINE CHECK
    # =========================================================================
    # Check for forced lines BEFORE MCTS (short-circuits when play is obvious)
    forced = None
    try:
        forced = detect_forced_line(battle)
        if forced and forced.confidence >= 0.90:
            logger.info(
                f"FORCED LINE (high confidence {forced.confidence}): "
                f"{forced.move} - {forced.reason}"
            )
            trace["decision_mode"] = "forced_line"
            trace["forced_line"] = {
                "move": forced.move,
                "confidence": forced.confidence,
                "reason": forced.reason,
                "line_type": forced.line_type,
            }
            trace["choice"] = forced.move
            oddities = detect_odd_move(battle, forced.move, ability_state)
            trace["oddities"] = oddities
            _log_oddities(forced.move, oddities)
            elapsed_total = time.time() - start_time
            trace["decision_time_s"] = round(elapsed_total, 3)
            return forced.move, trace
    except Exception as e:
        logger.debug(f"Forced line check error: {e}")

    # =========================================================================
    # MCTS PRIMARY SEARCH
    # =========================================================================
    logger.info("Searching for a move using MCTS...")

    try:
        # Determine search parameters based on battle state
        if battle.battle_type == BattleType.RANDOM_BATTLE:
            num_battles, search_time_per_battle = search_time_num_battles_randombattles(battle)
            prepare_fn = prepare_random_battles
        elif battle.battle_type == BattleType.BATTLE_FACTORY:
            num_battles, search_time_per_battle = search_time_num_battles_standard_battle(battle)
            prepare_fn = prepare_random_battles
        elif battle.battle_type == BattleType.STANDARD_BATTLE:
            num_battles, search_time_per_battle = search_time_num_battles_standard_battle(battle)
            prepare_fn = prepare_battles
        else:
            num_battles, search_time_per_battle = search_time_num_battles_standard_battle(battle)
            prepare_fn = prepare_battles

        if FoulPlayConfig.max_mcts_battles is not None:
            desired = max(1, FoulPlayConfig.max_mcts_battles)
            if not in_time_pressure or desired <= num_battles:
                num_battles = desired

        # ---- Game-aware time budgeting ----
        # PS gen9ou timer: ~150s total + 60s grace ≈ 210s per game.
        # Instead of burning 3000ms/sample and panicking at <60s, proactively
        # scale per-sample time so we never run the clock down.
        turn_num = battle.turn if isinstance(battle.turn, int) and battle.turn > 0 else 0
        if battle.time_remaining is not None:
            game_remaining_s = battle.time_remaining
        else:
            # Estimate conservatively: assume ~5s spent per turn so far
            game_remaining_s = max(210.0 - turn_num * 5.0, 30.0)
        est_turns_left = max(5, 50 - turn_num)
        # Budget per turn: divide remaining time, leave 2s for overhead
        turn_budget_s = (game_remaining_s / est_turns_left) - 2.0
        if turn_budget_s > 0:
            game_aware_ms = int((turn_budget_s * 1000) / max(num_battles, 1))
            game_aware_ms = max(game_aware_ms, 500)  # Floor: never below 500ms
            if game_aware_ms < search_time_per_battle:
                logger.info(
                    "Game-aware budget: %dms/sample (game_remaining=%.0fs, "
                    "turn=%d, est_left=%d, was %dms)",
                    game_aware_ms, game_remaining_s, turn_num,
                    est_turns_left, search_time_per_battle,
                )
                search_time_per_battle = game_aware_ms

        # Invest more in high-stakes turns (but respect game-aware cap)
        high_stakes = ability_state.opponent_active_is_threat or ability_state.ko_line_available
        if high_stakes and not in_time_pressure:
            boosted = int(search_time_per_battle * 1.5)
            boosted = min(boosted, FoulPlayConfig.search_time_ms * 2)
            # Don't let high-stakes boost exceed game-aware budget
            if turn_budget_s > 0:
                max_high_stakes_ms = int((turn_budget_s * 1000) / max(num_battles, 1))
                boosted = min(boosted, max_high_stakes_ms)
            search_time_per_battle = boosted

        try:
            sampled_battles = prepare_fn(battle, num_battles)
        except Exception as e:
            logger.warning(f"Battle sampling failed, using original: {e}")
            sampled_battles = [(battle, 1.0)]

        # Check time budget
        elapsed = time.time() - start_time
        remaining_budget = time_budget - elapsed - 2.0
        if remaining_budget <= 0:
            logger.warning("No time left for MCTS, using eval fallback")
            sampled_battles = []  # Signal to skip MCTS

        # Cap search time to fit within budget
        if sampled_battles and remaining_budget > 0:
            max_per_battle_ms = int(
                (remaining_budget * 1000) / max(len(sampled_battles), 1)
            )
            if max_per_battle_ms < search_time_per_battle:
                logger.info(
                    f"Reducing search time from {search_time_per_battle}ms to "
                    f"{max_per_battle_ms}ms to fit time budget"
                )
                search_time_per_battle = max(max_per_battle_ms, 10)

        trace["search"] = {
            "num_battles": len(sampled_battles),
            "search_time_ms": search_time_per_battle,
            "time_budget_s": time_budget,
        }
        logger.info(
            "Sampling {} simulated battles (MCTS) at {}ms each".format(
                len(sampled_battles), search_time_per_battle
            )
        )

        mcts_policy = {}
        mcts_meta = {}
        if sampled_battles:
            mcts_policy, mcts_meta = _run_mcts_policy_pass(
                sampled_battles,
                per_sample_ms=search_time_per_battle,
                max_samples=num_battles,
            )
            trace["mcts_meta"] = mcts_meta

        if mcts_policy:
            # Apply forced line bias to MCTS policy
            if forced and forced.confidence >= 0.70:
                if forced.move in mcts_policy:
                    boost = 1.0 + forced.confidence
                    mcts_policy[forced.move] *= boost
                    logger.info(
                        f"Forced line bias: {forced.move} boosted {boost:.2f}x "
                        f"(confidence {forced.confidence})"
                    )
                trace["forced_line_bias"] = {
                    "move": forced.move,
                    "confidence": forced.confidence,
                    "applied": forced.move in mcts_policy,
                }

            trace["mcts_policy_raw"] = dict(mcts_policy)

            choice = select_move_from_eval_scores(
                mcts_policy,
                ability_state=ability_state,
                battle=battle,
                playstyle=playstyle,
                decision_profile=decision_profile,
                trace=trace,
            )

            elapsed_total = time.time() - start_time
            logger.info(f"Choice: {choice} (decided in {elapsed_total:.1f}s)")
            trace["decision_mode"] = "mcts"
            trace["choice"] = choice
            oddities = detect_odd_move(battle, choice, ability_state)
            trace["oddities"] = oddities
            _log_oddities(choice, oddities)
            trace["decision_time_s"] = round(elapsed_total, 3)
            return choice, trace

        # MCTS failed or no time — fall back to 1-ply eval
        logger.warning("MCTS produced no policy, falling back to 1-ply eval")
        eval_scores = {}
        total_weight = 0.0
        eval_samples = sampled_battles if sampled_battles else [(battle, 1.0)]
        for sampled_battle, weight in eval_samples:
            try:
                scores = evaluate_position(sampled_battle)
                for move, score in scores.items():
                    eval_scores[move] = eval_scores.get(move, 0.0) + score * weight
                total_weight += weight
            except Exception as e:
                logger.warning(f"Eval failed for sample: {e}")

        if total_weight > 0:
            eval_scores = {k: v / total_weight for k, v in eval_scores.items()}

        if not eval_scores:
            eval_scores = evaluate_position(battle)

        if not eval_scores:
            fallback = _get_fallback_move(battle)
            trace["decision_mode"] = "fallback"
            trace["fallback_reason"] = "eval_empty"
            trace["choice"] = fallback
            oddities = detect_odd_move(battle, fallback, ability_state)
            trace["oddities"] = oddities
            _log_oddities(fallback, oddities)
            return fallback, trace

        # Apply forced line bias to eval scores
        if forced and forced.confidence >= 0.70:
            if forced.move in eval_scores:
                boost = 1.0 + forced.confidence
                eval_scores[forced.move] *= boost
                logger.info(
                    f"Forced line bias (eval fallback): {forced.move} boosted "
                    f"{boost:.2f}x (confidence {forced.confidence})"
                )

        trace["eval_scores_raw"] = dict(eval_scores)

        choice = select_move_from_eval_scores(
            eval_scores,
            ability_state=ability_state,
            battle=battle,
            playstyle=playstyle,
            decision_profile=decision_profile,
            trace=trace,
        )

        elapsed_total = time.time() - start_time
        logger.info(f"Choice: {choice} (decided in {elapsed_total:.1f}s)")
        trace["decision_mode"] = "eval_fallback"
        trace["choice"] = choice
        oddities = detect_odd_move(battle, choice, ability_state)
        trace["oddities"] = oddities
        _log_oddities(choice, oddities)
        trace["decision_time_s"] = round(elapsed_total, 3)
        return choice, trace

    except Exception as e:
        logger.error(f"MCTS search failed: {e}", exc_info=True)
        fallback = _get_fallback_move(battle)
        trace["decision_mode"] = "fallback"
        trace["fallback_reason"] = "mcts_exception"
        trace["choice"] = fallback
        oddities = detect_odd_move(battle, fallback, ability_state)
        trace["oddities"] = oddities
        _log_oddities(fallback, oddities)
        return fallback, trace
