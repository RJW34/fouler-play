#!/bin/bash
# =============================================================================
# Fouler Play Developer Loop (Simplified)
# =============================================================================
# Periodically pulls battle data from git and generates analysis reports.
# The reports can then be fed to an AI assistant for improvement suggestions.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
BRANCH="${BRANCH:-foulest-play}"
SLEEP_INTERVAL="${SLEEP_INTERVAL:-1800}"  # seconds between cycles (default 30 min)
LOG_FILE="${REPO_DIR}/infrastructure/linux/developer_loop.log"
LAST_ANALYSIS_MARKER="${REPO_DIR}/infrastructure/linux/.last_analysis_count"
ANALYZE_SCRIPT="${REPO_DIR}/infrastructure/linux/analyze_performance.sh"

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "${GREEN}[${timestamp}]${NC} $*" | tee -a "$LOG_FILE"
}

log_error() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "${RED}[${timestamp}] ERROR:${NC} $*" | tee -a "$LOG_FILE" >&2
}

log_warn() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "${YELLOW}[${timestamp}] WARNING:${NC} $*" | tee -a "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
RUNNING=true

cleanup() {
    RUNNING=false
    log "Received shutdown signal. Cleaning up..."
    log "Developer loop stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

# ---------------------------------------------------------------------------
# Helper: count battles in battle_stats.json
# ---------------------------------------------------------------------------
count_battles() {
    local battle_stats="${REPO_DIR}/battle_stats.json"
    if [ -f "$battle_stats" ]; then
        python3 -c "
import json, sys
try:
    with open('${battle_stats}', 'r') as f:
        data = json.load(f)
    battles = data.get('battles', [])
    print(len(battles))
except Exception:
    print(0)
" 2>/dev/null || echo "0"
    else
        echo "0"
    fi
}

# ===========================================================================
# Main Loop
# ===========================================================================
log "=========================================="
log "Fouler Play Developer Loop Starting"
log "=========================================="
log "Repository: $REPO_DIR"
log "Branch: $BRANCH"
log "Sleep interval: ${SLEEP_INTERVAL}s"
log "Log file: $LOG_FILE"
log "=========================================="

# Initialize last analysis marker if it doesn't exist
if [ ! -f "$LAST_ANALYSIS_MARKER" ]; then
    echo "0" > "$LAST_ANALYSIS_MARKER"
    log "Initialized analysis marker"
fi

# Main loop
while $RUNNING; do
    log "--- Starting new analysis cycle ---"

    # Step 1: Pull latest from git
    log "Pulling latest from origin/$BRANCH..."
    if git -C "$REPO_DIR" pull origin "$BRANCH" --quiet 2>&1 | tee -a "$LOG_FILE"; then
        log "Git pull successful"
    else
        log_error "Git pull failed. Will retry next cycle."
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    # Step 2: Check for new battles
    current_count=$(count_battles)
    last_count=$(cat "$LAST_ANALYSIS_MARKER" 2>/dev/null || echo "0")
    new_battles=$((current_count - last_count))

    log "Battle count: $current_count (last analyzed: $last_count, new: $new_battles)"

    if [ "$new_battles" -le 0 ]; then
        log "No new battles since last analysis. Sleeping for ${SLEEP_INTERVAL}s..."
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    # Step 3: Run analysis
    log "Running performance analysis (${new_battles} new battles)..."
    if bash "$ANALYZE_SCRIPT" 2>&1 | tee -a "$LOG_FILE"; then
        log "Analysis completed successfully"
        # Update marker
        echo "$current_count" > "$LAST_ANALYSIS_MARKER"
    else
        log_error "Analysis script failed"
        # Don't update marker - we'll try again next cycle
    fi

    log "--- Cycle complete ---"
    log "Next analysis in ${SLEEP_INTERVAL}s..."
    log ""
    sleep "$SLEEP_INTERVAL"
done
