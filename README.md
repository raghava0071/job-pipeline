# job-pipeline

An AI-powered job application pipeline: it discovers real job postings, scores each one against your profile with Claude, builds a tailored ATS-optimized resume per job, fills out the application form intelligently, and submits — with a human-in-the-loop safety net for anything it shouldn't guess at on its own.

> Python · [Anthropic Claude](https://www.anthropic.com) · [Playwright](https://playwright.dev) browser automation · MIT licensed

---

## Table of Contents

- [What actually works today](#what-actually-works-today)
- [Known limitations](#known-limitations)
- [Architecture overview](#architecture-overview)
- [Setup](#setup)
- [Running it](#running-it)
- [Configuration](#configuration)
- [File structure](#file-structure)
- [Security & privacy](#security--privacy)
- [Change management & safety net](#change-management--safety-net)
- [Roadmap](#roadmap)

---

## What actually works today

This project targets four ATS (applicant tracking system) platforms. They are **not** all in the same state, and this README says so plainly rather than pretending otherwise:

| Platform | Status | Notes |
|---|---|---|
| **Greenhouse** | ✅ Active, hardened | The real, working core of this project. Guest-apply flow (no account creation needed on most postings), live-tested, submits for real when you pass `--live`. |
| **Workday** | 🚧 Built, not yet reliable | Full step-walk automation exists (account creation, security questions, multi-step forms) but has never completed a successful live application end-to-end — account creation / email verification is the current blocker. Treat as experimental. |
| **LinkedIn** | ⏸ Built, disabled by design | Full Easy Apply automation with a 6-layer fake-job filter exists and worked previously. Deliberately turned off (`LINKEDIN_ENABLED = False` in `config.py`) — not worth the ongoing arms race against LinkedIn's bot detection. Code stays in the repo, unmaintained for now. |
| **Indeed** | ⏸ Built, disabled by design | Same situation as LinkedIn — Cloudflare's anti-bot measures made this not worth maintaining. `INDEED_ENABLED = False`. |

**If you clone this repo, you're really getting a Greenhouse automation tool** that happens to also contain three other platform integrations in various states of completeness. See [ROADMAP.md](ROADMAP.md) for the full, dated history of why each decision was made.

Greenhouse discovery works two ways, and you can use either or both:
- A curated list of ~30 companies known to post real openings (`GREENHOUSE_COMPANIES` in `config.py`)
- An optional expanded pool of **8,300+ real Greenhouse company slugs** (`data/greenhouse_companies_full.json`, sourced from the open-source [job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) project), rotated through in batches so you're not hammering Greenhouse's API with thousands of requests every run. Opt in with `GREENHOUSE_USE_EXPANDED_DISCOVERY=true` in `.env`.

## Known limitations

Two things this pipeline deliberately does **not** try to fully automate:

1. **Email verification codes (OTP).** Some Greenhouse postings send a one-time code to your email mid-application. There's a Gmail-polling mechanism for this (`mail_reader.py`), but it has not been reliably confirmed to catch every real-world case — the exact timing and wording of these prompts varies by company, and it's genuinely hard to guarantee blind. When it fails, the pipeline doesn't fail silently or guess — it falls through to the manual-assist pause below.

2. **Sensitive or judgment-call questions.** Anything the pipeline can't answer truthfully from your own saved data (accommodation requests, unusual legal questions, anything with no safe default) is never auto-filled with a guess. Instead, the browser pauses for **3 minutes** with the form left open, so you can fill in just that field yourself before it continues. This is an intentional design choice, not a bug — the alternative (inventing an answer) is worse.

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    Company discovery                             │
│   Curated list (config.py) + optional expanded 8,300-company     │
│   rotation, both hitting Greenhouse's own public per-company      │
│   boards-api.greenhouse.io/v1/boards/<company>/jobs endpoint      │
└───────────────────────────┬────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│         Title / domain relevance filter + fit scoring            │
│   is_target_role_title() cuts obvious mismatches for free,       │
│   then claude_engine.score_fit() (Claude Haiku) scores the rest  │
└───────────────────────────┬────────────────────────────────────────┘
                            ▼  (only jobs above ATS_FIT_THRESHOLD reach here)
┌──────────────────────────────────────────────────────────────────┐
│              resume_builder.py + cover_letter.py                 │
│   jd_parser.py extracts keywords → tailored .docx per job        │
└───────────────────────────┬────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                  3-tier form-fill answer system                  │
│   1. qa_answers.py / answer_bank.py — your own saved answers     │
│   2. claude_answers.py — Claude's past answers, human-reviewable │
│   3. Claude API (Haiku) — only for genuinely new fields          │
└───────────────────────────┬────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│     Completeness gate → Submit (--live only) or manual-assist    │
│   A required field with no truthful answer never gets a guess — │
│   it triggers the 3-minute manual-assist pause instead.          │
└───────────────────────────┬────────────────────────────────────────┘
                            ▼
              pipeline_logger.py (structured run log)
                     + send_summary.command (email)
```

## Setup

### Prerequisites

```bash
# Python 3.11+
brew install python@3.11

# Install dependencies
pip install -r requirements.txt

# Install Playwright's browser
playwright install chromium
```

### First-time configuration

```bash
# 1. Copy and fill in your own candidate profile
cp raghav_profile.example.py raghav_profile.py
# Edit raghav_profile.py — this file is gitignored, your real info never leaves your machine

# 2. Copy and fill in your environment variables
cp .env.example .env
# Edit .env — Anthropic API key, Gmail app password, home address, etc.

# 3. Validate the setup before running anything
python preflight_check.py
```

Both `raghav_profile.py` and `.env` are already listed in `.gitignore` — nothing you put in them will ever be committed.

## Running it

Greenhouse is the primary, supported entry point:

```bash
# Dry run (default, even without the flag) — scores jobs, builds resumes,
# fills forms, stops right before the Submit click. No application is ever sent.
python3 greenhouse_apply_now.py --limit 5

# Live run — actually submits. Test small first.
python3 greenhouse_apply_now.py --live --limit 3

# Test one specific job posting directly, skipping discovery entirely
python3 greenhouse_apply_now.py --url "https://job-boards.greenhouse.io/company/jobs/12345" --live
```

`--limit` caps how many applications get **submitted** in that run — it has nothing to do with how many jobs get scanned or scored; the pipeline typically screens hundreds of postings per run and only a small fraction clear the fit-score bar.

Workday, LinkedIn, and Indeed each have their own `_apply_now.py` entry point with the same `--dry-run`/`--live`/`--limit` pattern, but per the table above, only Workday is worth experimenting with right now, and even that isn't reliable yet.

## Configuration

All tunables live in `config.py` — no code changes needed for common adjustments.

```python
# Version — printed in every run's log, bumped with every meaningful change
PIPELINE_VERSION = "2.14.37"

# Platform toggles
GREENHOUSE_ENABLED = True
WORKDAY_ENABLED    = False
LINKEDIN_ENABLED   = False
INDEED_ENABLED     = False

# Fit-score gate — a job must score at or above this to get a resume built
ATS_FIT_THRESHOLD = 60

# Expanded Greenhouse company discovery (opt-in via .env)
GREENHOUSE_USE_EXPANDED_DISCOVERY = False   # set true in .env to enable
GREENHOUSE_EXPANDED_BATCH_SIZE    = 150     # companies checked per run when enabled
```

### Adding a company to the curated list

```python
GREENHOUSE_COMPANIES = [
    ...
    "company-greenhouse-slug",   # the token in job-boards.greenhouse.io/<slug>
]
```

## File structure

```
job_pipeline/
│
├── greenhouse_apply_now.py       # Primary, active entry point
├── workday_apply_now.py          # Built, not yet reliable — see Known Limitations
├── linkedin_apply_now.py         # Built, disabled by design
├── indeed_apply_now.py           # Built, disabled by design
├── run_all.py                    # Legacy LinkedIn+Indeed orchestrator — not the main path anymore
│
├── claude_engine.py              # AI scoring, profile summary, cover-letter drafting
├── resume_builder.py             # ATS-optimized Word resume generator
├── jd_parser.py                  # Job description keyword extractor
├── cover_letter.py               # Cover letter generator
│
├── qa_answers.py                 # Your manually curated Q&A (gitignored)
├── answer_bank.py                # Human-confirmed exact-match answers (gitignored data)
├── claude_answers.py             # Claude's auto-saved past answers (gitignored)
├── answer_cache.py               # SQLite fallback cache
├── mail_reader.py                # Gmail IMAP polling for OTP/verify-link emails
├── secure_store.py                # Encrypted credential storage (Workday accounts, etc.)
├── pipeline_logger.py            # Structured per-run JSON logging
├── salary_helper.py               # Salary range parsing + answer selection
├── tracker.py                    # Excel application tracker
├── preflight_check.py            # Pre-run environment/import validator
│
├── config.py                     # All tunable settings — single source of truth
├── raghav_profile.example.py     # Candidate profile template → copy to raghav_profile.py
├── .env.example                  # Environment variable template → copy to .env
│
├── requirements.txt
├── safe_update.sh / snapshot.sh  # Local-first safety workflow (see below)
│
└── data/                         # Runtime data — gitignored except .gitkeep and
                                   # greenhouse_companies_full.json (public company list)
```

## Security & privacy

**Never committed to this repo** (all gitignored):

| File / pattern | Reason |
|---|---|
| `raghav_profile.py` | Real name, email, phone, address, work history |
| `.env` | API keys, Gmail app password, Workday password |
| `qa_answers.py`, `claude_answers.py`, `answer_bank.py` | Personal form answers |
| `data/*` (except `.gitkeep` and the public company list) | Application logs, run history, crash logs — anything runtime-generated |
| `.*_session/` and any Chrome-profile-shaped directory | Browser cookies, login tokens, saved passwords |
| `*.docx`, `*.pdf` | Generated resumes/cover letters and any personal documents |

To use this for yourself: copy `raghav_profile.example.py` → `raghav_profile.py` and `.env.example` → `.env`, then fill in your own information. Nothing you enter in either file ever gets pushed anywhere.

If you're auditing this repo: as of `v2.14.36`, both the current tree and the full git history have been scrubbed of the original author's personal data — see the `[2.14.35]`/`[2.14.36]` entries in `CHANGELOG.md` for exactly what was found and fixed.

## Try it right now — 3 commands

```bash
git clone git@github.com:raghava0071/job-pipeline.git && cd job-pipeline
pip install -r requirements.txt && playwright install chromium
cp raghav_profile.example.py raghav_profile.py && cp .env.example .env
```
Fill in the two files above with your own info, then run `python preflight_check.py` followed by `python3 greenhouse_apply_now.py --limit 3` (dry-run by default — nothing gets submitted until you add `--live`).

## Change management & safety net

Local-first — no internet required for day-to-day protection.

```bash
# Before making any change
bash safe_update.sh start "describe what you're trying"   # creates an experiment branch

# After the change works
#  1. Bump PIPELINE_VERSION in config.py
#  2. Log it in CHANGELOG.md
bash safe_update.sh keep                                   # merges back to main, local only

# If the change breaks something
bash safe_update.sh discard                                 # back to last working state, instantly

# Push to GitHub (secondary backup)
bash snapshot.sh --push
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full, dated priority order and the real history behind each platform's status above — including exactly what's blocking Workday, why LinkedIn/Indeed were paused, and what's planned next (Lever, Ashby).
