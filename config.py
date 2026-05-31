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
FIT_THRESHOLD   = 65    # minimum Claude score (%) to apply. Raise to 70+ for quality.

# ── Apply Limits ───────────────────────────────────────────────────────────────
MAX_APPLIES_PER_RUN = 20    # safety cap — never apply to more than this in one run
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

# ── LinkedIn search queries ────────────────────────────────────────────────────
LINKEDIN_QUERIES = [
    "Data Engineer Entry Level",
    "Junior Data Engineer",
    "Data Analyst Entry Level",
    "Data Scientist Entry Level",
    "Analytics Engineer Entry Level",
    "Business Intelligence Analyst",
    "ETL Developer Entry Level",
    "ML Engineer Entry Level",
    "AI Engineer Entry Level",
    "BI Developer Entry Level",
    "PySpark Data Engineer",
    "Azure Data Engineer",
]

# ── Resume builder settings ────────────────────────────────────────────────────
ATS_TARGET_SCORE    = 98    # target keyword coverage %
RESUME_FONT         = "Calibri"
RESUME_MAX_BULLETS  = 7     # max bullets for primary job
RESUME_SIDE_BULLETS = 4     # max bullets for secondary jobs

# ── Candidate basics (non-sensitive — sensitive data stays in raghav_profile.py)
CANDIDATE_NAME      = "Raghavendra Karanam"
CANDIDATE_LOCATION  = "Boca Raton, FL"
CANDIDATE_EMAIL     = "raghavendrakaranam30@gmail.com"
CANDIDATE_PHONE     = "7038529618"
WORK_AUTH           = "F-1 OPT/STEM OPT — authorized, no sponsorship needed"
DEGREE              = "M.S. Data Science & Analytics, Florida Atlantic University (2025)"
YEARS_EXP_TOTAL     = "3+"
SALARY_EXPECTED     = "75000"

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
