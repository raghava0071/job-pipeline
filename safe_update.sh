#!/bin/bash
# safe_update.sh — Safely experiment without risking the working pipeline.
#
# USAGE:
#   bash safe_update.sh start  "trying new answer logic"   → creates experiment branch, saves main
#   bash safe_update.sh keep                                → experiment worked — merge back to main
#   bash safe_update.sh discard                             → experiment failed — go back to main
#   bash safe_update.sh status                              → see what branch you're on

set -e
cd "$(dirname "$0")"

COMMAND="${1:-status}"
DESCRIPTION="${2:-experiment $(date '+%Y-%m-%d %H:%M')}"
BRANCH="experiment/$(date '+%Y%m%d_%H%M')_$(echo "$DESCRIPTION" | tr ' /' '_' | tr -dc '[:alnum:]_' | cut -c1-30)"

case "$COMMAND" in

  start)
    echo ""
    echo "🔒 Saving current working state before experimenting..."
    bash "$(dirname "$0")/snapshot.sh" "before: $DESCRIPTION" 2>/dev/null || true

    echo ""
    echo "🌿 Creating experiment branch: $BRANCH"
    git checkout -b "$BRANCH"
    echo ""
    echo "✅ You're now on a safe experiment branch."
    echo "   Edit anything freely — main is untouched."
    echo ""
    echo "   When done:"
    echo "     bash safe_update.sh keep      → it worked, merge to main"
    echo "     bash safe_update.sh discard   → it failed, go back to main"
    echo ""
    ;;

  keep)
    CURRENT=$(git branch --show-current)
    if [ "$CURRENT" = "main" ] || [ "$CURRENT" = "master" ]; then
      echo "⚠️  Already on main — nothing to merge."
      exit 0
    fi
    echo ""
    echo "✅ Merging experiment '$CURRENT' into main..."
    bash "$(dirname "$0")/snapshot.sh" "experiment result: $CURRENT" 2>/dev/null || true
    git checkout main 2>/dev/null || git checkout master
    git merge "$CURRENT" --no-ff -m "merge: $CURRENT"
    git push origin main 2>/dev/null || git push origin master
    git branch -d "$CURRENT" 2>/dev/null || true
    echo ""
    echo "✅ Changes merged to main and pushed to GitHub."
    echo ""
    ;;

  discard)
    CURRENT=$(git branch --show-current)
    if [ "$CURRENT" = "main" ] || [ "$CURRENT" = "master" ]; then
      echo "⚠️  Already on main — nothing to discard."
      exit 0
    fi
    echo ""
    echo "🗑️  Discarding experiment '$CURRENT', returning to main..."
    git checkout main 2>/dev/null || git checkout master
    git branch -D "$CURRENT" 2>/dev/null || true
    echo ""
    echo "✅ Back on main. Your previous working code is intact."
    echo ""
    ;;

  status)
    echo ""
    echo "📍 Current branch: $(git branch --show-current)"
    echo "   Last commit:     $(git log -1 --oneline)"
    echo "   Uncommitted:     $(git status --short | grep -c '^.' || echo 0) file(s)"
    BRANCHES=$(git branch | grep experiment/ | wc -l | tr -d ' ')
    if [ "$BRANCHES" -gt 0 ]; then
      echo "   Experiment branches:"
      git branch | grep experiment/ | sed 's/^/     /'
    fi
    echo ""
    ;;

  *)
    echo "Usage: bash safe_update.sh [start|keep|discard|status] [description]"
    exit 1
    ;;
esac
