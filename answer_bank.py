#!/usr/bin/env python3
# =============================================================================
# ANSWER_BANK.PY — Central, human-reviewed answer bank
#
# Distinct from answer_cache.py (data/answer_cache.db) and claude_answers.py
# (CLAUDE_QA): those two are populated automatically by whatever the Claude
# API happened to answer at the time — useful, but not the same thing as
# "Raghav actually confirmed this is true." This file is the one place that
# is ONLY ever written to by a human decision:
#   - review_stuck_questions.py   — Raghav types a fresh answer once
#   - import_answer_history.py    — Raghav confirms/corrects an old answer
#     pulled from claude_answers.py / answer_cache.db before it's trusted
#
# GUARDS (do not weaken these):
#   - save() refuses to write a blank/whitespace-only answer. Nothing is
#     ever saved unless a real answer was actually given.
#   - Nothing in this file ever fabricates a Yes/No. It only stores and
#     returns what was explicitly provided to save().
#   - EEO/self-identification and consent/acknowledgement questions are
#     never written here (enforced by the callers — review_stuck_questions.py
#     and import_answer_history.py both filter those out before they ever
#     reach save()) — those stay per-form, answered fresh every time, per
#     Raghav's explicit instruction.
#
# Storage: data/answer_bank.json — {normalized_question: {question, answer,
# source, added}}. Checked from qa_answers.get_answer() FIRST, ahead of the
# static QA dict, since a bank entry reflects Raghav's most recent explicit
# confirmation.
#
# NORMALIZATION — deliberately EXACT match, no partial/substring matching.
# qa_answers.py's partial "key appears inside the label" matching has caused
# real truthfulness bugs in this codebase before (see qa_answers.py's
# _EXACT_ONLY_KEYS comment, and CHANGELOG v2.6.1's compound-question bug) —
# a short match swallowing part of a longer, differently-worded question.
# The bank exists to store answers to SPECIFIC question wordings Raghav has
# actually seen and answered; it should only ever fire on that same wording
# again, not on some other question that happens to share a substring. No
# truncation either (answer_cache.py's 120-char key has the same swallowing
# risk on long compound questions) — the full normalized text is the key.
# =============================================================================

import json
import re
from datetime import datetime
from pathlib import Path

BANK_FILE = Path.home() / "job_pipeline" / "data" / "answer_bank.json"

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalize(question: str) -> str:
    """Full-text normalized key — lowercase, punctuation stripped, whitespace
    collapsed. No truncation, no substring matching (see module docstring)."""
    if not question:
        return ""
    q = question.lower().strip()
    q = _PUNCT_RE.sub(" ", q)
    q = _WS_RE.sub(" ", q).strip()
    return q


def _load() -> dict:
    if not BANK_FILE.exists():
        return {}
    try:
        return json.loads(BANK_FILE.read_text(encoding="utf-8"))
    except Exception:
        # A corrupt bank file should never crash a live apply run — fail
        # closed to "no bank answers available", same as a missing file.
        return {}


def _save_all(bank: dict) -> None:
    BANK_FILE.parent.mkdir(parents=True, exist_ok=True)
    BANK_FILE.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Shared exclusion rules ────────────────────────────────────────────────
# Used by both review_stuck_questions.py (PART 1) and import_answer_history.py
# (PART 2) so the two tools agree on what never enters the bank. Mirrors
# greenhouse_apply_now.py's _EEO_SIGNALS (that file is the live form-filling
# engine; kept as a separate copy here rather than an import, since that
# module pulls in playwright/network deps this offline tooling doesn't need).
_EEO_SIGNALS = (
    "gender", "ethnicity", "race", "hispanic", "latino", "latina",
    "veteran", "disability", "disabilit", "sexual orientation",
    "self-identif", "self identif", "protected class",
)

# Consent / acknowledgement — per Raghav's explicit rule these also stay
# per-form, never bulk-answered from the bank.
_CONSENT_SIGNALS = (
    "consent", "acknowledge", "acknowledgement", "gdpr", "privacy notice",
    "i agree", "terms of service", "terms and conditions",
    "electronically sign", "e-signature", "digital signature",
)

_HASH_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)


def is_hash_label(label: str) -> bool:
    """True if this label is a generated DOM id, not real question text —
    nothing meaningful to show or ask about without live page context."""
    l = (label or "").lower().strip()
    return bool(_HASH_UUID_RE.match(l)) or (l.startswith("q_") and len(l) > 10)


def is_eeo_or_consent(label: str) -> str | None:
    """Returns a reason string if this label must stay per-form (EEO/
    self-identification or consent/acknowledgement — never bulk-answered
    from the bank), or None if it's fair game."""
    l = (label or "").lower()
    if any(s in l for s in _EEO_SIGNALS):
        return "EEO/self-identification — stays per-form"
    if any(s in l for s in _CONSENT_SIGNALS):
        return "consent/acknowledgement — stays per-form"
    return None


def normalize(question: str) -> str:
    """Public wrapper around _normalize() — for other tools (e.g.
    review_stuck_questions.py, import_answer_history.py) that need to
    dedupe/match using the exact same normalization the bank itself uses."""
    return _normalize(question)


def get(question: str) -> str | None:
    """Look up a bank answer for this exact (normalized) question text.
    Returns None if it's never been reviewed/answered — never guesses."""
    key = _normalize(question)
    if not key:
        return None
    entry = _load().get(key)
    return entry["answer"] if entry else None


def save(question: str, answer: str, source: str = "user") -> bool:
    """
    Save a real, human-given answer. Returns True if saved, False if
    refused (blank question/answer). This is the ONLY way entries get into
    the bank — there is no auto-populate path.

    source: "user" (typed fresh via review_stuck_questions.py) or
            "import_confirmed" (pulled from history via
            import_answer_history.py, then confirmed/corrected by Raghav —
            never written before that confirmation step).
    """
    key = _normalize(question)
    ans = str(answer).strip() if answer is not None else ""
    if not key or not ans:
        return False
    bank = _load()
    existing = bank.get(key)
    bank[key] = {
        "question": question.strip(),
        "answer": ans,
        "source": source,
        "added": datetime.now().isoformat(timespec="seconds"),
    }
    if existing and existing.get("answer") != ans:
        bank[key]["previous_answer"] = existing.get("answer")
    _save_all(bank)
    return True


def has(question: str) -> bool:
    return _normalize(question) in _load()


def all_entries() -> dict:
    """Full bank, keyed by normalized question — for review/import tooling."""
    return _load()


def count() -> int:
    return len(_load())


def print_stats() -> None:
    bank = _load()
    print(f"  🏦 Answer bank: {len(bank)} question(s) reviewed and saved")
    by_source = {}
    for entry in bank.values():
        by_source[entry.get("source", "?")] = by_source.get(entry.get("source", "?"), 0) + 1
    for src, n in sorted(by_source.items()):
        print(f"       {src}: {n}")


if __name__ == "__main__":
    print_stats()
