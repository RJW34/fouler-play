# Fouler Observation Queue

## Authority Boundary

Fouler is an observation producer. It has no Discord identity, network-delivery
authority, or command authority.

The only production path is:

1. Fouler code appends an observation to the local `events_queue.json` journal.
2. The battle runtime asks `infrastructure/event_poster.py` to validate and
   advance the bounded local journal after each completion.
3. It atomically writes `deku-project-event/v1` JSON files beneath the configured
   local DEKU outbox `pending` directory.
4. The separately managed DEKU relay transports accepted files and owns all chat
   identity, routing, throttling, and command-intake policy.

Project code must never load chat credentials, call a chat API, invoke a message
CLI, or run a remote delivery command. Stale standalone producers fail closed.

## Observation Envelope

Every outbox file is JSON and includes these authority fields:

```json
{
  "schemaVersion": "deku-project-event/v1",
  "kind": "observation",
  "authority": "none",
  "producer": "fouler-play",
  "source": "fouler-play.event-poster",
  "eventType": "battle_result",
  "dedupKey": "fouler-play:battle-result:<showdown-battle-id>",
  "evidenceRefs": ["<local-proof-path>"],
  "recommendedNextAction": "<recommendation for DEKU planning>"
}
```

`recommendedNextAction` is telemetry, not an instruction. DEKU must reject
project-authored, bot-authored, relay-authored, and self-authored chat messages
as operator command intake.

## Noise Policy

- Every completed `battle_result` produces exactly one DEKU observation.
- Its outbox event ID and dedup key derive from the Showdown battle ID, so a
  replay-enrichment update or retry cannot create a second Discord report.
- Routine analysis and batch events remain local and do not create additional
  outbox files.
- Performance alerts use a stable condition-and-edge dedup key. Changing battle
  IDs cannot reopen the same edge.

## Atomicity And Recovery

The local journal uses a file lock and last-good backup. Outbox writes use a
temporary file, flush and `fsync`, then `os.replace`. An existing event ID is
accepted only when the existing file has the same ID; otherwise processing
fails closed as a collision.

The local battle journal remains durable until its DEKU outbox write succeeds.
Reprocessing the same Showdown battle is idempotent locally and in the central
HERMES event queue.

## Retired Paths

- `infrastructure/event-handlers.py` is a fail-closed import tombstone.
- `scripts/fouler_deku_event_producer.ps1` exits nonzero and must not be
  scheduled.
- `docker-compose.yml` does not define an event producer.
- Mission-monitor state is written locally; the monitor cannot enqueue alerts.

Machine cutover must also disable and remove stale scheduled tasks, services,
containers, timers, and startup launchers that reference any retired path.
