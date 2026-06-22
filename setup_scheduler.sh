#!/bin/bash
# =============================================================================
# SETUP_SCHEDULER.SH — Install 3x daily auto-run via macOS launchd
#
# Runs run_all.py at:
#   8:00 AM  — morning run
#   12:00 PM — afternoon run
#   6:00 PM  — evening run
#
# Usage:
#   bash ~/job_pipeline/setup_scheduler.sh          # install
#   bash ~/job_pipeline/setup_scheduler.sh remove   # uninstall
#   bash ~/job_pipeline/setup_scheduler.sh status   # check status
# =============================================================================

PYTHON_PATH=$(which python)
PIPELINE_DIR="$HOME/job_pipeline"
LOG_DIR="$HOME/job_pipeline/data"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$LAUNCH_DIR"
mkdir -p "$LOG_DIR"

LABELS=("com.raghav.jobpipeline.morning" "com.raghav.jobpipeline.afternoon" "com.raghav.jobpipeline.evening")
HOURS=(8 12 18)
NAMES=("morning" "afternoon" "evening")

# ── Remove ────────────────────────────────────────────────────────────────────
if [ "$1" = "remove" ]; then
    for label in "${LABELS[@]}"; do
        plist="$LAUNCH_DIR/$label.plist"
        launchctl unload "$plist" 2>/dev/null
        rm -f "$plist"
        echo "  Removed: $label"
    done
    echo "Schedulers removed."
    exit 0
fi

# ── Status ────────────────────────────────────────────────────────────────────
if [ "$1" = "status" ]; then
    echo ""
    echo "Scheduler status:"
    for label in "${LABELS[@]}"; do
        if launchctl list "$label" &>/dev/null; then
            echo "  ACTIVE:   $label"
        else
            echo "  MISSING:  $label"
        fi
    done
    echo ""
    echo "Last morning run (tail -20):"
    tail -20 "$LOG_DIR/scheduler_morning_out.log" 2>/dev/null || echo "  (no log yet)"
    exit 0
fi

# ── Install ───────────────────────────────────────────────────────────────────
echo ""
echo "Installing 3x daily pipeline scheduler..."

for i in 0 1 2; do
    HOUR="${HOURS[$i]}"
    NAME="${NAMES[$i]}"
    LABEL="${LABELS[$i]}"
    PLIST="$LAUNCH_DIR/$LABEL.plist"

    launchctl unload "$PLIST" 2>/dev/null

    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_PATH}</string>
        <string>-u</string>
        <string>${PIPELINE_DIR}/run_all.py</string>
        <string>--li-limit</string>
        <string>50</string>
        <string>--in-limit</string>
        <string>100</string>
        <string>--wd-limit</string>
        <string>10</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PIPELINE_DIR}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${HOUR}</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/scheduler_${NAME}_out.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/scheduler_${NAME}_err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/anaconda3/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PYTHONPATH</key>
        <string>${PIPELINE_DIR}</string>
    </dict>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
EOF

    launchctl load "$PLIST"

    if launchctl list "$LABEL" &>/dev/null; then
        if [ $HOUR -eq 8 ]; then TIME_STR="8:00 AM "; fi
        if [ $HOUR -eq 12 ]; then TIME_STR="12:00 PM"; fi
        if [ $HOUR -eq 18 ]; then TIME_STR="6:00 PM "; fi
        echo "  Installed: $TIME_STR — $LABEL"
    else
        echo "  FAILED:    $LABEL — check permissions"
    fi
done

echo ""
echo "Pipeline runs automatically 3x daily:"
echo "  8:00 AM  — morning   (50 LinkedIn + 100 Indeed)"
echo "  12:00 PM — afternoon (50 LinkedIn + 100 Indeed)"
echo "  6:00 PM  — evening   (50 LinkedIn + 100 Indeed)"
echo ""
echo "  NOTE: Mac must be AWAKE at run times."
echo "        If asleep, that run is skipped — not delayed."
echo ""
echo "  Logs: $LOG_DIR/scheduler_morning_out.log"
echo "        $LOG_DIR/scheduler_afternoon_out.log"
echo "        $LOG_DIR/scheduler_evening_out.log"
echo ""
echo "  Check status:  bash ~/job_pipeline/setup_scheduler.sh status"
echo "  Uninstall:     bash ~/job_pipeline/setup_scheduler.sh remove"
