# =============================================================================
# CONFIG.PY — Single source of truth for the entire job pipeline
# Edit this file to change any behaviour across the whole system.
# =============================================================================

from pathlib import Path
import os

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR        = Path.home() / "job_pipeline"
RESUMES_DIR     = BASE_DIR / "resumes"
COVER_DIR       = BASE_DIR / "cover_letters"
DATA_DIR        = BASE_DIR / "data"
LOG_FILE        = DATA_DIR / "apply_log.json"
TRACKER_FILE    = DATA_DIR / "applications.xlsx"
SESSION_LI      = Path.home() / ".linkedin_session"
SESSION_IN      = Path.home() / ".indeed_session"
SESSION_WD      = BASE_DIR / ".workday_session"

# ── API & Model ────────────────────────────────────────────────────────────────
def get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        env = BASE_DIR / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key

CLAUDE_MODEL_FAST   = "claude-haiku-4-5-20251001"   # form filling, bullet rewriting
CLAUDE_MODEL_SMART  = "claude-sonnet-4-6"            # fit scoring, cover letters

# ── Fit Gate ───────────────────────────────────────────────────────────────────
FIT_THRESHOLD   = 62    # minimum Claude score (%) to apply. Lowered to 62 to catch more jobs.

# ── Apply Limits ───────────────────────────────────────────────────────────────
MAX_APPLIES_PER_RUN = 150   # safety cap — 50 per platform × 3 runs/day
APPLY_DELAY_SEC     = 2     # seconds between applications (be kind to LinkedIn)
FORM_MAX_STEPS      = 30    # max form steps before giving up
STUCK_THRESHOLD     = 15    # same button clicked this many times → declare stuck (LinkedIn can have many Review steps)

# ── Target Roles ───────────────────────────────────────────────────────────────
TARGET_ROLES = [
    "Data Engineer",
    "Data Analyst",
    "Data Scientist",
    "ML Engineer",
    "Analytics Engineer",
    "BI Analyst",
    "ETL Developer",
    "AI Engineer",
]

# ── Senior filter — NEVER apply to these ──────────────────────────────────────
SENIOR_WORDS = {
    "senior", "sr.", "sr ", "lead", "principal", "staff",
    "director", "manager", "head of", "vp ", "vice president",
    "chief", "architect", "distinguished", "fellow",
}

# ── Blocked companies — skip entirely, don't even attempt ─────────────────────
# Add any company name or Workday subdomain key here to permanently skip it.
# Matching is case-insensitive and partial (e.g. "airbus" matches "Airbus Group").
BLOCKED_COMPANIES = {
    "airbus",           # ag.wd3              — account locked
    "hcsc",             # hcsc.wd1            — account locked
    "maersk",           # maersk.wd3          — no Apply button found
    "philips",          # philips.wd3         — no Apply button found
    "amesconstruction", # amesconstruction    — account locked from retries
    "nc",               # nc.wd108            — no Apply button found
    "wvumedicine",      # wvumedicine.wd1     — security question loop
    "generalmotors",    # generalmotors.wd5   — French portal, no Apply button
}

# ── LinkedIn search queries ────────────────────────────────────────────────────
LINKEDIN_QUERIES = [
    # Data Engineering
    "Data Engineer Entry Level",
    "Junior Data Engineer",
    "Associate Data Engineer",
    "Data Engineer Python",
    "Data Engineer SQL",
    "PySpark Data Engineer",
    "Azure Data Engineer",
    "AWS Data Engineer",
    "GCP Data Engineer",
    "ETL Developer Entry Level",
    "Data Pipeline Engineer",
    "Cloud Data Engineer",
    "Analytics Engineer Entry Level",
    # Data Analysis
    "Data Analyst Entry Level",
    "Junior Data Analyst",
    "Associate Data Analyst",
    "Business Intelligence Analyst",
    "BI Analyst Entry Level",
    "BI Developer Entry Level",
    "Reporting Analyst Entry Level",
    "Business Analyst Data",
    "SQL Data Analyst",
    "Python Data Analyst",
    "Tableau Data Analyst",
    "Power BI Analyst",
    # Data Science / ML
    "Data Scientist Entry Level",
    "Junior Data Scientist",
    "ML Engineer Entry Level",
    "Machine Learning Engineer",
    "AI Engineer Entry Level",
    "Applied Scientist Entry Level",
    "NLP Engineer Entry Level",
    # Broader roles
    "Database Analyst",
    "Quantitative Analyst Entry Level",
    "Data Operations Analyst",
    "Data Platform Engineer",
    "Decision Scientist",
    "Product Analyst Data",
    "Marketing Data Analyst",
    "Financial Data Analyst",
]

# ── Indeed-specific search queries (broader than LinkedIn) ─────────────────────
INDEED_QUERIES = [
    # Data Engineering
    "Data Engineer",
    "Junior Data Engineer",
    "Data Engineer Python SQL",
    "ETL Developer",
    "Data Pipeline Engineer",
    "Analytics Engineer",
    "PySpark Engineer",
    "Azure Data Engineer",
    "AWS Data Engineer",
    "Databricks Engineer",
    "Snowflake Engineer",
    "dbt Analytics Engineer",
    # Data Analysis
    "Data Analyst",
    "Junior Data Analyst",
    "Business Intelligence Analyst",
    "BI Analyst",
    "SQL Analyst",
    "Python Analyst",
    "Reporting Analyst",
    "Business Analyst Data Analytics",
    "Tableau Developer",
    "Power BI Developer",
    # Data Science / ML
    "Data Scientist",
    "Junior Data Scientist",
    "Machine Learning Engineer",
    "ML Engineer",
    "AI Engineer",
    "NLP Engineer",
    "Applied Machine Learning",
    # Broad
    "Data Operations",
    "Data Platform Engineer",
    "Database Developer",
    "Quantitative Analyst",
    "Product Analytics",
    "Marketing Analytics",
    "Financial Analyst Data",
]

# ── Resume builder settings ────────────────────────────────────────────────────
ATS_TARGET_SCORE    = 98    # target keyword coverage %
RESUME_FONT         = "Calibri"
RESUME_MAX_BULLETS  = 7     # max bullets for primary job
RESUME_SIDE_BULLETS = 4     # max bullets for secondary jobs

# ── Candidate basics (non-sensitive — sensitive data stays in raghav_profile.py)
CANDIDATE_NAME      = "Raghavendra Karanam"
CANDIDATE_LOCATION  = "Delray Beach, FL"
CANDIDATE_EMAIL     = "raghavendrakaranam30@gmail.com"
CANDIDATE_PHONE     = "5618160256"
WORK_AUTH           = "F-1 OPT/STEM OPT — authorized, no sponsorship needed"
DEGREE              = "M.S. Data Science & Analytics, Florida Atlantic University (2025)"
YEARS_EXP_TOTAL     = "3+"
SALARY_EXPECTED     = "70000"

# ── Skill experience years — used in form filling ──────────────────────────────
# ── Workday search queries (used by workday_apply_now.py Google search) ───────
WORKDAY_QUERIES = [
    "Data Engineer entry level",
    "Data Analyst entry level",
    "Data Scientist entry level",
    "ML Engineer entry level",
    "Analytics Engineer",
    "Business Intelligence Analyst",
    "ETL Developer",
    "Machine Learning Engineer",
    "AI Engineer",
]

# ── Skill experience years — used in form filling ──────────────────────────────
SKILL_YEARS = {
    "python":           "4",
    "sql":              "4",
    "pandas":           "4",
    "numpy":            "4",
    "git":              "4",
    "data engineering": "3",
    "data analysis":    "3",
    "analytics":        "3",
    "etl":              "3",
    "postgresql":       "3",
    "mysql":            "3",
    "scikit-learn":     "3",
    "machine learning": "2",
    "deep learning":    "2",
    "nlp":              "2",
    "aws":              "2",
    "azure":            "2",
    "gcp":              "2",
    "cloud":            "2",
    "spark":            "2",
    "pyspark":          "2",
    "kafka":            "2",
    "airflow":          "2",
    "dbt":              "2",
    "snowflake":        "2",
    "databricks":       "2",
    "power bi":         "2",
    "tableau":          "2",
    "docker":           "2",
    "tensorflow":       "2",
    "pytorch":          "2",
    "rest api":         "3",
    "fastapi":          "2",
    "mongodb":          "2",
    "kubernetes":       "1",
}
