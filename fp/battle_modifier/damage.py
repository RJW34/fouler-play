# fp/battle_modifier/damage.py
# Auto-split from battle_modifier.py

from fp.battle_modifier._common import *  # noqa: F403
from fp.battle_modifier.switching import _switch_active_with_zoroark_from_reserves
from fp.battle_modifier._common import _parse_time_left_seconds, _side_id_from_protocol_ident

def sethp(battle, split_msg):
    # |-sethp|p2a: Jellicent|317/403|[from] move: Pain Split|[silent]
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
        # Guard: pkmn can be None during async transitions (faint/switchout)
        if pkmn is None:
            logger.debug("sethp: opponent pkmn is None, skipping")
            return
        new_hp_percentage = float(split_msg[3].split("/")[0]) / 100
        pkmn.hp = int(pkmn.max_hp * new_hp_percentage)
    else:
        pkmn = battle.user.active
        # Guard: pkmn can be None during async transitions (faint/switchout)
        if pkmn is None:
            logger.debug("sethp: user pkmn is None, skipping")
            return
        pkmn.hp = int(split_msg[3].split("/")[0])
        pkmn.max_hp = int(split_msg[3].split("/")[1].split()[0])


def heal_or_damage(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
        other_side = battle.user
        pkmn = battle.opponent.active
        if len(split_msg) == 5 and split_msg[4] == "[from] move: Revival Blessing":
            nickname = Pokemon.extract_nickname_from_pokemonshowdown_string(
                split_msg[2]
            )
            pkmn = side.find_reserve_pokemon_by_nickname(nickname)

        # Guard: pkmn can be None during async transitions (faint/switchout)
        if pkmn is None:
            logger.debug("heal_or_damage: opponent pkmn is None, skipping")
            return

        # opponent hp is given as a percentage
        if constants.FNT in split_msg[3]:
            pkmn.hp = 0
        else:
            new_hp_percentage = float(split_msg[3].split("/")[0]) / 100
            pkmn.hp = pkmn.max_hp * new_hp_percentage

    else:
        side = battle.user
        other_side = battle.opponent
        pkmn = battle.user.active
        if len(split_msg) == 5 and split_msg[4] == "[from] move: Revival Blessing":
            nickname = Pokemon.extract_nickname_from_pokemonshowdown_string(
                split_msg[2]
            )
            pkmn = side.find_reserve_pokemon_by_nickname(nickname)
        
        # Guard: pkmn can be None during async transitions (faint/switchout)
        if pkmn is None:
            logger.debug("heal_or_damage: user pkmn is None, skipping")
            return
        
        if constants.FNT in split_msg[3]:
            pkmn.hp = 0
        else:
            pkmn.hp = float(split_msg[3].split("/")[0])
            pkmn.max_hp = float(split_msg[3].split("/")[1].split()[0])

    # increase the amount of turns toxic has been active
    if (
        len(split_msg) == 5
        and constants.TOXIC in split_msg[3]
        and "[from] psn" in split_msg[4]
    ):
        side.side_conditions[constants.TOXIC_COUNT] += 1

    if (
        len(split_msg) == 6
        and split_msg[4].startswith("[from] item:")
        and other_side.name in split_msg[5]
    ):
        item = normalize_name(split_msg[4].split("item:")[-1])
        # Guard: other_side.active can be None during async transitions
        if other_side.active is not None:
            logger.info("Setting {}'s item to: {}".format(other_side.active.name, item))
            other_side.active.item = item

    if (
        len(split_msg) >= 5
        and split_msg[-1].startswith("[from]")
        and split_msg[-1].endswith("Healing Wish")
    ):
        logger.info(
            "{} was healed from healing wish, setting side condition to 0".format(
                side.active.name
            )
        )
        side.side_conditions[constants.HEALING_WISH] = 0

    # set the ability for the other side (the side not taking damage, '-damage' only)
    if (
        len(split_msg) == 6
        and split_msg[4].startswith("[from] ability:")
        and other_side.name in split_msg[5]
        and split_msg[1] == "-damage"
    ):
        ability = normalize_name(split_msg[4].split("ability:")[-1])
        # Guard: other_side.active can be None during async transitions
        if other_side.active is not None:
            logger.info(
                "Setting {}'s ability to: {}".format(other_side.active.name, ability)
            )
            other_side.active.ability = ability

    # set the ability of the side (the side being healed, '-heal' only)
    if (
        len(split_msg) == 6
        and constants.ABILITY in split_msg[4]
        and other_side.name in split_msg[5]
        and split_msg[1] == "-heal"
    ):
        ability = normalize_name(split_msg[4].split(constants.ABILITY)[-1].strip(": "))
        logger.info("Setting {}'s ability to: {}".format(pkmn.name, ability))
        pkmn.ability = ability

    # give that pokemon an item if this string specifies one
    # Handles self-damage items like Life Orb: |-damage|p2a: Pokemon|90/100|[from] item: Life Orb
    # And healing items like Leftovers: |-heal|p2a: Pokemon|95/100|[from] item: Leftovers
    # Only set if item is currently unknown (don't overwrite None which means item was consumed/knocked off)
    if len(split_msg) == 5 and constants.ITEM in split_msg[4] and pkmn.item is not None:
        item = normalize_name(split_msg[4].split(constants.ITEM)[-1].strip(": "))
        logger.info("Setting {}'s item to: {} (revealed by damage/heal)".format(pkmn.name, item))
        pkmn.item = item

    # gen 1 if you are trapping the opponent and hit yourself in confusion, the opponent is released
    if (
        battle.generation == "gen1"
        and split_msg[-1] == "[from] confusion"
        and other_side.active is not None
        and (
            constants.PARTIALLY_TRAPPED in other_side.active.volatile_statuses
            or other_side.active.volatile_status_durations[constants.PARTIALLY_TRAPPED]
            > 0
        )
    ):
        logger.info(
            f"{pkmn.name} hit itself in confusion, releasing partially trapped volatile on {other_side.active.name}"
        )
        remove_volatile(other_side.active, constants.PARTIALLY_TRAPPED)
        other_side.active.volatile_status_durations[constants.PARTIALLY_TRAPPED] = 0


def faint(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    # Guard: side.active can be None during async transitions (already processed faint)
    if side.active is None:
        logger.debug("faint: side.active is None, skipping")
        return

    side.active.hp = 0


def move(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
        pkmn = battle.opponent.active
        opposing_pkmn = battle.user.active
    else:
        side = battle.user
        pkmn = battle.user.active
        opposing_pkmn = battle.opponent.active

    if pkmn is None:
        logger.warning("Move received but active Pokemon is None; skipping move processing")
        return

    move_name = normalize_name(split_msg[3].strip().lower())

    zoroark_from_reserves = side.find_pokemon_in_reserves(
        "zoroark"
    ) or side.find_pokemon_in_reserves("zoroarkhisui")

    # in battle factory we can deduce that there is a zoroark in front of us
    # if we see a move that is not in the known moveset and a zoroark is in the reserves
    if (
        is_opponent(battle, split_msg)
        and zoroark_from_reserves is not None
        and "transform" not in pkmn.volatile_statuses
        and battle.battle_type
        in [BattleType.BATTLE_FACTORY, BattleType.STANDARD_BATTLE]
        and move_name not in TeamDatasets.get_all_possible_moves(pkmn)
        and move_name in TeamDatasets.get_all_possible_moves(zoroark_from_reserves)
        and "from" not in split_msg[-1]
    ):
        logger.info(
            "{} using {} means it is {}".format(
                pkmn.name, move_name, zoroark_from_reserves.name
            )
        )
        _switch_active_with_zoroark_from_reserves(side, zoroark_from_reserves)

        # the rest of this function uses `pkmn`, so we need to set it to the correct pkmn
        pkmn = zoroark_from_reserves

    # in randombattles we can deduce that there is a zoroark in front of us
    # if we see a move that is not in the known moveset, even if there is no
    # zoroark is in the reserves
    if (
        is_opponent(battle, split_msg)
        and battle.battle_type == BattleType.RANDOM_BATTLE
        and "transform" not in pkmn.volatile_statuses
        and move_name not in RandomBattleTeamDatasets.get_all_possible_moves(pkmn)
        and "from" not in split_msg[-1]
    ):
        actual_zoroark = None
        zoroark_hisui = Pokemon("zoroarkhisui", 100)
        zoroark_regular = Pokemon("zoroark", 100)
        if (
            zoroark_from_reserves is not None
            and move_name
            in RandomBattleTeamDatasets.get_all_possible_moves(zoroark_from_reserves)
        ):
            actual_zoroark = zoroark_from_reserves

        elif (
            battle.generation not in constants.NO_TEAM_PREVIEW_GENS
            and zoroark_from_reserves is None
            and move_name
            in RandomBattleTeamDatasets.get_all_possible_moves(zoroark_hisui)
        ):
            actual_zoroark = zoroark_hisui
            actual_zoroark.level = RandomBattleTeamDatasets.predict_set(
                actual_zoroark
            ).pkmn_set.level
            side.reserve.append(actual_zoroark)

        elif (
            battle.generation not in constants.NO_TEAM_PREVIEW_GENS
            and zoroark_from_reserves is None
            and move_name
            in RandomBattleTeamDatasets.get_all_possible_moves(zoroark_regular)
        ):
            actual_zoroark = zoroark_regular
            actual_zoroark.level = RandomBattleTeamDatasets.predict_set(
                actual_zoroark
            ).pkmn_set.level
            side.reserve.append(actual_zoroark)

        if actual_zoroark is not None:
            logger.info(
                "{} using {} means it is {}".format(
                    pkmn.name, move_name, actual_zoroark.name
                )
            )
            _switch_active_with_zoroark_from_reserves(side, actual_zoroark)

            # the rest of this function uses `pkmn`, so we need to set it to the correct pkmn
            pkmn = actual_zoroark

    # Track movepool usage (learns physical/special/mixed classification)
    # Only track opponent moves (we already know our own movepool)
    if is_opponent(battle, split_msg):
        record_move(pkmn.name, move_name)

    if (
        any(msg == "[from]Sleep Talk" for msg in split_msg)
        and battle.generation == "gen3"
    ):
        pkmn.gen_3_consecutive_sleep_talks += 1
        logger.info(
            "{} gen3 consecutive sleep talks: {}".format(
                pkmn.name, pkmn.gen_3_consecutive_sleep_talks
            )
        )
    elif move_name != "sleeptalk":
        pkmn.gen_3_consecutive_sleep_talks = 0

    # in gen1, if you successfully hit with a partially trapping move, the volatile is applied here
    # cannot use the 'cant' message because a slow wrap still needs the volatile/duration applied
    # e.g. |move|p1a: Dragonite|Wrap|p2a: Tauros|
    # does not activate on a miss: |move|p1a: Dragonite|Wrap|p2a: Tauros|[miss]
    if (
        battle.generation == "gen1"
        and all_move_json.get(move_name, {}).get(constants.VOLATILE_STATUS)
        == constants.PARTIALLY_TRAPPED
        and not any(msg == "[miss]" for msg in split_msg)
    ):
        opposing_pkmn.volatile_status_durations[constants.PARTIALLY_TRAPPED] += 1
        if constants.PARTIALLY_TRAPPED not in opposing_pkmn.volatile_statuses:
            opposing_pkmn.volatile_statuses.append(constants.PARTIALLY_TRAPPED)

        logger.info(
            f"{pkmn.name} successfully used Wrap, incrementing partially trapped volatile on "
            f"{opposing_pkmn.name} to {opposing_pkmn.volatile_status_durations[constants.PARTIALLY_TRAPPED]}"
        )

    # in gen1 if you just moved, you are released from partially trapped
    if battle.generation == "gen1" and (
        pkmn.volatile_status_durations[constants.PARTIALLY_TRAPPED] > 0
        or constants.PARTIALLY_TRAPPED in pkmn.volatile_statuses
    ):
        logger.info(f"{pkmn.name} used a move, removing partially trapped volatile")
        remove_volatile(pkmn, constants.PARTIALLY_TRAPPED)
        pkmn.volatile_status_durations[constants.PARTIALLY_TRAPPED] = 0

    # gen1 stat modification glitches.
    # swordsdance and agility nullify the effects of burn and paralysis respectively
    # This is implemented by setting a custom volatile
    if battle.generation == "gen1":
        if (
            move_name == "swordsdance" or move_name == "meditate"
        ) and pkmn.status == constants.BURN:
            logger.info(
                "{} used swordsdance with burn, nullifying the effects of burn".format(
                    pkmn.name
                )
            )
            pkmn.volatile_statuses.append("gen1burnnullify")
        elif move_name == "agility" and pkmn.status == constants.PARALYZED:
            logger.info(
                "{} used agility while paralyzed, nullifying the effects of paralysis".format(
                    pkmn.name
                )
            )
            pkmn.volatile_statuses.append("gen1paralysisnullify")

    if split_msg[-1] == "[from]Sleep Talk" or split_msg[-1] == "[from]move: Sleep Talk":
        move_object = pkmn.get_move(move_name)
        if move_object is None:
            pkmn.add_move(move_name)
            logger.info(
                "Added unrevealed {} to {}'s moves because it was called by sleeptalk".format(
                    move_name, pkmn.name
                )
            )
        return

    elif any(
        "[from]" in msg and msg != "[from]lockedmove" and msg != "[from] lockedmove"
        for msg in split_msg
    ):
        if split_msg[-1].startswith("[from] ability:"):
            ability = normalize_name(split_msg[-1].split("ability: ")[-1])
            logger.info("Setting {}'s ability to: {}".format(pkmn.name, ability))
            pkmn.ability = ability
        return

    if "destinybond" in pkmn.volatile_statuses:
        logger.info("Removing destinybond from {}".format(pkmn.name))
        remove_volatile(pkmn, "destinybond")

    if "encore" in pkmn.volatile_statuses:
        pkmn.volatile_status_durations["encore"] += 1
        logger.info(
            "Incrementing encore duration for {} to {}".format(
                pkmn.name, pkmn.volatile_status_durations["encore"]
            )
        )

    if (
        "taunt" in pkmn.volatile_statuses
        and battle.generation not in constants.TAUNT_DURATION_INCREMENT_END_OF_TURN
    ):
        pkmn.volatile_status_durations[constants.TAUNT] += 1
        logger.info(
            "Incrementing taunt duration for {} to {}".format(
                pkmn.name, pkmn.volatile_status_durations[constants.TAUNT]
            )
        )

    # remove volatile status if they have it
    # this is for preparation moves like Phantom Force
    if move_name in pkmn.volatile_statuses:
        logger.info("Removing volatile status {} from {}".format(move_name, pkmn.name))
        remove_volatile(pkmn, move_name)

    if move_name == "struggle":
        logger.info("Not adding struggle to {}'s moves".format(pkmn.name))
        return

    if move_name == "healingwish":
        logger.info(
            "{} used healingwish, setting side_condition to 1".format(pkmn.name)
        )
        side.side_conditions[constants.HEALING_WISH] = 1

    pkmn.moves_used_since_switch_in.add(move_name)

    # add the move to it's moves if it hasn't been seen
    # decrement the PP by one
    # if the move is unknown, do nothing
    pp_to_decrement = 2 if (opposing_pkmn and opposing_pkmn.ability == "pressure") else 1
    if is_opponent(battle, split_msg):
        try:
            pkmn.record_opponent_move(move_name, pp_to_decrement)
        except Exception:
            pass
    move_object = pkmn.get_move(move_name)
    if move_object is None:
        new_move = pkmn.add_move(move_name)
        if new_move is not None:
            new_move.current_pp -= pp_to_decrement
    else:
        move_object.current_pp -= pp_to_decrement
        logger.info(
            "{} already has the move {}. Decrementing the PP by {}".format(
                pkmn.name, move_name, pp_to_decrement
            )
        )

    # if this pokemon used two different moves without switching,
    # set a flag to signify that it cannot have a choice item
    if (
        is_opponent(battle, split_msg)
        and side.last_used_move.pokemon_name == side.active.name
        and side.last_used_move.move != move_name
    ):
        logger.info(
            "{} used two different moves - it cannot have a choice item".format(
                pkmn.name
            )
        )
        pkmn.can_have_choice_item = False
        if pkmn.item in constants.CHOICE_ITEMS and pkmn.item_inferred:
            logger.warning(
                "{} has a choice item, but used two different moves - setting it's item to UNKNOWN".format(
                    pkmn.name
                )
            )
            pkmn.item = constants.UNKNOWN_ITEM

    if unlikely_to_have_choice_item(move_name):
        logger.info(
            "{} using {} makes it unlikely to have a choice item. Setting can_have_choice_item to False".format(
                pkmn.name, move_name
            )
        )
        pkmn.can_have_choice_item = False

    try:
        mv = all_move_json[move_name]
        move_type = mv[constants.TYPE]
        if mv[constants.CATEGORY] != constants.STATUS:
            is_gen9 = battle.generation == "gen9"
            pkmn_gem = "{}gem".format(move_type)
            
            # In Gen 9, only Normal Gem is obtainable.
            if is_gen9 and pkmn_gem != "normalgem":
                pass
            else:
                logger.info(
                    "{} used a {} move, removing {} from possible items".format(
                        pkmn.name, move_type, pkmn_gem
                    )
                )
                pkmn.impossible_items.add(pkmn_gem)
    except KeyError:
        logger.debug("Move '{}' not found in move JSON when checking gem for {}".format(move_name, pkmn.name))

    try:
        if (
            all_move_json[move_name][constants.SELF][constants.VOLATILE_STATUS]
            == constants.LOCKED_MOVE
        ):
            logger.info("Adding lockedmove to {}".format(pkmn.name))
            pkmn.volatile_statuses.append(constants.LOCKED_MOVE)
    except KeyError:
        logger.debug("Move '{}' has no self/volatileStatus for {}".format(move_name, pkmn.name))

    try:
        if all_move_json[move_name][constants.CATEGORY] == constants.STATUS:
            logger.info(
                "{} used a status-move. Adding `assaultvest` to impossible items".format(
                    pkmn.name
                )
            )
            pkmn.impossible_items.add(constants.ASSAULT_VEST)
    except KeyError:
        logger.debug("Move '{}' has no category for {}".format(move_name, pkmn.name))

    try:
        category = all_move_json[move_name][constants.CATEGORY]
        logger.info("Setting {}'s last used move: {}".format(pkmn.name, move_name))
        if not any(
            "[from]move: Sleep Talk" in msg or "[from]Sleep Talk" in msg
            for msg in split_msg
        ):
            side.last_used_move = LastUsedMove(
                pokemon_name=pkmn.name, move=move_name, turn=battle.turn
            )
    except KeyError:
        category = None
        if not any(
            "[from]move: Sleep Talk" in msg or "[from]Sleep Talk" in msg
            for msg in split_msg
        ):
            side.last_used_move = LastUsedMove(
                pokemon_name=pkmn.name, move=constants.DO_NOTHING_MOVE, turn=battle.turn
            )

    # if this pokemon used a damaging move, eliminate the possibility of guessing a lifeorb
    # the lifeorb will reveal itself if it has it
    if category in constants.DAMAGING_CATEGORIES and not any(
        [
            normalize_name(a) in ["sheerforce", "magicguard"]
            for a in pokedex[pkmn.name][constants.ABILITIES].values()
        ]
    ):
        logger.info(
            "{} used a damaging move - not guessing lifeorb anymore".format(pkmn.name)
        )
        pkmn.impossible_items.add(constants.LIFE_ORB)

    # there is nothing special in the protocol for "wish" - it must be extracted here
    if move_name == constants.WISH and "still" not in split_msg[4]:
        logger.info(
            "{} used wish - expecting {} health of recovery next turn".format(
                side.active.name, side.active.max_hp / 2
            )
        )
        side.wish = (2, side.active.max_hp / 2)

    if move_name == "batonpass":
        side.baton_passing = True

    # |move|p1a: Slaking|Earthquake|p2a: Heatran
    if pkmn.ability == "truant" or pkmn.name == "slaking":
        if "truant" not in pkmn.volatile_statuses:
            logger.info("Adding 'truant' to {}'s volatiles".format(pkmn.name))
            pkmn.volatile_statuses.append("truant")


def get_damage_dealt(battle, split_msg, next_messages):
    move_name = normalize_name(split_msg[3])
    critical_hit = False

    if is_opponent(battle, split_msg):
        attacking_side = battle.opponent
        defending_side = battle.user
    else:
        attacking_side = battle.user
        defending_side = battle.opponent

    for line in next_messages:
        next_line_split = line.split("|")
        # if one of these strings appears in index 1 then
        # exit out since we are done with this pokemon's move
        if len(next_line_split) < 2 or next_line_split[1] in MOVE_END_STRINGS:
            break

        elif next_line_split[1] == "-crit":
            critical_hit = True

        # if '-damage' appears, we want to parse the percentage damage dealt
        elif (
            next_line_split[1] == "-damage"
            and defending_side.name in next_line_split[2]
        ):
            # Guard against None active pokemon (intermittent bug)
            if defending_side.active is None:
                logger.warning(f"defending_side.active is None for damage calculation, skipping")
                return None
            
            final_health, maxhp, _ = get_pokemon_info_from_condition(next_line_split[3])
            # maxhp can be 0 if the targetted pokemon fainted
            # the message would be: "0 fnt"
            if maxhp == 0:
                maxhp = defending_side.active.max_hp

            damage_dealt = (
                defending_side.active.hp / defending_side.active.max_hp
            ) * maxhp - final_health
            damage_percentage = round(damage_dealt / maxhp, 4)

            # Guard: attacking_side.active can also be None during async transitions
            if attacking_side.active is None:
                logger.warning(f"attacking_side.active is None for damage calculation, skipping")
                return None

            logger.info(
                "{} did {}% damage to {} with {}".format(
                    attacking_side.active.name,
                    damage_percentage * 100,
                    defending_side.active.name,
                    move_name,
                )
            )
            return DamageDealt(
                attacker=attacking_side.active.name,
                defender=defending_side.active.name,
                move=move_name,
                percent_damage=damage_percentage,
                crit=critical_hit,
            )


def _do_check(
    battle,
    battle_copy,
    possibilites,
    check_type,
    damage_dealt,
    bot_went_first,
    check_lower_bound,
    allow_emptying=False,
):
    actual_damage_dealt = damage_dealt.percent_damage * battle_copy.user.active.max_hp

    indicies_to_remove = []
    num_starting_possibilites = len(possibilites)
    for i in range(num_starting_possibilites):
        p = possibilites[i]
        if isinstance(p, PredictedPokemonSet):
            p = p.pkmn_set

        if not battle.opponent.active.ability:
            battle_copy.opponent.active.ability = p.ability
        if battle.opponent.active.item == constants.UNKNOWN_ITEM:
            battle_copy.opponent.active.item = p.item
        battle_copy.opponent.active.set_spread(
            p.nature, ",".join(str(x) for x in p.evs)
        )

        if check_type == "damage_received":
            actual_damage_dealt = (
                damage_dealt.percent_damage * battle_copy.opponent.active.max_hp
            )

            if bot_went_first:
                opponent_move = constants.DO_NOTHING_MOVE
            else:
                opponent_move = battle_copy.opponent.last_used_move.move

            damage, _ = poke_engine_get_damage_rolls(
                battle_copy, damage_dealt.move, opponent_move, bot_went_first
            )
        elif check_type == "damage_dealt":
            _, damage = poke_engine_get_damage_rolls(
                battle_copy,
                battle_copy.user.last_selected_move.move,
                damage_dealt.move,
                bot_went_first,
            )
        else:
            raise ValueError("Invalid check_type: {}".format(check_type))

        if damage_dealt.crit:
            max_damage = damage[1]
        else:
            max_damage = damage[0]

        damage = [max_damage * 0.85, max_damage]
        lower_bound_violated = check_lower_bound and (
            actual_damage_dealt < (damage[0] * 0.975 - 5)
        )
        upper_bound_violated = actual_damage_dealt > (damage[1] * 1.025 + 5)
        if lower_bound_violated or upper_bound_violated:
            logger.debug(
                "{} is invalid based on reverse damage calc. damage_dealt={}, lower={}, upper={}".format(
                    p, actual_damage_dealt, damage[0], damage[1]
                )
            )
            indicies_to_remove.append(i)

    if len(indicies_to_remove) == num_starting_possibilites and not allow_emptying:
        logger.warning("Would remove all possibilities, not removing any")
        logger.warning(f"{actual_damage_dealt=}")
        return

    for i in reversed(indicies_to_remove):
        possibilites.pop(i)


def update_dataset_possibilities(
    battle,
    damage_dealt,
    check_type,
):
    if (
        battle.wait
        or battle.generation in {"gen1", "gen2"}
        or battle.opponent.active is None
        or battle.opponent.active.hp <= 0
        or battle.opponent.active.name
        in ["ditto", "shedinja", "terapagosterastal", "meloetta", "meloettapirouette"]
        or battle.user.active.name
        in ["ditto", "shedinja", "terapagosterastal", "meloetta", "meloettapirouette"]
        or damage_dealt.move not in all_move_json
        or all_move_json[damage_dealt.move][constants.CATEGORY] == constants.STATUS
        or "multiaccuracy" in all_move_json[damage_dealt.move]
        or damage_dealt.move.startswith(constants.HIDDEN_POWER)
        or damage_dealt.percent_damage == 0
        or (
            check_type == "damage_dealt"
            and battle.opponent.last_used_move.move != damage_dealt.move
        )
        or (
            check_type == "damage_received"
            and battle.user.last_used_move.move != damage_dealt.move
        )
        or damage_dealt.move
        in [
            "pursuit",
            "struggle",
            "counter",
            "mirrorcoat",
            "metalburst",
            "foulplay",
            "meteorbeam",
            "electroshot",
            "ficklebeam",
            "lashout",
            "ragefist",
            "shellsidearm",
            "futuresight",
        ]
    ):
        return

    battle_copy = deepcopy(battle)

    if battle.battle_type == BattleType.RANDOM_BATTLE:
        possibilites = RandomBattleTeamDatasets.get_pkmn_sets_from_pkmn_name(
            battle.opponent.active
        )
        smogon_possibilities = None
        allow_emptying = False
    elif battle.battle_type == BattleType.BATTLE_FACTORY:
        possibilites = TeamDatasets.get_pkmn_sets_from_pkmn_name(battle.opponent.active)
        smogon_possibilities = None
        allow_emptying = False
    else:
        possibilites = TeamDatasets.get_pkmn_sets_from_pkmn_name(battle.opponent.active)
        smogon_possibilities = SmogonSets.get_pkmn_sets_from_pkmn_name(
            battle.opponent.active
        )
        allow_emptying = True

    check_lower_bound = True
    if check_type == "damage_dealt":
        user_percent_hp = round(battle.user.active.hp / battle.user.active.max_hp, 2)
        if abs(damage_dealt.percent_damage - user_percent_hp) < 0.02:
            check_lower_bound = False
        bot_went_first = (
            battle.user.last_used_move.turn == battle.opponent.last_used_move.turn
        )
    elif check_type == "damage_received":
        opponent_percent_hp = round(
            battle.opponent.active.hp / battle.opponent.active.max_hp, 2
        )
        if abs(damage_dealt.percent_damage - opponent_percent_hp) < 0.02:
            check_lower_bound = False
        bot_went_first = (
            battle.opponent.last_used_move.turn != battle.user.last_used_move.turn
        )
    else:
        raise ValueError("Invalid check_type: {}".format(check_type))

    logger.debug(f"{check_type=}")
    logger.debug(f"{check_lower_bound=}")
    logger.debug(f"{bot_went_first=}")

    _do_check(
        battle,
        battle_copy,
        possibilites,
        check_type,
        damage_dealt,
        bot_went_first,
        check_lower_bound,
        allow_emptying=allow_emptying,
    )

    if smogon_possibilities is not None:
        _do_check(
            battle,
            battle_copy,
            smogon_possibilities,
            check_type,
            damage_dealt,
            bot_went_first,
            check_lower_bound,
            allow_emptying=False,  # never completely empty smogon stats
        )


def check_rocky_helmet(battle, split_msg, msg_lines):
    # Bot used a move. Check if opponent has Rocky Helmet.
    if len(split_msg) < 5:
        return

    move_name = normalize_name(split_msg[3])
    if move_name not in all_move_json:
        return

    try:
        # Check generation validity (Rocky Helmet is Gen 5+)
        if battle.generation and int(battle.generation.replace("gen", "")) < 5:
            return
    except ValueError:
        pass

    mv_data = all_move_json[move_name]
    if not mv_data.get("flags", {}).get("contact"):
        return

    opponent = battle.opponent.active
    # Guard: opponent can be None during async transitions (faint/switchout)
    if opponent is None:
        return
    if opponent.item != constants.UNKNOWN_ITEM:
        return
    if "rockyhelmet" in opponent.impossible_items:
        return

    # Check for damage message
    took_helmet_damage = False
    valid_hit = True
    
    for line in msg_lines:
        s = line.split("|")
        if len(s) < 2:
            continue
            
        # Stop at next move/turn/upkeep
        if s[1] in ["move", "turn", "upkeep", "faint"]:
            break
            
        # |-damage|p1a: ...|[from] item: Rocky Helmet
        if len(s) >= 5 and s[1] == "-damage" and "item: Rocky Helmet" in s[4]:
             took_helmet_damage = True
             break
             
        # Check for miss/protect
        if s[1] in ["-miss", "-fail", "-immune", "cant"]:
             valid_hit = False
             break
        if s[1] == "-activate" and "Protect" in line:
             valid_hit = False
             break

    if took_helmet_damage:
         logger.info(f"Rocky Helmet detected on {opponent.name}")
         opponent.item = "rockyhelmet"
         opponent.item_inferred = True
    elif valid_hit:
         # If we made contact and didn't take damage, they don't have it
         logger.info(f"No Rocky Helmet damage on contact move against {opponent.name}. Adding to impossible items.")
         opponent.impossible_items.add("rockyhelmet")


