# Fouler Play — Codex Instructions

Competitive Pokemon Showdown ladder bot for `npctypebeat`, integrated into the DEKU stream stack.

## Critical Rules

- NEVER use `start`, `Invoke-Item`, `explorer`, `ii`, or `open` to open files
- NEVER modify `teams/`, `config.py`, `run.py`, `.env`, or files blocked by `infrastructure/guardrails.json`
- NEVER blame the teams first; improve the bot's decisions and reporting truth
- Use `C:\Python314\python.exe` for Python on MAGNETON
- Read `infrastructure/guardrails.json` before editing

## Architecture

- Runtime host: MAGNETON
- Canonical player-loop owner: `FoulerPlayPlayerLoop`
- Canonical stream-server owner: `FoulerPlayStreamServer`
- Browser panels served from `http://127.0.0.1:8777`
- OBS host: JIGGLYPUFF

## Batch / Autoresearch Contract

1. Run 30 battles
2. Maintain the `10/10/10` team split across the three canonical teams
3. Analyze the latest losses / matchup patterns
4. Implement one targeted fix
5. Validate with tests
6. Let the next real batch prove whether the fix helped

Useful analysis command:
```powershell
C:\Python314\python.exe -X utf8 -c "from infrastructure.autoresearch.matchup_analyzer import get_competitive_brief; print(get_competitive_brief())"
```

## Key Files

- `fp/search/main.py` — decision engine
- `fp/playstyle_config.py` — team-style tuning
- `battle_stats.json` — current runtime results
- `infrastructure/autoresearch/` — research loop
- `streaming/serve_obs_page.py` — stream server
- `streaming/fouler_stats.html` — live Fouler Stats panel

## Service Commands

```powershell
sc.exe query FoulerPlayPlayerLoop
sc.exe query FoulerPlayStreamServer
```

## Discord Reporting

- `#fouler-play` is a canonical feed, not a troubleshooting chat
- Canonical batch reporting runs through `D:\deku-workspace\scripts\fouler-play-pulse.py`
- Canonical project-feed events should be emitted through `D:\deku-workspace\scripts\symphony.py emit-event`
- Do not rely on stale direct repo-side Discord reporting assumptions as the reporting source of truth

Every batch report should include:
- batch record
- `10/10/10` split sanity
- latest autoresearch finding
- any proof-backed drift or recovery notes

## Git Hygiene

Keep runtime churn out of git. Before and after work, use:

```powershell
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py status --repo fouler-play
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py check --repo fouler-play --runtime-only
```

If runtime files were ever tracked, untrack them first:

```powershell
C:\Python314\python.exe D:\deku-workspace\scripts\git_hygiene.py fix-runtime --repo fouler-play
```
