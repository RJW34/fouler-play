"""
Playstyle Configuration System
Adjusts bot behavior based on team archetype
"""

from enum import Enum
from typing import Dict, Any


class Playstyle(Enum):
    """Team archetype classifications"""
    HYPER_OFFENSE = "hyper_offense"
    BULKY_OFFENSE = "bulky_offense"
    BALANCE = "balance"
    FAT = "fat"
    STALL = "stall"


class PlaystyleConfig:
    """Configuration parameters for different playstyles"""
    
    # Base configuration (default balanced play)
    BASE = {
        "switch_penalty_multiplier": 1.0,
        "hazard_value_multiplier": 1.0,
        "recovery_value_multiplier": 1.0,
        "setup_value_multiplier": 1.0,
        "pivot_value_multiplier": 1.0,
        "damage_value_multiplier": 1.0,
        "chip_damage_multiplier": 1.0,
        "longevity_multiplier": 1.0,
        "risk_tolerance": 1.0,
    }
    
    # Hyper Offense: Fast-paced, setup-heavy, minimal switching
    HYPER_OFFENSE = {
        "switch_penalty_multiplier": 1.4,      # Switching loses momentum
        "hazard_value_multiplier": 1.3,        # Screens/hazards enable sweeps
        "recovery_value_multiplier": 0.7,      # Less focus on recovery
        "setup_value_multiplier": 1.6,         # Setup is the win condition
        "pivot_value_multiplier": 0.8,         # Less pivoting
        "damage_value_multiplier": 1.4,        # Maximize damage output
        "chip_damage_multiplier": 0.9,         # Less patient chipping
        "longevity_multiplier": 0.8,           # Win fast or lose
        "risk_tolerance": 1.5,                 # Higher risk plays
    }
    
    # Bulky Offense: Balanced offense with some bulk
    BULKY_OFFENSE = {
        "switch_penalty_multiplier": 0.9,
        "hazard_value_multiplier": 1.2,
        "recovery_value_multiplier": 1.1,
        "setup_value_multiplier": 1.3,
        "pivot_value_multiplier": 1.3,
        "damage_value_multiplier": 1.2,
        "chip_damage_multiplier": 1.1,
        "longevity_multiplier": 1.1,
        "risk_tolerance": 1.2,
    }
    
    # Balance: Adaptable, well-rounded
    BALANCE = {
        "switch_penalty_multiplier": 1.0,
        "hazard_value_multiplier": 1.1,
        "recovery_value_multiplier": 1.1,
        "setup_value_multiplier": 1.1,
        "pivot_value_multiplier": 1.2,
        "damage_value_multiplier": 1.0,
        "chip_damage_multiplier": 1.1,
        "longevity_multiplier": 1.0,
        "risk_tolerance": 1.0,
    }
    
    # Fat: Defensive cores, pivot-heavy, chip damage
    FAT = {
        "switch_penalty_multiplier": 0.6,      # Switch freely for good matchups
        "hazard_value_multiplier": 1.5,        # Hazards critical for chip
        "recovery_value_multiplier": 1.5,      # Maintain defensive backbone
        "setup_value_multiplier": 0.9,         # Setup only when safe
        "pivot_value_multiplier": 1.6,         # Pivot constantly
        "damage_value_multiplier": 0.8,        # Patient play
        "chip_damage_multiplier": 1.5,         # Win through accumulation
        "longevity_multiplier": 1.6,           # Outlast opponent
        "risk_tolerance": 0.7,                 # Conservative plays
    }
    
    # Stall: Ultra-defensive, wear down opponent
    STALL = {
        "switch_penalty_multiplier": 0.5,      # Switch aggressively
        "hazard_value_multiplier": 1.7,        # Hazards are essential
        "recovery_value_multiplier": 1.8,      # Recovery is everything
        "setup_value_multiplier": 0.7,         # Minimal setup
        "pivot_value_multiplier": 1.7,         # Constant pivoting
        "damage_value_multiplier": 0.6,        # Don't rush damage
        "chip_damage_multiplier": 1.8,         # Win through chip
        "longevity_multiplier": 2.0,           # Maximum longevity
        "risk_tolerance": 0.5,                 # Extremely conservative
    }
    
    @classmethod
    def get_config(cls, playstyle: Playstyle) -> Dict[str, float]:
        """Get configuration for a playstyle"""
        configs = {
            Playstyle.HYPER_OFFENSE: cls.HYPER_OFFENSE,
            Playstyle.BULKY_OFFENSE: cls.BULKY_OFFENSE,
            Playstyle.BALANCE: cls.BALANCE,
            Playstyle.FAT: cls.FAT,
            Playstyle.STALL: cls.STALL,
        }
        return configs.get(playstyle, cls.BASE)
    
    @classmethod
    def detect_playstyle(cls, team_preview: list) -> Playstyle:
        """
        Auto-detect team playstyle from team preview
        This is a heuristic-based detection system
        """
        try:
            from fp.team_analysis import analyze_team
            analysis = analyze_team(team_preview)
            return analysis.playstyle
        except Exception:
            return Playstyle.BALANCE
    
    @classmethod
    def get_team_playstyle(cls, team_name: str) -> Playstyle:
        """
        Map team file names to playstyles
        This allows manual override of playstyle per team
        """
        team_mappings = {
            # Fat teams from top player
            "fat-team-1-stall": Playstyle.FAT,
            "fat-team-2-pivot": Playstyle.FAT,
            "fat-team-3-dondozo": Playstyle.FAT,
            
            # HO teams
            "screens-ho": Playstyle.HYPER_OFFENSE,
            "sand-offense": Playstyle.BULKY_OFFENSE,
            
            # Balanced teams
            "vert-screens": Playstyle.BALANCE,
            "example": Playstyle.BALANCE,
        }
        
        return team_mappings.get(team_name, Playstyle.BALANCE)


# Special ability awareness for fat teams
ABILITY_AWARENESS = {
    "unaware": {
        "description": "Ignores opponent's stat changes",
        "penalty": "Never setup vs Unaware (boosts are ignored)",
        "mons": ["Dondozo", "Quagsire", "Skeledirge", "Clefable"]
    },
    "regenerator": {
        "description": "Heals 33% HP on switch",
        "bonus": "Switch Regenerator mons freely for healing",
        "mons": ["Slowking-Galar", "Tornadus-Therian", "Toxapex", "Amoonguss"]
    },
    "pressure": {
        "description": "Drains 2 PP per move used",
        "bonus": "Stalling with Pressure + Sub/Protect drains PP fast",
        "mons": ["Kyurem", "Corviknight", "Zapdos", "Articuno-Galar"]
    },
}


# Pivot move detection
PIVOT_MOVES = {
    "u-turn", "volt-switch", "flip-turn", "parting-shot", 
    "teleport", "baton-pass", "chilly-reception"
}


# Recovery move detection
RECOVERY_MOVES = {
    "recover", "roost", "soft-boiled", "slack-off", "synthesis",
    "moonlight", "morning-sun", "rest", "shore-up", "wish",
    "heal-order", "milk-drink", "swallow"
}


# Hazard moves
HAZARD_MOVES = {
    "stealth-rock", "spikes", "toxic-spikes", "sticky-web"
}


# Phazing moves
PHAZE_MOVES = {
    "whirlwind", "roar", "dragon-tail", "circle-throw"
}
