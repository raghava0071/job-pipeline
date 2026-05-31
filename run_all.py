#!/usr/bin/env python3
# =============================================================================
# RUN_ALL.PY — Run LinkedIn + Indeed pipelines simultaneously
#
# Launches both platforms in parallel using Python multiprocessing.
# Each platform runs its own browser window independently.
# Combined summary printed at the end.
#
# USAGE:
#   python run_all.py                        # default: LinkedIn=10, Indeed=5
#   python run_all.py --li-limit 15          # override LinkedIn limit
#   python run_all.py --in-limit 8           # override Indeed limit
#   python run_all.py --dry-run              # dry run on both
#   python run_all.py --linkedin-only        # LinkedIn only
#   python run_all.py --indeed-only          # Indeed only
# =============================================================================

import sys, argparse, time, multiprocessing as mp
from pathlib import Path
from datetime import datetime

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))


def run_linkedin(limit, dry_run, result_queue):
    """Run LinkedIn pipeline in a subprocess."""
    try:
        import sys
        sys.path.insert(0, str(PIPELINE_DIR))
        import importlib.util, os

        # Load and run linkedin_apply_now main()
        spec = importlib.util.spec_from_file_location(
            "linkedin_apply_now",
            str(PIPELINE_DIR / "linkedin_apply_now.py")
        )
        mod = importlib.util.module_from_spec(spec)

        # Patch sys.argv so argparse inside the module sees our args
        old_argv = sys.argv[:]
        sys.argv = ["linkedin_apply_now.py", "--limit", str(limit)]
        if dry_run:
            sys.argv.append("--dry-run")

        try:
            spec.loader.exec_module(mod)
            mod.main()
            result_queue.put(("linkedin", "success"))
        except SystemExit:
            result_queue.put(("linkedin", "success"))
        finally:
            sys.argv = old_argv

    except Exception as e:
        result_queue.put(("linkedin", f"error: {e}"))


def run_indeed(limit, dry_run, result_queue):
    """Run Indeed pipeline in a subprocess."""
    try:
        import sys
        sys.path.insert(0, str(PIPELINE_DIR))
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "indeed_apply_now",
            str(PIPELINE_DIR / "indeed_apply_now.py")
        )
        mod = importlib.util.module_from_spec(spec)

        old_argv = sys.argv[:]
        sys.argv = ["indeed_apply_now.py", "--limit", str(limit)]
        if dry_run:
            sys.argv.append("--dry-run")

        try:
            spec.loader.exec_module(mod)
            mod.main()
            result_queue.put(("indeed", "success"))
        except SystemExit:
            result_queue.put(("indeed", "success"))
        finally:
            sys.argv = old_argv

    except Exception as e:
        result_queue.put(("indeed", f"error: {e}"))


def main():
    parser = argparse.ArgumentParser(description="Run LinkedIn + Indeed pipelines in parallel")
    parser.add_argument("--li-limit",      type=int, default=10,  help="LinkedIn max applies (default 10)")
    parser.add_argument("--in-limit",      type=int, default=5,   help="Indeed max applies (default 5)")
    parser.add_argument("--dry-run",       action="store_true",   help="Dry run on both platforms")
    parser.add_argument("--linkedin-only", action="store_true",   help="Run LinkedIn only")
    parser.add_argument("--indeed-only",   action="store_true",   help="Run Indeed only")
    args = parser.parse_args()

    now = datetime.now().strftime("%b %d, %Y at %I:%M %p")

    print(f"\n{'='*65}")
    print(f"  🚀 Job Pipeline — Full Run  {'[DRY RUN]' if args.dry_run else ''}")
    print(f"  {now}")
    print(f"  LinkedIn: up to {args.li_limit} jobs  |  Indeed: up to {args.in_limit} jobs")
    print(f"{'='*65}\n")

    # If only one platform requested, run directly (no multiprocessing overhead)
    if args.linkedin_only:
        print("  Running LinkedIn only...\n")
        q = mp.Queue()
        run_linkedin(args.li_limit, args.dry_run, q)
        return

    if args.indeed_only:
        print("  Running Indeed only...\n")
        q = mp.Queue()
        run_indeed(args.in_limit, args.dry_run, q)
        return

    # ── Run both in parallel ───────────────────────────────────────────────────
    result_queue = mp.Queue()

    print("  Starting both platforms simultaneously...")
    print("  (Two browser windows will open — one per platform)\n")

    li_proc = mp.Process(
        target=run_linkedin,
        args=(args.li_limit, args.dry_run, result_queue),
        name="LinkedIn"
    )
    in_proc = mp.Process(
        target=run_indeed,
        args=(args.in_limit, args.dry_run, result_queue),
        name="Indeed"
    )

    start = time.time()
    li_proc.start()
    time.sleep(5)   # Stagger startup so both browsers don't fight for login at once
    in_proc.start()

    # Wait for both to finish
    li_proc.join()
    in_proc.join()
    elapsed = int(time.time() - start)

    # Collect results
    results = {}
    while not result_queue.empty():
        platform, status = result_queue.get()
        results[platform] = status

    mins = elapsed // 60
    secs = elapsed % 60

    print(f"\n{'='*65}")
    print(f"  ✅ Both pipelines finished in {mins}m {secs}s")
    print(f"  LinkedIn: {results.get('linkedin', 'unknown')}")
    print(f"  Indeed:   {results.get('indeed', 'unknown')}")
    print(f"\n  Check your email for per-job notifications.")
    print(f"  Logs: ~/job_pipeline/data/applied_log.json (LinkedIn)")
    print(f"        ~/job_pipeline/data/indeed_applied_log.json (Indeed)")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    # Required for multiprocessing on macOS
    mp.set_start_method("spawn", force=True)
    main()
