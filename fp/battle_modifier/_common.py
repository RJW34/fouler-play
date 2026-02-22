# fp/battle_modifier/_common.py
# Shared imports, constants, and utility functions

import re
import json
from copy import deepcopy, copy
import logging

import constants
from constants import BattleType
from data import all_move_json
from data import pokedex
from data.pkmn_sets import (
    SmogonSets,
    RandomBattleTeamDatasets,
    TeamDatasets,
    PredictedPokemonSet,
)
from fp.battle import Pokemon, Battler, Battle
from fp.battle import LastUsedMove
from fp.battle import DamageDealt
from fp.battle import StatRange
from fp.search.poke_engine_helpers import poke_engine_get_damage_rolls
from fp.helpers import normalize_name, type_effectiveness_modifier
from fp.helpers import get_pokemon_info_from_condition
from fp.helpers import calculate_stats
from fp.helpers import (
    is_not_very_effective,
    is_super_effective,
    is_neutral_effectiveness,
)
from fp.battle import boost_multiplier_lookup
from fp.movepool_tracker import record_move


logger = logging.getLogger(__name__)

MOVE_END_STRINGS = {"move", "switch", "upkeep", "-miss", ""}
ITEMS_REVEALED_ON_SWITCH_IN = [
    # boosterenergy technically only revealed if pkmn has quarkdrive/protosynthesis
    # but if they don't have that it doesn't matter
    "boosterenergy",
    "airballoon",
]
ABILITIES_REVEALED_ON_SWITCH_IN = [
    "intimidate",
    "pressure",
    "neutralizinggas",
    "sandstream",
    "drought",
    "drizzle",
    "snowwarning",
]

# Conservative set for speed-order based item inference.
# These abilities have clearly visible switch-in activations and are safe for
# relative activation-order checks.
ABILITIES_SAFE_FOR_SPEED_ORDER_INFERENCE = {
    "intimidate",
    "pressure",
    "download",
    "trace",
    "frisk",
}

SIDE_CONDITION_DEFAULT_DURATION = {
    constants.REFLECT: 5,
    constants.LIGHT_SCREEN: 5,
    constants.AURORA_VEIL: 5,
    constants.SAFEGUARD: 5,
    constants.MIST: 5,
    constants.TAILWIND: 4,
}


def crit_rate_for_generation(generation):
    if generation == "gen1":
        return 205 / 105
    elif generation in [
        "gen2",
        "gen3",
        "gen4",
        "gen5",
    ]:
        return 2.0
    else:
        return 1.5


def can_have_priority_modified(battle, pokemon, move_name):
    return (
        "prankster"
        in [
            normalize_name(a)
            for a in pokedex[pokemon.name][constants.ABILITIES].values()
        ]
        or (move_name == "grassyglide" and battle.field == constants.GRASSY_TERRAIN)
        or (
            move_name in all_move_json
            and all_move_json[move_name][constants.CATEGORY] == constants.STATUS
            and "myceliummight"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
    )


def can_have_speed_modified(battle, pokemon):
    return (
        (
            pokemon.item is None
            and "unburden"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
        or (
            battle.weather == constants.RAIN
            and pokemon.ability is None
            and "swiftswim"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
        or (
            battle.weather == constants.SUN
            and pokemon.ability is None
            and "chlorophyll"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
        or (
            battle.weather == constants.SAND
            and pokemon.ability is None
            and "sandrush"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
        or (
            battle.weather in constants.HAIL_OR_SNOW
            and pokemon.ability is None
            and "slushrush"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
        or (
            battle.field == constants.ELECTRIC_TERRAIN
            and pokemon.ability is None
            and "surgesurfer"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
        or (
            pokemon.status == constants.PARALYZED
            and pokemon.ability is None
            and "quickfeet"
            in [
                normalize_name(a)
                for a in pokedex[pokemon.name][constants.ABILITIES].values()
            ]
        )
    )


def remove_volatile(pkmn, volatile):
    pkmn.volatile_statuses = [vs for vs in pkmn.volatile_statuses if vs != volatile]


def unlikely_to_have_choice_item(move_name):
    try:
        move_dict = all_move_json[move_name]
    except KeyError:
        return False

    if (
        constants.BOOSTS in move_dict
        and move_dict[constants.CATEGORY] == constants.STATUS
    ):
        return True
    elif move_name in ["substitute", "roost", "recover"]:
        return True

    return False


def is_opponent(battle, split_msg):
    return not split_msg[2].startswith(battle.user.name)


def get_move_information(m):
    # Given a |move| line from the PS protocol, extract the user of the move and the move object
    try:
        split_move_line = m.split("|")
        return split_move_line[2], all_move_json[normalize_name(split_move_line[3])]
    except KeyError:
        logger.warning(
            "Unknown move {} - using standard 0 priority move".format(
                normalize_name(m.split("|")[3])
            )
        )
        return m.split("|")[2], {constants.ID: "unknown", constants.PRIORITY: 0}


def _parse_time_left_seconds(text: str) -> int | None:
    if not text:
        return None

    lower = text.lower()
    if "opponent" in lower:
        return None

    # Try mm:ss format first (e.g., "1:30")
    match = re.search(r"(\d+)\s*:\s*(\d+)", text)
    if match:
        try:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            return minutes * 60 + seconds
        except ValueError:
            pass

    # Try "X min Y sec" formats
    minutes = 0
    seconds = 0
    match_min = re.search(r"(\d+)\s*min", lower)
    match_sec = re.search(r"(\d+)\s*sec", lower)
    try:
        if match_min:
            minutes = int(match_min.group(1))
        if match_sec:
            seconds = int(match_sec.group(1))
        if match_min or match_sec:
            return minutes * 60 + seconds
    except ValueError:
        pass

    # Try plain seconds (e.g., "120 sec")
    match = re.search(r"(\d+)\s*sec", lower)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None

    # Last resort: any integer in the string
    match = re.search(r"(\d+)", lower)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None

    return None


def _side_id_from_protocol_ident(ident: str):
    if not ident:
        return None
    # Protocol examples: "p1a: Kyurem", "p2a: Landorus-Therian"
    side_token = ident.split(":")[0].strip()
    if len(side_token) < 2:
        return None
    side_id = side_token[:2]
    if side_id in {"p1", "p2"}:
        return side_id
    return None


