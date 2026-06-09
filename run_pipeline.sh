#!/bin/bash
# ─────────────────────────────────────────────
# runjobs — Run the full job application pipeline
# Usage: runjobs
# ─────────────────────────────────────────────

cd /Users/raghava/job_pipeline

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
python indeed_apply_now.py --limit 50
echo ""

# Run LinkedIn applications
echo "💼 Starting LinkedIn applications (50 jobs)..."
python linkedin_apply_now.py --limit 50
echo ""

echo "✅ Pipeline complete!"
