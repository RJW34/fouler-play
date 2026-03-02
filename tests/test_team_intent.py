"""
Tests for the agnostic Team Intent Layer.

Tests that the build-signal inference engine correctly classifies
Pokemon roles, handles, and team context from arbitrary builds.
"""

import pytest
from fp.team_intent import (
    infer_pokemon_intent,
    build_team_context,
    PokemonIntent,
    TeamContext,
)


class TestPokemonIntentInference:
    """Test individual Pokemon intent inference."""

    def test_toxapex_special_wall(self):
        """Toxapex with max SpDef + Haze + Recover + Black Sludge -> special wall."""
        toxapex = {
            "species": "Toxapex",
            "item": "Black Sludge",
            "ability": "Regenerator",
            "moves": ["Scald", "Haze", "Recover", "Toxic Spikes"],
            "evs": {"hp": "252", "atk": "", "def": "4", "spa": "", "spd": "252", "spe": ""},
            "nature": "Calm",
        }
        intent = infer_pokemon_intent(toxapex)

        assert intent.role == "special wall", f"Expected 'special wall', got '{intent.role}'"
        assert intent.is_win_condition is False
        assert "recovery user" in intent.role_tags
        assert intent.recovery_threshold > 0
        assert intent.confidence >= 0.7

    def test_dragonite_setup_sweeper(self):
        """Dragonite with Dragon Dance + max Spe/Atk + no recovery -> setup sweeper."""
        dragonite = {
            "species": "Dragonite",
            "item": "Life Orb",
            "ability": "Multiscale",
            "moves": ["Dragon Dance", "Outrage", "Extreme Speed", "Earthquake"],
            "evs": {"hp": "", "atk": "252", "def": "", "spa": "", "spd": "4", "spe": "252"},
            "nature": "Adamant",
        }
        intent = infer_pokemon_intent(dragonite)

        assert intent.role == "setup sweeper", f"Expected 'setup sweeper', got '{intent.role}'"
        assert intent.is_win_condition is True
        assert "setup sweeper" in intent.role_tags
        assert intent.confidence >= 0.7

    def test_corviknight_physical_wall_pivot(self):
        """Corviknight with max Def + Roost + U-turn + Defog -> physical wall + pivot + hazard remover."""
        corviknight = {
            "species": "Corviknight",
            "item": "Rocky Helmet",
            "ability": "Pressure",
            "moves": ["Roost", "U-turn", "Defog", "Brave Bird"],
            "evs": {"hp": "252", "atk": "", "def": "252", "spa": "", "spd": "4", "spe": ""},
            "nature": "Impish",
        }
        intent = infer_pokemon_intent(corviknight)

        # Primary role should be physical wall
        assert intent.role == "physical wall", f"Expected 'physical wall', got '{intent.role}'"
        # Should have all three key tags
        assert "physical wall" in intent.role_tags
        assert "pivot" in intent.role_tags
        assert "hazard remover" in intent.role_tags
        assert intent.is_win_condition is False

    def test_gliscor_mixed_role(self):
        """Gliscor with Toxic Orb + Protect + Swords Dance + max Spe -> dual role."""
        gliscor = {
            "species": "Gliscor",
            "item": "Toxic Orb",
            "ability": "Poison Heal",
            "moves": ["Swords Dance", "Earthquake", "Protect", "Facade"],
            "evs": {"hp": "244", "atk": "", "def": "8", "spa": "", "spd": "", "spe": "252"},
            "nature": "Jolly",
        }
        intent = infer_pokemon_intent(gliscor)

        # Should recognize the setup sweeper aspect
        has_setup_tag = "setup sweeper" in intent.role_tags
        # And it has bulky/defensive qualities from Poison Heal + Protect
        # The role could be setup sweeper or substall depending on signals
        assert has_setup_tag or intent.role == "setup sweeper" or "substall" in intent.role_tags, \
            f"Expected setup sweeper or substall tag, got role='{intent.role}', tags={intent.role_tags}"
        # At minimum, it should have a substitute or setup-related tag
        assert any(t in intent.role_tags for t in ["setup sweeper", "substall", "substitute user"]), \
            f"Expected setup or substall functionality, got tags={intent.role_tags}"

    def test_team_context_full_team(self):
        """A 6-mon team should produce classified win_conditions and team_style."""
        team = [
            # Wall
            {
                "species": "Toxapex",
                "item": "Black Sludge",
                "ability": "Regenerator",
                "moves": ["Scald", "Haze", "Recover", "Toxic Spikes"],
                "evs": {"hp": "252", "atk": "", "def": "4", "spa": "", "spd": "252", "spe": ""},
                "nature": "Calm",
            },
            # Setup sweeper (win condition)
            {
                "species": "Dragonite",
                "item": "Life Orb",
                "ability": "Multiscale",
                "moves": ["Dragon Dance", "Outrage", "Extreme Speed", "Earthquake"],
                "evs": {"hp": "", "atk": "252", "def": "", "spa": "", "spd": "4", "spe": "252"},
                "nature": "Adamant",
            },
            # Physical wall + pivot
            {
                "species": "Corviknight",
                "item": "Rocky Helmet",
                "ability": "Pressure",
                "moves": ["Roost", "U-turn", "Defog", "Brave Bird"],
                "evs": {"hp": "252", "atk": "", "def": "252", "spa": "", "spd": "4", "spe": ""},
                "nature": "Impish",
            },
            # Hazard setter + pivot
            {
                "species": "Landorus-Therian",
                "item": "Leftovers",
                "ability": "Intimidate",
                "moves": ["Stealth Rock", "Earthquake", "U-turn", "Knock Off"],
                "evs": {"hp": "252", "atk": "", "def": "132", "spa": "", "spd": "124", "spe": ""},
                "nature": "Careful",
            },
            # Special attacker
            {
                "species": "Iron Valiant",
                "item": "Choice Specs",
                "ability": "Quark Drive",
                "moves": ["Moonblast", "Psyshock", "Focus Blast", "Thunderbolt"],
                "evs": {"hp": "", "atk": "", "def": "", "spa": "252", "spd": "4", "spe": "252"},
                "nature": "Timid",
            },
            # Revenge killer
            {
                "species": "Great Tusk",
                "item": "Choice Scarf",
                "ability": "Protosynthesis",
                "moves": ["Headlong Rush", "Close Combat", "Rapid Spin", "Knock Off"],
                "evs": {"hp": "", "atk": "252", "def": "", "spa": "", "spd": "4", "spe": "252"},
                "nature": "Jolly",
            },
        ]

        ctx = build_team_context(team)

        # Team style should be classified (not empty)
        assert ctx.team_style, "team_style should be classified"
        assert ctx.team_style in (
            "stall", "fat", "balance", "bulky offense", "hyper offense", "pivot heavy",
        ), f"Unexpected team style: {ctx.team_style}"

        # Win conditions should be non-empty (at least Dragonite)
        assert len(ctx.win_conditions) > 0, "Should have at least one win condition"
        assert "dragonite" in ctx.win_conditions, "Dragonite should be a win condition"

        # All 6 mons should have intent
        assert len(ctx.pokemon_intent) == 6, f"Expected 6 Pokemon intents, got {len(ctx.pokemon_intent)}"

        # Team rules should exist
        assert len(ctx.team_rules) > 0, "Should have team rules"


class TestIntentEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_team(self):
        """Empty team produces safe defaults."""
        ctx = build_team_context([])
        assert ctx.team_style == "balance"
        assert ctx.win_conditions == []
        assert len(ctx.pokemon_intent) == 0

    def test_minimal_pokemon_data(self):
        """Pokemon with minimal data doesn't crash."""
        minimal = {
            "species": "Pikachu",
            "item": "",
            "ability": "",
            "moves": [],
            "evs": {},
            "nature": "",
        }
        intent = infer_pokemon_intent(minimal)
        assert intent.role  # should have some role
        assert isinstance(intent.role_tags, list)
        assert isinstance(intent.confidence, float)

    def test_choice_scarf_revenge_killer(self):
        """Choice Scarf user classified as fast attacker."""
        scarf_user = {
            "species": "Garchomp",
            "item": "Choice Scarf",
            "ability": "Rough Skin",
            "moves": ["Earthquake", "Outrage", "Stone Edge", "Fire Fang"],
            "evs": {"hp": "", "atk": "252", "def": "", "spa": "", "spd": "4", "spe": "252"},
            "nature": "Jolly",
        }
        intent = infer_pokemon_intent(scarf_user)
        assert intent.role == "fast attacker", f"Expected fast attacker, got '{intent.role}'"
        assert intent.is_win_condition is False  # scarf users are revenge killers, not win conditions

    def test_regenerator_pivot(self):
        """Regenerator mon with pivot moves classified correctly."""
        slowking = {
            "species": "Slowking-Galar",
            "item": "Heavy-Duty Boots",
            "ability": "Regenerator",
            "moves": ["Future Sight", "Sludge Bomb", "Flip Turn", "Slack Off"],
            "evs": {"hp": "252", "atk": "", "def": "4", "spa": "252", "spd": "", "spe": ""},
            "nature": "Modest",
        }
        intent = infer_pokemon_intent(slowking)
        assert "pivot" in intent.role_tags, f"Expected pivot tag, got tags={intent.role_tags}"

    def test_handles_types_inference(self):
        """Bulky Water/Ground type should handle Fire, Electric, Rock, Poison."""
        gastrodon = {
            "species": "Gastrodon",
            "item": "Leftovers",
            "ability": "Storm Drain",
            "moves": ["Recover", "Scald", "Ice Beam", "Toxic"],
            "evs": {"hp": "252", "atk": "", "def": "4", "spa": "", "spd": "252", "spe": ""},
            "nature": "Calm",
        }
        intent = infer_pokemon_intent(gastrodon)
        # Storm Drain handles water, and type resistances handle fire/electric/rock/poison
        assert "water" in intent.handles_types, "Should handle water via Storm Drain"
        assert "electric" in intent.handles_types, "Should handle electric (immune via Ground type)"
        assert "fire" in intent.handles_types, "Should handle fire (resist via Water type)"
