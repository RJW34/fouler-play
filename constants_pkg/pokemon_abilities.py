# constants_pkg/pokemon_abilities.py
# Dynamic Pokemon-to-ability mapping from pokedex.json
# Generates sets of Pokemon that commonly have specific abilities

import json
from pathlib import Path

# Load pokedex.json once at module import
_POKEDEX_JSON_PATH = Path(__file__).parent.parent / "data" / "pokedex.json"


def _load_pokedex_data() -> dict:
    """Load and return the pokedex data."""
    try:
        with open(_POKEDEX_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


_POKEDEX_DATA = _load_pokedex_data()


def _normalize_ability(ability: str) -> str:
    """Normalize ability name to lowercase, no spaces/hyphens."""
    return ability.lower().replace(" ", "").replace("-", "")


def _get_pokemon_with_ability(target_ability: str, include_hidden: bool = True) -> set[str]:
    """
    Find all Pokemon that have a specific ability.

    Args:
        target_ability: The ability name (will be normalized)
        include_hidden: Whether to include hidden abilities (H slot)

    Returns:
        Set of Pokemon names (lowercase) that have this ability
    """
    target = _normalize_ability(target_ability)
    result = set()

    for pkmn_name, pkmn_data in _POKEDEX_DATA.items():
        abilities = pkmn_data.get("abilities", {})

        # Check primary ability (slot 0)
        if _normalize_ability(abilities.get("0", "")) == target:
            result.add(pkmn_name)
            continue

        # Check secondary ability (slot 1)
        if _normalize_ability(abilities.get("1", "")) == target:
            result.add(pkmn_name)
            continue

        # Check hidden ability (slot H)
        if include_hidden and _normalize_ability(abilities.get("H", "")) == target:
            result.add(pkmn_name)

    return result


def _get_pokemon_with_any_ability(target_abilities: list[str], include_hidden: bool = True) -> set[str]:
    """Find all Pokemon that have any of the specified abilities."""
    result = set()
    for ability in target_abilities:
        result |= _get_pokemon_with_ability(ability, include_hidden)
    return result


# =============================================================================
# POKEMON SETS BY ABILITY
# These are dynamically generated from pokedex.json
# =============================================================================

# Contrary: Stat changes are reversed (our drops become their boosts!)
POKEMON_COMMONLY_CONTRARY = _get_pokemon_with_ability("Contrary")

# Sap Sipper: Immune to Grass moves, +1 Atk
POKEMON_COMMONLY_SAP_SIPPER = _get_pokemon_with_ability("Sap Sipper")

# Sturdy: Survives any hit at full HP with 1 HP (like Focus Sash)
POKEMON_COMMONLY_STURDY = _get_pokemon_with_ability("Sturdy")

# Disguise: First hit is blocked (Mimikyu's signature)
POKEMON_COMMONLY_DISGUISE = _get_pokemon_with_ability("Disguise")

# Soundproof: Immune to sound-based moves
POKEMON_COMMONLY_SOUNDPROOF = _get_pokemon_with_ability("Soundproof")

# Bulletproof: Immune to ball/bullet moves
POKEMON_COMMONLY_BULLETPROOF = _get_pokemon_with_ability("Bulletproof")

# Overcoat: Immune to powder moves and weather damage
POKEMON_COMMONLY_OVERCOAT = _get_pokemon_with_ability("Overcoat")

# Earth Eater: Immune to Ground, heals instead
POKEMON_COMMONLY_EARTH_EATER = _get_pokemon_with_ability("Earth Eater")

# Justified: Dark moves give +1 Atk
POKEMON_COMMONLY_JUSTIFIED = _get_pokemon_with_ability("Justified")

# Steam Engine: Fire/Water moves give +6 Spe!
POKEMON_COMMONLY_STEAM_ENGINE = _get_pokemon_with_ability("Steam Engine")

# Supreme Overlord: Atk/SpA boost for each fainted ally
POKEMON_COMMONLY_SUPREME_OVERLORD = _get_pokemon_with_ability("Supreme Overlord")

# Wind Rider: Wind moves give +1 Atk instead of damage
POKEMON_COMMONLY_WIND_RIDER = _get_pokemon_with_ability("Wind Rider")

# Well-Baked Body: Fire moves give +2 Def instead of damage
POKEMON_COMMONLY_WELL_BAKED_BODY = _get_pokemon_with_ability("Well-Baked Body")

# Clear Body / White Smoke / Full Metal Body: Immune to stat drops
POKEMON_COMMONLY_STAT_DROP_IMMUNE = _get_pokemon_with_any_ability([
    "Clear Body", "White Smoke", "Full Metal Body"
])

# Mirror Armor: Reflects stat drops back to attacker
POKEMON_COMMONLY_MIRROR_ARMOR = _get_pokemon_with_ability("Mirror Armor")

# Fluffy: Takes 2x damage from Fire moves (opportunity for us!)
POKEMON_COMMONLY_FLUFFY = _get_pokemon_with_ability("Fluffy")

# Dry Skin: Takes 1.25x from Fire (opportunity), heals from Water
# Note: Already in POKEMON_COMMONLY_WATER_IMMUNE for water immunity
POKEMON_COMMONLY_DRY_SKIN = _get_pokemon_with_ability("Dry Skin")

# Prankster: Priority +1 on status moves (but fails vs Dark types)
POKEMON_WITH_PRANKSTER = _get_pokemon_with_ability("Prankster")

# Priority-blocking abilities: Queenly Majesty / Dazzling / Armor Tail
POKEMON_COMMONLY_QUEENLY_MAJESTY = _get_pokemon_with_ability("Queenly Majesty")
POKEMON_COMMONLY_DAZZLING = _get_pokemon_with_ability("Dazzling")
POKEMON_COMMONLY_ARMOR_TAIL = _get_pokemon_with_ability("Armor Tail")

# Intimidate: Drops opponent's Atk on switch-in (triggers Defiant/Competitive)
POKEMON_WITH_INTIMIDATE = _get_pokemon_with_ability("Intimidate")

# Air Balloon holders - this is item-based, not ability, so we define manually
# Common leads and setup sweepers that often hold Air Balloon
POKEMON_COMMONLY_AIR_BALLOON = {
    "heatran",      # Very common to dodge Ground
    "magnezone",    # Avoids 4x Ground weakness
    "aegislash",    # Sometimes used
    "excadrill",    # Lead sets
    "bisharp",      # Avoids Fighting Ground
    "kingambit",    # Same as Bisharp
}


# =============================================================================
# VERIFICATION / DEBUG
# =============================================================================

if __name__ == "__main__":
    print(f"Loaded {len(_POKEDEX_DATA)} Pokemon from pokedex.json")
    print()

    sets = [
        ("POKEMON_COMMONLY_CONTRARY", POKEMON_COMMONLY_CONTRARY),
        ("POKEMON_COMMONLY_SAP_SIPPER", POKEMON_COMMONLY_SAP_SIPPER),
        ("POKEMON_COMMONLY_STURDY", POKEMON_COMMONLY_STURDY),
        ("POKEMON_COMMONLY_DISGUISE", POKEMON_COMMONLY_DISGUISE),
        ("POKEMON_COMMONLY_SOUNDPROOF", POKEMON_COMMONLY_SOUNDPROOF),
        ("POKEMON_COMMONLY_BULLETPROOF", POKEMON_COMMONLY_BULLETPROOF),
        ("POKEMON_COMMONLY_OVERCOAT", POKEMON_COMMONLY_OVERCOAT),
        ("POKEMON_COMMONLY_EARTH_EATER", POKEMON_COMMONLY_EARTH_EATER),
        ("POKEMON_COMMONLY_JUSTIFIED", POKEMON_COMMONLY_JUSTIFIED),
        ("POKEMON_COMMONLY_STEAM_ENGINE", POKEMON_COMMONLY_STEAM_ENGINE),
        ("POKEMON_COMMONLY_SUPREME_OVERLORD", POKEMON_COMMONLY_SUPREME_OVERLORD),
        ("POKEMON_COMMONLY_WIND_RIDER", POKEMON_COMMONLY_WIND_RIDER),
        ("POKEMON_COMMONLY_WELL_BAKED_BODY", POKEMON_COMMONLY_WELL_BAKED_BODY),
        ("POKEMON_COMMONLY_STAT_DROP_IMMUNE", POKEMON_COMMONLY_STAT_DROP_IMMUNE),
        ("POKEMON_COMMONLY_MIRROR_ARMOR", POKEMON_COMMONLY_MIRROR_ARMOR),
        ("POKEMON_COMMONLY_FLUFFY", POKEMON_COMMONLY_FLUFFY),
        ("POKEMON_COMMONLY_DRY_SKIN", POKEMON_COMMONLY_DRY_SKIN),
        ("POKEMON_WITH_PRANKSTER", POKEMON_WITH_PRANKSTER),
        ("POKEMON_WITH_INTIMIDATE", POKEMON_WITH_INTIMIDATE),
    ]

    for name, pkmn_set in sets:
        if pkmn_set:
            print(f"{name} ({len(pkmn_set)}): {sorted(pkmn_set)[:5]}...")
        else:
            print(f"{name}: (empty)")
