#!/bin/bash
# ─────────────────────────────────────────────
# runjobs — Run the full job application pipeline
# Usage: runjobs
# ─────────────────────────────────────────────

cd /Users/raghava/job_pipeline

# Force Python to flush output line-by-line instead of block-buffering it.
# Without this, stdout piped through `tee` (below) buffers in ~4-8KB chunks —
# so if the pipeline gets cancelled mid-run (e.g. during a CAPTCHA), whatever
# hadn't been flushed yet (often the most recent, most useful minute of
# activity) never makes it into the saved log file. Bit Raghav directly on
# 2026-07-08: he solved a CAPTCHA and hit a stuck Submit button, cancelled the
# run, and none of it was in data/debug_logs/ — this is why.
export PYTHONUNBUFFERED=1

# Auto-save full run output so failures can be diagnosed without manual
# copy-paste (added 2026-07-08 at Raghav's request). Keeps the last 10 runs.
mkdir -p data/debug_logs
LOG_FILE="data/debug_logs/run_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
ls -1t data/debug_logs/run_*.log 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "📝 Full output being saved to: $LOG_FILE"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       Job Application Pipeline           ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Auto-backup raghav_profile.py to iCloud (private, never on GitHub)
ICLOUD=~/Library/Mobile\ Documents/com~apple~CloudDocs
if [ -d "$ICLOUD" ]; then
    cp raghav_profile.py "$ICLOUD/raghav_profile_PRIVATE.py" 2>/dev/null
    echo "🔒 Profile backed up to iCloud"
fi

# Pre-flight check — catch broken code before wasting a run
echo "🔍 Running pre-flight check..."
python preflight_check.py --strict
if [ $? -ne 0 ]; then
    echo "❌ Pre-flight failed — pipeline aborted. Fix errors above first."
    exit 1
fi
echo ""

# Pre-seed the answer cache
echo "📦 Seeding answer cache..."
python seed_cache.py
echo ""

# Run Indeed applications
echo "🔍 Starting Indeed applications (50 jobs)..."
python -u indeed_apply_now.py --limit 50
echo ""

# Run LinkedIn applications
echo "💼 Starting LinkedIn applications (50 jobs)..."
python -u linkedin_apply_now.py --limit 50
echo ""

echo "✅ Pipeline complete!"
