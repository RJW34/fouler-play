#!/usr/bin/env python3
"""
Fouler Play Team Performance Analytics
=======================================
Analyzes battle_stats.json and replay files to produce per-team performance
reports, identify worst matchups, track per-Pokemon KO/faint rates, and
generate actionable recommendations.

Usage:
    python -m replay_analysis.team_performance            # from project root
    python replay_analysis/team_performance.py             # standalone
    python replay_analysis/team_performance.py --json      # JSON-only output
    python replay_analysis/team_performance.py --summary   # human-readable only
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BATTLE_STATS_PATH = PROJECT_ROOT / "battle_stats.json"
TEAMS_DIR = PROJECT_ROOT / "teams" / "teams"
REPLAY_ANALYSIS_DIR = PROJECT_ROOT / "replay_analysis"
LOSSES_DIR = REPLAY_ANALYSIS_DIR / "losses"
REPORT_OUTPUT_PATH = REPLAY_ANALYSIS_DIR / "team_report.json"

# The bot's username on Showdown (used when parsing replay logs to determine
# which side is "ours").
BOT_USERNAME = "ALL CHUNG"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BattleRecord:
    """A single battle entry from battle_stats.json."""
    battle_id: str
    timestamp: str
    team_file: str
    result: str          # "win" | "loss"
    rating_before: int
    rating_after: int
    opponent_pokemon: List[str]
    bot_pokemon: List[str]
    replay_id: str


@dataclass
class ReplayFaintEvent:
    """A faint extracted from a replay log."""
    pokemon: str
    side: str            # "bot" | "opponent"
    turn: int
    killed_by: Optional[str] = None   # pokemon that delivered the KO


@dataclass
class ReplayKOEvent:
    """A KO attributed to a specific Pokemon."""
    attacker: str
    victim: str
    side: str            # which side the attacker is on: "bot" | "opponent"
    turn: int


@dataclass
class ReplayExtract:
    """Structured data pulled out of one replay JSON file."""
    battle_id: str
    bot_team: List[str]
    opponent_team: List[str]
    faints: List[ReplayFaintEvent]
    kos: List[ReplayKOEvent]
    total_turns: int
    winner: Optional[str] = None


@dataclass
class PokemonPerformance:
    """Aggregated per-Pokemon stats within a specific team."""
    name: str
    games: int = 0
    faints: int = 0
    kos: int = 0

    @property
    def faint_rate(self) -> float:
        return self.faints / self.games if self.games > 0 else 0.0

    @property
    def ko_rate(self) -> float:
        return self.kos / self.games if self.games > 0 else 0.0


@dataclass
class OpponentMatchup:
    """Win/loss record for a team against a specific opponent Pokemon."""
    pokemon: str
    wins: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def loss_rate(self) -> float:
        return self.losses / self.games if self.games > 0 else 0.0


@dataclass
class RatingBracketRecord:
    """Win/loss at a particular opponent-rating bracket."""
    bracket_label: str
    bracket_min: int
    bracket_max: int
    wins: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games > 0 else 0.0


@dataclass
class TeamStats:
    """Full analytics payload for one team."""
    team_file: str
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    elo_delta: int = 0
    # Windowed win rates
    last_10_wins: int = 0
    last_10_games: int = 0
    last_25_wins: int = 0
    last_25_games: int = 0
    last_50_wins: int = 0
    last_50_games: int = 0
    # Per-opponent-Pokemon matchups
    matchups: Dict[str, OpponentMatchup] = field(default_factory=dict)
    # Per-bot-Pokemon performance (from replay extraction)
    pokemon_performance: Dict[str, PokemonPerformance] = field(default_factory=dict)
    # Rating bracket records
    rating_brackets: Dict[str, RatingBracketRecord] = field(default_factory=dict)
    # Raw records kept for windowed calculations
    _records: List[BattleRecord] = field(default_factory=list, repr=False)

    @property
    def win_rate(self) -> float:
        return self.wins / self.games_played if self.games_played > 0 else 0.0

    @property
    def last_10_win_rate(self) -> float:
        return self.last_10_wins / self.last_10_games if self.last_10_games > 0 else 0.0

    @property
    def last_25_win_rate(self) -> float:
        return self.last_25_wins / self.last_25_games if self.last_25_games > 0 else 0.0

    @property
    def last_50_win_rate(self) -> float:
        return self.last_50_wins / self.last_50_games if self.last_50_games > 0 else 0.0

    @property
    def trend(self) -> str:
        """Compare last-10 win rate to overall to determine trend direction."""
        if self.last_10_games < 3:
            return "insufficient_data"
        diff = self.last_10_win_rate - self.win_rate
        if diff > 0.10:
            return "improving"
        if diff < -0.10:
            return "declining"
        return "stable"


# ---------------------------------------------------------------------------
# Utility: Wilson score confidence interval
# ---------------------------------------------------------------------------

def wilson_confidence_interval(
    wins: int, total: int, z: float = 1.96
) -> Tuple[float, float]:
    """
    Return the (lower, upper) bounds of the Wilson score interval for a
    binomial proportion.  *z* = 1.96 gives a 95 % confidence interval.
    Returns (0.0, 1.0) when *total* is zero.
    """
    if total == 0:
        return (0.0, 1.0)
    p_hat = wins / total
    denom = 1 + z * z / total
    centre = p_hat + z * z / (2 * total)
    spread = z * math.sqrt((p_hat * (1 - p_hat) + z * z / (4 * total)) / total)
    lower = max(0.0, (centre - spread) / denom)
    upper = min(1.0, (centre + spread) / denom)
    return (lower, upper)


# ---------------------------------------------------------------------------
# Team file parsing and matching
# ---------------------------------------------------------------------------

def parse_team_file(path: Path) -> List[str]:
    """
    Read a Showdown team paste and return the list of Pokemon species names
    (normalised to lower-case, no gender/forme suffixes stripped -- just the
    species token as it appears before the ``@`` or ``(`` on the first line
    of each Pokemon block).
    """
    pokemon: List[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return pokemon

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # A Pokemon line is the first non-blank line of a block and either
        # contains ``@`` (item) or starts a new Pokemon definition.
        # Examples:
        #   "Gliscor @ Toxic Orb"
        #   "Blissey (F) @ Heavy-Duty Boots"
        #   "Slowking-Galar @ Shuca Berry"
        if "@" in line or (line and not line.startswith(("Ability:", "Tera ", "EVs:", "IVs:", "-", "Bold", "Adamant", "Jolly", "Timid", "Careful", "Relaxed", "Impish", "Naive", "Calm", "Modest"))):
            # Only treat it as a Pokemon header if it contains '@'
            if "@" not in line:
                continue
            name_part = line.split("@")[0].strip()
            # Strip gender markers like ``(F)`` or ``(M)``
            name_part = re.sub(r"\s*\([MF]\)\s*$", "", name_part).strip()
            if name_part:
                pokemon.append(name_part)
    return pokemon


def _normalise_pokemon_name(name: str) -> str:
    """
    Collapse a Pokemon name to a canonical lower-case key so that minor
    spelling/form differences don't prevent matching.
    e.g. "Slowking-Galar" -> "slowkinggalar", "Ting-Lu" -> "tinglu"
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def load_all_team_files() -> Dict[str, Set[str]]:
    """
    Scan *teams/teams/* recursively and return a mapping of
    ``relative_team_path -> frozenset_of_normalised_pokemon_names``.

    The relative path uses forward slashes and no extension, matching the
    ``team_file`` field in battle_stats.json (e.g. ``gen9/ou/fat-team-1-stall``).
    """
    teams: Dict[str, Set[str]] = {}
    for team_path in TEAMS_DIR.rglob("*"):
        if team_path.is_dir():
            continue
        # Skip hidden files, READMEs, etc.
        if team_path.name.startswith(".") or team_path.suffix in (".md", ".txt"):
            continue
        pokemon = parse_team_file(team_path)
        if not pokemon:
            continue
        rel = team_path.relative_to(TEAMS_DIR).as_posix()
        teams[rel] = {_normalise_pokemon_name(p) for p in pokemon}
    return teams


def match_team_file(pokemon_names: List[str]) -> Optional[str]:
    """
    Given a list of 6 (or fewer) Pokemon species names from a battle, return
    the ``team_file`` key (relative path) of the best matching team on disk,
    or ``None`` if no team matches well enough.

    Matching strategy:
      1. Normalise both sides.
      2. Pick the team file with the highest Jaccard similarity.
      3. Require at least 4 out of 6 Pokemon in common to accept a match.
    """
    all_teams = load_all_team_files()
    if not all_teams:
        return None

    query = {_normalise_pokemon_name(p) for p in pokemon_names}
    best_path: Optional[str] = None
    best_score: float = 0.0

    for rel_path, team_set in all_teams.items():
        if not team_set:
            continue
        intersection = len(query & team_set)
        union = len(query | team_set)
        jaccard = intersection / union if union else 0.0
        if jaccard > best_score:
            best_score = jaccard
            best_path = rel_path

    # Require at least 4/6 overlap (Jaccard >= 4/8 = 0.5 in the worst case
    # where both sets have 6 members with 4 shared gives union = 8).
    if best_score < 0.45:
        return None
    return best_path


# ---------------------------------------------------------------------------
# Replay log parsing
# ---------------------------------------------------------------------------

def parse_replay_json(replay_path: Path) -> Optional[ReplayExtract]:
    """
    Parse a Pokemon Showdown replay JSON file and extract team compositions,
    faint events, and KO attributions.

    The replay JSON has a ``log`` field containing the Showdown protocol text.
    """
    try:
        data = json.loads(replay_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    log_text: str = data.get("log", "")
    if not log_text:
        return None

    battle_id: str = data.get("id", replay_path.stem)
    lines = log_text.split("\n")

    # Determine which player slot is the bot.
    bot_player: Optional[str] = None  # "p1" or "p2"
    for line in lines:
        if line.startswith("|player|"):
            parts = line.split("|")
            # |player|p1|ALL CHUNG|...
            if len(parts) >= 4 and BOT_USERNAME.lower() in parts[3].lower():
                bot_player = parts[2]
                break

    if bot_player is None:
        # Fallback: assume p1
        bot_player = "p1"
    opp_player = "p2" if bot_player == "p1" else "p1"

    # Collect team previews
    bot_team: List[str] = []
    opp_team: List[str] = []
    for line in lines:
        if line.startswith("|poke|"):
            parts = line.split("|")
            if len(parts) >= 4:
                player = parts[2]
                species = parts[3].split(",")[0].strip()
                if player == bot_player:
                    bot_team.append(species)
                elif player == opp_player:
                    opp_team.append(species)

    # Walk the log to find faints and attribute KOs.
    current_turn = 0
    # Track last attacking Pokemon per side so we can attribute KOs.
    last_attacker: Dict[str, str] = {}  # side_tag -> pokemon name
    # Track active Pokemon per side
    active: Dict[str, str] = {}  # "p1a" / "p2a" -> species name
    faints: List[ReplayFaintEvent] = []
    kos: List[ReplayKOEvent] = []
    winner: Optional[str] = None

    for line in lines:
        line = line.strip()

        if line.startswith("|turn|"):
            try:
                current_turn = int(line.split("|")[2])
            except (IndexError, ValueError):
                pass

        elif line.startswith("|switch|") or line.startswith("|drag|"):
            parts = line.split("|")
            if len(parts) >= 4:
                slot = parts[2].split(":")[0].strip()  # "p1a" or "p2a"
                species = parts[3].split(",")[0].strip()
                active[slot] = species

        elif line.startswith("|move|"):
            parts = line.split("|")
            if len(parts) >= 4:
                slot = parts[2].split(":")[0].strip()
                species = parts[2].split(":")[-1].strip() if ":" in parts[2] else parts[2]
                # Record the last attacker for KO attribution
                side_prefix = slot[:2]  # "p1" or "p2"
                last_attacker[side_prefix] = species

        elif line.startswith("|faint|"):
            parts = line.split("|")
            if len(parts) >= 3:
                slot = parts[2].split(":")[0].strip()
                species = parts[2].split(":")[-1].strip() if ":" in parts[2] else parts[2]
                species = species.split(",")[0].strip()
                side_prefix = slot[:2]
                faint_side = "bot" if side_prefix == bot_player else "opponent"

                # The KO was delivered by the *other* side's last attacker
                killer_side = opp_player if side_prefix == bot_player else bot_player
                killer = last_attacker.get(killer_side)

                faints.append(ReplayFaintEvent(
                    pokemon=species,
                    side=faint_side,
                    turn=current_turn,
                    killed_by=killer,
                ))
                if killer:
                    kos.append(ReplayKOEvent(
                        attacker=killer,
                        victim=species,
                        side="bot" if killer_side == bot_player else "opponent",
                        turn=current_turn,
                    ))

        elif line.startswith("|win|"):
            parts = line.split("|")
            if len(parts) >= 3:
                winner = parts[2].strip()

    return ReplayExtract(
        battle_id=battle_id,
        bot_team=bot_team,
        opponent_team=opp_team,
        faints=faints,
        kos=kos,
        total_turns=current_turn,
        winner=winner,
    )


def load_all_replays() -> Dict[str, ReplayExtract]:
    """
    Load and parse every replay JSON in the losses directory as well as the
    top-level replay_analysis directory.  Returns a dict keyed by battle_id.
    """
    extracts: Dict[str, ReplayExtract] = {}

    search_dirs = [LOSSES_DIR, REPLAY_ANALYSIS_DIR]
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for replay_file in search_dir.glob("*.json"):
            # Skip files that are clearly not replays
            if replay_file.name in ("battle_stats.json", "team_report.json",
                                    "replay_check.json", "stream_status.json",
                                    "active_battles.json"):
                continue
            ext = parse_replay_json(replay_file)
            if ext is not None and ext.bot_team:
                extracts[ext.battle_id] = ext

    return extracts


# ---------------------------------------------------------------------------
# Rating bracket helpers
# ---------------------------------------------------------------------------

RATING_BRACKETS = [
    ("below_1100", 0, 1099),
    ("1100_1199", 1100, 1199),
    ("1200_1299", 1200, 1299),
    ("1300_1399", 1300, 1399),
    ("1400_1499", 1400, 1499),
    ("1500_1599", 1500, 1599),
    ("1600_plus", 1600, 9999),
]


def _bracket_for_rating(rating: int) -> Tuple[str, int, int]:
    for label, lo, hi in RATING_BRACKETS:
        if lo <= rating <= hi:
            return (label, lo, hi)
    return ("unknown", 0, 0)


# ---------------------------------------------------------------------------
# Core analysis engine
# ---------------------------------------------------------------------------

def load_battle_stats() -> List[BattleRecord]:
    """Load battle_stats.json and return a list of BattleRecord objects."""
    if not BATTLE_STATS_PATH.is_file():
        print(f"[team_performance] battle_stats.json not found at {BATTLE_STATS_PATH}")
        return []

    try:
        raw = json.loads(BATTLE_STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[team_performance] Failed to read battle_stats.json: {exc}")
        return []

    records: List[BattleRecord] = []
    for entry in raw.get("battles", []):
        try:
            records.append(BattleRecord(
                battle_id=entry["battle_id"],
                timestamp=entry.get("timestamp", ""),
                team_file=entry.get("team_file", ""),
                result=entry.get("result", ""),
                rating_before=int(entry.get("rating_before", 0)),
                rating_after=int(entry.get("rating_after", 0)),
                opponent_pokemon=entry.get("opponent_pokemon", []),
                bot_pokemon=entry.get("bot_pokemon", []),
                replay_id=entry.get("replay_id", entry["battle_id"]),
            ))
        except (KeyError, ValueError):
            continue

    # Sort by timestamp ascending so windowed stats are computed correctly.
    records.sort(key=lambda r: r.timestamp)
    return records


def _resolve_team_file(record: BattleRecord) -> str:
    """
    Return the team_file for a battle record.  If the record already has one,
    use it.  Otherwise try to match via the bot_pokemon list.
    """
    if record.team_file:
        return record.team_file
    if record.bot_pokemon:
        matched = match_team_file(record.bot_pokemon)
        if matched:
            return matched
    return "unknown"


def build_team_stats(
    records: List[BattleRecord],
    replay_extracts: Dict[str, ReplayExtract],
) -> Dict[str, TeamStats]:
    """
    Aggregate battle records and replay data into per-team TeamStats objects.
    """
    teams: Dict[str, TeamStats] = {}

    for record in records:
        tf = _resolve_team_file(record)
        if tf not in teams:
            teams[tf] = TeamStats(team_file=tf)
        ts = teams[tf]
        ts._records.append(record)
        ts.games_played += 1
        is_win = record.result.lower() == "win"
        if is_win:
            ts.wins += 1
        else:
            ts.losses += 1
        ts.elo_delta += record.rating_after - record.rating_before

        # Per-opponent-Pokemon matchup tracking
        for opp_mon in record.opponent_pokemon:
            if opp_mon not in ts.matchups:
                ts.matchups[opp_mon] = OpponentMatchup(pokemon=opp_mon)
            if is_win:
                ts.matchups[opp_mon].wins += 1
            else:
                ts.matchups[opp_mon].losses += 1

        # Rating bracket tracking (use opponent's approximate rating =
        # our rating_before, since that is close to what the opponent was)
        bracket_label, bracket_min, bracket_max = _bracket_for_rating(record.rating_before)
        if bracket_label not in ts.rating_brackets:
            ts.rating_brackets[bracket_label] = RatingBracketRecord(
                bracket_label=bracket_label,
                bracket_min=bracket_min,
                bracket_max=bracket_max,
            )
        if is_win:
            ts.rating_brackets[bracket_label].wins += 1
        else:
            ts.rating_brackets[bracket_label].losses += 1

    # Compute windowed win rates
    for ts in teams.values():
        recs = ts._records  # already sorted by timestamp globally
        for window_size, attr_wins, attr_games in [
            (10, "last_10_wins", "last_10_games"),
            (25, "last_25_wins", "last_25_games"),
            (50, "last_50_wins", "last_50_games"),
        ]:
            tail = recs[-window_size:]
            setattr(ts, attr_games, len(tail))
            setattr(ts, attr_wins, sum(1 for r in tail if r.result.lower() == "win"))

    # Merge replay-level per-Pokemon data
    for ts in teams.values():
        team_pokemon_names: Set[str] = set()
        for rec in ts._records:
            team_pokemon_names.update(rec.bot_pokemon)

        # Initialise PokemonPerformance entries
        for pname in team_pokemon_names:
            if pname not in ts.pokemon_performance:
                ts.pokemon_performance[pname] = PokemonPerformance(name=pname)

        for rec in ts._records:
            ext = replay_extracts.get(rec.replay_id) or replay_extracts.get(rec.battle_id)
            for pname in rec.bot_pokemon:
                if pname in ts.pokemon_performance:
                    ts.pokemon_performance[pname].games += 1

            if ext is None:
                continue

            # Count faints for our Pokemon
            for faint_ev in ext.faints:
                if faint_ev.side == "bot":
                    pname = faint_ev.pokemon
                    if pname in ts.pokemon_performance:
                        ts.pokemon_performance[pname].faints += 1

            # Count KOs scored by our Pokemon
            for ko_ev in ext.kos:
                if ko_ev.side == "bot":
                    pname = ko_ev.attacker
                    if pname in ts.pokemon_performance:
                        ts.pokemon_performance[pname].kos += 1

    return teams


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

def generate_recommendations(teams: Dict[str, TeamStats]) -> Dict[str, Dict[str, Any]]:
    """
    For each team, identify the single biggest weakness and produce an
    actionable recommendation.
    """
    recommendations: Dict[str, Dict[str, Any]] = {}

    for tf, ts in teams.items():
        rec: Dict[str, Any] = {
            "team_file": tf,
            "weakness": None,
            "detail": "",
            "suggestion": "",
        }

        # Find the opponent Pokemon with the worst loss rate (min 3 games)
        worst_matchup: Optional[OpponentMatchup] = None
        worst_loss_rate = 0.0
        for mu in ts.matchups.values():
            if mu.games >= 3 and mu.loss_rate > worst_loss_rate:
                worst_loss_rate = mu.loss_rate
                worst_matchup = mu

        if worst_matchup and worst_loss_rate >= 0.55:
            lo, hi = wilson_confidence_interval(worst_matchup.losses, worst_matchup.games)
            rec["weakness"] = f"opponent_{worst_matchup.pokemon}"
            rec["detail"] = (
                f"Loses {worst_loss_rate:.0%} of games when opponent has "
                f"{worst_matchup.pokemon} ({worst_matchup.losses}L / "
                f"{worst_matchup.games}G, 95% CI [{lo:.0%}, {hi:.0%}])"
            )
            rec["suggestion"] = (
                f"Consider adding a dedicated check or counter for "
                f"{worst_matchup.pokemon} to {tf}, or avoid queuing this team "
                f"when {worst_matchup.pokemon} is common in the meta."
            )
        elif ts.games_played >= 5 and ts.win_rate < 0.45:
            rec["weakness"] = "overall_win_rate"
            rec["detail"] = (
                f"Overall win rate is only {ts.win_rate:.0%} across "
                f"{ts.games_played} games."
            )
            rec["suggestion"] = (
                f"This team may need a structural overhaul.  Review the "
                f"per-Pokemon faint rates to identify the weakest links."
            )
        else:
            rec["weakness"] = None
            rec["detail"] = "No critical weakness identified."
            rec["suggestion"] = "Keep collecting data."

        recommendations[tf] = rec

    return recommendations


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def build_json_report(
    teams: Dict[str, TeamStats],
    recommendations: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the full JSON-serialisable report dictionary."""

    report: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(),
        "total_battles": sum(ts.games_played for ts in teams.values()),
        "teams": {},
        "recommendations": recommendations,
    }

    for tf, ts in teams.items():
        # Worst matchups (sorted by loss rate descending, min 2 games)
        worst_matchups = sorted(
            [mu for mu in ts.matchups.values() if mu.games >= 2],
            key=lambda mu: mu.loss_rate,
            reverse=True,
        )[:10]

        # Per-Pokemon stats
        pokemon_stats = {}
        for pname, pp in ts.pokemon_performance.items():
            lo_faint, hi_faint = wilson_confidence_interval(pp.faints, pp.games)
            lo_ko, hi_ko = wilson_confidence_interval(pp.kos, pp.games)
            pokemon_stats[pname] = {
                "games": pp.games,
                "faints": pp.faints,
                "faint_rate": round(pp.faint_rate, 3),
                "faint_rate_ci_95": [round(lo_faint, 3), round(hi_faint, 3)],
                "kos": pp.kos,
                "ko_rate": round(pp.ko_rate, 3),
                "ko_rate_ci_95": [round(lo_ko, 3), round(hi_ko, 3)],
            }

        # Rating bracket records
        bracket_data = {}
        for blabel, br in ts.rating_brackets.items():
            lo, hi = wilson_confidence_interval(br.wins, br.games)
            bracket_data[blabel] = {
                "range": f"{br.bracket_min}-{br.bracket_max}",
                "games": br.games,
                "wins": br.wins,
                "losses": br.losses,
                "win_rate": round(br.win_rate, 3),
                "win_rate_ci_95": [round(lo, 3), round(hi, 3)],
            }

        # Overall CI
        wr_lo, wr_hi = wilson_confidence_interval(ts.wins, ts.games_played)

        team_entry: Dict[str, Any] = {
            "games_played": ts.games_played,
            "wins": ts.wins,
            "losses": ts.losses,
            "win_rate": round(ts.win_rate, 3),
            "win_rate_ci_95": [round(wr_lo, 3), round(wr_hi, 3)],
            "elo_delta": ts.elo_delta,
            "trend": ts.trend,
            "last_10": {
                "games": ts.last_10_games,
                "wins": ts.last_10_wins,
                "win_rate": round(ts.last_10_win_rate, 3),
            },
            "last_25": {
                "games": ts.last_25_games,
                "wins": ts.last_25_wins,
                "win_rate": round(ts.last_25_win_rate, 3),
            },
            "last_50": {
                "games": ts.last_50_games,
                "wins": ts.last_50_wins,
                "win_rate": round(ts.last_50_win_rate, 3),
            },
            "worst_matchups": [
                {
                    "opponent_pokemon": mu.pokemon,
                    "games": mu.games,
                    "wins": mu.wins,
                    "losses": mu.losses,
                    "loss_rate": round(mu.loss_rate, 3),
                    "loss_rate_ci_95": [
                        round(wilson_confidence_interval(mu.losses, mu.games)[0], 3),
                        round(wilson_confidence_interval(mu.losses, mu.games)[1], 3),
                    ],
                }
                for mu in worst_matchups
            ],
            "pokemon_performance": pokemon_stats,
            "rating_brackets": bracket_data,
        }
        report["teams"][tf] = team_entry

    return report


def format_human_summary(report: Dict[str, Any]) -> str:
    """Render the JSON report as a human-readable plaintext summary."""

    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("FOULER PLAY -- TEAM PERFORMANCE REPORT")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Total battles analysed: {report['total_battles']}")
    lines.append("=" * 72)

    for tf, td in report.get("teams", {}).items():
        lines.append("")
        lines.append("-" * 72)
        lines.append(f"TEAM: {tf}")
        lines.append("-" * 72)
        ci = td.get("win_rate_ci_95", [0, 1])
        lines.append(
            f"  Games: {td['games_played']}  |  "
            f"W/L: {td['wins']}/{td['losses']}  |  "
            f"Win Rate: {td['win_rate']:.1%} (95% CI: {ci[0]:.1%}-{ci[1]:.1%})"
        )
        lines.append(f"  ELO Delta: {td['elo_delta']:+d}  |  Trend: {td['trend']}")

        # Windowed rates
        for window_key, window_label in [("last_10", "Last 10"), ("last_25", "Last 25"), ("last_50", "Last 50")]:
            wd = td.get(window_key, {})
            if wd.get("games", 0) > 0:
                lines.append(
                    f"    {window_label}: {wd['wins']}W/{wd['games'] - wd['wins']}L "
                    f"({wd['win_rate']:.1%})"
                )

        # Worst matchups
        matchups = td.get("worst_matchups", [])
        if matchups:
            lines.append("")
            lines.append("  Worst Matchups:")
            for mu in matchups[:5]:
                ci = mu.get("loss_rate_ci_95", [0, 1])
                flag = " ***" if mu["loss_rate"] >= 0.70 and mu["games"] >= 3 else ""
                lines.append(
                    f"    vs {mu['opponent_pokemon']:20s}  "
                    f"{mu['losses']}L/{mu['games']}G  "
                    f"loss rate {mu['loss_rate']:.0%} "
                    f"(CI: {ci[0]:.0%}-{ci[1]:.0%}){flag}"
                )

        # Per-Pokemon performance
        pokemon = td.get("pokemon_performance", {})
        if pokemon:
            lines.append("")
            lines.append("  Per-Pokemon Performance:")
            # Sort by faint rate descending
            sorted_pkmn = sorted(
                pokemon.items(),
                key=lambda kv: kv[1].get("faint_rate", 0),
                reverse=True,
            )
            for pname, ps in sorted_pkmn:
                if ps.get("games", 0) == 0:
                    continue
                lines.append(
                    f"    {pname:20s}  "
                    f"games={ps['games']:3d}  "
                    f"faints={ps['faints']:3d} ({ps['faint_rate']:.0%})  "
                    f"KOs={ps['kos']:3d} ({ps['ko_rate']:.0%})"
                )

        # Rating brackets
        brackets = td.get("rating_brackets", {})
        if brackets:
            lines.append("")
            lines.append("  Win Rate by Opponent Rating:")
            for blabel, bd in sorted(brackets.items(), key=lambda kv: kv[1].get("range", "")):
                if bd.get("games", 0) == 0:
                    continue
                ci = bd.get("win_rate_ci_95", [0, 1])
                lines.append(
                    f"    {bd['range']:12s}  "
                    f"{bd['wins']}W/{bd['losses']}L  "
                    f"WR {bd['win_rate']:.0%} "
                    f"(CI: {ci[0]:.0%}-{ci[1]:.0%})"
                )

    # Recommendations
    recs = report.get("recommendations", {})
    if recs:
        lines.append("")
        lines.append("=" * 72)
        lines.append("RECOMMENDATIONS")
        lines.append("=" * 72)
        for tf, rec in recs.items():
            lines.append("")
            lines.append(f"  [{tf}]")
            if rec.get("weakness"):
                lines.append(f"    #1 Weakness: {rec['weakness']}")
                lines.append(f"    Detail:     {rec['detail']}")
                lines.append(f"    Suggestion: {rec['suggestion']}")
            else:
                lines.append(f"    {rec['detail']}")

    lines.append("")
    lines.append("=" * 72)
    lines.append("END OF REPORT")
    lines.append("=" * 72)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_team_report(
    *,
    write_json: bool = True,
    print_summary: bool = True,
) -> Dict[str, Any]:
    """
    End-to-end pipeline: load data, compute stats, write report, print
    summary.  Returns the JSON report dict.
    """
    print("[team_performance] Loading battle stats...")
    records = load_battle_stats()
    print(f"[team_performance] Loaded {len(records)} battle records.")

    print("[team_performance] Parsing replay files...")
    replay_extracts = load_all_replays()
    print(f"[team_performance] Parsed {len(replay_extracts)} replays.")

    print("[team_performance] Building per-team statistics...")
    teams = build_team_stats(records, replay_extracts)
    print(f"[team_performance] Found {len(teams)} distinct team(s).")

    recommendations = generate_recommendations(teams)
    report = build_json_report(teams, recommendations)

    if write_json:
        REPORT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_OUTPUT_PATH.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[team_performance] JSON report written to {REPORT_OUTPUT_PATH}")

    if print_summary:
        summary = format_human_summary(report)
        print()
        print(summary)

    return report


def main() -> None:
    """CLI entry point."""
    json_only = "--json" in sys.argv
    summary_only = "--summary" in sys.argv

    generate_team_report(
        write_json=not summary_only,
        print_summary=not json_only,
    )


if __name__ == "__main__":
    main()
