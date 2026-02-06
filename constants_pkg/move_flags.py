# constants_pkg/move_flags.py
# Dynamic move set extraction from moves.json at import time
# This provides accurate move categorization based on actual game data

import json
from pathlib import Path

# Load moves.json once at module import
_MOVES_JSON_PATH = Path(__file__).parent.parent / "data" / "moves.json"

def _load_moves_data() -> dict:
    """Load and return the moves data from moves.json."""
    try:
        with open(_MOVES_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Fallback for testing or unusual import paths
        return {}

_MOVES_DATA = _load_moves_data()


def _get_moves_by_flag(flag_name: str) -> set[str]:
    """Extract all move IDs that have a specific flag set to 1."""
    return {
        move_id
        for move_id, move_data in _MOVES_DATA.items()
        if move_data.get("flags", {}).get(flag_name) == 1
    }


def _get_moves_by_type(type_name: str) -> set[str]:
    """Extract all move IDs of a specific type."""
    return {
        move_id
        for move_id, move_data in _MOVES_DATA.items()
        if move_data.get("type") == type_name
    }


def _get_status_moves() -> set[str]:
    """Extract all status category moves."""
    return {
        move_id
        for move_id, move_data in _MOVES_DATA.items()
        if move_data.get("category") == "status"
    }


# =============================================================================
# MOVE SETS BY FLAG (extracted from moves.json flags field)
# =============================================================================

# Sound-based moves (blocked by Soundproof ability)
# These are moves with the "sound" flag that bypass Substitute
SOUND_MOVES = _get_moves_by_flag("sound")

# Powder/Spore moves (blocked by Overcoat ability and Grass types)
POWDER_MOVES = _get_moves_by_flag("powder")

# Bullet/Ball moves (blocked by Bulletproof ability)
BULLET_MOVES = _get_moves_by_flag("bullet")

# Wind moves (absorbed by Wind Rider for +1 Atk)
WIND_MOVES = _get_moves_by_flag("wind")

# Contact moves (trigger Rocky Helmet / Rough Skin / Iron Barbs, etc.)
CONTACT_MOVES = _get_moves_by_flag("contact")


# =============================================================================
# MOVE SETS BY TYPE
# =============================================================================

# Grass-type moves (blocked by Sap Sipper, heals them)
GRASS_TYPE_MOVES = _get_moves_by_type("grass")

# Dark-type moves (trigger Justified for +1 Atk)
DARK_TYPE_MOVES = _get_moves_by_type("dark")

# For reference - these are also in strategy.py but may be useful here
# FIRE_TYPE_MOVES = _get_moves_by_type("fire")
# WATER_TYPE_MOVES = _get_moves_by_type("water")


# =============================================================================
# SPECIAL MOVE CATEGORIES
# =============================================================================

# Moves that drop OUR stats when used (Contrary turns these into boosts)
# Format: {move_id: stat_that_drops}
# These moves normally hurt the user but are boosted by Contrary
SELF_STAT_DROP_MOVES = {
    # Moves that drop SpA by 2 when used
    "overheat",
    "dracometeor",
    "leafstorm",
    "fleurcannon",
    "psychoboost",
    # Moves that drop Atk/Def when used
    "superpower",  # -1 Atk, -1 Def
    "closecombat",  # -1 Def, -1 SpD
    "hammerarm",    # -1 Spe
    "icehammer",    # -1 Spe
    # V-create family (massive stat drops)
    "vcreate",      # -1 Def, -1 SpD, -1 Spe
}

# All status-category moves (useful for Prankster vs Dark interaction)
ALL_STATUS_MOVES = _get_status_moves()


# =============================================================================
# VERIFICATION
# =============================================================================

if __name__ == "__main__":
    print(f"Loaded {len(_MOVES_DATA)} moves from moves.json")
    print(f"SOUND_MOVES ({len(SOUND_MOVES)}): {sorted(SOUND_MOVES)[:10]}...")
    print(f"POWDER_MOVES ({len(POWDER_MOVES)}): {sorted(POWDER_MOVES)}")
    print(f"BULLET_MOVES ({len(BULLET_MOVES)}): {sorted(BULLET_MOVES)[:10]}...")
    print(f"WIND_MOVES ({len(WIND_MOVES)}): {sorted(WIND_MOVES)}")
    print(f"GRASS_TYPE_MOVES ({len(GRASS_TYPE_MOVES)}): {sorted(GRASS_TYPE_MOVES)[:10]}...")
    print(f"DARK_TYPE_MOVES ({len(DARK_TYPE_MOVES)}): {sorted(DARK_TYPE_MOVES)[:10]}...")
    print(f"SELF_STAT_DROP_MOVES: {SELF_STAT_DROP_MOVES}")
    print(f"ALL_STATUS_MOVES ({len(ALL_STATUS_MOVES)}): {sorted(ALL_STATUS_MOVES)[:10]}...")
