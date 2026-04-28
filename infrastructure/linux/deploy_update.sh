#!/bin/bash
# =============================================================================
# Deploy Update (Linux) — Pull latest code and record in build manifest
# =============================================================================
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
BRANCH="${BRANCH:-master}"
BATTLE_STATS="${REPO_DIR}/battle_stats.json"

PRE_SHA=$(git -C "$REPO_DIR" rev-parse --short=8 HEAD 2>/dev/null || echo "unknown")

git -C "$REPO_DIR" pull origin "$BRANCH" --quiet 2>&1 || {
    echo "[deploy] WARNING: git pull failed"
    exit 1
}

POST_SHA=$(git -C "$REPO_DIR" rev-parse --short=8 HEAD 2>/dev/null || echo "unknown")

if [ "$PRE_SHA" != "$POST_SHA" ]; then
    echo "[deploy] Code updated: $PRE_SHA -> $POST_SHA"
    python3 -c "
import sys, json, os
sys.path.insert(0, '$REPO_DIR')
try:
    from infrastructure.build_manifest import get_manifest
    m = get_manifest()
    battle_count = 0
    bs = '$BATTLE_STATS'
    if os.path.exists(bs):
        with open(bs) as f:
            d = json.load(f)
        battle_count = len(d.get('battles', d) if isinstance(d, dict) else d)
    entry = m.record_deploy(progress_count=battle_count, source='deploy_update.sh')
    print(f'Build manifest: {entry[\"sha\"]} at progress={entry[\"progress_at_deploy\"]}')
except Exception as e:
    print(f'WARNING: build manifest update failed: {e}', file=sys.stderr)
" 2>&1 || true
else
    echo "[deploy] No new code. Still on $POST_SHA."
fi
