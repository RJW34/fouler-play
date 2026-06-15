# Fouler Runtime Recovery Proof - 2026-06-15T04:26:28Z

## Scope

Objective: monitor and tweak fouler-play on JIGGLYPUFF until the live runtime and reporting path are satisfactory against the scaffold intent.

This report covers the current recovery pass. It does not claim the strategic 1700 ELO mission is complete.

## Local Checkout

- Repo: `C:\Users\mtoli\Documents\Code\fouler-play`
- Branch: `opus48/multisample-mcts`
- Local head after alignment: `ad2c0a46 Use rating delta as reporting result truth`
- Worktree after validation: clean

Change made locally:
- `fp/run_battle.py`: direct Discord, stream stats, and queued battle proof now derive result from `_battle_result_from_evidence`, preferring the signed authoritative ELO delta when available.
- `tests/test_rating_transition.py`: regression coverage for winner parsing contradicting rating movement.

## JIGGLYPUFF Runtime

- Host: `JIGGLYPUFF`
- Runtime path: `D:\Projects\fouler-play`
- Remote head: `f8758fc1 Use rating delta as reporting result truth`
- Lease: `fouler-devstream-supervise-20260615T022522Z-run30`
- Account in active lease/runner: `LEBOTJAMESXD00N`
- Supervisor stop file: absent
- Live PIDs verified alive:
  - bot: `259104`
  - battle session: `264776`
  - supervisor: `266492`

Active battles at final read:
- `battle-gen9ou-2632260992` vs `NewAccount1224`
- `battle-gen9ou-2632261839` vs `NOVEL00`
- `battle-gen9ou-2632261849` vs `jongwoo buffet`

## Reporting Evidence

Fresh battle rows from `battle_stats.json` and event queue posts agree on result and ELO direction:

- `battle-gen9ou-2632259448`: `win`, `1146 -> 1174 (+28)`, queue content says `battle result win` and `ELO gained 28`.
- `battle-gen9ou-2632259358`: `loss`, `1174 -> 1145 (-29)`, queue content says `battle result loss` and `ELO lost 29`.
- `battle-gen9ou-2632258759`: `loss`, `1145 -> 1119 (-26)`, queue content says `battle result loss` and `ELO lost 26`.

Earlier verified rows:
- `battle-gen9ou-2632256339`: `win`, `1128 -> 1154 (+26)`, direct log posted `WIN`.
- `battle-gen9ou-2632256378`: `win`, `1154 -> 1175 (+21)`, queue proof says `win` and `ELO gained 21`.
- `battle-gen9ou-2632256426`: `loss`, `1175 -> 1146 (-29)`, queue proof says `loss` and `ELO lost 29`.

Discord event poster:
- Command: `.\.venv\Scripts\python.exe infrastructure\event_poster.py --drain --max-events 5`
- Result: queue empty after drain.
- Doctor status: `ready`
- Pending battle results: `0`
- Webhook failures: `0`
- DNS failures: `0`
- Delivery failures: `0`
- Secret values printed: `false`

## Validation

Local commands:

```powershell
py -3 -c "import ast, pathlib; ast.parse(pathlib.Path('fp/run_battle.py').read_text(encoding='utf-8')); ast.parse(pathlib.Path('tests/test_rating_transition.py').read_text(encoding='utf-8')); print('OK')"
py -3 -c "from fp.run_battle import _battle_result_from_evidence; assert _battle_result_from_evidence('opponent','bot', elo_delta=43) == 'win'; assert _battle_result_from_evidence('bot','bot', elo_delta=-28) == 'loss'; print('OK')"
py -3 -m pytest tests/test_rating_transition.py tests/test_discord_reporting.py tests/test_replay_upload_resolver.py tests/test_event_queue_battle_result_expiry.py tests/test_event_poster_replay_resolver.py -q --basetemp .pytest-tmp
```

Results:
- Syntax/import smoke: `OK`
- Helper smoke: `OK`
- Reporting/evidence test slice: `69 passed`

Note: pytest emitted a cache warning because `.pytest_cache` could not be written. The selected tests completed successfully.

## Remaining Risks

- JIGGLYPUFF runtime worktree contains many untracked scratch/diagnostic files and one modified runtime data artifact (`stability_report.json`). I did not delete or revert them because they are runtime/scratch state and not required to prove reporting correctness.
- The active run is still in progress. Current runtime/reporting evidence is satisfactory, but long-term ELO improvement remains unproven and should be judged over full 30-battle batches plus replay-grounded analysis.
- The direct runtime branch and local checkout carry equivalent reporting fixes under different commits (`f8758fc1` remote, `ad2c0a46` local) because the deployed branch and local branch have different surrounding code.
