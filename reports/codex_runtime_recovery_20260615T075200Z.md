# Fouler Runtime Recovery Proof - 2026-06-15T07:52:00Z

## Scope

Continuation of the active JIGGLYPUFF monitoring goal. JIGGLY direct control remains unavailable, so this pass used public Showdown replay logs to identify a distinct, replay-backed move-success bug.

## Control State

Repo-native status still fails:

- Command: `py -3 scripts/jigglypuff_devstream_control.py status`
- Result: `status=blocked`, `healthy=false`, `running=false`
- Blocker: `JIGGLYPUFF fouler runtime did not return JSON`
- Raw evidence: SSH command to `Ryanj@192.168.1.126` timed out after 45 seconds; worker/status HTTP did not return JSON.

Public ladder still shows old-code runtime activity:

- Account: `LEBOTJAMESXD00N`
- Ladder API record observed: `38-46`, ELO `1109.5194881576945`
- Latest ladder timestamp observed: `1781509818`
- Public replay search returned `44` Gen 9 OU replays.

## Replay-Backed Issue

Public replay `gen9ou-2632328618` showed the bot clicking `Toxic` into a Snorlax whose ability was revealed as `Immunity`:

```text
|move|p1a: Gliscor|Toxic|p2a: Snorlax
|-immune|p2a: Snorlax|[from] ability: Immunity
```

Local oracle grounding:

- `oracle.pokemon("snorlax")` confirms Snorlax can have ability `Immunity`.
- `oracle.move("toxic")` confirms Toxic is a Poison-type status move.
- The replay protocol itself revealed the active ability as `Immunity`.

Existing `fp/search/move_validators.py` handled type-based poison status immunity, but not ability-based poison status immunity.

## Code Change

- `fp/search/move_validators.py`: `can_move_hit` now blocks direct Poison-type status moves when the revealed opponent ability is in `constants.IMMUNE_TO_POISON_ABILITIES`.
- The target check intentionally leaves side/self moves such as `Toxic Spikes` and `Baneful Bunker` alone.
- `tests/test_threat_bias.py`: added coverage for `Toxic` blocked by revealed `Immunity`, plus a guard that `Toxic Spikes` is not blocked by the active's `Immunity` ability.

## Validation

Commands run:

```powershell
py -3 -m pytest tests/test_threat_bias.py -q
py -3 -c "import ast; [ast.parse(open(p, encoding='utf-8').read()) for p in ['fp/search/move_validators.py','tests/test_threat_bias.py']]; print('syntax OK')"
py -3 -c "from fp.search.move_validators import filter_blocked_moves; from fp.search.main import find_best_move; print('import OK')"
py -3 -m pytest tests/ -q --basetemp C:\Users\mtoli\Documents\Code\_pytest_fouler_tmp\base -o cache_dir=C:\Users\mtoli\Documents\Code\_pytest_fouler_cache
```

Results:

- Focused threat-bias tests: `67 passed`
- Syntax/import gates: `syntax OK`, `import OK`
- Full suite: `1147 passed, 2 warnings in 25.59s`

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
