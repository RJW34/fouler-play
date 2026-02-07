import unittest
from unittest.mock import MagicMock
from copy import deepcopy

import constants
from data.pkmn_sets import (
    SmogonSets,
    TeamDatasets,
    PredictedPokemonSet,
    PokemonSet,
    PokemonMoveset,
)
from fp.battle import Pokemon, Battle, Battler, StatRange
from fp.search.standard_battles import bayesian_set_probabilities


class TestBayesianSetProbabilities(unittest.TestCase):
    def setUp(self):
        """Initialize datasets for testing"""
        SmogonSets.__init__()
        TeamDatasets.__init__()
        
        # Initialize with a simple gen9ou setup
        SmogonSets.initialize("gen9ou", {"landorustherian", "garchomp", "dragapult"})
        TeamDatasets.initialize("gen9ou", {"landorustherian", "garchomp", "dragapult"})

    def test_basic_probability_calculation(self):
        """Test that basic probability calculation works"""
        pkmn = Pokemon("landorustherian", 100)
        
        probs = bayesian_set_probabilities(pkmn)
        
        # Should return a non-empty list
        self.assertIsInstance(probs, list)
        
        # Probabilities should sum to 1 (or close to it due to floating point)
        if probs:
            total_prob = sum(p for _, p in probs)
            self.assertAlmostEqual(total_prob, 1.0, places=5)

    def test_revealed_move_eliminates_incompatible_sets(self):
        """Test that revealing a move eliminates sets without that move"""
        pkmn = Pokemon("garchomp", 100)
        
        # Get initial probabilities
        initial_probs = bayesian_set_probabilities(pkmn)
        initial_count = len(initial_probs)
        
        # Add a specific move that only some sets have
        pkmn.add_move("earthquake")
        
        # Get updated probabilities
        updated_probs = bayesian_set_probabilities(pkmn)
        
        # All remaining sets should contain earthquake
        for pkmn_set, _ in updated_probs:
            self.assertIn("earthquake", pkmn_set.pkmn_moveset.moves)
        
        # Should have fewer or equal sets (some may have been eliminated)
        self.assertLessEqual(len(updated_probs), initial_count)

    def test_multiple_revealed_moves_narrow_down_sets(self):
        """Test that revealing multiple moves narrows down the set possibilities"""
        pkmn = Pokemon("dragapult", 100)
        
        # Add multiple moves
        pkmn.add_move("dracometeor")
        pkmn.add_move("shadowball")
        pkmn.add_move("uturn")
        
        probs = bayesian_set_probabilities(pkmn)
        
        # All remaining sets should contain all three moves
        for pkmn_set, _ in probs:
            self.assertIn("dracometeor", pkmn_set.pkmn_moveset.moves)
            self.assertIn("shadowball", pkmn_set.pkmn_moveset.moves)
            self.assertIn("uturn", pkmn_set.pkmn_moveset.moves)

    def test_revealed_item_filters_sets(self):
        """Test that revealing an item filters incompatible sets"""
        pkmn = Pokemon("landorustherian", 100)
        pkmn.item = "choicescarf"
        
        probs = bayesian_set_probabilities(pkmn)
        
        # All remaining sets should have choice scarf or be compatible
        for pkmn_set, _ in probs:
            # Either the set has choice scarf or item is unknown
            self.assertTrue(
                pkmn_set.pkmn_set.item == "choicescarf" 
                or pkmn_set.pkmn_set.item_check(pkmn)
            )

    def test_revealed_ability_filters_sets(self):
        """Test that revealing an ability filters incompatible sets"""
        pkmn = Pokemon("garchomp", 100)
        pkmn.ability = "roughskin"
        
        probs = bayesian_set_probabilities(pkmn)
        
        # All remaining sets should have rough skin or be compatible
        for pkmn_set, _ in probs:
            self.assertTrue(pkmn_set.pkmn_set.ability_check(pkmn))

    def test_impossible_items_eliminates_sets(self):
        """Test that impossible items are properly eliminated"""
        pkmn = Pokemon("landorustherian", 100)
        pkmn.impossible_items.add("choicescarf")
        pkmn.impossible_items.add("choiceband")
        
        probs = bayesian_set_probabilities(pkmn)
        
        # No remaining set should have impossible items
        for pkmn_set, _ in probs:
            self.assertNotIn(pkmn_set.pkmn_set.item, pkmn.impossible_items)

    def test_impossible_abilities_eliminates_sets(self):
        """Test that impossible abilities are properly eliminated"""
        pkmn = Pokemon("garchomp", 100)
        pkmn.impossible_abilities.add("roughskin")
        
        probs = bayesian_set_probabilities(pkmn)
        
        # No remaining set should have impossible abilities
        for pkmn_set, _ in probs:
            self.assertNotIn(pkmn_set.pkmn_set.ability, pkmn.impossible_abilities)

    def test_speed_range_filtering(self):
        """Test that speed range is respected"""
        pkmn = Pokemon("garchomp", 100)
        # Set a narrow speed range
        pkmn.speed_range = StatRange(min=300, max=350)
        
        probs = bayesian_set_probabilities(pkmn)
        
        # All remaining sets should pass speed check
        for pkmn_set, _ in probs:
            self.assertTrue(pkmn_set.pkmn_set.speed_check(pkmn))

    def test_tera_type_filtering(self):
        """Test that terastallization filters sets correctly"""
        pkmn = Pokemon("garchomp", 100)
        pkmn.terastallized = True
        pkmn.tera_type = "fire"
        
        probs = bayesian_set_probabilities(pkmn)
        
        # All remaining sets should either have fire tera or no tera type specified
        for pkmn_set, _ in probs:
            if pkmn_set.pkmn_set.tera_type:
                self.assertEqual(pkmn_set.pkmn_set.tera_type, "fire")

    def test_no_compatible_sets_returns_empty(self):
        """Test that impossible constraints return empty list"""
        pkmn = Pokemon("garchomp", 100)
        
        # Add contradictory moves that no set would have together
        pkmn.add_move("earthquake")
        pkmn.add_move("surf")
        pkmn.add_move("flamethrower")
        pkmn.add_move("thunderbolt")
        pkmn.add_move("icebeam")  # 5 moves - impossible
        
        probs = bayesian_set_probabilities(pkmn)
        
        # Should handle gracefully - either empty or only compatible sets
        for pkmn_set, _ in probs:
            # Verify all revealed moves are in the set
            for move in pkmn.moves:
                self.assertIn(move.name, pkmn_set.pkmn_moveset.moves)

    def test_probabilities_sum_to_one(self):
        """Test that probabilities always sum to 1 after renormalization"""
        pkmn = Pokemon("landorustherian", 100)
        pkmn.add_move("earthquake")
        pkmn.item = "leftovers"
        
        probs = bayesian_set_probabilities(pkmn)
        
        if probs:
            total = sum(p for _, p in probs)
            self.assertAlmostEqual(total, 1.0, places=5)

    def test_higher_count_sets_have_higher_prior_probability(self):
        """Test that sets with higher counts start with higher probability"""
        pkmn = Pokemon("garchomp", 100)
        
        probs = bayesian_set_probabilities(pkmn)
        
        if len(probs) > 1:
            # Get two sets with different counts
            sets_by_count = sorted(probs.keys(), key=lambda s: s.pkmn_set.count, reverse=True)
            
            if len(sets_by_count) >= 2:
                highest_count_set = sets_by_count[0]
                lowest_count_set = sets_by_count[-1]
                
                # If counts differ, probabilities should differ accordingly
                if highest_count_set.pkmn_set.count > lowest_count_set.pkmn_set.count:
                    self.assertGreater(
                        probs[highest_count_set],
                        probs[lowest_count_set]
                    )

    def test_hidden_power_handling(self):
        """Test that hidden power types are handled correctly"""
        pkmn = Pokemon("landorustherian", 100)
        pkmn.add_move(constants.HIDDEN_POWER)
        pkmn.hidden_power_possibilities = ["ice"]
        
        probs = bayesian_set_probabilities(pkmn)
        
        # Sets with incompatible hidden power types should be filtered
        for pkmn_set in probs.keys():
            has_hp = any(m.startswith(constants.HIDDEN_POWER) for m in pkmn_set.pkmn_moveset.moves)
            if has_hp:
                # Should have a compatible hidden power type
                compatible_hp = [
                    f"{constants.HIDDEN_POWER}{p}{constants.HIDDEN_POWER_ACTIVE_MOVE_BASE_DAMAGE_STRING}"
                    for p in pkmn.hidden_power_possibilities
                ]
                has_compatible = any(hp in pkmn_set.pkmn_moveset.moves for hp in compatible_hp)
                # If the set has hidden power, it should be compatible
                # OR the set doesn't require hidden power at all
                self.assertTrue(has_compatible or not has_hp)

    def test_battle_context_optional(self):
        """Test that battle context is optional and doesn't break when None"""
        pkmn = Pokemon("garchomp", 100)
        
        # Should work with None battle
        probs = bayesian_set_probabilities(pkmn, None)
        
        self.assertIsInstance(probs, dict)

    def test_empty_datasets_returns_empty(self):
        """Test behavior when datasets are empty"""
        # Reinitialize with empty sets
        SmogonSets.__init__()
        TeamDatasets.__init__()
        
        pkmn = Pokemon("garchomp", 100)
        probs = bayesian_set_probabilities(pkmn)
        
        # Should return empty dict when no data available
        self.assertEqual(len(probs), 0)


if __name__ == "__main__":
    unittest.main()
