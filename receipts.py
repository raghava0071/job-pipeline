#!/usr/bin/env python3
# =============================================================================
# RECEIPTS.PY — shared, platform-agnostic per-application audit log
#
# ROADMAP.md task #3: "Build a receipts log per attempted application
# (fields filled, resume version, timestamp, success/fail) before adding
# more ATS handlers — it's what will tell you why something broke instead
# of guessing." Built now, before Lever/Ashby, so those handlers get
# receipts from day one instead of each one inventing its own log shape —
# any handler calls write_receipt() the same way.
#
# Deliberately ADDITIVE, not a replacement: greenhouse_apply_now.py's own
# greenhouse_applied_log.json (already_applied() dedup) and
# submitted_applications.json (live-submit-only detailed audit) keep doing
# exactly what they already do. This is the one cross-platform "what
# happened and why" trail — every platform, every attempt, dry-run
# included — that a human (or the next Claude session) can read to answer
# "why did DoorDash keep failing" without reconstructing it from print
# statements and screenshots.
#
# Never raises — a logging failure must never take down the apply run that
# triggered it, same convention every other log-writer in this codebase
# already follows (load/append/save wrapped in try/except, print a warning
# on failure instead of crashing).
#
# SCHEMA — one dict per entry in the JSON array in data/receipts.json:
#   receipt_id            "<platform>_<epoch_ms>" — unique, sortable by time
#   timestamp              ISO8601
#   platform               "Greenhouse" / "Lever" / "Ashby" / "Workday" / ...
#   company / title / url
#   resume_version          filename, "" if none was built
#   cover_letter_version    filename, "" if none was built
#   dry_run                 bool
#   fields                  {label: value} — every field actually resolved
#   fields_total            fields_filled + fields_skipped
#   fields_filled           len(fields)
#   fields_skipped          required fields with no truthful answer available
#   outcome                 "submitted" | "dry_run_ok" | "failed" |
#                            "skipped_incomplete" | "unverified"
#   reason                  human-readable, same string the handler already
#                            prints/logs elsewhere
#   seconds_to_complete     float, None if not timed by the caller
# =============================================================================

import json, time
from pathlib import Path
from datetime import datetime

import config as cfg

RECEIPTS_FILE = cfg.BASE_DIR / "data" / "receipts.json"


def _outcome(record: dict, success: bool, dry_run: bool) -> str:
    """Single source of truth for the outcome label — every handler's own
    ad-hoc `status` string (Greenhouse's "Unverified"/"Skipped-Incomplete"/
    "Dry-Run"/"Applied"/"Failed") maps onto this same small vocabulary so
    receipts stay comparable across platforms."""
    record = record or {}
    if record.get("unverified"):
        return "unverified"
    if record.get("skipped_incomplete") or record.get("account_required"):
        return "skipped_incomplete"
    if dry_run:
        return "dry_run_ok" if success else "skipped_incomplete"
    return "submitted" if success else "failed"


def write_receipt(platform: str, record: dict, success: bool, dry_run: bool,
                   resume_path: str = "", cover_letter_path: str = "",
                   seconds_to_complete: float = None) -> None:
    """Append one receipt for one attempted application. `record` is
    whatever dict the calling handler already builds for its own purposes
    (greenhouse_apply_now.py's `record`, or the equivalent once Lever/Ashby
    exist) — this function only reads from it, never requires the caller to
    restructure anything it already has. Safe to call with `record=None` or
    a partial dict (e.g. an exception fired before the caller finished
    building it) — every field below defaults sanely rather than raising."""
    try:
        record = record or {}
        fields = record.get("fields", {}) or {}
        unresolved = record.get("unresolved_required", []) or []
        seconds = round(seconds_to_complete, 1) if seconds_to_complete is not None else None
        receipt = {
            "receipt_id": f"{platform.lower()}_{int(time.time() * 1000)}",
            "timestamp": record.get("timestamp") or datetime.now().isoformat(),
            "platform": platform,
            "company": record.get("company", ""),
            "title": record.get("title", ""),
            "url": record.get("url", ""),
            "resume_version": Path(resume_path).name if resume_path else "",
            "cover_letter_version": Path(cover_letter_path).name if cover_letter_path else "",
            "dry_run": dry_run,
            "fields": fields,
            "fields_total": len(fields) + len(unresolved),
            "fields_filled": len(fields),
            "fields_skipped": len(unresolved),
            "outcome": _outcome(record, success, dry_run),
            "reason": record.get("reason", ""),
            "seconds_to_complete": seconds,
        }
        RECEIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(RECEIPTS_FILE.read_text()) if RECEIPTS_FILE.exists() else []
        existing.append(receipt)
        RECEIPTS_FILE.write_text(json.dumps(existing, indent=2))
        timing = f" ({seconds}s)" if seconds is not None else ""
        print(f"          🧾 Receipt: {receipt['fields_filled']} of {receipt['fields_total']} field(s) · "
              f"{receipt['fields_skipped']} skipped · {receipt['outcome']}{timing}")
    except Exception as e:
        print(f"          ⚠  Could not write receipt: {e}")


if __name__ == "__main__":
    # Quick self-test — no network/browser needed, just exercises the shape.
    write_receipt("Greenhouse", {
        "timestamp": datetime.now().isoformat(), "company": "TestCo", "title": "Test Role",
        "url": "https://boards.greenhouse.io/testco/jobs/123", "fields": {"first_name": "Raghavendra"},
        "unresolved_required": [], "reason": "confirmed after submit click",
    }, success=True, dry_run=False, resume_path="resume_v6.docx", seconds_to_complete=47.3)
    print(f"Wrote to {RECEIPTS_FILE}")
