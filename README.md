# Fouler Play

An overnight team-testing service for competitive Pokemon (gen9ou). Load your fat/stall teams, let the bot play them on ladder while you sleep, and get a morning report: which matchups were hard, which Pokemon underperformed, which replays to study.

Forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play).

## Quick Start

1. Copy `.env.example` to `.env` and set `PS_USERNAME` and `PS_PASSWORD`
2. Install requirements: `pip install -r requirements.txt`
3. Run: `python run.py` or double-click `start_one_touch.bat` (Windows)

## Architecture

```
run.py                     Entry point
fp/search/main.py          Decision engine: forced_lines -> eval -> penalty pipeline
fp/search/eval.py          1-ply position evaluation
fp/search/forced_lines.py  Forced sequence detection (OHKOs, forced switches)
fp/battle_modifier.py      Pokemon Showdown protocol parser
fp/run_battle.py           Battle loop + data collection
replay_analysis/           Morning report generator
```

The bot uses a 1-ply eval engine with 9 penalty layers to make decisions. It plays teams faithfully to their archetype (fat/stall) rather than optimizing for cheese wins.

## Agent Instructions

See [CLAUDE.md](CLAUDE.md) for autonomous agent operating instructions (DEKU/BAKUGO machines).

### Codex / coding-agent quick handoff

For coding agents or fresh sessions, the shortest reliable startup sequence is:

1. Read `CLAUDE.md`
2. Read `TASKBOARD.md`
3. Run `git status --short` and `git log --oneline -5`
4. Confirm the working branch matches current repo reality before editing (do not assume `master` if `TASKBOARD.md` says otherwise)
5. Respect `infrastructure/guardrails.json`
6. Check for local runtime-only changes before editing (`battle_stats.json`, logs, generated reports, data refreshes). In this repo, `fp/data/movepool_data.json` can also be locally dirty from refresh/reordering churn; treat it as unrelated unless your task is specifically about movepool data.

Recommended validation after any code change:

```bash
python -m pytest tests/ -v
python -c "import ast; ast.parse(open('fp/search/main.py').read())"
python -c "from fp.search.main import find_best_move; print('OK')"
```

Repo-specific cautions for coding agents:
- `run.py`, `config.py`, `.env`, and `teams/**` are protected
- working tree may contain unrelated local data churn (for example `fp/data/movepool_data.json`)
- many root docs describe experiments or ops snapshots; prefer `CLAUDE.md` + `TASKBOARD.md` as current intent

## Engine

This project uses [poke-engine](https://github.com/pmariglia/poke-engine) for battle simulation. Rust must be installed to build the engine from source.

```bash
pip install -r requirements.txt
```

To reinstall for a specific generation:
```bash
pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir poke-engine --config-settings="build-args=--features poke-engine/gen9 --no-default-features"
```

## Optional: Hybrid LLM Reranking

Hybrid mode keeps the normal eval engine, then asks an OpenAI model to rerank the top candidates. Set in `.env`:

```bash
DECISION_POLICY=hybrid
OPENAI_API_KEY_PLAYER=sk-...
```

Falls back to eval-only if no API key is configured.
