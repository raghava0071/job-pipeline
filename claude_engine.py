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

import config as _cfg
FIT_THRESHOLD = int(os.environ.get("FIT_THRESHOLD", str(_cfg.FIT_THRESHOLD)))

# ── Disk-backed score cache — survives across runs (same job not re-scored daily)
# Key: sha1(job_title.lower() + jd_text[:400])  Value: score dict
# TTL: 7 days — old entries purged on load so stale jobs don't block re-scoring
_SCORE_CACHE_FILE = Path(__file__).parent / "data" / "score_cache.json"
_SCORE_CACHE_TTL  = 7   # days

def _load_score_cache() -> dict:
    try:
        if not _SCORE_CACHE_FILE.exists():
            return {}
        raw   = json.loads(_SCORE_CACHE_FILE.read_text())
        cutoff = __import__("time").time() - _SCORE_CACHE_TTL * 86400
        return {k: v for k, v in raw.items() if v.get("_ts", 0) > cutoff}
    except Exception:
        return {}

def _save_score_cache(cache: dict) -> None:
    try:
        _SCORE_CACHE_FILE.parent.mkdir(exist_ok=True)
        _SCORE_CACHE_FILE.write_text(json.dumps(cache))
    except Exception:
        pass

_SCORE_CACHE: dict = _load_score_cache()

# ── API cost tracker ───────────────────────────────────────────────────────────
# Tracks every Claude API call this session. Printed in session summary email.
# Haiku pricing (June 2025): $0.80/MTok input, $4.00/MTok output
# Sonnet pricing:            $3.00/MTok input, $15.00/MTok output
_API_STATS = {
    "calls":           0,    # total API calls
    "cache_hits":      0,    # score cache hits (saved calls)
    "input_tokens":    0,    # total input tokens
    "output_tokens":   0,    # total output tokens
    "haiku_calls":     0,
    "sonnet_calls":    0,
    "estimated_cost":  0.0,  # USD — total
    "scoring_calls":   0,    # score_fit() calls only
    "scoring_cost":    0.0,  # cost of scoring calls only
}
_HAIKU_IN_COST  = 0.80 / 1_000_000   # per token
_HAIKU_OUT_COST = 4.00 / 1_000_000
_SONNET_IN_COST  = 3.00 / 1_000_000
_SONNET_OUT_COST = 15.00 / 1_000_000

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
        # ── Track cost ────────────────────────────────────────────────────────
        try:
            in_tok  = resp.usage.input_tokens  if hasattr(resp, "usage") else len(prompt) // 4
            out_tok = resp.usage.output_tokens if hasattr(resp, "usage") else max_tokens // 4
            is_fast = (model == MODEL_FAST)
            cost = (in_tok  * (_HAIKU_IN_COST  if is_fast else _SONNET_IN_COST)
                  + out_tok * (_HAIKU_OUT_COST if is_fast else _SONNET_OUT_COST))
            _API_STATS["calls"]          += 1
            _API_STATS["input_tokens"]   += in_tok
            _API_STATS["output_tokens"]  += out_tok
            _API_STATS["estimated_cost"] += cost
            if is_fast:
                _API_STATS["haiku_calls"] += 1
            else:
                _API_STATS["sonnet_calls"] += 1
        except Exception:
            pass
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠  Claude API error ({model}): {e}")
        return ""


def get_cost_summary() -> str:
    """Return a short string summarising API spend this session."""
    s = _API_STATS
    total_tok = s["input_tokens"] + s["output_tokens"]
    return (
        f"API calls: {s['calls']}  "
        f"(haiku: {s['haiku_calls']}, sonnet: {s['sonnet_calls']})  "
        f"| cache hits: {s['cache_hits']}  "
        f"| tokens: {total_tok:,}  "
        f"| est. cost: ${s['estimated_cost']:.4f}"
    )


def get_cost_dict() -> dict:
    """Return full API stats dict for detailed reporting."""
    s = _API_STATS
    other_cost = s["estimated_cost"] - s["scoring_cost"]
    avg_score_cost = s["scoring_cost"] / max(1, s["scoring_calls"])
    saved_cost = s["cache_hits"] * avg_score_cost
    return {
        "total_calls":    s["calls"],
        "cache_hits":     s["cache_hits"],
        "scoring_calls":  s["scoring_calls"],
        "scoring_cost":   round(s["scoring_cost"], 4),
        "other_cost":     round(other_cost, 4),
        "total_cost":     round(s["estimated_cost"], 4),
        "saved_cost":     round(saved_cost, 4),
        "input_tokens":   s["input_tokens"],
        "output_tokens":  s["output_tokens"],
        "haiku_calls":    s["haiku_calls"],
        "sonnet_calls":   s["sonnet_calls"],
    }


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
        _API_STATS["cache_hits"] += 1
        print(f"      ⚡ Score cache hit: {cached.get('score')}% (saved 1 API call)")
        return cached

    prompt = f"""You are a strict technical recruiter screening a resume for an entry/mid-level data role.
Score the candidate's fit. Be honest — a 72 should mean a real, strong match worth a recruiter's time.

CANDIDATE:
- M.S. Data Science & Analytics, Florida Atlantic University (May 2025)
- Work authorization: Authorized to work in the US for 3 years — NO sponsorship required
- Python: 4 yrs · SQL: 4 yrs · Data Engineering: 3 yrs
- Tech stack: ETL/ELT, Azure (ADF, ADLS, Databricks, Synapse), AWS, GCP, PySpark, Kafka,
  dbt, Snowflake, Power BI, Tableau, PostgreSQL, MongoDB, Docker, scikit-learn, TensorFlow, NLP
- Projects delivered: real-time computer vision pipeline (YOLOv8), NLP job-market dashboard,
  ML click-through model (AUC 0.98), PostgreSQL flight-price engine, AI job-application automation

JOB: {job_title} at {company}
JD: {jd_text[:1000]}

SCORING RULES:
- 85–100 (A): Strong skill alignment — candidate has ≥80% of required tech and matches the role squarely
- 72–84 (B): Good fit — has core skills, 1-2 nice-to-haves missing but fully closeable
- 65–71 (C): Borderline — has foundational skills but significant gaps in required tech
- 50–64 (D): Weak match — role needs tech/domain experience the candidate clearly lacks
- <50 (F): Skip — wrong domain, wrong level, or requires things candidate doesn't have

STRICT CRITERIA — score DOWN when:
- JD lists 3+ specific tools candidate has zero experience with (e.g. Salesforce, SAP, COBOL)
- Role requires industry domain the candidate has no background in (healthcare, finance compliance, etc.)
- JD says "5+ years" or specific seniority the candidate doesn't meet
- Role is clearly senior despite non-senior title (Staff, Principal, architect-level scope)

SCORE UP when:
- Core data stack (Python/SQL/cloud/ETL) matches the JD directly
- Candidate's project portfolio demonstrates the exact kind of work the role involves
- Role is entry/junior/associate level — candidate's 3yr background is ideal

Reply ONLY with valid JSON, single line:
{{"score":72,"grade":"B","reasoning":"one tight sentence on fit","strengths":["SQL","PySpark"],"missing":["Snowflake"],"apply":true}}

apply=true only if score>={FIT_THRESHOLD}"""

    _cost_before = _API_STATS["estimated_cost"]
    raw  = _ask(prompt, max_tokens=150, fast=True)   # haiku — 20x cheaper than sonnet
    _API_STATS["scoring_calls"] += 1
    _API_STATS["scoring_cost"]  += _API_STATS["estimated_cost"] - _cost_before
    data = _parse_json(raw)

    if not data or "score" not in data:
        return _fallback_score()

    # Normalize score to int — Claude occasionally returns it as a string "72"
    data["score"] = int(data.get("score", 0))
    data.setdefault("apply", data["score"] >= FIT_THRESHOLD)

    # Cache result to disk — survives across runs so same job isn't re-scored tomorrow
    data["_ts"] = __import__("time").time()
    _SCORE_CACHE[cache_key] = data
    _save_score_cache(_SCORE_CACHE)
    return data


def _fallback_score() -> dict:
    # CRITICAL: when Claude API is unavailable, return SKIP (not apply).
    # Returning apply=True here would cause blind applications to every job
    # the pipeline sees, which is exactly how scam/fake jobs get through.
    return {
        "score": 0, "grade": "F", "apply": False,
        "reasoning": "Claude API unavailable — skipping to avoid blind apply.",
        "strengths": [], "missing": [],
    }


# ── 2b. BATCHED LISTING-PAGE PRE-FILTER ────────────────────────────────────────
def prefilter_cards_batch(cards: list[dict]) -> dict:
    """
    Coarse pre-filter over a whole page/query of search-result cards using only
    their title + company + short snippet — NOT the full JD (that isn't
    available until the job page is actually opened). One haiku call replaces
    what would otherwise be a full page-open for every card, which is the
    expensive step this exists to avoid.

    IMPORTANT — this is NOT a replacement for score_fit(). It's intentionally
    permissive (biased toward "open") since a 1-2 line snippet is a much
    weaker signal than the full JD. Only obviously-wrong cards (senior/lead
    titles, clearly unrelated domain, staffing/consulting agencies) should get
    filtered here; score_fit() still runs the real, strict check on the full
    JD for anything this marks open.

    Returns {key: True/False} where key is each card's "jk" if present, else
    "url", else "title" — True = worth opening the full job page.
    Fails OPEN (returns True for everything) if Claude is unavailable or the
    response can't be parsed, so an API hiccup can never silently drop a job
    that would otherwise have been a real fit.
    """
    def _key(c: dict) -> str:
        return c.get("jk") or c.get("url") or c.get("title", "")

    if not cards:
        return {}
    if not CLAUDE_AVAILABLE:
        return {_key(c): True for c in cards}

    listing = "\n".join(
        f'{i+1}. {c.get("title","")} @ {c.get("company","")} — '
        f'{(c.get("snippet","") or "(no snippet)")[:200]}'
        for i, c in enumerate(cards)
    )

    prompt = f"""Fast, COARSE first pass over a page of job search results for an entry/mid-level data
professional. You only have the title, company, and a 1-2 line snippet for each job — NOT the full
description. Be permissive: only mark a job "skip" when it's obviously wrong — senior/lead/staff/principal/
director/manager level, a clearly unrelated domain (e.g. sales, nursing, retail, hospitality), or an
obvious staffing/consulting agency posting. When in doubt, mark it "open" — a stricter check against the
full job description runs afterward for anything you mark open here, so this step only needs to catch the
clear misses.

CANDIDATE: entry/mid-level data engineer/analyst/scientist — Python, SQL, ETL/ELT, Azure/AWS/GCP, PySpark,
Databricks, Snowflake, Power BI/Tableau, ML fundamentals.

JOBS:
{listing}

Reply ONLY with valid JSON mapping each number (as a string) to true (open) or false (skip). Single line,
no commentary, one entry per job listed above:
{{"1": true, "2": false}}"""

    raw = _ask(prompt, max_tokens=max(200, len(cards) * 15), fast=True)
    data = _parse_json(raw)

    if not isinstance(data, dict) or not data:
        # Couldn't parse a verdict — fail open rather than risk dropping jobs.
        return {_key(c): True for c in cards}

    result = {}
    for i, c in enumerate(cards):
        verdict = data.get(str(i + 1))
        result[_key(c)] = True if verdict is None else bool(verdict)
    return result


# ── 3. RESUME BULLET TAILORING ────────────────────────────────────────────────
def tailor_bullets(bullets: list[str], jd_text: str, job_title: str) -> list[str]:
    """
    Rewrites experience bullets to naturally match JD language.
    Keeps the same count. Falls back to originals if Claude fails.
    """
    if not CLAUDE_AVAILABLE or not bullets:
        return bullets

    original = "\n".join(f"- {b}" for b in bullets[:15])

    prompt = f"""Rewrite these resume bullets for a {job_title} role. Make them the kind that get recruiter callbacks at top tech companies.

Job Description:
{jd_text[:2000]}

Current bullets:
{original}

Rules — follow every one:
1. Keep EXACTLY the same number of bullets.
2. Start EVERY bullet with a strong past-tense action verb: Architected, Engineered, Automated, Optimized, Designed, Reduced, Accelerated, Deployed, Streamlined, Delivered, Unified. NEVER: "Helped", "Assisted", "Worked on", "Was responsible for".
3. Each bullet = action + what + measurable result. Lead with the impact.
4. If the original has a real number — preserve it exactly. If no number exists — add ONE scale descriptor: "production-grade", "enterprise-scale", "millions of records", "sub-second latency", "real-time", "50%+ faster". Do not stack multiple.
5. Mirror JD language naturally — if the JD says "data pipeline" use "data pipeline", not "ETL workflow".
6. Zero clichés: no "leveraged", "utilized", "passionate", "team player", "dynamic", "results-driven".
7. Each bullet: 20–30 words. Punchy and dense — no filler words.
8. Sound like a mid-level engineer who ships real production systems, not a student doing coursework.

Return ONLY the rewritten bullets, one per line, each starting with "- ".
No headers, no explanations, no commentary."""

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

    name   = profile.get("name", "Your Name")
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

    # FIXED 2026-08-25: this prompt used to hardcode "sponsorship = No" and
    # "relocate = No" as literal text, disagreeing with config.py's real
    # facts (REQUIRES_SPONSORSHIP=True) and raghav_profile.py's real
    # PROFILE["relocate"] (now True) — a THIRD place these facts were
    # duplicated and had drifted wrong, on top of qa_answers.py and the
    # per-platform PROFILE_FALLBACK dicts. Derived from the same sources now.
    try:
        import config as _cfg_va
        _sponsor_fact  = "Yes" if getattr(_cfg_va, "REQUIRES_SPONSORSHIP", False) else "No"
        _years_fact    = str(getattr(_cfg_va, "YEARS_EXPERIENCE", "2-3"))
    except Exception:
        _sponsor_fact, _years_fact = "Yes", "2-3"
    try:
        import raghav_profile as _rp_va
        _relocate_fact = "Yes" if _rp_va.PROFILE.get("relocate", False) else "No"
    except Exception:
        _relocate_fact = "No"

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
- For candidate Your Name: work auth = Yes, sponsorship = {_sponsor_fact}, salary = 85000, experience = {_years_fact} years, relocate = {_relocate_fact}
- Use short direct values — no long sentences for field values"""

    try:
        resp = _client.messages.create(
            model=MODEL_FAST,   # haiku — vision works fine, no need for sonnet here
            max_tokens=400,
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
