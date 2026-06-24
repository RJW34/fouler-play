#!/usr/bin/env python3
"""
Batch analyzer for Fouler Play bot replays.
Collects turn reviews, then sends a grounded prompt to an external reasoning agent.
Current default: Claude via OpenClaw for Pokemon-competent batch analysis.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.turn_review import TurnReviewer
from replay_analysis.loss_learning import aggregate_loss_lessons, build_loss_artifact
from replay_analysis.account_identity import resolve_bot_username
from fp.theknower_competitive import build_competitive_meta_context
from infrastructure.gen9_validation import Gen9Validator

# Analysis source contract:
# - Use a strong external reasoning agent via OpenClaw for Pokemon-competent analysis.
# - Current default remains Claude Opus because it has been the most reliable for this task.
# - Do NOT use qwen or other lightweight local LLMs for Pokemon analysis; they hallucinate mechanics.
ANALYSIS_PROVIDER = "openclaw"
ANALYSIS_MODEL = "anthropic/claude-opus-4-6"
REPORTS_DIR = PROJECT_ROOT / "replay_analysis" / "reports"
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
REPLAY_ANALYSIS_DIR = PROJECT_ROOT / "replay_analysis"


class BatchAnalyzer:
    """Analyzes batches of battles and generates improvement reports."""

    def __init__(self):
        self.bot_username = resolve_bot_username()
        self.reviewer = TurnReviewer(bot_username=self.bot_username)
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

    def build_analysis_prompt(self, reviews: List[str], stats: Dict, mechanics_summary: str = "") -> str:
        """Build a structured prompt for external reasoning analysis with domain grounding."""
        competitive_context = build_competitive_meta_context()
        mechanics_summary = mechanics_summary or "No local mechanics-backed loss summary was available for this batch."
        prompt = """You are analyzing Pokemon Showdown Gen9 OU battle replays for a competitive bot named {bot_username}.

=== DOMAIN KNOWLEDGE & CONSTRAINTS ===
DO NOT hallucinate Pokemon knowledge. Treat the reasoning agent as advisory only.
Mechanics, type, ability, damage, speed, hazard, and move claims are promotable only when they are backed by:
1. the mechanics-backed loss learning summary below,
2. the local replay/Showdown protocol excerpts below, or
3. the current TheKnower snapshot below.

If a claim is absent from those sources, label it unknown instead of making it a fact.
Do not reuse historical percentages or prior-batch conclusions unless they are present in this prompt.

CURRENT THEKNOWER COMPETITIVE SNAPSHOT:
{competitive_context}

MECHANICS-BACKED LOSS LEARNING SUMMARY:
{mechanics_summary}

=== BATCH ANALYSIS ===
BATCH STATISTICS:
- Total battles: {total}
- Wins: {wins}
- Losses: {losses}
- Win rate: {winrate:.1%}

TEAM PERFORMANCE:
{team_breakdown}

BATTLE REVIEWS:
{reviews}

=== ANALYSIS TASK ===
Identify CONCRETE, VERIFIABLE patterns. Cite specific battles and turn numbers.

DO NOT:
- Suggest Pokemon that don't exist or aren't viable in Gen9 OU
- Make claims about moves/abilities without grounding in the meta knowledge above
- Hallucinate metagame shifts—only reference what's in the reviews
- Override the mechanics-backed loss summary; unknown/rejected claims must stay unknown/rejected

DO:
1. RECURRING MISTAKES: What errors repeat? (Stealth Rock delays, bad switches, missed recoveries?)
2. MATCHUP PATTERNS: Which opponent archetypes cause consistent losses?
3. TEAM EXECUTION: Are teams underperforming due to composition OR pilot error?
4. LOSS ROOT CAUSES: Factor in: hazard control, recovery usage, switch timing, threat management
5. TOP 3 IMPROVEMENTS: Rank by expected win-rate impact. Be specific—"Use Roost on turn X vs threat Y" not "switch more"

Format response as structured improvement report with battle citations.
""".format(
            total=stats["total"],
            wins=stats["wins"],
            losses=stats["losses"],
            winrate=stats["wins"] / stats["total"] if stats["total"] > 0 else 0,
            team_breakdown=self._format_team_breakdown(stats["teams"]),
            reviews="\n".join(reviews[:15]),  # Limit to avoid token overflow
            competitive_context=competitive_context,
            mechanics_summary=mechanics_summary,
            bot_username=self.bot_username,
        )
        
        return prompt

    def collect_loss_learning_artifacts(self, last_n: int) -> List[Dict]:
        """Build deterministic loss artifacts from local replay JSONs.

        ONLY real played-out (piloting) losses are returned for engine/piloting
        learning. Infra-losses (inactivity/timeout/disconnect/forfeit/crash) are
        EXCLUDED -- they reflect latency/network health, not decision quality, and
        contaminate engine A/B and improve loops if ingested. The infra-loss rate
        is tracked separately as a latency-health metric via
        collect_loss_termination_metrics().
        """
        artifacts, _metrics = self.collect_loss_artifacts_and_metrics(last_n)
        return artifacts

    def collect_loss_artifacts_and_metrics(self, last_n: int) -> tuple[List[Dict], Dict]:
        """Return (piloting_loss_artifacts, termination_metrics).

        piloting_loss_artifacts: cleaned corpus -- played-out losses ONLY.
        termination_metrics: infra/latency health, separate from engine signal.
        """
        artifacts: List[Dict] = []
        battles = self.get_battle_stats()
        recent = battles[-last_n:] if len(battles) > last_n else battles
        metrics = {
            "losses_seen": 0,            # losses with a local replay we could classify
            "piloting_losses": 0,        # real played-out losses (kept)
            "infra_losses": 0,           # inactivity/timeout/disconnect/forfeit/crash (excluded)
            "by_termination": {},        # termination -> count
            "excluded_replay_ids": [],   # infra-loss replay ids (for audit)
        }
        for battle in recent:
            if battle.get("result") != "loss":
                continue
            replay_id = battle.get("replay_id", "")
            if not replay_id:
                continue
            clean_id = replay_id.replace("battle-", "", 1) if replay_id.startswith("battle-") else replay_id
            local_file = REPLAY_ANALYSIS_DIR / f"{clean_id}.json"
            if not local_file.exists():
                continue
            try:
                with local_file.open("r", encoding="utf-8") as handle:
                    replay_data = json.load(handle)
                artifact = build_loss_artifact(
                    replay_data,
                    bot_username=self.bot_username,
                    team_file=battle.get("team_file"),
                )
            except Exception as exc:
                print(f"Skipping mechanics-backed loss artifact for {replay_id}: {exc}")
                continue

            metrics["losses_seen"] += 1
            termination = str(artifact.get("termination") or "unknown")
            metrics["by_termination"][termination] = metrics["by_termination"].get(termination, 0) + 1

            if artifact.get("is_infra_loss"):
                # INFRA loss -> EXCLUDE from the engine/piloting learning corpus.
                metrics["infra_losses"] += 1
                metrics["excluded_replay_ids"].append(replay_id)
                continue

            metrics["piloting_losses"] += 1
            artifacts.append(artifact)

        seen = max(1, metrics["losses_seen"])
        metrics["infra_loss_rate"] = round(metrics["infra_losses"] / seen, 4)
        metrics["timeout_loss_rate"] = round(
            metrics["by_termination"].get("inactivity", 0)
            + metrics["by_termination"].get("timeout", 0),
            4,
        ) / seen
        return artifacts, metrics

    def collect_loss_termination_metrics(self, last_n: int) -> Dict:
        """Latency/infra health metrics derived from recent losses.

        A high infra/timeout-loss rate means FIX THE LATENCY, not the engine.
        """
        _artifacts, metrics = self.collect_loss_artifacts_and_metrics(last_n)
        return metrics

    def build_loss_learning_section(self, last_n: int) -> str:
        """Return a report-ready deterministic loss-learning summary.

        Engine/piloting lessons are derived ONLY from played-out losses; infra
        (latency/timeout) losses are reported as a SEPARATE health metric.
        """
        artifacts, metrics = self.collect_loss_artifacts_and_metrics(last_n)

        # Infra/latency health -- always reported, even when there are no
        # piloting losses, because a high timeout-loss rate is the signal to fix
        # latency rather than the engine.
        infra_lines = [
            "Loss-termination health (infra vs piloting):",
            f"- Losses classified: {metrics['losses_seen']}"
            f" | piloting (kept): {metrics['piloting_losses']}"
            f" | infra (excluded): {metrics['infra_losses']}",
            f"- INFRA-LOSS RATE: {metrics.get('infra_loss_rate', 0.0):.1%}"
            f"  (timeout/inactivity rate: {metrics.get('timeout_loss_rate', 0.0):.1%})",
            f"- By termination: {metrics['by_termination']}",
            "- NOTE: a high infra/timeout rate means FIX LATENCY, not the engine; "
            "infra losses are excluded from the lessons below.",
        ]

        if not artifacts:
            return "\n".join(
                infra_lines
                + [
                    "",
                    "No PLAYED-OUT loss artifacts were available for engine learning "
                    "(all recent losses were infra/latency, or no replay JSON saved). "
                    "Mechanics-backed engine learning is paused until a real played-out "
                    "loss is captured.",
                ]
            )
        summary = aggregate_loss_lessons(artifacts, min_repeats=2)
        lines = infra_lines + [
            "",
            f"Played-out loss artifacts reviewed (engine corpus): {len(artifacts)}",
            f"Escalation threshold: {summary['min_repeats']} repeated source-backed losses",
        ]
        if summary["proven_lessons"]:
            lines.append("Proven lessons:")
            for lesson in summary["proven_lessons"][:5]:
                lines.append(f"- {lesson['lesson_id']} ({lesson['evidence_count']} evidence items)")
                lines.append(f"  Adjustment: {lesson['guidance']['supported_adjustment']}")
        else:
            lines.append("Proven lessons: none yet")

        if summary["hypotheses"]:
            lines.append("Hypotheses:")
            for lesson in summary["hypotheses"][:5]:
                lines.append(f"- {lesson['lesson_id']} ({lesson['evidence_count']} evidence item)")

        must_not = summary["must_not_conclude"]
        lines.append(f"Unknown claims held back: {len(must_not['unknown_claims'])}")
        lines.append(f"Rejected claims: {len(must_not['rejected_claims'])}")
        lines.append(must_not["overfit_guardrail"])
        return "\n".join(lines)

    def _sanitize_reasoning_analysis(self, analysis: str) -> str:
        """Fail closed when reasoning-agent prose contains unsupported Pokemon claims."""
        validator = Gen9Validator()
        is_valid, errors, warnings = validator.validate_analysis(analysis)
        if is_valid and not warnings:
            return analysis
        blocker_count = len(errors) + len(warnings)
        print(f"✗ Reasoning-agent analysis rejected by Gen 9 validator ({blocker_count} blocker/warning item(s))")
        return (
            "Reasoning-agent output was withheld because the Gen 9 validation gate "
            f"found {blocker_count} unsupported or under-specified Pokemon mechanics claim(s). "
            "HERMES must regenerate the analysis from replay/protocol-backed evidence before "
            "using it for policy or source changes."
        )

    def _format_team_breakdown(self, teams: Dict) -> str:
        """Format team performance breakdown."""
        lines = []
        for team, perf in sorted(teams.items()):
            total = perf["wins"] + perf["losses"]
            wr = perf["wins"] / total if total > 0 else 0
            lines.append(f"  - {team}: {perf['wins']}-{perf['losses']} ({wr:.1%})")
        return "\n".join(lines) if lines else "  No team data available"

    def query_reasoning_agent(self, prompt: str) -> Optional[str]:
        """Query the configured external reasoning agent via OpenClaw.
        
        Current default uses Claude Opus for accurate Gen 9 OU reasoning.
        The surrounding workflow is provider-agnostic as long as the chosen model
        remains Pokemon-competent and can follow grounded prompts reliably.
        """
        try:
            print(f"Querying reasoning agent via OpenClaw (provider: {ANALYSIS_PROVIDER}, model: {ANALYSIS_MODEL})...")
            print(f"Prompt size: {len(prompt)} chars")
            
            # Use subprocess to call OpenClaw agent
            result = subprocess.run(
                [
                    "openclaw", "agent", "turn",
                    "--model", ANALYSIS_MODEL,
                    "--message", prompt,
                    "--timeoutSeconds", "120"
                ],
                capture_output=True,
                text=True,
                timeout=150
            )
            
            if result.returncode != 0:
                print(f"✗ Reasoning-agent query failed: {result.stderr}")
                return None
            
            # Extract response text
            response_text = result.stdout.strip()
            if not response_text:
                print("✗ Reasoning agent returned empty response")
                return None
            
            print(f"✓ Reasoning-agent analysis complete ({len(response_text)} chars)")
            return response_text
            
        except subprocess.TimeoutExpired:
            print("✗ Reasoning-agent query timed out after 2 minutes")
            return None
        except FileNotFoundError:
            print("✗ openclaw CLI not found. Is it installed?")
            return None
        except Exception as e:
            print(f"✗ Error querying reasoning agent: {e}")
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
        loss_learning_section = self.build_loss_learning_section(last_n)
        
        # Build prompt
        prompt = self.build_analysis_prompt(reviews, stats, loss_learning_section)
        
        # Query external reasoning agent
        analysis = self.query_reasoning_agent(prompt)
        if not analysis:
            print("⚠ Reasoning-agent analysis failed—falling back to stats-only report")
            return self.generate_stats_only_report(last_n, stats)
        analysis = self._sanitize_reasoning_analysis(analysis)
        
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

## Mechanics-Backed Loss Learning

{loss_learning_section}

## AI Analysis

{analysis}

---

*Analysis powered by {ANALYSIS_MODEL} via OpenClaw ({ANALYSIS_PROVIDER})*
"""
        
        report_file.write_text(report_content, encoding="utf-8")
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
        loss_learning_section = self.build_loss_learning_section(last_n)
        
        # Build stats-focused prompt
        prompt = f"""You are analyzing Pokemon Showdown competitive bot performance data for {self.bot_username}.

NOTE: Detailed replay data is unavailable (Pokemon Showdown purged replays). 
Aggregate statistics are not mechanics proof. Do not produce confident Pokemon
mechanics, matchup, move, item, ability, speed, or damage recommendations unless
they appear in the mechanics-backed loss summary below. If the summary says no
local artifacts are available, limit output to data-collection needs and clearly
label win-rate observations as hypotheses.

MECHANICS-BACKED LOSS LEARNING SUMMARY:
{loss_learning_section}

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
   - Team rotation changes only as a hypothesis, not a learned mechanics fact?
   - Saving replay JSONs locally for future detailed analysis?

Be specific about evidence gaps. Do not write unsupported mechanics claims as fact.
"""
        
        print("Querying reasoning agent for stats-only analysis...")
        analysis = self.query_reasoning_agent(prompt)
        
        if not analysis:
            print("✗ Failed to get reasoning-agent analysis")
            return None
        analysis = self._sanitize_reasoning_analysis(analysis)
        
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
**RECOMMENDATION:** Save replay JSONs locally after each battle before changing team/search policy from this report.

## Team Performance

{self._format_team_breakdown(stats['teams'])}

## Mechanics-Backed Loss Learning

{loss_learning_section}

## Battle History

{self._format_battle_list(recent)}

## AI Analysis

{analysis}

---

*Stats-only analysis powered by {ANALYSIS_MODEL} via OpenClaw ({ANALYSIS_PROVIDER})*  
*For detailed turn-by-turn analysis, implement local replay JSON storage.*
"""
        
        report_file.write_text(report_content, encoding="utf-8")
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
