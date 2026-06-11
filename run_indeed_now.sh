#!/bin/bash
# Run Indeed pipeline — fixed version
cd ~/job_pipeline

# No PDF conversion — pipeline uploads DOCX directly (same as LinkedIn)

python seed_cache.py
python -u indeed_apply_now.py --limit 50
