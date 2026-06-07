# Fouler Readiness Hardening - 2026-06-07

Scope: JIGGLYPUFF `D:\Projects\fouler-play`, branch `fix/atomic-singleton-lock-20260604`.

Base before this patch:

- `5c7c86a3b4bd5782fa578d3ea2825dc841f7865a` (`Harden Fouler runtime readiness guards`)
- Remote status was clean before patch application.

## Changes

- `fp/run_battle.py`: replaced untracked `asyncio.ensure_future(...)` replay JSON saves with a bounded task tracker (`REPLAY_SAVE_TASKS_MAX`, default `32`) that observes exceptions and closes skipped coroutines.
- `bot_monitor.py`: added a bounded pending-replay handoff TTL (`MONITOR_FINISHED_REPLAY_PENDING_MAX_AGE_SEC`, default `3600`) so batch summaries can preserve late replay handoff opportunities without retaining finished battle state indefinitely.
- `infrastructure/improve_loop.py`: every mutating run now requires `readyForOfflineIteration=true`; stale battle evidence blocks even a single sentinel-enabled iteration before a runtime lease is acquired.
- `scripts/devstream_session.py`: supervisor auto-improve now requires `readyForRecursiveAutoImprove=true`, runs `infrastructure/improve_loop.py --iterations 1` instead of direct `improve_agent.py`, and only runs `elo_watchdog.py` after the loop child exits cleanly.
- Tests updated/added for the stricter supervisor gate, single-iteration readiness block, replay-save task bounds, and pending replay handoff expiry.

## Evidence

Commands run on JIGGLYPUFF with `D:\Projects\fouler-play\.venv\Scripts\python.exe`:

- `git diff --check` -> `0`
- `python -m py_compile bot_monitor.py fp\run_battle.py infrastructure\improve_loop.py scripts\devstream_session.py` -> `0`
- Focused pytest:
  `python -m pytest tests\test_bot_monitor_replay_handoff.py tests\test_runtime_contracts.py tests\test_improve_loop_agent_failure.py tests\test_devstream_session_recovery.py tests\test_discord_reporting.py -q`
  -> `94 passed`
- Full pytest:
  `python -m pytest tests -q`
  -> `1130 passed, 2 warnings`
- Non-live readiness:
  `python infrastructure\improve_loop.py --readiness`
  -> exited `0`; `readyForOfflineIteration=false`, `readyForRecursiveAutoImprove=false`

Readiness output blockers at verification time:

- `auto-improvement disabled; set FOULER_PLAY_ENABLE_AUTO_IMPROVE=1 or pass --enable-auto-improve`
- `battle evidence stream stale (1879m since newest battle)`

## Go / No-Go

No-go for recursive auto-improvement and live ladder resume from this patch alone. The code gate is stricter, but verification still shows the battle evidence stream is stale and auto-improve is not enabled.

Safe next dry-run command, does not launch battles because `--execute` is omitted:

`D:\Projects\fouler-play\.venv\Scripts\python.exe scripts\devstream_session.py start --run-count 1 --max-concurrent-battles 1 --queue-timeout-seconds 180`

Rollback after commit: `git revert <commit>` on `fix/atomic-singleton-lock-20260604`.
