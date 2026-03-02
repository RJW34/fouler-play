# fp/battle_modifier/dispatcher.py
# Main battle update dispatcher

from fp.battle_modifier._common import *  # noqa: F403
from fp.battle_modifier._common import _parse_time_left_seconds, _side_id_from_protocol_ident
from fp.battle_modifier.switching import *  # noqa: F403
from fp.battle_modifier.damage import *  # noqa: F403
from fp.battle_modifier.status import *  # noqa: F403
from fp.battle_modifier.field import *  # noqa: F403
from fp.battle_modifier.items import *  # noqa: F403

def update_battle(battle: Battle, msg: str):
    msg_lines = msg.split("\n")
    for line in msg_lines:
        split_msg = line.split("|")
        if len(split_msg) < 2:
            continue

        action = split_msg[1].strip()
        if action == "request":
            request(battle, split_msg)
            process_battle_updates(battle)
            return not battle.wait
        else:
            battle.msg_list.append(line)

    return False


def process_battle_updates(battle: Battle):
    msg_lines = battle.msg_list
    check_speed_ranges(battle, msg_lines)
    for i, line in enumerate(msg_lines):
        split_msg = line.split("|")
        if len(split_msg) < 2:
            continue

        action = split_msg[1].strip()

        battle_modifiers_lookup = {
            "switch": switch,
            "faint": faint,
            "-fail": fail,
            "drag": drag,
            "-heal": heal_or_damage,
            "-damage": heal_or_damage,
            "-sethp": sethp,
            "move": move,
            "-setboost": setboost,
            "-boost": boost,
            "-unboost": unboost,
            "-status": status,
            "-activate": activate,
            "-anim": anim,
            "-prepare": prepare,
            "-start": start_volatile_status,
            "-singlemove": start_volatile_status,
            "-end": end_volatile_status,
            "-curestatus": curestatus,
            "-cureteam": cureteam,
            "-weather": weather,
            "-fieldstart": fieldstart,
            "-fieldend": fieldend,
            "-sidestart": sidestart,
            "-sideend": sideend,
            "-swapsideconditions": swapsideconditions,
            "-item": set_item,
            "-enditem": remove_item,
            "-immune": immune,
            "-ability": update_ability,
            "detailschange": form_change,
            "replace": illusion_end,
            "-formechange": form_change,
            "-transform": transform,
            "-mega": mega,
            "-terastallize": terastallize,
            "-zpower": zpower,
            "-clearnegativeboost": clearnegativeboost,
            "-clearboost": clearboost,
            "-clearallboost": clearallboost,
            "-singleturn": singleturn,
            "-mustrecharge": mustrecharge,
            "upkeep": upkeep,
            "cant": cant,
            "inactive": inactive,
            "inactiveoff": inactiveoff,
            "turn": turn,
            "noinit": noinit,
        }

        function_to_call = battle_modifiers_lookup.get(action)
        if function_to_call is not None:
            function_to_call(battle, split_msg)

        if action == "move" and is_opponent(battle, split_msg):
            if normalize_name(split_msg[3].strip()) == constants.HIDDEN_POWER:
                check_opponent_hiddenpower(battle, msg_lines[i + 1])
            check_choicescarf(battle, msg_lines)
            damage_dealt = get_damage_dealt(battle, split_msg, msg_lines[i + 1 :])
            if damage_dealt:
                update_dataset_possibilities(battle, damage_dealt, "damage_dealt")

        elif action == "move" and not is_opponent(battle, split_msg):
            damage_dealt = get_damage_dealt(battle, split_msg, msg_lines[i + 1 :])
            if damage_dealt:
                update_dataset_possibilities(battle, damage_dealt, "damage_received")

            check_rocky_helmet(battle, split_msg, msg_lines[i + 1 :])

        elif action == "switch" and is_opponent(battle, split_msg):
            check_heavydutyboots(battle, msg_lines[i + 1 :])

    check_choicescarf_from_ability_order(battle, msg_lines)
    battle.msg_list.clear()


async def async_update_battle(battle, msg):
    return update_battle(battle, msg)
