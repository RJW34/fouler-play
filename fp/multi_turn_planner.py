"""
Multi-Turn Planner - Evaluates move sequences over 3-turn horizon.

Instead of evaluating moves in isolation, evaluates the strategic value
of multi-turn sequences aligned with the gameplan.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from fp.gameplan_generator import Gameplan
from fp.helpers import normalize_name

logger = logging.getLogger(__name__)


class GamePhase(str, Enum):
    """Game phase classification."""
    EARLY = "early"
    MID = "mid"
    LATE = "late"


class MultiTurnPlanner:
    """Plans and evaluates multi-turn move sequences."""
    
    def get_game_phase(self, game_state: Any) -> GamePhase:
        """
        Determine current game phase based on total HP remaining.
        
        Args:
            game_state: Battle object
            
        Returns:
            GamePhase enum
        """
        try:
            # Calculate total HP across all pokemon
            total_hp = 0
            total_max_hp = 0
            
            # User's team
            if hasattr(game_state.user, "active") and game_state.user.active:
                total_hp += game_state.user.active.hp
                total_max_hp += game_state.user.active.max_hp
            
            if hasattr(game_state.user, "reserve"):
                for pkmn in game_state.user.reserve:
                    total_hp += pkmn.hp
                    total_max_hp += pkmn.max_hp
            
            # Opponent's team
            if hasattr(game_state.opponent, "active") and game_state.opponent.active:
                total_hp += game_state.opponent.active.hp
                total_max_hp += game_state.opponent.active.max_hp
            
            if hasattr(game_state.opponent, "reserve"):
                for pkmn in game_state.opponent.reserve:
                    total_hp += pkmn.hp
                    total_max_hp += pkmn.max_hp
            
            if total_max_hp == 0:
                return GamePhase.EARLY
            
            avg_hp_ratio = total_hp / total_max_hp
            
            # Phase thresholds
            if avg_hp_ratio > 0.7:
                return GamePhase.EARLY
            elif avg_hp_ratio > 0.4:
                return GamePhase.MID
            else:
                return GamePhase.LATE
        
        except Exception as e:
            logger.debug(f"Error calculating game phase: {e}")
            return GamePhase.EARLY
    
    def evaluate_sequence(
        self,
        current_state: Any,
        candidate_move: str,
        gameplan: Gameplan,
        depth: int = 3
    ) -> float:
        """
        Evaluate the strategic value of a move sequence.
        
        Instead of evaluating the move in isolation, consider what paths
        it opens up over the next few turns.
        
        Args:
            current_state: Current battle state
            candidate_move: Move to evaluate
            gameplan: Active gameplan
            depth: How many turns to look ahead (default 3)
            
        Returns:
            Sequence alignment score (0.0-1.0)
        """
        # Get current turn
        turn_number = getattr(current_state, "turn", 1)
        game_phase = self.get_game_phase(current_state)
        
        # Calculate alignment score for this move
        alignment = self.alignment_score(
            candidate_move,
            gameplan,
            turn_number,
            game_phase,
            current_state
        )
        
        # For simplicity, we'll use a heuristic approach rather than full simulation
        # Full simulation would require cloning battle state and stepping forward
        # This version uses strategic heuristics to estimate sequence value
        
        sequence_value = alignment
        
        # Discount future turns
        discount_factor = 0.5
        
        # Estimate follow-up value based on gameplan
        for turn_ahead in range(1, min(depth, 3) + 1):
            future_turn = turn_number + turn_ahead
            
            # Estimate what we'd want to do in future turns
            future_alignment = self._estimate_future_alignment(
                candidate_move,
                gameplan,
                future_turn,
                game_phase,
                current_state
            )
            
            sequence_value += future_alignment * (discount_factor ** turn_ahead)
        
        # Normalize to 0-1 range
        max_possible = 1.0 + sum(discount_factor ** i for i in range(1, depth + 1))
        normalized = sequence_value / max_possible
        
        return max(0.0, min(1.0, normalized))
    
    def alignment_score(
        self,
        move: str,
        gameplan: Gameplan,
        turn_number: int,
        game_phase: GamePhase,
        game_state: Any
    ) -> float:
        """
        How well does this move align with the gameplan?
        
        Args:
            move: Move string
            gameplan: Active gameplan
            turn_number: Current turn
            game_phase: Current game phase
            game_state: Battle state
            
        Returns:
            Alignment score (0.0-1.0)
        """
        move_normalized = normalize_name(move)
        archetype = gameplan.archetype
        
        # Base score
        score = 0.5
        
        # Check if it's a switch
        is_switch = move.startswith("switch ")
        
        # HAZARD STACK archetype
        if "Hazard" in archetype:
            # Early game: Hazard setup is critical
            if game_phase == GamePhase.EARLY:
                if move_normalized in ["stealthrock", "spikes", "toxicspikes"]:
                    # Check if hazard is already up
                    if not self._has_hazard(game_state, move_normalized):
                        score = 1.0  # Maximum priority
                    else:
                        score = 0.3  # Already set
                elif move_normalized in ["recover", "roost", "slackoff", "wish"]:
                    score = 0.7  # Good to maintain HP
                elif is_switch:
                    score = 0.5  # Moderate - positioning
                else:
                    score = 0.6  # Offensive moves okay
            
            # Mid game: Maintain hazards, wall
            elif game_phase == GamePhase.MID:
                if move_normalized in ["recover", "roost", "slackoff", "wish"]:
                    score = 0.9  # High priority
                elif move_normalized in ["toxic", "willowisp"]:
                    score = 0.8  # Status spreading good
                elif is_switch:
                    score = 0.6  # Positioning still important
                else:
                    score = 0.7  # Chip damage
            
            # Late game: Execute finisher
            else:  # LATE
                if is_switch:
                    score = 0.4  # Less switching
                elif move_normalized in ["recover", "roost"]:
                    score = 0.7  # Still maintain
                else:
                    score = 0.8  # Offensive moves to close out
        
        # STALL CORE archetype
        elif "Stall" in archetype:
            if game_phase == GamePhase.EARLY:
                if move_normalized in ["toxic", "willowisp", "stealthrock"]:
                    score = 0.9  # Setup status
                elif move_normalized in ["recover", "roost", "rest"]:
                    score = 0.8
                elif is_switch:
                    score = 0.7  # Positioning
                else:
                    score = 0.5
            
            elif game_phase == GamePhase.MID:
                if move_normalized in ["recover", "roost", "rest", "softboiled"]:
                    score = 1.0  # Maximum priority - survive
                elif move_normalized == "protect":
                    score = 0.9  # Scout and stall
                elif is_switch:
                    score = 0.7
                else:
                    score = 0.6
            
            else:  # LATE
                if move_normalized in ["recover", "roost"]:
                    score = 0.9  # Still survive
                elif not is_switch:
                    score = 0.7  # Start finishing
                else:
                    score = 0.5
        
        # PIVOT archetype
        elif "Pivot" in archetype:
            if move_normalized in ["uturn", "voltswitch", "partingshot", "flipturn"]:
                if game_phase == GamePhase.EARLY or game_phase == GamePhase.MID:
                    score = 0.9  # High priority
                else:
                    score = 0.6  # Less important late
            elif is_switch:
                score = 0.7  # Switching is part of strategy
            else:
                if game_phase == GamePhase.LATE:
                    score = 0.8  # More offensive late
                else:
                    score = 0.6
        
        # SETUP SWEEPER archetype
        elif "Setup" in archetype:
            # Check if this is a setup move
            is_setup = move_normalized in [
                "swordsdance", "nastyplot", "dragondance", "calmmind",
                "shellsmash", "quiverdance", "bulkup"
            ]
            
            if game_phase == GamePhase.EARLY:
                if is_setup:
                    score = 0.4  # Too early, risky
                elif is_switch:
                    score = 0.7  # Positioning
                else:
                    score = 0.8  # Wear down threats
            
            elif game_phase == GamePhase.MID:
                if is_setup:
                    score = 0.95  # Perfect time
                elif move_normalized in ["recover", "roost"]:
                    score = 0.8  # Maintain sweeper HP
                else:
                    score = 0.7
            
            else:  # LATE
                if not is_switch:
                    score = 0.9  # Sweep time
                else:
                    score = 0.5
        
        # HYPER OFFENSE archetype
        elif "Offense" in archetype or "HyperOffense" in archetype:
            if is_switch:
                score = 0.4  # Minimize switches
            elif move_normalized in ["recover", "roost"]:
                score = 0.5  # Not the focus
            else:
                score = 0.9  # Always attack
        
        # BALANCED archetype (default)
        else:
            score = 0.6  # Neutral
        
        return score
    
    def _estimate_future_alignment(
        self,
        current_move: str,
        gameplan: Gameplan,
        future_turn: int,
        game_phase: GamePhase,
        game_state: Any
    ) -> float:
        """
        Estimate strategic value of future turns based on current move.
        
        This is a heuristic - we don't actually simulate, just estimate
        whether the current move sets up good future options.
        """
        # Base future value
        future_value = 0.5
        
        archetype = gameplan.archetype
        current_normalized = normalize_name(current_move)
        
        # If we're setting hazards now, future turns are better
        if current_normalized in ["stealthrock", "spikes", "toxicspikes"]:
            if "Hazard" in archetype:
                future_value = 0.8  # Hazards enable future chip
        
        # If we're pivoting, we maintain momentum
        if current_normalized in ["uturn", "voltswitch", "partingshot"]:
            if "Pivot" in archetype:
                future_value = 0.7  # Maintains cycle
        
        # If we're setting up, future is sweep time
        if current_normalized in ["swordsdance", "nastyplot", "dragondance"]:
            if "Setup" in archetype and game_phase == GamePhase.MID:
                future_value = 0.9  # Setup enables sweep
        
        # If we're recovering, we extend longevity
        if current_normalized in ["recover", "roost", "rest"]:
            if "Stall" in archetype:
                future_value = 0.75  # Enables more stalling
        
        return future_value
    
    def eval_position_with_gameplan(
        self,
        game_state: Any,
        gameplan: Gameplan,
        game_phase: GamePhase,
        base_eval: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Adjust evaluation scores based on game phase and gameplan.
        
        Args:
            game_state: Battle state
            gameplan: Active gameplan
            game_phase: Current game phase
            base_eval: Base evaluation scores {move: score}
            
        Returns:
            Phase-adjusted evaluation scores
        """
        adjusted = {}
        active_pokemon = getattr(game_state.user, "active", None)
        
        if not active_pokemon:
            return base_eval
        
        active_name = normalize_name(active_pokemon.name)
        turn_number = getattr(game_state, "turn", 1)
        
        for move, score in base_eval.items():
            move_normalized = normalize_name(move)
            
            # Phase-specific adjustments
            multiplier = 1.0
            
            # Early game: Boost setup moves
            if game_phase == GamePhase.EARLY:
                # Check if this pokemon has mandatory moves
                if active_name in gameplan.mandatory_moves:
                    if move_normalized in [normalize_name(m) for m in gameplan.mandatory_moves[active_name]]:
                        multiplier = 1.5  # Boost mandatory moves
            
            # Mid game: Neutral - existing eval is good
            elif game_phase == GamePhase.MID:
                pass  # No special adjustments
            
            # Late game: Boost finisher execution
            elif game_phase == GamePhase.LATE:
                if active_name in gameplan.critical_pokemon:
                    # Boost offensive moves
                    if not move.startswith("switch ") and move_normalized not in ["recover", "roost", "protect"]:
                        multiplier = 1.2
            
            adjusted[move] = score * multiplier
        
        return adjusted
    
    def _has_hazard(self, game_state: Any, hazard_name: str) -> bool:
        """Check if a hazard is already set."""
        try:
            opponent_side = getattr(game_state.opponent, "side_conditions", {})
            
            if hazard_name == "stealthrock":
                return "stealthrock" in opponent_side
            elif hazard_name == "spikes":
                return "spikes" in opponent_side
            elif hazard_name == "toxicspikes":
                return "toxicspikes" in opponent_side
            
            return False
        except Exception:
            return False


def get_game_phase_from_state(game_state: Any) -> GamePhase:
    """
    Convenience function to get game phase.
    
    Args:
        game_state: Battle state
        
    Returns:
        GamePhase enum
    """
    planner = MultiTurnPlanner()
    return planner.get_game_phase(game_state)


def evaluate_move_sequence(
    game_state: Any,
    candidate_move: str,
    gameplan: Gameplan,
    depth: int = 3
) -> float:
    """
    Convenience function to evaluate a move sequence.
    
    Args:
        game_state: Battle state
        candidate_move: Move to evaluate
        gameplan: Active gameplan
        depth: Lookahead depth
        
    Returns:
        Sequence alignment score (0-1)
    """
    planner = MultiTurnPlanner()
    return planner.evaluate_sequence(game_state, candidate_move, gameplan, depth)
