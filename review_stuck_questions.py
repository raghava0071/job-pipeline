#!/usr/bin/env python3
# =============================================================================
# REVIEW_STUCK_QUESTIONS.PY — Answer every unique stuck question once
#
# Run this in a real terminal on your machine:
#   python3 review_stuck_questions.py            interactive review
#   python3 review_stuck_questions.py --list      just list what's pending, no prompts
#
# WHAT IT DOES
#   1. Reads data/stuck_questions.json (every run so far, and every future
#      run — just re-run this any time new stuck questions pile up).
#   2. Pulls the individual field labels out of every entry that has them.
#      Older entries logged before field-level tracking existed only have a
#      whole-page text dump (no fields[] list) — there's no reliable way to
#      split free page text back into individual question+answer pairs
#      without guessing, so those are counted and reported but not shown as
#      reviewable questions. See the summary line this script prints.
#   3. Deduplicates by exact normalized question text.
#   4. Drops EEO/self-identification and consent/acknowledgement questions.
#      Per Raghav's explicit rule these stay per-form, answered fresh every
#      time — never bulk-answered from a central bank.
#   5. Drops anything already resolvable via the existing chain
#      (answer_bank -> qa_answers.py -> profile_answers.py) — no point
#      asking again for something the pipeline can already answer.
#   6. For everything left: shows the question + type + options + an
#      example job it came from, and asks for your answer.
#
# GUARDS
#   - Blank/Enter = skip. Nothing is saved unless you actually type an
#     answer — answer_bank.save() itself refuses blank answers too, so this
#     is enforced twice.
#   - This script never generates or suggests a Yes/No — it only records
#     what you type.
#   - EEO/consent questions are filtered out before you ever see them here
#     (see _EEO_SIGNALS / _CONSENT_SIGNALS below) — they are not part of
#     this review, by design.
# =============================================================================

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path.home() / "job_pipeline"
STUCK_FILE = BASE_DIR / "data" / "stuck_questions.json"

sys.path.insert(0, str(BASE_DIR))
import answer_bank as _ab
import qa_answers as _qa

# EEO/consent exclusion and hash-label detection live in answer_bank.py —
# shared with import_answer_history.py (PART 2) so both tools agree on what
# never enters the bank. See answer_bank.is_eeo_or_consent() / is_hash_label().


def load_stuck() -> list:
    if not STUCK_FILE.exists():
        return []
    try:
        return json.loads(STUCK_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠  Could not parse {STUCK_FILE}: {e}")
        return []


def collect_unique_questions(stuck_jobs: list) -> tuple[list, int, int, int]:
    """Returns (pending, legacy_count, hash_count, excluded_count).
    pending = list of {"label","type","options","example_job"} — unique,
    not EEO/consent, not a meaningless hash label, not already answerable
    by the existing chain."""
    seen_keys = set()
    pending = []
    legacy_count = 0
    hash_count = 0
    excluded_count = 0

    for job in stuck_jobs:
        fields = job.get("fields") or []
        if not fields:
            legacy_count += 1
            continue
        jt = job.get("job_title", "?")
        co = job.get("company", "?")
        for f in fields:
            label = (f.get("label") or "").strip()
            if not label:
                continue
            key = _ab.normalize(label)
            if not key or key in seen_keys:
                continue

            if _ab.is_hash_label(label):
                seen_keys.add(key)
                hash_count += 1
                continue

            excl = _ab.is_eeo_or_consent(label)
            if excl:
                seen_keys.add(key)  # don't re-show it, but don't offer it either
                excluded_count += 1
                continue

            # Already answerable via the real resolution chain? Don't ask again.
            if _ab.get(label) is not None or _qa.get_answer(label) is not None:
                seen_keys.add(key)
                continue

            seen_keys.add(key)
            pending.append({
                "label": label,
                "type": f.get("type", ""),
                "options": f.get("options", []),
                "example_job": f"{jt} @ {co}",
            })

    return pending, legacy_count, hash_count, excluded_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="List pending questions, don't prompt")
    args = ap.parse_args()

    stuck_jobs = load_stuck()
    if not stuck_jobs:
        print(f"  No stuck questions found at {STUCK_FILE}.")
        return

    pending, legacy_count, hash_count, excluded_count = collect_unique_questions(stuck_jobs)

    print(f"\n  📋 {len(stuck_jobs)} logged stuck-question entries total.")
    if legacy_count:
        print(f"  ⏭  {legacy_count} entry/entries predate field-level tracking "
              f"(whole-page text only) — can't be split into individual "
              f"questions safely, skipped.")
    if hash_count:
        print(f"  ⏭  {hash_count} unique field(s) had no real question text "
              f"(generated DOM id only) — skipped, nothing meaningful to ask.")
    if excluded_count:
        print(f"  ⏭  {excluded_count} unique field(s) are EEO/consent — stay "
              f"per-form, not part of this review.")
    print(f"  🏦 Answer bank already has {_ab.count()} confirmed answer(s).")
    print(f"  ❓ {len(pending)} unique question(s) need your review.\n")

    if not pending:
        print("  ✅ Nothing pending — every stuck question is either already "
              "answered or is EEO/consent (which stays per-form).\n")
        return

    if args.list:
        for i, item in enumerate(pending, 1):
            print(f"  [{i}] {item['label']}")
            if item["options"]:
                print(f"        options: {', '.join(str(o) for o in item['options'])}")
        print()
        return

    answered = 0
    for i, item in enumerate(pending, 1):
        print(f"  [{i}/{len(pending)}] From: {item['example_job']}")
        print(f"  Question : {item['label']}")
        if item["type"]:
            print(f"  Type     : {item['type']}")
        if item["options"]:
            print(f"  Options  : {', '.join(str(o) for o in item['options'])}")
        try:
            answer = input("  Your answer (Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Stopped.")
            break
        if answer:
            if _ab.save(item["label"], answer, source="user"):
                answered += 1
                print("  ✅ Saved to answer bank.\n")
            else:
                print("  ⚠  Not saved (empty after all).\n")
        else:
            print("  ⏭  Skipped.\n")

    print(f"  Done — {answered} new answer(s) saved. "
          f"The pipeline will use them automatically from now on.\n")


if __name__ == "__main__":
    main()
