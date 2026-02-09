#!/bin/bash
# =============================================================================
# Fouler Play - Replay Analysis Pipeline
# =============================================================================
# Pulls latest battle_stats.json from git and generates team performance report
# suitable for AI-assisted improvement suggestions.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
BRANCH="${BRANCH:-master}"
BATTLE_STATS="${REPO_DIR}/battle_stats.json"
REPORT_DIR="${REPO_DIR}/replay_analysis/reports"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
REPORT_FILE="${REPORT_DIR}/analysis_${TIMESTAMP}.txt"
LATEST_LINK="${REPORT_DIR}/latest_analysis.txt"

# ---------------------------------------------------------------------------
# Colors for output
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[$(date '+%H:%M:%S')] WARNING:${NC} $*"
}

error() {
    echo -e "${RED}[$(date '+%H:%M:%S')] ERROR:${NC} $*" >&2
}

# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

log "=========================================="
log "Fouler Play - Performance Analysis Pipeline"
log "=========================================="

# Step 1: Pull latest data from git
log "Pulling latest from origin/$BRANCH..."
if git -C "$REPO_DIR" pull origin "$BRANCH" --quiet; then
    log "Git pull successful"
else
    error "Git pull failed"
    exit 1
fi

# Step 2: Check if battle_stats.json exists
if [ ! -f "$BATTLE_STATS" ]; then
    error "battle_stats.json not found at $BATTLE_STATS"
    error "The Windows bot (BAKUGO) may not have pushed data yet."
    exit 1
fi

# Count battles
battle_count=$(python3 -c "
import json
try:
    with open('$BATTLE_STATS', 'r') as f:
        data = json.load(f)
    battles = data.get('battles', [])
    print(len(battles))
except Exception as e:
    print(0)
")

log "Found $battle_count battles in battle_stats.json"

if [ "$battle_count" -eq 0 ]; then
    warn "No battles found in battle_stats.json"
    exit 0
fi

# Step 3: Generate team performance report
log "Generating team performance analysis..."
mkdir -p "$REPORT_DIR"

if ! python3 "$REPO_DIR/replay_analysis/team_performance.py" > "$REPORT_FILE" 2>&1; then
    error "team_performance.py failed"
    cat "$REPORT_FILE"
    exit 1
fi

# Create symlink to latest report
ln -sf "$(basename "$REPORT_FILE")" "$LATEST_LINK"

log "Report generated: $REPORT_FILE"
log "Latest report link: $LATEST_LINK"

# Step 4: Display summary
log ""
log "=========================================="
log "ANALYSIS SUMMARY"
log "=========================================="

# Extract key metrics from report
if grep -q "TEAM PERFORMANCE REPORT" "$REPORT_FILE"; then
    # Show total battles
    total_line=$(grep "Total battles analysed:" "$REPORT_FILE" || echo "")
    if [ -n "$total_line" ]; then
        echo "$total_line"
    fi
    
    # Show team summaries
    echo ""
    grep -A 3 "^TEAM:" "$REPORT_FILE" || true
    
    # Show recommendations
    echo ""
    echo "=========================================="
    grep -A 20 "^RECOMMENDATIONS" "$REPORT_FILE" | head -30 || true
fi

log ""
log "=========================================="
log "Full report available at: $REPORT_FILE"
log "For AI improvement suggestions, feed this report to Claude Code"
log "=========================================="
