"""
Speed-order certainty helpers.

This module is intentionally conservative. It only reports "guaranteed move first"
when we are faster than the opponent's plausible maximum effective speed, including:
- Known speed bounds from `speed_range`
- Possible Choice Scarf when item is still unknown
- Possible speed-modifying abilities when ability is still unknown and conditions match
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
from typing import List

import constants
from data import pokedex
from fp.battle import Battle, boost_multiplier_lookup
from fp.helpers import calculate_stats, normalize_name


@dataclass
class SpeedOrderAssessment:
    our_effective_speed: int
    opp_min_effective_speed: int
    opp_max_effective_speed: int
    guaranteed_move_first: bool
    guaranteed_move_second: bool
    uncertain: bool
    notes: List[str]


def _get_species_abilities(pokemon) -> set[str]:
    name = normalize_name(getattr(pokemon, "name", "") or "")
    if not name or name not in pokedex:
        return set()
    abilities = pokedex[name].get(constants.ABILITIES, {})
    return {normalize_name(a) for a in abilities.values() if isinstance(a, str)}


def _safe_int(value, default: int) -> int:
    if isinstance(value, Real):
        return int(value)
    return default


def _max_natural_speed(pokemon) -> int:
    base_stats = getattr(pokemon, "base_stats", None)
    if not isinstance(base_stats, dict):
        name = normalize_name(getattr(pokemon, "name", "") or "")
        base_stats = pokedex.get(name, {}).get(constants.BASESTATS, {})

    level = _safe_int(getattr(pokemon, "level", 100), 100)
    if level <= 0:
        level = 100
    if not isinstance(base_stats, dict) or constants.SPEED not in base_stats:
        stats = getattr(pokemon, "stats", {}) or {}
        return int(stats.get(constants.SPEED, 100) or 100)

    # Max practical ladder speed for singles: +Spe nature, 252 EVs, 31 IV.
    max_stats = calculate_stats(base_stats, level, nature="jolly", evs=(0, 0, 0, 0, 0, 252))
    return int(max_stats.get(constants.SPEED, 100) or 100)


def _raw_speed_bounds(pokemon) -> tuple[int, int]:
    speed_range = getattr(pokemon, "speed_range", None)
    min_raw = max(0, _safe_int(getattr(speed_range, "min", 0), 0))

    max_raw_attr = getattr(speed_range, "max", float("inf")) if speed_range is not None else float("inf")
    max_raw_val = float("inf")
    if isinstance(max_raw_attr, Real):
        max_raw_val = float(max_raw_attr)

    if max_raw_val is None or not isfinite(max_raw_val):
        max_raw = _max_natural_speed(pokemon)
    else:
        max_raw = int(max_raw_val)

    if max_raw < min_raw:
        max_raw = min_raw
    return min_raw, max_raw


def _known_speed_multiplier_from_current_state(battle: Battle, battler) -> float:
    active = getattr(battler, "active", None)
    if active is None:
        return 1.0
    stats = getattr(active, "stats", {}) or {}
    raw_current = _safe_int(stats.get(constants.SPEED, 0), 0)
    if raw_current <= 0:
        return 1.0
    effective_current = int(battle.get_effective_speed(battler))
    if effective_current <= 0:
        return 1.0
    return effective_current / raw_current


def _possible_unknown_speed_multiplier(battle: Battle, pokemon) -> tuple[float, list[str]]:
    notes: list[str] = []
    mult = 1.0

    item = getattr(pokemon, "item", None)
    can_have_choice_item = bool(getattr(pokemon, "can_have_choice_item", True))

    # Only assume Scarf possibility when item is truly unknown.
    if item == constants.UNKNOWN_ITEM and can_have_choice_item:
        mult *= 1.5
        notes.append("possible_choice_scarf")

    ability = normalize_name(getattr(pokemon, "ability", "") or "")
    if ability:
        return mult, notes

    abilities = _get_species_abilities(pokemon)
    weather = getattr(battle, "weather", None)
    field = getattr(battle, "field", None)
    status = getattr(pokemon, "status", None)

    weather_double = False
    if weather == constants.RAIN and "swiftswim" in abilities:
        weather_double = True
    elif weather == constants.SUN and "chlorophyll" in abilities:
        weather_double = True
    elif weather == constants.SAND and "sandrush" in abilities:
        weather_double = True
    elif weather in constants.HAIL_OR_SNOW and "slushrush" in abilities:
        weather_double = True

    if weather_double:
        mult *= 2.0
        notes.append("possible_weather_speed_ability")

    if field == constants.ELECTRIC_TERRAIN and "surgesurfer" in abilities:
        mult *= 2.0
        notes.append("possible_surge_surfer")

    # If ability is unknown and statused, Quick Feet may overturn baseline assumptions.
    if "quickfeet" in abilities and status is not None:
        if status == constants.PARALYZED:
            # Baseline likely includes para 0.5x; Quick Feet would be 1.5x => 3x uplift.
            mult *= 3.0
        else:
            mult *= 1.5
        notes.append("possible_quickfeet")

    # Unburden is only relevant when item has already been consumed.
    if item is None and "unburden" in abilities:
        mult *= 2.0
        notes.append("possible_unburden")

    return mult, notes


def assess_speed_order(battle: Battle) -> SpeedOrderAssessment:
    if battle is None or battle.user.active is None or battle.opponent.active is None:
        return SpeedOrderAssessment(
            our_effective_speed=0,
            opp_min_effective_speed=0,
            opp_max_effective_speed=0,
            guaranteed_move_first=False,
            guaranteed_move_second=False,
            uncertain=True,
            notes=["missing_active"],
        )

    our_effective = int(battle.get_effective_speed(battle.user))
    opp_min_raw, opp_max_raw = _raw_speed_bounds(battle.opponent.active)
    known_mult = _known_speed_multiplier_from_current_state(battle, battle.opponent)
    unknown_mult, notes = _possible_unknown_speed_multiplier(battle, battle.opponent.active)

    opp_min_effective = int(opp_min_raw * known_mult)
    opp_max_effective = int(opp_max_raw * known_mult * unknown_mult)

    trick_room_active = bool(getattr(battle, "trick_room", False))
    if trick_room_active:
        guaranteed_first = our_effective < opp_min_effective
        guaranteed_second = our_effective > opp_max_effective
    else:
        guaranteed_first = our_effective > opp_max_effective
        guaranteed_second = our_effective < opp_min_effective

    uncertain = not guaranteed_first and not guaranteed_second
    if trick_room_active:
        notes.append("trick_room")

    return SpeedOrderAssessment(
        our_effective_speed=our_effective,
        opp_min_effective_speed=opp_min_effective,
        opp_max_effective_speed=opp_max_effective,
        guaranteed_move_first=guaranteed_first,
        guaranteed_move_second=guaranteed_second,
        uncertain=uncertain,
        notes=notes,
    )


def adjusted_speed_for_display(pokemon) -> int:
    """Utility for debugging and tests (raw speed with boost stage only)."""
    if pokemon is None:
        return 0
    stats = getattr(pokemon, "stats", {}) or {}
    base = int(stats.get(constants.SPEED, 0) or 0)
    boosts = getattr(pokemon, "boosts", {}) or {}
    stage = int(boosts.get(constants.SPEED, 0) or 0)
    return int(base * boost_multiplier_lookup[stage])
