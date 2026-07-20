# Unattended operation (repeated bounded cycles)

How to make Fouler run more than one battle cycle without a human between
cycles, and why a scheduled trigger alone cannot do it.

Status as of 2026-07-20: the lease half is **done and live**. The supervisor
task still needs one elevated command. See "Current state" at the bottom.

---

## Why a timer cannot fix this

Each run needs a **registered lease with remaining cycles**. Firing the
supervisor on a timer against an exhausted or unregistered lease produces the
same error forever:

```
authorization_unknown: authorization digest is not registered
```

raised at `infrastructure/windows/fouler_lease_broker.py:899-903`, before any
action dispatch. Every lease issued before 2026-07-20 was `maxCycles: 1`, so
each one permitted exactly one cycle and then died. A trigger would just
retry into the wall.

The mechanism for repeated cycles already exists on both sides and needed no
schema change:

- the broker meters `SUM(cycle_count) <= max_cycles` with no state filter
  (`fouler_lease_broker.py:1154-1167`),
- the supervisor is already a real `while True:` loop
  (`scripts/devstream_session.py:4804`), sleeping `--sleep-seconds` between
  iterations and re-validating the lease each time.

What stopped it was configuration: `maxCycles: 1` in the lease and
`-MaxCycles 1` in the scheduled task.

---

## The one contract that is easy to get wrong

`--run-count` on the **issuer** and `-RunCount` on the **task** are not the
same number.

The issuer sets `maxRunCount` *and* `battleScope.runCount` from the single
`--run-count` flag (`scripts/devstream_runtime_lease.py:265,277`).
`lease.maxRunCount` is the **lease-lifetime battle budget** the broker meters
against. The task's `-RunCount` is the **per-cycle batch size**.

Validation is a **ceiling, not an equality** — `devstream_runtime_lease.py:707`
and `:716` both use `>`:

```python
if requested_run is not None and lease_run_count is not None and requested_run > lease_run_count:
    blockers.append(f"requested run count {requested_run} exceeds lease maxRunCount {lease_run_count}")
```

So a task running `-RunCount 30` against a `maxRunCount 120` lease validates
cleanly and can do it four times.

> **The trap:** issuing `--run-count 30 --max-cycles 4` passes validation and
> then dies at cycle 2 with `run_bound_exhausted`. The battle budget binds
> before the cycle budget does. For N cycles of B battles you must issue
> `--run-count (N*B) --max-cycles N`.

Keep `cycleCount = 1` per reservation. N cycles means N sequential
reservations of one cycle each, which the broker serializes via
`one_unresolved_reservation_per_authorization`. Reserving `cycleCount = N`
up front trips five separate assertions in `devstream_session.py`
(lines 543, 622-623, 874-875, 910, and the
`reserved_battles != reservation_count * run_count` identity at 856).

Other things that bind:

- **The proof window must cover the whole run.** It is re-checked on every
  `reserve-runtime` and `claim`. A 30-battle cycle takes roughly 2 hours, so
  4 cycles needs ~8h of window plus margin.
- **Capacity is never returned** (`"capacityReturned": False`,
  `fouler_lease_broker.py:1416`). A failed or abandoned cycle permanently
  burns its cycle and its battles. Budget headroom above the target N.
- **Bounds are inside the signature.** Changing `maxCycles` changes the
  authorization digest, which is the broker's primary key, and the `leases`
  table has immutability triggers (`:506-509`). You cannot raise a bound in
  place — you issue a new lease and register it.

---

## The loop: issue -> validate -> register -> stage -> run

### 1. Issue (DEKU — the only host with the signing key)

Use `--issue` (v3 signed). Never `--write`, which mints a v1 proof-window
lease that is rejected. Working reference script, preserved with its backup:

```
~/deku-fouler-signing/issue_fouler_94b98153_unattended_01_20260720.sh
```

Key arguments:

```bash
python3 scripts/devstream_runtime_lease.py \
  --issue \
  --purpose jigglypuff-runtime-start \
  --runtime-lease "$lease" \
  --deployment-receipt-input "$receipt" \
  --controller-private-key "$sign_root/keys/fouler-controller-ed25519.pem" \
  --controller-trust-store  "$sign_root/keys/controller-keys.json" \
  --controller-key-id deku-fouler-controller-20260715 \
  --account DekuFoulerFresh \
  --run-count 120 \        # TOTAL budget = cycles x batch
  --max-cycles 4 \
  --max-concurrent-battles 3 \
  --replay-behavior always \
  --valid-minutes 900 \
  --source-commit <40hex> \
  --change-id <MUST equal the receipt's changeId>
```

The `--change-id` must match the deployment receipt exactly. Do not invent a
new one. The script asserts the receipt SHA-256, a clean signing checkout at
the expected commit, and that the output lease does not already exist
(issuance writes `0444` with `O_EXCL`).

### 2. Validate — do not skip this

Registration is **not** signature-gated; it only self-checks a SHA-256 of the
payload file. The Ed25519 signature is verified by a separate validator step,
so skipping it means registering an unverified lease.

Full validation only passes on JIGGLYPUFF, because the lease is bound to that
physical host. On DEKU you will always see `hostname does not match` /
`host ID does not match`; that is expected and not a problem with the lease.

On JIGGLYPUFF, against the release tree (note `-I -B`; never let Python write
bytecode into the release):

```powershell
$rel = 'D:\Releases\fouler-play\<40hex>'
& "$rel\.venv\Scripts\python.exe" -I -B "$rel\scripts\devstream_runtime_lease.py" `
  --purpose jigglypuff-runtime-start `
  --runtime-lease <staged lease path> `
  --controller-trust-store 'C:\ProgramData\HERMES\authority\fouler\controller-keys.json' `
  --run-count 30 --max-cycles 4 --max-concurrent-battles 3 `
  --require-run-count --require-max-cycles --require-max-concurrent-battles `
  --require-deployment-receipt --verify-deployment-checkout
```

Require `"blockers": []` and `controllerAuthorization.authorized: true`.
Pass the **task's** per-cycle `-RunCount` here (30), not the lease total.

### 3. Register with the broker

Build the exact 19-field payload — the field set is compared with `==`, so a
missing or extra key fails with `registration_invalid`
(`fouler_lease_broker.py:273-276`). Shape and order mirror
`scripts/install_runtime_authority.ps1:579-599`. Every value except
`improveAuthorized` comes from the validator's `lease` summary.

```powershell
& $py -I -B $broker --store-path $store --marker-path $marker `
  register-lease --registration $regPath --expected-registration-sha256 $regSha
```

Expect `{"ok": true, "registered": true}`. Registration is idempotent only if
the leaseId *and* full payload match; otherwise `lease_identity_conflict`.

Note `install_runtime_authority.ps1` also re-applies release-tree ACLs and
requires keyring/manifest/account-season sources. When only a new lease needs
registering against an already-verified release, `register-lease` is the
narrower operation and does not touch the release tree.

### 4. Stage as the active lease

The supervisor reads `C:\ProgramData\HERMES\authority\fouler\runtime-lease.json`.
Copy the lease there and preserve the protected DACL exactly — clone the ACL
object from the previous file rather than hand-writing SDDL. `devstream-live`
must retain `ReadAndExecute` or the run fails.

### 5. Point the task at N cycles, then start

```powershell
# requires a genuinely elevated shell; an SSH session gets a UAC-filtered
# token and fails with "Access is denied"
$t = Get-ScheduledTask -TaskName 'HERMES-FoulerBattleSupervisor'
$new = $t.Actions[0].Arguments -replace ' -MaxCycles 1 ', ' -MaxCycles 4 '
Set-ScheduledTask -TaskName 'HERMES-FoulerBattleSupervisor' `
  -Action (New-ScheduledTaskAction -Execute $t.Actions[0].Execute -Argument $new)

Start-ScheduledTask -TaskName 'HERMES-FoulerBattleSupervisor'
```

Leave `-RunCount 30` alone: it is the per-cycle batch.

`install_battle_supervisor_task.ps1` forces `MaxCycles=1` only on the
`-ClearStopFile` sentinel-clear path (`:69-76`), and the wrapper only on the
recovery-proof-window path (`start_battle_supervisor_task.ps1:376-378`). The
normal path accepts any positive value; the installer allows 1-100.

The task has **no trigger** and is manual-start only
(`AllowDemandStart=True`). Once N cycles are configured, one start yields N
cycles. Adding a trigger is a separate decision and is only safe once a lease
with live capacity is reliably present, or it reintroduces the
fire-into-an-error problem this document exists to prevent.

### How it stops

Four independent stops, in the order they bite:

1. `--max-cycles` reached -> `state = "completed-max-cycles"`, exit 0.
2. Broker capacity -> `cycle_bound_exhausted` / `run_bound_exhausted` is
   normalized to `state = "completed-lease-consumption"`, exit 0.
3. Proof window expiry -> lease revalidation fails, exit 2 after the runner
   drains.
4. `pids\supervisor.stop` sentinel.

---

## improve_authorized — owner decision, and currently inert

`improve_authorized` is a real column on every lease and has been `0` on all
of them. It is **not** the lever for repeated runtime cycles; those are gated
by `maxCycles` alone, which is what this document configures.

Two things to know before anyone sets it:

1. **It is not read anywhere.** The broker stores it (`:387,828,852`) and
   never consults it in `_perform`, `_reserve_runtime`, or elsewhere, because
   `reserve-improve` is unconditionally rejected first:

   ```python
   if action == "reserve-improve":
       raise BrokerError("improve_control_plane_only",
           "runtime improvement is disabled and delegated to the external DEKU control plane")
   ```
   (`fouler_lease_broker.py:1114-1118`)

   So setting it to `1` today changes no behavior. Wiring it up means
   replacing that dead-end with a real `lease["improve_authorized"]` check.

2. **`improve-agent` is not in any delegation set.** `PURPOSE_DELEGATIONS`
   (`devstream_runtime_lease.py:65-99`) never grants it, so a
   `jigglypuff-runtime-start` lease can never authorize it. As a result
   `install_runtime_authority.ps1:940` — which validates with
   `-Purpose "improve-agent"` when `-ImproveAuthorized` is passed — cannot
   currently succeed against a normal runtime lease.

Granting improvement authority is an owner decision and was deliberately not
taken. All leases remain `improve_authorized = 0`.

---

## Current state (2026-07-20)

Done:

- Lease `fouler-jigglypuff-runtime-start-27dff1f5516c4b589fc8f65954876827`
  issued on DEKU: `maxRunCount 120`, `maxCycles 4`, `maxConcurrentBattles 3`,
  window `06:45:34Z -> 21:45:34Z`, against release `94b98153` and its existing
  `changeId fouler-golive-94b98153-20260719`.
- Validated on JIGGLYPUFF with `"blockers": []`, controller signature
  `sha256:iCmAZcCJHUG9AINiSgzUYf3qu_rs557QFs3hg2AIJCI`.
- Registered with the broker (`registered: true`); capacity reads
  **0/120 battles, 0/4 cycles used**.
- Staged as the active `runtime-lease.json` with a byte-identical DACL.

Remaining — one elevated command:

- The task still passes `-MaxCycles 1`, so a start today runs one 30-battle
  cycle against a 120-battle lease. Safe, just underused. Apply step 5 from
  an elevated shell to unlock all four cycles.

Backups and rollback:

```
C:\ProgramData\HERMES\backups\claude\fouler-unattended-lease-<stamp>\   # broker DB + registration
C:\ProgramData\HERMES\backups\claude\fouler-unattended-stage-<stamp>\   # previous lease, task XML + args
```

Restore with `runtime-lease.json.PREVIOUS` and
`Register-ScheduledTask -Xml (Get-Content task.PREVIOUS.xml -Raw) -Force`.

One observability nit found in passing: the per-release activation record
`authority\fouler\broker-activations\<commit>.json` still names the
batch30-02 lease even though batch30-03 ran afterwards. It is an audit
artifact, not a gate — nothing reads it — but it is stale.
