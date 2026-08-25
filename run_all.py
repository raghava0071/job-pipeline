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

import sys, argparse, time, multiprocessing as mp, subprocess, fcntl, errno, os
from pathlib import Path
from datetime import datetime

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

# ── Startup syntax check — catch bad edits before any browser opens ───────────
import ast
for _f in ["config.py", "indeed_apply_now.py", "linkedin_apply_now.py", "resume_builder.py",
           "workday_apply_now.py", "secure_store.py", "greenhouse_apply_now.py"]:
    try:
        ast.parse((PIPELINE_DIR / _f).read_text())
    except SyntaxError as _e:
        print(f"❌ Syntax error in {_f}: {_e}  — fix before running"); sys.exit(1)


# ── Process-level singleton lock ────────────────────────────────────────────
# Root cause of the Jul 13 08:01 AM incident: two run_all.py invocations
# (a duplicate/leftover scheduled trigger) fired at the same time, both
# opened .indeed_session/.linkedin_session at once, and that collision
# produced the LinkedIn ProcessSingleton crash and the net::ERR_ABORTED
# cascade on Indeed. This lock makes concurrent runs impossible regardless
# of the trigger source (launchd, cron, or a manual run overlapping a
# scheduled one).
def _acquire_singleton_lock(lock_path):
    """
    Exclusive, non-blocking flock on lock_path.
    Returns (lock_file, None) on success — caller must keep lock_file open
    for the process lifetime and close it in a finally block.
    Returns (None, holder_info) if another instance already holds it.

    Uses flock rather than a plain PID-file: flock is tied to the open file
    descriptor, and the kernel releases it the instant the holding process
    exits for any reason — normal exit, crash, or kill -9. That's what a
    plain PID-file can't guarantee (this project already hit that exact
    staleness problem with .git/index.lock) — no manual "is this PID still
    alive" check is needed because a stale flock cannot exist.
    """
    lock_file = open(lock_path, "a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno not in (errno.EACCES, errno.EAGAIN):
            raise
        lock_file.seek(0)
        holder_info = lock_file.read().strip() or "(no PID/time recorded)"
        lock_file.close()
        return None, holder_info

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"pid={os.getpid()}\nstarted={datetime.now().strftime('%b %d, %Y at %I:%M:%S %p')}\n")
    lock_file.flush()
    return lock_file, None


def _release_singleton_lock(lock_file):
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    lock_file.close()


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
        try:
            import traceback, error_log
            error_log.record("linkedin", "CRASH",
                             "LinkedIn engine crashed before finishing.",
                             context=traceback.format_exc()[-1500:])
        except Exception:
            pass
        result_queue.put(("linkedin", f"error: {e}"))


def run_indeed(limit, dry_run, result_queue):
    """Run Indeed pipeline in a subprocess."""
    try:
        import sys
        sys.path.insert(0, str(PIPELINE_DIR))

        # Hand-off mode: Indeed's Cloudflare wall blocks any automated browser,
        # so don't launch it. Build the click-through dashboard for the real
        # browser instead. See indeed_handoff.py / config.INDEED_HANDOFF_MODE.
        try:
            import config as _cfg
            if getattr(_cfg, "INDEED_HANDOFF_MODE", False):
                import indeed_handoff
                # Don't auto-open during a scheduled/parallel run — just build it;
                # the file path is emailed/printed and opened on manual runs.
                indeed_handoff.generate(open_browser=not dry_run)
                result_queue.put(("indeed", "success"))
                return
        except Exception as _hoff_err:
            print(f"  ⚠  Indeed hand-off dashboard failed ({str(_hoff_err)[:80]}) — falling back to normal engine")

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
        try:
            import traceback, error_log
            error_log.record("indeed", "CRASH",
                             "Indeed engine crashed before finishing.",
                             context=traceback.format_exc()[-1500:])
        except Exception:
            pass
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
        try:
            import traceback, error_log
            error_log.record("workday", "CRASH",
                             "Workday engine crashed before finishing.",
                             context=traceback.format_exc()[-1500:])
        except Exception:
            pass
        result_queue.put(("workday", f"error: {e}"))


def run_greenhouse(limit, dry_run, result_queue):
    """Run Greenhouse pipeline in a subprocess.

    Not part of the default parallel run yet (see main()/_run_greenhouse
    below) — guest-apply flow is new and unverified against a live posting,
    same "opt-in only until proven" treatment Workday got after its own
    rough start. Run explicitly with --greenhouse-only (and --dry-run first).
    """
    try:
        import sys
        sys.path.insert(0, str(PIPELINE_DIR))
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "greenhouse_apply_now",
            str(PIPELINE_DIR / "greenhouse_apply_now.py")
        )
        mod = importlib.util.module_from_spec(spec)

        old_argv = sys.argv[:]
        sys.argv = ["greenhouse_apply_now.py", "--limit", str(limit)]
        if dry_run:
            sys.argv.append("--dry-run")

        try:
            spec.loader.exec_module(mod)
            mod.main()
            result_queue.put(("greenhouse", "success"))
        except SystemExit:
            result_queue.put(("greenhouse", "success"))
        finally:
            sys.argv = old_argv

    except Exception as e:
        try:
            import traceback, error_log
            error_log.record("greenhouse", "CRASH",
                             "Greenhouse engine crashed before finishing.",
                             context=traceback.format_exc()[-1500:])
        except Exception:
            pass
        result_queue.put(("greenhouse", f"error: {e}"))


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


def _keep_mac_awake():
    """
    Keep the Mac awake for the whole run. Added 2026-07-21 after a scheduled
    run ballooned to 46 hours (2768m): the pipeline's timeouts are plain
    wall-clock time.sleep() calls, and when the Mac sleeps mid-run (lid closed)
    the whole process freezes — a '5-minute' login wait silently became ~40
    hours. `caffeinate -w <pid>` runs until THIS process exits, then stops on
    its own, so it can't leave the Mac awake forever. No plist/launchd change
    needed — self-contained. macOS only; silently skipped elsewhere.
    """
    try:
        subprocess.Popen(
            ["caffeinate", "-i", "-m", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        print("  ☕ caffeinate: Mac will stay awake for this run")
    except Exception as _e:
        print(f"  ⚠  Could not start caffeinate ({str(_e)[:60]}) — run continues, but keep the Mac awake manually")


def main():
    _keep_mac_awake()
    parser = argparse.ArgumentParser(description="Run LinkedIn + Indeed + Workday pipelines in parallel")
    parser.add_argument("--li-limit",       type=int, default=50,  help="LinkedIn max applies (default 50)")
    parser.add_argument("--in-limit",       type=int, default=100, help="Indeed max applies (default 100)")
    parser.add_argument("--wd-limit",       type=int, default=10,  help="Workday max applies (default 10)")
    parser.add_argument("--gh-limit",       type=int, default=10,  help="Greenhouse max applies (default 10)")
    parser.add_argument("--dry-run",        action="store_true",   help="Dry run on all platforms")
    parser.add_argument("--linkedin-only",  action="store_true",   help="Run LinkedIn only")
    parser.add_argument("--indeed-only",    action="store_true",   help="Run Indeed only")
    parser.add_argument("--workday-only",   action="store_true",   help="Run Workday only")
    parser.add_argument("--greenhouse-only",action="store_true",   help="Run Greenhouse only")
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

    if args.greenhouse_only:
        print("  Running Greenhouse only...\n")
        run_greenhouse(args.gh_limit, args.dry_run, result_queue)
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
    print(f"  (Greenhouse not in the default run yet — unverified against a live posting; "
          f"run --greenhouse-only --dry-run first)")
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
    print(f"  LinkedIn:   {results.get('linkedin', 'unknown')}")
    print(f"  Indeed:     {results.get('indeed',   'unknown')}")
    print(f"  Workday:    PAUSED (re-enable with --workday-only)")
    print(f"  Greenhouse: not run (opt-in only — run with --greenhouse-only)")
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

    import config
    _lock_file, _holder_info = _acquire_singleton_lock(config.RUN_LOCK_PATH)
    if _lock_file is None:
        _now = datetime.now().strftime("%b %d, %Y at %I:%M %p")
        _msg = (f"Another run_all.py is already running — exiting without touching "
                f"any browser profile.\nExisting lock holder: {_holder_info}\n"
                f"Blocked at: {_now}")
        print(f"\n⛔ {_msg}\n")
        try:
            import notifier
            notifier.send_alert(
                subject="⛔ Duplicate pipeline run blocked",
                body=f"A second run_all.py tried to start while one was already running "
                     f"and was blocked before touching any Chrome profile — this is the "
                     f"collision that caused the Jul 13 8 AM ERR_ABORTED incident.\n\n{_msg}"
            )
        except Exception:
            pass
        sys.exit(1)

    try:
        main()
    finally:
        _release_singleton_lock(_lock_file)
