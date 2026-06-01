# 🤖 AI Job Application Pipeline

> **Fully automated job application engine** — scrapes LinkedIn & Indeed, scores jobs with Claude AI, builds tailored resumes, writes cover letters, and submits applications end-to-end with no manual work.

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)](https://python.org)
[![Claude AI](https://img.shields.io/badge/Claude-AI%20Powered-orange?logo=anthropic)](https://anthropic.com)
[![Playwright](https://img.shields.io/badge/Playwright-Browser%20Automation-green?logo=playwright)](https://playwright.dev)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 🎯 What It Does

The pipeline runs on autopilot and handles every step of job searching:

| Step | What Happens |
|------|-------------|
| 🔍 **Search** | Finds fresh jobs on LinkedIn (Easy Apply) and Indeed (In-Portal) |
| 🎯 **Score** | Claude AI scores each job against your profile (65% gate) |
| 📄 **Resume** | Builds a tailored Word resume with 100% ATS keyword coverage |
| ✉️ **Cover Letter** | Writes a custom cover letter for every qualifying job |
| 🤖 **Apply** | Fills the full application form and submits automatically |
| 📧 **Notify** | Emails you confirmation with your resume attached |

---

## ✨ Key Features

- **Dual platform** — LinkedIn Easy Apply + Indeed In-Portal running simultaneously
- **Claude AI scoring** — skips jobs below 65% fit, saving time and API costs
- **Smart form filling** — answers W2, visa, salary, experience questions automatically
- **CAPTCHA handling** — pauses and alerts you by email + Mac notification when CAPTCHA appears
- **Answer cache** — SQLite cache avoids repeat Claude API calls for identical questions
- **Deduplication** — never applies to the same job twice across sessions
- **ATS optimization** — resumes guaranteed 100% keyword coverage in 1-2 passes
- **Email notifications** — get notified instantly with resume attached for every application

---

## 📁 Project Structure

```
job-pipeline/
├── run_all.py              # Main entry point — runs LinkedIn + Indeed in parallel
├── linkedin_apply_now.py   # LinkedIn Easy Apply engine
├── indeed_apply_now.py     # Indeed In-Portal apply engine
├── claude_engine.py        # Claude AI — scoring, resume writing, cover letters
├── resume_builder.py       # Word document resume builder (ATS optimized)
├── cover_letter.py         # Cover letter generator
├── jd_parser.py            # Job description keyword extractor
├── answer_cache.py         # SQLite cache for form question answers
├── qa_answers.py           # Master Q&A file for common application questions
├── notifier.py             # Gmail email notifications
├── config.py               # Central configuration (queries, thresholds, paths)
├── tracker.py              # Excel application tracker
├── setup_scheduler.sh      # Daily auto-run via launchd (macOS)
└── .env                    # Your credentials (never committed — see .env.example)
```

---

## 🚀 Quick Start

### 1. Clone and install

```bash
git clone https://github.com/raghava0071/job-pipeline.git
cd job-pipeline
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Set up your profile

Create `raghav_profile.py` (not committed — stays local):

```python
PROFILE = {
    "name": "Your Name",
    "email": "you@email.com",
    "phone": "5551234567",
    "location": "City, State",
    "linkedin": "https://linkedin.com/in/yourprofile",
    "github": "https://github.com/yourusername",
    "summary": "Your professional summary...",
    "experience": [...],
    "education": [...],
    "skills": [...],
}
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
ANTHROPIC_API_KEY=sk-ant-...
NOTIFY_EMAIL=you@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
CANDIDATE_EMAIL=you@gmail.com
HOME_PHONE=5551234567
HOME_ADDRESS=123 Your Street
HOME_CITY=Your City
HOME_CITY_STATE=Your City, ST
HOME_ZIP=12345
```

### 4. Run

```bash
# Run both LinkedIn + Indeed simultaneously
python -u run_all.py --in-limit 5

# Indeed only
python -u indeed_apply_now.py --limit 5

# Dry run (no actual submission)
python -u indeed_apply_now.py --limit 5 --dry-run
```

---

## ⚙️ Configuration

Edit `config.py` to customize:

```python
FIT_THRESHOLD = 65        # Minimum Claude score to apply (%)
SEARCH_QUERIES = [        # Job search terms
    "Data Engineer Entry Level",
    "Junior Data Engineer",
    "Data Analyst Entry Level",
    ...
]
SENIOR_WORDS = [          # Skip these title keywords
    "senior", "staff", "principal", "lead", "director", ...
]
```

---

## 📋 Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Runtime |
| Playwright | Latest | Browser automation |
| python-docx | Latest | Resume/cover letter Word files |
| Anthropic SDK | Latest | Claude AI integration |
| openpyxl | Latest | Excel tracker |

Install all at once:
```bash
pip install playwright python-docx anthropic openpyxl pandas python-dotenv
```

---

## 🔒 Security

- **Never commits** `.env`, `raghav_profile.py`, browser sessions, or SQLite cache
- All personal data (name, address, phone) stored only in local `.env` — loaded at runtime
- Gmail uses App Passwords (not your main password)
- See `.gitignore` for full exclusion list

---

## 🧠 How the AI Works

### Job Scoring
Claude reads the full job description and your profile, returns a 0-100% fit score with grade. Jobs below 65% are skipped immediately — no resume built, no API cost.

### Resume Building
1. JD parser extracts ATS keywords
2. Claude rewrites your bullet points to match keywords
3. ATS score verified to reach 100% coverage
4. Word document generated with professional formatting

### Form Filling
Priority order:
1. **`qa_answers.py`** — your pre-configured answers (instant, no API cost)
2. **SQLite cache** — previously answered questions (instant)
3. **Claude API** — unknown questions answered intelligently

---

## 📊 What You Get Per Application

- ✅ Tailored `.docx` resume (ATS optimized for that specific job)
- ✅ Custom cover letter
- ✅ Email confirmation with resume attached
- ✅ Entry in `Application_Tracker.xlsx`
- ✅ Screenshot of confirmation page
- ✅ JSON log entry

---

## 🗓️ Schedule Daily Runs

Set up automatic daily execution at 8 AM:

```bash
bash setup_scheduler.sh
```

This creates a macOS launchd job that runs `run_all.py` every morning automatically.

---

## ⚠️ Responsible Use

- This tool applies only to jobs you are genuinely qualified for (65%+ AI score)
- It respects platform rate limits via built-in delays
- CAPTCHA challenges pause the bot and alert you — no CAPTCHA is bypassed
- Designed for legitimate job seekers, not spam

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

Pull requests welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
