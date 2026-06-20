# AI-Powered Job Application Pipeline

An end-to-end automated job application system that scrapes live job listings from **LinkedIn**, **Indeed**, and **Workday**, scores them with Claude AI, builds a tailored ATS-optimized resume per job, fills application forms intelligently, and submits — all fully automated on a daily schedule.

> Built in Python · Powered by Claude AI (Anthropic) · Playwright browser automation · Runs 3× daily via macOS launchd

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Pipeline Components](#pipeline-components)
- [LinkedIn Fake Job Detection — 6-Layer System](#linkedin-fake-job-detection--6-layer-system)
- [Resume Engine](#resume-engine)
- [AI Form Filling](#ai-form-filling)
- [Scheduling](#scheduling)
- [Setup](#setup)
- [Configuration](#configuration)
- [File Structure](#file-structure)
- [Security & Privacy](#security--privacy)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DAILY SCHEDULE (launchd)                      │
│              Morning 9am · Afternoon 2pm · Evening 6pm               │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                    run_all.py  (orchestrator)
                    /          |           \
                  /            |             \
    linkedin_apply_now.py  indeed_apply_now.py  workday_apply_now.py
           │                    │                      │
           ▼                    ▼                      ▼
    ┌─────────────────────────────────────────────────────────┐
    │                  FAKE JOB FILTER (6 layers)              │
    │          Zero API cost — runs before any Claude call     │
    └─────────────────────────────────────────────────────────┘
           │
           ▼  (only real jobs reach here)
    ┌─────────────────────────────────────────────────────────┐
    │              claude_engine.py  (AI scoring)              │
    │    score_fit() — LinkedIn ≥80%  |  Indeed/WD ≥62%       │
    └─────────────────────────────────────────────────────────┘
           │
           ▼  (only high-fit jobs reach here)
    ┌─────────────────────────────────────────────────────────┐
    │            resume_builder.py  (ATS resume)               │
    │    jd_parser.py → keyword extraction → DOCX generation  │
    │    Verified ≥98% ATS keyword coverage per job            │
    └─────────────────────────────────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────────────────────────┐
    │         Playwright browser automation (form fill)        │
    │    qa_answers → claude_answers → SQLite cache → Claude   │
    │    79% cache hit rate · 3x fewer API calls               │
    └─────────────────────────────────────────────────────────┘
           │
           ▼
    ┌─────────────────────────────────────────────────────────┐
    │              notifier.py  (Gmail alerts)                 │
    │    Per-apply email + daily session summary               │
    └─────────────────────────────────────────────────────────┘
```

---

## Pipeline Components

### `run_all.py` — Orchestrator
Runs all three platform scrapers in sequence. Handles per-platform daily limits, logging, and session summary notifications. Called by launchd 3× daily.

### `linkedin_apply_now.py` — LinkedIn Engine
Scrapes LinkedIn job search results using Playwright, applies the 6-layer fake-job filter, scores with Claude, builds a custom resume, and submits via Easy Apply — all in a single browser session without navigating away from the search page.

- Daily limit: 50 applications
- Fit threshold: 80% (stricter than other platforms — quality over quantity)
- Easy Apply only — external Workday links are queued for the Workday engine

### `indeed_apply_now.py` — Indeed Engine
Searches Indeed across 40+ query terms, applies Cloudflare-aware delays, scores jobs, and submits via Indeed's Smart Apply flow.

- Daily limit: 150 applications
- Fit threshold: 62%
- Handles multi-step forms with resume upload and Claude form fill per job

### `workday_apply_now.py` — Workday Engine
Applies to enterprise Workday ATS portals (Capital One, Booz Allen, Deloitte, etc.) via Google search. Creates and reuses Workday accounts per company, handles security question flows.

- Per-company account management with encrypted credentials
- Handles 30-step forms with full Claude AI form completion

### `claude_engine.py` — AI Intelligence Layer
- `score_fit()` — scores candidate vs job match (Haiku model, cached by JD hash)
- `local_prefilter()` — zero-cost keyword pre-check before any API call
- `build_profile_summary()` — builds structured candidate context for scoring
- `tailor_bullets()` — rewrites resume bullets to match JD language

### `resume_builder.py` — ATS Resume Engine
Generates a Word (.docx) resume per job with:
- Keyword extraction from JD via `jd_parser.py`
- Synonym-aware matching (PySpark ↔ Apache Spark, ETL ↔ data pipelines, etc.)
- Verified ≥98% ATS keyword coverage (gap-fill section auto-added if needed)
- BEFORE → AFTER ATS score printed per run

### `jd_parser.py` — Job Description Parser
Extracts required skills, injectable keywords, and ATS coverage score from raw JD text. Feeds into the resume builder.

### `answer_cache.py` — Answer Cache (3-Tier)
Lookup order per form field:
1. `qa_answers.py` — manually curated master Q&A (highest priority)
2. `claude_answers.py` — Claude's past answers (auto-saved, human-reviewable)
3. SQLite cache — key-value fallback

79% cache hit rate → 3× fewer Claude API calls per run.

### `notifier.py` — Gmail Notification System
Sends per-application email (company, title, fit score, resume attached) and a daily session summary with applied/skipped/failed counts.

### `pipeline_logger.py` — Structured Run Logging
Per-run structured log with job-level detail: applied, skipped (with reason), failed, scores. Used for daily summary reports.

### `salary_helper.py` — Salary Intelligence
Parses posted salary ranges from JD text and picks the optimal answer within the candidate's acceptable range.

### `tracker.py` — Application Tracker
Maintains an Excel spreadsheet of all applications with status, company, title, fit score, resume used, and platform.

---

## LinkedIn Fake Job Detection — 6-Layer System

Every LinkedIn job passes through 6 layers **before any Claude API call is made** (zero token cost). A job is submitted only if it clears every layer.

```
Job Card Loaded
      │
      ├── Senior/Lead Filter ──────── "senior", "lead", "principal" in title → SKIP
      ├── Role Relevance Filter ───── no data/tech keyword in title → SKIP
      │
      ├── LAYER 0: LinkedIn Native Signals
      │     ├── Safety warning banner on job → SKIP (LinkedIn's own fraud flag)
      │     ├── Company followers < 50 → SKIP
      │     └── Company employees < 5 → SKIP
      │
      ├── LAYER 1: Title Signals
      │     ├── Off-target roles ("data entry", "scheduler", "HR coordinator") → SKIP
      │     └── Bot-generated typos ("databrick ", "data analys ", "data enginer") → SKIP
      │
      ├── LAYER 2: Company Name Signals
      │     ├── 70+ known bad actors (blocklist) → SKIP
      │     ├── Commonwealth/African suffixes (Limited, Ltd, Pvt Ltd, SME Ltd) → SKIP
      │     ├── Job-aggregator company names ("Jobs in United States...") → SKIP
      │     └── Non-ASCII company name → SKIP
      │
      ├── LAYER 3: Description Signals
      │     ├── Fraud red flags (WhatsApp, Telegram, wire transfer, SSN required) → SKIP
      │     ├── Body-shop/offshore language (C2C, bench candidates, current CTC) → SKIP
      │     └── Non-US location in location field → SKIP
      │
      ├── LAYER 4: Quality Heuristics
      │     ├── Description < 200 chars → SKIP
      │     ├── Fewer than 2 tech tool names in description → SKIP
      │     └── Applicant count ≥ 400 (stale bait posting) → SKIP
      │
      ├── Description Fingerprint Check
      │     └── Same description from 2+ different companies → SKIP (scam template)
      │
      ├── Company Trust Score (0–100)
      │     ├── Whitelisted known company → score 100, skip remaining checks
      │     ├── Score < 40 → SKIP (low-trust company)
      │     └── Score 30–70 (grey zone) → Claude Legitimacy Check
      │               ├── Claude says FAKE → SKIP
      │               └── Claude says REAL → proceed to fit scoring
      │
      └── Claude Fit Scoring (threshold: 80%)
            ├── Score < 80% → SKIP
            └── Score ≥ 80% → BUILD RESUME → APPLY ✅
```

### Company Trust Score Breakdown

| Signal | Points |
|--------|--------|
| Whitelisted known company | +100 (instant pass) |
| LinkedIn verified badge | +35 |
| 500+ followers | +20 |
| 100–500 followers | +10 |
| 50–100 followers | +5 |
| < 50 followers | −40 |
| 200+ employees | +20 |
| 50–200 employees | +10 |
| 5–50 employees | +5 |
| < 5 employees | −40 |
| LinkedIn safety warning | −100 (instant 0) |
| Base score | 50 |

---

## Resume Engine

Every application gets its own custom resume. The build process:

1. Parse the JD for required keywords (`jd_parser.py`)
2. Map synonyms — e.g. PySpark = Apache Spark, ETL = data pipelines, ADF = Azure Data Factory
3. Score current resume keyword coverage against JD requirements
4. Rewrite experience bullets to naturally mirror JD language
5. Auto-add a gap-fill "Technical Proficiencies" section for missing keywords
6. Verify final ATS score ≥ 98% before saving the file

Output: `CandidateName_CompanyName_JobTitle.docx` saved to `output/resumes/`

---

## AI Form Filling

For each form step, the pipeline uses a 3-tier answer system:

1. **Check `qa_answers.py`** — manually curated answers (salary, work auth, address, visa status). Highest priority, always correct.
2. **Check `claude_answers.py`** — Claude's past answers saved from prior runs. Human-reviewable and editable.
3. **Check SQLite cache** — key-value fallback from older runs.
4. **Call Claude (Haiku)** — only for fields not found in any cache. Returns a JSON map of all uncached fields in a single API call (no per-field round trips).
5. **Save new answers** — Claude's answers are saved back to `claude_answers.py` so future runs use cache instead.
6. **Pre-submit Claude review** — before clicking Submit, Claude reads the full review page and checks for blank required fields or obviously wrong answers.

---

## Scheduling

Three daily runs via macOS `launchd`:

| Run | Time | Platform Focus |
|-----|------|---------------|
| Morning | 9:00 AM | LinkedIn + Indeed |
| Afternoon | 2:00 PM | Indeed + Workday |
| Evening | 6:00 PM | All platforms |

Install the schedule:
```bash
bash setup_scheduler.sh
```

---

## Setup

### Prerequisites

```bash
# Python 3.11+
brew install python@3.11

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser
playwright install chromium
```

### First-time configuration

```bash
# 1. Copy and fill in your candidate profile
cp raghav_profile.example.py raghav_profile.py
# Edit raghav_profile.py — add your name, education, experience, skills

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env — add your Anthropic API key, Gmail credentials

# 3. Validate setup
python preflight_check.py
```

### Running the pipeline

```bash
# Dry run — scores + builds resumes, no actual submission
python linkedin_apply_now.py --dry-run --limit 5

# Live run with application limit
python linkedin_apply_now.py --limit 10

# Full pipeline (all platforms)
python run_all.py
```

---

## Configuration

All tunable settings are in `config.py` — no code changes needed for common adjustments.

### Key settings

```python
# Fit score thresholds
FIT_THRESHOLD          = 62   # Indeed/Workday minimum (%)
LINKEDIN_FIT_THRESHOLD = 80   # LinkedIn minimum (%) — stricter, 50 apps/day

# Daily apply limits
LINKEDIN_DAILY_LIMIT   = 50
MAX_APPLIES_PER_RUN    = 150

# Fake job detection thresholds (all tunable here)
LINKEDIN_MIN_COMPANY_FOLLOWERS  = 50    # fewer → skip
LINKEDIN_MIN_COMPANY_EMPLOYEES  = 5     # fewer → skip
LINKEDIN_MIN_TRUST_SCORE        = 40    # below → skip
LINKEDIN_LEGITIMACY_CHECK       = True  # Claude legitimacy check for grey zone
LINKEDIN_MAX_APPLICANTS         = 400   # stale posting threshold

# ATS resume target
ATS_TARGET_SCORE = 98   # minimum keyword coverage % before resume is saved
```

### Adding a fake company to the blocklist

In `config.py`, add to `FAKE_JOB_COMPANY_WORDS`:

```python
FAKE_JOB_COMPANY_WORDS = {
    ...
    "new scam company name",  # lowercase, partial match
}
```

### Adding a trusted company to the whitelist

In `config.py`, add to `COMPANY_WHITELIST`:

```python
COMPANY_WHITELIST = {
    ...
    "company name",  # lowercase — skips all fake-job checks for this company
}
```

---

## File Structure

```
job_pipeline/
│
├── run_all.py                    # Main orchestrator
│
├── linkedin_apply_now.py         # LinkedIn scraper + Easy Apply bot
├── indeed_apply_now.py           # Indeed scraper + Smart Apply bot
├── workday_apply_now.py          # Workday ATS bot
│
├── claude_engine.py              # AI scoring, pre-filter, bullet tailoring
├── resume_builder.py             # ATS-optimized Word resume generator
├── jd_parser.py                  # JD keyword extractor
├── cover_letter.py               # Cover letter generator
│
├── answer_cache.py               # SQLite answer cache (3-tier lookup)
├── salary_helper.py              # Salary range parser + answer picker
├── secure_store.py               # Encrypted credential storage
├── pipeline_logger.py            # Structured per-run logging
├── notifier.py                   # Gmail notification system
├── tracker.py                    # Excel application tracker
├── preflight_check.py            # Environment + credential validator
│
├── config.py                     # All tunable settings (single source of truth)
├── raghav_profile.example.py     # Candidate profile template → copy to raghav_profile.py
├── .env.example                  # Environment variable template → copy to .env
│
├── requirements.txt              # Python dependencies
├── setup_scheduler.sh            # Install macOS launchd schedule
│
├── data/                         # Runtime data — gitignored
│   ├── apply_log.json            # Full application history
│   ├── desc_fingerprints.json    # Scam template fingerprint store
│   └── *.log                     # Run logs
│
└── output/                       # Generated files — gitignored
    ├── resumes/                  # Per-job tailored resumes (.docx)
    └── cover_letters/            # Per-job cover letters (.docx)
```

---

## Security & Privacy

**What is never committed to this repo:**

| File | Reason |
|------|--------|
| `raghav_profile.py` | Real name, email, phone, address, work history |
| `.env` | API keys, Gmail password, Workday password, encryption key |
| `qa_answers.py` | Personal form answers (salary, address, visa status) |
| `claude_answers.py` | Auto-saved Claude answers — may contain personal data |
| `data/apply_log.json` | Full application history |
| `data/*.xlsx` | Application tracker with personal job search data |
| `.indeed_session/` | Browser cookies with active login sessions |
| `.linkedin_session/` | Same |
| `output/resumes/` | Personal resume documents |
| `*.docx`, `*.pdf` | Personal documents |

All personal data stays local. The repo contains only pipeline logic. To use this for yourself, copy `raghav_profile.example.py` → `raghav_profile.py` and fill in your own information.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Browser automation | Playwright (Chromium) |
| AI scoring & form fill | Anthropic Claude (Haiku + Sonnet) |
| Resume generation | python-docx |
| Answer caching | SQLite + in-memory dict |
| Scheduling | macOS launchd |
| Notifications | Gmail SMTP |
| Data storage | JSON + Excel (openpyxl) |
| Language | Python 3.11+ |

---

## Stats

- **Platforms:** LinkedIn · Indeed · Workday
- **Daily capacity:** ~200+ applications across all platforms
- **Fake job block rate:** ~95%+ caught before any API call
- **ATS coverage:** ≥98% verified per resume
- **Cache hit rate:** ~79% (3× fewer Claude API calls)
- **Form fill:** Pre-submit Claude review on every application with novel questions
