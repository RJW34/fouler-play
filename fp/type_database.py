"""
Authoritative Pokemon type/move database — pure data lookups, no LLM calls.

Loads from the existing data/ directory (pokedex.json, moves.json) which
contain Smogon-sourced data. Provides reliable type/move lookups for
autoresearch and decision-making.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Locate data files relative to the project root
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
_DATA_DIR = os.path.join(_PROJECT_ROOT, "data")

_POKEDEX_PATH = os.path.join(_DATA_DIR, "pokedex.json")
_MOVES_PATH = os.path.join(_DATA_DIR, "moves.json")

# Load data once at import time
_pokedex: dict = {}
_moves: dict = {}

try:
    with open(_POKEDEX_PATH, encoding="utf-8") as f:
        _pokedex = json.load(f)
except Exception as e:
    logger.error("Failed to load pokedex.json: %s", e)

try:
    with open(_MOVES_PATH, encoding="utf-8") as f:
        _moves = json.load(f)
except Exception as e:
    logger.error("Failed to load moves.json: %s", e)


def _normalize(name: str) -> str:
    """Normalize a Pokemon/move name to the key format used in the data files.

    Strips spaces, hyphens, dots, apostrophes and lowercases.
    This matches the Showdown data format where e.g. "Great Tusk" -> "greattusk".
    """
    return (
        name.replace(" ", "")
        .replace("-", "")
        .replace(".", "")
        .replace("'", "")
        .replace("%", "")
        .replace("*", "")
        .replace(":", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
        .lower()
    )


# ---- Type effectiveness chart ----
# Indices: normal=0, fire=1, water=2, electric=3, grass=4, ice=5,
#          fighting=6, poison=7, ground=8, flying=9, psychic=10,
#          bug=11, rock=12, ghost=13, dragon=14, dark=15, steel=16, fairy=17
_TYPE_INDICES = {
    "normal": 0, "fire": 1, "water": 2, "electric": 3, "grass": 4,
    "ice": 5, "fighting": 6, "poison": 7, "ground": 8, "flying": 9,
    "psychic": 10, "bug": 11, "rock": 12, "ghost": 13, "dragon": 14,
    "dark": 15, "steel": 16, "fairy": 17,
}

# fmt: off
_TYPE_CHART = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0.5, 0, 1, 1, 0.5, 1],
    [1, 0.5, 0.5, 1, 2, 2, 1, 1, 1, 1, 1, 2, 0.5, 1, 0.5, 1, 2, 1],
    [1, 2, 0.5, 1, 0.5, 1, 1, 1, 2, 1, 1, 1, 2, 1, 0.5, 1, 1, 1],
    [1, 1, 2, 0.5, 0.5, 1, 1, 1, 0, 2, 1, 1, 1, 1, 0.5, 1, 1, 1],
    [1, 0.5, 2, 1, 0.5, 1, 1, 0.5, 2, 0.5, 1, 0.5, 2, 1, 0.5, 1, 0.5, 1],
    [1, 0.5, 0.5, 1, 2, 0.5, 1, 1, 2, 2, 1, 1, 1, 1, 2, 1, 0.5, 1],
    [2, 1, 1, 1, 1, 2, 1, 0.5, 1, 0.5, 0.5, 0.5, 2, 0, 1, 2, 2, 0.5],
    [1, 1, 1, 1, 2, 1, 1, 0.5, 0.5, 1, 1, 1, 0.5, 0.5, 1, 1, 0, 2],
    [1, 2, 1, 2, 0.5, 1, 1, 2, 1, 0, 1, 0.5, 2, 1, 1, 1, 2, 1],
    [1, 1, 1, 0.5, 2, 1, 2, 1, 1, 1, 1, 2, 0.5, 1, 1, 1, 0.5, 1],
    [1, 1, 1, 1, 1, 1, 2, 2, 1, 1, 0.5, 1, 1, 1, 1, 0, 0.5, 1],
    [1, 0.5, 1, 1, 2, 1, 0.5, 0.5, 1, 0.5, 2, 1, 1, 0.5, 1, 2, 0.5, 0.5],
    [1, 2, 1, 1, 1, 2, 0.5, 1, 0.5, 2, 1, 2, 1, 1, 1, 1, 0.5, 1],
    [0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 1, 2, 1, 0.5, 1, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1, 0.5, 0],
    [1, 1, 1, 1, 1, 1, 0.5, 1, 1, 1, 2, 1, 1, 2, 1, 0.5, 1, 0.5],
    [1, 0.5, 0.5, 0.5, 1, 2, 1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 0.5, 2],
    [1, 0.5, 1, 1, 1, 1, 2, 0.5, 1, 1, 1, 1, 1, 1, 2, 2, 0.5, 1],
]
# fmt: on


def get_pokemon_types(name: str) -> Optional[list[str]]:
    """Return the type(s) of a Pokemon by name.

    Accepts any capitalization or spacing (e.g. "Great Tusk", "greattusk",
    "great-tusk" all work). Returns lowercase type names like ["ground", "fighting"].

    Returns None if the Pokemon is not found in the database.
    """
    key = _normalize(name)
    entry = _pokedex.get(key)
    if entry is None:
        return None
    types = entry.get("types")
    if types is None:
        return None
    return [t.lower() for t in types]


def get_pokemon_base_stats(name: str) -> Optional[dict]:
    """Return base stats dict for a Pokemon, or None if not found."""
    key = _normalize(name)
    entry = _pokedex.get(key)
    if entry is None:
        return None
    return entry.get("baseStats")


def get_pokemon_abilities(name: str) -> Optional[list[str]]:
    """Return list of ability names for a Pokemon, or None if not found."""
    key = _normalize(name)
    entry = _pokedex.get(key)
    if entry is None:
        return None
    abilities = entry.get("abilities", {})
    if not abilities:
        return None
    return list(abilities.values())


def get_type_effectiveness(atk_type: str, def_types: list[str]) -> float:
    """Calculate type effectiveness multiplier.

    Args:
        atk_type: The attacking move's type (e.g. "water")
        def_types: The defending Pokemon's types (e.g. ["ground", "fighting"])

    Returns:
        Effectiveness multiplier (0, 0.25, 0.5, 1, 2, or 4).
        Returns 1.0 if any type is unknown.
    """
    atk_lower = atk_type.lower()
    atk_idx = _TYPE_INDICES.get(atk_lower)
    if atk_idx is None:
        return 1.0

    multiplier = 1.0
    for def_type in def_types:
        def_lower = def_type.lower()
        def_idx = _TYPE_INDICES.get(def_lower)
        if def_idx is None:
            continue
        multiplier *= _TYPE_CHART[atk_idx][def_idx]

    return multiplier


def get_move_type(move_name: str) -> Optional[str]:
    """Return the type of a move, or None if not found."""
    key = _normalize(move_name)
    entry = _moves.get(key)
    if entry is None:
        return None
    return entry.get("type", "").lower() or None


def get_move_data(move_name: str) -> Optional[dict]:
    """Return full move data dict, or None if not found."""
    key = _normalize(move_name)
    return _moves.get(key)


def get_pokemon_moves(name: str) -> Optional[list[str]]:
    """Return known moves for a Pokemon from the movepool cache.

    This reads from fp/data/movepool_data.json which tracks moves
    the bot has observed in battles. Returns None if not found.

    Note: This is observed movepool data, not the full learnset.
    For competitive purposes, the observed moves are more relevant
    than the full learnset.
    """
    key = _normalize(name)
    movepool_path = os.path.join(_THIS_DIR, "data", "movepool_data.json")
    try:
        with open(movepool_path, encoding="utf-8") as f:
            movepool = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    entry = movepool.get(key)
    if entry is None:
        return None

    moves = []
    for category in ("physical_moves", "special_moves", "status_moves"):
        moves.extend(entry.get(category, []))
    return moves if moves else None
