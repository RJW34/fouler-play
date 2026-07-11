from __future__ import annotations

import json
import hashlib
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.pokedex_oracle import oracle as _oracle
from infrastructure.discord_reporting import build_contract_payload
from infrastructure.event_queue_lib import queue_event
from replay_analysis.account_identity import resolve_bot_username

BATTLE_STATS_PATH = PROJECT_ROOT / "battle_stats.json"
REPLAY_DIR = PROJECT_ROOT / "replay_analysis"
TRACE_DIR = PROJECT_ROOT / "logs" / "decision_traces"
REPORTS_DIR = REPLAY_DIR / "reports"
AUTORESEARCH_JSON_PATH = REPLAY_DIR / "autoresearch_latest.json"
AUTORESEARCH_MD_PATH = REPLAY_DIR / "reports" / "autoresearch_latest.md"
BATCH_HISTORY_DIR = REPLAY_DIR / "batches"

ROUTINE_CHANNEL = os.getenv("FOULER_ROUTINE_CHANNEL", "1466691161363054840")
RESEARCH_CHANNEL = os.getenv("FOULER_RESEARCH_CHANNEL", "1466869808200028264")
BOT_USERNAME = resolve_bot_username()
HAZARD_SETTING_MOVES = {"stealthrock", "spikes", "toxicspikes", "stickyweb"}
HAZARD_CONTROL_MOVES = {"defog", "rapidspin", "mortalspin", "tidyup", "courtchange"}
MAGIC_BOUNCE_REFLECTED_MOVES = HAZARD_SETTING_MOVES | {"toxic", "willowisp", "thunderwave", "glare"}


def _normalize_move_name(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


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
        self.elo_proof_path = self.project_root / "devstream" / "truth" / "latest-elo-proof.json"
        self.replay_dir = self.project_root / "replay_analysis"
        self.trace_dir = self.project_root / "logs" / "decision_traces"
        self.reports_dir = self.replay_dir / "reports"
        self.batch_history_dir = self.replay_dir / "batches"
        self.trace_evidence_dir = self.replay_dir / "evidence_traces"
        self.last_battle_source = "battle_stats.json"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.batch_history_dir.mkdir(parents=True, exist_ok=True)
        self.trace_evidence_dir.mkdir(parents=True, exist_ok=True)

    def _detect_team_paths(self, window: list[dict[str, Any]]) -> list[str]:
        """Extract unique team file paths from the battle window."""
        seen: set[str] = set()
        paths: list[str] = []
        for battle in window:
            tf = battle.get("team_file", "")
            if tf and tf not in seen:
                seen.add(tf)
                # Try common path patterns
                for prefix in ["gen9/ou/", ""]:
                    candidate = prefix + tf
                    full = self.project_root / "teams" / candidate
                    if full.exists():
                        paths.append(candidate)
                        break
        return paths

    def _team_file_candidates(self, team_file: Any) -> list[Path]:
        raw = str(team_file or "").strip().replace("\\", "/")
        if not raw:
            return []
        candidates = [self.project_root / "teams" / raw]
        if not raw.startswith("gen9/ou/"):
            candidates.append(self.project_root / "teams" / "gen9" / "ou" / raw)
        return candidates

    def _team_hazard_capabilities(self, team_file: Any) -> dict[str, Any]:
        """Return deterministic hazard capabilities for the exact team file.

        This keeps post-game research honest: a team without any hazard setter
        cannot be blamed for never setting Stealth Rock, while a hazard-control
        team can still be blamed for losing the hazard-control exchange.
        """
        for path in self._team_file_candidates(team_file):
            if not path.exists() or not path.is_file():
                continue
            moves: set[str] = set()
            move_labels: dict[str, str] = {}
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("- "):
                        label = line[2:].strip()
                        normalized = _normalize_move_name(label)
                        moves.add(normalized)
                        move_labels.setdefault(normalized, label)
            except OSError:
                break
            hazard_moves = [
                move_labels.get(move, move) for move in sorted(moves & HAZARD_SETTING_MOVES)
            ]
            control_moves = [
                move_labels.get(move, move) for move in sorted(moves & HAZARD_CONTROL_MOVES)
            ]
            return {
                "known": True,
                "teamFile": str(team_file or ""),
                "path": str(path.relative_to(self.project_root)).replace("\\", "/"),
                "hazardMoves": hazard_moves,
                "hazardControlMoves": control_moves,
                "canSetHazards": bool(hazard_moves),
                "canControlHazards": bool(control_moves),
            }
        return {
            "known": False,
            "teamFile": str(team_file or ""),
            "path": "",
            "hazardMoves": [],
            "hazardControlMoves": [],
            "canSetHazards": True,
            "canControlHazards": True,
        }

    def _latest_timestamp(self, battles: list[dict[str, Any]]) -> str:
        timestamps = [str(battle.get("timestamp") or "") for battle in battles]
        return max(timestamps) if timestamps else ""

    def _normalize_elo_proof_battle(self, game: dict[str, Any]) -> dict[str, Any]:
        battle_id = str(game.get("battleId") or game.get("battle_id") or "")
        replay_url = str(game.get("replayUrl") or game.get("replay_url") or "")
        replay_id = battle_id
        if not replay_id and replay_url:
            replay_id = replay_url.rstrip("/").split("/")[-1]
        return {
            "battle_id": battle_id or replay_id,
            "timestamp": game.get("timestamp"),
            "team_file": game.get("teamFile") or game.get("team_file"),
            "result": game.get("result"),
            "replay_id": replay_id,
            "replay_url": replay_url,
            "rating": game.get("ratingAfter") or game.get("rating"),
            "ratingBefore": game.get("ratingBefore"),
            "opponent": game.get("opponent", ""),
            "opponentRating": game.get("opponentRating"),
            "source": "devstream/truth/latest-elo-proof.json",
        }

    def load_elo_proof_battles(self) -> list[dict[str, Any]]:
        if not self.elo_proof_path.exists():
            return []
        try:
            data = json.loads(self.elo_proof_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        games = data.get("games", [])
        if not isinstance(games, list):
            return []
        battles = [
            self._normalize_elo_proof_battle(game)
            for game in games
            if isinstance(game, dict)
        ]
        battles = [battle for battle in battles if battle.get("battle_id") or battle.get("timestamp")]
        battles.sort(key=lambda b: b.get("timestamp", ""))
        return battles

    def load_battle_stats_battles(self) -> list[dict[str, Any]]:
        if not self.battle_stats_path.exists():
            return []
        data = json.loads(self.battle_stats_path.read_text(encoding="utf-8"))
        battles = data.get("battles", [])
        battles = [battle for battle in battles if isinstance(battle, dict) and not self._is_offline_eval_battle(battle)]
        battles.sort(key=lambda b: b.get("timestamp", ""))
        return battles

    def _is_offline_eval_battle(self, battle: dict[str, Any]) -> bool:
        """Return true for synthetic local eval battles that must not feed autoresearch."""
        if battle.get("offline_eval") is True or battle.get("offlineEval") is True:
            return True
        fields = [
            battle.get("source"),
            battle.get("battle_source"),
            battle.get("battleSource"),
            battle.get("eval_label"),
            battle.get("evalLabel"),
        ]
        text = " ".join(str(value).lower() for value in fields if value is not None)
        return "offline_eval" in text or "offline-eval" in text or "offline eval" in text

    def load_battles(self) -> list[dict[str, Any]]:
        local_battles = self.load_battle_stats_battles()
        proof_battles = self.load_elo_proof_battles()
        if proof_battles and self._latest_timestamp(proof_battles) > self._latest_timestamp(local_battles):
            self.last_battle_source = "devstream/truth/latest-elo-proof.json"
            return proof_battles
        self.last_battle_source = "battle_stats.json"
        return local_battles

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
                raw_bytes = path.read_bytes()
                raw = raw_bytes.decode("utf-8")
                trace = json.loads(raw)
                if isinstance(trace, dict):
                    snapshot_path, snapshot_sha = self._snapshot_trace_evidence(path, raw_bytes)
                    trace["_source_trace_path"] = path.relative_to(self.project_root).as_posix()
                    trace["_trace_path"] = snapshot_path.relative_to(self.project_root).as_posix()
                    trace["_trace_sha256"] = snapshot_sha
                    traces.append(trace)
            except Exception:
                continue
        return traces

    def _snapshot_trace_evidence(self, source_path: Path, raw: bytes) -> tuple[Path, str]:
        digest = hashlib.sha256(raw).hexdigest()
        snapshot_name = f"{source_path.stem}-{digest[:12]}{source_path.suffix}"
        snapshot_path = self.trace_evidence_dir / snapshot_name
        if not snapshot_path.exists() or hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != digest:
            snapshot_path.write_bytes(raw)
        return snapshot_path, digest

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

    def _hazard_issue(
        self,
        lines: list[str],
        bot_slot: str,
        team_caps: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        if not lines:
            return False, "hazard analysis unavailable without replay or Showdown protocol log lines"
        team_caps = team_caps or {}
        can_set_hazards = team_caps.get("canSetHazards", True) is True
        can_control_hazards = team_caps.get("canControlHazards", True) is True
        control_moves = {
            _normalize_move_name(move)
            for move in team_caps.get("hazardControlMoves", [])
        }
        our_hazards = False
        hazards_on_us = False
        control_used = False
        for line in lines:
            if "|-sidestart|" in line and ("Stealth Rock" in line or "Spikes" in line):
                if f"|{bot_slot}:" in line:
                    hazards_on_us = True
                else:
                    our_hazards = True
            if line.startswith("|move|") and f"|{bot_slot}a:" in line:
                parts = line.split("|")
                if len(parts) >= 4 and _normalize_move_name(parts[3]) in control_moves:
                    control_used = True
        if can_set_hazards and hazards_on_us and not our_hazards:
            return True, "opponent won the hazard race while bot never established its own chip engine"
        if can_set_hazards and not our_hazards:
            return True, "bot never established Stealth Rock or equivalent chip pressure"
        if (
            not can_set_hazards
            and can_control_hazards
            and hazards_on_us
            and not control_used
        ):
            moves = ", ".join(team_caps.get("hazardControlMoves", []) or ["hazard control"])
            return True, f"team has no hazard setter; opponent hazards stuck while bot never used {moves}"
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

    def _trace_has_request_legal_options(self, trace: dict[str, Any]) -> bool:
        legal_options = trace.get("legalOptions") if isinstance(trace.get("legalOptions"), dict) else {}
        request = trace.get("showdownRequest") if isinstance(trace.get("showdownRequest"), dict) else {}
        request_hash = str(legal_options.get("requestHash") or request.get("requestHash") or "").strip()
        legal_moves = legal_options.get("legalMoves") if isinstance(legal_options.get("legalMoves"), list) else request.get("legalMoves")
        legal_switches = legal_options.get("legalSwitches") if isinstance(legal_options.get("legalSwitches"), list) else request.get("legalSwitches")
        candidate_bounded = legal_options.get("candidateSetBounded") is True or request.get("candidateSetBounded") is True
        return bool(
            request_hash
            and len(request_hash) == 64
            and candidate_bounded
            and (
                (isinstance(legal_moves, list) and legal_moves)
                or (isinstance(legal_switches, list) and legal_switches)
            )
        )

    def _trace_loop_break_events(self, trace: dict[str, Any]) -> Iterable[dict[str, Any]]:
        for _block_name, block in self._trace_policy_blocks(trace):
            events = block.get("events") if isinstance(block.get("events"), list) else []
            for event in events:
                if not isinstance(event, dict):
                    continue
                if str(event.get("source") or "") == "decision_loop_break":
                    yield event

    def _trace_refutes_instability(self, trace: dict[str, Any], choice: str) -> bool:
        choice_norm = _normalize_move_name(choice)
        for event in self._trace_loop_break_events(trace):
            move_norm = _normalize_move_name(event.get("move"))
            if move_norm and choice_norm and move_norm != choice_norm:
                continue
            reason = str(event.get("reason") or "").lower()
            if (
                "_repeated_0_" in reason
                or "position_not_stagnant" in reason
                or "search_decisive" in reason
                or "last_mon_damage_progress" in reason
            ):
                return True
        return False

    def _trace_issue(self, traces: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        reasons: list[str] = []
        legal_option_proofs: list[str] = []
        loop_break_proofs: list[str] = []
        for trace in traces:
            choice = str(trace.get("choice", "")).strip()
            reason = str(trace.get("reason", "")).strip()
            if reason:
                reasons.append(reason)
            for event in self._trace_loop_break_events(trace):
                event_type = str(event.get("type") or "")
                event_reason = str(event.get("reason") or "")
                if event_type != "override":
                    continue
                if "forcing_distinct" not in event_reason and "full_cycle_penalty" not in event_reason:
                    continue
                loop_break_proofs.append(
                    f"loop-breaker intervened on {event.get('move', 'unknown')}: {event_reason}"
                )
            legal_options = trace.get("legalOptions") if isinstance(trace.get("legalOptions"), dict) else {}
            request = trace.get("showdownRequest") if isinstance(trace.get("showdownRequest"), dict) else {}
            request_hash = str(legal_options.get("requestHash") or request.get("requestHash") or "").strip()
            legal_moves = legal_options.get("legalMoves") if isinstance(legal_options.get("legalMoves"), list) else request.get("legalMoves")
            legal_switches = legal_options.get("legalSwitches") if isinstance(legal_options.get("legalSwitches"), list) else request.get("legalSwitches")
            if self._trace_has_request_legal_options(trace):
                legal_option_proofs.append(
                    "request-backed legal options: "
                    f"requestHash={request_hash} "
                    f"legalMoves={len(legal_moves) if isinstance(legal_moves, list) else 0} "
                    f"legalSwitches={len(legal_switches) if isinstance(legal_switches, list) else 0} "
                    f"trace={trace.get('_trace_path', 'unknown')} "
                    f"traceSha256={trace.get('_trace_sha256', 'unknown')}"
                )

        repeated: list[str] = []
        current_run: list[str] = []
        for trace in traces:
            choice = str(trace.get("choice", "")).strip()
            if not choice:
                current_run = []
                continue
            if current_run and current_run[-1] == choice:
                current_run.append(choice)
            else:
                current_run = [choice]
            if (
                len(current_run) >= 3
                and choice not in repeated
                and not self._trace_refutes_instability(trace, choice)
            ):
                repeated.append(choice)

        findings: list[str] = []
        if loop_break_proofs:
            findings.append("; ".join(loop_break_proofs[:3]))
        if repeated:
            findings.append(f"consecutive repeated action loop: {', '.join(repeated[:3])}")
        if reasons:
            timeout_count = sum(1 for r in reasons if "timeout" in r or "fallback" in r or "error" in r)
            if timeout_count >= 2:
                findings.append(f"decision traces show {timeout_count} fallback/timeout/error selections")
        if findings and legal_option_proofs:
            findings.append(legal_option_proofs[-1])
        return findings, reasons

    def _regret_issue(self, traces: list[dict[str, Any]]) -> tuple[bool, str]:
        """
        Replay-grounded REGRET MINING (P2).

        A turn is "high regret" when the move actually chosen had a much lower MCTS
        value than the best legal line the search found -- i.e. the engine knew a
        better move existed and a downstream layer (or a bad sample) overrode it.
        We read the decision trace's mcts_policy_raw (visit-count policy) and the
        final choice, and flag turns where chosen_value << best_value.

        This grounds the issue in the ACTUAL search output for that turn rather than
        a hardcoded heuristic bucket. Returns (flagged, detail).
        """
        REGRET_RATIO = 0.45  # chosen kept < 45% of the best line's weight
        high_regret_turns: list[str] = []
        for trace in traces:
            policy = trace.get("mcts_policy_raw")
            if not isinstance(policy, dict) or not policy:
                # Fall back to the eval policy if MCTS policy is absent.
                ev = trace.get("eval") if isinstance(trace.get("eval"), dict) else {}
                policy = ev.get("policy_pre_penalty") if isinstance(ev.get("policy_pre_penalty"), dict) else {}
            if not isinstance(policy, dict) or not policy:
                continue
            choice = str(trace.get("choice", "")).strip()
            if not choice or choice not in policy:
                continue
            try:
                weights = {k: float(v) for k, v in policy.items() if v is not None}
            except (TypeError, ValueError):
                continue
            if not weights:
                continue
            best_move = max(weights, key=weights.get)
            best_w = weights[best_move]
            chosen_w = weights.get(choice, 0.0)
            if best_w <= 0 or best_move == choice:
                continue
            if chosen_w < best_w * REGRET_RATIO:
                turn = trace.get("turn", trace.get("battle_turn", "?"))
                high_regret_turns.append(
                    f"turn {turn}: chose {choice} (mcts {chosen_w:.3f}) over "
                    f"{best_move} (mcts {best_w:.3f}); regret "
                    f"{(best_w - chosen_w) / best_w:.0%}"
                )
        if len(high_regret_turns) >= 2:
            return True, "; ".join(high_regret_turns[:3])
        return False, ""

    def _trace_policy_blocks(self, trace: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
        for key in ("mcts_only", "eval"):
            block = trace.get(key)
            if isinstance(block, dict):
                yield key, block

    def _magic_bounce_reflected_hazard_issue(self, traces: list[dict[str, Any]]) -> tuple[bool, str]:
        """Find reflected status/hazard moves that survived safety filtering.

        The packet 008 failure mode was not generic instability: the safety layer
        noticed Magic Bounce, multiplied Stealth Rock/Toxic down, but the reflected
        move still remained the top post-safety policy entry and was selected.
        """
        findings: list[str] = []
        for trace in traces:
            for block_name, block in self._trace_policy_blocks(trace):
                events = block.get("events") if isinstance(block.get("events"), list) else []
                reflected: dict[str, tuple[str, float]] = {}
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    if str(event.get("reason") or "") != "magic_bounce_reflects_status":
                        continue
                    move_text = str(event.get("move") or "").strip()
                    move_norm = _normalize_move_name(move_text.split(":")[-1])
                    if move_norm not in MAGIC_BOUNCE_REFLECTED_MOVES:
                        continue
                    try:
                        after_weight = float(event.get("after") or 0.0)
                    except (TypeError, ValueError):
                        after_weight = 0.0
                    reflected[move_norm] = (move_text, after_weight)

                if not reflected:
                    continue

                top_moves = block.get("top_moves") if isinstance(block.get("top_moves"), list) else []
                top_norm = ""
                best_alt_name = ""
                best_alt_weight = 0.0
                for index, row in enumerate(top_moves):
                    if not isinstance(row, dict):
                        continue
                    move_text = str(row.get("move") or "").strip()
                    move_norm = _normalize_move_name(move_text.split(":")[-1])
                    try:
                        weight = float(row.get("weight", row.get("eval_weight", 0.0)) or 0.0)
                    except (TypeError, ValueError):
                        weight = 0.0
                    if index == 0:
                        top_norm = move_norm
                    if move_norm not in reflected and weight > best_alt_weight:
                        best_alt_name = move_text
                        best_alt_weight = weight

                choice_norm = _normalize_move_name(trace.get("choice"))
                bad_norm = choice_norm if choice_norm in reflected else top_norm if top_norm in reflected else ""
                if not bad_norm:
                    continue

                move_text, after_weight = reflected[bad_norm]
                turn = trace.get("turn", trace.get("battle_turn", "?"))
                selected = "selected" if choice_norm == bad_norm else f"ranked first in {block_name}"
                detail = f"turn {turn}: {selected} {move_text} into Magic Bounce"
                if best_alt_name:
                    detail += f"; reflected_after={after_weight:.3f}; best_non_reflected={best_alt_name} {best_alt_weight:.3f}"
                trace_path = trace.get("_trace_path") or trace.get("_source_trace_path")
                trace_sha = trace.get("_trace_sha256")
                if trace_path and trace_sha:
                    detail += f"; trace={trace_path} traceSha256={trace_sha}"
                findings.append(detail)
        if findings:
            return True, "; ".join(findings[:3])
        return False, ""

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
        evidence_integrity = {
            "loss_count": len(losses),
            "losses_with_replay_json": 0,
            "losses_with_decision_trace": 0,
            "losses_with_request_legal_options": 0,
            "claims_without_evidence": [],
        }

        for battle in losses:
            replay_data = self._load_replay_json(battle.get("replay_id") or battle.get("battle_id"))
            lines = self._extract_log_lines(replay_data)
            bot_slot = self._parse_bot_slot(lines) if lines else "p1"
            battle_label = battle.get("battle_id", "unknown")
            team_counter[str(battle.get("team_file", "unknown"))] += 1
            traces = self._load_trace_files(battle.get("replay_id") or battle.get("battle_id"))
            has_request_legal_trace = any(self._trace_has_request_legal_options(trace) for trace in traces)
            if replay_data and lines:
                evidence_integrity["losses_with_replay_json"] += 1
            elif has_request_legal_trace:
                # Trace-only decision-instability findings are evidence-backed when the
                # trace includes the exact Showdown request legal option set.
                pass
            else:
                evidence_integrity["claims_without_evidence"].append({
                    "battle_id": battle_label,
                    "claim_class": "mechanics_or_strategy",
                    "reason": "no replay JSON, Showdown protocol log lines, or request-backed decision trace; battle stats may seed hypotheses but cannot support mechanics claims",
                })
            if traces:
                evidence_integrity["losses_with_decision_trace"] += 1
                if has_request_legal_trace:
                    evidence_integrity["losses_with_request_legal_options"] += 1

            for line in lines:
                if line.startswith("|poke|") and f"|{'p2' if bot_slot == 'p1' else 'p1'}|" in line:
                    species = line.split("|")[3].split(",")[0].strip()
                    if species:
                        opponent_counter[species] += 1

            team_caps = self._team_hazard_capabilities(battle.get("team_file"))
            hazard_issue, hazard_detail = self._hazard_issue(lines, bot_slot, team_caps)
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

            trace_findings, _ = self._trace_issue(traces)
            if trace_findings:
                pattern_counter["decision_instability"] += 1
                evidence_map["decision_instability"].append(f"{battle_label}: {'; '.join(trace_findings[:3])}")

            # P2: replay-grounded regret mining (chosen-move MCTS value << best legal)
            regret_flag, regret_detail = self._regret_issue(traces)
            if regret_flag:
                pattern_counter["search_regret"] += 1
                evidence_map["search_regret"].append(f"{battle_label}: {regret_detail}")

            magic_flag, magic_detail = self._magic_bounce_reflected_hazard_issue(traces)
            if magic_flag:
                pattern_counter["magic_bounce_reflected_hazard"] += 1
                evidence_map["magic_bounce_reflected_hazard"].append(f"{battle_label}: {magic_detail}")

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
            "search_regret": (
                "High-regret moves: the engine overrode its own best search line",
                "On multiple turns the move actually played had a much lower MCTS visit-value "
                "than the best legal line the search found -- the engine knew a stronger move "
                "existed but a downstream layer (or a bad opponent-set sample) selected a worse one.",
                "Audit the move-selection path between the MCTS policy and the final choice "
                "(penalty pipeline overrides, sampling quality). With FOULER_PENALTY_PIPELINE "
                "OFF this should shrink; if regret persists, the sampled opponent sets are wrong.",
            ),
            "magic_bounce_reflected_hazard": (
                "Magic Bounce reflected hazards are still being selected",
                "Decision traces show reflected status or hazard moves surviving safety filtering and being selected into Magic Bounce.",
                "Cap reflected status/hazard moves below at least one positive non-reflected option after Magic Bounce is detected; only leave them available as a last resort.",
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

        # ── Grounding: enrich opponent Pokemon with oracle data ──────
        # This prevents downstream hallucination by embedding actual
        # pokedex/moves/smogon data alongside every flagged opponent.
        grounded_opponents = []
        team_paths = self._detect_team_paths(window)
        for opp_entry in top_opponents:
            opp_name = opp_entry["pokemon"]
            grounded_entry = {
                **opp_entry,
                "grounding": _oracle.grounding_block(opp_name),
                "matchups": {},
            }
            for team_path in team_paths:
                mu = _oracle.matchup_summary(opp_name, team_path)
                if "error" not in mu:
                    team_label = Path(team_path).name
                    grounded_entry["matchups"][team_label] = {
                        "walls": mu["our_walls"],
                        "checks": mu["our_checks"],
                        "threatened": mu["our_threatened"],
                    }
            grounded_opponents.append(grounded_entry)

        # Ground our own teams too
        grounded_teams = {}
        for team_path in team_paths:
            team_label = Path(team_path).name
            try:
                profile = _oracle.team_profile(team_path)
                grounded_teams[team_label] = [
                    {
                        "name": m.get("name", "?"),
                        "types": m.get("types", []),
                        "ability": m.get("ability", ""),
                        "item": m.get("item", ""),
                        "moves": [
                            mv["name"] if isinstance(mv, dict) else mv
                            for mv in m.get("moves", [])
                        ],
                    }
                    for m in profile
                ]
            except Exception:
                pass

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "battle_source": self.last_battle_source,
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
            "top_opponent_pokemon": grounded_opponents,
            "grounded_context": {
                "source": "data/pokedex_oracle.py — all facts from pokedex.json, moves.json, smogon_stats_cache",
                "our_teams": grounded_teams,
            },
            "evidence_integrity": evidence_integrity,
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
        integrity = report.get("evidence_integrity") or {}
        if integrity:
            lines.append("## Evidence integrity")
            lines.append(
                f"Replay-backed losses: {integrity.get('losses_with_replay_json', 0)}/{integrity.get('loss_count', 0)}"
            )
            lines.append(
                f"Decision-trace-backed losses: {integrity.get('losses_with_decision_trace', 0)}/{integrity.get('loss_count', 0)}"
            )
            lines.append(
                f"Request-legal-option-backed losses: {integrity.get('losses_with_request_legal_options', 0)}/{integrity.get('loss_count', 0)}"
            )
            unsupported = integrity.get("claims_without_evidence") or []
            if unsupported:
                lines.append("Unsupported mechanics/strategy claims are blocked until replay or trace proof exists:")
                for item in unsupported[:5]:
                    lines.append(f"- {item.get('battle_id', 'unknown')}: {item.get('reason', 'missing evidence')}")
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
        # FOULER-COMPETITIVE-CONCEPTS-WIRE-2026-05-21: augment recommendations with strategic-concept
        # citations from data/competitive_pokemon_art (paraphrased catalog
        # grounded in "The Art Of Competitive Pokemon"). Tolerant — if the
        # hook is unavailable for any reason, the report is unchanged.
        try:
            from replay_analysis.autoresearch_concept_hook import attach_concept_citations_all, attach_concept_citations
            attach_concept_citations_all(report.get("issues", []) or [])
            if isinstance(report.get("top_issue"), dict):
                attach_concept_citations(report["top_issue"])
        except Exception:
            pass
        json_path = self.replay_dir / "autoresearch_latest.json"
        md_path = self.reports_dir / "autoresearch_latest.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(self.render_markdown(report), encoding="utf-8")

        batch_id = report.get("batch", {}).get("id", "batch-unknown")
        historical_json = self.batch_history_dir / f"{batch_id}.json"
        historical_md = self.batch_history_dir / f"{batch_id}.md"
        historical_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        historical_md.write_text(self.render_markdown(report), encoding="utf-8")
        # FOULER-HYPOTHESIS-CALL-SITE-2026-05-20: emit hypothesis records (no-op if module missing)
        try:
            _emit_hypothesis_ledger_safe(report)
        except Exception:
            pass
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


# FOULER-COMPLETION-2026-05-20: emit hypothesis records on every autoresearch write.
# Tolerant — if the ledger import fails we don't break the autoresearch run.
def _emit_hypothesis_ledger_safe(autoresearch_data):
    try:
        from replay_analysis import hypothesis_ledger as _hl
        return _hl.emit_from_autoresearch_output(autoresearch_data)
    except Exception:
        return []
