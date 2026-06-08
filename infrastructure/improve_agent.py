#!/usr/bin/env python3
"""
Batch-triggered coding agent — the recursive improvement step.

Called after each batch completes.  Reads the latest autoresearch report
(with grounding blocks from PokedexOracle), picks the top issue, asks
Claude to write ONE targeted fix, applies it, runs tests, and commits
if passing.  The ELO watchdog reverts if the fix hurts.

Usage:
    python infrastructure/improve_agent.py --enable-auto-improve
    python infrastructure/improve_agent.py --dry-run  # show what would change, don't apply
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.devstream_runtime_lease import RUNTIME_LEASE_PATH_ENV, validate_runtime_lease

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

AUTORESEARCH_PATH = PROJECT_ROOT / "replay_analysis" / "autoresearch_latest.json"
GUARDRAILS_PATH = PROJECT_ROOT / "infrastructure" / "guardrails.json"

# Files the agent is allowed to modify
ALLOWED_TARGETS = [
    "fp/search/main.py",
    "fp/search/eval.py",
    "fp/search/forced_lines.py",
    "fp/search/endgame.py",
    "fp/playstyle_config.py",
    "fp/team_analysis.py",
    "fp/opponent_model.py",
]

# Max lines of code context to send (keep prompt focused)
MAX_CODE_LINES = 500

MODEL = os.getenv("IMPROVE_AGENT_MODEL", "claude-sonnet-4-20250514")
BATTLE_ID_RE = re.compile(r"\b(?:battle-)?gen\d+[a-z0-9]*-\d+(?:-[a-z0-9]+)?\b", re.IGNORECASE)
MECHANICS_TERMS_RE = re.compile(
    r"\b(type|types|ability|abilities|move|moves|damage|weak|resist|immune|immunity|speed|hazard|terrain|weather)\b",
    re.IGNORECASE,
)
TRUSTED_GROUNDING_SOURCE_RE = re.compile(
    r"\b(showdown|replay|trace|pokedex|oracle|poke[-_ ]?engine|smogon|protocol|team\s*file|data/)\b",
    re.IGNORECASE,
)
UNTRUSTED_GROUNDING_SOURCE_RE = re.compile(
    r"\b(llm|model|claude|chatgpt|memory|assumption|prose|opinion|guess)\b",
    re.IGNORECASE,
)
TRACE_ONLY_DECISION_RE = re.compile(
    r"\b(decision[_ -]?instability|decision trace|fallback|timeout|repeated same action|loop)\b",
    re.IGNORECASE,
)
MECHANICS_OR_MATCHUP_RE = re.compile(
    r"\b(type|ability|damage|weak|resist|immune|immunity|terrain|weather|tera|hazard pressure|speed tier|coverage)\b",
    re.IGNORECASE,
)
SOURCE_POLICY_TARGET_RE = re.compile(
    r"^(?:fp/(?:search|eval|policy)/|fp/(?:hybrid_policy|run_battle)\.py)",
    re.IGNORECASE,
)
REPLAY_PROTOCOL_EVIDENCE_RE = re.compile(
    r"(\|request\||\|move\||\|switch\||\|turn\||\|win\||showdown[-_ ]?request|showdown[-_ ]?protocol|replay[_ -]?json|requesthash)",
    re.IGNORECASE,
)
LEGAL_OPTION_EVIDENCE_RE = re.compile(
    r"(\|request\||showdown[-_ ]?request|battle[_ -]?request|requesthash|legal[_ -]?options?|legal[_ -]?moves?|legal[_ -]?switch(?:es)?|candidate[_ -]?set)",
    re.IGNORECASE,
)
REQUEST_HASH_RE = re.compile(r"\brequestHash=([a-f0-9]{64})\b", re.IGNORECASE)
LEGAL_COUNT_RE = re.compile(r"\blegal(?:Moves|Switches)=(\d+)\b")

# --- Deploy-spacing + deploy-record (Phase D improvement-loop hardening) ---
# The bot applies a diff to live files at commit time, so each accepted change is
# immediately in play. Without spacing, multiple unvalidated changes stack faster
# than elo_watchdog can attribute ELO to any one of them -> the loop "changes a lot
# but never learns". We (a) refuse to ship a new change until the previous one has
# had min_games_between_deploys live games, and (b) record a deploy entry so the
# watchdog has something to revert.
BATTLE_STATS_PATH = PROJECT_ROOT / "battle_stats.json"
DEPLOY_LOG_PATH = PROJECT_ROOT / "infrastructure" / "deploy_log.json"
AUTO_IMPROVE_SENTINEL = "FOULER_PLAY_ENABLE_AUTO_IMPROVE"
AUTO_PUSH_SENTINEL = "FOULER_PLAY_ENABLE_AUTO_PUSH"
PUSH_REMOTE_ENV = "IMPROVE_AGENT_PUSH_REMOTE"
PUSH_BRANCH_ENV = "IMPROVE_AGENT_PUSH_BRANCH"
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
DEPLOY_WIN_RATE_SAMPLE_BATTLES = 30


def env_flag_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUTHY_ENV_VALUES


def auto_improve_enabled(cli_enabled: bool = False) -> bool:
    return bool(cli_enabled or env_flag_enabled(AUTO_IMPROVE_SENTINEL))


def auto_push_enabled(cli_enabled: bool = False) -> bool:
    return bool(cli_enabled or env_flag_enabled(AUTO_PUSH_SENTINEL))


def explicit_push_target(push_remote: str | None = None, push_branch: str | None = None) -> tuple[str, str]:
    remote = (push_remote or os.getenv(PUSH_REMOTE_ENV) or "").strip()
    branch = (push_branch or os.getenv(PUSH_BRANCH_ENV) or "").strip()
    if not remote or not branch:
        raise ValueError(
            f"git push requires explicit --push-remote/--push-branch or "
            f"{PUSH_REMOTE_ENV}/{PUSH_BRANCH_ENV}; no default push target is allowed"
        )
    if remote.lower() == "origin" and branch.lower() == "master":
        raise ValueError("refusing unsafe push target origin master")
    return remote, branch


def load_guardrails() -> dict:
    try:
        return json.loads(GUARDRAILS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def min_games_between_deploys() -> int:
    safety = load_guardrails().get("safety", {})
    try:
        return int(safety.get("min_games_between_deploys", 15))
    except (TypeError, ValueError):
        return 15


def _load_battles() -> list:
    try:
        data = json.loads(BATTLE_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("battles", [])
    return data if isinstance(data, list) else []


def _latest_deploy_timestamp() -> str | None:
    try:
        log = json.loads(DEPLOY_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(log, list):
        return None
    deploys = [e for e in log if isinstance(e, dict) and e.get("type") == "deploy"]
    return deploys[-1].get("timestamp") if deploys else None


def games_since_last_deploy() -> int:
    """Count battles recorded after the most recent deploy entry."""
    battles = _load_battles()
    ts = _latest_deploy_timestamp()
    if not ts:
        return len(battles)  # no prior deploy on record -> nothing gating us
    try:
        cutoff = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return len(battles)
    count = 0
    for b in battles:
        bt = b.get("timestamp") or b.get("time") or ""
        try:
            if datetime.fromisoformat(bt) > cutoff:
                count += 1
        except (ValueError, TypeError):
            continue
    return count


def current_win_rate_snapshot(battles: list, sample_size: int = DEPLOY_WIN_RATE_SAMPLE_BATTLES) -> tuple[float | None, int]:
    """Return a recent decisive-battle win-rate snapshot for deploy attribution."""
    decisive = [b for b in battles if isinstance(b, dict) and b.get("result") in ("win", "loss")]
    if sample_size > 0:
        decisive = decisive[-sample_size:]
    if not decisive:
        return None, 0
    wins = sum(1 for b in decisive if b.get("result") == "win")
    return wins / len(decisive), len(decisive)


def record_deploy(pre_commit: str, post_commit: str) -> None:
    """Append a deploy entry so elo_watchdog can attribute/revert and spacing is tracked."""
    try:
        log = []
        if DEPLOY_LOG_PATH.exists():
            try:
                log = json.loads(DEPLOY_LOG_PATH.read_text(encoding="utf-8"))
            except Exception:
                log = []
        if not isinstance(log, list):
            log = []
        battles = _load_battles()
        elo = None
        if battles:
            last = battles[-1]
            elo = last.get("elo", last.get("rating"))
        win_rate_at_deploy, win_rate_sample = current_win_rate_snapshot(battles)
        log.append({
            "timestamp": datetime.now().isoformat(),
            "type": "deploy",
            "pre_commit": pre_commit or "unknown",
            "post_commit": post_commit or "unknown",
            "elo_at_deploy": elo,
            "win_rate_at_deploy": win_rate_at_deploy,
            "win_rate_sample_at_deploy": win_rate_sample,
            "source": "improve_agent",
        })
        DEPLOY_LOG_PATH.write_text(json.dumps(log, indent=2), encoding="utf-8")
        wr_text = f"{win_rate_at_deploy:.1%}" if win_rate_at_deploy is not None else "unknown"
        print(
            f"[AGENT] Recorded deploy entry (post={str(post_commit)[:8]}, "
            f"elo_at_deploy={elo}, win_rate_at_deploy={wr_text}, sample={win_rate_sample})."
        )
    except Exception as e:
        print(f"[AGENT] WARN: failed to record deploy entry: {e}")


def load_autoresearch() -> dict:
    if not AUTORESEARCH_PATH.exists():
        return {}
    return json.loads(AUTORESEARCH_PATH.read_text(encoding="utf-8"))


def text_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]


def top_issue_evidence(top_issue: dict) -> list[str]:
    return text_list(top_issue.get("proof") or top_issue.get("evidence"))


def battle_ids_from_evidence(evidence: list[str]) -> list[str]:
    seen: set[str] = set()
    battle_ids: list[str] = []
    for item in evidence:
        for match in BATTLE_ID_RE.finditer(item):
            battle_id = match.group(0)
            if battle_id.lower() in seen:
                continue
            seen.add(battle_id.lower())
            battle_ids.append(battle_id)
    return battle_ids


def has_replay_protocol_evidence(report: dict, proof: list[str]) -> bool:
    """Return true only when the report includes falsifiable Showdown request/protocol/replay truth."""
    for key in ("request", "battle_request", "battleRequest", "replay_json", "replayJson"):
        if report.get(key):
            return True
    evidence_blob = "\n".join(proof)
    for key in ("protocol_lines", "protocolLines", "showdown_protocol", "showdownProtocol", "request", "battle_request", "battleRequest", "replay_json", "replayJson"):
        value = report.get(key)
        if value:
            evidence_blob += "\n" + json.dumps(value, sort_keys=True)
    grounded = report.get("grounded_context") if isinstance(report.get("grounded_context"), dict) else {}
    evidence_blob += "\n" + str(grounded.get("source") or "")
    return bool(REPLAY_PROTOCOL_EVIDENCE_RE.search(evidence_blob))


def has_request_legal_option_evidence(report: dict, proof: list[str]) -> bool:
    """Return true only when current Showdown request/legal-option evidence bounds policy edits."""
    def positive_int(value: object) -> bool:
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return False

    def raw_showdown_request_has_legal_options(value: dict) -> bool:
        active = value.get("active") if isinstance(value.get("active"), list) else []
        legal_move_count = 0
        for request in active:
            if not isinstance(request, dict):
                continue
            moves = request.get("moves") if isinstance(request.get("moves"), list) else []
            legal_move_count += sum(1 for move in moves if isinstance(move, dict) and move.get("disabled") is not True)
        side = value.get("side") if isinstance(value.get("side"), dict) else {}
        side_pokemon = side.get("pokemon") if isinstance(side.get("pokemon"), list) else []
        legal_switch_count = sum(
            1
            for mon in side_pokemon
            if isinstance(mon, dict)
            and mon.get("active") is not True
            and not str(mon.get("condition") or "").startswith("0 fnt")
        )
        return bool(active or side_pokemon) and (
            legal_move_count > 0
            or legal_switch_count > 0
            or "forceSwitch" in value
            or "wait" in value
        )

    def structured(value: object) -> bool:
        if isinstance(value, dict):
            if raw_showdown_request_has_legal_options(value):
                return True
            request_hash = value.get("requestHash")
            has_request_hash = isinstance(request_hash, str) and re.fullmatch(r"[a-f0-9]{64}", request_hash, re.IGNORECASE)
            legal_moves = value.get("legalMoves") or value.get("legal_moves")
            legal_switches = value.get("legalSwitches") or value.get("legal_switches")
            candidate_bounded = value.get("candidateSetBounded") is True or value.get("candidate_set_bounded") is True
            if has_request_hash and candidate_bounded and (
                (isinstance(legal_moves, list) and bool(legal_moves))
                or (isinstance(legal_switches, list) and bool(legal_switches))
                or value.get("forceSwitch") is not None
                or value.get("wait") is not None
            ):
                return True
            return any(structured(child) for child in value.values())
        if isinstance(value, list):
            return any(structured(child) for child in value)
        return False

    def text_has_showdown_request_protocol(text: str) -> bool:
        for line in text.splitlines():
            if "|request|" not in line:
                continue
            raw = line.split("|request|", 1)[1].strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and raw_showdown_request_has_legal_options(payload):
                return True
        return False

    for key in ("request", "battle_request", "battleRequest", "legal_options", "legalOptions", "candidate_set", "candidateSet"):
        if structured(report.get(key)):
            return True
    for key in ("protocol_lines", "protocolLines", "showdown_protocol", "showdownProtocol"):
        value = report.get(key)
        if isinstance(value, list) and any(text_has_showdown_request_protocol(str(item)) for item in value):
            return True
        if isinstance(value, str) and text_has_showdown_request_protocol(value):
            return True
    integrity = report.get("evidence_integrity") if isinstance(report.get("evidence_integrity"), dict) else {}
    if not positive_int(integrity.get("losses_with_request_legal_options")):
        return False
    evidence_blob = "\n".join(proof)
    for key in (
        "protocol_lines",
        "protocolLines",
        "showdown_protocol",
        "showdownProtocol",
        "request",
        "battle_request",
        "battleRequest",
        "legal_options",
        "legalOptions",
        "legal_moves",
        "legalMoves",
        "legal_switches",
        "legalSwitches",
        "candidate_set",
        "candidateSet",
    ):
        value = report.get(key)
        if value:
            evidence_blob += "\n" + json.dumps(value, sort_keys=True)
    if not LEGAL_OPTION_EVIDENCE_RE.search(evidence_blob):
        return False
    if not REQUEST_HASH_RE.search(evidence_blob):
        return False
    return any(int(match.group(1)) > 0 for match in LEGAL_COUNT_RE.finditer(evidence_blob))


def validate_autoresearch_for_improvement(report: dict) -> list[str]:
    """Require replay/protocol-grounded evidence before a coding agent can patch."""
    blockers: list[str] = []
    top = report.get("top_issue", {})
    if not isinstance(top, dict) or not top:
        return ["autoresearch report has no top_issue"]
    proof = top_issue_evidence(top)
    battle_ids = battle_ids_from_evidence(proof)
    batch = report.get("batch") if isinstance(report.get("batch"), dict) else {}
    grounded = report.get("grounded_context") if isinstance(report.get("grounded_context"), dict) else {}
    source_contract = str(grounded.get("source") or "")
    evidence_integrity = report.get("evidence_integrity") if isinstance(report.get("evidence_integrity"), dict) else {}
    mechanics_text = "\n".join([
        str(top.get("key") or ""),
        str(top.get("title") or ""),
        str(top.get("summary") or ""),
        str(top.get("recommendation") or ""),
        "\n".join(proof),
    ])
    trace_only_issue = bool(TRACE_ONLY_DECISION_RE.search(mechanics_text)) and not bool(MECHANICS_OR_MATCHUP_RE.search(mechanics_text))
    if not proof:
        blockers.append("top_issue has no proof/evidence strings")
    if proof and not battle_ids:
        blockers.append("top_issue proof is not linked to Showdown battle ids")
    if not report.get("generated_at") and not report.get("generatedAt"):
        blockers.append("autoresearch report has no generated_at timestamp")
    if not batch.get("id"):
        blockers.append("autoresearch report has no batch id")
    if source_contract and UNTRUSTED_GROUNDING_SOURCE_RE.search(source_contract):
        blockers.append("grounded_context.source is not a trusted non-LLM authority")
    if MECHANICS_TERMS_RE.search(mechanics_text):
        if not source_contract:
            blockers.append("mechanics-adjacent issue lacks grounded_context.source")
        elif not TRUSTED_GROUNDING_SOURCE_RE.search(source_contract):
            blockers.append("mechanics-adjacent issue lacks trusted Showdown/oracle/engine source")
        if not has_replay_protocol_evidence(report, proof):
            blockers.append("mechanics/policy issue lacks replay/protocol evidence")
    if report.get("unsupported_mechanics_claims"):
        blockers.append("autoresearch contains unsupported mechanics claims")
    if evidence_integrity.get("claims_without_evidence") and not trace_only_issue:
        blockers.append("evidence_integrity reports claims without replay/trace evidence")
    target_file = pick_target_file(report)
    if trace_only_issue and SOURCE_POLICY_TARGET_RE.search(target_file) and not has_request_legal_option_evidence(report, proof):
        blockers.append(
            f"trace-only decision issue cannot target {target_file} without current Showdown request-backed legal-option evidence"
        )
    if any(
        isinstance(item, dict) and str(item.get("status") or "").lower() == "rejected"
        for item in report.get("mechanics_claims", [])
    ):
        blockers.append("autoresearch contains rejected mechanics claims")
    return blockers


def pick_target_file(report: dict) -> str:
    """Pick which code file to send based on the top issue."""
    top = report.get("top_issue", {})
    key = top.get("key", "")
    # Route issues to the most relevant file
    if key in ("hazard_pressure", "early_bleeding"):
        return "fp/search/eval.py"
    if key == "endgame_conversion":
        return "fp/search/endgame.py"
    # Default: the penalty pipeline
    return "fp/search/main.py"


FUNC_NAME_RE = re.compile(r"\b([a-z_][a-z0-9_]{3,})\s*\(", re.IGNORECASE)


def _implicated_symbols(report: dict) -> list[str]:
    """
    Extract candidate function/symbol names the report implicates, so we can send
    the agent the SPECIFIC functions instead of a blind 500-line tail. Looks at the
    top issue title/proof and any explicit `target_symbols`/`functions` fields.
    """
    top = report.get("top_issue", {}) if isinstance(report, dict) else {}
    names: list[str] = []
    for key in ("target_symbols", "functions", "implicated_functions"):
        val = report.get(key) or top.get(key)
        if isinstance(val, (list, tuple)):
            names.extend(str(v) for v in val)
        elif isinstance(val, str):
            names.append(val)
    blob = " ".join(
        [str(top.get("title", "")), " ".join(text_list(top.get("proof") or top.get("evidence")))]
    )
    # snake_case identifiers that look like function calls
    for m in FUNC_NAME_RE.finditer(blob):
        cand = m.group(1)
        if "_" in cand or cand.islower():
            names.append(cand)
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        nl = n.strip()
        if nl and nl.lower() not in seen:
            seen.add(nl.lower())
            out.append(nl)
    return out


def _extract_functions_from_source(source: str, wanted: list[str]) -> str:
    """Return the source of the named top-level/methods functions (with a little
    surrounding context), using AST line spans. Empty string if none matched."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    lines = source.splitlines()
    wanted_set = {w.lower() for w in wanted}
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.lower() in wanted_set:
                start = max(0, (node.lineno - 1) - 2)  # include 2 lines of context
                end = getattr(node, "end_lineno", node.lineno)
                spans.append((start, end, node.name))
    if not spans:
        return ""
    spans.sort()
    chunks: list[str] = []
    for start, end, name in spans:
        body = "\n".join(lines[start:end])
        chunks.append(f"# ---- function: {name} (lines {start + 1}-{end}) ----\n{body}")
    return "\n\n".join(chunks)


def read_code_file(rel_path: str, report: dict | None = None) -> str:
    """
    Read a code file for the agent prompt.

    For small files, return the whole file. For large files (e.g. the 7k-line
    fp/search/main.py), extract the SPECIFIC functions implicated by the report
    instead of blindly tailing MAX_CODE_LINES (which sent the agent the wrong
    region -- the penalty pipeline tail -- regardless of the actual issue).
    Falls back to the tail only if no implicated function is found.
    """
    full_path = PROJECT_ROOT / rel_path
    if not full_path.exists():
        return f"# File not found: {rel_path}"
    source = full_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    if len(lines) <= MAX_CODE_LINES:
        return "\n".join(lines)

    if report is not None:
        wanted = _implicated_symbols(report)
        extracted = _extract_functions_from_source(source, wanted)
        if extracted:
            header = (
                f"# NOTE: {rel_path} is {len(lines)} lines. Showing the functions "
                f"implicated by the top issue ({', '.join(wanted[:6])}). Edit ONLY "
                f"these unless you have strong evidence the fix belongs elsewhere.\n"
            )
            # Guard prompt size: cap extracted region.
            ex_lines = extracted.splitlines()
            if len(ex_lines) > MAX_CODE_LINES * 3:
                ex_lines = ex_lines[: MAX_CODE_LINES * 3]
                extracted = "\n".join(ex_lines) + "\n# ...(truncated)..."
            return header + extracted

    # Fallback: last MAX_CODE_LINES.
    return "\n".join(lines[-MAX_CODE_LINES:])


def build_prompt(report: dict, code: str, target_file: str) -> str:
    """Build the focused prompt for the coding agent."""
    top_issue = report.get("top_issue", {})
    grounded = report.get("grounded_context", {})
    opponents = report.get("top_opponent_pokemon", [])

    # Build opponent grounding section
    opponent_section = ""
    for opp in opponents[:3]:
        g = opp.get("grounding", {})
        if "error" in g:
            continue
        matchups = opp.get("matchups", {})
        opponent_section += f"\n### {g.get('pokemon', opp['pokemon'])} (seen in {opp['count']} losses)\n"
        opponent_section += f"Types: {g.get('types', [])}\n"
        opponent_section += f"Abilities: {g.get('abilities', {})}\n"
        moves_str = ", ".join(
            f"{m['name']} ({m.get('type','?')}/{m.get('basePower',0)}bp/{m.get('category','?')}, {m.get('usage_pct',0)}%)"
            for m in g.get("common_moves", [])[:6]
        )
        opponent_section += f"Common moves: {moves_str}\n"
        for team_name, mu in matchups.items():
            opponent_section += f"  vs {team_name}: walls={mu.get('walls',[])}, checks={mu.get('checks',[])}, threatened={mu.get('threatened',[])}\n"

    # Build our teams section
    teams_section = ""
    for team_name, mons in grounded.get("our_teams", {}).items():
        teams_section += f"\n### {team_name}\n"
        for mon in mons:
            moves = ", ".join(mon.get("moves", []))
            teams_section += f"- {mon['name']} ({'/'.join(mon.get('types',[]))}) [{mon.get('ability','')}] @ {mon.get('item','')}: {moves}\n"

    return textwrap.dedent(f"""\
    You are improving a competitive Pokemon gen9ou battle bot.
    The bot plays fat/stall teams on Pokemon Showdown ladder.
    Current ELO: ~1359, target: 1700.

    ## Autoresearch Report (latest batch)
    Record: {report.get('wins',0)}-{report.get('losses',0)} ({report.get('win_rate',0):.1%} WR)

    ### Top Issue: {top_issue.get('title', 'none')}
    {top_issue.get('summary', '')}
    Recommendation: {top_issue.get('recommendation', '')}
    Evidence:
    {chr(10).join('- ' + p for p in top_issue_evidence(top_issue)[:5])}

    ## Grounded Opponent Data (from pokedex.json + moves.json + Smogon stats)
    {opponent_section}

    ## Our Teams (from team files)
    {teams_section}

    ## Code to modify: {target_file}
    ```python
    {code}
    ```

    ## Your task
    Make ONE targeted change to the code above that addresses the top issue.
    The change should be small, focused, and testable.

    CRITICAL RULES:
    - Use ONLY the Pokemon data provided above. Do NOT use your own knowledge of Pokemon types, abilities, or moves.
    - The type chart, move effects, and ability interactions above are from the authoritative data files.
    - Treat prose recommendations as advisory. The replay/trace proof and local oracle data are the only sources of truth.
    - Output ONLY a unified diff (--- a/{target_file} / +++ b/{target_file}).
    - Do not add new files. Do not modify files outside {target_file}.
    - Keep changes under 50 lines.
    - Penalties, not blocks — reduce move weights, never remove options entirely.
    - The bot must play fat/stall faithfully, not cheese.

    Output the diff and nothing else.
    """)


def _find_claude_cli() -> str | None:
    """Locate the `claude` CLI (Max OAuth path). No ANTHROPIC_API_KEY needed."""
    override = os.getenv("IMPROVE_AGENT_CLAUDE_CLI")
    if override and Path(override).exists():
        return override
    from shutil import which
    found = which("claude")
    if found:
        return found
    # Common per-user install locations on the devstream hosts.
    candidates = [
        Path.home() / ".local" / "bin" / "claude.exe",   # JIGGLYPUFF (Windows)
        Path.home() / ".local" / "bin" / "claude",        # ubunztu / DEKU (Linux)
        Path("/usr/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _call_claude_cli(prompt: str, cli_path: str) -> str:
    """Drive the `claude` CLI in headless print mode (uses the host's Max OAuth login).

    This is the autonomous path on the devstream hosts: no ANTHROPIC_API_KEY is
    present, but `claude -p` authenticates via the already-installed Max OAuth
    credentials that HERMES uses. The prompt is fed on stdin so it never hits
    argv length limits.
    """
    cli_model = os.getenv("IMPROVE_AGENT_CLI_MODEL", "sonnet")
    cmd = [cli_path, "-p", "--model", cli_model]
    result = subprocess.run(
        cmd,
        input=prompt,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=int(os.getenv("IMPROVE_AGENT_CLI_TIMEOUT", "300")),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: {result.stderr.strip()[:500]}"
        )
    out = (result.stdout or "").strip()
    if not out:
        raise RuntimeError("claude CLI returned empty output")
    return out


def _call_claude_sdk(prompt: str) -> str:
    """API-key path. Only used when ANTHROPIC_API_KEY is explicitly set."""
    import anthropic
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def call_claude(prompt: str) -> str:
    """Return Claude's response text via whichever auth path is available.

    Order of preference:
      1. `claude` CLI subprocess (Max OAuth) — the autonomous path on the
         devstream hosts; needs NO ANTHROPIC_API_KEY. This is what lets the
         self-improvement loop run unattended on JIGGLYPUFF/DEKU.
      2. anthropic SDK — only when ANTHROPIC_API_KEY is actually set.

    The CLI is preferred so the loop works on machines that have a Max login
    but no API key. If both paths are unavailable we raise a clear, actionable
    error instead of crashing on a bare `import anthropic`.
    """
    prefer_sdk = bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv(
        "IMPROVE_AGENT_PREFER_SDK", ""
    ).lower() in ("1", "true", "yes")

    cli_path = _find_claude_cli()
    if cli_path and not prefer_sdk:
        try:
            return _call_claude_cli(prompt, cli_path)
        except Exception as cli_err:
            print(f"[AGENT] claude CLI path failed ({cli_err}); trying SDK fallback.")
            if os.getenv("ANTHROPIC_API_KEY"):
                return _call_claude_sdk(prompt)
            raise

    if os.getenv("ANTHROPIC_API_KEY"):
        return _call_claude_sdk(prompt)

    if cli_path:
        # prefer_sdk was set but no key; fall back to the CLI anyway.
        return _call_claude_cli(prompt, cli_path)

    raise RuntimeError(
        "No LLM path available: the `claude` CLI was not found on PATH and "
        "ANTHROPIC_API_KEY is not set. Install/login the Claude CLI "
        "(`claude` on PATH, Max OAuth) or set ANTHROPIC_API_KEY."
    )


def extract_diff(response: str) -> str:
    """Extract the unified diff from Claude's response.

    Robust against the model wrapping the diff in a fenced code block: a
    closing ``` fence used to be swallowed into the patch body, producing
    "corrupt patch" errors. We start at the first diff header and stop at the
    closing fence or the first prose line after the body. Blank context lines
    are normalized to a single space so git apply accepts them.
    """
    lines = response.strip().splitlines()
    diff_lines: list[str] = []
    in_diff = False
    for line in lines:
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            in_diff = True
            diff_lines.append(line)
            continue
        if not in_diff:
            continue
        if line.strip().startswith("```"):
            break
        if line == "":
            diff_lines.append(" ")
            continue
        if line[0] in (" ", "+", "-", chr(92)):
            diff_lines.append(line)
            continue
        break
    text = "\n".join(diff_lines)
    return text + "\n" if text else ""


def _diff_header_path(line: str) -> str | None:
    match = re.match(r"^(?:---|\+\+\+) (?:[ab]/)?(.+)$", line.strip())
    if not match:
        return None
    path = match.group(1).strip()
    if path == "/dev/null":
        return path
    return path.replace("\\", "/")


def validate_diff_scope(diff_text: str, target_file: str) -> list[str]:
    """Fail closed unless a unified diff only modifies target_file."""
    target = target_file.replace("\\", "/").strip()
    blockers: list[str] = []
    paths: set[str] = set()
    changed_lines = 0
    has_hunk = False
    for line in diff_text.splitlines():
        if line.startswith(("diff --git", "new file mode", "deleted file mode", "rename from", "rename to", "Binary files ")):
            blockers.append(f"unsupported diff metadata: {line[:80]}")
            continue
        header_path = _diff_header_path(line)
        if header_path:
            paths.add(header_path)
            if header_path == "/dev/null":
                blockers.append("diff creates or deletes files; only in-place target edits are allowed")
            continue
        if line.startswith("@@"):
            has_hunk = True
            continue
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            changed_lines += 1
    if not has_hunk:
        blockers.append("diff has no unified hunk")
    unexpected = sorted(path for path in paths if path != target)
    if unexpected:
        blockers.append(f"diff touches paths outside target {target}: {', '.join(unexpected)}")
    if target not in paths:
        blockers.append(f"diff does not explicitly target {target}")
    if changed_lines > 50:
        blockers.append(f"diff changes {changed_lines} lines; limit is 50")
    return blockers


def apply_diff(diff_text: str, target_file: str) -> bool:
    """Apply a unified diff to the target file. Returns True on success."""
    scope_blockers = validate_diff_scope(diff_text, target_file)
    if scope_blockers:
        print("[AGENT] Diff scope validation failed:")
        for blocker in scope_blockers:
            print(f"[AGENT] BLOCKER: {blocker}")
        return False
    diff_path = PROJECT_ROOT / ".agent_diff.patch"
    diff_path.write_text(diff_text, encoding="utf-8")
    try:
        apply_flags = ["--recount", "--whitespace=nowarn"]
        result = subprocess.run(
            ["git", "apply", "--check", *apply_flags, str(diff_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "apply", "--check", *apply_flags, "-C1", str(diff_path)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                print(f"[AGENT] Diff doesn't apply cleanly: {result.stderr}")
                return False
            apply_flags = [*apply_flags, "-C1"]
        subprocess.run(
            ["git", "apply", *apply_flags, str(diff_path)],
            cwd=str(PROJECT_ROOT),
            check=True,
        )
        return True
    except Exception as e:
        print(f"[AGENT] Failed to apply diff: {e}")
        return False
    finally:
        diff_path.unlink(missing_ok=True)


def restore_file_snapshot(target_file: str, snapshot: str) -> None:
    """Restore the one file this agent was allowed to edit without touching other dirty work."""
    full_path = PROJECT_ROOT / target_file
    full_path.write_text(snapshot, encoding="utf-8")


def run_tests() -> bool:
    """Run the test suite. Returns True if all pass."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    print(f"[AGENT] Tests: {result.stdout.strip().splitlines()[-1] if result.stdout.strip() else 'no output'}")
    return result.returncode == 0


EVAL_GATE_ENABLED = str(os.getenv("IMPROVE_AGENT_EVAL_GATE", "1")).lower() in {
    "1", "true", "yes", "on",
}
EVAL_GATE_BATTLES = int(os.getenv("IMPROVE_AGENT_EVAL_BATTLES", "200"))
EVAL_GATE_BASELINE = os.getenv("IMPROVE_AGENT_EVAL_BASELINE", "simple")
EVAL_GATE_TEAM = os.getenv("IMPROVE_AGENT_EVAL_TEAM", "gen9/ou/fat-team-1-stall")
FROZEN_BASELINE_PATH = PROJECT_ROOT / "eval_results" / "offline" / "frozen.json"


def offline_eval_gate() -> tuple[bool, dict]:
    """
    The REAL acceptance gate (P1): a candidate change is accepted only if it beats
    a FROZEN baseline by a statistically significant win-rate margin over
    N>=200-400 battles on a local poke-env + pokemon-showdown harness.

    pytest is only a cheap pre-filter (run earlier in main()); THIS is the gate.

    Returns (accepted, detail). Requires a stored frozen baseline at
    eval_results/offline/frozen.json (produce it once with offline_eval.py).
    If the eval harness is unavailable or unusable, the gate fails closed rather
    than silently passing a change to live.
    """
    import importlib.util

    if not EVAL_GATE_ENABLED:
        return True, {"skipped": "IMPROVE_AGENT_EVAL_GATE disabled"}

    eval_script = PROJECT_ROOT / "infrastructure" / "offline_eval.py"
    venv_py = PROJECT_ROOT / ".venv-eval" / "Scripts" / "python.exe"
    if not venv_py.exists():
        venv_py = PROJECT_ROOT / ".venv-eval" / "bin" / "python"
    if not eval_script.exists() or not venv_py.exists():
        detail = {
            "error": "eval_harness_unavailable",
            "eval_script_exists": eval_script.exists(),
            "venv_python_exists": venv_py.exists(),
            "eval_script": str(eval_script),
            "venv_python": str(venv_py),
            "readiness_command": f"{sys.executable} infrastructure/offline_eval_readiness.py --require-ready",
        }
        print("[AGENT] ERROR: offline eval harness/venv missing; eval gate FAIL-CLOSED "
              "(run infrastructure/offline_eval_readiness.py --require-ready for provisioning proof).")
        return False, detail

    try:
        probe = subprocess.run(
            [str(venv_py), "--version"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except Exception as exc:
        print(f"[AGENT] ERROR: eval venv python is unusable; eval gate FAIL-CLOSED: {exc}")
        return False, {
            "error": "eval_python_unusable",
            "venv_python": str(venv_py),
            "detail": str(exc),
            "readiness_command": f"{sys.executable} infrastructure/offline_eval_readiness.py --require-ready",
        }
    if probe.returncode != 0:
        print("[AGENT] ERROR: eval venv python failed --version; eval gate FAIL-CLOSED.")
        return False, {
            "error": "eval_python_unusable",
            "venv_python": str(venv_py),
            "returncode": probe.returncode,
            "stderr": (probe.stderr or "")[-500:],
            "readiness_command": f"{sys.executable} infrastructure/offline_eval_readiness.py --require-ready",
        }

    # Run the candidate arm.
    print(f"[AGENT] Running offline eval gate: {EVAL_GATE_BATTLES} battles vs "
          f"{EVAL_GATE_BASELINE} on {EVAL_GATE_TEAM} ...")
    cand_path = PROJECT_ROOT / "eval_results" / "offline" / "candidate.json"
    cand_path.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [
                str(venv_py), str(eval_script),
                "--battles", str(EVAL_GATE_BATTLES),
                "--team", EVAL_GATE_TEAM,
                "--baseline", EVAL_GATE_BASELINE,
                "--label", "candidate",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=EVAL_GATE_BATTLES * 220 + 300,
        )
    except subprocess.TimeoutExpired as exc:
        print("[AGENT] Eval gate timed out; treating as FAIL.")
        return False, {"error": "candidate eval timed out", "timeout": exc.timeout}
    print(proc.stdout[-1500:] if proc.stdout else "(no eval stdout)")
    if proc.returncode != 0:
        print("[AGENT] Eval gate command failed; treating as FAIL.")
        if proc.stderr:
            print(proc.stderr[-1500:])
        return False, {
            "error": "candidate eval failed",
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "")[-1000:],
        }
    if not cand_path.exists():
        print("[AGENT] Eval gate produced no candidate result; treating as FAIL.")
        return False, {"error": "no candidate result"}

    cand = json.loads(cand_path.read_text(encoding="utf-8"))
    # If we have a frozen reference, do the two-proportion comparison.
    if FROZEN_BASELINE_PATH.exists():
        cmp_proc = subprocess.run(
            [str(venv_py), str(eval_script), "--compare", "frozen", "candidate"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        print(cmp_proc.stdout)
        if cmp_proc.returncode != 0:
            print("[AGENT] Eval comparison failed; treating as FAIL.")
            return False, {
                "error": "frozen comparison failed",
                "returncode": cmp_proc.returncode,
                "stderr": (cmp_proc.stderr or "")[-1000:],
            }
        verdict_path = PROJECT_ROOT / "eval_results" / "offline" / "compare-frozen-vs-candidate.json"
        if verdict_path.exists():
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            return bool(verdict.get("ACCEPT", False)), verdict
        return False, {"error": "frozen comparison produced no verdict"}

    # No frozen reference yet: accept only if the candidate beats the baseline
    # with a Wilson lower bound > 0.50 (we are confident fouler wins >half).
    accepted = bool(cand.get("fouler_wilson_lcb", 0.0) > 0.50)
    return accepted, {
        "fouler_win_rate": cand.get("fouler_win_rate"),
        "fouler_wilson_lcb": cand.get("fouler_wilson_lcb"),
        "rule": "wilson_lcb_gt_0.50 (no frozen reference)",
    }


def syntax_check(target_file: str) -> bool:
    """AST parse check on the modified file."""
    import ast
    try:
        full_path = PROJECT_ROOT / target_file
        ast.parse(full_path.read_text(encoding="utf-8"))
        return True
    except SyntaxError as e:
        print(f"[AGENT] Syntax error: {e}")
        return False


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def commit_and_push(
    target_file: str,
    issue_title: str,
    *,
    push_enabled: bool = False,
    push_remote: str | None = None,
    push_branch: str | None = None,
) -> bool:
    """Commit the accepted change and optionally push to an explicit target."""
    try:
        push_target = explicit_push_target(push_remote, push_branch) if push_enabled else None
        pre_commit = _git_head()
        subprocess.run(
            ["git", "add", target_file],
            cwd=str(PROJECT_ROOT),
            check=True,
        )
        msg = (
            f"auto: {issue_title[:60]}\n\n"
            f"Automated fix from improve_agent.py based on autoresearch report.\n"
            f"Target: {target_file}\n"
            f"Timestamp: {datetime.now().isoformat()}\n\n"
            f"Co-Authored-By: Claude Sonnet 4 <noreply@anthropic.com>"
        )
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(PROJECT_ROOT),
            check=True,
        )
        post_commit = _git_head()
        # Record the deploy BEFORE the push: the diff is already applied to live
        # files, so the change is in play regardless of whether the push succeeds.
        record_deploy(pre_commit, post_commit)
        if push_target is None:
            print(
                f"[AGENT] Committed locally. Git push disabled; set {AUTO_PUSH_SENTINEL}=1 "
                f"or pass --enable-git-push with an explicit push target."
            )
            return True
        remote, branch = push_target
        subprocess.run(
            ["git", "push", remote, f"HEAD:{branch}"],
            cwd=str(PROJECT_ROOT),
            check=True,
        )
        print(f"[AGENT] Committed and pushed successfully to {remote} HEAD:{branch}.")
        return True
    except ValueError as e:
        print(f"[AGENT] Git push blocked: {e}")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[AGENT] Git failed: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-triggered coding agent")
    parser.add_argument("--dry-run", action="store_true", help="Show diff but don't apply")
    parser.add_argument(
        "--enable-auto-improve",
        action="store_true",
        help=f"Allow this agent to mutate files and commit. Alternative: {AUTO_IMPROVE_SENTINEL}=1.",
    )
    parser.add_argument("--max-cycles", type=int, default=0, help="Explicit recursive-improvement cycle bound covered by the runtime lease.")
    parser.add_argument(
        "--runtime-lease",
        help=f"Path to proof-window runtime lease JSON. Default: {RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json.",
    )
    parser.add_argument(
        "--enable-git-push",
        action="store_true",
        help=f"Allow git push after a successful local commit. Alternative: {AUTO_PUSH_SENTINEL}=1.",
    )
    parser.add_argument(
        "--push-remote",
        help=f"Explicit git remote for push; alternatively set {PUSH_REMOTE_ENV}.",
    )
    parser.add_argument(
        "--push-branch",
        help=f"Explicit git branch for push; alternatively set {PUSH_BRANCH_ENV}.",
    )
    args = parser.parse_args()

    print(f"[AGENT] {datetime.now().isoformat()} — Starting improvement cycle")

    if not args.dry_run and not auto_improve_enabled(args.enable_auto_improve):
        print(
            f"[AGENT] BLOCKED: auto-improvement is disabled. Set {AUTO_IMPROVE_SENTINEL}=1 "
            f"or pass --enable-auto-improve to allow mutation."
        )
        return 2
    if not args.dry_run:
        lease_guard = validate_runtime_lease(
            purpose="improve-agent",
            lease_path=args.runtime_lease,
            requested_max_cycles=args.max_cycles,
            require_max_cycles=True,
        )
        if not lease_guard.get("ok"):
            print("[AGENT] BLOCKED: runtime lease/proof window is required for recursive improvement.")
            for blocker in lease_guard.get("blockers") or []:
                print(f"[AGENT] BLOCKER: {blocker}")
            return 2

    # 1. Load autoresearch report
    report = load_autoresearch()
    if not report or not report.get("top_issue"):
        print("[AGENT] No autoresearch report or no issues found. Skipping.")
        return 0

    blockers = validate_autoresearch_for_improvement(report)
    if blockers:
        print("[AGENT] Autoresearch is not promotable. Skipping.")
        for blocker in blockers:
            print(f"[AGENT] BLOCKER: {blocker}")
        return 0

    # Deploy-spacing gate: don't ship another change until the previous one has had
    # enough live games to be judged by elo_watchdog. Prevents unvalidated changes
    # from stacking (the root of "edits constantly but ELO never climbs").
    min_games = min_games_between_deploys()
    since = games_since_last_deploy()
    if since < min_games:
        print(
            f"[AGENT] Deferring: only {since}/{min_games} games since last deploy. "
            f"Letting the previous change be validated (elo_watchdog) before shipping another."
        )
        return 0

    top = report["top_issue"]
    print(f"[AGENT] Top issue: {top['title']}")
    evidence = top_issue_evidence(top)
    battle_ids = battle_ids_from_evidence(evidence)
    print(f"[AGENT] Evidence: {len(battle_ids)} battle(s), {len(evidence)} evidence item(s)")

    # 2. Pick target file and load code
    target_file = pick_target_file(report)
    print(f"[AGENT] Target file: {target_file}")
    code = read_code_file(target_file, report)

    # 3. Build prompt and call Claude
    prompt = build_prompt(report, code, target_file)
    print(f"[AGENT] Prompt built ({len(prompt)} chars). Calling {MODEL}...")

    if args.dry_run:
        print("[AGENT] DRY RUN — would send prompt to Claude. Exiting.")
        print(f"[AGENT] Prompt preview (first 500 chars):\n{prompt[:500]}")
        return 0

    response = call_claude(prompt)
    print(f"[AGENT] Got response ({len(response)} chars)")

    # 4. Extract and validate diff
    diff_text = extract_diff(response)
    if not diff_text:
        print("[AGENT] No valid diff in response. Skipping.")
        print(f"[AGENT] Response preview: {response[:300]}")
        return 0

    print(f"[AGENT] Diff extracted ({len(diff_text.splitlines())} lines)")

    # 5. Apply diff
    original_target_text = (PROJECT_ROOT / target_file).read_text(encoding="utf-8")
    if not apply_diff(diff_text, target_file):
        print("[AGENT] Diff failed to apply. Skipping.")
        return 1

    # 6. Syntax check
    if not syntax_check(target_file):
        print("[AGENT] Syntax check failed. Reverting.")
        restore_file_snapshot(target_file, original_target_text)
        return 1

    # 7. Run tests — now only a CHEAP PRE-FILTER, not the acceptance gate.
    if not run_tests():
        print("[AGENT] Tests failed (pre-filter). Reverting.")
        restore_file_snapshot(target_file, original_target_text)
        return 1

    # 8. REAL acceptance gate (P1): offline win-rate eval vs frozen baseline.
    accepted, detail = offline_eval_gate()
    print(f"[AGENT] Eval gate verdict: ACCEPT={accepted} :: {json.dumps(detail)[:600]}")
    if not accepted:
        print("[AGENT] Eval gate REJECTED the change (no significant win-rate gain "
              "/ regression). Reverting.")
        restore_file_snapshot(target_file, original_target_text)
        return 1

    # 9. Commit and push
    push_requested = auto_push_enabled(args.enable_git_push)
    if commit_and_push(
        target_file,
        top["title"],
        push_enabled=push_requested,
        push_remote=args.push_remote,
        push_branch=args.push_branch,
    ):
        if push_requested:
            print(f"[AGENT] Successfully committed and pushed fix for: {top['title']}")
        else:
            print(f"[AGENT] Successfully committed local fix for: {top['title']}")
        return 0
    else:
        print("[AGENT] Commit/push failed. Change is staged but not pushed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
