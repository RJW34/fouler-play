"""
Strategic Move Filter - Pre-eval filtering to enforce gameplan constraints.

Removes moves that contradict the gameplan BEFORE they reach evaluation.
Implements hard constraints and commitment heuristics.
"""

import logging
from typing import List, Dict, Any, Optional

from fp.gameplan_generator import Gameplan
from fp.helpers import normalize_name

logger = logging.getLogger(__name__)


class StrategicFilter:
    """Filters moves based on gameplan constraints."""
    
    def filter_moves_strategically(
        self,
        available_moves: List[str],
        game_state: Any,  # Battle object
        gameplan: Gameplan,
        turn_number: int
    ) -> List[str]:
        """
        Remove moves that contradict the gameplan BEFORE they reach eval.
        This is a hard constraint, not a penalty.
        
        Args:
            available_moves: List of available move strings
            game_state: Current battle state
            gameplan: Active gameplan
            turn_number: Current turn number
            
        Returns:
            Filtered list of moves
        """
        if not available_moves:
            return available_moves
        
        filtered = available_moves.copy()
        active_pokemon = getattr(game_state.user, "active", None)
        
        if not active_pokemon:
            return filtered
        
        active_name = normalize_name(active_pokemon.name)
        
        # RULE 1: Mandatory moves come first (early game)
        mandatory_filtered = self._filter_mandatory_moves(
            filtered, active_name, active_pokemon, game_state, gameplan, turn_number
        )
        if mandatory_filtered and len(mandatory_filtered) < len(filtered):
            logger.info(f"Mandatory move filter: {len(filtered)} -> {len(mandatory_filtered)} moves")
            filtered = mandatory_filtered
        
        # RULE 2: Protect critical Pokemon HP
        hp_filtered = self._filter_critical_pokemon_hp(
            filtered, active_name, active_pokemon, gameplan
        )
        if hp_filtered:
            filtered = hp_filtered
        
        # RULE 3: Prevent prohibited switches
        switch_filtered = self._filter_prohibited_switches(
            filtered, active_name, game_state, gameplan
        )
        if switch_filtered:
            filtered = switch_filtered
        
        # RULE 4: Excessive switching penalty
        budget_filtered = self._filter_switch_budget(
            filtered, game_state, gameplan
        )
        if budget_filtered:
            filtered = budget_filtered
        
        # Ensure we don't filter everything
        if not filtered:
            logger.warning("Strategic filter removed all moves - reverting to original")
            return available_moves
        
        return filtered
    
    def _filter_mandatory_moves(
        self,
        moves: List[str],
        active_name: str,
        active_pokemon: Any,
        game_state: Any,
        gameplan: Gameplan,
        turn_number: int
    ) -> List[str]:
        """Force mandatory moves if timing constraint is active."""
        # Check if this pokemon has mandatory moves
        if active_name not in gameplan.mandatory_moves:
            return moves
        
        mandatory = gameplan.mandatory_moves[active_name]
        
        # Check timing constraints
        forced_moves = []
        for move_name in mandatory:
            if move_name in gameplan.must_happen_by_turn:
                deadline = gameplan.must_happen_by_turn[move_name]
                
                # Only force if we're approaching deadline and move hasn't been used
                if turn_number <= deadline:
                    # Check if this hazard is already up
                    if move_name in ["stealthrock", "spikes", "toxicspikes"]:
                        if not self._has_hazard(game_state, move_name):
                            # Find matching move in available moves
                            for available_move in moves:
                                if self._move_matches(available_move, move_name):
                                    forced_moves.append(available_move)
        
        # If we have forced moves, return only those
        if forced_moves:
            return forced_moves
        
        return moves
    
    def _filter_critical_pokemon_hp(
        self,
        moves: List[str],
        active_name: str,
        active_pokemon: Any,
        gameplan: Gameplan
    ) -> List[str]:
        """Don't switch out critical Pokemon if below HP minimum."""
        if active_name not in gameplan.hp_minimums:
            return moves
        
        min_hp = gameplan.hp_minimums[active_name]
        current_hp_ratio = active_pokemon.hp / max(active_pokemon.max_hp, 1)
        
        # If below minimum, remove switches
        if current_hp_ratio < min_hp:
            non_switches = [m for m in moves if not m.startswith("switch ")]
            if non_switches:
                logger.info(
                    f"Critical Pokemon {active_name} at {current_hp_ratio:.1%} HP "
                    f"(min {min_hp:.1%}) - removing switches"
                )
                return non_switches
        
        return moves
    
    def _filter_prohibited_switches(
        self,
        moves: List[str],
        active_name: str,
        game_state: Any,
        gameplan: Gameplan
    ) -> List[str]:
        """Remove switches that are explicitly prohibited."""
        if not gameplan.prohibited_switches:
            return moves
        
        filtered = []
        for move in moves:
            if move.startswith("switch "):
                target = move.split(" ", 1)[1]
                target_normalized = normalize_name(target)
                
                # Check if this switch is prohibited
                is_prohibited = False
                for from_poke, to_poke, reason in gameplan.prohibited_switches:
                    if active_name == normalize_name(from_poke) and target_normalized == normalize_name(to_poke):
                        logger.info(f"Prohibited switch: {from_poke} -> {to_poke} ({reason})")
                        is_prohibited = True
                        break
                
                if not is_prohibited:
                    filtered.append(move)
            else:
                filtered.append(move)
        
        return filtered if filtered else moves
    
    def _filter_switch_budget(
        self,
        moves: List[str],
        game_state: Any,
        gameplan: Gameplan
    ) -> List[str]:
        """Penalize excessive switching."""
        # Count recent switches
        recent_switches = self._count_recent_switches(game_state)
        
        # If over budget, remove all switches except critical
        if recent_switches > gameplan.switch_budget:
            non_switches = [m for m in moves if not m.startswith("switch ")]
            if non_switches:
                logger.info(
                    f"Switch budget exceeded ({recent_switches} > {gameplan.switch_budget}) - "
                    f"removing switches"
                )
                return non_switches
        
        return moves
    
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
        except Exception as e:
            logger.debug(f"Error checking hazard {hazard_name}: {e}")
            return False
    
    def _move_matches(self, available_move: str, target_move: str) -> bool:
        """Check if an available move matches the target move name."""
        # Normalize both
        available_normalized = normalize_name(available_move)
        target_normalized = normalize_name(target_move)
        
        return available_normalized == target_normalized
    
    def _count_recent_switches(self, game_state: Any) -> int:
        """Count switches in the last 8 turns."""
        try:
            # Look at battle history if available
            turn = getattr(game_state, "turn", 0)
            
            # Simple heuristic: check user's team for pokemon that have been active
            # This is a simplified version - full implementation would track switch history
            # For now, return 0 to not block switches until proper tracking is added
            return 0
        except Exception:
            return 0


class CommitmentHeuristic:
    """Reduces indecision by encouraging commitment to chosen strategies."""
    
    def apply_commitment_boost(
        self,
        move_scores: Dict[str, float],
        last_decision: Optional[str],
        turns_in_current_pokemon: int,
        game_state: Any
    ) -> Dict[str, float]:
        """
        Boost non-switch moves if we just committed to staying in.
        
        Args:
            move_scores: Dict of move -> score
            last_decision: Last selected move
            turns_in_current_pokemon: How many turns current pokemon has been active
            game_state: Battle state
            
        Returns:
            Adjusted move scores
        """
        if not last_decision or not move_scores:
            return move_scores
        
        # If we're in an attacking pokemon and it's been <2 turns, boost attacks
        if not last_decision.startswith("switch ") and turns_in_current_pokemon < 2:
            adjusted = {}
            for move, score in move_scores.items():
                if not move.startswith("switch "):
                    # Boost non-switches by 15%
                    adjusted[move] = score * 1.15
                else:
                    # Penalize switches by 15%
                    adjusted[move] = score * 0.85
            
            logger.debug("Applied commitment boost to non-switches")
            return adjusted
        
        return move_scores


def filter_moves_with_gameplan(
    available_moves: List[str],
    game_state: Any,
    gameplan: Gameplan,
    turn_number: int
) -> List[str]:
    """
    Convenience function to filter moves based on gameplan.
    
    Args:
        available_moves: Available move strings
        game_state: Battle state
        gameplan: Active gameplan
        turn_number: Current turn
        
    Returns:
        Filtered moves
    """
    filter_engine = StrategicFilter()
    return filter_engine.filter_moves_strategically(
        available_moves, game_state, gameplan, turn_number
    )


def apply_commitment_heuristic(
    move_scores: Dict[str, float],
    last_decision: Optional[str],
    turns_in_current: int,
    game_state: Any
) -> Dict[str, float]:
    """
    Convenience function to apply commitment heuristic.
    
    Args:
        move_scores: Move scores
        last_decision: Last move selected
        turns_in_current: Turns in current pokemon
        game_state: Battle state
        
    Returns:
        Adjusted scores
    """
    heuristic = CommitmentHeuristic()
    return heuristic.apply_commitment_boost(
        move_scores, last_decision, turns_in_current, game_state
    )
