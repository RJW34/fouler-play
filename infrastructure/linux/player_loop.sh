#!/bin/bash
# Bounded compatibility entry point for old Linux installs.
#
# The historical implementation ran an infinite direct ladder loop and pushed
# battle data to git. Current devstream runtime ownership lives in
# scripts/devstream_session.py supervise, which checks the mission start gate,
# runtime lease, stop-loss rails, and max-cycle bound before any ladder batch.

set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
RUN_COUNT="${RUN_COUNT:-${BATCH_SIZE:-}}"
MAX_CONCURRENT="${MAX_CONCURRENT:-${MAX_CONCURRENT_BATTLES:-1}}"
MAX_CYCLES="${MAX_CYCLES:-}"
QUEUE_TIMEOUT_SECONDS="${QUEUE_TIMEOUT_SECONDS:-180}"
SLEEP_SECONDS="${SLEEP_SECONDS:-15}"
RUNTIME_LEASE="${RUNTIME_LEASE:-${FOULER_RUNTIME_LEASE:-devstream/truth/runtime-lease.json}}"

fail() {
    echo "ERROR: $*" >&2
    exit 2
}

positive_int() {
    case "${1:-}" in
        ''|*[!0-9]*) return 1 ;;
        0) return 1 ;;
        *) return 0 ;;
    esac
}

positive_int "$RUN_COUNT" || fail "RUN_COUNT or BATCH_SIZE must be a positive bounded value."
positive_int "$MAX_CONCURRENT" || fail "MAX_CONCURRENT must be a positive bounded value."
positive_int "$MAX_CYCLES" || fail "MAX_CYCLES must be a positive bounded value."

cd "$REPO_DIR"

if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    PY="$REPO_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    PY="python"
fi

command=(
    "$PY"
    "scripts/devstream_session.py"
    "supervise"
    "--run-count" "$RUN_COUNT"
    "--max-concurrent-battles" "$MAX_CONCURRENT"
    "--max-cycles" "$MAX_CYCLES"
    "--queue-timeout-seconds" "$QUEUE_TIMEOUT_SECONDS"
    "--sleep-seconds" "$SLEEP_SECONDS"
    "--runtime-lease" "$RUNTIME_LEASE"
)

if [ "${FOULER_PLAY_ENABLE_AUTO_IMPROVE:-0}" = "1" ]; then
    command+=("--enable-auto-improve")
fi

echo "Delegating legacy Linux player loop to bounded Fouler supervisor."
echo "Run count: $RUN_COUNT; max concurrent battles: $MAX_CONCURRENT; max cycles: $MAX_CYCLES"
exec "${command[@]}"
