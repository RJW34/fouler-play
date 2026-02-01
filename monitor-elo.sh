#!/bin/bash
# ELO Monitor for LEBOTJAMESXD005
# Target: 1700 in gen9ou
# Alerts on: timeouts, crashes, ELO drops

TARGET_ELO=1700
USERNAME="LEBOTJAMESXD005"
PROFILE_URL="https://pokemonshowdown.com/users/lebotjamesxd005"
LOG_FILE="/home/ryan/projects/fouler-play/elo-monitor.log"

# Get current ELO from profile page
get_current_elo() {
    curl -s "$PROFILE_URL" | grep "gen9ou" | grep -oP '<strong>\K\d+' | head -1
}

# Check if bot process is running
check_bot_running() {
    ps aux | grep -i "LEBOTJAMESXD005" | grep -v grep > /dev/null
    return $?
}

# Check battle timeout (if monitor.log hasn't been updated in 10 minutes)
check_timeout() {
    if [ -f monitor.log ]; then
        last_update=$(stat -c %Y monitor.log)
        current_time=$(date +%s)
        diff=$((current_time - last_update))
        
        if [ $diff -gt 600 ]; then
            echo "TIMEOUT: monitor.log hasn't updated in $((diff/60)) minutes"
            return 1
        fi
    fi
    return 0
}

echo "$(date): Starting ELO monitor" | tee -a "$LOG_FILE"

while true; do
    # Check if bot is running
    if ! check_bot_running; then
        echo "$(date): ❌ BOT CRASHED - process not found" | tee -a "$LOG_FILE"
        # Could add auto-restart here
    fi
    
    # Check for timeout
    if ! check_timeout; then
        echo "$(date): ⏱️ BATTLE TIMEOUT DETECTED" | tee -a "$LOG_FILE"
        tail -100 monitor.log | tee -a "$LOG_FILE"
    fi
    
    # Get current ELO
    CURRENT_ELO=$(get_current_elo)
    
    if [ -n "$CURRENT_ELO" ]; then
        echo "$(date): Current ELO: $CURRENT_ELO / Target: $TARGET_ELO ($(($TARGET_ELO - $CURRENT_ELO)) to go)" | tee -a "$LOG_FILE"
        
        if [ "$CURRENT_ELO" -ge "$TARGET_ELO" ]; then
            echo "$(date): 🎯 TARGET REACHED! ELO: $CURRENT_ELO" | tee -a "$LOG_FILE"
            break
        fi
    else
        echo "$(date): ⚠️ Could not fetch ELO from profile" | tee -a "$LOG_FILE"
    fi
    
    sleep 300  # Check every 5 minutes
done
