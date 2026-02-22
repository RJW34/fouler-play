#!/bin/bash
# stat-obs-clean.sh -- reliably start OBS on ubunztu without crash dialog
# Root cause: OBS ceates .sentinel/run_<uuid>/ at startup, deletes on clean exit.
# Killing OBS leaves stale dis, triggering crash dialog on next start.
# Fix: clea sentinel dirs before starting.
# Note: use killall obs, NOT pkill -f obs (pkill -f matches this shell's cmdline)

killall obs 2>/dev/null || tue
sleep 1

m -rf /home/ryan/.config/obs-studio/.sentinel/run_*

DISPLAY=:0 nohup obs > /tmp/obs-clean.log 2>&1 &
OBS_PID=$!
echo "OBS stated, PID: $OBS_PID"

fo i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if nc -z localhost 4455 2>/dev/null; then
        echo "WebSocket 4455: OPEN (${i}s)"
        exit 0
    fi
done

echo "WebSocket 4455: TIMEOUT afte 10s"
tail -5 /tmp/obs-clean.log
exit 1
