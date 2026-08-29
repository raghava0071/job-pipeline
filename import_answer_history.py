#!/usr/bin/env python3
# =============================================================================
# IMPORT_ANSWER_HISTORY.PY — Pre-populate the answer bank from real history
#
# Run this in a real terminal on your machine:
#   python3 import_answer_history.py                 generate the candidate
#                                                      review report, write
#                                                      nothing to the bank
#   python3 import_answer_history.py --confirm        walk through each
#                                                      candidate one at a
#                                                      time and decide
#
# WHERE THE HISTORY COMES FROM (see CHANGELOG for the full count breakdown)
#   - claude_answers.py (CLAUDE_QA dict)  — every answer this pipeline has
#     ever actually typed into a real Indeed/LinkedIn/Greenhouse/Workday
#     form field and kept, across every past run. ~1,100+ entries.
#   - data/answer_cache.db (answer_cache table) — the SQLite auto-cache of
#     the same kind of thing, ~1,800 rows; its keys are a more heavily
#     normalized (punctuation-stripped, truncated) version of the question,
#     so text pulled from here is sometimes less readable than CLAUDE_QA.
#   NOT usable for this: the applied-application logs (indeed_applied_log.json
#   etc.) — those record job title/company/status/timestamp, not the
#   individual form answers given, so there's nothing to import from them.
#
# CRITICAL GUARD (rule from Raghav, do not weaken):
#   Nothing from history is trusted silently. Every candidate below is
#   surfaced in data/answer_bank_import_review.json AND printed here for
#   Raghav to actually look at before any of it reaches the answer bank.
#   Only --confirm (interactive, one question at a time) can write to the
#   bank, and only after Raghav accepts or corrects each one individually —
#   there is no bulk "accept everything" shortcut, deliberately.
#
# WHAT COUNTS AS "CLEAR FACTUAL" (rule 6 — only these get imported):
#   work authorization / sponsorship / citizenship, years of experience,
#   current/prior employer & job title, education, relocation/commute/
#   onsite/remote willingness, notice period / start date, salary/rate
#   expectations. See _FACTUAL_SIGNALS below.
#   Everything else — essays, subjective self-assessments, anything that
#   doesn't match one of those categories — is left out entirely, for
#   review_stuck_questions.py (PART 1) to handle if it ever recurs as a
#   real stuck question, not guessed at here from old data.
#   EEO/self-identification and consent/acknowledgement are excluded too,
#   same as PART 1 (answer_bank.is_eeo_or_consent) — they stay per-form.
# =============================================================================

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path.home() / "job_pipeline"
REVIEW_FILE = BASE_DIR / "data" / "answer_bank_import_review.json"
CACHE_DB = BASE_DIR / "data" / "answer_cache.db"

sys.path.insert(0, str(BASE_DIR))
import answer_bank as _ab

# ── "Clear factual" categories (rule 6) ──────────────────────────────────────
_FACTUAL_SIGNALS = (
    # work authorization / sponsorship / citizenship
    "authorized to work", "work authorization", "legally authorized",
    "require sponsorship", "require visa", "visa sponsorship",
    "immigration sponsorship", "visa status", "work permit", "work on w2",
    "us citizen", "citizenship", "green card", "permanent resident",
    # years of experience
    "years of experience", "years experience", "how many years",
    "years of relevant", "years with", "year of experience",
    # current / prior employer
    "current employer", "current company", "most recent employer",
    "recent employer", "previous employer", "prior employer",
    "have you worked at", "previously employed at", "previously been employed",
    "current job title", "recent job title", "current position", "current role",
    # education
    "highest level of education", "education level", "degree type",
    "field of study", "graduation year", "year of graduation",
    "grade point average", "what is your gpa",
    # location / relocation / commute / onsite / remote
    "willing to relocate", "able to relocate", "open to relocation",
    "comfortable commuting", "able to commute", "willing to commute",
    "able to work onsite", "willing to work onsite", "comfortable working onsite",
    "open to remote", "comfortable working remotely",
    # notice / start date
    "notice period", "how soon can you start", "when can you start",
    "available to start", "earliest start date",
    # salary / rate
    "salary expectation", "desired salary", "expected salary",
    "desired rate", "expected rate", "hourly rate", "desired pay",
    "expected compensation", "desired compensation",
)

# Defense in depth — things that could slip past the factual-keyword filter
# but are obviously not real answers (resume filename selections, raw UI
# element ids, page-state values).
_JUNK_LABEL_SIGNALS = ("select resume", "resumeselection", ".docx", ".pdf", "current page")
_HEX32_RE = re.compile(r'^[0-9a-f]{32}$', re.I)
_EMBER_ID_RE = re.compile(r'ember\d+', re.I)


def _is_junk(label: str, answer: str) -> bool:
    l = label.lower()
    if _HEX32_RE.match(l.strip()):
        return True
    if any(s in l for s in _JUNK_LABEL_SIGNALS):
        return True
    if _EMBER_ID_RE.search(str(answer)):
        return True
    return False


def _is_factual(label: str) -> bool:
    l = label.lower()
    return any(s in l for s in _FACTUAL_SIGNALS)


def _looks_essay(answer: str) -> bool:
    """Rule 6 — skip anything ambiguous/essay-style even if the label
    matched a factual keyword. A real factual answer is short."""
    a = str(answer)
    return len(a) > 150 or "\n" in a


def load_claude_qa() -> list[tuple[str, str]]:
    try:
        import claude_answers as ca
        return list(ca.CLAUDE_QA.items())
    except Exception as e:
        print(f"  ⚠  Could not load claude_answers.py: {e}")
        return []


def load_answer_cache() -> list[tuple[str, str]]:
    if not CACHE_DB.exists():
        return []
    try:
        con = sqlite3.connect(str(CACHE_DB))
        rows = con.execute("SELECT question_key, answer FROM answer_cache").fetchall()
        con.close()
        return [(k, a) for k, a in rows]
    except Exception as e:
        print(f"  ⚠  Could not read {CACHE_DB}: {e}")
        return []


def build_candidates() -> list[dict]:
    """Merge both sources, filter to clear-factual-only, group by normalized
    question, and flag any question where the two sources disagree."""
    grouped = {}  # normalized_key -> {"labels": set, "answers": {value: set(sources)}}

    for source_name, rows in (("claude_answers.py", load_claude_qa()),
                               ("answer_cache.db", load_answer_cache())):
        for label, answer in rows:
            label = str(label or "").strip()
            answer = str(answer or "").strip()
            if not label or not answer:
                continue
            if _ab.is_hash_label(label) or _is_junk(label, answer):
                continue
            if _ab.is_eeo_or_consent(label):
                continue
            if not _is_factual(label):
                continue
            if _looks_essay(answer):
                continue
            # Already confirmed in the bank under this exact wording? Skip —
            # nothing to re-review.
            if _ab.get(label) is not None:
                continue

            key = _ab.normalize(label)
            if not key:
                continue
            g = grouped.setdefault(key, {"labels": set(), "answers": {}})
            g["labels"].add(label)
            g["answers"].setdefault(answer, set()).add(source_name)

    candidates = []
    for key, g in grouped.items():
        # Prefer the longest raw label seen (most likely to be the full,
        # readable original wording rather than a mangled cache key).
        best_label = max(g["labels"], key=len)
        answer_variants = [
            {"value": val, "sources": sorted(srcs)}
            for val, srcs in g["answers"].items()
        ]
        conflict = len(answer_variants) > 1
        candidates.append({
            "question": best_label,
            "normalized": key,
            "answers": answer_variants,
            "conflict": conflict,
        })

    candidates.sort(key=lambda c: (c["conflict"] is False, c["question"].lower()))
    # ^ conflicts first (need the most attention), then alphabetical
    candidates.sort(key=lambda c: not c["conflict"])
    return candidates


def write_review_file(candidates: list[dict]) -> None:
    REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_FILE.write_text(json.dumps(candidates, indent=2, ensure_ascii=False), encoding="utf-8")


def print_summary(candidates: list[dict]) -> None:
    conflicts = [c for c in candidates if c["conflict"]]
    clean = [c for c in candidates if not c["conflict"]]
    print(f"\n  📥 {len(candidates)} candidate answer(s) found in history "
          f"(claude_answers.py + answer_cache.db), filtered to clear-factual "
          f"questions only.")
    print(f"  ⚠️  {len(conflicts)} have conflicting past answers from "
          f"different sources — need your judgment call.")
    print(f"  ✅ {len(clean)} have one consistent past answer — still need "
          f"your confirmation, not auto-trusted.")
    print(f"\n  Full list written to: {REVIEW_FILE}\n")

    if conflicts:
        print("  ── Conflicts (review these first) ──")
        for c in conflicts[:15]:
            print(f"    {c['question']}")
            for v in c["answers"]:
                print(f"        → {v['value']!r}  ({', '.join(v['sources'])})")
        if len(conflicts) > 15:
            print(f"    ... and {len(conflicts) - 15} more in {REVIEW_FILE}")
        print()

    if clean:
        print("  ── Consistent past answers (sample) ──")
        for c in clean[:20]:
            v = c["answers"][0]
            print(f"    {c['question'][:70]:70s} → {v['value']!r}")
        if len(clean) > 20:
            print(f"    ... and {len(clean) - 20} more in {REVIEW_FILE}")
        print()


def confirm_interactively(candidates: list[dict]) -> None:
    print(f"\n  Reviewing {len(candidates)} candidate(s). For each: "
          f"Enter = accept the answer shown, type a correction, or 's' to skip.\n")
    accepted = 0
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}/{len(candidates)}] {c['question']}")
        if c["conflict"]:
            print("     Past sources disagree:")
            for j, v in enumerate(c["answers"], 1):
                print(f"       {j}. {v['value']!r}  ({', '.join(v['sources'])})")
            try:
                raw = input("     Type the correct answer, a number to pick one, "
                             "or 's' to skip: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Stopped.")
                break
            if not raw or raw.lower() == "s":
                print("     ⏭  Skipped.\n")
                continue
            if raw.isdigit() and 1 <= int(raw) <= len(c["answers"]):
                final = c["answers"][int(raw) - 1]["value"]
            else:
                final = raw
        else:
            v = c["answers"][0]
            try:
                raw = input(f"     Past answer: {v['value']!r} "
                             f"({', '.join(v['sources'])}) — Enter to accept, "
                             f"type to correct, 's' to skip: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Stopped.")
                break
            if raw.lower() == "s":
                print("     ⏭  Skipped.\n")
                continue
            final = raw if raw else v["value"]

        if _ab.save(c["question"], final, source="import_confirmed"):
            accepted += 1
            print("     ✅ Saved to answer bank.\n")
        else:
            print("     ⚠  Not saved (empty).\n")

    print(f"  Done — {accepted} answer(s) confirmed and saved to the bank.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                     help="Interactively confirm/correct each candidate and save accepted ones")
    args = ap.parse_args()

    candidates = build_candidates()
    write_review_file(candidates)
    print_summary(candidates)

    if not candidates:
        return

    if args.confirm:
        confirm_interactively(candidates)
    else:
        print(f"  Nothing was written to the answer bank. Review "
              f"{REVIEW_FILE}, then re-run with --confirm when ready to go "
              f"through them.\n")


if __name__ == "__main__":
    main()
