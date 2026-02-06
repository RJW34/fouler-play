"""
Endgame Solver for Fouler-Play
Phase 4.1 Implementation

Solves deterministic endgame scenarios (1v1, 2v1, etc.)
to find optimal plays without relying on MCTS.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import constants
from fp.battle import Battle
from fp.helpers import type_effectiveness_modifier
from data import all_move_json

logger = logging.getLogger(__name__)


@dataclass
class EndgameResult:
    """Result of endgame analysis."""
    best_move: Optional[str]
    expected_outcome: float  # 1.0 = win, 0.0 = lose, 0.5 = uncertain
    is_deterministic: bool
    depth_searched: int
    explanation: str = ""


def is_endgame(battle: Battle, max_pokemon: int = 3) -> bool:
    """Check if we're in a simple endgame scenario."""
    our_pokemon = [battle.user.active] + battle.user.reserve if battle.user.active else battle.user.reserve
    opp_pokemon = [battle.opponent.active] + battle.opponent.reserve if battle.opponent.active else battle.opponent.reserve

    our_alive = sum(1 for p in our_pokemon if p and p.hp > 0)
    opp_alive = sum(1 for p in opp_pokemon if p and p.hp > 0)

    return our_alive <= max_pokemon and opp_alive <= max_pokemon


def get_speed(pokemon) -> int:
    """Get Pokemon's effective speed stat."""
    if pokemon is None:
        return 0

    # Try stats dict first
    if hasattr(pokemon, "stats") and isinstance(pokemon.stats, dict):
        base_speed = pokemon.stats.get(constants.SPEED, 50)
    else:
        base_speed = getattr(pokemon, "speed", 50) or 50

    # Apply boosts
    boosts = getattr(pokemon, "boosts", {}) or {}
    speed_boost = boosts.get(constants.SPEED, 0)

    # Boost multipliers: +1 = 1.5x, +2 = 2x, etc.
    if speed_boost > 0:
        multiplier = (2 + speed_boost) / 2
    elif speed_boost < 0:
        multiplier = 2 / (2 - speed_boost)
    else:
        multiplier = 1.0

    # Apply paralysis
    status = getattr(pokemon, "status", None)
    if status == constants.PARALYSIS:
        multiplier *= 0.5

    return int(base_speed * multiplier)


def can_outspeed(our_pokemon, opp_pokemon) -> bool:
    """Check if we outspeed opponent."""
    return get_speed(our_pokemon) > get_speed(opp_pokemon)


def estimate_damage(attacker, defender, move_name: str) -> float:
    """
    Rough damage estimate as fraction of defender HP.
    Returns 0.0 to 1.0+ (can exceed 1.0 for overkill).
    """
    if attacker is None or defender is None:
        return 0.0

    move_data = all_move_json.get(move_name, {})
    base_power = move_data.get(constants.BASE_POWER, 0)

    if base_power == 0:
        return 0.0

    move_type = move_data.get(constants.TYPE, "normal")
    category = move_data.get(constants.CATEGORY, "Status")

    # Get attack/defense stats
    if category == constants.PHYSICAL:
        if hasattr(attacker, "stats") and isinstance(attacker.stats, dict):
            atk = attacker.stats.get(constants.ATTACK, 100)
        else:
            atk = 100
        if hasattr(defender, "stats") and isinstance(defender.stats, dict):
            def_ = defender.stats.get(constants.DEFENSE, 100)
        else:
            def_ = 100

        # Apply boosts
        atk_boost = (getattr(attacker, "boosts", {}) or {}).get(constants.ATTACK, 0)
        def_boost = (getattr(defender, "boosts", {}) or {}).get(constants.DEFENSE, 0)

    elif category == constants.SPECIAL:
        if hasattr(attacker, "stats") and isinstance(attacker.stats, dict):
            atk = attacker.stats.get(constants.SPECIAL_ATTACK, 100)
        else:
            atk = 100
        if hasattr(defender, "stats") and isinstance(defender.stats, dict):
            def_ = defender.stats.get(constants.SPECIAL_DEFENSE, 100)
        else:
            def_ = 100

        # Apply boosts
        atk_boost = (getattr(attacker, "boosts", {}) or {}).get(constants.SPECIAL_ATTACK, 0)
        def_boost = (getattr(defender, "boosts", {}) or {}).get(constants.SPECIAL_DEFENSE, 0)
    else:
        return 0.0

    # Apply stat boosts
    if atk_boost > 0:
        atk *= (2 + atk_boost) / 2
    elif atk_boost < 0:
        atk *= 2 / (2 - atk_boost)

    if def_boost > 0:
        def_ *= (2 + def_boost) / 2
    elif def_boost < 0:
        def_ *= 2 / (2 - def_boost)

    # Type effectiveness
    defender_types = getattr(defender, "types", []) or []
    effectiveness = type_effectiveness_modifier(move_type, defender_types)

    if effectiveness == 0:
        return 0.0

    # STAB
    attacker_types = getattr(attacker, "types", []) or []
    stab = 1.5 if move_type in attacker_types else 1.0

    # Simplified damage formula
    # Real formula: ((2*L/5+2)*P*A/D)/50+2) * modifiers
    # Simplified assuming level 100
    damage = (((2 * 100 / 5 + 2) * base_power * atk / def_) / 50 + 2) * effectiveness * stab

    # Normalize to HP ratio
    defender_max_hp = getattr(defender, "max_hp", 1) or 1
    damage_ratio = damage / defender_max_hp

    return damage_ratio


def can_ko(attacker, defender) -> Tuple[bool, Optional[str], float]:
    """
    Check if attacker can KO defender.
    Returns (can_ko, best_move, damage_ratio).
    """
    if attacker is None or defender is None:
        return False, None, 0.0

    best_damage = 0.0
    best_move = None

    moves = getattr(attacker, "moves", []) or []
    for move in moves:
        move_name = move.name if hasattr(move, "name") else str(move)

        # Skip disabled moves
        if hasattr(move, "disabled") and move.disabled:
            continue
        if hasattr(move, "current_pp") and move.current_pp <= 0:
            continue

        damage = estimate_damage(attacker, defender, move_name)
        if damage > best_damage:
            best_damage = damage
            best_move = move_name

    defender_hp_ratio = defender.hp / max(getattr(defender, "max_hp", 1), 1)

    return best_damage >= defender_hp_ratio, best_move, best_damage


def solve_1v1(our_pokemon, opp_pokemon) -> EndgameResult:
    """Solve a 1v1 endgame."""
    if our_pokemon is None or opp_pokemon is None:
        return EndgameResult(None, 0.5, False, 1, "Missing Pokemon")

    we_outspeed = can_outspeed(our_pokemon, opp_pokemon)
    we_can_ko, our_best_move, our_damage = can_ko(our_pokemon, opp_pokemon)
    they_can_ko, _, their_damage = can_ko(opp_pokemon, our_pokemon)

    # Speed tie (rare, assume we lose)
    our_speed = get_speed(our_pokemon)
    opp_speed = get_speed(opp_pokemon)
    speed_tie = our_speed == opp_speed

    if we_outspeed and we_can_ko:
        return EndgameResult(
            our_best_move, 1.0, True, 1,
            f"We outspeed ({our_speed} vs {opp_speed}) and KO"
        )

    if not we_outspeed and they_can_ko:
        # They KO us first
        return EndgameResult(
            our_best_move, 0.0, True, 1,
            f"They outspeed ({opp_speed} vs {our_speed}) and KO us"
        )

    if we_outspeed and not we_can_ko:
        if they_can_ko:
            # We hit first but can't KO, they KO us back
            return EndgameResult(
                our_best_move, 0.0, True, 1,
                "We can't KO, they KO us back"
            )
        else:
            # Neither can KO in one hit - check 2HKO
            their_hp_ratio = opp_pokemon.hp / max(opp_pokemon.max_hp, 1)
            our_hp_ratio = our_pokemon.hp / max(our_pokemon.max_hp, 1)

            turns_to_ko_them = their_hp_ratio / max(our_damage, 0.01)
            turns_to_ko_us = our_hp_ratio / max(their_damage, 0.01)

            if turns_to_ko_them < turns_to_ko_us:
                return EndgameResult(
                    our_best_move, 0.8, True, 2,
                    f"We win the slugfest ({turns_to_ko_them:.1f} vs {turns_to_ko_us:.1f} turns)"
                )
            else:
                return EndgameResult(
                    our_best_move, 0.2, True, 2,
                    f"They win the slugfest ({turns_to_ko_us:.1f} vs {turns_to_ko_them:.1f} turns)"
                )

    if speed_tie:
        return EndgameResult(
            our_best_move, 0.5, False, 1,
            f"Speed tie ({our_speed}), outcome uncertain"
        )

    # Fallback
    return EndgameResult(our_best_move, 0.5, False, 1, "Complex situation")


def solve_endgame(battle: Battle, max_depth: int = 4) -> Optional[EndgameResult]:
    """
    Attempt to solve an endgame position.
    Returns None if too complex or not an endgame.
    """
    if not is_endgame(battle):
        return None

    our_pokemon = [p for p in [battle.user.active] + battle.user.reserve if p and p.hp > 0]
    opp_pokemon = [p for p in [battle.opponent.active] + battle.opponent.reserve if p and p.hp > 0]

    our_alive = len(our_pokemon)
    opp_alive = len(opp_pokemon)

    logger.debug(f"Endgame analysis: {our_alive}v{opp_alive}")

    # 1v1 is solvable
    if our_alive == 1 and opp_alive == 1:
        result = solve_1v1(our_pokemon[0], opp_pokemon[0])
        logger.info(f"1v1 endgame: {result.explanation}")
        return result

    # 2v1 - we're up
    if our_alive == 2 and opp_alive == 1:
        # Check if current active can handle it
        active_result = solve_1v1(battle.user.active, opp_pokemon[0])
        if active_result.expected_outcome >= 0.8:
            logger.info(f"2v1 endgame (active wins): {active_result.explanation}")
            return active_result

        # Consider switching to backup
        backup = [p for p in our_pokemon if p != battle.user.active][0] if our_pokemon else None
        if backup:
            backup_result = solve_1v1(backup, opp_pokemon[0])
            if backup_result.expected_outcome > active_result.expected_outcome:
                switch_move = f"switch {backup.name}"
                return EndgameResult(
                    switch_move, backup_result.expected_outcome, False, 2,
                    f"Switch to {backup.name} is better matchup"
                )

        return EndgameResult(
            active_result.best_move, 0.7, False, 1,
            "2v1 advantage, staying in"
        )

    # 1v2 - we're down
    if our_alive == 1 and opp_alive == 2:
        result = solve_1v1(our_pokemon[0], battle.opponent.active)
        # Reduce expected outcome since we need to beat 2
        result.expected_outcome *= 0.5
        result.explanation = f"1v2 disadvantage: {result.explanation}"
        logger.info(f"1v2 endgame: {result.explanation}")
        return result

    # More complex endgames - return None to let MCTS handle
    logger.debug("Complex endgame, deferring to MCTS")
    return None
