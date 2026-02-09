"""
Smart sweep prevention logic replacement for fouler-play

This replaces the blanket 85% stay penalty with nuanced counterplay that considers:
1. Can we KO the boosted opponent before they sweep?
2. Do we have Haze or phazing moves?
3. Do we have Unaware on our team (boosts don't matter)?
4. Is our current mon bulky enough to tank even at +2?
5. Only penalize if actually threatened AND can't fight back
"""

from fp.helpers import normalize_name
from data import all_move_json
import constants


PHAZING_MOVES = {"roar", "whirlwind", "dragontail", "circlethrow"}
HAZE_MOVES = {"haze", "clearsmog"}
UNAWARE_MONS = {"dondozo", "quagsire", "clefable", "skeledirge"}  # Common Unaware users


def has_unaware_on_team(battle):
    """Check if we have an Unaware Pokemon on our team."""
    if not battle or not battle.user:
        return False
    
    # Check active Pokemon
    if battle.user.active:
        ability = getattr(battle.user.active, "ability", None)
        if ability and normalize_name(ability) == "unaware":
            return True
        # Check if it's a common Unaware user (ability unknown)
        name = normalize_name(battle.user.active.name)
        base_name = normalize_name(getattr(battle.user.active, "base_name", "") or battle.user.active.name)
        if name in UNAWARE_MONS or base_name in UNAWARE_MONS:
            return True
    
    # Check reserves
    for reserve_pkmn in battle.user.reserve:
        ability = getattr(reserve_pkmn, "ability", None)
        if ability and normalize_name(ability) == "unaware":
            return True
        # Check if it's a common Unaware user (ability unknown)
        name = normalize_name(reserve_pkmn.name)
        base_name = normalize_name(getattr(reserve_pkmn, "base_name", "") or reserve_pkmn.name)
        if name in UNAWARE_MONS or base_name in UNAWARE_MONS:
            return True
    
    return False


def current_mon_has_counterplay(battle, move_name):
    """Check if current mon has tools to handle boosted threats."""
    if not battle or not battle.user or not battle.user.active:
        return False
    
    current_moves = []
    for move in getattr(battle.user.active, "moves", []):
        mv_name = normalize_name(move.name if hasattr(move, "name") else str(move))
        current_moves.append(mv_name)
    
    # Check if we have Haze or phazing moves
    has_reset = any(mv in HAZE_MOVES or mv in PHAZING_MOVES for mv in current_moves)
    
    # Check if current mon has Unaware
    ability = getattr(battle.user.active, "ability", None)
    has_unaware_ability = ability and normalize_name(ability) == "unaware"
    
    # Check if it's a common Unaware user
    name = normalize_name(battle.user.active.name)
    base_name = normalize_name(getattr(battle.user.active, "base_name", "") or battle.user.active.name)
    is_unaware_mon = name in UNAWARE_MONS or base_name in UNAWARE_MONS
    
    return has_reset or has_unaware_ability or is_unaware_mon


def can_likely_ko_boosted_opponent(battle, move_name, ability_state):
    """
    Estimate if we can KO the boosted opponent with this move.
    
    This is a simplified check:
    - If it's a strong attacking move (80+ BP) and opponent is under 50% HP
    - If it's a super-effective move
    - If it's a priority move and opponent is low HP
    
    Full damage calculation is expensive, so we use heuristics.
    """
    if not battle or not battle.user or not battle.user.active:
        return False
    
    move_data = all_move_json.get(move_name, {})
    move_category = move_data.get(constants.CATEGORY, "")
    base_power = move_data.get(constants.BASE_POWER, 0)
    
    # Not a damaging move
    if move_category not in {constants.PHYSICAL, constants.SPECIAL}:
        return False
    
    # Priority move + low HP opponent = likely KO
    priority = move_data.get(constants.PRIORITY, 0)
    if priority > 0 and ability_state.opponent_hp_percent < 0.35:
        return True
    
    # Strong move + opponent under 50% HP = likely KO
    if base_power >= 90 and ability_state.opponent_hp_percent < 0.5:
        return True
    
    # Strong move + opponent under 60% HP + STAB or SE = likely KO
    if base_power >= 80 and ability_state.opponent_hp_percent < 0.6:
        # Check for STAB or super-effective (simplified)
        move_type = move_data.get(constants.TYPE, "")
        our_types = getattr(battle.user.active, "types", []) or []
        
        # STAB check
        has_stab = move_type.lower() in [t.lower() for t in our_types]
        
        if has_stab:
            return True
        
        # Super-effective check (simplified - would need opponent types for full accuracy)
        # For now, assume we know what we're doing if using a strong move at medium HP
        return False
    
    return False


def is_current_mon_bulky(battle):
    """Check if current mon is bulky enough to tank boosted hits."""
    if not battle or not battle.user or not battle.user.active:
        return False
    
    pkmn = battle.user.active
    
    # Check if mon is above 70% HP (can tank at least one hit)
    hp_percent = pkmn.hp / pkmn.max_hp if pkmn.max_hp > 0 else 0
    if hp_percent < 0.7:
        return False
    
    # Check base stats if available (simplified)
    base_stats = getattr(pkmn, "base_stats", {})
    if base_stats:
        # Consider bulky if Def + SpD + HP > 350 (rough heuristic)
        bulk = (
            base_stats.get(constants.DEFENSE, 0) +
            base_stats.get(constants.SPECIAL_DEFENSE, 0) +
            base_stats.get(constants.HITPOINTS, 0)
        )
        if bulk > 350:
            return True
    
    # Check if it's a known bulky mon (simplified list)
    bulky_mons = {
        "blissey", "chansey", "toxapex", "corviknight", "skarmory",
        "ferrothorn", "hippowdon", "gastrodon", "dondozo", "clodsire",
        "gliscor", "landorustherian", "zapdos", "moltres", "slowbro",
        "slowking", "tangrowth", "amoonguss", "alomomola", "quagsire"
    }
    
    name = normalize_name(pkmn.name)
    base_name = normalize_name(getattr(pkmn, "base_name", "") or pkmn.name)
    
    return name in bulky_mons or base_name in bulky_mons


def smart_sweep_prevention(
    penalty,
    reason,
    move,
    move_name,
    ability_state,
    battle,
    PENALTY_PASSIVE_VS_BOOSTED,
    BOOST_SWITCH_VS_BOOSTED,
    BOOST_PHAZE_VS_BOOSTED,
    BOOST_REVENGE_VS_BOOSTED,
    SETUP_MOVES,
    STATUS_ONLY_MOVES,
    PHAZING_MOVES,
    PRIORITY_MOVES,
):
    """
    Smart sweep prevention logic that considers actual threat level and counterplay options.
    
    Returns: (penalty, reason) tuple
    """
    if not ability_state.opponent_has_offensive_boost:
        return penalty, reason
    
    boost_level = max(ability_state.opponent_attack_boost, ability_state.opponent_spa_boost)
    
    # PRIORITY 1: Heavily boost switching vs boosted opponent (unchanged, this is good)
    if move.startswith("switch "):
        if penalty >= 1.0:
            # Scale boost with boost level (+1 = 1.6x, +2 = 2.0x, +3+ = 2.4x)
            switch_boost = BOOST_SWITCH_VS_BOOSTED + (boost_level - 1) * 0.2
            penalty = max(penalty, switch_boost)
            reason = f"Switch vs +{boost_level} boosted threat (CRITICAL)"
        return penalty, reason
    
    # PRIORITY 2: Massively boost phazing moves vs boosted opponent (unchanged)
    if move_name in PHAZING_MOVES:
        if penalty >= 1.0:
            penalty = max(penalty, BOOST_PHAZE_VS_BOOSTED)
            reason = f"Phaze +{boost_level} boosted sweeper (reset threat)"
        return penalty, reason
    
    # PRIORITY 3: Boost priority/revenge moves IF we can KO
    if move_name in PRIORITY_MOVES:
        if penalty >= 1.0:
            # Check if we can likely KO
            if can_likely_ko_boosted_opponent(battle, move_name, ability_state):
                penalty = max(penalty, BOOST_REVENGE_VS_BOOSTED)
                reason = f"Priority vs +{boost_level} threat (can KO)"
            else:
                # Don't boost as much if we probably can't KO
                penalty = max(penalty, 1.2)
                reason = f"Priority vs +{boost_level} threat (uncertain KO)"
        return penalty, reason
    
    # PRIORITY 4: HEAVILY penalize passive/setup moves vs boosted opponent (unchanged, this is correct)
    if move_name in SETUP_MOVES or move_name in STATUS_ONLY_MOVES:
        # Exception: If we have Unaware on the team, setting up isn't as bad
        if has_unaware_on_team(battle):
            penalty = min(penalty, 0.4)  # 60% penalty instead of 75%
            reason = f"Setup vs +{boost_level} threat (have Unaware wall available)"
        else:
            penalty = min(penalty, PENALTY_PASSIVE_VS_BOOSTED)
            reason = f"PASSIVE vs +{boost_level} boosted threat (will sweep)"
        return penalty, reason
    
    # PRIORITY 5: Smart stay evaluation (THIS IS THE KEY FIX)
    move_data = all_move_json.get(move_name, {})
    move_category = move_data.get(constants.CATEGORY, "")
    base_power = move_data.get(constants.BASE_POWER, 0)
    
    is_strong_attack = (
        move_category in {constants.PHYSICAL, constants.SPECIAL} 
        and base_power >= 80
    )
    
    # Check if staying is actually viable
    can_ko = can_likely_ko_boosted_opponent(battle, move_name, ability_state)
    has_counterplay = current_mon_has_counterplay(battle, move_name)
    is_bulky = is_current_mon_bulky(battle)
    has_unaware_available = has_unaware_on_team(battle)
    
    # CASE 1: We can KO them - staying is GOOD
    if can_ko:
        if penalty >= 1.0:
            penalty = max(penalty, 1.3)
            reason = f"Can KO +{boost_level} threat (end sweep attempt)"
        return penalty, reason
    
    # CASE 2: We have Haze/phazing moves - staying is GOOD
    if has_counterplay:
        if penalty >= 1.0:
            penalty = max(penalty, 1.4)
            reason = f"Have Haze/Phaze vs +{boost_level} threat (can reset)"
        return penalty, reason
    
    # CASE 3: We're bulky and above 70% HP - staying is OKAY for at least +1
    if is_bulky and boost_level == 1:
        # Don't penalize bulky mons staying in at +1
        reason = f"Bulky mon vs +1 threat (can tank and respond)"
        return penalty, reason
    
    # CASE 4: We have Unaware on the team and we're bulky - switching is better but not critical
    if is_bulky and has_unaware_available:
        # Light penalty, not heavy
        penalty = min(penalty, 0.6)  # 40% penalty instead of 85%
        reason = f"Bulky mon vs +{boost_level}, have Unaware available"
        return penalty, reason
    
    # CASE 5: Strong attack at +1 boost - not that scary yet, especially if we resist
    if is_strong_attack and boost_level == 1:
        # Light penalty only
        penalty = min(penalty, 0.7)  # 30% penalty instead of 85%
        reason = f"Strong attack vs +1 threat (manageable)"
        return penalty, reason
    
    # CASE 6: +2 or higher WITHOUT counterplay - THIS is where heavy penalties make sense
    if boost_level >= 2 and not (can_ko or has_counterplay or is_bulky):
        if not is_strong_attack:
            # Heavy penalty for weak moves vs +2+
            penalty = min(penalty, 0.25)  # 75% penalty
            reason = f"Weak move vs +{boost_level} threat (very dangerous)"
        else:
            # Moderate penalty for strong moves vs +2+
            penalty = min(penalty, 0.5)  # 50% penalty
            reason = f"Strong move vs +{boost_level} threat (risky)"
        return penalty, reason
    
    # CASE 7: +1 WITHOUT counterplay - moderate penalty for weak moves only
    if boost_level == 1 and not is_strong_attack and not (can_ko or has_counterplay):
        # Moderate penalty for weak moves vs +1
        penalty = min(penalty, 0.6)  # 40% penalty instead of 85%
        reason = f"Weak move vs +1 threat (should consider switching)"
        return penalty, reason
    
    # Default: No penalty adjustment for strong attacks vs +1
    return penalty, reason
