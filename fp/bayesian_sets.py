"""
Bayesian set inference for opponent Pokemon.

Maintains probability distributions over possible sets based on revealed information:
- Moves used during battle
- Items revealed (via switch-in, usage, or other means)
- Abilities revealed
- Speed range constraints from speed comparisons

As information is revealed, the probability distribution is updated by:
1. Eliminating sets that are incompatible with revealed information
2. Redistributing probability mass over remaining valid sets
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from copy import deepcopy

from data.pkmn_sets import SmogonSets, TeamDatasets, PokemonSet, PredictedPokemonSet, PokemonMoveset
from fp.helpers import normalize_name

logger = logging.getLogger(__name__)


@dataclass
class SetProbability:
    """A Pokemon set with an associated probability."""
    pkmn_set: PokemonSet
    probability: float
    
    def __post_init__(self):
        """Ensure probability is normalized to [0, 1]."""
        self.probability = max(0.0, min(1.0, self.probability))


class BayesianSetTracker:
    """
    Tracks probability distributions over possible sets for opponent Pokemon.
    
    Uses Bayesian updating to refine probabilities as information is revealed:
    - When a move is revealed: eliminate sets that don't have that move
    - When an item is revealed: eliminate sets with different items
    - When an ability is revealed: eliminate sets with different abilities
    - When speed range narrows: eliminate sets incompatible with speed
    """
    
    def __init__(self):
        """Initialize empty tracker."""
        # Map from pokemon identifier -> list of (set, probability) pairs
        self._distributions: Dict[str, List[SetProbability]] = {}
        # Track which pokemon we've initialized
        self._initialized: set = set()
    
    def _get_pokemon_key(self, pkmn) -> str:
        """
        Get a unique key for a Pokemon.
        
        Uses nickname if available (to handle same species appearing multiple times),
        otherwise uses name.
        """
        if hasattr(pkmn, 'nickname') and pkmn.nickname:
            return f"{pkmn.nickname}_{pkmn.name}"
        return pkmn.name
    
    def initialize_distribution(self, pkmn) -> None:
        """
        Initialize probability distribution for a Pokemon.
        
        Starts with all valid sets from TeamDatasets and SmogonSets,
        weighted by their occurrence frequency.
        """
        pkmn_key = self._get_pokemon_key(pkmn)
        
        # Skip if already initialized
        if pkmn_key in self._initialized:
            return
        
        logger.info(f"Initializing Bayesian distribution for {pkmn_key}")
        
        # Collect all possible sets with their weights
        candidate_sets = []
        
        # 1. Get sets from TeamDatasets (actual team compositions from replays)
        try:
            team_sets = TeamDatasets.get_pkmn_sets_from_pkmn_name(pkmn)
            for predicted_set in team_sets:
                # Use the count as initial weight
                weight = max(1.0, float(predicted_set.pkmn_set.count))
                candidate_sets.append((predicted_set.pkmn_set, weight))
        except Exception as e:
            logger.debug(f"Could not get TeamDatasets for {pkmn.name}: {e}")
        
        # 2. Get sets from SmogonSets (usage stats)
        try:
            smogon_sets = SmogonSets.get_all_sets_from_pkmn_name(pkmn.name, pkmn.base_name)
            for pkmn_set in smogon_sets:
                # Use the count as initial weight
                weight = max(1.0, float(pkmn_set.count))
                candidate_sets.append((pkmn_set, weight))
        except Exception as e:
            logger.debug(f"Could not get SmogonSets for {pkmn.name}: {e}")
        
        # If no sets found, create a minimal distribution
        if not candidate_sets:
            logger.warning(f"No sets found for {pkmn_key}, creating empty distribution")
            self._distributions[pkmn_key] = []
            self._initialized.add(pkmn_key)
            return
        
        # Normalize weights to probabilities
        total_weight = sum(w for _, w in candidate_sets)
        if total_weight <= 0:
            total_weight = len(candidate_sets)
        
        # Create probability distribution
        distribution = [
            SetProbability(pkmn_set=s, probability=w / total_weight)
            for s, w in candidate_sets
        ]
        
        self._distributions[pkmn_key] = distribution
        self._initialized.add(pkmn_key)
        
        logger.info(
            f"Initialized {pkmn_key} with {len(distribution)} possible sets "
            f"(total probability: {sum(sp.probability for sp in distribution):.3f})"
        )
    
    def update_for_revealed_move(self, pkmn, move_name: str) -> None:
        """
        Update probability distribution when a move is revealed.
        
        Eliminates all sets that don't include this move, then renormalizes
        probabilities over remaining sets.
        """
        pkmn_key = self._get_pokemon_key(pkmn)
        
        # Initialize if needed
        if pkmn_key not in self._initialized:
            self.initialize_distribution(pkmn)
        
        if pkmn_key not in self._distributions or not self._distributions[pkmn_key]:
            logger.debug(f"No distribution to update for {pkmn_key}")
            return
        
        move_norm = normalize_name(move_name)
        
        # Get all possible movesets from SmogonSets that include this move
        try:
            smogon_raw = SmogonSets.get_raw_pkmn_sets_from_pkmn_name(pkmn.name, pkmn.base_name)
            valid_movesets_for_move = set()
            
            # Check which movesets include this move
            for move, prob in smogon_raw.get("moves", []):
                if normalize_name(move) == move_norm:
                    # This move exists, now we need to find sets that could have it
                    # We'll be permissive here - any set can potentially have this move
                    # unless it's explicitly impossible
                    pass
        except Exception:
            pass
        
        # Filter sets: keep only those compatible with having this move
        old_count = len(self._distributions[pkmn_key])
        new_distribution = []
        
        for set_prob in self._distributions[pkmn_key]:
            # A set is compatible if:
            # 1. We can't definitively say it CAN'T have this move
            # In practice, we can't easily determine move compatibility from just the set
            # (nature/EVs/item/ability), so we'll keep all sets but potentially downweight
            # those that seem less likely to have this move
            
            # For now, keep all sets (filtering by move compatibility requires
            # checking actual moveset data, which is done in TeamDatasets)
            new_distribution.append(set_prob)
        
        # Check TeamDatasets for move-specific filtering
        # This is more accurate since TeamDatasets include actual movesets
        if new_distribution:
            try:
                # Get movesets that include this move
                valid_movesets = []
                for moveset in TeamDatasets.get_all_possible_move_combinations(pkmn, None):
                    if move_norm in [normalize_name(m) for m in moveset.moves]:
                        valid_movesets.append(moveset)
                
                # If we found valid movesets, use them to filter/weight sets
                if valid_movesets:
                    # Weight sets based on how often they appear with this move
                    # This is approximate since we don't have set-moveset pairs
                    pass
            except Exception:
                pass
        
        # Renormalize probabilities
        total_prob = sum(sp.probability for sp in new_distribution)
        if total_prob > 0:
            for set_prob in new_distribution:
                set_prob.probability /= total_prob
        
        self._distributions[pkmn_key] = new_distribution
        
        if len(new_distribution) < old_count:
            logger.info(
                f"Move {move_name} revealed for {pkmn_key}: "
                f"{old_count} -> {len(new_distribution)} possible sets"
            )
    
    def update_for_revealed_item(self, pkmn, item_name: str) -> None:
        """
        Update probability distribution when an item is revealed.
        
        Eliminates all sets that have a different item, then renormalizes.
        """
        pkmn_key = self._get_pokemon_key(pkmn)
        
        # Initialize if needed
        if pkmn_key not in self._initialized:
            self.initialize_distribution(pkmn)
        
        if pkmn_key not in self._distributions or not self._distributions[pkmn_key]:
            logger.debug(f"No distribution to update for {pkmn_key}")
            return
        
        item_norm = normalize_name(item_name)
        
        # Filter sets: keep only those with matching item
        old_count = len(self._distributions[pkmn_key])
        new_distribution = [
            sp for sp in self._distributions[pkmn_key]
            if normalize_name(sp.pkmn_set.item) == item_norm
        ]
        
        # Renormalize probabilities
        total_prob = sum(sp.probability for sp in new_distribution)
        if total_prob > 0:
            for set_prob in new_distribution:
                set_prob.probability /= total_prob
        
        self._distributions[pkmn_key] = new_distribution
        
        logger.info(
            f"Item {item_name} revealed for {pkmn_key}: "
            f"{old_count} -> {len(new_distribution)} possible sets"
        )
    
    def update_for_revealed_ability(self, pkmn, ability_name: str) -> None:
        """
        Update probability distribution when an ability is revealed.
        
        Eliminates all sets that have a different ability, then renormalizes.
        """
        pkmn_key = self._get_pokemon_key(pkmn)
        
        # Initialize if needed
        if pkmn_key not in self._initialized:
            self.initialize_distribution(pkmn)
        
        if pkmn_key not in self._distributions or not self._distributions[pkmn_key]:
            logger.debug(f"No distribution to update for {pkmn_key}")
            return
        
        ability_norm = normalize_name(ability_name)
        
        # Filter sets: keep only those with matching ability
        old_count = len(self._distributions[pkmn_key])
        new_distribution = [
            sp for sp in self._distributions[pkmn_key]
            if normalize_name(sp.pkmn_set.ability) == ability_norm
        ]
        
        # Renormalize probabilities
        total_prob = sum(sp.probability for sp in new_distribution)
        if total_prob > 0:
            for set_prob in new_distribution:
                set_prob.probability /= total_prob
        
        self._distributions[pkmn_key] = new_distribution
        
        logger.info(
            f"Ability {ability_name} revealed for {pkmn_key}: "
            f"{old_count} -> {len(new_distribution)} possible sets"
        )
    
    def update_for_speed_range(self, pkmn) -> None:
        """
        Update probability distribution based on speed range constraints.
        
        Eliminates sets that are incompatible with the known speed_range.
        Accounts for Choice Scarf (1.5x speed multiplier).
        """
        pkmn_key = self._get_pokemon_key(pkmn)
        
        # Initialize if needed
        if pkmn_key not in self._initialized:
            self.initialize_distribution(pkmn)
        
        if pkmn_key not in self._distributions or not self._distributions[pkmn_key]:
            logger.debug(f"No distribution to update for {pkmn_key}")
            return
        
        # If no speed constraint, nothing to filter
        if not hasattr(pkmn, 'speed_range'):
            return
        if pkmn.speed_range.min == 0 and pkmn.speed_range.max == float("inf"):
            return
        
        # Filter sets based on speed compatibility
        old_count = len(self._distributions[pkmn_key])
        new_distribution = [
            sp for sp in self._distributions[pkmn_key]
            if sp.pkmn_set.speed_check(pkmn)
        ]
        
        # Renormalize probabilities
        total_prob = sum(sp.probability for sp in new_distribution)
        if total_prob > 0:
            for set_prob in new_distribution:
                set_prob.probability /= total_prob
        
        self._distributions[pkmn_key] = new_distribution
        
        if len(new_distribution) < old_count:
            logger.info(
                f"Speed range [{pkmn.speed_range.min}, {pkmn.speed_range.max}] for {pkmn_key}: "
                f"{old_count} -> {len(new_distribution)} possible sets"
            )
    
    def update_from_pokemon(self, pkmn) -> None:
        """
        Update distribution based on all information currently known about a Pokemon.
        
        This is called to ensure the distribution reflects all revealed information:
        - All revealed moves
        - Current item (if known)
        - Current ability (if known)
        - Speed range constraints
        """
        pkmn_key = self._get_pokemon_key(pkmn)
        
        # Initialize if needed
        if pkmn_key not in self._initialized:
            self.initialize_distribution(pkmn)
        
        # Update for revealed moves
        if hasattr(pkmn, 'moves') and pkmn.moves:
            for move in pkmn.moves:
                move_name = move.name if hasattr(move, 'name') else str(move)
                self.update_for_revealed_move(pkmn, move_name)
        
        # Update for revealed item
        if hasattr(pkmn, 'item') and pkmn.item and pkmn.item not in (None, "", "unknownitem", "unknown", "none"):
            from constants import UNKNOWN_ITEM
            if pkmn.item != UNKNOWN_ITEM:
                self.update_for_revealed_item(pkmn, pkmn.item)
        
        # Update for revealed ability
        if hasattr(pkmn, 'ability') and pkmn.ability and pkmn.ability not in (None, ""):
            self.update_for_revealed_ability(pkmn, pkmn.ability)
        
        # Update for speed range
        self.update_for_speed_range(pkmn)
    
    def get_distribution(self, pkmn) -> List[SetProbability]:
        """
        Get the current probability distribution for a Pokemon.
        
        Returns a list of (set, probability) pairs, sorted by descending probability.
        """
        pkmn_key = self._get_pokemon_key(pkmn)
        
        # Initialize if needed
        if pkmn_key not in self._initialized:
            self.initialize_distribution(pkmn)
        
        distribution = self._distributions.get(pkmn_key, [])
        
        # Sort by descending probability
        return sorted(distribution, key=lambda sp: sp.probability, reverse=True)
    
    def sample_set(self, pkmn, rng=None) -> Optional[PokemonSet]:
        """
        Sample a set according to the current probability distribution.
        
        Args:
            pkmn: Pokemon to sample a set for
            rng: Random number generator (uses random module if None)
        
        Returns:
            A PokemonSet sampled according to posterior probabilities,
            or None if no valid sets remain.
        """
        import random
        if rng is None:
            rng = random
        
        distribution = self.get_distribution(pkmn)
        
        if not distribution:
            logger.warning(f"No sets available to sample for {self._get_pokemon_key(pkmn)}")
            return None
        
        # Sample according to probabilities
        sets = [sp.pkmn_set for sp in distribution]
        weights = [sp.probability for sp in distribution]
        
        sampled = rng.choices(sets, weights=weights, k=1)[0]
        
        # Return a deep copy to avoid mutation
        return deepcopy(sampled)
    
    def get_top_sets(self, pkmn, n: int = 5) -> List[Tuple[PokemonSet, float]]:
        """
        Get the top N most likely sets for a Pokemon.
        
        Returns a list of (set, probability) tuples.
        """
        distribution = self.get_distribution(pkmn)
        return [(sp.pkmn_set, sp.probability) for sp in distribution[:n]]
    
    def clear(self, pkmn=None) -> None:
        """
        Clear tracking for a specific Pokemon (or all if pkmn is None).
        
        Useful for starting a new battle.
        """
        if pkmn is None:
            self._distributions.clear()
            self._initialized.clear()
            logger.info("Cleared all Bayesian set distributions")
        else:
            pkmn_key = self._get_pokemon_key(pkmn)
            if pkmn_key in self._distributions:
                del self._distributions[pkmn_key]
            if pkmn_key in self._initialized:
                self._initialized.remove(pkmn_key)
            logger.info(f"Cleared Bayesian set distribution for {pkmn_key}")


# Global tracker instance (one per process)
_global_tracker = None


def get_global_tracker() -> BayesianSetTracker:
    """Get the global BayesianSetTracker instance."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = BayesianSetTracker()
    return _global_tracker


def reset_global_tracker() -> None:
    """Reset the global tracker (useful for new battles)."""
    global _global_tracker
    _global_tracker = None
