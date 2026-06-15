# Fouler Runtime Recovery Proof - 2026-06-15T07:48:00Z

## Scope

Continuation of the active JIGGLYPUFF monitoring goal. Direct JIGGLY control remains unavailable, so this pass used public Showdown replay logs for current battle evidence.

## Control State

Repo-native status still fails:

- Command: `py -3 scripts/jigglypuff_devstream_control.py status`
- Result: `status=blocked`, `healthy=false`, `running=false`
- Blocker: `JIGGLYPUFF fouler runtime did not return JSON`
- Raw evidence: SSH connects to `192.168.1.126:22` but times out during banner exchange; worker/status HTTP does not return JSON.

## Replay-Backed Issue

Public replay `gen9ou-2632296483` shows a legal-but-failing recovery line:

```text
|move|p1a: Pecharunt|Recover||[still]
|-fail|p1a: Pecharunt|heal
...
|move|p1a: Pecharunt|Recover||[still]
|-fail|p1a: Pecharunt|heal
```

The later failures were under Encore, but the first full-HP recovery failure came before that lock. This is the same architecture problem as the Magic Bounce fix: the default MCTS-only path bypasses the old broad oddity penalty pipeline, so legal moves that literally fail can still be selected when MCTS overvalues them.

## Code Change

- `fp/search/main.py`: `_apply_hard_legality_and_safety` now severely demotes recovery moves when our active Pokemon is at >=95% HP and there is at least one positive-weight non-recovery alternative.
- `tests/test_threat_bias.py`: added coverage that full-HP recovery loses to a positive attacking alternative, and that it remains selectable when it is the only positive legal option.

The move is demoted, not removed. Forced/only-positive recovery remains legal.

## Validation

Commands run:

```powershell
py -3 -m pytest tests/test_threat_bias.py -q
py -3 -c "import ast; ast.parse(open('fp/search/main.py', encoding='utf-8').read()); ast.parse(open('tests/test_threat_bias.py', encoding='utf-8').read()); print('syntax OK')"
py -3 -c "from fp.search.main import find_best_move, select_move_from_eval_scores; print('import OK')"
py -3 -m pytest tests/ -q --basetemp C:\Users\mtoli\Documents\Code\_pytest_fouler_tmp\base -o cache_dir=C:\Users\mtoli\Documents\Code\_pytest_fouler_cache
```

Results:

- Focused threat-bias tests: `65 passed`
- Syntax/import gates: `syntax OK`, `import OK`
- Full suite: `1145 passed, 2 warnings in 25.58s`

## Deploy Status

Not deployed to JIGGLY in this pass because the authenticated control path is still blocked. Public ladder movement until this commit is deployed should be treated as old-code runtime behavior.

Next exact deploy action once control returns:

```powershell
Set-Location D:\Projects\fouler-play
git fetch origin
git status --short
git cherry-pick <this-commit>
.\.venv\Scripts\python.exe -m pytest tests/test_threat_bias.py -q
py -3 scripts/jigglypuff_devstream_control.py status
```
