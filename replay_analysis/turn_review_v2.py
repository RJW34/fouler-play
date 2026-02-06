#!/usr/bin/env python3
"""
Turn Review V2 - Context-aware battle analysis with type matchups and strategic reasoning
"""

from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Optional
import json

# Import Pokemon type effectiveness data
import sys
sys.path.append(str(Path(__file__).parent.parent))
from data import all_move_json, pokedex
import constants


@dataclass
class TurnContext:
    """Rich context for a critical turn"""
    turn_number: int
    bot_active: str
    bot_hp_percent: float
    opp_active: str
    opp_hp_percent: float
    bot_choice: str
    bot_team_status: str
    opp_team_status: str
    field_conditions: List[str]
    replay_url: str
    
    # Enhanced context
    type_matchup: str  # "Resists", "Weak to", "Neutral", "Immune"
    remaining_threats: List[str]  # Opponent Pokemon that threaten bot
    remaining_checks: List[str]  # Bot Pokemon that check opponent
    specific_reasoning: str  # Why this turn matters
    concrete_alternatives: List[Tuple[str, str]]  # Context-aware options


def get_type_effectiveness(attacker_types: List[str], defender_types: List[str]) -> float:
    """Calculate type effectiveness multiplier"""
    # Simplified type chart - expand this for full accuracy
    type_chart = {
        # Format: (attacking_type, defending_type): multiplier
        ("Fire", "Grass"): 2.0, ("Fire", "Water"): 0.5, ("Fire", "Steel"): 2.0,
        ("Water", "Fire"): 2.0, ("Water", "Ground"): 2.0, ("Water", "Grass"): 0.5,
        ("Grass", "Water"): 2.0, ("Grass", "Fire"): 0.5, ("Grass", "Flying"): 0.5,
        ("Electric", "Water"): 2.0, ("Electric", "Ground"): 0.0, ("Electric", "Grass"): 0.5,
        ("Fighting", "Normal"): 2.0, ("Fighting", "Dark"): 2.0, ("Fighting", "Flying"): 0.5,
        ("Steel", "Fairy"): 2.0, ("Steel", "Fire"): 0.5, ("Steel", "Water"): 0.5,
        ("Ghost", "Normal"): 0.0, ("Ghost", "Ghost"): 2.0, ("Ghost", "Dark"): 0.5,
        ("Dark", "Psychic"): 2.0, ("Dark", "Ghost"): 2.0, ("Dark", "Fairy"): 0.5,
        ("Fairy", "Dragon"): 2.0, ("Fairy", "Dark"): 2.0, ("Fairy", "Steel"): 0.5,
        ("Dragon", "Dragon"): 2.0, ("Dragon", "Fairy"): 0.0, ("Dragon", "Steel"): 0.5,
        ("Flying", "Grass"): 2.0, ("Flying", "Fighting"): 2.0, ("Flying", "Electric"): 0.5,
        ("Ground", "Fire"): 2.0, ("Ground", "Electric"): 2.0, ("Ground", "Flying"): 0.0,
        ("Rock", "Flying"): 2.0, ("Rock", "Fire"): 2.0, ("Rock", "Fighting"): 0.5,
        ("Bug", "Grass"): 2.0, ("Bug", "Fire"): 0.5, ("Bug", "Flying"): 0.5,
        ("Poison", "Grass"): 2.0, ("Poison", "Fairy"): 2.0, ("Poison", "Steel"): 0.0,
        ("Ice", "Dragon"): 2.0, ("Ice", "Flying"): 2.0, ("Ice", "Fire"): 0.5,
        ("Psychic", "Fighting"): 2.0, ("Psychic", "Dark"): 0.0, ("Psychic", "Steel"): 0.5,
    }
    
    effectiveness = 1.0
    for att_type in attacker_types:
        for def_type in defender_types:
            key = (att_type, def_type)
            if key in type_chart:
                effectiveness *= type_chart[key]
    
    return effectiveness


def analyze_matchup(bot_pokemon: str, opp_pokemon: str, bot_team: str, opp_team: str) -> dict:
    """Analyze the type matchup and strategic positioning"""
    
    # Get Pokemon data (simplified - expand with actual pokedex)
    # For now, return placeholder analysis
    
    # Parse team status to get available Pokemon
    bot_alive = [p.split()[0] for p in bot_team.split("|") if "fainted" not in p.lower()]
    opp_alive = [p.split()[0] for p in opp_team.split("|") if "fainted" not in p.lower()]
    
    return {
        "defensive_matchup": "Neutral",  # "Resists", "Weak", "Neutral", "Immune"
        "offensive_matchup": "Neutral",  # Can we threaten them?
        "remaining_threats": opp_alive,
        "remaining_checks": bot_alive,
        "should_preserve": False,  # Is this Pokemon critical for a specific threat?
    }


def generate_faint_review(
    turn: int,
    pokemon: str,
    move_used: str,
    opp_active: str,
    bot_team: str,
    opp_team: str,
    replay_url: str
) -> TurnContext:
    """Generate context-aware review for a Pokemon fainting"""
    
    matchup = analyze_matchup(pokemon, opp_active, bot_team, opp_team)
    
    # Determine specific reasoning
    if matchup["should_preserve"]:
        reasoning = f"{pokemon} was critical for checking {', '.join(matchup['remaining_threats'][:2])} — lost an important answer"
    else:
        reasoning = f"{pokemon} went down vs {opp_active} — could have preserved HP or dealt more damage first"
    
    # Generate context-aware alternatives
    alternatives = []
    
    if matchup["defensive_matchup"] == "Weak":
        alternatives.append((
            "Switch out on the turn before",
            f"{pokemon} was weak to {opp_active}'s attacks — switching preserved it for later"
        ))
    
    if move_used != "unknown":
        alternatives.append((
            f"Use different move than {move_used}",
            "Check if a different move would have KO'd or forced a switch"
        ))
    
    if matchup["remaining_checks"]:
        alternatives.append((
            f"Switch to {matchup['remaining_checks'][0]}",
            f"Better defensive matchup vs {opp_active}"
        ))
    
    # If no specific alternatives, give generic
    if not alternatives:
        alternatives = [
            ("Preserve HP by switching earlier", "Save for late-game cleanup or specific threats"),
            ("Deal more damage before going down", "Chip opponent to secure KO later"),
        ]
    
    return TurnContext(
        turn_number=turn,
        bot_active=pokemon,
        bot_hp_percent=0,
        opp_active=opp_active,
        opp_hp_percent=100,  # Approximate
        bot_choice=f"{move_used}" if move_used != "unknown" else "fainted",
        bot_team_status=bot_team,
        opp_team_status=opp_team,
        field_conditions=[],
        replay_url=replay_url,
        type_matchup=matchup["defensive_matchup"],
        remaining_threats=matchup["remaining_threats"][:3],
        remaining_checks=matchup["remaining_checks"][:3],
        specific_reasoning=reasoning,
        concrete_alternatives=alternatives[:3],
    )


def generate_switch_review(
    turn: int,
    switched_to: str,
    opp_active: str,
    bot_hp: float,
    opp_hp: float,
    bot_team: str,
    opp_team: str,
    replay_url: str
) -> TurnContext:
    """Generate context-aware review for a switch"""
    
    matchup = analyze_matchup(switched_to, opp_active, bot_team, opp_team)
    
    # Determine specific reasoning based on matchup
    if matchup["defensive_matchup"] == "Resists":
        reasoning = f"Good switch: {switched_to} resists {opp_active}'s attacks — safe positioning"
    elif matchup["defensive_matchup"] == "Weak":
        reasoning = f"Risky switch: {switched_to} is weak to {opp_active} — could take heavy damage"
    elif matchup["offensive_matchup"] == "Good":
        reasoning = f"{switched_to} threatens {opp_active} — forces switch or trades favorably"
    else:
        reasoning = f"{switched_to} vs {opp_active} — neutral matchup, unclear advantage"
    
    # Generate alternatives
    alternatives = []
    
    if matchup["defensive_matchup"] == "Weak" and matchup["remaining_checks"]:
        alternatives.append((
            f"Switch to {matchup['remaining_checks'][0]} instead",
            f"Better defensive matchup vs {opp_active}"
        ))
    
    alternatives.append((
        "Stay in and attack",
        "Maintain momentum and deal damage before they can react"
    ))
    
    if matchup["should_preserve"]:
        alternatives.append((
            "Save this Pokemon for later",
            f"{switched_to} is critical for {matchup['remaining_threats'][0]} — avoid unnecessary risk"
        ))
    
    # Default
    if not alternatives:
        alternatives = [
            ("Aggressive play", "Stay in and apply pressure"),
            ("Conservative switch", "Preserve HP for important matchups later"),
        ]
    
    return TurnContext(
        turn_number=turn,
        bot_active=switched_to,
        bot_hp_percent=bot_hp,
        opp_active=opp_active,
        opp_hp_percent=opp_hp,
        bot_choice=f"Switched to {switched_to}",
        bot_team_status=bot_team,
        opp_team_status=opp_team,
        field_conditions=[],
        replay_url=replay_url,
        type_matchup=matchup["defensive_matchup"],
        remaining_threats=matchup["remaining_threats"][:3],
        remaining_checks=matchup["remaining_checks"][:3],
        specific_reasoning=reasoning,
        concrete_alternatives=alternatives[:3],
    )


def format_compact_review(ctx: TurnContext) -> str:
    """Format turn review in compact, readable Discord format"""
    
    # Header
    msg = f"**Turn {ctx.turn_number}**: {ctx.bot_choice}\n"
    msg += f"🔗 {ctx.replay_url}\n\n"
    
    # Compact board state
    msg += f"**Matchup**: {ctx.bot_active} ({ctx.bot_hp_percent:.0f}%) vs {ctx.opp_active} ({ctx.opp_hp_percent:.0f}%)\n"
    
    if ctx.type_matchup != "Neutral":
        msg += f"**Type**: {ctx.type_matchup}\n"
    
    # Critical reasoning (the key improvement)
    msg += f"\n💡 **{ctx.specific_reasoning}**\n"
    
    # Compact team status
    msg += f"\n**Remaining**:\n"
    msg += f"• Us: {', '.join(ctx.remaining_checks) if ctx.remaining_checks else 'N/A'}\n"
    msg += f"• Them: {', '.join(ctx.remaining_threats) if ctx.remaining_threats else 'N/A'}\n"
    
    # Alternatives (context-aware!)
    msg += f"\n❓ **Better options?**\n"
    for i, (option, reason) in enumerate(ctx.concrete_alternatives, 1):
        msg += f"{i}. **{option}** — {reason}\n"
    
    return msg


if __name__ == "__main__":
    # Test with sample data
    ctx = generate_faint_review(
        turn=5,
        pokemon="Toxapex",
        move_used="Scald",
        opp_active="Landorus-Therian",
        bot_team="Gliscor 78% | Skarmory 100% | Toxapex fainted",
        opp_team="Landorus-Therian 45% | Kingambit 100%",
        replay_url="https://replay.pokemonshowdown.com/test"
    )
    
    print(format_compact_review(ctx))
