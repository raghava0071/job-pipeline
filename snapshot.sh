#!/bin/bash
# snapshot.sh — Save your current pipeline state locally (laptop-first).
#
# USAGE:
#   bash snapshot.sh                        # save locally (default)
#   bash snapshot.sh "before new logic"     # save with a message
#   bash snapshot.sh --push                 # save + push to GitHub
#   bash snapshot.sh "my message" --push    # save with message + push to GitHub

set -e
cd "$(dirname "$0")"

# Parse args — message is any non-flag arg, --push triggers GitHub push
MSG=""
PUSH=false
for arg in "$@"; do
  if [ "$arg" = "--push" ]; then
    PUSH=true
  else
    MSG="$arg"
  fi
done
MSG="${MSG:-snapshot $(date '+%Y-%m-%d %H:%M')}"

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

# Commit locally
git commit -m "$MSG"
echo "✅ Snapshot saved locally."
echo "   Commit: $(git log -1 --oneline)"

# Push to GitHub only if --push was passed
if [ "$PUSH" = true ]; then
  echo ""
  echo "Pushing to GitHub..."
  git push origin main 2>/dev/null || git push origin master 2>/dev/null
  echo "✅ Also pushed to GitHub: $(git remote get-url origin)"
fi

echo ""
