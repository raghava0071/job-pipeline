# =============================================================================
# profile_answers.py — single source of truth for FACTUAL application answers
#
# Two layers, checked in order by callers (qa_answers.get_answer() and, for
# Greenhouse, _smart_fill_greenhouse_fields()):
#
#   Layer A — answer_from_profile(label)
#       Deterministic, zero API cost. Pattern-matches the question against
#       real facts in raghav_profile.py / config.py. Returns "Yes"/"No"/a
#       short value, or None if this module has no confident, truthful
#       answer for it.
#
#   Layer B — answer_via_claude_fallback(label, field_type, options)
#       Only reached when Layer A returns None. Calls the Claude API,
#       grounded strictly in the same profile facts, instructed to answer
#       "UNKNOWN" rather than guess. UNKNOWN (or anything that doesn't
#       validate) becomes None here too.
#
# HARD TRUTHFULNESS RULE (non-negotiable — 2026-08-25 request):
#   - "Have you used <tool>?"   -> Yes ONLY if <tool> (or a known synonym)
#                                   is actually a key in raghav_profile.SKILL_YEARS.
#                                   Never a blanket Yes to an unnamed/unlisted tool.
#   - "Do you have N+ years?"   -> compared against real years (overall
#                                   ~cfg.YEARS_EXPERIENCE, or per-skill if a
#                                   tool is named). N above what's real -> No.
#   - "<X> degree / post-PhD?"  -> compared against
#                                   raghav_profile.PROFILE["max_education_level"]
#                                   ("master"). Never claims a PhD.
#   - The Claude fallback (Layer B) answers ONLY from the facts it is given
#     and must reply UNKNOWN if those facts don't support a confident
#     answer — UNKNOWN is converted to None here, never guessed into a Yes.
#
# Both layers return None when they can't answer truthfully — callers treat
# None exactly like today's "route to stuck_questions.json" signal. Nothing
# in this module ever fabricates a qualification.
# =============================================================================

import re
from typing import List, Optional

import config as cfg
import raghav_profile as rp
from jd_parser import EDU_LEVELS, EDU_PATTERNS, SYNONYMS


# ══════════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════════

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _expand_tool(name: str) -> List[str]:
    """A tool name plus any known synonyms (reuses jd_parser.SYNONYMS)."""
    name = name.lower().strip()
    variants = {name}
    for v in SYNONYMS.get(name, []):
        variants.add(v.lower())
    for master, syns in SYNONYMS.items():
        if name in [s.lower() for s in syns]:
            variants.add(master.lower())
    return list(variants)


def _skill_years_for(tool: str) -> Optional[str]:
    """
    Years-of-experience string for `tool` if it (or a synonym) is a real
    skill in raghav_profile.SKILL_YEARS — the canonical skill/years source
    (see config.py's SKILL_YEARS comment for why it's not cfg.SKILL_YEARS).
    Returns None if `tool` isn't a real skill — callers must not guess Yes.
    """
    tool_n = _norm(tool)
    if not tool_n:
        return None
    for key, years in rp.SKILL_YEARS.items():
        if tool_n == key or tool_n in _expand_tool(key) or key in _expand_tool(tool_n):
            return years
    return None


def _best_skill_match(phrase: str) -> Optional[str]:
    """
    Check every contiguous word n-gram in `phrase` against SKILL_YEARS,
    longest first (e.g. "years of data analytics experience" -> tries
    "data analytics" then falls back to "analytics" alone, which is a real
    SKILL_YEARS key). Guards against regex captures that grab extra filler
    words on either side (e.g. "python before" -> still finds "python").
    """
    words = _norm(phrase).split()
    n = len(words)
    for length in range(n, 0, -1):
        for start in range(0, n - length + 1):
            years = _skill_years_for(" ".join(words[start:start + length]))
            if years is not None:
                return years
    return None


_GENERIC_EXPERIENCE_WORDS = {
    "professional", "work", "industry", "total", "overall", "relevant",
}

_FILLER_TAIL_WORDS = {
    "before", "previously", "professionally", "now", "currently", "recently",
    "ever", "at", "work", "in", "a", "the", "this", "our", "your", "you",
    "we", "company", "role", "position", "job", "for", "with", "any",
    "prior", "capacity", "environment", "setting", "context", "please",
}


def _strip_filler_tail(words: List[str]) -> List[str]:
    while words and words[-1] in _FILLER_TAIL_WORDS:
        words = words[:-1]
    return words


def _looks_like_tool_token(words: List[str]) -> bool:
    """True if `words` plausibly names a real technology (short, alnum-ish),
    so a confident 'No' is safe to give when it's not in SKILL_YEARS."""
    return bool(words) and len(words) <= 4 and all(
        re.match(r"^[a-z0-9\+\#\./-]+$", w) for w in words
    )


def _looks_company_named(label: str) -> bool:
    """Heuristic: does the label contain a capitalized proper-noun token
    (not at sentence start) or a quoted name — i.e. does it actually name
    a specific company, vs. a generic 'have you worked before' question?"""
    return bool(re.search(r"\b[A-Z][a-zA-Z]{2,}\b", label[3:]))


def _required_edu_level(label_lower: str) -> Optional[int]:
    """If `label_lower` asks about a specific degree level, return the
    highest EDU_LEVELS rank it's asking about, else None."""
    if not re.search(
        r"degree|education|phd|master|bachelor|doctorate|post-?phd|post-?doctoral",
        label_lower,
    ):
        return None
    best = None
    for level_name, pattern in EDU_PATTERNS.items():
        if re.search(pattern, label_lower):
            rank = EDU_LEVELS[level_name]
            if best is None or rank > best:
                best = rank
    return best


_TOOL_QUESTION_RE = re.compile(
    r"(?:have you used|do you have experience (?:with|in|using)|"
    r"are you familiar with|familiarity with|proficien(?:cy|t) (?:with|in)|"
    r"experience (?:using|working with)|have you worked with|"
    r"knowledge of|hands[- ]on experience with)\s+"
    r"(?P<tool>[A-Za-z0-9\+\#\.\-/ ]{2,40}?)"
    r"(?:\?|$|\.|,)",
    re.IGNORECASE,
)

_YEARS_QUESTION_RE = re.compile(
    r"(?P<n>\d+)\+?\s*(?:or more\s*)?years?.{0,40}?experience"
    r"(?:.{0,20}?(?:with|in|using)\s+(?P<tool>[A-Za-z0-9\+\#\.\-/ ]{2,40}?))?"
    r"(?:\?|$|\.|,)",
    re.IGNORECASE,
)

_EMPLOYMENT_HISTORY_RE = re.compile(
    r"(previously|prior(ly)?|before|ever)\s+(worked|employed)|"
    r"worked\s+(at|for)\s|employed\s+(at|by)\s",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════
#  Layer A — deterministic profile lookup
# ══════════════════════════════════════════════════════════════════

def answer_from_profile(label: str) -> Optional[str]:
    """
    Try to answer `label` purely from real facts in raghav_profile.py /
    config.py. Returns "Yes" / "No" / a short factual string, or None if
    this module has no confident, truthful answer — caller should then try
    answer_via_claude_fallback() and finally route to stuck questions.
    """
    if not label or len(label) < 3:
        return None
    l = _norm(label)

    # ── Work authorization ──────────────────────────────────────────────
    if re.search(
        r"authorized to work|legally (able|authorized|permitted) to work|"
        r"work authorization|eligible to work (legally )?in|work legally",
        l,
    ):
        return "Yes" if cfg.AUTHORIZED_TO_WORK_NOW else "No"

    # ── Sponsorship ──────────────────────────────────────────────────────
    if re.search(r"require.{0,15}sponsorship|need.{0,15}sponsorship|visa sponsorship|sponsor.{0,15}visa", l):
        return "Yes" if cfg.REQUIRES_SPONSORSHIP else "No"

    # ── Relocation ─────────────────────────────────────────────────────
    if re.search(r"willing to relocate|open to relocat|able to relocate", l):
        return "Yes" if rp.PROFILE.get("relocate") else "No"

    # ── Hybrid / on-site / in-office (incl. named-city variants like SF) ──
    if re.search(r"hybrid|on[- ]?site|in[- ]office|in[- ]person", l) and \
       re.search(r"willing|able|open to|available|comfortable|can you|would you", l):
        return "Yes" if rp.PROFILE.get("hybrid_or_onsite_ok") else "No"

    # ── Prior employment at a specific named company ─────────────────────
    if _EMPLOYMENT_HISTORY_RE.search(l):
        for name in rp.EMPLOYERS_WORKED_AT:
            if name in l:
                return "Yes"
        if _looks_company_named(label):
            # Question names a real company that isn't a known past
            # employer — truthful answer is No, not a guess or a skip.
            return "No"
        return None

    # ── Education level / degree comparisons ─────────────────────────────
    required = _required_edu_level(l)
    if required is not None:
        my_level = EDU_LEVELS.get(rp.PROFILE.get("max_education_level", "master"), 4)
        return "Yes" if my_level >= required else "No"

    # ── "How many years of [X] experience do you have?" — open-ended,
    # answered with the REAL number, never a threshold guess. Only answers
    # with the generic overall-experience figure when no specific domain is
    # named (or the named words are themselves generic, e.g. "professional
    # experience"); if a specific domain IS named and isn't a real skill,
    # this returns None rather than guessing a number for work never done. ──
    m = re.search(r"how many years\b(.*?)\bexperience\b", l)
    if m:
        middle_words = [
            w for w in m.group(1).split()
            if w not in {"of", "you", "have", "do", "did", "spent", "in", "roles", "the", "total"}
        ]
        if not middle_words or all(w in _GENERIC_EXPERIENCE_WORDS for w in middle_words):
            return str(cfg.YEARS_EXPERIENCE)
        years = _best_skill_match(" ".join(middle_words))
        return years if years is not None else None

    # ── "Do you have N+ years [of X]?" ────────────────────────────────────
    m = _YEARS_QUESTION_RE.search(l)
    if m:
        n = int(m.group("n"))
        tool = m.group("tool")
        if tool:
            years = _best_skill_match(tool)
            if years is None:
                # Named a specific tool we don't have real years for —
                # never guess Yes. Let it fall through to Claude/stuck.
                return None
            return "Yes" if int(years) >= n else "No"
        return "Yes" if cfg.YEARS_EXPERIENCE >= n else "No"

    # ── "Have you used / are you familiar with <tool>?" ───────────────────
    m = _TOOL_QUESTION_RE.search(l)
    if m:
        raw_tool = m.group("tool").strip(" .,")
        words = _strip_filler_tail(_norm(raw_tool).split())
        if words:
            tool = " ".join(words)
            years = _best_skill_match(tool)
            if years is not None:
                return "Yes"
            if _looks_like_tool_token(words):
                # Real, specific, tool-shaped token that isn't in
                # SKILL_YEARS — truthful answer is No, per the hard
                # truthfulness rule. Never a blanket Yes.
                return "No"
        return None

    return None


# ══════════════════════════════════════════════════════════════════
#  Layer B — Claude API fallback, strictly grounded in profile facts
# ══════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """You are answering a job application question on behalf of a real candidate.
You may use ONLY the facts listed below. Never invent, assume, or infer a
skill, tool, employer, certification, degree, or qualification that is not
explicitly listed. If the facts do not clearly support a confident answer,
reply with exactly: UNKNOWN

Rules:
- "Have you used/do you have experience with <tool>?" -> Yes ONLY if that
  exact tool (or a very close synonym, e.g. "Spark" / "PySpark") appears in
  the skills list below. Otherwise -> No.
- "Do you have N+ years of X?" -> compare N against the real years listed.
  If real years < N, answer No. Never round up.
- Education / degree questions -> compare against the real highest degree
  listed. Never claim a higher degree than what is listed.
- Work authorization / sponsorship / relocation / hybrid questions -> use
  the exact facts given, do not guess.
- If the question is not a factual yes/no or short-answer question about
  the candidate (e.g. a free-form essay prompt, or a cover-letter-style
  answer), reply UNKNOWN.

Reply with ONLY one of: Yes / No / UNKNOWN / a short factual value (e.g. a
number or a short phrase) if the question clearly asks for one (e.g. "how
many years of X"). No explanation, no punctuation beyond what's needed.
"""


def _profile_facts_block() -> str:
    skill_lines = "\n".join(f"  - {k}: {v} years" for k, v in rp.SKILL_YEARS.items())
    employer_lines = ", ".join(sorted(rp.EMPLOYERS_WORKED_AT)) or "(none)"
    sponsor = ("will require H-1B sponsorship in the future"
               if cfg.REQUIRES_SPONSORSHIP else "does not require sponsorship")
    return f"""CANDIDATE FACTS (use ONLY these — do not add anything):
- Work authorization: authorized to work now = {cfg.AUTHORIZED_TO_WORK_NOW} (F-1 STEM OPT)
- Sponsorship: {sponsor}
- Willing to relocate: {rp.PROFILE.get('relocate')}
- Willing to work hybrid/on-site (including San Francisco): {rp.PROFILE.get('hybrid_or_onsite_ok')}
- Highest degree held: {rp.PROFILE.get('max_education_level', 'master')} (M.S. Data Science & Analytics, Florida Atlantic University, 2025) — NOT a PhD
- Total professional experience: {cfg.YEARS_EXPERIENCE} years
- Real past employers (ONLY these — answer No for any other company): {employer_lines}
- Skills and real years of experience with each (ONLY these tools count as "used"):
{skill_lines}
"""


def answer_via_claude_fallback(
    label: str,
    field_type: str = "yes_no",
    options: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Layer B — only called after answer_from_profile() returns None. Asks
    Claude to answer `label` strictly from real profile facts. Returns None
    (never a guess) if Claude can't answer confidently, if the API is
    unavailable, or if its answer doesn't validate against `options`.
    """
    if not label or len(label) < 3:
        return None

    try:
        import claude_engine
    except Exception:
        return None

    prompt = _profile_facts_block()
    if options:
        opt_list = "\n".join(f"  - {o}" for o in options)
        prompt += f"\nThe answer MUST be exactly one of these options, or UNKNOWN:\n{opt_list}\n"
    prompt += f"\nQUESTION: {label}\n\nANSWER:"

    try:
        raw = claude_engine._ask(prompt, system=_SYSTEM_PROMPT, max_tokens=30, fast=True)
    except Exception:
        return None

    if not raw:
        return None
    ans = raw.strip().strip('"').strip(".")
    ans_l = ans.lower()

    if not ans or ans_l == "unknown":
        return None

    if field_type == "yes_no":
        if ans_l.startswith("yes"):
            return "Yes"
        if ans_l.startswith("no"):
            return "No"
        return None  # didn't shape-match a yes/no answer — don't guess

    if options:
        for o in options:
            if ans_l == o.strip().lower():
                return o
        return None  # not one of the offered choices — don't guess

    if len(ans) <= 60:
        return ans
    return None
