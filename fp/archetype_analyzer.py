"""
Archetype Analyzer - Classifies team archetypes for strategic planning.

Detects team archetypes (HazardStack, Pivot, StallCore, SetupSweeper, Balanced/HO)
and returns strategic metadata for gameplan generation.
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from fp.helpers import normalize_name
from constants_pkg.strategy import SETUP_MOVES
from fp.playstyle_config import HAZARD_MOVES, PIVOT_MOVES, RECOVERY_MOVES

logger = logging.getLogger(__name__)


class ArchetypeEnum(str, Enum):
    """Team archetype classification."""
    HAZARD_STACK = "HazardStack"
    PIVOT = "Pivot"
    STALL_CORE = "StallCore"
    SETUP_SWEEPER = "SetupSweeper"
    BALANCED = "Balanced"
    HYPER_OFFENSE = "HyperOffense"


@dataclass
class TeamArchetype:
    """Result of archetype classification."""
    archetype: ArchetypeEnum
    confidence: float  # 0.0-1.0
    primary_win_condition: str
    secondary_win_condition: Optional[str] = None
    critical_pokemon: List[str] = None
    mandatory_setup: List[str] = None
    prohibited_switches: List[Tuple[str, str, str]] = None  # (from, to, reason)
    
    def __post_init__(self):
        if self.critical_pokemon is None:
            self.critical_pokemon = []
        if self.mandatory_setup is None:
            self.mandatory_setup = []
        if self.prohibited_switches is None:
            self.prohibited_switches = []


# Known walls/defensive cores
WALL_POKEMON = {
    "blissey", "chansey", "toxapex", "skarmory", "corviknight", 
    "ting-lu", "tinglu", "dondozo", "clodsire", "slowking-galar", "slowkinggalar",
    "ferrothorn", "hippowdon", "quagsire", "gastrodon"
}

# Known sweepers
SWEEPER_POKEMON = {
    "dragonite", "garchomp", "salamence", "gyarados", "kingambit",
    "iron-valiant", "ironvaliant", "roaring-moon", "roaringmoon",
    "great-tusk", "greattusk", "volcarona", "dragapult"
}

# Setup move categories
STAT_BOOST_MOVES = {
    "swordsdance", "nastyplot", "dragondance", "calmmind", "shellsmash",
    "quiverdance", "bulkup", "irondefense", "agility", "rockpolish"
}


class ArchetypeAnalyzer:
    """Analyzes team composition to determine strategic archetype."""
    
    def classify_team(self, team_data: List[Dict]) -> TeamArchetype:
        """
        Classify a team's archetype based on composition and movesets.
        
        Args:
            team_data: List of pokemon dicts with species, moves, item, ability
            
        Returns:
            TeamArchetype with classification and strategic metadata
        """
        # Extract team features
        features = self._extract_features(team_data)
        
        # Run detection rules in priority order
        hazard_result = self._detect_hazard_stack(features, team_data)
        if hazard_result and hazard_result.confidence >= 0.7:
            return hazard_result
        
        stall_result = self._detect_stall_core(features, team_data)
        if stall_result and stall_result.confidence >= 0.7:
            return stall_result
        
        pivot_result = self._detect_pivot(features, team_data)
        if pivot_result and pivot_result.confidence >= 0.7:
            return pivot_result
        
        setup_result = self._detect_setup_sweeper(features, team_data)
        if setup_result and setup_result.confidence >= 0.6:
            return setup_result
        
        ho_result = self._detect_hyper_offense(features, team_data)
        if ho_result and ho_result.confidence >= 0.6:
            return ho_result
        
        # Default to balanced
        return self._classify_balanced(features, team_data)
    
    def _extract_features(self, team_data: List[Dict]) -> Dict:
        """Extract relevant features from team composition."""
        features = {
            "hazard_setters": [],
            "hazard_types": set(),
            "pivot_users": [],
            "walls": [],
            "sweepers": [],
            "setup_users": [],
            "recovery_users": [],
            "offensive_pokemon": [],
            "defensive_pokemon": [],
        }
        
        for pkmn in team_data:
            species = normalize_name(pkmn.get("species", ""))
            moves = [normalize_name(m) for m in pkmn.get("moves", [])]
            
            # Hazard detection
            for move in moves:
                if move in HAZARD_MOVES:
                    features["hazard_setters"].append(species)
                    features["hazard_types"].add(move)
            
            # Pivot detection
            for move in moves:
                if move in PIVOT_MOVES:
                    features["pivot_users"].append(species)
                    break
            
            # Wall detection
            if species in WALL_POKEMON:
                features["walls"].append(species)
            
            # Sweeper detection
            if species in SWEEPER_POKEMON:
                features["sweepers"].append(species)
            
            # Setup move detection
            for move in moves:
                if move in STAT_BOOST_MOVES:
                    features["setup_users"].append((species, move))
            
            # Recovery detection
            for move in moves:
                if move in RECOVERY_MOVES:
                    features["recovery_users"].append(species)
                    break
            
            # Offensive vs defensive classification
            # Simple heuristic: walls are defensive, others need analysis
            if species in WALL_POKEMON:
                features["defensive_pokemon"].append(species)
            else:
                # Check for offensive moves
                offensive_moves = [m for m in moves if not any([
                    m in RECOVERY_MOVES,
                    m in HAZARD_MOVES,
                    m in {"protect", "substitute", "toxic", "willowisp", "thunderwave"}
                ])]
                if len(offensive_moves) >= 2:
                    features["offensive_pokemon"].append(species)
                else:
                    features["defensive_pokemon"].append(species)
        
        return features
    
    def _detect_hazard_stack(self, features: Dict, team_data: List[Dict]) -> Optional[TeamArchetype]:
        """Detect Hazard Stack archetype."""
        hazard_count = len(features["hazard_setters"])
        unique_hazards = len(features["hazard_types"])
        wall_count = len(features["walls"])
        
        # Needs at least 1 hazard setter, preferably 2+ with different hazard types
        if hazard_count == 0:
            return None
        
        confidence = 0.0
        
        # Strong indicators
        if unique_hazards >= 2 and wall_count >= 2:
            confidence = 0.9
        elif unique_hazards >= 2 and wall_count >= 1:
            confidence = 0.8
        elif hazard_count >= 2 and wall_count >= 2:
            confidence = 0.75
        elif unique_hazards >= 1 and wall_count >= 2:
            confidence = 0.7
        else:
            return None
        
        # Identify critical pokemon
        critical = features["hazard_setters"][:2] + features["walls"][:2]
        
        # Identify mandatory setup moves
        mandatory = list(features["hazard_types"])
        
        return TeamArchetype(
            archetype=ArchetypeEnum.HAZARD_STACK,
            confidence=confidence,
            primary_win_condition="Set all hazards early (turn 1-5), then wall and wear down opponent",
            secondary_win_condition="Finisher sweep after opponent is weakened",
            critical_pokemon=critical,
            mandatory_setup=mandatory,
        )
    
    def _detect_stall_core(self, features: Dict, team_data: List[Dict]) -> Optional[TeamArchetype]:
        """Detect Stall Core archetype."""
        wall_count = len(features["walls"])
        recovery_count = len(features["recovery_users"])
        offensive_count = len(features["offensive_pokemon"])
        
        # Needs 3+ walls and high recovery usage
        if wall_count < 3:
            return None
        
        confidence = 0.0
        
        if wall_count >= 4 and recovery_count >= 3:
            confidence = 0.95
        elif wall_count >= 3 and recovery_count >= 3:
            confidence = 0.85
        elif wall_count >= 3 and recovery_count >= 2:
            confidence = 0.75
        else:
            return None
        
        # Identify the defensive core
        critical = features["walls"][:3]
        
        return TeamArchetype(
            archetype=ArchetypeEnum.STALL_CORE,
            confidence=confidence,
            primary_win_condition="Survive indefinitely through defensive core, wear with chip damage and status",
            secondary_win_condition="Slow finisher cleanup after opponent is exhausted",
            critical_pokemon=critical,
            mandatory_setup=[],
        )
    
    def _detect_pivot(self, features: Dict, team_data: List[Dict]) -> Optional[TeamArchetype]:
        """Detect Pivot/Momentum archetype."""
        pivot_count = len(features["pivot_users"])
        
        # Needs 3+ pivot moves
        if pivot_count < 3:
            return None
        
        confidence = 0.0
        
        if pivot_count >= 4:
            confidence = 0.9
        elif pivot_count >= 3:
            confidence = 0.75
        else:
            return None
        
        # All pivot users are critical
        critical = features["pivot_users"][:4]
        
        return TeamArchetype(
            archetype=ArchetypeEnum.PIVOT,
            confidence=confidence,
            primary_win_condition="Maintain momentum through pivot cycles, gain positioning advantage",
            secondary_win_condition="Wear down opponent through consistent pressure",
            critical_pokemon=critical,
            mandatory_setup=[],
        )
    
    def _detect_setup_sweeper(self, features: Dict, team_data: List[Dict]) -> Optional[TeamArchetype]:
        """Detect Setup Sweeper archetype."""
        setup_users = features["setup_users"]
        
        if not setup_users:
            return None
        
        # Find the primary sweeper
        sweeper, setup_move = setup_users[0]
        
        confidence = 0.7 if len(setup_users) >= 1 else 0.5
        
        return TeamArchetype(
            archetype=ArchetypeEnum.SETUP_SWEEPER,
            confidence=confidence,
            primary_win_condition=f"Preserve {sweeper}, setup {setup_move}, then sweep",
            secondary_win_condition="Weaken threats before setup turn",
            critical_pokemon=[sweeper],
            mandatory_setup=[setup_move],
        )
    
    def _detect_hyper_offense(self, features: Dict, team_data: List[Dict]) -> Optional[TeamArchetype]:
        """Detect Hyper Offense archetype."""
        offensive_count = len(features["offensive_pokemon"])
        wall_count = len(features["walls"])
        
        # Needs 5+ offensive pokemon, minimal walls
        if offensive_count < 5 or wall_count > 1:
            return None
        
        confidence = 0.7
        
        return TeamArchetype(
            archetype=ArchetypeEnum.HYPER_OFFENSE,
            confidence=confidence,
            primary_win_condition="Apply constant offensive pressure, overwhelm opponent",
            secondary_win_condition="Prevent opponent setup through aggression",
            critical_pokemon=features["sweepers"][:3] if features["sweepers"] else features["offensive_pokemon"][:3],
            mandatory_setup=[],
        )
    
    def _classify_balanced(self, features: Dict, team_data: List[Dict]) -> TeamArchetype:
        """Default balanced classification."""
        offensive_count = len(features["offensive_pokemon"])
        defensive_count = len(features["defensive_pokemon"])
        
        critical = []
        if features["sweepers"]:
            critical.extend(features["sweepers"][:2])
        if features["walls"]:
            critical.extend(features["walls"][:1])
        
        return TeamArchetype(
            archetype=ArchetypeEnum.BALANCED,
            confidence=0.5,
            primary_win_condition="Apply pressure opportunistically, execute available strategies",
            secondary_win_condition="Adapt to opponent's weaknesses",
            critical_pokemon=critical,
            mandatory_setup=[],
        )


def analyze_team_archetype(team_data: List[Dict]) -> TeamArchetype:
    """
    Convenience function to analyze a team's archetype.
    
    Args:
        team_data: List of pokemon dicts
        
    Returns:
        TeamArchetype classification
    """
    analyzer = ArchetypeAnalyzer()
    return analyzer.classify_team(team_data)
