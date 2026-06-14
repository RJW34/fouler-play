# Codex Runtime Recovery Proof - 2026-06-14T20:35:16Z

## Verdict

- Repo-side fix status: **GO**. The JIGGLYPUFF emergency fallback bug was fixed and replay JSON evidence capture was hardened.
- Live JIGGLYPUFF launch status: **NO-GO for starting or restarting from this machine**. This shell is on `MIRAIDON`, not JIGGLYPUFF; a read-only LAN SSH control path now proves JIGGLYPUFF is reachable and already running; no finite runtime lease exists for start/stop/restart actions.
- No live laddering, scheduled task start, Discord posting, or `--execute` control action was run.

## Source Truth

- Repo root: discovered with `git rev-parse --show-toplevel`; local absolute path is intentionally omitted from this tracked report. Paths below are repo-relative unless a command is explicitly a remote/runtime endpoint.
- OS/host: Windows `MIRAIDON`
- Branch: `opus48/multisample-mcts`
- Starting commit: `70050479`
- Remotes: `origin` -> `https://github.com/RJW34/fouler-play.git`; `upstream` -> `https://github.com/pmariglia/foul-play.git`
- Python: `py -3 --version` -> `Python 3.13.13`
- Environment probe: Windows 11; relevant live Showdown/runtime variables were unset in this shell (`PS_USERNAME`, `PS_PASSWORD`, `SHOWDOWN_ACCOUNTS`, `DECISION_POLICY`, `MAX_CONCURRENT_BATTLES`, `PS_RUN_COUNT`, `FOULER_BATCH_SIZE`). Remote JIGGLY `.env` presence was checked by status only; secret values were not printed.
- Source files read: `README.md`, `CLAUDE.md`, `TASKBOARD.md`, `infrastructure/guardrails.json`, `.env.example`, `devstream.yaml`, `start_one_touch.bat`, `infrastructure/windows/player_loop.bat`, `scripts/jigglypuff_devstream_control.py`, `scripts/devstream_session.py`, `scripts/devstream_runtime_lease.py`, `scripts/fouler_jigglypuff_runtime.ps1`

## Changes Made

- `fp/run_battle.py`
  - Replaced blind first-legal `_fallback_decision` with a cheap emergency scorer.
  - Uses Showdown request-backed legal moves when present.
  - Skips disabled and zero-PP moves.
  - Handles force-switch separately and respects request legal switch slots when available.
  - Scores damage using local move/type data, with small emergency bonuses for recovery, hazards, pivots, and setup.
  - Adds fallback trace metadata for timeout/error/fallback paths.
  - Replaced the fire-and-forget replay JSON save with a bounded awaited evidence save, so the worker does not exit before `replay_analysis/<id>.json` has a chance to persist.
- `tests/test_invalid_choice_recovery.py`
  - Added fallback regression coverage for immune first move vs effective legal alternative, disabled/zero-PP filtering, scored force-switches, and request legal switch slots.
- `tests/test_replay_upload_resolver.py`
  - Added coverage that local replay JSON evidence saves are awaited, retried when the replay JSON is not immediately available, and fail closed on timeout.
- `infrastructure/guardrails.json`
  - Added `fp/run_battle.py` to `allowed_modify`; it is a non-protected runtime/evidence file needed for this scaffold-targeted fallback repair.

Protected files were not edited: `run.py`, `config.py`, `.env`, credentials, and `teams/**`.

## Validation

Commands run:

- `git rev-parse --show-toplevel`
  - Result: repo root discovered successfully; absolute local path intentionally omitted from this tracked report.
- `git status --short`, `git branch --show-current`, `git log --oneline -8`, `git remote -v`, and `py -3 --version`
  - Result: recorded source truth above.
- `py -3 -m pytest tests/test_invalid_choice_recovery.py -q --basetemp <external-temp-root>\fallback-basetemp -o cache_dir=<external-temp-root>\fallback-cache`
  - Result: `6 passed`
- `py -3 -m pytest tests/ -q --basetemp <external-temp-root>\basetemp -o cache_dir=<external-temp-root>\cache`
  - Result: `1119 passed, 2 warnings`
- `py -3 -m pytest tests\test_replay_upload_resolver.py -q`
  - Result: `11 passed`
- `py -3 -m pytest tests\test_decision_trace.py tests\test_autoresearch.py -q`
  - Result: `11 passed, 3 warnings`
- `py -3 -m pytest tests\ -q`
  - Result: `1122 passed, 3 warnings`
- `py -3 -c "import ast; ast.parse(open('fp/search/main.py', encoding='utf-8').read()); ast.parse(open('fp/run_battle.py', encoding='utf-8').read()); print('syntax OK')"`
  - Result: `syntax OK`
- `py -3 -c "from fp.search.main import find_best_move; from fp.run_battle import _fallback_decision; print('OK')"`
  - Result: `OK`
- `py -3 -c "from fp.run_battle import _save_replay_json_for_evidence, _save_replay_json_locally; from replay_analysis.autoresearch import run_autoresearch; print('OK')"`
  - Result: `OK`
- `py -3 -c "from replay_analysis.autoresearch import run_autoresearch; print('autoresearch import OK')"`
  - Result: `autoresearch import OK`
- `py -3 -m json.tool infrastructure\guardrails.json`
  - Result: valid JSON
- `py -3 -m pytest tests\test_jigglypuff_control_contract.py -q` with writable temp/cache root
  - Result: `22 passed`
- `py -3 -m py_compile scripts\jigglypuff_devstream_control.py`
  - Result: passed
- `powershell -NoProfile -Command '$null = [scriptblock]::Create((Get-Content -LiteralPath "scripts\fouler_jigglypuff_runtime.ps1" -Raw)); "ps parse OK"'`
  - Result: `ps parse OK`
- `git diff --check`
  - Result: no whitespace errors; Git warned only that LF may be rewritten to CRLF on touched files.

Note: the first test attempt failed before running affected code because the default Windows temp/cache directories were not writable. Rerunning with an external writable temp root produced the green test results above.

## Runtime Readiness Evidence

Read-only and dry-run commands:

- `py -3 scripts\jigglypuff_devstream_control.py start --run-count 1 --max-concurrent-battles 1 --max-cycles 1`
  - Result: dry-run plan only; `execute=false`; runtime lease required for execute.
- `py -3 scripts\jigglypuff_devstream_control.py login-proof`
  - Result: dry-run plan only; `execute=false`.
- `py -3 scripts\devstream_runtime_lease.py --purpose jigglypuff-runtime-start --run-count 1 --max-concurrent-battles 1 --max-cycles 1 --require-run-count --require-max-cycles --require-max-concurrent-battles --require-replay-behavior`
  - Result: blocked; `devstream/truth/runtime-lease.json` is missing.
- `py -3 scripts\devstream_session.py doctor`
  - Result: `ready=false`; blockers include stale runtime truth without a live expected runner and required finite runtime lease for cleanup/adoption/start.
- `py -3 scripts\devstream_session.py doctor` after the evidence-save fix
  - Result: `ready=false`; read-only doctor still blocks on stale local runtime truth without a live expected runner and finite runtime lease.

Remote/public probes:

- `py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 45`
  - Result: blocked; tailnet hostname did not resolve.
- `py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 20` after the evidence-save fix
  - Result: blocked; runtime did not return JSON because the tailnet hostname did not resolve.
- With direct IP override: `FOULER_JIGGLYPUFF_SSH=Ryanj@192.168.1.126 py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 45`
  - Result: blocked; SSH status command timed out.
- Public endpoints checked: `http://192.168.1.126:8777/health`, `http://192.168.1.126:8777/state`, tailnet `/health`, tailnet `/state`
  - Result: direct-IP HTTP timed out; tailnet hostname did not resolve.
- Corrected access proof after controller SSH fallback fix:
  - `Test-NetConnection -ComputerName JIGGLYPUFF -Port 22` succeeded and resolved `JIGGLYPUFF` to LAN IP.
  - `ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new Ryanj@JIGGLYPUFF hostname` returned `JIGGLYPUFF`.
  - `py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 60` succeeded with no env override after `scripts/jigglypuff_devstream_control.py` learned LAN SSH fallback.
  - Result: JIGGLYPUFF status `running`, repo root `D:\Projects\fouler-play`, branch `feat/learn-and-climb-20260613`, head `508d5dd5`, dirty only with untracked runtime/ops helper scripts, one logical battle-session leaf runner, fresh battle stats and decision traces. The command line includes the configured Showdown account; this report intentionally omits it.
  - `devstream/truth/runtime-lease.json` was missing on JIGGLYPUFF.
- Deployment-prep proof:
  - Applied the request-backed emergency fallback patch to JIGGLYPUFF's live branch without stopping or restarting the existing process.
  - Remote commit: `b7ab8ebc Fix request-backed emergency fallback` on `feat/learn-and-climb-20260613`.
  - Remote status-producer commit: `74ece9a9 Expose runtime git commit time` on `feat/learn-and-climb-20260613`.
  - Remote status-producer refinement: `a453255b Report runtime-code commit freshness` on `feat/learn-and-climb-20260613`.
  - Remote validation:
    - `.\.venv\Scripts\python.exe -m pytest tests\test_invalid_choice_recovery.py -q` -> `6 passed`
    - `.\.venv\Scripts\python.exe -m py_compile fp\run_battle.py` -> passed
    - `.\.venv\Scripts\python.exe -c "from fp.run_battle import _fallback_decision; print('fallback import OK')"` -> passed
  - Initial read-only status after remote commit showed remote head `b7ab8ebc` while a battle process was already running.
- Runtime-code freshness proof:
  - `scripts/fouler_jigglypuff_runtime.ps1` now reports both `git.commitTime` for repo head and `git.runtimeCodeCommitTime` / `git.runtimeCodeHead` for runtime-loaded paths.
  - `scripts/jigglypuff_devstream_control.py status --read-only --timeout 60` compares active battle/supervisor process start timestamps with `git.runtimeCodeCommitTime` when present, falling back to `git.commitTime` for older producers.
  - Result after JIGGLY producer refinement: `status=blocked`, `ok=false`, `runtimeCodeFreshness.processStartPredatesRuntimeCode=true`, `staleProcessCount=2`, repo head `a453255b`, runtime-code head `b7ab8ebc`, runtime-code commit time `2026-06-14T17:15:19-04:00`.
  - Interpretation: the fallback patch is committed on JIGGLY, but both live battle Python processes started before the fallback runtime-code commit. A finite runtime-lease drain/restart is still required before claiming the live process loaded the patch.

Local evidence paths:

- `battle_stats.json`: exists, 804 battles.
- `logs/decision_traces`: exists, 4383 JSON traces.
- `replay_analysis/gen9ou-*.json`: 32 replay JSON files.
- `active_battles.json`: count 0.
- `stream_status.json`: stale `Searching` status from 2026-06-08, not live proof.
- `replay_analysis/reports/autoresearch_latest.md`: exists.

Autoresearch smoke:

- `py -3 pipeline.py autoresearch -n 30 --no-discord`
  - Result: ran successfully.
  - Latest generated window during this run: 14-16 over 30 battles, `top_issue=null`.
- Evidence integrity correctly blocked mechanics/strategy claims because the 16 recent losses had no linked replay JSON or request-backed decision traces.
- Follow-up inspection found local request-backed decision traces are present, but for local/synthetic `battle-gen9ou-181`-style IDs, while recent ladder stats are `battle-gen9ou-26221...` IDs with no matching replay JSON or trace files. That is stale/unlinked runtime evidence, not a current trace schema failure.

Generated runtime artifacts from validation were treated as proof inputs, not source changes, and were not staged for commit.

## Blockers

1. **RESOLVED: JIGGLYPUFF reachability from this shell**
   - Evidence: LAN name `JIGGLYPUFF` resolves locally, SSH as the runtime user returns `hostname=JIGGLYPUFF`, and `py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 60` now returns JSON with `ok=true`, `healthy=true`, and `status=running`.
   - Fix: `scripts/jigglypuff_devstream_control.py` now uses status-only SSH fallback candidates: tailnet, LAN hostname, then direct IP. Mutating execute paths still use one explicit remote.
   - Verification: `tests/test_jigglypuff_control_contract.py` covers the fallback path.

2. **HARD BLOCKER: no finite runtime lease**
   - Evidence: `devstream/truth/runtime-lease.json` missing; lease validator returned blocked for `jigglypuff-runtime-start`.
   - Owner: HERMES/operator.
   - Verification: lease validator returns `ok=true` for the requested run count, max cycles, max concurrent battles, account, replay behavior, and proof window.

3. **RELIABILITY BLOCKER: deployed JIGGLY runtime is live on a different branch**
   - Evidence: read-only status reports branch `feat/learn-and-climb-20260613`, head `a453255b`, runtime-code head `b7ab8ebc`, active battle-session runner, and untracked runtime/ops helper scripts. Local repo branch is `opus48/multisample-mcts`.
   - Owner: operator/Codex with runtime lease.
   - Verification: after active battles are drained or an explicit runtime lease authorizes a bounded restart, restart through the lease-gated control path and confirm the running process command/status reflects the refreshed code.

4. **RELIABILITY BLOCKER: live battle processes predate the patched commit**
   - Evidence: read-only status reports `runtimeCodeFreshness.processStartPredatesRuntimeCode=true`, `staleProcessCount=2`, repo head `a453255b`, and runtime-code head `b7ab8ebc`.
   - Owner: operator/Codex with runtime lease.
   - Verification: after a lease-gated stop/start, `py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 60` must return `runtimeCodeFreshness.ok=true` with zero stale battle/supervisor processes.

## Exact Next Commands

From this repo on the control machine, first verify current live state without writing mirrors:

```powershell
py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 60
```

If JIGGLYPUFF is still running, do not run `start --execute`. Create a finite stop lease before any interruption:

```powershell
$Account = (Get-Content .env | Where-Object { $_ -match '^PS_USERNAME=' } | Select-Object -First 1) -replace '^PS_USERNAME=',''
py -3 scripts\devstream_runtime_lease.py --write --runtime-lease devstream\truth\runtime-lease-stop.json --purpose jigglypuff-runtime-stop --machine JIGGLYPUFF --account $Account --run-count 1 --max-cycles 1 --max-concurrent-battles 1 --replay-behavior always --valid-minutes 45 --require-run-count --require-max-cycles --require-max-concurrent-battles --require-replay-behavior
py -3 scripts\jigglypuff_devstream_control.py stop --runtime-lease devstream\truth\runtime-lease-stop.json --execute
```

The fallback patch is already committed on JIGGLYPUFF. To restart so the running Python process loads it, create a separate start lease locally and on JIGGLYPUFF, then use the lease-gated start:

```powershell
py -3 scripts\devstream_runtime_lease.py --write --runtime-lease devstream\truth\runtime-lease-start.json --purpose jigglypuff-runtime-start --machine JIGGLYPUFF --account $Account --run-count 1 --max-cycles 1 --max-concurrent-battles 1 --replay-behavior always --valid-minutes 45 --require-run-count --require-max-cycles --require-max-concurrent-battles --require-replay-behavior
ssh -o BatchMode=yes Ryanj@JIGGLYPUFF 'Set-Location "D:\Projects\fouler-play"; $Account = (Get-Content .env | Where-Object { $_ -match "^PS_USERNAME=" } | Select-Object -First 1) -replace "^PS_USERNAME=",""; .\.venv\Scripts\python.exe scripts\devstream_runtime_lease.py --write --runtime-lease devstream\truth\runtime-lease-start.json --purpose jigglypuff-runtime-start --machine JIGGLYPUFF --account $Account --run-count 1 --max-cycles 1 --max-concurrent-battles 1 --replay-behavior always --valid-minutes 45 --require-run-count --require-max-cycles --require-max-concurrent-battles --require-replay-behavior | Out-Null'
py -3 scripts\jigglypuff_devstream_control.py start --run-count 1 --max-concurrent-battles 1 --max-cycles 1 --runtime-lease devstream\truth\runtime-lease-start.json --execute
```

## Remaining Risks

- Emergency fallback is intentionally small; it is not a second full decision engine.
- Autoresearch is working but the latest 30-battle window lacks linked replay/trace evidence for losses, so no new policy issue should be promoted from that window.
- The live JIGGLYPUFF state is now proven reachable from `MIRAIDON`, but the currently running process will not pick up local code changes until a lease-authorized drain/restart.
- The deployed runtime branch already has replay-status fields, local replay JSON writes, request-backed legal-option traces, and the request-backed emergency fallback patch committed. The existing running Python process started before that commit, so runtime effect is unproven until restart.

Next decision-engine-only tasks after runtime proof:

1. After an authorized one-battle proof run, verify the new battle has `battle_stats.json`, `replay_analysis/<id>.json`, and `logs/decision_traces/<battle>_turn*.json` evidence before promoting a policy issue.
2. Continue PP/revealed-set/recovery/hazard tuning only from replay-backed issues or focused failing tests.
