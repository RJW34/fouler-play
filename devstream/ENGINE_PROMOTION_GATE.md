# Fouler Engine Promotion Gate

Fouler engine changes are candidates until historical proof promotes them.
Autoresearch may propose a repair, but it does not accept a repair by itself.

The promotion gate is written by:

```powershell
.\.venv\Scripts\python.exe scripts\fouler_engine_promotion_gate.py --write
```

Exit codes are part of the contract:

- `0`: the candidate is promotion-ready.
- `2`: the candidate is intentionally blocked by historical proof. This is a
  valid gate result, not a tooling failure.
- Any other nonzero exit: treat as a gate/tooling failure and repair the proof
  reader before making gameplay changes.

Operator or acceptance flows that need to continue into the digest should allow
exit `2` explicitly:

```powershell
.\.venv\Scripts\python.exe scripts\fouler_engine_promotion_gate.py --write
if ($LASTEXITCODE -notin 0,2) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe scripts\fouler_cycle_digest.py --write
```

It writes:

- `devstream/truth/engine-promotion-gate.json`
- `devstream/truth/engine-promotion-gate.md`

The gate is read-only with respect to runtime. It does not start battles, post to
Discord, touch OBS, or read secrets.

## Promotion Rules

A packet is not promotable unless all of these are true:

- The latest work packet is implemented and has ground-truth evidence.
- The packet has offline acceptance evidence.
- `post-packet-eval.json` is accepted and preservation proof is satisfied.
- Offline eval result proof is accepted.
- Autoresearch issue shifts do not show new worsened failure classes.
- Rating truth is coherent across battle stats and live profile proof.
- Decision trace history exists and does not show high-regret selected moves.

If the gate is blocked, `scripts/fouler_cycle_digest.py` ranks
`engine-promotion` ahead of routine ladder continuation. This prevents narrow
replay fixes from being silently treated as accepted engine upgrades while the
broader bot regresses.
