#!/usr/bin/env python3
"""
Pre-Flight Verification Script for Fouler Play
Must be run before every batch to catch configuration issues ahead of time.

Usage:
    python pre_flight_check.py
    python pre_flight_check.py --run-count 15 --num-workers 3
    
Import in launch.py:
    from pre_flight_check import run_pre_flight_check
    if not run_pre_flight_check():
        sys.exit(1)
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Tuple, Optional

# Colors for output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def check_mark(passed: bool) -> str:
    """Return colored check mark or X"""
    if passed:
        return f"{Colors.GREEN}✅{Colors.RESET}"
    return f"{Colors.RED}❌{Colors.RESET}"


class PreFlightChecker:
    def __init__(self, run_count: int = None, num_workers: int = None):
        self.repo_root = Path(__file__).resolve().parent
        self.env_file = self.repo_root / ".env"
        self.battle_stats_file = self.repo_root / "battle_stats.json"
        self.teams_dir = self.repo_root / "teams"
        
        # Load .env
        self.env = self._load_env()
        
        # Override with CLI args if provided
        self.run_count = run_count if run_count is not None else int(self.env.get("PS_RUN_COUNT", "999999"))
        self.num_workers = num_workers if num_workers is not None else int(self.env.get("MAX_CONCURRENT_BATTLES", "1"))
        
        self.failures = []
        self.warnings = []
        
    def _load_env(self) -> dict:
        """Load .env file into dictionary"""
        env = {}
        if not self.env_file.exists():
            return env
        
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Strip inline comments
            if "#" in val:
                val = val.split("#")[0].strip()
            # Remove quotes
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            env[key] = val
        return env
    
    def _calculate_quota(self, run_count: int, num_workers: int) -> List[int]:
        """Calculate per-worker quotas exactly as run.py does"""
        if num_workers > 1 and run_count <= 999999:
            base = run_count // num_workers
            remainder = run_count % num_workers
            quotas = [base + (1 if i < remainder else 0) for i in range(num_workers)]
            return quotas
        else:
            return [0] * num_workers  # 0 = no per-worker limit
    
    def check_quota_verification(self) -> bool:
        """1. Quota Verification - test quota logic with known values"""
        print(f"\n{Colors.BOLD}1. QUOTA VERIFICATION{Colors.RESET}")
        
        test_cases = [
            (15, 3, [5, 5, 5]),
            (20, 3, [7, 7, 6]),
            (999999, 3, [333333, 333333, 333333]),
        ]
        
        all_passed = True
        for run_count, workers, expected in test_cases:
            calculated = self._calculate_quota(run_count, workers)
            passed = calculated == expected
            all_passed = all_passed and passed
            
            total = sum(calculated)
            status = check_mark(passed)
            
            print(f"  {status} {run_count} battles / {workers} workers = {calculated} (sum={total})")
            
            if not passed:
                self.failures.append(
                    f"Quota calculation mismatch for {run_count}/{workers}: "
                    f"expected {expected}, got {calculated}"
                )
        
        # Show actual batch quotas
        actual_quotas = self._calculate_quota(self.run_count, self.num_workers)
        total = sum(actual_quotas) if actual_quotas[0] != 0 else self.run_count
        
        print(f"\n  {Colors.BLUE}Batch Config:{Colors.RESET}")
        print(f"    Run count: {self.run_count}")
        print(f"    Workers: {self.num_workers}")
        print(f"    Per-worker quotas: {actual_quotas}")
        print(f"    Expected total battles: {total}")
        
        # Sanity check: sum should equal run_count (unless infinite mode)
        if actual_quotas[0] != 0:
            if sum(actual_quotas) != self.run_count:
                all_passed = False
                self.failures.append(
                    f"Quota sum mismatch: quotas sum to {sum(actual_quotas)} "
                    f"but run_count is {self.run_count}"
                )
                print(f"  {check_mark(False)} Sum verification FAILED")
        
        return all_passed
    
    def check_team_rotation(self) -> bool:
        """2. Team Rotation - verify team files exist and are valid"""
        print(f"\n{Colors.BOLD}2. TEAM ROTATION{Colors.RESET}")
        
        team_names_str = self.env.get("TEAM_NAMES")
        if not team_names_str:
            print(f"  {Colors.YELLOW}⚠️  No TEAM_NAMES configured (single team mode){Colors.RESET}")
            self.warnings.append("TEAM_NAMES not set - using single team mode")
            return True
        
        team_names = [t.strip() for t in team_names_str.split(",")]
        print(f"  Found {len(team_names)} configured teams:")
        
        all_passed = True
        valid_teams = []
        
        for i, team_name in enumerate(team_names):
            team_path = self.teams_dir / team_name
            
            # Check if path exists
            if not team_path.exists():
                print(f"  {check_mark(False)} Team {i}: {team_name} - PATH NOT FOUND")
                self.failures.append(f"Team file/dir not found: {team_path}")
                all_passed = False
                continue
            
            # Check if it's a valid team file or directory
            if team_path.is_file():
                # Single team file
                try:
                    content = team_path.read_text(encoding="utf-8")
                    # Basic validation: should contain Pokemon Showdown format markers
                    if not any(marker in content for marker in ["===", "@", "Ability:", "EVs:", "-"]):
                        print(f"  {check_mark(False)} Team {i}: {team_name} - INVALID FORMAT")
                        self.failures.append(f"Team file appears invalid: {team_name}")
                        all_passed = False
                    else:
                        print(f"  {check_mark(True)} Team {i}: {team_name} (file)")
                        valid_teams.append(team_name)
                except Exception as e:
                    print(f"  {check_mark(False)} Team {i}: {team_name} - READ ERROR: {e}")
                    self.failures.append(f"Failed to read team: {team_name}")
                    all_passed = False
            
            elif team_path.is_dir():
                # Directory of team files
                team_files = [f for f in team_path.glob("*") if f.is_file() and not f.name.startswith(".")]
                if not team_files:
                    print(f"  {check_mark(False)} Team {i}: {team_name} - EMPTY DIRECTORY")
                    self.failures.append(f"No team files in directory: {team_name}")
                    all_passed = False
                else:
                    print(f"  {check_mark(True)} Team {i}: {team_name} (directory with {len(team_files)} files)")
                    valid_teams.append(team_name)
            else:
                print(f"  {check_mark(False)} Team {i}: {team_name} - NOT FILE OR DIR")
                self.failures.append(f"Invalid team path type: {team_name}")
                all_passed = False
        
        if not valid_teams:
            self.failures.append("No valid teams found!")
            return False
        
        # Simulate team assignment for 3 workers × 15 battles
        print(f"\n  {Colors.BLUE}Simulated Assignment (3 workers × 15 battles):{Colors.RESET}")
        
        simulation_battles = 15
        simulation_workers = 3
        per_worker = simulation_battles // simulation_workers  # 5 each
        
        for worker_id in range(simulation_workers):
            assignments = []
            for battle_num in range(per_worker):
                global_battle_num = worker_id + battle_num * simulation_workers
                team_idx = global_battle_num % len(valid_teams)
                assignments.append(valid_teams[team_idx])
            
            print(f"    Worker {worker_id}: {assignments}")
        
        return all_passed
    
    def check_config_sanity(self) -> bool:
        """3. Config Sanity - verify critical environment variables"""
        print(f"\n{Colors.BOLD}3. CONFIG SANITY{Colors.RESET}")
        
        all_passed = True
        
        # PS_USERNAME
        username = self.env.get("PS_USERNAME")
        if username:
            print(f"  {check_mark(True)} PS_USERNAME: {username}")
        else:
            print(f"  {check_mark(False)} PS_USERNAME: NOT SET")
            self.failures.append("PS_USERNAME not configured")
            all_passed = False
        
        # PS_PASSWORD
        password = self.env.get("PS_PASSWORD")
        if password:
            masked = password[:3] + "*" * (len(password) - 3) if len(password) > 3 else "***"
            print(f"  {check_mark(True)} PS_PASSWORD: {masked}")
        else:
            print(f"  {check_mark(False)} PS_PASSWORD: NOT SET")
            self.failures.append("PS_PASSWORD not configured")
            all_passed = False
        
        # BOT_LOG_LEVEL
        log_level = self.env.get("BOT_LOG_LEVEL", "INFO")
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level.upper() in valid_levels:
            print(f"  {check_mark(True)} BOT_LOG_LEVEL: {log_level}")
        else:
            print(f"  {check_mark(False)} BOT_LOG_LEVEL: {log_level} (invalid, must be one of {valid_levels})")
            self.warnings.append(f"Invalid BOT_LOG_LEVEL: {log_level}")
        
        # MAX_MCTS_BATTLES
        max_mcts = self.env.get("MAX_MCTS_BATTLES", "1")
        try:
            mcts_val = int(max_mcts)
            if mcts_val > 0:
                print(f"  {check_mark(True)} MAX_MCTS_BATTLES: {mcts_val}")
            else:
                print(f"  {check_mark(False)} MAX_MCTS_BATTLES: {mcts_val} (must be > 0)")
                self.failures.append(f"MAX_MCTS_BATTLES must be > 0, got {mcts_val}")
                all_passed = False
        except ValueError:
            print(f"  {check_mark(False)} MAX_MCTS_BATTLES: {max_mcts} (not a number)")
            self.failures.append(f"MAX_MCTS_BATTLES is not a number: {max_mcts}")
            all_passed = False
        
        # PS_FORMAT
        ps_format = self.env.get("PS_FORMAT", "gen9ou")
        valid_formats = ["gen9ou", "gen9randombattle", "gen9vgc2024"]
        if ps_format == "gen9ou":
            print(f"  {check_mark(True)} PS_FORMAT: {ps_format}")
        else:
            # Warn but don't fail - other formats might be valid
            print(f"  {check_mark(True)} PS_FORMAT: {ps_format} (non-standard)")
            self.warnings.append(f"Using non-standard format: {ps_format}")
        
        return all_passed
    
    def check_battle_stats(self) -> bool:
        """4. Battle Stats - verify battle_stats.json is valid"""
        print(f"\n{Colors.BOLD}4. BATTLE STATS{Colors.RESET}")
        
        if not self.battle_stats_file.exists():
            print(f"  {Colors.YELLOW}⚠️  battle_stats.json does not exist (will be created on first battle){Colors.RESET}")
            self.warnings.append("battle_stats.json not found - fresh start")
            return True
        
        all_passed = True
        
        # Check if parseable
        try:
            with open(self.battle_stats_file, "r") as f:
                data = json.load(f)
            print(f"  {check_mark(True)} battle_stats.json is valid JSON")
        except json.JSONDecodeError as e:
            print(f"  {check_mark(False)} battle_stats.json is CORRUPTED: {e}")
            self.failures.append(f"battle_stats.json parse error: {e}")
            return False
        
        # Verify structure
        if "battles" not in data:
            print(f"  {check_mark(False)} battle_stats.json missing 'battles' key")
            self.failures.append("battle_stats.json has invalid structure")
            all_passed = False
        else:
            battles = data["battles"]
            if not isinstance(battles, list):
                print(f"  {check_mark(False)} 'battles' is not a list")
                self.failures.append("battle_stats.json 'battles' is not a list")
                all_passed = False
            else:
                print(f"  {check_mark(True)} Found {len(battles)} recorded battles")
                
                # Calculate totals
                wins = sum(1 for b in battles if b.get("result") == "win")
                losses = sum(1 for b in battles if b.get("result") == "loss")
                
                print(f"    Total battles: {len(battles)}")
                print(f"    Wins: {wins}")
                print(f"    Losses: {losses}")
                print(f"    Win rate: {wins/(wins+losses)*100:.1f}%" if (wins+losses) > 0 else "    Win rate: N/A")
                
                # Per-team breakdown
                team_stats = {}
                for battle in battles:
                    team = battle.get("team_file", "unknown")
                    result = battle.get("result")
                    if team not in team_stats:
                        team_stats[team] = {"wins": 0, "losses": 0}
                    if result == "win":
                        team_stats[team]["wins"] += 1
                    elif result == "loss":
                        team_stats[team]["losses"] += 1
                
                print(f"\n  {Colors.BLUE}Per-Team Stats:{Colors.RESET}")
                for team, stats in sorted(team_stats.items()):
                    total = stats["wins"] + stats["losses"]
                    wr = stats["wins"] / total * 100 if total > 0 else 0
                    print(f"    {team}: {stats['wins']}W / {stats['losses']}L ({wr:.1f}%)")
                
                # Check for worker quota violations
                # This is a heuristic: if we have TEAM_NAMES set and per-worker quotas,
                # we can estimate if any worker ran more battles than expected
                if self.env.get("TEAM_NAMES") and self.num_workers > 1:
                    team_names = [t.strip() for t in self.env.get("TEAM_NAMES", "").split(",")]
                    if len(team_names) == self.num_workers:
                        print(f"\n  {Colors.BLUE}Worker Quota Check (heuristic):{Colors.RESET}")
                        
                        # Count battles per team (assuming 1:1 team:worker mapping)
                        for i, team_name in enumerate(team_names):
                            team_basename = Path(team_name).name
                            team_battle_count = sum(
                                1 for b in battles 
                                if team_basename in b.get("team_file", "")
                            )
                            
                            quota = self._calculate_quota(self.run_count, self.num_workers)[i]
                            
                            if quota > 0 and team_battle_count > quota:
                                print(f"    {check_mark(False)} Worker {i} ({team_basename}): "
                                      f"ran {team_battle_count} battles, quota was {quota}")
                                self.warnings.append(
                                    f"Worker {i} exceeded quota: {team_battle_count} > {quota}"
                                )
                            else:
                                status = "quota N/A" if quota == 0 else f"quota {quota}"
                                print(f"    {check_mark(True)} Worker {i} ({team_basename}): "
                                      f"{team_battle_count} battles ({status})")
        
        return all_passed
    
    def run_all_checks(self) -> bool:
        """Run all pre-flight checks"""
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}PRE-FLIGHT VERIFICATION{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
        
        checks = [
            ("Quota Verification", self.check_quota_verification),
            ("Team Rotation", self.check_team_rotation),
            ("Config Sanity", self.check_config_sanity),
            ("Battle Stats", self.check_battle_stats),
        ]
        
        results = []
        for name, check_fn in checks:
            try:
                passed = check_fn()
                results.append((name, passed))
            except Exception as e:
                print(f"\n{Colors.RED}EXCEPTION in {name}: {e}{Colors.RESET}")
                self.failures.append(f"{name} crashed: {e}")
                results.append((name, False))
        
        # Summary
        print(f"\n{Colors.BOLD}{'='*60}{Colors.RESET}")
        print(f"{Colors.BOLD}SUMMARY{Colors.RESET}")
        print(f"{Colors.BOLD}{'='*60}{Colors.RESET}")
        
        for name, passed in results:
            print(f"{check_mark(passed)} {name}")
        
        if self.warnings:
            print(f"\n{Colors.YELLOW}Warnings:{Colors.RESET}")
            for warning in self.warnings:
                print(f"  ⚠️  {warning}")
        
        if self.failures:
            print(f"\n{Colors.RED}Failures:{Colors.RESET}")
            for failure in self.failures:
                print(f"  ❌ {failure}")
            
            print(f"\n{Colors.RED}{Colors.BOLD}❌ PRE-FLIGHT CHECK FAILED{Colors.RESET}")
            print(f"{Colors.RED}Fix the issues above before running the batch.{Colors.RESET}")
            return False
        else:
            print(f"\n{Colors.GREEN}{Colors.BOLD}✅ ALL PRE-FLIGHT CHECKS PASSED{Colors.RESET}")
            print(f"{Colors.GREEN}Ready to launch batch.{Colors.RESET}")
            return True


def run_pre_flight_check(run_count: int = None, num_workers: int = None) -> bool:
    """
    Run pre-flight checks. Returns True if all checks pass, False otherwise.
    
    Args:
        run_count: Override run count from .env
        num_workers: Override worker count from .env
    
    Returns:
        bool: True if all checks pass, False otherwise
    """
    checker = PreFlightChecker(run_count=run_count, num_workers=num_workers)
    return checker.run_all_checks()


def main():
    parser = argparse.ArgumentParser(
        description="Pre-flight verification for Fouler Play batches"
    )
    parser.add_argument(
        "--run-count",
        type=int,
        help="Override run count from .env"
    )
    parser.add_argument(
        "--num-workers",
        type=int,
        help="Override number of workers from .env"
    )
    args = parser.parse_args()
    
    passed = run_pre_flight_check(
        run_count=args.run_count,
        num_workers=args.num_workers
    )
    
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
