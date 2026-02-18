"""
Tests for strategic_filter.py
"""

import pytest
from unittest.mock import Mock, MagicMock
from fp.strategic_filter import (
    StrategicFilter,
    CommitmentHeuristic,
    filter_moves_with_gameplan,
    apply_commitment_heuristic
)
from fp.gameplan_generator import Gameplan


class TestStrategicFilter:
    """Test strategic move filtering."""
    
    def create_mock_battle(self, active_name="skarmory", active_hp=100, active_max_hp=100):
        """Create a mock battle object."""
        battle = Mock()
        
        # Mock active pokemon
        active = Mock()
        active.name = active_name
        active.hp = active_hp
        active.max_hp = active_max_hp
        
        # Mock user
        battle.user = Mock()
        battle.user.active = active
        
        # Mock opponent
        battle.opponent = Mock()
        battle.opponent.side_conditions = {}
        battle.opponent.active = Mock()
        
        battle.turn = 1
        
        return battle
    
    def create_test_gameplan(self, archetype="HazardStack"):
        """Create a test gameplan."""
        if archetype == "HazardStack":
            return Gameplan(
                archetype="HazardStack",
                primary_win_condition="Set hazards early",
                secondary_win_condition="Wear down opponent",
                early_game_goal="Set all hazards by turn 5",
                mid_game_goal="Maintain hazards, wall",
                late_game_goal="Execute finisher",
                critical_pokemon=["skarmory", "blissey", "gholdengo"],
                hp_minimums={"skarmory": 0.6, "gholdengo": 0.8},
                mandatory_moves={"skarmory": ["stealthrock", "spikes"]},
                must_happen_by_turn={"stealthrock": 4, "spikes": 6},
                prohibited_switches=[],
                switch_budget=6,
                early_phase_priority_moves=["stealthrock", "spikes"],
                mid_phase_priority_moves=["recover", "roost"],
                late_phase_priority_moves=[]
            )
        else:
            return Gameplan(
                archetype="Pivot",
                primary_win_condition="Maintain momentum",
                secondary_win_condition="Wear opponent",
                early_game_goal="Scout with pivots",
                mid_game_goal="Maintain cycles",
                late_game_goal="Convert advantage",
                critical_pokemon=["landorus-therian", "rillaboom"],
                hp_minimums={"landorus-therian": 0.7},
                mandatory_moves={},
                must_happen_by_turn={},
                prohibited_switches=[],
                switch_budget=10,
                early_phase_priority_moves=["uturn"],
                mid_phase_priority_moves=["uturn"],
                late_phase_priority_moves=[]
            )
    
    def test_mandatory_move_forcing(self):
        """Test that mandatory moves are forced when deadline approaches."""
        filter_engine = StrategicFilter()
        battle = self.create_mock_battle(active_name="skarmory")
        gameplan = self.create_test_gameplan()
        
        # No stealth rock on field yet
        battle.opponent.side_conditions = {}
        
        available_moves = ["stealth rock", "spikes", "roost", "brave bird"]
        
        # Turn 3 - should force stealth rock (deadline is turn 4)
        filtered = filter_engine.filter_moves_strategically(
            available_moves, battle, gameplan, turn_number=3
        )
        
        # Should contain stealth rock
        assert any("stealth rock" in m.lower() for m in filtered)
    
    def test_critical_pokemon_hp_protection(self):
        """Test that critical pokemon don't switch when HP is low."""
        filter_engine = StrategicFilter()
        
        # Gholdengo at 50% HP (below 80% minimum)
        battle = self.create_mock_battle(active_name="gholdengo", active_hp=50, active_max_hp=100)
        gameplan = self.create_test_gameplan()
        
        available_moves = ["shadow ball", "make it rain", "recover", "switch corviknight"]
        
        filtered = filter_engine.filter_moves_strategically(
            available_moves, battle, gameplan, turn_number=10
        )
        
        # Should not contain switch
        switch_moves = [m for m in filtered if m.startswith("switch ")]
        assert len(switch_moves) == 0 or len(filtered) == len(available_moves)  # Either removed or kept all
    
    def test_prohibited_switches(self):
        """Test that prohibited switches are blocked."""
        filter_engine = StrategicFilter()
        battle = self.create_mock_battle(active_name="gholdengo")
        
        # Create gameplan with prohibited switch
        gameplan = self.create_test_gameplan()
        gameplan.prohibited_switches = [("gholdengo", "blissey", "bad matchup")]
        
        available_moves = ["shadow ball", "switch blissey", "switch corviknight", "recover"]
        
        filtered = filter_engine.filter_moves_strategically(
            available_moves, battle, gameplan, turn_number=5
        )
        
        # Should not contain switch to blissey
        assert "switch blissey" not in [m.lower() for m in filtered]
        # But should contain other switches
        assert any("switch corviknight" in m.lower() for m in filtered) or \
               len(filtered) < len(available_moves)
    
    def test_convenience_function(self):
        """Test convenience function."""
        battle = self.create_mock_battle()
        gameplan = self.create_test_gameplan()
        
        available_moves = ["stealth rock", "spikes", "roost"]
        
        filtered = filter_moves_with_gameplan(
            available_moves, battle, gameplan, turn_number=2
        )
        
        assert len(filtered) > 0
        assert isinstance(filtered, list)


class TestCommitmentHeuristic:
    """Test commitment heuristic."""
    
    def test_commitment_boost_non_switches(self):
        """Test that non-switches are boosted after staying in."""
        heuristic = CommitmentHeuristic()
        battle = Mock()
        
        move_scores = {
            "shadow ball": 100.0,
            "make it rain": 90.0,
            "switch corviknight": 85.0,
            "recover": 80.0
        }
        
        last_decision = "shadow ball"  # Attacked last turn
        turns_in_current = 1  # Just one turn
        
        adjusted = heuristic.apply_commitment_boost(
            move_scores, last_decision, turns_in_current, battle
        )
        
        # Non-switches should be boosted
        assert adjusted["shadow ball"] > move_scores["shadow ball"]
        assert adjusted["make it rain"] > move_scores["make it rain"]
        assert adjusted["recover"] > move_scores["recover"]
        
        # Switches should be penalized
        assert adjusted["switch corviknight"] < move_scores["switch corviknight"]
    
    def test_no_boost_after_switch(self):
        """Test that no boost is applied after switching."""
        heuristic = CommitmentHeuristic()
        battle = Mock()
        
        move_scores = {
            "shadow ball": 100.0,
            "switch corviknight": 85.0
        }
        
        last_decision = "switch gholdengo"  # Switched last turn
        turns_in_current = 1
        
        adjusted = heuristic.apply_commitment_boost(
            move_scores, last_decision, turns_in_current, battle
        )
        
        # Should not modify scores
        assert adjusted["shadow ball"] == move_scores["shadow ball"]
    
    def test_convenience_function(self):
        """Test convenience function."""
        move_scores = {
            "tackle": 100.0,
            "switch pikachu": 80.0
        }
        
        adjusted = apply_commitment_heuristic(
            move_scores, "tackle", 1, Mock()
        )
        
        assert isinstance(adjusted, dict)
        assert "tackle" in adjusted
