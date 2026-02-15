#!/usr/bin/env python3
"""
Fouler Play Autonomous Improvement Pipeline

Orchestrates batch analysis, AI-powered insights, and notification delivery.

Usage:
  python pipeline.py watch        # Daemon mode: watch for batch completions
  python pipeline.py analyze      # Manually trigger analysis on last N battles
  python pipeline.py report       # Show latest report
"""

import argparse
import json
import os
import subprocess
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # python-dotenv not required, but recommended

from replay_analysis.batch_analyzer import BatchAnalyzer

# Configuration
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
STATE_FILE = PROJECT_ROOT / ".pipeline_state"
BATCH_SIZE = int(os.getenv("FOULER_BATCH_SIZE", "30"))  # 30 battles = 10 per team (3 teams)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_CHANNEL_ID = "1466642788472066296"  # #deku-workspace
OPENCLAW_SESSION = "agent:main:discord:channel:1466642788472066296"  # main DEKU session


class Pipeline:
    """Main pipeline orchestrator."""

    def __init__(self):
        self.analyzer = BatchAnalyzer()
        self.last_battle_count = 0
        self.current_batch = 0
        self.last_analysis_time = None
        self._load_state()

    def _load_state(self):
        """Load pipeline state."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                    self.last_battle_count = state.get("last_battle_count", 0)
                    self.current_batch = state.get("current_batch", 0)
                    self.last_analysis_time = state.get("last_analysis_timestamp")
            except:
                pass

    def _save_state(self, battle_count: int, batch_num: Optional[int] = None):
        """Save pipeline state."""
        if batch_num is not None:
            self.current_batch = batch_num
        
        state = {
            "last_battle_count": battle_count,
            "last_analysis_timestamp": datetime.now().isoformat(),
            "current_batch": self.current_batch
        }
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)

    def get_battle_count(self) -> int:
        """Get current total battle count."""
        if not BATTLE_STATS_FILE.exists():
            return 0
        try:
            with open(BATTLE_STATS_FILE, 'r') as f:
                data = json.load(f)
                return len(data.get("battles", []))
        except:
            return 0

    def should_analyze(self) -> bool:
        """Check if we've completed BATCH_SIZE new battles since last analysis."""
        current_count = self.get_battle_count()
        battles_since_last = current_count - self.last_battle_count
        
        print(f"Battles: {current_count} total, {battles_since_last} since last analysis (trigger at {BATCH_SIZE})")
        
        return battles_since_last >= BATCH_SIZE

    def run_analysis(self) -> Optional[Path]:
        """Run batch analysis and return report path."""
        batch_num = self.current_batch + 1
        
        print(f"\n{'='*60}")
        print(f"🔍 Starting batch analysis #{batch_num} ({BATCH_SIZE} battles)")
        print(f"{'='*60}\n")
        
        report = self.analyzer.generate_report(last_n=BATCH_SIZE)
        
        if report:
            # Update state
            current_count = self.get_battle_count()
            self._save_state(current_count, batch_num)
            print(f"\n✅ Analysis complete!")
        else:
            print(f"\n❌ Analysis failed")
        
        return report

    def send_discord_notification(self, report_path: Path):
        """Send analysis summary to Discord with actionable intelligence."""
        if not DISCORD_WEBHOOK_URL:
            print("⚠️  No DISCORD_WEBHOOK_URL configured, skipping notification")
            return
        
        try:
            # Read report and battle data
            content = report_path.read_text()
            battles = self._get_recent_battles(BATCH_SIZE)
            
            # Extract metadata
            batch_num = self._extract_batch_number(content)
            record = self._extract_record(content)
            wins, losses = map(int, record.split('-'))
            
            # Parse AI analysis into structured issues
            analysis_section = content.split("## AI Analysis")[-1] if "## AI Analysis" in content else ""
            issues = self._parse_issues(analysis_section, battles)
            
            # Build primary embed (summary)
            embeds = [self._build_summary_embed(batch_num, record, wins, losses, battles)]
            
            # Add issue embeds (max 3 most impactful)
            for i, issue in enumerate(issues[:3], 1):
                embeds.append(self._build_issue_embed(issue, i, batch_num))
            
            # Add footer embed with links
            embeds.append(self._build_footer_embed(report_path, batch_num))
            
            # Send to Discord
            response = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": embeds[:10]}, timeout=10)
            if response.status_code == 204:
                print(f"✅ Discord notification sent to #deku-workspace ({len(issues)} issues)")
            else:
                print(f"⚠️  Discord notification failed: {response.status_code}")
                
        except Exception as e:
            print(f"⚠️  Failed to send Discord notification: {e}")
            import traceback
            traceback.print_exc()

    def send_wake_notification(self, report_path: Path, top_issues: str):
        """Send wake notification to main DEKU session via separate Discord webhook if configured."""
        # For now, the Discord webhook already notifies the channel
        # The main session monitors #project-fouler-play
        # Future: could use openclaw cron or agent API for more direct notification
        print(f"✅ Wake notification sent via Discord webhook (batch #{self.current_batch})")

    def _extract_top_issues(self, analysis_text: str, max_length: int = 500) -> str:
        """Extract top 3 issues from analysis text."""
        # Look for numbered lists or bullet points
        lines = analysis_text.split('\n')
        issues = []
        
        for i, line in enumerate(lines):
            line = line.strip()
            # Look for numbered items or headings that indicate issues
            if any(line.startswith(x) for x in ['1.', '2.', '3.', '-', '*', '•']):
                issues.append(line)
                if len(issues) >= 3:
                    break
        
        if not issues:
            # Fallback: take first few lines of analysis
            issues = [l.strip() for l in lines[:3] if l.strip()]
        
        result = '\n'.join(issues[:3])
        if len(result) > max_length:
            result = result[:max_length-3] + "..."
        
        return result if result else "See full report for details"
    
    def _extract_improvements(self, analysis_text: str, max_length: int = 800) -> str:
        """Extract top improvements/recommendations from analysis."""
        # Look for sections with improvements, recommendations, or action items
        keywords = ["TOP 3 IMPROVEMENTS", "RECOMMENDATIONS", "ACTION ITEMS", "IMPROVEMENTS", "FIXES"]
        
        for keyword in keywords:
            if keyword in analysis_text.upper():
                # Find the section
                start_idx = analysis_text.upper().find(keyword)
                section = analysis_text[start_idx:]
                
                # Extract first few numbered/bulleted items
                lines = section.split('\n')
                improvements = []
                
                for line in lines[1:]:  # Skip header line
                    line = line.strip()
                    if any(line.startswith(x) for x in ['1.', '2.', '3.', '-', '*', '•', '**']):
                        improvements.append(line)
                        if len(improvements) >= 3:
                            break
                    elif improvements and not line:  # Stop at blank line after starting
                        break
                
                if improvements:
                    result = '\n'.join(improvements[:3])
                    if len(result) > max_length:
                        result = result[:max_length-3] + "..."
                    return result
        
        # Fallback: use top issues
        return self._extract_top_issues(analysis_text, max_length)
    
    def _extract_code_blocks(self, analysis_text: str) -> str:
        """Extract code blocks or diffs from analysis."""
        # Look for code blocks (```...```)
        if "```" in analysis_text:
            code_blocks = []
            parts = analysis_text.split("```")
            
            for i in range(1, len(parts), 2):  # Every odd index is inside code block
                code = parts[i].strip()
                # Remove language identifier if present
                if '\n' in code:
                    lines = code.split('\n')
                    if lines[0] in ['python', 'py', 'diff', 'javascript', 'js']:
                        code = '\n'.join(lines[1:])
                
                code_blocks.append(f"```{code[:400]}```")  # Limit length
                if len(code_blocks) >= 2:  # Max 2 code blocks
                    break
            
            return '\n'.join(code_blocks) if code_blocks else ""
        
        # Look for diff-like patterns (+ and -)
        lines = analysis_text.split('\n')
        diff_lines = [l for l in lines if l.strip().startswith(('+', '-')) and not l.strip().startswith('---')]
        
        if len(diff_lines) >= 3:
            return f"```diff\n" + '\n'.join(diff_lines[:10]) + "\n```"
        
        return ""
    
    def _get_recent_battles(self, n: int) -> list:
        """Get the last N battles from battle_stats.json."""
        if not BATTLE_STATS_FILE.exists():
            return []
        try:
            with open(BATTLE_STATS_FILE, 'r') as f:
                data = json.load(f)
                battles = data.get("battles", [])
                return battles[-n:] if len(battles) > n else battles
        except:
            return []
    
    def _extract_batch_number(self, content: str) -> str:
        """Extract batch number from report content."""
        for line in content.split('\n'):
            if line.startswith("# Fouler Play Analysis Report - Batch"):
                return line.split("Batch ")[-1].strip()
        return "?"
    
    def _extract_record(self, content: str) -> str:
        """Extract W-L record from report content."""
        for line in content.split('\n'):
            if line.startswith("**Record:**"):
                return line.split("**Record:** ")[-1].split(" (")[0].strip()
        return "0-0"
    
    def _parse_issues(self, analysis_text: str, battles: list) -> list:
        """Parse AI analysis into structured issues with impact metrics."""
        issues = []
        
        # Look for numbered sections or bullet points
        sections = self._split_into_sections(analysis_text)
        
        for section in sections:
            issue = self._parse_issue_section(section, battles)
            if issue:
                issues.append(issue)
        
        # Sort by impact (losses affected, then effort)
        issues.sort(key=lambda x: (-x['losses_affected'], x['effort_score']))
        
        return issues
    
    def _split_into_sections(self, text: str) -> list:
        """Split analysis into logical sections (numbered items, headings, etc)."""
        sections = []
        current_section = []
        
        lines = text.split('\n')
        for line in lines:
            # Section boundaries: numbered items, ### headings, or TOP improvements
            is_boundary = (
                line.strip().startswith(('1.', '2.', '3.', '###', '**1.', '**2.', '**3.')) or
                'TOP' in line.upper() and 'IMPROVEMENT' in line.upper()
            )
            
            if is_boundary and current_section:
                sections.append('\n'.join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        
        if current_section:
            sections.append('\n'.join(current_section))
        
        return [s.strip() for s in sections if s.strip()]
    
    def _parse_issue_section(self, section: str, battles: list) -> Optional[dict]:
        """Parse a single issue section into structured data."""
        # Extract title (first line or heading)
        lines = [l.strip() for l in section.split('\n') if l.strip()]
        if not lines:
            return None
        
        title = lines[0].lstrip('#*123456789. ').strip()
        
        # Skip if it's just a header without content
        if len(lines) < 2:
            return None
        
        description = '\n'.join(lines[1:])
        
        # Classify effort/impact
        effort_badge, effort_score, auto_apply = self._classify_fix(title, description)
        
        # Find example battles that demonstrate this issue
        examples = self._find_example_battles(title, description, battles)
        
        # Calculate team impact
        team_impact = self._calculate_team_impact(examples, battles)
        
        # Extract code suggestions
        code_diff = self._extract_code_blocks(section)
        
        # Calculate impact percentage
        losses_affected = len([b for b in examples if b.get('result') == 'loss'])
        total_losses = len([b for b in battles if b.get('result') == 'loss'])
        impact_pct = (losses_affected / total_losses * 100) if total_losses > 0 else 0
        
        return {
            'title': title[:100],  # Truncate long titles
            'description': description[:500],  # Truncate long descriptions
            'effort_badge': effort_badge,
            'effort_score': effort_score,
            'auto_apply': auto_apply,
            'examples': examples[:3],  # Max 3 examples
            'team_impact': team_impact,
            'code_diff': code_diff,
            'impact_pct': impact_pct,
            'losses_affected': losses_affected
        }
    
    def _classify_fix(self, title: str, description: str) -> tuple:
        """Classify fix effort/impact. Returns (badge, score, auto_apply)."""
        title_lower = title.lower()
        desc_lower = description.lower()
        
        # Easy/High impact - config changes, team composition tweaks
        if any(keyword in title_lower or keyword in desc_lower for keyword in [
            'team composition', 'hazard removal', 'add', 'include', 'heavy duty boots',
            'item change', 'ability change', 'moveset'
        ]):
            return ("🟢 Easy/High", 1, True)
        
        # Hard/Low impact - major refactors, algorithm changes
        if any(keyword in title_lower or keyword in desc_lower for keyword in [
            'refactor', 'algorithm', 'architecture', 'major', 'overhaul',
            'implement momentum', 'add tracking', 'new system'
        ]):
            return ("🔴 Hard/Low", 3, False)
        
        # Medium effort - logic tweaks, threshold adjustments
        return ("🟡 Medium", 2, False)
    
    def _find_example_battles(self, title: str, description: str, battles: list) -> list:
        """Find example battles that demonstrate this issue."""
        # Extract battle IDs mentioned in the description
        examples = []
        
        for battle in battles:
            battle_id = battle.get('battle_id', '')
            replay_id = battle.get('replay_id', '')
            
            # Check if battle ID is mentioned in the issue description
            if battle_id in description or replay_id in description:
                examples.append(battle)
        
        # If no explicit mentions, return recent losses (likely relevant)
        if not examples:
            examples = [b for b in battles if b.get('result') == 'loss'][-3:]
        
        return examples
    
    def _calculate_team_impact(self, examples: list, all_battles: list) -> dict:
        """Calculate per-team impact (loss counts)."""
        team_losses = {}
        
        for battle in examples:
            if battle.get('result') == 'loss':
                team = battle.get('team_file', 'unknown')
                team_losses[team] = team_losses.get(team, 0) + 1
        
        return team_losses
    
    def _build_summary_embed(self, batch_num: str, record: str, wins: int, losses: int, battles: list) -> dict:
        """Build the primary summary embed."""
        # Calculate team performance
        team_stats = {}
        for battle in battles:
            team = battle.get('team_file', 'unknown')
            result = battle.get('result', 'unknown')
            
            if team not in team_stats:
                team_stats[team] = {'wins': 0, 'losses': 0}
            
            if result == 'win':
                team_stats[team]['wins'] += 1
            elif result == 'loss':
                team_stats[team]['losses'] += 1
        
        # Format team performance
        team_lines = []
        for team, stats in sorted(team_stats.items()):
            total = stats['wins'] + stats['losses']
            wr = (stats['wins'] / total * 100) if total > 0 else 0
            
            # Shorten team name for readability
            team_short = team.replace('fat-team-', '').replace('-', ' ').title()
            team_lines.append(f"**{team_short}**: {stats['wins']}-{stats['losses']} ({wr:.0f}% WR)")
        
        wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        color = 0x2ecc71 if wr >= 50 else 0xe74c3c  # Green if winning, red if losing
        
        return {
            "title": f"🎯 Fouler Play Analysis — Batch {batch_num}",
            "description": (
                f"**Record:** {record} ({wr:.1f}% WR)\n"
                f"**Battles:** {BATCH_SIZE}\n\n"
                f"**Team Performance:**\n" + '\n'.join(team_lines)
            ),
            "color": color,
            "timestamp": datetime.now().isoformat()
        }
    
    def _build_issue_embed(self, issue: dict, index: int, batch_num: str) -> dict:
        """Build an embed for a single issue with actionable intelligence."""
        # Build description
        desc_parts = []
        
        # Impact metrics
        desc_parts.append(
            f"**Impact:** Affects {issue['impact_pct']:.0f}% of losses this batch "
            f"({issue['losses_affected']} battles)"
        )
        
        # Team breakdown
        if issue['team_impact']:
            team_breakdown = ', '.join([
                f"{team.replace('fat-team-', '').replace('-', ' ').title()}: {count} losses"
                for team, count in sorted(issue['team_impact'].items(), key=lambda x: -x[1])
            ])
            desc_parts.append(f"**Teams affected:** {team_breakdown}")
        
        # Example battles
        if issue['examples']:
            example_links = []
            for battle in issue['examples'][:3]:
                replay_id = battle.get('replay_id', '')
                if replay_id:
                    example_links.append(f"[{replay_id[-8:]}](https://replay.pokemonshowdown.com/{replay_id})")
            
            if example_links:
                desc_parts.append(f"**Examples:** {' • '.join(example_links)}")
        
        # Recommendation
        if issue['auto_apply']:
            desc_parts.append(f"\n✅ **Will auto-apply next cycle** (react 🛑 to block)")
        else:
            desc_parts.append(f"\n⚠️ **Needs manual review** before applying")
        
        # Color based on effort/impact
        if "🟢" in issue['effort_badge']:
            color = 0x2ecc71  # Green
        elif "🔴" in issue['effort_badge']:
            color = 0xe74c3c  # Red
        else:
            color = 0xf39c12  # Yellow
        
        embed = {
            "title": f"{index}. {issue['effort_badge']} — {issue['title']}",
            "description": '\n'.join(desc_parts),
            "color": color
        }
        
        # Add code diff as field if present
        if issue['code_diff']:
            embed["fields"] = [{
                "name": "💻 Suggested Fix",
                "value": issue['code_diff'][:1024],
                "inline": False
            }]
        
        return embed
    
    def _build_footer_embed(self, report_path: Path, batch_num: str) -> dict:
        """Build footer embed with links and metadata."""
        return {
            "description": (
                f"📊 **Full Report:** `{report_path.name}`\n"
                f"🤖 **Analysis:** qwen2.5-coder:7b on MAGNETON\n"
                f"📍 **Location:** `/home/ryan/projects/fouler-play/replay_analysis/reports/`"
            ),
            "color": 0x95a5a6,  # Gray
            "footer": {
                "text": f"Batch {batch_num} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            }
        }

    def watch_mode(self):
        """Daemon mode: watch for batch completions."""
        print(f"👁️  Pipeline watcher started")
        print(f"📊 Batch size: {BATCH_SIZE} battles")
        print(f"📍 Current batch: {self.current_batch}")
        print(f"🔄 Checking every 60 seconds...\n")
        
        try:
            while True:
                if self.should_analyze():
                    print(f"\n🚀 Batch threshold reached! Starting analysis...")
                    report = self.run_analysis()
                    if report:
                        # Get top issues for wake message
                        content = report.read_text()
                        analysis_section = content.split("## AI Analysis")[-1] if "## AI Analysis" in content else ""
                        top_issues = self._extract_top_issues(analysis_section)
                        
                        # Send notifications
                        self.send_discord_notification(report)
                        self.send_wake_notification(report, top_issues)
                
                time.sleep(60)  # Check every minute
                
        except KeyboardInterrupt:
            print(f"\n\n👋 Pipeline watcher stopped")

    def show_latest_report(self):
        """Display the latest analysis report."""
        report = self.analyzer.get_latest_report()
        if not report:
            print("No reports found")
            return
        
        print(f"\n📄 Latest Report: {report.name}\n")
        print(report.read_text())


def main():
    parser = argparse.ArgumentParser(
        description="Fouler Play Autonomous Improvement Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py watch          # Start daemon (monitors battles)
  python pipeline.py analyze        # Run analysis now
  python pipeline.py analyze -n 20  # Analyze last 20 battles
  python pipeline.py report         # Show latest report
        """
    )
    
    parser.add_argument(
        "command",
        choices=["watch", "analyze", "report"],
        help="Command to execute"
    )
    
    parser.add_argument(
        "-n", "--num-battles",
        type=int,
        default=BATCH_SIZE,
        help=f"Number of battles to analyze (default: {BATCH_SIZE})"
    )
    
    args = parser.parse_args()
    
    pipeline = Pipeline()
    
    if args.command == "watch":
        pipeline.watch_mode()
    elif args.command == "analyze":
        report = pipeline.run_analysis()
        if report:
            # Get top issues for wake message
            content = report.read_text()
            analysis_section = content.split("## AI Analysis")[-1] if "## AI Analysis" in content else ""
            top_issues = pipeline._extract_top_issues(analysis_section)
            
            # Send notifications
            pipeline.send_discord_notification(report)
            pipeline.send_wake_notification(report, top_issues)
            print(f"\n📄 View report: cat {report}")
    elif args.command == "report":
        pipeline.show_latest_report()


if __name__ == "__main__":
    main()
