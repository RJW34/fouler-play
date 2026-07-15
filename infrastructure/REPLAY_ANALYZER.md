# Replay Analyzer (Retired Standalone Path)

The historical cron-driven replay analyzer is not a production launcher.
Managed Fouler supervisor cycles own bounded replay analysis and persist the
result locally.

Production rules:

- Do not install the historical cron entry or wrapper as a background service.
- Do not add chat credentials or network-delivery code to replay analysis.
- Per-battle analysis remains local evidence.
- One bounded-session observation digest is written to the local DEKU outbox.
- DEKU owns transport, identity, routing, throttling, and command-intake policy.

The old analyzer files may remain useful as implementation reference, but any
stale cron job, timer, task, container, or startup launcher invoking them must be
disabled during machine cutover.
