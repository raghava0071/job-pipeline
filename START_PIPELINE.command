#!/bin/bash
# ====================================================================
#  RAGHAVENDRA KARANAM — JOB APPLICATION PIPELINE
#  Double-click in Finder to run.
#
#  STRICT RULES (hardcoded, never change):
#    ✅ LinkedIn  → Easy Apply ONLY (never external redirect)
#    ✅ Indeed    → In-Portal ONLY  (never "Apply on Company Site")
#    ❌ All other portals  → SKIP
#    ✅ Claude fit gate    → Only score ≥ 65% gets a resume + apply
# ====================================================================

# Remove macOS quarantine flag so script runs without Gatekeeper block
xattr -d com.apple.quarantine "$0" 2>/dev/null

PIPELINE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PIPELINE_DIR" || exit 1

# ── Python binary (prefer conda) ─────────────────────────────────────────────
PY="/opt/anaconda3/bin/python3"
if ! command -v "$PY" &>/dev/null; then
    PY="$(command -v python3 || command -v python)"
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
hr() { echo "  ──────────────────────────────────────────────────────────"; }
ok() { echo "  ✅  $1"; }
info() { echo "  ℹ️   $1"; }
warn() { echo "  ⚠️   $1"; }

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║      RAGHAVENDRA KARANAM  —  PRO JOB APPLICATION PIPELINE        ║"
echo "║   LinkedIn Easy Apply  +  Indeed In-Portal  •  Claude AI Scoring  ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Python: $PY"
echo "  Dir   : $PIPELINE_DIR"
hr
echo ""

# ── Step 0: Quick dependency check ───────────────────────────────────────────
echo "  📦  Checking dependencies..."
"$PY" -c "import anthropic, pandas, playwright, docx, openpyxl, rich" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "  Installing missing packages..."
    "$PY" -m pip install anthropic rich pandas openpyxl playwright python-docx \
        --quiet --break-system-packages 2>/dev/null \
      || "$PY" -m pip install anthropic rich pandas openpyxl playwright python-docx --quiet 2>/dev/null
    "$PY" -m playwright install chromium 2>/dev/null
fi
ok "Dependencies ready"
echo ""

# ── Mode menu ────────────────────────────────────────────────────────────────
echo "  ┌──────────────────────────────────────────────────────────────┐"
echo "  │  Choose what to do:                                           │"
echo "  │                                                               │"
echo "  │  1) 🔄  Full run  (scrape → score → build resumes → apply)   │"
echo "  │         Scrapes LinkedIn + Indeed → Claude scores all jobs    │"
echo "  │         Builds custom resumes → confirms → auto-applies       │"
echo "  │                                                               │"
echo "  │  2) 🔵  LinkedIn Easy Apply only                              │"
echo "  │         Scrape LinkedIn → score → build resumes → apply       │"
echo "  │                                                               │"
echo "  │  3) 🟡  Indeed In-Portal only                                 │"
echo "  │         Scrape Indeed → score → build resumes → apply         │"
echo "  │                                                               │"
echo "  │  4) 📊  Score + build resumes  (skip scraping)               │"
echo "  │         Uses existing linkedin_jobs.csv / indeed_jobs.csv     │"
echo "  │                                                               │"
echo "  │  5) 👀  Preview apply queue  (dry run, no submissions)        │"
echo "  │                                                               │"
echo "  │  6) 🚀  Apply NOW  (resumes already built, just apply)        │"
echo "  │                                                               │"
echo "  └──────────────────────────────────────────────────────────────┘"
echo ""
echo -n "  Enter choice [1-6]: "
read -r CHOICE
echo ""

# ═════════════════════════════════════════════════════════════════════════════
# SUB-FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

run_linkedin_scrape() {
    echo ""
    echo "  🔵  Step 1/3 — Scraping LinkedIn Easy Apply jobs..."
    hr
    "$PY" linkedin_scraper.py
    LINKEDIN_COUNT=$([ -f data/linkedin_jobs.csv ] && tail -n +2 data/linkedin_jobs.csv | wc -l | tr -d ' ' || echo 0)
    echo ""
    ok "LinkedIn: $LINKEDIN_COUNT Easy Apply jobs collected → data/linkedin_jobs.csv"
}

run_indeed_scrape() {
    echo ""
    echo "  🟡  Step 2/3 — Scraping Indeed In-Portal jobs..."
    hr
    "$PY" indeed_scraper.py
    INDEED_COUNT=$([ -f data/indeed_jobs.csv ] && tail -n +2 data/indeed_jobs.csv | wc -l | tr -d ' ' || echo 0)
    echo ""
    ok "Indeed: $INDEED_COUNT In-Portal jobs collected → data/indeed_jobs.csv"
}

run_score_and_build() {
    local label="${1:-Step}"
    echo ""
    echo "  🤖  $label — Claude AI scoring + custom resume build..."
    hr
    echo "  This scores every job (0-100%) and builds a tailored resume for each."
    echo "  Only jobs scoring ≥ 65% will get a resume and appear in the apply queue."
    echo ""
    "$PY" master_run.py --skip-fetch
}

run_dry_run() {
    echo ""
    echo "  👀  Dry-run preview — showing apply queue (no submissions)..."
    hr
    "$PY" auto_apply.py --dry-run
}

run_apply() {
    local source_flag="${1:---source all}"
    echo ""
    echo "  ──────────────────────────────────────────────────────────────"
    echo "  ⚡  About to ACTUALLY APPLY to jobs on LinkedIn & Indeed."
    echo "  ──────────────────────────────────────────────────────────────"
    echo ""
    echo "  Rules enforced automatically:"
    echo "    • LinkedIn  → Easy Apply button ONLY (skips external)"
    echo "    • Indeed    → In-Portal ONLY (skips 'Apply on Company Site')"
    echo "    • Fit gate  → Only ≥ 65% Claude score"
    echo "    • Custom resume attached to every application"
    echo ""
    echo -n "  Type YES to apply, or press Enter to cancel: "
    read -r CONFIRM
    echo ""

    if [ "$CONFIRM" = "YES" ]; then
        "$PY" auto_apply.py --execute $source_flag
    else
        warn "Cancelled — no applications submitted."
        info "Run option 5 to preview the queue, or 6 when ready to apply."
    fi
}

# ═════════════════════════════════════════════════════════════════════════════
# DISPATCH
# ═════════════════════════════════════════════════════════════════════════════

case "$CHOICE" in

    1)  # Full run: both scrapers → score → apply
        echo "  🔄  FULL RUN — LinkedIn + Indeed → Score → Build → Apply"
        hr

        run_linkedin_scrape
        run_indeed_scrape
        run_score_and_build "Step 3/3"

        echo ""
        hr
        echo "  📋  APPLY QUEUE PREVIEW (jobs qualifying at ≥65% fit score):"
        hr
        "$PY" auto_apply.py --dry-run
        hr

        run_apply "--source all"
        ;;

    2)  # LinkedIn only
        echo "  🔵  LINKEDIN ONLY — Easy Apply jobs"
        hr

        run_linkedin_scrape
        run_score_and_build "Step 2/2"

        echo ""
        hr
        echo "  📋  LINKEDIN APPLY QUEUE PREVIEW:"
        hr
        "$PY" auto_apply.py --dry-run --source linkedin
        hr

        run_apply "--source linkedin"
        ;;

    3)  # Indeed only
        echo "  🟡  INDEED ONLY — In-Portal jobs"
        hr

        run_indeed_scrape
        run_score_and_build "Step 2/2"

        echo ""
        hr
        echo "  📋  INDEED APPLY QUEUE PREVIEW:"
        hr
        "$PY" auto_apply.py --dry-run --source indeed
        hr

        run_apply "--source indeed"
        ;;

    4)  # Score and build only (already have CSVs)
        echo "  📊  SCORE + BUILD — using existing scraped job files"
        hr

        # Show what we have
        LI=$([ -f data/linkedin_jobs.csv ] && tail -n +2 data/linkedin_jobs.csv | wc -l | tr -d ' ' || echo 0)
        IND=$([ -f data/indeed_jobs.csv ] && tail -n +2 data/indeed_jobs.csv | wc -l | tr -d ' ' || echo 0)
        info "linkedin_jobs.csv : $LI jobs"
        info "indeed_jobs.csv   : $IND jobs"

        if [ "$LI" -eq 0 ] && [ "$IND" -eq 0 ]; then
            warn "No job files found. Run option 1, 2, or 3 first to scrape jobs."
            echo ""
        else
            run_score_and_build "Scoring + building resumes"
        fi
        ;;

    5)  # Preview dry run
        echo "  👀  PREVIEW — Apply queue (no applications submitted)"
        hr
        "$PY" auto_apply.py --dry-run
        ;;

    6)  # Apply now (resumes already built)
        echo "  🚀  APPLY NOW — resumes already built"
        hr
        run_dry_run
        run_apply "--source all"
        ;;

    *)
        warn "Invalid choice. Running dry-run preview instead..."
        "$PY" auto_apply.py --dry-run
        ;;
esac

# ── Final summary ─────────────────────────────────────────────────────────────
echo ""
hr
echo "  📁  Files:"
echo "      LinkedIn jobs : $PIPELINE_DIR/data/linkedin_jobs.csv"
echo "      Indeed jobs   : $PIPELINE_DIR/data/indeed_jobs.csv"
echo "      Resumes       : $PIPELINE_DIR/resumes/"
echo "      Cover letters : $PIPELINE_DIR/cover_letters/"
echo "      Apply log     : $PIPELINE_DIR/data/apply_log.json"
echo "      Tracker       : $PIPELINE_DIR/data/Application_Tracker.xlsx"
hr
echo ""
echo "  Press any key to close..."
read -n 1 -s
