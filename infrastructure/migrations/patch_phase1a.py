#!/usr/bin/env python3
"""Phase 1a patcher: add an AST-anchored whole-function generator path to
infrastructure/improve_agent.py and make it the PRIMARY generation strategy.

The legacy unified-diff path remains as a fallback. Idempotent + anchored.
Run on JIGGLY from repo root:  python patch_phase1a.py [--check]
"""
from __future__ import annotations
import ast, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "infrastructure" / "improve_agent.py"

# --- Block A: helpers, inserted right after the MODEL = ... line. ---
ANCHOR_A = 'MODEL = os.getenv("IMPROVE_AGENT_MODEL", "claude-sonnet-4-20250514")'
BLOCK_A = ANCHOR_A + '''

# PHASE 1a: AST-anchored whole-function replacement. Asking the model for a
# unified DIFF against the 7.5k-line fp/search/main.py failed to apply ~100% of
# the time ("patch does not apply") because the model's hunk context drifts in a
# file that large -- the dominant `agent_failed` outcome that kept the loop from
# EVER reaching its acceptance gate. Instead we send the model the ENTIRE target
# function and have it return a complete REPLACEMENT function, then splice it in
# by AST node span. No hunk matching => apply rate ~0% -> ~100%.
WHOLE_FUNC_EDIT = str(os.getenv("FOULER_WHOLE_FUNC_EDIT", "1")).lower() not in {
    "0", "false", "no", "off"
}
# Default functions to target per file when the report implicates none. These
# are the decision-surface functions whose override behavior is Root #1.
DEFAULT_TARGET_FUNCS = {
    "fp/search/main.py": ["select_move_from_eval_scores"],
    "fp/search/eval.py": ["evaluate_position", "_estimate_damage_ratio"],
}

try:
    from infrastructure.whole_function_edit import (
        find_function_span as _wfe_find_span,
        splice_function as _wfe_splice,
        list_function_names as _wfe_list,
    )
except Exception:  # pragma: no cover
    _wfe_find_span = _wfe_splice = _wfe_list = None


def pick_target_function(report: dict, target_file: str, source: str) -> str | None:
    """Choose ONE function in target_file to replace. Prefer a report-implicated
    symbol that actually exists in the file; else the file's configured default."""
    if _wfe_list is None:
        return None
    present = set(_wfe_list(source))
    for sym in _implicated_symbols(report):
        if sym in present:
            return sym
    for cand in DEFAULT_TARGET_FUNCS.get(target_file, []):
        if cand in present:
            return cand
    return None


def build_whole_function_prompt(report: dict, target_file: str, func_name: str,
                                func_source: str) -> str:
    """Prompt the model to return ONE complete replacement function (no diff)."""
    top = report.get("top_issue", {})
    return textwrap.dedent(f"""\
    You are improving a competitive Pokemon gen9ou battle bot that plays
    fat/stall teams on the Pokemon Showdown ladder.
    {_ladder_line()}

    ## Top issue to fix
    {top.get('title', 'none')}
    {top.get('summary', '')}
    Recommendation: {top.get('recommendation', '')}
    Evidence:
    {chr(10).join('- ' + p for p in top_issue_evidence(top)[:5])}

    ## Engine context (IMPORTANT)
    The bot runs a Rust MCTS search. On turns where MCTS produces a DECISIVE
    visit policy, the homegrown eval/penalty layers sometimes OVERRIDE the
    search's best move -- a strict win-equity error. Prefer fixes that make the
    final decision RESPECT a decisive MCTS policy and only diverge when MCTS is
    genuinely flat. Use penalties (down-weights), never hard blocks.

    ## Function to rewrite: {func_name}  (in {target_file})
    Return the COMPLETE replacement for THIS function only -- same name, same
    signature, full body. Do NOT return a diff. Do NOT include any other
    function or any prose. Output ONLY a single ```python code block containing
    the entire function.

    Current implementation:
    ```python
    {func_source}
    ```
    """)


def generate_whole_function_replacement(report: dict, target_file: str):
    """Returns (func_name, new_source_text) or (None, None) on failure.
    new_source_text is the ENTIRE patched file content ready to write."""
    if not WHOLE_FUNC_EDIT or _wfe_splice is None:
        return None, None
    full_path = PROJECT_ROOT / target_file
    if not full_path.exists():
        return None, None
    source = full_path.read_text(encoding="utf-8")
    func_name = pick_target_function(report, target_file, source)
    if not func_name:
        print("[AGENT] whole-func: no target function resolved; falling back to diff path.")
        return None, None
    span = _wfe_find_span(source, func_name)
    if span is None:
        print(f"[AGENT] whole-func: function '{func_name}' not unique/found; fallback.")
        return None, None
    prompt = build_whole_function_prompt(report, target_file, func_name, span.source)
    print(f"[AGENT] whole-func: targeting {func_name} "
          f"(lines {span.start_line}-{span.end_line}); prompt {len(prompt)} chars. "
          f"Calling {MODEL}...")
    response = call_claude(prompt)
    print(f"[AGENT] whole-func: got response ({len(response)} chars)")
    new_source, msg = _wfe_splice(source, func_name, response)
    if new_source is None:
        print(f"[AGENT] whole-func: splice rejected: {msg}; fallback to diff path.")
        return None, None
    print(f"[AGENT] whole-func: {msg}")
    return func_name, new_source
'''

# --- Block B: in main(), try the whole-function path before the diff path. ---
ANCHOR_B = """    # 2. Pick target file and load code
    target_file = pick_target_file(report)
    print(f"[AGENT] Target file: {target_file}")
    code = read_code_file(target_file, report)"""
BLOCK_B = """    # 2. Pick target file and load code
    target_file = pick_target_file(report)
    print(f"[AGENT] Target file: {target_file}")

    # 2b. PHASE 1a: try AST-anchored whole-function replacement first (apply-rate
    # ~100% vs the diff path's ~0% on the 7.5k-line file). On success we skip the
    # diff extract/apply entirely and go straight to syntax/tests/gate.
    original_target_text = (PROJECT_ROOT / target_file).read_text(encoding="utf-8")
    wf_name, wf_source = (None, None)
    try:
        wf_name, wf_source = generate_whole_function_replacement(report, target_file)
    except Exception as _wf_exc:
        print(f"[AGENT] whole-func path errored ({_wf_exc}); using diff path.")
    if wf_source is not None:
        (PROJECT_ROOT / target_file).write_text(wf_source, encoding="utf-8")
        if not syntax_check(target_file):
            print("[AGENT] whole-func: syntax check failed. Reverting.")
            restore_file_snapshot(target_file, original_target_text)
            return 1
        if not run_tests():
            print("[AGENT] whole-func: tests failed (pre-filter). Reverting.")
            restore_file_snapshot(target_file, original_target_text)
            return 1
        accepted, detail = offline_eval_gate()
        print(f"[AGENT] whole-func eval gate verdict: ACCEPT={accepted} :: {json.dumps(detail)[:600]}")
        if not accepted:
            print("[AGENT] whole-func: eval gate REJECTED. Reverting.")
            restore_file_snapshot(target_file, original_target_text)
            return 1
        push_requested = auto_push_enabled(args.enable_git_push)
        if commit_and_push(target_file, f"{top['title']} (whole-func {wf_name})",
                           push_enabled=push_requested,
                           push_remote=args.push_remote, push_branch=args.push_branch):
            print(f"[AGENT] whole-func: committed fix to {wf_name} for: {top['title']}")
            return 0
        print("[AGENT] whole-func: commit failed; staged not pushed.")
        return 1

    code = read_code_file(target_file, report)"""


def main() -> int:
    check = "--check" in sys.argv
    src = AGENT.read_text(encoding="utf-8")

    if "generate_whole_function_replacement" in src and "PHASE 1a" in src:
        print("[phase1a] already applied (idempotent no-op).")
        return 0
    if ANCHOR_A not in src:
        print("[phase1a] ERROR: anchor A (MODEL=) not found.", file=sys.stderr)
        return 11
    if ANCHOR_B not in src:
        print("[phase1a] ERROR: anchor B (pick target file block) not found.", file=sys.stderr)
        return 12

    src = src.replace(ANCHOR_A, BLOCK_A, 1)
    src = src.replace(ANCHOR_B, BLOCK_B, 1)
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"[phase1a] ERROR: patched improve_agent.py does not parse: {e}", file=sys.stderr)
        return 13
    if check:
        print("[phase1a] CHECK ok: would apply helpers + main() whole-func path.")
        return 0
    AGENT.write_text(src, encoding="utf-8")
    print("[phase1a] applied: whole-function generator wired as primary path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
