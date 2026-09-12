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
else
  echo "Files being saved:"
  echo "$STAGED" | sed 's/^/  ✅ /'
  echo ""

  # Commit locally
  git commit -m "$MSG"
  echo "✅ Snapshot saved locally."
  echo "   Commit: $(git log -1 --oneline)"
fi

# BUG FIX 2026-09-11 (found live by Raghav): --push used to be INSIDE the
# "nothing new to save" early-exit above, via a bare `exit 0` before this
# block ever ran. Committing (new changes) and pushing (existing commits to
# GitHub) are two separate concerns — running `snapshot.sh --push` right
# after `snapshot.sh "msg"` (a very natural two-step habit) had nothing NEW
# to stage on the second call, so it exited before ever reaching the push
# step. Real consequence: 137 local commits sat completely unpushed,
# un-backed-up, with the tool reporting success both times. Push now always
# runs when --push is passed, regardless of whether this call had anything
# new to commit.
if [ "$PUSH" = true ]; then
  echo ""
  echo "Pushing to GitHub..."
  git push origin main 2>/dev/null || git push origin master 2>/dev/null
  echo "✅ Also pushed to GitHub: $(git remote get-url origin)"
fi

echo ""
