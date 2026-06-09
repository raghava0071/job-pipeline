#!/usr/bin/env python3
"""
jd_parser.py — PRO ATS Engine v4
Multi-Dimensional Scoring — 5 Industry-Standard Dimensions

  [35%] Keyword Coverage   — JD terms found in resume (synonym-aware)
  [25%] Skills Alignment   — Required vs preferred skills breakdown
  [20%] Experience Match   — YOE requirement vs candidate experience
  [10%] Education Match    — Degree requirement vs MS + relevant certs
  [10%] Title Relevance    — Job role alignment with target profile

Modeled after enterprise ATS platforms:
  Taleo · Workday · iCIMS · Greenhouse · Lever · SmartRecruiters

Composite score = weighted sum of all 5 dimensions.
Per-dimension breakdown shown in console + saved to CSV.
"""

import os, re, sys, math, json
import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(__file__))
from raghav_profile import PROFILE, EXPERIENCE, SKILLS, EDUCATION, TARGET_ROLES, COMMON_QA

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
RAW_CSV  = os.path.join(DATA_DIR, "raw_jobs.csv")
OUT_CSV  = os.path.join(DATA_DIR, "filtered_jobs.csv")

# ══════════════════════════════════════════════════════════════════
#  SCORING WEIGHTS  (must sum to 1.0)
# ══════════════════════════════════════════════════════════════════
W = {
    "keyword":    0.35,
    "skills":     0.25,
    "experience": 0.20,
    "education":  0.10,
    "title":      0.10,
}

# ══════════════════════════════════════════════════════════════════
#  CANDIDATE DATA  (pulled from raghav_profile.py)
# ══════════════════════════════════════════════════════════════════
CANDIDATE_YOE_ENGINEERING  = 1.5   # years data engineering (Knowvia + internship)
CANDIDATE_YOE_ANALYTICS    = 3.0   # years analytics (MISA + volunteer + internship)
CANDIDATE_YOE_TOTAL        = 3.0   # total relevant professional experience
CANDIDATE_EDUCATION_LEVEL  = "master"   # MS in Data Science & Analytics, FAU 2025

CANDIDATE_SKILLS_FLAT = [s.lower() for group in SKILLS.values() for s in group]

# ══════════════════════════════════════════════════════════════════
#  SYNONYM EXPANSION  (any of these terms count as the same keyword)
# ══════════════════════════════════════════════════════════════════
SYNONYMS: Dict[str, List[str]] = {
    "pyspark":              ["apache spark", "spark", "spark sql", "sparksql"],
    "apache spark":         ["pyspark", "spark", "spark sql", "sparksql"],
    "spark":                ["pyspark", "apache spark"],
    "machine learning":     ["ml", "ai/ml", "ai", "deep learning"],
    "ml":                   ["machine learning", "ai/ml", "artificial intelligence"],
    "etl":                  ["elt", "extract transform load", "data pipeline", "pipelines"],
    "elt":                  ["etl", "data pipeline"],
    "azure":                ["microsoft azure", "azure cloud", "ms azure"],
    "microsoft azure":      ["azure"],
    "aws":                  ["amazon web services", "amazon aws", "ec2", "s3"],
    "amazon web services":  ["aws"],
    "gcp":                  ["google cloud", "google cloud platform", "bigquery"],
    "google cloud":         ["gcp"],
    "sql":                  ["mysql", "postgresql", "tsql", "t-sql", "plsql", "pl/sql", "ansi sql"],
    "python":               ["python3", "python 3"],
    "power bi":             ["powerbi", "power bi desktop", "dax", "pbi"],
    "tableau":              ["tableau desktop", "tableau server", "data visualization"],
    "databricks":           ["delta lake", "delta lakehouse", "lakehouse"],
    "kubernetes":           ["k8s", "container orchestration"],
    "docker":               ["containerization", "containers", "dockerfile"],
    "airflow":              ["apache airflow", "workflow orchestration", "dag"],
    "kafka":                ["apache kafka", "event streaming", "message queue"],
    "dbt":                  ["data build tool", "dbt core", "dbt cloud"],
    "snowflake":            ["snowflake cloud", "snowflake data platform"],
    "redshift":             ["amazon redshift"],
    "bigquery":             ["google bigquery", "bq", "gcp analytics"],
    "ci/cd":                ["continuous integration", "devops", "github actions", "jenkins"],
    "git":                  ["github", "gitlab", "version control", "git flow"],
    "terraform":            ["infrastructure as code", "iac", "pulumi"],
    "rest":                 ["rest api", "restful", "api", "web services"],
    "pandas":               ["dataframe", "numpy"],
    "hadoop":               ["hdfs", "mapreduce", "hive", "hbase"],
    "data warehouse":       ["data warehousing", "dwh", "edw", "enterprise data warehouse"],
    "data lake":            ["data lakehouse", "lake", "adls", "s3"],
    "adf":                  ["azure data factory", "data factory"],
    "adls":                 ["azure data lake storage", "adls gen2"],
    "ssis":                 ["sql server integration services"],
    "no-code":              ["low-code", "no code"],
    "bi":                   ["business intelligence", "analytics", "reporting"],
    "nlp":                  ["natural language processing", "text mining"],
    "llm":                  ["large language model", "generative ai", "gpt", "openai"],
}

# ══════════════════════════════════════════════════════════════════
#  TITLE RELEVANCE SCORES  (0–100)
# ══════════════════════════════════════════════════════════════════
TITLE_MATCH: Dict[str, int] = {
    "data engineer":                 100,
    "senior data engineer":          100,
    "lead data engineer":            100,
    "principal data engineer":       100,
    "staff data engineer":           100,
    "cloud data engineer":           100,
    "azure data engineer":           100,
    "aws data engineer":             100,
    "gcp data engineer":             100,
    "big data engineer":             98,
    "data platform engineer":        98,
    "analytics engineer":            97,
    "etl developer":                 95,
    "etl engineer":                  95,
    "data infrastructure engineer":  95,
    "data pipeline engineer":        95,
    "data integration engineer":     94,
    "bi engineer":                   88,
    "bi developer":                  86,
    "business intelligence engineer":85,
    "data analyst":                  84,
    "senior data analyst":           84,
    "data scientist":                78,
    "machine learning engineer":     72,
    "ml engineer":                   72,
    "software engineer":             55,
    "backend engineer":              50,
    "full stack":                    45,
    "full-stack":                    45,
    "java":                          35,
    "frontend":                      30,
    "mobile":                        25,
}

# Education level hierarchy
EDU_LEVELS = {"high school": 1, "associate": 2, "bachelor": 3, "master": 4, "phd": 5, "doctorate": 5}
EDU_PATTERNS = {
    "phd":       r"\b(ph\.?d|doctorate|doctoral)\b",
    "master":    r"\b(master|m\.?s\.?|msc|m\.?eng|mba|graduate degree)\b",
    "bachelor":  r"\b(bachelor|b\.?s\.?|b\.?a\.?|b\.?eng|undergraduate)\b",
    "associate": r"\b(associate|a\.?s\.?|a\.?a\.?)\b",
}

# ══════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _expand(keyword: str) -> List[str]:
    """Return keyword + all its synonyms."""
    kw = keyword.lower()
    variants = {kw}
    for v in SYNONYMS.get(kw, []):
        variants.add(v.lower())
    # reverse: if any synonym maps back here
    for master, syns in SYNONYMS.items():
        if kw in [s.lower() for s in syns]:
            variants.add(master.lower())
    return list(variants)


def _in_text(keyword: str, text: str) -> bool:
    """Check if keyword or any synonym is present in text."""
    for variant in _expand(keyword):
        pattern = r"\b" + re.escape(variant) + r"\b"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _extract_jd_keywords(jd_text: str) -> List[str]:
    """
    Pull technology keywords from a JD.
    Covers tools, frameworks, cloud platforms, languages, and soft requirements.
    """
    candidates = set()
    jd_lower = jd_text.lower()

    # Tech term patterns
    tech_patterns = [
        r"\b(python|pyspark|spark|kafka|airflow|hadoop|hive|flink|dbt|terraform)\b",
        r"\b(sql|mysql|postgresql|t-sql|pl/sql|tsql|oracle|sqlite)\b",
        r"\b(azure|aws|gcp|google cloud|snowflake|databricks|redshift|bigquery)\b",
        r"\b(azure data factory|adls|adls gen2|adf|cosmos db|synapse|power bi|powerbi)\b",
        r"\b(tableau|looker|qlik|metabase|dax|power query)\b",
        r"\b(docker|kubernetes|k8s|helm|terraform|ansible|jenkins|github actions)\b",
        r"\b(pandas|numpy|scikit-learn|tensorflow|pytorch|keras|mlflow)\b",
        r"\b(java|scala|go|golang|rust|c\+\+|javascript|node\.?js|react)\b",
        r"\b(etl|elt|data pipeline|data warehouse|data lake|data lakehouse|data mart)\b",
        r"\b(rest api|restful|graphql|grpc|microservices|event-driven)\b",
        r"\b(git|github|gitlab|bitbucket|jira|confluence|agile|scrum)\b",
        r"\b(machine learning|deep learning|nlp|llm|generative ai|computer vision)\b",
        r"\b(ci/cd|devops|mlops|dataops|infrastructure as code|iac)\b",
    ]
    for pat in tech_patterns:
        for m in re.finditer(pat, jd_lower):
            candidates.add(m.group().strip())

    # NOTE: Do NOT extract generic capitalized words from the JD.
    # That approach pulls in job-posting boilerplate ("Preferred Qualifications",
    # "Job Summary", "Full-Time", "United States", "Bachelor", "Six Sigma", etc.)
    # and injects them into the resume's skills section as fake keywords.
    # The tech_patterns above already cover every real technical term we need.

    # Deduplicate: remove sub-terms already covered by longer terms
    final = []
    sorted_cands = sorted(candidates, key=len, reverse=True)
    seen_words = set()
    for term in sorted_cands:
        words = set(term.split())
        if not words.issubset(seen_words):
            final.append(term)
            seen_words.update(words)

    return list(set(final))[:80]  # cap at 80 keywords per JD


def _build_generic_resume_text() -> str:
    """Honest baseline: only always-include jobs + core skills."""
    parts = []
    for job in EXPERIENCE:
        if job.get("include_always"):
            parts.append(job["title"])
            parts.append(job["company"])
            parts.extend(job.get("tools", []))
            parts.extend(job.get("bullets", []))
    for group in SKILLS.values():
        parts.extend(group)
    return " ".join(parts).lower()


def _build_injectable_pool() -> str:
    """Full pool: all experience + all skills."""
    parts = []
    for job in EXPERIENCE:
        parts.append(job["title"])
        parts.append(job["company"])
        parts.extend(job.get("tools", []))
        parts.extend(job.get("bullets", []))
    for group in SKILLS.values():
        parts.extend(group)
    return " ".join(parts).lower()


# ══════════════════════════════════════════════════════════════════
#  DIMENSION SCORERS
# ══════════════════════════════════════════════════════════════════

def score_keyword(jd_keywords: List[str], resume_text: str) -> Tuple[float, int, int, List[str]]:
    """
    Dimension 1 — Keyword Coverage (35%)
    Returns (score_0_to_100, matched, total, missing_list)
    """
    if not jd_keywords:
        return 100.0, 0, 0, []
    matched = [kw for kw in jd_keywords if _in_text(kw, resume_text)]
    missing = [kw for kw in jd_keywords if not _in_text(kw, resume_text)]
    score   = len(matched) / len(jd_keywords) * 100
    return round(score, 1), len(matched), len(jd_keywords), missing


def score_skills(jd_text: str, resume_pool: str) -> Tuple[float, int, int, int, int]:
    """
    Dimension 2 — Skills Alignment (25%)
    Splits JD skills into Required vs Preferred, scores each group.
    Required skills count 2x vs preferred.
    Returns (score, req_matched, req_total, pref_matched, pref_total)
    """
    jd_lower = jd_text.lower()

    # Split JD into required vs preferred sections
    required_section  = ""
    preferred_section = ""
    for line in jd_lower.split("\n"):
        if any(w in line for w in ["required", "must have", "must-have", "minimum", "mandatory", "essential"]):
            required_section += " " + line
        elif any(w in line for w in ["preferred", "nice to have", "bonus", "plus", "advantage", "optional"]):
            preferred_section += " " + line
        else:
            required_section += " " + line  # default: required

    # Extract skill tokens (tech terms from each section)
    def _skills_in(text):
        found = set()
        for skill in CANDIDATE_SKILLS_FLAT:
            if len(skill) >= 3 and _in_text(skill, text):
                found.add(skill)
        # Also extract from SYNONYMS keys
        for key in SYNONYMS:
            if _in_text(key, text):
                found.add(key)
        return found

    req_in_jd   = _skills_in(required_section)
    pref_in_jd  = _skills_in(preferred_section) - req_in_jd

    req_matched  = sum(1 for s in req_in_jd  if _in_text(s, resume_pool))
    pref_matched = sum(1 for s in pref_in_jd if _in_text(s, resume_pool))

    req_total  = max(len(req_in_jd),  1)
    pref_total = max(len(pref_in_jd), 1)

    # Weighted: required 2x, preferred 1x
    numerator   = (req_matched * 2) + pref_matched
    denominator = (req_total   * 2) + pref_total
    score = numerator / denominator * 100 if denominator > 0 else 100.0

    return round(score, 1), req_matched, req_total, pref_matched, pref_total


def score_experience(jd_text: str) -> Tuple[float, float, str]:
    """
    Dimension 3 — Experience Match (20%)
    Extracts YOE requirement from JD, compares to candidate's experience.
    Returns (score, required_yoe, match_label)
    """
    patterns = [
        r"(\d+)\+?\s*(?:–|-|to)\s*(\d+)\s*(?:years?|yrs?)",
        r"(\d+)\+\s*(?:years?|yrs?)",
        r"(?:minimum|at least|min\.?)\s+(\d+)\s*(?:years?|yrs?)",
        r"(\d+)\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp\.?)",
        r"experience[:\s]+(\d+)\s*(?:\+)?\s*(?:years?|yrs?)",
    ]
    jd_lower = jd_text.lower()
    required_yoe = 0.0

    for pat in patterns:
        m = re.search(pat, jd_lower)
        if m:
            groups = [float(g) for g in m.groups() if g]
            required_yoe = max(groups)
            break

    if required_yoe == 0:
        return 100.0, 0, "No YOE stated"

    # Use analytics YOE for analyst roles, engineering YOE otherwise
    candidate_yoe = CANDIDATE_YOE_TOTAL

    if required_yoe <= 0:
        score, label = 100.0, "No requirement"
    elif candidate_yoe >= required_yoe:
        score, label = 100.0, "Meets requirement"
    elif candidate_yoe >= required_yoe * 0.75:
        score, label = 80.0, "Close to requirement"
    elif candidate_yoe >= required_yoe * 0.50:
        score, label = 60.0, "Partial match"
    else:
        # Proportional falloff
        score = max(30.0, (candidate_yoe / required_yoe) * 100)
        label = "Below requirement"

    return round(score, 1), required_yoe, label


def score_education(jd_text: str) -> Tuple[float, str, str]:
    """
    Dimension 4 — Education Match (10%)
    Extracts required degree from JD, compares to candidate's MS degree.
    Returns (score, required_level, match_label)
    """
    jd_lower = jd_text.lower()
    required_level = "none"

    for level, pat in EDU_PATTERNS.items():
        if re.search(pat, jd_lower):
            required_level = level
            break

    candidate_level = CANDIDATE_EDUCATION_LEVEL  # "master"

    cand_rank = EDU_LEVELS.get(candidate_level, 3)
    req_rank  = EDU_LEVELS.get(required_level,  0)

    if req_rank == 0:
        return 100.0, "None stated", "No requirement"
    elif cand_rank >= req_rank:
        return 100.0, required_level.title(), "Exceeds/Meets"
    elif cand_rank == req_rank - 1:
        return 80.0,  required_level.title(), "One level below"
    else:
        return 60.0,  required_level.title(), "Below requirement"


def score_title(job_title: str) -> Tuple[float, str]:
    """
    Dimension 5 — Title Relevance (10%)
    Matches job title against known role alignment scores.
    Returns (score, matched_key)
    """
    title_lower = job_title.lower()
    best_score  = 40  # baseline for any tech role
    best_key    = "other"

    for key, val in TITLE_MATCH.items():
        if key in title_lower:
            if val > best_score:
                best_score = val
                best_key   = key

    return float(best_score), best_key


# ══════════════════════════════════════════════════════════════════
#  COMPOSITE SCORER
# ══════════════════════════════════════════════════════════════════

def compute_ats_score(
    job_title: str,
    jd_text:   str,
    resume_text: str,
    injectable:  str,
) -> dict:
    """
    Full multi-dimensional ATS score.
    Returns dict with all dimension scores + weighted composite.
    Two passes: BEFORE (generic resume) and AFTER (full injectable pool).
    """
    jd_keywords = _extract_jd_keywords(jd_text)

    # ── BEFORE (generic resume only) ──────────────────────────────
    kw_b,  kw_match_b,  kw_total_b,  kw_miss_b  = score_keyword(jd_keywords, resume_text)
    sk_b,  sr_m_b, sr_t_b, sp_m_b, sp_t_b       = score_skills(jd_text, resume_text)
    exp_b, yoe_req, exp_label                     = score_experience(jd_text)
    edu_b, edu_req, edu_label                     = score_education(jd_text)
    ttl_b, ttl_key                                = score_title(job_title)

    before = (
        kw_b  * W["keyword"]  +
        sk_b  * W["skills"]   +
        exp_b * W["experience"] +
        edu_b * W["education"] +
        ttl_b * W["title"]
    )

    # ── AFTER (full injectable pool) ──────────────────────────────
    kw_a,  kw_match_a, kw_total_a, kw_miss_a     = score_keyword(jd_keywords, injectable)
    sk_a,  sr_m_a, sr_t_a, sp_m_a, sp_t_a        = score_skills(jd_text, injectable)
    # Experience, Education, Title don't change between before/after
    exp_a = exp_b
    edu_a = edu_b
    ttl_a = ttl_b

    after = (
        kw_a  * W["keyword"]  +
        sk_a  * W["skills"]   +
        exp_a * W["experience"] +
        edu_a * W["education"] +
        ttl_a * W["title"]
    )

    return {
        # Composite
        "initial_score":    round(before, 1),
        "optimized_score":  round(after,  1),
        # Dimension — BEFORE
        "kw_before":    kw_b,  "kw_matched_b":  kw_match_b,  "kw_total": kw_total_b,
        "sk_before":    sk_b,  "sk_req_matched_b": sr_m_b,   "sk_req_total": sr_t_b,
        "exp_score":    exp_b, "yoe_required": yoe_req,       "exp_label": exp_label,
        "edu_score":    edu_b, "edu_required": edu_req,       "edu_label": edu_label,
        "ttl_score":    ttl_b, "ttl_matched": ttl_key,
        # Dimension — AFTER
        "kw_after":     kw_a,  "kw_matched_a":  kw_match_a,  "kw_missing": kw_miss_a,
        "sk_after":     sk_a,  "sk_req_matched_a": sr_m_a,   "sk_pref_matched_a": sp_m_a,
        # Keywords
        "jd_keywords":      jd_keywords,
        "coverage_gap":     kw_miss_a,
        "injectable_keywords": [k for k in kw_miss_a if _in_text(k, injectable)],
    }


def print_ats_breakdown(title: str, company: str, scores: dict):
    """Print a per-dimension ATS score table to console."""
    W_pct = {k: int(v * 100) for k, v in W.items()}
    print(f"\n  ┌─ ATS BREAKDOWN  {title[:30]} @ {company[:20]}")
    print(f"  │  {'Dimension':<22}  {'Before':>6}  {'After':>6}  {'Weight':>6}  {'Pts(After)':>10}")
    print(f"  │  {'─'*22}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*10}")

    dims = [
        ("Keyword Coverage",  scores["kw_before"],  scores["kw_after"],  W_pct["keyword"]),
        ("Skills Alignment",  scores["sk_before"],  scores["sk_after"],  W_pct["skills"]),
        ("Experience Match",  scores["exp_score"],  scores["exp_score"], W_pct["experience"]),
        ("Education Match",   scores["edu_score"],  scores["edu_score"], W_pct["education"]),
        ("Title Relevance",   scores["ttl_score"],  scores["ttl_score"], W_pct["title"]),
    ]
    for name, b, a, wt in dims:
        pts = a * wt / 100
        flag = "↑" if a > b else ("✓" if a == b else "↓")
        print(f"  │  {name:<22}  {b:>5.1f}%  {a:>5.1f}%  [{wt:>2}%]  {pts:>8.1f}pts  {flag}")

    print(f"  │  {'─'*22}  {'─'*6}  {'─'*6}  {'─'*6}  {'─'*10}")

    isc = scores["initial_score"]
    osc = scores["optimized_score"]
    delta = osc - isc
    rating = "🟢 GREAT" if osc >= 85 else ("🟡 GOOD" if osc >= 70 else "🔴 NEEDS WORK")
    print(f"  │  {'COMPOSITE SCORE':<22}  {isc:>5.1f}%  {osc:>5.1f}%  [ — ]  {osc:>8.1f}pts  {rating}")
    print(f"  │  YOE required: {scores['yoe_required']}yr  ({scores['exp_label']})  |  "
          f"Education: {scores['edu_required']} ({scores['edu_label']})")
    print(f"  │  Keywords: {scores['kw_matched_a']}/{scores['kw_total']} matched  |  "
          f"Missing after tailoring: {len(scores['kw_missing'])}")
    print(f"  └─ Δ = {delta:+.1f}%  (injectable keywords add {delta:.1f} pts)")


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═"*66)
    print("  JOB PIPELINE — PRO ATS Engine v4")
    print("  5-Dimension Scoring  |  Keyword + Skills + YOE + Education + Title")
    print("═"*66)

    if not os.path.exists(RAW_CSV):
        print(f"  ❌  raw_jobs.csv not found at {RAW_CSV}")
        return None

    raw = pd.read_csv(RAW_CSV)
    print(f"  ✓  Loaded {len(raw)} jobs from raw_jobs.csv")

    generic_text = _build_generic_resume_text()
    injectable   = _build_injectable_pool()

    rows = []
    for _, row in raw.iterrows():
        title   = str(row.get("job_title", row.get("title", "Unknown")))
        company = str(row.get("employer_name", row.get("company", "Unknown")))
        jd_text = str(row.get("job_description", row.get("jd_full", "")))
        salary  = str(row.get("job_salary_period", row.get("salary", "Not listed")))
        location= str(row.get("job_city", row.get("location", "Remote"))) + " " + str(row.get("job_state", ""))
        link    = str(row.get("job_apply_link", row.get("apply_link", "")))
        posted  = str(row.get("job_posted_at_datetime_utc", row.get("posted", "")))[:10]
        job_id  = str(row.get("job_id", ""))

        scores = compute_ats_score(title, jd_text, generic_text, injectable)
        print_ats_breakdown(title, company, scores)

        rows.append({
            "job_id":       job_id,
            "title":        title,
            "company":      company,
            "location":     location.strip(),
            "salary":       salary,
            "apply_link":   link,
            "jd_full":      jd_text,
            "jd_preview":   jd_text[:300],
            "posted":       posted,
            # Composite scores
            "initial_score":    scores["initial_score"],
            "optimized_score":  scores["optimized_score"],
            "actual_initial_score":    0.0,  # filled by resume_builder
            "actual_optimized_score":  0.0,  # filled by resume_builder
            # Dimension scores
            "dim_keyword_before":  scores["kw_before"],
            "dim_keyword_after":   scores["kw_after"],
            "dim_skills_before":   scores["sk_before"],
            "dim_skills_after":    scores["sk_after"],
            "dim_experience":      scores["exp_score"],
            "dim_education":       scores["edu_score"],
            "dim_title":           scores["ttl_score"],
            "yoe_required":        scores["yoe_required"],
            "edu_required":        scores["edu_required"],
            "exp_label":           scores["exp_label"],
            # ATS detail
            "kw_matched":     scores["kw_matched_a"],
            "kw_total":       scores["kw_total"],
            "role_relevance": scores["ttl_score"],
            "jd_keywords":    json.dumps(scores["jd_keywords"]),
            "coverage_gap":   json.dumps(scores["coverage_gap"]),
            "injectable_keywords": json.dumps(scores["injectable_keywords"]),
        })

    df = pd.DataFrame(rows)

    # Summary table
    print("\n" + "═"*80)
    print("  ATS SCORE SUMMARY  —  BEFORE vs AFTER  (5-Dimension Composite)")
    print("═"*80)
    print(f"  {'Job Title':<30}  {'Company':<18}  {'Before':>6}  {'After':>6}  {'Delta':>5}  {'YOE Req':>7}")
    print(f"  {'─'*30}  {'─'*18}  {'─'*6}  {'─'*6}  {'─'*5}  {'─'*7}")
    for _, r in df.iterrows():
        delta = r["optimized_score"] - r["initial_score"]
        print(f"  {str(r['title'])[:30]:<30}  {str(r['company'])[:18]:<18}  "
              f"{r['initial_score']:>5.1f}%  {r['optimized_score']:>5.1f}%  "
              f"{delta:>+5.1f}%  {str(r['yoe_required'])+'yr':>7}")
    avg_b = df["initial_score"].mean()
    avg_a = df["optimized_score"].mean()
    print(f"\n  {'AVERAGE':<30}  {'─'*18}  {avg_b:>5.1f}%  {avg_a:>5.1f}%  {avg_a-avg_b:>+5.1f}%")
    print("═"*80 + "\n")

    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"  ✓  Saved {len(df)} scored jobs → {OUT_CSV}\n")
    return df


def parse_jd(jd_text: str, job_title: str) -> dict:
    """
    Public API used by master_run.py — parse a JD and return ATS scores + keywords.
    Wraps compute_ats_score() with sensible defaults.

    Returns dict with:
      jd_keywords          : list[str]  — tech terms found in JD
      injectable_keywords  : list[str]  — candidate has these, JD wants them
      initial_score        : float      — ATS score with generic resume
      optimized_score      : float      — ATS score with full injectable pool
      + all dimension scores from compute_ats_score()
    """
    if not jd_text or str(jd_text).strip().lower() in ("", "nan", "none"):
        return {
            "jd_keywords":         [],
            "injectable_keywords": [],
            "initial_score":       0.0,
            "optimized_score":     0.0,
        }

    generic_text = _build_generic_resume_text()
    injectable   = _build_injectable_pool()

    try:
        scores = compute_ats_score(job_title or "Data Role", jd_text, generic_text, injectable)
    except Exception as e:
        return {
            "jd_keywords":         [],
            "injectable_keywords": [],
            "initial_score":       0.0,
            "optimized_score":     0.0,
            "error":               str(e),
        }

    return {
        "jd_keywords":         scores.get("jd_keywords",         []),
        "injectable_keywords": scores.get("injectable_keywords", []),
        "initial_score":       scores.get("initial_score",       0.0),
        "optimized_score":     scores.get("optimized_score",     0.0),
        # Dimension detail for tracker
        "dim_keyword_before":  scores.get("kw_before",  0.0),
        "dim_keyword_after":   scores.get("kw_after",   0.0),
        "dim_skills_before":   scores.get("sk_before",  0.0),
        "dim_skills_after":    scores.get("sk_after",   0.0),
        "dim_experience":      scores.get("exp_score",  0.0),
        "dim_education":       scores.get("edu_score",  0.0),
        "dim_title":           scores.get("ttl_score",  0.0),
        "yoe_required":        scores.get("yoe_required", 0),
        "coverage_gap":        scores.get("coverage_gap", []),
    }


if __name__ == "__main__":
    main()
