#!/bin/bash
# Job Pipeline — Full Run (Indeed + LinkedIn, 50 each)
cd ~/job_pipeline

echo "============================================================"
echo "  Job Pipeline — Full Run"
echo "  $(date)"
echo "============================================================"
echo ""
echo "Step 0: Saving snapshot before run..."
bash snapshot.sh "pre-run snapshot $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "  (snapshot skipped — no changes or no network)"
echo ""
echo "Step 1: Seeding answer cache..."
python seed_cache.py
echo ""
echo "Step 2: Running Indeed (50 applications)..."
python -u indeed_apply_now.py --limit 50
echo ""
echo "Step 3: Running LinkedIn (50 applications)..."
python -u linkedin_apply_now.py --limit 50
echo ""
echo "============================================================"
echo "  Pipeline complete. Press any key to close."
echo "============================================================"
read -n 1
