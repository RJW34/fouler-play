#!/usr/bin/env python3
"""
Batch-triggered coding agent — the recursive improvement step.

Called after each batch completes.  Reads the latest autoresearch report
(with grounding blocks from PokedexOracle), picks the top issue, asks
Claude to write ONE targeted fix, applies it, runs tests, and commits
if passing.  The ELO watchdog reverts if the fix hurts.

Usage:
    python infrastructure/improve_agent.py          # normal run
    python infrastructure/improve_agent.py --dry-run  # show what would change, don't apply
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

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


def load_autoresearch() -> dict:
    if not AUTORESEARCH_PATH.exists():
        return {}
    return json.loads(AUTORESEARCH_PATH.read_text(encoding="utf-8"))


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


def read_code_file(rel_path: str) -> str:
    """Read a code file, truncated to MAX_CODE_LINES from the most relevant section."""
    full_path = PROJECT_ROOT / rel_path
    if not full_path.exists():
        return f"# File not found: {rel_path}"
    lines = full_path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= MAX_CODE_LINES:
        return "\n".join(lines)
    # For large files, return the last MAX_CODE_LINES (penalty pipeline is at the end)
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
    {chr(10).join('- ' + p for p in top_issue.get('proof', [])[:5])}

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
    - Output ONLY a unified diff (--- a/{target_file} / +++ b/{target_file}).
    - Do not add new files. Do not modify files outside {target_file}.
    - Keep changes under 50 lines.
    - Penalties, not blocks — reduce move weights, never remove options entirely.
    - The bot must play fat/stall faithfully, not cheese.

    Output the diff and nothing else.
    """)


def call_claude(prompt: str) -> str:
    """Call Claude API and return the response text."""
    import anthropic
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def extract_diff(response: str) -> str:
    """Extract the unified diff from Claude's response."""
    lines = response.strip().splitlines()
    diff_lines = []
    in_diff = False
    for line in lines:
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            in_diff = True
        if in_diff:
            diff_lines.append(line)
        # Also capture lines starting with +/- when in diff context
        elif diff_lines and (line.startswith("+") or line.startswith("-") or line.startswith(" ")):
            diff_lines.append(line)
    return "\n".join(diff_lines) if diff_lines else ""


def apply_diff(diff_text: str, target_file: str) -> bool:
    """Apply a unified diff to the target file. Returns True on success."""
    diff_path = PROJECT_ROOT / ".agent_diff.patch"
    diff_path.write_text(diff_text, encoding="utf-8")
    try:
        result = subprocess.run(
            ["git", "apply", "--check", str(diff_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[AGENT] Diff doesn't apply cleanly: {result.stderr}")
            return False
        subprocess.run(
            ["git", "apply", str(diff_path)],
            cwd=str(PROJECT_ROOT),
            check=True,
        )
        return True
    except Exception as e:
        print(f"[AGENT] Failed to apply diff: {e}")
        return False
    finally:
        diff_path.unlink(missing_ok=True)


def run_tests() -> bool:
    """Run the test suite. Returns True if all pass."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    print(f"[AGENT] Tests: {result.stdout.strip().splitlines()[-1] if result.stdout.strip() else 'no output'}")
    return result.returncode == 0


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


def commit_and_push(target_file: str, issue_title: str) -> bool:
    """Commit the change and push to origin."""
    try:
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
        subprocess.run(
            ["git", "push", "origin", "master"],
            cwd=str(PROJECT_ROOT),
            check=True,
        )
        print("[AGENT] Committed and pushed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[AGENT] Git failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Batch-triggered coding agent")
    parser.add_argument("--dry-run", action="store_true", help="Show diff but don't apply")
    args = parser.parse_args()

    print(f"[AGENT] {datetime.now().isoformat()} — Starting improvement cycle")

    # 1. Load autoresearch report
    report = load_autoresearch()
    if not report or not report.get("top_issue"):
        print("[AGENT] No autoresearch report or no issues found. Skipping.")
        return

    top = report["top_issue"]
    print(f"[AGENT] Top issue: {top['title']}")
    print(f"[AGENT] Evidence: {top.get('evidence_count', 0)} battles")

    # 2. Pick target file and load code
    target_file = pick_target_file(report)
    print(f"[AGENT] Target file: {target_file}")
    code = read_code_file(target_file)

    # 3. Build prompt and call Claude
    prompt = build_prompt(report, code, target_file)
    print(f"[AGENT] Prompt built ({len(prompt)} chars). Calling {MODEL}...")

    if args.dry_run:
        print("[AGENT] DRY RUN — would send prompt to Claude. Exiting.")
        print(f"[AGENT] Prompt preview (first 500 chars):\n{prompt[:500]}")
        return

    response = call_claude(prompt)
    print(f"[AGENT] Got response ({len(response)} chars)")

    # 4. Extract and validate diff
    diff_text = extract_diff(response)
    if not diff_text:
        print("[AGENT] No valid diff in response. Skipping.")
        print(f"[AGENT] Response preview: {response[:300]}")
        return

    print(f"[AGENT] Diff extracted ({len(diff_text.splitlines())} lines)")

    # 5. Apply diff
    if not apply_diff(diff_text, target_file):
        print("[AGENT] Diff failed to apply. Skipping.")
        return

    # 6. Syntax check
    if not syntax_check(target_file):
        print("[AGENT] Syntax check failed. Reverting.")
        subprocess.run(["git", "checkout", target_file], cwd=str(PROJECT_ROOT))
        return

    # 7. Run tests
    if not run_tests():
        print("[AGENT] Tests failed. Reverting.")
        subprocess.run(["git", "checkout", target_file], cwd=str(PROJECT_ROOT))
        return

    # 8. Commit and push
    if commit_and_push(target_file, top["title"]):
        print(f"[AGENT] Successfully deployed fix for: {top['title']}")
    else:
        print("[AGENT] Commit/push failed. Change is staged but not pushed.")


if __name__ == "__main__":
    main()
