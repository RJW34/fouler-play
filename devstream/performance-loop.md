# fouler-play Performance Loop

The product goal is a high-level Gen 9 OU bot, not merely a bot that can queue games. The target is sustained 1800+ ELO performance and credible games against humans around that rating.

## Metrics

- Current ladder ELO and peak session ELO.
- Win/loss record per bounded batch.
- Results against rating bands when opponent rating is known.
- Loss reasons grouped by matchup, decision error, team preview issue, endgame conversion, and invalid-choice recovery.
- Decision trace coverage for each completed game.
- Replay links for representative wins and losses.
- Machine-readable ELO proof conforming to `devstream/truth/elo-proof.schema.json`.

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
5. Write a batch completion file with counts, rating movement, replay ids, report paths, and ELO proof.
6. Generate DEKU work packets from the report.

## Launch Gaps

- `scripts/devstream_session.py` plans bounded start/stop behavior, but the reviewed execute path is still intentionally blocked.
- No drain-first stop executor yet.
- The 1800+ ELO proof schema exists, but no live proof file is generated at batch end yet.
- Some legacy services/docs still duplicate pipeline intent.
- `scripts/devstream_packetize.py` can draft packets from autoresearch output; the write path still needs human review and orchestration policy.
