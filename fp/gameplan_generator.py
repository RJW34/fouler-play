"""
Gameplan Generator - Creates structured gameplans from archetype classification.

Generates actionable gameplans with critical milestones, resource constraints,
and decision filters based on team archetype.
"""

import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from fp.archetype_analyzer import TeamArchetype, ArchetypeEnum
from fp.helpers import normalize_name

logger = logging.getLogger(__name__)


@dataclass
class Gameplan:
    """Structured gameplan for strategic battle execution."""
    archetype: str
    primary_win_condition: str
    secondary_win_condition: str
    
    # Game phase goals
    early_game_goal: str
    mid_game_goal: str
    late_game_goal: str
    
    # Resource constraints
    critical_pokemon: List[str] = field(default_factory=list)
    hp_minimums: Dict[str, float] = field(default_factory=dict)  # pokemon: min_hp_ratio
    mandatory_moves: Dict[str, List[str]] = field(default_factory=dict)  # pokemon: [moves]
    
    # Timing constraints
    must_happen_by_turn: Dict[str, int] = field(default_factory=dict)  # move: turn_number
    
    # Anti-patterns
    prohibited_switches: List[Tuple[str, str, str]] = field(default_factory=list)  # (from, to, reason)
    switch_budget: int = 10  # max switches in 8 turns
    
    # Phase-specific priorities
    early_phase_priority_moves: List[str] = field(default_factory=list)
    mid_phase_priority_moves: List[str] = field(default_factory=list)
    late_phase_priority_moves: List[str] = field(default_factory=list)


class GameplanGenerator:
    """Generates structured gameplans from archetype classification."""
    
    def generate(self, archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
        """
        Generate a gameplan based on team archetype.
        
        Args:
            archetype: TeamArchetype classification result
            team_data: Original team data for detailed analysis
            
        Returns:
            Structured Gameplan
        """
        if archetype.archetype == ArchetypeEnum.HAZARD_STACK:
            return self._generate_hazard_stack_plan(archetype, team_data)
        elif archetype.archetype == ArchetypeEnum.STALL_CORE:
            return self._generate_stall_core_plan(archetype, team_data)
        elif archetype.archetype == ArchetypeEnum.PIVOT:
            return self._generate_pivot_plan(archetype, team_data)
        elif archetype.archetype == ArchetypeEnum.SETUP_SWEEPER:
            return self._generate_setup_sweeper_plan(archetype, team_data)
        elif archetype.archetype == ArchetypeEnum.HYPER_OFFENSE:
            return self._generate_hyper_offense_plan(archetype, team_data)
        else:  # Balanced
            return self._generate_balanced_plan(archetype, team_data)
    
    def _generate_hazard_stack_plan(self, archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
        """Generate gameplan for Hazard Stack archetype."""
        # Find hazard setters and their specific hazard moves
        mandatory_moves = {}
        must_happen = {}
        
        for pkmn in team_data:
            species = normalize_name(pkmn.get("species", ""))
            moves = [normalize_name(m) for m in pkmn.get("moves", [])]
            
            hazard_moves = []
            for move in moves:
                if move in ["stealthrock", "spikes", "toxicspikes"]:
                    hazard_moves.append(move)
                    # Set timing constraints
                    if move == "stealthrock":
                        must_happen["stealthrock"] = 4
                    elif move == "spikes":
                        must_happen["spikes"] = 6
                    elif move == "toxicspikes":
                        must_happen["toxicspikes"] = 6
            
            if hazard_moves:
                mandatory_moves[species] = hazard_moves
        
        # HP minimums for critical pokemon
        hp_minimums = {}
        for pkmn in archetype.critical_pokemon[:3]:
            # Hazard setters need to survive setup, finishers need high HP
            hp_minimums[pkmn] = 0.6
        
        return Gameplan(
            archetype=str(archetype.archetype),
            primary_win_condition=archetype.primary_win_condition,
            secondary_win_condition=archetype.secondary_win_condition or "Finisher sweep after chip damage",
            early_game_goal="Set all hazards by turn 5-6, establish defensive positioning",
            mid_game_goal="Maintain hazards, wall opponent's offense, chip damage",
            late_game_goal="Execute finisher when opponent is worn down",
            critical_pokemon=archetype.critical_pokemon,
            hp_minimums=hp_minimums,
            mandatory_moves=mandatory_moves,
            must_happen_by_turn=must_happen,
            prohibited_switches=[],
            switch_budget=6,  # Moderate switching allowed for positioning
            early_phase_priority_moves=list(must_happen.keys()) + ["recover", "roost", "slackoff"],
            mid_phase_priority_moves=["recover", "roost", "slackoff", "toxic", "willowisp"],
            late_phase_priority_moves=[],  # Offensive moves in late game
        )
    
    def _generate_stall_core_plan(self, archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
        """Generate gameplan for Stall Core archetype."""
        # Identify recovery users and walls
        hp_minimums = {}
        mandatory_moves = {}
        
        for pkmn in team_data:
            species = normalize_name(pkmn.get("species", ""))
            moves = [normalize_name(m) for m in pkmn.get("moves", [])]
            
            if species in archetype.critical_pokemon:
                hp_minimums[species] = 0.5  # Walls can afford to take damage
                
                # Find recovery and status moves
                key_moves = []
                for move in moves:
                    if move in ["recover", "roost", "slackoff", "rest", "wish", "softboiled"]:
                        key_moves.append(move)
                    elif move in ["toxic", "willowisp", "thunderwave"]:
                        key_moves.append(move)
                
                if key_moves:
                    mandatory_moves[species] = key_moves
        
        return Gameplan(
            archetype=str(archetype.archetype),
            primary_win_condition=archetype.primary_win_condition,
            secondary_win_condition=archetype.secondary_win_condition or "Outlast opponent",
            early_game_goal="Establish defensive wall by turn 5, scout opponent",
            mid_game_goal="Survive through recovery and status, chip opponent",
            late_game_goal="Wear down opponent completely, finisher cleanup",
            critical_pokemon=archetype.critical_pokemon,
            hp_minimums=hp_minimums,
            mandatory_moves=mandatory_moves,
            must_happen_by_turn={},
            prohibited_switches=[],
            switch_budget=8,  # More switching allowed for defensive positioning
            early_phase_priority_moves=["toxic", "willowisp", "stealthrock"],
            mid_phase_priority_moves=["recover", "roost", "rest", "protect"],
            late_phase_priority_moves=["recover", "roost"],
        )
    
    def _generate_pivot_plan(self, archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
        """Generate gameplan for Pivot/Momentum archetype."""
        # Find pivot users
        mandatory_moves = {}
        
        for pkmn in team_data:
            species = normalize_name(pkmn.get("species", ""))
            moves = [normalize_name(m) for m in pkmn.get("moves", [])]
            
            pivot_moves = []
            for move in moves:
                if move in ["uturn", "voltswitch", "partingshot", "teleport", "batonpass", "flipturn"]:
                    pivot_moves.append(move)
            
            if pivot_moves and species in archetype.critical_pokemon:
                mandatory_moves[species] = pivot_moves
        
        # HP minimums - pivot users need to stay healthy
        hp_minimums = {pkmn: 0.7 for pkmn in archetype.critical_pokemon[:4]}
        
        return Gameplan(
            archetype=str(archetype.archetype),
            primary_win_condition=archetype.primary_win_condition,
            secondary_win_condition=archetype.secondary_win_condition or "Wear through momentum",
            early_game_goal="Scout with pivot moves, gain positioning advantage",
            mid_game_goal="Maintain momentum cycles, wear opponent through switches",
            late_game_goal="Convert momentum advantage into KOs",
            critical_pokemon=archetype.critical_pokemon,
            hp_minimums=hp_minimums,
            mandatory_moves=mandatory_moves,
            must_happen_by_turn={},
            prohibited_switches=[],
            switch_budget=10,  # High switching allowed - it's the strategy!
            early_phase_priority_moves=["uturn", "voltswitch", "partingshot"],
            mid_phase_priority_moves=["uturn", "voltswitch", "partingshot"],
            late_phase_priority_moves=[],
        )
    
    def _generate_setup_sweeper_plan(self, archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
        """Generate gameplan for Setup Sweeper archetype."""
        # Find the sweeper and setup moves
        sweeper = archetype.critical_pokemon[0] if archetype.critical_pokemon else None
        setup_moves = archetype.mandatory_setup
        
        mandatory_moves = {}
        hp_minimums = {}
        must_happen = {}
        
        if sweeper:
            # Sweeper MUST survive until setup
            hp_minimums[sweeper] = 0.8
            
            # Find the sweeper's setup move
            for pkmn in team_data:
                species = normalize_name(pkmn.get("species", ""))
                if species == sweeper:
                    moves = [normalize_name(m) for m in pkmn.get("moves", [])]
                    setup = [m for m in moves if m in [
                        "swordsdance", "nastyplot", "dragondance", "calmmind", 
                        "shellsmash", "quiverdance", "bulkup"
                    ]]
                    if setup:
                        mandatory_moves[species] = setup
                        # Setup should happen mid-game when threats are cleared
                        must_happen[setup[0]] = 15
        
        return Gameplan(
            archetype=str(archetype.archetype),
            primary_win_condition=archetype.primary_win_condition,
            secondary_win_condition=archetype.secondary_win_condition or "Clear path for sweep",
            early_game_goal="Remove threats to sweeper, scout opponent",
            mid_game_goal="Position sweeper safely, setup when opponent is weak",
            late_game_goal="Execute sweep with boosted sweeper",
            critical_pokemon=archetype.critical_pokemon,
            hp_minimums=hp_minimums,
            mandatory_moves=mandatory_moves,
            must_happen_by_turn=must_happen,
            prohibited_switches=[],
            switch_budget=6,
            early_phase_priority_moves=[],
            mid_phase_priority_moves=setup_moves,
            late_phase_priority_moves=[],
        )
    
    def _generate_hyper_offense_plan(self, archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
        """Generate gameplan for Hyper Offense archetype."""
        # All attackers are critical
        hp_minimums = {pkmn: 0.6 for pkmn in archetype.critical_pokemon}
        
        return Gameplan(
            archetype=str(archetype.archetype),
            primary_win_condition=archetype.primary_win_condition,
            secondary_win_condition=archetype.secondary_win_condition or "Overwhelm defenses",
            early_game_goal="Apply immediate pressure, prevent opponent setup",
            mid_game_goal="Maintain offensive tempo, break walls",
            late_game_goal="Close out with fastest attacker",
            critical_pokemon=archetype.critical_pokemon,
            hp_minimums=hp_minimums,
            mandatory_moves={},
            must_happen_by_turn={},
            prohibited_switches=[],
            switch_budget=5,  # Low switching - stay aggressive
            early_phase_priority_moves=[],
            mid_phase_priority_moves=[],
            late_phase_priority_moves=[],
        )
    
    def _generate_balanced_plan(self, archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
        """Generate gameplan for Balanced archetype."""
        hp_minimums = {pkmn: 0.6 for pkmn in archetype.critical_pokemon}
        
        return Gameplan(
            archetype=str(archetype.archetype),
            primary_win_condition=archetype.primary_win_condition,
            secondary_win_condition=archetype.secondary_win_condition or "Adapt to situation",
            early_game_goal="Scout opponent, identify win path",
            mid_game_goal="Execute strongest available strategy",
            late_game_goal="Convert advantage into victory",
            critical_pokemon=archetype.critical_pokemon,
            hp_minimums=hp_minimums,
            mandatory_moves={},
            must_happen_by_turn={},
            prohibited_switches=[],
            switch_budget=7,
            early_phase_priority_moves=[],
            mid_phase_priority_moves=[],
            late_phase_priority_moves=[],
        )


def generate_gameplan_from_archetype(archetype: TeamArchetype, team_data: List[Dict]) -> Gameplan:
    """
    Convenience function to generate gameplan from archetype.
    
    Args:
        archetype: TeamArchetype classification
        team_data: Original team data
        
    Returns:
        Structured Gameplan
    """
    generator = GameplanGenerator()
    return generator.generate(archetype, team_data)
