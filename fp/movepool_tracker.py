"""
Movepool Tracker - Learn Pokemon threat categories from actual battle data

Tracks which moves each Pokemon actually uses in battles to classify:
- Physical-only threats (Gliscor, Garchomp, Great Tusk)
- Special-only threats (Gholdengo, Heatran, Primarina)
- Mixed threats (Dragapult, Iron Valiant, Zamazenta)

This solves the "Gliscor special bulk" problem - bot won't value irrelevant defensive stats.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Set, List, Optional
from collections import defaultdict
from dataclasses import dataclass, asdict
from enum import Enum

from data import all_move_json

logger = logging.getLogger(__name__)


class ThreatCategory(Enum):
    """Pokemon threat classification based on observed moves"""
    PHYSICAL_ONLY = "physical_only"  # Only uses physical attacks
    SPECIAL_ONLY = "special_only"    # Only uses special attacks
    MIXED = "mixed"                   # Uses both physical and special
    STATUS_ONLY = "status_only"       # Only uses status moves (rare, but exists)
    UNKNOWN = "unknown"               # Not enough data yet


@dataclass
class MovepoolData:
    """Movepool statistics for a specific Pokemon"""
    pokemon_name: str
    physical_moves: Set[str]
    special_moves: Set[str]
    status_moves: Set[str]
    times_seen: int  # How many battles we've observed this Pokemon
    
    @property
    def threat_category(self) -> ThreatCategory:
        """Classify threat based on observed moves"""
        has_physical = len(self.physical_moves) > 0
        has_special = len(self.special_moves) > 0
        has_status = len(self.status_moves) > 0
        
        if has_physical and has_special:
            return ThreatCategory.MIXED
        elif has_physical:
            return ThreatCategory.PHYSICAL_ONLY
        elif has_special:
            return ThreatCategory.SPECIAL_ONLY
        elif has_status:
            return ThreatCategory.STATUS_ONLY
        else:
            return ThreatCategory.UNKNOWN
    
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict"""
        return {
            "pokemon_name": self.pokemon_name,
            "physical_moves": list(self.physical_moves),
            "special_moves": list(self.special_moves),
            "status_moves": list(self.status_moves),
            "times_seen": self.times_seen,
            "threat_category": self.threat_category.value
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "MovepoolData":
        """Deserialize from JSON dict"""
        return cls(
            pokemon_name=data["pokemon_name"],
            physical_moves=set(data["physical_moves"]),
            special_moves=set(data["special_moves"]),
            status_moves=set(data["status_moves"]),
            times_seen=data["times_seen"]
        )


class MovepoolTracker:
    """
    Tracks movepool usage across battles to learn Pokemon threat categories
    
    Usage:
        tracker = MovepoolTracker()
        
        # During battle, record moves as they're used
        tracker.record_move("gliscor", "earthquake")
        tracker.record_move("gliscor", "toxic")
        
        # Check threat category
        category = tracker.get_threat_category("gliscor")
        # Returns: ThreatCategory.PHYSICAL_ONLY
        
        # Persist to disk
        tracker.save()
    """
    
    def __init__(self, data_file: Path = None):
        self.data_file = data_file or Path("fp/data/movepool_data.json")
        self.movepool_db: Dict[str, MovepoolData] = {}
        self.move_categories = self._load_move_categories()
        self._load()
    
    def _load_move_categories(self) -> Dict[str, str]:
        """
        Load move category data from data/all_move_json.json
        Returns dict: {move_name: category} where category is "physical", "special", or "status"
        """
        categories = {}
        for move_name, move_data in all_move_json.items():
            category = move_data.get("category", "status")
            categories[move_name] = category
        return categories
    
    def _load(self):
        """Load existing movepool data from disk"""
        if not self.data_file.exists():
            logger.info(f"No existing movepool data at {self.data_file}, starting fresh")
            return
        
        try:
            with open(self.data_file, 'r') as f:
                data = json.load(f)
                for pokemon_name, pokemon_data in data.items():
                    self.movepool_db[pokemon_name] = MovepoolData.from_dict(pokemon_data)
            logger.info(f"Loaded movepool data for {len(self.movepool_db)} Pokemon")
        except Exception as e:
            logger.error(f"Failed to load movepool data: {e}")
    
    def save(self):
        """Save movepool data to disk"""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            pokemon_name: movepool.to_dict()
            for pokemon_name, movepool in self.movepool_db.items()
        }
        
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"Saved movepool data for {len(self.movepool_db)} Pokemon to {self.data_file}")
    
    def record_move(self, pokemon_name: str, move_name: str):
        """
        Record a move usage by a Pokemon
        
        Args:
            pokemon_name: Normalized Pokemon name (e.g., "gliscor", "landorustherian")
            move_name: Normalized move name (e.g., "earthquake", "toxic")
        """
        # Get or create movepool entry
        if pokemon_name not in self.movepool_db:
            self.movepool_db[pokemon_name] = MovepoolData(
                pokemon_name=pokemon_name,
                physical_moves=set(),
                special_moves=set(),
                status_moves=set(),
                times_seen=0
            )
        
        movepool = self.movepool_db[pokemon_name]
        
        # Categorize the move
        move_category = self.move_categories.get(move_name, "status")
        
        if move_category == "physical":
            movepool.physical_moves.add(move_name)
        elif move_category == "special":
            movepool.special_moves.add(move_name)
        else:
            movepool.status_moves.add(move_name)
        
        logger.debug(f"Recorded {move_category} move '{move_name}' for {pokemon_name}")
    
    def record_battle_appearance(self, pokemon_name: str):
        """
        Record that we've seen this Pokemon in a battle (for confidence tracking)
        Call this once per battle when the Pokemon appears, not per move
        """
        if pokemon_name not in self.movepool_db:
            self.movepool_db[pokemon_name] = MovepoolData(
                pokemon_name=pokemon_name,
                physical_moves=set(),
                special_moves=set(),
                status_moves=set(),
                times_seen=0
            )
        
        self.movepool_db[pokemon_name].times_seen += 1
    
    def get_threat_category(self, pokemon_name: str) -> ThreatCategory:
        """
        Get the threat category for a Pokemon based on observed moves
        
        Returns:
            ThreatCategory enum value (PHYSICAL_ONLY, SPECIAL_ONLY, MIXED, etc.)
        """
        if pokemon_name not in self.movepool_db:
            return ThreatCategory.UNKNOWN
        
        return self.movepool_db[pokemon_name].threat_category
    
    def get_movepool_data(self, pokemon_name: str) -> Optional[MovepoolData]:
        """Get full movepool data for a Pokemon (None if not tracked yet)"""
        return self.movepool_db.get(pokemon_name)
    
    def get_stats_summary(self) -> dict:
        """Get summary statistics about tracked Pokemon"""
        by_category = defaultdict(list)
        for pokemon_name, movepool in self.movepool_db.items():
            by_category[movepool.threat_category].append(pokemon_name)
        
        return {
            "total_pokemon": len(self.movepool_db),
            "by_category": {
                category.value: {
                    "count": len(pokemon_list),
                    "examples": pokemon_list[:5]  # Show first 5
                }
                for category, pokemon_list in by_category.items()
            }
        }
    
    def print_summary(self):
        """Print a human-readable summary of learned movepools"""
        stats = self.get_stats_summary()
        logger.info("=" * 60)
        logger.info(f"MOVEPOOL TRACKER SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total Pokemon tracked: {stats['total_pokemon']}")
        logger.info("")
        
        for category_name, category_data in stats["by_category"].items():
            logger.info(f"{category_name.upper()}: {category_data['count']} Pokemon")
            logger.info(f"  Examples: {', '.join(category_data['examples'])}")
            logger.info("")
        
        logger.info("=" * 60)


# Global singleton instance
_global_tracker: Optional[MovepoolTracker] = None


def get_global_tracker() -> MovepoolTracker:
    """Get the global movepool tracker instance (singleton)"""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = MovepoolTracker()
        # Auto-save on exit
        import atexit
        atexit.register(_global_tracker.save)
    return _global_tracker


def record_move(pokemon_name: str, move_name: str):
    """Convenience function to record a move using the global tracker"""
    get_global_tracker().record_move(pokemon_name, move_name)


def record_battle_appearance(pokemon_name: str):
    """Convenience function to record a battle appearance using the global tracker"""
    get_global_tracker().record_battle_appearance(pokemon_name)


def get_threat_category(pokemon_name: str) -> ThreatCategory:
    """Convenience function to get threat category using the global tracker"""
    return get_global_tracker().get_threat_category(pokemon_name)
