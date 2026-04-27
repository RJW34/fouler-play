# fouler-play Performance Loop

The product goal is a high-level Gen 9 OU bot, not merely a bot that can queue games. The target is sustained 1800+ ELO performance and credible games against humans around that rating.

## Metrics

- Current ladder ELO and peak session ELO.
- Win/loss record per bounded batch.
- Results against rating bands when opponent rating is known.
- Loss reasons grouped by matchup, decision error, team preview issue, endgame conversion, and invalid-choice recovery.
- Decision trace coverage for each completed game.
- Replay links for representative wins and losses.

## DEKU Linkage

DEKU should consume the batch outputs and produce constrained work packets, not broad rewrites. A good packet names:

- evidence from replays or decision traces
- the suspected failure class
- the exact code area allowed to change
- the validation commands
- the next battle-batch proof expected after the patch

## Batch Lifecycle

1. Start a bounded batch with declared run count and team.
2. Keep OBS surfaces live for battles, stats, and current analysis state.
3. Write runtime truth while battles are active.
4. Drain active battles before stopping.
5. Write a batch completion file with counts, rating movement, replay ids, and report paths.
6. Generate DEKU work packets from the report.

## Launch Gaps

- No canonical devstream start wrapper yet.
- No drain-first stop wrapper yet.
- No formal 1800+ ELO proof file yet.
- Some legacy services/docs still duplicate pipeline intent.
- DEKU linkage needs to be constrained around reports and work packets instead of free-form edits.
