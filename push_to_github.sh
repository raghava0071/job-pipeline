#!/bin/bash
# push_to_github.sh — Push job_pipeline to GitHub (safe files only)
set -e

cd ~/job_pipeline

# Init git if not already
if [ ! -d ".git" ]; then
    git init
    echo "✅ Git initialized"
fi

# Always write/update .gitignore to block sensitive files
cat > .gitignore << 'EOF'
# Sensitive — never push these
.env
raghav_profile.py

# Browser sessions
.indeed_session/
.linkedin_session/

# Generated data (not needed in repo)
__pycache__/
*.pyc
*.pyo
data/answer_cache.db
data/*.db
screenshots/
pipeline_log.txt

# Output files (large, regenerated each run)
resumes/
cover_letters/
output/
data/raw_jobs.csv
data/filtered_jobs.csv
data/linkedin_jobs.csv
data/scheduler_out.log
data/scheduler_err.log
EOF

# Stage ALL safe project files
git add *.py *.sh *.md *.txt .gitignore .env.example LICENSE 2>/dev/null || true

# Also stage data files that are useful to keep (logs, tracker)
git add data/apply_log.json data/indeed_applied_log.json 2>/dev/null || true
git add data/Application_Tracker.xlsx 2>/dev/null || true

# Show what's being committed
echo "Files staged:"
git diff --cached --name-only 2>/dev/null | sed 's/^/  ✅ /'

# Commit
MSG="Pipeline update — $(date '+%Y-%m-%d %H:%M')"
if git diff --cached --quiet; then
    echo "ℹ️  Nothing new to commit — all files already up to date"
else
    git commit -m "$MSG"
fi

# Push — check remote is set
REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
if [ -z "$REMOTE" ]; then
    echo ""
    echo "⚠️  No GitHub remote set yet."
    echo "   Run this once:"
    echo "   git remote add origin https://github.com/YOUR_USERNAME/job-pipeline.git"
    echo "   Then run this script again."
    exit 1
fi

git push origin main 2>/dev/null || git push origin master 2>/dev/null || git push --set-upstream origin main
echo "✅ Pushed to GitHub: $REMOTE"
