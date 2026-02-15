#!/usr/bin/env python3
"""
OBS Battle Monitor File Updater

Utility to update battle data files that OBS reads from.
Can be imported by Fouler Play bot or used standalone for testing.
"""

import os
import sys
from pathlib import Path
from typing import Literal, Optional

# Determine platform and base path
if sys.platform == "win32":
    BASE_PATH = Path(r"C:\Users\Ryan\projects\fouler-play\logs")
    BOT_NAME = "chung"
else:
    BASE_PATH = Path("/home/ryan/projects/fouler-play/logs")
    BOT_NAME = "deku"

# Ensure logs directory exists
BASE_PATH.mkdir(parents=True, exist_ok=True)

# Type aliases
Status = Literal["searching", "battling", "won", "lost"]
Team = Literal["stall", "pivot", "dondozo"]


class BattleMonitor:
    """Manages battle state files for OBS display."""
    
    def __init__(self, bot_name: str = None, base_path: Path = None):
        """
        Initialize battle monitor.
        
        Args:
            bot_name: 'deku' or 'chung' (auto-detected by default)
            base_path: Path to logs directory (auto-detected by default)
        """
        self.bot_name = bot_name or BOT_NAME
        self.base_path = base_path or BASE_PATH
        self.battle_files = {
            1: self.base_path / f"{self.bot_name}-battle-1.txt",
            2: self.base_path / f"{self.bot_name}-battle-2.txt",
            3: self.base_path / f"{self.bot_name}-battle-3.txt",
        }
    
    def update_battle(
        self,
        slot: int,
        opponent: Optional[str] = None,
        team: Optional[Team] = None,
        status: Optional[Status] = None,
        elo: Optional[int] = None,
        turns: Optional[int] = None,
        record: Optional[str] = None,
    ):
        """
        Update battle file with new data.
        
        Args:
            slot: Battle slot (1, 2, or 3)
            opponent: Opponent username
            team: Team name (stall, pivot, dondozo)
            status: Battle status (searching, battling, won, lost)
            elo: Current ELO rating
            turns: Current turn count
            record: Win-loss record (format: "W-L")
        """
        if slot not in [1, 2, 3]:
            raise ValueError(f"Invalid slot: {slot}. Must be 1, 2, or 3.")
        
        filepath = self.battle_files[slot]
        
        # Read current state
        current = self.read_battle(slot)
        
        # Update with provided values
        if opponent is not None:
            current["opponent"] = opponent
        if team is not None:
            current["team"] = team
        if status is not None:
            current["status"] = status
        if elo is not None:
            current["elo"] = elo
        if turns is not None:
            current["turns"] = turns
        if record is not None:
            current["record"] = record
        
        # Write atomically
        self._write_atomic(filepath, current)
    
    def read_battle(self, slot: int) -> dict:
        """
        Read current battle state from file.
        
        Args:
            slot: Battle slot (1, 2, or 3)
        
        Returns:
            dict with keys: opponent, team, status, elo, turns, record
        """
        if slot not in [1, 2, 3]:
            raise ValueError(f"Invalid slot: {slot}. Must be 1, 2, or 3.")
        
        filepath = self.battle_files[slot]
        
        if not filepath.exists():
            # Return default state
            return {
                "opponent": "Searching...",
                "team": ["stall", "pivot", "dondozo"][slot - 1],
                "status": "searching",
                "elo": 1400,
                "turns": 0,
                "record": "0-0",
            }
        
        data = {}
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    key, value = line.split("=", 1)
                    data[key] = value
        
        # Ensure all fields exist
        return {
            "opponent": data.get("opponent", "Searching..."),
            "team": data.get("team", "unknown"),
            "status": data.get("status", "searching"),
            "elo": int(data.get("elo", 1400)),
            "turns": int(data.get("turns", 0)),
            "record": data.get("record", "0-0"),
        }
    
    def _write_atomic(self, filepath: Path, data: dict):
        """Write data to file atomically (temp + rename)."""
        temp_path = filepath.with_suffix(".tmp")
        
        with open(temp_path, "w") as f:
            f.write(f"opponent={data['opponent']}\n")
            f.write(f"team={data['team']}\n")
            f.write(f"status={data['status']}\n")
            f.write(f"elo={data['elo']}\n")
            f.write(f"turns={data['turns']}\n")
            f.write(f"record={data['record']}\n")
        
        # Atomic replace
        temp_path.replace(filepath)
    
    def reset_battle(self, slot: int, team: Team):
        """
        Reset battle to searching state.
        
        Args:
            slot: Battle slot (1, 2, or 3)
            team: Team name (stall, pivot, dondozo)
        """
        current = self.read_battle(slot)
        self.update_battle(
            slot,
            opponent="Searching...",
            team=team,
            status="searching",
            turns=0,
            # Keep ELO and record
        )
    
    def set_searching(self, slot: int):
        """Set battle to searching state (keeps ELO/record)."""
        self.update_battle(slot, opponent="Searching...", status="searching", turns=0)
    
    def set_battling(self, slot: int, opponent: str, turn: int = 1):
        """Set battle to active battling state."""
        self.update_battle(slot, opponent=opponent, status="battling", turns=turn)
    
    def set_won(self, slot: int, new_elo: int, new_record: str):
        """Set battle to won state and update stats."""
        self.update_battle(slot, status="won", elo=new_elo, record=new_record)
    
    def set_lost(self, slot: int, new_elo: int, new_record: str):
        """Set battle to lost state and update stats."""
        self.update_battle(slot, status="lost", elo=new_elo, record=new_record)
    
    def increment_turn(self, slot: int):
        """Increment turn counter for active battle."""
        current = self.read_battle(slot)
        self.update_battle(slot, turns=current["turns"] + 1)


def test_mode():
    """Populate all battle files with realistic test data."""
    monitor = BattleMonitor()
    
    # DEKU/CHUNG battle 1: Active battle
    monitor.update_battle(
        1,
        opponent="CoolTrainer123",
        team="stall",
        status="battling",
        elo=1450,
        turns=12,
        record="15-8",
    )
    
    # DEKU/CHUNG battle 2: Just won
    monitor.update_battle(
        2,
        opponent="PikachuMaster",
        team="pivot",
        status="won",
        elo=1472,
        turns=24,
        record="16-8",
    )
    
    # DEKU/CHUNG battle 3: Searching
    monitor.update_battle(
        3,
        opponent="Searching...",
        team="dondozo",
        status="searching",
        elo=1401,
        turns=0,
        record="10-5",
    )
    
    print(f"✅ Test data written to {monitor.base_path}")
    print(f"   Bot: {monitor.bot_name}")
    print(f"   Files: {', '.join([f.name for f in monitor.battle_files.values()])}")


def reset_mode():
    """Reset all battles to searching state."""
    monitor = BattleMonitor()
    teams = ["stall", "pivot", "dondozo"]
    
    for slot, team in enumerate(teams, 1):
        monitor.reset_battle(slot, team)
    
    print(f"✅ All battles reset to searching state")


def demo_mode():
    """Demonstrate a full battle lifecycle."""
    import time
    
    monitor = BattleMonitor()
    slot = 1
    team = "stall"
    
    print(f"Demo: Battle lifecycle for {monitor.bot_name} slot {slot} ({team})")
    print()
    
    # Start searching
    print("1. Searching for opponent...")
    monitor.reset_battle(slot, team)
    monitor.update_battle(slot, elo=1400, record="0-0")
    time.sleep(2)
    
    # Battle starts
    print("2. Battle started against 'TestOpponent'")
    monitor.set_battling(slot, "TestOpponent", turn=1)
    time.sleep(1)
    
    # Simulate turns
    for turn in range(2, 11):
        print(f"3. Turn {turn}...")
        monitor.increment_turn(slot)
        time.sleep(0.5)
    
    # Win the battle
    print("4. Battle won! ELO: 1400 → 1425")
    monitor.set_won(slot, new_elo=1425, new_record="1-0")
    time.sleep(3)
    
    # Back to searching
    print("5. Returning to search...")
    monitor.set_searching(slot)
    
    print()
    print("✅ Demo complete. Check OBS for updates.")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="OBS Battle Monitor Updater")
    parser.add_argument(
        "mode",
        choices=["test", "reset", "demo"],
        help="Operation mode: test (populate test data), reset (clear all), demo (simulate battle)",
    )
    
    args = parser.parse_args()
    
    if args.mode == "test":
        test_mode()
    elif args.mode == "reset":
        reset_mode()
    elif args.mode == "demo":
        demo_mode()
