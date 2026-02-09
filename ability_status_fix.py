# GENERAL ability-status threat awareness fix
# This replaces the WoW-specific hardcoded logic in commit 236e62c

def get_opponent_status_threats(opponent_pokemon, movepool_data=None):
    """
    Returns set of status types the opponent can inflict.
    Checks revealed moves + potential moves from movepool.
    
    Returns: set of status codes like {'brn', 'par', 'slp', 'frz', 'psn', 'tox'}
    """
    from constants import STATUS, SECONDARY, NON_VOLATILE_STATUSES
    from data import all_move_json
    from fp.search.main import normalize_name
    
    if not opponent_pokemon:
        return set()
    
    threats = set()
    
    # Check revealed moves
    revealed_moves = getattr(opponent_pokemon, "moves", []) or []
    for mv in revealed_moves:
        move_name = normalize_name(mv.name if hasattr(mv, "name") else str(mv))
        move_data = all_move_json.get(move_name, {})
        
        # Direct status effect (e.g., Thunder Wave -> par)
        if STATUS in move_data and move_data[STATUS] in NON_VOLATILE_STATUSES:
            threats.add(move_data[STATUS])
        
        # Secondary status effect (e.g., Scald 30% burn, Nuzzle 100% par)
        secondary = move_data.get(SECONDARY, {})
        if isinstance(secondary, dict) and 'status' in secondary:
            status_code = secondary['status']
            if status_code in NON_VOLATILE_STATUSES:
                threats.add(status_code)
    
    # Check potential moves from movepool (what opponent COULD have)
    if movepool_data:
        opponent_name = normalize_name(opponent_pokemon.name)
        opp_data = movepool_data.get(opponent_name, {})
        
        # Check all move categories (status, physical, special)
        # Physical/special moves can have secondary status effects (Scald, Nuzzle, etc.)
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
                threats.add(move_data[STATUS])
            
            secondary = move_data.get(SECONDARY, {})
            if isinstance(secondary, dict) and 'status' in secondary:
                status_code = secondary['status']
                if status_code in NON_VOLATILE_STATUSES:
                    threats.add(status_code)
    
    return threats


def is_status_threatening_for_ability(status_codes, our_pokemon):
    """
    Returns (is_threatened, ability_name, reason) tuple.
    
    Checks if any of the given status codes would be BAD for our Pokemon's ability.
    GENERAL logic for all ability-status interactions.
    
    Args:
        status_codes: set of status codes like {'brn', 'par', 'slp'}
        our_pokemon: our active Pokemon object
    
    Returns:
        (bool, str, str): (is_threatened, ability_name, reason)
    """
    from constants import POKEMON_COMMONLY_POISON_HEAL
    from fp.search.main import normalize_name
    
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
        # Any non-poison status ruins Poison Heal
        harmful_statuses = status_codes - {'psn', 'tox'}
        if harmful_statuses:
            return (True, 'Poison Heal', f"threatened by {harmful_statuses}")
    
    # =========================================================================
    # GUTS: Wants status (boosts Attack), but:
    #   - BRN halves Attack (bad!)
    #   - PAR reduces speed (mixed - attack boost but slow)
    #   - SLP/FRZ prevent action (very bad!)
    # =========================================================================
    is_guts = ability_norm == 'guts' or our_name_norm in getattr(constants, 'POKEMON_COMMONLY_GUTS', set())
    
    if is_guts:
        # Burn is actively harmful (negates Guts boost)
        # Sleep/Freeze prevent action
        harmful = status_codes & {'brn', 'slp', 'frz'}
        if harmful:
            return (True, 'Guts', f"threatened by {harmful}")
    
    # =========================================================================
    # TOXIC BOOST: Wants PSN/TOX, threatened by other status
    # =========================================================================
    is_toxic_boost = ability_norm == 'toxicboost'
    
    if is_toxic_boost:
        harmful_statuses = status_codes - {'psn', 'tox'}
        if harmful_statuses:
            return (True, 'Toxic Boost', f"threatened by {harmful_statuses}")
    
    # =========================================================================
    # MARVEL SCALE / QUICK FEET: Want status, but:
    #   - PAR reduces speed (bad for Quick Feet specifically)
    #   - SLP/FRZ prevent action
    # =========================================================================
    is_marvel_scale = ability_norm == 'marvelscale'
    is_quick_feet = ability_norm == 'quickfeet'
    
    if is_marvel_scale or is_quick_feet:
        # Sleep/Freeze prevent action
        harmful = status_codes & {'slp', 'frz'}
        # Paralysis is bad for Quick Feet specifically
        if is_quick_feet and 'par' in status_codes:
            harmful.add('par')
        if harmful:
            return (True, ability_norm.title(), f"threatened by {harmful}")
    
    # =========================================================================
    # MAGIC GUARD: Immune to status damage, doesn't care
    # (No threat, not included here)
    # =========================================================================
    
    return (False, None, None)


# Example usage in apply_ability_penalties:
"""
# OLD (WoW-specific hardcoded):
if is_poison_heal and our_status is None:
    has_burn_move = any(...)  # Hardcoded burn moves
    wow_common_users = {...}  # Hardcoded species list
    likely_has_wow = opponent_name_norm in wow_common_users
    
    if has_burn_move or likely_has_wow:
        # Penalize...

# NEW (GENERAL):
if our_status is None:  # Not already statused
    opponent_threats = get_opponent_status_threats(opponent_active, movepool_data)
    is_threatened, ability_name, reason = is_status_threatening_for_ability(opponent_threats, our_active)
    
    if is_threatened:
        move_data = all_move_json.get(move_name, {})
        move_category = move_data.get(constants.CATEGORY, "")
        base_power = move_data.get(constants.BASE_POWER, 0)
        
        # Penalize passive/setup moves
        if move_name in SETUP_MOVES or move_category == constants.STATUS:
            penalty = min(penalty, PENALTY_PASSIVE_VS_BOOSTED)
            reason_text = f"{ability_name} vs status threat ({reason}) - avoid passive"
        
        # Moderately penalize weak attacks
        elif move_category in {constants.PHYSICAL, constants.SPECIAL} and base_power < 70:
            penalty = min(penalty, 0.7)
            reason_text = f"{ability_name} vs status threat - prefer strong attack or switch"
"""
