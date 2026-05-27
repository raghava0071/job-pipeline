#!/bin/bash
# ====================================================================
#  RAGHAVENDRA KARANAM — PRO JOB APPLICATION PIPELINE LAUNCHER
#  Double-click in Finder to run. Choose a mode from the menu.
# ====================================================================

PIPELINE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PIPELINE_DIR"

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║      RAGHAVENDRA KARANAM — PRO JOB APPLICATION PIPELINE          ║"
echo "║        Data Engineer  |  98%+ ATS  |  Auto-Apply Engine           ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ── STEP 0: Load RapidAPI key ─────────────────────────────────────────────────
OLD_PIPELINE="$HOME/Desktop/job-pipeline-project/pipeline.py"
RAPIDAPI_KEY=""

if [ -f "$OLD_PIPELINE" ]; then
    RAPIDAPI_KEY=$(grep -o '"[A-Za-z0-9]\{40,\}"' "$OLD_PIPELINE" | head -1 | tr -d '"')
    if [ -z "$RAPIDAPI_KEY" ]; then
        RAPIDAPI_KEY=$(grep -o "'[A-Za-z0-9]\{40,\}'" "$OLD_PIPELINE" | head -1 | tr -d "'")
    fi
fi

if [ -n "$RAPIDAPI_KEY" ]; then
    echo "  ✅  RapidAPI key loaded"
    export RAPIDAPI_KEY
else
    echo "  Enter your RapidAPI key:"
    read -r RAPIDAPI_KEY
    export RAPIDAPI_KEY
fi
echo ""

# ── STEP 1: Install all dependencies ─────────────────────────────────────────
echo "  📦  Installing Python dependencies..."
pip install python-docx openpyxl pandas requests playwright \
    --quiet --break-system-packages 2>/dev/null \
  || pip install python-docx openpyxl pandas requests playwright --quiet 2>/dev/null \
  || pip3 install python-docx openpyxl pandas requests playwright --quiet

echo "  🌐  Installing Playwright browser (for auto-apply)..."
python -m playwright install chromium 2>/dev/null \
  || python3 -m playwright install chromium 2>/dev/null

echo "  ✅  All dependencies ready"
echo ""

# ── STEP 2: Choose mode ───────────────────────────────────────────────────────
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  Choose pipeline mode:                                    │"
echo "  │                                                           │"
echo "  │  1) Full pipeline  — Fetch jobs + build resumes           │"
echo "  │     (fetches fresh jobs, builds 98%+ ATS resumes)         │"
echo "  │                                                           │"
echo "  │  2) Full pipeline + AUTO-APPLY                            │"
echo "  │     (same as 1, then auto-applies via LinkedIn/browser)   │"
echo "  │                                                           │"
echo "  │  3) Use existing jobs + rebuild resumes                   │"
echo "  │     (skips API fetch, reuses raw_jobs.csv)                │"
echo "  │                                                           │"
echo "  │  4) Auto-apply ONLY (resumes already built)               │"
echo "  │                                                           │"
echo "  │  5) Preview auto-apply (dry run — no real applications)   │"
echo "  └──────────────────────────────────────────────────────────┘"
echo ""
echo -n "  Enter choice [1-5]: "
read -r CHOICE

echo ""

case "$CHOICE" in
    1)
        echo "  🚀  Running full pipeline (fetch + resume build)..."
        python master_run.py 2>&1 || python3 master_run.py 2>&1
        ;;
    2)
        echo "  🚀  Running full pipeline + AUTO-APPLY..."
        echo "  📧  LinkedIn credentials will be prompted during auto-apply."
        echo ""
        python master_run.py --auto-apply 2>&1 || python3 master_run.py --auto-apply 2>&1
        ;;
    3)
        echo "  🚀  Rebuilding resumes from existing jobs..."
        python master_run.py --skip-fetch 2>&1 || python3 master_run.py --skip-fetch 2>&1
        ;;
    4)
        echo "  🤖  Running auto-apply engine only..."
        echo "  📧  Enter your LinkedIn email (for Easy Apply automation):"
        read -r LINKEDIN_EMAIL
        export LINKEDIN_EMAIL
        python auto_apply.py --execute 2>&1 || python3 auto_apply.py --execute 2>&1
        ;;
    5)
        echo "  👀  Dry-run preview (no real applications submitted)..."
        python auto_apply.py --execute --dry-run 2>&1 || python3 auto_apply.py --execute --dry-run 2>&1
        ;;
    *)
        echo "  Running full pipeline..."
        python master_run.py 2>&1 || python3 master_run.py 2>&1
        ;;
esac

echo ""
echo "  ═══════════════════════════════════════════════════════════"
echo "  📁  Your files are saved in:"
echo "      Resumes:        $(pwd)/output/resumes/"
echo "      Cover Letters:  $(pwd)/output/cover_letters/"
echo "      Tracker:        $(pwd)/data/Application_Tracker.xlsx"
echo "      Apply Log:      $(pwd)/data/apply_log.json"
echo "  ═══════════════════════════════════════════════════════════"
echo ""
echo "  Press any key to close..."
read -n 1 -s
