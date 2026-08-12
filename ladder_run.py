"""Greenfield direct launcher for the foul-play PLAYER.

Runs the exact same `run_foul_play()` battle loop as run.py -- same MCTS mover,
same websocket auto-reconnect, same resume-active-battles logic -- but WITHOUT
run.py's `__main__` block, which wraps startup in the dead HERMES runtime-lease /
signing-broker singleton guard (process_lock.acquire_lock -> validate_runtime_lease).
That lease plane is intentionally bypassed here: this is greenfield continuous
laddering, not the bounded-season/lease pipeline.

Singleton protection is provided by ladder_supervisor.py (one supervisor -> one
child) plus run_foul_play()'s own stale-search/stale-battle cleanup at startup.

Credentials are never placed on argv: --ps-password defaults to os.getenv(PS_PASSWORD),
which run.py loads from .env at import time.
"""
import asyncio
import logging

import run  # noqa: F401  (import runs module-level dotenv load; NOT the __main__ lease guard)

# Silence the per-damage-roll MCTS DEBUG spam (~800KB/s to disk during long stall
# games). This is pure instrumentation from the search engine; suppressing it frees
# disk I/O so concurrent battles don't get starved on a slow move. The mover itself
# is unchanged. (A child logger set to WARNING drops its DEBUG records regardless of
# the root level init_logging sets later.)
for _name in ("fp.search.poke_engine_helpers",):
    logging.getLogger(_name).setLevel(logging.WARNING)


if __name__ == "__main__":
    asyncio.run(run.run_foul_play())
