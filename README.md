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
