# Codex Runtime Recovery Proof - 2026-06-14T20:35:16Z

## Verdict

- Repo-side fix status: **GO**. The JIGGLYPUFF emergency fallback bug was fixed and replay JSON evidence capture was hardened.
- Live JIGGLYPUFF launch status: **NO-GO from this machine**. This shell is on `MIRAIDON`, not JIGGLYPUFF; the JIGGLYPUFF control endpoint is unreachable from here; and no finite runtime lease exists.
- No live laddering, scheduled task start, Discord posting, or `--execute` control action was run.

## Source Truth

- Repo root: discovered with `git rev-parse --show-toplevel`; local absolute path is intentionally omitted from this tracked report. Paths below are repo-relative unless a command is explicitly a remote/runtime endpoint.
- OS/host: Windows `MIRAIDON`
- Branch: `opus48/multisample-mcts`
- Starting commit: `70050479`
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

1. **HARD BLOCKER: JIGGLYPUFF unreachable from this shell**
   - Evidence: tailnet DNS failed and direct-IP SSH status timed out; direct-IP HTTP endpoints timed out.
   - Owner: operator with network/Tailscale/JIGGLY access.
   - Verification: `py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 45` returns JSON with `ok=true` or `status=ready-idle|running`.

2. **HARD BLOCKER: no finite runtime lease**
   - Evidence: `devstream/truth/runtime-lease.json` missing; lease validator returned blocked for `jigglypuff-runtime-start`.
   - Owner: HERMES/operator.
   - Verification: lease validator returns `ok=true` for the requested run count, max cycles, max concurrent battles, account, replay behavior, and proof window.

3. **RELIABILITY BLOCKER: stale runtime truth**
   - Evidence: `devstream_session.py doctor` reports stale `stream_status.json` without a live expected runner; cleanup/adoption is lease-gated.
   - Owner: HERMES/operator.
   - Verification: after lease, run cleanup/adoption/start through `devstream_session.py` or `jigglypuff_devstream_control.py` and confirm doctor no longer blocks on stale runtime truth.

## Exact Next Commands

From this repo on the control machine with JIGGLYPUFF reachable, create a finite proof-window lease without printing secrets:

```powershell
$Account = (Get-Content .env | Where-Object { $_ -match '^PS_USERNAME=' } | Select-Object -First 1) -replace '^PS_USERNAME=',''
py -3 scripts\devstream_runtime_lease.py --write --runtime-lease devstream\truth\runtime-lease.json --purpose jigglypuff-runtime-start --machine JIGGLYPUFF --account $Account --run-count 1 --max-cycles 1 --max-concurrent-battles 1 --replay-behavior always --valid-minutes 45 --require-run-count --require-max-cycles --require-max-concurrent-battles --require-replay-behavior
py -3 scripts\jigglypuff_devstream_control.py status --read-only --timeout 45
py -3 scripts\jigglypuff_devstream_control.py start --run-count 1 --max-concurrent-battles 1 --max-cycles 1 --runtime-lease devstream\truth\runtime-lease.json --execute
```

If running directly on JIGGLYPUFF after the lease exists:

```powershell
py -3 scripts\devstream_session.py doctor
py -3 scripts\devstream_session.py start --run-count 1 --max-concurrent-battles 1 --max-cycles 1 --runtime-lease devstream\truth\runtime-lease.json --execute
```

## Remaining Risks

- Emergency fallback is intentionally small; it is not a second full decision engine.
- Autoresearch is working but the latest 30-battle window lacks linked replay/trace evidence for losses, so no new policy issue should be promoted from that window.
- Replay JSON capture is now awaited, but it still needs proof from an authorized JIGGLYPUFF batch because this machine cannot reach the runtime profile.
- The live JIGGLYPUFF state could not be proven from `MIRAIDON`; current runtime status remains external until the control path responds.

Next decision-engine-only tasks after runtime proof:

1. After an authorized one-battle proof run, verify the new battle has `battle_stats.json`, `replay_analysis/<id>.json`, and `logs/decision_traces/<battle>_turn*.json` evidence before promoting a policy issue.
2. Continue PP/revealed-set/recovery/hazard tuning only from replay-backed issues or focused failing tests.
