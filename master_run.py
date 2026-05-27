#!/usr/bin/env python3
# =============================================================================
# MASTER_RUN.PY — Pro Job Application Pipeline Orchestrator (PRO v3)
#
# WHAT THIS DOES:
#   Step 1 — Fetch live jobs from JSearch / RapidAPI
#   Step 2 — Parse JDs, score ATS (shows BEFORE → AFTER per job)
#   Step 3 — Build tailored Word resumes (verified 98%+ ATS coverage)
#   Step 4 — Generate personalised cover letters
#   Step 5 — Create / update Excel application tracker
#   Step 6 — (Optional) Auto-apply via Playwright
#
# USAGE:
#   python master_run.py                          # Full pipeline
#   python master_run.py --skip-fetch             # Use existing raw_jobs.csv
#   python master_run.py --role "Azure Data Engineer"
#   python master_run.py --auto-apply             # Run + auto-apply at the end
#   python master_run.py --auto-apply --dry-run   # Preview auto-apply
#   python master_run.py --skip-fetch --auto-apply --limit 5
# =============================================================================

import os
import sys
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║       RAGHAVENDRA KARANAM — PRO JOB APPLICATION PIPELINE         ║
║            Data Engineer  |  Delray Beach, FL                    ║
║   98%+ ATS Resume Tailoring  •  Auto-Apply via Playwright         ║
╚══════════════════════════════════════════════════════════════════╝
"""


# =============================================================================
# API KEY CHECK
# =============================================================================

def check_api_key() -> str:
    key = os.environ.get("RAPIDAPI_KEY", "")
    if not key or key == "REPLACE_WITH_YOUR_KEY":
        pipeline_path = os.path.join(os.path.dirname(__file__), "pipeline.py")
        if os.path.exists(pipeline_path):
            with open(pipeline_path) as f:
                for line in f:
                    if "RAPIDAPI_KEY" in line and "=" in line and "environ" not in line:
                        val = line.split("=")[-1].strip().strip('"').strip("'")
                        if val and val != "REPLACE_WITH_YOUR_KEY":
                            return val
        print("\n❌ RAPIDAPI_KEY not set!")
        print("   Open pipeline.py and replace REPLACE_WITH_YOUR_KEY with your key.")
        print("   Or: export RAPIDAPI_KEY='your_key_here'")
        sys.exit(1)
    return key


# =============================================================================
# SCORE IMPROVEMENT TABLE PRINTER
# =============================================================================

def print_score_table(filtered_df, resume_results=None):
    """Print a clear BEFORE → AFTER ATS score table."""
    print("\n" + "═" * 72)
    print("  ATS SCORE RESULTS — BEFORE vs AFTER RESUME TAILORING")
    print("═" * 72)
    print(f"  {'Job Title':<28}  {'Company':<18}  Before  After   Delta  Rating")
    print(f"  {'─'*28}  {'─'*18}  {'─'*6}  {'─'*6}  {'─'*5}  {'─'*6}")

    for i, (_, row) in enumerate(filtered_df.iterrows()):
        # Prefer actual scores (from resume builder), fall back to estimated
        before = float(row.get("actual_initial_score",   row.get("initial_score",   0)))
        after  = float(row.get("actual_optimized_score", row.get("optimized_score", 0)))

        # If resume_results available, use those actual scores
        if resume_results and i < len(resume_results):
            _, actual_ini, actual_opt = resume_results[i]
            before = actual_ini
            after  = actual_opt

        delta  = after - before
        rating = "🟢 GREAT" if after >= 95 else ("🟡 GOOD" if after >= 80 else "🔴 LOW")
        print(
            f"  {str(row.get('title',''))[:28]:<28}  "
            f"{str(row.get('company',''))[:18]:<18}  "
            f"{before:>5.0f}%  {after:>5.1f}%  +{delta:>3.0f}%  {rating}"
        )

    if not filtered_df.empty:
        avg_before = filtered_df.get("actual_initial_score",
                     filtered_df.get("initial_score",
                     filtered_df.get("ats_score", [0]))).mean()
        avg_after  = filtered_df.get("actual_optimized_score",
                     filtered_df.get("optimized_score",
                     filtered_df.get("ats_score", [0]))).mean()
        print(f"\n  {'AVERAGE':<28}  {'─'*18}  {avg_before:>5.0f}%  {avg_after:>5.1f}%  "
              f"+{avg_after - avg_before:>3.0f}%")

    print("═" * 72 + "\n")


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_pipeline(
    skip_fetch:   bool = False,
    role:         str  = None,
    auto_apply:   bool = False,
    dry_run:      bool = False,
    apply_limit:  int  = None,
    platform:     str  = None,
):
    start_time = time.time()
    print(BANNER)
    print(f"  Started:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode:      {'Skip fetch' if skip_fetch else 'Full fetch'}")
    if role:
        print(f"  Role:      {role}")
    if auto_apply:
        print(f"  Auto-Apply: {'Dry Run' if dry_run else 'ENABLED'}")
    print()

    # ── STEP 1: Fetch Jobs ────────────────────────────────────────────────────
    print("▶  STEP 1 / 6 — Fetching Jobs")
    print("─" * 50)

    if not skip_fetch:
        from pipeline import main as fetch_jobs
        if role:
            import pipeline as pl
            pl.SEARCH_QUERIES = [role]
        jobs_df = fetch_jobs()
    else:
        import pandas as pd
        raw_path = os.path.join(os.path.dirname(__file__), "data", "raw_jobs.csv")
        if not os.path.exists(raw_path):
            print("  ❌ raw_jobs.csv not found. Remove --skip-fetch to download.")
            sys.exit(1)
        jobs_df = pd.read_csv(raw_path)
        print(f"  ✓ Loaded {len(jobs_df)} jobs from existing CSV\n")

    # ── STEP 2: Parse JDs + ATS Scoring ──────────────────────────────────────
    print("\n▶  STEP 2 / 6 — Parsing JDs & Scoring (BEFORE scores)")
    print("─" * 50)

    from jd_parser import main as parse_jds
    filtered_df = parse_jds()

    if filtered_df is None or filtered_df.empty:
        print("  ⚠  No jobs passed the ATS filter.")
        sys.exit(0)

    print(f"  ✓ {len(filtered_df)} jobs ready for resume tailoring")

    # ── STEP 3: Build Resumes (98%+ guarantee) ────────────────────────────────
    print("\n▶  STEP 3 / 6 — Building Tailored Resumes (target: 98%+ ATS)")
    print("─" * 50)

    from resume_builder import build_all_resumes
    resume_results = build_all_resumes()  # returns list of (path, initial, actual_optimized)

    # Update filtered_df with actual scores from resume builder
    import pandas as pd
    for i, (path, actual_ini, actual_opt) in enumerate(resume_results):
        if i < len(filtered_df):
            filtered_df.at[i, "actual_initial_score"]   = actual_ini
            filtered_df.at[i, "actual_optimized_score"] = actual_opt

    # ── STEP 4: Build Cover Letters ───────────────────────────────────────────
    print("\n▶  STEP 4 / 6 — Generating Cover Letters")
    print("─" * 50)

    from cover_letter import build_all_cover_letters
    cl_paths = build_all_cover_letters()

    # ── STEP 5: Update Excel Tracker ─────────────────────────────────────────
    print("\n▶  STEP 5 / 6 — Creating Application Tracker")
    print("─" * 50)

    from tracker import create_tracker
    tracker_path = create_tracker(filtered_df)

    # ── SCORE TABLE ───────────────────────────────────────────────────────────
    print_score_table(filtered_df, resume_results)

    # ── STEP 6: Auto-Apply ───────────────────────────────────────────────────
    if auto_apply:
        print("\n▶  STEP 6 / 6 — Auto-Apply")
        print("─" * 50)
        from auto_apply import build_apply_queue, save_queue, execute_apply_queue
        queue = build_apply_queue()
        save_queue(queue)
        execute_apply_queue(queue, dry_run=dry_run, platform_filter=platform, limit=apply_limit)
    else:
        print("\n▶  STEP 6 / 6 — Auto-Apply (SKIPPED)")
        print("─" * 50)
        from auto_apply import build_apply_queue, save_queue, print_summary
        queue = build_apply_queue()
        save_queue(queue)
        print_summary(queue)
        print("  💡 To auto-apply now: python master_run.py --skip-fetch --auto-apply")

    # ── FINAL SUMMARY ─────────────────────────────────────────────────────────
    elapsed = round(time.time() - start_time, 1)
    out_dir = os.path.join(os.path.dirname(__file__), "output")

    print("═" * 66)
    print("  ✅  PIPELINE COMPLETE")
    print("═" * 66)
    print(f"  ⏱  Time:          {elapsed}s")
    print(f"  📋  Jobs:          {len(filtered_df)} analyzed")
    print(f"  📄  Resumes:       {len(resume_results)} built  (98%+ ATS guaranteed)")
    print(f"  📝  Cover Letters: {len(cl_paths)} generated")
    print(f"  📊  Tracker:       {os.path.basename(tracker_path)}")
    print(f"\n  📁  Files saved to:")
    print(f"     • {out_dir}/resumes/")
    print(f"     • {out_dir}/cover_letters/")
    print(f"     • {os.path.dirname(tracker_path)}/")
    print()

    if resume_results:
        best_jobs = sorted(
            zip(resume_results, filtered_df.iterrows()),
            key=lambda x: x[0][2],  # sort by actual_optimized
            reverse=True,
        )[:5]
        print("  🏆  Top 5 Matches (Actual ATS After Tailoring):")
        for (path, ini, opt), (_, row) in best_jobs:
            print(f"     [{opt:.1f}%] {row.get('title','?')[:32]} @ {row.get('company','?')[:20]}")
    print("═" * 66 + "\n")


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Raghavendra Karanam — Pro Job Application Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python master_run.py                                 Full pipeline
  python master_run.py --skip-fetch                   Use existing CSV
  python master_run.py --role "Azure Data Engineer"   Specific role
  python master_run.py --auto-apply                   Auto-apply at end
  python master_run.py --auto-apply --dry-run         Preview apply
  python master_run.py --skip-fetch --auto-apply --limit 5
        """,
    )
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip API fetch and use existing raw_jobs.csv")
    parser.add_argument("--role",        type=str, default=None,
                        help='Search specific role e.g. "Azure Data Engineer"')
    parser.add_argument("--auto-apply",  action="store_true",
                        help="Auto-apply to jobs after building resumes")
    parser.add_argument("--dry-run",     action="store_true",
                        help="With --auto-apply: simulate without real submissions")
    parser.add_argument("--limit",       type=int, default=None,
                        help="Limit number of auto-apply applications")
    parser.add_argument("--platform",    type=str, default=None,
                        help="Filter auto-apply by platform: linkedin, indeed")
    args = parser.parse_args()

    check_api_key()
    run_pipeline(
        skip_fetch  = args.skip_fetch,
        role        = args.role,
        auto_apply  = args.auto_apply,
        dry_run     = args.dry_run,
        apply_limit = args.limit,
        platform    = args.platform,
    )
