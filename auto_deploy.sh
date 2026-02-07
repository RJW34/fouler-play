#!/usr/bin/env bash
# auto_deploy.sh — Run tests, sanity check, restart bot_monitor if tests pass
# Called by DEKU after writing code patches from the improvement pipeline

set -euo pipefail

FP_DIR="/home/ryan/projects/fouler-play"
COUNTER_FILE="/tmp/fp-losses-since-deploy"

cd "$FP_DIR"

echo "=== Auto Deploy ==="
echo ""

# Step 1: Syntax check critical files
echo "[1/4] Syntax check..."
python3 -c "import ast; ast.parse(open('constants.py').read())" || { echo "FAIL: constants.py syntax error"; exit 1; }
python3 -c "import ast; ast.parse(open('fp/search/main.py').read())" || { echo "FAIL: main.py syntax error"; exit 1; }
python3 -c "import ast; ast.parse(open('bot_monitor.py').read())" || { echo "FAIL: bot_monitor.py syntax error"; exit 1; }
python3 -c "import ast; ast.parse(open('replay_analysis/turn_review.py').read())" || { echo "FAIL: turn_review.py syntax error"; exit 1; }
echo "  All syntax checks passed"

# Step 2: Run tests if available
echo "[2/4] Running tests..."
if [[ -d "tests" ]] && command -v pytest >/dev/null 2>&1; then
    if python3 -m pytest tests/ -x -q 2>&1; then
        echo "  Tests passed"
    else
        echo "  Tests failed — aborting deploy"
        exit 1
    fi
else
    echo "  No test suite available, skipping"
fi

# Step 3: Sanity check — import critical modules
echo "[3/4] Import sanity check..."
python3 -c "
from replay_analysis.turn_review import TurnReviewer
from replay_analysis.analyzer import ReplayAnalyzer
print('  Imports OK')
" || { echo "FAIL: import error"; exit 1; }

# Step 4: Restart bot_monitor if running
echo "[4/4] Restarting bot_monitor..."
if systemctl --user is-active fouler-play.service >/dev/null 2>&1; then
    systemctl --user restart fouler-play.service
    echo "  fouler-play service restarted"
elif pgrep -f "bot_monitor.py" >/dev/null 2>&1; then
    echo "  bot_monitor running outside systemd — manual restart needed"
else
    echo "  bot_monitor not running — no restart needed"
fi

# Reset loss counter
echo "0" > "$COUNTER_FILE"
echo ""
echo "Deploy complete. Loss counter reset."
