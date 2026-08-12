#!/usr/bin/env python3
"""
Integration test for strategic overhaul.

Tests archetype detection and gameplan generation on actual team files.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict

import pytest

from fp.archetype_analyzer import analyze_team_archetype, ArchetypeEnum
from fp.gameplan_generator import generate_gameplan_from_archetype
from fp.battle_decision import initialize_battle_strategy

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_showdown_team(team_text: str) -> List[Dict]:
    """Parse Pokemon Showdown team format into team data."""
    team_data = []
    
    # Split by double newline (separates pokemon)
    pokemon_blocks = team_text.strip().split('\n\n')
    
    for block in pokemon_blocks:
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if not lines:
            continue
        
        # Parse first line: "Species @ Item" or "Species (Form) @ Item"
        first_line = lines[0]
        if ' @ ' in first_line:
            species_part, item = first_line.split(' @ ', 1)
        else:
            species_part = first_line
            item = ""
        
        # Remove gender and nicknames
        species = species_part.split(' (')[0].strip()
        
        # Extract ability, moves, etc.
        ability = ""
        moves = []
        
        for line in lines[1:]:
            if line.startswith('Ability:'):
                ability = line.split(':', 1)[1].strip()
            elif line.startswith('- '):
                move = line[2:].strip()
                moves.append(move)
        
        pokemon = {
            "species": species,
            "item": item,
            "ability": ability,
            "moves": moves
        }
        team_data.append(pokemon)
    
    return team_data


def run_team_analysis(team_name: str, team_path: Path, expected_archetype: ArchetypeEnum = None):
    """Test archetype analysis and gameplan generation for a team."""
    logger.info(f"\n{'='*80}")
    logger.info(f"Testing team: {team_name}")
    logger.info(f"{'='*80}")
    
    # Load team file
    team_text = team_path.read_text()
    team_data = parse_showdown_team(team_text)
    
    logger.info(f"Team composition: {[p['species'] for p in team_data]}")
    
    # Analyze archetype
    archetype = analyze_team_archetype(team_data)
    
    logger.info(f"\nARCHETYPE ANALYSIS:")
    logger.info(f"  Archetype: {archetype.archetype}")
    logger.info(f"  Confidence: {archetype.confidence:.2f}")
    logger.info(f"  Primary Win Condition: {archetype.primary_win_condition}")
    logger.info(f"  Critical Pokemon: {archetype.critical_pokemon}")
    logger.info(f"  Mandatory Setup: {archetype.mandatory_setup}")
    
    # Generate gameplan
    gameplan = generate_gameplan_from_archetype(archetype, team_data)
    
    logger.info(f"\nGAMEPLAN:")
    logger.info(f"  Early Game Goal: {gameplan.early_game_goal}")
    logger.info(f"  Mid Game Goal: {gameplan.mid_game_goal}")
    logger.info(f"  Late Game Goal: {gameplan.late_game_goal}")
    logger.info(f"  Must Happen By Turn: {gameplan.must_happen_by_turn}")
    logger.info(f"  HP Minimums: {gameplan.hp_minimums}")
    logger.info(f"  Switch Budget: {gameplan.switch_budget}")
    
    # Validate if expected archetype provided
    if expected_archetype:
        if archetype.archetype != expected_archetype:
            logger.warning(
                f"  ⚠️  Expected {expected_archetype}, got {archetype.archetype}"
            )
        else:
            logger.info(f"  ✅ Correctly identified as {expected_archetype}")
    
    return {
        "team_name": team_name,
        "archetype": str(archetype.archetype),
        "confidence": archetype.confidence,
        "critical_pokemon": archetype.critical_pokemon,
        "gameplan_early": gameplan.early_game_goal,
        "gameplan_mid": gameplan.mid_game_goal,
        "gameplan_late": gameplan.late_game_goal
    }


def main():
    """Run integration tests on all fat teams."""
    logger.info("STRATEGIC OVERHAUL INTEGRATION TEST")
    logger.info("Testing archetype detection and gameplan generation\n")
    
    results = []
    
    # Test Team 1: Stall
    team1_path = Path("teams/gen9/ou/fat-team-1-stall")
    if team1_path.exists():
        result = run_team_analysis(
            "Fat Team 1 (Stall)",
            team1_path,
            expected_archetype=ArchetypeEnum.HAZARD_STACK  # Has Stealth Rock + Spikes
        )
        results.append(result)
    
    # Test Team 2: Pivot
    team2_path = Path("teams/gen9/ou/fat-team-2-pivot")
    if team2_path.exists():
        result = run_team_analysis(
            "Fat Team 2 (Pivot)",
            team2_path,
            expected_archetype=ArchetypeEnum.PIVOT
        )
        results.append(result)
    
    # Test Team 3: Dondozo
    team3_path = Path("teams/gen9/ou/fat-team-3-dondozo")
    if team3_path.exists():
        result = run_team_analysis(
            "Fat Team 3 (Dondozo)",
            team3_path,
            expected_archetype=ArchetypeEnum.STALL_CORE
        )
        results.append(result)
    
    # Summary
    logger.info(f"\n{'='*80}")
    logger.info("SUMMARY")
    logger.info(f"{'='*80}")
    
    for result in results:
        logger.info(f"{result['team_name']}: {result['archetype']} (confidence: {result['confidence']:.2f})")
    
    # Save results
    output_path = Path("data/strategic_overhaul_test_results.json")
    output_path.parent.mkdir(exist_ok=True)
    
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"\nResults saved to: {output_path}")
    logger.info("\n✅ Integration test complete!")


def test_strategic_overhaul_smoke():
    """Pytest-safe smoke test for archetype analysis on the first available fat team."""
    candidates = [
        ("Fat Team 1 (Stall)", Path("teams/gen9/ou/fat-team-1-stall"), ArchetypeEnum.HAZARD_STACK),
        ("Fat Team 2 (Pivot)", Path("teams/gen9/ou/fat-team-2-pivot"), ArchetypeEnum.PIVOT),
        ("Fat Team 3 (Dondozo)", Path("teams/gen9/ou/fat-team-3-dondozo"), ArchetypeEnum.STALL_CORE),
    ]
    for team_name, team_path, expected in candidates:
        if team_path.exists():
            result = run_team_analysis(team_name, team_path, expected_archetype=expected)
            assert result["team_name"] == team_name
            assert result["gameplan_early"]
            assert result["gameplan_mid"]
            assert result["gameplan_late"]
            return
    pytest.skip("No fat-team files found for strategic overhaul smoke test")


if __name__ == "__main__":
    main()
