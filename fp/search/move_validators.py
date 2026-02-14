import constants
from data import all_move_json
from fp.helpers import normalize_name, type_effectiveness_modifier

# Recovery moves that heal the user
RECOVERY_MOVES_NORM = {
    "recover", "roost", "softboiled", "slackoff", "synthesis",
    "moonlight", "morningsun", "rest", "shoreup", "wish",
    "healorder", "milkdrink", "swallow"
}

# Pivoting moves that switch out the user after dealing damage
PIVOT_MOVES_NORM = {
    "uturn", "voltswitch", "flipturn", "partingshot", "teleport",
    "batonpass", "shedtail"
}

# Status moves that actually inflict status conditions (not just stat changes)
# Only these should be blocked when opponent already has a status
STATUS_INFLICTING_MOVES_NORM = {
    "toxic", "poisonpowder", "toxicthread", "banefulbunker",  # poison
    "willowisp",  # burn
    "thunderwave", "stunspore", "glare",  # paralysis
    "sleeppowder", "spore", "hypnosis", "darkvoid", "yawn", "lovelykiss", "grasswhistle", "sing",  # sleep
    "confuseray", "swagger", "flatter", "teeterdance", "sweetkiss",  # confusion (volatile, but still)
}

# Good as Gold blocks status moves that directly affect the opposing active.
# It should NOT block self-targeting or side-targeting utility/recovery.
GOOD_AS_GOLD_NONBLOCKED_TARGETS = {
    "self",
    "allyside",
    "foeside",
    "allyteam",
    "adjacentally",
    "adjacentallyorself",
    "all",
}

# Ogerpon forms that get Speed boost from Terastalizing (Embody Aspect - Teal Mask)
# Regular Ogerpon (Grass/Grass) gets +1 Speed when Terastalizing
SPEED_TERA_POKEMON = {"ogerpon"}


def _extract_move_name(move_choice: str) -> str:
    if not move_choice:
        return ""
    raw = move_choice.split(":")[-1] if ":" in move_choice else move_choice
    return normalize_name(raw)


def is_status_move_blocked_by_good_as_gold(move_name: str, move_data: dict | None = None) -> bool:
    """Return True only when Good as Gold should block this status move."""
    if move_name not in constants.ALL_STATUS_MOVES:
        return False

    if move_data is None:
        move_data = all_move_json.get(move_name, {})

    target = normalize_name(move_data.get("target", ""))
    return target not in GOOD_AS_GOLD_NONBLOCKED_TARGETS


def can_move_hit(move_choice: str, ability_state) -> tuple[bool, str | None]:
    """Return (allowed, reason) for a move versus the opponent's current abilities."""
    move_name = _extract_move_name(move_choice)
    if not move_name:
        return True, None

    move_data = all_move_json.get(move_name, {})
    category = move_data.get(constants.CATEGORY)
    priority = move_data.get(constants.PRIORITY, 0)

    # Hard blocks and reflections
    if ability_state.has_good_as_gold and is_status_move_blocked_by_good_as_gold(move_name, move_data):
        return False, "good_as_gold_blocks_status"
    if ability_state.has_magic_bounce and move_name in constants.MAGIC_BOUNCE_REFLECTED_MOVES:
        return False, "magic_bounce_reflects"

    # Sound/Bullet/Powder immunities
    if ability_state.has_soundproof and move_name in constants.SOUND_MOVES:
        return False, "soundproof_immunity"
    if ability_state.has_bulletproof and move_name in constants.BULLET_MOVES:
        return False, "bulletproof_immunity"
    if move_name in constants.POWDER_MOVES:
        if ability_state.has_overcoat or ability_state.is_grass_type:
            return False, "powder_immunity"

    # Priority blocking abilities (Queenly Majesty / Dazzling / Armor Tail)
    if priority > 0 and getattr(ability_state, "has_priority_block", False):
        return False, "priority_blocked"

    # Sap Sipper: Grass immune (treat as blocked for safety)
    if ability_state.has_sap_sipper and move_name in constants.GRASS_TYPE_MOVES:
        return False, "sap_sipper_immunity"

    # Levitate / Air Balloon / Earth Eater: Ground immunity
    if move_name in constants.DAMAGING_GROUND_MOVES:
        if ability_state.has_levitate or ability_state.has_air_balloon or ability_state.has_earth_eater:
            return False, "ground_immunity"

    # Status-inflicting moves into an already statused target fail
    # (but stat-lowering moves like Parting Shot are fine)
    if move_name in STATUS_INFLICTING_MOVES_NORM and ability_state.has_status:
        return False, "status_already_present"

    return True, None


def filter_blocked_moves(
    policy: dict[str, float],
    ability_state,
    battle=None,
    trace_events: list[dict] | None = None,
):
    """Apply hard blocking to moves that won't work against current abilities or battle state."""
    filtered = {}
    for move, weight in policy.items():
        allowed, reason = can_move_hit(move, ability_state)
        move_name = _extract_move_name(move)
        move_data = all_move_json.get(move_name, {})
        move_type = move_data.get(constants.TYPE, "")
        category = move_data.get(constants.CATEGORY)

        opp_types = []
        opp_volatiles = []
        if battle is not None and getattr(battle, "opponent", None) and battle.opponent.active:
            opp_active = battle.opponent.active
            if getattr(opp_active, "terastallized", False) and getattr(opp_active, "tera_type", None):
                opp_types = [opp_active.tera_type]
            else:
                opp_types = [t for t in (opp_active.types or []) if t]
            opp_volatiles = [v for v in (opp_active.volatile_statuses or []) if v]

        # Status into Substitute usually fails (unless sound-based or Infiltrator)
        if (
            allowed
            and category == constants.STATUS
            and "substitute" in opp_volatiles
            and move_name not in constants.SOUND_MOVES
        ):
            our_ability = ""
            if battle is not None and getattr(battle, "user", None) and battle.user.active:
                our_ability = normalize_name(battle.user.active.ability or "")
            if our_ability != "infiltrator":
                allowed = False
                reason = "status_into_substitute"

        # Explicit status-type immunities (avoid wasting turns)
        if allowed and category == constants.STATUS and opp_types:
            opp_type_set = {t.lower() for t in opp_types}
            if move_name in {"toxic", "toxicspikes"} and (
                "steel" in opp_type_set or "poison" in opp_type_set
            ):
                allowed = False
                reason = "poison_status_immunity"
            elif move_name == "willowisp" and "fire" in opp_type_set:
                allowed = False
                reason = "burn_status_immunity"
            elif move_name == "thunderwave" and (
                "ground" in opp_type_set or "electric" in opp_type_set
            ):
                allowed = False
                reason = "paralysis_status_immunity"

        # General type immunity check (only for damaging moves)
        # Scrappy allows Normal/Fighting to hit Ghost — skip immunity check.
        our_ability = ""
        if battle is not None and getattr(battle, "user", None) and battle.user.active:
            our_ability = normalize_name(battle.user.active.ability or "")
        has_scrappy = our_ability == "scrappy"
        if (
            allowed
            and move_type
            and opp_types
            and category in constants.DAMAGING_CATEGORIES
            and not has_scrappy
        ):
            if type_effectiveness_modifier(move_type, opp_types) == 0:
                allowed = False
                reason = "type_immunity"

        # Future Sight/Doom Desire cannot be stacked on the same target slot
        if battle is not None and move_name in {constants.FUTURE_SIGHT, "doomdesire"}:
            opp_fs_turns = 0
            try:
                opp_fs_turns = battle.opponent.future_sight[0]
            except Exception:
                opp_fs_turns = 0
            if opp_fs_turns > 0:
                allowed = False
                reason = "future_sight_already_pending"

        if not allowed:
            # Type immunity = move literally deals 0 damage; near-zero weight.
            # Other blocks (status immunity, substitute, etc.) keep tiny residual.
            if reason == "type_immunity":
                new_weight = 0.0
            else:
                new_weight = weight * 0.001
            filtered[move] = new_weight
            if trace_events is not None:
                trace_events.append(
                    {
                        "type": "block",
                        "source": "ability_filter",
                        "move": move,
                        "reason": reason,
                        "before": weight,
                        "after": new_weight,
                    }
                )
        else:
            applied_penalty = False

            # Penalize consecutive Protect-like moves (low success rate)
            # Make it MUCH harsher when behind (negative momentum) to avoid death loops
            if (
                battle is not None
                and move_name in constants.PROTECT_MOVES
                and getattr(battle, "user", None)
                and battle.user.active
                and getattr(battle.user, "last_used_move", None)
            ):
                last_move = normalize_name(battle.user.last_used_move.move or "")
                last_user = battle.user.last_used_move.pokemon_name
                if (
                    last_move in constants.PROTECT_MOVES
                    and last_user == battle.user.active.name
                ):
                    # Check momentum - harsher penalty when behind
                    momentum_level = getattr(ability_state, "momentum_level", "neutral")
                    if momentum_level in ("negative", "strong_negative"):
                        # Near-zero when behind - consecutive Protect is death
                        new_weight = weight * 0.01
                        reason_str = "consecutive_protect_behind"
                    else:
                        new_weight = weight * 0.05
                        reason_str = "consecutive_protect"
                    filtered[move] = new_weight
                    if trace_events is not None:
                        trace_events.append(
                            {
                                "type": "penalty",
                                "source": "ability_filter",
                                "move": move,
                                "reason": reason_str,
                                "before": weight,
                                "after": new_weight,
                            }
                        )
                    applied_penalty = True

            # Penalize recovery moves when at low HP and opponent has a boosted threat
            # This prevents the "Roost at 20% into Knock Off" pattern
            if (
                not applied_penalty
                and move_name in RECOVERY_MOVES_NORM
                and battle is not None
                and ability_state is not None
            ):
                our_hp = getattr(ability_state, "our_hp_percent", 1.0)
                opp_boosts = {}
                opp_is_boosted = False
                if battle.opponent.active:
                    opp_boosts = getattr(battle.opponent.active, "boosts", {}) or {}
                    opp_atk = opp_boosts.get(constants.ATTACK, 0)
                    opp_spa = opp_boosts.get(constants.SPECIAL_ATTACK, 0)
                    opp_is_boosted = opp_atk >= 2 or opp_spa >= 2

                # Low HP (<35%) against a boosted attacker = suicidal to heal
                if our_hp <= 0.35 and opp_is_boosted:
                    new_weight = weight * 0.1
                    filtered[move] = new_weight
                    if trace_events is not None:
                        trace_events.append(
                            {
                                "type": "penalty",
                                "source": "ability_filter",
                                "move": move,
                                "reason": "recovery_in_ko_range_vs_boost",
                                "before": weight,
                                "after": new_weight,
                            }
                        )
                    applied_penalty = True

                # Even at moderate HP (<50%), if opponent is +4 or higher, it's dangerous
                elif our_hp <= 0.50 and (opp_boosts.get(constants.ATTACK, 0) >= 4 or opp_boosts.get(constants.SPECIAL_ATTACK, 0) >= 4):
                    new_weight = weight * 0.15
                    filtered[move] = new_weight
                    if trace_events is not None:
                        trace_events.append(
                            {
                                "type": "penalty",
                                "source": "ability_filter",
                                "move": move,
                                "reason": "recovery_vs_heavily_boosted",
                                "before": weight,
                                "after": new_weight,
                            }
                        )
                    applied_penalty = True

            # Penalize pivoting (U-turn, Volt Switch, etc.) when we have significant stat boosts
            # Pivoting clears our boosts - throwing away +2 or +4 Attack is a huge momentum loss
            if (
                not applied_penalty
                and move_name in PIVOT_MOVES_NORM
                and battle is not None
                and battle.user
                and battle.user.active
            ):
                our_boosts = getattr(battle.user.active, "boosts", {}) or {}
                our_atk = our_boosts.get(constants.ATTACK, 0)
                our_spa = our_boosts.get(constants.SPECIAL_ATTACK, 0)
                our_spe = our_boosts.get(constants.SPEED, 0)
                max_offensive_boost = max(our_atk, our_spa)

                ko_available = getattr(ability_state, "ko_line_available", False) if ability_state else False
                ko_move = getattr(ability_state, "ko_line_move", "") if ability_state else ""

                # Check if we have significant boosts worth preserving
                if max_offensive_boost >= 2:
                    can_tera_for_speed = False
                    reason_str = "pivot_wastes_boosts"

                    # Extra harsh if we also have a KO available
                    if ko_available:
                        reason_str = "pivot_wastes_boosts_ko_available"

                    # Check if we're a Speed-Tera Pokemon (like Ogerpon-Teal)
                    our_poke = normalize_name(battle.user.active.name or "")
                    already_tera = False
                    if hasattr(battle.user.active, 'terastallized'):
                        already_tera = battle.user.active.terastallized
                    elif hasattr(battle.user.active, 'tera') and battle.user.active.tera:
                        already_tera = battle.user.active.tera.get('active', False)

                    if our_poke in SPEED_TERA_POKEMON and not already_tera:
                        can_tera_for_speed = True
                        reason_str = "pivot_wastes_boosts_tera_speed"

                    # Severity based on how many boosts we'd throw away
                    # +4 or more: 95% penalty (0.05 multiplier)
                    # +2 or +3: 85% penalty (0.15 multiplier)
                    if max_offensive_boost >= 4:
                        new_weight = weight * 0.05  # Throwing away +4 is almost always wrong
                    elif ko_available or can_tera_for_speed:
                        new_weight = weight * 0.10  # KO available or Tera speed = almost always take it
                    else:
                        new_weight = weight * 0.15  # +2/+3 without immediate KO line

                    filtered[move] = new_weight
                    if trace_events is not None:
                        trace_events.append(
                            {
                                "type": "penalty",
                                "source": "ability_filter",
                                "move": move,
                                "reason": reason_str,
                                "before": weight,
                                "after": new_weight,
                                "our_atk_boost": our_atk,
                                "our_spa_boost": our_spa,
                                "ko_available": ko_available,
                                "ko_move": ko_move,
                            }
                        )
                    applied_penalty = True

            if not applied_penalty:
                filtered[move] = weight
    return filtered
