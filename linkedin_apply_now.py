#!/opt/anaconda3/bin/python3
# =============================================================================
# LINKEDIN_APPLY_NOW.PY — Scrape + Score + Apply in ONE browser session
#
# FLOW (per job card, no URL navigation):
#   1. Load search results page (Easy Apply filter)
#   2. Click job card → right panel loads (title, company, description, Easy Apply btn)
#   3. Score with Claude AI — skip if < 65%
#   4. Build custom Word resume + cover letter
#   5. Click Easy Apply in right panel → fill form → submit
#   6. Log → next card
#
# WHY THIS WORKS: Job is live (we can see it). Apply immediately — no expiry risk.
#
# USAGE:
#   python linkedin_apply_now.py
#   python linkedin_apply_now.py --limit 5   # max 5 applies
#   python linkedin_apply_now.py --dry-run   # score + build resumes, no submit
# =============================================================================

import sys, time, json as json, argparse
from pathlib import Path
from datetime import datetime, timedelta

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import config as cfg
import answer_cache as _cache   # SQLite answer cache — avoids repeat Claude calls
try:
    import qa_answers as _qa    # Master Q&A — checked before cache and Claude
except ImportError:
    _qa = None
try:
    import claude_answers as _claude_ans  # Auto-saved Claude answers (human-reviewable)
except ImportError:
    _claude_ans = None
try:
    from salary_helper import pick_salary as _pick_salary, salary_rule_for_prompt as _salary_rule
except ImportError:
    _pick_salary = lambda jd, title: "75000"
    _salary_rule = lambda jd, title: "- salary: answer 75000 (plain number only)"
import notifier                  # Gmail notifications on each apply (optional)                        # ← global values for the whole project

DATA_DIR    = cfg.DATA_DIR
SESSION_DIR = cfg.SESSION_LI
LOG_FILE      = cfg.LOG_FILE
SCREENSHOTS   = cfg.BASE_DIR / "screenshots"
SCREENSHOTS.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
cfg.RESUMES_DIR.mkdir(parents=True, exist_ok=True)
cfg.COVER_DIR.mkdir(parents=True, exist_ok=True)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("pip install playwright && python -m playwright install chromium")

# ── Pull all settings from config.py (single source of truth) ─────────────────
SEARCH_QUERIES = cfg.LINKEDIN_QUERIES

def is_good_level(title):
    t = title.lower() + " "   # trailing space guards word-boundary matches
    return not any(bad in t for bad in cfg.SENIOR_WORDS)

def is_relevant_role(title):
    """
    Return True if the job title is in Raghav's target domain.
    Prevents applying to off-domain titles like 'Scheduler', 'Project Engineer',
    'HR Coordinator', etc. that occasionally appear in data-keyword searches.
    """
    t = title.lower() + " "
    return any(kw in t for kw in getattr(cfg, "TARGET_ROLE_KEYWORDS", set()))

def is_fake_job(title, company, description, applicant_count=0, location="",
                has_safety_warning=False, is_company_verified=False,
                company_followers=-1, company_employees=-1):
    """
    Return (True, reason) if job looks fake/spam — skip before scoring.
    Multi-layer check — ALL signals pulled from cfg.py for easy tuning.

    LAYER 0 — LinkedIn native signals (LinkedIn's own fraud detection)
    LAYER 1 — Title signals (cheapest — string contains)
    LAYER 2 — Company name signals
    LAYER 3 — Description signals (fraud / body-shop / international)
    LAYER 4 — Applicant count / length heuristics
    """
    t = title.lower() + " "   # trailing space so word-end patterns work
    c = company.lower().strip()
    d = description.lower()
    loc = location.lower()

    # ── LAYER 0: LinkedIn's own fraud/trust signals ───────────────────────────
    # These cost zero — LinkedIn already did the analysis. Trust their flags.

    # 0A. LinkedIn safety warning banner — hard stop, no exceptions.
    if has_safety_warning and getattr(cfg, "LINKEDIN_SKIP_ON_SAFETY_WARNING", True):
        return True, "LinkedIn safety warning banner detected"

    # 0B. Company verified status — if LINKEDIN_TRUST_VERIFIED_ONLY=True, skip unverified.
    if getattr(cfg, "LINKEDIN_TRUST_VERIFIED_ONLY", False) and not is_company_verified:
        return True, "company not LinkedIn-verified (TRUST_VERIFIED_ONLY mode)"

    # 0C. Follower count too low — real tech employers have followers.
    min_followers = getattr(cfg, "LINKEDIN_MIN_COMPANY_FOLLOWERS", 50)
    if company_followers >= 0 and company_followers < min_followers:
        return True, f"company too few LinkedIn followers ({company_followers} < {min_followers})"

    # 0D. Employee count too low — 1-5 employees claiming to hire a data team.
    min_employees = getattr(cfg, "LINKEDIN_MIN_COMPANY_EMPLOYEES", 5)
    if company_employees >= 0 and company_employees < min_employees:
        return True, f"company too few employees ({company_employees} < {min_employees})"

    # ── LAYER 1A: Title keyword signals (spam phrases + off-target roles) ─────
    for w in cfg.FAKE_JOB_TITLE_WORDS:
        if w in t:
            return True, f"off-target title: '{w}'"

    # ── LAYER 1B: Title typo signals (bot-generated postings) ─────────────────
    for w in getattr(cfg, "FAKE_JOB_TITLE_TYPOS", set()):
        if w in t:
            return True, f"title typo/spam: '{w.strip()}'"

    # ── LAYER 2A: Company name — known bad actors ──────────────────────────────
    for w in cfg.FAKE_JOB_COMPANY_WORDS:
        if w in c:
            return True, f"blocked company: '{w}'"

    # ── LAYER 2B: Company name suffix patterns (Commonwealth / African corps) ──
    for suffix in getattr(cfg, "FAKE_COMPANY_SUFFIX_WORDS", set()):
        if c.endswith(suffix) or c.endswith(" " + suffix):
            return True, f"non-US company suffix: '{suffix}'"

    # ── LAYER 2C: Suspicious patterns inside company name ─────────────────────
    for pat in getattr(cfg, "FAKE_COMPANY_NAME_PATTERNS", set()):
        if pat in c:
            return True, f"suspicious company name pattern: '{pat}'"

    # ── LAYER 2D: Company name contains job-board language ────────────────────
    # e.g. "Jobs in United States and Europe and United Kingdom"
    if any(phrase in c for phrase in ("jobs in ", "openings in ", "hiring in ", "jobs via ")):
        return True, "company name looks like a job aggregator/board"

    # ── LAYER 2E: Non-ASCII company name — strong international signal ─────────
    try:
        c.encode("ascii")
    except UnicodeEncodeError:
        return True, "non-ASCII company name (international posting)"

    # ── LAYER 2F: Blocked companies list ──────────────────────────────────────
    # NOTE: cfg.BLOCKED_COMPANIES uses Workday subdomain keys (e.g. "nc", "hcsc")
    # which are too short for substring matching against company names.
    # Only use entries that are ≥5 characters to avoid false positives.
    for blocked in getattr(cfg, "BLOCKED_COMPANIES", set()):
        if len(blocked) >= 5 and blocked in c:
            return True, f"blocked company: '{blocked}'"

    # ── LAYER 3A: Blatant fraud signals in description ────────────────────────
    for w in cfg.FAKE_JOB_DESC_SIGNALS:
        if w in d:
            return True, f"fraud signal in description: '{w}'"

    # ── LAYER 3B: International / non-US body-shop signals in description ─────
    for w in getattr(cfg, "FAKE_JOB_DESC_INTL_SIGNALS", set()):
        if w in d:
            return True, f"non-US/body-shop signal: '{w}'"

    # ── LAYER 3C: Job location is outside the US ──────────────────────────────
    for loc_signal in getattr(cfg, "FAKE_JOB_LOCATION_SIGNALS", set()):
        if loc_signal in loc:
            return True, f"non-US location: '{loc_signal}' in '{location}'"

    # ── LAYER 4A: Too many applicants → stale bait posting ────────────────────
    max_ap = getattr(cfg, "LINKEDIN_MAX_APPLICANTS", 400)
    if applicant_count and applicant_count >= max_ap:
        return True, f"too many applicants ({applicant_count} ≥ {max_ap})"

    # ── LAYER 4B: Description too short — not a real JD ──────────────────────
    if len(description.strip()) < 200:
        return True, f"description too short ({len(description.strip())} chars)"

    # ── LAYER 4C: Description has almost no tech keywords ─────────────────────
    # Real data/tech JDs always mention at least 2 tech tools or concepts.
    tech_kws = {
        "python", "sql", "data", "pipeline", "etl", "analytics", "engineer",
        "cloud", "aws", "azure", "gcp", "spark", "kafka", "tableau", "power bi",
        "machine learning", "ai", "database", "api", "bigquery", "snowflake",
        "databricks", "dbt", "airflow", "pandas", "numpy", "scikit", "tensorflow",
        "pytorch", "docker", "kubernetes", "git", "postgresql", "mysql", "mongodb",
        "reporting", "dashboard", "bi ", "warehouse", "lakehouse",
    }
    tech_hit = sum(1 for kw in tech_kws if kw in d)
    if tech_hit < 2:
        return True, f"description lacks tech content ({tech_hit} tech keywords)"

    return False, ""

def score_company_trust(company, has_safety_warning=False, is_company_verified=False,
                        company_followers=-1, company_employees=-1):
    """
    Returns a trust score 0-100 for the company based on LinkedIn signals.
    > 70  → trusted, skip legitimacy check
    30-70 → grey zone, run Claude legitimacy check
    < 30  → skip (fake/untrustworthy)
    100   → whitelisted known company, skip ALL checks
    """
    weights = getattr(cfg, "TRUST_SCORE_WEIGHTS", {})
    c_lower = company.lower().strip()

    # ── Whitelist check — instant 100 ─────────────────────────────────────────
    for known in getattr(cfg, "COMPANY_WHITELIST", set()):
        if known in c_lower:
            return 100, "whitelisted"

    # ── Safety warning — instant 0 ────────────────────────────────────────────
    if has_safety_warning:
        return 0, "LinkedIn safety warning"

    score = weights.get("base_score", 50)
    reasons = []

    # ── Verified badge ─────────────────────────────────────────────────────────
    if is_company_verified:
        score += weights.get("verified_badge", 35)
        reasons.append("verified")

    # ── Follower count ─────────────────────────────────────────────────────────
    if company_followers >= 0:
        if company_followers >= 500:
            score += weights.get("followers_500_plus", 20)
            reasons.append(f"{company_followers:,} followers")
        elif company_followers >= 100:
            score += weights.get("followers_100_to_500", 10)
            reasons.append(f"{company_followers} followers")
        elif company_followers >= 50:
            score += weights.get("followers_50_to_100", 5)
            reasons.append(f"{company_followers} followers")
        else:
            score += weights.get("followers_under_50", -40)
            reasons.append(f"only {company_followers} followers")

    # ── Employee count ─────────────────────────────────────────────────────────
    if company_employees >= 0:
        if company_employees >= 200:
            score += weights.get("employees_200_plus", 20)
            reasons.append(f"{company_employees} employees")
        elif company_employees >= 50:
            score += weights.get("employees_50_to_200", 10)
            reasons.append(f"{company_employees} employees")
        elif company_employees >= 5:
            score += weights.get("employees_5_to_50", 5)
            reasons.append(f"{company_employees} employees")
        else:
            score += weights.get("employees_under_5", -40)
            reasons.append(f"only {company_employees} employees")

    score = max(0, min(100, score))
    return score, " | ".join(reasons) if reasons else "no signals"


def check_job_legitimacy(title, company, description, trust_score,
                         company_followers=-1, company_employees=-1,
                         is_company_verified=False):
    """
    Claude haiku legitimacy check — separate from fit scoring.
    Asks: 'Is this a real job at a real company?' not 'Does Raghav fit?'
    Only called for grey-zone companies (trust score 30-70).
    Returns (is_legitimate: bool, reason: str, confidence: int)
    """
    import anthropic, os, json as _json, re as _re

    _api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not _api_key:
        try:
            env_path = Path.home() / "job_pipeline" / ".env"
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    _api_key = line.split("=", 1)[1].strip()
        except Exception:
            pass

    if not _api_key:
        return True, "API key not found — skipping legitimacy check", 50

    signals = []
    if is_company_verified: signals.append("LinkedIn-verified company")
    if company_followers >= 0: signals.append(f"{company_followers} LinkedIn followers")
    if company_employees >= 0: signals.append(f"{company_employees} employees on LinkedIn")
    signals.append(f"Trust score: {trust_score}/100")

    prompt = f"""You are a job fraud detection expert. Evaluate whether this LinkedIn job posting is REAL or a SCAM/GHOST JOB.

JOB: {title}
COMPANY: {company}
COMPANY SIGNALS: {', '.join(signals) if signals else 'none available'}

JOB DESCRIPTION (first 1500 chars):
{description[:1500]}

Red flags for FAKE jobs:
- Vague or generic responsibilities with no specific tech stack
- No mention of actual product, team, or business context
- Company sounds unrelated to data/tech (logistics, tourism, NGO, events)
- Suspiciously short or templated description
- No mention of the company's actual business
- Inflated titles for tiny companies (e.g. "Senior Data Engineer" at a 3-person company)
- Description copy-pasted from multiple other postings
- Contact via WhatsApp/email/Telegram instead of ATS
- Requests personal info (SSN, bank details, passport) upfront

Green flags for REAL jobs:
- Specific tech stack mentioned (e.g. Spark, dbt, Snowflake, specific cloud)
- Mentions actual team size, product, or business context
- Clear responsibilities tied to a real business problem
- Company name matches a recognizable tech/data employer
- Professional ATS-style application flow

Reply ONLY in JSON:
{{"legitimate": true, "confidence": 85, "reason": "one sentence", "red_flags": [], "green_flags": ["specific tech stack", "clear business context"]}}

confidence = 0-100 (how sure you are)
legitimate = true if real job, false if scam/ghost/fake"""

    try:
        client = anthropic.Anthropic(api_key=_api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        data = _json.loads(m.group()) if m else {}
        legitimate  = data.get("legitimate", True)
        confidence  = int(data.get("confidence", 50))
        reason      = data.get("reason", "")
        red_flags   = data.get("red_flags", [])
        return legitimate, reason, confidence, red_flags
    except Exception as e:
        # On error, give benefit of doubt but log it
        return True, f"legitimacy check error: {e}", 50, []


def build_url(kw):
    import urllib.parse
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode({
        "keywords": kw, "location": "United States",
        "sortBy": "DD", "f_TPR": "r604800", "f_LF": "f_AL",
    })

FINGERPRINT_FILE = cfg.DATA_DIR / "desc_fingerprints.json"

def load_fingerprints():
    try:
        return json.loads(FINGERPRINT_FILE.read_text()) if FINGERPRINT_FILE.exists() else {}
    except Exception:
        return {}

def save_fingerprints(fp):
    FINGERPRINT_FILE.write_text(json.dumps(fp, indent=2))

def check_description_fingerprint(description, company, fingerprints):
    """
    Hash the first 300 chars of the description (normalized).
    If this hash was seen from a DIFFERENT company before → scam template.
    Returns (is_scam_template: bool, reason: str)
    """
    if not getattr(cfg, "LINKEDIN_FINGERPRINT_ENABLED", True):
        return False, ""

    import hashlib
    # Normalize: lowercase, strip whitespace, collapse spaces
    normalized = " ".join(description[:300].lower().split())
    fp_hash = hashlib.sha1(normalized.encode()).hexdigest()[:16]

    if fp_hash not in fingerprints:
        fingerprints[fp_hash] = {"companies": [company], "count": 1}
        return False, ""

    entry = fingerprints[fp_hash]
    companies = entry.get("companies", [])
    if company not in companies:
        companies.append(company)
        entry["companies"] = companies
        entry["count"] = len(companies)

    min_companies = getattr(cfg, "LINKEDIN_FINGERPRINT_MIN_COMPANIES", 2)
    if len(companies) >= min_companies:
        others = [c for c in companies if c != company][:3]
        return True, f"scam template seen from {len(companies)} companies: {others}"

    return False, ""


def load_log():
    try:
        return json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
    except:
        return []

def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2))

def already_applied(url, log, title="", company=""):
    """Dedup by URL (normalized) AND by company+title pair."""
    key = url.split("?")[0].rstrip("/")
    for e in log:
        if e.get("status") not in ("Applied", "Already Applied"):
            continue
        if e.get("url","").split("?")[0].rstrip("/") == key:
            return True
        if title and company:
            if (e.get("company","").lower().strip() == company.lower().strip()
                    and e.get("title","").lower().strip() == title.lower().strip()):
                return True
    return False

def ensure_login(page):
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
    time.sleep(3)
    if "feed" in page.url and page.locator("nav").count() > 0:
        print("  ✅  LinkedIn: logged in")
        return

    # When running via scheduler (no terminal), input() crashes with EOF.
    # Instead: send email alert and wait up to 5 minutes for user to log in manually.
    print("\n  🔐  LinkedIn not logged in — sending alert and waiting up to 5 minutes...")
    try:
        import notifier
        notifier.send_alert(
            subject="🔐 LinkedIn Login Required — Pipeline Paused",
            body=(
                "The job pipeline needs you to log in to LinkedIn.\n\n"
                "1. Open the Chromium browser window on your Mac\n"
                "2. Log in to LinkedIn\n"
                "3. The pipeline will continue automatically within 5 minutes\n\n"
                "If you don't log in, this run will be skipped."
            )
        )
    except Exception as e:
        print(f"  ⚠  Could not send alert: {e}")

    # Wait up to 5 minutes for login
    for i in range(60):
        time.sleep(5)
        try:
            page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=10000)
            time.sleep(2)
            if "feed" in page.url and page.locator("nav").count() > 0:
                print("  ✅  LinkedIn: logged in successfully")
                return
        except:
            pass
        if i % 12 == 11:
            print(f"  ⏳ Still waiting for LinkedIn login... ({(i+1)*5}s elapsed)")

    print("  ❌ LinkedIn login timeout — skipping this run")

def extract_right_panel(page):
    """Extract job details from the right panel after clicking a card."""
    return page.evaluate("""
        () => {
            let title = '';
            for (const sel of [
                '.job-details-jobs-unified-top-card__job-title h1',
                '.jobs-unified-top-card__job-title h1',
                'h1.t-24', 'h2.t-24', 'h1'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 2) { title = el.innerText.trim(); break; }
            }

            let company = '';
            for (const sel of [
                '.job-details-jobs-unified-top-card__company-name a',
                '.jobs-unified-top-card__company-name a',
                '.jobs-unified-top-card__subtitle a',
                '[class*="company-name"]'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 1) { company = el.innerText.trim(); break; }
            }

            let location = '';
            for (const sel of ['.jobs-unified-top-card__bullet', '.topcard__flavor--bullet']) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim()) { location = el.innerText.trim(); break; }
            }

            let description = '';
            for (const sel of ['#job-details', '.jobs-description__content', '.jobs-description-content__text']) {
                const el = document.querySelector(sel);
                if (el && el.innerText.length > 50) { description = el.innerText.substring(0, 3500); break; }
            }

            // Check if Easy Apply button is in right panel
            const allBtns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            let hasEasyApply = false;
            for (const b of allBtns) {
                const t  = (b.textContent||'').toLowerCase().trim();
                const al = (b.getAttribute('aria-label')||'').toLowerCase();
                if ((t.includes('easy apply') || al.includes('easy apply to'))) {
                    hasEasyApply = true; break;
                }
            }

            // Get current job URL from page
            const jobUrl = window.location.href;

            // Detect Workday apply links
            let workdayUrl = '';
            const WD_DOMAINS = ['myworkdayjobs.com', 'workday.com/jobs'];
            for (const b of allBtns) {
                const href = (b.getAttribute('href') || '');
                if (WD_DOMAINS.some(d => href.includes(d))) {
                    workdayUrl = href;
                    break;
                }
            }
            // Also check all links on the page
            if (!workdayUrl) {
                const allLinks = Array.from(document.querySelectorAll('a[href]'));
                for (const a of allLinks) {
                    const href = a.getAttribute('href') || '';
                    if (WD_DOMAINS.some(d => href.includes(d))) {
                        workdayUrl = href;
                        break;
                    }
                }
            }

            // Applicant count — "Be among the first 25" or "500+ applicants"
            let applicantCount = 0;
            const apSelectors = [
                '.jobs-unified-top-card__applicant-count',
                '.tvm__text--neutral',
                '[class*="applicant"]',
                '.jobs-unified-top-card__subtitle-primary-grouping span',
            ];
            for (const sel of apSelectors) {
                for (const el of document.querySelectorAll(sel)) {
                    const t = el.innerText || '';
                    const m = t.match(/(\d+)\+?\s*applicant/i);
                    if (m) { applicantCount = parseInt(m[1]); break; }
                    if (t.toLowerCase().includes('first 25')) { applicantCount = 10; break; }
                    if (t.toLowerCase().includes('first 50')) { applicantCount = 30; break; }
                }
                if (applicantCount > 0) break;
            }

            // Reposted / Promoted flags
            const pageText = document.body.innerText.toLowerCase();
            const isReposted = pageText.includes('reposted');
            const isPromoted = pageText.includes('promoted');

            // ── LinkedIn native fraud / trust signals ─────────────────────────
            // 1. Safety warning banner — LinkedIn's own fraud team flagged this job.
            //    Appears as a yellow/orange alert above the job description.
            //    Text varies: "Safety reminder", "LinkedIn flagged", "Be cautious", etc.
            let hasSafetyWarning = false;
            const safetySelectors = [
                '[data-test-job-safety-reminder]',
                '.job-safety-reminder',
                '.jobs-safety-reminder',
                '[class*="safety-reminder"]',
                '[class*="safety_reminder"]',
                '[class*="fraud-warning"]',
                '[class*="scam-warning"]',
            ];
            for (const sel of safetySelectors) {
                if (document.querySelector(sel)) { hasSafetyWarning = true; break; }
            }
            // Also scan visible text for safety-related phrases
            if (!hasSafetyWarning) {
                const safetyPhrases = [
                    'safety reminder',
                    'be cautious',
                    'linkedin has flagged',
                    'report this job',
                    'suspicious activity',
                    'legitimate employers never ask',
                    'never pay to apply',
                    'be wary of',
                ];
                const bodyLower = document.body.innerText.toLowerCase();
                hasSafetyWarning = safetyPhrases.some(p => bodyLower.includes(p));
            }

            // 2. Company verified badge — blue checkmark next to company name.
            let isCompanyVerified = false;
            const verifiedSelectors = [
                '[data-test-job-company-verified]',
                '[aria-label*="verified"]',
                '[class*="verified-badge"]',
                '[class*="company-verified"]',
                '.artdeco-icon--verified',
                'li-icon[type="linkedin-bug"]',  // LinkedIn verified icon type
            ];
            for (const sel of verifiedSelectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const label = (el.getAttribute('aria-label') || el.textContent || '').toLowerCase();
                    if (label.includes('verif') || sel.includes('verif')) {
                        isCompanyVerified = true; break;
                    }
                }
            }

            // 3. Company follower count — shown as "X followers" near company name.
            let companyFollowers = -1;  // -1 = not found
            const followerSelectors = [
                '.jobs-unified-top-card__company-info',
                '.job-details-jobs-unified-top-card__company-info',
                '.jobs-company__box',
                '[class*="company-info"]',
                '.artdeco-entity-lockup__caption',
            ];
            for (const sel of followerSelectors) {
                for (const el of document.querySelectorAll(sel)) {
                    const txt = el.innerText || '';
                    const m = txt.match(/([\d,]+)\s*follower/i);
                    if (m) {
                        companyFollowers = parseInt(m[1].replace(/,/g, ''));
                        break;
                    }
                }
                if (companyFollowers >= 0) break;
            }
            // Fallback: scan all spans/divs for follower text
            if (companyFollowers < 0) {
                for (const el of document.querySelectorAll('span, div, p')) {
                    const txt = (el.innerText || '').trim();
                    if (txt.length < 60) {
                        const m = txt.match(/^([\d,]+)\s*follower/i);
                        if (m) { companyFollowers = parseInt(m[1].replace(/,/g, '')); break; }
                    }
                }
            }

            // 4. Company employee count — shown in company info panel.
            let companyEmployees = -1;  // -1 = not found
            const empPatterns = [
                /(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\s*employee/i,   // "10-50 employees"
                /(\d[\d,]+)\s*\+?\s*employee/i,                  // "500+ employees"
                /(\d[\d,]+)\s*people/i,                          // "200 people"
                /company size[:\s]+(\d[\d,]+)/i,
            ];
            const companyPanel = document.querySelector(
                '.jobs-company__box, .job-details-company, [class*="company-box"]'
            );
            const searchArea = companyPanel || document.body;
            const searchText = searchArea.innerText || '';
            for (const pat of empPatterns) {
                const m = searchText.match(pat);
                if (m) {
                    // For range patterns like "10-50", take the lower bound
                    companyEmployees = parseInt(m[1].replace(/,/g, ''));
                    break;
                }
            }

            return { title, company, location, description, hasEasyApply, jobUrl, workdayUrl,
                     applicantCount, isReposted, isPromoted,
                     // LinkedIn native trust signals:
                     hasSafetyWarning, isCompanyVerified,
                     companyFollowers, companyEmployees };
        }
    """) or {}

def click_easy_apply(page):
    """Click the Easy Apply button using native Playwright click."""
    for sel in [
        "button[aria-label*='Easy Apply to']",
        "button[aria-label='Easy Apply']",
        "button:has-text('Easy Apply')",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible() and not btn.is_disabled():
                btn.click()
                return True
        except:
            pass
    # dispatchEvent fallback
    return page.evaluate("""
        () => {
            const all = Array.from(document.querySelectorAll('button, [role="button"]'));
            for (const el of all) {
                const t  = (el.textContent||'').toLowerCase().trim();
                const al = (el.getAttribute('aria-label')||'').toLowerCase();
                if ((t === 'easy apply' || al.includes('easy apply to')) && !el.disabled) {
                    el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
                    return true;
                }
            }
            return false;
        }
    """)

def fill_and_submit_form(page, resume_path, job_title="", company=""):
    """
    Walk the Easy Apply multi-step form.
    Claude AI reads every question on every step and decides the answer.
    No dumb keyword matching — real intelligence for every field.
    """
    import raghav_profile as rp
    import anthropic, os, json as _json

    p = rp.PROFILE

    # ── Claude client ──────────────────────────────────────────────────────────
    _api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not _api_key:
        try:
            env_path = Path.home() / "job_pipeline" / ".env"
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    _api_key = line.split("=", 1)[1].strip()
        except:
            pass
    _claude = anthropic.Anthropic(api_key=_api_key)

    # ── Full profile context sent to Claude for every form step ───────────────
    skill_years_str = "\n".join(
        f"  {k}: {v} years" for k,v in cfg.SKILL_YEARS.items()
    )
    PROFILE_CONTEXT = f"""
Candidate: Raghavendra Karanam
Email: {p.get('email','your_email@gmail.com')}
Phone: {p.get('phone','7038529618')}
Location: Delray Beach, FL 33484
LinkedIn: {p.get('linkedin_url','https://www.linkedin.com/in/raghavendra-karanam')}
GitHub: {p.get('github_url','https://github.com/yourusername')}
Portfolio: {p.get('portfolio_url','https://www.linkedin.com/in/raghavendra-karanam')}

Education:
  Master of Science, Data Science & Analytics — Florida Atlantic University (May 2025), GPA 3.5
  B.Tech, Computer Science & Engineering — JNTU (2022)

Work Authorization: F-1 OPT/STEM OPT — legally authorized to work in USA, NO sponsorship needed
Total Professional Experience: 3+ years (including undergrad projects, internships, grad research)
Expected Salary: {_pick_salary(jd_text if "jd_text" in dir() else "", job_title)} (plain number, no $ signs)
Veteran: No | Disability: No | Gender: Male (prefer not to say) | Ethnicity: Asian (prefer not to say)
Willing to relocate: Yes | Work mode: Remote / Hybrid / On-site

Experience by skill (be accurate — candidate used these since undergrad):
{skill_years_str}

Key skills: Python, SQL, PySpark, Spark, Kafka, Airflow, dbt, AWS, Azure, GCP, Snowflake,
Power BI, Tableau, Docker, Kubernetes, TensorFlow, PyTorch, scikit-learn, pandas, numpy,
FastAPI, PostgreSQL, MySQL, MongoDB, Databricks, Azure Data Factory, BigQuery, Redshift,
Git, GitHub, Terraform, REST APIs

IMPORTANT — when a form asks "how many years of X experience":
- Use the per-skill years above (e.g. Python=4, SQL=4, pandas=4, data engineering=3)
- If a skill is not in the list, default to 2 years
- Never answer less than 2 for any data/programming skill

Applying for: {job_title} at {company}
""".strip()

    CONFIRM = [
        "application was sent", "your application was submitted",
        "application submitted", "applied to", "successfully applied",
        "your application has been", "application complete",
        "application was sent to", "sent your application",
        "you've applied", "you applied", "application received",
        "thank you for applying", "thanks for applying",
    ]

    def confirmed():
        txt = page.evaluate("() => document.body.innerText.toLowerCase()")
        return any(s in txt for s in CONFIRM)

    def modal_open():
        return page.evaluate("() => !!document.querySelector('[data-test-modal], .jobs-easy-apply-modal, [role=\"dialog\"][aria-label*=\"pply\"]')")

    def extract_form_fields():
        """
        Scrape the current form step and return a structured list of all
        visible questions with their type and available options.
        """
        return page.evaluate("""
        () => {
            const fields = [];
            const modal = document.querySelector('[data-test-modal], .jobs-easy-apply-modal, [role="dialog"]');
            const root  = modal || document.body;

            function cleanLabel(raw) {
                var lines = raw.split(String.fromCharCode(10)).map(function(l){return l.trim();}).filter(function(l){return l.length>2;});
                return lines[0] || raw.trim();
            }

            function getLabel(el) {
                const id = el.id;
                if (id) {
                    const lbl = root.querySelector('label[for="' + id + '"]');
                    if (lbl) return cleanLabel(lbl.innerText);
                }
                // walk up to find a label or legend
                let p = el.parentElement;
                for (let i=0; i<6 && p; i++, p=p.parentElement) {
                    const leg = p.querySelector('legend');
                    if (leg) return cleanLabel(leg.innerText);
                    // artdeco form label (more specific — check before generic label)
                    const al = p.querySelector('.artdeco-form-element__label, [data-test-form-element-label]');
                    if (al) return cleanLabel(al.innerText);
                    const lbl = p.querySelector('label');
                    if (lbl && !lbl.htmlFor) return cleanLabel(lbl.innerText);
                }
                return el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '';
            }

            function isVisible(el) {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && !el.disabled;
            }

            // text / tel / number / url inputs
            root.querySelectorAll('input[type=text],input[type=tel],input[type=number],input[type=url],input[type=email]').forEach(el => {
                if (!isVisible(el) || el.value.trim()) return;
                fields.push({type:'text', id:el.id||'', label:getLabel(el), current:el.value});
            });

            // textareas
            root.querySelectorAll('textarea').forEach(el => {
                if (!isVisible(el) || el.value.trim()) return;
                fields.push({type:'textarea', id:el.id||'', label:getLabel(el), current:el.value});
            });

            // selects
            root.querySelectorAll('select').forEach(el => {
                if (!isVisible(el)) return;
                const opts = Array.from(el.options).map(o=>({value:o.value, text:o.text.trim()}));
                fields.push({type:'select', id:el.id||'', label:getLabel(el),
                             current:el.value, options:opts});
            });

            // radio groups — group by name
            const radioGroups = {};
            root.querySelectorAll('input[type=radio]').forEach(el => {
                if (!isVisible(el)) return;
                const nm = el.name || el.id;
                if (!radioGroups[nm]) radioGroups[nm] = {name:nm, options:[], question:getLabel(el)};
                const lbl = root.querySelector('label[for="'+el.id+'"]');
                radioGroups[nm].options.push({id:el.id, value:el.value, label:lbl?lbl.innerText.trim():'', checked:el.checked});
            });
            Object.values(radioGroups).forEach(g => {
                if (g.options.some(o=>o.checked)) return; // already answered
                fields.push({type:'radio', name:g.name, label:g.question, options:g.options});
            });

            // checkboxes
            root.querySelectorAll('input[type=checkbox]').forEach(el => {
                if (!isVisible(el) || el.checked) return;
                fields.push({type:'checkbox', id:el.id||'', label:getLabel(el)});
            });

            return fields;
        }
        """)

    def claude_answer_all_fields(fields, step_num):
        """
        Send ALL visible questions on this form step to Claude at once.
        Claude returns a JSON mapping field_id/name → answer.
        """
        if not fields:
            return {}

        # Truncate per-field option lists so prompt stays manageable
        fields_trimmed = []
        for f in fields:
            fc = dict(f)
            if "options" in fc and len(fc["options"]) > 20:
                fc["options"] = fc["options"][:20] + [{"value":"...", "label":"(truncated)"}]
            fields_trimmed.append(fc)
        fields_desc = _json.dumps(fields_trimmed, indent=2)[:4000]   # hard cap
        prompt = f"""You are filling out a job application form on behalf of this candidate.

CANDIDATE PROFILE:
{PROFILE_CONTEXT}

FORM FIELDS ON THIS STEP (step {step_num}):
{fields_desc}

For EACH field, return the best answer. Return ONLY a JSON object like:
{{
  "field_id_or_name": "answer",
  ...
}}

Rules:
- For text/tel/number/url/email: return the exact string to type
- For textarea / open-ended essay questions: return a genuine, professional 2-4 sentence answer drawing from the candidate's background. NEVER return blank for a required field — blank required fields block form submission.
- For select: return the EXACT option value (from the "value" key in options), pick the most appropriate
- For radio: return the EXACT option id to click
- For checkbox: return "check" if it should be checked (e.g. agreement/terms), else "skip"
- Work authorization: candidate IS authorized (OPT), does NOT need sponsorship → answer Yes/authorized
- For "years of X experience" questions: use the per-skill years in the profile above (Python=4, SQL=4, etc.)
- Salary: 75000 (or 75000-90000 if a range is needed)
- For industry-specific questions where candidate has no direct experience (e.g. healthcare, senior care): answer honestly but positively — highlight transferable skills and eagerness to learn
- For unknown/unclear questions: use best professional judgment from the profile context; never leave required fields blank
- NEVER answer less than 2 for any data/programming/analytics skill
- Do NOT add any explanation — return raw JSON only"""

        # ── Check qa_answers FIRST, then cache, then Claude ─────────────────────
        cached_answers = {}
        uncached_fields = []
        for f in fields:
            lbl = f.get("label", f.get("name", ""))
            fid = f.get("id","") or f.get("name","")

            # 1. qa_answers.py — master Q&A (manually curated, highest priority)
            qa_hit = _qa.get_answer(lbl) if (_qa and lbl) else None
            if qa_hit is not None:
                cached_answers[fid] = qa_hit
                continue

            # 2. claude_answers.py — Claude's past answers (auto-saved, human-reviewable)
            ca_hit = _claude_ans.get(lbl) if (_claude_ans and lbl) else None
            if ca_hit is not None:
                cached_answers[fid] = ca_hit
                continue

            # 3. SQLite cache (legacy)
            cached = _cache.get(lbl)
            if cached is not None:
                cached_answers[fid] = cached
                # Promote to claude_answers.py so it's visible and editable
                if _claude_ans:
                    _claude_ans.save(lbl, cached)
            else:
                uncached_fields.append(f)

        if cached_answers and not uncached_fields:
            return cached_answers   # 100% cache hit — no Claude call needed

        # Re-build prompt for only the uncached fields
        if uncached_fields:
            fields_trimmed2 = []
            for f in uncached_fields:
                fc = dict(f)
                if "options" in fc and len(fc["options"]) > 20:
                    fc["options"] = fc["options"][:20] + [{"value":"...", "label":"(truncated)"}]
                fields_trimmed2.append(fc)
            fields_desc2 = _json.dumps(fields_trimmed2, indent=2)[:4000]
            prompt = prompt.replace(fields_desc, fields_desc2)  # swap in uncached only

        try:
            resp = _claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,   # essay questions can need 3000+ tokens for 20+ fields
                messages=[{"role":"user","content":prompt}]
            )
            raw = resp.content[0].text.strip()
            # Strip markdown code fences
            if "```" in raw:
                raw = raw.split("```")[1].replace("json","").strip()
            # Try full parse first, then greedy JSON extraction as fallback
            try:
                new_answers = _json.loads(raw)
            except Exception:
                import re as _re
                m = _re.search(r'\{.*\}', raw, _re.DOTALL)
                if m:
                    try:
                        new_answers = _json.loads(m.group())
                    except Exception:
                        new_answers = {}
                else:
                    new_answers = {}
            # Save new answers to claude_answers.py (permanent, reviewable) + SQLite cache
            for f in uncached_fields:
                lbl = f.get("label", f.get("name",""))
                fid = f.get("id","") or f.get("name","")
                if fid in new_answers and new_answers[fid]:
                    _cache.save(lbl, new_answers[fid])
                    if _claude_ans:
                        _claude_ans.save(lbl, str(new_answers[fid]))
            merged = {**cached_answers, **new_answers}
            return merged
        except Exception as e:
            print(f"        ⚠ Claude field-fill error: {e}")
            # Profile-based fallback — never leave fields blank
            PROFILE_FALLBACK = {
                "work authorization": "Yes", "authorized to work": "Yes",
                "legally authorized": "Yes", "sponsorship": "No",
                "visa": "F-1 STEM OPT", "salary": _pick_salary(locals().get("jd_text",""), locals().get("job_title","")), "compensation": _pick_salary(locals().get("jd_text",""), locals().get("job_title","")),
                "hourly rate": "40",
                "start date": (datetime.now() + timedelta(days=14)).strftime("%m/%d/%Y"),
                "notice": (datetime.now() + timedelta(days=14)).strftime("%m/%d/%Y"),
                "relocat": "No", "remote": "Yes", "gender": "I don't wish to answer",
                "ethnicity": "I don't wish to answer", "race": "I don't wish to answer",
                "veteran": "I am not a protected veteran", "disability": "I don't wish to answer",
                "years of experience": "2", "background check": "Yes", "drug test": "Yes",
                "18 or older": "Yes", "us citizen": "No", "green card": "No",
                "sms": "Yes", "text message": "Yes", "consent to receive": "Yes",
                "opt in": "Yes", "opt-in": "Yes", "recruiting text": "Yes",
                "contact me": "Yes", "reach me": "Yes", "reach you": "Yes",
                "linkedin": "https://www.linkedin.com/in/raghavendra-karanam",
                "phone": os.environ.get("HOME_PHONE",""), "city": os.environ.get("HOME_CITY",""), "state": os.environ.get("HOME_STATE","FL"), "zip": os.environ.get("HOME_ZIP",""),
            }
            for f in uncached_fields:
                lbl = f.get("label", f.get("name",""))
                fid = f.get("id","") or f.get("name","")
                for kw, val in PROFILE_FALLBACK.items():
                    if kw in lbl.lower():
                        cached_answers[fid] = val
                        if _claude_ans: _claude_ans.save(lbl, val)
                        break
            return cached_answers

    def apply_claude_answers(fields, answers):
        """Apply Claude's answers to each field on the page."""
        for field in fields:
            ftype = field.get("type")
            fid   = field.get("id","") or field.get("name","")
            ans   = answers.get(fid)
            if ans is None:
                # try label as fallback key
                ans = answers.get(field.get("label",""))
            if ans is None:
                continue

            try:
                if ftype in ("text","tel","number","url","email","textarea"):
                    if fid:
                        el = page.locator(f"#{fid}").first
                    else:
                        el = page.locator(f"[aria-label='{field.get('label','')}']").first
                    if el.count() and el.is_visible():
                        el.fill(str(ans))
                        print(f"          ✏  '{field.get('label','?')[:40]}' → '{str(ans)[:50]}'")
                        time.sleep(0.1)

                elif ftype == "select":
                    if fid:
                        sel = page.locator(f"select#{fid}").first
                    else:
                        sel = page.locator("select").first
                    if sel.count() and sel.is_visible():
                        _ans_str = str(ans)

                        # State-aware candidate expansion — works regardless of
                        # whether the cache returned "FL" or "Florida".
                        # Maps both directions: abbr→full and full→abbr.
                        _US_STATES = {
                            "alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR",
                            "california":"CA","colorado":"CO","connecticut":"CT","delaware":"DE",
                            "florida":"FL","georgia":"GA","hawaii":"HI","idaho":"ID",
                            "illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS",
                            "kentucky":"KY","louisiana":"LA","maine":"ME","maryland":"MD",
                            "massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS",
                            "missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV",
                            "new hampshire":"NH","new jersey":"NJ","new mexico":"NM","new york":"NY",
                            "north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK",
                            "oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC",
                            "south dakota":"SD","tennessee":"TN","texas":"TX","utah":"UT",
                            "vermont":"VT","virginia":"VA","washington":"WA","west virginia":"WV",
                            "wisconsin":"WI","wyoming":"WY","district of columbia":"DC",
                        }
                        _ABBR_TO_FULL = {v: k.title() for k, v in _US_STATES.items()}

                        _a = _ans_str.strip()
                        _a_lower = _a.lower()
                        _a_upper = _a.upper()

                        if _a_lower in _US_STATES:
                            # Answer is a full state name, e.g. "Florida"
                            _abbr = _US_STATES[_a_lower]          # "FL"
                            _full = _a.title()                     # "Florida"
                        elif _a_upper in _ABBR_TO_FULL:
                            # Answer is an abbreviation, e.g. "FL"
                            _abbr = _a_upper                       # "FL"
                            _full = _ABBR_TO_FULL[_a_upper]       # "Florida"
                        else:
                            _abbr = _a
                            _full = _a

                        # ── Email address SELECT: skip — LinkedIn forces the account
                        # email in this dropdown, no option will match the profile email.
                        _lbl_lower = field.get('label','').lower().strip()
                        if 'email' in _lbl_lower and ftype == 'select':
                            print(f"          ⏭  skip email SELECT (LinkedIn account email only)")
                            continue

                        # ── Phone country code: expand to common variants ─────────
                        _phone_candidates = []
                        if 'country' in _lbl_lower or 'phone' in _lbl_lower or 'code' in _lbl_lower:
                            # Extract the +N part if present, e.g. "(+1)" → "+1"
                            import re as _re2
                            _m = _re2.search(r'\(\+(\d+)\)', _a)
                            _dial = f"+{_m.group(1)}" if _m else ""
                            # Country name portion (strip dial code)
                            _country_part = _re2.sub(r'\s*\(\+\d+\)\s*', '', _a).strip()
                            _phone_candidates = [
                                _a,                                        # original
                                _country_part,                             # "United States of America"
                                "United States",                           # common short form
                                f"United States ({_dial})" if _dial else "",  # "United States (+1)"
                                f"United States of America ({_dial})" if _dial else "",
                                _dial,                                     # "+1"
                                f"({_dial})" if _dial else "",             # "(+1)"
                                f"US ({_dial})" if _dial else "",
                                "United States +1",
                            ]
                            _phone_candidates = [c for c in _phone_candidates if c]

                        # Try every plausible format the form might use
                        _candidates = (
                            _phone_candidates if _phone_candidates else [
                                _full,                         # "Florida"
                                _abbr,                         # "FL"
                                f"{_abbr} - {_full}",          # "FL - Florida"
                                f"{_full} ({_abbr})",          # "Florida (FL)"
                                _abbr.lower(),                 # "fl"
                                _full.upper(),                 # "FLORIDA"
                            ]
                        )

                        _selected = False
                        # Try by value first (option value attr), then by label (visible text)
                        for _method in ("value", "label"):
                            for _try in _candidates:
                                try:
                                    if _method == "value":
                                        sel.select_option(value=_try, timeout=1000)
                                    else:
                                        sel.select_option(label=_try, timeout=1000)
                                    _selected = True
                                    break
                                except Exception:
                                    pass
                            if _selected:
                                break

                        # ── Fuzzy fallback: scan all <option> texts for partial match ──
                        if not _selected:
                            try:
                                _all_opts = sel.locator("option").all()
                                _a_words = set(_ans_str.lower().split())
                                _best = None
                                _best_score = 0
                                for _opt in _all_opts:
                                    _otxt = (_opt.text_content() or "").strip()
                                    if not _otxt:
                                        continue
                                    _otxt_l = _otxt.lower()
                                    # Word overlap score
                                    _owords = set(_otxt_l.split())
                                    _score = len(_a_words & _owords)
                                    if _score > _best_score:
                                        _best_score = _score
                                        _best = _otxt
                                if _best and _best_score >= 2:
                                    sel.select_option(label=_best, timeout=1000)
                                    _selected = True
                                    _ans_str = f"{_ans_str} → fuzzy matched '{_best}'"
                            except Exception:
                                pass

                        if _selected:
                            print(f"          📋 '{field.get('label','?')[:40]}' → '{_ans_str}'")
                        else:
                            print(f"          ⚠  select '{field.get('label','?')[:40]}' — no match for '{_ans_str}'")
                        time.sleep(0.1)

                elif ftype == "radio":
                    # ans = the id of the radio button to click
                    radio = page.locator(f"input[type=radio]#{ans}").first
                    if not radio.count():
                        # fallback: match by value
                        radio = page.locator(f"input[type=radio][value='{ans}']").first
                    if radio.count() and radio.is_visible():
                        radio.click()
                        lbl_text = next((o["label"] for o in field.get("options",[]) if o["id"]==ans), ans)
                        print(f"          🔘 '{field.get('label','?')[:40]}' → '{lbl_text[:50]}'")
                        time.sleep(0.2)

                elif ftype == "checkbox":
                    if str(ans).lower() == "check":
                        cb = page.locator(f"input[type=checkbox]#{fid}").first
                        if cb.count() and cb.is_visible() and not cb.is_checked():
                            cb.click()
                            print(f"          ☑  '{field.get('label','?')[:40]}' → checked")
                            time.sleep(0.1)

            except Exception as e:
                pass  # silently skip fields that can't be filled

    resume_uploaded   = False   # track whether resume was actually attached
    prev_btn_text     = None
    same_btn_count    = 0
    filled_log        = []      # running log of everything Claude filled
    about_to_submit   = False   # flag: next button is Submit

    def check_resume_uploaded():
        """
        Check if LinkedIn shows OUR specific resume filename in the modal.
        Must match the actual filename — generic words like 'resume' don't count.
        """
        if not resume_path:
            return False
        try:
            txt = page.evaluate("""
                () => {
                    const modal = document.querySelector('[data-test-modal], .jobs-easy-apply-modal, [role="dialog"]');
                    const root  = modal || document.body;
                    return root.innerText;
                }
            """)
            fname = Path(resume_path).stem.lower()   # filename without extension
            # Only count as uploaded if OUR filename appears in the modal
            # LinkedIn shows it as e.g. "Raghavendra_Karanam_CompanyName_Title"
            # Check at least the first 20 chars of the filename
            key = fname[:20].lower()
            return key in txt.lower()
        except:
            return False

    def claude_pre_submit_review():
        """
        Before submitting, scrape the entire review page and ask Claude to
        verify everything looks correct. Print a clear checklist.
        """
        try:
            review_text = page.evaluate("""
                () => {
                    const modal = document.querySelector('[data-test-modal], .jobs-easy-apply-modal, [role="dialog"]');
                    return (modal || document.body).innerText;
                }
            """)
            print()
            print("        ┌─────────────────────────────────────────────────────")
            print("        │  🔍 CLAUDE PRE-SUBMIT REVIEW")
            print("        │")

            prompt = f"""You are reviewing a LinkedIn Easy Apply form before final submission.

CANDIDATE: Raghavendra Karanam
APPLYING FOR: {job_title} at {company}

WHAT WAS FILLED DURING THE APPLICATION:
{_json.dumps(filled_log, indent=2)}

RESUME UPLOADED: {'✅ YES — ' + str(resume_path).split('/')[-1] if resume_uploaded else '⚠ Not confirmed yet (LinkedIn may have auto-attached it)'}

REVIEW PAGE TEXT (what LinkedIn shows before Submit):
{review_text[:2000]}

IMPORTANT RULES FOR YOUR REVIEW:
- The email shown is the LinkedIn account email — it CANNOT be changed. Do NOT flag email as an issue.
- The phone shown is from LinkedIn's saved profile — acceptable, do NOT flag.
- Only flag REAL blocking issues: completely blank required fields, or obviously wrong answers (e.g. wrong name, wrong email domain).
- Resume upload status is uncertain — do NOT block submission based on resume upload status alone.
- A resume with a slightly different job title in the filename is fine.

Check these things and respond in this exact format:
RESUME: [✅ Confirmed / ❌ Not detected / ⚠ Unclear]
NAME: [value shown]
EMAIL: [value shown — note: this is the LinkedIn account email, cannot be changed]
PHONE: [value shown]
WORK AUTH: [value shown or "Not shown"]
EXPERIENCE: [key values shown]
ISSUES: [list ONLY real blocking problems — or "None"]
VERDICT: [SAFE TO SUBMIT / DO NOT SUBMIT — reason]"""

            resp = _claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role":"user","content":prompt}]
            )
            review = resp.content[0].text.strip()
            for line in review.split("\n"):
                print(f"        │  {line}")
            print("        └─────────────────────────────────────────────────────")
            print()

            # Return whether Claude says safe to submit
            return "DO NOT SUBMIT" not in review.upper()
        except Exception as e:
            print(f"        │  ⚠ Review error: {e} — proceeding with submit")
            print("        └─────────────────────────────────────────────────────")
            return True

    _need_review = False   # track if Claude answered anything new this form

    for step in range(cfg.FORM_MAX_STEPS):
        if confirmed():
            return True, "confirmed via page text"
        if step > 0 and not modal_open():
            return True, "modal closed after submit"

        time.sleep(1.0)  # reduced from 1.5 — let DOM settle

        # ── Upload resume ──────────────────────────────────────────────────────
        # CRITICAL: click MUST happen INSIDE expect_file_chooser context.
        # Pre-clicking outside the context opens the OS dialog before Playwright
        # can intercept it — the dialog closes immediately and upload fails.
        if resume_path and not resume_uploaded:
            try:
                fname = Path(resume_path).name
                print(f"          📎 Attempting resume upload: {fname}")

                def _click_upload_btn():
                    """Click the upload/change button or label→input. Call inside expect_file_chooser."""
                    return page.evaluate("""
                        () => {
                            const modal = document.querySelector(
                                '[data-test-modal],[role="dialog"],.jobs-easy-apply-modal');
                            const root = modal || document.body;
                            // Priority 1 — label linked to a file input (most reliable)
                            for (const inp of root.querySelectorAll('input[type="file"]')) {
                                if (inp.id) {
                                    const lbl = root.querySelector('label[for="' + inp.id + '"]');
                                    if (lbl) { lbl.click(); return 'label'; }
                                }
                                inp.style.cssText = 'display:block!important;opacity:1!important;';
                                inp.click();
                                return 'input_click';
                            }
                            // Priority 2 — button / role=button with upload/change text
                            const all = Array.from(root.querySelectorAll('button,[role="button"],label'));
                            const btn = all.find(el => {
                                const t = (el.textContent + (el.getAttribute('aria-label')||'')).toLowerCase();
                                return t.includes('upload') || t.includes('change resume') || t.includes('replace');
                            });
                            if (btn) { btn.click(); return btn.textContent.trim().slice(0,30); }
                            return null;
                        }
                    """)

                # Strategy 1 — intercept OS file chooser (click INSIDE context)
                uploaded = False
                try:
                    with page.expect_file_chooser(timeout=5000) as fc_info:
                        trigger = _click_upload_btn()   # click happens HERE, inside context
                    if trigger:
                        fc = fc_info.value
                        fc.set_files(str(resume_path))
                        print(f"          📎 Resume uploaded via file chooser ✅  ({trigger})")
                        uploaded = True
                        time.sleep(2.0)
                    # if trigger is None no button was found — fall through
                except Exception:
                    pass  # timeout or no file chooser — try direct input

                # Strategy 2 — click button, wait for DOM to update, set files directly
                if not uploaded:
                    # Re-click the change button to reveal the file input in DOM
                    _click_upload_btn()
                    time.sleep(1.2)   # wait for LinkedIn to render file input
                    try:
                        page.evaluate("""
                            () => {
                                document.querySelectorAll('input[type="file"]').forEach(inp => {
                                    inp.style.cssText = 'display:block!important;opacity:1!important;position:fixed!important;top:0!important;left:0!important;z-index:9999!important;';
                                });
                            }
                        """)
                        upload_el = page.locator("input[type='file']").first
                        upload_el.set_input_files(str(resume_path))
                        uploaded = True
                        print(f"          📎 Resume uploaded via direct input ✅")
                        time.sleep(1.5)
                    except Exception:
                        # No file input on this step — resume section may appear later
                        # Do NOT mark as uploaded — keep trying on subsequent steps
                        print(f"          ⚠  No upload trigger on this step — will retry later")
                        uploaded = False

                if uploaded:
                    resume_uploaded = True
                # If not uploaded, resume_uploaded stays False so next step retries

            except Exception as e:
                print(f"          ⚠  Resume upload error: {e}")
                # Don't mark as uploaded on error — retry next step
        # ── Detect which nav buttons are visible (to know where we are) ───
        nav_buttons_visible = page.evaluate("""
            () => {
                const names = ["Submit application","Submit my application","Review","Next","Continue","Done"];
                return names.filter(n => {
                    const btn = document.querySelector('button:not([disabled])');
                    const all = Array.from(document.querySelectorAll('button:not([disabled])'));
                    return all.some(b => b.textContent.trim().toLowerCase().includes(n.toLowerCase()) &&
                                        b.getBoundingClientRect().width > 0);
                });
            }
        """)
        about_to_submit = any(s in str(nav_buttons_visible) for s in ["Submit", "Review"])

        # ── Answer fields: qa_answers → cache → Claude ───────────────────────
        fields = extract_form_fields()
        if fields:
            # Count how many need Claude (not in qa_answers or cache)
            _needs_claude = sum(
                1 for f in fields
                if not (_qa and _qa.get_answer(f.get("label", f.get("name", ""))))
                and not _cache.get(f.get("label", f.get("name", "")))
            )
            if _needs_claude:
                _need_review = True   # Claude answered something → run pre-submit review
                print(f"        🤖 Claude answering {_needs_claude}/{len(fields)} field(s) on step {step}...")
            else:
                print(f"        ⚡ Step {step}: all {len(fields)} field(s) answered from qa/cache")
            answers = claude_answer_all_fields(fields, step)
            # Log what was filled for the pre-submit review
            for f in fields:
                fid = f.get("id","") or f.get("name","")
                ans = answers.get(fid) or answers.get(f.get("label",""))
                if ans:
                    filled_log.append({"step": step, "label": f.get("label","?"), "answer": str(ans)[:80]})
            apply_claude_answers(fields, answers)
            time.sleep(0.5)

        # ── Navigate form ──────────────────────────────────────────────────
        btn_clicked = None
        for btn_text in ["Submit application", "Submit my application", "Review", "Next", "Continue", "Done"]:
            try:
                btn = page.locator(f"button:has-text('{btn_text}')").first
                if btn.count() > 0 and btn.is_visible() and not btn.is_disabled():

                    # ── PRE-SUBMIT REVIEW — only run if Claude answered unknown fields ──
                    # Skip if all fields were answered from qa_answers/cache (fast path)
                    if "Submit" in btn_text and "Review" not in btn_text:
                        if _need_review:   # only review when Claude had to answer something new
                            safe = claude_pre_submit_review()
                            if not safe:
                                print(f"        ❌ Claude review blocked submission — skipping")
                                return False, "Claude review blocked submission"
                        else:
                            print(f"        ⚡ Skipping pre-submit review (all answers from qa_answers/cache)")

                    btn.click()
                    btn_clicked = btn_text
                    print(f"        step {step}: clicked '{btn_text}'")
                    break
            except:
                continue

        # Detect infinite loop
        if btn_clicked == prev_btn_text:
            same_btn_count += 1
        else:
            same_btn_count = 0
            prev_btn_text = btn_clicked

        if same_btn_count >= cfg.STUCK_THRESHOLD // 2:
            # ── Vision assist: halfway to stuck threshold, let Claude see the page ──
            print(f"        👁  Calling Claude Vision to inspect stuck form (same btn {same_btn_count}x)...")
            try:
                import claude_engine as _ce
                _ss_bytes = page.screenshot()
                _page_txt = page.evaluate("() => document.body.innerText")[:3000]
                _vision   = _ce.vision_assist(_ss_bytes, _page_txt,
                                              job_title, company)
                _action   = _vision.get("action", "click_button")

                if _action == "fill_field":
                    print(f"        👁  Vision filling {len(_vision.get('fields', []))} field(s)...")
                    for _fld in _vision.get("fields", []):
                        _lbl = _fld.get("label", "")
                        _val = _fld.get("value", "")
                        if not _lbl or not _val:
                            continue
                        try:
                            _sel = f"[placeholder*='{_lbl}' i],[aria-label*='{_lbl}' i]"
                            _el = page.query_selector(_sel)
                            if _el:
                                _el.fill(_val)
                                print(f"           ✔ Vision filled '{_lbl}' = '{_val}'")
                        except Exception:
                            pass

                elif _action == "skip":
                    print(f"        👁  Vision sees confirmation — marking as submitted")
                    return True, "submitted (vision confirmed)"

            except Exception as _ve:
                print(f"        👁  Vision assist error: {_ve}")

        if same_btn_count >= cfg.STUCK_THRESHOLD:
            # Before giving up — if we're stuck on Review, look for a Submit button
            has_submit = page.evaluate("""
                () => {
                    const all = Array.from(document.querySelectorAll('button:not([disabled])'));
                    return all.find(b => {
                        const t = (b.textContent || '').toLowerCase().trim();
                        return t.includes('submit') || t.includes('send application');
                    });
                }
            """)
            if has_submit and "Review" in (btn_clicked or ""):
                try:
                    sub = page.locator("button:not([disabled])").filter(has_text="Submit application")
                    if sub.count() == 0:
                        sub = page.locator("button:not([disabled])").filter(has_text="Submit")
                    sub.first.click(timeout=3000)
                    print(f"        🎯 Stuck on Review — clicked Submit directly ✅")
                    time.sleep(2.5)
                    return True, "submitted from review (stuck rescue)"
                except Exception:
                    pass
            print(f"        ⚠ Stuck on '{btn_clicked}' — force-skipping form")
            return False, f"stuck on {btn_clicked}"

        if btn_clicked:
            # Use config-driven waits — LinkedIn has no CAPTCHA so we can go faster
            _submit_wait = getattr(cfg, "LINKEDIN_SUBMIT_WAIT_SEC", 2.5)
            _step_wait   = getattr(cfg, "LINKEDIN_STEP_WAIT_SEC",   0.8)
            wait = _submit_wait if ("Submit" in btn_clicked or "Done" in btn_clicked) else _step_wait
            time.sleep(wait)
            if "Submit" in btn_clicked or "Done" in btn_clicked:
                if confirmed():
                    return True, "confirmed after submit click"
                if not modal_open():
                    return True, "modal closed after submit"
        else:
            visible = page.evaluate(
                "() => Array.from(document.querySelectorAll('button:not([disabled])')).filter(b=>{const r=b.getBoundingClientRect();return r.width>0&&r.height>0;}).map(b=>b.textContent.trim().substring(0,25)).filter(t=>t.length>0).slice(0,6)"
            )
            print(f"        step {step}: no nav button. visible={visible}")
            if confirmed():
                return True, "confirmed (no button)"
            if not modal_open():
                return True, "modal closed"
            break

    if confirmed():
        return True, "confirmed after loop"
    return False, "form exhausted without confirmation"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=0, help="Max applies (0=unlimited)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  LINKEDIN APPLY NOW — Scrape + Score + Apply in ONE session  ║")
    print("║  No CSV. No expiry. Apply while job is live on screen.       ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    import claude_engine  as ce
    import resume_builder as rb
    import cover_letter   as cl_mod
    import raghav_profile as rp
    from pipeline_logger import RunLogger
    _run_log = RunLogger("linkedin")

    full_profile = {
        **rp.PROFILE,
        "skills":     getattr(rp, "ALL_SKILLS_FLAT", []) or
                      [s for grp in getattr(rp, "SKILLS", {}).values() for s in grp],
        "experience": getattr(rp, "EXPERIENCE", []),
        "education":  getattr(rp, "EDUCATION",  []),
    }
    profile_summary = ce.build_profile_summary(full_profile)

    log = load_log()
    fingerprints = load_fingerprints()
    applied_count = 0
    total_processed = 0

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    # Clear stale SingletonLock so scheduler can start even if prior run crashed
    for _lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        _lp = SESSION_DIR / _lk
        if _lp.exists():
            try:
                _lp.unlink()
                print(f"  🔓 Cleared stale {_lk}")
            except Exception:
                pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        ensure_login(page)

        for qi, kw in enumerate(SEARCH_QUERIES, 1):
            if args.limit and applied_count >= args.limit:
                break

            print(f"\n  [{qi}/{len(SEARCH_QUERIES)}] Searching: '{kw}'")
            page.goto(build_url(kw), wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            # Scroll to load cards
            for _ in range(4):
                page.keyboard.press("End"); time.sleep(1.2)
            page.keyboard.press("Home"); time.sleep(1)

            job_ids = page.evaluate("""
                () => {
                    const seen = new Set(), res = [];
                    for (const a of document.querySelectorAll('a[href*="/jobs/view/"]')) {
                        const m = a.href.match(/jobs\\/view\\/(\\d+)/);
                        if (m && !seen.has(m[1])) { seen.add(m[1]); res.push(m[1]); }
                    }
                    return res;
                }
            """) or []

            print(f"    Found {len(job_ids)} job cards")

            for jid in job_ids:
                if args.limit and applied_count >= args.limit:
                    break

                job_url = f"https://www.linkedin.com/jobs/view/{jid}/"
                if already_applied(job_url, log):  # URL-only check here; company+title checked after details load
                    print(f"      [{jid}] already applied — skip")
                    continue

                # Click the card link to load right panel
                try:
                    card = page.locator(f'a[href*="/jobs/view/{jid}"]').first
                    if card.count() > 0:
                        card.click()
                        time.sleep(3)
                    else:
                        continue
                except:
                    continue

                details = extract_right_panel(page)
                # Second dedup: company+title (catches same job across search queries)
                if already_applied(job_url, log,
                                   title=details.get("title",""),
                                   company=details.get("company","")):
                    print(f"      [{jid}] {details.get('company','')} — {details.get('title','')} → already applied (dedup)")
                    continue
                title       = details.get("title", "").strip()
                company     = details.get("company", "").strip()
                description = details.get("description", "").strip()
                has_ea      = details.get("hasEasyApply", False)
                live_url    = details.get("jobUrl", job_url)

                if not title or not company:
                    print(f"      [{jid}] no title/company — skip")
                    continue

                if len(description) < 100:
                    print(f"      [{jid}] {company} — description too short, retrying...")
                    time.sleep(2)
                    details     = extract_right_panel(page)
                    description = details.get("description", "").strip()
                    if len(description) < 100:
                        print(f"      [{jid}] still no description — skip")
                        continue

                if not is_good_level(title):
                    print(f"      [{jid}] {company} — {title[:40]} → SKIP senior/lead")
                    _run_log.job_skip(title, company, "senior/lead filter", url=live_url)
                    continue

                # ── Role relevance filter — must be in Raghav's target domain ─
                if not is_relevant_role(title):
                    print(f"      [{jid}] {company} — {title[:40]} → SKIP off-domain role")
                    _run_log.job_skip(title, company, "off-domain role", url=live_url)
                    continue

                # ── Fake job filter: zero API cost — protects the 50/day limit ─
                applicant_count      = details.get("applicantCount", 0)
                is_reposted          = details.get("isReposted", False)
                is_promoted          = details.get("isPromoted", False)
                location             = details.get("location", "")
                has_safety_warning   = details.get("hasSafetyWarning", False)
                is_company_verified  = details.get("isCompanyVerified", False)
                company_followers    = details.get("companyFollowers", -1)
                company_employees    = details.get("companyEmployees", -1)

                # Log LinkedIn trust signals for transparency
                trust_info = []
                if has_safety_warning:   trust_info.append("⚠ SAFETY WARNING")
                if is_company_verified:  trust_info.append("✅ verified")
                if company_followers >= 0: trust_info.append(f"{company_followers:,} followers")
                if company_employees >= 0: trust_info.append(f"{company_employees} employees")
                if trust_info:
                    print(f"      [{jid}] LinkedIn signals: {' | '.join(trust_info)}")

                fake, fake_reason = is_fake_job(
                    title, company, description, applicant_count, location,
                    has_safety_warning=has_safety_warning,
                    is_company_verified=is_company_verified,
                    company_followers=company_followers,
                    company_employees=company_employees,
                )
                if fake:
                    print(f"      [{jid}] {company} — {title[:40]} → SKIP fake/spam ({fake_reason})")
                    _run_log.job_skip(title, company, f"fake job: {fake_reason}", url=live_url)
                    continue

                # ── Description fingerprint check — catches scam templates ─────
                is_template, fp_reason = check_description_fingerprint(
                    description, company, fingerprints
                )
                save_fingerprints(fingerprints)
                if is_template:
                    print(f"      [{jid}] {company} — {title[:40]} → SKIP scam template ({fp_reason})")
                    _run_log.job_skip(title, company, f"scam template: {fp_reason}", url=live_url)
                    continue

                # ── Company trust score ────────────────────────────────────────
                trust_score, trust_reason = score_company_trust(
                    company,
                    has_safety_warning=has_safety_warning,
                    is_company_verified=is_company_verified,
                    company_followers=company_followers,
                    company_employees=company_employees,
                )
                min_trust = getattr(cfg, "LINKEDIN_MIN_TRUST_SCORE", 40)
                grey_min  = getattr(cfg, "LINKEDIN_LEGITIMACY_GREY_ZONE_MIN", 30)
                grey_max  = getattr(cfg, "LINKEDIN_LEGITIMACY_GREY_ZONE_MAX", 70)

                trust_bar = "█" * int(trust_score // 10) + "░" * (10 - int(trust_score // 10))
                trust_label = "✅ trusted" if trust_score >= grey_max else ("⚠ grey zone" if trust_score >= grey_min else "❌ low trust")
                print(f"      [{jid}] Trust: [{trust_bar}] {trust_score}/100 {trust_label} — {trust_reason}")

                if trust_score < min_trust:
                    print(f"      [{jid}] {company} — {title[:40]} → SKIP low trust ({trust_score} < {min_trust})")
                    _run_log.job_skip(title, company, f"low trust score {trust_score}", url=live_url)
                    continue

                # ── Claude legitimacy check for grey-zone companies ────────────
                # Whitelisted (score=100) and trusted (score>70) skip this.
                # Only grey-zone companies (30-70) pay for this Claude call.
                if getattr(cfg, "LINKEDIN_LEGITIMACY_CHECK", True) and grey_min <= trust_score <= grey_max:
                    print(f"      [{jid}] Grey zone — running Claude legitimacy check...")
                    legit, legit_reason, legit_conf, red_flags = check_job_legitimacy(
                        title, company, description, trust_score,
                        company_followers=company_followers,
                        company_employees=company_employees,
                        is_company_verified=is_company_verified,
                    )
                    flag_str = ", ".join(red_flags[:3]) if red_flags else "none"
                    print(f"      [{jid}] Legitimacy: {'✅ REAL' if legit else '❌ FAKE'} "
                          f"({legit_conf}% confidence) — {legit_reason}")
                    if red_flags:
                        print(f"      [{jid}] Red flags: {flag_str}")
                    if not legit:
                        print(f"      [{jid}] {company} — {title[:40]} → SKIP Claude flagged as fake")
                        _run_log.job_skip(title, company,
                                          f"Claude legitimacy check failed ({legit_conf}% conf): {legit_reason}",
                                          url=live_url)
                        continue
                if is_reposted:
                    print(f"      [{jid}] {company} — {title[:40]} → ⚠ Reposted (applying anyway)")
                if is_promoted:
                    print(f"      [{jid}] {company} — {title[:40]} → ⚠ Promoted listing")

                # ── Local pre-filter: zero API cost ───────────────────────────
                # Skip obvious mismatches before spending any tokens on scoring.
                _skip_local, _match_count = ce.local_prefilter(description, title)
                if _skip_local:
                    print(f"      [{jid}] {company} — {title[:40]} → SKIP (local filter: {_match_count} skill matches)")
                    _run_log.job_skip(title, company, f"local prefilter {_match_count} skills", url=live_url)
                    continue

                if not has_ea:
                    # Check if this job has a Workday apply link — queue it for workday_apply_now.py
                    wd_url = details.get("workdayUrl", "")
                    if wd_url:
                        try:
                            import workday_apply_now as _wd
                            _wd.add_to_wd_queue({
                                "title": title, "company": company,
                                "url": wd_url, "description": description,
                                "source": "linkedin",
                            })
                            print(f"      [{jid}] {company} — {title[:40]} → 📥 Queued for Workday")
                        except Exception as _wde:
                            print(f"      [{jid}] Workday queue error: {_wde}")
                    else:
                        print(f"      [{jid}] {company} — {title[:40]} → no Easy Apply btn")
                    continue

                total_processed += 1
                print(f"\n    🔵 {company} — {title[:50]}")

                # Score with Claude
                fit = ce.score_fit(profile_summary, description, title, company)
                score = float(fit.get("score", 0))
                grade = fit.get("grade", "?")
                bar = "█" * int(score//10) + "░" * (10-int(score//10))
                print(f"      [{bar}] {score:.0f}% {grade}")

                li_threshold = getattr(cfg, "LINKEDIN_FIT_THRESHOLD", 80)
                if score < li_threshold:
                    print(f"      Below LinkedIn gate ({li_threshold}%) — skip")
                    log.append({"timestamp": datetime.now().isoformat(), "company": company,
                                "title": title, "url": live_url, "fit_score": score,
                                "status": "Below Gate", "note": f"{score:.0f}% < {li_threshold}%"})
                    save_log(log)
                    _run_log.job_skip(title, company, f"below gate {score:.0f}%", fit_score=score, url=live_url)
                    continue

                # Build resume
                print(f"      Building resume...")
                resume_path = ""
                cover_letter_path = ""
                try:
                    import jd_parser as jdp
                    parsed = jdp.parse_jd(description, title)
                    res = rb.build_resume(
                        job_title=title, company=company,
                        jd_keywords=parsed.get("jd_keywords", []),
                        injectable_kws=parsed.get("injectable_keywords", []),
                        initial_score=parsed.get("initial_score", 0),
                        optimized_score=parsed.get("optimized_score", 0),
                        jd_text=description,
                        profile_summary=full_profile.get("summary", ""),
                    )
                    resume_path = res[0] if isinstance(res, tuple) else str(res)
                    print(f"      ✅ Resume: {Path(resume_path).name}")
                except Exception as e:
                    print(f"      ⚠️  Resume error: {e}")

                # Cover letters PAUSED — resume does the heavy lifting.
                cover_letter_path = ""

                if args.dry_run:
                    print(f"      [DRY RUN] Would apply")
                    log.append({"timestamp": datetime.now().isoformat(), "company": company,
                                "title": title, "url": live_url, "fit_score": score,
                                "status": "Dry Run", "note": "dry run"})
                    save_log(log)
                    continue

                # APPLY — Easy Apply button is RIGHT HERE in the right panel
                print(f"      Clicking Easy Apply...")
                ea_clicked = click_easy_apply(page)
                if not ea_clicked:
                    print(f"      ❌ Could not click Easy Apply")
                    log.append({"timestamp": datetime.now().isoformat(), "company": company,
                                "title": title, "url": live_url, "fit_score": score,
                                "status": "Failed", "note": "Easy Apply btn not clickable",
                                "resume_path": resume_path})
                    save_log(log)
                    continue

                time.sleep(3)
                dry_run = getattr(args, "dry_run", False)
                if dry_run:
                    print(f"      🔍 DRY RUN — skipping submit for {title} @ {company}")
                    submitted, reason = False, "dry-run"
                else:
                    submitted, reason = fill_and_submit_form(page, resume_path, job_title=details.get("title",""), company=details.get("company",""))

                status = "Applied" if submitted else "Failed"
                icon   = "✅" if submitted else "❌"
                print(f"      {icon} {status} — {reason}")

                _run_log.job_start(title, company, live_url, fit_score=score, grade=grade)
                _run_log.job_result(status, reason=reason, resume_file=Path(resume_path).name if resume_path else "")

                log.append({
                    "timestamp":   datetime.now().isoformat(),
                    "company":     company,
                    "title":       title,
                    "platform":    "LinkedIn",
                    "url":         live_url,
                    "fit_score":   score,
                    "status":      status,
                    "note":        reason,
                    "resume_path": resume_path,
                })
                save_log(log)

                if submitted:
                    applied_count += 1

                    # Screenshot the confirmation — take it IMMEDIATELY after submit,
                    # before anything navigates away. Wait a beat for the success modal.
                    ss_path = None
                    try:
                        time.sleep(1.5)   # let "Application submitted" modal appear
                        safe_co = re.sub(r'[^\w]', '_', company)[:30]
                        safe_ti = re.sub(r'[^\w]', '_', title)[:30]
                        _ss_file = SCREENSHOTS / f"LI_{safe_co}_{safe_ti}.png"
                        page.screenshot(path=str(_ss_file), full_page=False)
                        if _ss_file.exists() and _ss_file.stat().st_size > 0:
                            ss_path = _ss_file
                            print(f"          📸 Screenshot saved → {_ss_file.name}")
                        else:
                            print(f"          📸 Screenshot file empty — not attaching")
                    except Exception as _sse:
                        print(f"          📸 Screenshot failed: {_sse}")

                    # Email notification — resume + cover letter + screenshot attached
                    notifier.notify_applied(
                        title=title,
                        company=company,
                        fit_score=score,
                        resume_path=resume_path or "",
                        cover_letter_path=cover_letter_path or "",
                        platform="LinkedIn",
                        job_url=live_url or job_url or "",
                        screenshot_path=str(ss_path) if ss_path else ""
                    )
                    time.sleep(3)

                # Go back to search results
                page.go_back()
                time.sleep(2)

            time.sleep(2)

        browser.close()

    applied = sum(1 for e in log if e.get("status") == "Applied")
    _cost_summary = ce.get_cost_summary()
    print(f"\n  ── Done: {applied_count} applied this session | {total_processed} scored ──")
    print(f"  💰 {_cost_summary}")
    _cache.print_stats()
    _run_log.finish(searches_run=len(SEARCH_QUERIES), jobs_found=total_processed)
    notifier.notify_session_done(applied=applied_count,
                                  scored=total_processed,
                                  skipped=total_processed - applied_count,
                                  api_cost_summary=_cost_summary)


if __name__ == "__main__":
    main()
