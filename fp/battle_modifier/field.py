# fp/battle_modifier/field.py
# Auto-split from battle_modifier.py

from fp.battle_modifier._common import *  # noqa: F403
from fp.battle_modifier._common import _parse_time_left_seconds, _side_id_from_protocol_ident

def setboost(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    stat = constants.STAT_ABBREVIATION_LOOKUPS[split_msg[3].strip()]
    amount = int(split_msg[4].strip())

    pkmn.boosts[stat] = amount


def boost(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    if pkmn is None:
        logger.warning("boost: active pokemon is None, skipping")
        return

    stat = constants.STAT_ABBREVIATION_LOOKUPS[split_msg[3].strip()]
    amount = int(split_msg[4].strip())

    pkmn.boosts[stat] = min(pkmn.boosts[stat] + amount, constants.MAX_BOOSTS)
    logger.info(
        "{}'s {} was boosted by {} to {}".format(
            pkmn.name, stat, amount, pkmn.boosts[stat]
        )
    )


def unboost(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    if pkmn is None:
        logger.warning("unboost: active pokemon is None, skipping")
        return

    stat = constants.STAT_ABBREVIATION_LOOKUPS[split_msg[3].strip()]
    amount = int(split_msg[4].strip())

    pkmn.boosts[stat] = max(pkmn.boosts[stat] - amount, -1 * constants.MAX_BOOSTS)
    logger.info(
        "{}'s {} was unboosted by {} to {}".format(
            pkmn.name, stat, amount, pkmn.boosts[stat]
        )
    )


def weather(battle, split_msg):
    # The weather message on its own `|-weather|RainDance` does not contain information about
    #  which side caused it unless it was from an ability
    #  `|-weather|RainDance|[from] ability: Drizzle|[of] p2a: Politoed`
    #
    # If that information is present, we can infer certain things about the Side
    side = None
    side_name = None
    if len(split_msg) == 5:
        if battle.opponent.name in split_msg[-1]:
            side = battle.opponent
            side_name = "opponent"
        else:
            side = battle.user
            side_name = "user"

    weather_name = normalize_name(split_msg[2].split(":")[-1].strip())
    logger.info("Weather {} is active".format(weather_name))
    battle.weather = weather_name

    if weather_name == "none":
        logger.info("Resetting weather source to None")
        battle.weather_source = None
    elif side is not None and side_name is not None:
        battle.weather_source = f"{side_name}:{side.active.name}"

    if split_msg[-1] == "[upkeep]" and battle.weather_turns_remaining > 0:
        battle.weather_turns_remaining -= 1
    elif split_msg[-1] == "[upkeep]":
        logger.debug("Weather {} permanently active".format(weather_name))
    elif (
        len(split_msg) > 3
        and battle.generation in ["gen3", "gen4", "gen5"]
        and split_msg[3].startswith("[from] ability:")
    ):
        battle.weather_turns_remaining = -1
    elif (
        side is not None
        and weather_name == constants.SUN
        and side.active.item == "heatrock"
    ):
        logger.info("{} has heatrock, assuming 8 turns of sun".format(side.active.name))
        battle.weather_turns_remaining = 8
    elif (
        side is not None
        and weather_name == constants.RAIN
        and side.active.item == "damprock"
    ):
        logger.info(
            "{} has damprock, assuming 8 turns of rain".format(side.active.name)
        )
        battle.weather_turns_remaining = 8
    elif (
        side is not None
        and weather_name == constants.SAND
        and side.active.item == "smoothrock"
    ):
        logger.info(
            "{} has smoothrock, assuming 8 turns of sand".format(side.active.name)
        )
        battle.weather_turns_remaining = 8
    elif (
        side is not None
        and weather_name in constants.HAIL_OR_SNOW
        and side.active.item == "icyrock"
    ):
        logger.info("{} has icyrock, assuming 8 turns of hail".format(side.active.name))
        battle.weather_turns_remaining = 8
    else:
        battle.weather_turns_remaining = 5

    logger.info("Weather turns remaining: {}".format(battle.weather_turns_remaining))
    if battle.weather_turns_remaining == 0:
        logger.info(
            "Weather {} did not end when expected, giving 3 more turns".format(
                weather_name
            )
        )
        battle.weather_turns_remaining = 3
        if (
            battle.weather_source is not None
            and battle.weather_source != ""
            and battle.weather_source.startswith("opponent")
        ):
            side = battle.opponent
            pkmn_name = battle.weather_source.split(":")[-1]
            pkmn = (
                side.active
                if side.active.name == pkmn_name
                else side.find_pokemon_in_reserves(pkmn_name)
            )
            if pkmn is not None and pkmn.item == constants.UNKNOWN_ITEM:
                if weather_name == constants.SUN:
                    item = "heatrock"
                elif weather_name == constants.RAIN:
                    item = "damprock"
                elif weather_name == constants.SAND:
                    item = "smoothrock"
                elif weather_name in constants.HAIL_OR_SNOW:
                    item = "icyrock"
                else:
                    item = constants.UNKNOWN_ITEM

                logger.info(
                    "Weather not ending means that opponent's {} has a {}".format(
                        pkmn.name, item
                    )
                )
                pkmn.item = item

    if side is not None and len(split_msg) >= 5 and side.name in split_msg[4]:
        ability = normalize_name(split_msg[3].split(":")[-1].strip())
        logger.info("Setting {} ability to {}".format(side.active.name, ability))
        side.active.ability = ability


def fieldstart(battle, split_msg):
    """Set the battle's field condition"""
    field_name = normalize_name(split_msg[2].split(":")[-1].strip())

    # some field effects show up as a `-fieldstart` item but are separate from the other fields
    if field_name == constants.TRICK_ROOM:
        logger.info("Setting trickroom")
        battle.trick_room = True
        battle.trick_room_turns_remaining = 5
    elif field_name == constants.GRAVITY:
        logger.info("Setting gravity")
        battle.gravity = True
    else:
        logger.info("Setting the field to {}".format(field_name))
        battle.field = field_name
        battle.field_turns_remaining = 5


def fieldend(battle, split_msg):
    """Remove the battle's field condition"""
    field_name = normalize_name(split_msg[2].split(":")[-1].strip())

    # some field effects show up as a `-fieldend` item but are separate from the other fields
    if field_name == constants.TRICK_ROOM:
        logger.info("Removing trick room")
        battle.trick_room = False
        battle.trick_room_turns_remaining = 0
    elif field_name == constants.GRAVITY:
        logger.info("Removing gravity")
        battle.gravity = False
    else:
        logger.info("Setting the field to None")
        battle.field = None
        battle.field_turns_remaining = 0


def sidestart(battle, split_msg):
    # Inconsistencies in the protocol mean parse after the `:` to get the side condition
    # |-sidestart|p2: Name|Reflect
    # |-sidestart|p2: Name|move: Light Screen
    # |-sidestart|p2: Name|Spikes
    # |-sidestart|p1: Name|move: Stealth Rock
    #
    # Some side conditions have an explicit duration such as lightscreen, reflect, etc.
    # Others are incremented by 1

    condition = split_msg[3].split(":")[-1].strip()
    condition = normalize_name(condition)
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    if condition in SIDE_CONDITION_DEFAULT_DURATION:
        increment_amount = SIDE_CONDITION_DEFAULT_DURATION[condition]
        # Guard: side.active can be None during async transitions
        if (
            condition in ["reflect", "lightscreen", "auroraveil"]
            and side.active is not None
            and side.active.item == "lightclay"
        ):
            increment_amount += 3

        side.side_conditions[condition] = increment_amount
        # Guard: side.active can be None during async transitions
        active_name = side.active.name if side.active is not None else "None"
        logger.info(
            "Setting side condition {} to {} for {}".format(
                condition, SIDE_CONDITION_DEFAULT_DURATION[condition], active_name
            )
        )
    else:
        side.side_conditions[condition] += 1
        # Guard: side.active can be None during async transitions
        active_name = side.active.name if side.active is not None else "None"
        logger.info(
            "Incremented side condition {} to {} for {}".format(
                condition, side.side_conditions[condition], active_name
            )
        )


def sideend(battle, split_msg):
    """Remove a side effect such as stealth rock or sticky web"""
    condition = split_msg[3].split(":")[-1].strip()
    condition = normalize_name(condition)

    if is_opponent(battle, split_msg):
        logger.info("Side condition {} ending for opponent".format(condition))
        battle.opponent.side_conditions[condition] = 0
    else:
        logger.info("Side condition {} ending for user".format(condition))
        battle.user.side_conditions[condition] = 0


def swapsideconditions(battle, _):
    user_sc = battle.user.side_conditions
    opponent_sc = battle.opponent.side_conditions
    for side_condition in constants.COURT_CHANGE_SWAPS:
        user_sc[side_condition], opponent_sc[side_condition] = (
            opponent_sc[side_condition],
            user_sc[side_condition],
        )


def clearnegativeboost(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("clearnegativeboost: pkmn is None, skipping")
        return

    for stat, value in pkmn.boosts.items():
        if value < 0:
            logger.info("Setting {}'s {} boost to 0".format(pkmn.name, stat))
            pkmn.boosts[stat] = 0


def clearboost(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("clearboost: pkmn is None, skipping")
        return

    for stat, value in pkmn.boosts.items():
        logger.info("Setting {}'s {} boost to 0".format(pkmn.name, stat))
        pkmn.boosts[stat] = 0


def clearallboost(battle, _):
    pkmn = battle.user.active
    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is not None:
        for stat, value in pkmn.boosts.items():
            if value != 0:
                logger.info("Setting {}'s {} boost to 0".format(pkmn.name, stat))
                pkmn.boosts[stat] = 0

    pkmn = battle.opponent.active
    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is not None:
        for stat, value in pkmn.boosts.items():
            if value != 0:
                logger.info("Setting {}'s {} boost to 0".format(pkmn.name, stat))
                pkmn.boosts[stat] = 0


