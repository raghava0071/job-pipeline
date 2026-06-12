#!/opt/anaconda3/bin/python3
# =============================================================================
# CLAUDE_ENGINE.PY — AI Intelligence Layer
#
# Cost-optimised: scoring uses haiku, cover letters use haiku.
# Fit scores are cached in-process by JD hash — same job across multiple
# search queries costs exactly 1 API call, not N.
# Local keyword pre-filter runs before any API call — obvious mismatches
# are rejected instantly with zero token spend.
# =============================================================================

import os
import json
import re
import hashlib
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
    MODEL        = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    MODEL_FAST   = "claude-haiku-4-5-20251001"   # used for scoring + cover letters
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    _client = None
    MODEL      = None
    MODEL_FAST = None

FIT_THRESHOLD = int(os.environ.get("FIT_THRESHOLD", "65"))

# ── In-process score cache — survives the whole run, not just one job ──────────
# Key: sha1(job_title.lower() + jd_text[:400])  Value: score dict
_SCORE_CACHE: dict = {}

# ── Candidate skill keywords for local pre-filter ─────────────────────────────
_LOCAL_SKILLS = {
    "python", "sql", "pyspark", "spark", "kafka", "airflow", "hadoop",
    "etl", "elt", "pipeline", "data warehouse", "data lake", "snowflake",
    "databricks", "dbt", "aws", "azure", "gcp", "google cloud",
    "power bi", "tableau", "looker", "pandas", "numpy", "scikit-learn",
    "tensorflow", "pytorch", "machine learning", "deep learning", "nlp",
    "docker", "kubernetes", "git", "postgresql", "mysql", "mongodb",
    "rest api", "fastapi", "flask", "streamlit", "r", "scala",
    "data engineer", "data analyst", "data scientist", "analytics",
    "bi", "business intelligence", "reporting", "dashboard",
}

# ── Internal helpers ───────────────────────────────────────────────────────────
def _ask(prompt: str, system: str = "", max_tokens: int = 1000, fast: bool = False) -> str:
    """Call Claude. fast=True uses haiku (~20x cheaper) for structured/scoring tasks."""
    if not CLAUDE_AVAILABLE or not _client:
        return ""
    model = MODEL_FAST if fast else MODEL
    try:
        resp = _client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system or "You are an expert career coach and technical recruiter specializing in data roles.",
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠  Claude API error ({model}): {e}")
        return ""


def local_prefilter(jd_text: str, job_title: str) -> tuple[bool, int]:
    """
    Zero-cost local keyword check BEFORE any API call.
    Returns (should_skip, match_count).
    If match_count < 2, the job is almost certainly below gate — skip instantly.
    This eliminates ~50-60% of scoring API calls with zero token spend.
    """
    jd_lower = jd_text.lower()
    title_lower = job_title.lower()

    # Hard domain check — if none of these words appear, it's definitely off-domain
    domain_words = {"data", "sql", "python", "analytics", "engineer", "analyst",
                    "scientist", "bi", "etl", "pipeline", "database", "reporting",
                    "intelligence", "machine learning", "ml", "cloud", "spark"}
    has_domain = any(w in jd_lower or w in title_lower for w in domain_words)
    if not has_domain:
        return True, 0  # skip — zero data/tech content

    # Count candidate skill matches in JD
    matches = sum(1 for skill in _LOCAL_SKILLS if skill in jd_lower)

    # Skip if fewer than 2 real skills match — Claude would score this <50%
    return (matches < 2), matches

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
    Score candidate fit. Uses haiku (fast + cheap) — structured JSON output
    is well within haiku's capability. In-process cache avoids re-scoring
    the same job seen across multiple search queries.
    """
    if not CLAUDE_AVAILABLE:
        return _fallback_score()

    # ── In-process cache: same job = 0 extra API calls ────────────────────────
    cache_key = hashlib.sha1(
        (job_title.lower() + jd_text[:400]).encode()
    ).hexdigest()
    if cache_key in _SCORE_CACHE:
        cached = _SCORE_CACHE[cache_key]
        print(f"      ⚡ Score cache hit: {cached.get('score')}% (saved 1 API call)")
        return cached

    prompt = f"""Score this candidate for an entry/mid-level data role. Focus on SKILLS MATCH.

Candidate facts:
- M.S. Data Science, Florida Atlantic University 2025
- Python: 4 yrs, SQL: 4 yrs, Data Engineering: 2-3 yrs
- Skills: ETL/ELT, Azure, AWS, GCP, Spark, Kafka, dbt, Snowflake, Power BI, Tableau,
  PostgreSQL, MongoDB, Docker, scikit-learn, TensorFlow, NLP, Streamlit
- Projects: real-time CV pipeline (YOLOv8), NLP job market dashboard, ML CTR prediction (AUC 0.98),
  PostgreSQL flight price engine, AI automation pipeline (300+ apps/day)
- Do NOT penalize entry/mid level — this IS an entry/mid role

JOB: {job_title} at {company}
JD (first 2000 chars): {jd_text[:2000]}

Reply ONLY in JSON:
{{"score":72,"grade":"B","reasoning":"one sentence","strengths":["SQL","Python"],"missing":["Snowflake"],"apply":true}}

Scoring: 85-100=A(excellent), 70-84=B(good), 65-69=C(apply), 50-64=D(skip), <50=F(skip)
apply=true if score>={FIT_THRESHOLD}"""

    raw  = _ask(prompt, max_tokens=250, fast=True)   # haiku — 20x cheaper than sonnet
    data = _parse_json(raw)

    if not data or "score" not in data:
        return _fallback_score()

    data.setdefault("apply", data.get("score", 0) >= FIT_THRESHOLD)

    # Cache result — if this job appears in another query, skip the API call
    _SCORE_CACHE[cache_key] = data
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

    prompt = f"""Rewrite these resume bullets for a {job_title} role. Make them executive-quality — the kind that get callbacks at top tech companies.

Job Description:
{jd_text[:2000]}

Current bullets:
{original}

Rules:
- Keep EXACTLY the same number of bullets
- Start EVERY bullet with a powerful, specific action verb (Architected, Engineered, Delivered, Reduced, Accelerated, Deployed, Automated, Streamlined — NOT "Helped", "Assisted", "Worked on")
- Each bullet = one achievement with context + action + impact. Lead with the result when possible.
- Naturally weave in JD keywords — do not keyword-stuff
- Quantify wherever the original has numbers — preserve and improve them
- Zero clichés: no "leveraged", "utilized", "passionate", "team player", "fast-paced environment"
- No dashes, no hyphens as connectors mid-sentence. Use clean, direct prose.
- Max 20 words per bullet. Tight, punchy, confident.
- Sound like a senior engineer who ships real systems, not a student describing homework.

Return ONLY the rewritten bullets, one per line, each starting with "- ".
No headers, no explanations."""

    raw   = _ask(prompt, max_tokens=1500, fast=True)   # haiku — 4x cheaper, same structured output quality
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

    import random
    # Pick a different opening angle each time to prevent repetition
    angles = [
        "open with a sharp observation about what makes this company's data challenge interesting or unique",
        "open with the single most relevant technical thing you've built that maps to this role",
        "open with a confident statement about the specific problem this role solves and why you're the right engineer for it",
        "open with a brief story — one moment or project that directly connects to what this company needs",
        "open with what drew you specifically to this company's space or product, then link to your technical fit",
    ]
    opening_angle = random.choice(angles)

    prompt = f"""You are writing an executive-level cover letter for a data engineering job application. This must feel personally written, not templated.

Candidate: {name}
Role: {job_title}
Company: {company}

Candidate background (use specific facts from this — do not make things up):
{profile_summary[:1500]}

Job description (read carefully to understand what this company actually needs):
{jd_text[:2000]}

Write exactly 3 paragraphs. No salutation. No sign-off. No "Dear Hiring Manager". No subject line.

PARAGRAPH 1 — Hook (2-3 sentences):
{opening_angle}. Be specific to THIS company and THIS role. Do not use generic phrases.

PARAGRAPH 2 — Proof (3-4 sentences):
Name 2 or 3 real, concrete technical achievements from the candidate's background that directly match what this job needs. Include real tools, real outcomes, real scale where possible. Make this paragraph feel like a conversation between two engineers, not a list.

PARAGRAPH 3 — Close (2 sentences):
One sentence that ties the candidate's trajectory to this company's direction. One confident call to action — no begging, no "I hope to hear from you soon", no "thank you for your time".

STRICT RULES:
- No bullet points. No hyphens used as bullets. No dashes used as list separators.
- Never start any sentence with "I am" as the opener.
- No clichés: "passionate", "team player", "go-getter", "I am writing to express", "I am excited to apply", "dynamic", "leverage", "synergy"
- No mention of visa, OPT, sponsorship, work authorization.
- Max 200 words total.
- Every sentence must be doing real work — cut anything vague or generic.
- Sound like someone who could walk in tomorrow and ship production data infrastructure.

Return ONLY the 3 paragraphs separated by blank lines. Nothing else."""

    result = _ask(prompt, max_tokens=700, fast=True)   # haiku — good enough for cover letters
    if not result or result.startswith("ERROR") or "Error code" in result or len(result) < 100:
        # Fallback: still better than the old template
        return (
            f"The scale of what {company} is building with data is exactly the kind of "
            f"challenge I have been engineering toward. As a {job_title}, I would bring "
            f"production-proven experience building the systems that turn raw data into "
            f"decisions at scale.\n\n"
            f"At Knowvia Tech, I architected end-to-end ETL pipelines on Azure and AWS "
            f"processing millions of records daily, cut pipeline latency through intelligent "
            f"partitioning and PySpark optimization, and built data quality frameworks that "
            f"caught issues before they reached downstream consumers. At FAU, I delivered "
            f"an M.S. in Data Science and applied that depth to real production systems.\n\n"
            f"I would welcome a conversation about what {company} is building and how I "
            f"can contribute from day one."
        )
    return result

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

# ── 6. VISION ASSIST — sees the screen when pipeline is stuck ─────────────────
def vision_assist(screenshot_bytes: bytes, page_text: str,
                  job_title: str, company: str) -> dict:
    """
    Called when the pipeline is stuck on a form page.
    Sends a screenshot + page text to Claude Vision.
    Returns a dict describing what to do next:
      {
        "issue":   "brief description of what Claude sees",
        "action":  "fill_field" | "click_button" | "skip" | "captcha",
        "fields":  [{"label": "...", "value": "..."}],   # fields to fill
        "button":  "Continue" | "Next" | "Submit" | ...,  # button to click after filling
        "reason":  "why this action"
      }
    """
    if not CLAUDE_AVAILABLE or not _client:
        return {"issue": "Claude unavailable", "action": "skip", "fields": [], "button": "Continue", "reason": ""}

    import base64

    try:
        img_b64 = base64.standard_b64encode(screenshot_bytes).decode("utf-8")
    except Exception as e:
        return {"issue": f"Screenshot encode error: {e}", "action": "skip", "fields": [], "button": "Continue", "reason": ""}

    prompt = f"""You are helping an automated job application bot that is STUCK on a form page.

Job: {job_title} at {company}

The bot cannot figure out how to proceed. Look at the screenshot and the page text below.
Tell the bot exactly what to do to move forward and complete the application.

PAGE TEXT (scraped):
{page_text[:2000]}

Respond ONLY in this JSON format:
{{
  "issue": "one-line description of what you see on screen",
  "action": "fill_field" | "click_button" | "captcha" | "skip",
  "fields": [
    {{"label": "exact field label or placeholder", "value": "what to type or select"}}
  ],
  "button": "exact button text to click after filling (e.g. Continue, Next, Submit)",
  "reason": "one sentence explaining why"
}}

Rules:
- If you see a CAPTCHA → action = "captcha", fields = [], button = ""
- If there are unfilled required fields → action = "fill_field", list each field with a value
- If all fields look filled but no progress → action = "click_button", button = the correct button text
- If the page looks like a confirmation/success → action = "skip" (already submitted)
- For candidate Raghavendra Karanam: work auth = Yes, sponsorship = No, salary = 85000, experience = 2-3 years, relocate = No
- Use short direct values — no long sentences for field values"""

    try:
        resp = _client.messages.create(
            model=MODEL,
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        }
                    },
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        raw  = resp.content[0].text.strip()
        data = _parse_json(raw)
        if data and "action" in data:
            data.setdefault("fields", [])
            data.setdefault("button", "Continue")
            data.setdefault("reason", "")
            print(f"  👁  Vision: {data.get('issue','?')} → {data.get('action')} ({data.get('reason','')})")
            return data
        else:
            print(f"  👁  Vision: could not parse response — raw: {raw[:200]}")
    except Exception as e:
        print(f"  👁  Vision API error: {e}")

    return {"issue": "Vision parse failed", "action": "click_button", "fields": [], "button": "Continue", "reason": "fallback"}


# ── Quick self-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Claude available : {CLAUDE_AVAILABLE}")
    print(f"Model            : {MODEL}")
    print(f"Fit threshold    : {FIT_THRESHOLD}%")

    if CLAUDE_AVAILABLE:
        test = _ask("Say 'Claude engine is working!' and nothing else.")
        print(f"Test response    : {test}")
