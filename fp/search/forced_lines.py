"""
Forced Line Detection for Fouler-Play

Detects forced sequences where the optimal play is clear enough
to short-circuit the penalty pipeline.

Lines detected (in priority order):
1. We outspeed + can OHKO → take the KO (confidence 0.95)
2. Opponent OHKOs us + we can't KO back → must switch to resist (confidence 0.85)
3. Opponent at +2 and we have phaze → use it (confidence 0.80)
4. We 2HKO and they can't 2HKO us → stay in (confidence 0.75)
"""

import logging
from dataclasses import dataclass
from typing import Optional

import constants
from fp.battle import Battle
from fp.helpers import POKEMON_TYPE_INDICES, normalize_name, type_effectiveness_modifier
from fp.movepool_tracker import get_threat_category, ThreatCategory
from fp.search.speed_order import assess_speed_order
from data import all_move_json

logger = logging.getLogger(__name__)

PRIORITY_MOVES_SET = {
    "extremespeed", "aquajet", "machpunch", "bulletpunch", "iceshard",
    "shadowsneak", "suckerpunch", "quickattack", "accelerock",
    "grassyglide", "jettail",
}

PHAZE_MOVES_SET = {"whirlwind", "roar", "dragontail", "circlethrow", "haze", "clearsmog"}


_VALID_TYPES = set(POKEMON_TYPE_INDICES)


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


@dataclass
class ForcedLine:
    """A detected forced line of play."""
    move: str
    confidence: float
    reason: str
    line_type: str  # "guaranteed_ko", "forced_switch", "phaze", "stay_in"


def _estimate_damage(attacker, defender, move_name: str) -> float:
    """Estimate damage as fraction of defender's max HP."""
    if attacker is None or defender is None:
        return 0.0

    move_data = all_move_json.get(move_name, {})
    base_power = move_data.get(constants.BASE_POWER, 0)
    if base_power == 0:
        return 0.0

    move_type = _normalize_type_name(move_data.get(constants.TYPE, "normal")) or "normal"
    category = move_data.get(constants.CATEGORY, "Status")

    if category == constants.PHYSICAL:
        atk = attacker.stats.get(constants.ATTACK, 100) if isinstance(attacker.stats, dict) else 100
        def_ = defender.stats.get(constants.DEFENSE, 100) if isinstance(defender.stats, dict) else 100
        atk_boost = (getattr(attacker, "boosts", {}) or {}).get(constants.ATTACK, 0)
        def_boost = (getattr(defender, "boosts", {}) or {}).get(constants.DEFENSE, 0)
    elif category == constants.SPECIAL:
        atk = attacker.stats.get(constants.SPECIAL_ATTACK, 100) if isinstance(attacker.stats, dict) else 100
        def_ = defender.stats.get(constants.SPECIAL_DEFENSE, 100) if isinstance(defender.stats, dict) else 100
        atk_boost = (getattr(attacker, "boosts", {}) or {}).get(constants.SPECIAL_ATTACK, 0)
        def_boost = (getattr(defender, "boosts", {}) or {}).get(constants.SPECIAL_DEFENSE, 0)
    else:
        return 0.0

    if atk_boost > 0:
        atk *= (2 + atk_boost) / 2
    elif atk_boost < 0:
        atk *= 2 / (2 - atk_boost)
    if def_boost > 0:
        def_ *= (2 + def_boost) / 2
    elif def_boost < 0:
        def_ *= 2 / (2 - def_boost)

    defender_types = _get_effective_types(defender)
    effectiveness = type_effectiveness_modifier(move_type, defender_types)
    if effectiveness == 0:
        return 0.0

    attacker_types = _sanitize_type_list(getattr(attacker, "types", []) or [])
    attacker_tera = _normalize_type_name(getattr(attacker, "tera_type", None))
    if getattr(attacker, "terastallized", False) and attacker_tera:
        if attacker_tera not in attacker_types:
            attacker_types.append(attacker_tera)
    stab = 1.5 if move_type in attacker_types else 1.0

    damage = (((2 * 100 / 5 + 2) * base_power * atk / def_) / 50 + 2) * effectiveness * stab
    defender_max_hp = getattr(defender, "max_hp", 1) or 1
    return damage / defender_max_hp


def _get_usable_moves(pokemon) -> list[str]:
    """Get list of usable move names for a pokemon."""
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


def _has_priority_move(move_name: str) -> bool:
    """Check if move has positive priority."""
    move_data = all_move_json.get(move_name, {})
    return move_data.get(constants.PRIORITY, 0) > 0


def _revealed_switch_punish_exists(
    battle: Battle, move_name: str, our_hp_ratio: float
) -> tuple[bool, str]:
    """
    Return True only when there is a *proven* drawback to clicking `move_name`.

    Philosophy:
    - Default to productive pressure (take obvious attacks).
    - Only suppress forced "guaranteed KO" if a revealed switch-in can clearly
      punish this exact click.
    """
    our = battle.user.active
    if our is None:
        return False, ""

    for target in battle.opponent.reserve:
        if target is None:
            continue
        fainted_attr = getattr(target, "fainted", False)
        is_fainted = fainted_attr if isinstance(fainted_attr, bool) else False
        if is_fainted or getattr(target, "hp", 0) <= 0:
            continue

        incoming = _estimate_damage(our, target, move_name)
        target_hp_ratio = target.hp / max(target.max_hp, 1)

        # If our click still chunks this switch-in, it's usually productive.
        if incoming >= target_hp_ratio or incoming > 0.20:
            continue

        revealed_moves = _get_usable_moves(target)
        if not revealed_moves:
            # Unrevealed punish is speculative: do not over-predict.
            continue

        punish = 0.0
        punish_move = ""
        for m in revealed_moves:
            d = _estimate_damage(target, our, m)
            if d > punish:
                punish = d
                punish_move = m

        # "Proven drawback": they switch into a low-progress hit and can
        # immediately threaten lethal.
        if punish >= our_hp_ratio and incoming <= 0.20:
            return True, (
                f"{target.name} can switch into {move_name} ({incoming:.0%}) "
                f"and KO with revealed {punish_move} ({punish:.0%})"
            )

    return False, ""


def _best_switch_into_resist(battle: Battle) -> Optional[str]:
    """Find the best switch target that takes the least damage from opponent.

    Uses three layers:
    1. Opponent's REVEALED MOVES for actual damage (catches coverage moves)
    2. STAB type matchup as fallback when no moves are revealed
    3. THREAT CATEGORY to prefer the right wall (Blissey vs special, Skarmory vs physical)
    """
    opp = battle.opponent.active
    if opp is None:
        return None

    opp_types = _get_effective_types(opp)
    opp_moves = _get_usable_moves(opp) if opp else []

    # Get threat category for defensive stat routing
    opp_name = getattr(opp, "name", "") or ""
    threat_cat = ThreatCategory.UNKNOWN
    if opp_name:
        try:
            threat_cat = get_threat_category(opp_name)
        except Exception:
            pass

    best_target = None
    best_score = -999.0

    for pkmn in battle.user.reserve:
        if pkmn.hp <= 0:
            continue

        target_types = _get_effective_types(pkmn)
        if not target_types:
            continue

        if opp_moves:
            # Use revealed moves: find worst damage to this target
            worst_dmg = 0.0
            for move_name in opp_moves:
                dmg = _estimate_damage(opp, pkmn, move_name)
                worst_dmg = max(worst_dmg, dmg)
            pkmn_hp_ratio = pkmn.hp / max(pkmn.max_hp, 1)
            # Score: lower damage taken = better. Penalize hard if they KO us.
            if worst_dmg >= pkmn_hp_ratio:
                score = -2.0  # they KO this target
            else:
                score = -worst_dmg
        elif opp_types:
            # Fallback: STAB type matchup
            worst_eff = max(
                type_effectiveness_modifier(t, target_types) for t in opp_types
            )
            score = -worst_eff
        else:
            score = 0.0

        # Threat category: route to the right wall
        target_stats = getattr(pkmn, "stats", {})
        if isinstance(target_stats, dict):
            if threat_cat == ThreatCategory.SPECIAL_ONLY:
                # Special threat — Blissey (SpDef ~350) should score highest
                spd = target_stats.get(constants.SPECIAL_DEFENSE, 100)
                score += spd / 250.0 * 0.4
            elif threat_cat == ThreatCategory.PHYSICAL_ONLY:
                # Physical threat — route to highest Def
                def_ = target_stats.get(constants.DEFENSE, 100)
                score += def_ / 250.0 * 0.4
            elif threat_cat == ThreatCategory.MIXED:
                combined = (target_stats.get(constants.DEFENSE, 100) +
                            target_stats.get(constants.SPECIAL_DEFENSE, 100))
                score += combined / 500.0 * 0.3
            else:
                # Unknown — use base stats to guess
                opp_base = getattr(opp, "base_stats", {})
                if isinstance(opp_base, dict):
                    opp_atk = opp_base.get(constants.ATTACK, 80)
                    opp_spa = opp_base.get(constants.SPECIAL_ATTACK, 80)
                    if opp_spa > opp_atk + 20:
                        spd = target_stats.get(constants.SPECIAL_DEFENSE, 100)
                        score += spd / 250.0 * 0.3
                    elif opp_atk > opp_spa + 20:
                        def_ = target_stats.get(constants.DEFENSE, 100)
                        score += def_ / 250.0 * 0.3

        # HP matters
        hp_ratio = pkmn.hp / max(pkmn.max_hp, 1)
        score += hp_ratio * 0.3

        if score > best_score:
            best_score = score
            best_target = pkmn.name

    return best_target


def detect_forced_line(battle: Battle) -> Optional[ForcedLine]:
    """
    Detect forced lines of play.
    Returns ForcedLine if a clear forced play exists, None otherwise.

    Priority order:
    1. Guaranteed KO (outspeed + OHKO)
    2. Forced switch (opponent OHKOs us, we can't KO back, have resist)
    3. Phaze vs boosted opponent
    4. We 2HKO and they can't 2HKO us → stay in
    """
    our = battle.user.active
    opp = battle.opponent.active

    if our is None or opp is None:
        return None

    speed_assessment = assess_speed_order(battle)
    guaranteed_move_first = speed_assessment.guaranteed_move_first
    our_moves = _get_usable_moves(our)
    opp_types = _get_effective_types(opp)
    our_hp_ratio = our.hp / max(our.max_hp, 1)
    opp_hp_ratio = opp.hp / max(opp.max_hp, 1)

    # === LINE 1: Guaranteed KO ===
    # We outspeed (or have priority) AND can OHKO
    for move_name in our_moves:
        dmg = _estimate_damage(our, opp, move_name)
        if dmg >= opp_hp_ratio:
            has_prio = _has_priority_move(move_name)
            if guaranteed_move_first or has_prio:
                proven_drawback, drawback_reason = _revealed_switch_punish_exists(
                    battle, move_name, our_hp_ratio
                )
                if proven_drawback:
                    logger.info(
                        "FORCED LINE: skipping guaranteed KO with %s due to proven switch drawback: %s",
                        move_name,
                        drawback_reason,
                    )
                    continue
                speed_str = "guaranteed_outspeed" if guaranteed_move_first else "priority"
                logger.info(
                    f"FORCED LINE: guaranteed KO with {move_name} "
                    f"({speed_str}, {dmg:.2f} vs {opp_hp_ratio:.2f} HP)"
                )
                return ForcedLine(
                    move=move_name,
                    confidence=0.95,
                    reason=f"Guaranteed KO: {move_name} ({speed_str}, deals {dmg:.0%} vs {opp_hp_ratio:.0%} HP)",
                    line_type="guaranteed_ko",
                )

    # === LINE 2: Forced switch (opponent OHKOs us, we can't KO them) ===
    opp_moves = _get_usable_moves(opp)
    opp_best_dmg = 0.0
    for move_name in opp_moves:
        d = _estimate_damage(opp, our, move_name)
        opp_best_dmg = max(opp_best_dmg, d)

    # If no revealed moves, estimate conservatively
    if not opp_moves and opp_types:
        our_types = _get_effective_types(our)
        for t in opp_types:
            eff = type_effectiveness_modifier(t, our_types)
            opp_best_dmg = max(opp_best_dmg, 0.3 * 1.5 * eff)

    if opp_best_dmg >= our_hp_ratio:
        # Opponent can KO us. Can we KO them first?
        our_best_dmg = 0.0
        our_best_move = None
        for move_name in our_moves:
            d = _estimate_damage(our, opp, move_name)
            if d > our_best_dmg:
                our_best_dmg = d
                our_best_move = move_name

        can_trade = our_best_dmg >= opp_hp_ratio and (
            guaranteed_move_first or (our_best_move and _has_priority_move(our_best_move))
        )

        if not can_trade and not battle.force_switch:
            # We can't KO them and they KO us → find a resist
            resist_target = _best_switch_into_resist(battle)
            if resist_target:
                logger.info(
                    f"FORCED LINE: switch to {resist_target} "
                    f"(opponent deals {opp_best_dmg:.2f} vs our {our_hp_ratio:.2f} HP)"
                )
                return ForcedLine(
                    move=f"switch {resist_target}",
                    confidence=0.85,
                    reason=f"Forced switch: opponent KOs us ({opp_best_dmg:.0%} vs {our_hp_ratio:.0%}), switching to {resist_target}",
                    line_type="forced_switch",
                )

    # === LINE 3: Phaze vs boosted opponent ===
    opp_boosts = getattr(opp, "boosts", {}) or {}
    opp_atk_boost = opp_boosts.get(constants.ATTACK, 0)
    opp_spa_boost = opp_boosts.get(constants.SPECIAL_ATTACK, 0)
    total_offensive_boosts = max(opp_atk_boost, opp_spa_boost)

    if total_offensive_boosts >= 2:
        for move_name in our_moves:
            norm = normalize_name(move_name)
            if norm in PHAZE_MOVES_SET:
                logger.info(
                    f"FORCED LINE: phaze with {move_name} "
                    f"(opponent at +{total_offensive_boosts})"
                )
                return ForcedLine(
                    move=move_name,
                    confidence=0.80,
                    reason=f"Phaze: opponent boosted to +{total_offensive_boosts}, using {move_name}",
                    line_type="phaze",
                )

    # === LINE 4: We 2HKO and they can't 2HKO us → stay in ===
    if guaranteed_move_first:
        our_best_dmg = 0.0
        our_best_move = None
        for move_name in our_moves:
            d = _estimate_damage(our, opp, move_name)
            if d > our_best_dmg:
                our_best_dmg = d
                our_best_move = move_name

        if our_best_move and our_best_dmg * 2 >= opp_hp_ratio:
            # We 2HKO them
            if opp_best_dmg * 2 < our_hp_ratio:
                # They can't 2HKO us → stay in and attack
                logger.info(
                    f"FORCED LINE: stay in with {our_best_move} "
                    f"(we 2HKO: {our_best_dmg:.2f}x2, they can't: {opp_best_dmg:.2f}x2)"
                )
                return ForcedLine(
                    move=our_best_move,
                    confidence=0.75,
                    reason=f"Stay in: we 2HKO with {our_best_move} ({our_best_dmg:.0%}x2), they can't 2HKO us ({opp_best_dmg:.0%}x2)",
                    line_type="stay_in",
                )

    return None
