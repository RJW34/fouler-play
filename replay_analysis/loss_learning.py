#!/usr/bin/env python3
"""Mechanics-backed loss learning for local Showdown replay artifacts.

This module is intentionally deterministic. It parses battle logs into evidence,
then validates any lesson or claim against local data or explicit battle-log
proof. If the local repo cannot back a claim, the claim stays unknown or rejected.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from replay_analysis.account_identity import resolve_bot_username

from data import all_move_json, pokedex
from fp.helpers import type_effectiveness_modifier

HAZARD_NAMES = {
    "stealthrock": "Stealth Rock",
    "spikes": "Spikes",
    "toxicspikes": "Toxic Spikes",
    "stickyweb": "Sticky Web",
}
HAZARD_DAMAGE_FROM = {
    "stealthrock": "Stealth Rock",
    "spikes": "Spikes",
    "toxicspikes": "Toxic Spikes",
}
GROUNDING_SOURCES = {
    "pokemon_data": "data/pokedex.json (Pokemon Showdown-derived via data/scripts/update_pokedex.py)",
    "move_data": "data/moves.json (Pokemon Showdown-derived via data/scripts/update_moves.py)",
    "type_chart": "fp.helpers.DAMAGE_MULTIPICATION_ARRAY",
    "usage_sets": "data/pkmn_sets_cache/<format>/showdown_sets.json and replay_moves.json",
    "battle_log": "local Pokemon Showdown replay/log lines",
}


def normalize_id(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def display_species(slot_or_species: str) -> str:
    text = (slot_or_species or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1].strip()
    return text.split(",")[0].strip()


def slot_id(slot_token: str) -> str:
    """Position slot id from a ``|move|`` / ``|-damage|`` actor token.

    ``p2a: Tesla`` -> ``p2a``. This is the position slot, NOT the side.
    """
    return (slot_token or "").split(":", 1)[0].strip().lower()


class NicknameResolver:
    """Resolve a battle-log actor token to its real SPECIES, not its nickname.

    Showdown ``|switch|`` / ``|drag|`` / ``|replace|`` lines are the only lines that
    carry BOTH the slot+nickname (``p2a: Tesla``) and the real species
    (``Iron Valiant, F``). Every later ``|move|`` / ``|-damage|`` / ``|faint|`` line
    carries ONLY ``p2a: Tesla`` (the nickname). Keying learning data off the nickname
    (the prior behavior of ``display_species``) produces garbage keys like
    ``tesla``/``jackblack`` that the live lookup (species ``Pokemon.name``) can never
    match. This resolver tracks the species currently occupying each slot so callers
    can recover the true species.
    """

    def __init__(self) -> None:
        # slot id (e.g. "p2a") -> current real species display name
        self._slot_to_species: dict[str, str] = {}
        # (slot id, normalized nickname) -> real species, persists after switch-out
        self._nick_to_species: dict[tuple[str, str], str] = {}

    def note_switch(self, slot_token: str, species_token: str) -> None:
        slot = slot_id(slot_token)
        nick = display_species(slot_token)
        species = display_species(species_token)
        if not slot or not species:
            return
        self._slot_to_species[slot] = species
        if nick:
            self._nick_to_species[(slot, normalize_id(nick))] = species

    def resolve(self, actor_token: str) -> str:
        """Return the real species for a ``|move|``/``|-damage|`` actor token.

        Falls back to the nickname only if the slot was never seen in a switch/drag
        (should not happen for well-formed logs, but stays non-fatal).
        """
        slot = slot_id(actor_token)
        nick = display_species(actor_token)
        by_nick = self._nick_to_species.get((slot, normalize_id(nick)))
        if by_nick:
            return by_nick
        by_slot = self._slot_to_species.get(slot)
        if by_slot:
            return by_slot
        return nick


def slot_side(slot: str) -> str:
    head = (slot or "").split(":", 1)[0].lower()
    if head.startswith("p1"):
        return "p1"
    if head.startswith("p2"):
        return "p2"
    return ""


def split_line(line: str) -> list[str]:
    return (line or "").strip().split("|")


@dataclass
class ClaimValidation:
    status: str
    claim: dict[str, Any]
    reason: str
    sources: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)


# Loss-termination taxonomy. INFRA losses are caused by latency/timeouts/network,
# NOT by piloting/engine decisions, so they MUST be excluded from the engine/
# piloting learning corpus (a high infra-loss rate means "fix latency", not "fix
# the engine"). PILOTING losses are real played-out games and are the ONLY losses
# that should inform decision/engine improvement.
INFRA_TERMINATIONS = frozenset({"inactivity", "timeout", "disconnect", "crash", "forfeit"})
PILOTING_TERMINATIONS = frozenset({"played_out"})


def classify_loss_termination(
    *,
    result: str,
    bot_name: str,
    winner: str,
    log_lines: list[str],
    bot_move_count: int,
    total_turns: int,
) -> tuple[str, str, str]:
    """Classify HOW a battle ended, to separate infra/latency losses from real
    played-out (piloting) losses.

    Returns (termination, loss_class, evidence_text):
      termination : inactivity|timeout|disconnect|crash|forfeit|played_out|unknown
      loss_class  : "infra" (exclude from engine learning) | "piloting" (keep) | ""
      evidence    : the protocol line / heuristic that drove the classification

    Detection is grounded in the Showdown protocol the bot actually receives
    (verified on live replays 2026-06-24), in priority order:
      1. Explicit "lost due to inactivity" / "<bot> forfeited." server messages.
      2. Inactivity-timer depletion against the bot ("<bot> has 0 seconds left").
      3. Disconnect markers in the log.
      4. Heuristic: our move-count is abnormally low for the turn count (we stopped
         submitting moves => almost certainly a latency/timeout death), even if the
         server's terminal message was not captured in the saved log.
    Anything else that reached a normal |win| is treated as a played-out loss.
    """
    bot_id = normalize_id(bot_name)
    lowered = "\n".join(log_lines).lower()

    def _mentions_bot(line: str) -> bool:
        # Match the bot by display name appearing in the message text.
        return bool(bot_name) and bot_name.lower() in line.lower()

    if result != "loss":
        return "played_out", "", ""

    # 1 + 2: explicit inactivity / forfeit / timeout messages against the bot.
    for line in log_lines:
        ll = line.lower()
        if "|-message|" in ll or "|inactive|" in ll:
            if "lost due to inactivity" in ll and _mentions_bot(line):
                return "inactivity", "infra", line.strip()
            if "forfeited" in ll and _mentions_bot(line):
                return "forfeit", "infra", line.strip()
            # "<bot> has 0 seconds left." == our cumulative clock hit zero.
            m = re.search(r"has\s+0\s+seconds?\s+left", ll)
            if m and _mentions_bot(line):
                return "timeout", "infra", line.strip()

    # Generic inactivity/forfeit anywhere (winner-side phrasing varies); only
    # attribute to infra when the bot is the loser (it is, result == loss).
    if "lost due to inactivity" in lowered:
        return "inactivity", "infra", "lost due to inactivity"
    if re.search(r"\bforfeited\b", lowered) and not _winner_is_bot(winner, bot_name):
        # A forfeit recorded as our loss is an infra/operational loss.
        return "forfeit", "infra", "forfeited"

    # 3: disconnect markers.
    if "disconnect" in lowered or "connection" in lowered and "lost" in lowered:
        return "disconnect", "infra", "disconnect marker in log"

    # 4: heuristic move-count check. In a genuine played-out gen9ou loss the bot
    # submits ~1 action per turn. If we made far fewer moves than turns elapsed,
    # we stopped responding (latency/timeout death) regardless of the captured
    # terminal message. Require a minimum game length so very short stomps
    # (legitimate fast losses) are not misflagged.
    if total_turns >= 6 and bot_move_count <= max(2, total_turns * 0.4):
        return (
            "inactivity",
            "infra",
            f"low move-count: {bot_move_count} moves over {total_turns} turns",
        )

    return "played_out", "piloting", ""


def _winner_is_bot(winner: str, bot_name: str) -> bool:
    return bool(winner) and normalize_id(winner) == normalize_id(bot_name)


@dataclass
class LossEvidence:
    replay_id: str
    format: str
    bot_username: str
    bot_side: str
    result: str
    winner: str
    players: dict[str, str]
    teams: dict[str, list[str]]
    revealed_sets: dict[str, dict[str, Any]]
    faint_turns: list[dict[str, Any]]
    key_kos: list[dict[str, Any]]
    hazards: list[dict[str, Any]]
    weather: list[dict[str, Any]]
    terrain: list[dict[str, Any]]
    statuses: list[dict[str, Any]]
    speed_order_clues: list[dict[str, Any]]
    decisive_turns: list[dict[str, Any]]
    unresolved_unknowns: list[dict[str, Any]]
    mechanics_claims: list[dict[str, Any]]
    # Termination classification (FIX 2, 2026-06-24). Defaulted fields MUST come
    # after the required ones above.
    #   termination: inactivity|timeout|disconnect|crash|forfeit|played_out|unknown
    #   loss_class:  "infra" (exclude from engine learning) | "piloting" (keep) | "" (non-loss)
    #   is_infra_loss: convenience bool == (result == loss and loss_class == infra)
    termination: str = "unknown"
    loss_class: str = ""
    is_infra_loss: bool = False
    termination_evidence: str = ""
    source_contract: dict[str, str] = field(default_factory=lambda: GROUNDING_SOURCES.copy())


class LocalMechanics:
    """Local-only Pokemon mechanics and evidence validator."""

    def __init__(self, project_root: Path = PROJECT_ROOT, format_id: str = "gen9ou"):
        self.project_root = project_root
        self.format_id = normalize_id(format_id) or "gen9ou"
        self._sets = self._load_json(project_root / "data" / "pkmn_sets_cache" / self.format_id / "showdown_sets.json")
        self._replay_moves = self._load_json(project_root / "data" / "pkmn_sets_cache" / self.format_id / "replay_moves.json")

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def pokemon(self, species: str) -> dict[str, Any] | None:
        return pokedex.get(normalize_id(species))

    def move(self, move: str) -> dict[str, Any] | None:
        return all_move_json.get(normalize_id(move))

    def move_type(self, move: str) -> str | None:
        data = self.move(move)
        if not data:
            return None
        return str(data.get("type", "")).lower() or None

    def types(self, species: str) -> list[str]:
        data = self.pokemon(species) or {}
        return [str(t).lower() for t in data.get("types", [])]

    def abilities(self, species: str) -> set[str]:
        data = self.pokemon(species) or {}
        abilities = data.get("abilities", {}) or {}
        return {normalize_id(v) for v in abilities.values() if v}

    def common_moves(self, species: str) -> set[str]:
        species_id = normalize_id(species)
        moves: set[str] = set()

        dex_sets = self._sets.get("dex", {}) if isinstance(self._sets, dict) else {}
        for display_name, sets in dex_sets.items():
            if normalize_id(display_name) != species_id or not isinstance(sets, dict):
                continue
            for set_data in sets.values():
                for move in set_data.get("moves", []) if isinstance(set_data, dict) else []:
                    moves.add(normalize_id(move))

        replay_entry = self._replay_moves.get(species_id)
        if isinstance(replay_entry, dict):
            for combo in replay_entry:
                moves.update(normalize_id(m) for m in combo.split("|") if m)
        return moves

    def type_multiplier(self, move: str, target_species: str) -> float | None:
        move_type = self.move_type(move)
        target_types = self.types(target_species)
        if not move_type or not target_types:
            return None
        try:
            return float(type_effectiveness_modifier(move_type, target_types))
        except KeyError:
            return None

    @staticmethod
    def effectiveness_bucket(multiplier: float) -> str:
        if multiplier == 0:
            return "immune"
        if multiplier > 1:
            return "super_effective"
        if multiplier < 1:
            return "resisted"
        return "neutral"

    def validate_claim(self, claim: dict[str, Any], evidence: LossEvidence | None = None) -> ClaimValidation:
        kind = str(claim.get("kind", "")).strip().lower()
        if kind == "type_effectiveness":
            return self._validate_type_effectiveness(claim)
        if kind == "immunity":
            expected = dict(claim)
            expected["expected"] = "immune" if claim.get("expected", True) else "not_immune"
            return self._validate_type_effectiveness(expected)
        if kind == "legal_move":
            return self._validate_legal_move(claim, evidence)
        if kind == "ability":
            return self._validate_ability(claim, evidence)
        if kind == "speed_order":
            return self._validate_speed_order(claim, evidence)
        if kind == "damage":
            return self._validate_damage(claim, evidence)
        return ClaimValidation("unknown", claim, f"unsupported claim kind: {kind}")

    def _validate_type_effectiveness(self, claim: dict[str, Any]) -> ClaimValidation:
        move = str(claim.get("move", ""))
        target = str(claim.get("target", ""))
        expected = str(claim.get("expected", "")).lower()
        multiplier = self.type_multiplier(move, target)
        if multiplier is None:
            return ClaimValidation(
                "unknown",
                claim,
                "missing local move or target typing data",
                [GROUNDING_SOURCES["move_data"], GROUNDING_SOURCES["pokemon_data"]],
            )
        actual = self.effectiveness_bucket(multiplier)
        sources = [GROUNDING_SOURCES["move_data"], GROUNDING_SOURCES["pokemon_data"], GROUNDING_SOURCES["type_chart"]]
        if not expected:
            return ClaimValidation(
                "source_backed",
                {**claim, "actual": actual, "multiplier": multiplier},
                "computed from local move type, target typing, and type chart",
                sources,
            )
        if expected == actual or (expected == "not_immune" and actual != "immune"):
            return ClaimValidation(
                "source_backed",
                {**claim, "actual": actual, "multiplier": multiplier},
                "claim matches local type chart",
                sources,
            )
        return ClaimValidation(
            "rejected",
            {**claim, "actual": actual, "multiplier": multiplier},
            "claim contradicts local type chart",
            sources,
        )

    def _validate_legal_move(self, claim: dict[str, Any], evidence: LossEvidence | None) -> ClaimValidation:
        species = str(claim.get("pokemon", ""))
        move = str(claim.get("move", ""))
        move_id = normalize_id(move)
        if not self.move(move):
            return ClaimValidation("rejected", claim, "move is absent from local Pokemon Showdown move data", [GROUNDING_SOURCES["move_data"]])

        if evidence:
            observed = evidence.revealed_sets.get(normalize_id(species), {}).get("moves", [])
            if move_id in {normalize_id(m) for m in observed}:
                return ClaimValidation("source_backed", claim, "move was observed in the battle log", [GROUNDING_SOURCES["battle_log"]])

        common = self.common_moves(species)
        if move_id in common:
            return ClaimValidation("source_backed", claim, "move appears in local format usage/set data", [GROUNDING_SOURCES["usage_sets"]])
        return ClaimValidation(
            "unknown",
            claim,
            "move exists, but no local learnset proof or battle-log usage supports this Pokemon using it",
            [GROUNDING_SOURCES["move_data"], GROUNDING_SOURCES["usage_sets"]],
        )

    def _validate_ability(self, claim: dict[str, Any], evidence: LossEvidence | None) -> ClaimValidation:
        species = str(claim.get("pokemon", ""))
        ability = str(claim.get("ability", ""))
        ability_id = normalize_id(ability)
        if evidence:
            observed = evidence.revealed_sets.get(normalize_id(species), {}).get("ability")
            if observed and normalize_id(observed) == ability_id:
                return ClaimValidation("source_backed", claim, "ability was observed in the battle log", [GROUNDING_SOURCES["battle_log"]])

        allowed = self.abilities(species)
        if not allowed:
            return ClaimValidation("unknown", claim, "missing local Pokemon ability data", [GROUNDING_SOURCES["pokemon_data"]])
        if ability_id in allowed:
            return ClaimValidation("source_backed", claim, "ability is listed for this Pokemon in local pokedex data", [GROUNDING_SOURCES["pokemon_data"]])
        return ClaimValidation("rejected", claim, "ability is not listed for this Pokemon in local pokedex data", [GROUNDING_SOURCES["pokemon_data"]])

    def _validate_speed_order(self, claim: dict[str, Any], evidence: LossEvidence | None) -> ClaimValidation:
        if not evidence:
            return ClaimValidation("unknown", claim, "speed order needs battle-log evidence")
        turn = claim.get("turn")
        faster = normalize_id(claim.get("faster"))
        slower = normalize_id(claim.get("slower"))
        for clue in evidence.speed_order_clues:
            if turn is not None and clue.get("turn") != turn:
                continue
            first = normalize_id(clue.get("first"))
            second = normalize_id(clue.get("second"))
            if first == faster and second == slower:
                return ClaimValidation("source_backed", claim, "move order was observed in this turn", [GROUNDING_SOURCES["battle_log"]], [clue])
            if first == slower and second == faster:
                return ClaimValidation("rejected", claim, "battle-log move order contradicts this speed claim", [GROUNDING_SOURCES["battle_log"]], [clue])
        return ClaimValidation("unknown", claim, "no same-turn move-order clue supports this speed claim", [GROUNDING_SOURCES["battle_log"]])

    def _validate_damage(self, claim: dict[str, Any], evidence: LossEvidence | None) -> ClaimValidation:
        if not evidence:
            return ClaimValidation("unknown", claim, "damage claims require observed battle-log damage or a fully specified calc")
        move = normalize_id(claim.get("move"))
        target = normalize_id(claim.get("target"))
        turn = claim.get("turn")
        observed = []
        for ko in evidence.key_kos:
            if move and normalize_id(ko.get("move")) != move:
                continue
            if target and normalize_id(ko.get("target")) != target:
                continue
            if turn is not None and ko.get("turn") != turn:
                continue
            observed.append(ko)
        if observed:
            return ClaimValidation("source_backed", claim, "damage/faint outcome was observed in the battle log", [GROUNDING_SOURCES["battle_log"]], observed)
        return ClaimValidation(
            "unknown",
            claim,
            "no complete local damage range is available for unobserved sets; keeping claim unknown",
            [GROUNDING_SOURCES["battle_log"]],
        )


class LossLogIngestor:
    def __init__(self, bot_username: str | None = None):
        self.bot_username = bot_username or resolve_bot_username()

    def ingest(self, replay_data: dict[str, Any], team_file: str | None = None) -> LossEvidence:
        log_lines = str(replay_data.get("log", "")).splitlines()
        players: dict[str, str] = {}
        teams: dict[str, list[str]] = {"p1": [], "p2": []}
        active_slots: dict[str, str] = {}
        revealed: dict[str, dict[str, Any]] = defaultdict(lambda: {"moves": [], "items": [], "ability": None, "status": None})
        faint_turns: list[dict[str, Any]] = []
        key_kos: list[dict[str, Any]] = []
        hazards: list[dict[str, Any]] = []
        weather: list[dict[str, Any]] = []
        terrain: list[dict[str, Any]] = []
        statuses: list[dict[str, Any]] = []
        speed_clues: list[dict[str, Any]] = []
        move_orders: dict[int, list[dict[str, Any]]] = defaultdict(list)
        damage_events: list[dict[str, Any]] = []

        current_turn = 0
        winner = ""
        last_move_for_target_slot: dict[str, dict[str, Any]] = {}
        last_damage_for_slot: dict[str, dict[str, Any]] = {}
        # Resolve actor/target tokens (p2a: Tesla) to real species (Iron Valiant)
        # so key_kos.attacker / problem_pokemon keys match the live species lookup.
        resolver = NicknameResolver()

        for raw_line in log_lines:
            parts = split_line(raw_line)
            if len(parts) < 2:
                continue
            tag = parts[1]
            if tag == "player" and len(parts) >= 4:
                players[parts[2]] = parts[3]
            elif tag == "poke" and len(parts) >= 4:
                side = parts[2]
                if side in teams:
                    teams[side].append(display_species(parts[3]))
            elif tag == "turn" and len(parts) >= 3:
                current_turn = int(parts[2])
            elif tag in {"switch", "drag", "replace"} and len(parts) >= 4:
                side = slot_side(parts[2])
                species = display_species(parts[3])
                resolver.note_switch(parts[2], parts[3])
                if side:
                    active_slots[side] = species
                    revealed[normalize_id(species)]
            elif tag == "move" and len(parts) >= 4:
                actor_side = slot_side(parts[2])
                actor = resolver.resolve(parts[2])
                move = parts[3]
                target_side = slot_side(parts[4]) if len(parts) >= 5 else ""
                target = resolver.resolve(parts[4]) if len(parts) >= 5 else ""
                entry = {
                    "turn": current_turn,
                    "side": actor_side,
                    "pokemon": actor,
                    "move": move,
                    "target_side": target_side,
                    "target": target,
                }
                if move not in revealed[normalize_id(actor)]["moves"]:
                    revealed[normalize_id(actor)]["moves"].append(move)
                move_orders[current_turn].append(entry)
                if target:
                    last_move_for_target_slot[parts[4].split(":", 1)[0].strip()] = entry
            elif tag in {"-supereffective", "-resisted", "-immune"} and len(parts) >= 3:
                slot = parts[2].split(":", 1)[0].strip()
                last = last_move_for_target_slot.get(slot)
                if last:
                    last.setdefault("effectiveness_log", []).append(tag[1:])
            elif tag == "-damage" and len(parts) >= 4:
                target = resolver.resolve(parts[2])
                target_side = slot_side(parts[2])
                source = self._from_clause(parts[4:])
                event = {
                    "turn": current_turn,
                    "target": target,
                    "target_side": target_side,
                    "hp": parts[3],
                    "source": source,
                }
                slot = parts[2].split(":", 1)[0].strip()
                move = last_move_for_target_slot.get(slot)
                if move and not source:
                    event["move"] = move.get("move")
                    event["attacker"] = move.get("pokemon")
                    event["attacker_side"] = move.get("side")
                    event["effectiveness_log"] = move.get("effectiveness_log", [])
                damage_events.append(event)
                last_damage_for_slot[slot] = event
            elif tag == "faint" and len(parts) >= 3:
                target = resolver.resolve(parts[2])
                target_side = slot_side(parts[2])
                slot = parts[2].split(":", 1)[0].strip()
                faint = {"turn": current_turn, "pokemon": target, "side": target_side}
                faint_turns.append(faint)
                damage = last_damage_for_slot.get(slot)
                if damage:
                    key_kos.append({**damage, "turn": current_turn})
            elif tag == "-sidestart" and len(parts) >= 4:
                hazard_id = normalize_id(parts[3].replace("move:", ""))
                if hazard_id in HAZARD_NAMES:
                    hazards.append({"turn": current_turn, "side": slot_side(parts[2]) or parts[2].split(":", 1)[0], "hazard": HAZARD_NAMES[hazard_id]})
            elif tag == "-weather" and len(parts) >= 3:
                weather.append({"turn": current_turn, "weather": parts[2], "source": self._from_clause(parts[3:])})
            elif tag in {"-fieldstart", "-fieldend"} and len(parts) >= 3:
                terrain.append({"turn": current_turn, "event": tag[1:], "condition": parts[2]})
            elif tag == "-status" and len(parts) >= 4:
                pokemon = resolver.resolve(parts[2])
                status = {"turn": current_turn, "pokemon": pokemon, "side": slot_side(parts[2]), "status": parts[3], "source": self._from_clause(parts[4:])}
                statuses.append(status)
                revealed[normalize_id(pokemon)]["status"] = parts[3]
            elif tag == "-ability" and len(parts) >= 4:
                pokemon = resolver.resolve(parts[2])
                revealed[normalize_id(pokemon)]["ability"] = parts[3]
            elif tag in {"-item", "-enditem"} and len(parts) >= 4:
                pokemon = resolver.resolve(parts[2])
                item = parts[3]
                if item not in revealed[normalize_id(pokemon)]["items"]:
                    revealed[normalize_id(pokemon)]["items"].append(item)
            elif tag == "win" and len(parts) >= 3:
                winner = parts[2]

        for turn, moves in move_orders.items():
            for idx in range(len(moves) - 1):
                first = moves[idx]
                second = moves[idx + 1]
                if first.get("side") and second.get("side") and first.get("side") != second.get("side"):
                    speed_clues.append(
                        {
                            "turn": turn,
                            "first": first["pokemon"],
                            "second": second["pokemon"],
                            "basis": "same-turn move order",
                            "first_move": first["move"],
                            "second_move": second["move"],
                        }
                    )

        bot_side = self._detect_bot_side(players)
        result = "win" if winner and normalize_id(winner) == normalize_id(players.get(bot_side, "")) else "loss"
        decisive_turns = self._decisive_turns(bot_side, faint_turns, key_kos, max_turn=current_turn)
        unknowns = self._unknowns(teams, revealed)
        mechanics = LocalMechanics(format_id=str(replay_data.get("formatid") or replay_data.get("format") or "gen9ou"))
        claims = self._derive_claims(mechanics, bot_side, key_kos, damage_events, hazards)

        # --- Termination classification (FIX 2): separate infra/latency losses
        # (inactivity/timeout/disconnect/forfeit) from real played-out piloting
        # losses so only the latter feed engine/piloting improvement. ---
        bot_move_count = 0
        for _turn, _moves in move_orders.items():
            for _entry in _moves:
                if _entry.get("side") == bot_side:
                    bot_move_count += 1
        bot_display_name = players.get(bot_side, "") or self.bot_username
        termination, loss_class, term_evidence = classify_loss_termination(
            result=result,
            bot_name=bot_display_name,
            winner=winner,
            log_lines=log_lines,
            bot_move_count=bot_move_count,
            total_turns=current_turn,
        )
        is_infra_loss = result == "loss" and loss_class == "infra"

        if team_file:
            revealed["__team_file__"] = {"path": team_file, "moves": [], "items": [], "ability": None, "status": None}

        return LossEvidence(
            replay_id=str(replay_data.get("id", "")),
            format=str(replay_data.get("format") or replay_data.get("formatid") or ""),
            bot_username=self.bot_username,
            bot_side=bot_side,
            result=result,
            winner=winner,
            termination=termination,
            loss_class=loss_class,
            is_infra_loss=is_infra_loss,
            termination_evidence=term_evidence,
            players=players,
            teams=teams,
            revealed_sets=dict(revealed),
            faint_turns=faint_turns,
            key_kos=key_kos,
            hazards=hazards,
            weather=weather,
            terrain=terrain,
            statuses=statuses,
            speed_order_clues=speed_clues,
            decisive_turns=decisive_turns,
            unresolved_unknowns=unknowns,
            mechanics_claims=[asdict(c) for c in claims],
        )

    @staticmethod
    def _from_clause(parts: Iterable[str]) -> str:
        for part in parts:
            if str(part).startswith("[from]"):
                return str(part).replace("[from]", "").strip()
        return ""

    def _detect_bot_side(self, players: dict[str, str]) -> str:
        wanted = normalize_id(self.bot_username)
        for side, player in players.items():
            if normalize_id(player) == wanted:
                return side
        return "p1"

    @staticmethod
    def _decisive_turns(bot_side: str, faint_turns: list[dict[str, Any]], key_kos: list[dict[str, Any]], max_turn: int) -> list[dict[str, Any]]:
        bot_faints = [f for f in faint_turns if f.get("side") == bot_side]
        decisive = []
        for faint in bot_faints[-3:]:
            decisive.append({"turn": faint["turn"], "event": "bot_faint", "pokemon": faint["pokemon"]})
        if not decisive and max_turn:
            decisive.append({"turn": max_turn, "event": "final_turn"})
        ko_turns = {k.get("turn") for k in key_kos if k.get("turn") in {d.get("turn") for d in decisive}}
        for ko in key_kos:
            if ko.get("turn") in ko_turns:
                decisive.append({"turn": ko.get("turn"), "event": "ko_context", "detail": ko})
        return decisive

    @staticmethod
    def _unknowns(teams: dict[str, list[str]], revealed: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        unknowns: list[dict[str, Any]] = []
        for side, species_list in teams.items():
            for species in species_list:
                data = revealed.get(normalize_id(species), {})
                moves = data.get("moves", [])
                if len(moves) < 4:
                    unknowns.append({"kind": "unrevealed_moves", "side": side, "pokemon": species, "revealed_count": len(moves)})
                if not data.get("ability"):
                    unknowns.append({"kind": "unrevealed_ability", "side": side, "pokemon": species})
                if not data.get("items"):
                    unknowns.append({"kind": "unrevealed_item", "side": side, "pokemon": species})
        return unknowns

    @staticmethod
    def _derive_claims(
        mechanics: LocalMechanics,
        bot_side: str,
        key_kos: list[dict[str, Any]],
        damage_events: list[dict[str, Any]],
        hazards: list[dict[str, Any]],
    ) -> list[ClaimValidation]:
        claims: list[ClaimValidation] = []
        for ko in key_kos:
            move = ko.get("move")
            target = ko.get("target")
            if move and target:
                validation = mechanics.validate_claim({"kind": "type_effectiveness", "move": move, "target": target})
                validation.evidence.append({"turn": ko.get("turn"), "target": target, "move": move, "hp": ko.get("hp")})
                claims.append(validation)
        for event in damage_events:
            source = normalize_id(event.get("source"))
            if source in HAZARD_DAMAGE_FROM and event.get("target_side") == bot_side:
                claims.append(
                    ClaimValidation(
                        "source_backed",
                        {"kind": "hazard_damage", "hazard": HAZARD_DAMAGE_FROM[source], "target": event.get("target")},
                        "hazard damage was observed on the bot side in the battle log",
                        [GROUNDING_SOURCES["battle_log"]],
                        [event],
                    )
                )
        bot_hazards = [h for h in hazards if h.get("side") == bot_side]
        for hazard in bot_hazards:
            claims.append(
                ClaimValidation(
                    "source_backed",
                    {"kind": "hazard_pressure", "hazard": hazard.get("hazard"), "side": bot_side},
                    "hazard was set on the bot side in the battle log",
                    [GROUNDING_SOURCES["battle_log"]],
                    [hazard],
                )
            )
        return claims


def build_loss_artifact(
    replay_data: dict[str, Any],
    bot_username: str | None = None,
    team_file: str | None = None,
) -> dict[str, Any]:
    ingestor = LossLogIngestor(bot_username=bot_username)
    return asdict(ingestor.ingest(replay_data, team_file=team_file))


def aggregate_loss_lessons(artifacts: list[dict[str, Any]], min_repeats: int = 2) -> dict[str, Any]:
    """Build conservative team guidance from one or more loss artifacts."""
    counters: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    for artifact in artifacts:
        replay_id = artifact.get("replay_id", "")
        for wrapped in artifact.get("mechanics_claims", []):
            status = wrapped.get("status")
            claim = wrapped.get("claim", {})
            if status == "rejected":
                rejected.append({"replay_id": replay_id, "claim": claim, "reason": wrapped.get("reason")})
                continue
            if status == "unknown":
                unknown.append({"replay_id": replay_id, "claim": claim, "reason": wrapped.get("reason")})
                continue
            if status != "source_backed":
                continue
            lesson_id = _lesson_id_from_claim(claim)
            if not lesson_id:
                continue
            counters[lesson_id] += 1
            examples[lesson_id].append({"replay_id": replay_id, "claim": claim, "evidence": wrapped.get("evidence", [])[:2]})

    proven = []
    hypotheses = []
    insufficient = []
    for lesson_id, count in counters.most_common():
        entry = {
            "lesson_id": lesson_id,
            "evidence_count": count,
            "examples": examples[lesson_id][:3],
            "guidance": _guidance_for_lesson(lesson_id, count),
        }
        if count >= min_repeats:
            proven.append(entry)
        elif count > 0:
            hypotheses.append(entry)
        else:
            insufficient.append(entry)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(artifacts),
        "min_repeats": min_repeats,
        "proven_lessons": proven,
        "hypotheses": hypotheses,
        "insufficient_evidence": insufficient,
        "must_not_conclude": {
            "unknown_claims": unknown,
            "rejected_claims": rejected,
            "overfit_guardrail": f"single-loss findings remain hypotheses until seen in at least {min_repeats} losses",
        },
        "source_contract": GROUNDING_SOURCES.copy(),
    }


def _lesson_id_from_claim(claim: dict[str, Any]) -> str:
    kind = claim.get("kind")
    if kind == "hazard_damage":
        return f"bot_took_hazard_damage:{normalize_id(claim.get('hazard'))}"
    if kind == "hazard_pressure":
        return f"hazard_pressure_on_bot_side:{normalize_id(claim.get('hazard'))}"
    if kind == "type_effectiveness" and claim.get("actual") == "super_effective":
        move_type = ""
        move_data = all_move_json.get(normalize_id(claim.get("move"))) or {}
        move_type = str(move_data.get("type", "")).lower()
        return f"bot_fainted_to_super_effective:{move_type or normalize_id(claim.get('move'))}"
    if kind == "type_effectiveness" and claim.get("actual") == "immune":
        return f"move_hit_immunity:{normalize_id(claim.get('move'))}"
    return ""


def _guidance_for_lesson(lesson_id: str, count: int) -> dict[str, str]:
    if lesson_id.startswith("bot_took_hazard_damage") or lesson_id.startswith("hazard_pressure_on_bot_side"):
        return {
            "failed": "The loss evidence shows hazards damaging or pressuring our side.",
            "supported_adjustment": "Review hazard prevention/removal timing and avoid unnecessary switches while hazards remain up.",
            "needs_more_samples": "Do not change team structure unless this repeats across multiple losses with matching replay evidence.",
        }
    if lesson_id.startswith("bot_fainted_to_super_effective"):
        return {
            "failed": "A KO involved locally verified super-effective damage.",
            "supported_adjustment": "Audit switch and preservation decisions around that attacking type in the cited turns.",
            "needs_more_samples": "Do not declare a team-wide matchup flaw until this repeats against the same type or threat class.",
        }
    return {
        "failed": "A source-backed loss pattern repeated.",
        "supported_adjustment": "Review the cited turns before changing team or search policy.",
        "needs_more_samples": "Keep as conservative guidance unless the pattern persists.",
    }


def load_replay(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build mechanics-backed loss-learning artifacts from local replay JSON files.")
    parser.add_argument("replay_json", nargs="+", type=Path)
    parser.add_argument("--bot-username", default=resolve_bot_username())
    parser.add_argument("--team-file", default=None)
    parser.add_argument("--min-repeats", type=int, default=2)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON output path for aggregate learning summary.")
    args = parser.parse_args(argv)

    artifacts = [build_loss_artifact(load_replay(path), bot_username=args.bot_username, team_file=args.team_file) for path in args.replay_json]
    summary = aggregate_loss_lessons(artifacts, min_repeats=args.min_repeats)
    payload = {"artifacts": artifacts, "team_learning_summary": summary}
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
