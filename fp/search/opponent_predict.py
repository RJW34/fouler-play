"""
Opponent Response Prediction for Fouler-Play

Predicts whether the opponent will stay or switch, and if switching,
which Pokemon is most likely to come in. Used by eval.py to make the
minimax assumption smarter than "opponent always clicks strongest attack."

Signals used for switch prediction:
- Bad matchup (they can't damage us, we threaten them)
- Low HP with reserves alive
- All revealed moves resisted by us
- Counter-signals: boosted (invested, won't leave), trapped

Signals used for switch-in prediction:
- Type matchup vs our STAB types
- HP remaining
- Hazard cost on their side
- Can threaten us with revealed moves
"""

import logging
from dataclasses import dataclass
from typing import Optional

import constants
from fp.battle import Battle
from fp.helpers import POKEMON_TYPE_INDICES, normalize_name, type_effectiveness_modifier
from data import all_move_json

logger = logging.getLogger(__name__)

_VALID_TYPES = set(POKEMON_TYPE_INDICES)

# Trapping abilities and volatile statuses
_TRAPPING_VOLATILES = {"partiallytrapped", "trapped", "cantflee"}
_TRAPPING_ABILITIES = {"shadowtag", "arenatrap", "magnetpull"}


def _normalize_type_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = normalize_name(value)
    if normalized in _VALID_TYPES:
        return normalized
    return None


def _sanitize_type_list(values) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    for value in values:
        normalized = _normalize_type_name(value)
        if normalized:
            cleaned.append(normalized)
    return cleaned


def _get_effective_types(pokemon) -> list[str]:
    base_types = _sanitize_type_list(getattr(pokemon, "types", []) or [])
    tera_type = _normalize_type_name(getattr(pokemon, "tera_type", None))
    if getattr(pokemon, "terastallized", False) and tera_type:
        return [tera_type]
    return base_types


def _estimate_damage_ratio(attacker, defender, move_name: str) -> float:
    """Estimate damage as fraction of defender's max HP (simplified)."""
    if attacker is None or defender is None:
        return 0.0

    move_data = all_move_json.get(move_name, {})
    base_power = move_data.get(constants.BASE_POWER, 0)
    category = move_data.get(constants.CATEGORY, "Status")
    move_type = _normalize_type_name(move_data.get(constants.TYPE, "normal")) or "normal"

    if category not in (constants.PHYSICAL, constants.SPECIAL):
        return 0.0

    defender_max_hp = max(float(getattr(defender, "max_hp", 1) or 1), 1.0)
    defender_types = _get_effective_types(defender)
    effectiveness = type_effectiveness_modifier(move_type, defender_types)
    if effectiveness == 0:
        return 0.0

    # Handle fixed-damage moves
    fixed_damage = move_data.get("damage")
    if isinstance(fixed_damage, (int, float)) and fixed_damage > 0:
        return min(float(fixed_damage) / defender_max_hp, 1.0)
    if isinstance(fixed_damage, str) and normalize_name(fixed_damage) == "level":
        level = float(getattr(attacker, "level", 100) or 100)
        return min(level / defender_max_hp, 1.0)

    if base_power == 0:
        return 0.0

    if category == constants.PHYSICAL:
        atk = attacker.stats.get(constants.ATTACK, 100) if isinstance(attacker.stats, dict) else 100
        def_ = defender.stats.get(constants.DEFENSE, 100) if isinstance(defender.stats, dict) else 100
        atk_boost = (getattr(attacker, "boosts", {}) or {}).get(constants.ATTACK, 0)
        def_boost = (getattr(defender, "boosts", {}) or {}).get(constants.DEFENSE, 0)
    else:
        atk = attacker.stats.get(constants.SPECIAL_ATTACK, 100) if isinstance(attacker.stats, dict) else 100
        def_ = defender.stats.get(constants.SPECIAL_DEFENSE, 100) if isinstance(defender.stats, dict) else 100
        atk_boost = (getattr(attacker, "boosts", {}) or {}).get(constants.SPECIAL_ATTACK, 0)
        def_boost = (getattr(defender, "boosts", {}) or {}).get(constants.SPECIAL_DEFENSE, 0)

    if atk_boost > 0:
        atk *= (2 + atk_boost) / 2
    elif atk_boost < 0:
        atk *= 2 / (2 - atk_boost)
    if def_boost > 0:
        def_ *= (2 + def_boost) / 2
    elif def_boost < 0:
        def_ *= 2 / (2 - def_boost)

    attacker_types = _sanitize_type_list(getattr(attacker, "types", []) or [])
    attacker_tera = _normalize_type_name(getattr(attacker, "tera_type", None))
    if getattr(attacker, "terastallized", False) and attacker_tera:
        if attacker_tera not in attacker_types:
            attacker_types.append(attacker_tera)
    stab = 1.5 if move_type in attacker_types else 1.0

    damage = (((2 * 100 / 5 + 2) * base_power * atk / def_) / 50 + 2) * effectiveness * stab
    return damage / defender_max_hp


def _get_usable_moves(pokemon) -> list[str]:
    if pokemon is None:
        return []
    moves = []
    for move in getattr(pokemon, "moves", []) or []:
        move_name = move.name if hasattr(move, "name") else str(move)
        if hasattr(move, "disabled") and move.disabled:
            continue
        if hasattr(move, "current_pp") and move.current_pp <= 0:
            continue
        moves.append(move_name)
    return moves


def _best_damage_to(attacker, defender) -> float:
    """Best damage attacker can deal to defender as fraction of defender's max HP."""
    best = 0.0
    for move_name in _get_usable_moves(attacker):
        dmg = _estimate_damage_ratio(attacker, defender, move_name)
        if dmg > best:
            best = dmg
    return best


def _count_alive_reserve(battler) -> int:
    """Count alive reserve Pokemon (excludes active)."""
    count = 0
    for p in getattr(battler, "reserve", []):
        if p is None:
            continue
        fainted = getattr(p, "fainted", False)
        if isinstance(fainted, bool) and fainted:
            continue
        if getattr(p, "hp", 0) <= 0:
            continue
        count += 1
    return count


def _is_trapped(opp) -> bool:
    """Check if the opponent's active is trapped."""
    volatile = getattr(opp, "volatile_statuses", []) or []
    for v in volatile:
        if normalize_name(str(v)) in _TRAPPING_VOLATILES:
            return True
    return False


@dataclass
class OpponentAction:
    """Predicted opponent action for the current turn."""
    action: str  # "stays" or "switches"
    confidence: float  # 0.0-1.0
    best_move: Optional[str] = None
    best_move_damage: float = 0.0
    predicted_switchin: Optional[str] = None
    switchin_best_damage_to_us: float = 0.0


def predict_opponent_action(battle: Battle) -> OpponentAction:
    """
    Predict whether the opponent will stay or switch.

    Accumulates a switch score from independent signals, then
    thresholds at 0.50 to decide. Conservative by default.
    """
    our = battle.user.active
    opp = battle.opponent.active
    if our is None or opp is None:
        return OpponentAction(action="stays", confidence=0.5)

    # Check trapping first — trapped opponents can't switch
    if _is_trapped(opp):
        opp_best_move = None
        opp_best_dmg = 0.0
        for move_name in _get_usable_moves(opp):
            dmg = _estimate_damage_ratio(opp, our, move_name)
            if dmg > opp_best_dmg:
                opp_best_dmg = dmg
                opp_best_move = move_name
        return OpponentAction(
            action="stays",
            confidence=1.0,
            best_move=opp_best_move,
            best_move_damage=opp_best_dmg,
        )

    # Also check if WE have a trapping ability
    our_ability = normalize_name(getattr(our, "ability", "") or "")
    if our_ability in _TRAPPING_ABILITIES:
        # Check if the trapping applies (magnetpull only traps steel, etc.)
        trap_applies = False
        if our_ability == "shadowtag":
            opp_ability = normalize_name(getattr(opp, "ability", "") or "")
            trap_applies = opp_ability != "shadowtag"
        elif our_ability == "arenatrap":
            opp_types = _get_effective_types(opp)
            opp_ability = normalize_name(getattr(opp, "ability", "") or "")
            trap_applies = "flying" not in opp_types and opp_ability != "levitate"
        elif our_ability == "magnetpull":
            opp_types = _get_effective_types(opp)
            trap_applies = "steel" in opp_types

        if trap_applies:
            opp_best_move = None
            opp_best_dmg = 0.0
            for move_name in _get_usable_moves(opp):
                dmg = _estimate_damage_ratio(opp, our, move_name)
                if dmg > opp_best_dmg:
                    opp_best_dmg = dmg
                    opp_best_move = move_name
            return OpponentAction(
                action="stays",
                confidence=1.0,
                best_move=opp_best_move,
                best_move_damage=opp_best_dmg,
            )

    opp_reserves_alive = _count_alive_reserve(battle.opponent)
    if opp_reserves_alive == 0:
        # No reserves — opponent must stay
        opp_best_move = None
        opp_best_dmg = 0.0
        for move_name in _get_usable_moves(opp):
            dmg = _estimate_damage_ratio(opp, our, move_name)
            if dmg > opp_best_dmg:
                opp_best_dmg = dmg
                opp_best_move = move_name
        return OpponentAction(
            action="stays",
            confidence=1.0,
            best_move=opp_best_move,
            best_move_damage=opp_best_dmg,
        )

    # Compute opponent's best damage to us
    opp_best_dmg = 0.0
    opp_best_move = None
    for move_name in _get_usable_moves(opp):
        dmg = _estimate_damage_ratio(opp, our, move_name)
        if dmg > opp_best_dmg:
            opp_best_dmg = dmg
            opp_best_move = move_name

    # Compute our best damage to opponent
    our_best_dmg = _best_damage_to(our, opp)

    # Accumulate switch signals
    switch_score = 0.0

    # Signal 1: Bad matchup — they can't hurt us, we threaten them
    if opp_best_dmg < 0.12 and our_best_dmg > 0.40:
        switch_score += 0.50

    # Signal 2: Low HP with reserves
    opp_hp_ratio = opp.hp / max(opp.max_hp, 1)
    if opp_hp_ratio < 0.25 and opp_reserves_alive >= 2:
        switch_score += 0.25

    # Signal 3: All revealed moves resisted
    opp_moves = _get_usable_moves(opp)
    if opp_moves:
        all_resisted = all(
            _estimate_damage_ratio(opp, our, m) < 0.08 for m in opp_moves
        )
        if all_resisted:
            switch_score += 0.30

    # Counter-signal: Boosted — invested in setup, unlikely to leave
    opp_boosts = getattr(opp, "boosts", {}) or {}
    opp_atk_boost = opp_boosts.get(constants.ATTACK, 0)
    opp_spa_boost = opp_boosts.get(constants.SPECIAL_ATTACK, 0)
    max_offensive_boost = max(opp_atk_boost, opp_spa_boost)
    if max_offensive_boost >= 2:
        switch_score -= 0.40
    elif max_offensive_boost >= 1:
        switch_score -= 0.20

    # Clamp to [0, 1]
    switch_score = max(0.0, min(1.0, switch_score))

    if switch_score > 0.50:
        # Predict switch
        predicted_target = _predict_switch_target(battle)
        switchin_dmg = 0.0
        if predicted_target is not None:
            # Find the reserve Pokemon and compute their best damage to us
            for p in battle.opponent.reserve:
                if p is None:
                    continue
                if getattr(p, "name", None) == predicted_target:
                    switchin_dmg = _best_damage_to(p, our)
                    break

        return OpponentAction(
            action="switches",
            confidence=switch_score,
            predicted_switchin=predicted_target,
            switchin_best_damage_to_us=switchin_dmg,
        )
    else:
        # Predict stays
        return OpponentAction(
            action="stays",
            confidence=1.0 - switch_score,
            best_move=opp_best_move,
            best_move_damage=opp_best_dmg,
        )


def _predict_switch_target(battle: Battle) -> Optional[str]:
    """
    Predict which opponent reserve Pokemon will switch in.

    Scores each alive reserve by:
    - Type matchup vs our STAB types
    - HP remaining
    - Hazard cost on their side
    - Can threaten us with revealed moves
    """
    our = battle.user.active
    if our is None:
        return None

    our_types = _get_effective_types(our)
    opp_side_conditions = getattr(battle.opponent, "side_conditions", {})

    best_name = None
    best_score = -999.0

    for p in battle.opponent.reserve:
        if p is None:
            continue
        fainted = getattr(p, "fainted", False)
        if isinstance(fainted, bool) and fainted:
            continue
        if getattr(p, "hp", 0) <= 0:
            continue

        score = 0.0
        p_types = _get_effective_types(p)

        # Type matchup vs our STAB types
        for our_type in our_types:
            eff = type_effectiveness_modifier(our_type, p_types)
            if eff < 1.0:
                score += 0.5  # resists our STAB
            elif eff > 1.0:
                score -= 0.3  # weak to our STAB

        # HP remaining
        hp_ratio = p.hp / max(p.max_hp, 1)
        score += 0.3 * hp_ratio

        # Hazard cost on their side
        h_cost = _estimate_hazard_cost(p, opp_side_conditions)
        score -= 0.5 * h_cost

        # Can threaten us with revealed moves
        p_moves = _get_usable_moves(p)
        if p_moves:
            best_dmg = max(_estimate_damage_ratio(p, our, m) for m in p_moves)
            if best_dmg > 0.30:
                score += 0.3

        if score > best_score:
            best_score = score
            best_name = getattr(p, "name", None)

    return best_name


def predict_after_ko_switchin(battle: Battle) -> Optional[str]:
    """
    Predict which opponent Pokemon will come in after their active is KO'd.
    Same logic as _predict_switch_target.
    """
    return _predict_switch_target(battle)


def _estimate_hazard_cost(pokemon, side_conditions) -> float:
    """Estimate hazard damage on switch-in as fraction of max HP."""
    if pokemon is None:
        return 0.0

    item = normalize_name(getattr(pokemon, "item", "") or "")
    if item == "heavydutyboots":
        return 0.0

    cost = 0.0
    sc = side_conditions if isinstance(side_conditions, dict) else {}

    # Stealth Rock
    if sc.get(constants.STEALTH_ROCK, 0) > 0:
        p_types = _get_effective_types(pokemon)
        sr_eff = type_effectiveness_modifier("rock", p_types)
        cost += 0.125 * sr_eff

    # Spikes
    spikes = sc.get(constants.SPIKES, 0)
    if spikes > 0:
        p_types = _get_effective_types(pokemon)
        is_flying = "flying" in p_types
        has_levitate = normalize_name(getattr(pokemon, "ability", "") or "") == "levitate"
        if not is_flying and not has_levitate:
            spike_dmg = {1: 0.125, 2: 0.1667, 3: 0.25}
            cost += spike_dmg.get(min(spikes, 3), 0.25)

    return cost
