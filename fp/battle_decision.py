"""
Battle Decision - Strategic decision layer integration.

Integrates archetype analysis, gameplan generation, strategic filtering,
and multi-turn planning into the decision pipeline.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from copy import deepcopy

from fp.archetype_analyzer import ArchetypeAnalyzer, TeamArchetype, analyze_team_archetype
from fp.gameplan_generator import GameplanGenerator, Gameplan, generate_gameplan_from_archetype
from fp.strategic_filter import StrategicFilter, CommitmentHeuristic
from fp.multi_turn_planner import MultiTurnPlanner, GamePhase
from fp.helpers import normalize_name

logger = logging.getLogger(__name__)


# Global cache for archetypes and gameplans (per battle)
_battle_cache: Dict[str, Dict[str, Any]] = {}


class StrategicDecisionLayer:
    """Manages strategic decision-making with archetype awareness."""
    
    def __init__(self):
        self.analyzer = ArchetypeAnalyzer()
        self.gameplan_generator = GameplanGenerator()
        self.strategic_filter = StrategicFilter()
        self.multi_turn_planner = MultiTurnPlanner()
        self.commitment_heuristic = CommitmentHeuristic()
    
    def initialize_for_battle(self, battle_tag: str, team_data: List[Dict]) -> Tuple[TeamArchetype, Gameplan]:
        """
        Initialize archetype and gameplan for a battle (called once at start).
        
        Args:
            battle_tag: Unique battle identifier
            team_data: List of pokemon dicts for our team
            
        Returns:
            Tuple of (TeamArchetype, Gameplan)
        """
        # Check cache first
        if battle_tag in _battle_cache:
            cached = _battle_cache[battle_tag]
            return cached["archetype"], cached["gameplan"]
        
        # Analyze archetype
        archetype = self.analyzer.classify_team(team_data)
        logger.info(
            f"[STRATEGIC] Battle {battle_tag}: Archetype={archetype.archetype}, "
            f"Confidence={archetype.confidence:.2f}"
        )
        logger.info(f"[STRATEGIC] Win condition: {archetype.primary_win_condition}")
        
        # Generate gameplan
        gameplan = self.gameplan_generator.generate(archetype, team_data)
        logger.info(
            f"[STRATEGIC] Gameplan: Early={gameplan.early_game_goal}, "
            f"Mid={gameplan.mid_game_goal}, Late={gameplan.late_game_goal}"
        )
        
        # Cache for this battle
        _battle_cache[battle_tag] = {
            "archetype": archetype,
            "gameplan": gameplan,
            "team_data": team_data
        }
        
        return archetype, gameplan
    
    def get_cached_gameplan(self, battle_tag: str) -> Optional[Gameplan]:
        """
        Get cached gameplan for a battle.
        
        Args:
            battle_tag: Battle identifier
            
        Returns:
            Gameplan if cached, None otherwise
        """
        if battle_tag in _battle_cache:
            return _battle_cache[battle_tag]["gameplan"]
        return None
    
    def clear_battle_cache(self, battle_tag: str):
        """Clear cache for a finished battle."""
        if battle_tag in _battle_cache:
            del _battle_cache[battle_tag]
            logger.debug(f"[STRATEGIC] Cleared cache for {battle_tag}")
    
    def enhance_move_selection(
        self,
        available_moves: List[str],
        game_state: Any,
        battle_tag: str,
        last_decision: Optional[str] = None,
        turns_in_current: int = 0
    ) -> Tuple[List[str], Dict[str, float]]:
        """
        Apply strategic layer to move selection.
        
        This is called BEFORE the main search/eval to:
        1. Filter moves based on gameplan
        2. Provide strategic scoring adjustments
        
        Args:
            available_moves: List of available move strings
            game_state: Battle object
            battle_tag: Battle identifier
            last_decision: Last move selected
            turns_in_current: Turns current pokemon has been active
            
        Returns:
            Tuple of (filtered_moves, strategic_scores)
        """
        # Get cached gameplan
        gameplan = self.get_cached_gameplan(battle_tag)
        if not gameplan:
            logger.warning(f"[STRATEGIC] No gameplan found for {battle_tag}")
            return available_moves, {}
        
        turn_number = getattr(game_state, "turn", 1)
        
        # STEP 1: Strategic filtering (hard constraints)
        filtered_moves = self.strategic_filter.filter_moves_strategically(
            available_moves,
            game_state,
            gameplan,
            turn_number
        )
        
        if len(filtered_moves) < len(available_moves):
            logger.info(
                f"[STRATEGIC] Filtered {len(available_moves)} -> {len(filtered_moves)} moves "
                f"(turn {turn_number})"
            )
        
        # STEP 2: Multi-turn sequence evaluation
        game_phase = self.multi_turn_planner.get_game_phase(game_state)
        strategic_scores = {}
        
        for move in filtered_moves:
            # Calculate alignment score
            alignment = self.multi_turn_planner.alignment_score(
                move,
                gameplan,
                turn_number,
                game_phase,
                game_state
            )
            
            # Calculate sequence value (3-turn lookahead)
            sequence_value = self.multi_turn_planner.evaluate_sequence(
                game_state,
                move,
                gameplan,
                depth=3
            )
            
            # Combine: 60% current alignment + 40% sequence value
            strategic_scores[move] = 0.6 * alignment + 0.4 * sequence_value
        
        logger.debug(
            f"[STRATEGIC] Phase={game_phase}, "
            f"Top moves: {sorted(strategic_scores.items(), key=lambda x: x[1], reverse=True)[:3]}"
        )
        
        return filtered_moves, strategic_scores
    
    def apply_commitment_boost(
        self,
        move_scores: Dict[str, float],
        last_decision: Optional[str],
        turns_in_current: int,
        game_state: Any
    ) -> Dict[str, float]:
        """
        Apply commitment heuristic to reduce indecision.
        
        Args:
            move_scores: Current move scores
            last_decision: Last selected move
            turns_in_current: Turns in current pokemon
            game_state: Battle state
            
        Returns:
            Adjusted scores
        """
        return self.commitment_heuristic.apply_commitment_boost(
            move_scores,
            last_decision,
            turns_in_current,
            game_state
        )


# Global instance for convenience
_strategic_layer = StrategicDecisionLayer()


def initialize_battle_strategy(battle_tag: str, team_data: List[Dict]) -> Tuple[TeamArchetype, Gameplan]:
    """
    Initialize strategic layer for a battle.
    
    Args:
        battle_tag: Battle identifier
        team_data: Team composition
        
    Returns:
        Tuple of (TeamArchetype, Gameplan)
    """
    return _strategic_layer.initialize_for_battle(battle_tag, team_data)


def get_battle_gameplan(battle_tag: str) -> Optional[Gameplan]:
    """
    Get gameplan for a battle.
    
    Args:
        battle_tag: Battle identifier
        
    Returns:
        Gameplan if available
    """
    return _strategic_layer.get_cached_gameplan(battle_tag)


def clear_battle_strategy(battle_tag: str):
    """
    Clear strategic data for a finished battle.
    
    Args:
        battle_tag: Battle identifier
    """
    _strategic_layer.clear_battle_cache(battle_tag)


def enhance_move_selection_strategic(
    available_moves: List[str],
    game_state: Any,
    battle_tag: str,
    last_decision: Optional[str] = None,
    turns_in_current: int = 0
) -> Tuple[List[str], Dict[str, float]]:
    """
    Apply strategic enhancements to move selection.
    
    Args:
        available_moves: Available moves
        game_state: Battle state
        battle_tag: Battle identifier
        last_decision: Last move
        turns_in_current: Turns in current pokemon
        
    Returns:
        Tuple of (filtered_moves, strategic_scores)
    """
    return _strategic_layer.enhance_move_selection(
        available_moves,
        game_state,
        battle_tag,
        last_decision,
        turns_in_current
    )


def apply_strategic_commitment(
    move_scores: Dict[str, float],
    last_decision: Optional[str],
    turns_in_current: int,
    game_state: Any
) -> Dict[str, float]:
    """
    Apply commitment heuristic.
    
    Args:
        move_scores: Move scores
        last_decision: Last move
        turns_in_current: Turns in current
        game_state: Battle state
        
    Returns:
        Adjusted scores
    """
    return _strategic_layer.apply_commitment_boost(
        move_scores,
        last_decision,
        turns_in_current,
        game_state
    )
