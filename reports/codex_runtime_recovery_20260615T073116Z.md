# Fouler Runtime Recovery Proof - 2026-06-15T07:31:16Z

## Scope

Objective: keep `fouler-play` on JIGGLYPUFF aligned with the original scaffold intent: a bounded Gen 9 OU fat/stall ladder runtime that records replay/decision evidence and reports battle results truthfully.

This report is a continuation proof, not a claim that the 1700 ELO product mission is complete.

## Source Of Truth Read

- Scaffold prompt: `C:\Users\mtoli\.codex\attachments\01302732-bf0d-4204-bd26-0ff667d20c0b\pasted-text.txt`
- Scaffold pack: `C:\Users\mtoli\Downloads\fouler_codex_scaffold_pack.zip`
- Repo docs: `README.md`, `CLAUDE.md`, `TASKBOARD.md`
- Guardrails: `infrastructure/guardrails.json`
- Launchers inspected: `start_one_touch.bat`, `infrastructure/windows/player_loop.bat`, `scripts/devstream_session.py`

## Local Checkout

- Repo root: `C:/Users/mtoli/Documents/Code/fouler-play`
- OS: Windows 11
- Branch: `opus48/multisample-mcts`
- Head: `5345ea9c Correct battle result reports from rating movement`
- Remotes:
  - `origin`: `https://github.com/RJW34/fouler-play.git`
  - `upstream`: `https://github.com/pmariglia/foul-play.git`
- Python used for validation: `py -3`, Python `3.13.13`
- Local environment checked without printing secrets: `PS_USERNAME`, `PS_PASSWORD`, `SHOWDOWN_ACCOUNTS`, `DECISION_POLICY`, `MAX_CONCURRENT_BATTLES`, `PS_RUN_COUNT`, and `FOULER_BATCH_SIZE` were unset in this shell.

## Code Change Since Previous Proof

Commit `5345ea9c` made the Discord reporting formatter trust ladder rating movement before a stale parsed result:

- `infrastructure/discord_reporting.py`: added result derivation from `rating_delta`, falling back to `elo_after - elo_before`, then to the payload `result`.
- `tests/test_discord_reporting.py`: regression coverage for a payload that says `result=loss` while rating movement is positive; expected output is now a win with gained ELO and structured winner/loser fields corrected.

This is a direct reporting correctness fix, not new Discord infrastructure.

## Validation

Commands run:

```powershell
py -3 -m pytest tests/ -q --basetemp C:\Users\mtoli\Documents\Code\_pytest_fouler_tmp\base -o cache_dir=C:\Users\mtoli\Documents\Code\_pytest_fouler_cache
py -3 -c "import ast; ast.parse(open('fp/search/main.py', encoding='utf-8').read()); print('syntax OK')"
py -3 -c "from fp.search.main import find_best_move; print('OK')"
py -3 -c "from replay_analysis.autoresearch import run_autoresearch; print('autoresearch import OK')"
py -3 pipeline.py autoresearch -n 30 --no-discord
```

Results:

- Full test suite: `1142 passed, 2 warnings in 46.72s`
- Import/syntax gates: `syntax OK`, `OK`, `autoresearch import OK`
- Autoresearch generated `replay_analysis/autoresearch_latest.json` and `replay_analysis/reports/autoresearch_latest.md`.
- Autoresearch window: `14-16 (47% WR)` on the local available May 31 data.
- Autoresearch top issue: none. Evidence integrity correctly blocked mechanics/strategy claims because that local window has `0` losses with replay JSON, `0` with decision traces, and `0` with request-backed legal options.

Notes:

- A first full-suite attempt using the default temp directory failed with `PermissionError: [WinError 5] Access is denied: C:\Users\mtoli\AppData\Local\Temp\pytest-of-mtoli`. Rerunning with temp/cache outside the repo produced the passing result above.
- A repo-local temp run produced one path-normalization failure because pytest temp files were under the repo root; this was environmental and disappeared when the temp base was moved outside the repo.

## JIGGLYPUFF Runtime Evidence

Last directly verified JIGGLY state from the earlier recovery pass:

- Reporting patch was deployed to `D:\Projects\fouler-play`.
- Remote focused tests passed: `57 passed`.
- Bot process was active as PID `270144`.
- Active battles were present under account `LEBOTJAMESXD00N`.
- Event queue had no pending events at the last direct read: `31 expired`, `46 posted`.

Public Showdown ladder evidence collected after that patch:

- Account: `LEBOTJAMESXD00N`
- Format: `gen9ou`
- Ladder record returned by Showdown: `34-42`
- ELO returned by Showdown: `1084.1198768891204`
- GXE returned by Showdown: `37.8`
- Last played timestamp returned by Showdown: `1781508502`, which is `2026-06-15T07:28:22Z`

This proves the account continued to produce ladder activity after the reporting fix window. It does not by itself prove the latest local Discord queue state.

## Current Remote Access State

Direct JIGGLY inspection is currently degraded:

- `Test-NetConnection JIGGLYPUFF -Port 22`: TCP succeeds.
- `ssh -vv Ryanj@JIGGLYPUFF "echo ok"`: connection establishes, then times out during SSH banner exchange.
- `Test-NetConnection JIGGLYPUFF -Port 8777`: TCP fails; OBS/state HTTP surface is not reachable.
- SMB/admin share checks for `\\JIGGLYPUFF\D$\Projects\fouler-play`: unavailable from this account.
- `sc.exe \\JIGGLYPUFF query sshd`: access denied.
- `tasklist /S JIGGLYPUFF`: username/password rejected.
- `ssh ubunztu`: configured DEKU hostname `ubunztu.tail4859dd.ts.net` does not resolve from this machine.
- Repo-native wrapper check:
  - Command: `py -3 scripts/jigglypuff_devstream_control.py status`
  - Result: `status=blocked`, `healthy=false`, `running=false`
  - Blocker: `JIGGLYPUFF fouler runtime did not return JSON`
  - Raw transport evidence: SSH returned `Connection timed out during banner exchange`; worker/status HTTP attempts on `192.168.1.126:8791`, `jigglypuff.tail4859dd.ts.net:8791`, and `/state` did not return usable JSON.

Current conclusion: JIGGLY itself appears reachable on the network, but the SSH service/control path is unhealthy or overloaded. I did not kill or restart anything because I do not have a working authenticated remote-control channel and the bot may still be mid-run.

## Remaining Work

1. Restore or verify the JIGGLY control path, preferably by restarting only the Windows OpenSSH service or clearing stuck SSH worker processes from an interactive/admin session.
2. Once SSH returns, inspect `D:\Projects\fouler-play\events_queue.json`, `battle_stats.json`, `logs\codex-live-run-cmd.log`, and `logs\decision_traces\*.json`.
3. If the reporting formatter patch is still only a working-tree change on JIGGLY, commit or deploy `5345ea9c` there so the hotfix is durable.
4. Do not make more decision-engine changes until current replay/trace evidence is available; the local autoresearch gate correctly says the available local data is not enough for mechanics claims.

## Worktree Notes

Protected files were not edited. Existing generated/runtime churn remains:

- `fp/data/movepool_data.json`
- `replay_analysis/autoresearch_latest.json`

No secrets were printed or written to this report.
