#!/bin/bash
# =============================================================================
# Fouler-Play Developer Loop (Linux Machine)
# =============================================================================
# Continuously pulls battle data, analyzes performance, and invokes Claude Code
# to produce targeted improvements. Commits and pushes changes that pass tests.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
BRANCH="foulest-play"
SLEEP_INTERVAL="${SLEEP_INTERVAL:-1800}"  # seconds between cycles (default 30 min)
LOG_FILE="${REPO_DIR}/infrastructure/linux/developer_loop.log"
LAST_ANALYSIS_MARKER="${REPO_DIR}/infrastructure/linux/.last_analysis_count"
ANALYSIS_PROMPT="${REPO_DIR}/infrastructure/linux/analysis_prompt.md"
GUARDRAILS="${REPO_DIR}/infrastructure/guardrails.json"
BATTLE_STATS="${REPO_DIR}/battle_stats.json"
TEAM_REPORT="${REPO_DIR}/infrastructure/linux/.latest_team_report.txt"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[${timestamp}] $*" | tee -a "$LOG_FILE"
}

log_error() {
    log "ERROR: $*" >&2
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
    if [ -f "$BATTLE_STATS" ]; then
        python3 -c "
import json, sys
try:
    with open('$BATTLE_STATS', 'r') as f:
        data = json.load(f)
    if isinstance(data, list):
        print(len(data))
    elif isinstance(data, dict) and 'battles' in data:
        print(len(data['battles']))
    else:
        print(0)
except Exception:
    print(0)
"
    else
        echo "0"
    fi
}

# ---------------------------------------------------------------------------
# Helper: get current ELO from battle_stats.json
# ---------------------------------------------------------------------------
get_current_elo() {
    if [ -f "$BATTLE_STATS" ]; then
        python3 -c "
import json, sys
try:
    with open('$BATTLE_STATS', 'r') as f:
        data = json.load(f)
    if isinstance(data, list) and len(data) > 0:
        last = data[-1]
        print(last.get('elo', last.get('rating', 'unknown')))
    elif isinstance(data, dict):
        print(data.get('elo', data.get('rating', 'unknown')))
    else:
        print('unknown')
except Exception:
    print('unknown')
"
    else
        echo "unknown"
    fi
}

# ---------------------------------------------------------------------------
# Helper: syntax check all modified Python files
# ---------------------------------------------------------------------------
syntax_check() {
    log "Running syntax check on modified files..."
    local files
    files=$(git -C "$REPO_DIR" diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)
    if [ -z "$files" ]; then
        log "No Python files staged; syntax check passed."
        return 0
    fi
    local failed=0
    for f in $files; do
        if ! python3 -m py_compile "$REPO_DIR/$f" 2>/dev/null; then
            log_error "Syntax error in $f"
            failed=1
        fi
    done
    return $failed
}

# ---------------------------------------------------------------------------
# Helper: run tests
# ---------------------------------------------------------------------------
run_tests() {
    log "Running tests..."
    if [ -d "$REPO_DIR/tests" ]; then
        if python3 -m pytest "$REPO_DIR/tests" --tb=short -q 2>&1 | tee -a "$LOG_FILE"; then
            log "Tests passed."
            return 0
        else
            log_error "Tests failed."
            return 1
        fi
    else
        log "No tests directory found; skipping tests."
        return 0
    fi
}

# ---------------------------------------------------------------------------
# Helper: check if a file is in the allowed_modify list
# ---------------------------------------------------------------------------
check_guardrails() {
    log "Checking file guardrails..."
    python3 -c "
import json, fnmatch, sys

with open('$GUARDRAILS', 'r') as f:
    rules = json.load(f)

allowed = rules.get('allowed_modify', [])
never = rules.get('never_modify', [])

import subprocess
result = subprocess.run(
    ['git', '-C', '$REPO_DIR', 'diff', '--cached', '--name-only'],
    capture_output=True, text=True
)
changed = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

violations = []
for path in changed:
    # Check never_modify first
    for pattern in never:
        if fnmatch.fnmatch(path, pattern):
            violations.append(f'BLOCKED (never_modify): {path}')
            break
    else:
        # Check allowed_modify
        ok = False
        for pattern in allowed:
            if fnmatch.fnmatch(path, pattern):
                ok = True
                break
        if not ok:
            violations.append(f'NOT ALLOWED: {path}')

if violations:
    for v in violations:
        print(v, file=sys.stderr)
    sys.exit(1)
else:
    print('All modified files are within guardrails.')
    sys.exit(0)
"
}

# ===========================================================================
# Main Loop
# ===========================================================================
log "=========================================="
log "Fouler-Play Developer Loop starting"
log "Repo: $REPO_DIR"
log "Branch: $BRANCH"
log "Sleep interval: ${SLEEP_INTERVAL}s"
log "=========================================="

# Initialize last analysis marker if it does not exist
if [ ! -f "$LAST_ANALYSIS_MARKER" ]; then
    echo "0" > "$LAST_ANALYSIS_MARKER"
fi

while $RUNNING; do
    log "--- Cycle start ---"

    # Step 1: Pull latest
    log "Pulling latest from $BRANCH..."
    if ! git -C "$REPO_DIR" pull origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE"; then
        log_error "git pull failed. Retrying next cycle."
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    # Step 2: Check for new battles
    current_count=$(count_battles)
    last_count=$(cat "$LAST_ANALYSIS_MARKER" 2>/dev/null || echo "0")
    new_battles=$((current_count - last_count))

    log "Battle count: $current_count (last analyzed: $last_count, new: $new_battles)"

    if [ "$new_battles" -le 0 ]; then
        log "No new battles since last analysis. Sleeping..."
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    # Step 3: Generate team performance report
    log "Generating team performance report..."
    if [ -f "$REPO_DIR/replay_analysis/team_performance.py" ]; then
        if python3 "$REPO_DIR/replay_analysis/team_performance.py" > "$TEAM_REPORT" 2>&1; then
            log "Team report generated successfully."
        else
            log_error "team_performance.py failed. Using fallback summary."
            echo "Team report generation failed. Analyze battle_stats.json directly." > "$TEAM_REPORT"
        fi
    else
        log "replay_analysis/team_performance.py not found. Using raw stats."
        echo "No team_performance.py available. Analyze battle_stats.json directly." > "$TEAM_REPORT"
    fi

    # Step 4: Build the Claude Code prompt
    current_elo=$(get_current_elo)
    report_content=$(cat "$TEAM_REPORT")

    # Read the analysis prompt template and fill placeholders
    prompt_filled=$(cat "$ANALYSIS_PROMPT")
    prompt_filled="${prompt_filled//\{\{CURRENT_ELO\}\}/$current_elo}"
    prompt_filled="${prompt_filled//\{\{NEW_BATTLES\}\}/$new_battles}"
    prompt_filled="${prompt_filled//\{\{TEAM_REPORT\}\}/$report_content}"

    # Write filled prompt to temp file
    filled_prompt_file="${REPO_DIR}/infrastructure/linux/.filled_prompt.md"
    echo "$prompt_filled" > "$filled_prompt_file"

    # Step 5: Invoke Claude Code CLI
    log "Invoking Claude Code for analysis and improvement..."
    if claude -p "$filled_prompt_file" \
        --allowedTools "Edit,Write,Read,Bash,Glob,Grep" \
        --output-format text \
        2>&1 | tee -a "$LOG_FILE"; then
        log "Claude Code session completed."
    else
        log_error "Claude Code session failed or exited with error."
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    # Step 6: Check if there are any changes
    if git -C "$REPO_DIR" diff --quiet && git -C "$REPO_DIR" diff --cached --quiet; then
        log "No changes produced by Claude Code. Sleeping..."
        echo "$current_count" > "$LAST_ANALYSIS_MARKER"
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    # Step 7: Stage changes, check guardrails, syntax, and tests
    git -C "$REPO_DIR" add -A

    if ! check_guardrails; then
        log_error "Guardrail violation detected. Discarding changes."
        git -C "$REPO_DIR" checkout -- .
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    if ! syntax_check; then
        log_error "Syntax check failed. Discarding changes."
        git -C "$REPO_DIR" checkout -- .
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    if ! run_tests; then
        log_error "Tests failed. Discarding changes."
        git -C "$REPO_DIR" checkout -- .
        sleep "$SLEEP_INTERVAL"
        continue
    fi

    # Step 8: Commit and push
    commit_msg="auto: improvement cycle (ELO: ${current_elo}, battles analyzed: ${new_battles})"
    log "Committing: $commit_msg"
    git -C "$REPO_DIR" commit -m "$commit_msg" 2>&1 | tee -a "$LOG_FILE"

    if git -C "$REPO_DIR" push origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE"; then
        log "Changes pushed successfully."
    else
        log_error "git push failed."
    fi

    # Update marker
    echo "$current_count" > "$LAST_ANALYSIS_MARKER"

    log "--- Cycle complete ---"
    log "Sleeping for ${SLEEP_INTERVAL}s..."
    sleep "$SLEEP_INTERVAL"
done
