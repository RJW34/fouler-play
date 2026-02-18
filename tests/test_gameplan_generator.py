"""
Tests for gameplan_generator.py
"""

import pytest
from fp.archetype_analyzer import ArchetypeAnalyzer, ArchetypeEnum, TeamArchetype
from fp.gameplan_generator import (
    GameplanGenerator,
    Gameplan,
    generate_gameplan_from_archetype
)


class TestGameplanGenerator:
    """Test gameplan generation from archetypes."""
    
    def test_hazard_stack_gameplan(self):
        """Test gameplan generation for hazard stack archetype."""
        # Create a hazard stack archetype
        archetype = TeamArchetype(
            archetype=ArchetypeEnum.HAZARD_STACK,
            confidence=0.9,
            primary_win_condition="Set hazards early, wear down opponent",
            critical_pokemon=["skarmory", "blissey", "gholdengo"],
            mandatory_setup=["stealthrock", "spikes"]
        )
        
        team = [
            {"species": "Skarmory", "moves": ["Stealth Rock", "Spikes", "Roost", "Whirlwind"],
             "item": "Rocky Helmet", "ability": "Sturdy"},
            {"species": "Blissey", "moves": ["Soft-Boiled", "Seismic Toss", "Toxic", "Stealth Rock"],
             "item": "Heavy-Duty Boots", "ability": "Natural Cure"},
            {"species": "Gholdengo", "moves": ["Shadow Ball", "Make It Rain", "Recover", "Nasty Plot"],
             "item": "Air Balloon", "ability": "Good as Gold"},
            {"species": "Ting-Lu", "moves": ["Earthquake", "Spikes", "Ruination", "Whirlwind"],
             "item": "Leftovers", "ability": "Vessel of Ruin"},
            {"species": "Corviknight", "moves": ["Brave Bird", "Roost", "U-turn", "Defog"],
             "item": "Leftovers", "ability": "Pressure"},
            {"species": "Toxapex", "moves": ["Scald", "Recover", "Toxic", "Haze"],
             "item": "Black Sludge", "ability": "Regenerator"}
        ]
        
        generator = GameplanGenerator()
        gameplan = generator.generate(archetype, team)
        
        assert isinstance(gameplan, Gameplan)
        assert gameplan.archetype == str(ArchetypeEnum.HAZARD_STACK)
        assert "hazard" in gameplan.early_game_goal.lower()
        assert len(gameplan.must_happen_by_turn) > 0
        assert "stealthrock" in gameplan.must_happen_by_turn or \
               any("stealth" in k.lower() for k in gameplan.must_happen_by_turn.keys())
        assert len(gameplan.critical_pokemon) > 0
        assert len(gameplan.hp_minimums) > 0
        assert gameplan.switch_budget > 0
    
    def test_stall_core_gameplan(self):
        """Test gameplan generation for stall core archetype."""
        archetype = TeamArchetype(
            archetype=ArchetypeEnum.STALL_CORE,
            confidence=0.95,
            primary_win_condition="Survive indefinitely through defensive core",
            critical_pokemon=["dondozo", "blissey", "toxapex"],
            mandatory_setup=[]
        )
        
        team = [
            {"species": "Dondozo", "moves": ["Wave Crash", "Rest", "Sleep Talk", "Curse"],
             "item": "Leftovers", "ability": "Unaware"},
            {"species": "Blissey", "moves": ["Soft-Boiled", "Seismic Toss", "Toxic", "Stealth Rock"],
             "item": "Heavy-Duty Boots", "ability": "Natural Cure"},
            {"species": "Toxapex", "moves": ["Scald", "Recover", "Toxic", "Haze"],
             "item": "Black Sludge", "ability": "Regenerator"},
            {"species": "Skarmory", "moves": ["Brave Bird", "Spikes", "Roost", "Whirlwind"],
             "item": "Rocky Helmet", "ability": "Sturdy"},
            {"species": "Clodsire", "moves": ["Earthquake", "Recover", "Toxic", "Curse"],
             "item": "Leftovers", "ability": "Unaware"},
            {"species": "Corviknight", "moves": ["Brave Bird", "Roost", "Defog", "U-turn"],
             "item": "Leftovers", "ability": "Pressure"}
        ]
        
        generator = GameplanGenerator()
        gameplan = generator.generate(archetype, team)
        
        assert gameplan.archetype == str(ArchetypeEnum.STALL_CORE)
        assert "defensive" in gameplan.early_game_goal.lower() or \
               "wall" in gameplan.early_game_goal.lower()
        assert "recover" in gameplan.mid_phase_priority_moves or \
               any("recover" in m.lower() for m in gameplan.mid_phase_priority_moves)
        assert len(gameplan.critical_pokemon) >= 3
    
    def test_pivot_gameplan(self):
        """Test gameplan generation for pivot archetype."""
        archetype = TeamArchetype(
            archetype=ArchetypeEnum.PIVOT,
            confidence=0.85,
            primary_win_condition="Maintain momentum through pivot cycles",
            critical_pokemon=["landorus-therian", "tornadus-therian", "rillaboom"],
            mandatory_setup=[]
        )
        
        team = [
            {"species": "Landorus-Therian", "moves": ["Earthquake", "U-turn", "Stealth Rock", "Stone Edge"],
             "item": "Choice Scarf", "ability": "Intimidate"},
            {"species": "Tornadus-Therian", "moves": ["Hurricane", "U-turn", "Knock Off", "Heat Wave"],
             "item": "Assault Vest", "ability": "Regenerator"},
            {"species": "Rillaboom", "moves": ["Grassy Glide", "U-turn", "Wood Hammer", "Knock Off"],
             "item": "Choice Band", "ability": "Grassy Surge"},
            {"species": "Dragapult", "moves": ["Dragon Darts", "U-turn", "Shadow Ball", "Will-O-Wisp"],
             "item": "Choice Specs", "ability": "Infiltrator"},
            {"species": "Slowking-Galar", "moves": ["Future Sight", "Chilly Reception", "Flamethrower", "Sludge Bomb"],
             "item": "Assault Vest", "ability": "Regenerator"},
            {"species": "Garchomp", "moves": ["Earthquake", "Dragon Claw", "Swords Dance", "Fire Fang"],
             "item": "Life Orb", "ability": "Rough Skin"}
        ]
        
        generator = GameplanGenerator()
        gameplan = generator.generate(archetype, team)
        
        assert gameplan.archetype == str(ArchetypeEnum.PIVOT)
        assert "momentum" in gameplan.primary_win_condition.lower() or \
               "pivot" in gameplan.primary_win_condition.lower()
        assert gameplan.switch_budget >= 8  # Pivot teams allow more switches
        assert any("uturn" in m.lower() or "voltswitch" in m.lower() 
                   for m in gameplan.early_phase_priority_moves)
    
    def test_setup_sweeper_gameplan(self):
        """Test gameplan generation for setup sweeper archetype."""
        archetype = TeamArchetype(
            archetype=ArchetypeEnum.SETUP_SWEEPER,
            confidence=0.75,
            primary_win_condition="Preserve Dragonite, setup Dragon Dance, then sweep",
            critical_pokemon=["dragonite"],
            mandatory_setup=["dragondance"]
        )
        
        team = [
            {"species": "Dragonite", "moves": ["Dragon Dance", "Outrage", "Earthquake", "Extreme Speed"],
             "item": "Lum Berry", "ability": "Multiscale"},
            {"species": "Landorus-Therian", "moves": ["Earthquake", "U-turn", "Stealth Rock", "Stone Edge"],
             "item": "Rocky Helmet", "ability": "Intimidate"},
            {"species": "Corviknight", "moves": ["Brave Bird", "Roost", "Defog", "U-turn"],
             "item": "Leftovers", "ability": "Pressure"},
            {"species": "Slowking-Galar", "moves": ["Future Sight", "Chilly Reception", "Flamethrower", "Toxic"],
             "item": "Assault Vest", "ability": "Regenerator"},
            {"species": "Rillaboom", "moves": ["Grassy Glide", "Wood Hammer", "Knock Off", "U-turn"],
             "item": "Assault Vest", "ability": "Grassy Surge"},
            {"species": "Heatran", "moves": ["Magma Storm", "Earth Power", "Stealth Rock", "Taunt"],
             "item": "Air Balloon", "ability": "Flash Fire"}
        ]
        
        generator = GameplanGenerator()
        gameplan = generator.generate(archetype, team)
        
        assert gameplan.archetype == str(ArchetypeEnum.SETUP_SWEEPER)
        assert "dragonite" in [p.lower() for p in gameplan.critical_pokemon]
        assert "dragonite" in gameplan.hp_minimums
        assert gameplan.hp_minimums["dragonite"] >= 0.7  # High HP threshold
        assert any("dragondance" in m.lower() for m in gameplan.mid_phase_priority_moves)
    
    def test_balanced_gameplan(self):
        """Test gameplan generation for balanced archetype."""
        archetype = TeamArchetype(
            archetype=ArchetypeEnum.BALANCED,
            confidence=0.6,
            primary_win_condition="Apply pressure opportunistically",
            critical_pokemon=["garchomp", "heatran"],
            mandatory_setup=[]
        )
        
        team = [
            {"species": "Garchomp", "moves": ["Earthquake", "Dragon Claw", "Stone Edge", "Fire Fang"],
             "item": "Life Orb", "ability": "Rough Skin"},
            {"species": "Heatran", "moves": ["Magma Storm", "Earth Power", "Stealth Rock", "Taunt"],
             "item": "Air Balloon", "ability": "Flash Fire"},
            {"species": "Latios", "moves": ["Draco Meteor", "Psyshock", "Surf", "Trick"],
             "item": "Choice Scarf", "ability": "Levitate"},
            {"species": "Ferrothorn", "moves": ["Power Whip", "Knock Off", "Stealth Rock", "Leech Seed"],
             "item": "Leftovers", "ability": "Iron Barbs"},
            {"species": "Rotom-Wash", "moves": ["Hydro Pump", "Volt Switch", "Will-O-Wisp", "Pain Split"],
             "item": "Choice Specs", "ability": "Levitate"},
            {"species": "Azumarill", "moves": ["Aqua Jet", "Play Rough", "Belly Drum", "Knock Off"],
             "item": "Sitrus Berry", "ability": "Huge Power"}
        ]
        
        generator = GameplanGenerator()
        gameplan = generator.generate(archetype, team)
        
        assert gameplan.archetype == str(ArchetypeEnum.BALANCED)
        assert len(gameplan.critical_pokemon) > 0
        assert gameplan.switch_budget > 0
    
    def test_convenience_function(self):
        """Test convenience function."""
        archetype = TeamArchetype(
            archetype=ArchetypeEnum.HAZARD_STACK,
            confidence=0.9,
            primary_win_condition="Set hazards early",
            critical_pokemon=["skarmory", "blissey"],
            mandatory_setup=["stealthrock"]
        )
        
        team = [
            {"species": "Skarmory", "moves": ["Stealth Rock", "Spikes", "Roost", "Whirlwind"],
             "item": "Rocky Helmet", "ability": "Sturdy"},
            {"species": "Blissey", "moves": ["Soft-Boiled", "Seismic Toss", "Toxic", "Stealth Rock"],
             "item": "Heavy-Duty Boots", "ability": "Natural Cure"},
            {"species": "Gholdengo", "moves": ["Shadow Ball", "Make It Rain", "Recover", "Nasty Plot"],
             "item": "Air Balloon", "ability": "Good as Gold"},
            {"species": "Ting-Lu", "moves": ["Earthquake", "Spikes", "Ruination", "Whirlwind"],
             "item": "Leftovers", "ability": "Vessel of Ruin"},
            {"species": "Corviknight", "moves": ["Brave Bird", "Roost", "U-turn", "Defog"],
             "item": "Leftovers", "ability": "Pressure"},
            {"species": "Toxapex", "moves": ["Scald", "Recover", "Toxic", "Haze"],
             "item": "Black Sludge", "ability": "Regenerator"}
        ]
        
        gameplan = generate_gameplan_from_archetype(archetype, team)
        
        assert isinstance(gameplan, Gameplan)
        assert gameplan.archetype == str(ArchetypeEnum.HAZARD_STACK)
