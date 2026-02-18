"""
Tests for multi_turn_planner.py
"""

import pytest
from unittest.mock import Mock
from fp.multi_turn_planner import (
    MultiTurnPlanner,
    GamePhase,
    get_game_phase_from_state,
    evaluate_move_sequence
)
from fp.gameplan_generator import Gameplan


class TestMultiTurnPlanner:
    """Test multi-turn planning."""
    
    def create_mock_battle(self, team_hp_ratios=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                          opp_hp_ratios=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0]):
        """Create a mock battle with specified HP ratios."""
        battle = Mock()
        battle.turn = 1
        
        # Mock user's team
        battle.user = Mock()
        active = Mock()
        active.name = "skarmory"
        active.hp = int(100 * team_hp_ratios[0])
        active.max_hp = 100
        battle.user.active = active
        
        battle.user.reserve = []
        for i, ratio in enumerate(team_hp_ratios[1:], 1):
            pkmn = Mock()
            pkmn.name = f"pokemon{i}"
            pkmn.hp = int(100 * ratio)
            pkmn.max_hp = 100
            battle.user.reserve.append(pkmn)
        
        # Mock opponent's team
        battle.opponent = Mock()
        opp_active = Mock()
        opp_active.name = "opponent_active"
        opp_active.hp = int(100 * opp_hp_ratios[0])
        opp_active.max_hp = 100
        battle.opponent.active = opp_active
        battle.opponent.side_conditions = {}
        
        battle.opponent.reserve = []
        for i, ratio in enumerate(opp_hp_ratios[1:], 1):
            pkmn = Mock()
            pkmn.name = f"opp_pokemon{i}"
            pkmn.hp = int(100 * ratio)
            pkmn.max_hp = 100
            battle.opponent.reserve.append(pkmn)
        
        return battle
    
    def create_test_gameplan(self, archetype="HazardStack"):
        """Create a test gameplan."""
        return Gameplan(
            archetype=archetype,
            primary_win_condition="Test win condition",
            secondary_win_condition="Test secondary",
            early_game_goal="Early goal",
            mid_game_goal="Mid goal",
            late_game_goal="Late goal",
            critical_pokemon=["skarmory", "blissey"],
            hp_minimums={"skarmory": 0.6},
            mandatory_moves={"skarmory": ["stealthrock"]},
            must_happen_by_turn={"stealthrock": 4},
            prohibited_switches=[],
            switch_budget=6,
            early_phase_priority_moves=["stealthrock"],
            mid_phase_priority_moves=["recover"],
            late_phase_priority_moves=[]
        )
    
    def test_game_phase_early(self):
        """Test early game phase detection."""
        planner = MultiTurnPlanner()
        
        # All pokemon at >70% HP
        battle = self.create_mock_battle(
            team_hp_ratios=[1.0, 1.0, 0.9, 0.8, 1.0, 1.0],
            opp_hp_ratios=[1.0, 0.9, 1.0, 0.8, 1.0, 1.0]
        )
        
        phase = planner.get_game_phase(battle)
        assert phase == GamePhase.EARLY
    
    def test_game_phase_mid(self):
        """Test mid game phase detection."""
        planner = MultiTurnPlanner()
        
        # Average HP around 50-60%
        battle = self.create_mock_battle(
            team_hp_ratios=[0.7, 0.5, 0.6, 0.4, 0.8, 0.5],
            opp_hp_ratios=[0.6, 0.5, 0.7, 0.4, 0.6, 0.5]
        )
        
        phase = planner.get_game_phase(battle)
        assert phase == GamePhase.MID
    
    def test_game_phase_late(self):
        """Test late game phase detection."""
        planner = MultiTurnPlanner()
        
        # Average HP < 40%
        battle = self.create_mock_battle(
            team_hp_ratios=[0.3, 0.2, 0.1, 0.0, 0.4, 0.3],
            opp_hp_ratios=[0.2, 0.3, 0.0, 0.1, 0.4, 0.2]
        )
        
        phase = planner.get_game_phase(battle)
        assert phase == GamePhase.LATE
    
    def test_alignment_score_hazard_early(self):
        """Test alignment scoring for hazard moves in early game."""
        planner = MultiTurnPlanner()
        battle = self.create_mock_battle()
        gameplan = self.create_test_gameplan("HazardStack")
        
        # Stealth Rock in early game should score high
        score = planner.alignment_score(
            "stealth rock",
            gameplan,
            turn_number=2,
            game_phase=GamePhase.EARLY,
            game_state=battle
        )
        
        assert score >= 0.7  # Should be high priority
    
    def test_alignment_score_pivot(self):
        """Test alignment scoring for pivot moves."""
        planner = MultiTurnPlanner()
        battle = self.create_mock_battle()
        gameplan = self.create_test_gameplan("Pivot")
        
        # U-turn in early/mid game should score high for pivot teams
        score = planner.alignment_score(
            "u-turn",
            gameplan,
            turn_number=5,
            game_phase=GamePhase.MID,
            game_state=battle
        )
        
        assert score >= 0.7
    
    def test_alignment_score_recovery_stall(self):
        """Test alignment scoring for recovery moves on stall teams."""
        planner = MultiTurnPlanner()
        battle = self.create_mock_battle()
        gameplan = self.create_test_gameplan("StallCore")
        
        # Recover in mid game should score very high for stall
        score = planner.alignment_score(
            "recover",
            gameplan,
            turn_number=10,
            game_phase=GamePhase.MID,
            game_state=battle
        )
        
        assert score >= 0.8
    
    def test_evaluate_sequence(self):
        """Test sequence evaluation."""
        planner = MultiTurnPlanner()
        battle = self.create_mock_battle()
        gameplan = self.create_test_gameplan("HazardStack")
        
        # Evaluate stealth rock sequence
        sequence_value = planner.evaluate_sequence(
            battle,
            "stealth rock",
            gameplan,
            depth=3
        )
        
        # Should return a value between 0 and 1
        assert 0.0 <= sequence_value <= 1.0
        # For hazard stack early game, should be relatively high
        assert sequence_value >= 0.4
    
    def test_eval_position_with_gameplan(self):
        """Test phase-specific eval adjustments."""
        planner = MultiTurnPlanner()
        battle = self.create_mock_battle()
        gameplan = self.create_test_gameplan("HazardStack")
        
        base_eval = {
            "stealth rock": 100.0,
            "spikes": 90.0,
            "roost": 80.0,
            "brave bird": 85.0
        }
        
        # Early game - should boost mandatory moves
        adjusted = planner.eval_position_with_gameplan(
            battle,
            gameplan,
            GamePhase.EARLY,
            base_eval
        )
        
        # Stealth rock should be boosted (it's mandatory)
        assert adjusted["stealth rock"] >= base_eval["stealth rock"]
    
    def test_convenience_function_get_phase(self):
        """Test get_game_phase_from_state convenience function."""
        battle = self.create_mock_battle()
        
        phase = get_game_phase_from_state(battle)
        
        assert isinstance(phase, GamePhase)
        assert phase == GamePhase.EARLY  # Fresh teams
    
    def test_convenience_function_evaluate_sequence(self):
        """Test evaluate_move_sequence convenience function."""
        battle = self.create_mock_battle()
        gameplan = self.create_test_gameplan()
        
        score = evaluate_move_sequence(battle, "stealth rock", gameplan, depth=3)
        
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
