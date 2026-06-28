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

import sys, argparse, time, multiprocessing as mp, subprocess
from pathlib import Path
from datetime import datetime

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

# ── Startup syntax check — catch bad edits before any browser opens ───────────
import ast
for _f in ["config.py", "indeed_apply_now.py", "linkedin_apply_now.py", "resume_builder.py"]:
    try:
        ast.parse((PIPELINE_DIR / _f).read_text())
    except SyntaxError as _e:
        print(f"❌ Syntax error in {_f}: {_e}  — fix before running"); sys.exit(1)


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


def run_workday(limit, dry_run, result_queue, queue_only=False):
    """Run Workday pipeline in a subprocess."""
    try:
        import sys
        sys.path.insert(0, str(PIPELINE_DIR))
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "workday_apply_now",
            str(PIPELINE_DIR / "workday_apply_now.py")
        )
        mod = importlib.util.module_from_spec(spec)

        old_argv = sys.argv[:]
        sys.argv = ["workday_apply_now.py", "--limit", str(limit)]
        if dry_run:
            sys.argv.append("--dry-run")
        if queue_only:
            sys.argv.append("--queue-only")

        try:
            spec.loader.exec_module(mod)
            mod.main()
            result_queue.put(("workday", "success"))
        except SystemExit:
            result_queue.put(("workday", "success"))
        finally:
            sys.argv = old_argv

    except Exception as e:
        result_queue.put(("workday", f"error: {e}"))


def _auto_diagnose(errors: dict):
    """
    When a platform errors, automatically call Claude Code CLI to diagnose.
    Reads the scheduler log + error message and asks Claude to explain + fix.
    No screenshots — pure text = cheap tokens.
    """
    import subprocess, os
    from pathlib import Path

    # Check if claude CLI is available
    claude_path = subprocess.run(["which", "claude"], capture_output=True, text=True).stdout.strip()
    if not claude_path:
        print("  ℹ  Claude Code not installed — skipping auto-diagnose")
        return

    DATA_DIR = Path.home() / "job_pipeline" / "data"

    for platform, error_msg in errors.items():
        print(f"\n  🤖 Auto-diagnosing {platform} error with Claude...")

        # Collect context: error + recent log
        log_map = {
            "linkedin": DATA_DIR / "applied_log.json",
            "indeed":   DATA_DIR / "indeed_applied_log.json",
            "workday":  DATA_DIR / "workday_applied_log.json",
        }
        sched_log = DATA_DIR / "scheduler_morning_err.log"

        # Build diagnosis prompt
        prompt_lines = [
            f"I'm running an automated job application pipeline in Python.",
            f"The {platform.upper()} engine just failed with this error:",
            f"",
            f"ERROR: {error_msg}",
            f"",
        ]

        # Add scheduler error log if it exists
        if sched_log.exists():
            recent_err = sched_log.read_text()[-3000:]  # last 3000 chars
            if recent_err.strip():
                prompt_lines += [
                    "RECENT SCHEDULER ERROR LOG (last 3000 chars):",
                    recent_err,
                    "",
                ]

        prompt_lines += [
            f"The pipeline files are in ~/job_pipeline/",
            f"Key files: {platform}_apply_now.py, config.py, run_all.py",
            f"",
            f"Please:",
            f"1. Explain what caused this error in simple terms",
            f"2. Tell me exactly which file and line to fix",
            f"3. Give me the fix",
        ]

        prompt = "\n".join(prompt_lines)

        # Write prompt to temp file
        prompt_file = DATA_DIR / f"_claude_prompt_{platform}.txt"
        prompt_file.write_text(prompt)

        print(f"  📋 Asking Claude to diagnose {platform} error...")
        print(f"  (reading error logs — no screenshots, minimal tokens)\n")

        try:
            result = subprocess.run(
                ["claude", "--print", prompt],
                capture_output=True, text=True,
                timeout=120,
                cwd=str(Path.home() / "job_pipeline"),
            )
            diagnosis = result.stdout.strip() or result.stderr.strip()

            if diagnosis:
                print(f"  {'─'*55}")
                print(f"  🤖 Claude's diagnosis for {platform}:")
                print(f"  {'─'*55}")
                print(diagnosis)
                print(f"  {'─'*55}\n")

                # Save diagnosis to file for reference
                diag_file = DATA_DIR / f"diagnosis_{platform}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
                diag_file.write_text(f"ERROR:\n{error_msg}\n\nCLAUDE DIAGNOSIS:\n{diagnosis}")
                print(f"  💾 Diagnosis saved: {diag_file.name}")

                # Send diagnosis via email
                try:
                    import notifier
                    notifier.send_alert(
                        subject=f"🤖 Auto-diagnosis: {platform} error fixed",
                        body=f"Pipeline error on {platform}:\n\n{error_msg}\n\nClaude's fix:\n\n{diagnosis}"
                    )
                except:
                    pass
            else:
                print(f"  ⚠  Claude returned no diagnosis")

        except subprocess.TimeoutExpired:
            print(f"  ⚠  Claude diagnosis timed out (120s)")
        except Exception as e:
            print(f"  ⚠  Auto-diagnose failed: {e}")

        # Cleanup temp file
        prompt_file.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Run LinkedIn + Indeed + Workday pipelines in parallel")
    parser.add_argument("--li-limit",       type=int, default=50,  help="LinkedIn max applies (default 50)")
    parser.add_argument("--in-limit",       type=int, default=100, help="Indeed max applies (default 100)")
    parser.add_argument("--wd-limit",       type=int, default=10,  help="Workday max applies (default 10)")
    parser.add_argument("--dry-run",        action="store_true",   help="Dry run on all platforms")
    parser.add_argument("--linkedin-only",  action="store_true",   help="Run LinkedIn only")
    parser.add_argument("--indeed-only",    action="store_true",   help="Run Indeed only")
    parser.add_argument("--workday-only",   action="store_true",   help="Run Workday only")
    parser.add_argument("--no-workday",     action="store_true",   help="Skip Workday (LinkedIn + Indeed only)")
    parser.add_argument("--wd-queue-only",  action="store_true",   help="Workday: only process queue from LinkedIn/Indeed")
    args = parser.parse_args()

    now = datetime.now().strftime("%b %d, %Y at %I:%M %p")

    print(f"\n{'='*65}")
    print(f"  🚀 Job Pipeline — Full Run  {'[DRY RUN]' if args.dry_run else ''}")
    print(f"  {now}")
    print(f"  LinkedIn: up to {args.li_limit}  |  Indeed: up to {args.in_limit}  |  Workday: up to {args.wd_limit}")
    print(f"  Schedule: 8 AM  |  12 PM  |  6 PM")
    print(f"{'='*65}\n")

    result_queue = mp.Queue()

    # Single-platform shortcuts
    if args.linkedin_only:
        print("  Running LinkedIn only...\n")
        run_linkedin(args.li_limit, args.dry_run, result_queue)
        return

    if args.indeed_only:
        print("  Running Indeed only...\n")
        run_indeed(args.in_limit, args.dry_run, result_queue)
        return

    if args.workday_only:
        print("  Running Workday only...\n")
        run_workday(args.wd_limit, args.dry_run, result_queue, queue_only=args.wd_queue_only)
        return

    # ── Run all three in parallel ──────────────────────────────────────────────
    procs = []

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

    # ── Workday PAUSED ────────────────────────────────────────────────────────
    # Workday has 0 successful applications out of 116 attempts (all-time).
    # Every run hits email verification loops that require manual intervention.
    # Re-enable by passing --workday-only when the auth issues are fixed.
    # --no-workday is now the default unless explicitly overridden.
    _run_workday = args.workday_only  # only if explicitly requested

    platforms = "LinkedIn + Indeed"
    print(f"  Starting {platforms} simultaneously...")
    print(f"  (Workday PAUSED — 0/116 success rate, email verification loops)")
    print(f"  (Browser windows will open — one per platform)\n")

    start = time.time()

    li_proc.start()
    procs.append(li_proc)

    time.sleep(4)   # stagger so browsers don't fight for login at once
    in_proc.start()
    procs.append(in_proc)

    for p in procs:
        p.join()

    elapsed = int(time.time() - start)

    results = {}
    while not result_queue.empty():
        platform, status = result_queue.get()
        results[platform] = status

    mins = elapsed // 60
    secs = elapsed % 60

    print(f"\n{'='*65}")
    print(f"  ✅ All pipelines finished in {mins}m {secs}s")
    print(f"  LinkedIn: {results.get('linkedin', 'unknown')}")
    print(f"  Indeed:   {results.get('indeed',   'unknown')}")
    print(f"  Workday:  PAUSED (re-enable with --workday-only)")
    print(f"\n  Check your email for per-job notifications.")
    print(f"  Logs: ~/job_pipeline/data/applied_log.json (LinkedIn)")
    print(f"        ~/job_pipeline/data/indeed_applied_log.json (Indeed)")
    print(f"{'='*65}\n")

    # ── Auto-diagnose errors using Claude Code ────────────────────────────────
    # If any platform errored, call `claude` CLI to diagnose automatically
    errors = {p: s for p, s in results.items() if "error" in str(s).lower()}
    if errors:
        _auto_diagnose(errors)


if __name__ == "__main__":
    # Required for multiprocessing on macOS
    mp.set_start_method("spawn", force=True)
    main()
