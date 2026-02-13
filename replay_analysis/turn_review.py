#!/usr/bin/env python3
"""
Turn review system.

Primary guarantee:
- Every replay review includes turn-by-turn coverage starting at turn 1.
- Lead context is always preserved so early-game pace decisions are reviewable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data import pokedex
from fp.helpers import type_effectiveness_modifier


DEFAULT_BOT_USERNAME = "ALL CHUNG"
SETUP_MOVES = {
    "calmmind",
    "curse",
    "dragondance",
    "nastyplot",
    "quiverdance",
    "swordsdance",
    "bulkup",
}
HAZARD_MOVES = {"stealthrock", "spikes", "stickyweb", "toxicspikes"}


@dataclass
class TurnSnapshot:
    """Represents one reviewed turn."""

    turn_number: int
    bot_active: str
    bot_hp_percent: float
    opp_active: str
    opp_hp_percent: float
    bot_choice: str
    bot_team_status: str
    opp_team_status: str
    field_conditions: List[str]
    why_critical: str
    replay_url: str
    alternative_options: List[Tuple[str, str]]
    lead_matchup: str = ""


class TurnReviewer:
    """Analyzes replay logs and builds complete turn reviews."""

    def __init__(self, bot_username: str = DEFAULT_BOT_USERNAME):
        self.bot_username = bot_username
        project_root = Path(__file__).parent.parent
        self.reviews_dir = project_root / "replay_analysis" / "turn_reviews"
        self.reviews_dir.mkdir(exist_ok=True)
        self._pokedex_lookup = self._build_pokedex_lookup()

    @staticmethod
    def _norm_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (name or "").lower())

    def _build_pokedex_lookup(self) -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        # Pass 1: direct keys and canonical display names.
        for key, entry in pokedex.items():
            names = {key, str(entry.get("name", ""))}
            for nm in names:
                n = self._norm_name(nm)
                if n:
                    lookup.setdefault(n, key)
        # Pass 2: base species aliases (do not override canonical mappings).
        for key, entry in pokedex.items():
            base_species = str(entry.get("baseSpecies", ""))
            n = self._norm_name(base_species)
            if n:
                lookup.setdefault(n, key)
        return lookup

    def _resolve_species_key(self, species: str) -> Optional[str]:
        if not species:
            return None
        n = self._norm_name(species)
        if not n:
            return None
        return self._pokedex_lookup.get(n, n if n in pokedex else None)

    @staticmethod
    def _format_eff(mult: float) -> str:
        if mult <= 0:
            return "immune"
        if mult >= 4:
            return "4x"
        if mult >= 2:
            return "2x"
        if mult <= 0.25:
            return "0.25x"
        if mult < 1:
            return "0.5x"
        return "1x"

    def _get_species_profile(self, species: str) -> Tuple[List[str], Optional[int], Optional[str]]:
        key = self._resolve_species_key(species)
        if not key or key not in pokedex:
            return [], None, None
        entry = pokedex[key]
        types = [t.lower() for t in entry.get("types", []) if isinstance(t, str)]
        base_stats = entry.get("baseStats", {}) or {}
        speed = base_stats.get("speed")
        try:
            speed_val = int(speed) if speed is not None else None
        except (TypeError, ValueError):
            speed_val = None
        return types, speed_val, key

    def _evaluate_lead_matchup(self, bot_lead: str, opp_lead: str) -> str:
        bot_types, bot_speed, bot_key = self._get_species_profile(bot_lead)
        opp_types, opp_speed, opp_key = self._get_species_profile(opp_lead)
        if not bot_types or not opp_types:
            return "Lead matchup: unknown (missing typing data)."

        bot_pressure = max(
            type_effectiveness_modifier(t, opp_types) for t in bot_types
        )
        opp_pressure = max(
            type_effectiveness_modifier(t, bot_types) for t in opp_types
        )

        score = 0.0
        reasons: List[str] = []

        if bot_pressure >= 2:
            score += 1.0
            reasons.append(f"our STAB pressure {self._format_eff(bot_pressure)}")
        elif bot_pressure < 1:
            score -= 0.6
            reasons.append(f"our STAB pressure only {self._format_eff(bot_pressure)}")
        else:
            reasons.append("our STAB pressure neutral")

        if opp_pressure >= 2:
            score -= 1.0
            reasons.append(f"opp threat {self._format_eff(opp_pressure)}")
        elif opp_pressure < 1:
            score += 0.6
            reasons.append(f"opp threat only {self._format_eff(opp_pressure)}")
        else:
            reasons.append("opp threat neutral")

        speed_note = "speed unknown"
        if bot_speed is not None and opp_speed is not None:
            if bot_speed > opp_speed:
                score += 0.5
                speed_note = f"speed edge ({bot_speed}>{opp_speed})"
            elif bot_speed < opp_speed:
                score -= 0.5
                speed_note = f"speed disadvantage ({bot_speed}<{opp_speed})"
            else:
                speed_note = f"speed tie ({bot_speed})"

        if score >= 1.0:
            verdict = "favorable"
        elif score <= -1.0:
            verdict = "unfavorable"
        else:
            verdict = "neutral"

        bot_name = pokedex.get(bot_key, {}).get("name", bot_lead) if bot_key else bot_lead
        opp_name = pokedex.get(opp_key, {}).get("name", opp_lead) if opp_key else opp_lead
        return (
            f"Lead matchup: {verdict} ({bot_name} vs {opp_name}; "
            f"{', '.join(reasons)}; {speed_note})."
        )

    @staticmethod
    def _extract_player_side_map(log_lines: List[str]) -> Dict[str, str]:
        side_map: Dict[str, str] = {}
        for line in log_lines:
            if not line.startswith("|player|"):
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                side_map[parts[2]] = parts[3]
        return side_map

    def _detect_bot_side(self, side_map: Dict[str, str]) -> str:
        target = self._norm_name(self.bot_username)
        for side, player_name in side_map.items():
            if self._norm_name(player_name) == target:
                return side
        # Safe fallback keeps parser functional even when account name changes.
        return "p1"

    @staticmethod
    def _parse_hp_percent(condition: str) -> Optional[float]:
        raw = (condition or "").strip().lower()
        if not raw:
            return None
        if "fnt" in raw:
            return 0.0

        pct_match = re.search(r"(\d+(?:\.\d+)?)%", raw)
        if pct_match:
            return max(0.0, min(100.0, float(pct_match.group(1))))

        frac_match = re.search(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", raw)
        if frac_match:
            num = float(frac_match.group(1))
            den = float(frac_match.group(2))
            if den <= 0:
                return None
            return max(0.0, min(100.0, 100.0 * num / den))
        return None

    @staticmethod
    def _split_showdown_line(line: str) -> List[str]:
        # Showdown lines start with '|', so first item is empty.
        return line.strip().split("|")

    @staticmethod
    def _extract_slot_side(slot_token: str) -> Optional[str]:
        # Examples:
        # "p1a: Ting-Lu", "p2a: Gliscor"
        head = (slot_token or "").split(":")[0].strip().lower()
        if head.startswith("p1"):
            return "p1"
        if head.startswith("p2"):
            return "p2"
        return None

    @staticmethod
    def _extract_species_token(species_token: str, fallback_slot: str = "") -> str:
        if species_token:
            return species_token.split(",")[0].strip()
        if ":" in fallback_slot:
            return fallback_slot.split(":", 1)[1].strip().split(",")[0]
        return "Unknown"

    @staticmethod
    def _build_alternatives(bot_choice: str) -> List[Tuple[str, str]]:
        move = (bot_choice or "").lower()
        if move.startswith("switch "):
            return [
                ("Stay in and attack", "Preserve board tempo"),
                ("Switch to a different teammate", "Find better defensive/offensive role"),
            ]
        if move in HAZARD_MOVES:
            return [
                ("Direct attack", "Contest immediate damage race"),
                ("Pivot move", "Gain safer initiative"),
            ]
        if move in SETUP_MOVES:
            return [
                ("Attack immediately", "Reduce setup risk"),
                ("Defensive pivot", "Stabilize before setup"),
            ]
        return [
            ("Alternative attack line", "Compare damage and speed control"),
            ("Pivot line", "Compare long-term positioning"),
        ]

    @staticmethod
    def _critical_reason(
        turn_number: int,
        bot_choice: str,
        turn_events: List[str],
    ) -> str:
        choice = (bot_choice or "").lower()
        events_joined = " ".join(turn_events).lower()

        if turn_number == 1:
            return "Lead turn (pace-setting): opener heavily influences the rest of the game."
        if "faint:bot" in events_joined:
            return "Bot Pokemon fainted: evaluate whether this was avoidable."
        if "faint:opp" in events_joined:
            return "Opponent fainted: evaluate conversion quality and follow-up."
        if choice.startswith("switch "):
            return "Switch turn: evaluate matchup pivot value."
        if choice in HAZARD_MOVES:
            return "Hazard decision turn: evaluate tempo vs chip tradeoff."
        if choice in SETUP_MOVES:
            return "Setup decision turn: evaluate greed vs board safety."
        return "Normal decision turn: evaluate move quality and sequencing."

    @staticmethod
    def _critical_score(turn: TurnSnapshot) -> int:
        reason = turn.why_critical.lower()
        if "lead turn" in reason:
            return 100
        if "fainted" in reason:
            return 90
        if "setup decision" in reason or "hazard decision" in reason:
            return 70
        if "switch turn" in reason:
            return 55
        return 40

    def extract_full_turns(self, replay_data: Dict, replay_url: str) -> List[TurnSnapshot]:
        """
        Parse replay and return a complete turn-by-turn review from turn 1 onward.
        """
        log_lines = replay_data.get("log", "").split("\n")
        if not log_lines:
            return []

        side_map = self._extract_player_side_map(log_lines)
        bot_side = self._detect_bot_side(side_map)
        opp_side = "p2" if bot_side == "p1" else "p1"

        current_turn = 0
        bot_active = "Unknown"
        opp_active = "Unknown"
        lead_bot = None
        lead_opp = None
        bot_hp = 100.0
        opp_hp = 100.0
        bot_alive = set()
        opp_alive = set()
        bot_hazards: List[str] = []
        opp_hazards: List[str] = []

        per_turn: Dict[int, Dict[str, object]] = {}

        def ensure_turn(turn_no: int) -> Dict[str, object]:
            if turn_no not in per_turn:
                field_snapshot = []
                if bot_hazards:
                    field_snapshot.append(f"bot_hazards={','.join(sorted(bot_hazards))}")
                if opp_hazards:
                    field_snapshot.append(f"opp_hazards={','.join(sorted(opp_hazards))}")
                per_turn[turn_no] = {
                    "bot_active": bot_active,
                    "opp_active": opp_active,
                    "bot_hp": bot_hp,
                    "opp_hp": opp_hp,
                    "bot_choice": "",
                    "opp_choice": "",
                    "events": [],
                    "field": field_snapshot,
                }
            return per_turn[turn_no]

        for raw_line in log_lines:
            line = raw_line.strip()
            if not line:
                continue

            parts = self._split_showdown_line(line)
            if len(parts) < 2:
                continue

            tag = parts[1]

            if tag == "turn" and len(parts) >= 3:
                try:
                    current_turn = int(parts[2])
                    ensure_turn(current_turn)
                except ValueError:
                    continue
                continue

            if tag in {"switch", "drag"} and len(parts) >= 5:
                slot = parts[2]
                side = self._extract_slot_side(slot)
                species = self._extract_species_token(parts[3], slot)
                hp_pct = self._parse_hp_percent(parts[4])
                if side == bot_side:
                    if lead_bot is None:
                        lead_bot = species
                    bot_active = species
                    bot_alive.add(species)
                    if hp_pct is not None:
                        bot_hp = hp_pct
                elif side == opp_side:
                    if lead_opp is None:
                        lead_opp = species
                    opp_active = species
                    opp_alive.add(species)
                    if hp_pct is not None:
                        opp_hp = hp_pct

                if current_turn >= 1:
                    td = ensure_turn(current_turn)
                    if side == bot_side and not td["bot_choice"]:
                        td["bot_choice"] = f"switch {species.lower()}"
                    elif side == opp_side and not td["opp_choice"]:
                        td["opp_choice"] = f"switch {species.lower()}"
                    td["events"].append(f"switch:{'bot' if side == bot_side else 'opp'}:{species}")
                continue

            if tag == "move" and len(parts) >= 4 and current_turn >= 1:
                slot = parts[2]
                side = self._extract_slot_side(slot)
                move = parts[3].strip().lower().replace(" ", "")
                td = ensure_turn(current_turn)
                if side == bot_side and not td["bot_choice"]:
                    td["bot_choice"] = move
                elif side == opp_side and not td["opp_choice"]:
                    td["opp_choice"] = move
                td["events"].append(f"move:{'bot' if side == bot_side else 'opp'}:{move}")
                continue

            if tag in {"-damage", "-heal"} and len(parts) >= 4:
                slot = parts[2]
                side = self._extract_slot_side(slot)
                hp_pct = self._parse_hp_percent(parts[3])
                if hp_pct is None:
                    continue
                if side == bot_side:
                    bot_hp = hp_pct
                elif side == opp_side:
                    opp_hp = hp_pct
                if current_turn >= 1:
                    td = ensure_turn(current_turn)
                    if side == bot_side:
                        td["bot_hp"] = bot_hp
                    elif side == opp_side:
                        td["opp_hp"] = opp_hp
                continue

            if tag == "faint" and len(parts) >= 3 and current_turn >= 1:
                slot = parts[2]
                side = self._extract_slot_side(slot)
                name = self._extract_species_token("", slot)
                td = ensure_turn(current_turn)
                if side == bot_side:
                    bot_hp = 0.0
                    td["bot_hp"] = 0.0
                    td["events"].append(f"faint:bot:{name}")
                    bot_alive.discard(name)
                elif side == opp_side:
                    opp_hp = 0.0
                    td["opp_hp"] = 0.0
                    td["events"].append(f"faint:opp:{name}")
                    opp_alive.discard(name)
                continue

            if tag == "-sidestart" and len(parts) >= 4:
                side_token = parts[2].strip().lower()
                cond_token = parts[3].strip().lower().replace("move: ", "")
                if cond_token in HAZARD_MOVES:
                    if side_token.startswith(bot_side):
                        if cond_token not in bot_hazards:
                            bot_hazards.append(cond_token)
                    elif side_token.startswith(opp_side):
                        if cond_token not in opp_hazards:
                            opp_hazards.append(cond_token)
                if current_turn >= 1:
                    td = ensure_turn(current_turn)
                    td["events"].append(f"sidestart:{side_token}:{cond_token}")
                    td["field"] = []
                    if bot_hazards:
                        td["field"].append(f"bot_hazards={','.join(sorted(bot_hazards))}")
                    if opp_hazards:
                        td["field"].append(f"opp_hazards={','.join(sorted(opp_hazards))}")
                continue

            if tag == "-sideend" and len(parts) >= 4:
                side_token = parts[2].strip().lower()
                cond_token = parts[3].strip().lower().replace("move: ", "")
                if side_token.startswith(bot_side) and cond_token in bot_hazards:
                    bot_hazards.remove(cond_token)
                elif side_token.startswith(opp_side) and cond_token in opp_hazards:
                    opp_hazards.remove(cond_token)
                if current_turn >= 1:
                    td = ensure_turn(current_turn)
                    td["events"].append(f"sideend:{side_token}:{cond_token}")
                    td["field"] = []
                    if bot_hazards:
                        td["field"].append(f"bot_hazards={','.join(sorted(bot_hazards))}")
                    if opp_hazards:
                        td["field"].append(f"opp_hazards={','.join(sorted(opp_hazards))}")
                continue

        lead_matchup = self._evaluate_lead_matchup(lead_bot or bot_active, lead_opp or opp_active)

        snapshots: List[TurnSnapshot] = []
        for turn_no in sorted(t for t in per_turn.keys() if t >= 1):
            td = per_turn[turn_no]
            field_conditions = list(td["field"])

            bot_choice = (td["bot_choice"] or "unknown").strip()
            opp_choice = (td["opp_choice"] or "unknown").strip()
            if turn_no == 1 and bot_choice == "unknown":
                bot_choice = "lead_no_action_captured"

            why = self._critical_reason(
                turn_no,
                bot_choice,
                td["events"],
            )
            if turn_no == 1:
                why = f"{why} {lead_matchup}"

            snapshot = TurnSnapshot(
                turn_number=turn_no,
                bot_active=str(td["bot_active"] or "Unknown"),
                bot_hp_percent=float(td["bot_hp"] or 0.0),
                opp_active=str(td["opp_active"] or "Unknown"),
                opp_hp_percent=float(td["opp_hp"] or 0.0),
                bot_choice=bot_choice,
                bot_team_status=f"alive_seen={max(len(bot_alive), 1)}",
                opp_team_status=f"alive_seen={max(len(opp_alive), 1)}",
                field_conditions=field_conditions,
                why_critical=f"{why} Opp action: {opp_choice}.",
                replay_url=replay_url,
                alternative_options=self._build_alternatives(bot_choice),
                lead_matchup=lead_matchup if turn_no == 1 else "",
            )
            snapshots.append(snapshot)

        return snapshots

    def extract_critical_turns(self, replay_data: Dict, replay_url: str) -> List[TurnSnapshot]:
        """
        Return up to 3 highest-impact turns, always including turn 1 when available.
        """
        full_turns = self.extract_full_turns(replay_data, replay_url)
        if not full_turns:
            return []

        by_turn = {t.turn_number: t for t in full_turns}
        selected: List[TurnSnapshot] = []

        if 1 in by_turn:
            selected.append(by_turn[1])

        rest = [t for t in full_turns if t.turn_number != 1]
        rest.sort(key=self._critical_score, reverse=True)
        for snap in rest:
            if len(selected) >= 3:
                break
            selected.append(snap)

        selected.sort(key=lambda s: s.turn_number)
        return selected

    def format_for_discord(self, turn: TurnSnapshot) -> str:
        """Format a single turn review for Discord."""
        msg = f"[Turn {turn.turn_number}] Replay: {turn.replay_url}\n\n"
        msg += "Board state:\n"
        msg += f"- Bot: {turn.bot_active} ({turn.bot_hp_percent:.0f}% HP)\n"
        msg += f"- Opp: {turn.opp_active} ({turn.opp_hp_percent:.0f}% HP)\n"
        if turn.lead_matchup:
            msg += f"- Lead matchup: {turn.lead_matchup}\n"
        if turn.field_conditions:
            msg += f"- Field: {', '.join(turn.field_conditions)}\n"
        msg += f"\nBot chose: {turn.bot_choice}\n"
        msg += f"Why this turn matters: {turn.why_critical}\n"
        msg += "\nAlternatives to review:\n"
        for option, reason in turn.alternative_options:
            msg += f"- {option} ({reason})\n"
        return msg.strip()

    def format_full_review(self, replay_data: Dict, replay_url: str) -> str:
        """
        Produce a compact full-battle review string from turn 1 onward.
        """
        turns = self.extract_full_turns(replay_data, replay_url)
        if not turns:
            return f"No turn data parsed for {replay_url}"

        lines = [f"Full battle review from Turn 1: {replay_url}"]
        for t in turns:
            lines.append(
                f"T{t.turn_number}: {t.bot_active} ({t.bot_hp_percent:.0f}%) -> {t.bot_choice}; "
                f"{t.why_critical}"
            )
        return "\n".join(lines)

    def save_turn_review(self, turn: TurnSnapshot):
        """Persist a turn review JSON blob for later feedback loops."""
        replay_id = turn.replay_url.rstrip("/").split("/")[-1]
        filename = f"turn_{turn.turn_number}_{replay_id}.json"
        filepath = self.reviews_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            payload = asdict(turn)
            payload["feedback"] = None
            json.dump(payload, f, indent=2)

    def analyze_and_post(self, replay_data: Dict, replay_url: str) -> List[str]:
        """
        Keep legacy behavior for Discord posting:
        returns formatted messages for key turns, with turn 1 guaranteed.
        """
        critical_turns = self.extract_critical_turns(replay_data, replay_url)
        messages = []
        for turn in critical_turns:
            self.save_turn_review(turn)
            messages.append(self.format_for_discord(turn))
        return messages


def _fetch_replay_json(replay_url: str) -> Dict:
    import requests

    replay_id = replay_url.rstrip("/").split("/")[-1]
    url = f"https://replay.pokemonshowdown.com/{replay_id}.json"
    resp = requests.get(url, timeout=12)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Review replay turns from turn 1 onward")
    parser.add_argument("--replay-url", required=True, help="Showdown replay URL")
    parser.add_argument(
        "--mode",
        choices=["critical", "full"],
        default="full",
        help="critical: top 3 turns incl. turn 1; full: every turn from turn 1",
    )
    parser.add_argument(
        "--bot-name",
        default=DEFAULT_BOT_USERNAME,
        help="Bot account name used for side detection",
    )
    args = parser.parse_args()

    reviewer = TurnReviewer(bot_username=args.bot_name)
    replay_json = _fetch_replay_json(args.replay_url)

    if args.mode == "critical":
        critical = reviewer.extract_critical_turns(replay_json, args.replay_url)
        for snap in critical:
            print(reviewer.format_for_discord(snap))
            print("\n" + "-" * 80 + "\n")
    else:
        print(reviewer.format_full_review(replay_json, args.replay_url))
