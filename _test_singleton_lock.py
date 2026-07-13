"""
Throwaway verification script — NOT part of the pipeline, kept on disk per
Raghav's established precedent (same as _test_pin_diagnostics.py and
_test_captcha_identity.py: not deleted after running).

Verifies the v1.9.0 process-level singleton lock added to run_all.py's
entrypoint, added after the Jul 13 08:01 AM incident where two concurrent
run_all.py invocations (duplicate scheduled trigger) both opened the same
Chrome profile directories at once and produced a ProcessSingleton crash +
net::ERR_ABORTED cascade.

Checks:
  1. First acquire on a fresh lock path succeeds.
  2. A second, independent acquire attempt on the SAME path (simulating a
     second concurrent run_all.py) is correctly blocked and reports the
     first holder's PID + start time (no manual PID-liveness check needed --
     flock is fd-scoped, not file-content-scoped).
  3. After releasing the first lock, a new acquire attempt succeeds again.
  4. Simulates a crash: kill -9 an actual child process holding the lock,
     confirm the OS releases the flock automatically (no stale lock),
     proving the "crash doesn't leave a stale lock" requirement without
     needing manual PID-alive checks.
"""
import sys, os, signal, time, tempfile, types
sys.path.insert(0, ".")

# Load run_all.py's source directly and patch PIPELINE_DIR to the real cwd
# instead of Path.home() / "job_pipeline" -- this test may run in a sandbox
# where $HOME doesn't point at the actual repo checkout, unrelated to the
# lock logic under test. __main__ block is guarded (if __name__==...), so
# this exec never touches a real browser profile or acquires the real lock.
_src = open("run_all.py").read()
_src = _src.replace(
    'PIPELINE_DIR = Path.home() / "job_pipeline"',
    f'PIPELINE_DIR = Path({os.getcwd()!r})'
)
run_all = types.ModuleType("run_all")
run_all.__file__ = "run_all.py"
exec(compile(_src, "run_all.py", "exec"), run_all.__dict__)

LOCK_PATH = tempfile.mktemp(suffix="_test_run_all.lock")

# 1. First acquire succeeds
lock1, holder1 = run_all._acquire_singleton_lock(LOCK_PATH)
assert lock1 is not None and holder1 is None, f"expected first acquire to succeed, got {lock1!r} {holder1!r}"
print("[1/4] first acquire succeeds — OK")

# 2. Second acquire (same process, independent fd) is blocked, reports holder info
lock2, holder2 = run_all._acquire_singleton_lock(LOCK_PATH)
assert lock2 is None, "expected second concurrent acquire to be blocked"
assert "pid=" in holder2 and "started=" in holder2, f"expected holder info with pid/started, got: {holder2!r}"
print(f"[2/4] second acquire correctly blocked, holder info reported: {holder2!r} — OK")

# 3. Release, then re-acquire succeeds
run_all._release_singleton_lock(lock1)
lock3, holder3 = run_all._acquire_singleton_lock(LOCK_PATH)
assert lock3 is not None and holder3 is None, "expected acquire to succeed again after release"
print("[3/4] re-acquire after release succeeds — OK")
run_all._release_singleton_lock(lock3)

# 4. Crash simulation: fork a child that acquires the lock then gets kill -9'd;
#    confirm the parent can acquire immediately after, proving no stale lock.
CRASH_LOCK_PATH = tempfile.mktemp(suffix="_test_crash.lock")
pid = os.fork()
if pid == 0:
    # child: acquire and hold forever (until killed)
    lf, _ = run_all._acquire_singleton_lock(CRASH_LOCK_PATH)
    assert lf is not None
    time.sleep(30)
    os._exit(0)
else:
    time.sleep(0.5)  # let child acquire
    _blocked_lock, _blocked_holder = run_all._acquire_singleton_lock(CRASH_LOCK_PATH)
    assert _blocked_lock is None, "expected lock to be held by child before kill"
    os.kill(pid, signal.SIGKILL)  # hard crash, no finally/cleanup runs
    os.waitpid(pid, 0)
    time.sleep(0.2)
    lock4, holder4 = run_all._acquire_singleton_lock(CRASH_LOCK_PATH)
    assert lock4 is not None, f"expected lock to be free immediately after kill -9, got holder: {holder4!r}"
    print("[4/4] lock auto-released after kill -9 of holder (no stale lock, no manual PID check needed) — OK")
    run_all._release_singleton_lock(lock4)

print("\nALL CHECKS PASSED")
