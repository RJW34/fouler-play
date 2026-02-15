#!/usr/bin/env python3
"""
Test matchup analyzer on 5 sample replays to validate gameplan generation.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List
import logging

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from fp.matchup_analyzer import analyze_matchup, Gameplan
from fp.helpers import normalize_name

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def extract_team_from_replay(replay_file: Path) -> tuple[List[Dict], List[Dict], str]:
    """
    Extract our team and opponent team from a replay JSON.
    
    Returns:
        (our_team, opponent_team, battle_result)
    """
    try:
        with open(replay_file, 'r') as f:
            data = json.load(f)
        
        log = data.get("log", "")
        players = data.get("players", [])
        
        # Determine which player is us (ALL CHUNG, BugInTheCode, etc.)
        our_names = ["ALL CHUNG", "BugInTheCode", "thepeakmos"]
        our_player = None
        opponent_name = None
        
        for i, player in enumerate(players):
            if any(name.lower() in player.lower() for name in our_names):
                our_player = f"p{i+1}"
                opponent_name = players[1-i] if len(players) == 2 else "Unknown"
                break
        
        if not our_player:
            # Fallback: assume p1 is us
            our_player = "p1"
            opponent_name = players[1] if len(players) > 1 else "Unknown"
        
        # Parse team preview
        our_team = []
        opp_team = []
        
        for line in log.split("\n"):
            if line.startswith("|poke|"):
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                player_id = parts[2]
                pkmn_data = parts[3]
                
                # Parse species (may have form, gender, level)
                species = pkmn_data.split(",")[0].strip()
                species = normalize_name(species)
                
                pkmn = {
                    "species": species,
                    "moves": [],  # Will be filled as we see moves
                    "item": "",
                    "ability": ""
                }
                
                if player_id == our_player:
                    our_team.append(pkmn)
                else:
                    opp_team.append(pkmn)
            
            # Track revealed moves
            elif line.startswith("|move|"):
                parts = line.split("|")
                if len(parts) < 4:
                    continue
                player_pkmn = parts[2]
                move = normalize_name(parts[3])
                
                # Determine which team
                if player_pkmn.startswith(f"{our_player}a"):
                    # Our move
                    if our_team and move not in our_team[0].get("moves", []):
                        our_team[0]["moves"].append(move)
                else:
                    # Opponent move
                    if opp_team and move not in opp_team[0].get("moves", []):
                        opp_team[0]["moves"].append(move)
            
            # Track revealed items
            elif "|item|" in line:
                parts = line.split("|")
                if len(parts) < 4:
                    continue
                player_pkmn = parts[2]
                item = normalize_name(parts[3])
                
                if player_pkmn.startswith(f"{our_player}a"):
                    if our_team:
                        our_team[0]["item"] = item
                else:
                    if opp_team:
                        opp_team[0]["item"] = item
            
            # Track revealed abilities
            elif "|-ability|" in line:
                parts = line.split("|")
                if len(parts) < 4:
                    continue
                player_pkmn = parts[2]
                ability = normalize_name(parts[3])
                
                if player_pkmn.startswith(f"{our_player}a"):
                    if our_team:
                        our_team[0]["ability"] = ability
                else:
                    if opp_team:
                        opp_team[0]["ability"] = ability
        
        # Determine result
        result = "unknown"
        if "|win|" in log:
            winner_line = [l for l in log.split("\n") if l.startswith("|win|")]
            if winner_line:
                winner = winner_line[0].split("|")[2]
                if any(name.lower() in winner.lower() for name in our_names):
                    result = "win"
                else:
                    result = "loss"
        elif "|tie|" in log:
            result = "tie"
        
        return our_team, opp_team, result
    
    except Exception as e:
        logger.error(f"Failed to parse replay {replay_file}: {e}")
        return [], [], "error"


def test_matchup_analyzer():
    """Run matchup analyzer on 5 sample replays."""
    
    # Find sample replay files (prefer losses for analysis)
    replay_dir = Path("replay_analysis")
    
    replay_files = list(replay_dir.glob("gen9ou-*.json"))[:5]
    
    if not replay_files:
        logger.error("No replay files found for testing")
        return
    
    logger.info(f"Testing matchup analyzer on {len(replay_files)} replays")
    logger.info("=" * 80)
    
    results = []
    
    for i, replay_file in enumerate(replay_files, 1):
        logger.info(f"\n[{i}/5] Analyzing {replay_file.name}")
        logger.info("-" * 80)
        
        our_team, opp_team, result = extract_team_from_replay(replay_file)
        
        if not our_team or not opp_team:
            logger.warning(f"Could not extract teams from {replay_file.name}")
            continue
        
        logger.info(f"Our team ({len(our_team)} Pokemon): {', '.join(p['species'] for p in our_team)}")
        logger.info(f"Opponent team ({len(opp_team)} Pokemon): {', '.join(p['species'] for p in opp_team)}")
        logger.info(f"Battle result: {result.upper()}")
        
        # Generate gameplan (use fresh analysis, no cache for testing)
        try:
            gameplan = analyze_matchup(our_team, opp_team, use_cache=False)
            
            logger.info("\n📋 GENERATED GAMEPLAN:")
            logger.info(f"  Win Condition: {gameplan.win_condition}")
            logger.info(f"  Strategy: {gameplan.our_strategy}")
            logger.info(f"  Opponent Win Condition: {gameplan.opponent_win_condition}")
            logger.info(f"  Opponent Weaknesses: {', '.join(gameplan.opponent_weaknesses)}")
            logger.info(f"  Key Pivot Triggers:")
            for trigger in gameplan.key_pivot_triggers:
                logger.info(f"    - {trigger}")
            if gameplan.lead_preference:
                logger.info(f"  Lead Preference: {gameplan.lead_preference}")
            if gameplan.backup_plan:
                logger.info(f"  Backup Plan: {gameplan.backup_plan}")
            
            # Save gameplan for this replay
            output_file = Path("replay_analysis") / f"{replay_file.stem}_gameplan.json"
            with open(output_file, 'w') as f:
                json.dump(gameplan.to_dict(), f, indent=2)
            logger.info(f"\n✅ Saved gameplan to {output_file}")
            
            results.append({
                "replay": replay_file.name,
                "result": result,
                "gameplan": gameplan.to_dict(),
                "our_team_size": len(our_team),
                "opp_team_size": len(opp_team)
            })
        
        except Exception as e:
            logger.error(f"Failed to generate gameplan: {e}", exc_info=True)
            results.append({
                "replay": replay_file.name,
                "result": "error",
                "error": str(e)
            })
    
    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total replays analyzed: {len(results)}")
    successful = sum(1 for r in results if "gameplan" in r)
    logger.info(f"Successful gameplan generations: {successful}/{len(results)}")
    
    # Save full results
    summary_file = Path("replay_analysis/matchup_analyzer_test_results.json")
    with open(summary_file, 'w') as f:
        json.dump({
            "test_date": "2026-02-15",
            "replays_tested": len(results),
            "successful": successful,
            "results": results
        }, f, indent=2)
    
    logger.info(f"\n✅ Full test results saved to {summary_file}")
    
    # Quality assessment
    logger.info("\n📊 QUALITY ASSESSMENT:")
    logger.info("Review the generated gameplans and compare with actual battle outcomes.")
    logger.info("For each gameplan, assess:")
    logger.info("  1. Does the win condition make sense for the matchup?")
    logger.info("  2. Are opponent weaknesses accurately identified?")
    logger.info("  3. Would following this strategy have improved our chances?")
    logger.info("  4. Are pivot triggers specific and actionable?")
    logger.info("\nUse replay_analysis/gameplan_evaluation_template.md for detailed review.")


if __name__ == "__main__":
    test_matchup_analyzer()
