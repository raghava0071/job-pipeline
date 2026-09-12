#!/usr/bin/env python3
"""
build_question_bank.py — Raghav's requested "what questions are we getting"
report. Reads every receipt in data/receipts.json (every application attempt,
answered and skipped alike), deduplicates by question label, and writes
data/question_bank.json: one entry per DISTINCT question ever seen, across
every company/application, with:
  - how many times it's been seen
  - which companies asked it
  - whether the pipeline currently has a real answer for it, and what that
    answer is
  - if NOT currently answered: the skip reason, and an empty "your_answer"
    field Raghav can fill in by hand

This does not change how the pipeline answers anything — it's a read-only
report for review. Once Raghav fills in "your_answer" for a question here,
that value still needs to be wired into qa_answers.py / raghav_profile.py
for the pipeline to actually use it — this file is the review step, not the
wiring step.

Usage: python3 build_question_bank.py
"""
import json
from pathlib import Path
from collections import OrderedDict

BASE_DIR = Path(__file__).parent
RECEIPTS_FILE = BASE_DIR / "data" / "receipts.json"
OUT_FILE = BASE_DIR / "data" / "question_bank.json"


def _load_receipts():
    if not RECEIPTS_FILE.exists():
        return []
    data = json.loads(RECEIPTS_FILE.read_text())
    if isinstance(data, list):
        return data
    return data.get("receipts", []) if isinstance(data, dict) else []


def main():
    receipts = _load_receipts()
    bank = OrderedDict()  # label -> entry

    for r in receipts:
        company = r.get("company", "")
        title = r.get("title", "")
        ts = r.get("timestamp", "")
        reason = r.get("reason", "") or ""
        fields = r.get("fields", {}) or {}

        for label, answer in fields.items():
            entry = bank.setdefault(label, {
                "label": label,
                "times_seen": 0,
                "companies": [],
                "example_answer": None,
                "currently_answered": False,
                "your_answer": "",
            })
            entry["times_seen"] += 1
            if company and company not in entry["companies"]:
                entry["companies"].append(company)
            has_real_answer = bool(str(answer).strip())
            if has_real_answer and not entry["currently_answered"]:
                entry["currently_answered"] = True
                entry["example_answer"] = answer
            entry["last_seen"] = ts

        # A skipped/unresolved question sometimes only shows up in the
        # `reason` string (e.g. "required field(s) with no truthful answer
        # available: X; Y"), not as a zero-value entry in `fields` — catch
        # those too so nothing genuinely unanswered gets missed here.
        if "no truthful answer available:" in reason:
            unresolved_part = reason.split("no truthful answer available:", 1)[1]
            for lbl in [p.strip() for p in unresolved_part.split(";") if p.strip()]:
                entry = bank.setdefault(lbl, {
                    "label": lbl,
                    "times_seen": 0,
                    "companies": [],
                    "example_answer": None,
                    "currently_answered": False,
                    "your_answer": "",
                })
                entry["times_seen"] += 1
                if company and company not in entry["companies"]:
                    entry["companies"].append(company)
                entry["last_seen"] = ts

        # Added 2026-09-11 (Raghav's explicit request — "save all kinds of
        # questions it sees... from these we can improve"): `all_questions_seen`
        # (v2.14.19+) is EVERY question the page had, answered or not — this
        # is what closes the real gap above: a non-required question that got
        # skipped (an essay, a no-decline-option EEO field, an uncached
        # short_text field) never showed up in `fields` OR in the "no
        # truthful answer available" reason string, only in
        # data/stuck_questions.json. Older receipts (pre-2.14.19) won't have
        # this key — falls back to [] so this stays additive, not breaking.
        for q in (r.get("all_questions_seen", []) or []):
            lbl = q.get("label", "")
            if not lbl:
                continue
            entry = bank.setdefault(lbl, {
                "label": lbl,
                "times_seen": 0,
                "companies": [],
                "example_answer": None,
                "currently_answered": False,
                "your_answer": "",
            })
            entry["times_seen"] += 1
            if company and company not in entry["companies"]:
                entry["companies"].append(company)
            entry["last_seen"] = ts
            # The real answer value (if any) is picked up by the `fields`
            # loop above, which runs first — this loop only exists to make
            # sure a SKIPPED question that isn't required (so it never hit
            # the "no truthful answer available" reason string either) still
            # gets an entry at all, instead of being invisible to this report.

    # Split into answered vs needs-your-input for easy review, most-common first
    needs_input = sorted(
        [e for e in bank.values() if not e["currently_answered"]],
        key=lambda e: -e["times_seen"]
    )
    already_answered = sorted(
        [e for e in bank.values() if e["currently_answered"]],
        key=lambda e: -e["times_seen"]
    )

    out = {
        "generated_from": str(RECEIPTS_FILE),
        "total_distinct_questions": len(bank),
        "needs_your_input_count": len(needs_input),
        "already_answered_count": len(already_answered),
        "needs_your_input": needs_input,
        "already_answered": already_answered,
    }
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT_FILE}")
    print(f"  {len(bank)} distinct questions seen across {len(receipts)} application attempts")
    print(f"  {len(needs_input)} need your input (fill in \"your_answer\" in the JSON)")
    print(f"  {len(already_answered)} already have a working answer")
    if needs_input:
        print("\n  Top questions needing your input:")
        for e in needs_input[:10]:
            print(f"    [{e['times_seen']}x] {e['label'][:100]}")


if __name__ == "__main__":
    main()
