"""
Team Intent Layer — Agnostic Build-Signal Inference Engine.

Given a Pokemon's full build (species, item, ability, moves, EV spread, nature),
infers structured strategic intent. Fully team-agnostic: no hardcoded teams.
Derives role, handles, loadout annotations, and correct play rules dynamically.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from data import pokedex
import constants
from fp.helpers import normalize_name, type_effectiveness_modifier

logger = logging.getLogger(__name__)

# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class PokemonIntent:
    """Inferred strategic intent for a single Pokemon."""
    role: str                            # primary classified role
    role_tags: List[str]                 # e.g. ["hazard setter", "pivot", "special wall"]
    handles_types: List[str]             # type categories this mon checks
    handles_abilities: List[str]         # abilities it counters via its own ability/item
    loadout_annotations: Dict[str, str]  # item/move/EV -> strategic explanation
    correct_play: List[str]             # generated behavioral rules
    recovery_threshold: float           # HP% to prioritize recovery (0.0–1.0)
    is_win_condition: bool              # True if setup sweeper or primary breaker
    confidence: float                   # 0.0–1.0 inference confidence


@dataclass
class TeamContext:
    """Inferred strategic context for an entire team."""
    team_style: str                      # "stall", "balance", "hyper offense", "pivot heavy", "fat"
    win_conditions: List[str]            # normalized names of setup sweepers / primary breakers
    pokemon_intent: Dict[str, PokemonIntent]  # keyed by normalized species name
    team_rules: List[str]                # global inferred rules


# ============================================================================
# Move Category Lookups
# ============================================================================

RECOVERY_MOVES = {
    "recover", "roost", "softboiled", "slackoff", "synthesis",
    "moonlight", "morningsun", "rest", "shoreup", "wish",
    "healorder", "milkdrink", "swallow", "strengthsap",
}

SETUP_MOVES = {
    "swordsdance", "nastyplot", "calmmind", "dragondance", "quiverdance",
    "shellsmash", "bulkup", "curse", "coil", "irondefense", "amnesia",
    "agility", "autotomize", "rockpolish", "shiftgear", "victorydance",
    "bellydrum", "growth", "workup", "geomancy", "filletaway", "tidyup",
    "clangoroussoul", "noretreat", "tailglow", "cottonguard", "cosmicpower",
    "torchsong",
}

PIVOT_MOVES = {
    "uturn", "voltswitch", "partingshot", "chillyreception", "flipturn",
    "teleport", "batonpass",
}

HAZARD_MOVES = {
    "stealthrock", "spikes", "toxicspikes", "stickyweb",
}

HAZARD_REMOVAL_MOVES = {
    "defog", "rapidspin", "mortalspin", "tidyup", "courtchange",
}

PHAZING_MOVES = {
    "whirlwind", "roar", "dragontail", "circlethrow", "yawn",
}

STATUS_MOVES = {
    "toxic", "willowisp", "thunderwave", "stunspore", "glare",
    "spore", "sleeppowder", "hypnosis", "yawn", "nuzzle",
    "haze", "taunt", "encore", "trickroom", "tailwind",
}

CLERIC_MOVES = {
    "healbell", "aromatherapy", "wish",
}

# ============================================================================
# Item Strategic Purpose Lookup (~40 common items)
# ============================================================================

ITEM_ANNOTATIONS = {
    "leftovers": "passive recovery each turn — signals a bulky or defensive role",
    "heavydutyboots": "immune to entry hazards on switch-in — protects a Pokemon that switches frequently",
    "blacksludge": "passive recovery for Poison-types — signals defensive/pivot role",
    "rockyhelmet": "punishes physical contact moves — signals a physical wall meant to check physical attackers",
    "choiceband": "locks into one move at 1.5x Attack — signals a wallbreaker or revenge killer",
    "choicespecs": "locks into one move at 1.5x SpA — signals a special wallbreaker",
    "choicescarf": "locks into one move at 1.5x Speed — signals a revenge killer or speed control",
    "lifeorb": "1.3x damage for 10% HP recoil — signals an all-out attacker or setup sweeper",
    "eviolite": "1.5x Def/SpDef for NFE — signals a defensive pivot or wall",
    "assaultvest": "1.5x SpDef, no status moves — signals a specially bulky attacker or pivot",
    "focussash": "survives one KO from full HP — signals a lead, suicide setter, or frail sweeper",
    "toxicorb": "inflicts Toxic on holder — enables Poison Heal or Guts; protects from other status",
    "flameorb": "inflicts Burn on holder — enables Guts (boosted Attack + burn immunity)",
    "covertcloak": "blocks secondary effects — meant to tank through flinch/stat drops/status from moves",
    "abilityshield": "protects ability from suppression — preserves key defensive or offensive ability",
    "airballoon": "levitate until hit — grants temporary Ground immunity for a key switch-in",
    "sitrusberry": "heals 25% HP when below 50% — signals a bulky pivot or lead that wants one free recovery",
    "weaknesspolicy": "boosts Atk/SpA +2 when hit super-effectively — signals a bulky setup sweeper baiting hits",
    "boosterenergy": "activates Protosynthesis/Quark Drive — boosts highest stat; signals speed or power role",
    "clearamulet": "blocks stat drops — protects against Intimidate, Parting Shot, etc.",
    "shedshell": "immune to trapping — signals a defensive mon that needs to escape trappers",
    "mentalherb": "cures Taunt/Encore once — signals a support mon that must set up hazards/screens",
    "lightclay": "extends screen duration to 8 turns — signals a screens setter for hyper offense",
    "redcard": "forces opponent switch on contact — disrupts setup or momentum",
    "custapberry": "priority at low HP — signals an endgame hazard setter or suicide lead",
    "lumberry": "cures status once — protects setup sweeper or prevents status disruption",
    "throatspray": "boosts SpA after using sound move — signals a special attacker with sound STAB",
    "loadeddice": "multi-hit moves guaranteed 4-5 hits — signals a multi-hit attacker (Scale Shot, Icicle Spear)",
    "mirrorherb": "copies opponent stat boosts once — punishes opponent setup",
    "whiteherb": "restores dropped stats once — signals Shell Smash or Overheat user",
    "expertbelt": "1.2x super-effective damage — signals a coverage-heavy attacker",
    "scopelens": "boosted crit rate — signals a crit-fishing attacker",
    "muscleband": "1.1x physical damage — budget choice for physical attackers",
    "wiseglasses": "1.1x special damage — budget choice for special attackers",
    "safetygoggles": "blocks powder/weather damage — signals a mon that needs to handle Spore or sandstorm chip",
    "protectivepads": "blocks contact effects — signals safe pivoting vs Rocky Helmet/Iron Barbs",
    "terrainextender": "extends terrain to 8 turns — signals terrain setter support",
    "widelens": "1.1x accuracy — signals reliance on low-accuracy moves (Focus Blast, Hypnosis)",
}

# ============================================================================
# Ability Handles Inference
# ============================================================================

ABILITY_HANDLES_MAP = {
    # Type-absorbing abilities
    "waterabsorb": {"handles_types": ["water"], "desc": "absorbs Water moves for healing"},
    "stormdrain": {"handles_types": ["water"], "desc": "absorbs Water moves, boosts SpA"},
    "dryskin": {"handles_types": ["water"], "desc": "absorbs Water moves; weak to Fire"},
    "voltabsorb": {"handles_types": ["electric"], "desc": "absorbs Electric moves for healing"},
    "lightningrod": {"handles_types": ["electric"], "desc": "absorbs Electric moves, boosts SpA"},
    "motordrive": {"handles_types": ["electric"], "desc": "absorbs Electric for Speed boost"},
    "flashfire": {"handles_types": ["fire"], "desc": "absorbs Fire moves, boosts own Fire"},
    "wellbakedbody": {"handles_types": ["fire"], "desc": "absorbs Fire moves, boosts Def"},
    "eartheater": {"handles_types": ["ground"], "desc": "absorbs Ground moves for healing"},
    "levitate": {"handles_types": ["ground"], "desc": "immune to Ground moves"},
    "sapsipper": {"handles_types": ["grass"], "desc": "absorbs Grass moves, boosts Atk"},
    # Defensive utility abilities
    "unaware": {"handles_abilities": ["setup sweeper"], "desc": "ignores opponent stat boosts — walls setup sweepers"},
    "naturalcure": {"handles_abilities": ["status"], "desc": "cures status on switch-out — free status absorption"},
    "regenerator": {"handles_abilities": ["chip damage"], "desc": "heals 33% on switch — enables free pivoting"},
    "magicbounce": {"handles_abilities": ["hazard setter", "status"], "desc": "reflects status moves and hazards"},
    "goodasgold": {"handles_abilities": ["status"], "desc": "immune to all status moves"},
    "poisonheal": {"handles_abilities": ["status"], "desc": "Toxic heals instead of damaging — immune to other status"},
    "thickfat": {"handles_types": ["fire", "ice"], "desc": "halves Fire and Ice damage"},
    "heatproof": {"handles_types": ["fire"], "desc": "halves Fire damage"},
    "fluffy": {"handles_types": ["contact"], "desc": "halves contact damage; weak to Fire"},
    "furcoat": {"handles_types": ["physical"], "desc": "doubles Defense — handles physical attackers"},
    "multiscale": {"handles_abilities": ["first hit"], "desc": "halves damage at full HP — setup opportunity"},
    "shadowshield": {"handles_abilities": ["first hit"], "desc": "halves damage at full HP"},
    "filter": {"handles_abilities": ["super effective"], "desc": "reduces super-effective damage by 25%"},
    "solidrock": {"handles_abilities": ["super effective"], "desc": "reduces super-effective damage by 25%"},
    "prismarmor": {"handles_abilities": ["super effective"], "desc": "reduces super-effective damage by 25%"},
    "icescales": {"handles_types": ["special"], "desc": "halves special damage — walls special attackers"},
    "wonderguard": {"handles_abilities": ["non-super-effective"], "desc": "immune to non-super-effective moves"},
    "overcoat": {"handles_abilities": ["powder", "weather chip"], "desc": "immune to powder moves and weather damage"},
    "contrary": {"handles_abilities": ["stat drops"], "desc": "stat drops become boosts"},
    "mirrorarmor": {"handles_abilities": ["stat drops"], "desc": "bounces back stat drops"},
    "purifyingsalt": {"handles_types": ["ghost"], "handles_abilities": ["status"], "desc": "halves Ghost damage, immune to status"},
}

# ============================================================================
# Item Handles Inference
# ============================================================================

ITEM_HANDLES_MAP = {
    "covertcloak": {"handles_abilities": ["secondary effects"], "desc": "blocks flinch, stat drops, and status from move effects"},
    "abilityshield": {"handles_abilities": ["ability suppression"], "desc": "prevents ability nullification (Mold Breaker, Neutralizing Gas)"},
    "airballoon": {"handles_types": ["ground"], "desc": "temporary Ground immunity until hit"},
    "safetygoggles": {"handles_abilities": ["powder", "weather chip"], "desc": "blocks powder moves and weather damage"},
    "shedshell": {"handles_abilities": ["trapping"], "desc": "escapes Arena Trap, Shadow Tag, etc."},
    "clearamulet": {"handles_abilities": ["stat drops", "intimidate"], "desc": "prevents stat reductions"},
    "protectivepads": {"handles_abilities": ["contact punishment"], "desc": "blocks Rocky Helmet/Iron Barbs/Rough Skin chip"},
}

# ============================================================================
# All Pokemon Types for matchup inference
# ============================================================================

ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic",
    "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy",
]

# ============================================================================
# Nature → EV tendency mapping
# ============================================================================

NATURE_STAT_BOOST = {
    "adamant": "atk", "jolly": "spe", "modest": "spa", "timid": "spe",
    "bold": "def", "impish": "def", "calm": "spd", "careful": "spd",
    "brave": "atk", "quiet": "spa", "relaxed": "def", "sassy": "spd",
    "naive": "spe", "hasty": "spe", "naughty": "atk", "lonely": "atk",
    "mild": "spa", "rash": "spa",
}

NATURE_STAT_DROP = {
    "adamant": "spa", "jolly": "spa", "modest": "atk", "timid": "atk",
    "bold": "atk", "impish": "spa", "calm": "atk", "careful": "spa",
    "brave": "spe", "quiet": "spe", "relaxed": "spe", "sassy": "spe",
    "naive": "spd", "hasty": "def", "naughty": "spd", "lonely": "def",
    "mild": "def", "rash": "spd",
}

# ============================================================================
# Offensive move type lookup for handles inference
# ============================================================================

MOVE_TYPE_MAP = {
    # We'll use the moves.json data at runtime, but keep a static fallback
    # for common coverage moves. The engine looks up from data first.
}


def _get_move_data(move_name: str) -> dict:
    """Look up move data from the game data files."""
    try:
        from data import all_move_json
        return all_move_json.get(normalize_name(move_name), {})
    except (ImportError, Exception):
        return {}


def _get_pokedex_entry(species: str) -> dict:
    """Look up pokedex data for a species."""
    return pokedex.get(normalize_name(species), {})


def _ev_val(val) -> int:
    """Safely parse an EV value."""
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


# ============================================================================
# Role Classification Engine
# ============================================================================


def _classify_role(
    species: str,
    moves: set,
    item: str,
    ability: str,
    evs: dict,
    nature: str,
    base_stats: dict,
) -> tuple:
    """
    Classify a Pokemon's primary role and tags from build signals.

    Returns: (primary_role: str, role_tags: list[str], is_win_condition: bool, confidence: float)
    """
    ev_hp = _ev_val(evs.get("hp"))
    ev_atk = _ev_val(evs.get("atk"))
    ev_def = _ev_val(evs.get("def"))
    ev_spa = _ev_val(evs.get("spa"))
    ev_spd = _ev_val(evs.get("spd"))
    ev_spe = _ev_val(evs.get("spe"))

    base_hp = base_stats.get("hp", 0)
    base_atk = base_stats.get("attack", 0)
    base_def = base_stats.get("defense", 0)
    base_spa = base_stats.get("special-attack", 0)
    base_spd = base_stats.get("special-defense", 0)
    base_spe = base_stats.get("speed", 0)

    # Derive nature influence
    nature_norm = normalize_name(nature) if nature else ""
    nature_boosts = NATURE_STAT_BOOST.get(nature_norm, "")
    nature_drops = NATURE_STAT_DROP.get(nature_norm, "")

    # Move category flags
    has_recovery = bool(moves & RECOVERY_MOVES)
    has_setup = bool(moves & SETUP_MOVES)
    has_pivot = bool(moves & PIVOT_MOVES)
    has_hazard = bool(moves & HAZARD_MOVES)
    has_hazard_removal = bool(moves & HAZARD_REMOVAL_MOVES)
    has_phazing = bool(moves & PHAZING_MOVES)
    has_status = bool(moves & STATUS_MOVES)
    has_cleric = bool(moves & CLERIC_MOVES)

    # Item categories
    passive_items = {"leftovers", "heavydutyboots", "blacksludge", "eviolite", "rockyhelmet", "shedshell"}
    offensive_items = {"choiceband", "choicespecs", "lifeorb", "expertbelt", "muscleband", "wiseglasses", "loadeddice", "throatspray"}
    choice_items = {"choiceband", "choicespecs", "choicescarf"}
    scarf_item = item == "choicescarf"

    is_passive_item = item in passive_items
    is_offensive_item = item in offensive_items

    # EV thresholds
    max_ev = 252
    high_ev = 200
    has_max_spd_evs = ev_spd >= high_ev
    has_max_def_evs = ev_def >= high_ev
    has_max_hp_evs = ev_hp >= high_ev
    has_offensive_evs = ev_atk >= high_ev or ev_spa >= high_ev
    has_speed_evs = ev_spe >= high_ev
    total_bulk_evs = ev_hp + ev_def + ev_spd
    total_offense_evs = ev_atk + ev_spa + ev_spe

    # Scoring signals
    role_tags = []
    scores = {}
    confidence = 0.7  # base

    # Special Wall signals
    sw_score = 0.0
    if has_max_spd_evs:
        sw_score += 3.0
    if has_max_hp_evs:
        sw_score += 1.5
    if has_recovery:
        sw_score += 2.5
    if is_passive_item:
        sw_score += 1.5
    if nature_boosts == "spd":
        sw_score += 1.0
    if base_spd >= 100:
        sw_score += 1.0
    if ability in ("unaware", "naturalcure", "regenerator"):
        sw_score += 1.0
    scores["special wall"] = sw_score

    # Physical Wall signals
    pw_score = 0.0
    if has_max_def_evs:
        pw_score += 3.0
    if has_max_hp_evs:
        pw_score += 1.5
    if has_recovery:
        pw_score += 2.5
    if is_passive_item:
        pw_score += 1.5
    if item == "rockyhelmet":
        pw_score += 1.0
    if nature_boosts == "def":
        pw_score += 1.0
    if base_def >= 100:
        pw_score += 1.0
    if ability in ("unaware", "regenerator", "ironbarbs", "roughskin"):
        pw_score += 1.0
    scores["physical wall"] = pw_score

    # Setup Sweeper signals
    ss_score = 0.0
    if has_setup:
        ss_score += 3.5
    if has_offensive_evs:
        ss_score += 2.0
    if has_speed_evs:
        ss_score += 1.5
    if item in ("lifeorb", "weaknesspolicy", "lumberry", "sitrusberry", "whiteherb"):
        ss_score += 1.0
    if nature_boosts in ("atk", "spa", "spe"):
        ss_score += 0.5
    if not has_recovery:
        ss_score += 0.5  # sweepers typically don't waste a move on recovery
    scores["setup sweeper"] = ss_score

    # Fast Attacker signals
    fa_score = 0.0
    if has_speed_evs and has_offensive_evs and not has_setup:
        fa_score += 4.0
    if is_offensive_item and item not in choice_items:
        fa_score += 1.5
    if scarf_item:
        fa_score += 2.5
    if not has_recovery:
        fa_score += 0.5
    if base_spe >= 100:
        fa_score += 1.0
    scores["fast attacker"] = fa_score

    # Wallbreaker signals
    wb_score = 0.0
    if item in ("choiceband", "choicespecs"):
        wb_score += 4.0
    if has_offensive_evs and not has_setup:
        wb_score += 2.0
    if is_offensive_item:
        wb_score += 1.5
    if not has_recovery:
        wb_score += 0.5
    scores["wallbreaker"] = wb_score

    # Pivot signals
    pv_score = 0.0
    if has_pivot:
        pv_score += 3.5
    if ability == "regenerator":
        pv_score += 2.5
    if total_bulk_evs >= 300:
        pv_score += 1.5
    if item in ("heavydutyboots", "assaultvest", "eviolite"):
        pv_score += 1.0
    if scarf_item and has_pivot:
        pv_score += 1.0
    scores["pivot"] = pv_score

    # SubStall / Pressure signals
    sub_score = 0.0
    if "substitute" in moves:
        sub_score += 2.5
        if item in ("leftovers", "blacksludge"):
            sub_score += 2.0
        if "protect" in moves:
            sub_score += 2.0
        if ability == "poisonheal":
            sub_score += 2.5
        if ability == "pressure":
            sub_score += 1.0
    scores["substall"] = sub_score

    # Support / Utility signals
    sup_score = 0.0
    if has_hazard:
        sup_score += 2.0
    if has_hazard_removal:
        sup_score += 2.0
    if has_status:
        sup_score += 1.0
    if has_cleric:
        sup_score += 1.5
    if has_phazing:
        sup_score += 1.0
    if item in ("lightclay", "mentalherb", "focussash"):
        sup_score += 1.5
    if "reflect" in moves or "lightscreen" in moves or "auroraveil" in moves:
        sup_score += 2.5
    scores["support"] = sup_score

    # Add role tags based on thresholds
    if has_hazard:
        role_tags.append("hazard setter")
    if has_hazard_removal:
        role_tags.append("hazard remover")
    if has_pivot:
        role_tags.append("pivot")
    if has_phazing:
        role_tags.append("phazer")
    if has_recovery:
        role_tags.append("recovery user")
    if has_cleric:
        role_tags.append("cleric")
    if has_status:
        role_tags.append("status spreader")
    if "substitute" in moves:
        role_tags.append("substitute user")
    if "reflect" in moves or "lightscreen" in moves or "auroraveil" in moves:
        role_tags.append("screens setter")

    # Determine primary role from highest score
    primary_role = max(scores, key=scores.get)
    top_score = scores[primary_role]

    # Add primary role to tags if not already
    if primary_role not in role_tags:
        role_tags.insert(0, primary_role)

    # Dual roles: if another role scores within 70% of the top, add it
    for role, score in scores.items():
        if role != primary_role and score >= top_score * 0.7 and score >= 3.0:
            if role not in role_tags:
                role_tags.append(role)

    # Confidence: higher when primary role is clearly dominant
    if top_score >= 8.0:
        confidence = 0.95
    elif top_score >= 6.0:
        confidence = 0.85
    elif top_score >= 4.0:
        confidence = 0.75
    elif top_score >= 2.0:
        confidence = 0.6
    else:
        confidence = 0.4

    # Win condition: setup sweepers, primary breakers, or mons with setup + offensive stats
    is_win_condition = False
    if primary_role == "setup sweeper":
        is_win_condition = True
    elif primary_role == "wallbreaker" and (is_offensive_item or has_offensive_evs):
        is_win_condition = True
    elif has_setup and has_offensive_evs:
        is_win_condition = True

    return primary_role, role_tags, is_win_condition, confidence


# ============================================================================
# Handles Inference
# ============================================================================


def _infer_handles(
    species: str,
    types: list,
    ability: str,
    item: str,
    moves: set,
    evs: dict,
    base_stats: dict,
) -> tuple:
    """
    Infer what types and abilities this Pokemon handles.

    Returns: (handles_types: list[str], handles_abilities: list[str])
    """
    handles_types = set()
    handles_abilities = set()

    ev_def = _ev_val(evs.get("def"))
    ev_spd = _ev_val(evs.get("spd"))
    ev_hp = _ev_val(evs.get("hp"))
    total_bulk_evs = ev_hp + ev_def + ev_spd
    is_bulky = total_bulk_evs >= 300

    base_def = base_stats.get("defense", 0)
    base_spd = base_stats.get("special-defense", 0)
    base_hp = base_stats.get("hp", 0)

    # --- Ability-based handles ---
    ability_norm = normalize_name(ability)
    if ability_norm in ABILITY_HANDLES_MAP:
        entry = ABILITY_HANDLES_MAP[ability_norm]
        for t in entry.get("handles_types", []):
            handles_types.add(t)
        for a in entry.get("handles_abilities", []):
            handles_abilities.add(a)

    # --- Item-based handles ---
    item_norm = normalize_name(item)
    if item_norm in ITEM_HANDLES_MAP:
        entry = ITEM_HANDLES_MAP[item_norm]
        for t in entry.get("handles_types", []):
            handles_types.add(t)
        for a in entry.get("handles_abilities", []):
            handles_abilities.add(a)

    # --- Type-based handles ---
    # A bulky mon (high bulk EVs or high base stats) that resists a type "handles" it
    has_meaningful_bulk = is_bulky or (base_def >= 90 and base_hp >= 70) or (base_spd >= 90 and base_hp >= 70)

    if has_meaningful_bulk and types:
        for attacking_type in ALL_TYPES:
            try:
                effectiveness = type_effectiveness_modifier(attacking_type, types)
                if effectiveness <= 0.5:  # resist or immune
                    handles_types.add(attacking_type)
                elif effectiveness == 0:  # immune
                    handles_types.add(attacking_type)
            except (KeyError, Exception):
                pass

    # --- Move-based handles: if we have a super-effective move against a type
    # AND we have bulk to take hits from that type, we "handle" it ---
    if has_meaningful_bulk:
        for move_name in moves:
            move_data = _get_move_data(move_name)
            if not move_data:
                continue
            move_type = move_data.get(constants.TYPE, move_data.get("type", ""))
            if not move_type:
                continue
            move_type = normalize_name(move_type)
            bp = move_data.get(constants.BASE_POWER, move_data.get("basePower", 0))
            if bp and int(bp) > 0:
                # Check what types this move is super effective against
                for defending_type in ALL_TYPES:
                    try:
                        eff = type_effectiveness_modifier(move_type, [defending_type])
                        if eff >= 2.0:
                            handles_types.add(defending_type)
                    except (KeyError, Exception):
                        pass

    return sorted(handles_types), sorted(handles_abilities)


# ============================================================================
# Loadout Annotations
# ============================================================================


def _annotate_loadout(
    species: str,
    item: str,
    ability: str,
    moves: set,
    evs: dict,
    nature: str,
) -> Dict[str, str]:
    """Generate strategic annotations for each loadout element."""
    annotations = {}

    # Item annotation
    item_norm = normalize_name(item)
    if item_norm in ITEM_ANNOTATIONS:
        annotations[f"item:{item}"] = ITEM_ANNOTATIONS[item_norm]
    elif item:
        annotations[f"item:{item}"] = f"{item} — no specific strategic annotation available"

    # Ability annotation
    ability_norm = normalize_name(ability)
    if ability_norm in ABILITY_HANDLES_MAP:
        annotations[f"ability:{ability}"] = ABILITY_HANDLES_MAP[ability_norm]["desc"]
    elif ability:
        annotations[f"ability:{ability}"] = f"{ability} — standard ability"

    # Move annotations
    for move in moves:
        move_norm = normalize_name(move)
        if move_norm in RECOVERY_MOVES:
            annotations[f"move:{move}"] = "recovery — sustains longevity and defensive presence"
        elif move_norm in SETUP_MOVES:
            annotations[f"move:{move}"] = "setup — boosts stats for sweeping potential"
        elif move_norm in PIVOT_MOVES:
            annotations[f"move:{move}"] = "pivot — maintains momentum and enables safe switching"
        elif move_norm in HAZARD_MOVES:
            annotations[f"move:{move}"] = "hazard — chips opponents on switch-in"
        elif move_norm in HAZARD_REMOVAL_MOVES:
            annotations[f"move:{move}"] = "hazard removal — clears entry hazards from our side"
        elif move_norm in PHAZING_MOVES:
            annotations[f"move:{move}"] = "phazing — forces opponent switches, racks hazard chip"
        elif move_norm in STATUS_MOVES:
            annotations[f"move:{move}"] = "status — disrupts opponent via status condition or utility"
        else:
            move_data = _get_move_data(move_norm)
            bp = move_data.get(constants.BASE_POWER, move_data.get("basePower", 0)) if move_data else 0
            if bp and int(bp) > 0:
                move_type = move_data.get(constants.TYPE, move_data.get("type", "unknown"))
                cat = move_data.get(constants.CATEGORY, move_data.get("category", "unknown"))
                annotations[f"move:{move}"] = f"offensive ({cat} {move_type}, {bp}bp)"
            elif move_data:
                annotations[f"move:{move}"] = "utility/status move"
            else:
                annotations[f"move:{move}"] = "move (no data available)"

    # EV spread annotation
    ev_parts = []
    ev_hp = _ev_val(evs.get("hp"))
    ev_atk = _ev_val(evs.get("atk"))
    ev_def = _ev_val(evs.get("def"))
    ev_spa = _ev_val(evs.get("spa"))
    ev_spd = _ev_val(evs.get("spd"))
    ev_spe = _ev_val(evs.get("spe"))

    if ev_hp >= 200:
        ev_parts.append("max HP investment for overall bulk")
    if ev_def >= 200:
        ev_parts.append("max Def investment signals physical tank role")
    if ev_spd >= 200:
        ev_parts.append("max SpDef investment signals special tank role")
    if ev_atk >= 200:
        ev_parts.append("max Atk investment for physical damage output")
    if ev_spa >= 200:
        ev_parts.append("max SpA investment for special damage output")
    if ev_spe >= 200:
        ev_parts.append("max Speed investment to outpace threats")

    if ev_parts:
        annotations["ev_spread"] = "; ".join(ev_parts)

    # Nature annotation
    nature_norm = normalize_name(nature) if nature else ""
    if nature_norm in NATURE_STAT_BOOST:
        boosted = NATURE_STAT_BOOST[nature_norm]
        dropped = NATURE_STAT_DROP.get(nature_norm, "")
        stat_names = {"atk": "Attack", "def": "Defense", "spa": "Sp.Atk", "spd": "Sp.Def", "spe": "Speed"}
        boost_name = stat_names.get(boosted, boosted)
        drop_name = stat_names.get(dropped, dropped)
        annotations[f"nature:{nature}"] = f"+{boost_name} -{drop_name} — prioritizes {boost_name}"

    return annotations


# ============================================================================
# Correct Play Rules Generation
# ============================================================================


def _generate_correct_play(
    primary_role: str,
    role_tags: list,
    has_recovery: bool,
    recovery_threshold: float,
) -> List[str]:
    """Generate behavioral rules from the inferred role."""
    rules = []

    if primary_role == "special wall" or "special wall" in role_tags:
        rules.append(f"Use recovery when below {int(recovery_threshold * 100)}% HP")
        rules.append("Stay in vs special attackers you resist or are neutral to")
        rules.append("Switch out vs strong physical attackers")
        rules.append("Absorb status moves if you have Natural Cure or cleric support")

    if primary_role == "physical wall" or "physical wall" in role_tags:
        rules.append(f"Use recovery aggressively when below {int(recovery_threshold * 100)}% HP")
        rules.append("Stay in vs physical attackers you resist")
        rules.append("Switch out vs strong special attackers or setup sweepers you can't phaze")
        if "phazer" in role_tags:
            rules.append("Phaze (Whirlwind/Roar/Dragon Tail) setup sweepers before they get too many boosts")

    if primary_role == "setup sweeper" or "setup sweeper" in role_tags:
        rules.append("Set up on resisted hits or passive mons")
        rules.append("Don't set up when the opponent can 2HKO through the boost")
        rules.append("Attack once at +2 or higher if threats remain")
        rules.append("Preserve HP for setup opportunities — don't take unnecessary chip")

    if primary_role == "fast attacker" or "fast attacker" in role_tags:
        rules.append("Attack aggressively — your role is to deal damage")
        rules.append("Don't stay in vs mons that resist your coverage")
        rules.append("Use your speed advantage to apply pressure before they switch")

    if primary_role == "wallbreaker":
        rules.append("Hit hard immediately — your role is to break through walls")
        rules.append("Predict switches with coverage moves")
        rules.append("Don't stay in vs faster threats that can revenge kill")

    if primary_role == "pivot" or "pivot" in role_tags:
        rules.append("U-turn/Volt Switch/pivot on predicted switches to maintain momentum")
        rules.append("Don't stay in unnecessarily — your value is in cycling")
        rules.append("Pivot into teammates that match up well against the opponent's switch-in")

    if primary_role == "substall":
        rules.append("Maintain Substitute to block status and chip")
        rules.append("Recover HP to sustain Substitute cycles")
        rules.append("PP stall key moves with Protect + Substitute")

    if "hazard setter" in role_tags:
        rules.append("Prioritize getting hazards up early in the game")
        rules.append("Don't re-set hazards that are already up")

    if "hazard remover" in role_tags:
        rules.append("Clear hazards when your team is taking significant entry damage")
        rules.append("Save Defog/Rapid Spin for when it matters — don't waste turns on an empty field")

    if "screens setter" in role_tags:
        rules.append("Set screens early to enable teammates to set up")

    if "cleric" in role_tags:
        rules.append("Preserve yourself to heal teammates' status conditions")

    return rules


# ============================================================================
# Recovery Threshold Inference
# ============================================================================


def _infer_recovery_threshold(primary_role: str, has_recovery: bool, ability: str) -> float:
    """Infer the HP% at which recovery should be prioritized."""
    if not has_recovery:
        return 0.0  # no recovery move available

    base_threshold = 0.50  # default: recover around 50%

    if primary_role in ("special wall", "physical wall"):
        base_threshold = 0.60  # walls should recover proactively
    elif primary_role == "substall":
        base_threshold = 0.75  # substall needs HP for Substitutes
    elif primary_role in ("pivot", "support"):
        base_threshold = 0.50
    elif primary_role in ("setup sweeper", "fast attacker", "wallbreaker"):
        base_threshold = 0.35  # offensive mons only recover when desperate

    # Regenerator mons recover passively, so threshold can be lower
    if normalize_name(ability) == "regenerator":
        base_threshold = max(base_threshold - 0.10, 0.25)

    # Poison Heal provides passive recovery
    if normalize_name(ability) == "poisonheal":
        base_threshold = max(base_threshold - 0.15, 0.20)

    return base_threshold


# ============================================================================
# Main Inference Function
# ============================================================================


def infer_pokemon_intent(pokemon_dict: Dict[str, Any]) -> PokemonIntent:
    """
    Infer the strategic intent of a single Pokemon from its build.

    Args:
        pokemon_dict: Dict with keys: species, item, ability, moves, evs, nature
                      (as produced by team_converter.export_to_dict)

    Returns:
        PokemonIntent with classified role, tags, handles, annotations, rules
    """
    species = normalize_name(pokemon_dict.get("species", ""))
    item = normalize_name(pokemon_dict.get("item", ""))
    ability = normalize_name(pokemon_dict.get("ability", ""))
    moves_raw = pokemon_dict.get("moves", [])
    moves = {normalize_name(m) for m in moves_raw}
    evs = pokemon_dict.get("evs", {}) or {}
    nature = pokemon_dict.get("nature", "")

    # Look up base stats from pokedex
    dex_entry = _get_pokedex_entry(species)
    base_stats = dex_entry.get(constants.BASESTATS, {})
    types = dex_entry.get(constants.TYPES, [])

    # Phase 1: Role classification
    primary_role, role_tags, is_win_condition, confidence = _classify_role(
        species, moves, item, ability, evs, nature, base_stats,
    )

    # Phase 2: Handles inference
    handles_types, handles_abilities = _infer_handles(
        species, types, ability, item, moves, evs, base_stats,
    )

    # Phase 3: Loadout annotations
    loadout_annotations = _annotate_loadout(
        species, pokemon_dict.get("item", ""), pokemon_dict.get("ability", ""),
        moves_raw, evs, nature,
    )

    # Phase 4: Recovery threshold
    has_recovery = bool(moves & RECOVERY_MOVES)
    recovery_threshold = _infer_recovery_threshold(primary_role, has_recovery, ability)

    # Phase 5: Correct play rules
    correct_play = _generate_correct_play(primary_role, role_tags, has_recovery, recovery_threshold)

    return PokemonIntent(
        role=primary_role,
        role_tags=role_tags,
        handles_types=handles_types,
        handles_abilities=handles_abilities,
        loadout_annotations=loadout_annotations,
        correct_play=correct_play,
        recovery_threshold=recovery_threshold,
        is_win_condition=is_win_condition,
        confidence=confidence,
    )


# ============================================================================
# Team Context Builder
# ============================================================================


def build_team_context(team_dict: List[Dict[str, Any]]) -> TeamContext:
    """
    Build a full TeamContext from a team dictionary.

    Args:
        team_dict: List of Pokemon dicts (from team_converter.export_to_dict)

    Returns:
        TeamContext with inferred team style, win conditions, and per-mon intent
    """
    if not team_dict:
        return TeamContext(
            team_style="balance",
            win_conditions=[],
            pokemon_intent={},
            team_rules=["No team loaded — play conservatively"],
        )

    pokemon_intent = {}
    win_conditions = []
    role_counts = {}

    for pkmn_dict in team_dict:
        species = normalize_name(pkmn_dict.get("species", ""))
        if not species:
            continue
        intent = infer_pokemon_intent(pkmn_dict)
        pokemon_intent[species] = intent
        if intent.is_win_condition:
            win_conditions.append(species)
        # Count roles for team style inference
        role_counts[intent.role] = role_counts.get(intent.role, 0) + 1
        for tag in intent.role_tags:
            role_counts[tag] = role_counts.get(tag, 0) + 1

    # Infer team style
    team_style = _infer_team_style(role_counts, len(team_dict))

    # Generate team-level rules
    team_rules = _generate_team_rules(team_style, win_conditions, pokemon_intent)

    return TeamContext(
        team_style=team_style,
        win_conditions=win_conditions,
        pokemon_intent=pokemon_intent,
        team_rules=team_rules,
    )


def _infer_team_style(role_counts: Dict[str, int], team_size: int) -> str:
    """Infer team style from aggregate role distribution."""
    walls = role_counts.get("special wall", 0) + role_counts.get("physical wall", 0)
    recovery = role_counts.get("recovery user", 0)
    setup = role_counts.get("setup sweeper", 0)
    pivots = role_counts.get("pivot", 0)
    hazards = role_counts.get("hazard setter", 0)
    breakers = role_counts.get("wallbreaker", 0) + role_counts.get("fast attacker", 0)
    screens = role_counts.get("screens setter", 0)

    # Stall: heavy walls + recovery, minimal offense
    if walls >= 3 and recovery >= 4 and setup <= 1 and breakers <= 1:
        return "stall"

    # Fat: defensive backbone with some offense
    if walls >= 2 and recovery >= 3 and pivots >= 2:
        return "fat"

    # Hyper Offense: screens + multiple setup sweepers
    if screens >= 1 and setup >= 2:
        return "hyper offense"
    if setup >= 3 and recovery <= 1:
        return "hyper offense"

    # Pivot Heavy: lots of pivoting, bulk focus
    if pivots >= 3 and walls >= 1:
        return "pivot heavy"

    # Bulky Offense: some bulk + offensive pressure
    if breakers >= 2 and (walls >= 1 or recovery >= 2) and pivots >= 1:
        return "bulky offense"

    # Balance: well-rounded
    return "balance"


def _generate_team_rules(
    team_style: str,
    win_conditions: List[str],
    pokemon_intent: Dict[str, PokemonIntent],
) -> List[str]:
    """Generate team-level strategic rules."""
    rules = []

    if win_conditions:
        wc_names = ", ".join(win_conditions)
        rules.append(f"Preserve win conditions ({wc_names}) — don't sacrifice them for chip damage")
        rules.append("Create setup opportunities for win conditions via pivoting and wallbreaking")

    if team_style == "stall":
        rules.append("Play passively — outlast the opponent through recovery and hazard chip")
        rules.append("Never trade mons unnecessarily; every team member serves a defensive role")
        rules.append("Get hazards up ASAP and prevent hazard removal")
    elif team_style == "fat":
        rules.append("Control the game through defensive cycling and pivoting")
        rules.append("Apply chip damage via hazards and status; let the opponent break themselves")
        rules.append("Recover aggressively to maintain defensive backbone")
    elif team_style == "hyper offense":
        rules.append("Set up screens/hazards immediately, then sweep")
        rules.append("Sacrifice lead if needed to get screens/hazards up")
        rules.append("Don't play passively — attack before they can set up defenses")
    elif team_style == "pivot heavy":
        rules.append("Maintain momentum through constant pivoting")
        rules.append("Stack hazards to punish opponent switching")
        rules.append("Get free switches for your offensive threats via U-turn/Parting Shot")
    elif team_style == "bulky offense":
        rules.append("Break through walls first, then clean up with win conditions")
        rules.append("Use bulk to pivot safely and create attacking opportunities")
        rules.append("Don't overcommit — balance aggression with preservation")
    else:  # balance
        rules.append("Adapt to the opponent's playstyle — be reactive")
        rules.append("Keep options open; balance offensive pressure with defensive stability")

    # Hazard awareness
    hazard_setters = [name for name, intent in pokemon_intent.items() if "hazard setter" in intent.role_tags]
    hazard_removers = [name for name, intent in pokemon_intent.items() if "hazard remover" in intent.role_tags]
    if hazard_setters:
        rules.append(f"Hazard setters: {', '.join(hazard_setters)} — prioritize getting hazards up")
    if hazard_removers:
        rules.append(f"Hazard removers: {', '.join(hazard_removers)} — preserve for hazard control")
    elif not hazard_removers:
        rules.append("No hazard removal — avoid taking unnecessary switches into hazards")

    return rules


# ============================================================================
# Integration Helpers
# ============================================================================


def get_intent_for_pokemon(team_context: TeamContext, pokemon_name: str) -> Optional[PokemonIntent]:
    """Get the intent for a specific Pokemon from the team context."""
    name_norm = normalize_name(pokemon_name)
    return team_context.pokemon_intent.get(name_norm)


def should_recover(
    team_context: TeamContext,
    pokemon_name: str,
    current_hp_fraction: float,
) -> bool:
    """Check if a Pokemon should use recovery based on intent thresholds."""
    intent = get_intent_for_pokemon(team_context, pokemon_name)
    if intent is None:
        return current_hp_fraction < 0.4  # default
    return current_hp_fraction < intent.recovery_threshold


def get_role_score_adjustments(
    team_context: TeamContext,
    pokemon_name: str,
    opponent_types: List[str],
    is_opponent_physical: bool,
    is_opponent_special: bool,
    is_opponent_boosted: bool,
) -> Dict[str, float]:
    """
    Get soft scoring adjustments based on team intent.

    Returns a dict of adjustment categories -> multipliers:
      "recovery_boost", "stay_in_bonus", "switch_bonus",
      "setup_boost", "pivot_boost"
    """
    adjustments = {}
    intent = get_intent_for_pokemon(team_context, pokemon_name)
    if intent is None:
        return adjustments

    role = intent.role

    # Recovery boost: walls below threshold
    if role in ("special wall", "physical wall", "substall"):
        adjustments["recovery_boost"] = 1.3  # 30% boost to recovery moves

    # Stay-in bonus: if opponent type is in handles_types
    stay_in = 1.0
    for opp_type in opponent_types:
        if opp_type in intent.handles_types:
            stay_in += 0.15  # 15% per handled type
    if stay_in > 1.0:
        adjustments["stay_in_bonus"] = min(stay_in, 1.5)

    # Switch bonus: wrong matchup for this role
    if role == "special wall" and is_opponent_physical:
        adjustments["switch_bonus"] = 1.2
    elif role == "physical wall" and is_opponent_special:
        adjustments["switch_bonus"] = 1.2

    # Unaware vs boosted opponents
    if "setup sweeper" in intent.handles_abilities and is_opponent_boosted:
        adjustments["stay_in_bonus"] = adjustments.get("stay_in_bonus", 1.0) + 0.3

    # Setup boost: setup sweeper facing favorable matchup
    if intent.is_win_condition and role == "setup sweeper":
        if not is_opponent_boosted:  # don't set up into boosted threats
            adjustments["setup_boost"] = 1.25

    # Pivot boost: pivot mons should keep cycling
    if "pivot" in intent.role_tags:
        adjustments["pivot_boost"] = 1.2

    return adjustments
