# AGENTS.md — Fouler Play

> Codex-and-friends orientation. **Full agent manual is `CLAUDE.md`** in this directory — read that first; this file is the quick "where do I start" pointer.

## What this repo is

An overnight team-testing service for competitive Pokemon (gen9ou): load fat/stall teams, the bot ladders them while you sleep, you get a morning report on hard matchups and underperforming Pokemon. Forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play).

## Required reading order for a fresh session

1. `CLAUDE.md` — the full operating manual (machine roles, responsibilities, mission, never-modify files)
2. `TASKBOARD.md` — current phase + cross-machine action items
3. `infrastructure/guardrails.json` — protected files + safety thresholds
4. `git log --oneline -5` + `git status --short` — recent state
5. Confirm working branch matches what `TASKBOARD.md` says (not just `master`)

## Build / test

```bash
pip install -r requirements.txt           # poke-engine builds from source; needs Rust
python -m pytest tests/ -v                # full test suite
python -c "import ast; ast.parse(open('fp/search/main.py').read())"   # syntax check
python -c "from fp.search.main import find_best_move; print('OK')"    # import smoke
```

Runtime:
```bash
python run.py                             # play one session
python pipeline.py analyze -n 30          # batch analyze last 30 battles + autoresearch
python pipeline.py autoresearch -n 30     # autoresearch only
```

## DO NOT modify

Per `infrastructure/guardrails.json` and CLAUDE.md "NEVER MODIFY" lines:
- `run.py`, `config.py`, `.env`, `teams/**`
- `fp/data/movepool_data.json` is often locally dirty from refresh churn — treat as unrelated unless the task is specifically movepool data
- Files matching `never_modify` in guardrails.json

## Architectural conventions

- **Penalties, not blocks** — reduce move weights, never remove options entirely
- **Ground all Pokemon facts** via `data.pokedex_oracle.oracle` (types, abilities, moves from `data/pokedex.json` + `data/moves.json`). Never use LLM knowledge for game data
- **One improvement per cycle** — small correct changes beat ambitious broken ones
- **Tests must pass** before every push: `python -m pytest tests/ -v`

## Machine roles (active devstream as of 2026-05)

- **DEKU on ubunztu** (Linux) = brains: decision-making improvements, replay analysis, dev loop, tests, upstream merges
- **JIGGLYPUFF** (Windows) = brawn: bot runtime, battle data collection, environment, ELO monitoring
- Hostname detection is **OS-based not name-based** — see CLAUDE.md "How to identify your machine"

Note: `MAGNETON` mentioned in CLAUDE.md as a possible runtime hostname is RETIRED as of 2026-05-13 (see the-abso-citadel/docs/hermes/HERMES-BODY-2026-05-13.md). Treat MAGNETON references in this repo as historical.

## Pushing

`master` branch on `origin` (github.com/RJW34/fouler-play). Pull `upstream` (pmariglia/foul-play) for upstream merges.
