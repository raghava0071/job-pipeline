#!/bin/bash
# Run Indeed pipeline — fixed version
cd ~/job_pipeline

# Auto-save full run output so failures can be diagnosed without manual
# copy-paste (added 2026-07-08 at Raghav's request). Keeps the last 10 runs.
mkdir -p data/debug_logs
LOG_FILE="data/debug_logs/run_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
ls -1t data/debug_logs/run_*.log 2>/dev/null | tail -n +11 | xargs -r rm -f
echo "📝 Full output being saved to: $LOG_FILE"

bash snapshot.sh "pre-run snapshot $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || true

# No PDF conversion — pipeline uploads DOCX directly (same as LinkedIn)

python seed_cache.py
python -u indeed_apply_now.py --limit 50
