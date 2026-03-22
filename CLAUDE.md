# Fouler Play

Competitive Pokemon Showdown ladder bot for `npctypebeat`, running three concurrent Gen 9 OU battles and feeding the live DEKU stream stack.

## Mission

Prove that the three specified fat/stall teams can climb meaningfully toward the high ladder while preserving their intended playstyle. The bot must:
- keep battling continuously
- improve through autoresearch
- report batch-level results truthfully
- remain streamable with working overlays and browser panels

1700 ELO is the quality bar for useful competitive validation, not just a vanity number.

## Canonical Landscape Contract

### Host roles
- **MAGNETON**: Fouler Play battle runtime, stream server, browser panels, DEKU control plane
- **JIGGLYPUFF**: OBS / livestream host
- **ubunztu**: Pokemon AI RTMP/runtime host for Emerald / Fire Red, not the Fouler Play execution host

### Discord roles
- `#deku-workspace` is the only control room
- `#fouler-play` is a canonical proof/state feed
- project feed updates should come from canonical workspace reporting, not ad hoc repo-local chatter

## Canonical Repo

Active working copy:
- `D:\Projects with Claude\fouler-play`

Stale copy:
- `D:\Projects\fouler-play` (do not use)

Branch truth:
- `master` is the canonical branch unless current git state proves otherwise

## Runtime Truth

### Windows services on MAGNETON
- `FoulerPlayPlayerLoop`: canonical player loop owner
- `FoulerPlayStreamServer`: canonical stream server / browser panel owner

Do not reintroduce duplicate scheduled-task owners for the player loop.

### Stream surfaces
- Stream server runs on `http://127.0.0.1:8777`
- OBS on JIGGLYPUFF uses the browser sources and battle slots provided by that server
- Fouler Play is part of the live stream stack, not an optional side project

## Batch / Autoresearch Contract

The canonical reporting unit is a **30-battle batch**:
- `10` battles with `fat-team-1-stall`
- `10` battles with `fat-team-2-pivot`
- `10` battles with `fat-team-3-dondozo`

Every completed batch should surface:
- total batch record
- per-team split sanity against the `10/10/10` contract
- latest autoresearch finding / focus area
- any clear runtime or decision-quality drift

Canonical batch reporting path:
- `D:\deku-workspace\scripts\fouler-play-pulse.py`

Canonical autoresearch wrapper:
- `D:\deku-workspace\scripts\run-fouler-play-autoresearch.ps1`

Do not rely on old direct Discord webhook/report scripts as the source of truth.

## Development Loop

1. Confirm the bot is actually battling, not just "process running"
2. Review recent losses / battle output / decision traces
3. Run or inspect autoresearch findings
4. Make one targeted improvement
5. Validate with tests
6. Let the next real battle batch measure whether the change helped

## Code Areas That Matter Most

- `fp/search/main.py`: decision engine core
- `fp/search/eval.py`: position evaluation
- `fp/playstyle_config.py`: fat/stall tuning
- `fp/run_battle.py`: battle loop behavior
- `infrastructure/autoresearch/`: research and improvement loop
- `streaming/serve_obs_page.py`: browser panel server
- `streaming/fouler_stats.html`: stream-facing stats panel

## Guardrails

- Read `infrastructure/guardrails.json` before editing
- Do not modify protected files listed there
- Do not treat teams as disposable; improve decision-making instead
- Do not fake health because a process exists

## Reporting Contract

Follow `D:\deku-workspace\docs\TEAM_REPORTING_CONTRACT.md`.

For `#fouler-play`, the project feed should emphasize:
- batch completion
- autoresearch findings
- recoveries that changed runtime truth
- proof-backed stagnation when the bot is alive but not producing useful results

Do not spam per-battle chatter into the feed.

## Stream Readiness Relationship

Fouler Play is part of the stream landscape:
- battle slots must render
- Fouler Stats must show truthful totals/current state
- OBS scenes on JIGGLYPUFF must show the live Fouler Play panels correctly

If the stream surface is stale or broken, that is a real project blocker.

## Testing

```bash
python -m pytest tests/ -v
python -c "from fp.search.main import find_best_move; print('OK')"
```

Prefer small, measurable fixes with proof over broad speculative rewrites.

## Git Hygiene

This repo should only stay dirty for intentional source work. Runtime files like batch stats, Discord state, research logs, and temp outputs stay local.

Canonical check:

```powershell
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py check --repo fouler-play --runtime-only
```

If runtime artifacts were ever tracked, strip them from the index first:

```powershell
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py fix-runtime --repo fouler-play
```
