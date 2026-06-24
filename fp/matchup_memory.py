"""Matchup memory: loss-derived weights that bias live play (safe weights path).

This module is the single connection point between the loss-analysis pipeline and
the live decision path. It does NOT generate or apply code diffs. It only:

  1. READS ``fp/matchup_weights.json`` (loss-derived ``bad_matchups`` /
     ``problem_pokemon``) and exposes a bounded, multiplicative bias over the
     engine's own candidate policy dict (``{decision: score}``). The bias never
     adds candidates and never removes legality - it only nudges the engine's
     existing legal candidates, mirroring the proven ``forced_line_bias`` pattern.

  2. POPULATES ``fp/matchup_weights.json`` from deterministic loss artifacts
     (see ``replay_analysis/loss_learning.py``). Observed losses -> weights ->
     biased play. This is reviewable data, not generated code.

Design intent (mission-critical): every battle was previously played on a frozen
policy. With this module wired in, observed losses raise the weight on the
opponent species that beat us, and the live policy then prefers pivoting away
from those species instead of repeating the losing line.

All behavior is bounded and fail-safe: any error in loading or biasing returns
the engine's original policy unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Canonical weights file (read by live path, written by the updater).
WEIGHTS_PATH = Path(__file__).resolve().parent / "matchup_weights.json"

# --- Tunables (env-overridable, all bounded) -------------------------------
# Master switch. Default ON; set MATCHUP_MEMORY_ENABLED=0 to fall back to the
# pre-existing frozen-policy behavior instantly without a code change.
ENABLED = os.getenv("MATCHUP_MEMORY_ENABLED", "1").lower() in {"1", "true", "yes", "on"}

# A species counts as a live "problem pokemon" once it has KO'd our mons in at
# least this many DISTINCT losses. Re-tuned 5 -> 8 (2026-06-24) after a
# ground-truth sweep showed the prior threshold flagged ~80 species live (most of
# the OU metagame) -> indiscriminate switch-pressure that churns rather than
# learns. At >=8 distinct losses the set collapses to the genuine repeat threats
# (Great Tusk, Ogerpon, Kingambit, Gholdengo, Dragonite, Raging Bolt, Kyurem,
# Gliscor, Zamazenta, Iron Valiant, Darkrai, Hoopa, Dondozo, Dragapult). The
# nickname->species fix (loss_learning.NicknameResolver) also consolidates KO
# credit onto real species, so true counts only sharpen this set further.
PROBLEM_MIN_LOSSES = max(1, int(os.getenv("MATCHUP_MEMORY_PROBLEM_MIN_LOSSES", "8")))

# Secondary KO-count gate. The PRIMARY signal is losses_present (distinct losses
# the species KO'd us in). kos_on_us (raw KO count, accumulates across losses) is
# only a corroborating signal and uses its own, higher threshold so a species that
# racked up many KOs across just a couple of blowout losses does not get flagged
# as a persistent repeat threat. Comparing raw kos against the loss threshold (the
# prior behavior) was a category error that over-fired the bias.
PROBLEM_MIN_KOS = max(PROBLEM_MIN_LOSSES, int(os.getenv("MATCHUP_MEMORY_PROBLEM_MIN_KOS", "20")))

# A species counts as a "bad matchup" once we have at least this many games vs
# it and our loss rate against it is at least the threshold below. Re-tuned
# (min_games 4 -> 6, loss_rate 0.60 -> 0.65) on the same 2026-06-24 sweep:
# trims the bad-matchup flag set ~30 -> ~12 so only matchups we genuinely lose
# the majority of are biased against.
BAD_MATCHUP_MIN_GAMES = max(2, int(os.getenv("MATCHUP_MEMORY_BAD_MIN_GAMES", "6")))
BAD_MATCHUP_LOSS_RATE = min(0.95, max(0.5, float(os.getenv("MATCHUP_MEMORY_BAD_LOSS_RATE", "0.70"))))

# Bias strengths. Bounded small so MCTS/eval remains the dominant signal: this
# is a tie-breaker / nudge, not an override.
SWITCH_BOOST = min(1.6, max(1.0, float(os.getenv("MATCHUP_MEMORY_SWITCH_BOOST", "1.18"))))
STAY_DAMP = min(1.0, max(0.7, float(os.getenv("MATCHUP_MEMORY_STAY_DAMP", "0.92"))))

# --- A/B validation harness (forward mandate, 2026-06-24) ------------------
# When MATCHUP_MEMORY_AB=1 the bias is applied on a deterministic ~50% of battles
# (hashed by battle id) instead of always-on, and the arm ("on"/"off") is logged
# per battle to AB_LOG_PATH. scripts/analyze_matchup_ab.py then joins those arms
# against battle_stats.json results to measure bias-on vs bias-off WR on the live
# ladder. This is the only honest way to prove the matchup-memory lever actually
# climbs ELO rather than just churning. Off by default (pure always-on behavior).
AB_ENABLED = os.getenv("MATCHUP_MEMORY_AB", "0").lower() in {"1", "true", "yes", "on"}
AB_LOG_PATH = Path(os.getenv("MATCHUP_MEMORY_AB_LOG", str(Path(__file__).resolve().parent.parent / "logs" / "matchup_ab_log.jsonl")))
_ab_logged_battles: set[str] = set()


def _battle_id(battle) -> str:
    for attr in ("battle_tag", "battle_id", "room_id", "tag"):
        val = getattr(battle, attr, None)
        if val:
            return str(val)
    return ""


def ab_arm_for_battle(battle) -> str:
    """Deterministic A/B arm for this battle: 'on' or 'off' (stable per battle id)."""
    bid = _battle_id(battle)
    if not bid:
        return "on"  # no id -> default to applying the bias
    import hashlib

    h = int(hashlib.sha1(bid.encode("utf-8")).hexdigest()[:8], 16)
    return "on" if (h % 2 == 0) else "off"


def _log_ab_arm(bid: str, arm: str, species_id: str, flagged: dict | None) -> None:
    if not bid or bid in _ab_logged_battles:
        return
    _ab_logged_battles.add(bid)
    try:
        AB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "battle_id": bid,
            "arm": arm,
            "first_flagged_opponent": species_id or None,
            "flag_kind": (flagged or {}).get("kind") if flagged else None,
        }
        with AB_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(rec) + "\n")
    except Exception as exc:
        logger.debug("matchup_memory: ab log write failed: %s", exc)


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def load_weights(path: Path | None = None) -> dict[str, Any]:
    """Load the loss-derived weights. Returns a safe empty structure on any error."""
    target = path or WEIGHTS_PATH
    empty = {"bad_matchups": {}, "problem_pokemon": {}, "updated_at": None}
    try:
        if not target.exists():
            return empty
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return empty
        data.setdefault("bad_matchups", {})
        data.setdefault("problem_pokemon", {})
        if not isinstance(data["bad_matchups"], dict):
            data["bad_matchups"] = {}
        if not isinstance(data["problem_pokemon"], dict):
            data["problem_pokemon"] = {}
        return data
    except Exception as exc:  # never let weights break the bot
        logger.debug("matchup_memory: failed to load weights: %s", exc)
        return empty


def _opponent_active_id(battle) -> str:
    try:
        opp = getattr(battle, "opponent", None)
        active = getattr(opp, "active", None) if opp is not None else None
        if active is None:
            return ""
        return _norm(getattr(active, "name", ""))
    except Exception:
        return ""


def opponent_is_flagged(species_id: str, weights: dict[str, Any]) -> dict[str, Any] | None:
    """Return a reason dict if the opponent species is a known threat, else None."""
    if not species_id:
        return None

    problem = weights.get("problem_pokemon", {})
    bad = weights.get("bad_matchups", {})

    entry = problem.get(species_id)
    if isinstance(entry, dict):
        kos = int(entry.get("kos_on_us", 0) or 0)
        losses = int(entry.get("losses_present", entry.get("losses", 0)) or 0)
        if losses >= PROBLEM_MIN_LOSSES or kos >= PROBLEM_MIN_KOS:
            return {
                "kind": "problem_pokemon",
                "species": species_id,
                "kos_on_us": kos,
                "losses_present": losses,
            }

    bentry = bad.get(species_id)
    if isinstance(bentry, dict):
        games = int(bentry.get("games", 0) or 0)
        loss_rate = float(bentry.get("loss_rate", 0.0) or 0.0)
        if games >= BAD_MATCHUP_MIN_GAMES and loss_rate >= BAD_MATCHUP_LOSS_RATE:
            return {
                "kind": "bad_matchup",
                "species": species_id,
                "games": games,
                "loss_rate": round(loss_rate, 3),
            }
    return None


def bias_policy(
    policy: dict[str, float],
    battle,
    *,
    weights: dict[str, Any] | None = None,
    trace: dict | None = None,
) -> dict[str, float]:
    """Apply a bounded loss-derived bias to the engine's candidate policy.

    The opponent's currently-active species is matched against the loss-derived
    weights. If it is a known threat, ``switch *`` candidates are boosted and
    non-switch (stay-in) candidates are mildly damped, so the bot prefers
    pivoting away from matchups that historically beat it. No candidate is added
    or removed; only the engine's own scores are reweighted.

    Returns a NEW dict; never mutates the input. Returns the input unchanged on
    any error or when disabled.
    """
    if not ENABLED or not policy:
        return policy
    try:
        w = weights if weights is not None else load_weights()
        species_id = _opponent_active_id(battle)
        flagged = opponent_is_flagged(species_id, w)

        # A/B harness: when enabled, ~half of battles run with the bias OFF so we
        # can measure its real WR effect. Log the arm once per battle (even when
        # not flagged this turn, so the arm assignment is captured early).
        if AB_ENABLED:
            arm = ab_arm_for_battle(battle)
            _log_ab_arm(_battle_id(battle), arm, species_id, flagged)
            if arm == "off":
                if trace is not None:
                    trace["matchup_memory_bias"] = {"applied": False, "ab_arm": "off"}
                return policy

        if not flagged:
            return policy

        # Don't fight a forced switch or override when there is nothing to pivot to.
        biased: dict[str, float] = {}
        switch_keys = [k for k in policy if str(k).startswith("switch ")]
        if not switch_keys:
            return policy

        for decision, score in policy.items():
            try:
                val = float(score)
            except (TypeError, ValueError):
                biased[decision] = score
                continue
            if str(decision).startswith("switch "):
                biased[decision] = val * SWITCH_BOOST
            else:
                biased[decision] = val * STAY_DAMP

        if trace is not None:
            trace["matchup_memory_bias"] = {
                "applied": True,
                "ab_arm": "on" if AB_ENABLED else "always",
                "opponent_species": species_id,
                "flag": flagged,
                "switch_boost": SWITCH_BOOST,
                "stay_damp": STAY_DAMP,
                "candidates_biased": len(biased),
            }
        logger.info(
            "matchup_memory: biasing vs %s (%s) - switch_boost=%.2f stay_damp=%.2f",
            species_id, flagged.get("kind"), SWITCH_BOOST, STAY_DAMP,
        )
        return biased
    except Exception as exc:
        logger.debug("matchup_memory: bias_policy failed, using engine policy: %s", exc)
        return policy


# ---------------------------------------------------------------------------
# Populate side: build weights from deterministic loss artifacts.
# ---------------------------------------------------------------------------

def update_weights_from_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate loss artifacts (from loss_learning.build_loss_artifact) into weights.

    For each artifact we look at the opponent's revealed team and which opponent
    Pokemon KO'd our side:
      * bad_matchups[opp_species]: games / losses / loss_rate vs that species
        (counts every opponent team member, win or loss).
      * problem_pokemon[opp_species]: how many of OUR mons it KO'd and in how
        many distinct losses it appeared as a KO source.

    Returns a fresh weights dict (does not mutate ``existing``); ``existing`` is
    accepted for forward-compat but a full rebuild from the artifact window is
    used so stale species naturally age out as old replays leave the window.
    """
    bad: dict[str, dict[str, int]] = {}
    problem: dict[str, dict[str, int]] = {}

    for art in artifacts:
        if not isinstance(art, dict):
            continue
        bot_side = str(art.get("bot_side") or "")
        result = str(art.get("result") or "")
        teams = art.get("teams") or {}
        if bot_side not in {"p1", "p2"}:
            continue
        opp_side = "p2" if bot_side == "p1" else "p1"
        opp_team = [s for s in (teams.get(opp_side) or []) if s]
        is_loss = result == "loss"

        # bad_matchups: every opponent team member is a "game" vs that species.
        for species in opp_team:
            sid = _norm(species)
            if not sid:
                continue
            row = bad.setdefault(sid, {"games": 0, "losses": 0})
            row["games"] += 1
            if is_loss:
                row["losses"] += 1

        if not is_loss:
            continue

        # problem_pokemon: opponent attackers that KO'd OUR mons in this loss.
        ko_attackers_this_loss: set[str] = set()
        for ko in art.get("key_kos") or []:
            if not isinstance(ko, dict):
                continue
            if str(ko.get("target_side") or "") != bot_side:
                continue  # only count KOs against our side
            attacker = ko.get("attacker")
            if not attacker:
                continue
            sid = _norm(attacker)
            if not sid:
                continue
            row = problem.setdefault(sid, {"kos_on_us": 0, "losses_present": 0})
            row["kos_on_us"] += 1
            ko_attackers_this_loss.add(sid)
        for sid in ko_attackers_this_loss:
            problem[sid]["losses_present"] += 1

    # finalize loss_rate
    bad_out: dict[str, Any] = {}
    for sid, row in bad.items():
        games = max(1, row["games"])
        bad_out[sid] = {
            "games": row["games"],
            "losses": row["losses"],
            "loss_rate": round(row["losses"] / games, 4),
        }

    return {
        "bad_matchups": bad_out,
        "problem_pokemon": problem,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "replay_analysis/loss_learning.build_loss_artifact",
        "artifact_count": len(artifacts),
    }


def write_weights(weights: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write the weights JSON."""
    target = path or WEIGHTS_PATH
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(weights, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
