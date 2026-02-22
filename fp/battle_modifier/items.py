# fp/battle_modifier/items.py
# Auto-split from battle_modifier.py

from fp.battle_modifier._common import *  # noqa: F403
from fp.battle_modifier.switching import _switch_active_with_zoroark_from_reserves
from fp.battle_modifier._common import _parse_time_left_seconds, _side_id_from_protocol_ident

def set_item(battle, split_msg):
    """Set the opponent's item"""
    if is_opponent(battle, split_msg):
        side = battle.opponent
        other_side = battle.user
    else:
        side = battle.user
        other_side = battle.opponent

    item = normalize_name(split_msg[3].strip())

    if (
        len(split_msg) >= 5
        and side.active.removed_item is None
        and item != side.active.item
        and side.active.item not in [constants.UNKNOWN_ITEM]
    ):
        logger.info("{}'s removed item is {}".format(side.active.name, item))
        side.active.removed_item = side.active.item

    # when the bot gets tricked we set the opponent's removed item
    if (
        len(split_msg) >= 5
        and "[from] move: Trick" in split_msg[4]
        and not is_opponent(battle, split_msg)
        and other_side.active is not None
        and other_side.active.removed_item is None
    ):
        logger.info("Setting opponent's removed_item to {}".format(item))
        other_side.active.removed_item = item

    # for gen5 frisk only
    # the frisk message will (incorrectly imo) show the item as belonging to the
    # pokemon with frisk
    #
    # e.g. Furret is frisking the opponent:
    # |-item|p2a: Furret|Life Orb|[from] ability: Frisk|[of] p2a: Furret
    if (
        len(split_msg) == 6
        and split_msg[4] == "[from] ability: Frisk"
        and split_msg[2] in split_msg[5]
    ):
        logger.info(
            "{} frisked the opponent's item as {}".format(side.active.name, item)
        )
        # Guard: other_side.active can be None during async transitions
        if other_side.active is not None:
            logger.info("Setting {}'s item to {}".format(other_side.active.name, item))
            other_side.active.item = item
    else:
        logger.info("Setting {}'s item to {}".format(side.active.name, item))
        side.active.item = item


def remove_item(battle, split_msg):
    """Remove the opponent's item"""
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    # Guard: side.active can be None during async transitions (faint/switchout)
    if side.active is None:
        logger.debug("remove_item: side.active is None, skipping")
        return

    item = normalize_name(split_msg[3].strip())

    logger.info("Removing {}'s item: {}".format(side.active.name, item))
    side.active.item = None

    if side.active.removed_item is None:
        logger.info("Setting {}'s removed item to {}".format(side.active.name, item))
        side.active.removed_item = item

    if "unburden" not in side.active.volatile_statuses and "unburden" in [
        normalize_name(a)
        for a in pokedex[side.active.name][constants.ABILITIES].values()
    ]:
        logger.info("Adding unburden volatile to {}".format(side.active.name))
        side.active.volatile_statuses.append("unburden")

    if len(split_msg) >= 5 and "knockoff" in normalize_name(split_msg[4]):
        logger.info("Knockoff removed {}'s item".format(side.active.name))
        side.active.knocked_off = True


def immune(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
        pkmn = side.active
    else:
        side = battle.user
        pkmn = side.active

    # Guard: pkmn/side.active can be None during async transitions (faint/switchout)
    if pkmn is None or side.active is None:
        logger.debug("immune: pkmn/side.active is None, skipping")
        return

    for msg in split_msg:
        if constants.ABILITY in normalize_name(msg):
            ability = normalize_name(msg.split(":")[-1])
            logger.info("Setting {}'s ability to {}".format(side.active.name, ability))
            side.active.ability = ability

    zoroark_from_reserves = side.find_pokemon_in_reserves(
        "zoroark"
    ) or side.find_pokemon_in_reserves("zoroarkhisui")

    expected_damage_rolls, _ = poke_engine_get_damage_rolls(
        deepcopy(battle), battle.user.last_used_move.move, "none", True
    )

    # Zoroark checks
    if (
        is_opponent(battle, split_msg)
        and not side.active.name.startswith("zoroark")
        and battle.user.last_used_move.move in all_move_json
        and all_move_json[battle.user.last_used_move.move][constants.CATEGORY]
        != constants.STATUS
        and type_effectiveness_modifier(
            all_move_json[battle.user.last_used_move.move][constants.TYPE],
            side.active.types,
        )
        != 0
        and "from" not in split_msg[-1]
        and not all(x == 0 for x in expected_damage_rolls)
        and battle.user.future_sight[0] != 1
        and not (
            side.active.terastallized
            and type_effectiveness_modifier(
                all_move_json[battle.user.last_used_move.move][constants.TYPE],
                [side.active.tera_type],
            )
            == 0
        )
    ):
        # Battle Factory: Zoroark must be in the reserves
        # and must be immune to the last used move by the bot
        if (
            battle.battle_type == BattleType.BATTLE_FACTORY
            and zoroark_from_reserves is not None
            and type_effectiveness_modifier(
                all_move_json[battle.user.last_used_move.move][constants.TYPE],
                zoroark_from_reserves.types,
            )
            == 0
        ):
            logger.info(
                "{} was immune to {} when it shouldn't be - it is {}".format(
                    pkmn.name,
                    battle.user.last_used_move.move,
                    zoroark_from_reserves.name,
                )
            )
            _switch_active_with_zoroark_from_reserves(side, zoroark_from_reserves)

        # Random Battle: Zoroark may be in the reserves so we need to check the move type
        # that it was immune to
        elif battle.battle_type == BattleType.RANDOM_BATTLE:
            actual_zoroark = None
            zoroark_hisui = Pokemon("zoroarkhisui", 100)
            zoroark_regular = Pokemon("zoroark", 100)

            # zoroark was in the reserves - just use that one
            if (
                zoroark_from_reserves is not None
                and type_effectiveness_modifier(
                    all_move_json[battle.user.last_used_move.move][constants.TYPE],
                    zoroark_from_reserves.types,
                )
                == 0
            ):
                actual_zoroark = zoroark_from_reserves

            # hisui zoroark
            elif (
                zoroark_from_reserves is None
                and type_effectiveness_modifier(
                    all_move_json[battle.user.last_used_move.move][constants.TYPE],
                    zoroark_hisui.types,
                )
                == 0
                and zoroark_hisui.name in RandomBattleTeamDatasets.pkmn_sets
            ):
                actual_zoroark = zoroark_hisui
                actual_zoroark.level = RandomBattleTeamDatasets.predict_set(
                    actual_zoroark
                ).pkmn_set.level
                side.reserve.append(actual_zoroark)

            # regular zoroark
            elif (
                zoroark_from_reserves is None
                and type_effectiveness_modifier(
                    all_move_json[battle.user.last_used_move.move][constants.TYPE],
                    zoroark_regular.types,
                )
                == 0
                and zoroark_regular.name in RandomBattleTeamDatasets.pkmn_sets
            ):
                actual_zoroark = zoroark_regular
                actual_zoroark.level = RandomBattleTeamDatasets.predict_set(
                    actual_zoroark
                ).pkmn_set.level
                side.reserve.append(actual_zoroark)

            # if we found a zoroark from one of those branches
            if actual_zoroark is not None:
                logger.info(
                    "{} was immune to {} when it shouldn't be - it is {}".format(
                        pkmn.name,
                        battle.user.last_used_move.move,
                        actual_zoroark.name,
                    )
                )
                _switch_active_with_zoroark_from_reserves(side, actual_zoroark)


def update_ability(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
        other_side = battle.user
    else:
        side = battle.user
        other_side = battle.opponent

    ability = normalize_name(split_msg[3])
    if len(split_msg) >= 6 and "ability:" in split_msg[4]:
        original_ability = normalize_name(split_msg[4].split(":")[-1])
        logger.info(
            "Setting {}'s original ability to {}".format(
                side.active.name, original_ability
            )
        )
        side.active.original_ability = original_ability

        if split_msg[5].startswith("[of]") and other_side.name in split_msg[5]:
            # Guard: other_side.active can be None during async transitions
            if other_side.active is not None:
                logger.info(
                    "Setting {}'s ability to {}".format(other_side.active.name, ability)
                )
                other_side.active.ability = ability
    elif ability == "asone":
        if side.active.name == "calyrexice":
            ability = "asoneglastrier"
        elif side.active.name == "calyrexshadow":
            ability = "asonespectrier"
        else:
            logger.warning(
                "Unknown asone ability for {} - defaulting to asoneglastrier".format(
                    side.active.name
                )
            )
            ability = "asoneglastrier"
    elif side.active.ability in ["asoneglastrier", "asonespectrier"]:
        logger.info(
            "{} has the ability {}, will not change to {}".format(
                side.active.name, side.active.ability, ability
            )
        )
        ability = side.active.ability

    logger.info("Setting {}'s ability to {}".format(side.active.name, ability))
    side.active.ability = ability


def check_speed_ranges(battle, msg_lines):
    """
    Intention:
        This function is intended to set the min or max possible speed that the opponent's
        active Pokemon could possibly have given a turn that just happened.

        For example: if both the bot and the opponent use an equal priority move but the
        opponent moves first, then the opponent's min_speed attribute will be set to the
        bots actual speed. This is because the opponent must have at least that much speed
        for it to have gone first.

        These min/max speeds are set without knowledge of items. If the opponent goes first
        when having a choice scarf then min speed will still be set to the bots speed. When
        it comes time to guess a Pokemon's possible set(s), the item must be taken into account
        as well when determining the final speed of a Pokemon. Abilities are NOT taken into
        consideration because their speed modifications are subject to certain conditions
        being present, whereas a choice scarf ALWAYS boosts speed.

        If there is a situation where an ability could have modified the turn order (either by
        changing a move's priority or giving a Pokemon more speed) then this check should be
        skipped. Examples are:
            - either side switched
            - the opponent COULD have a speed-boosting weather ability AND that weather is up
            - the opponent COULD have prankster and it used a status move
            - Grassy Glide is used when Grassy Terrain is up
    """
    # If either active Pokemon is None (e.g. fainted), skip speed range check
    if battle.user.active is None or battle.opponent.active is None:
        return

    for ln in msg_lines:
        # If either side switched this turn - don't do this check
        if ln.startswith("|switch|"):
            return

        # if anyone got `cant` or hit themselves in confusion
        # skip this check as we don't know if they used a priority move
        if ln.startswith("|cant|") or (
            ln.startswith("|-activate|") and ln.endswith("confusion")
        ):
            return

        # If anyone used a custapberry, skip this check
        if ln.startswith("|-enditem|") and (
            "custapberry" in normalize_name(ln) or "Custap Berry" in ln
        ):
            return

        # If anyone had quick claw activate, skip this check
        if "quickclaw" in normalize_name(ln) or "Quick Claw" in ln:
            return

        # If anyone had quick claw activate, skip this check
        if "quickdraw" in normalize_name(ln) or "Quick Draw" in ln:
            return

    moves = [get_move_information(m) for m in msg_lines if m.startswith("|move|")]
    number_of_moves = len(moves)
    if number_of_moves not in [1, 2]:
        return

    if (
        number_of_moves == 1
        and moves[0][0].startswith(battle.opponent.name)
        and moves[0][1][constants.ID] != "pursuit"
    ):
        moves.append(
            (
                "{}a: {}".format(battle.opponent.name, battle.user.active.name),
                all_move_json[normalize_name(battle.user.last_selected_move.move)],
            )
        )

    # if the bot knocked out the opponent there's nothing to do here
    elif number_of_moves == 1:
        return

    if (
        moves[0][1][constants.PRIORITY] != moves[1][1][constants.PRIORITY]
        or moves[0][1][constants.ID] == "encore"
    ):
        return

    bot_went_first = moves[0][0].startswith(battle.user.name)

    if (
        battle.opponent.active is None
        or battle.opponent.active.item == "choicescarf"
        or can_have_speed_modified(battle, battle.opponent.active)
        or (
            not bot_went_first
            and can_have_priority_modified(
                battle, battle.opponent.active, moves[0][1][constants.ID]
            )
        )
        or (
            bot_went_first
            and can_have_priority_modified(
                battle, battle.user.active, moves[0][1][constants.ID]
            )
        )
    ):
        return

    battle_copy = deepcopy(battle)
    battle_copy_for_stats = deepcopy(battle_copy)
    battle_copy.user.active.status = battle_copy_for_stats.user.active.stats

    speed_threshold = int(
        boost_multiplier_lookup[battle_copy.user.active.boosts[constants.SPEED]]
        * battle_copy.user.active.stats[constants.SPEED]
        / boost_multiplier_lookup[battle_copy.opponent.active.boosts[constants.SPEED]]
    )

    if "protosynthesisspe" in battle.opponent.active.volatile_statuses:
        speed_threshold = int(speed_threshold / 1.5)

    if battle.opponent.side_conditions[constants.TAILWIND]:
        speed_threshold = int(speed_threshold / 2)

    if battle.user.side_conditions[constants.TAILWIND]:
        speed_threshold = int(speed_threshold * 2)

    if battle.opponent.active.status == constants.PARALYZED:
        if battle.generation in ["gen4", "gen5", "gen6"]:
            speed_threshold = int(speed_threshold * 4)
        else:
            speed_threshold = int(speed_threshold * 2)

    if battle.user.active.status == constants.PARALYZED:
        if battle.generation in ["gen4", "gen5", "gen6"]:
            speed_threshold = int(speed_threshold / 4)
        else:
            speed_threshold = int(speed_threshold / 2)

    if battle.user.active.item == "choicescarf":
        speed_threshold = int(speed_threshold * 1.5)

    if "protosynthesisspe" in battle.user.active.volatile_statuses:
        speed_threshold = int(speed_threshold * 1.5)

    # we want to swap which attribute gets updated in trickroom because the slower pokemon goes first
    if battle.trick_room:
        bot_went_first = not bot_went_first

    if bot_went_first:
        opponent_max_speed = min(
            battle.opponent.active.speed_range.max, speed_threshold
        )
        battle.opponent.active.speed_range = StatRange(
            min=battle.opponent.active.speed_range.min, max=opponent_max_speed
        )
        logger.info(
            "Updated {}'s max speed to {}".format(
                battle.opponent.active.name, battle.opponent.active.speed_range.max
            )
        )

    else:
        opponent_min_speed = max(
            battle.opponent.active.speed_range.min, speed_threshold
        )
        battle.opponent.active.speed_range = StatRange(
            min=opponent_min_speed, max=battle.opponent.active.speed_range.max
        )
        logger.info(
            "Updated {}'s min speed to {}".format(
                battle.opponent.active.name, battle.opponent.active.speed_range.min
            )
        )


def check_opponent_hiddenpower(battle, msg_line):
    """
    `msg_line` is should be the line *after* |-move|...|Hidden Power|...
    and is meant to be called for the opponent's pkmn only

    This function checks if the move was resisted, super-effective, or neutral.
    It then updates pkmn.hidden_power_possibilities based on that information
    """
    attacker = battle.opponent.active
    defender_types = battle.user.active.types
    logger.info(
        "Checking hiddenpower possibilities for opponent's {}".format(attacker.name)
    )
    logger.info(
        "Starting hiddenpower possibilities {}".format(
            attacker.hidden_power_possibilities
        )
    )

    next_line_split_msg = msg_line.split("|")
    if next_line_split_msg[1] == "-resisted":
        logger.info("{} resisted hiddenpower".format(defender_types))
        for t in list(attacker.hidden_power_possibilities):
            if not is_not_very_effective(t, defender_types):
                attacker.hidden_power_possibilities.remove(t)

    elif next_line_split_msg[1] == "-supereffective":
        logger.info("{} was weak to hiddenpower".format(defender_types))
        for t in list(attacker.hidden_power_possibilities):
            if not is_super_effective(t, defender_types):
                attacker.hidden_power_possibilities.remove(t)

    elif next_line_split_msg[1] == "-damage":
        logger.info("{} was neutral to hiddenpower".format(defender_types))
        for t in list(attacker.hidden_power_possibilities):
            if not is_neutral_effectiveness(t, defender_types):
                attacker.hidden_power_possibilities.remove(t)

    else:
        logger.info(
            "Cannot update hiddenpower possibilities with: {}".format(
                next_line_split_msg[1]
            )
        )
        return

    logger.info(
        "Remaining hiddenpower possibilities: {}".format(
            attacker.hidden_power_possibilities
        )
    )


def check_choicescarf_from_ability_order(battle, msg_lines):
    """
    Infer Choice Scarf from switch-in ability activation order.

    This is intentionally conservative:
    - only when both sides switched in this message batch
    - only for clearly pronounced switch-in abilities
    - only when opponent activates before us
    - only if opponent cannot naturally be faster at max speed without Scarf
    """
    if battle.generation in ["gen1", "gen2", "gen3"] or battle.trick_room:
        return

    if battle.user.active is None or battle.opponent.active is None:
        return

    opp = battle.opponent.active
    if opp.item != constants.UNKNOWN_ITEM or not opp.can_have_choice_item:
        return

    if can_have_speed_modified(battle, opp):
        return

    switched_sides = set()
    ability_events = []
    for idx, ln in enumerate(msg_lines):
        if ln.startswith("|switch|"):
            split_ln = ln.split("|")
            if len(split_ln) >= 3:
                side_id = _side_id_from_protocol_ident(split_ln[2])
                if side_id:
                    switched_sides.add(side_id)
        if not ln.startswith("|-ability|"):
            continue
        split_ln = ln.split("|")
        if len(split_ln) < 4:
            continue
        side_id = _side_id_from_protocol_ident(split_ln[2])
        if side_id not in {battle.user.name, battle.opponent.name}:
            continue
        ability = normalize_name(split_ln[3])
        if ability in ABILITIES_SAFE_FOR_SPEED_ORDER_INFERENCE:
            ability_events.append((idx, side_id, ability))

    if battle.user.name not in switched_sides or battle.opponent.name not in switched_sides:
        return

    user_event = next((e for e in ability_events if e[1] == battle.user.name), None)
    opp_event = next((e for e in ability_events if e[1] == battle.opponent.name), None)
    if user_event is None or opp_event is None:
        return

    # We only infer Scarf when opponent's pronounced ability triggers before ours.
    if opp_event[0] >= user_event[0]:
        return

    battle_copy = deepcopy(battle)
    # Compute opponent's max plausible speed WITHOUT scarf.
    battle_copy.opponent.active.set_spread("jolly", "0,0,0,0,0,252")
    if battle_copy.opponent.active.item == constants.UNKNOWN_ITEM:
        battle_copy.opponent.active.item = None

    opp_no_scarf_speed = battle_copy.get_effective_speed(battle_copy.opponent)
    our_effective_speed = battle_copy.get_effective_speed(battle_copy.user)

    if our_effective_speed > opp_no_scarf_speed:
        logger.info(
            "Opponent %s ability order implies Choice Scarf (%s before %s, %d > %d no-scarf max)",
            opp.name,
            opp_event[2],
            user_event[2],
            our_effective_speed,
            opp_no_scarf_speed,
        )
        opp.item = "choicescarf"
        opp.item_inferred = True


def check_choicescarf(battle, msg_lines):
    # If either side switched this turn - don't do this check
    if any(
        battle.generation in ["gen1", "gen2", "gen3"]
        or ln.startswith("|switch|")
        or ln.startswith("|cant|")
        or (ln.startswith("|-activate|") and ln.endswith("confusion"))
        for ln in msg_lines
    ) or battle.user.last_selected_move.move.startswith("switch "):
        return

    moves = [get_move_information(m) for m in msg_lines if m.startswith("|move|")]
    number_of_moves = len(moves)

    # if the bot went first we cannot ever infer a choicescarf
    if number_of_moves not in [1, 2] or moves[0][0].startswith(battle.user.name):
        return

    elif number_of_moves == 1:
        moves.append(
            (
                "{}a: {}".format(battle.opponent.name, battle.user.active.name),
                all_move_json[normalize_name(battle.user.last_selected_move.move)],
            )
        )

    if moves[0][1][constants.PRIORITY] != moves[1][1][constants.PRIORITY]:
        return

    battle_copy = deepcopy(battle)
    if (
        battle.opponent.active is None
        or battle.opponent.active.item != constants.UNKNOWN_ITEM
        or not battle.opponent.active.can_have_choice_item
        or can_have_speed_modified(battle, battle.opponent.active)
        or can_have_priority_modified(
            battle, battle.opponent.active, moves[0][1][constants.ID]
        )
        or can_have_priority_modified(
            battle, battle.user.active, moves[1][1][constants.ID]
        )
        or (
            battle_copy.user.active.ability == "unburden"
            and battle_copy.user.active.item is None
        )
    ):
        return

    if battle.battle_type == BattleType.RANDOM_BATTLE:
        battle_copy.opponent.active.set_spread(
            "serious", "85,85,85,85,85,85"
        )  # random battles have known spreads
    else:
        if battle.trick_room:
            battle_copy.opponent.active.set_spread(
                "quiet", "0,0,0,0,0,0"
            )  # assume as slow as possible in trickroom
        else:
            battle_copy.opponent.active.set_spread(
                "jolly", "0,0,0,0,0,252"
            )  # assume as fast as possible
    opponent_effective_speed = battle_copy.get_effective_speed(battle_copy.opponent)
    bot_effective_speed = battle_copy.get_effective_speed(battle_copy.user)

    if battle.trick_room:
        has_scarf = opponent_effective_speed > bot_effective_speed
    else:
        has_scarf = bot_effective_speed > opponent_effective_speed

    if has_scarf:
        logger.info(
            "Opponent {} could not have gone first - setting it's item to choicescarf".format(
                battle.opponent.active.name
            )
        )
        battle.opponent.active.item = "choicescarf"
        battle.opponent.active.item_inferred = True


def check_heavydutyboots(battle, msg_lines):
    side_to_check = battle.opponent

    if (
        battle.generation not in ["gen8", "gen9"]
        or side_to_check.active.item != constants.UNKNOWN_ITEM
        or "magicguard"
        in [
            normalize_name(a)
            for a in pokedex[side_to_check.active.name][constants.ABILITIES].values()
        ]
    ):
        return

    if side_to_check.side_conditions[constants.STEALTH_ROCK] > 0:
        pkmn_took_stealthrock_damage = False
        for line in msg_lines:
            split_line = line.split("|")

            # |-damage|p2a: Weedle|88/100|[from] Stealth Rock
            if (
                len(split_line) > 4
                and split_line[1] == "-damage"
                and split_line[2].startswith(side_to_check.name)
                and split_line[4] == "[from] Stealth Rock"
            ):
                pkmn_took_stealthrock_damage = True

        if not pkmn_took_stealthrock_damage:
            logger.info("{} has heavydutyboots".format(side_to_check.active.name))
            side_to_check.active.item = "heavydutyboots"
            side_to_check.active.item_inferred = True
        else:
            logger.info(
                "{} was affected by stealthrock, it cannot have heavydutyboots".format(
                    side_to_check.active.name
                )
            )
            side_to_check.active.impossible_items.add(constants.HEAVY_DUTY_BOOTS)

    elif (
        side_to_check.side_conditions[constants.SPIKES] > 0
        and "levitate"
        not in [
            normalize_name(a)
            for a in pokedex[side_to_check.active.name][constants.ABILITIES].values()
        ]
        and not side_to_check.active.has_type("flying")
        and side_to_check.active.ability != "levitate"
    ):
        pkmn_took_spikes_damage = False
        for line in msg_lines:
            split_line = line.split("|")

            # |-damage|p2a: Weedle|88/100|[from] Spikes
            if (
                len(split_line) > 4
                and split_line[1] == "-damage"
                and split_line[2].startswith(side_to_check.name)
                and split_line[4] == "[from] Spikes"
            ):
                pkmn_took_spikes_damage = True

        if not pkmn_took_spikes_damage:
            logger.info("{} has heavydutyboots".format(side_to_check.active.name))
            side_to_check.active.item = "heavydutyboots"
            side_to_check.active.item_inferred = True
        else:
            logger.info(
                "{} was affected by spikes, it cannot have heavydutyboots".format(
                    side_to_check.active.name
                )
            )
            side_to_check.active.impossible_items.add(constants.HEAVY_DUTY_BOOTS)
    elif (
        side_to_check.side_conditions[constants.TOXIC_SPIKES] > 0
        and side_to_check.active.status is None
        and not side_to_check.active.has_type("flying")
        and not side_to_check.active.has_type("poison")
        and not side_to_check.active.has_type("steel")
        and side_to_check.active.ability != "levitate"
        and "levitate"
        not in [
            normalize_name(a)
            for a in pokedex[side_to_check.active.name][constants.ABILITIES].values()
        ]
        and side_to_check.active.ability not in constants.IMMUNE_TO_POISON_ABILITIES
    ):
        pkmn_took_toxicspikes_poison = False
        for line in msg_lines:
            split_line = line.split("|")

            # a pokemon can be toxic-ed from sources other than toxicspikes
            # stopping at one of these strings ensures those other sources aren't considered
            if len(split_line) < 2 or split_line[1] in {"move", "upkeep", ""}:
                break

            # |-status|p2a: Pikachu|psn
            if (
                split_line[1] == "-status"
                and (
                    split_line[3] == constants.POISON
                    or split_line[3] == constants.TOXIC
                )
                and split_line[2].startswith(side_to_check.name)
            ):
                pkmn_took_toxicspikes_poison = True

        if not pkmn_took_toxicspikes_poison:
            logger.info("{} has heavydutyboots".format(side_to_check.active.name))
            side_to_check.active.item = "heavydutyboots"
            side_to_check.active.item_inferred = True
        else:
            logger.info(
                "{} was affected by toxicspikes, it cannot have heavydutyboots".format(
                    side_to_check.active.name
                )
            )
            side_to_check.active.impossible_items.add(constants.HEAVY_DUTY_BOOTS)

    elif (
        side_to_check.side_conditions[constants.STICKY_WEB] > 0
        and not side_to_check.active.has_type("flying")
        and "levitate"
        not in [
            normalize_name(a)
            for a in pokedex[side_to_check.active.name][constants.ABILITIES].values()
        ]
    ):
        pkmn_was_affected_by_stickyweb = False
        for line in msg_lines:
            split_line = line.split("|")

            # |-activate|p2a: Gengar|move: Sticky Web
            if (
                len(split_line) == 4
                and split_line[1] == "-activate"
                and split_line[2].startswith(side_to_check.name)
                and split_line[3] == "move: Sticky Web"
            ):
                pkmn_was_affected_by_stickyweb = True

        if not pkmn_was_affected_by_stickyweb:
            logger.info("{} has heavydutyboots".format(side_to_check.active.name))
            side_to_check.active.item = "heavydutyboots"
            side_to_check.active.item_inferred = True
        else:
            logger.debug(
                "{} was affected by sticky web, it cannot have heavydutyboots".format(
                    side_to_check.active.name
                )
            )
            side_to_check.active.impossible_items.add(constants.HEAVY_DUTY_BOOTS)


