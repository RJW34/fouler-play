# Offline Eval Readiness

`infrastructure/improve_agent.py` fails closed when the offline eval harness is
missing or unusable. Do not bypass that gate. Use the read-only doctor first:

```powershell
python infrastructure/offline_eval_readiness.py --require-ready
```

The command prints JSON and does not start Pokemon Showdown, Discord posting,
ladder battles, HERMES/DEKU, or services. Recursive improvement may resume only
when `recursiveImprovementReady` is `true`.

By default the doctor also performs a read-only TCP probe against
`EVAL_SHOWDOWN_PORT` so a missing local eval server is reported before the
baseline command launches Fouler. Use `--skip-server-check` only when producing
static documentation/proof that should not touch the network.

## Provisioning

On Windows:

```powershell
py -3 -m venv .venv-eval
.venv-eval\Scripts\python.exe -m pip install --upgrade pip
.venv-eval\Scripts\python.exe -m pip install -r infrastructure\requirements-eval.txt
```

On Linux:

```bash
python3 -m venv .venv-eval
.venv-eval/bin/python -m pip install --upgrade pip
.venv-eval/bin/python -m pip install -r infrastructure/requirements-eval.txt
```

Install or clone Pokemon Showdown separately, then start a local no-security
server on the configured eval port. The doctor reports the exact command under
`commands.showdownServer`; default:

```bash
node pokemon-showdown start --no-security 8765
```

## Eval Commands

The recursive gate candidate command is reported under
`commands.candidateEval`. By default it is equivalent to:

```powershell
.venv-eval\Scripts\python.exe infrastructure\offline_eval.py --battles 200 --team gen9/ou/fat-team-1-stall --baseline simple --label candidate
```

Create the frozen proof baseline first:

```powershell
.venv-eval\Scripts\python.exe infrastructure\offline_eval.py --battles 200 --team gen9/ou/fat-team-1-stall --baseline simple --label frozen --no-setsample
```

After the improve gate runs a candidate, the compare proof command is:

```powershell
.venv-eval\Scripts\python.exe infrastructure\offline_eval.py --compare frozen candidate
```

Environment knobs consumed by both the doctor and improve gate:

- `IMPROVE_AGENT_EVAL_BATTLES`, default `200`
- `IMPROVE_AGENT_EVAL_TEAM`, default `gen9/ou/fat-team-1-stall`
- `IMPROVE_AGENT_EVAL_BASELINE`, default `simple`
- `EVAL_SHOWDOWN_PORT`, default `8765`

## Required Proof

The readiness doctor requires:

- `.venv-eval` Python exists and can import `poke_env` and `websockets`
- local no-security Pokemon Showdown is reachable on `EVAL_SHOWDOWN_PORT`
- `infrastructure/offline_eval.py` exists
- `infrastructure/_offline_baseline.py` exists
- configured team file exists
- `eval_results/offline/frozen.json` exists, has at least
  `IMPROVE_AGENT_EVAL_BATTLES` battles, and contains `label`, `battles`,
  `fouler_wins`, `fouler_win_rate`, and `fouler_wilson_lcb`

After a candidate run, improvement acceptance proof is:

- `eval_results/offline/candidate.json`
- `eval_results/offline/compare-frozen-vs-candidate.json`
