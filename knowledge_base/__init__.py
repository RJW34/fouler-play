"""
Fouler Play Knowledge Base

Structured domain knowledge loader for Pokemon battle decisions.
Inspired by Melee Replay Analyzer's knowledge base architecture.

Usage:
    from knowledge_base import KnowledgeBase
    
    kb = KnowledgeBase()
    move = kb.get_move("psychic")
    matchup = kb.get_matchup("psychic_vs_fighting")
    phase = kb.get_phase("early_game")
"""

import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class KnowledgeBase:
    """
    Load and access Pokemon battle knowledge from YAML files.
    
    Provides typed access to moves, abilities, items, matchups,
    strategies, and battle situations.
    """
    
    def __init__(self, base_path: Optional[Path] = None):
        if base_path is None:
            base_path = Path(__file__).parent
        self.base_path = Path(base_path)
        
        # Lazy-loaded caches
        self._moves: Optional[Dict[str, Any]] = None
        self._abilities: Optional[Dict[str, Any]] = None
        self._items: Optional[Dict[str, Any]] = None
        self._matchups: Optional[Dict[str, Any]] = None
        self._strategies: Optional[Dict[str, Any]] = None
        self._situations: Optional[Dict[str, Any]] = None
    
    def _load_yaml_dir(self, subdir: str) -> Dict[str, Any]:
        """Load all YAML files from a subdirectory into one dict."""
        combined = {}
        dir_path = self.base_path / subdir
        
        if not dir_path.exists():
            return combined
        
        for yaml_file in dir_path.glob("*.yaml"):
            with open(yaml_file, 'r') as f:
                data = yaml.safe_load(f)
                if data:
                    combined.update(data)
        
        return combined
    
    @property
    def moves(self) -> Dict[str, Any]:
        """Lazy-load move knowledge."""
        if self._moves is None:
            self._moves = self._load_yaml_dir("moves")
        return self._moves
    
    @property
    def abilities(self) -> Dict[str, Any]:
        """Lazy-load ability knowledge."""
        if self._abilities is None:
            self._abilities = self._load_yaml_dir("abilities")
        return self._abilities
    
    @property
    def items(self) -> Dict[str, Any]:
        """Lazy-load item knowledge."""
        if self._items is None:
            self._items = self._load_yaml_dir("items")
        return self._items
    
    @property
    def matchups(self) -> Dict[str, Any]:
        """Lazy-load matchup patterns."""
        if self._matchups is None:
            self._matchups = self._load_yaml_dir("matchups")
        return self._matchups
    
    @property
    def strategies(self) -> Dict[str, Any]:
        """Lazy-load strategy knowledge."""
        if self._strategies is None:
            self._strategies = self._load_yaml_dir("strategies")
        return self._strategies
    
    @property
    def situations(self) -> Dict[str, Any]:
        """Lazy-load situation patterns."""
        if self._situations is None:
            self._situations = self._load_yaml_dir("situations")
        return self._situations
    
    # Convenience methods
    
    def get_move(self, move_name: str) -> Optional[Dict[str, Any]]:
        """Get move data by name."""
        return self.moves.get(move_name)
    
    def get_ability(self, ability_name: str) -> Optional[Dict[str, Any]]:
        """Get ability data by name."""
        return self.abilities.get(ability_name)
    
    def get_item(self, item_name: str) -> Optional[Dict[str, Any]]:
        """Get item data by name."""
        return self.items.get(item_name)
    
    def get_matchup(self, matchup_key: str) -> Optional[Dict[str, Any]]:
        """
        Get matchup pattern by key.
        
        Example keys: "psychic_vs_fighting", "water_vs_fire"
        """
        return self.matchups.get(matchup_key)
    
    def get_strategy(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        """Get strategy data by name."""
        return self.strategies.get(strategy_name)
    
    def get_situation(self, situation_name: str) -> Optional[Dict[str, Any]]:
        """Get situation pattern by name."""
        return self.situations.get(situation_name)
    
    def find_moves_with_tag(self, tag: str) -> Dict[str, Any]:
        """Find all moves with a specific tag."""
        return {
            name: data
            for name, data in self.moves.items()
            if tag in data.get("tags", [])
        }
    
    def get_common_holders(self, move_name: str) -> list:
        """Get list of Pokemon that commonly use this move."""
        move_data = self.get_move(move_name)
        if move_data:
            return move_data.get("common_holders", [])
        return []
    
    def reload(self):
        """Force reload all knowledge from disk."""
        self._moves = None
        self._abilities = None
        self._items = None
        self._matchups = None
        self._strategies = None
        self._situations = None


# Singleton instance for easy importing
kb = KnowledgeBase()


# Convenience exports
def get_move(name: str) -> Optional[Dict[str, Any]]:
    """Get move data by name."""
    return kb.get_move(name)


def get_matchup(key: str) -> Optional[Dict[str, Any]]:
    """Get matchup pattern by key."""
    return kb.get_matchup(key)


def get_strategy(name: str) -> Optional[Dict[str, Any]]:
    """Get strategy data by name."""
    return kb.get_strategy(name)


__all__ = [
    "KnowledgeBase",
    "kb",
    "get_move",
    "get_matchup",
    "get_strategy",
]
