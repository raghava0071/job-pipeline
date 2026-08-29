#!/usr/bin/env python3
# =============================================================================
# IMPORT_ANSWER_HISTORY.PY — Pre-populate the answer bank from real history
#
# Run this in a real terminal on your machine:
#   python3 import_answer_history.py              generate/refresh
#                                                   data/answer_bank_review.csv
#                                                   — nothing written to the
#                                                   bank yet
#   [edit the CSV: fix 'decision' to 'approve'/anything-else per row, or
#    edit 'proposed_answer' to change the value, or delete a row entirely]
#   python3 import_answer_history.py --apply-csv   import every row marked
#                                                   'approve' — the only
#                                                   thing that writes to
#                                                   the bank
#
#   python3 import_answer_history.py --confirm     legacy: one-at-a-time
#                                                   terminal review, no
#                                                   grouping — kept for
#                                                   small batches only
#
# GROUPING (added 2026-08-29 — 375 individually-normalized candidates was
# too many to review one at a time when most were just the same question
# phrased differently). Near-duplicate phrasings of the SAME real question
# ("are you authorized to work in the US" said nine ways) are grouped into
# ONE CSV row; approving that row writes the approved answer to every real
# historical phrasing folded into it (data/answer_bank_review_groups.json
# holds the full membership) — so the bank still auto-fills every wording
# actually seen, not just the one shown. Grouping is conservative: different
# "years of X experience" questions for different X stay separate rows
# (different, real, different-per-skill answers) — see _group_key().
# Whole GROUPS (not just exact phrasings) already answered in the bank
# under any wording are skipped and never shown again.
#
# SUSPICIOUS-ANSWER FLAGGING — flagged rows sort to the top of the CSV,
# pre-marked 'REVIEW' instead of 'approve':
#   - a years-of-experience answer that isn't a plain number, or is outside
#     ~3 years (2-4) — Raghav's real total experience, so anything further
#     off deserves a second look (still might be correct for an
#     off-domain skill like "sales" or "healthcare" — the flag just asks
#     you to confirm it, it doesn't reject it)
#   - a bare True/False/Yes/No answer to a question that isn't actually a
#     yes/no question (needs a real value — an employer name, a number, a
#     date, ...)
#   - conflicting answers across the phrasings folded into one group
#   - anything else too short/empty to be a real answer
# Clean (unflagged) rows are pre-marked 'approve' so a clean CSV edit really
# can be "fix the flagged rows, leave everything else, run --apply-csv" —
# per Raghav's explicit request for a bulk-approve path for the rows that
# are already clearly correct. This is still not silent: Raghav has to
# open the file, see what 'approve' is attached to, and choose to run the
# import command.
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
#   Nothing from history reaches the bank without Raghav explicitly marking
#   it approved in the CSV (or, in legacy --confirm mode, accepting it one
#   at a time in the terminal) and running an explicit import command.
#   apply_csv() is the ONLY function that writes to the bank from the CSV,
#   and only for rows whose 'decision' column says approve.
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
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path.home() / "job_pipeline"
REVIEW_FILE = BASE_DIR / "data" / "answer_bank_import_review.json"
CACHE_DB = BASE_DIR / "data" / "answer_cache.db"
GROUP_CSV = BASE_DIR / "data" / "answer_bank_review.csv"
GROUP_SIDECAR = BASE_DIR / "data" / "answer_bank_review_groups.json"

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


# =============================================================================
# GROUPING — collapse near-duplicate phrasings of the SAME real question into
# one review row (added 2026-08-29, per Raghav: 375 individually-normalized
# candidates was too many to review one at a time when most of them are just
# "are you authorized to work in the US" said nine different ways).
#
# _group_key() below is deliberately CONSERVATIVE — it only merges phrasings
# when the underlying fact is genuinely the same. It does NOT merge different
# "how many years of X experience" questions for different X (those have
# real, different, correct answers per skill) — it sub-groups by the
# extracted skill token instead, so "years of .NET" and "years of Azure"
# stay separate rows. It also keeps clearly-distinct-but-related concepts
# apart on purpose (citizen vs green card vs notice-period vs start-date) —
# see the comments on each rule below for why.
#
# IMPORTANT: this grouping is a REVIEW-TIME convenience only. The answer
# bank itself still matches on exact normalized text (answer_bank.py's
# deliberate design, unchanged) — approving a group writes the SAME approved
# answer to every real historical phrasing that was collapsed into it (via
# GROUP_SIDECAR), so the pipeline still auto-fills every wording it's
# actually seen, not just the one shown in the CSV.
# =============================================================================

_SKILL_YEARS_RE = [
    re.compile(r'years?\s+of\s+(.+?)\s+experience', re.I),
    re.compile(r'years?\s+experience\s+in\s+(.+?)(?:\s+do\s+you|\s*\?|$)', re.I),
    re.compile(r'years?\s+in\s+(.+?)\s+do\s+you\s+have', re.I),
    re.compile(r'experience\s+(?:with|in)\s+(.+?)(?:\s*\?|$)', re.I),
]


# Generic "how many years of experience" phrasings with no named skill —
# all the same real question (total years), grouped together as "general"
# rather than falling through to a singleton "other" group (which used to
# also trip the too-short-answer flag on a perfectly normal single-digit
# answer like "3").
_GENERIC_YEARS_RE = re.compile(
    r'\b(total|overall)?\s*years?\s+of\s+(relevant\s+|professional\s+|work(ing)?\s+)?'
    r'experience\b|^\s*years?\s+of\s+experience\s*$', re.I
)


def _extract_skill(label: str) -> str | None:
    """For a 'years of X experience' style question, pull out X (the skill/
    domain token) so different skills stay in different groups. Returns
    "general" for a bare years-of-experience question with no named skill,
    or None if this doesn't look like a years-of-experience question at all."""
    l = label.lower()
    if "year" not in l:
        return None
    for rx in _SKILL_YEARS_RE:
        m = rx.search(l)
        if m:
            skill = m.group(1)
            skill = re.sub(r'\([^)]*\)', '', skill)          # drop parentheticals
            skill = re.sub(r'\bdo\s+you\s+(currently\s+)?have\b', '', skill)
            skill = re.sub(r'[^\w\s./#+&-]', '', skill)        # strip stray punctuation
            skill = re.sub(r'\s+', ' ', skill).strip()
            if skill:
                return skill
    if _GENERIC_YEARS_RE.search(l):
        return "general"
    return None


def _extract_named_company(label: str) -> str | None:
    """For 'have you worked at X' style questions — X must stay its own
    group per company, never merged with a different company."""
    m = re.search(
        r'(?:worked at|employed by|employed at)\s+([a-z0-9 .&\'-]+?)'
        r'(?:\s+as\s|\s*[?,.]|$)', label.lower()
    )
    if m:
        return re.sub(r'\s+', ' ', m.group(1)).strip()
    return None


# Ordered (label_lower, group_id) keyword rules — first match wins. Kept
# deliberately granular: concepts that are related but not identical (e.g.
# "green card" and "us citizen") get their own group rather than being
# merged just because they often happen to share the same answer today.
_GROUP_RULES = (
    ("visa sponsorship", "sponsorship"),
    ("sponsorship", "sponsorship"),
    ("visa status", "visa_status"),
    ("work permit", "work_authorization"),
    ("work authorization", "work_authorization"),
    ("authorized to work", "work_authorization"),
    ("legally authorized", "work_authorization"),
    ("provide valid work authorizat", "work_authorization"),
    ("work on w2", "work_on_w2"),
    ("green card", "green_card_or_pr"),
    ("permanent resident", "green_card_or_pr"),
    ("us citizen", "citizenship"),
    ("citizenship", "citizenship"),
    ("highest level of education", "education_level"),
    ("education level", "education_level"),
    ("degree type", "education_level"),
    ("field of study", "education_field"),
    ("graduation year", "education_grad_year"),
    ("year of graduation", "education_grad_year"),
    ("grade point average", "education_gpa"),
    ("what is your gpa", "education_gpa"),
    ("relocat", "relocation"),
    ("commut", "commute"),
    ("onsite", "onsite"),
    ("on-site", "onsite"),
    ("on site", "onsite"),
    ("remote", "remote_work"),
    ("notice period", "notice_period"),
    ("how soon can you start", "start_date"),
    ("when can you start", "start_date"),
    ("available to start", "start_date"),
    ("when are you available to start", "start_date"),
    ("earliest start date", "start_date"),
    ("start date", "start_date"),
    ("current job title", "job_title"),
    ("recent job title", "job_title"),
    ("current position", "job_title"),
    ("current role", "job_title"),
    ("current employer", "current_employer"),
    ("current company", "current_employer"),
    ("recent employer", "current_employer"),
)

# Groups where a plain Yes/No/True/False IS a legitimate, complete answer —
# everything else flags a bare boolean as suspicious (rule 3/4: "any 'True'/
# 'No' answer to a question that needs a real value").
_BOOLEAN_OK_GROUPS = {
    "sponsorship", "work_authorization", "work_on_w2", "green_card_or_pr",
    "citizenship", "relocation", "commute", "onsite", "remote_work",
    "prior_employer_named",   # "have you worked at X?" is itself a yes/no question
}


def _group_key(label: str) -> tuple[str, str]:
    """Returns (group_id, group_kind) for a question label. group_kind is
    used later to decide which suspicious-answer checks apply."""
    l = label.lower()

    skill = _extract_skill(label)
    if skill:
        return f"years_experience::{skill}", "years_experience"

    company = _extract_named_company(label)
    if company:
        return f"worked_at::{company}", "prior_employer_named"

    if "hour" in l or "/hr" in l or "per hour" in l:
        if any(s in l for s in ("salary", "rate", "compensation", "pay", "wage")):
            return "salary_hourly", "salary_hourly"

    if any(s in l for s in ("salary", "compensation", "desired pay",
                             "rate you are looking for", "expected rate", "desired rate")):
        return "salary_annual", "salary_annual"

    for signal, gid in _GROUP_RULES:
        if signal in l:
            return gid, gid

    # Nothing matched — stays its own singleton group, same as before grouping existed.
    return f"single::{_ab.normalize(label)}", "other"


def _flag_candidate(group_kind: str, answer: str) -> str | None:
    """Rule 3/4 — suspicious-answer checks. Returns a flag reason or None."""
    a = str(answer).strip()

    if group_kind == "years_experience":
        m = re.match(r'^-?\d+(\.\d+)?$', a)
        if not m:
            return f"years answer {a!r} isn't a plain number"
        n = float(a)
        if not (2 <= n <= 4):
            return f"years answer '{a}' is far from your real ~3 years"
        return None

    if a.lower() in ("true", "false", "yes", "no") and group_kind not in _BOOLEAN_OK_GROUPS:
        return f"'{a}' is a bare yes/no but this question needs a real value"

    # A short answer is only suspicious if it isn't just a plain number —
    # single-digit answers ("3", "0") are completely normal for years/counts.
    if len(a) < 2 and not re.match(r'^-?\d+(\.\d+)?$', a):
        return f"answer '{a}' looks too short to be real"

    return None


def build_groups(candidates: list[dict]) -> list[dict]:
    """Buckets the already-factual-filtered candidates from build_candidates()
    into near-duplicate groups. Also drops any group where a phrasing that
    means the SAME thing is already in the answer bank under different
    wording — not just an exact-text match (build_candidates() already
    handles the exact-text case)."""

    # Which concepts has Raghav already answered, under ANY wording? Compute
    # the same group key for every bank entry's stored question text.
    already_answered_groups = set()
    for entry in _ab.all_entries().values():
        gid, _ = _group_key(entry.get("question", ""))
        if not gid.startswith("single::"):   # singleton groups can't "cover" anything else
            already_answered_groups.add(gid)

    buckets = {}  # group_id -> {"kind": str, "members": [candidate,...]}
    for c in candidates:
        gid, kind = _group_key(c["question"])
        if gid in already_answered_groups:
            continue
        b = buckets.setdefault(gid, {"kind": kind, "members": []})
        b["members"].append(c)

    groups = []
    for gid, b in buckets.items():
        members = b["members"]
        # One flat list of every distinct (value, source) seen across every
        # phrasing folded into this group.
        value_counts = Counter()
        member_rows = []
        for c in members:
            for v in c["answers"]:
                value_counts[v["value"]] += len(v["sources"])
            member_rows.append({
                "question": c["question"],
                "answers": c["answers"],
            })
        distinct_values = list(value_counts.keys())
        proposed = value_counts.most_common(1)[0][0] if value_counts else ""

        reasons = []
        if len(distinct_values) > 1:
            alt = "; ".join(f"{v!r}" for v in distinct_values if v != proposed)
            reasons.append(f"conflicting answers across phrasings: {alt}")
        flag = _flag_candidate(b["kind"], proposed)
        if flag:
            reasons.append(flag)

        display_label = max((m["question"] for m in members), key=len)
        groups.append({
            "group_id": gid,
            "question": display_label,
            "kind": b["kind"],
            "variant_count": len(members),
            "proposed_answer": proposed,
            "alternatives": [v for v in distinct_values if v != proposed],
            "flagged": bool(reasons),
            "flag_reason": "; ".join(reasons),
            "members": member_rows,
        })

    # Flagged first, then alphabetical — rule 3/4 ("show me the flagged ones first")
    groups.sort(key=lambda g: g["question"].lower())
    groups.sort(key=lambda g: not g["flagged"])
    return groups


def write_group_review(groups: list[dict]) -> None:
    """Writes the compact, editable CSV (what Raghav actually edits) plus a
    full-fidelity sidecar JSON (every real phrasing folded into each group —
    needed so approving one CSV row can write the bank entry for every
    wording, not just the one shown)."""
    GROUP_CSV.parent.mkdir(parents=True, exist_ok=True)

    with GROUP_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["group_id", "question", "variant_count", "proposed_answer",
                    "alternatives", "flagged", "flag_reason", "decision"])
        for g in groups:
            w.writerow([
                g["group_id"],
                g["question"],
                g["variant_count"],
                g["proposed_answer"],
                "; ".join(g["alternatives"]),
                "REVIEW" if g["flagged"] else "",
                g["flag_reason"],
                "REVIEW" if g["flagged"] else "approve",
            ])

    sidecar = {g["group_id"]: g["members"] for g in groups}
    GROUP_SIDECAR.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")


def print_group_summary(groups: list[dict]) -> None:
    flagged = [g for g in groups if g["flagged"]]
    clean = [g for g in groups if not g["flagged"]]
    total_phrasings = sum(g["variant_count"] for g in groups)
    print(f"\n  📥 {len(groups)} unique question(s) after grouping "
          f"({total_phrasings} historical phrasings collapsed into them).")
    print(f"  🚩 {len(flagged)} flagged for your attention — shown first in the CSV.")
    print(f"  ✅ {len(clean)} look clean — pre-marked 'approve', change any you disagree with.")
    print(f"\n  Edit {GROUP_CSV}")
    print(f"  (the 'decision' column: 'approve' imports proposed_answer as-is; "
          f"edit proposed_answer to change the value being imported; anything "
          f"else in 'decision', or deleting the row, means skip)")
    print(f"\n  Then run: python3 import_answer_history.py --apply-csv\n")

    if flagged:
        print("  ── Flagged — review these first ──")
        for g in flagged[:25]:
            print(f"    [{g['group_id']}] {g['question'][:65]}")
            print(f"        proposed: {g['proposed_answer']!r}  —  {g['flag_reason']}")
        if len(flagged) > 25:
            print(f"    ... and {len(flagged) - 25} more in {GROUP_CSV}")
        print()


def apply_csv() -> None:
    """Reads the (possibly hand-edited) CSV and writes every approved row's
    proposed_answer to every real historical phrasing folded into that
    group. This is the ONLY function in this file that can write to the
    bank from a CSV — and only for rows explicitly marked approved."""
    if not GROUP_CSV.exists() or not GROUP_SIDECAR.exists():
        print(f"  ⚠  No review CSV found — run python3 import_answer_history.py first.")
        return

    sidecar = json.loads(GROUP_SIDECAR.read_text(encoding="utf-8"))
    accepted_groups = 0
    accepted_phrasings = 0
    skipped = 0
    unknown = 0

    with GROUP_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            decision = (row.get("decision") or "").strip().lower()
            if decision not in ("approve", "y", "yes", "ok"):
                skipped += 1
                continue

            gid = row.get("group_id", "")
            answer = (row.get("proposed_answer") or "").strip()
            members = sidecar.get(gid)
            if members is None:
                print(f"  ⚠  Unknown group_id {gid!r} — skipping (was the CSV edited by hand?)")
                unknown += 1
                continue
            if not answer:
                print(f"  ⚠  {gid}: approved but proposed_answer is blank — skipping.")
                skipped += 1
                continue

            saved_any = False
            for m in members:
                if _ab.save(m["question"], answer, source="import_confirmed"):
                    accepted_phrasings += 1
                    saved_any = True
            if saved_any:
                accepted_groups += 1

    print(f"\n  ✅ Imported {accepted_groups} question(s) "
          f"({accepted_phrasings} historical phrasing(s) now covered in the bank).")
    if skipped:
        print(f"  ⏭  {skipped} row(s) not approved — left out.")
    if unknown:
        print(f"  ⚠  {unknown} row(s) had an unrecognized group_id — left out.")
    print()


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
    ap.add_argument("--apply-csv", action="store_true",
                     help="Import whatever's marked approved in data/answer_bank_review.csv")
    ap.add_argument("--confirm", action="store_true",
                     help="Legacy mode: interactively confirm every ungrouped candidate "
                          "one at a time (no grouping/CSV) — slow with a large backlog, "
                          "kept for small batches")
    args = ap.parse_args()

    if args.apply_csv:
        apply_csv()
        return

    candidates = build_candidates()

    if args.confirm:
        write_review_file(candidates)
        print_summary(candidates)
        if candidates:
            confirm_interactively(candidates)
        return

    # Default: grouped CSV workflow.
    groups = build_groups(candidates)
    if not groups:
        print("\n  ✅ Nothing left to review — every clear-factual question from "
              "history is already covered in the answer bank (exactly, or via a "
              "group you've already answered under a different wording).\n")
        return
    write_group_review(groups)
    print_group_summary(groups)


if __name__ == "__main__":
    main()
