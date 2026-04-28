"""Tests for PokedexOracle — grounding layer for all Pokemon facts."""

import pytest
from data.pokedex_oracle import oracle


class TestCoreLookups:
    """Verify oracle returns correct data from the actual data files."""

    def test_pokemon_lookup(self):
        g = oracle.pokemon("gholdengo")
        assert g is not None
        assert g["types"] == ["steel", "ghost"]
        assert g["abilities"]["0"] == "Good as Gold"

    def test_move_lookup(self):
        m = oracle.move("shadowball")
        assert m is not None
        assert m["type"] == "ghost"
        assert m["basePower"] == 80
        assert m["category"] == "special"

    def test_unknown_pokemon_returns_none(self):
        assert oracle.pokemon("notarealmon") is None

    def test_unknown_move_returns_none(self):
        assert oracle.move("notarealmove") is None


class TestTypeEffectiveness:
    """Verify the oracle's type chart matches known correct matchups."""

    def test_ghost_resisted_by_dark(self):
        """Ghost→Dark is 0.5x, NOT immune. This was a real bug."""
        assert oracle.effectiveness("ghost", ["dark"]) == 0.5

    def test_ghost_immune_to_normal(self):
        assert oracle.effectiveness("normal", ["ghost"]) == 0.0

    def test_fighting_immune_to_ghost(self):
        assert oracle.effectiveness("fighting", ["ghost"]) == 0.0

    def test_ground_super_effective_vs_steel(self):
        assert oracle.effectiveness("ground", ["steel"]) == 2.0

    def test_dual_type_multiplication(self):
        # Ghost vs Dark/Ground: 0.5 (dark resist) * 1.0 (ground neutral) = 0.5
        assert oracle.effectiveness("ghost", ["dark", "ground"]) == 0.5

    def test_fire_vs_steel_ghost(self):
        # Fire vs Steel/Ghost: 2.0 (steel) * 1.0 (ghost) = 2.0
        assert oracle.effectiveness("fire", ["steel", "ghost"]) == 2.0


class TestValidation:
    """Verify the oracle can catch incorrect claims."""

    def test_validate_correct_ability(self):
        assert oracle.validate_ability_claim("gholdengo", "Good as Gold") is True

    def test_validate_wrong_ability(self):
        assert oracle.validate_ability_claim("gholdengo", "Levitate") is False

    def test_validate_correct_type_claim(self):
        correct, actual = oracle.validate_type_claim("ghost", ["dark"], 0.5)
        assert correct is True
        assert actual == 0.5

    def test_validate_wrong_type_claim(self):
        """The exact bug that was in TASKBOARD: Ghost 'immune' to Dark."""
        correct, actual = oracle.validate_type_claim("ghost", ["dark"], 0.0)
        assert correct is False
        assert actual == 0.5


class TestSmogonData:
    """Verify Smogon usage data is accessible."""

    def test_common_sets_returns_data(self):
        sets = oracle.common_sets("gholdengo")
        assert "moves" in sets
        assert len(sets["moves"]) > 0
        # Shadow Ball should be in top moves
        assert any("shadowball" in k for k in sets["moves"])

    def test_common_sets_unknown_pokemon(self):
        sets = oracle.common_sets("notarealmon")
        assert sets["moves"] == {}


class TestTeamParsing:
    """Verify we can parse our actual team files."""

    def test_parse_stall_team(self):
        team = oracle.parse_team_file("gen9/ou/fat-team-1-stall")
        assert len(team) == 6
        names = [m["name"] for m in team]
        assert "Gliscor" in names
        assert "Gholdengo" in names

    def test_team_profile_enriches_types(self):
        team = oracle.team_profile("gen9/ou/fat-team-1-stall")
        gliscor = next(m for m in team if m["name"] == "Gliscor")
        assert gliscor["types"] == ["ground", "flying"]

    def test_team_profile_enriches_moves(self):
        team = oracle.team_profile("gen9/ou/fat-team-1-stall")
        gliscor = next(m for m in team if m["name"] == "Gliscor")
        eq = next(m for m in gliscor["moves"] if isinstance(m, dict) and m["name"] == "Earthquake")
        assert eq["type"] == "ground"
        assert eq["basePower"] == 100


class TestMatchupSummary:
    """Verify matchup analysis against our teams."""

    def test_matchup_returns_structure(self):
        mu = oracle.matchup_summary("gholdengo", "gen9/ou/fat-team-1-stall")
        assert "our_walls" in mu
        assert "our_checks" in mu
        assert "our_threatened" in mu
        assert "per_pokemon" in mu

    def test_matchup_unknown_opponent(self):
        mu = oracle.matchup_summary("notarealmon", "gen9/ou/fat-team-1-stall")
        assert "error" in mu


class TestGroundingBlock:
    """Verify grounding blocks contain everything needed to prevent hallucination."""

    def test_grounding_block_complete(self):
        block = oracle.grounding_block("gholdengo")
        assert "source" in block
        assert block["types"] == ["steel", "ghost"]
        assert "common_moves" in block
        assert len(block["common_moves"]) > 0
        assert "common_items" in block
        assert "common_tera" in block
        # Each move should have type/power data
        for move in block["common_moves"]:
            if "type" in move:
                assert "basePower" in move
                assert "category" in move

    def test_grounding_block_unknown(self):
        block = oracle.grounding_block("notarealmon")
        assert "error" in block


class TestCodeAssumptionsGrounding:
    """Verify that assumptions hardcoded in the decision engine match reality.

    These tests catch drift between what the code believes and what
    pokedex.json actually says. If any of these fail, the decision
    engine is operating on wrong facts.
    """

    def test_unaware_pokemon_actually_have_unaware(self):
        """Verify POKEMON_COMMONLY_UNAWARE list in constants matches pokedex."""
        from constants import POKEMON_COMMONLY_UNAWARE
        for mon_name in POKEMON_COMMONLY_UNAWARE:
            # The constants list uses normalized names
            dex = oracle.pokemon(mon_name)
            if dex is None:
                continue  # might be a form name not in base pokedex
            abilities = [v.lower().replace(" ", "") for v in dex["abilities"].values()]
            assert "unaware" in abilities, (
                f"{mon_name} is in POKEMON_COMMONLY_UNAWARE but doesn't have "
                f"Unaware in pokedex.json (has: {dex['abilities']})"
            )
