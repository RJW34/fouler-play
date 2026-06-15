# JIGGLY Reporting Runtime Status - 2026-06-15T08:19:19Z

## Current code truth

- Canonical local repo: `C:\Users\mtoli\Documents\Code\fouler-play`
- Host: `MIRAIDON`
- Branch: `opus48/multisample-mcts`
- Latest code-changing head pushed to origin: `b3e1b90b Recognize non-opponent winner in battle reports`
- Remote proof at the time of verification: `origin/opus48/multisample-mcts` resolved to `b3e1b90b844e9b6134c2cf346c15ba7ce9c84ab8`

## Reporting fixes now on origin

- `1eef3a00 Preserve battle result in Discord reporting`
  - Keeps explicit battle result as the source of truth.
  - ELO movement is displayed as context.
  - Contradictory ELO now renders as `ELO check needed` instead of relabeling a battle.
- `b3e1b90b Recognize non-opponent winner in battle reports`
  - Handles stale configured account names.
  - If Showdown's terminal winner is not the known opponent, reporting classifies it as our win.
  - The exact screenshot case is covered: stale `LEBOTJAMESXD004`, terminal winner `LEBOTJAMESXD00N`, opponent `murdockfejao`.

## Validation

- Canonical repo focused reporting/rating tests: `55 passed`
- Canonical repo full suite: `1148 passed, 2 warnings`
- Canonical repo syntax/import checks: passed
- Stale local worktree `C:\Users\mtoli\Documents\Code\fouler-play-jiggly-fix`:
  - Cherry-picked both reporting corrections as `7677aadf` and `e5e3439a`
  - Focused reporting/rating tests: `59 passed`
  - Unrelated dirty files remain in `infrastructure/autoresearch/*.py`; they were not reverted or altered beyond preserving local state.

## Live JIGGLY status

JIGGLY is not currently controllable from this session, so live deployment and runtime verification are not proven.

Observed access failures:

- `py -3 scripts\jigglypuff_devstream_control.py status`
  - `JIGGLYPUFF fouler runtime did not return JSON`
  - SSH: `Connection timed out during banner exchange`
  - worker HTTP `http://192.168.1.126:8791/fouler/status`: timeout
  - Tailscale DNS `jigglypuff.tail4859dd.ts.net`: getaddrinfo failed
  - OBS/state mirror `http://192.168.1.126:8777/state`: unavailable
- Port scan:
  - Open: 22, 80, 135, 139, 445, 5985, 8080, 5357
  - Closed/unreachable: 443, 5986, 8000, 8777, 8791, 9000, 3389
  - HTTP on 80/8080 accepts TCP but times out before returning an HTTP response.
- WinRM:
  - Port 5985 is open, but `Invoke-Command` and CIM fail because this client cannot update TrustedHosts without elevation.
  - Current TrustedHosts: `192.168.1.181`
  - Attempted append of `JIGGLYPUFF,192.168.1.126` failed with `Access is denied`.
  - Rollback value would be `192.168.1.181` if an elevated change is later made.
- SMB / service control / process query:
  - `\\JIGGLYPUFF\D$` and `\\JIGGLYPUFF\Users` unavailable from this token.
  - `sc.exe \\JIGGLYPUFF query sshd`: access denied.
  - `schtasks /query /s JIGGLYPUFF /tn FoulerPlayOneTouch`: access denied.
  - `tasklist /S JIGGLYPUFF`: username/password rejected.
  - Legacy WMI: access denied.

## Next recovery command

From an elevated PowerShell on MIRAIDON, either:

```powershell
Set-Item WSMan:\localhost\Client\TrustedHosts -Value '192.168.1.181,JIGGLYPUFF,192.168.1.126' -Force
```

then retry:

```powershell
Invoke-Command -ComputerName JIGGLYPUFF -ScriptBlock { hostname; git -C D:\Projects\fouler-play rev-parse HEAD }
```

or fix/restart OpenSSH directly on JIGGLY, then run:

```powershell
py -3 scripts\jigglypuff_devstream_control.py status
```

Once a command channel returns, deploy target is:

```powershell
git -C D:\Projects\fouler-play fetch origin opus48/multisample-mcts
git -C D:\Projects\fouler-play checkout opus48/multisample-mcts
git -C D:\Projects\fouler-play pull --ff-only origin opus48/multisample-mcts
git -C D:\Projects\fouler-play rev-parse HEAD
```

Expected deployed code head: `b3e1b90b844e9b6134c2cf346c15ba7ce9c84ab8` or a later descendant containing it.
