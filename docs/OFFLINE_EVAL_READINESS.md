# Fouler Evaluation Readiness

Fouler has two local evaluation surfaces with different authority:

1. `infrastructure/offline_eval.py` plays against a simple, max-base-power, or
   random poke-env opponent. It is useful for transport and gross-regression
   smoke only. It can never authorize an engine promotion.
2. `infrastructure/head_to_head_eval.py` plays the candidate engine directly
   against the exact frozen Git checkout. This is the only local battle result
   that can authorize promotion or reopen a stop-loss recovery window.

Do not substitute one proof for the other.

## Read-Only Doctor

Run the weak-baseline infrastructure doctor without starting ladder battles or
Discord reporting:

```powershell
python infrastructure/offline_eval_readiness.py --require-ready
```

The doctor verifies the eval Python, runtime imports, Node/npm/git, the local
Pokemon Showdown checkout, team file, frozen smoke artifact, stale status files,
and process-lock state. Unless cleanup is explicitly requested under a valid
lease, it does not mutate files, start Pokemon Showdown, launch Fouler, or post
events.

`recursiveImprovementReady=true` means the local harness prerequisites are
available. It does not mean an engine candidate has passed promotion.

## Weak-Baseline Smoke

The legacy smoke commands remain useful for confirming end-to-end transport:

```powershell
.venv-eval\Scripts\python.exe infrastructure\offline_eval.py --battles 200 --team gen9/ou/fat-team-1-stall --baseline simple --label frozen --no-setsample --search-time-ms 100 --concurrency 3 --manage-showdown-server
.venv-eval\Scripts\python.exe infrastructure\offline_eval.py --battles 200 --team gen9/ou/fat-team-1-stall --baseline simple --label candidate --search-time-ms 100 --concurrency 3 --manage-showdown-server
.venv-eval\Scripts\python.exe infrastructure\offline_eval.py --compare frozen candidate
```

The comparison artifact always emits:

- `promotion_eligible=false`
- `ACCEPT=false`
- a `smoke_passed` transport/gross-regression signal

`infrastructure/offline_eval_readiness.py` maps older `ACCEPT=true` artifacts to
legacy smoke evidence only. Its result proof always reports `accepted=false` and
`promotionEligible=false`.

## Candidate-Vs-Frozen Gate

The discriminating gate requires exactly one unstaged change to an allowlisted
engine file. Staged changes and unrelated tracked changes fail closed.

```powershell
python infrastructure/head_to_head_eval.py `
  --candidate-file fp/search/main.py `
  --battles 60 `
  --require-promotion
```

The 60-battle default is a balanced matrix:

- three mission teams: stall, balance, and Dondozo
- every ordered non-mirror team pairing
- candidate as challenger and accepter
- five battles in each of 12 cells

Promotion requires all of the following:

- exactly 60 or more completed battles in a multiple of 12
- no ties, disconnect truth gaps, duplicate battle IDs, underfilled cells, or
  nonzero agent exits
- candidate effect at least `+10%` over frozen
- one-sided exact-binomial `p < 0.01`
- one durably pre-registered attempt from a maximum of five candidate trials for
  the evaluated runtime family; Bonferroni bounds the family to `alpha <= 0.05`
- no below-50% candidate result by connection role or candidate team
- exact frozen commit, candidate file, binary patch SHA-256, raw arm files,
  Python/package set, Showdown checkout, controller, and runtime provenance
- all temporary agents, Showdown sidecars, and Git worktrees cleaned

`eval_results/head_to_head/latest.json` is only a hash-addressed pointer to the
canonical `<runId>/result.json`. It is written only after temporary-worktree
cleanup and durable attempt-ledger finalization succeed.

For an operational identical-code smoke, use one battle per cell:

```powershell
python infrastructure/head_to_head_eval.py `
  --candidate-file fp/search/main.py `
  --battles 12 `
  --allow-identical-smoke
```

An identical or sub-60 smoke is always `promotion-blocked`, even when all 12
cells execute cleanly.

## Checkout Provenance

`infrastructure/head_to_head_proof.py` validates both artifact structure and
the current checkout. A previously accepted artifact becomes unusable when:

- its frozen commit is missing or is not an ancestor of `HEAD`
- a different engine file changed after the frozen commit
- the proven candidate file has a different binary patch hash
- the current declared runtime closure has tracked or untracked changes
- matrix cell, battle-ID, team, role, result, effect, or p-value evidence is
  incomplete

This prevents stale `latest.json` state from reopening laddering after newer
engine code replaces the proven candidate.

## Stop-Loss Recovery

After a ladder stop-loss, `scripts/fouler_mission_monitor.py` reports the
compatibility field `offlineEvalResumeProof`, but policy
`fouler-head-to-head-resume-proof/v1` now reads only the candidate-vs-frozen
artifact and checkout provenance. The weak-baseline candidate/compare files do
not satisfy this gate.

One bounded live recovery window still requires a fresh implemented work packet,
post-packet battle evidence, autoresearch coverage, and a valid finite runtime
lease in addition to the accepted head-to-head proof.

## Health Output

```powershell
python scripts/devstream_health.py --skip-http
```

The health payload exposes weak smoke separately:

- `offlineEvalReadiness`: read-only harness prerequisite status
- `usefulWorkProof.offlineEvalResultProof`: weak-baseline smoke artifact
- `usefulWorkProof.weakBaselineSmokeProofReady`: smoke passed, never promotion
- `usefulWorkProof.status=offline-eval-smoke-passed`: useful offline evidence,
  not permission to deploy or resume laddering

## Configuration

Candidate-vs-frozen gate settings:

- `IMPROVE_AGENT_EVAL_BATTLES`, default `60`; must be at least 60 and a
  multiple of 12
- `IMPROVE_AGENT_EVAL_TEAMS`, fixed to the three mission benchmark paths
- `IMPROVE_AGENT_EVAL_SHOWDOWN_PORT`, default `8791`
- `IMPROVE_AGENT_EVAL_SEARCH_TIME_MS`, default `1200`
- `IMPROVE_AGENT_EVAL_PER_BATTLE_TIMEOUT`, default `240`
- `IMPROVE_AGENT_TEST_TIMEOUT_SECONDS`, default `360`

The attempt ledger is not environment-configurable. Production eval and proof
always read the immutable authority file at
`~/.deku/state/fouler-h2h-ledger-authority.json`. That file pins the absolute
SQLite path and ledger ID and is cross-bound to matching metadata in the
database. Legacy `FOULER_H2H_LEDGER_PATH` and `FOULER_H2H_LEDGER_ID` values are
ignored.

Provision the authority and ledger explicitly once:

```powershell
$ledgerId = [guid]::NewGuid().ToString("N")
python infrastructure/head_to_head_eval.py `
  --initialize-ledger `
  --ledger-id $ledgerId
```

`--ledger-path <absolute-path>` may select the SQLite location only during this
one-time initialization. Both files are created exclusively; initialization
refuses to replace either one. Normal evaluation opens the pinned database
read/write but never creates or replaces it, while proof opens it read-only. A
killed or failed attempt still spends its slot. The budget is keyed by the
frozen runtime, controller, Python/package set, Showdown checkout, and closed
child environment, so unrelated Git commits do not reset the family. A missing,
malformed, moved, writable, or replaced authority, or mismatched database
identity, blocks evaluation and proof instead of silently resetting history.

The supervisor defaults `--improve-timeout-seconds` to 18000 and refuses an
explicit timeout below the matrix-derived floor. Its improve-agent runtime lease
uses the evaluation battle count, not the live ladder batch size.

Weak-smoke doctor settings remain independent:

- `IMPROVE_AGENT_EVAL_CONCURRENCY`
- `IMPROVE_AGENT_EVAL_MANAGE_SHOWDOWN`
- `EVAL_SHOWDOWN_ADOPT_EXISTING`
- `IMPROVE_AGENT_EVAL_TEAM`
- `IMPROVE_AGENT_EVAL_BASELINE`
- `EVAL_SHOWDOWN_PORT`, default `8765`
- `POKEMON_SHOWDOWN_DIR`, default sibling `..\pokemon-showdown`

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

The sibling Pokemon Showdown checkout must contain `package.json`, the
`pokemon-showdown` launcher, and installed `node_modules`. Managed evaluation
starts its own local `--no-security` sidecar and must stop it before publishing
the proof artifact.
