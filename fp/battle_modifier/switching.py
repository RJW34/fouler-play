# fp/battle_modifier/switching.py
# Auto-split from battle_modifier.py

from fp.battle_modifier._common import *  # noqa: F403
from fp.battle_modifier._common import _parse_time_left_seconds, _side_id_from_protocol_ident

def request(battle, split_msg):
    if len(split_msg) >= 2:
        battle_json = json.loads(split_msg[2].strip("'"))
        logger.debug("Received battle JSON from server: {}".format(battle_json))
        battle.rqid = battle_json[constants.RQID]

        if battle_json.get(constants.FORCE_SWITCH):
            battle.force_switch = True
        else:
            battle.force_switch = False

        if battle_json.get(constants.WAIT):
            battle.wait = True
        else:
            battle.wait = False

        battle.request_json = battle_json


def inactive(battle, split_msg):
    if len(split_msg) < 3:
        return
    text = split_msg[2]
    if constants.TIME_LEFT not in text:
        return

    time_left = _parse_time_left_seconds(text)
    if time_left is None:
        logger.warning("Failed to parse time left from inactive msg: '%s'", text)
        return

    battle.time_remaining = time_left
    logger.debug("Time left: %s", time_left)


def inactiveoff(battle, _):
    battle.time_remaining = None


def user_just_switched_into_zoroark(battle, switch_or_drag):
    """
    some truly heinous shit going on here, can we ban this fucker?

    Two scenarios we can detect we are a zoroark:
      1. We switched and the last action we selected starts with `switch zoroark` (to account for both zoroarks)
      2. We were dragged (circle throw, etc) AND the active pkmn on the next turn is zoroark

    is it not sound to check for "we switched or dragged and the request JSON has zoroark as active?"
    No. If we switched into zoroark and then got circle-thrown out then the request JSON would not have
        zoroark as active but our switch needs to have been into zoroark.

    This doesn't need to deal with the first-turn switch-in of the user's Zoroark because the first-turn is
    instantiated from the request_json
    """

    return (
        # Scenario 1
        (
            switch_or_drag == "switch"
            and battle.user.last_selected_move.move.startswith("switch zoroark")
        )
        # Scenario 2
        or (
            switch_or_drag == "drag"
            and battle.request_json is not None
            and battle.request_json[constants.SIDE][constants.POKEMON][0][
                constants.DETAILS
            ].startswith("Zoroark")
            and battle.request_json[constants.SIDE][constants.POKEMON][0][
                constants.ACTIVE
            ]
        )
    )


def switch(battle, split_msg):
    switch_or_drag(battle, split_msg, switch_or_drag="switch")


def drag(battle, split_msg):
    switch_or_drag(battle, split_msg, switch_or_drag="drag")


def switch_or_drag(battle, split_msg, switch_or_drag="switch"):
    if is_opponent(battle, split_msg):
        side_name = "opponent"
        side = battle.opponent
        other_side = battle.user
        logger.info("Opponent has switched - clearing the last used move")
    else:
        side_name = "user"
        side = battle.user
        other_side = battle.opponent
        side.side_conditions[constants.TOXIC_COUNT] = 0

    baton_passed_boosts = None
    switch_keep_volatiles = []
    if side.active is not None:
        # set the pkmn's types back to their original value if the types were changed
        # if the pkmn is terastallized, this does not happen
        if constants.TYPECHANGE in side.active.volatile_statuses:
            original_types = pokedex[side.active.name][constants.TYPES]
            logger.info(
                "{} had it's type changed - changing its types back to {}".format(
                    side.active.name, original_types
                )
            )
            side.active.types = original_types

        # if the target was transformed, reset its transformed attributes
        if constants.TRANSFORM in side.active.volatile_statuses:
            logger.info(
                "{} was transformed. Resetting its transformed attributes".format(
                    side.active.name
                )
            )
            side.active.stats = calculate_stats(
                side.active.base_stats, side.active.level
            )
            side.active.ability = side.active.original_ability
            side.active.moves = []
            side.active.types = pokedex[side.active.name][constants.TYPES]

        if (
            side.active.original_ability is not None
            and side.active.ability != side.active.original_ability
        ):
            logger.info(
                "{}'s ability was modified to {} - setting it back to {} on switch-out".format(
                    side.active.name, side.active.ability, side.active.original_ability
                )
            )
            side.active.ability = side.active.original_ability
            side.active.original_ability = None

        if split_msg[-1] == "[from] Baton Pass":
            side.baton_passing = False
            logger.info(
                "Baton passing, preserving boosts: {}".format(dict(side.active.boosts))
            )
            baton_passed_boosts = deepcopy(side.active.boosts)

            if constants.SUBSTITUTE in side.active.volatile_statuses:
                logger.info("Baton passing, preserving substitute")
                switch_keep_volatiles.append(constants.SUBSTITUTE)
            if constants.LEECH_SEED in side.active.volatile_statuses:
                logger.info("Baton passing, preserving leechseed")
                switch_keep_volatiles.append(constants.LEECH_SEED)
        elif split_msg[-1] == "[from] Shed Tail":
            side.shed_tailing = False

            if constants.SUBSTITUTE in side.active.volatile_statuses:
                logger.info("Shed tailing, preserving substitute")
                switch_keep_volatiles.append(constants.SUBSTITUTE)

        # gen5 rest turns are reset upon switching
        if battle.generation == "gen5" and side.active.status == constants.SLEEP:
            if side.active.rest_turns != 0:
                logger.info(
                    "{} switched while asleep and with non-zero rest turns, resetting rest turns to 3".format(
                        side.active.name
                    )
                )
                side.active.rest_turns = 3
            else:
                logger.info(
                    "{} switched while asleep, resetting sleep turns to 0".format(
                        side.active.name
                    )
                )
                side.active.sleep_turns = 0

        # gen3 rest turns are decremented by the number of consecutive sleep talks
        if battle.generation == "gen3" and side.active.status == constants.SLEEP:
            if side.active.rest_turns != 0:
                side.active.rest_turns += side.active.gen_3_consecutive_sleep_talks
                logger.info(
                    "gen3 {} switched with {} consecutive sleep talks. Incrementing rest turns by {}".format(
                        side.active.name,
                        side.active.gen_3_consecutive_sleep_talks,
                        side.active.gen_3_consecutive_sleep_talks,
                    )
                )
            elif side.active.sleep_turns != 0:
                logger.info(
                    "gen3 {} switched with {} consecutive sleep talks. Decrementing sleep turns by {}".format(
                        side.active.name,
                        side.active.gen_3_consecutive_sleep_talks,
                        side.active.gen_3_consecutive_sleep_talks,
                    )
                )
                side.active.sleep_turns -= side.active.gen_3_consecutive_sleep_talks

        side.active.gen_3_consecutive_sleep_talks = 0

        side.active.moves_used_since_switch_in.clear()

        # reset the boost of the pokemon being replaced
        side.active.boosts.clear()

        # reset the volatile statuses of the pokemon being replaced
        side.active.volatile_statuses.clear()
        side.active.volatile_status_durations.clear()

        # reset toxic count for this side
        side.side_conditions[constants.TOXIC_COUNT] = 0

        # if the side is alive and has regenerator, give it back 1/3 of it's maxhp
        if (
            side.active.hp > 0
            and not side.active.fainted
            and side.active.ability == "regenerator"
        ):
            health_healed = int(side.active.max_hp / 3)
            side.active.hp = min(side.active.hp + health_healed, side.active.max_hp)
            logger.info(
                "{} switched out with regenerator. Healing it to {}/{}".format(
                    side.active.name, side.active.hp, side.active.max_hp
                )
            )

        if side.active.name in ["cramorantgulping", "cramorantgorging"]:
            logger.info(
                "Resetting {} to 'cramorant' on switch out".format(side.active.name)
            )
            side.active.name = "cramorant"

    if side_name == "user" and user_just_switched_into_zoroark(battle, switch_or_drag):
        logger.info(
            "User switched/dragged into Zoroark - replacing the split_msg pokemon"
        )
        logger.info("Starting split_msg: {}".format(split_msg))
        request_json_zoroark = [
            p
            for p in battle.request_json[constants.SIDE][constants.POKEMON]
            if p[constants.DETAILS].startswith("Zoroark")
        ]
        assert len(request_json_zoroark) == 1
        request_json_zoroark = request_json_zoroark[0]
        split_msg[2] = f"{request_json_zoroark[constants.IDENT]}"
        split_msg[3] = f"{request_json_zoroark[constants.DETAILS]}"
        logger.info("New split_msg: {}".format(split_msg))

    # check if the pokemon exists in the reserves
    # if it does not, then the newly-created pokemon is used (for formats without team preview)
    nickname = split_msg[2]
    temp_pkmn = Pokemon.from_switch_string(split_msg[3], nickname=nickname)
    pkmn = side.find_pokemon_in_reserves(temp_pkmn.name)

    if pkmn is None:
        pkmn = Pokemon.from_switch_string(split_msg[3], nickname=nickname)

        # for standard battles gen4 and lower
        # we want to add the new pokemon to the datasets as they are revealed
        # because there is no teampreview
        if (
            battle.battle_type == BattleType.STANDARD_BATTLE
            and battle.generation in constants.NO_TEAM_PREVIEW_GENS
        ):
            SmogonSets.add_new_pokemon(pkmn.name)
            TeamDatasets.add_new_pokemon(pkmn.name)
            logger.info("Adding new pokemon '{}' to the datasets".format(pkmn.name))

        # some pokemon do not reveal their forme during team preview. Arceus, Silvally, Genesect, etc.
        # if this is the case, they would have been given a flag during team preview, and we can pull them out here
        unknown_forme_pkmn = side.find_reserve_pkmn_by_unknown_forme(temp_pkmn.name)
        if unknown_forme_pkmn:
            side.reserve.remove(unknown_forme_pkmn)
    else:
        if pkmn.name != temp_pkmn.name:
            logger.info("Renaming {} -> {}".format(pkmn.name, temp_pkmn.name))
            pkmn.name = temp_pkmn.name
        pkmn.nickname = temp_pkmn.nickname

        # Zoroark edge-case nonsense
        # if this pokemon turns out to be zoroark it may have permanent conditions change that need to be un-done after
        # finding out it is zoroark e.g. the HP value of this pokemon on switch-in is preserved so we can reset it if it
        # turns out to be zoroark
        pkmn.hp_at_switch_in = pkmn.hp
        pkmn.status_at_switch_in = pkmn.status

        side.reserve.remove(pkmn)

    split_hp_msg = split_msg[4].split("/")
    if is_opponent(battle, split_msg):
        new_hp_percentage = float(split_hp_msg[0]) / 100
        if (
            pkmn.hp != new_hp_percentage * pkmn.max_hp
            and "regenerator"
            in [
                normalize_name(a)
                for a in pokedex[pkmn.name][constants.ABILITIES].values()
            ]
            and pkmn.ability is None
        ):
            logger.info(
                "{} switched out with {}% HP but now has {}% HP, setting its ability to regenerator".format(
                    pkmn.name,
                    pkmn.hp / pkmn.max_hp * 100,
                    new_hp_percentage * 100,
                )
            )
            pkmn.ability = "regenerator"
        pkmn.hp = pkmn.max_hp * new_hp_percentage
    else:
        pkmn.hp = float(split_hp_msg[0])
        pkmn.max_hp = float(split_hp_msg[1].split()[0])

    side.last_used_move = LastUsedMove(
        pokemon_name=None, move="switch {}".format(pkmn.name), turn=battle.turn
    )

    # pkmn != active is a special edge-case for Zoroark
    if side.active is not None and pkmn != side.active:
        side.reserve.append(side.active)

    side.active = pkmn

    # zacian-crowned is technically still zacian before switching in for the first time
    # this is handled by set-prediction for the opponent, but for the bot's pkmn we
    # need to re-apply the stats that the P.S. server sends us because prior to the first
    # switch-in the stats would be for zacian, not zacian-crowned
    if side_name == "user" and pkmn.name in ["zaciancrowned", "zamazentacrowned"]:
        battle.user.re_initialize_active_pokemon_from_request_json(battle.request_json)

    for ability in ABILITIES_REVEALED_ON_SWITCH_IN:
        if battle.generation == "gen3" and ability == "pressure":
            # gen3 pressure is not revealed on switch-in
            continue

        if (
            (
                ability == "sandstream"
                and battle.weather
                in [constants.SAND, constants.HEAVY_RAIN, constants.DESOLATE_LAND]
            )
            or (
                ability == "drought"
                and battle.weather
                in [constants.SUN, constants.HEAVY_RAIN, constants.DESOLATE_LAND]
            )
            or (
                ability == "drizzle"
                and battle.weather
                in [constants.RAIN, constants.HEAVY_RAIN, constants.DESOLATE_LAND]
            )
            or (
                ability == "snowwarning"
                and battle.weather
                in [
                    constants.HAIL,
                    constants.SNOW,
                    constants.HEAVY_RAIN,
                    constants.DESOLATE_LAND,
                ]
            )
        ):
            logger.info(
                "Not adding {} to {}'s impossible abilities because the weather would not have triggered".format(
                    ability,
                    pkmn.name,
                )
            )
            continue

        if ability not in pkmn.impossible_abilities and (
            other_side.active is not None
            and other_side.active.ability != "neutralizinggas"
        ):
            logger.info(
                "{} switched in, adding {} to impossible abilities".format(
                    pkmn.name, ability
                )
            )
            pkmn.impossible_abilities.add(ability)

    for item in ITEMS_REVEALED_ON_SWITCH_IN:
        if item not in pkmn.impossible_items:
            logger.info(
                "{} switched in, adding {} to impossible items".format(pkmn.name, item)
            )
            pkmn.impossible_items.add(item)

    if baton_passed_boosts is not None:
        logger.info(
            "Applying baton passed boosts to {}: {}".format(
                side.active.name, dict(baton_passed_boosts)
            )
        )
        side.active.boosts = baton_passed_boosts
    for volatile in switch_keep_volatiles:
        logger.info("Keeping volatile on switch: {}".format(volatile))
        side.active.volatile_statuses.append(volatile)


def terastallize(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("terastallize: pkmn is None, skipping")
        return

    pkmn.terastallized = True
    pkmn.tera_type = normalize_name(split_msg[3])
    logger.info(
        "{} terastallized. Tera type: {}, Original types: {}".format(
            pkmn.name, pkmn.tera_type, pkmn.types
        )
    )


def _switch_active_with_zoroark_from_reserves(
    opponent_side: Battler, zoroark_from_reserves: Pokemon
):
    """
    This is called when we are 100% sure that the opponent's active pkmn is a zoroark
    This swaps the active pkmn with the zoroark from the reserves

    Assumptions:
        - The `zoroark_from_reserves` MUST be in `opponent_side.reserve`
    """
    pkmn = opponent_side.active

    # any moves used by this pkmn since switching in need to be removed because we cannot guarantee that they
    # belong to this pkmn
    for mv in pkmn.moves_used_since_switch_in:
        logger.info(
            "Removing {} from {}'s moves because it is {}".format(
                mv, pkmn.name, zoroark_from_reserves.name
            )
        )
        pkmn.remove_move(mv)
        if zoroark_from_reserves.get_move(mv) is None:
            zoroark_from_reserves.add_move(mv)

    # set attributes on zoroark that were on the pokemon that we thought was zoroark
    # and clear those attributes from the pokemon that we thought was zoroark
    pkmn_hp_percent = float(pkmn.hp) / pkmn.max_hp
    zoroark_from_reserves.hp = zoroark_from_reserves.max_hp * pkmn_hp_percent
    zoroark_from_reserves.boosts = copy(pkmn.boosts)
    zoroark_from_reserves.status = pkmn.status
    zoroark_from_reserves.volatile_statuses = copy(pkmn.volatile_statuses)
    zoroark_from_reserves.terastallized = pkmn.terastallized
    zoroark_from_reserves.tera_type = pkmn.tera_type
    pkmn.boosts.clear()
    pkmn.status = None
    pkmn.volatile_statuses.clear()
    pkmn.volatile_status_durations.clear()

    if pkmn.terastallized:
        pkmn.terastallized = False
        pkmn.tera_type = None

    zoroark_from_reserves.zoroark_disguised_as = pkmn.name

    # swap the pkmn places
    opponent_side.reserve.append(pkmn)
    opponent_side.active = zoroark_from_reserves
    opponent_side.reserve.remove(zoroark_from_reserves)


def illusion_end(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    if (
        is_opponent(battle, split_msg)
        and side.active.name not in ["zoroark", "zoroarkhisui"]
        and side.active.zoroark_disguised_as is None
    ):
        logger.info("Illusion ending for opponent")
        hp_percent = float(side.active.hp) / side.active.max_hp
        previous_boosts = side.active.boosts
        previous_status = side.active.status
        previous_item = side.active.item

        zoroark_from_switch_string = Pokemon.from_switch_string(split_msg[3])
        zoroark_reserve_index = None
        for index, pkmn in enumerate(side.reserve):
            if pkmn == zoroark_from_switch_string:
                zoroark_reserve_index = index
                break

        pkmn_disguised_as = side.active
        pkmn_disguised_as.item = constants.UNKNOWN_ITEM
        side.reserve.append(pkmn_disguised_as)
        if zoroark_reserve_index is not None:
            reserve_zoroark = side.reserve.pop(zoroark_reserve_index)
            side.active = reserve_zoroark
        else:
            side.active = zoroark_from_switch_string

        # the moves that have been used since this pkmn switched-in need
        # to be un-associated with the pkmn being disguised as and need to
        # be associated with the new pkmn instead
        for mv in pkmn_disguised_as.moves_used_since_switch_in:
            pkmn_disguised_as.remove_move(mv)
            if side.active.get_move(mv) is None:
                side.active.add_move(mv)

        # the pokemon that we thought was active needs some attributes reset to
        # whatever the values were at switch-in as any changes that happened to zoroark
        # since switching in have not happened to the actual pokemon
        if pkmn_disguised_as.hp_at_switch_in != pkmn_disguised_as.hp:
            logger.info(
                "Resetting {}'s HP {} to its value at switch-in: {}/{} ({}%)".format(
                    pkmn_disguised_as.name,
                    int(pkmn_disguised_as.hp),
                    pkmn_disguised_as.hp_at_switch_in,
                    pkmn_disguised_as.max_hp,
                    round(
                        100
                        * pkmn_disguised_as.hp_at_switch_in
                        / pkmn_disguised_as.max_hp,
                        1,
                    ),
                )
            )
            pkmn_disguised_as.hp = pkmn_disguised_as.hp_at_switch_in
        if pkmn_disguised_as.status_at_switch_in != pkmn_disguised_as.status:
            logger.info(
                "Resetting {}'s status {} to its value at switch-in: {}".format(
                    pkmn_disguised_as.name,
                    pkmn_disguised_as.status,
                    pkmn_disguised_as.status_at_switch_in,
                )
            )
            pkmn_disguised_as.status = pkmn_disguised_as.status_at_switch_in

        side.active.hp = hp_percent * side.active.max_hp
        side.active.boosts = previous_boosts
        side.active.status = previous_status
        side.active.item = previous_item

    side.active.zoroark_disguised_as = None


def form_change(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
        is_user = False
    else:
        side = battle.user
        is_user = True

    logger.info("Form Change: {} -> {}".format(side.active.name, split_msg[3]))
    side.active.forme_change(split_msg[3])
    if is_user:
        side.re_initialize_active_pokemon_from_request_json(battle.request_json)


def zpower(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    # Guard: side.active can be None during async transitions (faint/switchout)
    if side.active is None:
        logger.debug("zpower: side.active is None, skipping")
        return

    logger.info("{} Used a Z-Move, setting item to None".format(side.active.name))
    side.active.item = None


def mega(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    # Guard: side.active can be None during async transitions (faint/switchout)
    if side.active is None:
        logger.debug("mega: side.active is None, skipping")
        return

    side.active.is_mega = True
    forced_mega_ability = normalize_name(
        pokedex[side.active.name][constants.ABILITIES]["0"]
    )
    side.active.ability = forced_mega_ability
    logger.info(
        "Mega-Pokemon: {} with ability {}".format(side.active.name, forced_mega_ability)
    )


def transform(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
        other_side = battle.user
    else:
        side = battle.user
        other_side = battle.opponent

    transformed_into_name = other_side.active.name
    logger.info(
        "{} transformed into {}".format(side.active.name, transformed_into_name)
    )
    side.active.boosts = deepcopy(other_side.active.boosts)
    logger.info(
        "Copied {}'s boosts: {}".format(side.active.name, dict(side.active.boosts))
    )

    if constants.TRANSFORM not in side.active.volatile_statuses:
        side.active.volatile_statuses.append(constants.TRANSFORM)

    transformed_into = other_side.active
    side.active.stats = deepcopy(transformed_into.stats)
    side.active.moves = deepcopy(transformed_into.moves)
    side.active.types = deepcopy(transformed_into.types)
    side.active.boosts = deepcopy(transformed_into.boosts)

    for mv in side.active.moves:
        mv.current_pp = 5

    if split_msg[-1].startswith("[from]") and "ability:" in split_msg[-1]:
        side.active.original_ability = normalize_name(
            split_msg[-1].split("ability:")[-1].strip()
        )
    elif side.active.ability is not None:
        side.active.original_ability = side.active.ability

    side.active.ability = deepcopy(transformed_into.ability)


def turn(battle, split_msg):
    battle.turn = int(split_msg[2])
    logger.info("")
    logger.info("Turn: {}".format(battle.turn))


def noinit(battle, split_msg):
    if split_msg[2] == "rename":
        battle.battle_tag = split_msg[3]
        logger.info("Renamed battle to {}".format(battle.battle_tag))


