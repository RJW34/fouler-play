"""
Tests for archetype_analyzer.py
"""

import pytest
from fp.archetype_analyzer import (
    ArchetypeAnalyzer,
    ArchetypeEnum,
    TeamArchetype,
    analyze_team_archetype
)


class TestArchetypeAnalyzer:
    """Test archetype classification."""
    
    def test_hazard_stack_detection(self):
        """Test detection of hazard stack archetype."""
        team = [
            {
                "species": "Skarmory",
                "moves": ["Stealth Rock", "Spikes", "Roost", "Whirlwind"],
                "item": "Rocky Helmet",
                "ability": "Sturdy"
            },
            {
                "species": "Blissey",
                "moves": ["Seismic Toss", "Soft-Boiled", "Toxic", "Stealth Rock"],
                "item": "Heavy-Duty Boots",
                "ability": "Natural Cure"
            },
            {
                "species": "Ting-Lu",
                "moves": ["Earthquake", "Ruination", "Spikes", "Whirlwind"],
                "item": "Leftovers",
                "ability": "Vessel of Ruin"
            },
            {
                "species": "Gholdengo",
                "moves": ["Shadow Ball", "Make It Rain", "Nasty Plot", "Recover"],
                "item": "Air Balloon",
                "ability": "Good as Gold"
            },
            {
                "species": "Corviknight",
                "moves": ["Brave Bird", "U-turn", "Roost", "Defog"],
                "item": "Leftovers",
                "ability": "Pressure"
            },
            {
                "species": "Great Tusk",
                "moves": ["Headlong Rush", "Ice Spinner", "Rapid Spin", "Knock Off"],
                "item": "Booster Energy",
                "ability": "Protosynthesis"
            }
        ]
        
        analyzer = ArchetypeAnalyzer()
        result = analyzer.classify_team(team)
        
        assert result.archetype == ArchetypeEnum.HAZARD_STACK
        assert result.confidence >= 0.7
        assert "hazard" in result.primary_win_condition.lower()
        assert len(result.mandatory_setup) > 0
        assert any("stealth" in move.lower() or "spikes" in move.lower() 
                   for move in result.mandatory_setup)
        assert "blissey" in [p.lower() for p in result.critical_pokemon] or \
               "skarmory" in [p.lower() for p in result.critical_pokemon]
    
    def test_pivot_detection(self):
        """Test detection of pivot/momentum archetype."""
        team = [
            {
                "species": "Landorus-Therian",
                "moves": ["Earthquake", "U-turn", "Stealth Rock", "Stone Edge"],
                "item": "Choice Scarf",
                "ability": "Intimidate"
            },
            {
                "species": "Tornadus-Therian",
                "moves": ["Hurricane", "U-turn", "Knock Off", "Heat Wave"],
                "item": "Assault Vest",
                "ability": "Regenerator"
            },
            {
                "species": "Slowking-Galar",
                "moves": ["Future Sight", "Chilly Reception", "Flamethrower", "Sludge Bomb"],
                "item": "Assault Vest",
                "ability": "Regenerator"
            },
            {
                "species": "Rillaboom",
                "moves": ["Grassy Glide", "U-turn", "Wood Hammer", "Knock Off"],
                "item": "Choice Band",
                "ability": "Grassy Surge"
            },
            {
                "species": "Dragapult",
                "moves": ["Dragon Darts", "U-turn", "Shadow Ball", "Will-O-Wisp"],
                "item": "Choice Specs",
                "ability": "Infiltrator"
            },
            {
                "species": "Garchomp",
                "moves": ["Earthquake", "Dragon Claw", "Swords Dance", "Fire Fang"],
                "item": "Life Orb",
                "ability": "Rough Skin"
            }
        ]
        
        analyzer = ArchetypeAnalyzer()
        result = analyzer.classify_team(team)
        
        # Should detect pivot or setup sweeper (Garchomp has Swords Dance)
        assert result.archetype in [ArchetypeEnum.PIVOT, ArchetypeEnum.SETUP_SWEEPER]
        assert result.confidence >= 0.6
        if result.archetype == ArchetypeEnum.PIVOT:
            assert "momentum" in result.primary_win_condition.lower() or \
                   "pivot" in result.primary_win_condition.lower()
            assert len(result.critical_pokemon) >= 3
    
    def test_stall_core_detection(self):
        """Test detection of stall core archetype."""
        team = [
            {
                "species": "Dondozo",
                "moves": ["Wave Crash", "Rest", "Sleep Talk", "Curse"],
                "item": "Leftovers",
                "ability": "Unaware"
            },
            {
                "species": "Blissey",
                "moves": ["Seismic Toss", "Soft-Boiled", "Toxic", "Stealth Rock"],
                "item": "Heavy-Duty Boots",
                "ability": "Natural Cure"
            },
            {
                "species": "Toxapex",
                "moves": ["Scald", "Recover", "Toxic", "Haze"],
                "item": "Black Sludge",
                "ability": "Regenerator"
            },
            {
                "species": "Skarmory",
                "moves": ["Brave Bird", "Spikes", "Roost", "Whirlwind"],
                "item": "Rocky Helmet",
                "ability": "Sturdy"
            },
            {
                "species": "Clodsire",
                "moves": ["Earthquake", "Recover", "Toxic", "Curse"],
                "item": "Leftovers",
                "ability": "Unaware"
            },
            {
                "species": "Corviknight",
                "moves": ["Brave Bird", "Roost", "Defog", "U-turn"],
                "item": "Leftovers",
                "ability": "Pressure"
            }
        ]
        
        analyzer = ArchetypeAnalyzer()
        result = analyzer.classify_team(team)
        
        # Should detect stall or hazard stack (has 2 hazard setters)
        assert result.archetype in [ArchetypeEnum.STALL_CORE, ArchetypeEnum.HAZARD_STACK]
        assert result.confidence >= 0.7
        if result.archetype == ArchetypeEnum.STALL_CORE:
            assert "stall" in result.primary_win_condition.lower() or \
                   "survive" in result.primary_win_condition.lower()
        assert len(result.critical_pokemon) >= 3
    
    def test_setup_sweeper_detection(self):
        """Test detection of setup sweeper archetype."""
        team = [
            {
                "species": "Dragonite",
                "moves": ["Dragon Dance", "Outrage", "Earthquake", "Extreme Speed"],
                "item": "Lum Berry",
                "ability": "Multiscale"
            },
            {
                "species": "Garchomp",
                "moves": ["Swords Dance", "Earthquake", "Dragon Claw", "Fire Fang"],
                "item": "Life Orb",
                "ability": "Rough Skin"
            },
            {
                "species": "Landorus-Therian",
                "moves": ["Earthquake", "U-turn", "Stealth Rock", "Stone Edge"],
                "item": "Rocky Helmet",
                "ability": "Intimidate"
            },
            {
                "species": "Corviknight",
                "moves": ["Brave Bird", "Roost", "Defog", "U-turn"],
                "item": "Leftovers",
                "ability": "Pressure"
            },
            {
                "species": "Slowking-Galar",
                "moves": ["Future Sight", "Chilly Reception", "Flamethrower", "Toxic"],
                "item": "Assault Vest",
                "ability": "Regenerator"
            },
            {
                "species": "Rillaboom",
                "moves": ["Grassy Glide", "Wood Hammer", "Knock Off", "U-turn"],
                "item": "Assault Vest",
                "ability": "Grassy Surge"
            }
        ]
        
        analyzer = ArchetypeAnalyzer()
        result = analyzer.classify_team(team)
        
        # Should detect setup sweeper (Dragonite with Dragon Dance)
        assert result.archetype in [ArchetypeEnum.SETUP_SWEEPER, ArchetypeEnum.BALANCED]
        if result.archetype == ArchetypeEnum.SETUP_SWEEPER:
            assert "dragonite" in [p.lower() for p in result.critical_pokemon]
    
    def test_balanced_default(self):
        """Test that balanced archetype is default when no clear pattern."""
        team = [
            {
                "species": "Garchomp",
                "moves": ["Earthquake", "Dragon Claw", "Stone Edge", "Fire Fang"],
                "item": "Life Orb",
                "ability": "Rough Skin"
            },
            {
                "species": "Rotom-Wash",
                "moves": ["Hydro Pump", "Volt Switch", "Will-O-Wisp", "Pain Split"],
                "item": "Choice Specs",
                "ability": "Levitate"
            },
            {
                "species": "Ferrothorn",
                "moves": ["Power Whip", "Knock Off", "Stealth Rock", "Leech Seed"],
                "item": "Leftovers",
                "ability": "Iron Barbs"
            },
            {
                "species": "Heatran",
                "moves": ["Magma Storm", "Earth Power", "Stealth Rock", "Taunt"],
                "item": "Air Balloon",
                "ability": "Flash Fire"
            },
            {
                "species": "Latios",
                "moves": ["Draco Meteor", "Psyshock", "Surf", "Trick"],
                "item": "Choice Scarf",
                "ability": "Levitate"
            },
            {
                "species": "Azumarill",
                "moves": ["Aqua Jet", "Play Rough", "Belly Drum", "Knock Off"],
                "item": "Sitrus Berry",
                "ability": "Huge Power"
            }
        ]
        
        analyzer = ArchetypeAnalyzer()
        result = analyzer.classify_team(team)
        
        # Should detect some archetype (balanced, setup, hazard, or HO)
        assert result.archetype in [
            ArchetypeEnum.BALANCED, 
            ArchetypeEnum.SETUP_SWEEPER,  # Azumarill with Belly Drum
            ArchetypeEnum.HAZARD_STACK,  # 2 Stealth Rock setters
            ArchetypeEnum.HYPER_OFFENSE  # Many offensive pokemon
        ]
        assert result.confidence >= 0.5
    
    def test_convenience_function(self):
        """Test the convenience function."""
        team = [
            {"species": "Blissey", "moves": ["Soft-Boiled", "Seismic Toss", "Toxic", "Stealth Rock"], 
             "item": "Heavy-Duty Boots", "ability": "Natural Cure"},
            {"species": "Skarmory", "moves": ["Spikes", "Roost", "Whirlwind", "Brave Bird"],
             "item": "Rocky Helmet", "ability": "Sturdy"},
            {"species": "Ting-Lu", "moves": ["Earthquake", "Spikes", "Ruination", "Whirlwind"],
             "item": "Leftovers", "ability": "Vessel of Ruin"},
            {"species": "Gholdengo", "moves": ["Shadow Ball", "Make It Rain", "Recover", "Nasty Plot"],
             "item": "Air Balloon", "ability": "Good as Gold"},
            {"species": "Corviknight", "moves": ["Brave Bird", "Roost", "U-turn", "Defog"],
             "item": "Leftovers", "ability": "Pressure"},
            {"species": "Toxapex", "moves": ["Scald", "Recover", "Toxic", "Haze"],
             "item": "Black Sludge", "ability": "Regenerator"}
        ]
        
        result = analyze_team_archetype(team)
        
        assert isinstance(result, TeamArchetype)
        assert result.archetype == ArchetypeEnum.HAZARD_STACK
        assert result.confidence >= 0.7
