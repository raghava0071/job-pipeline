#!/bin/bash
# =============================================================================
# SETUP_SCHEDULER.SH — Install daily auto-run via macOS launchd
#
# Runs linkedin_apply_now.py --limit 10 every morning at 8:00 AM automatically.
# No terminal needed — pipeline runs silently in background.
#
# Usage:
#   bash ~/job_pipeline/setup_scheduler.sh          # install
#   bash ~/job_pipeline/setup_scheduler.sh remove   # uninstall
# =============================================================================

PLIST_NAME="com.raghav.jobpipeline"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
PYTHON_PATH=$(which python)
PIPELINE_DIR="$HOME/job_pipeline"
LOG_DIR="$HOME/job_pipeline/data"

# ── Remove / uninstall ────────────────────────────────────────────────────────
if [ "$1" = "remove" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null
    rm -f "$PLIST_PATH"
    echo "✅ Scheduler removed."
    exit 0
fi

# ── Install ───────────────────────────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$LOG_DIR"

cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_PATH}</string>
        <string>${PIPELINE_DIR}/linkedin_apply_now.py</string>
        <string>--limit</string>
        <string>10</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PIPELINE_DIR}</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/scheduler_out.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/scheduler_err.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" 2>/dev/null
launchctl load "$PLIST_PATH"

echo ""
echo "✅ Daily pipeline scheduler installed!"
echo ""
echo "   Runs every morning at 8:00 AM automatically"
echo "   Applies to up to 10 jobs per day"
echo "   Logs saved to: $LOG_DIR/scheduler_out.log"
echo ""
echo "   To uninstall:  bash ~/job_pipeline/setup_scheduler.sh remove"
echo "   To check status: launchctl list | grep raghav"
echo ""
