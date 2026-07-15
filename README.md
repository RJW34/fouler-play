# Fouler Play

> **Mission and intent: see [MISSION.md](MISSION.md) -- what this project is FOR (read first).**
> **Architecture: see [ARCHITECTURE.md](ARCHITECTURE.md) (the truthful current engine map).**

A finite-lease competitive Pokemon (gen9ou) battle agent for the DEKU/HERMES devstream. Live ladder work runs in bounded proof windows, while recursive improvement work runs through a local Pokemon Showdown offline-eval harness before any change is trusted.

Forked from [pmariglia/foul-play](https://github.com/pmariglia/foul-play).

## Quick Start

1. Copy `.env.example` to `.env` and set `PS_USERNAME` and `PS_PASSWORD`
2. Install requirements: `pip install -r requirements.txt`
3. Run local tests: `python -m pytest -q`
4. Inspect a runtime plan: `python scripts/devstream_session.py doctor`

Live laddering is never started directly. Production uses an immutable clean
release, deployment receipt, finite v2 lease, and
`HERMES-FoulerBattleSupervisor` via `scripts/install_battle_supervisor_task.ps1`.
`start_one_touch.bat`, direct launchers, and legacy watchdogs are fail-closed
tombstones retained only so stale tasks cannot revive old behavior.

## Architecture

```
run.py                     Entry point + battle-worker pool
fp/search/main.py          Decision engine: clock-safety -> endgame -> forced-line -> MCTS -> argmax
fp/search/forced_lines.py  Forced sequence detection (OHKOs, forced switches)
fp/search/eval.py          1-ply position evaluation (eval-fallback path only)
fp/battle_modifier.py      Pokemon Showdown protocol parser
fp/run_battle.py           Battle loop + data collection
replay_analysis/           Morning report generator
```

The engine is **MCTS-first**: it searches over Bayesian-sampled opponent sets and picks the
top line by deterministic argmax under clock-safety and hard-legality/survival safety. The
older heuristic **penalty pipeline is default-OFF** (`FOULER_PENALTY_PIPELINE=0`) and only
runs on the eval-fallback branch. It plays teams faithfully to their archetype (fat/stall)
rather than optimizing for cheese wins. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full
verified pipeline and a layer-by-layer ON/OFF/DORMANT status table.

## Autoresearch MVP

Fouler Play now includes a deterministic end-to-end autoresearch loop built on artifacts the bot already produces:
- `battle_stats.json` for the recent result window
- `replay_analysis/*.json` for saved replay logs
- `logs/decision_traces/*.json` for per-turn choice/fallback evidence

Run it directly:

```bash
python pipeline.py autoresearch -n 30
```

Or let the normal batch pipeline trigger it alongside the existing batch report:

```bash
python pipeline.py analyze -n 30
```

Outputs:
- `replay_analysis/autoresearch_latest.json`
- `replay_analysis/reports/autoresearch_latest.md`

## DEKU Static Eval Loop

The offline eval harness is the preferred static process for HERMES/DEKU improvement work:

```bash
python infrastructure/offline_eval_readiness.py --require-ready
python infrastructure/offline_eval.py --battles 200 --team gen9/ou/fat-team-1-stall --baseline simple --label frozen --no-setsample --search-time-ms 100 --manage-showdown-server
python infrastructure/offline_eval.py --battles 200 --team gen9/ou/fat-team-1-stall --baseline simple --label candidate --search-time-ms 100 --manage-showdown-server
python infrastructure/offline_eval.py --compare frozen candidate
```

`IMPROVE_AGENT_EVAL_SEARCH_TIME_MS` controls the generated readiness commands. `IMPROVE_AGENT_EVAL_MANAGE_SHOWDOWN` defaults on so each bounded eval starts and stops its own local no-security Showdown sidecar instead of relying on a resident server.

Discord wiring:
- routine summary -> `1466691161363054840`
- deeper autoresearch post -> `1466869808200028264`

Current MVP focuses on real recurring-loss signals rather than fake AI commentary:
- hazard-pressure failures
- early-material bleeding
- long-game/endgame conversion losses
- decision-trace fallback/timeout instability

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
