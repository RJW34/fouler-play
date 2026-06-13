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

The doctor also verifies Node/npm/git and the configured Pokemon Showdown
checkout by metadata only. It does not run `npm`, start Showdown, launch
Discord, start ladder battles, or manage services.

## HERMES No-Runtime Useful-Work Proof

When no live battle runner is leased, HERMES should prove whether Fouler is
still producing useful work through offline analysis instead of treating stale
runtime truth as progress.

Use this no-start status command for handoff reports:

```powershell
python scripts/devstream_health.py --skip-http
```

The health payload includes:

- `offlineEvalReadiness`: summarized output from the offline eval doctor in
  no-start mode.
- `readiness.offlineEvalReady`: whether the offline recursive-improvement gate
  is ready without considering a live Showdown server probe.
- `usefulWorkProof`: one combined HERMES signal that is ready only if a live
  bounded battle runtime, completed cycle proof, or offline eval harness can
  prove current work.

For completion-level proof, run the stricter gate:

```powershell
python infrastructure/offline_eval_readiness.py --require-ready
```

Do not archive stale runtime truth, clean dead offline status files with
`--execute-cleanup`, start Showdown, run ladder battles, post to Discord, or
touch JIGGLYPUFF scheduled tasks unless a current finite HERMES proof-window
lease authorizes that action.

## Provisioning

On Windows:

```powershell
py -3 -m venv .venv-eval
.venv-eval\Scripts\python.exe -m pip install --upgrade pip
.venv-eval\Scripts\python.exe -m pip install -r infrastructure\requirements-eval.txt
```

`.venv-eval` is only the poke-env challenger environment. The Fouler side of
the harness runs `run.py` with a separate runtime Python that can import
`requirements.txt` dependencies such as `aiohttp` and `poke-engine`. By default
the harness probes the current interpreter, `.venv`, and the system Python
launcher. Override it when needed:

```powershell
$env:FOULER_RUNTIME_PYTHON = 'py -3'
```

On Linux:

```bash
python3 -m venv .venv-eval
.venv-eval/bin/python -m pip install --upgrade pip
.venv-eval/bin/python -m pip install -r infrastructure/requirements-eval.txt
```

### Pokemon Showdown

The default checkout location is a sibling of this repo:

```text
..\pokemon-showdown
```

Override it with `POKEMON_SHOWDOWN_DIR` when the checkout lives elsewhere. The
readiness doctor expects that directory to contain `package.json`, the
`pokemon-showdown` launcher file, and installed `node_modules`.

Provision or repair the checkout without starting it:

```powershell
$showdown = 'C:\Users\mtoli\Documents\Code\pokemon-showdown'
if (!(Test-Path -LiteralPath $showdown)) {
  git clone https://github.com/smogon/pokemon-showdown.git $showdown
}
Push-Location -LiteralPath $showdown
npm ci
Pop-Location
```

On Linux:

```bash
showdown=/home/ryan/pokemon-showdown
test -d "$showdown" || git clone https://github.com/smogon/pokemon-showdown.git "$showdown"
cd "$showdown"
npm ci
```

Only after provisioning, an operator can start a local no-security server on the
configured eval port. The doctor reports the cwd under
`commands.showdownServerCwd` and the command under `commands.showdownServer`;
default command:

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
- `POKEMON_SHOWDOWN_DIR`, default sibling `..\pokemon-showdown`

## Required Proof

The readiness doctor requires:

- `.venv-eval` Python exists and can import `poke_env` and `websockets`
- Fouler runtime Python can import the `run.py` dependencies from
  `requirements.txt`
- `node`, `npm`, and `git` are available for reproducible Showdown provisioning
- configured Pokemon Showdown checkout has `package.json`, `pokemon-showdown`,
  and installed `node_modules`
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
