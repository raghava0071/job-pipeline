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
# LinkedIn is 80% — only 50 slots/day, every one must count.
# Indeed is 72% — bigger pool, can be slightly looser, but no junk.
FIT_THRESHOLD          = 72   # Indeed/Workday minimum Claude score (%)
LINKEDIN_FIT_THRESHOLD = 80   # LinkedIn minimum — 50/day hard cap

# ── Apply Limits ───────────────────────────────────────────────────────────────
MAX_APPLIES_PER_RUN    = 200  # total cap per run across all platforms
LINKEDIN_DAILY_LIMIT   = 50   # hard LinkedIn cap (platform limit)
INDEED_DAILY_LIMIT     = 150  # target Indeed applications per day
APPLY_DELAY_SEC      = 2    # seconds between applications
FORM_MAX_STEPS       = 30   # max form steps before giving up
STUCK_THRESHOLD      = 15   # same button clicked this many times → declare stuck

# ── LinkedIn speed settings ────────────────────────────────────────────────────
# LinkedIn has no robot/CAPTCHA checks — run faster than Indeed.
LINKEDIN_STEP_WAIT_SEC   = 0.8   # wait after each non-submit form step (was 1.0)
LINKEDIN_SUBMIT_WAIT_SEC = 2.5   # wait after Submit/Done click (was 3.0)
LINKEDIN_SEARCH_DELAY_SEC = 2    # between search queries on LinkedIn

# ── Fake job detection — checked BEFORE scoring (zero API cost) ────────────────
# Jobs matching any of these are silently skipped and don't count against the limit.
# LAYER 1: Title-level blocks — non-target role types or spam phrases
FAKE_JOB_TITLE_WORDS = {
    # Spam / urgency phrases
    "urgently hiring", "immediate opening", "multiple openings",
    "great opportunity", "exciting opportunity", "work from home - no experience",
    "no experience necessary", "no experience needed",
    # Non-target roles — completely off-profile (even if they say "data")
    "data entry",           # data entry ≠ data engineering/analytics
    "typist", "clerical", "copy typ",
    "virtual assistant", "administrative assistant",
    "transcription", "data transcrib",
    "content moderator", "content moderation",
    "social media manager", "social media coordinator",
    "customer service", "customer support",
    "sales representative", "sales associate",
    "account manager",      # not a tech/data role
    "dispatcher", "scheduler",
    "project engineer",     # civil/construction — not data
    "reservoir engineer",   # oil & gas
    "pipeline engineer",    # oil & gas / civil
    "mechanical engineer",  # not data
    "process engineer",     # not data
    "field engineer",       # not data
    "quality control",      # not data
    "supply chain",         # not data
    "procurement",
    "marketing coordinator", "marketing manager",
    "hr coordinator", "human resources",
    "legal assistant", "paralegal",
    "financial advisor", "loan officer", "insurance agent",
    "nurse", "therapist", "counselor", "social worker",
    "teacher", "tutor", "instructor",
}

# ── Title typo signals — spam postings routinely misspell role names ──────────
# These exact substrings in the job title (lowercased) mark it as bot-generated.
FAKE_JOB_TITLE_TYPOS = {
    "data analys ",         # "Data Analys" (missing trailing 't')
    "data analys(",         # same, no space after
    " analys ",             # standalone misspelling in any title
    "data enginer",         # "enginer" instead of "engineer"
    "data entr ",           # "Data Entr Analyst" — truncated
    "data anlayst",         # transposed letters
    "data analsyt",         # transposed letters
    "data entery",          # "entery" instead of "entry"
    "databrick ",           # "Databrick Engineer" — missing trailing 's'
    "junior data analys",   # very common spam pattern
    "fresher data",         # India-origin posting ("Fresher Data Analyst")
    "us it recruiter",      # recruiter spam
    "bench sales",          # visa/bench-sales body shop
    "cloud enginer",        # another engineer typo
}

# LAYER 2: Company-name blocks
FAKE_JOB_COMPANY_WORDS = {
    # ── Generic staffing mills that post ghost/aggregated jobs ──────────────────
    "staffing solutions", "staffing group", "staffing inc", "staffing llc",
    "recruiting solutions", "recruiting group", "talent solutions",
    "it staffing", "tech staffing", "global staffing", "us staffing",
    "placement services", "manpower", "adecco", "randstad", "kelly services",
    "spherion", "aerotek", "apex group", "teksystems", "insight global",
    # Mid-tier body-shops / bench-sales firms from actual apply log
    "smart it frame",       # body shop / C2C mill
    "abacus service",       # staffing body shop
    "tpi global",           # offshore staffing
    "proven recruiting",    # staffing agency
    "visionaire partners",  # staffing
    "jobgether",            # job aggregator (re-posts others' jobs — not the actual employer)
    "jobright",             # AI job aggregator — not the real hiring company
    "fortray",              # foreign staffing / fake US presence
    "agl resources",        # energy utility posting fake analyst roles
    # ── Non-US companies masquerading as US tech employers ─────────────────────
    "monster gulf",         # Middle East job board — not a US employer
    "jobs in united states",# spam company name pattern
    "jobs in europe",       # spam
    "joham movers",         # international moving company
    "barca eventos",        # Brazilian events company
    "png n0",               # Papua New Guinea SME
    # ── Non-tech industries that occasionally post "data" jobs ─────────────────
    "movers limited",       # moving/logistics
    "marine offshore",      # offshore maritime
    "swiss marine",         # maritime
    "eventos e turismo",    # events & tourism (Portuguese)
    "eventos turismo",
    "height governance",
    "altura governance",    # fake governance body
    "meta globals",         # fake Meta clone
    "chill & play",         # entertainment/leisure — not a tech company
    "chill and play",
    "femme circle",         # non-tech lifestyle brand
    "psiluencer",           # influencer platform — not a tech employer
    "for you agency",       # talent/influencer agency
    "eyestem research",     # biotech — off-domain
    "rahmah academy",       # Islamic education institute
    "research excellence",  # often used by fake academic posting farms
    # ── Non-US / international organizations ───────────────────────────────────
    "rotary club", "rotaract",          # service clubs
    "lions club", "kiwanis",
    "journal of ", "studies journal",   # academic journals
    "crop sciences", "crop science",    # agriculture
    "ghostwriting", "ghost writing",    # content mills
    "servicios de salud",               # Spanish health services
    "estudios avanzados",               # Spanish/LatAm institutes
    "english and business",             # language schools
    "language school", "language academy",
    "indie games", "game studio",       # game studios using spam postings
    "últimas noticias", "noticias",     # Spanish newspapers
    "soluciones", "servicios",          # Spanish-language company signals
    "associação", "associacion",        # Portuguese/Spanish associations
    "fondazione", "fundação",           # Italian/Portuguese foundations
    "conseil régional",                 # French regional council
    "groupe conseil",                   # French consulting group
    # ── Commonwealth/African company registration patterns ─────────────────────
    # Companies registered as "XYZ Limited" or "XYZ Ltd" are typically UK,
    # Nigeria, Ghana, Kenya — not US-based tech employers.
    # (Checked separately via FAKE_COMPANY_SUFFIX_WORDS below)
}

# Suspicious company name suffixes — Commonwealth/African registration patterns
# These are checked against the LAST word(s) of the company name.
FAKE_COMPANY_SUFFIX_WORDS = {
    "limited",   # "JOHAM MOVERS LIMITED" — UK/Nigeria pattern
    "ltd",       # same
    "sme ltd",   # "PNG N0.1 SME LTD" — Asia-Pacific SME
    "plc",       # UK public limited company
    "pty ltd",   # Australian/South African
    "pvt ltd",   # Indian private limited
    "pvt. ltd",
    "private limited",
    "nig. ltd",  # Nigeria
}

# Suspicious PATTERNS in company names (regex-style keywords)
# If ANY of these appear anywhere in the company name, flag it.
FAKE_COMPANY_NAME_PATTERNS = {
    "n0.",       # "N0.1" style — Asia-Pacific numbering
    " sme ",     # Small Medium Enterprise designation
    "xxxxxxxxxx",# placeholder company names
    " nig ",     # Nigeria abbreviation
    " pty ",     # Australian/South African
    "ventures llp",
    "consortium",# often used by fake multi-company aggregators
}

# ── International / non-English description signals ───────────────────────────
# LAYER 3: Description-level blocks
FAKE_JOB_DESC_INTL_SIGNALS = {
    # Spanish-language postings — no real US hiring process
    "solo candidatos locales",
    "candidatos locales",
    "ubicación:",
    "postular aquí",
    "enviar cv",
    "aplicar aquí",
    "somente candidatos",           # Portuguese
    "envie seu currículo",          # Portuguese
    "curriculum vitae",             # formal CV language — non-US job market
    # India-origin body-shop / offshore staffing signals
    "c2c only", "c2c preferred", "corp to corp only", "corp-to-corp",
    "no h1b", "h1b transfer", "h4 ead", "opt cpt",
    "bench candidates", "available on bench", "resources on bench",
    "immediate joiners only", "immediate joiner",
    "notice period:", "current ctc", "expected ctc",
    "please share your resume", "share your profile",
    "urgent requirement", "urgently required", "asap requirement",
    "looking for resources", "need consultants",
    "w2 only", "position is for w2", "w2 consultant",
    # Non-US contact patterns
    "whatsapp us", "reach us on whatsapp",
    "apply on telegram", "message us on telegram",
    # African / Middle Eastern job market signals
    "apply via email to", "send cv to", "drop your cv",
    "candidates in nigeria", "candidates in kenya", "candidates in ghana",
    "middle east candidates", "gulf candidates",
    # Generic credential-harvesting
    "bank verification number", "bvn",
    "national id number", "national identification",
}

# LAYER 4: Blatant fraud signals in description
FAKE_JOB_DESC_SIGNALS = {
    # Contact/payment red flags
    "whatsapp", "telegram", "wire transfer", "gift card",
    "ssn required", "social security number required", "bank account number",
    # Earnings bait
    "make $500", "earn $500", "make $1000", "earn $1000",
    "per hour from home", "per day from home",
    # Credential harvesting
    "send your resume to", "email resume to", "text resume to",
}

# LAYER 5: Non-tech location signals — if the job location field contains these,
# it's not a US job regardless of what the posting says.
FAKE_JOB_LOCATION_SIGNALS = {
    "nigeria", "ghana", "kenya", "south africa", "pakistan",
    "india", "bangladesh", "sri lanka", "philippines",
    "dubai", "abu dhabi", "riyadh", "doha", "kuwait",
    "united kingdom", "united arab emirates",
    "canada",       # Raghav is on F-1 OPT — US work authorization only
    "australia",
}

# Skip jobs with this many or more applicants (stale/fake bait postings)
LINKEDIN_MAX_APPLICANTS = 400

# ── Company whitelist — known legitimate employers, skip ALL fake-job checks ──
# These are real companies. Pipeline goes straight to fit scoring for them.
COMPANY_WHITELIST = {
    # Big tech
    "google", "amazon", "microsoft", "apple", "meta", "netflix",
    "salesforce", "oracle", "ibm", "intel", "nvidia", "adobe",
    # Finance / consulting
    "jpmorgan", "j.p. morgan", "goldman sachs", "morgan stanley",
    "deloitte", "ernst & young", "ey", "pwc", "kpmg", "accenture",
    "capital one", "charles schwab", "fidelity", "bloomberg",
    # Healthcare / gov / defense
    "booz allen", "leidos", "saic", "caci", "lmco", "lockheed",
    "northrop grumman", "raytheon", "general dynamics",
    "unitedhealth", "elevance", "cigna", "humana",
    # Mid-tier tech & data companies
    "databricks", "snowflake", "dbt labs", "fivetran", "airbyte",
    "palantir", "datadog", "confluent", "mongodb", "elastic",
    "tableau", "looker", "domo", "thoughtspot",
    "doordash", "airbnb", "lyft", "uber", "stripe", "square",
    "affirm", "coinbase", "robinhood",
    # Staffing / contracting (real ones — not body-shops)
    "dexian", "kforce", "robert half", "beacon hill",
}

# ── Company trust scoring ──────────────────────────────────────────────────────
# Minimum trust score (0-100) to proceed to Claude fit scoring.
# Jobs below this are skipped regardless of other filters passing.
# 40 = moderate gate (recommended). 60 = strict. 0 = disabled.
LINKEDIN_MIN_TRUST_SCORE = 40

# Trust score weights — points added/subtracted for each signal
TRUST_SCORE_WEIGHTS = {
    "verified_badge":        +35,   # LinkedIn verified = strong real-company signal
    "followers_500_plus":    +20,   # well-established company
    "followers_100_to_500":  +10,   # small but real
    "followers_50_to_100":   +5,    # borderline
    "followers_under_50":    -40,   # almost certainly fake
    "employees_200_plus":    +20,   # real organization
    "employees_50_to_200":   +10,
    "employees_5_to_50":     +5,    # tiny but possible startup
    "employees_under_5":     -40,   # no real team
    "safety_warning":        -100,  # LinkedIn flagged it — instant 0
    "on_whitelist":          +100,  # known real company
    "base_score":            50,    # start from 50 (benefit of doubt)
}

# ── LinkedIn legitimacy check via Claude ──────────────────────────────────────
# If True, jobs in the "grey zone" trust score (30-70) get a second Claude call
# that specifically asks: "is this a real job at a real company?"
# Uses haiku (cheap) — ~$0.001 per check.
LINKEDIN_LEGITIMACY_CHECK = True
LINKEDIN_LEGITIMACY_GREY_ZONE_MIN = 30   # below this → skip without Claude check
LINKEDIN_LEGITIMACY_GREY_ZONE_MAX = 70   # above this → proceed without Claude check

# ── Description fingerprinting ────────────────────────────────────────────────
# Detects scam templates: same description text appearing from multiple companies.
LINKEDIN_FINGERPRINT_ENABLED = True
LINKEDIN_FINGERPRINT_MIN_COMPANIES = 2   # seen from this many different companies → scam

# ── LinkedIn native trust signals ─────────────────────────────────────────────
# These are scraped directly from the LinkedIn UI — LinkedIn's own fraud team
# already flagged or measured these. Using them costs zero API calls.

# If LinkedIn shows a safety/fraud warning banner on the job → hard skip.
# This is the strongest possible signal — LinkedIn's own system flagged it.
LINKEDIN_SKIP_ON_SAFETY_WARNING = True

# Minimum company followers on LinkedIn.
# Real tech companies hiring data engineers have at least a few hundred followers.
# Fake companies created to post scam jobs typically have <50.
LINKEDIN_MIN_COMPANY_FOLLOWERS = 50

# Minimum employees showing on LinkedIn company page.
# A "data engineering" employer with 1–5 employees is almost always fake.
# Set to 0 to disable (some legitimate startups are tiny).
LINKEDIN_MIN_COMPANY_EMPLOYEES = 5

# If True, ONLY apply to companies with LinkedIn's verified badge.
# Conservative — set False by default so small legitimate startups aren't excluded.
LINKEDIN_TRUST_VERIFIED_ONLY = False

# ── Indeed speed + Cloudflare mitigation ──────────────────────────────────────
# Tuned for throughput: fast enough to hit 150 apps/day, slow enough to avoid CF blocks.
INDEED_SEARCH_DELAY_MIN  = 3    # seconds between queries (was 5 — too slow)
INDEED_SEARCH_DELAY_MAX  = 8    # seconds between queries (was 12 — too slow)
INDEED_PAGE_DELAY_MIN    = 2    # seconds after each page load (was 3)
INDEED_PAGE_DELAY_MAX    = 5    # seconds after each page load (was 7)
INDEED_SCROLL_SEARCHES   = True # simulate human scroll between searches
INDEED_CF_RETRY_WAIT_SEC = 30   # seconds to wait if Cloudflare challenge (was 45)
INDEED_PAGES_PER_QUERY   = 3    # how many result pages to scrape per query (was 2)
                                 # 3 pages = ~45 job cards per query

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
    "executive",            # Executive Director, Executive Analyst etc.
    "associate director",   # technically senior
    " iii", " iv", " v",   # seniority suffixes (Engineer III, Engineer IV)
}

# ── Role relevance filter — title must contain at least one of these ───────────
# Even if a job passes all fake-job checks, if the title has zero relevance to
# data/ML/analytics/engineering, skip it. Prevents "Junior Analyst" at a
# shipping company, "AI Engineer" from a Gulf job board, etc.
TARGET_ROLE_KEYWORDS = {
    "data", "analytics", "analyst", "engineer", "scientist",
    "ml", "machine learning", "ai ", "artificial intelligence",
    "bi ", "business intelligence", "etl", "pipeline",
    "database", "sql", "python", "cloud", "platform",
    "reporting", "insight", "intelligence", "quantitative",
    "nlp", "deep learning", "modeling",
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
# Spread across diverse query forms so LinkedIn returns different card sets per query.
# More unique queries = more unique job cards = more shots at 50/day.
LINKEDIN_QUERIES = [
    # Data Engineering — core
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
    "Databricks Data Engineer",
    "Snowflake Data Engineer",
    "dbt Analytics Engineer",
    "Data Engineer Remote",
    "Data Engineer New Grad",
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
    "Data Analyst Remote",
    "Analytics Analyst",
    "Insights Analyst",
    "Product Analyst",
    # Data Science / ML
    "Data Scientist Entry Level",
    "Junior Data Scientist",
    "ML Engineer Entry Level",
    "Machine Learning Engineer",
    "AI Engineer Entry Level",
    "Applied Scientist Entry Level",
    "NLP Engineer Entry Level",
    "Data Scientist Remote",
    "Machine Learning Analyst",
    # Broader roles
    "Database Analyst",
    "Quantitative Analyst Entry Level",
    "Data Operations Analyst",
    "Data Platform Engineer",
    "Decision Scientist",
    "Product Analyst Data",
    "Marketing Data Analyst",
    "Financial Data Analyst",
    "Healthcare Data Analyst",
    "Operations Research Analyst",
]

# ── Indeed-specific search queries (broader than LinkedIn) ─────────────────────
# Each query scrapes 3 pages (~45 cards). 40 queries × 45 cards = ~1,800 potential cards.
# After filters (senior, domain, fit≥72%), expect ~100-150 actual applications per day.
INDEED_QUERIES = [
    # Data Engineering — varied query forms to pull different result sets
    "Data Engineer",
    "Junior Data Engineer",
    "Entry Level Data Engineer",
    "Data Engineer Python SQL",
    "Data Engineer Python",
    "ETL Developer",
    "ETL Data Engineer",
    "Data Pipeline Engineer",
    "Analytics Engineer",
    "PySpark Engineer",
    "PySpark Data Engineer",
    "Azure Data Engineer",
    "AWS Data Engineer",
    "GCP Data Engineer",
    "Databricks Engineer",
    "Snowflake Data Engineer",
    "dbt Engineer",
    "Cloud Data Engineer",
    "Data Warehouse Engineer",
    "Big Data Engineer",
    # Data Analysis
    "Data Analyst",
    "Junior Data Analyst",
    "Entry Level Data Analyst",
    "Business Intelligence Analyst",
    "BI Analyst",
    "SQL Data Analyst",
    "Python Data Analyst",
    "Reporting Analyst",
    "Business Analyst Data",
    "Tableau Developer",
    "Power BI Developer",
    "Tableau Analyst",
    "Power BI Analyst",
    "Data Analytics Analyst",
    "Insights Analyst",
    "Product Analyst",
    # Data Science / ML
    "Data Scientist",
    "Junior Data Scientist",
    "Entry Level Data Scientist",
    "Machine Learning Engineer",
    "ML Engineer",
    "AI Engineer",
    "NLP Engineer",
    "Applied Machine Learning",
    "Machine Learning Analyst",
    # Broad
    "Data Operations Engineer",
    "Data Platform Engineer",
    "Database Developer",
    "Database Analyst",
    "Quantitative Analyst",
    "Product Analytics Engineer",
    "Marketing Data Analyst",
    "Financial Data Analyst",
    "Healthcare Data Analyst",
]

# ── Resume builder settings ────────────────────────────────────────────────────
ATS_TARGET_SCORE    = 98    # target keyword coverage %
RESUME_FONT         = "Calibri"
RESUME_MAX_BULLETS  = 7     # max bullets for primary job
RESUME_SIDE_BULLETS = 4     # max bullets for secondary jobs

# ── Candidate basics (non-sensitive — sensitive data stays in raghav_profile.py)
CANDIDATE_NAME      = os.environ.get("CANDIDATE_NAME", "Your Name")
CANDIDATE_LOCATION  = os.environ.get("HOME_CITY_STATE", "City, ST")
CANDIDATE_EMAIL     = os.environ.get("CANDIDATE_EMAIL", "your.email@gmail.com")
CANDIDATE_PHONE     = os.environ.get("HOME_PHONE", "")
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
