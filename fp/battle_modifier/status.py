# fp/battle_modifier/status.py
# Auto-split from battle_modifier.py

from fp.battle_modifier._common import *  # noqa: F403
from fp.battle_modifier._common import _parse_time_left_seconds, _side_id_from_protocol_ident

def fail(battle, split_msg):
    # |-fail|p2a: Dragapult|unboost|[from] ability: Clear Body|[of] p2a: Dragapult
    if (
        len(split_msg) > 5
        and split_msg[4].startswith("[from] ability: ")
        and split_msg[5].startswith("[of]")
    ):
        ability_side = (
            battle.user
            if split_msg[5].startswith(f"[of] {battle.user.name}")
            else battle.opponent
        )
        ability = normalize_name(split_msg[4].split("ability: ")[-1])
        # Guard: ability_side.active can be None during async transitions
        if ability_side.active is not None:
            logger.info(
                "Setting {}'s ability to: {}".format(ability_side.active.name, ability)
            )
            ability_side.active.ability = ability


def status(battle, split_msg):
    if is_opponent(battle, split_msg):
        other_side = battle.user
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active
        other_side = battle.opponent

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("status: pkmn is None, skipping")
        return

    if len(split_msg) > 4 and "item: " in split_msg[4]:
        pkmn.item = normalize_name(split_msg[4].split("item:")[-1])

    if len(split_msg) == 5 and split_msg[3] == "slp":
        if split_msg[4] == "[from] move: Rest":
            logger.info("Setting rest_turns to 3 for {}".format(pkmn.name))
            pkmn.rest_turns = 3
        else:
            logger.info("Setting sleep_turns to 0 for {}".format(pkmn.name))
            pkmn.sleep_turns = 0

    status_name = split_msg[3].strip()
    logger.info("{} got status: {}".format(pkmn.name, status_name))
    pkmn.status = status_name

    if status_name is not None:
        logger.info(
            "No longer guessing lumberry because {} got status {}".format(
                pkmn.name, status_name
            )
        )
        pkmn.impossible_items.add("lumberry")

    # ["", "-status", "p1a: Caterpie", "brn", "[from] ability: Flame Body", "[of] p2a: Caterpie"]
    if (
        len(split_msg) > 5
        and split_msg[4].startswith("[from] ability: ")
        and split_msg[5].startswith("[of]")
        and split_msg[5].startswith(f"[of] {other_side.name}")
    ):
        ability = normalize_name(split_msg[4].split("ability: ")[-1])
        # Guard: other_side.active can be None during async transitions
        if other_side.active is not None:
            logger.info("Setting {}'s ability to: {}".format(other_side.active.name, ability))
            other_side.active.ability = ability


def activate(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
        other_pkmn = battle.user.active
    else:
        pkmn = battle.user.active
        other_pkmn = battle.opponent.active

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("activate: pkmn is None, skipping")
        return

    if (
        normalize_name(split_msg[3]) == constants.SUBSTITUTE
        and split_msg[4] == "[damage]"
    ):
        logger.info(
            "{}'s substitute took damage, setting substitute_hit to True".format(
                pkmn.name
            )
        )
        pkmn.substitute_hit = True

    if split_msg[3].lower() == "move: poltergeist":
        item = normalize_name(split_msg[4])
        logger.info("{} has the item {}".format(pkmn.name, item))
        pkmn.item = item

    if split_msg[3].lower().startswith("ability: "):
        ability = normalize_name(split_msg[3].split(":")[-1].strip())
        logger.info("Setting {}'s ability to {}".format(pkmn.name, ability))
        pkmn.ability = ability

        if ability in ["mummy", "lingeringaroma"]:
            original_ability = normalize_name(split_msg[4])
            other_pkmn.ability = ability
            other_pkmn.original_ability = original_ability
            logger.info(
                "{}'s ability was changed from {} to {}".format(
                    other_pkmn.name, original_ability, ability
                )
            )

    elif split_msg[3].lower().startswith("item: ") and not any(
        i == "[consumed]" for i in split_msg
    ):
        item = normalize_name(split_msg[3].split(":")[-1].strip())
        logger.info("Setting {}'s item to {}".format(pkmn.name, item))
        pkmn.item = item

    if split_msg[3].lower().startswith("move: "):
        move_name = normalize_name(split_msg[3].split(":")[-1].strip())
        if (
            move_name in all_move_json
            and all_move_json[move_name].get("volatileStatus")
            == constants.PARTIALLY_TRAPPED
        ):
            logger.info("{} was partially trapped by {}".format(pkmn.name, move_name))
            pkmn.volatile_statuses.append(constants.PARTIALLY_TRAPPED)


def anim(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("anim: pkmn is None, skipping")
        return

    anim_name = normalize_name(split_msg[3].strip())
    if anim_name in pkmn.volatile_statuses:
        logger.info(
            "Removing volatile status {} from {} because of -anim".format(
                anim_name, pkmn.name
            )
        )
        remove_volatile(pkmn, anim_name)


def prepare(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("prepare: pkmn is None, skipping")
        return

    being_prepared = normalize_name(split_msg[3])
    if being_prepared in pkmn.volatile_statuses:
        logger.warning(
            "{} already has the volatile status {}".format(pkmn.name, being_prepared)
        )
    else:
        logger.info(
            "Adding the volatile status {} to {}".format(being_prepared, pkmn.name)
        )
        pkmn.volatile_statuses.append(being_prepared)


def start_volatile_status(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
        side = battle.opponent
    else:
        pkmn = battle.user.active
        side = battle.user

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("start_volatile_status: pkmn is None, skipping")
        return

    volatile_status = normalize_name(split_msg[3].split(":")[-1])

    # for some reason futuresight is sent with the `-start` message
    # `-start` is typically reserved for volatile statuses
    if volatile_status == constants.FUTURE_SIGHT:
        side.future_sight = (3, pkmn.name)
        return

    if volatile_status.startswith("perish"):
        logger.info(
            "{} got {}. Removing other `perish` volatiles".format(
                pkmn.name, volatile_status
            )
        )
        logger.info("Starting volatiles: {}".format(pkmn.volatile_statuses))
        pkmn.volatile_statuses = [
            vs for vs in pkmn.volatile_statuses if not vs.startswith("perish")
        ]
        pkmn.volatile_statuses.append(volatile_status)
        logger.info("Ending volatiles: {}".format(pkmn.volatile_statuses))
        return

    if volatile_status not in pkmn.volatile_statuses:
        logger.info(
            "Starting the volatile status {} on {}".format(volatile_status, pkmn.name)
        )
        pkmn.volatile_statuses.append(volatile_status)

    if volatile_status == constants.SUBSTITUTE:
        if len(split_msg) >= 5 and split_msg[4] == "[from] move: Shed Tail":
            logger.info(
                "{} started a substitute from shed tail - setting shed_tailing to True".format(
                    pkmn.name
                )
            )
            side.shed_tailing = True
        logger.info(
            "{} started a substitute - setting substitute_hit to False".format(
                pkmn.name
            )
        )
        pkmn.substitute_hit = False

    if volatile_status == constants.SLOW_START:
        logger.info("{} started slow start - setting slow_start to 6".format(pkmn.name))
        pkmn.volatile_status_durations[constants.SLOW_START] = 6

    if volatile_status == constants.CONFUSION:
        logger.info("{} got confused, no longer guessing lumberry".format(pkmn.name))
        pkmn.impossible_items.add("lumberry")
        if split_msg[-1] == "[fatigue]":
            logger.info(
                "{} got confused from fatigue, removing lockedmove from volatile statuses".format(
                    pkmn.name
                )
            )
            remove_volatile(pkmn, constants.LOCKED_MOVE)
            side.active.volatile_status_durations[constants.LOCKED_MOVE] = 0

    if volatile_status == constants.DYNAMAX:
        pkmn.hp *= 2
        pkmn.max_hp *= 2
        logger.info(
            "{} started dynamax - doubling their HP to {}/{}".format(
                pkmn.name, pkmn.hp, pkmn.max_hp
            )
        )

    if constants.ABILITY in split_msg[3]:
        pkmn.ability = volatile_status

    if len(split_msg) == 6 and constants.ABILITY in normalize_name(split_msg[5]):
        pkmn.ability = normalize_name(split_msg[5].split("ability:")[-1])

    if volatile_status == constants.TYPECHANGE:
        if split_msg[4] == "[from] move: Reflect Type":
            pkmn_name = normalize_name(split_msg[5].split(":")[-1])
            new_types = deepcopy(pokedex[pkmn_name][constants.TYPES])
        else:
            new_types = [normalize_name(t) for t in split_msg[4].split("/")]

        logger.info("Setting {}'s types to {}".format(pkmn.name, new_types))
        pkmn.types = new_types


def end_volatile_status(battle, split_msg):
    if is_opponent(battle, split_msg):
        pkmn = battle.opponent.active
    else:
        pkmn = battle.user.active

    # Guard: pkmn can be None during async transitions (faint/switchout)
    if pkmn is None:
        logger.debug("end_volatile_status: pkmn is None, skipping")
        return

    volatile_status = normalize_name(split_msg[3].split(":")[-1])
    if volatile_status == constants.SUBSTITUTE:
        logger.info("Substitute ended for {}".format(pkmn.name))
        pkmn.substitute_hit = False

    if volatile_status == "protosynthesis" or volatile_status == "quarkdrive":
        for vs in pkmn.volatile_statuses:
            if vs.startswith(volatile_status):
                logger.info("Removing {} from {}".format(vs, pkmn.name))
                pkmn.volatile_statuses.remove(vs)
    elif len(split_msg) >= 5 and constants.PARTIALLY_TRAPPED in split_msg[4]:
        remove_volatile(pkmn, constants.PARTIALLY_TRAPPED)
    elif volatile_status not in pkmn.volatile_statuses:
        logger.warning(
            "{} does not have the volatile status '{}'. Volatiles: {}".format(
                pkmn, volatile_status, pkmn.volatile_statuses
            )
        )
    else:
        logger.info(
            "Removing the volatile status {} from {}".format(volatile_status, pkmn.name)
        )
        remove_volatile(pkmn, volatile_status)
        if volatile_status in pkmn.volatile_status_durations:
            pkmn.volatile_status_durations[volatile_status] = 0
            logger.info(
                "Setting {}'s {} duration to 0".format(pkmn.name, volatile_status)
            )
        if volatile_status == constants.DYNAMAX:
            pkmn.hp /= 2
            pkmn.max_hp /= 2
            logger.info(
                "{} ended dynamax - halving their HP to {}/{}".format(
                    pkmn.name, pkmn.hp, pkmn.max_hp
                )
            )


def curestatus(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    pkmn_name = split_msg[2].split(":")[-1].strip()

    if normalize_name(pkmn_name) == side.active.name:
        pkmn = side.active
    else:
        try:
            pkmn = next(
                filter(lambda x: x.name == normalize_name(pkmn_name), side.reserve)
            )
        except StopIteration:
            logger.warning(
                "The pokemon {} does not exist in the party, defaulting to the active pokemon".format(
                    normalize_name(pkmn_name)
                )
            )
            pkmn = side.active

    # even if rest wasn't the cause of sleep, this should be set to 0
    if pkmn.status == constants.SLEEP:
        logger.info(
            "{} is being cured of sleep, setting rest_turns & sleep_turns to 0".format(
                pkmn.name
            )
        )
        pkmn.rest_turns = 0
        pkmn.sleep_turns = 0
    elif pkmn.status == constants.TOXIC:
        side.side_conditions[constants.TOXIC_COUNT] = 0

    pkmn.status = None


def cureteam(battle, split_msg):
    """Cure every pokemon on the opponent's team of it's status"""
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    side.active.status = None
    for pkmn in filter(lambda p: isinstance(p, Pokemon), side.reserve):
        pkmn.status = None
        pkmn.rest_turns = 0
        pkmn.sleep_turns = 0


def singleturn(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
    else:
        side = battle.user

    move_name = normalize_name(split_msg[3].split(":")[-1])
    if move_name in constants.PROTECT_VOLATILE_STATUSES:
        # increment by 2 because the `upkeep` function will decrement by 1 on every end-of-turn
        side.side_conditions[constants.PROTECT] += 2
        logger.info(
            "{} used a protect move, set protect side condition to {}".format(
                side.active.name, side.side_conditions[constants.PROTECT]
            )
        )

    # |-singleturn|p1a: Skarmory|move: Roost
    elif move_name == constants.ROOST:
        # set to 2 because the `upkeep` function will decrement by 1 on every end-of-turn
        side.active.volatile_statuses.append(constants.ROOST)
        logger.info(
            "{} has acquired the 'roost' volatilestatus".format(side.active.name)
        )


def mustrecharge(battle, split_msg):
    # Bot's side does not get mustrecharge because the request JSON
    # will contain the only available `recharge` move
    if is_opponent(battle, split_msg):
        side = battle.opponent
        logger.info("{} must recharge".format(side.active.name))
        side.active.volatile_statuses.append("mustrecharge")
    else:
        side = battle.user

    # Truant and mustrecharge together means that you only recharge next turn
    if "truant" in side.active.volatile_statuses:
        logger.info(
            "{} must recharge with truant, removing truant".format(side.active.name)
        )
        remove_volatile(side.active, "truant")


def cant(battle, split_msg):
    if is_opponent(battle, split_msg):
        side = battle.opponent
        other_side = battle.user
        opponent = True
    else:
        side = battle.user
        other_side = battle.opponent
        opponent = False

    # Guard: Pokemon not active yet (race condition during team preview/switch)
    if side.active is None:
        logger.warning(
            f"Received 'cant' message but pokemon not active yet for {split_msg}. "
            f"Ignoring (likely team preview or delayed switch message)."
        )
        return

    side.last_used_move = LastUsedMove(
        pokemon_name=side.active.name,
        move=side.last_used_move.move,
        turn=battle.turn,
    )

    # |cant|p1a: Slaking|ability: Truant
    if len(split_msg) == 4 and split_msg[3] == "ability: Truant":
        logger.info(
            "{} got 'cant' from truant, removing truant volatile".format(
                side.active.name
            )
        )
        remove_volatile(side.active, "truant")

    # |cant|p2a: Tauros|recharge
    if len(split_msg) == 4 and split_msg[3] == "recharge":
        logger.info(
            "{} got 'cant' from recharge, removing mustrecharge volatile".format(
                side.active.name
            )
        )
        if opponent and "mustrecharge" not in side.active.volatile_statuses:
            logger.warning(
                "{} did not have mustrecharge but recharged".format(side.active.name)
            )

        remove_volatile(side.active, "mustrecharge")

    # |cant|p2a: Politoed|move: Taunt|Toxic
    if len(split_msg) == 4 and split_msg[3].startswith("move: "):
        move_name = normalize_name(split_msg[3].split(":")[-1])
        move_object = side.active.get_move(move_name)
        if move_object is None:
            side.active.add_move(move_name)
            logger.info(
                "Adding {} to {}'s moves from 'cant'".format(
                    move_name, side.active.name
                )
            )

    if len(split_msg) == 4 and split_msg[3] == constants.SLEEP:
        logger.info("{} got 'cant' from sleep".format(side.active.name))
        if side.active.rest_turns > 1:
            side.active.rest_turns -= 1
            logger.info(
                "Decrementing {}'s rest_turns to {}".format(
                    side.active.name, side.active.rest_turns
                )
            )
        elif side.active.rest_turns == 1:
            logger.critical(
                "{} has rest_turns==1 and got 'cant' from sleep".format(
                    side.active.name
                )
            )
            exit(1)
        else:
            side.active.sleep_turns += 1
            logger.info(
                "Incrementing {}'s sleep_turns to {}".format(
                    side.active.name, side.active.sleep_turns
                )
            )

    # gen1 if you get `cant` from full paralysis while the opponent is partiallytrapped, they are freed
    if (
        battle.generation == "gen1"
        and len(split_msg) == 4
        and split_msg[3] == constants.PARALYZED
        and other_side.active is not None
        and (
            constants.PARTIALLY_TRAPPED in other_side.active.volatile_statuses
            or other_side.active.volatile_status_durations[constants.PARTIALLY_TRAPPED]
            > 0
        )
    ):
        logger.info(
            f"{side.active.name} got 'cant' while target {other_side.active.name} was partially trapped, "
            f"removing partiallytrapped volatile from {other_side.active.name}"
        )
        remove_volatile(other_side.active, constants.PARTIALLY_TRAPPED)
        other_side.active.volatile_status_durations[constants.PARTIALLY_TRAPPED] = 0


def upkeep(battle, _):
    if battle.trick_room:
        battle.trick_room_turns_remaining -= 1
        logger.info(
            "Trick Room turns remaining: {}".format(battle.trick_room_turns_remaining)
        )

    if battle.field is not None and battle.field_turns_remaining > 0:
        battle.field_turns_remaining -= 1
        logger.info(
            "{} turns remaining: {}".format(battle.field, battle.field_turns_remaining)
        )

    if battle.field is not None and battle.field_turns_remaining == 0:
        logger.info(
            "{} did not end when expected, giving 3 more turns".format(battle.field)
        )
        battle.field_turns_remaining = 3

    if battle.user.active is not None and constants.ROOST in battle.user.active.volatile_statuses:
        logger.info(
            "Removing 'roost' from {}'s volatiles".format(battle.user.active.name)
        )
        battle.user.active.volatile_statuses = [
            v for v in battle.user.active.volatile_statuses if v != constants.ROOST
        ]

    if battle.opponent.active is not None and constants.ROOST in battle.opponent.active.volatile_statuses:
        logger.info(
            "Removing 'roost' from {}'s volatiles".format(battle.opponent.active.name)
        )
        battle.opponent.active.volatile_statuses = [
            v for v in battle.opponent.active.volatile_statuses if v != constants.ROOST
        ]

    for side in [battle.user, battle.opponent]:
        side_string = "opponent" if side == battle.opponent else "user"

        # Guard: side.active can be None during async transitions (faint/switchout)
        if side.active is None:
            continue

        if (
            "taunt" in side.active.volatile_statuses
            and battle.generation in constants.TAUNT_DURATION_INCREMENT_END_OF_TURN
        ):
            side.active.volatile_status_durations[constants.TAUNT] += 1
            logger.info(
                "Incrementing taunt duration for {} to {}".format(
                    side_string,
                    side.active.volatile_status_durations[constants.TAUNT],
                )
            )

        if constants.LOCKED_MOVE in side.active.volatile_statuses:
            side.active.volatile_status_durations[constants.LOCKED_MOVE] += 1
            logger.info(
                "Incremented lockedmove for {} to {}".format(
                    side_string,
                    side.active.volatile_status_durations[constants.LOCKED_MOVE],
                )
            )

        if side.side_conditions[constants.REFLECT] > 0:
            side.side_conditions[constants.REFLECT] -= 1
            logger.info(
                "Decrementing reflect for {} to {}".format(
                    side_string, side.side_conditions[constants.REFLECT]
                )
            )
            if side.side_conditions[constants.REFLECT] == 0:
                logger.info(
                    "reflect did not end for {} when expected, giving it 3 more turns".format(
                        side_string
                    )
                )
                side.side_conditions[constants.REFLECT] = 3

        if side.side_conditions[constants.LIGHT_SCREEN] > 0:
            side.side_conditions[constants.LIGHT_SCREEN] -= 1
            logger.info(
                "Decrementing lightscreen for {} to {}".format(
                    side_string, side.side_conditions[constants.LIGHT_SCREEN]
                )
            )
            if side.side_conditions[constants.LIGHT_SCREEN] == 0:
                logger.info(
                    "lightscreen did not end for {} when expected, giving it 3 more turns".format(
                        side_string
                    )
                )
                side.side_conditions[constants.LIGHT_SCREEN] = 3

        if side.side_conditions[constants.AURORA_VEIL] > 0:
            side.side_conditions[constants.AURORA_VEIL] -= 1
            logger.info(
                "Decrementing auroraveil for {} to {}".format(
                    side_string, side.side_conditions[constants.AURORA_VEIL]
                )
            )
            if side.side_conditions[constants.AURORA_VEIL] == 0:
                logger.info(
                    "auroraveil did not end for {} when expected, giving it 3 more turns".format(
                        side_string
                    )
                )
                side.side_conditions[constants.AURORA_VEIL] = 3

        if side.side_conditions[constants.TAILWIND] > 0:
            side.side_conditions[constants.TAILWIND] -= 1
            logger.info(
                "Decrementing tailwind for {} to {}".format(
                    side_string, side.side_conditions[constants.TAILWIND]
                )
            )

        if side.side_conditions[constants.MIST] > 0:
            side.side_conditions[constants.MIST] -= 1
            logger.info(
                "Decrementing mist for {} to {}".format(
                    side_string, side.side_conditions[constants.MIST]
                )
            )

        if side.side_conditions[constants.SAFEGUARD] > 0:
            side.side_conditions[constants.SAFEGUARD] -= 1
            logger.info(
                "Decrementing safeguard for {} to {}".format(
                    side_string, side.side_conditions[constants.SAFEGUARD]
                )
            )

        pkmn = side.active
        if constants.YAWN in pkmn.volatile_statuses:
            previous_duration = pkmn.volatile_status_durations[constants.YAWN]
            if previous_duration == 0:
                pkmn.volatile_status_durations[constants.YAWN] = 1
            elif previous_duration == 1:
                pkmn.volatile_status_durations[constants.YAWN] = 0
                remove_volatile(pkmn, constants.YAWN)
                logger.info("Removed yawn volatile from {}".format(pkmn.name))
            else:
                raise ValueError(
                    "Got yawn duration {} for {}".format(previous_duration, pkmn.name)
                )
            logger.info(
                "{} had yawn at the end of the turn, changed duration from {} to {}".format(
                    pkmn.name,
                    previous_duration,
                    pkmn.volatile_status_durations[constants.YAWN],
                )
            )
        if constants.SLOW_START in pkmn.volatile_statuses:
            pkmn.volatile_status_durations[constants.SLOW_START] -= 1
            logger.info(
                "Decremented slow start duration for {} to {}".format(
                    pkmn.name, pkmn.volatile_status_durations[constants.SLOW_START]
                )
            )

        if (
            battle.generation == "gen3"
            and pkmn.status == constants.SLEEP
            and side.last_used_move.move != "sleeptalk"
        ):
            pkmn.gen_3_consecutive_sleep_talks = 0
            logger.info(
                "{} is asleep but didn't use sleeptalk, decrementing gen_3_consecutive_sleep_talks to 0".format(
                    pkmn.name
                )
            )

    if battle.user.side_conditions[constants.PROTECT] > 0:
        battle.user.side_conditions[constants.PROTECT] -= 1
        logger.info(
            "Setting protect to {} for the bot".format(
                battle.user.side_conditions[constants.PROTECT]
            )
        )

    if battle.opponent.side_conditions[constants.PROTECT] > 0:
        battle.opponent.side_conditions[constants.PROTECT] -= 1
        logger.info(
            "Setting protect to {} for the opponent".format(
                battle.opponent.side_conditions[constants.PROTECT]
            )
        )

    if battle.user.wish[0] > 0:
        battle.user.wish = (battle.user.wish[0] - 1, battle.user.wish[1])
        logger.info("Decrementing wish to {} for the bot".format(battle.user.wish[0]))

    if battle.opponent.wish[0] > 0:
        battle.opponent.wish = (battle.opponent.wish[0] - 1, battle.opponent.wish[1])
        logger.info(
            "Decrementing wish to {} for the opponent".format(battle.opponent.wish[0])
        )

    if battle.user.future_sight[0] > 0:
        battle.user.future_sight = (
            battle.user.future_sight[0] - 1,
            battle.user.future_sight[1],
        )
        logger.info(
            "Decrementing future_sight to {} for the bot".format(
                battle.user.future_sight[0]
            )
        )

    if battle.opponent.future_sight[0] > 0:
        battle.opponent.future_sight = (
            battle.opponent.future_sight[0] - 1,
            battle.opponent.future_sight[1],
        )
        logger.info(
            "Decrementing future_sight to {} for the opponent".format(
                battle.opponent.future_sight[0]
            )
        )

    # If a pkmn has less than maxhp during upkeep,
    # we do not want to guess leftovers/blacksludge anymore when it is time to guess an item
    # leftovers and blacksludge will reveal themselves at the end of the turn if they exist
    opp_pkmn = battle.opponent.active
    # Guard: opp_pkmn can be None during async transitions (faint/switchout)
    if opp_pkmn is not None and opp_pkmn.hp < opp_pkmn.max_hp:
        logger.info(
            "{} has less than maxhp during upkeep, no longer guessing leftovers or blacksludge".format(
                opp_pkmn.name
            )
        )
        opp_pkmn.impossible_items.add(constants.LEFTOVERS)
        opp_pkmn.impossible_items.add(constants.BLACK_SLUDGE)

    # Guard: opp_pkmn can be None during async transitions (faint/switchout)
    if opp_pkmn is not None and opp_pkmn.status is None:
        opp_pkmn.impossible_items.add("flameorb")
        opp_pkmn.impossible_items.add("toxicorb")


