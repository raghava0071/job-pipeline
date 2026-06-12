#!/bin/bash
# Run Indeed pipeline — fixed version
cd ~/job_pipeline

bash snapshot.sh "pre-run snapshot $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || true

# No PDF conversion — pipeline uploads DOCX directly (same as LinkedIn)

python seed_cache.py
python -u indeed_apply_now.py --limit 50
