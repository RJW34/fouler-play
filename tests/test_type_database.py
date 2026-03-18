"""Tests for fp.type_database — authoritative Pokemon data lookups."""

import pytest
from fp.type_database import (
    get_pokemon_types,
    get_type_effectiveness,
    get_move_type,
    get_pokemon_abilities,
    get_pokemon_base_stats,
)


class TestGetPokemonTypes:
    """Verify type lookups against known Pokemon."""

    def test_great_tusk_is_ground_fighting(self):
        """The bug that caused autoresearch to be disabled: Haiku called
        Great Tusk Ground/Bug. It's Ground/Fighting."""
        result = get_pokemon_types("great tusk")
        assert result == ["ground", "fighting"]

    def test_great_tusk_normalized(self):
        assert get_pokemon_types("greattusk") == ["ground", "fighting"]

    def test_great_tusk_hyphenated(self):
        assert get_pokemon_types("great-tusk") == ["ground", "fighting"]

    def test_kyurem(self):
        assert get_pokemon_types("kyurem") == ["dragon", "ice"]

    def test_dondozo(self):
        assert get_pokemon_types("dondozo") == ["water"]

    def test_ting_lu(self):
        assert get_pokemon_types("ting-lu") == ["dark", "ground"]

    def test_gholdengo(self):
        assert get_pokemon_types("gholdengo") == ["steel", "ghost"]

    def test_blissey(self):
        assert get_pokemon_types("blissey") == ["normal"]

    def test_unknown_returns_none(self):
        assert get_pokemon_types("fakemon_xyz_123") is None

    def test_empty_string_returns_none(self):
        assert get_pokemon_types("") is None


class TestGetTypeEffectiveness:
    """Verify type chart calculations."""

    def test_water_vs_ground_fighting(self):
        # Water is super effective vs Ground (2x), neutral vs Fighting (1x) = 2.0
        result = get_type_effectiveness("water", ["ground", "fighting"])
        assert result == 2.0

    def test_ice_vs_dragon_ice(self):
        # Ice is super effective vs Dragon (2x), not very effective vs Ice (0.5) = 1.0
        result = get_type_effectiveness("ice", ["dragon", "ice"])
        assert result == 1.0

    def test_ghost_vs_normal(self):
        # Ghost doesn't affect Normal = 0
        result = get_type_effectiveness("ghost", ["normal"])
        assert result == 0.0

    def test_electric_vs_ground(self):
        # Electric doesn't affect Ground = 0
        result = get_type_effectiveness("electric", ["ground"])
        assert result == 0.0

    def test_fire_vs_steel(self):
        result = get_type_effectiveness("fire", ["steel"])
        assert result == 2.0

    def test_ground_vs_fire_steel(self):
        # Ground: 2x vs Fire, 2x vs Steel = 4x
        result = get_type_effectiveness("ground", ["fire", "steel"])
        assert result == 4.0

    def test_unknown_attack_type(self):
        # Unknown attack type should return 1.0
        result = get_type_effectiveness("banana", ["water"])
        assert result == 1.0

    def test_unknown_defense_type(self):
        # Unknown defense type should be skipped
        result = get_type_effectiveness("fire", ["banana"])
        assert result == 1.0

    def test_empty_defense_types(self):
        result = get_type_effectiveness("fire", [])
        assert result == 1.0


class TestGetMoveType:
    """Verify move type lookups."""

    def test_earthquake(self):
        assert get_move_type("earthquake") == "ground"

    def test_ice_beam(self):
        assert get_move_type("ice beam") == "ice"

    def test_flamethrower(self):
        assert get_move_type("flamethrower") == "fire"

    def test_unknown_move(self):
        assert get_move_type("fake_move_xyz") is None


class TestGetPokemonAbilities:
    """Verify ability lookups."""

    def test_great_tusk(self):
        abilities = get_pokemon_abilities("great tusk")
        assert abilities is not None
        assert "Protosynthesis" in abilities

    def test_unknown_pokemon(self):
        assert get_pokemon_abilities("fakemon_xyz") is None


class TestGetPokemonBaseStats:
    """Verify base stat lookups."""

    def test_blissey_hp(self):
        stats = get_pokemon_base_stats("blissey")
        assert stats is not None
        assert stats["hp"] == 255

    def test_unknown_pokemon(self):
        assert get_pokemon_base_stats("fakemon_xyz") is None
