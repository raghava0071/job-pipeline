#!/usr/bin/env python3
# =============================================================================
# PIPELINE_LOGGER.PY — Structured run logging for future analysis
#
# Every run writes a JSON log to data/runs/run_YYYYMMDD_HHMMSS_<platform>.json
# Each log contains: platform, start/end time, every job attempted, outcomes,
# cache stats, error details — everything needed to improve the pipeline.
#
# USAGE:
#   from pipeline_logger import RunLogger
#   log = RunLogger("linkedin")
#   log.job_start(title, company, url)
#   log.job_result("Applied", fit_score=82, steps=4)
#   log.finish(cache_hits=120, cache_misses=10)
# =============================================================================

import json
import time
from datetime import datetime
from pathlib import Path

BASE_DIR  = Path.home() / "job_pipeline"
RUNS_DIR  = BASE_DIR / "data" / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


class RunLogger:
    """
    One instance per pipeline run (one platform).
    Thread-safe within a single process (multiprocessing safe because
    each platform runs in its own process with its own logger instance).
    """

    def __init__(self, platform: str):
        self.platform   = platform.lower()
        self.start_ts   = datetime.utcnow().isoformat() + "Z"
        self.start_time = time.time()
        self.jobs       = []          # list of job result dicts
        self._current   = {}          # job being processed right now
        self.meta       = {}          # cache stats, search stats, etc.
        self.errors     = []          # platform-level errors

        stamp     = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = RUNS_DIR / f"run_{stamp}_{self.platform}.json"
        print(f"  📋 Run log: {self.path.name}")

    # ── Job lifecycle ──────────────────────────────────────────────────────────

    def job_start(self, title: str, company: str, url: str = "",
                  fit_score: float = 0, grade: str = "?"):
        """Call when you begin processing a job."""
        self._current = {
            "title":      title,
            "company":    company,
            "url":        url,
            "fit_score":  fit_score,
            "grade":      grade,
            "status":     "in_progress",
            "reason":     "",
            "steps":      0,
            "fields_filled": 0,
            "cache_hits": 0,
            "claude_calls": 0,
            "resume_file": "",
            "ats_score":  0,
            "started_at": datetime.utcnow().isoformat() + "Z",
            "elapsed_s":  0,
        }

    def job_result(self, status: str, reason: str = "", steps: int = 0,
                   fields_filled: int = 0, cache_hits: int = 0,
                   claude_calls: int = 0, resume_file: str = "", ats_score: float = 0):
        """Call when a job finishes (Applied, Failed, Skipped, etc.)."""
        if not self._current:
            return
        self._current.update({
            "status":        status,
            "reason":        reason,
            "steps":         steps,
            "fields_filled": fields_filled,
            "cache_hits":    cache_hits,
            "claude_calls":  claude_calls,
            "resume_file":   resume_file,
            "ats_score":     ats_score,
            "elapsed_s":     round(time.time() - self.start_time, 1),
        })
        self.jobs.append(dict(self._current))
        self._current = {}
        self._save()   # save after each job so data survives crashes

    def job_skip(self, title: str, company: str, reason: str,
                 fit_score: float = 0, url: str = ""):
        """Shortcut for jobs that are skipped immediately (senior filter, below gate, etc.)."""
        self.jobs.append({
            "title":      title,
            "company":    company,
            "url":        url,
            "fit_score":  fit_score,
            "grade":      "",
            "status":     "Skipped",
            "reason":     reason,
            "steps":      0,
            "fields_filled": 0,
            "cache_hits": 0,
            "claude_calls": 0,
            "resume_file": "",
            "ats_score":  0,
            "started_at": datetime.utcnow().isoformat() + "Z",
            "elapsed_s":  0,
        })

    # ── Platform-level events ──────────────────────────────────────────────────

    def log_error(self, error: str, context: str = ""):
        """Log a platform-level error (login failure, browser crash, etc.)."""
        self.errors.append({
            "error":   error,
            "context": context,
            "ts":      datetime.utcnow().isoformat() + "Z",
        })
        self._save()

    def finish(self, cache_hits: int = 0, cache_misses: int = 0,
               cache_total: int = 0, searches_run: int = 0,
               jobs_found: int = 0):
        """Call at the very end of the run."""
        self.meta = {
            "cache_hits":    cache_hits,
            "cache_misses":  cache_misses,
            "cache_total":   cache_total,
            "cache_hit_rate": round(cache_hits / max(cache_hits + cache_misses, 1) * 100, 1),
            "searches_run":  searches_run,
            "jobs_found":    jobs_found,
        }
        self._save()
        self._print_summary()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _payload(self) -> dict:
        elapsed = round(time.time() - self.start_time, 1)
        counts  = {}
        for j in self.jobs:
            s = j.get("status", "Unknown")
            counts[s] = counts.get(s, 0) + 1

        return {
            "platform":   self.platform,
            "start":      self.start_ts,
            "end":        datetime.utcnow().isoformat() + "Z",
            "elapsed_s":  elapsed,
            "summary":    counts,
            "applied":    counts.get("Applied", 0),
            "failed":     counts.get("Failed", 0),
            "skipped":    counts.get("Skipped", 0),
            "total_jobs": len(self.jobs),
            "meta":       self.meta,
            "errors":     self.errors,
            "jobs":       self.jobs,
        }

    def _save(self):
        try:
            self.path.write_text(json.dumps(self._payload(), indent=2))
        except Exception as e:
            print(f"  ⚠  Logger save error: {e}")

    def _print_summary(self):
        p = self._payload()
        mins = int(p["elapsed_s"]) // 60
        secs = int(p["elapsed_s"]) % 60
        print(f"\n  {'─'*50}")
        print(f"  📊 Run summary — {self.platform.upper()}")
        print(f"  {'─'*50}")
        print(f"  ✅ Applied  : {p['applied']}")
        print(f"  ❌ Failed   : {p['failed']}")
        print(f"  ⏭  Skipped  : {p['skipped']}")
        print(f"  🔎 Total    : {p['total_jobs']} jobs processed")
        if self.meta:
            hr = self.meta.get('cache_hit_rate', 0)
            print(f"  📦 Cache    : {hr}% hit rate")
        print(f"  ⏱  Duration : {mins}m {secs}s")
        print(f"  💾 Log      : {self.path.name}")
        print(f"  {'─'*50}\n")


# ── Utility: load and analyze all past runs ───────────────────────────────────

def load_all_runs(platform: str = None) -> list:
    """
    Load all run logs. Filter by platform if given.
    Returns list of run dicts sorted by start time (newest first).
    """
    runs = []
    for f in RUNS_DIR.glob("run_*.json"):
        try:
            data = json.loads(f.read_text())
            if platform and data.get("platform") != platform.lower():
                continue
            runs.append(data)
        except Exception:
            pass
    return sorted(runs, key=lambda r: r.get("start", ""), reverse=True)


def print_analysis(days: int = 7):
    """
    Print a multi-day analysis of all pipeline runs.
    Useful for spotting trends: which queries work best, failure patterns, etc.
    """
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    runs   = [r for r in load_all_runs() if r.get("start", "") >= cutoff]

    if not runs:
        print(f"No run logs found in the last {days} days.")
        return

    total_applied  = sum(r.get("applied", 0) for r in runs)
    total_failed   = sum(r.get("failed", 0) for r in runs)
    total_skipped  = sum(r.get("skipped", 0) for r in runs)
    total_runs     = len(runs)

    print(f"\n{'='*55}")
    print(f"  Pipeline Analysis — last {days} days  ({total_runs} runs)")
    print(f"{'='*55}")
    print(f"  Total applied : {total_applied}")
    print(f"  Total failed  : {total_failed}")
    print(f"  Total skipped : {total_skipped}")

    if total_runs > 0:
        print(f"  Avg applied/run: {total_applied / total_runs:.1f}")

    # Failure reason breakdown
    reasons = {}
    for r in runs:
        for j in r.get("jobs", []):
            if j.get("status") == "Failed":
                reason = j.get("reason", "unknown")[:50]
                reasons[reason] = reasons.get(reason, 0) + 1
    if reasons:
        print(f"\n  Top failure reasons:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1])[:5]:
            print(f"    {count:3d}x  {reason}")

    # Skip reason breakdown
    skip_reasons = {}
    for r in runs:
        for j in r.get("jobs", []):
            if j.get("status") == "Skipped":
                reason = j.get("reason", "unknown")[:50]
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
    if skip_reasons:
        print(f"\n  Top skip reasons:")
        for reason, count in sorted(skip_reasons.items(), key=lambda x: -x[1])[:5]:
            print(f"    {count:3d}x  {reason}")

    print(f"{'='*55}\n")


if __name__ == "__main__":
    print_analysis(days=7)
