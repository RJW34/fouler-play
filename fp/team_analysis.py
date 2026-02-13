from dataclasses import dataclass, field
from typing import Dict, List, Set

import constants
from data import pokedex
from fp.helpers import normalize_name
from fp.playstyle_config import Playstyle, PIVOT_MOVES, RECOVERY_MOVES, HAZARD_MOVES

from constants_pkg.strategy import SETUP_MOVES, PRIORITY_MOVES


SCREEN_MOVES = {"reflect", "lightscreen", "auroraveil"}
REMOVAL_MOVES = {"defog", "rapidspin", "mortalspin", "tidyup", "courtchange"}
SPEED_CONTROL_MOVES = {"tailwind", "trickroom"}

HAZARD_MOVES_NORM = {normalize_name(m) for m in HAZARD_MOVES}
PIVOT_MOVES_NORM = {normalize_name(m) for m in PIVOT_MOVES}
RECOVERY_MOVES_NORM = {normalize_name(m) for m in RECOVERY_MOVES}
SETUP_MOVES_NORM = {normalize_name(m) for m in SETUP_MOVES}
PRIORITY_MOVES_NORM = {normalize_name(m) for m in PRIORITY_MOVES}
SCREEN_MOVES_NORM = {normalize_name(m) for m in SCREEN_MOVES}
REMOVAL_MOVES_NORM = {normalize_name(m) for m in REMOVAL_MOVES}


@dataclass
class TeamAnalysis:
    playstyle: Playstyle
    hazard_setters: Set[str] = field(default_factory=set)
    hazard_removers: Set[str] = field(default_factory=set)
    pivots: Set[str] = field(default_factory=set)
    setup_sweepers: Set[str] = field(default_factory=set)
    screens_setters: Set[str] = field(default_factory=set)
    recovery_users: Set[str] = field(default_factory=set)
    wincons: Set[str] = field(default_factory=set)
    speed_control: Set[str] = field(default_factory=set)
    breakers: Set[str] = field(default_factory=set)
    bulky: Set[str] = field(default_factory=set)
    priority_users: Set[str] = field(default_factory=set)


def _ev_val(val: str | int | None) -> int:
    try:
        return int(val) if val is not None and val != "" else 0
    except ValueError:
        return 0


def _get_base_stat(species: str, stat_key: str) -> int:
    try:
        return int(pokedex[species][constants.BASESTATS][stat_key])
    except Exception:
        return 0


def _classify_pokemon(pkmn: Dict) -> Dict:
    species = normalize_name(pkmn.get("species", ""))
    moves = {normalize_name(m) for m in pkmn.get("moves", [])}
    item = normalize_name(pkmn.get("item", ""))
    ability = normalize_name(pkmn.get("ability", ""))
    evs = pkmn.get("evs", {}) or {}
    ev_hp = _ev_val(evs.get("hp"))
    ev_atk = _ev_val(evs.get("atk"))
    ev_def = _ev_val(evs.get("def"))
    ev_spa = _ev_val(evs.get("spa"))
    ev_spd = _ev_val(evs.get("spd"))
    ev_spe = _ev_val(evs.get("spe"))

    base_speed = _get_base_stat(species, constants.SPEED)

    has_setup = bool(moves & SETUP_MOVES_NORM)
    has_hazard = bool(moves & HAZARD_MOVES_NORM)
    has_removal = bool(moves & REMOVAL_MOVES_NORM)
    has_screen = bool(moves & SCREEN_MOVES_NORM)
    has_pivot = bool(moves & PIVOT_MOVES_NORM)
    has_recovery = bool(moves & RECOVERY_MOVES_NORM)
    has_priority = bool(moves & PRIORITY_MOVES_NORM)
    has_speed_control_move = bool(moves & {normalize_name(m) for m in SPEED_CONTROL_MOVES})

    is_speed_control = (
        item == "choicescarf"
        or item == "boosterenergy"
        or ev_spe >= 200
        or base_speed >= 105
        or has_speed_control_move
    )

    is_breaker = item in {"choiceband", "choicespecs", "lifeorb", "expertbelt"}
    if not is_breaker and (ev_atk >= 200 or ev_spa >= 200) and not has_recovery:
        is_breaker = True

    is_bulky = ev_hp >= 200 and (ev_def >= 120 or ev_spd >= 120)
    if has_recovery and (ev_def + ev_spd + ev_hp) >= 300:
        is_bulky = True

    # Wincon heuristic: setup + speed / power
    is_wincon = False
    if has_setup and (is_speed_control or ev_atk >= 200 or ev_spa >= 200 or base_speed >= 100):
        is_wincon = True

    return {
        "species": species,
        "has_setup": has_setup,
        "has_hazard": has_hazard,
        "has_removal": has_removal,
        "has_screen": has_screen,
        "has_pivot": has_pivot,
        "has_recovery": has_recovery,
        "has_priority": has_priority,
        "is_speed_control": is_speed_control,
        "is_breaker": is_breaker,
        "is_bulky": is_bulky,
        "is_wincon": is_wincon,
        "item": item,
        "ability": ability,
    }


def detect_playstyle_from_counts(
    setup_count: int,
    screen_count: int,
    recovery_count: int,
    hazard_count: int,
    bulky_count: int,
    breaker_count: int,
    pivot_count: int,
) -> Playstyle:
    if screen_count >= 1 and setup_count >= 2:
        return Playstyle.HYPER_OFFENSE
    if recovery_count >= 3 and hazard_count >= 2 and setup_count <= 1:
        # If little breaking power, call it stall
        if breaker_count <= 1:
            return Playstyle.STALL
        # Hazard-stacking teams with multiple pivots/breakers usually need
        # proactive conversion lines, not pure fat/stall passivity.
        if breaker_count >= 2 and pivot_count >= 2:
            return Playstyle.BULKY_OFFENSE
        return Playstyle.FAT
    if setup_count >= 2 and recovery_count <= 1:
        return Playstyle.HYPER_OFFENSE
    if bulky_count >= 3 and breaker_count >= 2 and pivot_count >= 2:
        return Playstyle.BULKY_OFFENSE
    if bulky_count >= 3 and setup_count >= 1:
        return Playstyle.FAT
    if breaker_count >= 2 and recovery_count >= 1:
        return Playstyle.BULKY_OFFENSE
    return Playstyle.BALANCE


def analyze_team(team_dict: List[Dict]) -> TeamAnalysis:
    if not team_dict:
        return TeamAnalysis(playstyle=Playstyle.BALANCE)

    hazard_setters = set()
    hazard_removers = set()
    pivots = set()
    setup_sweepers = set()
    screens_setters = set()
    recovery_users = set()
    wincons = set()
    speed_control = set()
    breakers = set()
    bulky = set()
    priority_users = set()

    setup_count = 0
    screen_count = 0
    recovery_count = 0
    hazard_count = 0
    bulky_count = 0
    breaker_count = 0

    for pkmn in team_dict:
        info = _classify_pokemon(pkmn)
        species = info["species"]

        if info["has_hazard"]:
            hazard_setters.add(species)
            hazard_count += 1
        if info["has_removal"]:
            hazard_removers.add(species)
        if info["has_pivot"]:
            pivots.add(species)
        if info["has_setup"]:
            setup_sweepers.add(species)
            setup_count += 1
        if info["has_screen"]:
            screens_setters.add(species)
            screen_count += 1
        if info["has_recovery"]:
            recovery_users.add(species)
            recovery_count += 1
        if info["has_priority"]:
            priority_users.add(species)
        if info["is_speed_control"]:
            speed_control.add(species)
        if info["is_breaker"]:
            breakers.add(species)
            breaker_count += 1
        if info["is_bulky"]:
            bulky.add(species)
            bulky_count += 1
        if info["is_wincon"]:
            wincons.add(species)

    playstyle = detect_playstyle_from_counts(
        setup_count,
        screen_count,
        recovery_count,
        hazard_count,
        bulky_count,
        breaker_count,
        len(pivots),
    )

    return TeamAnalysis(
        playstyle=playstyle,
        hazard_setters=hazard_setters,
        hazard_removers=hazard_removers,
        pivots=pivots,
        setup_sweepers=setup_sweepers,
        screens_setters=screens_setters,
        recovery_users=recovery_users,
        wincons=wincons,
        speed_control=speed_control,
        breakers=breakers,
        bulky=bulky,
        priority_users=priority_users,
    )
