#!/bin/bash
# LinkedIn-only run — clears stale session lock first
cd ~/job_pipeline

echo "============================================================"
echo "  LinkedIn Apply — 50 applications"
echo "  $(date)"
echo "============================================================"
echo "Saving snapshot before run..."
bash snapshot.sh "pre-run snapshot $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "  (snapshot skipped)"
echo ""
echo "Clearing stale session lock if present..."
rm -f ~/.linkedin_session/SingletonLock 2>/dev/null && echo "Lock cleared." || echo "No lock found."
echo ""
echo "Running LinkedIn (50 applications)..."
python -u linkedin_apply_now.py --limit 50
echo ""
echo "============================================================"
echo "  Done. Press any key to close."
echo "============================================================"
read -n 1
