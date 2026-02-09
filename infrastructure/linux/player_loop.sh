#!/bin/bash
# =============================================================================
# Fouler-Play Player Loop (Linux Machine - DEKU)
# =============================================================================
# Plays ladder matches using BugInTheCode account, pushes stats/replays to GitHub
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
BRANCH="master"
BATCH_SIZE="${BATCH_SIZE:-15}"  # games per batch
MAX_CONCURRENT="${MAX_CONCURRENT:-3}"
ENV_FILE="${REPO_DIR}/.env.deku"
TEAM_LIST="${REPO_DIR}/teams/teams/fat-teams.list"
LOG_FILE="${REPO_DIR}/infrastructure/linux/player_loop.log"

# Load env
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "ERROR: .env.deku not found"
    exit 1
fi

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[${timestamp}] $*" | tee -a "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
log "=========================================="
log "Fouler-Play Player Loop (DEKU) starting"
log "Account: ${PS_USERNAME}"
log "Batch size: ${BATCH_SIZE} games"
log "Concurrent battles: ${MAX_CONCURRENT}"
log "=========================================="

cd "$REPO_DIR"

# Activate venv
source venv/bin/activate

while true; do
    log "--- Starting batch of ${BATCH_SIZE} games ---"
    
    # Pull latest code first
    log "Pulling latest from $BRANCH..."
    git pull origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE" || true
    
    # Play games
    log "Playing ${BATCH_SIZE} games..."
    python run.py \
        --websocket-uri "${PS_WEBSOCKET_URI}" \
        --ps-username "${PS_USERNAME}" \
        --ps-password "${PS_PASSWORD}" \
        --bot-mode search_ladder \
        --pokemon-format "${PS_FORMAT}" \
        --team-list "${TEAM_LIST}" \
        --max-concurrent-battles "${MAX_CONCURRENT}" \
        --run-count "${BATCH_SIZE}" \
        --search-time-ms "${PS_SEARCH_TIME_MS}" \
        --save-replay always \
        --log-level INFO \
        2>&1 | tee -a "$LOG_FILE"
    
    # Push stats and replays
    log "Pushing battle stats and replays..."
    git add battle_stats.json replays/ 2>&1 | tee -a "$LOG_FILE" || true
    
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "DEKU: batch of ${BATCH_SIZE} games (${PS_USERNAME})" 2>&1 | tee -a "$LOG_FILE"
        git push origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE"
        log "Pushed stats successfully."
    else
        log "No new data to push."
    fi
    
    log "--- Batch complete ---"
    log "Waiting 10 seconds before next batch..."
    sleep 10
done
