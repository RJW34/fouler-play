# Fouler Runtime Recovery Proof - 2026-06-15T07:40:00Z

## Scope

Continuation of the active JIGGLYPUFF monitoring goal. This pass used public Showdown replay evidence because JIGGLY's direct control path remains unavailable.

## Runtime Signals

Repo-native status remains blocked:

- Command: `py -3 scripts/jigglypuff_devstream_control.py status`
- Result: `status=blocked`, `healthy=false`, `running=false`
- Blocker: `JIGGLYPUFF fouler runtime did not return JSON`
- SSH evidence: TCP `22` connects, then OpenSSH times out during banner exchange.
- HTTP evidence: `192.168.1.126:8791` and `192.168.1.126:8777` are not returning status/state.
- Tailscale evidence: local Tailscale service exists but is stopped and disabled; starting it is blocked by `Access is denied`.

Public Showdown evidence proves the account continued to ladder:

- Account: `LEBOTJAMESXD00N`
- Ladder API record observed after the code commit: `36-45`, ELO `1080.2990954436173`
- Latest ladder timestamp observed after the code commit: `1781509241` = `2026-06-15T07:40:41Z`
- Public replay search returned `41` Gen 9 OU replays.
- Latest public replay observed: `gen9ou-2632325972`, uploaded `2026-06-15T07:39:40Z`.
- That replay finished as an 8-turn loss to `RoKoBaTuRiNa`.

## Replay-Backed Issue

Fresh public replay `gen9ou-2632325972` proves a current decision error:

```text
|move|p1a: Gliscor|Spikes|p2a: Hatterene
|move|p2a: Hatterene|Spikes|p1a: Gliscor|[from] ability: Magic Bounce
|-sidestart|p1: LEBOTJAMESXD00N|Spikes
...
|move|p1a: Gliscor|Spikes|p2a: Hatterene
|move|p2a: Hatterene|Spikes|p1a: Gliscor|[from] ability: Magic Bounce
|-sidestart|p1: LEBOTJAMESXD00N|Spikes
```

Local oracle grounding:

- `oracle.pokemon("hatterene")` shows Hatterene can have hidden ability `Magic Bounce`.
- The replay log itself reveals Magic Bounce on the first reflected `Spikes`.

The bot should not repeat a reflected hazard into the same Magic Bounce active.

## Code Change

- `fp/search/main.py`: added an always-on MCTS-only safety penalty in `_apply_hard_legality_and_safety` for `MAGIC_BOUNCE_REFLECTED_MOVES` when `OpponentAbilityState.has_magic_bounce` is true. The move is severely demoted, not removed, so it remains a legal last resort.
- `tests/test_threat_bias.py`: added `test_mcts_only_demotes_magic_bounce_reflected_hazard`, proving high-weight `spikes` loses to `earthquake` under Magic Bounce and records trace metadata.

This is not a broad penalty-pipeline re-enable. It is a narrow MCTS safety guard for a replay-proven self-harming legal move.

## Validation

Commands run:

```powershell
py -3 -m pytest tests/test_threat_bias.py -q
py -3 -c "import ast; ast.parse(open('fp/search/main.py', encoding='utf-8').read()); ast.parse(open('tests/test_threat_bias.py', encoding='utf-8').read()); print('syntax OK')"
py -3 -c "from fp.search.main import find_best_move, select_move_from_eval_scores; print('import OK')"
py -3 -m pytest tests/ -q --basetemp C:\Users\mtoli\Documents\Code\_pytest_fouler_tmp\base -o cache_dir=C:\Users\mtoli\Documents\Code\_pytest_fouler_cache
```

Results:

- Focused threat-bias tests: `63 passed`
- Syntax/import gates: `syntax OK`, `import OK`
- Full suite: `1143 passed, 2 warnings in 50.11s`

## Deploy Status

Not deployed to JIGGLY in this pass because the only authenticated control route is still blocked. Public ladder movement after this local commit should be treated as old-code runtime behavior until JIGGLY is reachable and this commit is deployed.

- SSH banner exchange times out.
- Resident worker/status HTTP returns no JSON.
- OBS/state HTTP is down.
- SMB/RPC/admin service paths are unavailable from this account.

Next exact action when control returns:

```powershell
Set-Location D:\Projects\fouler-play
git fetch origin
git status --short
git cherry-pick <this-commit>
.\.venv\Scripts\python.exe -m pytest tests/test_threat_bias.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_discord_reporting.py tests/test_rating_transition.py -q
```

If SSH remains stuck, restart only the Windows OpenSSH service or clear stuck `sshd.exe` children from an interactive/admin JIGGLY session, then rerun:

```powershell
py -3 scripts/jigglypuff_devstream_control.py status
```
