# JIGGLYPUFF Fouler Operations

JIGGLYPUFF runs Fouler only from a clean immutable release. Runtime identity is
the exact Git commit/tree/runtime manifest, deployment receipt, finite v2 lease,
lease ID, session ID, account, and completed-battle activation receipt.

## Owners

- Battle lifecycle: `HERMES-FoulerBattleSupervisor`
- Battle command: `scripts/devstream_session.py supervise`
- OBS HTTP surface: `HERMES-FoulerObsServer` Windows service
- Discord intake and delivery: DEKU event queue and global DEKU identity
- Output start/stop: operator-gated OBS action after rehearsal and health gates

All one-touch launchers, boot/keepalive tasks, lease-autorenew scripts, OBS
scheduled tasks, direct webhook posters, and ffmpeg Twitch controllers are
retired fail-closed tombstones.

## Read-Only Checks

```powershell
Get-ScheduledTask -TaskName HERMES-FoulerBattleSupervisor -ErrorAction SilentlyContinue
Get-Service HERMES-FoulerObsServer -ErrorAction SilentlyContinue
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8777/health
Get-Content devstream\truth\health.json
```

Do not kill broad Python process patterns, pull into the live release, run a
direct launcher, or start OBS output from a watchdog. Deployment, task cleanup,
credential rotation, rehearsal, and go-live require the current operator plan
and preserved rollback backups.
