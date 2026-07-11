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
    # BUG FIX 2026-07-11: this step was missing entirely — "keep" went
    # straight to `git checkout main` + `git merge` without ever committing
    # whatever was edited on the experiment branch. Since uncommitted
    # working-tree changes survive a branch checkout when they don't
    # conflict, the merge itself was always a silent no-op ("Already up to
    # date") and the real edits just sat as uncommitted changes on main —
    # discovered only because the next `start` call's snapshot swept them up
    # under the NEXT cycle's "before: ..." message, mislabeling every commit
    # in this workflow's history with the wrong description. Committing here
    # first means the merge actually carries real commits, and the message
    # correctly reflects what this experiment was for.
    echo "💾 Committing changes made on '$CURRENT'..."
    git add -A
    git commit -m "$CURRENT" --quiet 2>/dev/null || echo "   (nothing new to commit)"
    echo ""
    echo "✅ Merging experiment '$CURRENT' into main..."
    git checkout main 2>/dev/null || git checkout master
    git merge "$CURRENT" --no-ff -m "merge: $CURRENT"
    git branch -d "$CURRENT" 2>/dev/null || true
    echo ""
    echo "✅ Changes merged to main — saved locally on your laptop."
    echo "   When ready to back up to GitHub: bash snapshot.sh --push"
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
    # BUG FIX 2026-07-11: `git checkout main` alone does NOT remove
    # uncommitted working-tree edits when they don't conflict with main's
    # own tracked content — so "discard" was silently keeping every
    # uncommitted change from the failed experiment instead of actually
    # throwing it away, the opposite of what its name and the message below
    # promise. Reset the working tree back to main's committed state first.
    git reset --hard HEAD --quiet 2>/dev/null || true
    git checkout main 2>/dev/null || git checkout master
    git reset --hard main --quiet 2>/dev/null || git reset --hard master --quiet 2>/dev/null || true
    git clean -fd --quiet 2>/dev/null || true
    git branch -D "$CURRENT" 2>/dev/null || true
    echo ""
    echo "✅ Back on main. Your previous working code is intact — experimental edits discarded."
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
