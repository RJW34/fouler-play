from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infrastructure.discord_reporting import build_contract_payload
from infrastructure.event_queue_lib import queue_event

BATTLE_STATS_PATH = PROJECT_ROOT / "battle_stats.json"
REPLAY_DIR = PROJECT_ROOT / "replay_analysis"
TRACE_DIR = PROJECT_ROOT / "logs" / "decision_traces"
REPORTS_DIR = REPLAY_DIR / "reports"
AUTORESEARCH_JSON_PATH = REPLAY_DIR / "autoresearch_latest.json"
AUTORESEARCH_MD_PATH = REPLAY_DIR / "reports" / "autoresearch_latest.md"
BATCH_HISTORY_DIR = REPLAY_DIR / "batches"

ROUTINE_CHANNEL = os.getenv("FOULER_ROUTINE_CHANNEL", "1466691161363054840")
RESEARCH_CHANNEL = os.getenv("FOULER_RESEARCH_CHANNEL", "1466869808200028264")
BOT_USERNAME = os.getenv("PS_USERNAME", "npctypebeat")


@dataclass
class ResearchIssue:
    key: str
    title: str
    score: float
    evidence_count: int
    summary: str
    recommendation: str
    proof: list[str]


class AutoResearcher:
    def __init__(self, *, project_root: Path | None = None):
        self.project_root = project_root or PROJECT_ROOT
        self.battle_stats_path = self.project_root / "battle_stats.json"
        self.replay_dir = self.project_root / "replay_analysis"
        self.trace_dir = self.project_root / "logs" / "decision_traces"
        self.reports_dir = self.replay_dir / "reports"
        self.batch_history_dir = self.replay_dir / "batches"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.batch_history_dir.mkdir(parents=True, exist_ok=True)

    def load_battles(self) -> list[dict[str, Any]]:
        if not self.battle_stats_path.exists():
            return []
        data = json.loads(self.battle_stats_path.read_text(encoding="utf-8"))
        battles = data.get("battles", [])
        battles.sort(key=lambda b: b.get("timestamp", ""))
        return battles

    def recent_window(self, battles: list[dict[str, Any]], last_n: int = 30) -> list[dict[str, Any]]:
        return battles[-last_n:] if len(battles) > last_n else battles

    def prior_window(self, battles: list[dict[str, Any]], last_n: int = 30) -> list[dict[str, Any]]:
        if len(battles) <= last_n:
            return []
        start = max(0, len(battles) - (last_n * 2))
        end = max(0, len(battles) - last_n)
        return battles[start:end]

    def _normalize_replay_id(self, replay_id: str | None) -> str:
        if not replay_id:
            return ""
        rid = replay_id.replace("battle-", "", 1)
        return rid.removesuffix(".json")

    def _load_replay_json(self, replay_id: str | None) -> dict[str, Any] | None:
        rid = self._normalize_replay_id(replay_id)
        if not rid:
            return None
        path = self.replay_dir / f"{rid}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _load_trace_files(self, replay_id: str | None, limit: int = 60) -> list[dict[str, Any]]:
        rid = self._normalize_replay_id(replay_id)
        if not rid or not self.trace_dir.exists():
            return []
        battle_tag = f"battle-{rid}"
        paths = sorted(
            self.trace_dir.glob(f"{battle_tag}_turn*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        traces: list[dict[str, Any]] = []
        for path in paths[-limit:]:
            try:
                traces.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return traces

    def _extract_log_lines(self, replay_data: dict[str, Any] | None) -> list[str]:
        if not replay_data:
            return []
        log = replay_data.get("log", "")
        return [line.strip() for line in log.split("\n") if line.strip()]

    def _parse_bot_slot(self, lines: Iterable[str]) -> str:
        for line in lines:
            if line.startswith("|player|"):
                parts = line.split("|")
                if len(parts) >= 4 and BOT_USERNAME.lower() in parts[3].lower():
                    return parts[2]
        return "p1"

    def _hazard_issue(self, lines: list[str], bot_slot: str) -> tuple[bool, str]:
        our_rocks = False
        opp_rocks = False
        for line in lines:
            if "|-sidestart|" in line and ("Stealth Rock" in line or "Spikes" in line):
                if f"|{bot_slot}:" in line:
                    opp_rocks = True
                else:
                    our_rocks = True
        if opp_rocks and not our_rocks:
            return True, "opponent won the hazard race while bot never established its own chip engine"
        if not our_rocks:
            return True, "bot never established Stealth Rock or equivalent chip pressure"
        return False, ""

    def _early_faint_issue(self, lines: list[str], bot_slot: str) -> tuple[bool, str]:
        current_turn = 0
        our_faints: list[tuple[int, str]] = []
        for line in lines:
            if line.startswith("|turn|"):
                try:
                    current_turn = int(line.split("|")[2])
                except Exception:
                    pass
            elif line.startswith("|faint|") and f"|{bot_slot}" in line:
                mon = line.split(":")[-1].strip()
                our_faints.append((current_turn, mon))
        early = [entry for entry in our_faints if entry[0] and entry[0] <= 8]
        if len(early) >= 2:
            mons = ", ".join(mon for _, mon in early[:3])
            return True, f"multiple Pokemon fainted by turn 8 ({mons})"
        return False, ""

    def _long_game_loss_issue(self, battle: dict[str, Any], replay_data: dict[str, Any] | None) -> tuple[bool, str]:
        if battle.get("result") != "loss":
            return False, ""
        log = self._extract_log_lines(replay_data)
        turns = 0
        for line in log:
            if line.startswith("|turn|"):
                try:
                    turns = max(turns, int(line.split("|")[2]))
                except Exception:
                    pass
        if turns >= 35:
            return True, f"loss lasted {turns} turns, suggesting endgame conversion or long-game planning failure"
        return False, ""

    def _trace_issue(self, traces: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        choice_counter: Counter[str] = Counter()
        reasons: list[str] = []
        for trace in traces:
            choice = str(trace.get("choice", "")).strip()
            if choice:
                choice_counter[choice] += 1
            reason = str(trace.get("reason", "")).strip()
            if reason:
                reasons.append(reason)
        repeated = [name for name, count in choice_counter.items() if count >= 3]
        findings: list[str] = []
        if repeated:
            findings.append(f"repeated same action patterns: {', '.join(repeated[:3])}")
        if reasons:
            timeout_count = sum(1 for r in reasons if "timeout" in r or "fallback" in r or "error" in r)
            if timeout_count >= 2:
                findings.append(f"decision traces show {timeout_count} fallback/timeout/error selections")
        return findings, reasons

    def _build_batch_identity(self, window: list[dict[str, Any]]) -> dict[str, Any]:
        if not window:
            return {
                "id": "batch-empty",
                "start_battle_id": None,
                "end_battle_id": None,
                "start_timestamp": None,
                "end_timestamp": None,
                "size": 0,
            }
        start = window[0]
        end = window[-1]
        end_slug = str(end.get("battle_id") or "unknown").replace("battle-gen9ou-", "")[:16]
        return {
            "id": f"batch-{len(window)}-{end_slug}",
            "start_battle_id": start.get("battle_id"),
            "end_battle_id": end.get("battle_id"),
            "start_timestamp": start.get("timestamp"),
            "end_timestamp": end.get("timestamp"),
            "size": len(window),
        }

    def _summarize_window(self, window: list[dict[str, Any]]) -> dict[str, Any]:
        wins = sum(1 for battle in window if battle.get("result") == "win")
        losses = sum(1 for battle in window if battle.get("result") == "loss")
        total = len(window)
        win_rate = (wins / total) if total else 0.0
        teams = Counter(str(battle.get("team_file", "unknown")) for battle in window)
        return {
            "wins": wins,
            "losses": losses,
            "total": total,
            "win_rate": win_rate,
            "record": f"{wins}-{losses} ({round(win_rate * 100)}% WR)" if total else "0-0 (0% WR)",
            "top_teams": [{"team": team, "count": count} for team, count in teams.most_common(3)],
        }

    def _compare_issue_maps(self, current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
        current_issues = {issue["key"]: issue for issue in current.get("issues", [])}
        previous_issues = {issue["key"]: issue for issue in previous.get("issues", [])}
        keys = sorted(set(current_issues) | set(previous_issues))
        shifts = []
        for key in keys:
            cur = current_issues.get(key)
            prev = previous_issues.get(key)
            cur_count = int((cur or {}).get("evidence_count", 0))
            prev_count = int((prev or {}).get("evidence_count", 0))
            delta = cur_count - prev_count
            if delta == 0 and cur_count == 0 and prev_count == 0:
                continue
            shifts.append({
                "key": key,
                "title": (cur or prev or {}).get("title", key),
                "current_count": cur_count,
                "previous_count": prev_count,
                "delta": delta,
                "direction": "worse" if delta > 0 else "better" if delta < 0 else "flat",
            })
        shifts.sort(key=lambda item: (abs(item["delta"]), item["current_count"], item["title"]), reverse=True)
        return {
            "top_shift": shifts[0] if shifts else None,
            "shifts": shifts[:5],
        }

    def _compare_opponents(self, current: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
        current_map = {item["pokemon"]: item["count"] for item in current.get("top_opponent_pokemon", [])}
        previous_map = {item["pokemon"]: item["count"] for item in previous.get("top_opponent_pokemon", [])}
        rows = []
        for pokemon in sorted(set(current_map) | set(previous_map)):
            delta = current_map.get(pokemon, 0) - previous_map.get(pokemon, 0)
            if delta:
                rows.append({
                    "pokemon": pokemon,
                    "current_count": current_map.get(pokemon, 0),
                    "previous_count": previous_map.get(pokemon, 0),
                    "delta": delta,
                })
        rows.sort(key=lambda row: abs(row["delta"]), reverse=True)
        return rows[:5]

    def _build_regression_summary(self, current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any] | None:
        if not previous:
            return None
        current_wr = float(current.get("win_rate", 0.0))
        previous_wr = float(previous.get("win_rate", 0.0))
        wr_delta = current_wr - previous_wr
        issue_compare = self._compare_issue_maps(current, previous)
        opponent_shifts = self._compare_opponents(current, previous)
        status = "flat"
        if wr_delta >= 0.10:
            status = "improving"
        elif wr_delta <= -0.10:
            status = "regressing"
        elif issue_compare.get("top_shift") and issue_compare["top_shift"]["delta"] > 0:
            status = "watch"
        lead = issue_compare.get("top_shift")
        summary_parts = [
            f"current batch {current['window_summary']['record']} vs previous {previous['window_summary']['record']}"
        ]
        if wr_delta:
            summary_parts.append(f"WR delta {wr_delta:+.0%}")
        if lead:
            verb = "up" if lead["delta"] > 0 else "down" if lead["delta"] < 0 else "flat"
            summary_parts.append(f"lead issue shift: {lead['title']} {verb} ({lead['previous_count']}→{lead['current_count']})")
        return {
            "status": status,
            "previous_batch_id": previous.get("batch", {}).get("id"),
            "win_rate_delta": wr_delta,
            "record_delta": {
                "wins": current.get("wins", 0) - previous.get("wins", 0),
                "losses": current.get("losses", 0) - previous.get("losses", 0),
            },
            "issue_compare": issue_compare,
            "opponent_shifts": opponent_shifts,
            "summary_line": "; ".join(summary_parts),
        }

    def analyze(self, *, last_n: int = 30, battles: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        battles = battles or self.load_battles()
        window = self.recent_window(battles, last_n=last_n)
        losses = [b for b in window if b.get("result") == "loss"]
        wins = [b for b in window if b.get("result") == "win"]

        pattern_counter: Counter[str] = Counter()
        evidence_map: dict[str, list[str]] = defaultdict(list)
        team_counter: Counter[str] = Counter()
        opponent_counter: Counter[str] = Counter()

        for battle in losses:
            replay_data = self._load_replay_json(battle.get("replay_id") or battle.get("battle_id"))
            lines = self._extract_log_lines(replay_data)
            bot_slot = self._parse_bot_slot(lines) if lines else "p1"
            battle_label = battle.get("battle_id", "unknown")
            team_counter[str(battle.get("team_file", "unknown"))] += 1

            for line in lines:
                if line.startswith("|poke|") and f"|{'p2' if bot_slot == 'p1' else 'p1'}|" in line:
                    species = line.split("|")[3].split(",")[0].strip()
                    if species:
                        opponent_counter[species] += 1

            hazard_issue, hazard_detail = self._hazard_issue(lines, bot_slot)
            if hazard_issue:
                pattern_counter["hazard_pressure"] += 1
                evidence_map["hazard_pressure"].append(f"{battle_label}: {hazard_detail}")

            early_issue, early_detail = self._early_faint_issue(lines, bot_slot)
            if early_issue:
                pattern_counter["early_bleeding"] += 1
                evidence_map["early_bleeding"].append(f"{battle_label}: {early_detail}")

            long_issue, long_detail = self._long_game_loss_issue(battle, replay_data)
            if long_issue:
                pattern_counter["endgame_conversion"] += 1
                evidence_map["endgame_conversion"].append(f"{battle_label}: {long_detail}")

            trace_findings, _ = self._trace_issue(self._load_trace_files(battle.get("replay_id") or battle.get("battle_id")))
            if trace_findings:
                pattern_counter["decision_instability"] += 1
                evidence_map["decision_instability"].append(f"{battle_label}: {'; '.join(trace_findings[:2])}")

        issue_defs = {
            "hazard_pressure": (
                "Hazard pressure is being lost",
                "Losses repeatedly come from games where Fouler Play never establishes its own chip engine or gives up the hazards race.",
                "Raise hazard-setting urgency earlier in neutral matchups, especially on stall/fat lines that need passive damage to convert.",
            ),
            "early_bleeding": (
                "Opening turns are bleeding too much material",
                "Recent losses often involve multiple early faints before the bot stabilizes.",
                "Bias opening decisions toward preserving defensive pivots and reduce greedy lines before turn 8.",
            ),
            "endgame_conversion": (
                "Long games are not being converted cleanly",
                "The bot reaches playable long games and still loses after turn 35, pointing to endgame planning weakness rather than raw matchup hopelessness.",
                "Add stronger endgame-preservation heuristics: protect wincon HP, value recovery higher, and avoid unnecessary trades once ahead on resources.",
            ),
            "decision_instability": (
                "Decision traces show unstable fallback behavior",
                "Losses contain repeated fallback/timeout/error decisions or obvious repeated action loops.",
                "Prioritize stability fixes around slow or failing decision branches before chasing niche matchup ideas.",
            ),
        }

        issues: list[ResearchIssue] = []
        for key, count in pattern_counter.most_common():
            title, summary, recommendation = issue_defs[key]
            proof = evidence_map.get(key, [])[:5]
            score = count + min(len(proof) * 0.1, 0.5)
            issues.append(
                ResearchIssue(
                    key=key,
                    title=title,
                    score=score,
                    evidence_count=len(proof),
                    summary=summary,
                    recommendation=recommendation,
                    proof=proof,
                )
            )

        top_issue = issues[0] if issues else None
        top_opponents = [{"pokemon": k, "count": v} for k, v in opponent_counter.most_common(5)]
        top_teams = [{"team": k, "losses": v} for k, v in team_counter.most_common(5)]
        batch_identity = self._build_batch_identity(window)
        window_summary = self._summarize_window(window)

        result = {
            "generated_at": datetime.now().isoformat(),
            "batch": batch_identity,
            "window_size": len(window),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(window)) if window else 0.0,
            "window_summary": window_summary,
            "top_issue": {
                "key": top_issue.key,
                "title": top_issue.title,
                "summary": top_issue.summary,
                "recommendation": top_issue.recommendation,
                "proof": top_issue.proof,
            } if top_issue else None,
            "issues": [
                {
                    "key": issue.key,
                    "title": issue.title,
                    "score": issue.score,
                    "evidence_count": issue.evidence_count,
                    "summary": issue.summary,
                    "recommendation": issue.recommendation,
                    "proof": issue.proof,
                }
                for issue in issues
            ],
            "top_loss_teams": top_teams,
            "top_opponent_pokemon": top_opponents,
        }
        return result

    def compare_with_previous(self, battles: list[dict[str, Any]], current_report: dict[str, Any], *, last_n: int) -> dict[str, Any] | None:
        previous_window = self.prior_window(battles, last_n=last_n)
        if not previous_window:
            return None
        previous_report = self.analyze(last_n=len(previous_window), battles=previous_window)
        previous_report["batch"] = self._build_batch_identity(previous_window)
        previous_report["window_summary"] = self._summarize_window(previous_window)
        return self._build_regression_summary(current_report, previous_report) | {"previous_report": previous_report}

    def render_markdown(self, report: dict[str, Any]) -> str:
        lines = []
        lines.append("# Fouler Play Autoresearch")
        lines.append("")
        lines.append(f"Generated: {report['generated_at']}")
        lines.append(f"Batch: {report.get('batch', {}).get('id', 'unknown')}")
        lines.append(f"Window: last {report['window_size']} battles")
        lines.append(f"Record: {report['wins']}-{report['losses']} ({report['win_rate']:.1%} WR)")
        lines.append("")

        regression = report.get("regression") or {}
        if regression:
            lines.append("## Regression check vs previous batch")
            lines.append(f"Status: **{regression.get('status', 'unknown')}**")
            lines.append(f"Comparison: {regression.get('summary_line', 'n/a')}")
            lead_shift = ((regression.get("issue_compare") or {}).get("top_shift")) or None
            if lead_shift:
                lines.append(
                    f"Lead issue shift: {lead_shift['title']} ({lead_shift['previous_count']} → {lead_shift['current_count']}, delta {lead_shift['delta']:+d})"
                )
            for shift in regression.get("opponent_shifts", [])[:3]:
                lines.append(
                    f"- Opponent shift: {shift['pokemon']} ({shift['previous_count']} → {shift['current_count']}, delta {shift['delta']:+d})"
                )
            lines.append("")

        top = report.get("top_issue")
        if top:
            lines.append("## Top issue")
            lines.append(f"**{top['title']}**")
            lines.append(top['summary'])
            lines.append("")
            lines.append("### Evidence")
            for proof in top.get("proof", []):
                lines.append(f"- {proof}")
            lines.append("")
            lines.append("### Next action")
            lines.append(f"- {top['recommendation']}")
            lines.append("")

        lines.append("## Ranked issues")
        for idx, issue in enumerate(report.get("issues", []), start=1):
            lines.append(f"### {idx}. {issue['title']}")
            lines.append(f"- Summary: {issue['summary']}")
            lines.append(f"- Recommendation: {issue['recommendation']}")
            for proof in issue.get("proof", [])[:3]:
                lines.append(f"- Proof: {proof}")
            lines.append("")

        lines.append("## Frequent loss contexts")
        for item in report.get("top_loss_teams", []):
            lines.append(f"- Team {item['team']}: {item['losses']} losses")
        for item in report.get("top_opponent_pokemon", []):
            lines.append(f"- Opponent pokemon {item['pokemon']}: seen in {item['count']} loss replays")
        return "\n".join(lines).strip() + "\n"

    def save_report(self, report: dict[str, Any]) -> tuple[Path, Path]:
        json_path = self.replay_dir / "autoresearch_latest.json"
        md_path = self.reports_dir / "autoresearch_latest.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(self.render_markdown(report), encoding="utf-8")

        batch_id = report.get("batch", {}).get("id", "batch-unknown")
        historical_json = self.batch_history_dir / f"{batch_id}.json"
        historical_md = self.batch_history_dir / f"{batch_id}.md"
        historical_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        historical_md.write_text(self.render_markdown(report), encoding="utf-8")
        return json_path, md_path

    def queue_discord_reports(self, report: dict[str, Any], *, json_path: Path, md_path: Path) -> None:
        top = report.get("top_issue") or {}
        regression = report.get("regression") or {}
        issue_shift = ((regression.get("issue_compare") or {}).get("top_shift")) or {}
        regression_line = regression.get("summary_line", "")
        trend_label = regression.get("status") or ("improving" if report['wins'] > report['losses'] else "slipping" if report['losses'] > report['wins'] else "flat")

        routine_payload = build_contract_payload(
            "PROOF",
            f"autoresearch refresh: {top.get('title', 'no top issue found')}",
            f"autoresearch scanned the latest {report['window_size']} battles and found {len(report.get('issues', []))} recurring issue clusters.",
            "Routine fouler-play updates should stay compact and point directly at the highest-value next fix.",
            f"artifact {json_path.name}; artifact {md_path.name}; window {report['window_size']}; top issue {top.get('key', 'none')}",
            top.get("recommendation", "Collect more battles and rerun autoresearch."),
            source="replay_analysis.autoresearch",
            report=json_path.name,
            markdown_report=md_path.name,
            window=report["window_size"],
            top_issue=top.get("key", "none"),
            top_issues=top.get("title", "no dominant issue found"),
            recent_record=f"last {report['window_size']}: {report['wins']}-{report['losses']} ({round(report['win_rate'] * 100)}% WR)",
            loss_pattern=top.get("summary", ""),
            next_battle_action=top.get("recommendation", "Collect more battles and rerun autoresearch."),
            trend=trend_label,
            performance_change=regression_line,
            code_fix_hint=(
                f"compare vs {regression.get('previous_batch_id')}: {issue_shift.get('title', '')} {issue_shift.get('previous_count', 0)}→{issue_shift.get('current_count', 0)}"
                if regression.get("previous_batch_id") and issue_shift else ""
            ),
        )
        queue_event("autoresearch_summary", ROUTINE_CHANNEL, routine_payload, dedup_window_sec=15)

        deep_lines = [
            f"[PROOF] autoresearch deep dive: {top.get('title', 'no top issue found')}",
            f"What happened: scanned last {report['window_size']} battles; record {report['wins']}-{report['losses']} ({report['win_rate']:.1%} WR); ranked {len(report.get('issues', []))} recurring loss patterns.",
            f"Why it matters: {top.get('summary', 'No dominant issue yet.')}"
        ]
        if regression_line:
            deep_lines.append(f"Regression check: {regression_line}")
        proof_bits = list(top.get("proof", []))[:4]
        for shift in regression.get("opponent_shifts", [])[:3]:
            proof_bits.append(
                f"opponent shift {shift['pokemon']} {shift['previous_count']}→{shift['current_count']} ({shift['delta']:+d})"
            )
        proof_bits.append(f"artifact {json_path.name}")
        proof_bits.append(f"artifact {md_path.name}")
        deep_lines.append("Proof: " + "; ".join(proof_bits))
        deep_lines.append("Remaining: " + top.get("recommendation", "Collect more battle evidence."))
        queue_event("autoresearch_deep_dive", RESEARCH_CHANNEL, "\n".join(deep_lines), dedup_window_sec=15)


def run_autoresearch(*, last_n: int = 30, queue_discord: bool = True) -> dict[str, Any]:
    researcher = AutoResearcher()
    battles = researcher.load_battles()
    report = researcher.analyze(last_n=last_n, battles=battles)
    regression = researcher.compare_with_previous(battles, report, last_n=last_n)
    if regression:
        previous_report = regression.pop("previous_report")
        report["regression"] = regression
        report["previous_batch"] = {
            "batch": previous_report.get("batch", {}),
            "window_summary": previous_report.get("window_summary", {}),
            "top_issue": previous_report.get("top_issue"),
        }
    json_path, md_path = researcher.save_report(report)
    if queue_discord:
        researcher.queue_discord_reports(report, json_path=json_path, md_path=md_path)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Fouler Play autoresearch")
    parser.add_argument("-n", "--num-battles", type=int, default=30)
    parser.add_argument("--no-discord", action="store_true")
    args = parser.parse_args()

    report = run_autoresearch(last_n=args.num_battles, queue_discord=not args.no_discord)
    print(json.dumps(report, indent=2, ensure_ascii=False))
