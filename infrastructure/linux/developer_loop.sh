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
BRANCH="${BRANCH:-master}"
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

    # Step 1: Sync to remote — split data-sync from code-sync.
    # FOULER-DEVLOOP-DATA-CODE-SPLIT-2026-05-20:
    #   DATA: force-checkout battle data files from origin (JIGGLYPUFF is
    #         the source of truth for battle_stats/replays).
    #   CODE: fast-forward merge only. Never `reset --hard` — that destroyed
    #         a 12-day-old hazard-pressure deliverable on 2026-05-20.
    log "Syncing to origin/$BRANCH (data fast-path + code ff-merge)..."
    if git -C "$REPO_DIR" fetch origin "$BRANCH" --quiet 2>&1 | tee -a "$LOG_FILE"; then
        for data_file in battle_stats.json active_battles.json stream_status.json; do
            if git -C "$REPO_DIR" cat-file -e "origin/$BRANCH:$data_file" 2>/dev/null; then
                git -C "$REPO_DIR" checkout "origin/$BRANCH" -- "$data_file" 2>>"$LOG_FILE" || true
            fi
        done
        if git -C "$REPO_DIR" merge --ff-only "origin/$BRANCH" --quiet 2>&1 | tee -a "$LOG_FILE"; then
            log "Sync OK ($(git -C "$REPO_DIR" rev-parse --short HEAD))"
        else
            log_warn "branch diverged from origin/$BRANCH; data synced, code unchanged. Rebase manually if needed."
        fi
    else
        log_error "Git fetch failed. Will retry next cycle."
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
