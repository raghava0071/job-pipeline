#!/opt/anaconda3/bin/python3
# =============================================================================
# CLAUDE_ENGINE.PY — AI Intelligence Layer
# Powered by Claude claude-sonnet-4-6
#
# Provides:
#   - score_fit()          → How well do you match this job? (0-100 + reasoning)
#   - tailor_bullets()     → Rewrite resume bullets to match JD language
#   - write_cover_letter() → Genuine custom cover letter per job
#   - is_good_level()      → Entry/mid level check (filter out Senior/Lead/Director)
# =============================================================================

import os
import json
import re
from pathlib import Path

# ── Load .env ──────────────────────────────────────────────────────────────────
def _load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

# ── Setup Claude client ────────────────────────────────────────────────────────
try:
    import anthropic
    _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    MODEL   = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    _client = None
    MODEL   = None

FIT_THRESHOLD = int(os.environ.get("FIT_THRESHOLD", "65"))

# ── Internal helper ────────────────────────────────────────────────────────────
def _ask(prompt: str, system: str = "", max_tokens: int = 1000) -> str:
    if not CLAUDE_AVAILABLE or not _client:
        return ""
    try:
        resp = _client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system or "You are an expert career coach and technical recruiter specializing in data roles.",
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"ERROR:{e}"

def _parse_json(text: str) -> dict:
    try:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {}

# ── 1. LEVEL FILTER ───────────────────────────────────────────────────────────
SENIOR_WORDS = {
    "senior", "sr.", "sr ", "lead", "principal", "staff",
    "director", "manager", "head of", "vp ", "vice president",
    "chief", "architect", "distinguished", "fellow",
}

def is_good_level(title: str) -> bool:
    """
    Returns True if the job title looks like entry or mid level.
    Filters out Senior / Lead / Director / Manager / VP etc.
    """
    t = title.lower()
    return not any(w in t for w in SENIOR_WORDS)

# ── 2. FIT SCORING ────────────────────────────────────────────────────────────
def score_fit(profile_summary: str, jd_text: str,
              job_title: str, company: str) -> dict:
    """
    Claude scores candidate fit for a specific job.

    Returns dict:
      score     : int  0-100
      grade     : str  A / B+ / B / C / D
      reasoning : str  brief explanation
      strengths : list top matching skills
      missing   : list gaps / concerns
      apply     : bool should we apply?
    """
    if not CLAUDE_AVAILABLE:
        return _fallback_score()

    prompt = f"""You are evaluating an ENTRY/MID LEVEL job candidate for a data role.
Score how well they match the job. Focus on SKILLS MATCH, not years of experience.

IMPORTANT CONTEXT:
- Candidate has 3+ years total experience (including undergrad projects + grad school + work)
- Python/SQL: 4 years (since undergrad 2020)
- Data Engineering, ETL, Cloud: 2-3 years
- If the job says "2-3 years experience" and candidate has matching skills → high score
- Do NOT penalize for being entry/mid level — this role IS entry/mid level
- A skill match should be weighted heavily even if candidate's title doesn't match exactly

=== JOB ===
Title   : {job_title}
Company : {company}

Job Description:
{jd_text[:3000]}

=== CANDIDATE ===
{profile_summary[:3000]}

Score based on:
1. Technical skill overlap (50%) — do their skills match what's listed?
2. Role relevance (30%) — is this a data/analytics/ML/engineering role they can do?
3. Education & background (20%) — M.S. Data Science is a strong signal

Respond ONLY in this JSON format (no other text):
{{
  "score": 72,
  "grade": "B",
  "reasoning": "Strong SQL and Python skills match well. Azure and Spark align with JD.",
  "strengths": ["SQL", "Python", "Azure"],
  "missing": ["Snowflake (minor gap)"],
  "apply": true
}}

Scoring guide:
  85-100 → Excellent skill match   (A)
  70-84  → Good match              (B)
  65-69  → Decent match, apply     (C)
  50-64  → Weak match, skip        (D)
  below 50 → Poor fit, definitely skip
apply = true if score >= {FIT_THRESHOLD}"""

    raw  = _ask(prompt, max_tokens=500)
    data = _parse_json(raw)

    if not data or "score" not in data:
        return _fallback_score()

    data.setdefault("apply", data.get("score", 0) >= FIT_THRESHOLD)
    return data

def _fallback_score() -> dict:
    return {
        "score": 65, "grade": "C", "apply": True,
        "reasoning": "Claude unavailable — default score applied.",
        "strengths": [], "missing": [],
    }

# ── 3. RESUME BULLET TAILORING ────────────────────────────────────────────────
def tailor_bullets(bullets: list[str], jd_text: str, job_title: str) -> list[str]:
    """
    Rewrites experience bullets to naturally match JD language.
    Keeps the same count. Falls back to originals if Claude fails.
    """
    if not CLAUDE_AVAILABLE or not bullets:
        return bullets

    original = "\n".join(f"- {b}" for b in bullets[:15])

    prompt = f"""Rewrite these resume bullets to better match the job description below.

Job Title: {job_title}

Job Description (key parts):
{jd_text[:2000]}

Current bullets:
{original}

Rules:
- Keep EXACTLY the same number of bullets
- Use keywords from the JD naturally — never keyword-stuff
- Start every bullet with a strong action verb (Designed, Built, Optimized, Led, etc.)
- Keep or improve any numbers/metrics
- Sound like a real person, not a robot
- Max 2 lines per bullet

Return ONLY the rewritten bullets, one per line, each starting with "- ".
No headers, no explanations."""

    raw   = _ask(prompt, max_tokens=1500)
    lines = [l.strip().lstrip("- ").strip()
             for l in raw.splitlines()
             if l.strip().startswith("-")]

    # Only use Claude output if it returned a sensible number of bullets
    if len(lines) >= max(1, len(bullets) // 2):
        return lines
    return bullets  # fallback

# ── 4. COVER LETTER ───────────────────────────────────────────────────────────
def write_cover_letter(name: str, profile_summary: str,
                        jd_text: str, job_title: str, company: str) -> str:
    """
    Writes a genuine, concise, custom cover letter.
    Returns plain text (caller handles formatting/docx).
    """
    if not CLAUDE_AVAILABLE:
        return (
            f"As a data professional with hands-on experience in SQL, Python, and "
            f"data engineering, I am excited to apply for the {job_title} role at {company}.\n\n"
            f"My background aligns well with your requirements and I would welcome "
            f"the opportunity to contribute to your team.\n\n"
            f"Best regards,\n{name}"
        )

    prompt = f"""Write a cover letter for this job application.

Candidate : {name}
Role      : {job_title} at {company}

Candidate background:
{profile_summary[:1500]}

Job description:
{jd_text[:2000]}

Requirements:
- Exactly 3 short paragraphs
- Para 1: Why this specific role + company excites you (2 sentences)
- Para 2: 2-3 concrete skills/achievements that match the JD
- Para 3: Confident closing with call to action (1-2 sentences)
- Do NOT start with "I" or "Dear Hiring Manager"
- Do NOT use clichés: "I am writing to", "I am a passionate", "team player"
- Under 220 words total
- Professional, direct, warm
- No salutation, no signature — just the 3 paragraphs"""

    return _ask(prompt, max_tokens=600)

# ── 5. PROFILE SUMMARY BUILDER ────────────────────────────────────────────────
def build_profile_summary(profile: dict) -> str:
    """
    Converts raghav_profile dict into a full readable summary for Claude scoring.
    Shows ALL skills, correct experience order, and skill years.
    """
    import config as cfg

    name   = profile.get("name", "Raghavendra Karanam")
    skills = profile.get("skills", [])
    exp    = profile.get("experience", [])
    edu    = profile.get("education", [{}])

    # Education
    edu_line = ""
    if edu:
        e0 = edu[0]
        edu_line = f"{e0.get('degree','')}, {e0.get('field','')}, {e0.get('school','')}"

    # Skills — show ALL of them, not just 25
    all_skills = ", ".join(skills) if skills else "Python, SQL, PySpark, Azure, AWS, GCP, Snowflake, dbt, Airflow, Kafka, Spark, Power BI, Tableau, Docker, TensorFlow, pandas, scikit-learn"

    # Skill years — key context for scoring
    skill_years_lines = "\n".join(
        f"  {k}: {v} yrs" for k, v in cfg.SKILL_YEARS.items()
    )

    # Experience — primary data jobs first, max 3 bullets each
    exp_lines = []
    primary   = [e for e in exp if e.get("include_always") or "data" in e.get("title","").lower() or "engineer" in e.get("title","").lower()]
    secondary = [e for e in exp if e not in primary]
    ordered   = (primary + secondary)[:4]

    for e in ordered:
        role    = e.get("title", "")
        company = e.get("company", "")
        dur     = e.get("duration", "")
        bullets = e.get("bullets", e.get("responsibilities", []))[:3]
        exp_lines.append(f"\n  [{role}] @ {company}  ({dur})")
        for b in bullets:
            exp_lines.append(f"    • {b[:120]}")

    return f"""CANDIDATE: {name}
EDUCATION : {edu_line}
WORK AUTH : F-1 OPT/STEM OPT — authorized, no sponsorship needed
TOTAL EXP : 3+ years (undergrad + grad research + professional)

SKILLS (all):
  {all_skills}

EXPERIENCE BY SKILL (years):
{skill_years_lines}

WORK HISTORY:
{"".join(exp_lines)}"""

# ── Quick self-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Claude available : {CLAUDE_AVAILABLE}")
    print(f"Model            : {MODEL}")
    print(f"Fit threshold    : {FIT_THRESHOLD}%")

    if CLAUDE_AVAILABLE:
        test = _ask("Say 'Claude engine is working!' and nothing else.")
        print(f"Test response    : {test}")
