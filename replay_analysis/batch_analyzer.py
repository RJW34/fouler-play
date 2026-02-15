#!/usr/bin/env python3
"""
Batch analyzer for Fouler Play bot replays.
Collects turn reviews, sends to Ollama on MAGNETON for analysis.
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.turn_review import TurnReviewer

# ROOT CAUSE FIX: Use local Ollama instead of remote MAGNETON
# Local Ollama is running on ubunztu at localhost:11434
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5-coder:3b"
REPORTS_DIR = PROJECT_ROOT / "replay_analysis" / "reports"
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
REPLAY_ANALYSIS_DIR = PROJECT_ROOT / "replay_analysis"


class BatchAnalyzer:
    """Analyzes batches of battles and generates improvement reports."""

    def __init__(self):
        self.reviewer = TurnReviewer(bot_username="BugInTheCode")
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def get_battle_stats(self) -> List[Dict]:
        """Load all battles from battle_stats.json."""
        if not BATTLE_STATS_FILE.exists():
            return []
        try:
            with open(BATTLE_STATS_FILE, 'r') as f:
                data = json.load(f)
                return data.get("battles", [])
        except Exception as e:
            print(f"Error loading battle_stats.json: {e}")
            return []

    def get_unreviewed_replays(self, last_n: int) -> List[Dict]:
        """Get the last N battles that haven't been analyzed yet."""
        battles = self.get_battle_stats()
        if not battles:
            return []
        
        # Take the last N battles
        recent_battles = battles[-last_n:] if len(battles) > last_n else battles
        
        # Check which have turn reviews
        unreviewed = []
        for battle in recent_battles:
            replay_id = battle.get("replay_id", "")
            if not replay_id:
                continue
            
            # Check if we have turn reviews for this battle
            turn_review_files = list(REPLAY_ANALYSIS_DIR.glob(f"turn_reviews/turn_*_{replay_id}.json"))
            if not turn_review_files:
                unreviewed.append(battle)
        
        return unreviewed

    def analyze_replay(self, replay_url: str) -> Optional[str]:
        """Run turn_review.py on a replay and return full review text.
        
        ROOT CAUSE FIX: Check local files FIRST before hitting Pokemon Showdown.
        Priority: logs/*.log > replay_analysis/*.json > Pokemon Showdown API
        """
        try:
            import requests
            replay_id = replay_url.rstrip("/").split("/")[-1]
            replay_data = None
            
            # PRIORITY 1: Check logs directory for battle log file
            logs_dir = PROJECT_ROOT / "logs"
            # Battle IDs in logs have format: battle-gen9ou-2539943964_OpponentName.log
            log_files = list(logs_dir.glob(f"{replay_id}_*.log")) + list(logs_dir.glob(f"{replay_id}.log"))
            
            if log_files:
                log_file = log_files[0]
                print(f"✓ Using local battle log: {log_file.name}")
                # Parse battle log to extract replay-compatible data
                # For now, we'll still try to convert it to replay JSON format
                # The battle logger should have saved a .json file too
                json_from_log = log_file.with_suffix('.json')
                if json_from_log.exists():
                    with open(json_from_log, 'r') as f:
                        replay_data = json.load(f)
            
            # PRIORITY 2: Check replay_analysis directory for saved replay JSON
            if not replay_data:
                local_replay_id = replay_id.replace("battle-", "") if replay_id.startswith("battle-") else replay_id
                local_file = REPLAY_ANALYSIS_DIR / f"{local_replay_id}.json"
                
                if local_file.exists():
                    print(f"✓ Using saved replay JSON: {local_replay_id}.json")
                    with open(local_file, 'r') as f:
                        replay_data = json.load(f)
            
            # PRIORITY 3 (LAST RESORT): Fetch from Pokemon Showdown API
            if not replay_data:
                print(f"⚠ Local replay not found, fetching from Pokemon Showdown...")
                json_url = f"https://replay.pokemonshowdown.com/{replay_id}.json"
                resp = requests.get(json_url, timeout=15)
                
                if resp.status_code != 200:
                    print(f"✗ Failed to fetch replay {replay_id}: {resp.status_code} (no local fallback)")
                    return None
                
                replay_data = resp.json()
                print(f"✓ Fetched from Pokemon Showdown API")
            
            # Extract full turn review
            turns = self.reviewer.extract_full_turns(replay_data, replay_url)
            
            if not turns:
                return None
            
            # Format turns for analysis
            review_lines = [f"Replay: {replay_url}"]
            review_lines.append(f"Result: {turns[0].why_critical.split('Lead matchup:')[1] if 'Lead matchup:' in turns[0].why_critical else 'Unknown'}")
            review_lines.append("\nTurn-by-turn breakdown:")
            
            for turn in turns:
                review_lines.append(
                    f"Turn {turn.turn_number}: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP) vs "
                    f"{turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)"
                )
                review_lines.append(f"  Bot chose: {turn.bot_choice}")
                review_lines.append(f"  Context: {turn.why_critical}")
                review_lines.append("")
            
            return "\n".join(review_lines)
            
        except Exception as e:
            print(f"Error analyzing replay {replay_url}: {e}")
            return None

    def collect_batch_reviews(self, last_n: int = 10, min_age_hours: int = 2) -> tuple[List[str], Dict]:
        """Collect reviews for N battles that are old enough to have public replays.
        
        ROOT CAUSE FIX: Pokemon Showdown replays aren't instantly available.
        Filter battles to only those >= min_age_hours old before trying to fetch.
        Skip unavailable replays gracefully instead of aborting.
        """
        from datetime import datetime, timedelta, timezone
        
        battles = self.get_battle_stats()
        if not battles:
            return [], {"total": 0, "wins": 0, "losses": 0}
        
        # Filter to battles older than min_age_hours
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
        old_enough = [
            b for b in battles 
            if datetime.fromisoformat(b["timestamp"].replace("Z", "+00:00")) < cutoff_time
        ]
        
        if not old_enough:
            print(f"⚠ No battles older than {min_age_hours}h found. Replays may not be available yet.")
            print(f"  Total battles: {len(battles)}")
            if battles:
                latest = datetime.fromisoformat(battles[-1]["timestamp"].replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600
                print(f"  Latest battle age: {age_hours:.1f}h")
            return [], {"total": 0, "wins": 0, "losses": 0}
        
        # Take the last N battles from the filtered set
        recent = old_enough[-last_n:] if len(old_enough) > last_n else old_enough
        
        print(f"✓ Found {len(old_enough)} battles older than {min_age_hours}h, analyzing last {len(recent)}")
        
        # Calculate stats
        stats = {
            "total": len(recent),
            "wins": sum(1 for b in recent if b.get("result") == "win"),
            "losses": sum(1 for b in recent if b.get("result") == "loss"),
            "teams": {}
        }
        
        # Team breakdown
        for battle in recent:
            team = battle.get("team_file", "unknown")
            result = battle.get("result", "unknown")
            if team not in stats["teams"]:
                stats["teams"][team] = {"wins": 0, "losses": 0}
            if result == "win":
                stats["teams"][team]["wins"] += 1
            elif result == "loss":
                stats["teams"][team]["losses"] += 1
        
        # Collect reviews (skip unavailable replays gracefully)
        reviews = []
        success_count = 0
        fail_count = 0
        
        for battle in recent:
            replay_id = battle.get("replay_id", "")
            if not replay_id:
                continue
            
            # FIX: battle_stats.json stores "battle-gen9ou-X" but PS URLs need "gen9ou-X"
            clean_id = replay_id.replace("battle-", "", 1) if replay_id.startswith("battle-") else replay_id
            replay_url = f"https://replay.pokemonshowdown.com/{clean_id}"
            review = self.analyze_replay(replay_url)
            
            if review:
                reviews.append(f"--- Battle: {replay_id} (Result: {battle.get('result', 'unknown')}) ---")
                reviews.append(review)
                reviews.append("")
                success_count += 1
            else:
                fail_count += 1
                print(f"  ✗ Skipping {replay_id} (404 or parse error)")
                # Continue instead of abort
        
        print(f"Review collection: {success_count} succeeded, {fail_count} failed")
        
        if success_count == 0 and fail_count > 0:
            print(f"⚠ ALL {fail_count} replays failed to fetch. Try increasing min_age_hours or wait longer.")
        return reviews, stats

    def build_analysis_prompt(self, reviews: List[str], stats: Dict) -> str:
        """Build a structured prompt for Ollama analysis."""
        prompt = """You are analyzing Pokemon Showdown battle replays for a competitive bot named BugInTheCode.

BATCH STATISTICS:
- Total battles: {total}
- Wins: {wins}
- Losses: {losses}
- Win rate: {winrate:.1%}

TEAM PERFORMANCE:
{team_breakdown}

BATTLE REVIEWS:
{reviews}

ANALYSIS TASK:
Identify patterns and provide actionable improvements. Focus on:

1. RECURRING MISTAKES: What decision-making errors appear across multiple battles?
2. MATCHUP WEAKNESSES: Which opponent strategies consistently cause problems?
3. TEAM COMPOSITION ISSUES: Which teams underperform? Why?
4. LOSS PATTERNS: What common factors appear in losses? (e.g., never sets hazards, switches too aggressively)
5. TOP 3 IMPROVEMENTS: Rank by expected impact on win rate.

Be specific and cite battle examples. Format your response as a structured analysis report.
""".format(
            total=stats["total"],
            wins=stats["wins"],
            losses=stats["losses"],
            winrate=stats["wins"] / stats["total"] if stats["total"] > 0 else 0,
            team_breakdown=self._format_team_breakdown(stats["teams"]),
            reviews="\n".join(reviews[:15])  # Limit to avoid token overflow
        )
        
        return prompt

    def _format_team_breakdown(self, teams: Dict) -> str:
        """Format team performance breakdown."""
        lines = []
        for team, perf in sorted(teams.items()):
            total = perf["wins"] + perf["losses"]
            wr = perf["wins"] / total if total > 0 else 0
            lines.append(f"  - {team}: {perf['wins']}-{perf['losses']} ({wr:.1%})")
        return "\n".join(lines) if lines else "  No team data available"

    def query_ollama(self, prompt: str) -> Optional[str]:
        """Send prompt to local Ollama and return response.
        
        ROOT CAUSE FIX: Query local Ollama on ubunztu instead of remote MAGNETON.
        Local Ollama runs at http://localhost:11434
        """
        try:
            import requests
            
            print(f"Querying local Ollama (model: {OLLAMA_MODEL})...")
            print(f"Prompt size: {len(prompt)} chars")
            
            # Build JSON payload
            payload = {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            }
            
            # Query local Ollama API
            response = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json=payload,
                timeout=300  # 5 minute timeout for generation
            )
            
            if response.status_code != 200:
                print(f"✗ Ollama API error: HTTP {response.status_code}")
                print(f"Response: {response.text[:200]}")
                return None
            
            # Parse response
            response_data = response.json()
            
            if "error" in response_data:
                print(f"✗ Ollama API error: {response_data['error']}")
                return None
            
            analysis_text = response_data.get("response", "")
            print(f"✓ Ollama analysis complete ({len(analysis_text)} chars)")
            return analysis_text
            
        except requests.Timeout:
            print("✗ Ollama query timed out after 5 minutes")
            return None
        except requests.ConnectionError:
            print("✗ Cannot connect to Ollama. Is it running? Check: ollama serve")
            return None
        except Exception as e:
            print(f"✗ Error querying Ollama: {e}")
            import traceback
            traceback.print_exc()
            return None

    def generate_report(self, last_n: int = 10) -> Optional[Path]:
        """Generate a full analysis report for the last N battles.
        
        ROOT CAUSE FIX: Fallback to stats-only analysis when replays unavailable.
        Pokemon Showdown purges replays after ~1 week, so we need this fallback.
        """
        print(f"Collecting reviews for last {last_n} battles...")
        reviews, stats = self.collect_batch_reviews(last_n)
        
        if not reviews:
            print("⚠ No replay data available. Falling back to stats-only analysis...")
            # Use stats-only analysis mode
            return self.generate_stats_only_report(last_n, stats)
        
        print(f"Collected {len(reviews)} battle reviews.")
        print(f"Stats: {stats['wins']}-{stats['losses']} ({stats['wins']/(stats['wins']+stats['losses'])*100:.1f}% WR)")
        
        # Build prompt
        prompt = self.build_analysis_prompt(reviews, stats)
        
        # Query Ollama
        analysis = self.query_ollama(prompt)
        if not analysis:
            print("Failed to get analysis from Ollama")
            return None
        
        # Generate report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_num = len(list(REPORTS_DIR.glob("batch_*.md"))) + 1
        report_file = REPORTS_DIR / f"batch_{batch_num:04d}_{timestamp}.md"
        
        report_content = f"""# Fouler Play Analysis Report - Batch {batch_num}

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Battles Analyzed:** {stats['total']} (last {last_n})
**Record:** {stats['wins']}-{stats['losses']} ({stats['wins']/(stats['wins']+stats['losses'])*100:.1f}% WR)

## Team Performance

{self._format_team_breakdown(stats['teams'])}

## AI Analysis

{analysis}

---

*Analysis powered by {OLLAMA_MODEL} on ubunztu (local)*
"""
        
        report_file.write_text(report_content)
        print(f"Report saved to: {report_file}")
        return report_file
    
    def generate_stats_only_report(self, last_n: int, stats: Dict) -> Optional[Path]:
        """Generate analysis report using only battle stats (no replays).
        
        WORKAROUND: When Pokemon Showdown has purged replays and we don't have
        local replay JSONs saved, we can still provide value by analyzing win rates,
        team performance, and making recommendations based on aggregate stats.
        """
        battles = self.get_battle_stats()
        recent = battles[-last_n:] if len(battles) > last_n else battles
        
        # Build stats-focused prompt
        prompt = f"""You are analyzing Pokemon Showdown competitive bot performance data for BugInTheCode.

NOTE: Detailed replay data is unavailable (Pokemon Showdown purged replays). 
Provide analysis based on AGGREGATE STATISTICS and TEAM PERFORMANCE patterns.

BATCH STATISTICS ({last_n} battles):
- Total: {stats['total']}
- Wins: {stats['wins']}
- Losses: {stats['losses']}
- Win Rate: {stats['wins']/(stats['wins']+stats['losses'])*100:.1f}%

TEAM PERFORMANCE BREAKDOWN:
{self._format_team_breakdown(stats['teams'])}

DETAILED BATTLE LOG:
{self._format_battle_list(recent)}

ANALYSIS TASK (stats-only mode):
Without access to turn-by-turn replay data, focus on aggregate patterns:

1. **TEAM WIN RATE ANALYSIS**: Which teams are underperforming? Which are succeeding?
2. **CONSISTENCY**: Are certain teams volatile (inconsistent results)?
3. **SAMPLE SIZE**: Is the data sufficient to draw conclusions, or do we need more battles?
4. **TEAM COMPOSITION HYPOTHESIS**: Based on team names and win rates, what might be working/failing?
5. **NEXT STEPS**: What should we prioritize?
   - More data collection?
   - Team rotation changes?
   - Saving replay JSONs locally for future detailed analysis?

Be specific and actionable. Acknowledge the limitation of not having replay data.
"""
        
        print(f"Querying Ollama for stats-only analysis...")
        analysis = self.query_ollama(prompt)
        
        if not analysis:
            print("✗ Failed to get Ollama analysis")
            return None
        
        # Generate report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_num = len(list(REPORTS_DIR.glob("batch_*.md"))) + 1
        report_file = REPORTS_DIR / f"batch_{batch_num:04d}_{timestamp}_stats_only.md"
        
        report_content = f"""# Fouler Play Analysis Report - Batch {batch_num} (Stats-Only Mode)

**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Battles Analyzed:** {stats['total']} (last {last_n})
**Record:** {stats['wins']}-{stats['losses']} ({stats['wins']/(stats['wins']+stats['losses'])*100:.1f}% WR)

⚠️ **NOTE:** This analysis is based on aggregate statistics only.  
Replay data unavailable (Pokemon Showdown purged replays after ~1 week).  
**RECOMMENDATION:** Modify bot to save replay JSONs locally after each battle.

## Team Performance

{self._format_team_breakdown(stats['teams'])}

## Battle History

{self._format_battle_list(recent)}

## AI Analysis

{analysis}

---

*Stats-only analysis powered by {OLLAMA_MODEL} on ubunztu (local)*  
*For detailed turn-by-turn analysis, implement local replay JSON storage.*
"""
        
        report_file.write_text(report_content)
        print(f"✓ Stats-only report saved to: {report_file}")
        return report_file
    
    def _format_battle_list(self, battles: List[Dict]) -> str:
        """Format a list of battles for stats-only analysis."""
        lines = []
        for i, battle in enumerate(battles, 1):
            result = battle.get('result', 'unknown')
            team = battle.get('team_file', 'unknown').replace('fat-team-', 'Team ').replace('-', ' ').title()
            opponent = battle.get('opponent', 'Unknown')
            replay_id = battle.get('replay_id', 'N/A')[-12:]  # Last 12 chars
            
            result_emoji = "✅" if result == "win" else "❌" if result == "loss" else "❓"
            lines.append(f"{i}. {result_emoji} {result.upper()} vs {opponent} using {team} (ID: {replay_id})")
        
        return '\n'.join(lines) if lines else "No battle data available"

    def get_latest_report(self) -> Optional[Path]:
        """Get the most recent report file."""
        reports = sorted(REPORTS_DIR.glob("batch_*.md"), key=lambda p: p.stat().st_mtime)
        return reports[-1] if reports else None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Batch analyze Fouler Play battles")
    parser.add_argument("-n", "--num-battles", type=int, default=10,
                        help="Number of recent battles to analyze")
    args = parser.parse_args()
    
    analyzer = BatchAnalyzer()
    report = analyzer.generate_report(last_n=args.num_battles)
    
    if report:
        print(f"\n✅ Analysis complete: {report}")
        print(f"\nView with: cat {report}")
    else:
        print("\n❌ Analysis failed")
        sys.exit(1)
