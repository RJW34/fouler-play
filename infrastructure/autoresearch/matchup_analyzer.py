#!/usr/bin/env python3
"""
Matchup Analyzer — competitive-focused loss analysis.

Instead of counting surface stats ("220 Pokemon fainted"), this asks
the questions a competitive player would ask:

1. What OPPONENT Pokemon do we consistently lose to?
2. What specific matchups are we misplaying?
3. Which of our Pokemon keep dying to the same threats?
4. What play patterns should change against specific threats?

Reads decision traces (which contain full opponent team snapshots)
and battle logs to build matchup-level understanding.
"""
import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DECISION_TRACES_DIR = PROJECT_ROOT / "logs" / "decision_traces"
BATTLE_STATS_FILE = PROJECT_ROOT / "battle_stats.json"
MATCHUP_WEIGHTS_FILE = PROJECT_ROOT / "fp" / "matchup_weights.json"


def _load_battle_results() -> dict[str, dict]:
    """Load battle results keyed by battle_id."""
    if not BATTLE_STATS_FILE.exists():
        return {}
    data = json.loads(BATTLE_STATS_FILE.read_text())
    results = {}
    for b in data.get("battles", []):
        bid = b.get("battle_id", "")
        results[bid] = b
    return results


def _load_traces_for_battle(battle_tag: str) -> list[dict]:
    """Load all decision traces for a specific battle."""
    traces = []
    if not DECISION_TRACES_DIR.exists():
        return traces
    for f in DECISION_TRACES_DIR.iterdir():
        if f.name.startswith(battle_tag.replace("battle-", "")) or battle_tag in f.name:
            try:
                traces.append(json.loads(f.read_text()))
            except Exception:
                continue
    traces.sort(key=lambda t: t.get("turn", 0))
    return traces


def _get_loss_battle_tags() -> list[str]:
    """Get battle tags for all losses."""
    results = _load_battle_results()
    return [
        bid for bid, data in results.items()
        if data.get("result") == "loss"
    ]


def analyze_opponent_threats() -> dict[str, Any]:
    """Find which opponent Pokemon we consistently lose to.

    Returns a ranked list of opponent Pokemon that appear most often
    in our losses, with details about what they did to us.
    """
    loss_tags = _get_loss_battle_tags()
    results = _load_battle_results()

    # Count opponent Pokemon that appear in losses
    opponent_pokemon_in_losses: Counter = Counter()
    opponent_pokemon_details: dict[str, list] = defaultdict(list)

    for tag in loss_tags:
        traces = _load_traces_for_battle(tag)
        if not traces:
            continue

        team_file = results.get(tag, {}).get("team_file", "unknown")
        seen_opponents = set()

        for trace in traces:
            snap = trace.get("snapshot", {})
            opp_active = snap.get("opponent", {}).get("active", {})
            opp_name = opp_active.get("name", "")

            if opp_name and opp_name not in seen_opponents:
                seen_opponents.add(opp_name)

            # Check opponent reserve too
            for reserve in snap.get("opponent", {}).get("reserve", []):
                rname = reserve.get("name", "")
                if rname:
                    seen_opponents.add(rname)

        for opp in seen_opponents:
            opponent_pokemon_in_losses[opp] += 1
            opponent_pokemon_details[opp].append({
                "battle": tag,
                "team": team_file,
            })

    # Rank by frequency
    ranked = []
    total_losses = len(loss_tags)
    for pokemon, count in opponent_pokemon_in_losses.most_common(20):
        teams_we_lost_with = Counter(
            d["team"] for d in opponent_pokemon_details[pokemon]
        )
        ranked.append({
            "pokemon": pokemon,
            "losses_against": count,
            "loss_rate_pct": round(count / max(total_losses, 1) * 100, 1),
            "teams_affected": dict(teams_we_lost_with.most_common(5)),
        })

    return {
        "total_losses_analyzed": total_losses,
        "top_threats": ranked[:15],
    }


def analyze_our_casualties() -> dict[str, Any]:
    """Find which of OUR Pokemon keep dying and to what.

    Looks at traces where our Pokemon fainted and identifies
    what opponent Pokemon was active when they went down.
    """
    loss_tags = _get_loss_battle_tags()

    # Track: our_pokemon -> opponent_pokemon that was active when ours fainted
    casualty_matchups: dict[str, Counter] = defaultdict(Counter)
    casualty_count: Counter = Counter()

    for tag in loss_tags:
        traces = _load_traces_for_battle(tag)
        prev_our_active = None

        for trace in traces:
            snap = trace.get("snapshot", {})
            # Our side
            our_active = None
            user = snap.get("opponent", {})  # Note: snapshot perspective matters
            # Check both sides to find our team
            for side_key in ["user", "opponent"]:
                side = snap.get(side_key, {})
                active = side.get("active", {})
                if active and active.get("name"):
                    if side_key == "user":
                        our_active = active.get("name")

            opp = snap.get("opponent", {}).get("active", {})
            opp_name = opp.get("name", "")

            # Detect faint: our active changed and prev had low HP
            if prev_our_active and our_active and prev_our_active != our_active:
                # A switch happened — could be voluntary or from faint
                # Check reserve to see if prev is still alive
                reserve = snap.get("user", {}).get("reserve", []) if "user" in snap else []
                prev_alive = any(
                    r.get("name") == prev_our_active and not r.get("fainted", False)
                    for r in reserve
                )
                if not prev_alive:
                    casualty_count[prev_our_active] += 1
                    if opp_name:
                        casualty_matchups[prev_our_active][opp_name] += 1

            prev_our_active = our_active

    ranked = []
    for our_mon, count in casualty_count.most_common(10):
        killers = casualty_matchups[our_mon].most_common(5)
        ranked.append({
            "our_pokemon": our_mon,
            "times_fainted": count,
            "killed_by": [{"opponent": k, "count": c} for k, c in killers],
        })

    return {
        "casualties": ranked,
    }


def analyze_team_matchups() -> dict[str, Any]:
    """Analyze per-team win/loss patterns against opponent archetypes.

    Identifies which teams struggle against what.
    """
    results = _load_battle_results()

    team_vs_opponent: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"wins": 0, "losses": 0})
    )

    for tag, data in results.items():
        team = data.get("team_file", "unknown")
        result = data.get("result", "")
        if result not in ("win", "loss"):
            continue

        traces = _load_traces_for_battle(tag)
        if not traces:
            continue

        # Get opponent team composition from first trace
        first = traces[0]
        snap = first.get("snapshot", {})
        opp_reserve = snap.get("opponent", {}).get("reserve", [])
        opp_active = snap.get("opponent", {}).get("active", {})

        opp_team = set()
        if opp_active and opp_active.get("name"):
            opp_team.add(opp_active["name"])
        for r in opp_reserve:
            if r.get("name"):
                opp_team.add(r["name"])

        # Track results against each opponent Pokemon
        for opp_mon in opp_team:
            if result == "win":
                team_vs_opponent[team][opp_mon]["wins"] += 1
            else:
                team_vs_opponent[team][opp_mon]["losses"] += 1

    # Find problem matchups: opponents we lose to more than we beat
    problem_matchups = []
    for team, opponents in team_vs_opponent.items():
        for opp_mon, record in opponents.items():
            total = record["wins"] + record["losses"]
            if total < 3:
                continue  # Not enough data
            win_rate = record["wins"] / total
            if win_rate < 0.4:  # We lose more than 60% against this Pokemon
                problem_matchups.append({
                    "team": team,
                    "opponent_pokemon": opp_mon,
                    "wins": record["wins"],
                    "losses": record["losses"],
                    "win_rate_pct": round(win_rate * 100, 1),
                    "sample_size": total,
                })

    problem_matchups.sort(key=lambda x: x["win_rate_pct"])

    return {
        "problem_matchups": problem_matchups[:20],
    }


def update_matchup_weights() -> dict:
    """Analyze battle data and write/update fp/matchup_weights.json.

    This is the key feedback loop: analysis results get written to a file
    that the decision engine reads at startup to adjust switch evaluations.

    Returns the weights dict that was written.
    """
    results = _load_battle_results()
    matchups = analyze_team_matchups()
    casualties = analyze_our_casualties()

    # Build the bad_matchups structure:
    # team -> opponent_pokemon -> {loss_rate, sample_size, suggested_lead}
    bad_matchups: dict[str, dict] = {}

    for problem in matchups.get("problem_matchups", []):
        team = problem["team"]
        opp_mon = problem["opponent_pokemon"]
        total = problem["sample_size"]
        loss_rate = round(1.0 - problem["win_rate_pct"] / 100.0, 3)

        if team not in bad_matchups:
            bad_matchups[team] = {}

        # Determine suggested lead: find which of our Pokemon fared best
        # against this opponent by checking casualty data
        suggested_lead = _find_best_counter(team, opp_mon, results)

        bad_matchups[team][opp_mon] = {
            "loss_rate": loss_rate,
            "sample_size": total,
            "suggested_lead": suggested_lead,
        }

    # Build good_leads structure from casualty analysis:
    # our_pokemon -> list of opponents they struggle against
    problem_pokemon: dict[str, list[str]] = {}
    for casualty in casualties.get("casualties", []):
        our_mon = casualty["our_pokemon"]
        killers = [k["opponent"] for k in casualty.get("killed_by", []) if k["count"] >= 2]
        if killers:
            problem_pokemon[our_mon] = killers

    weights = {
        "bad_matchups": bad_matchups,
        "problem_pokemon": problem_pokemon,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Write the file
    try:
        MATCHUP_WEIGHTS_FILE.write_text(json.dumps(weights, indent=2))
        logger.info("Updated matchup weights: %d teams with bad matchups", len(bad_matchups))
    except Exception as e:
        logger.error("Failed to write matchup weights: %s", e)

    return weights


def _find_best_counter(team: str, opponent_mon: str, results: dict) -> str | None:
    """Find the best Pokemon on our team to lead against a specific opponent.

    Looks at battles where we WON against teams containing this opponent
    and finds which of our Pokemon was active when we had momentum.
    Returns None if no clear counter is found.
    """
    # For now, return None — this can be enriched as more trace data accumulates.
    # The structure is in place for the decision engine to use.
    return None


def get_competitive_brief() -> str:
    """Generate a competitive-focused improvement brief.

    This replaces the surface-level stats with actual matchup analysis.
    """
    threats = analyze_opponent_threats()
    matchups = analyze_team_matchups()

    lines = ["## Fouler-Play Competitive Analysis\n"]

    # Top threats
    top = threats.get("top_threats", [])[:8]
    if top:
        lines.append(f"### Opponent Threats (across {threats['total_losses_analyzed']} losses)")
        lines.append("Pokemon we lose to most often:\n")
        for t in top:
            teams = ", ".join(f"{k}({v})" for k, v in t["teams_affected"].items())
            lines.append(
                f"  - **{t['pokemon']}**: in {t['losses_against']} losses "
                f"({t['loss_rate_pct']}%) — teams: {teams}"
            )
        lines.append("")

    # Problem matchups
    problems = matchups.get("problem_matchups", [])[:10]
    if problems:
        lines.append("### Problem Matchups (we lose >60% against these)")
        lines.append("Team-specific weaknesses to address:\n")
        for p in problems:
            lines.append(
                f"  - **{p['team']}** vs {p['opponent_pokemon']}: "
                f"{p['wins']}W-{p['losses']}L ({p['win_rate_pct']}% WR, n={p['sample_size']})"
            )
        lines.append("")

    if not top and not problems:
        lines.append("Not enough trace data for competitive analysis. Need more battles with decision traces enabled.")

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print(get_competitive_brief())
    print()
    weights = update_matchup_weights()
    n_bad = sum(len(v) for v in weights.get("bad_matchups", {}).values())
    print(f"Matchup weights updated: {n_bad} bad matchups tracked")
    print(f"Written to: {MATCHUP_WEIGHTS_FILE}")
