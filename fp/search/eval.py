"""
1-Ply Damage-Based Position Evaluation for Fouler-Play

Replaces MCTS rollout scores with deterministic damage calculations.
For each legal move, computes a score using poke-engine's calculate_damage()
with minimax-style opponent response estimation.

Integrates tempo awareness, item awareness, sacking prevention, and KO detection.
"""

import logging

import constants
from fp.battle import Battle
from fp.helpers import POKEMON_TYPE_INDICES, normalize_name, type_effectiveness_modifier
from fp.playstyle_config import RECOVERY_MOVES, PIVOT_MOVES, HAZARD_MOVES
from fp.movepool_tracker import get_threat_category, ThreatCategory
from fp.search.speed_order import assess_speed_order
from data import all_move_json

logger = logging.getLogger(__name__)

# Move category sets (normalized)
_RECOVERY_NORM = {normalize_name(m) for m in RECOVERY_MOVES}
_PIVOT_NORM = {normalize_name(m) for m in PIVOT_MOVES}
_HAZARD_NORM = {normalize_name(m) for m in HAZARD_MOVES}

STATUS_MOVES_OFFENSIVE = {
    "toxic", "willowisp", "thunderwave", "spore", "sleeppowder",
    "stunspore", "glare", "nuzzle", "yawn", "hypnosis",
}

PHAZE_MOVES = {"whirlwind", "roar", "dragontail", "circlethrow"}

# Items that grant hazard immunity on switch
HAZARD_IMMUNITY_ITEMS = {"heavydutyboots"}

# Choice items
CHOICE_ITEMS = {"choiceband", "choicespecs", "choicescarf"}

# Passive recovery items
PASSIVE_RECOVERY_ITEMS = {"leftovers", "blacksludge"}

# Contact moves (subset for Rocky Helmet check)
CONTACT_CHECK_ITEMS = {"rockyhelmet"}

# Regenerator ability
REGENERATOR_ABILITY = "regenerator"

# Setup moves that signal "this Pokemon will boost and sweep if given time"
SETUP_MOVES_SET = {
    "nastyplot", "swordsdance", "dragondance", "calmmind", "quiverdance",
    "bulkup", "bellydrum", "shiftgear", "tidyup", "irondefense",
    "agility", "autotomize", "shellsmash", "coil", "victorydance",
    "growth", "workup",
}


_VALID_TYPES = set(POKEMON_TYPE_INDICES)

# Fixed-damage move support.
# These moves have base power 0 but still represent meaningful progress.
_HALF_HP_DAMAGE_MOVES = {
    "superfang",
    "ruination",
    "naturesmadness",
    "naturefury",
}


def _is_contact_move(move_name: str, move_data: dict | None = None) -> bool:
    data = move_data if isinstance(move_data, dict) else all_move_json.get(move_name, {})
    flags = data.get("flags", {}) if isinstance(data, dict) else {}
    if isinstance(flags, dict):
        return bool(flags.get("contact"))
    return False


def _contact_recoil_fraction(pokemon) -> float:
    """Estimate passive recoil dealt to contact attackers when pokemon is hit."""
    if pokemon is None:
        return 0.0
    recoil = 0.0
    item = normalize_name(getattr(pokemon, "item", "") or "")
    if item == "rockyhelmet":
        recoil += 1.0 / 6.0
    ability = normalize_name(getattr(pokemon, "ability", "") or "")
    if ability in {"roughskin", "ironbarbs"}:
        recoil += 1.0 / 8.0
    return recoil


def _is_fixed_damage_move(move_name: str, move_data: dict | None = None) -> bool:
    """True when the move deals fixed or HP-fraction damage despite 0 BP."""
    norm = normalize_name(move_name)
    if norm in _HALF_HP_DAMAGE_MOVES:
        return True

    data = move_data if isinstance(move_data, dict) else all_move_json.get(move_name, {})
    fixed_damage = data.get("damage")
    if isinstance(fixed_damage, (int, float)) and fixed_damage > 0:
        return True
    if isinstance(fixed_damage, str) and normalize_name(fixed_damage) == "level":
        return True
    return False


def _is_damaging_move(move_name: str, move_data: dict | None = None) -> bool:
    """True for attack-category moves that can deal immediate HP damage."""
    data = move_data if isinstance(move_data, dict) else all_move_json.get(move_name, {})
    category = data.get(constants.CATEGORY, "Status")
    if category not in (constants.PHYSICAL, constants.SPECIAL):
        return False
    base_power = data.get(constants.BASE_POWER, 0)
    return bool(base_power and base_power > 0) or _is_fixed_damage_move(move_name, data)


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


def _get_effective_types(pokemon) -> list:
    """Get a Pokemon's effective types, accounting for Terastallization."""
    base_types = _sanitize_type_list(getattr(pokemon, "types", []) or [])
    tera_type = _normalize_type_name(getattr(pokemon, "tera_type", None))
    if getattr(pokemon, "terastallized", False) and tera_type:
        return [tera_type]
    return base_types


def _get_our_moves(battle: Battle) -> list[tuple[str, dict]]:
    """Get list of (move_name, move_data) for our active Pokemon's usable moves."""
    active = battle.user.active
    if active is None:
        return []

    result = []
    for move in active.moves:
        move_name = move.name if hasattr(move, "name") else str(move)
        if hasattr(move, "disabled") and move.disabled:
            continue
        if hasattr(move, "current_pp") and move.current_pp <= 0:
            continue
        move_data = all_move_json.get(move_name, {})
        result.append((move_name, move_data))
    return result


def _estimate_damage_ratio(attacker, defender, move_name: str) -> float:
    """
    Estimate damage as fraction of defender's max HP.
    Uses the simplified formula from endgame.py as primary method.
    """
    if attacker is None or defender is None:
        return 0.0

    move_data = all_move_json.get(move_name, {})
    move_type = _normalize_type_name(move_data.get(constants.TYPE, "normal")) or "normal"
    category = move_data.get(constants.CATEGORY, "Status")
    base_power = move_data.get(constants.BASE_POWER, 0)

    if category not in (constants.PHYSICAL, constants.SPECIAL):
        return 0.0

    defender_max_hp = max(float(getattr(defender, "max_hp", 1) or 1), 1.0)
    defender_types = _get_effective_types(defender)
    effectiveness = type_effectiveness_modifier(move_type, defender_types)
    if effectiveness == 0:
        return 0.0

    # Fixed-damage moves (e.g., Seismic Toss, Night Shade) should not be
    # treated like 0-BP status moves. They are often critical progress lines.
    fixed_damage = move_data.get("damage")
    if isinstance(fixed_damage, (int, float)) and fixed_damage > 0:
        return min(float(fixed_damage) / defender_max_hp, 1.0)
    if isinstance(fixed_damage, str) and normalize_name(fixed_damage) == "level":
        raw_level = getattr(attacker, "level", None)
        if isinstance(raw_level, (int, float)) and raw_level > 0:
            level = float(raw_level)
        else:
            level = 100.0
        return min(level / defender_max_hp, 1.0)

    norm = normalize_name(move_name)
    if norm in _HALF_HP_DAMAGE_MOVES:
        current_hp = max(float(getattr(defender, "hp", 0) or 0), 0.0)
        return min((0.5 * current_hp) / defender_max_hp, 1.0)

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

    # Apply stat boosts
    if atk_boost > 0:
        atk *= (2 + atk_boost) / 2
    elif atk_boost < 0:
        atk *= 2 / (2 - atk_boost)

    if def_boost > 0:
        def_ *= (2 + def_boost) / 2
    elif def_boost < 0:
        def_ *= 2 / (2 - def_boost)

    # STAB — Tera type also grants STAB
    attacker_types = _sanitize_type_list(getattr(attacker, "types", []) or [])
    attacker_tera = _normalize_type_name(getattr(attacker, "tera_type", None))
    if getattr(attacker, "terastallized", False) and attacker_tera:
        if attacker_tera not in attacker_types:
            attacker_types.append(attacker_tera)
    stab = 1.5 if move_type in attacker_types else 1.0

    # Simplified damage formula (level 100)
    damage = (((2 * 100 / 5 + 2) * base_power * atk / def_) / 50 + 2) * effectiveness * stab

    return damage / defender_max_hp


def _apply_switch_progress_cap(
    scores: dict[str, float],
    moves: list[tuple[str, dict]],
    *,
    opp_can_ko: bool,
    bad_matchup: bool,
    can_slow_pivot_now: bool,
) -> None:
    """
    Keep switch scores from drowning out obvious on-board progress lines.

    This is intentionally conservative:
    - No cap when we are in danger or in a bad matchup.
    - Only cap when we already have a solid direct progress move.
    """
    if opp_can_ko or bad_matchup:
        return

    best_progress = 0.0
    for move_name, move_data in moves:
        move_score = float(scores.get(move_name, 0.0) or 0.0)
        if move_score <= 0:
            continue
        norm = normalize_name(move_name)
        if _is_damaging_move(move_name, move_data) or norm in _PIVOT_NORM or norm in PHAZE_MOVES:
            best_progress = max(best_progress, move_score)

    # Do not cap when no meaningful stay-in line exists.
    if best_progress < 0.08:
        return

    cap_mult = 1.35
    if can_slow_pivot_now:
        cap_mult = 1.15
    switch_cap = best_progress * cap_mult

    for move_name, move_score in list(scores.items()):
        if move_name.startswith("switch ") and move_score > switch_cap:
            scores[move_name] = switch_cap


def _has_priority(move_name: str) -> bool:
    """Check if a move has positive priority."""
    move_data = all_move_json.get(move_name, {})
    return move_data.get(constants.PRIORITY, 0) > 0


def _opponent_best_damage(battle: Battle) -> float:
    """Estimate opponent's best damage to our active as fraction of our max HP."""
    opp = battle.opponent.active
    our = battle.user.active
    if opp is None or our is None:
        return 0.0

    best = 0.0
    opp_moves = getattr(opp, "moves", []) or []
    for move in opp_moves:
        move_name = move.name if hasattr(move, "name") else str(move)
        dmg = _estimate_damage_ratio(opp, our, move_name)
        if dmg > best:
            best = dmg

    # If opponent has no revealed moves, estimate from STAB
    opp_eff_types = _get_effective_types(opp)
    if not opp_moves and opp_eff_types:
        # Conservative fallback when no moves are revealed yet.
        # Keep this modest; overestimating unknown damage creates premature
        # defensive lines (over-switching / over-healing) in neutral states.
        our_eff_types = _get_effective_types(our)
        for t in opp_eff_types:
            eff = type_effectiveness_modifier(t, our_eff_types)
            estimated = 0.12 * eff
            if estimated > best:
                best = estimated

    return best


def _opponent_can_ko_us(battle: Battle) -> bool:
    """Can the opponent KO our active this turn?"""
    our = battle.user.active
    if our is None:
        return False
    hp_ratio = our.hp / max(our.max_hp, 1)
    return _opponent_best_damage(battle) >= hp_ratio


def _is_free_turn(battle: Battle) -> bool:
    """
    Detect "free turns": we're safe enough that recovery wastes tempo.
    True when we're above 70% HP AND opponent's max damage is <15%.
    """
    our = battle.user.active
    if our is None:
        return False
    hp_ratio = our.hp / max(our.max_hp, 1)
    if hp_ratio < 0.70:
        return False
    opp_best = _opponent_best_damage(battle)
    return opp_best < 0.15


def _is_threatened(battle: Battle) -> bool:
    """Check if opponent can 2HKO us (we're threatened)."""
    our = battle.user.active
    if our is None:
        return False
    hp_ratio = our.hp / max(our.max_hp, 1)
    opp_best = _opponent_best_damage(battle)
    return opp_best * 2 >= hp_ratio


def _our_item_normalized(battle: Battle) -> str:
    """Get our active Pokemon's item, normalized."""
    active = battle.user.active
    if active is None:
        return ""
    item = getattr(active, "item", "") or ""
    return normalize_name(item)


def _hazard_cost(battle: Battle, pokemon=None) -> float:
    """Estimate hazard damage on switch-in as fraction of max HP."""
    if pokemon is None:
        return 0.0

    # Check Heavy-Duty Boots
    item = normalize_name(getattr(pokemon, "item", "") or "")
    if item in HAZARD_IMMUNITY_ITEMS:
        return 0.0

    cost = 0.0
    sc = battle.user.side_conditions

    # Stealth Rock
    if sc.get(constants.STEALTH_ROCK, 0) > 0:
        pkmn_types = _get_effective_types(pokemon)
        sr_eff = type_effectiveness_modifier("rock", pkmn_types)
        cost += 0.125 * sr_eff

    # Spikes (up to 3 layers)
    spikes = sc.get(constants.SPIKES, 0)
    if spikes > 0:
        # Check if grounded
        pkmn_types = _get_effective_types(pokemon)
        is_flying = "flying" in pkmn_types
        has_levitate = normalize_name(getattr(pokemon, "ability", "") or "") == "levitate"
        if not is_flying and not has_levitate:
            spike_dmg = {1: 0.125, 2: 0.1667, 3: 0.25}
            cost += spike_dmg.get(min(spikes, 3), 0.25)

    return cost


def _opponent_best_damage_to(opp, target) -> float:
    """Estimate opponent's best damage to a specific target as fraction of target's max HP."""
    if opp is None or target is None:
        return 0.0

    best = 0.0
    opp_moves = getattr(opp, "moves", []) or []
    for move in opp_moves:
        move_name = move.name if hasattr(move, "name") else str(move)
        dmg = _estimate_damage_ratio(opp, target, move_name)
        if dmg > best:
            best = dmg

    return best


def _opp_has_setup_potential(opp) -> bool:
    """Check if the opponent Pokemon is a known setup threat.

    Uses movepool tracker data (learned across battles) and also
    checks revealed moves in the current battle.
    """
    opp_name = getattr(opp, "name", "") or ""
    if not opp_name:
        return False

    # Check movepool tracker for known setup moves
    try:
        from fp.movepool_tracker import get_global_tracker
        tracker = get_global_tracker()
        data = tracker.get_movepool_data(opp_name)
        if data and data.status_moves:
            if data.status_moves & SETUP_MOVES_SET:
                return True
    except Exception:
        pass

    # Check currently revealed moves
    for move in getattr(opp, "moves", []) or []:
        move_name = move.name if hasattr(move, "name") else str(move)
        if normalize_name(move_name) in SETUP_MOVES_SET:
            return True

    return False


def _score_switch(battle: Battle, target_name: str) -> float:
    """Score a switch to a specific Pokemon.

    Uses three layers of information:
    1. Opponent's REVEALED MOVES for actual damage calculation
    2. STAB type matchup as fallback when no moves are revealed
    3. THREAT CATEGORY from movepool tracker to route to the right wall
       (e.g., Blissey for special threats, Skarmory for physical)
    """
    opp = battle.opponent.active
    if opp is None:
        return 0.0

    target = None
    for p in battle.user.reserve:
        if p.name == target_name:
            target = p
            break
    if target is None or target.hp <= 0:
        return 0.0

    score = 0.0
    target_types = _get_effective_types(target)
    opp_types = _get_effective_types(opp)

    # PRIMARY: Check opponent's revealed moves for actual damage to this target
    opp_moves = getattr(opp, "moves", []) or []
    opp_move_norm = {
        normalize_name(m.name if hasattr(m, "name") else str(m)) for m in opp_moves
    }
    if opp_moves:
        opp_best_dmg = _opponent_best_damage_to(opp, target)
        target_hp_ratio = target.hp / max(target.max_hp, 1)

        if opp_best_dmg >= target_hp_ratio:
            # Opponent can KO this target — terrible switch
            score -= 0.5
        elif opp_best_dmg >= target_hp_ratio * 0.5:
            # Opponent can 2HKO — risky switch
            score -= 0.2
        elif opp_best_dmg < 0.15:
            # Opponent barely scratches this target — great switch
            score += 0.4
        elif opp_best_dmg < 0.25:
            # Opponent does modest damage — decent switch
            score += 0.2
        else:
            # Neutral-ish damage
            score += 0.05
        if "saltcure" in opp_move_norm:
            target_type_set = {normalize_name(t) for t in target_types}
            if target_type_set & {"water", "steel"}:
                # Salt Cure does 25%/turn to Water/Steel pivots.
                score -= 0.30

        # Rocky Helmet / contact punish trap:
        # reward switches that can secure recoil KOs when the opponent likely
        # clicks a contact move into this target.
        recoil_frac = _contact_recoil_fraction(target)
        if recoil_frac > 0:
            best_contact_damage = 0.0
            has_contact_threat = False
            for mv in opp_moves:
                mv_name = mv.name if hasattr(mv, "name") else str(mv)
                mv_data = all_move_json.get(mv_name, {})
                if not _is_contact_move(mv_name, mv_data):
                    continue
                has_contact_threat = True
                best_contact_damage = max(
                    best_contact_damage,
                    _estimate_damage_ratio(opp, target, mv_name),
                )

            opp_hp_ratio = opp.hp / max(opp.max_hp, 1)
            if has_contact_threat and best_contact_damage < target_hp_ratio:
                if recoil_frac >= opp_hp_ratio:
                    score += 0.55
                elif recoil_frac * 0.90 >= opp_hp_ratio:
                    score += 0.30
    else:
        # FALLBACK: No revealed moves, use STAB type matchup
        if opp_types:
            worst_stab_eff = max(
                type_effectiveness_modifier(t, target_types) for t in opp_types
            )
            if worst_stab_eff <= 0.5:
                score += 0.4  # double resist
            elif worst_stab_eff <= 1.0:
                score += 0.2  # resist or neutral
            else:
                score -= 0.3  # weak to their STAB

    # === THREAT CATEGORY: route to correct wall ===
    # When the movepool tracker knows the opponent's threat type,
    # reward switches to Pokemon with the matching defensive stat.
    # This is what routes Blissey (high SpDef) against Moltres-Galar (special_only).
    opp_name = getattr(opp, "name", "") or ""
    threat_cat = ThreatCategory.UNKNOWN
    if opp_name:
        try:
            threat_cat = get_threat_category(opp_name)
        except Exception:
            pass

    target_stats = getattr(target, "stats", {})
    if isinstance(target_stats, dict):
        target_def = target_stats.get(constants.DEFENSE, 100)
        target_spd = target_stats.get(constants.SPECIAL_DEFENSE, 100)

        if threat_cat == ThreatCategory.SPECIAL_ONLY:
            # Special attacker — value SpDef heavily
            # Blissey (SpDef ~350+) gets a huge boost here
            spd_bonus = target_spd / 250.0  # ~1.4 for Blissey, ~0.4 for glass cannons
            score += spd_bonus * 0.3
        elif threat_cat == ThreatCategory.PHYSICAL_ONLY:
            # Physical attacker — value Def heavily
            def_bonus = target_def / 250.0
            score += def_bonus * 0.3
        elif threat_cat == ThreatCategory.MIXED:
            # Mixed — value both, slight preference for higher stat
            combined = (target_def + target_spd) / 500.0
            score += combined * 0.2
        else:
            # Unknown threat type — fall back to base stats for a guess
            opp_base = getattr(opp, "base_stats", {})
            if isinstance(opp_base, dict):
                opp_atk = opp_base.get(constants.ATTACK, 80)
                opp_spa = opp_base.get(constants.SPECIAL_ATTACK, 80)
                if opp_spa > opp_atk + 20:
                    # Likely special — favor SpDef
                    score += (target_spd / 250.0) * 0.2
                elif opp_atk > opp_spa + 20:
                    # Likely physical — favor Def
                    score += (target_def / 250.0) * 0.2

    # === SETUP THREAT: urgent routing ===
    # If the opponent is a known setup sweeper, switches to the best wall
    # get extra priority. This prevents "shuffling while they boost" by
    # making the first switch go to the right answer.
    if _opp_has_setup_potential(opp):
        # Extra bonus for high relevant defensive stat
        if isinstance(target_stats, dict):
            if threat_cat == ThreatCategory.SPECIAL_ONLY:
                if target_spd >= 250:
                    score += 0.25  # Blissey-tier special wall
                elif target_spd >= 180:
                    score += 0.10  # decent special bulk
            elif threat_cat == ThreatCategory.PHYSICAL_ONLY:
                if target_def >= 250:
                    score += 0.25  # Skarmory/Dondozo-tier physical wall
                elif target_def >= 180:
                    score += 0.10
            else:
                # Unknown but has setup — reward bulkiest option
                bulk = (target_stats.get(constants.DEFENSE, 100) +
                        target_stats.get(constants.SPECIAL_DEFENSE, 100))
                if bulk >= 450:
                    score += 0.15

    # HP preservation
    hp_ratio = target.hp / max(target.max_hp, 1)
    score += hp_ratio * 0.2

    # Regenerator bonus
    ability = normalize_name(getattr(target, "ability", "") or "")
    if ability == REGENERATOR_ABILITY:
        score += 0.15

    # Hazard cost
    h_cost = _hazard_cost(battle, target)
    score -= h_cost * 0.5

    # Can we threaten the opponent offensively?
    if target_types and opp_types:
        best_off = max(
            type_effectiveness_modifier(t, opp_types) for t in target_types
        )
        if best_off >= 2.0:
            score += 0.15
        elif best_off >= 1.0:
            score += 0.05

    return max(score, 0.01)  # never completely zero


def _score_status_move(battle: Battle, move_name: str) -> float:
    """Score a status move based on target vulnerability."""
    opp = battle.opponent.active
    if opp is None:
        return 0.0

    opp_types = _get_effective_types(opp)
    opp_status = getattr(opp, "status", None)
    opp_name = normalize_name(getattr(opp, "name", "") or "")
    opp_ability = normalize_name(getattr(opp, "ability", "") or "")
    likely_poison_heal = opp_ability == "poisonheal" or opp_name in {"gliscor", "breloom"}
    likely_purifying_salt = opp_ability == "purifyingsalt" or opp_name == "garganacl"

    # Already statused → near-zero
    if opp_status:
        return 0.02

    norm = normalize_name(move_name)

    # Ability-level immunities that dramatically reduce status value.
    if likely_purifying_salt and norm in STATUS_MOVES_OFFENSIVE:
        return 0.0

    if norm == "toxic":
        if likely_poison_heal:
            return 0.0
        # Immune: Poison, Steel types
        if "poison" in opp_types or "steel" in opp_types:
            return 0.0
        return 0.35

    if norm in ("willowisp",):
        # Immune: Fire types
        if "fire" in opp_types:
            return 0.0
        # Extra value vs physical attackers
        opp_atk = opp.stats.get(constants.ATTACK, 100) if isinstance(opp.stats, dict) else 100
        opp_spa = opp.stats.get(constants.SPECIAL_ATTACK, 100) if isinstance(opp.stats, dict) else 100
        if opp_atk > opp_spa:
            return 0.4  # physical attacker, great target
        return 0.25

    if norm == "thunderwave":
        # Immune: Ground, Electric types
        if "ground" in opp_types or "electric" in opp_types:
            return 0.0
        return 0.3

    if norm in ("spore", "sleeppowder", "hypnosis", "yawn"):
        # Immune: Grass types (for powder)
        if norm in ("spore", "sleeppowder", "stunspore") and "grass" in opp_types:
            return 0.0
        return 0.35

    # Generic status move
    return 0.2


def _score_hazard_move(battle: Battle, move_name: str) -> float:
    """Score a hazard-setting move."""
    opp_alive = sum(
        1 for p in ([battle.opponent.active] + battle.opponent.reserve)
        if p is not None and p.hp > 0
    )

    norm = normalize_name(move_name)
    sc = battle.opponent.side_conditions

    if norm == "stealthrock":
        if sc.get(constants.STEALTH_ROCK, 0) > 0:
            return 0.02  # already up
        return 0.15 * max(opp_alive - 1, 1)  # more value with more opponents

    if norm == "spikes":
        layers = sc.get(constants.SPIKES, 0)
        if layers >= 3:
            return 0.02
        return 0.10 * max(opp_alive - 1, 1) * (1.0 - layers * 0.25)

    if norm == "toxicspikes":
        layers = sc.get(constants.TOXIC_SPIKES, 0)
        if layers >= 2:
            return 0.02
        return 0.10 * max(opp_alive - 1, 1)

    if norm == "stickyweb":
        if sc.get(constants.STICKY_WEB, 0) > 0:
            return 0.02
        return 0.10 * max(opp_alive - 1, 1)

    return 0.1


def evaluate_position(battle: Battle) -> dict[str, float]:
    """
    Evaluate all legal moves for the current position.
    Returns dict[move_name, score] ready for the penalty pipeline.

    Scoring principles:
    - Damaging moves: avg_damage / opponent_hp with KO bonuses
    - Minimax: subtract 0.5 * opponent's best response damage ratio
    - Switches: type matchup, HP preservation, Regenerator, hazard cost
    - Recovery: hp_deficit * value, suppressed on free turns (tempo fix)
    - Status: target vulnerability check
    - Hazards: opponent_alive_count * hazard_value
    - Pivots: damage + switch-advantage
    """
    scores = {}
    our = battle.user.active
    opp = battle.opponent.active

    if our is None:
        return scores

    # Forced switch turns have only one legal action type.
    # Keep scoring strictly legal here so downstream selection never needs to
    # recover from illegal move choices.
    if battle.force_switch:
        for pkmn in battle.user.reserve:
            if pkmn.hp > 0:
                scores[f"switch {pkmn.name}"] = _score_switch(battle, pkmn.name)
        total = sum(scores.values())
        if total > 0:
            scores = {k: v / total for k, v in scores.items()}
        return scores

    speed_assessment = assess_speed_order(battle)
    guaranteed_move_first = speed_assessment.guaranteed_move_first
    guaranteed_move_second = speed_assessment.guaranteed_move_second
    opp_best_dmg = _opponent_best_damage(battle)
    free_turn = _is_free_turn(battle)
    threatened = _is_threatened(battle)
    our_item = _our_item_normalized(battle)
    our_hp_ratio = our.hp / max(our.max_hp, 1)

    # Is our Pokemon Choice-locked?
    is_choice_locked = our_item in CHOICE_ITEMS and constants.LOCKED_MOVE in (getattr(our, "volatile_statuses", []) or [])

    # Can opponent KO us?
    opp_can_ko = opp_best_dmg >= our_hp_ratio

    # Assault Vest check
    is_assault_vest = our_item == "assaultvest"
    moves = _get_our_moves(battle)

    # Slow-pivot windows: if we are guaranteed to move second and can live the hit,
    # pivot moves often outperform hard switches because we bring the next mon in safely.
    legal_pivot_now = any(
        normalize_name(mn) in _PIVOT_NORM for mn, _ in moves
    )
    can_slow_pivot_now = (
        guaranteed_move_second
        and legal_pivot_now
        and not opp_can_ko
    )

    # === BAD MATCHUP DETECTION ===
    # If our best damaging move does very little to the opponent, we have
    # no business being in this matchup. Suppress non-damaging moves and
    # strongly prefer switching. Example: Gholdengo (Ghost) vs Moltres-Galar
    # (Dark) — Ghost STAB does 0, we should get out immediately.
    our_best_damage = 0.0
    for mn, md in moves:
        if _is_damaging_move(mn, md):
            dmg = _estimate_damage_ratio(our, opp, mn)
            our_best_damage = max(our_best_damage, dmg)

    # "Bad matchup" = our best attack does less than 10% to them
    bad_matchup = opp is not None and our_best_damage < 0.10
    for move_name, move_data in moves:
        norm = normalize_name(move_name)
        category = move_data.get(constants.CATEGORY, "Status")
        base_power = move_data.get(constants.BASE_POWER, 0)

        # === Assault Vest: can't use status moves ===
        if is_assault_vest and category not in (constants.PHYSICAL, constants.SPECIAL):
            scores[move_name] = 0.001
            continue

        # === Choice-locked: suppress non-attack moves ===
        if is_choice_locked and category not in (constants.PHYSICAL, constants.SPECIAL):
            scores[move_name] = 0.001
            continue

        if _is_damaging_move(move_name, move_data):
            # === DAMAGING MOVE ===
            dmg_ratio = _estimate_damage_ratio(our, opp, move_name)

            # Base score
            score = dmg_ratio

            # KO bonus
            opp_hp_ratio = opp.hp / max(opp.max_hp, 1) if opp else 1.0
            if dmg_ratio >= opp_hp_ratio:
                score += 0.5  # can KO
                if guaranteed_move_first or _has_priority(move_name):
                    score += 0.3  # guaranteed KO

            # Minimax: subtract opponent's best response (dampened)
            if not (dmg_ratio >= opp_hp_ratio and (guaranteed_move_first or _has_priority(move_name))):
                # Only penalize if we're not getting a guaranteed KO
                score -= 0.5 * opp_best_dmg

            # Pivot bonus: damage + switch advantage
            if norm in _PIVOT_NORM:
                pivot_bonus = 0.1
                if guaranteed_move_second:
                    if threatened:
                        pivot_bonus += 0.22
                    else:
                        pivot_bonus += 0.08
                elif guaranteed_move_first:
                    # Fast pivots are still useful, but expose the switch-in.
                    pivot_bonus += 0.03
                score += pivot_bonus

            scores[move_name] = max(score, 0.01)

        elif norm in _RECOVERY_NORM:
            # === RECOVERY MOVE ===
            hp_deficit = 1.0 - our_hp_ratio
            recovery_value = hp_deficit * 0.6

            if free_turn:
                # TEMPO FIX (Phase 3A): suppress recovery on free turns
                recovery_value *= 0.2
            elif threatened:
                # Boost recovery when threatened
                recovery_value *= 1.5

            # Choice-locked shouldn't recover (already handled above)
            scores[move_name] = max(recovery_value, 0.01)

        elif norm in _HAZARD_NORM:
            # === HAZARD SETTING ===
            score = _score_hazard_move(battle, move_name)

            if free_turn:
                # Free turns are great for hazards
                score *= 1.5

            scores[move_name] = max(score, 0.01)

        elif norm in STATUS_MOVES_OFFENSIVE:
            # === STATUS MOVE ===
            score = _score_status_move(battle, move_name)

            if free_turn:
                score *= 1.5

            scores[move_name] = max(score, 0.01)

        elif norm in PHAZE_MOVES:
            # === PHAZING ===
            opp_boosts = getattr(opp, "boosts", {}) or {}
            total_offensive_boosts = max(
                opp_boosts.get(constants.ATTACK, 0),
                opp_boosts.get(constants.SPECIAL_ATTACK, 0),
            )
            total_boosts = sum(max(0, v) for v in opp_boosts.values())

            if total_offensive_boosts >= 2:
                # CRITICAL: opponent is a setup sweeper, phaze NOW
                # This must outscore all switches to prevent the
                # "shuffle around while they boost to +6" problem
                score = 0.8 + total_offensive_boosts * 0.1
            elif total_boosts >= 2:
                score = 0.5
            else:
                score = 0.15
            scores[move_name] = score

        elif norm in _PIVOT_NORM:
            # === PIVOT (non-damaging, e.g. teleport) ===
            score = 0.2
            if free_turn:
                score *= 1.3
            if guaranteed_move_second:
                if opp_can_ko:
                    score *= 0.55
                elif threatened:
                    score *= 2.0
                else:
                    score *= 1.45
            elif guaranteed_move_first:
                score *= 0.9
            scores[move_name] = score

        else:
            # === OTHER MOVES (setup, protect, etc.) ===
            # Give a baseline score; penalty layers handle context
            scores[move_name] = 0.15

    # === SACKING PREVENTION (Phase 3C) ===
    if opp_can_ko and opp is not None:
        our_best_dmg_ratio = 0.0
        for move_name, move_data in moves:
            if _is_damaging_move(move_name, move_data):
                dmg = _estimate_damage_ratio(our, opp, move_name)
                our_best_dmg_ratio = max(our_best_dmg_ratio, dmg)

        opp_hp_ratio = opp.hp / max(opp.max_hp, 1)
        if our_best_dmg_ratio < 0.20 * opp_hp_ratio:
            # We can't threaten them AND they KO us → penalize staying in
            for move_name in list(scores.keys()):
                if not move_name.startswith("switch "):
                    cat = all_move_json.get(move_name, {}).get(constants.CATEGORY, "Status")
                    if cat in (constants.PHYSICAL, constants.SPECIAL):
                        scores[move_name] *= 0.3

    # === BAD MATCHUP: suppress staying in when we can't threaten opponent ===
    # If we can't do meaningful damage, recovery/status/hazards are pointless
    # because we're just giving the opponent free turns. Switch out.
    if bad_matchup and not battle.force_switch:
        for move_name in list(scores.keys()):
            if not move_name.startswith("switch "):
                norm = normalize_name(move_name)
                # Phaze moves are still useful even in bad matchups
                if norm in PHAZE_MOVES:
                    continue
                # Pivot moves are fine (we leave after)
                if norm in _PIVOT_NORM:
                    scores[move_name] *= 1.2  # pivoting out is good
                    continue
                # Everything else (recover, status, weak attacks) is bad
                scores[move_name] *= 0.15
        logger.info(
            f"Bad matchup: our best damage = {our_best_damage:.1%}, "
            f"suppressing non-switch/non-phaze moves"
        )

    # === SETUP THREAT DETECTION ===
    # If the opponent is a known setup sweeper and we don't threaten them
    # enough to force them out, suppress passive plays (recovery, status).
    # Example: Gholdengo vs Moltres-Galar — Steel STAB does ~15%, but
    # Moltres will Nasty Plot and sweep. We need to get to Blissey NOW.
    # This is broader than bad_matchup (which requires <10% best damage).
    if opp is not None and not battle.force_switch:
        opp_is_setup_threat = _opp_has_setup_potential(opp)
        if opp_is_setup_threat and our_best_damage < 0.30:
            # We can't pressure a setup sweeper — suppress recovery/passive
            # but allow attacks (even weak ones can chip) and pivots
            for move_name in list(scores.keys()):
                if move_name.startswith("switch "):
                    continue
                norm = normalize_name(move_name)
                if norm in PHAZE_MOVES:
                    continue  # phazing is great vs setup
                if norm in _PIVOT_NORM:
                    scores[move_name] *= 1.3  # pivoting out is good
                    continue
                if norm in _RECOVERY_NORM:
                    scores[move_name] *= 0.1  # DO NOT recover vs setup sweepers
                    continue
                # Status and hazards are also bad — they give free setup turns
                cat = all_move_json.get(move_name, {}).get(constants.CATEGORY, "Status")
                if cat not in (constants.PHYSICAL, constants.SPECIAL):
                    scores[move_name] *= 0.2
            logger.info(
                f"Setup threat: {getattr(opp, 'name', '?')} has setup moves, "
                f"our best damage = {our_best_damage:.1%}, suppressing passive plays"
            )

    # === ITEM AWARENESS (Phase 3B) ===
    # Rocky Helmet: boost staying in vs physical/contact attackers
    if our_item == "rockyhelmet" and opp is not None:
        opp_atk = opp.stats.get(constants.ATTACK, 100) if isinstance(opp.stats, dict) else 100
        opp_spa = opp.stats.get(constants.SPECIAL_ATTACK, 100) if isinstance(opp.stats, dict) else 100
        if opp_atk > opp_spa:
            # Opponent is physical, staying in punishes them
            for move_name in scores:
                if not move_name.startswith("switch "):
                    scores[move_name] *= 1.15

    # Leftovers/Black Sludge: factor passive recovery into sustainability
    if our_item in PASSIVE_RECOVERY_ITEMS:
        # Slightly reduce urgency of manual recovery
        for move_name in list(scores.keys()):
            norm = normalize_name(move_name)
            if norm in _RECOVERY_NORM:
                scores[move_name] *= 0.9

    # === SCORE SWITCHES ===
    # Detect if opponent is boosted (switching gives them free turns to sweep)
    opp_boosts = getattr(opp, "boosts", {}) or {} if opp else {}
    opp_offensive_boosts = max(
        opp_boosts.get(constants.ATTACK, 0),
        opp_boosts.get(constants.SPECIAL_ATTACK, 0),
    ) if opp_boosts else 0
    opp_is_boosting = opp_offensive_boosts >= 1

    if not battle.force_switch:
        for pkmn in battle.user.reserve:
            if pkmn.hp > 0:
                switch_name = f"switch {pkmn.name}"
                sw_score = _score_switch(battle, pkmn.name)

                # Tempo tax: if we can already pressure the board, do not let
                # baseline switch scores dominate by default.
                if not bad_matchup and not opp_can_ko and our_best_damage >= 0.18:
                    sw_score *= 0.82

                # If we have a clean slow-pivot line, hard switching is usually inferior.
                if can_slow_pivot_now:
                    sw_score *= 0.78

                # ANTI-SETUP: penalize switches when opponent is boosted
                # Every switch gives them another free turn to set up
                if opp_is_boosting:
                    # Scale penalty with boost level: +1 = 0.5x, +2 = 0.3x, +3+ = 0.2x
                    if opp_offensive_boosts >= 3:
                        sw_score *= 0.2
                    elif opp_offensive_boosts >= 2:
                        sw_score *= 0.3
                    else:
                        sw_score *= 0.5

                # Sacking prevention: boost switches when we're getting KOd for nothing
                if opp_can_ko and not opp_is_boosting:
                    our_best_dmg_ratio = 0.0
                    for move_name, move_data in moves:
                        if _is_damaging_move(move_name, move_data):
                            dmg = _estimate_damage_ratio(our, opp, move_name)
                            our_best_dmg_ratio = max(our_best_dmg_ratio, dmg)
                    opp_hp_ratio = opp.hp / max(opp.max_hp, 1) if opp else 1.0
                    if our_best_dmg_ratio < 0.20 * opp_hp_ratio:
                        sw_score *= 1.5

                scores[switch_name] = sw_score
    else:
        # Force switch: only switches are legal
        for pkmn in battle.user.reserve:
            if pkmn.hp > 0:
                switch_name = f"switch {pkmn.name}"
                scores[switch_name] = _score_switch(battle, pkmn.name)

    if not battle.force_switch:
        _apply_switch_progress_cap(
            scores,
            moves,
            opp_can_ko=opp_can_ko,
            bad_matchup=bad_matchup,
            can_slow_pivot_now=can_slow_pivot_now,
        )

    # Normalize scores so they sum to ~1.0 (makes them compatible with penalty pipeline)
    total = sum(scores.values())
    if total > 0:
        scores = {k: v / total for k, v in scores.items()}

    return scores
