#!/bin/bash
# push_to_github.sh — Push job_pipeline to GitHub (safe files only)
set -e

cd ~/job_pipeline

# Init git if not already
if [ ! -d ".git" ]; then
    git init
    echo "✅ Git initialized"
fi

# Create .gitignore to never push sensitive files
cat > .gitignore << 'EOF'
.env
raghav_profile.py
.indeed_session/
.linkedin_session/
__pycache__/
*.pyc
*.pyo
data/answer_cache.db
screenshots/
pipeline_log.txt
EOF

# Add all safe files
git add \
    indeed_apply_now.py \
    linkedin_apply_now.py \
    auto_apply.py \
    run_all.py \
    master_run.py \
    config.py \
    claude_engine.py \
    resume_builder.py \
    cover_letter.py \
    jd_parser.py \
    linkedin_scraper.py \
    indeed_scraper.py \
    answer_cache.py \
    notifier.py \
    tracker.py \
    qa_answers.py \
    phase4_errors.md \
    setup_scheduler.sh \
    push_to_github.sh \
    .gitignore \
    2>/dev/null || true

# Commit
MSG="Update pipeline — $(date '+%Y-%m-%d %H:%M')"
git commit -m "$MSG" 2>/dev/null || echo "Nothing new to commit"

# Push — set remote if not set
REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
if [ -z "$REMOTE" ]; then
    echo ""
    echo "⚠️  No GitHub remote set yet."
    echo "   Run this once to connect your repo:"
    echo "   git remote add origin https://github.com/YOUR_USERNAME/job_pipeline.git"
    echo "   Then run this script again."
    exit 1
fi

git push origin main 2>/dev/null || git push origin master 2>/dev/null || git push --set-upstream origin main
echo "✅ Pushed to GitHub: $REMOTE"
