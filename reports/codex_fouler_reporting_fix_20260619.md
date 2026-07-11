# Codex Fouler Reporting Repair - 2026-06-19

## Scope

Live repo: `D:\Projects\fouler-play` on JIGGLYPUFF.

User-visible defects repaired:

- Discord battle reports had wrong "last 5" windows because the report could be queued before the just-finished battle was visible in `battle_stats.json`.
- Showdown timeouts / disconnect-style terminal states were being treated like ties by parts of the reporting path instead of operational losses.
- The "What happened" and `viewerSummary` fields reused fake or repetitive flavor text and sometimes captured stale short-id prose as the opponent.
- Already formatted stale queued content could bypass the newer formatter and still post bad Discord text.

## Root Cause

`fp/run_battle.py` generated report text while live stats enrichment was still catching up, and it carried a short-id recap such as `2635342342: win vs ...` plus generic win flavor into the event payload. `infrastructure.discord_reporting.redacted_report_summary(...)` then parsed formatted battle text by joining the headline and "What happened" with a space, which let the opponent capture the next short-id sentence. Finally, `infrastructure.event_poster.post_to_discord(...)` posted some already formatted queue content without normalizing it through the fixed formatter.

## Files Changed

- `infrastructure/discord_reporting.py`
  - Sanitizes opponent names in payload and formatted-report paths.
  - Rebuilds already formatted stale battle contracts when they contain parseable battle proof.
  - Replaces short-id recaps and generic battle-ended text with concrete fact lines.
  - Merges the current result into recent-window summaries and dedupes across `battle_id`, `battle_tag`, and `id`.
  - Adds safety alert helpers for loss streaks and low recent decisive win rate.

- `infrastructure/event_poster.py`
  - Normalizes `event["content"]` through `format_payload_or_message(...)` immediately before validation and transport.
  - This prevents old in-memory or stale queued formatted content from bypassing the fixed reporting formatter.

- `fp/run_battle.py`
  - Treats no-winner timeout / disconnect terminal states as operational losses.
  - Queues operational-loss battle reports.
  - Uses `summarize_recent_results_with_current(...)` for last-5 / trend windows.
  - Stops generating the stale short-id "what happened" line and routine generic win flavor.

- `tests/test_discord_reporting.py`
  - Covers operational losses, current-battle merge, alias dedupe, opponent sanitation, stale formatted contract rebuilds, and safety alert triggers.

## Backups And Rollback

Latest pre-deploy backups created on JIGGLYPUFF:

- `D:\Projects\fouler-play\fp\run_battle.py.pre-codex-reporting-fieldfix-20260619T183855Z.bak`
- `D:\Projects\fouler-play\infrastructure\discord_reporting.py.pre-codex-reporting-fieldfix-20260619T183855Z.bak`
- `D:\Projects\fouler-play\infrastructure\event_poster.py.pre-codex-reporting-fieldfix-20260619T183855Z.bak`
- `D:\Projects\fouler-play\tests\test_discord_reporting.py.pre-codex-reporting-fieldfix-20260619T183855Z.bak`

Earlier same-day backups with suffixes `20260619T183032Z`, `20260619T183226Z`, and `20260619T183256Z` are also present.

Rollback path if needed: drain the live ladder session, copy the matching `.bak` files back over the four changed files, rerun the reporting tests, and restart only the Fouler battle supervisor / event poster path.

## Verification

Focused test command on JIGGLYPUFF:

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_discord_reporting.py tests/test_timeout_result_accounting.py
```

Result:

```text
59 passed in 4.87s
```

Only observed warning: pytest-asyncio deprecation about unset default fixture loop scope.

Discord queue / poster proof:

- Queue health after correction: `pending=0`, `webhookFailures=0`.
- Correction event for the last stale post: `reporting_correction` id `cdaab6fe-f88`, posted HTTP 204 at `2026-06-19 14:39:56`.
- Fresh patched-runner event: `battle_result` id `b4f24bf5-6aa`, battle `battle-gen9ou-2635351471`, posted HTTP 204 at `2026-06-19 14:45:47`.

Fresh live report content proved:

- Headline: `battle result win vs Mang0.cuh`
- Battle state: `battle win; vs Mang0.cuh; 18 turns; id 2635351471; public replay gen9ou-2635351471`
- Viewer summary: `battle finished win vs Mang0.cuh using gen9ou in 18 turns; replay public; ELO gained 17 (1338 -> 1355, +17); last 5: 3-2 (60% WR)`
- No stale short-id `2635351471: win vs ...` What Happened prose.
- No repeated `Win has public replay proof` flavor.
- No opponent pollution.
- Last-five window matches the latest five rows in `battle_stats.json`.

## Live Runtime State

At the latest check:

- One active ladder battle was running: `battle-gen9ou-2635353458` vs `soumatou_story`.
- `max_concurrent_battles` was `1`.
- No duplicate `run.py` ladder clients were visible.
- The Fouler OBS page server was still running.

## Remaining Gaps

- `devstream\truth\runtime-lease.json` still showed an expired proof window while the supervisor reported the lease as active. The current supervisor cycle continued running, but HERMES should not treat an expired lease timestamp as healthy without an explicit renewal or grace-state explanation.
- `stream_status.json` can lag active battle truth; it showed `status: Searching` while `active_battles.json` had a live battle. This is a reporting freshness problem for OBS / HERMES surfaces, not a battle-client duplicate.
- The repo has many pre-existing uncommitted files and backup artifacts. This report only covers the reporting fix files listed above.

## Next Verification

For the next Fouler pass, verify:

- `devstream\truth\runtime-lease.json` is renewed or the supervisor refuses to start after expiry.
- `stream_status.json` reflects active battle state within one update interval.
- The next posted `battle_result` after `battle-gen9ou-2635351471` keeps the same clean reporting shape.
