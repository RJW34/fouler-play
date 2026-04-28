"""
PokedexOracle — single source of truth for Pokemon facts.

Every Pokemon-specific claim in this project (type matchups, abilities,
move effects, common sets) MUST come from this module or the underlying
data files.  Never use LLM knowledge for Pokemon facts.

Usage:
    from data.pokedex_oracle import oracle
    oracle.pokemon("gholdengo")        # types, stats, abilities
    oracle.move("shadowball")          # type, power, category, effects
    oracle.effectiveness("ghost", ["dark", "ground"])  # 0.5
    oracle.common_sets("gholdengo")    # top moves/items/tera from Smogon
    oracle.grounding_block("gholdengo")  # full profile for embedding
    oracle.team_profile("gen9/ou/fat-team-1-stall")  # our team's Pokemon
    oracle.matchup_summary("gholdengo", "gen9/ou/fat-team-1-stall")
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DATA_DIR.parent


def _normalize(name: str) -> str:
    """Normalize a Pokemon/move name to pokedex key format."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class PokedexOracle:
    """Canonical lookup layer for all Pokemon data in this project."""

    def __init__(self):
        self._pokedex: dict[str, Any] = _load_json(_DATA_DIR / "pokedex.json")
        self._moves: dict[str, Any] = _load_json(_DATA_DIR / "moves.json")
        self._smogon: dict[str, Any] = {}
        smogon_path = _DATA_DIR / "smogon_stats_cache" / "gen9ou-0.json"
        if smogon_path.exists():
            self._smogon = _load_json(smogon_path)
        # Type chart from helpers.py
        from fp.helpers import (
            DAMAGE_MULTIPICATION_ARRAY,
            POKEMON_TYPE_INDICES,
        )
        self._type_chart = DAMAGE_MULTIPICATION_ARRAY
        self._type_indices = POKEMON_TYPE_INDICES

    # ── Core lookups ─────────────────────────────────────────────────

    def pokemon(self, name: str) -> dict[str, Any] | None:
        """Look up a Pokemon by name.  Returns types, baseStats, abilities."""
        key = _normalize(name)
        entry = self._pokedex.get(key)
        if entry is None:
            return None
        return {
            "name": entry.get("name", name),
            "types": entry.get("types", []),
            "baseStats": entry.get("baseStats", {}),
            "abilities": entry.get("abilities", {}),
        }

    def move(self, name: str) -> dict[str, Any] | None:
        """Look up a move by name.  Returns type, power, category, effects."""
        key = _normalize(name)
        entry = self._moves.get(key)
        if entry is None:
            return None
        result = {
            "name": entry.get("name", name),
            "type": entry.get("type", ""),
            "basePower": entry.get("basePower", 0),
            "category": entry.get("category", ""),
            "accuracy": entry.get("accuracy", 0),
            "pp": entry.get("pp", 0),
            "priority": entry.get("priority", 0),
        }
        if entry.get("secondary"):
            result["secondary"] = entry["secondary"]
        if entry.get("flags"):
            result["flags"] = entry["flags"]
        return result

    def effectiveness(self, atk_type: str, def_types: list[str]) -> float:
        """Compute type effectiveness multiplier from the authoritative chart."""
        atk_idx = self._type_indices.get(atk_type)
        if atk_idx is None:
            return 1.0
        mult = 1.0
        for def_type in def_types:
            def_idx = self._type_indices.get(def_type)
            if def_idx is None:
                continue
            mult *= self._type_chart[atk_idx][def_idx]
        return mult

    # ── Smogon usage data ────────────────────────────────────────────

    def common_sets(self, name: str, top_n: int = 6) -> dict[str, Any]:
        """Return top moves, items, tera types from Smogon usage stats."""
        # Smogon keys use display names like "Gholdengo"
        smogon_entry = None
        name_norm = _normalize(name)
        for display_name, data in self._smogon.items():
            if _normalize(display_name) == name_norm:
                smogon_entry = data
                break
        if smogon_entry is None:
            return {"moves": {}, "items": {}, "tera_types": {}, "abilities": {}}

        def _top(d: dict, n: int) -> dict:
            if not isinstance(d, dict):
                return {}
            sorted_items = sorted(d.items(), key=lambda x: x[1], reverse=True)
            total = sum(v for _, v in sorted_items) or 1
            return {k: round(v / total * 100, 1) for k, v in sorted_items[:n]}

        return {
            "moves": _top(smogon_entry.get("Moves", {}), top_n),
            "items": _top(smogon_entry.get("Items", {}), top_n),
            "tera_types": _top(smogon_entry.get("Tera Types", {}), top_n),
            "abilities": _top(smogon_entry.get("Abilities", {}), top_n),
        }

    # ── Team files ───────────────────────────────────────────────────

    def parse_team_file(self, team_path: str) -> list[dict[str, Any]]:
        """Parse a Showdown team file into structured Pokemon entries."""
        full_path = _PROJECT_ROOT / "teams" / team_path
        if not full_path.exists():
            return []
        text = full_path.read_text(encoding="utf-8")
        mons = []
        current: dict[str, Any] = {}
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                if current:
                    mons.append(current)
                    current = {}
                continue
            if "@" in line and not current.get("name"):
                parts = line.split("@")
                current["name"] = parts[0].strip().split("(")[0].strip()
                current["item"] = parts[1].strip() if len(parts) > 1 else ""
                current["moves"] = []
            elif line.startswith("Ability:"):
                current["ability"] = line.split(":", 1)[1].strip()
            elif line.startswith("Tera Type:"):
                current["tera_type"] = line.split(":", 1)[1].strip()
            elif line.startswith("EVs:"):
                current["evs"] = line.split(":", 1)[1].strip()
            elif line.startswith("- "):
                current.setdefault("moves", []).append(line[2:].strip())
            elif "Nature" in line:
                current["nature"] = line.split("Nature")[0].strip()
        if current:
            mons.append(current)
        return mons

    def team_profile(self, team_path: str) -> list[dict[str, Any]]:
        """Parse a team file and enrich each Pokemon with oracle data."""
        mons = self.parse_team_file(team_path)
        for mon in mons:
            dex = self.pokemon(mon.get("name", ""))
            if dex:
                mon["types"] = dex["types"]
                mon["baseStats"] = dex["baseStats"]
            for i, move_name in enumerate(mon.get("moves", [])):
                move_data = self.move(move_name)
                if move_data:
                    mon["moves"][i] = {
                        "name": move_name,
                        "type": move_data["type"],
                        "basePower": move_data["basePower"],
                        "category": move_data["category"],
                    }
        return mons

    # ── Matchup analysis ─────────────────────────────────────────────

    def matchup_summary(
        self, opponent_name: str, team_path: str
    ) -> dict[str, Any]:
        """Compute how an opponent Pokemon matches up against our team.

        Returns which of our Pokemon resist it, which are threatened,
        and which of our moves hit it super-effectively.
        """
        opp = self.pokemon(opponent_name)
        if opp is None:
            return {"error": f"unknown Pokemon: {opponent_name}"}
        opp_types = opp["types"]
        team = self.team_profile(team_path)
        sets = self.common_sets(opponent_name)
        # What offensive types does the opponent commonly bring?
        opp_atk_types: list[str] = []
        for move_key in sets.get("moves", {}):
            move_data = self.move(move_key)
            if move_data and move_data["category"] in ("physical", "special"):
                if move_data["type"] not in opp_atk_types:
                    opp_atk_types.append(move_data["type"])

        results: list[dict[str, Any]] = []
        for mon in team:
            mon_name = mon.get("name", "?")
            mon_types = mon.get("types", [])
            # How much does opponent's STAB threaten us?
            worst_incoming = 1.0
            for atk_type in opp_atk_types:
                eff = self.effectiveness(atk_type, mon_types)
                if eff > worst_incoming:
                    worst_incoming = eff
            # How well do our moves hit the opponent?
            best_outgoing = 0.0
            best_move = ""
            for m in mon.get("moves", []):
                if isinstance(m, dict) and m["category"] in ("physical", "special"):
                    eff = self.effectiveness(m["type"], opp_types)
                    if eff > best_outgoing:
                        best_outgoing = eff
                        best_move = m["name"]
            results.append({
                "pokemon": mon_name,
                "types": mon_types,
                "worst_incoming_eff": worst_incoming,
                "best_outgoing_eff": best_outgoing,
                "best_move": best_move,
            })

        # Classify
        walls = [r for r in results if r["worst_incoming_eff"] <= 0.5]
        checks = [r for r in results if r["best_outgoing_eff"] >= 2.0]
        threatened = [r for r in results if r["worst_incoming_eff"] >= 2.0]

        return {
            "opponent": opp["name"],
            "opponent_types": opp_types,
            "opponent_common_atk_types": opp_atk_types,
            "our_walls": [w["pokemon"] for w in walls],
            "our_checks": [
                {"pokemon": c["pokemon"], "move": c["best_move"]}
                for c in checks
            ],
            "our_threatened": [t["pokemon"] for t in threatened],
            "per_pokemon": results,
        }

    # ── Grounding block ──────────────────────────────────────────────

    def grounding_block(self, pokemon_name: str) -> dict[str, Any]:
        """Generate a complete, structured profile for embedding in reports.

        This is the anti-hallucination payload: everything an agent needs
        to reason about this Pokemon without using LLM knowledge.
        """
        dex = self.pokemon(pokemon_name)
        if dex is None:
            return {"error": f"unknown Pokemon: {pokemon_name}"}
        sets = self.common_sets(pokemon_name)
        # Enrich common moves with actual move data
        enriched_moves: list[dict[str, Any]] = []
        for move_key, usage_pct in sets.get("moves", {}).items():
            move_data = self.move(move_key)
            if move_data:
                enriched_moves.append({
                    "name": move_data["name"],
                    "type": move_data["type"],
                    "basePower": move_data["basePower"],
                    "category": move_data["category"],
                    "usage_pct": usage_pct,
                })
            else:
                enriched_moves.append({"name": move_key, "usage_pct": usage_pct})
        return {
            "source": "data/pokedex.json + data/moves.json + data/smogon_stats_cache/gen9ou-0.json",
            "pokemon": dex["name"],
            "types": dex["types"],
            "baseStats": dex["baseStats"],
            "abilities": dex["abilities"],
            "common_moves": enriched_moves,
            "common_items": sets.get("items", {}),
            "common_tera": sets.get("tera_types", {}),
        }

    def validate_ability_claim(
        self, pokemon_name: str, ability_name: str
    ) -> bool:
        """Verify that a Pokemon can actually have a given ability."""
        dex = self.pokemon(pokemon_name)
        if dex is None:
            return False
        abilities = dex.get("abilities", {})
        norm_claim = _normalize(ability_name)
        for _, ab in abilities.items():
            if _normalize(ab) == norm_claim:
                return True
        return False

    def validate_type_claim(
        self, atk_type: str, def_types: list[str], claimed_mult: float
    ) -> tuple[bool, float]:
        """Verify a type effectiveness claim.  Returns (correct, actual)."""
        actual = self.effectiveness(atk_type, def_types)
        return abs(actual - claimed_mult) < 0.01, actual


# Module-level singleton — import and use directly.
oracle = PokedexOracle()
