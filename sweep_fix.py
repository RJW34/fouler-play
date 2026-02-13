"""
Smart sweep prevention logic replacement for fouler-play

This replaces the blanket 85% stay penalty with nuanced counterplay that considers:
1. Can we KO the boosted opponent before they sweep?
2. Do we have Haze or phazing moves?
3. Do we have Unaware on our team (boosts don't matter)?
4. Is our current mon bulky enough to tank even at +2?
5. Only penalize if actually threatened AND can't fight back
"""

from fp.helpers import normalize_name, type_effectiveness_modifier
from data import all_move_json
import constants


PHAZING_MOVES = {"roar", "whirlwind", "dragontail", "circlethrow"}
HAZE_MOVES = {"haze", "clearsmog"}
UNAWARE_MONS = {"dondozo", "quagsire", "clefable", "skeledirge"}  # Common Unaware users
HALF_HP_DAMAGE_MOVES = {"superfang", "ruination", "naturesmadness", "naturefury"}


def _is_fixed_damage_attack(move_name: str, move_data: dict | None = None) -> bool:
    """Return True for fixed-damage attacks (0 BP but real progress)."""
    norm = normalize_name(move_name)
    if norm in HALF_HP_DAMAGE_MOVES:
        return True
    data = move_data if isinstance(move_data, dict) else all_move_json.get(move_name, {})
    fixed_damage = data.get("damage")
    if isinstance(fixed_damage, (int, float)) and fixed_damage > 0:
        return True
    if isinstance(fixed_damage, str) and normalize_name(fixed_damage) == "level":
        return True
    return False


def _estimate_fixed_damage_ratio(battle, move_name: str, move_data: dict) -> float:
    """Estimate fixed-damage output as fraction of opponent max HP."""
    if not battle or not getattr(battle, "opponent", None) or not battle.opponent.active:
        return 0.0

    defender = battle.opponent.active
    defender_max_hp = max(float(getattr(defender, "max_hp", 1) or 1), 1.0)
    defender_hp = max(float(getattr(defender, "hp", 0) or 0), 0.0)
    norm = normalize_name(move_name)

    fixed_damage = move_data.get("damage")
    if isinstance(fixed_damage, (int, float)) and fixed_damage > 0:
        return min(float(fixed_damage) / defender_max_hp, 1.0)
    if isinstance(fixed_damage, str) and normalize_name(fixed_damage) == "level":
        attacker = getattr(getattr(battle, "user", None), "active", None)
        raw_level = getattr(attacker, "level", None)
        if isinstance(raw_level, (int, float)) and raw_level > 0:
            level = float(raw_level)
        else:
            level = 100.0
        return min(level / defender_max_hp, 1.0)
    if norm in HALF_HP_DAMAGE_MOVES:
        return min((0.5 * defender_hp) / defender_max_hp, 1.0)
    return 0.0


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

    # Fixed-damage lines (Seismic Toss, Night Shade, Ruination, etc.)
    # can be immediate KO/progress even though BP is 0.
    if _is_fixed_damage_attack(move_name, move_data):
        fixed_ratio = _estimate_fixed_damage_ratio(battle, move_name, move_data)
        if fixed_ratio >= max(float(ability_state.opponent_hp_percent or 0.0), 0.01):
            return True
        if fixed_ratio >= 0.30 and ability_state.opponent_hp_percent <= 0.35:
            return True
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


def _get_switch_target_pokemon(battle, move):
    """Resolve switch target Pokemon object from a move string like 'switch dondozo'."""
    if not battle or not move.startswith("switch "):
        return None

    target_name = normalize_name(move.split("switch ", 1)[1].strip())
    for p in getattr(battle.user, "reserve", []) or []:
        if not p:
            continue
        name = normalize_name(getattr(p, "name", "") or "")
        base_name = normalize_name(getattr(p, "base_name", "") or name)
        if target_name in {name, base_name}:
            return p
    return None


def _switch_target_has_reset_or_unaware(target):
    """Return True when a switch target has direct anti-setup counterplay."""
    if target is None:
        return False

    ability = normalize_name(getattr(target, "ability", None) or "")
    if ability == "unaware":
        return True

    name = normalize_name(getattr(target, "name", "") or "")
    base_name = normalize_name(getattr(target, "base_name", "") or name)
    if name in UNAWARE_MONS or base_name in UNAWARE_MONS:
        return True

    moves = getattr(target, "moves", []) or []
    for mv in moves:
        mv_name = normalize_name(mv.name if hasattr(mv, "name") else str(mv))
        if mv_name in HAZE_MOVES or mv_name in PHAZING_MOVES:
            return True
    return False


def _switch_target_is_safe_vs_boosted_threat(battle, target):
    """Conservative check for whether a switch target is a sane anti-sweep pivot."""
    if target is None:
        return True
    opp = getattr(getattr(battle, "opponent", None), "active", None)
    if opp is None:
        return True

    target_hp = float(getattr(target, "hp", 0) or 0)
    target_max_hp = max(float(getattr(target, "max_hp", 1) or 1), 1.0)
    if target_hp <= 0:
        return False
    if target_hp / target_max_hp < 0.35:
        return False

    status = getattr(target, "status", None)
    moves = getattr(target, "moves", []) or []
    move_names = {normalize_name(m.name if hasattr(m, "name") else str(m)) for m in moves}
    if status == constants.FROZEN:
        return False
    if status == constants.SLEEP and "sleeptalk" not in move_names:
        return False

    target_types = getattr(target, "types", []) or []
    opp_types = getattr(opp, "types", []) or []
    if target_types and opp_types:
        worst_stab = max(type_effectiveness_modifier(t, target_types) for t in opp_types)
        if worst_stab >= 2.0 and not _switch_target_has_reset_or_unaware(target):
            return False

    return True


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
    move_name = normalize_name(move_name)

    if not ability_state.opponent_has_offensive_boost:
        return penalty, reason
    
    boost_level = max(ability_state.opponent_attack_boost, ability_state.opponent_spa_boost)
    has_counterplay = current_mon_has_counterplay(battle, move_name)
    is_bulky = is_current_mon_bulky(battle)
    has_unaware_available = has_unaware_on_team(battle)
    
    # PRIORITY 1: Switching vs boosted opponents.
    # Keep this conservative at +1 to avoid unnecessary ping-ponging.
    if move.startswith("switch "):
        switch_target = _get_switch_target_pokemon(battle, move)
        if (
            switch_target is not None
            and not _switch_target_is_safe_vs_boosted_threat(battle, switch_target)
        ):
            if penalty >= 1.0:
                unsafe_penalty = 0.78 if boost_level >= 2 else 0.90
                penalty = min(penalty, unsafe_penalty)
                reason = f"Avoid unsafe switch vs +{boost_level} threat"
            return penalty, reason

        if penalty >= 1.0:
            our_hp_percent = float(getattr(ability_state, "our_hp_percent", 1.0) or 1.0)
            switch_boost = 1.0

            if boost_level >= 3:
                switch_boost = BOOST_SWITCH_VS_BOOSTED + min(0.05 * (boost_level - 3), 0.15)
                if has_counterplay:
                    switch_boost = min(switch_boost, 1.25)
                    reason = f"Switch vs +{boost_level} threat (counterplay available)"
                else:
                    switch_boost = min(switch_boost, 1.55)
                    reason = f"Switch vs +{boost_level} boosted threat (CRITICAL)"
            elif boost_level == 2:
                if has_counterplay:
                    switch_boost = 1.0
                elif is_bulky:
                    switch_boost = 1.08
                    reason = "Switch vs +2 threat (bulky stay viable)"
                else:
                    switch_boost = 1.22
                    reason = "Switch vs +2 boosted threat"
            else:
                # At +1, avoid forcing switches unless we are low and lack answers.
                if not has_counterplay and not is_bulky and our_hp_percent <= 0.50:
                    switch_boost = 1.10
                    reason = "Switch vs +1 threat (low HP, limited counterplay)"

            penalty = max(penalty, switch_boost)
        return penalty, reason
    
    # PRIORITY 2: Massively boost phazing moves vs boosted opponent (unchanged)
    if move_name in PHAZING_MOVES or move_name in HAZE_MOVES:
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
    move_data = all_move_json.get(move_name, {})
    move_category = move_data.get(constants.CATEGORY, "")
    base_power = move_data.get(constants.BASE_POWER, 0)
    is_status_move = move_category == constants.STATUS
    is_reset_move = move_name in PHAZING_MOVES or move_name in HAZE_MOVES
    is_parting_shot = move_name == "partingshot"

    if move_name in SETUP_MOVES or (is_status_move and not is_reset_move and not is_parting_shot):
        passive_penalty = PENALTY_PASSIVE_VS_BOOSTED
        if boost_level <= 1:
            passive_penalty = max(passive_penalty, 0.45)
        elif boost_level == 2:
            passive_penalty = max(passive_penalty, 0.32)

        # Exception: If we have Unaware on the team, setting up isn't as bad
        if has_unaware_on_team(battle):
            # Still discourage passive lines, but don't hard-force out at low boosts.
            unaware_floor = 0.50 if boost_level <= 1 else 0.40
            penalty = min(penalty, max(passive_penalty, unaware_floor))
            reason = f"Setup vs +{boost_level} threat (have Unaware wall available)"
        else:
            penalty = min(penalty, passive_penalty)
            reason = f"PASSIVE vs +{boost_level} boosted threat (will sweep)"
        return penalty, reason

    # Parting Shot is disruptive, but still usually weaker than direct anti-sweep lines.
    if is_parting_shot:
        penalty = min(penalty, 0.75)
        reason = f"Parting Shot vs +{boost_level} threat (situational tempo line)"
        return penalty, reason

    # PRIORITY 5: Smart stay evaluation (THIS IS THE KEY FIX)
    
    is_strong_attack = (
        move_category in {constants.PHYSICAL, constants.SPECIAL}
        and (base_power >= 80 or _is_fixed_damage_attack(move_name, move_data))
    )
    
    # Check if staying is actually viable
    can_ko = can_likely_ko_boosted_opponent(battle, move_name, ability_state)
    
    # CASE 1: We can KO them - staying is GOOD
    if can_ko:
        if penalty >= 1.0:
            penalty = max(penalty, 1.3)
            reason = f"Can KO +{boost_level} threat (end sweep attempt)"
        return penalty, reason
    
    # CASE 2: We have Haze/phazing available - don't auto-boost every move.
    # Only attack lines may get a slight bump for pressuring while keeping reset in pocket.
    if has_counterplay:
        if penalty >= 1.0 and move_category in {constants.PHYSICAL, constants.SPECIAL}:
            penalty = max(penalty, 1.1)
            reason = f"Attack while reset option exists vs +{boost_level} threat"
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
