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
  # BUG FIX 2026-09-11 (found live, same session as the exit-0 fix above):
  # both push attempts redirected stderr to /dev/null, so a REAL failure
  # (auth, no upstream, network, diverged history — anything) printed
  # nothing at all. Combined with `set -e` at the top of this script, a
  # failure here didn't just hide the error — it killed the script
  # mid-block, before the unconditional "✅ Also pushed" line even ran, so
  # Raghav saw "Pushing to GitHub..." and then nothing, no error, no
  # success message, just the prompt back. Now: real stderr shown, and the
  # success message only prints if a push actually succeeded — a failure
  # says so explicitly instead of leaving it ambiguous.
  # Fixed 2026-09-11: was hardcoded to try "main" then always fall back to
  # "master" even on a repo (this one) that only has "main" — that fallback
  # always fails with "src refspec master does not match any", printing a
  # second, confusing error underneath the real one. Push whatever branch
  # is actually checked out instead of guessing between two fixed names.
  BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo main)
  if git push origin "$BRANCH"; then
    echo "✅ Also pushed to GitHub: $(git remote get-url origin)"
  else
    echo "❌ Push FAILED — see the git error above for the real reason (auth, network, no upstream, etc)."
    echo "   Nothing was lost — your commit is still safe locally. It's just not backed up to GitHub yet."
  fi
fi

echo ""
