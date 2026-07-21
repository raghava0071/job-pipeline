#!/usr/bin/env python3
# =============================================================================
# ERROR_LOG.PY — One single, human-readable error log for the whole pipeline
#
# WHY THIS EXISTS:
#   The per-run JSON logs (pipeline_logger.py) are great for machine analysis
#   but scattered across hundreds of files, one per run per platform. When
#   something goes wrong — especially the Indeed "Are you a robot?" / Cloudflare
#   wall that has blocked applications for weeks — there was no single place to
#   look and see, in plain English, exactly what failed and when.
#
#   This module writes every error to ONE growing text file:
#       data/pipeline_errors.log
#   Each entry is a clear, timestamped block you can just open and read.
#
# USAGE (from anywhere in the pipeline):
#       import error_log
#       error_log.record(
#           platform="indeed",
#           kind="ROBOT_CHECK",          # short category, UPPER_SNAKE
#           detail="Cloudflare 'Are you a robot?' checkbox — page did not pass",
#           context="Query: Data Analyst | url=https://indeed.com/...",
#           url="https://www.indeed.com/...",
#       )
#
#   Never raises — logging an error must never itself crash the pipeline.
#
# READ IT:
#       python error_log.py            → prints the whole error log
#       python error_log.py 20         → prints the last 20 entries
#       tail -f data/pipeline_errors.log
# =============================================================================

from datetime import datetime
from pathlib import Path

# Resolve the log path from config if available, else fall back to the standard
# location. Kept dependency-light on purpose so this module can be imported from
# anywhere (including inside except-blocks) without risk of a circular import.
try:
    import config as _cfg
    _LOG_PATH = Path(getattr(_cfg, "ERROR_LOG_PATH",
                             Path.home() / "job_pipeline" / "data" / "pipeline_errors.log"))
except Exception:
    _LOG_PATH = Path.home() / "job_pipeline" / "data" / "pipeline_errors.log"


def _short_version() -> str:
    try:
        import config as _cfg
        return str(getattr(_cfg, "PIPELINE_VERSION", "?"))
    except Exception:
        return "?"


def record(platform: str, kind: str, detail: str,
           context: str = "", url: str = "") -> None:
    """
    Append one clear, timestamped error block to data/pipeline_errors.log.

    platform : "indeed" | "linkedin" | "workday" | "pipeline"
    kind     : short UPPER_SNAKE category, e.g. ROBOT_CHECK, CAPTCHA,
               LOGIN_TIMEOUT, BROWSER_CLOSED, SESSION_BLOCKED, CRASH
    detail   : one-line plain-English description of what went wrong
    context  : optional extra info (query, counts, exception text, etc.)
    url      : optional page URL where it happened

    Guaranteed never to raise — a logging failure must not break a run.
    """
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "",
            "=" * 70,
            f"[{ts}]  {platform.upper()}  —  {kind}",
            "-" * 70,
            f"  {detail}".rstrip(),
        ]
        if url:
            lines.append(f"  URL     : {url}")
        if context:
            # Indent multi-line context so the block stays readable.
            for i, cline in enumerate(str(context).splitlines() or [str(context)]):
                label = "  Context : " if i == 0 else "            "
                lines.append(f"{label}{cline}")
        lines.append(f"  Pipeline: v{_short_version()}")
        block = "\n".join(lines) + "\n"

        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(block)
    except Exception:
        # Deliberately swallow — logging must never crash the caller.
        pass


def read_tail(n: int = 0) -> str:
    """Return the whole log, or just the last n entries if n > 0."""
    try:
        text = _LOG_PATH.read_text(encoding="utf-8")
    except Exception:
        return f"(no error log yet at {_LOG_PATH})"
    if n <= 0:
        return text
    # Entries are separated by the 70-char '=' rule.
    blocks = text.split("=" * 70)
    kept = ("=" * 70).join(blocks[-n:])
    return kept.lstrip("\n")


if __name__ == "__main__":
    import sys
    _n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    print(read_tail(_n))
