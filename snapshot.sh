#!/bin/bash
# snapshot.sh — Save your current pipeline state to GitHub instantly.
# Run this anytime: bash snapshot.sh
# Run before a risky edit: bash snapshot.sh "before reworking answer logic"

set -e
cd "$(dirname "$0")"

MSG="${1:-snapshot $(date '+%Y-%m-%d %H:%M')}"

echo ""
echo "📸 Saving snapshot: $MSG"
echo "──────────────────────────────────────────"

# Remove stale lock if present
rm -f .git/index.lock 2>/dev/null || true

# Stage all tracked changes (modifications + deletions)
git add -u

# Stage new important files (not bulk run logs or sessions)
git add \
  *.py *.sh *.md *.txt .gitignore .env.example LICENSE \
  *.command \
  data/apply_log.json data/indeed_applied_log.json \
  data/Application_Tracker.xlsx \
  Raghavendra_Karanam_Micro1_Data_Engineer.docx \
  2>/dev/null || true

# Show what's being saved
STAGED=$(git diff --cached --name-only 2>/dev/null)
if [ -z "$STAGED" ]; then
  echo "ℹ️  Nothing new to save — already up to date."
  echo ""
  exit 0
fi

echo "Files being saved:"
echo "$STAGED" | sed 's/^/  ✅ /'
echo ""

# Commit
git commit -m "$MSG"

# Push to GitHub
echo "Pushing to GitHub..."
git push origin main 2>/dev/null || git push origin master 2>/dev/null
echo ""
echo "✅ Snapshot saved to GitHub: $(git remote get-url origin)"
echo "   Commit: $(git log -1 --oneline)"
echo ""
