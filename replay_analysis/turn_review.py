#!/usr/bin/env python3
"""
Turn Review System - Extract critical decision points from replays
Posts specific turns to Discord for expert feedback
"""

import json
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TurnSnapshot:
    """Represents a critical turn for review"""
    turn_number: int
    bot_active: str
    bot_hp_percent: float
    opp_active: str
    opp_hp_percent: float
    bot_choice: str  # What the bot chose
    bot_team_status: str  # e.g., "4 healthy, 2 weak"
    opp_team_status: str
    field_conditions: List[str]  # Hazards, weather, etc.
    why_critical: str  # Why this turn matters
    replay_url: str
    alternative_options: List[Tuple[str, str]]  # (move/switch, reason to consider)


class TurnReviewer:
    """Analyzes replays to identify critical decision points"""
    
    def __init__(self):
        # Use path relative to this file to be cross-platform safe
        project_root = Path(__file__).parent.parent
        self.reviews_dir = project_root / "replay_analysis" / "turn_reviews"
        self.reviews_dir.mkdir(exist_ok=True)
        
    def extract_critical_turns(self, replay_data: Dict, replay_url: str) -> List[TurnSnapshot]:
        """
        Identify the most important decision points in a battle
        Returns up to 3 critical turns worth reviewing
        """
        critical_turns = []
        log_lines = replay_data.get("log", "").split("\n")
        
        bot_name = os.getenv("PS_USERNAME", "ALL CHUNG").strip()
        current_turn = 0
        
        # Track game state
        bot_active = None
        opp_active = None
        bot_team = {}  # Pokemon -> HP%
        opp_team = {}
        field_conditions = []
        turn_events = {}  # Turn -> events
        
        # Initialize turn 0 for pre-battle events
        turn_events[0] = {
            "moves": [],
            "switches": [],
            "faints": [],
            "hazards": [],
            "damage": []
        }
        
        for line in log_lines:
            line = line.strip()
            
            # Track turn number
            if line.startswith("|turn|"):
                current_turn = int(line.split("|")[2])
                turn_events[current_turn] = {
                    "moves": [],
                    "switches": [],
                    "faints": [],
                    "hazards": [],
                    "damage": []
                }
                
            # Track active Pokemon
            elif line.startswith("|switch|") or line.startswith("|drag|"):
                parts = line.split("|")
                player = parts[2].split(":")[0]
                pokemon = parts[2].split(":")[1].strip().split(",")[0]
                hp_info = parts[3] if len(parts) > 3 else "100/100"
                
                # Ensure turn exists in dictionary
                if current_turn not in turn_events:
                    turn_events[current_turn] = {
                        "moves": [],
                        "switches": [],
                        "faints": [],
                        "hazards": [],
                        "damage": []
                    }
                
                if player == "p1":  # Bot
                    bot_active = pokemon
                    turn_events[current_turn]["switches"].append(("bot", pokemon))
                else:
                    opp_active = pokemon
                    turn_events[current_turn]["switches"].append(("opp", pokemon))
                    
            # Track moves
            elif line.startswith("|move|"):
                parts = line.split("|")
                player = parts[2].split(":")[0]
                move = parts[3].lower()
                
                # Ensure turn exists
                if current_turn not in turn_events:
                    turn_events[current_turn] = {
                        "moves": [],
                        "switches": [],
                        "faints": [],
                        "hazards": [],
                        "damage": []
                    }
                
                if player == "p1":
                    turn_events[current_turn]["moves"].append(("bot", move))
                else:
                    turn_events[current_turn]["moves"].append(("opp", move))
                    
            # Track faints (CRITICAL)
            elif line.startswith("|faint|"):
                parts = line.split("|")
                player = parts[2].split(":")[0]
                pokemon = parts[2].split(":")[1].strip()
                
                # Ensure turn exists
                if current_turn not in turn_events:
                    turn_events[current_turn] = {
                        "moves": [],
                        "switches": [],
                        "faints": [],
                        "hazards": [],
                        "damage": []
                    }
                
                if player == "p1":
                    turn_events[current_turn]["faints"].append(("bot", pokemon))
                else:
                    turn_events[current_turn]["faints"].append(("opp", pokemon))
                    
            # Track hazards
            elif "|-sidestart|" in line and ("Stealth Rock" in line or "Spikes" in line):
                # Ensure turn exists
                if current_turn not in turn_events:
                    turn_events[current_turn] = {
                        "moves": [],
                        "switches": [],
                        "faints": [],
                        "hazards": [],
                        "damage": []
                    }
                
                if "p1" in line:
                    field_conditions.append(f"Bot has hazards")
                    turn_events[current_turn]["hazards"].append("bot")
                else:
                    turn_events[current_turn]["hazards"].append("opp")
                    
        # Now identify critical turns
        # Priority:
        # 1. Turns where bot's Pokemon fainted (switching error?)
        # 2. Turns where opponent's Pokemon fainted (good decision)
        # 3. Major setup attempts
        # 4. Critical switches
        
        for turn, events in turn_events.items():
            if turn == 0:
                continue
                
            # Bot's Pokemon fainted - CRITICAL
            if any(faint[0] == "bot" for faint in events["faints"]):
                pokemon = [f[1] for f in events["faints"] if f[0] == "bot"][0]
                critical_turns.append(TurnSnapshot(
                    turn_number=turn,
                    bot_active=bot_active or "Unknown",
                    bot_hp_percent=0,  # Fainted
                    opp_active=opp_active or "Unknown",
                    opp_hp_percent=100,  # Estimate
                    bot_choice=f"{pokemon} fainted",
                    bot_team_status="Unknown",
                    opp_team_status="Unknown",
                    field_conditions=field_conditions.copy(),
                    why_critical=f"❌ {pokemon} fainted - should we have switched out earlier?",
                    replay_url=replay_url,
                    alternative_options=[
                        ("Switch out earlier", "Preserve this Pokemon"),
                        ("Different move choice", "Deal more damage before fainting")
                    ]
                ))
                
            # Bot made a switch (was it good?)
            if any(switch[0] == "bot" for switch in events["switches"]):
                pokemon = [s[1] for s in events["switches"] if s[0] == "bot"][0]
                critical_turns.append(TurnSnapshot(
                    turn_number=turn,
                    bot_active=pokemon,
                    bot_hp_percent=100,  # Just switched in
                    opp_active=opp_active or "Unknown",
                    opp_hp_percent=100,
                    bot_choice=f"Switched to {pokemon}",
                    bot_team_status="Unknown",
                    opp_team_status="Unknown",
                    field_conditions=field_conditions.copy(),
                    why_critical=f"🔄 Switched to {pokemon} - was this the right play?",
                    replay_url=replay_url,
                    alternative_options=[
                        ("Stay in and attack", "Maintain momentum"),
                        ("Switch to different Pokemon", "Better matchup")
                    ]
                ))
                
        # Return top 3 most critical turns
        return critical_turns[:3]
    
    def format_for_discord(self, turn: TurnSnapshot) -> str:
        """Format a turn review as a Discord message"""
        msg = f"📋 **Turn {turn.turn_number} Review**\n"
        msg += f"🔗 Replay: {turn.replay_url}\n\n"
        
        msg += f"**Board State:**\n"
        msg += f"• Bot: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP)\n"
        msg += f"• Opponent: {turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)\n"
        
        if turn.field_conditions:
            msg += f"• Field: {', '.join(turn.field_conditions)}\n"
        
        msg += f"\n**Bot chose:** {turn.bot_choice}\n"
        msg += f"**Why critical:** {turn.why_critical}\n\n"
        
        msg += f"❓ **Was this the right play?**\n"
        for option, reason in turn.alternative_options:
            msg += f"• Alternative: {option} ({reason})\n"
        
        msg += f"\n*Reply with your feedback on this turn!*"
        
        return msg
    
    def save_turn_review(self, turn: TurnSnapshot):
        """Save turn review for tracking feedback"""
        filename = f"turn_{turn.turn_number}_{turn.replay_url.split('/')[-1]}.json"
        filepath = self.reviews_dir / filename
        
        with open(filepath, 'w') as f:
            json.dump({
                "turn": turn.turn_number,
                "bot_choice": turn.bot_choice,
                "why_critical": turn.why_critical,
                "replay_url": turn.replay_url,
                "alternatives": turn.alternative_options,
                "feedback": None  # Will be filled in when Ryan responds
            }, f, indent=2)
    
    def analyze_and_post(self, replay_data: Dict, replay_url: str) -> List[str]:
        """
        Full pipeline: extract critical turns and format for Discord
        Returns list of formatted messages ready to post
        """
        critical_turns = self.extract_critical_turns(replay_data, replay_url)
        
        messages = []
        for turn in critical_turns:
            self.save_turn_review(turn)
            messages.append(self.format_for_discord(turn))
            
        return messages


if __name__ == "__main__":
    # Test with example
    reviewer = TurnReviewer()
    
    # Example usage
    example_turn = TurnSnapshot(
        turn_number=7,
        bot_active="Gliscor",
        bot_hp_percent=78,
        opp_active="Zamazenta",
        opp_hp_percent=100,
        bot_choice="Switched to Skarmory",
        bot_team_status="4 healthy, 2 weak",
        opp_team_status="5 healthy, 1 weak",
        field_conditions=["Stealth Rock (opponent's side)"],
        why_critical="🔄 Switched to Skarmory - was this the right matchup?",
        replay_url="https://replay.pokemonshowdown.com/example",
        alternative_options=[
            ("Stay in with Gliscor", "Earthquake does decent damage"),
            ("Switch to Gholdengo", "Better special bulk vs Zamazenta")
        ]
    )
    
    print(reviewer.format_for_discord(example_turn))
