# Retired Watcher Setup

The legacy watcher, cron, direct-webhook, and self-deployment paths documented here
are retired. They must not be reinstalled or used as production launchers.

Current production authority is limited to:

- `HERMES-FoulerBattleSupervisor` for battle sessions from an immutable release,
  with an exact deployment receipt and finite DEKU-signed v3 runtime lease.
- `HERMES-FoulerObsServer` for the read-only OBS overlay service.
- The DEKU event queue for Discord reporting and operator intake.

See `README.md`, `ARCHITECTURE.md`, and `BAKUGO_OPERATIONS_GUIDE.md` for the
current deployment and operating contract.
