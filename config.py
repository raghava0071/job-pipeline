# =============================================================================
# CONFIG.PY — Single source of truth for the entire job pipeline
# Edit this file to change any behaviour across the whole system.
# =============================================================================

# ── Version ────────────────────────────────────────────────────────────────────
# Bump this whenever you make a meaningful change so the morning log shows
# which version ran. Format: MAJOR.MINOR.PATCH
#   MAJOR — big structural change (new platform, new flow)
#   MINOR — new feature or filter added
#   PATCH — small fix or tuning
PIPELINE_VERSION = "2.4.0"

# Minimum seconds after a CAPTCHA is first detected before a "solved"
# declaration is trusted, regardless of which signal claims it — added
# 2026-07-13 after two same-day incidents (see CHANGELOG v1.9.1/v1.9.2/
# v1.9.3) where a stale/unrelated g-recaptcha-response token caused
# "solved" to fire ~1s after pinning, well before a human could have
# actually completed an image-selection challenge. This is a backstop
# independent of the element-scoping fixes, not a replacement for them —
# do not remove this thinking it's redundant once scoping looks solid; the
# whole reason it exists is that scoping was already fixed once (v1.9.2)
# and still wasn't enough (v1.9.3).
CAPTCHA_MIN_SOLVE_FLOOR_SEC = 3

# ── Platform switches — turn a platform off without touching its code ──────────
# Set to False to skip that platform entirely for the current run.
# Useful when testing a fix on one platform while keeping others live.
INDEED_ENABLED     = True
LINKEDIN_ENABLED   = True
WORKDAY_ENABLED    = True
GREENHOUSE_ENABLED = True   # guest-apply only — see greenhouse_apply_now.py

# Indeed hand-off mode: Indeed's Cloudflare wall blocks any automated browser
# (confirmed for weeks — real Chrome works, the pipeline's does not). When True,
# the Indeed step does NOT launch the blocked browser; instead it builds an HTML
# dashboard of your Indeed searches to click through in your REAL browser (where
# Indeed works). See indeed_handoff.py. Set False only if Indeed ever stops
# blocking the automated browser.
INDEED_HANDOFF_MODE = True

# ── Factual answers — single source of truth for every platform ────────────────
# These are the ONLY place these facts should be hardcoded. Every apply engine's
# form-answering logic must read from here, never carry its own copy — added
# 2026-08-25 after finding qa_answers.py's sponsorship-question answers were all
# hardcoded to "No"/"False" (wrong — Raghav will need H-1B sponsorship in the
# future) and greenhouse_apply_now.py's PROFILE_FALLBACK had "sponsorship": "No"
# copied verbatim from Workday's, same wrong value in a second place. Two
# different files disagreeing about the same fact is exactly the bug class this
# section exists to prevent.
AUTHORIZED_TO_WORK_NOW = True    # F-1 STEM OPT — currently authorized, no gap
REQUIRES_SPONSORSHIP   = True    # will need H-1B sponsorship in the future — answer "Yes", not "No"
YEARS_EXPERIENCE       = 3       # real total professional experience — used to answer
                                  # "do you have N+ years" questions HONESTLY (No if
                                  # N > this), never blindly "Yes" and never skipped
EARLIEST_START_DATE    = "Immediately"   # distinct from notice-period fields (still "2 weeks")

from pathlib import Path
import os

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR        = Path.home() / "job_pipeline"
RESUMES_DIR     = BASE_DIR / "resumes"
COVER_DIR       = BASE_DIR / "cover_letters"
DATA_DIR        = BASE_DIR / "data"
LOG_FILE        = DATA_DIR / "apply_log.json"
ERROR_LOG_PATH  = DATA_DIR / "pipeline_errors.log"   # single human-readable error log (robot/Cloudflare blocks + all errors) — see error_log.py
INDEED_HANDOFF_HTML = DATA_DIR / "indeed_handoff.html"   # Indeed hand-off dashboard (open in real browser) — see indeed_handoff.py
TRACKER_FILE    = DATA_DIR / "applications.xlsx"
SESSION_LI      = Path.home() / ".linkedin_session"
SESSION_IN      = Path.home() / ".indeed_session"
RUN_LOCK_PATH   = Path("/tmp/run_all.lock")   # singleton lock — blocks a second run_all.py from starting while one is already running (Jul 13 duplicate-trigger incident)
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

def _env(key: str, default: str = "") -> str:
    """Read a value from the OS environment, falling back to parsing .env
    directly if it's not already set. Needed because nothing in this
    pipeline loads .env into the process environment globally (no
    python-dotenv, no shell `source .env`) — every process starts with only
    real OS env vars, so a bare `os.environ.get(key, default)` silently
    returns the hardcoded default for anything that only lives in .env.

    CONFIRMED 2026-07-11: this exact gap left CANDIDATE_EMAIL resolving to
    the literal placeholder "your.email@gmail.com" during Workday account
    creation instead of the real address — Raghav caught it live in the
    browser. Same bug class already fixed once in workday_apply_now.py's
    _get_wd_password() (missing return statement, different symptom, same
    root cause: an env value that's only ever in .env, never in the real
    process environment)."""
    val = os.environ.get(key, "")
    if not val:
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith(f"{key}="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return val or default

CLAUDE_MODEL_FAST   = "claude-haiku-4-5-20251001"   # form filling, bullet rewriting
CLAUDE_MODEL_SMART  = "claude-sonnet-4-6"            # fit scoring, cover letters

# ── Fit Gate ───────────────────────────────────────────────────────────────────
# 65% = good throughput sweet spot. 72% was too strict — only 2-3 apps per run.
# Claude engine default also uses 65%, so these are now in sync.
FIT_THRESHOLD          = 60   # Indeed/Workday minimum Claude score (%)
# Raised 60 → 72 on 2026-07-10 at Raghav's request: 26% of LinkedIn
# applications (230/873) were landing at the bottom of the 60 floor (65-69%,
# the weakest matches that still cleared the bar), which was flagged as a
# plausible contributor to weak response rates. Trades application VOLUME for
# match QUALITY — expect meaningfully fewer LinkedIn applications per run than
# before. No real response-rate data exists yet to confirm this helps (see
# CHANGELOG 1.3.3) — this is a deliberate bet, not a confirmed fix. Revisit
# once real outcome data exists, or if throughput drops too far.
LINKEDIN_FIT_THRESHOLD = 72   # LinkedIn minimum — raised from 60, trading volume for match quality

# ── Scoring method — Claude (paid, smarter) vs free ATS keyword match ─────────
# Per Raghav's request (2026-07-06): default to the free path, no API cost.
# The free path (jd_parser.compute_ats_score — keyword/skills/experience/
# education/title match, the original pre-Claude scoring method) can't judge
# things Claude catches, like off-domain industry requirements or a
# secretly-senior role — expect more false-approves. Flip this back to True
# any time to restore Claude's smarter (but paid, ~$1.50-2/day at 3 runs/day)
# judgment with zero other code changes needed.
USE_CLAUDE_SCORING = False
ATS_FIT_THRESHOLD  = 60   # minimum free ATS score (%) — separate scale from
                          # FIT_THRESHOLD above, watch a run and retune if
                          # apply volume/quality looks off

# Experience-gap hard gate — added 2026-08-25 after confirming a real Anthropic
# "Data Engineer" posting (5+ yrs required, no senior word in title) scored 75%
# and would have auto-applied despite Raghav having 3 yrs (60% of what's asked).
# Keyword/skills/education/title dimensions (80% combined weight) don't care
# about experience at all, so a big YOE gap alone can't fail the composite
# score. This ratio is a separate, title-independent reject: if candidate YOE
# is below (required_yoe * this ratio), the job is rejected no matter how high
# the rest of the score is. 0.7 == candidate must have at least 70% of the
# stated required years.
MIN_EXPERIENCE_RATIO = 0.7

# ── Apply Limits ───────────────────────────────────────────────────────────────
MAX_APPLIES_PER_RUN    = 200  # total cap per run across all platforms
LINKEDIN_DAILY_LIMIT   = 50   # hard LinkedIn cap (platform limit)
INDEED_DAILY_LIMIT     = 150  # target Indeed applications per day
APPLY_DELAY_SEC      = 2    # seconds between applications
FORM_MAX_STEPS       = 15   # max form steps before giving up
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

CLEARANCE_KEYWORDS = {
    "security clearance", "secret clearance", "top secret", "ts/sci",
    "dod clearance", "clearance required", "public trust", "polygraph",
    # Citizenship / visa ineligibility
    "must be a us citizen", "must be us citizen", "us citizens only",
    "green card required", "green card holder", "permanent resident only",
    "no opt", "no cpt", "no visa", "no sponsorship", "itar",
    "us person", "us persons only",
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
    # Body-shops confirmed from apply log — keep adding as seen
    "beaconfire", "contractstaffingrecruiters", "american unit",
    "legacy ai tech", "legacy ai", "vcmax", "zb group", "prosum",
    "net2source", "n2s global", "morgan mckinley", "akkodis",
    "synchrony systems", "synergy ventures", "raas infotek", "vertex elite",
    "washon", "aliando", "foresight works", "ursus", "system one",
    # Mid-tier body-shops / bench-sales firms from actual apply log
    "russell tobin",        # staffing — 19x form failures in apply log
    "glocomms",             # staffing — 17x form failures
    "come near",            # staffing — 15x form failures
    "allied resources technical consultants",  # staffing — 5x failures
    "bayone solutions",     # staffing — 5x failures
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
    # Large Indian IT / global consulting firms with US operations
    "tata consultancy", "tcs", "infosys", "wipro", "cognizant", "hcl",
    "tech mahindra", "capgemini", "ltimindtree", "mphasis", "hexaware",
}
# NOTE: several entries above (staffing firms, Indian IT/consulting firms,
# deloitte/ey/pwc/kpmg/accenture) are ALSO in STAFFING_CONSULTANCY_COMPANY_WORDS
# below and get skipped by staffing_filter.py regardless of being whitelisted
# here. This whitelist means "not a scam" for LinkedIn's trust score — it does
# NOT mean "apply to it"; the staffing/consultancy exclusion is independent
# and takes priority (Raghav's preference: no staffing or consulting jobs,
# even from legitimate firms).

# ── Staffing / consultancy exclusion (user preference, not fraud detection) ───
# Raghav doesn't want staffing-agency or consulting-firm jobs on LinkedIn or
# Indeed, period — these are typically real, legitimate employers, just not
# the kind of direct-hire role he's looking for. Checked in staffing_filter.py,
# independent of COMPANY_WHITELIST above and independent of the fake-job/fraud
# checks — a company can be "not a scam" and still get skipped here.
SKIP_STAFFING_CONSULTANCY = True

STAFFING_CONSULTANCY_COMPANY_WORDS = {
    # Generic keywords — catches staffing/consulting firms not on any list below
    "staffing", "consulting", "consultancy", "consultants",
    "recruiting", "recruitment", "talent acquisition", "talent solutions",
    "workforce solutions", "professional services", "resource management",
    "managed services", "outsourcing", "body shop", "bench sales",
    "placement services", "hr solutions", "human capital", "staff augmentation",
    # Named staffing agencies — real companies, still staffing
    "manpower", "adecco", "randstad", "kelly services", "spherion",
    "aerotek", "apex systems", "teksystems", "insight global",
    "dexian", "kforce", "robert half", "beacon hill", "actalent",
    "cybercoders", "modis", "artech", "collabera", "mastech",
    "volt", "kelly ocg", "yoh", "hays", "michael page", "cornerstone staffing",
    # Added 2026-07-10 after auditing the actual applied-companies list —
    # "akraya" and "sharp decisions" had BOTH slipped through as recently as
    # 2026-07-09, a full week after this filter first shipped (2026-07-02),
    # since neither name contains a generic staffing keyword. The other five
    # below are also confirmed real staffing/recruiting firms found in the
    # same applied-companies audit (pre-dating this filter, so not currently
    # leaking, but confirmed real and will keep resurfacing in future
    # searches if not pre-emptively blocked).
    "akraya", "sharp decisions", "brooksource", "talent groups", "akkodis",
    "software guidance & assistance", "harrison clarke",
    # Global IT-services / body-shop-style firms — direct-hire FTE but still
    # a "you work at whatever client we place you at" consulting model.
    # 2026-07-10: Raghav asked why big-name companies like TCS/Infosys never
    # come up as applied — turned out they were deliberately excluded here.
    # He wants the big, well-known Indian IT majors allowed again (US-based
    # roles only — already enforced independently by FAKE_JOB_LOCATION_
    # SIGNALS containing "india", which runs in is_fake_job() BEFORE this
    # staffing check, so removing these names here does NOT open the door to
    # India-based postings from them). Smaller/less-known IT-services firms
    # stay excluded — not asked to unblock those specifically.
    "capgemini", "ltimindtree", "mphasis", "hexaware",
    "mindtree", "persistent systems", "zensar", "birlasoft", "l&t infotech",
    "sonata software", "cigniti", "virtusa", "syntel", "genpact",
    # Big management / professional-services consulting
    # NOTE: deliberately no bare "ey" — as a 2-letter substring it would
    # false-positive on "money", "key", "turkey", etc. "ernst & young" below
    # covers the real cases; a posting under the bare "EY" brand name alone
    # is a known gap, accepted to avoid false positives elsewhere.
    "deloitte", "ernst & young", "pwc", "kpmg", "accenture",
    "bcg", "mckinsey", "bain & company", "slalom", "west monroe",
    "guidehouse", "grant thornton",
}

# Explicit allowlist, checked BEFORE the generic-keyword loop above — the big
# Indian IT majors' official names contain generic words that need to stay
# blocked for everyone else ("Tata Consultancy Services" contains
# "consultancy"), so a plain keyword-removal alone isn't enough to unblock
# them. See staffing_filter.py for how this is used.
STAFFING_CONSULTANCY_EXPLICIT_ALLOW = {
    "tata consultancy", "tcs", "infosys", "wipro", "cognizant",
    "hcl technologies", "hcltech", "tech mahindra",
}

# Description-level signals — the posting talks like a staffing/consulting
# engagement ("our client", "bench", C2C/W2 contract language) even when the
# company name itself doesn't give it away.
STAFFING_CONSULTANCY_DESC_SIGNALS = {
    "on behalf of our client", "one of our clients", "our client is seeking",
    "our client is looking for", "client of ours", "for our client",
    "our client, a", "multiple client engagements", "client site",
    "consulting engagement", "staff augmentation",
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
INDEED_BROWSER_LAUNCH_TIMEOUT_MS = 60000  # fail fast (1 min) instead of Playwright's
                                 # default hang if the Chromium profile is stuck/locked

# How often ensure_login() rechecks whether you've logged in, and for how
# long total, before giving up. Widened 5s → 60s on 2026-07-11 at Raghav's
# request — fewer, less frequent requests while waiting, especially
# important if Cloudflare is involved (see the check right next to this loop
# in indeed_apply_now.py). Total wait budget kept at 5 minutes either way.
INDEED_LOGIN_WAIT_INTERVAL_SEC = 60   # seconds between each login recheck
INDEED_LOGIN_WAIT_TOTAL_SEC    = 300  # total time to wait before giving up

# ── Indeed block detection — stop early instead of grinding for hours ─────────
# Unattended (scheduled) runs can't solve CAPTCHAs, so repeated CAPTCHA cooldowns
# or repeated zero-result searches almost always mean the session is blocked,
# not just rate-limited. Give up after this many rather than looping all day.
CAPTCHA_MAX_COOLDOWNS_PER_RUN     = 2   # unsolved-CAPTCHA cooldown cycles before stopping the run
INDEED_EMPTY_QUERY_BAIL_THRESHOLD = 4   # consecutive 0-card searches before stopping the run

# A CONFIRMED Cloudflare "Additional Verification Required" page is a much
# stronger, unambiguous signal than an empty search (which could have other
# causes) — bail much faster than the empty-query threshold above instead of
# hammering the next query against a wall we already know is there. Added
# 2026-07-10 after Raghav had to manually cancel a run that kept hitting
# Cloudflare on every query.
INDEED_CF_BLOCK_BAIL_THRESHOLD    = 2   # consecutive CONFIRMED Cloudflare blocks before stopping the run

# A soft-blocked session (Cloudflare shadow-throttling) doesn't always return
# exactly 0 cards every search — sometimes 1-2 trickle through, which resets
# the consecutive-zero counter above and lets the run grind for hours at a
# tiny fraction of normal yield (seen 2026-07-06: 45 cards across 54 searches
# in 76 minutes, average <1 card/search, never hit the zero-streak bail).
# This checks the rolling average instead of relying on strict zero streaks.
INDEED_LOW_YIELD_MIN_QUERIES = 6   # don't judge yield until this many queries have run
INDEED_LOW_YIELD_AVG_CARDS   = 5   # avg cards/query below this after MIN_QUERIES = likely blocked

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
    "data analyst", "data engineer", "data scientist", "data platform",
    "analytics engineer", "analytics analyst", "analytics ",
    "ml engineer", "machine learning", "ai engineer", "applied scientist",
    "bi analyst", "bi developer", "business intelligence",
    "etl", "pipeline engineer", "database analyst", "database developer",
    "reporting analyst", "insights analyst", "quantitative analyst",
    "nlp engineer", "deep learning", "data modeling",
    "databricks", "snowflake", "dbt ", "spark engineer",
}

# ── Negative title filter — hard-excludes generic software-engineering roles
# even when a TARGET_ROLE_KEYWORDS phrase coincidentally overlaps ─────────────
# Added 2026-08-25 after a Greenhouse dry run (8/8 GitLab jobs) showed
# "Backend Engineer (Ruby)" (78%), "Fullstack Engineer (TypeScript)" (67%),
# "Forward Deployed Engineer" (64%), and "Customer Success Engineer" (73%)
# passing the fit gate. Raghav is a DATA engineer/analyst, not a general
# software engineer — these titles were slipping through Greenhouse's old
# per-engine keyword check because a bare "engineer"/"ai" substring matches
# almost any tech title. A title on this list is rejected UNLESS one of
# DATA_QUALIFIER_WORDS also appears as its own word (so "Data Platform
# Engineer" survives, bare "Platform Engineer" doesn't). See
# is_target_role_title() below — the single place this logic runs.
NEGATIVE_ROLE_TITLE_WORDS = {
    "backend engineer", "back-end engineer", "back end engineer",
    "frontend engineer", "front-end engineer", "front end engineer",
    "fullstack engineer", "full stack engineer", "full-stack engineer",
    "forward deployed engineer",
    "software engineer", "software developer",
    "site reliability engineer",
    "devops engineer",
    "solutions engineer", "sales engineer",
    "customer success engineer", "support engineer", "field engineer",
    "security engineer",
    "infrastructure engineer",
    "platform engineer",       # bare — "data platform engineer" is rescued below
    "qa engineer", "test engineer", "automation engineer",
    "mobile engineer", "ios engineer", "android engineer",
    "web developer", "ui engineer", "ux engineer",
    "embedded engineer", "firmware engineer", "hardware engineer",
    "game developer", "game engineer",
}

# A NEGATIVE_ROLE_TITLE_WORDS hit is forgiven if one of these appears as its
# own word in the title too — real data-flavored roles that also happen to
# say "engineer" ("Data Platform Engineer", "Analytics Infrastructure Engineer").
DATA_QUALIFIER_WORDS = {
    "data", "analytics", "analyst", "machine learning", "ml", "etl",
    "data science", "data scientist", "bi", "business intelligence",
    "quantitative", "nlp", "database", "warehouse", "pipeline",
}

import re as _re

def _word_in(word: str, padded_lower_text: str) -> bool:
    """Word-boundary substring match — NOT a naive `word in text` check.
    The old per-engine filters used bare `kw in title.lower()`, which matches
    "ai engineer" inside "AI Engineering" (GitLab's team/org name, not the
    role itself) — exactly how "Backend Engineer, AI Engineering: Agent
    Observability" slipped past the old TARGET_ROLE_KEYWORDS check. Word
    boundaries close that gap: "engineer" no longer matches inside
    "engineering" because the character right after it (`i`) is a word char."""
    return _re.search(r'(?<!\w)' + _re.escape(word.strip().lower()) + r'(?!\w)',
                       padded_lower_text) is not None

def is_target_role_title(title: str) -> bool:
    """
    Single source of truth for "is this title actually in Raghav's target
    domain (data engineering / analytics / ML)?" — greenhouse_apply_now.py,
    linkedin_apply_now.py, and workday_apply_now.py all call this instead of
    keeping their own copy-pasted substring-matching checks (each had a
    slightly different, all equally loose, version before 2026-08-25).

    Logic:
      1. If the title matches a NEGATIVE_ROLE_TITLE_WORDS phrase (generic
         software-engineering role) AND no DATA_QUALIFIER_WORDS word rescues
         it, reject immediately — no amount of positive keyword overlap
         elsewhere in the title matters.
      2. Otherwise, accept only if a TARGET_ROLE_KEYWORDS phrase is present
         (word-boundary matched, so "ai engineer" no longer matches inside
         "ai engineering").
    """
    t = f" {(title or '').lower()} "

    for bad in NEGATIVE_ROLE_TITLE_WORDS:
        if _word_in(bad, t) and not any(_word_in(q, t) for q in DATA_QUALIFIER_WORDS):
            return False

    return any(_word_in(kw, t) for kw in TARGET_ROLE_KEYWORDS)

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
    # Data Engineering — broad queries first (highest card volume)
    "Data Engineer Entry Level",
    "Junior Data Engineer",
    "Associate Data Engineer",
    "Data Engineer New Grad",
    "Data Engineer Remote",
    "Analytics Engineer Entry Level",
    "Data Platform Engineer",
    "Data Operations Analyst",
    # Data Analysis
    "Data Analyst Entry Level",
    "Junior Data Analyst",
    "Associate Data Analyst",
    "Business Intelligence Analyst",
    "BI Developer Entry Level",
    "Power BI Analyst",
    # Data Science / ML
    "Data Scientist Entry Level",
    "Junior Data Scientist",
    "ML Engineer Entry Level",
    "AI Engineer Entry Level",
    # Broader roles
    "Database Analyst",
    "Product Analyst",
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
CANDIDATE_NAME      = _env("CANDIDATE_NAME", "Your Name")
CANDIDATE_LOCATION  = _env("HOME_CITY_STATE", "City, ST")
CANDIDATE_EMAIL     = _env("CANDIDATE_EMAIL", "your.email@gmail.com")
CANDIDATE_PHONE     = _env("HOME_PHONE", "")
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

# ── Greenhouse company board tokens (used by greenhouse_apply_now.py's API
# discovery — https://boards-api.greenhouse.io/v1/boards/{token}/jobs) ────────
# Replaces the old GREENHOUSE_QUERIES Google-search terms — added 2026-08-25
# after Google started blocking the site:boards.greenhouse.io search itself.
# The public Job Board API pulls ALL of a company's live postings in one call
# (no per-query search terms needed); this pipeline's own title/keyword
# filters (SENIOR_WORDS, DATA_KEYWORDS in greenhouse_apply_now.py) still
# decide which of those postings are relevant, same as before.
# Each entry is the company's "board token" — the slug in its Greenhouse URL
# (boards.greenhouse.io/TOKEN or job-boards.greenhouse.io/TOKEN). Seeded with
# a handful confirmed live via search as of 2026-08-25; a token that stops
# working (company left Greenhouse, renamed its board) fails loudly with a
# 404 printed for that company — nothing is invented to fill the gap. Add/
# remove tokens freely; this is the only place they're listed.
GREENHOUSE_COMPANIES = [
    # Verified HTTP 200 + confirmed non-senior (no Sr/Staff/Principal/Lead/
    # Director/Manager/etc in title) data-engineer/analyst/analytics/BI
    # postings live on the board as of 2026-08-25 — see CHANGELOG for the
    # exact per-company counts. "doordash" was a dead token (404); the
    # correct slug is "doordashusa". "gitlab", "robinhood", and "instacart"
    # were dropped — re-checked the same day and each had 0-1 non-senior
    # data postings, all software-engineering-heavy. Ranked roughly by
    # non-senior data-role yield observed on the verification date; expect
    # this to drift as postings churn — re-verify with the --url/API check
    # in the CHANGELOG before adding new tokens, a 404 is silently skipped
    # per-company but still wastes a request every run.
    "brex",             # fintech — Data Analyst II, Data Engineer (US + intl)
    "doordashusa",      # logistics/marketplace — Analytics Engineer, Data Analyst, SWE-Data Platform
    "affirm",           # fintech
    "coinbase",         # fintech/crypto
    "gusto",            # HR/payroll SaaS
    "cultureamp",       # HR SaaS
    "sigmacomputing",   # BI/data analytics platform
    "asana",            # productivity SaaS
    "klaviyo",          # marketing SaaS — Analytics Engineer (Boston)
    "fivetran",         # data infrastructure/ELT
    "samsara",          # IoT/fleet data
    "chime",            # fintech/neobank
    "faire",            # wholesale marketplace
    "smartsheet",       # productivity SaaS
    "amplitude",        # product analytics platform
]

# ── US-only location filter — Greenhouse ────────────────────────────────────
# Greenhouse's public Job Board API returns a structured `location.name` per
# posting (e.g. "Remote, Canada; Remote, United States" for a role open to
# either country, or "Bangalore, India" for a single-country listing).
# Raghav is on F-1 STEM OPT — US work authorization only — so a posting whose
# location.name doesn't mention the US isn't one he can actually take, no
# matter how high it scores. Added 2026-08-25 after a dry run passed a
# Bangalore-based "AI Engineer" (72%), a Bangalore "Backend Engineer, Geo
# Team" (76%), a Canada-only "Backend Engineer (Ruby)" (78%), a UAE "Forward
# Deployed Engineer - META" (64%), and an EMEA-only "Forward Deployed
# Engineer - EMEA" (64%) — greenhouse_apply_now.py was already fetching
# `location` from the API but never checked it against anything.
US_LOCATION_SIGNALS = ("united states", "usa", "u.s.a", "u.s.")

# Countries/regions that mean NOT US, checked before the positive signals so
# a listing that mixes one of these with an actual US option ("Remote,
# Canada; Remote, United States") still correctly returns True — the
# non-US check only wins when NO US signal is also present.
NON_US_LOCATION_SIGNALS = (
    "canada", "mexico", "brazil", "united kingdom", "ireland", "germany",
    "france", "italy", "netherlands", "sweden", "denmark", "spain",
    "portugal", "poland", "india", "bangalore", "pakistan", "philippines",
    "bangladesh", "sri lanka", "nigeria", "ghana", "kenya", "south africa",
    "australia", "new zealand", "singapore", "japan", "china", "hong kong",
    "uae", "united arab emirates", "dubai", "saudi arabia", "israel",
    "emea", "latam", "apac",
)

# Standard 2-letter USPS state codes — the dominant Greenhouse format for
# purely-domestic US postings is bare "City, ST" with no country name at
# all (confirmed against DoorDash, Brex, Gusto, Klaviyo, Samsara listings
# 2026-08-25). Matched against the ORIGINAL-case location string (not
# lowercased) so e.g. "OR" (Oregon) can't accidentally match the English
# word "or" elsewhere in the string.
US_STATE_ABBREVS = (
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN",
    "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV",
    "NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN",
    "TX","UT","VT","VA","WA","WV","WI","WY","DC",
)

US_STATE_NAMES = (
    "alabama","alaska","arizona","arkansas","california","colorado",
    "connecticut","delaware","florida","georgia","hawaii","idaho",
    "illinois","indiana","iowa","kansas","kentucky","louisiana","maine",
    "maryland","massachusetts","michigan","minnesota","mississippi",
    "missouri","montana","nebraska","nevada","new hampshire","new jersey",
    "new mexico","north carolina","north dakota","ohio","oklahoma",
    "oregon","pennsylvania","rhode island","south carolina","south dakota",
    "tennessee","texas","utah","vermont","virginia","west virginia",
    "wisconsin","wyoming",
)

_US_STATE_ABBR_RE = _re.compile(r',\s*(' + '|'.join(US_STATE_ABBREVS) + r')\b')

def is_us_location(location: str) -> bool:
    """True if `location` (Greenhouse's location.name field) lists the US as
    an eligible location. Handles BOTH formats Greenhouse companies actually
    use: explicit country name ("San Francisco, California, United States")
    and the far more common domestic-only format that never spells out the
    country at all ("San Francisco, CA" / "Remote - US"). An earlier version
    of this function only checked for the literal string "united states" and
    would have wrongly rejected almost every purely-domestic US company —
    confirmed 2026-08-25 against real DoorDash/Brex/Gusto/Klaviyo/Samsara
    postings, none of which say "United States" anywhere.

    Non-US country/region signals are checked first, but only reject when NO
    US signal is present either — so "Remote, Canada; Remote, United States"
    still returns True (the US option makes it reachable) while "São Paulo,
    São Paulo, Brazil" correctly returns False. A blank/fully-unrecognized
    location fails closed (treated as NOT US)."""
    loc = (location or "").lower()
    if not loc:
        return False

    has_us_signal = (
        any(sig in loc for sig in US_LOCATION_SIGNALS)
        or bool(_US_STATE_ABBR_RE.search(location or ""))
        or any(name in loc for name in US_STATE_NAMES)
        or bool(_re.search(r'\bremote\s*[-,]?\s*us\b', loc))
    )
    if has_us_signal:
        return True
    if any(sig in loc for sig in NON_US_LOCATION_SIGNALS):
        return False
    return False

# ── Skill experience years — NOT CANONICAL, see raghav_profile.SKILL_YEARS ────
# Discovered 2026-08-25 while building profile_answers.py: this dict and
# raghav_profile.py's SKILL_YEARS had silently drifted apart — this one is
# missing tools raghav_profile.py's version has (R, Java, JavaScript, ELT,
# data pipelines, data warehouse, Looker, Flask, Django). raghav_profile.py
# is the correct single source of truth for skill/years — it's the private,
# gitignored personal-profile file (see raghav_profile.py's own docstring:
# "Single source of truth for all job application automation"), which is
# where personal skill facts belong, not here. This dict is left in place
# unchanged (removing it risks breaking something not yet found) but is no
# longer read by profile_answers.py, claude_engine.py, or
# linkedin_apply_now.py — all three now import raghav_profile.SKILL_YEARS
# directly. Do not add new code that reads config.SKILL_YEARS; use
# raghav_profile.SKILL_YEARS instead.
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
