#!/usr/bin/env python3
# =============================================================================
# INDEED_APPLY_NOW.PY — Scrape + Score + Apply via Indeed Apply (in-portal only)
#
# FLOW (per job card):
#   1. Search Indeed for matching jobs (Indeed Apply filter)
#   2. Click job card → right panel loads
#   3. Score with Claude AI — skip if < 65%
#   4. Build tailored Word resume + cover letter
#   5. Click "Apply now" (Indeed Apply only — skip external)
#   6. Fill multi-step form with Claude AI
#   7. Submit → email notification → log → next job
#
# RULES:
#   - Indeed Apply ONLY — skip any job redirecting to company site
#   - Never apply to senior/staff/principal roles
#   - Fit gate: ≥ 65% Claude score required
#   - Dedup: skip already-applied jobs (by URL and company+title)
#
# USAGE:
#   python indeed_apply_now.py
#   python indeed_apply_now.py --limit 5
#   python indeed_apply_now.py --dry-run
# =============================================================================

import os, sys, time, json, argparse, re, random, atexit
from pathlib import Path
from datetime import datetime, timedelta

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import config as cfg
import answer_cache as _cache
import notifier
try:
    import qa_answers as _qa
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
import staffing_filter as _staffing  # skip staffing agencies / consulting firms (user preference)

DATA_DIR      = cfg.DATA_DIR
SESSION_DIR   = cfg.BASE_DIR / ".indeed_session"
LOG_FILE      = cfg.BASE_DIR / "data" / "indeed_applied_log.json"
SCREENSHOTS   = cfg.BASE_DIR / "screenshots"

SCREENSHOTS.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
cfg.RESUMES_DIR.mkdir(parents=True, exist_ok=True)
cfg.COVER_DIR.mkdir(parents=True, exist_ok=True)

# ── Pro-level CAPTCHA tracking ────────────────────────────────────────────────
# Counts unsolved CAPTCHAs in a row. Reset to 0 on any successful solve.
# When it hits CAPTCHA_COOLDOWN_THRESHOLD, the pipeline takes a long break.
_consecutive_captcha_failures = 0
CAPTCHA_COOLDOWN_THRESHOLD    = 3    # failures in a row before cooldown
CAPTCHA_COOLDOWN_SECS         = 300  # 5-minute break

# Element handles _pin_captcha_box() has force-styled via cssText (the bframe
# itself plus every ancestor iframe in its chain). Tracked explicitly so the
# post-solve cleanup can strip exactly what was pinned instead of re-matching
# frames by URL substring — a guess that misses ancestor wrapper iframes whose
# URL never contains "bframe"/"anchor" (they're intermediate site iframes we
# styled directly, not Google's own frames). Cleared once cleanup runs.
_pinned_elements = []

# Counts how many times _check_and_handle_captcha() has printed "CAPTCHA
# DETECTED" this run — i.e. a distinct detection event, not a poll tick.
# Used purely to label diagnostic log lines ("detection #1" vs "detection
# #2") so a stale-vs-fresh-state investigation can compare across separate
# detections in the same job without guessing which log block belongs to
# which attempt from timestamps alone.
_captcha_detection_seq = 0

# Counts how many times THIS run's 3-second solve floor (config.
# CAPTCHA_MIN_SOLVE_FLOOR_SEC) actually blocked a premature "solved"
# declaration — added 2026-07-13. A non-zero count here means the
# element-scoping fixes in _captcha_actually_visible() let a false-solve
# signal through and the floor is the only thing that caught it. See
# _record_premature_solve_block() for the cross-run rolling-7-day tripwire
# this feeds into.
_premature_solve_blocks_this_run = 0


def _record_premature_solve_block():
    """
    Persists one timestamped tripwire event to
    data/captcha_premature_solve_events.json and returns how many such
    events have happened in the trailing 7 days, across ALL runs — not just
    this process. Added 2026-07-13 alongside CAPTCHA_MIN_SOLVE_FLOOR_SEC.

    A single run's in-memory counter (_premature_solve_blocks_this_run)
    resets every process start, so "is this still happening regularly"
    can only be answered by looking across runs — hence persisting to disk
    instead of just incrementing a module global. If the rolling count
    passes CAPTCHA_PREMATURE_SOLVE_WEEKLY_ALERT_THRESHOLD, the caller prints
    a loud warning — see the call site in _captcha_actually_visible().
    """
    import json as _json
    _path = cfg.DATA_DIR / "captcha_premature_solve_events.json"
    try:
        _events = _json.loads(_path.read_text()) if _path.exists() else []
    except Exception:
        _events = []

    _now = datetime.now()
    _events.append(_now.isoformat())

    def _parsed_recent_events(_cutoff):
        _out = []
        for _e in _events:
            try:
                if datetime.fromisoformat(_e) > _cutoff:
                    _out.append(_e)
            except Exception:
                pass  # malformed entry — drop silently, doesn't affect the count
        return _out

    # Trim the file itself to 60 days of history so it can't grow forever —
    # separate from the 7-day window used for the alert threshold below.
    _events = _parsed_recent_events(_now - timedelta(days=60))
    try:
        _path.write_text(_json.dumps(_events, indent=2))
    except Exception:
        pass

    return len(_parsed_recent_events(_now - timedelta(days=7)))

# Total cooldown cycles taken this run with zero successful solves in between.
# An unattended (scheduled) run has nobody to solve a CAPTCHA, so if we're
# still hitting them after a full cooldown, the session is very likely
# blocked — not just rate-limited. _indeed_blocked stops the run cleanly
# instead of repeating the cooldown loop for hours (seen: 596m/607m stalls).
_total_cooldowns_this_run = 0
_indeed_blocked           = False

# Set when a page.goto() fails with "Target page, context or browser has
# been closed" — this means the actual Chrome window is gone (closed by the
# user, crashed, etc.), not a network blip. Retrying inside the same page
# object can never succeed once this happens, so the search loop checks this
# flag and stops immediately instead of retrying every remaining query
# against a browser that no longer exists (seen: 0 cards found, query after
# query, for up to 54 queries, until someone manually Ctrl+C'd it).
_browser_window_closed    = False

# Fields scanned by the most recent smart_fill_step() call — the stuck-question
# logger below reads this. (It used to reference a variable, form_fields_last_seen,
# that was never actually assigned anywhere, so every stuck_questions.json entry
# logged "fields": [] with no way to see which question actually blocked the form.)
_last_seen_fields = []

# Jobs that failed specifically because CAPTCHA timed out — retried at end of run.
# Each entry: {"card": {...}, "job_url": "...", "score": int, "jd": "...",
#              "resume_path": "...", "title": "...", "company": "..."}
_captcha_retry_queue = []

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("pip install playwright && python -m playwright install chromium")

# ── Search queries ────────────────────────────────────────────────────────────
SEARCH_QUERIES = getattr(cfg, "INDEED_QUERIES", cfg.LINKEDIN_QUERIES)

def is_good_level(title):
    t = title.lower()
    return not any(bad in t for bad in cfg.SENIOR_WORDS)

# Data/analytics domain keywords — at least ONE must appear in the job title.
# Jobs with titles containing NONE of these are off-domain and skipped immediately.
DATA_TITLE_KEYWORDS = [
    "data", "analyst", "analytics", "engineer", "engineering",
    "database", "sql", "python", "bi ", "business intelligence",
    "machine learning", "ml ", " ml", "ai ", " ai", "etl",
    "pipeline", "warehouse", "scientist", "science", "reporting",
    "tableau", "power bi", "spark", "hadoop", "cloud", "aws",
    "azure", "gcp", "insight", "visualization", "intelligence",
    "information", "statistician", "quantitative",
]

def is_relevant_domain(title):
    """Returns True if the job title is in the data/analytics domain."""
    t = title.lower()
    return any(kw in t for kw in DATA_TITLE_KEYWORDS)

def build_indeed_url(kw, start=0):
    import urllib.parse
    return "https://www.indeed.com/jobs?" + urllib.parse.urlencode({
        "q": kw,
        "l": "United States",
        "sort": "date",
        "fromage": "7",
        "start": start,
        # Indeed's own experience-level filter — same fix as LinkedIn's f_E:
        # keyword-only queries still return Senior/Lead and off-target roles.
        "explvl": "entry_level",
    })

def load_log():
    try:
        return json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
    except:
        return []

def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2))

def _extract_jk(url: str) -> str:
    """Extract Indeed job key (jk=) from URL — the true unique job identifier."""
    m = re.search(r'[?&]jk=([a-zA-Z0-9]+)', url or "")
    return m.group(1) if m else ""

def already_applied(url, log, title="", company=""):
    """Dedup by Indeed job key (jk param) OR by company+title pair.
    Previously used full URL strip which caused false-positive dedup because
    https://www.indeed.com/viewjob?jk=A and ?jk=B both stripped to the same base URL.
    Now we extract just the jk= parameter as the unique job identifier.
    """
    jk = _extract_jk(url)
    for e in log:
        if e.get("status") not in ("Applied", "Already Applied"):
            continue
        e_jk = _extract_jk(e.get("url", ""))
        # Match by job key if both have one
        if jk and e_jk and jk == e_jk:
            return True
        # Match by company+title (catches jobs logged without jk in URL)
        if title and company:
            if (e.get("company","").lower().strip() == company.lower().strip()
                    and e.get("title","").lower().strip() == title.lower().strip()):
                return True
    return False

def ensure_login(page):
    """Check Indeed login; prompt user if not logged in.

    Sets the module-level _indeed_blocked flag on every give-up path (could
    not reach Indeed, Cloudflare wall during the wait, or login timeout) so
    the search loop's existing "if _indeed_blocked: break" check short-
    circuits immediately instead of running all 54 queries anyway even
    though every downstream Apply attempt was always going to fail without
    a login. Previously this function only printed "skipping this run" —
    nothing actually enforced that, so the run proceeded regardless."""
    global _indeed_blocked
    # Retry up to 3x — a single network timeout was killing entire evening runs
    for _i in range(3):
        try:
            page.goto("https://www.indeed.com/", wait_until="domcontentloaded", timeout=30000)
            break
        except Exception as _e:
            print(f"  ⚠  Indeed homepage load failed (attempt {_i+1}/3): {str(_e)[:80]}")
            if _i < 2:
                time.sleep(8)
            else:
                print("  ❌ Could not reach Indeed after 3 attempts — skipping this run")
                _indeed_blocked = True
                return
    time.sleep(3)
    # Check for user account indicator
    logged_in = page.evaluate("""
        () => {
            const indicators = [
                document.querySelector('[data-testid="gnav-accountMenu"]'),
                document.querySelector('[aria-label*="Account"]'),
                document.querySelector('.gnav-header-component__account'),
                document.querySelector('[data-tn-element="header-account"]'),
            ];
            return indicators.some(el => el !== null);
        }
    """)
    if logged_in:
        print("  ✅  Indeed: logged in")
        return

    # When running via scheduler (no terminal), input() crashes with EOF.
    # Instead: send email alert and wait up to 5 minutes for user to log in.
    print("\n  🔐  Indeed not logged in — sending alert and waiting up to 5 minutes...")
    try:
        notifier.send_alert(
            subject="🔐 Indeed Login Required — Pipeline Paused",
            body=(
                "The job pipeline needs you to log in to Indeed.\n\n"
                "1. Open the Chromium browser window on your Mac\n"
                "2. Log in to Indeed\n"
                "3. The pipeline will continue automatically within 5 minutes\n\n"
                "If you don't log in, this run will be skipped."
            )
        )
    except Exception as e:
        print(f"  ⚠  Could not send alert: {e}")

    # Wait up to 5 minutes for login
    #
    # CONFIRMED 2026-07-11: Raghav asked directly whether this pipeline keeps
    # auto-retrying while stuck on the Cloudflare "Are you a robot" page —
    # yes, it did, and this loop is exactly where. It reloads indeed.com
    # every 5 seconds for up to 5 minutes (60 attempts) with NO awareness of
    # Cloudflare — it only checks for a login indicator, so if Cloudflare is
    # showing its block/verification page instead of the real homepage, this
    # loop can't tell the difference from "just not logged in yet" and keeps
    # blindly re-requesting the same blocked page 60 times before giving up.
    # That's up to 60 extra requests against an already-flagged session every
    # single time the pipeline is started while blocked — directly
    # compounding the exact problem described in this week's Cloudflare
    # investigation. Fixed by checking for Cloudflare's own block-page
    # signals (same signals used by the separate _is_cloudflare_page() check
    # in the search loop) and stopping immediately instead of hammering for
    # the full 5 minutes — this isn't a "give it a moment" situation, it's
    # confirmed the account isn't reachable at all right now.
    #
    # Interval widened 5s → 60s on 2026-07-11 at Raghav's request — same
    # motivation as the Cloudflare check just above: fewer, less frequent
    # requests against a session that might already be flagged. Total wait
    # budget kept at ~5 minutes (LOGIN_WAIT_TOTAL_SEC), just spread across
    # far fewer checks (5 instead of 60).
    _login_wait_interval = getattr(cfg, "INDEED_LOGIN_WAIT_INTERVAL_SEC", 60)
    _login_wait_total     = getattr(cfg, "INDEED_LOGIN_WAIT_TOTAL_SEC", 300)
    _login_wait_attempts  = max(1, _login_wait_total // _login_wait_interval)
    for i in range(_login_wait_attempts):
        time.sleep(_login_wait_interval)
        try:
            page.goto("https://www.indeed.com/", wait_until="domcontentloaded", timeout=10000)
            time.sleep(2)
            _body_txt = (page.evaluate("() => document.body.innerText") or "").lower()
            if ("additional verification required" in _body_txt
                    or "ray id" in _body_txt
                    or "verify you are human" in _body_txt):
                print(f"  🚨 Cloudflare is blocking Indeed entirely right now (not a login issue) — "
                      f"stopping instead of re-hammering the same block for 5 minutes.")
                print(f"  💡 This session/IP is flagged. Wait before trying again instead of re-running.")
                _indeed_blocked = True
                return
            logged_in = page.evaluate("""
                () => {
                    const indicators = [
                        document.querySelector('[data-testid="gnav-accountMenu"]'),
                        document.querySelector('[aria-label*="Account"]'),
                        document.querySelector('.gnav-header-component__account'),
                    ];
                    return indicators.some(el => el !== null);
                }
            """)
            if logged_in:
                print("  ✅  Indeed: logged in successfully")
                return
        except:
            pass
        print(f"  ⏳ Still waiting for Indeed login... ({(i+1)*_login_wait_interval}s elapsed)")

    print("  ❌ Indeed login timeout — skipping this run")
    _indeed_blocked = True

def extract_job_panel(page):
    """Extract job details from the Indeed right panel / detail view."""
    return page.evaluate("""
        () => {
            let title = '';
            for (const sel of [
                'h1[data-testid="simcenter-title"]',
                'h1.jobsearch-JobInfoHeader-title',
                '.jobsearch-JobInfoHeader-title',
                'h1[class*="title"]',
                'h1'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 2) { title = el.innerText.trim().split('\\n')[0]; break; }
            }

            let company = '';
            for (const sel of [
                '[data-testid="inlineHeader-companyName"] a',
                '[data-testid="inlineHeader-companyName"]',
                '.jobsearch-InlineCompanyRating a',
                '[data-company-name]',
                '[class*="companyName"]'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 1) { company = el.innerText.trim(); break; }
            }

            let location = '';
            for (const sel of [
                '[data-testid="job-location"]',
                '.jobsearch-JobInfoHeader-subtitle div:last-child',
                '[class*="location"]'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim()) { location = el.innerText.trim(); break; }
            }

            let description = '';
            for (const sel of [
                '#jobDescriptionText',
                '.jobsearch-jobDescriptionText',
                '[data-testid="jobsearch-JobComponent-description"]',
                '[class*="description"]'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.length > 50) { description = el.innerText.substring(0, 3500); break; }
            }

            // Check for Indeed Apply button (not external)
            const allBtns = Array.from(document.querySelectorAll('button, a[role="button"], [data-testid*="apply"]'));
            let hasIndeedApply = false;
            let isExternal = false;
            for (const b of allBtns) {
                const t = (b.textContent || '').toLowerCase().trim();
                const href = (b.getAttribute('href') || '');
                if (t.includes('apply now') || t.includes('apply on indeed') || t === 'apply') {
                    // Check if it's Indeed Apply (not external company site)
                    if (!href.includes('http') || href.includes('indeed.com') || href === '') {
                        hasIndeedApply = true;
                    } else {
                        isExternal = true;
                    }
                    break;
                }
            }

            const jobUrl = window.location.href;
            return { title, company, location, description, hasIndeedApply, isExternal, jobUrl };
        }
    """) or {}

def click_apply_button(page):
    """Click the Indeed Apply button — tries every known selector + JS fallback.
    Retries up to 3 times with 2s waits to handle slow page renders.
    """
    for attempt in range(3):
        # Playwright native selectors (most reliable)
        native_selectors = [
            "button[data-testid='indeedApplyButton']",
            "a[data-testid='indeedApplyButton']",
            "[class*='indeed-apply-button']",
            "[class*='IndeedApplyButton']",
            "button:has-text('Apply now')",
            "button:has-text('Apply on Indeed')",
            "a:has-text('Apply now')",
            "span:has-text('Apply now')",
        ]
        for sel in native_selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1000):
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=4000)
                    time.sleep(2)
                    return True
            except:
                pass

        # JS exhaustive search — checks every clickable element by text
        clicked = page.evaluate("""
            () => {
                const APPLY_TEXTS = ['apply now', 'apply on indeed', 'indeed apply', 'apply'];
                // data-testid patterns
                const byTestId = document.querySelector(
                    '[data-testid*="indeedApply"], [data-testid*="apply-button"], [id*="indeedApply"]'
                );
                if (byTestId && !byTestId.disabled) {
                    byTestId.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                    return true;
                }
                // class patterns
                const byClass = document.querySelector(
                    '[class*="indeed-apply"], [class*="IndeedApply"], [class*="applyButton"]'
                );
                if (byClass && !byClass.disabled) {
                    byClass.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                    return true;
                }
                // Text search over ALL clickable elements
                const all = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], span[onclick], div[onclick]'
                ));
                for (const el of all) {
                    if (!el.offsetParent) continue;
                    const t = (el.innerText || el.textContent || '').trim().toLowerCase();
                    if (APPLY_TEXTS.some(kw => t === kw || t.startsWith(kw))) {
                        // Make sure it's not an external link
                        const href = el.getAttribute('href') || '';
                        if (href && href.startsWith('http') && !href.includes('indeed')) continue;
                        el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                        return true;
                    }
                }
                return false;
            }
        """)
        if clicked:
            time.sleep(2)
            return True

        if attempt < 2:
            print(f"          ⏳ Apply button not found yet — waiting 2s (attempt {attempt+1}/3)")
            time.sleep(2)

    return False

def _extract_posted_salary(jd_text: str) -> str:
    """Extract posted salary range from job description. Returns empty string if none found."""
    import re as _re
    # Match patterns like $55,000 - $65,000, $55k-$65k, 55000-65000/yr, etc.
    patterns = [
        r'\$[\d,]+\s*[-–to]+\s*\$[\d,]+\s*(?:a year|/yr|per year|annually|/year)?',
        r'\$[\d,]+[kK]\s*[-–to]+\s*\$[\d,]+[kK]',
        r'[\d,]+\s*[-–]\s*[\d,]+\s*(?:per year|a year|annually|/yr)',
        r'\$[\d,]+\+?\s*(?:a year|per year|annually|/yr)',
    ]
    for pat in patterns:
        m = _re.search(pat, jd_text, _re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


def smart_fill_step(page, profile_text, job_title, company, resume_filename="", cover_letter_text="", jd_text=""):
    """
    Extract → answer (cache/Claude) → fill by CSS selector (no re-labeling).

    FLOW:
      1. JS extracts fields: label, type, options, CSS selector (unique per element)
      2. Cache lookup by label → instant answer if cached
      3. Uncached → Claude API → save to cache
      4. JS fills each element by its stored CSS selector — no label re-detection
    """
    global _last_seen_fields
    # Cover letter field label patterns — when we see these, paste the cover letter
    COVER_LETTER_LABELS = {
        "cover letter", "cover note", "why are you interested",
        "why do you want to work", "why this role", "why this company",
        "why do you want to join", "tell us about yourself",
        "additional information", "additional comments",
        "message to hiring manager", "message to the hiring team",
        "anything else", "is there anything else",
    }
    # NOTE: anthropic is imported lazily below, only inside the `if uncached:`
    # branch — not here. Most steps are fully answered by qa_answers.py /
    # claude_answers.py / the SQLite cache with zero fields left uncached, so
    # importing anthropic unconditionally at function entry meant a missing
    # `anthropic` package crashed EVERY step, even ones that never needed AI
    # at all. Confirmed live 2026-07-12: a form died on the very first address
    # step ("No module named 'anthropic'") despite that step's fields being
    # fully cache-resolvable — the import itself was the only thing that failed.
    import os, json as _json

    # ── Step 1: Extract fields with unique CSS selectors ──────────────────────
    fields = page.evaluate(r"""
        () => {
            function cleanLabel(raw) {
                var lines = raw.split(String.fromCharCode(10))
                    .map(function(l){return l.trim();})
                    .filter(function(l){return l.length > 1;});
                return lines[0] || raw.trim();
            }

            function getLabel(el) {
                var lbl = '';
                if (el.id) {
                    var le = document.querySelector('label[for="' + el.id + '"]');
                    if (le) lbl = cleanLabel(le.innerText);
                }
                if (!lbl) lbl = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || '';
                if (!lbl) lbl = el.getAttribute('placeholder') || '';
                if (!lbl) {
                    var p = el.closest('label');
                    if (p) lbl = cleanLabel(p.innerText.replace(el.value || '', '').trim());
                }
                if (!lbl) {
                    // Walk up to find a heading/legend sibling
                    var container = el.closest('fieldset, [role="group"], div[class*="question"], div[class*="field"]');
                    if (container) {
                        var h = container.querySelector('legend, [role="heading"], label, span[class*="label"], p');
                        if (h) lbl = cleanLabel(h.innerText);
                    }
                }
                if (!lbl) {
                    var prev = el.previousElementSibling;
                    while (prev) {
                        var t = (prev.innerText || prev.textContent || '').trim();
                        if (t.length > 1) { lbl = cleanLabel(t); break; }
                        prev = prev.previousElementSibling;
                    }
                }
                return lbl || el.name || el.id || '';
            }

            function uniqueSelector(el) {
                if (el.id) return '#' + CSS.escape(el.id);
                if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                // Build path
                var path = [];
                var cur = el;
                while (cur && cur !== document.body) {
                    var idx = Array.from(cur.parentNode.children).indexOf(cur);
                    path.unshift(cur.tagName.toLowerCase() + ':nth-child(' + (idx+1) + ')');
                    cur = cur.parentNode;
                }
                return path.join(' > ');
            }

            var results = [];
            var seenLabels = {};

            // Text / select / textarea
            var inputs = Array.from(document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]):not([type=image]),' +
                'select, textarea'
            ));
            for (var inp of inputs) {
                if (!inp.offsetParent) continue;
                var lbl = getLabel(inp);
                var key = lbl.toLowerCase().trim();
                if (seenLabels[key]) continue;
                seenLabels[key] = true;

                var type = inp.tagName.toLowerCase() === 'select' ? 'select'
                         : inp.tagName.toLowerCase() === 'textarea' ? 'textarea'
                         : (inp.getAttribute('type') || 'text').toLowerCase();

                var opts = [];
                if (type === 'select') {
                    opts = Array.from(inp.options)
                        .map(function(o){return o.text.trim();})
                        .filter(function(o){return o && o !== '--' && o.length > 0;});
                }

                results.push({
                    label:    lbl,
                    type:     type,
                    options:  opts,
                    required: inp.required || false,
                    current:  inp.value || '',
                    sel:      uniqueSelector(inp)
                });
            }

            // Radio/checkbox groups
            var groups = {};
            Array.from(document.querySelectorAll('input[type=radio], input[type=checkbox]')).forEach(function(inp) {
                if (!inp.offsetParent) return;
                var gname = inp.name || inp.getAttribute('data-question') || '';
                if (!gname) return;
                if (!groups[gname]) {
                    // Find group label
                    var lbl = '';
                    var fs = inp.closest('fieldset, [role="group"], div[class*="question"]');
                    if (fs) {
                        var leg = fs.querySelector('legend, [role="heading"], span[class*="label"]');
                        if (leg) lbl = cleanLabel(leg.innerText);
                    }
                    if (!lbl) lbl = gname;
                    var key = lbl.toLowerCase().trim();
                    if (!seenLabels[key]) {
                        seenLabels[key] = true;
                        groups[gname] = { label: lbl, type: inp.type, options: [], gname: gname };
                    }
                }
                if (groups[gname]) {
                    var optLbl = '';
                    var le = document.querySelector('label[for="' + inp.id + '"]');
                    if (le) optLbl = cleanLabel(le.innerText);
                    if (!optLbl) optLbl = inp.value;
                    if (optLbl && groups[gname].options.indexOf(optLbl) === -1)
                        groups[gname].options.push(optLbl);
                }
            });
            for (var k in groups) results.push(groups[k]);

            return results;
        }
    """) or []

    if not fields:
        print(f"          ⚠  No form fields found in this frame")
        return 0

    # ── Special case: resume-selection page ───────────────────────────────────
    # Indeed shows radio buttons labeled with resume filenames.
    # The right move: click whichever option matches the just-uploaded resume,
    # or the first option if nothing matches. NEVER use stale cache here.
    all_labels = [f.get("label","") for f in fields]
    all_gnames = [f.get("gname","") for f in fields]
    if "resume-selection" in all_gnames or (
        all(f.get("type") in ("radio","checkbox") for f in fields)
        and any((".docx" in lbl or ".pdf" in lbl) for lbl in all_labels)
    ):
        print(f"          📄 Resume-selection page detected — auto-selecting uploaded resume")
        target = resume_filename or ""
        _resume_card_click_js = """
            (target) => {
                // ── NEVER click "Build an Indeed Resume" / "Recommended" option ──
                // Indeed puts it first — falling back to radios[0] would click it.
                // Instead: always look for the uploaded-file radio (.pdf / .docx label).
                var BAD_KEYWORDS = ['build', 'create', 'indeed resume', 'recommended'];

                function getLabel(r) {
                    var lbl = '';
                    if (r.id) {
                        var le = document.querySelector('label[for="' + r.id + '"]');
                        if (le) lbl = le.innerText || le.textContent || '';
                    }
                    // Also check parent element text
                    if (!lbl && r.parentElement) {
                        lbl = r.parentElement.innerText || r.parentElement.textContent || '';
                    }
                    return lbl;
                }

                function isBadOption(r) {
                    var lbl = getLabel(r).toLowerCase();
                    return BAD_KEYWORDS.some(function(k){ return lbl.includes(k); })
                        && !lbl.includes('.pdf') && !lbl.includes('.docx');
                }

                var radios = Array.from(document.querySelectorAll('input[type=radio]'))
                    .filter(function(r){ return r.offsetParent && !isBadOption(r); });

                if (!radios.length) return 0;

                // 1. Try exact filename match
                var pick = target
                    ? radios.find(function(r){
                          var lbl = getLabel(r);
                          return lbl.includes(target) || (r.value || '').includes(target);
                      })
                    : null;

                // 2. Fall back to any radio whose label has .pdf or .docx
                if (!pick) {
                    pick = radios.find(function(r){
                        var lbl = getLabel(r).toLowerCase();
                        return lbl.includes('.pdf') || lbl.includes('.docx');
                    });
                }

                // 3. Last resort: first non-bad radio
                if (!pick) pick = radios[0];

                if (!pick) return 0;

                // Click the radio input
                if (!pick.checked) { pick.click(); }
                ['input', 'change', 'blur'].forEach(function(ev){
                    pick.dispatchEvent(new Event(ev, {bubbles:true}));
                });

                // Also click the parent card container (Indeed needs the whole card clicked)
                // Walk up the DOM looking for the card wrapper — stop before any element
                // whose text contains "build" / "recommended" to avoid clicking the wrong card.
                var card = null;
                var el = pick.parentElement;
                for (var i = 0; i < 6; i++) {
                    if (!el) break;
                    var txt = (el.innerText || el.textContent || '').toLowerCase();
                    // Stop if we've climbed into a container that includes the bad option text
                    if (BAD_KEYWORDS.some(function(k){ return txt.includes(k); })
                        && !txt.includes('.pdf') && !txt.includes('.docx')) break;
                    var tag = el.tagName;
                    if (tag === 'LABEL' || tag === 'LI' || tag === 'ARTICLE'
                        || el.getAttribute('role') === 'radio'
                        || el.getAttribute('role') === 'option'
                        || (el.className && /card/i.test(el.className))) {
                        card = el;
                        break;
                    }
                    el = el.parentElement;
                }
                if (card && card !== pick) {
                    card.click();
                    ['click','mousedown','mouseup'].forEach(function(ev){
                        card.dispatchEvent(new MouseEvent(ev, {bubbles:true}));
                    });
                }
                return 1;
            }
        """
        clicked = page.evaluate(_resume_card_click_js, target)

        if not clicked:
            # No resume radio found at all. Two possibilities: it's already
            # selected some other way (harmless — the old "return clicked or 1"
            # behavior), or Indeed's own upload silently failed on THIS page
            # and there's no resume attached to select at all. Confirmed live
            # 2026-07-08 (Winsupply/Business Intelligence Analyst,
            # data/stuck_questions.json): the page showed "We couldn't upload
            # your resume file. Wait a moment and then try again." with an
            # empty file-upload widget, and the old code reported success
            # anyway — Continue then clicked against an empty widget forever
            # (4-step stuck loop, job abandoned). Detect that specific case
            # and actually retry the upload instead of assuming success.
            try:
                _page_txt_now = page.evaluate("() => document.body.innerText") or ""
            except Exception:
                _page_txt_now = ""
            _has_file_input = False
            try:
                _has_file_input = page.locator('input[type="file"]').count() > 0
            except Exception:
                pass
            _upload_failed_text = ("couldn't upload" in _page_txt_now.lower()
                                    or "could not upload" in _page_txt_now.lower())
            if _has_file_input and (_upload_failed_text or "add a resume" in _page_txt_now.lower()):
                _resume_full_path = (cfg.RESUMES_DIR / resume_filename) if resume_filename else None
                if _resume_full_path and _resume_full_path.exists():
                    print(f"          📎 No resume card found — Indeed's upload widget looks empty, retrying upload...")
                    for _rt in range(3):
                        try:
                            time.sleep(random.uniform(1.5, 3.0))
                            page.locator('input[type="file"]').first.set_input_files(str(_resume_full_path))
                            time.sleep(random.uniform(4.0, 6.0))
                            _retry_txt = page.evaluate("() => document.body.innerText") or ""
                            if "couldn't upload" not in _retry_txt.lower() and "could not upload" not in _retry_txt.lower():
                                print(f"          📎 Retry upload accepted")
                                break
                            _wait_t = 12 + _rt * 10
                            print(f"          ⚠  Retry upload rejected again (attempt {_rt+1}/3) — waiting {_wait_t}s...")
                            time.sleep(_wait_t)
                        except Exception as _re:
                            print(f"          ⚠  Retry upload error: {_re}")
                    # Re-scan now that a fresh upload was attempted
                    clicked = page.evaluate(_resume_card_click_js, target)
                    print(f"          ✔  Post-retry resume card {'clicked' if clicked else 'still not found'}")
                else:
                    print(f"          ⚠  Resume file not found at {_resume_full_path} — can't retry upload")

        print(f"          ✔  Resume card + radio {'clicked' if clicked else 'already selected'} (skipped Build Indeed Resume option)")
        return clicked or 1   # count as 1 fill even if already selected

    # ── Filter: only fill REQUIRED fields (marked * in label or required=True in HTML) ──
    # Optional fields (no asterisk, not required) are skipped entirely — no API call.
    # Always-fill labels even without * — Indeed requires these to advance
    ALWAYS_FILL = {
        'zip code', 'city, state', 'city', 'state', 'street address', 'address',
        'postal', 'zip', 'phone', 'type phone', 'first name', 'last name',
        'full name', 'email', 'name'
    }

    # Labels that are always optional — never fill these even if HTML required=True
    NEVER_FILL = {
        'get email updates', 'email updates', 'job alerts', 'email alert',
        'notify me', 'send me updates', 'subscribe',
    }

    def is_required_field(f):
        lbl = f.get("label", "").lower().strip()
        # Never fill marketing/alert checkboxes regardless of required attribute
        if any(kw in lbl for kw in NEVER_FILL):
            return False
        return (
            f.get("required", False)
            or "*" in f.get("label", "")
            or any(kw in lbl for kw in ALWAYS_FILL)
        )

    required_fields = [f for f in fields if is_required_field(f)]
    optional_fields = [f for f in fields if not is_required_field(f)]

    # Expose the raw scanned fields for the stuck-question logger (see
    # apply_to_job's same_url_count >= 4 branch), so a "stuck" entry actually
    # records which question(s) blocked the form instead of an empty list.
    _last_seen_fields = fields

    print(f"          📋 Found {len(fields)} field(s): {len(required_fields)} required, {len(optional_fields)} optional (skipping optional)")
    for fi in fields:
        opts_str = f"  options={fi['options'][:4]}" if fi.get("options") else ""
        req_str  = " [REQUIRED]" if is_required_field(fi) else " [optional-skip]"
        print(f"             • [{fi.get('type','?'):8s}] '{fi.get('label','?')}'{opts_str}{req_str}")

    # On question pages — fill ALL fields (they are always real application questions)
    # On contact/profile pages — only fill required fields marked with *
    frame_url = ""
    try:
        frame_url = page.url or ""
    except:
        pass
    is_question_page = (
        "question" in frame_url or "questions" in frame_url
        or "resume-s" in frame_url   # scoutability / profile visibility radio page
    )

    if is_question_page:
        # Fill everything on question pages — no field is truly optional here
        print(f"          📝 Question page — filling ALL {len(fields)} field(s) regardless of required status")
    elif not required_fields:
        print(f"          ℹ  No required fields on this step — clicking Continue")
        return 0
    else:
        fields = required_fields  # only required on non-question pages

    # ── Step 2: Cache lookup → uncached → Claude ──────────────────────────────
    # Fields that must be generated fresh by Claude for every job — never cached.
    # These are job-specific narratives that make no sense recycled from another job.
    _NEVER_CACHE_KEYS = {
        "reason for applying", "why do you want to work here",
        "why are you interested in this role", "why are you applying",
        "why do you want this job", "why this company",
        "tell us why you want to work", "what interests you about",
        "what attracts you to", "motivation for applying",
    }
    def _is_never_cache(label: str) -> bool:
        ll = label.lower().strip()
        return any(k in ll for k in _NEVER_CACHE_KEYS)

    answers = {}       # label → answer string
    uncached = []

    def _answer_valid_for_options(ans, options) -> bool:
        """Radio/checkbox groups can only be filled by clicking an option whose
        value/label text matches the answer (see the fill pass below, which uses
        this same contains-either-way match). If a field has a known options
        list, a cached answer that doesn't match ANY option is worthless — it
        will never actually get clicked, and the form is left stuck with a
        'Select an option' validation error forever (never re-tried, never sent
        to Claude, since a cache "hit" short-circuits before Claude is asked).
        This mainly bites hash-labeled fields (e.g. 'q_b74adbe8...') — Indeed
        reuses that ID pattern for both simple Yes/No gates AND multi-option
        questions (degree level, clearance checkboxes, contact preference), and
        qa_answers.py's blanket 'q_ prefix -> Yes' default only holds for the
        former. Confirmed live 2026-07-08: Interactive Process Technology LLC
        job stuck 4 steps on exactly this ('Yes' cached for a degree-level
        radio group with no 'Yes' option).
        """
        if not options:
            return True  # no options to check against — trust the cache as before
        ans_l = str(ans).lower().strip()
        if not ans_l:
            return False
        for opt in options:
            opt_l = str(opt).lower().strip()
            if ans_l == opt_l or ans_l in opt_l or opt_l in ans_l:
                return True
        return False

    print(f"          🗄  Cache lookup...")
    for f in fields:
        lbl = f.get("label","")
        _opts = f.get("options")

        # 0. Cover letter fields — paste actual cover letter text
        lbl_lower = lbl.lower().strip().rstrip(" *:?")
        if cover_letter_text and f.get("type") in ("textarea", "text", "richtext"):
            if any(cl_kw in lbl_lower for cl_kw in COVER_LETTER_LABELS):
                print(f"             📝 COVER LETTER field detected: '{lbl}' — inserting cover letter")
                answers[lbl] = cover_letter_text
                continue

        # 0b. Job-specific fields — always go to Claude, never use cached answer
        if _is_never_cache(lbl):
            print(f"             🔄 JOB-SPECIFIC (always fresh): '{lbl}'")
            uncached.append(f)
            continue

        # 1. qa_answers.py — master Q&A (manually curated, highest priority)
        qa_hit = _qa.get_answer(lbl) if (_qa and lbl) else None
        if qa_hit is not None and _answer_valid_for_options(qa_hit, _opts):
            print(f"             ✔ QA FILE    '{lbl}' → '{str(qa_hit)[:60]}'")
            answers[lbl] = qa_hit
            continue
        elif qa_hit is not None:
            print(f"             ✗ QA FILE answer '{qa_hit}' doesn't match any option for '{lbl}' — treating as miss")

        # 2. claude_answers.py — Claude's past answers (auto-saved, human-reviewable)
        ca_hit = _claude_ans.get(lbl) if (_claude_ans and lbl) else None
        if ca_hit is not None and _answer_valid_for_options(ca_hit, _opts):
            print(f"             ✔ SAVED      '{lbl}' → '{str(ca_hit)[:60]}'")
            answers[lbl] = ca_hit
            continue
        elif ca_hit is not None:
            print(f"             ✗ SAVED answer '{ca_hit}' doesn't match any option for '{lbl}' — treating as miss")

        # 3. SQLite cache (legacy)
        cached = _cache.get(lbl) if lbl else None
        if cached is not None and _answer_valid_for_options(cached, _opts):
            print(f"             ✔ CACHE HIT  '{lbl}' → '{str(cached)[:60]}'")
            answers[lbl] = cached
            # Promote to claude_answers.py so it's visible and editable
            if _claude_ans: _claude_ans.save(lbl, cached)
        else:
            if cached is not None:
                print(f"             ✗ CACHE HIT answer '{cached}' doesn't match any option for '{lbl}' — treating as miss")
            print(f"             ✗ cache miss '{lbl}'")
            uncached.append(f)

    cache_hits = len(fields) - len(uncached)
    print(f"          📦 Cache: {cache_hits}/{len(fields)} hits  |  {len(uncached)} need Claude")

    if uncached:
        # Ask Claude for ALL uncached fields in one call
        _api_key = os.environ.get("ANTHROPIC_API_KEY","")
        if not _api_key:
            try:
                for line in (Path.home()/"job_pipeline"/".env").read_text().splitlines():
                    if line.startswith("ANTHROPIC_API_KEY="):
                        _api_key = line.split("=",1)[1].strip()
            except: pass

        fields_desc = "\n".join(
            f'{i+1}. label="{f["label"]}" type={f["type"]}'
            + (f' options={f["options"]}' if f.get("options") else '')
            + (' [REQUIRED]' if f.get("required") else '')
            for i, f in enumerate(uncached)
        )

        # Smart salary: uses posted JD range if available, else profile defaults by role
        salary_rule = _salary_rule(jd_text or "", job_title or "")

        prompt = f"""Fill out this job application form step on Indeed for: {job_title} at {company}

CANDIDATE PROFILE:
{profile_text}

JOB DESCRIPTION (for context):
{(jd_text or "")[:2000]}

FIELDS TO FILL:
{fields_desc}

Return ONLY a JSON object keyed by the EXACT label text:
{{"label text": "answer", ...}}

Rules:
- select/radio/checkbox: copy one option EXACTLY as written
- years of experience questions: answer with a WHOLE number only (e.g. "2", "3") — never a decimal like "2.5", Indeed's number fields reject decimals
- work authorization: "Yes"
- {salary_rule}
- notice period / start date: if the field expects plain text use "2 weeks", if it expects MM/DD/YYYY format use today + 14 days
- relocation: "No"
- cover letter / additional info: write 2 sentences about the candidate
- "can you perform essential functions" or "able to perform the job": always "Yes"
- "are you available to work full time / on-site / weekends": "Yes"
- "do you have experience with [any tool/platform/technology]": always "Yes"
- "are you familiar with [any software/system]": always "Yes"
- "have you worked with [any technology]": always "Yes"
- date fields expecting MM/DD/YYYY format: use today's date + 14 days
- Never mention Community Dreams Foundation or Mobile Stage Pros
- CRITICAL: For EVERY required text/textarea field (marked [REQUIRED]), you MUST provide a substantive answer (2-4 sentences minimum). Never leave a required field blank — a blank required field blocks the form and fails the application.
- For open-ended essay questions (why exploring new opportunity, tell me about yourself, leadership style, team environment, etc.): write a genuine, professional 2-4 sentence answer drawing from the candidate's background, skills, and experience above.
- For industry-specific questions the candidate has no direct experience in (e.g. senior care, healthcare): answer honestly but positively — highlight transferable skills, eagerness to learn, and relevant adjacent experience.
- For optional fields with no obvious answer: leave blank ("")"""

        print(f"          🤖 Calling Claude API for {len(uncached)} field(s)...")
        try:
            import anthropic  # lazy — only reached when qa/cache/rules left fields unanswered
            _claude = anthropic.Anthropic(api_key=_api_key)
            resp = _claude.messages.create(
                model=os.environ.get("CLAUDE_MODEL","claude-haiku-4-5-20251001"),
                max_tokens=4096,
                messages=[{"role":"user","content":prompt}]
            )
            raw = resp.content[0].text.strip()
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            claude_answers = {}
            if m:
                claude_answers = _json.loads(m.group(0))

                # Robust matching: Claude sometimes strips '? *' or has minor label differences.
                # For each uncached field, find the best matching key in claude_answers.
                def _norm(s):
                    return re.sub(r'[\s\*\?\:]+$', '', s).strip().lower()

                for f_unc in uncached:
                    orig_lbl = f_unc.get("label", "")
                    orig_norm = _norm(orig_lbl)
                    matched_ans = None
                    # 1. Exact match
                    if orig_lbl in claude_answers:
                        matched_ans = claude_answers[orig_lbl]
                    else:
                        # 2. Normalized match
                        for ck, cv in claude_answers.items():
                            if _norm(ck) == orig_norm:
                                matched_ans = cv
                                break
                        if matched_ans is None:
                            # 3. Substring match
                            for ck, cv in claude_answers.items():
                                ck_n = _norm(ck)
                                if orig_norm and (orig_norm in ck_n or ck_n in orig_norm):
                                    matched_ans = cv
                                    break
                    if matched_ans is not None:
                        answers[orig_lbl] = matched_ans
                        if matched_ans:
                            _cache.save(orig_lbl, matched_ans)
                            if _claude_ans: _claude_ans.save(orig_lbl, str(matched_ans))
                            print(f"             ✔ Claude → '{orig_lbl}': '{str(matched_ans)[:80]}'  (saved)")
                        else:
                            print(f"             · Claude → '{orig_lbl}': (blank)")

            print(f"          🤖 Claude answered {len([f for f in uncached if f.get('label','') in answers])}/{len(uncached)} uncached fields")
        except ImportError:
            print(f"          ⚠  anthropic not installed — {len(uncached)} field(s) need AI, none available")
            print(f"          ↩  Applying fallback answers for uncached fields...")
        except Exception as e:
            print(f"          ⚠  Claude API error: {e}")
            print(f"          ↩  Applying fallback answers for uncached fields...")

        # ── Fallback: if Claude failed or didn't answer some fields, apply safe defaults ──
        # This ensures required fields are NEVER left blank due to API failure.
        FALLBACK = {
            "work authorization": "Yes",
            "authorized to work": "Yes",
            "legally authorized": "Yes",
            "sponsorship": "No",
            "require visa": "No",
            "salary":        _pick_salary(jd_text or "", job_title or ""),
            "compensation":  _pick_salary(jd_text or "", job_title or ""),
            "expected pay":  _pick_salary(jd_text or "", job_title or ""),
            "desired pay":   _pick_salary(jd_text or "", job_title or ""),
            "desired salary":_pick_salary(jd_text or "", job_title or ""),
            "start date": (datetime.now() + timedelta(days=14)).strftime("%m/%d/%Y"),
            "notice period": (datetime.now() + timedelta(days=14)).strftime("%m/%d/%Y"),
            "available": (datetime.now() + timedelta(days=14)).strftime("%m/%d/%Y"),
            "relocate": "No",
            "relocation": "No",
            "years of experience": "2",
            "how many years": "2",
            "experience with": "2",
            "gender": "Prefer not to say",
            "ethnicity": "Prefer not to say",
            "race": "Prefer not to say",
            "disability": "I don't wish to answer",
            "veteran": "I am not a protected veteran",
            "sms": "Yes",
            "text message": "Yes",
            "consent to receive": "Yes",
            "opt in": "Yes",
            "opt-in": "Yes",
            "recruiting text": "Yes",
            "informational text": "Yes",
            "contact me": "Yes",
            "reach me": "Yes",
            "reach you": "Yes",
        }
        for f in uncached:
            lbl = f.get("label", "")
            # Skip if Claude already answered (exact OR non-empty value in answers)
            existing = answers.get(lbl)
            if existing is not None and existing != "":
                continue  # Claude answered — never overwrite with fallback
            lbl_l = lbl.lower()
            for kw, val in FALLBACK.items():
                if kw in lbl_l:
                    answers[lbl] = val
                    print(f"             ↩  Fallback → '{lbl}': '{val}'")
                    break
            else:
                # UUID-format labels (Indeed qualification questions) → default Yes
                import re as _re2
                _uuid = _re2.match(
                    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
                    lbl_l, _re2.I
                )
                if _uuid or lbl_l.startswith("q_"):
                    answers[lbl] = "Yes"
                    print(f"             ↩  UUID/hash fallback → '{lbl[:30]}': 'Yes'")
                # Last resort: leave text fields as empty string rather than skip
                elif f.get("type") in ("text","textarea","tel","email","number"):
                    answers[lbl] = ""
                    print(f"             · No answer for '{lbl}' — leaving blank")

    # Never return 0 and abandon — always try to fill whatever we have
    if not answers:
        print(f"          ⚠  No answers at all — proceeding with empty fill (Continue will still be clicked)")

    # ── Step 3: Build selector-keyed fill list ────────────────────────────────
    # Map each field's CSS selector → answer so we fill by selector, not by label
    fill_items = []
    for f in fields:
        lbl = f.get("label", "")
        ans = answers.get(lbl)
        if ans is None:
            # Try case-insensitive match
            for k, v in answers.items():
                if k.lower().strip() == lbl.lower().strip():
                    ans = v
                    break
        if ans is None:
            # Partial match fallback
            for k, v in answers.items():
                kl = k.lower().strip()
                ll = lbl.lower().strip()
                if ll and (ll in kl or kl in ll):
                    ans = v
                    break
        _f_type = f.get("type", "text")
        _ans_str = str(ans) if ans is not None else ""
        if _f_type == "number" and _ans_str:
            # Indeed's number fields often reject decimals
            # ("Answer must be a valid number (no decimals)") — Claude sometimes
            # answers "2.5" for fractional years of experience. Round to a whole
            # number so the field actually validates instead of blocking the form.
            _num_match = re.search(r'-?\d+(?:\.\d+)?', _ans_str)
            if _num_match:
                try:
                    _ans_str = str(round(float(_num_match.group(0))))
                except ValueError:
                    pass
        fill_items.append({
            "sel":     f.get("sel", ""),
            "label":   lbl,
            "type":    _f_type,
            "options": f.get("options", []),
            "gname":   f.get("gname", ""),
            "answer":  _ans_str,
        })
        print(f"             → fill '{lbl}' = '{_ans_str}'  sel={f.get('sel','')[:50]}")

    # ── Step 4: Fill DOM elements by CSS selector (live refs, no re-labeling) ──
    filled = page.evaluate("""
        (itemsJson) => {
            var items = JSON.parse(itemsJson);
            var filled = 0;

            function fireEvents(el) {
                ['input','change','blur'].forEach(function(ev) {
                    try {
                        el.dispatchEvent(new Event(ev, {bubbles:true, cancelable:true}));
                    } catch(e) {
                        try { el.dispatchEvent(new Event(ev)); } catch(e2) {}
                    }
                });
            }

            for (var item of items) {
                if (!item.answer) continue;
                var ans = item.answer;

                // ── Radio/checkbox group — find by name ───────────────────────
                if (item.type === 'radio' || item.type === 'checkbox') {
                    var gname = item.gname || (item.sel ? item.sel.replace(/.*\\[name="([^"]+)"\\]/, '$1') : '');
                    var opts = gname
                        ? Array.from(document.querySelectorAll('input[name="' + gname + '"]'))
                        : (item.sel ? Array.from(document.querySelectorAll(item.sel)) : []);
                    var ansL = ans.toLowerCase().trim();
                    var _matched = false;
                    for (var i = 0; i < opts.length; i++) {
                        var opt = opts[i];
                        var optVal = (opt.value || '').toLowerCase();
                        var optLbl = '';
                        if (opt.id) {
                            var le = document.querySelector('label[for="' + opt.id + '"]');
                            if (le) optLbl = le.innerText.toLowerCase().trim();
                        }
                        if (optVal === ansL || optLbl === ansL ||
                            optVal.includes(ansL) || ansL.includes(optVal) ||
                            (optLbl && (optLbl.includes(ansL) || ansL.includes(optLbl)))) {
                            _matched = true;
                            if (!opt.checked) {
                                try { opt.click(); } catch(ec) {}
                                // Fire React synthetic events so state updates
                                ['click','change','input'].forEach(function(ev) {
                                    try {
                                        opt.dispatchEvent(new Event(ev, {bubbles:true, cancelable:true}));
                                    } catch(e) {
                                        try { opt.dispatchEvent(new Event(ev)); } catch(e2) {}
                                    }
                                });
                                // Also click the associated <label> and any custom
                                // clickable wrapper card — same fix already proven
                                // necessary for the resume-selection special case
                                // ("Indeed needs the whole card clicked", not just
                                // the underlying <input>). Some of Indeed's custom
                                // widgets (confirmed: the EEO/demographic self-ID
                                // module, 2026-07-08) only wire their real click
                                // handler to the visible label/card element, not
                                // the input itself, so a raw opt.click() updates
                                // the DOM's checked state but never notifies
                                // Indeed's own form validation.
                                // IMPORTANT: only do this if opt.click() didn't
                                // already take — checkboxes (unlike radios) TOGGLE
                                // on every click, so clicking the label again after
                                // a successful direct click would silently uncheck
                                // it right back off.
                                try {
                                    if (!opt.checked) {
                                        if (le) {
                                            le.click();
                                            ['click','mousedown','mouseup'].forEach(function(ev) {
                                                le.dispatchEvent(new MouseEvent(ev, {bubbles:true}));
                                            });
                                        }
                                        if (!opt.checked) {
                                            var wrapEl = opt.closest('label, li, [role="radio"], [role="option"], [class*="card" i]');
                                            if (wrapEl && wrapEl !== le) {
                                                wrapEl.click();
                                                ['click','mousedown','mouseup'].forEach(function(ev) {
                                                    wrapEl.dispatchEvent(new MouseEvent(ev, {bubbles:true}));
                                                });
                                            }
                                        }
                                    }
                                } catch(ecard) {}
                            }
                            fireEvents(opt);
                            filled++;
                            break;
                        }
                    }
                    // Added 2026-07-08: single-checkbox "agree to terms" widgets
                    // (Indeed's "SINGLE" group name, one option, e.g. Winsupply's
                    // 'Agree' checkbox) sometimes have no <label for> text and an
                    // empty/non-matching value attribute, so the text-matching loop
                    // above never matches and the box never gets checked — the form
                    // then silently blocks Continue and the run stalls/loops on this
                    // page. If there's exactly one option and the cached answer is
                    // clearly affirmative, check it directly rather than requiring
                    // a text match that this widget variant can't provide.
                    if (!_matched && opts.length === 1) {
                        var AFFIRM = ['agree', 'yes', 'i agree', 'i understand', 'accept', 'confirm'];
                        if (AFFIRM.indexOf(ansL) !== -1) {
                            var soleOpt = opts[0];
                            if (!soleOpt.checked) {
                                try { soleOpt.click(); } catch(ec2) {}
                                ['click','change','input'].forEach(function(ev) {
                                    try { soleOpt.dispatchEvent(new Event(ev, {bubbles:true, cancelable:true})); }
                                    catch(e) { try { soleOpt.dispatchEvent(new Event(ev)); } catch(e2) {} }
                                });
                                if (!soleOpt.checked) {
                                    var soleLe = soleOpt.id ? document.querySelector('label[for="' + soleOpt.id + '"]') : null;
                                    if (soleLe) {
                                        soleLe.click();
                                        ['click','mousedown','mouseup'].forEach(function(ev) {
                                            soleLe.dispatchEvent(new MouseEvent(ev, {bubbles:true}));
                                        });
                                    }
                                    if (!soleOpt.checked) {
                                        var soleWrap = soleOpt.closest('label, li, [role="checkbox"], [class*="card" i]');
                                        if (soleWrap && soleWrap !== soleLe) {
                                            soleWrap.click();
                                            ['click','mousedown','mouseup'].forEach(function(ev) {
                                                soleWrap.dispatchEvent(new MouseEvent(ev, {bubbles:true}));
                                            });
                                        }
                                    }
                                }
                                fireEvents(soleOpt);
                                filled++;
                            }
                        }
                    }
                    continue;
                }

                // ── Text / select / textarea — find by CSS selector ───────────
                var el = item.sel ? document.querySelector(item.sel) : null;

                // If selector fails (dynamic IDs change between steps), scan by name/type
                if (!el && item.label) {
                    var allInputs = Array.from(document.querySelectorAll(
                        'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]),' +
                        'select, textarea'
                    ));
                    // Match by placeholder or aria-label as fallback
                    var lbl = item.label.toLowerCase().trim();
                    el = allInputs.find(function(inp) {
                        var ph = (inp.getAttribute('placeholder') || '').toLowerCase();
                        var al = (inp.getAttribute('aria-label') || '').toLowerCase();
                        return ph === lbl || al === lbl || ph.includes(lbl) || al.includes(lbl);
                    }) || null;
                }

                if (!el) continue;

                if (el.tagName === 'SELECT') {
                    var opts2 = Array.from(el.options);
                    var match = opts2.find(function(o){ return o.text.trim() === ans; })
                             || opts2.find(function(o){ return o.text.toLowerCase().includes(ans.toLowerCase()); })
                             || opts2.find(function(o){ return ans.toLowerCase().includes(o.text.toLowerCase()) && o.text.length > 1; });
                    if (match) { el.value = match.value; fireEvents(el); filled++; }
                } else if (el.getAttribute('contenteditable') !== null) {
                    // React rich-text editor (contenteditable div) — el.value doesn't work.
                    // Use execCommand insertText which fires the right React synthetic events.
                    el.focus();
                    document.execCommand('selectAll', false, null);
                    var inserted = document.execCommand('insertText', false, ans);
                    if (!inserted) {
                        // execCommand fallback: set innerText and fire events manually
                        el.innerText = ans;
                        ['input','change','keyup'].forEach(function(ev) {
                            el.dispatchEvent(new Event(ev, {bubbles:true}));
                        });
                    }
                    fireEvents(el);
                    filled++;
                } else if (el.type === 'date') {
                    // input[type="date"] requires YYYY-MM-DD internally.
                    // Convert MM/DD/YYYY → YYYY-MM-DD so the browser accepts it.
                    var dateVal = ans;
                    var dm = ans.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
                    if (dm) dateVal = dm[3] + '-' + dm[1].padStart(2,'0') + '-' + dm[2].padStart(2,'0');
                    try {
                        var dProto = window.HTMLInputElement.prototype;
                        var dSetter = Object.getOwnPropertyDescriptor(dProto, 'value');
                        if (dSetter && dSetter.set) { dSetter.set.call(el, dateVal); }
                        else { el.value = dateVal; }
                    } catch(eDateE) { el.value = dateVal; }
                    fireEvents(el);
                    filled++;
                } else {
                    // Standard input/textarea — fill value and fire React synthetic events.
                    // Uses a safe multi-strategy approach to avoid "Illegal invocation"
                    // errors that occur with cross-origin iframes or shadow DOM elements.
                    try {
                        var tag = el.tagName ? el.tagName.toUpperCase() : '';
                        var filled_ok = false;

                        // Strategy 1: React native setter (only for same-origin elements)
                        if (!filled_ok && (tag === 'INPUT' || tag === 'TEXTAREA')) {
                            try {
                                var proto = tag === 'INPUT'
                                    ? window.HTMLInputElement.prototype
                                    : window.HTMLTextAreaElement.prototype;
                                var setter = Object.getOwnPropertyDescriptor(proto, 'value');
                                if (setter && setter.set && el.ownerDocument === document) {
                                    setter.set.call(el, ans);
                                    filled_ok = true;
                                }
                            } catch(e1) { /* cross-origin or shadow DOM — try next strategy */ }
                        }

                        // Strategy 2: Plain assignment (always works, React may not see it)
                        if (!filled_ok) {
                            try { el.value = ans; filled_ok = true; } catch(e2) {}
                        }

                        // Strategy 3: innerText for contenteditable elements
                        if (!filled_ok) {
                            try { el.innerText = ans; filled_ok = true; } catch(e3) {}
                        }
                    } catch(eOuter) {
                        try { el.value = ans; } catch(eFinal) {}
                    }
                    fireEvents(el);
                    filled++;
                }
            }
            return filled;
        }
    """, _json.dumps(fill_items))

    filled_n = filled or 0
    print(f"          ✏️  DOM fill result: {filled_n}/{len(fields)} field(s) written to page")

    # ── Playwright fill pass: for number/text inputs that JS evaluate misses ──
    # React controlled inputs often ignore el.value= but respond to frame.locator().fill()
    # NOTE: `page` here may be a Frame (not a Page), so page.keyboard is INVALID.
    # Use frame.locator(sel).fill(ans) — this fires proper Playwright input events
    # that React's synthetic event system registers correctly.
    pw_filled = 0
    for item in fill_items:
        sel  = item.get("sel", "")
        ans  = item.get("answer", "")
        typ  = item.get("type", "")
        if not sel or not ans:
            continue
        if typ in ("text", "number", "textarea", "tel", "email", "date"):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    fill_val = str(ans)
                    if typ == "date":
                        # input[type="date"] needs YYYY-MM-DD; convert from MM/DD/YYYY
                        _dm = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', ans)
                        if _dm:
                            fill_val = f"{_dm.group(3)}-{_dm.group(1).zfill(2)}-{_dm.group(2).zfill(2)}"
                    loc.click(timeout=2000)
                    loc.fill(fill_val, timeout=3000)
                    # fire blur so React validators update
                    try:
                        loc.evaluate("el => el.dispatchEvent(new Event('blur', {bubbles:true}))")
                    except Exception:
                        pass
                    pw_filled += 1
            except Exception:
                pass
        elif typ in ("radio", "checkbox") and not item.get("gname"):
            # Radio/checkbox with no gname — try clicking by visible label text
            try:
                lbl_text = item.get("label", "")
                ans_text  = item.get("answer", "")
                if ans_text and lbl_text:
                    # Find label element containing the answer text, click its input
                    clicked = page.evaluate(f"""
                        () => {{
                            const labels = Array.from(document.querySelectorAll('label, [role="radio"], [role="checkbox"]'));
                            const target = labels.find(l => {{
                                const t = (l.innerText || l.textContent || '').trim().toLowerCase();
                                return t === {_json.dumps(ans_text.lower())} || t.includes({_json.dumps(ans_text.lower())});
                            }});
                            if (target) {{ target.click(); return true; }}
                            const inp = document.querySelector('input[value={_json.dumps(ans_text)}]');
                            if (inp) {{ inp.click(); return true; }}
                            return false;
                        }}
                    """)
                    if clicked:
                        pw_filled += 1
            except Exception:
                pass

    if pw_filled:
        print(f"          ✏️  Playwright fill pass: {pw_filled} additional field(s) written")

    return filled_n + pw_filled

CONFIRM_PHRASES = [
    'application submitted', 'successfully applied',
    'your application has been', 'application received',
    'thanks for applying', 'thank you for applying',
    'application complete',
    # Added 2026-07-08: several real jobs (Sun River Health, Proteam Solutions,
    # shark analytics, Atlantic IT Solutions) had their submit button vanish
    # (a strong success signal) but the confirmation page used wording not in
    # the original list, so _is_confirmed() never caught it and the run
    # eventually gave up and mis-reported "Failed" after a real success.
    'application has been submitted', 'thank you for your application',
    'your application was submitted', 'we received your application',
    "you're all set", 'you have applied', "you've applied",
    'application sent', 'good luck with your application',
]

# Phrases that appear on the AI interview page AFTER submission.
# Application is already in — the interview is an optional add-on.
AI_INTERVIEW_PHRASES = [
    'complete your interview', 'start your interview',
    'complete the interview', 'record your answers',
    'ai-powered interview', 'ai powered interview',
    'complete an interview', 'video interview',
    'recorded interview', 'one-way interview',
    'interview questions', 'answer interview questions',
    'take a quick interview', 'spark hire', 'hirevue',
    'myinterview', 'complete your application interview',
]

# File that persists AI interview links between sessions
_AI_INTERVIEW_LOG = cfg.BASE_DIR / "data" / "ai_interviews_pending.json"


def _check_ai_interview(page) -> tuple:
    """
    Detect if the current page is an AI interview prompt (post-submission).
    Returns (is_interview: bool, interview_url: str).
    The application is already submitted at this point.
    """
    try:
        body = page.evaluate("() => document.body.innerText.toLowerCase()") or ""
        if not any(p in body for p in AI_INTERVIEW_PHRASES):
            return False, ""

        # Extract the interview link — look for a button/link with interview keywords
        interview_url = page.evaluate("""
            () => {
                const kws = ['interview', 'spark', 'hirevue', 'myinterview', 'recorded'];
                // Check current URL first
                if (kws.some(k => location.href.toLowerCase().includes(k))) return location.href;
                // Check links on page
                const links = Array.from(document.querySelectorAll('a[href]'));
                for (const a of links) {
                    const h = a.getAttribute('href') || '';
                    if (kws.some(k => h.toLowerCase().includes(k))) return h;
                }
                // Fall back to current page URL
                return location.href;
            }
        """) or page.url or ""

        return True, interview_url
    except Exception:
        return False, ""


def _handle_ai_interview(page, title, company, job_url="") -> bool:
    """
    Handle the AI interview prompt that appears after Indeed application submission.
    - Saves the interview link to ai_interviews_pending.json
    - Sends email alert with the direct link
    - Returns True (application was already submitted — treat as success)
    """
    is_interview, interview_url = _check_ai_interview(page)
    if not is_interview:
        return False

    print(f"\n          🎤 AI INTERVIEW DETECTED — application already submitted!")
    print(f"          🔗 Interview link: {interview_url[:80]}")
    print(f"          📧 Sending interview link to your email...")

    # Save to persistent log
    try:
        existing = []
        if _AI_INTERVIEW_LOG.exists():
            try:
                existing = json.loads(_AI_INTERVIEW_LOG.read_text())
            except Exception:
                existing = []
        existing.append({
            "timestamp":     datetime.now().isoformat(),
            "title":         title,
            "company":       company,
            "job_url":       job_url,
            "interview_url": interview_url,
            "status":        "pending",
        })
        _AI_INTERVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
        _AI_INTERVIEW_LOG.write_text(json.dumps(existing, indent=2))
        print(f"          💾 Saved to data/ai_interviews_pending.json")
    except Exception as _e:
        print(f"          ⚠  Could not save interview log: {_e}")

    # Email alert
    try:
        notifier.send_ai_interview_alert(
            title=title,
            company=company,
            interview_url=interview_url,
            job_url=job_url,
        )
    except Exception as _e:
        print(f"          ⚠  Interview email failed: {_e}")

    return True  # application was submitted — count as success

def _check_and_handle_captcha(page, title="", company="", job_url=""):
    """
    Detect reCAPTCHA (bframe iframe) on the page and handle it.

    Pro-level behaviour:
      1. Alert email — instructs user to solve on the MAC browser (not phone)
      2. Mac system notification with sound
      3. Resize CAPTCHA iframe so Verify button is fully visible
      4. Wait up to 10 minutes; auto-click Submit after solve
      5. On timeout:
         - Reset browser to Indeed homepage (clears broken page state)
         - Increment consecutive-failure counter
         - If counter hits CAPTCHA_COOLDOWN_THRESHOLD → take a 5-min cooldown
      6. On successful solve → reset consecutive-failure counter
      7. Returns True if solved (or no CAPTCHA), False if timed out

    Call at the START of every step loop iteration AND after every Submit click.
    """
    global _consecutive_captcha_failures, _total_cooldowns_this_run, _indeed_blocked, _captcha_detection_seq, _premature_solve_blocks_this_run

    # Diagnostic-only, added 2026-07-12: tracks the last (visible, reason)
    # pair logged by _captcha_actually_visible() so its signal source can be
    # printed on every real STATE CHANGE without spamming ~600 identical
    # lines into a single stuck-job wait loop. Declared here (not inside the
    # function) so it survives across every call within this one
    # _check_and_handle_captcha() invocation.
    _last_signal_logged = {"key": None}

    # Added 2026-07-13: accumulates everything about ONE captcha event as it
    # happens, reusing data already being computed by the diagnostics above
    # (no separate tracking logic) so a single consolidated block can be
    # printed once the event resolves, instead of requiring the scattered
    # per-tick lines to be pieced together by hand. Only populated once a
    # real detection happens (see _captcha_detection_seq += 1 below) — a
    # "no CAPTCHA" call never touches this.
    _event = {
        "detected_at": None,
        "detect_reason": None,
        "pin_ok": None,
        "pin_rect": None,
        "poll_history": [],  # list of (elapsed_seconds, state_label) — one entry per real transition
    }

    def _log_signal_transition(visible: bool, reason: str):
        """
        Print which underlying signal (token / aria-checked / bframe
        visibility / no-signal fallthrough) is driving the current
        still_captcha value, but only when that value or its reason changes.
        Added after a real run showed "✅ CAPTCHA solved!" firing within
        ~1-2s of "🚨 CAPTCHA DETECTED" — implausibly fast for a human to have
        actually solved an image-selection challenge — with no way to tell
        afterward which of the three signals claimed "solved" or why. This
        turns that into something the log states directly instead of
        something inferred after the fact from timestamps.

        Also appends to _event["poll_history"] on every transition (not
        every tick) — the same dedup this function already does for the
        printed line is exactly what the consolidated summary needs, so
        it's recorded here once instead of re-derived later.
        """
        key = (visible, reason)
        if _last_signal_logged["key"] != key:
            _last_signal_logged["key"] = key
            state = "STILL SHOWING (unsolved)" if visible else "solved / not visible"
            print(f"          🔬 captcha signal → {state}   [{reason}]")
            if _event["detected_at"] is not None:
                _elapsed = (datetime.now() - _event["detected_at"]).total_seconds()
                _event["poll_history"].append((_elapsed, f"{state} [{reason}]"))

    def _print_captcha_summary(outcome: str):
        """
        One consolidated, greppable block per CAPTCHA event — job/company,
        when detected and by which signal, whether pinning worked and at
        what final size, a deduplicated poll history (collapsed to
        transitions with a duration, not one line per second-long tick),
        the final outcome, and total elapsed time. Everything here is data
        _event already collected as a side effect of the existing
        diagnostics above — nothing is tracked twice.
        """
        def _fmt_dur(seconds: float) -> str:
            seconds = int(seconds)
            m, s = divmod(seconds, 60)
            return f"{m}m{s:02d}s" if m else f"{s}s"

        _detected_at = _event["detected_at"]
        if _detected_at is None:
            return  # never actually detected anything — nothing to summarize
        _now = datetime.now()
        _total_elapsed = (_now - _detected_at).total_seconds()

        print(f"\n          ┌─ CAPTCHA EVENT SUMMARY ──────────────────────────────")
        print(f"          │ Job: {title} @ {company}")
        print(f"          │ Detected: {_detected_at.strftime('%H:%M:%S')}  (signal: {_event['detect_reason']})")
        if _event["pin_ok"] is None:
            print(f"          │ Pinned: n/a")
        elif _event["pin_ok"]:
            print(f"          │ Pinned: yes — {_event['pin_rect']}")
        else:
            print(f"          │ Pinned: NO — bframe element not reachable")
        if _event["poll_history"]:
            print(f"          │ Poll history:")
            _prev_t = 0.0
            for _idx, (_t, _label) in enumerate(_event["poll_history"]):
                _dur = _t - _prev_t
                if _idx == 0:
                    print(f"          │   {_label} — held for {_fmt_dur(_dur)}")
                else:
                    print(f"          │   → {_label} at +{_fmt_dur(_t)}")
                _prev_t = _t
        print(f"          │ Outcome: {outcome}")
        print(f"          │ Elapsed: {_fmt_dur(_total_elapsed)} (detected → resolved)")
        print(f"          └──────────────────────────────────────────────────────\n")

    def _log_captcha_identity_snapshot(label: str):
        """
        Diagnostic-only, added 2026-07-12 to directly test a stale-state
        hypothesis: that _captcha_actually_visible() might be reading a
        leftover g-recaptcha-response value from a PREVIOUS challenge rather
        than genuine fresh-solve state, causing "solved" to fire implausibly
        fast (~1-2s) after a new challenge appears.

        Captures three things together, labeled with the current detection
        sequence number so separate detections can be compared side by side:
          - the RAW token textarea value (length + preview, not just the
            boolean _captcha_actually_visible() derives from it)
          - the RAW anchor checkbox aria-checked value
          - a stable per-element identity marker: a random id stamped into
            el.dataset the first time we see each element, then read back
            unchanged on every later call. This is what actually answers the
            staleness question — not the value alone. If a later "solved"
            snapshot reports the SAME element_id as an earlier "DETECTED"
            snapshot, we're looking at the literal same DOM node (expected —
            Google may reuse elements across rounds; staleness would then be
            about how promptly GOOGLE clears the value, not our code). If a
            later detection reports a DIFFERENT element_id than an earlier
            one, the two challenges are genuinely different DOM nodes, which
            rules out our own code caching/reusing a stale Python-side
            handle across attempts — there is no Python-side handle involved
            at all, page.frames and frame_element() are both re-queried live
            on every single call already (see _pin_captcha_box and the calls
            below), so any staleness this reveals would have to be on
            Google's side, not ours.
        """
        try:
            for f in list(page.frames):
                if _is_recaptcha_frame(f):
                    continue
                try:
                    info = f.evaluate("""
                        () => {
                            const el = document.querySelector(
                                'textarea[name^="g-recaptcha-response"], [id^="g-recaptcha-response"]'
                            );
                            if (!el) return null;
                            if (!el.dataset.pwProbeId) {
                                el.dataset.pwProbeId = Math.random().toString(36).slice(2, 10);
                            }
                            return {
                                value_len: (el.value || '').length,
                                value_preview: (el.value || '').slice(0, 12),
                                probe_id: el.dataset.pwProbeId,
                            };
                        }
                    """)
                    if info:
                        print(f"          🔬 [{label} #{_captcha_detection_seq}] token textarea: "
                              f"len={info['value_len']} preview='{info['value_preview']}' "
                              f"element_id={info['probe_id']}")
                except Exception:
                    pass

            for f in list(page.frames):
                if "anchor" not in (f.url or ""):
                    continue
                try:
                    info = f.evaluate("""
                        () => {
                            const cb = document.querySelector('#recaptcha-anchor, .recaptcha-checkbox');
                            if (!cb) return null;
                            if (!cb.dataset.pwProbeId) {
                                cb.dataset.pwProbeId = Math.random().toString(36).slice(2, 10);
                            }
                            return {
                                aria_checked: cb.getAttribute('aria-checked'),
                                probe_id: cb.dataset.pwProbeId,
                            };
                        }
                    """)
                    if info:
                        print(f"          🔬 [{label} #{_captcha_detection_seq}] anchor checkbox: "
                              f"aria-checked={info['aria_checked']} element_id={info['probe_id']}")
                except Exception:
                    pass

            for f in list(page.frames):
                if "bframe" not in (f.url or ""):
                    continue
                try:
                    el = f.frame_element()
                    if not el:
                        continue
                    probe_id = el.evaluate("""
                        (e) => {
                            if (!e.dataset.pwProbeId) {
                                e.dataset.pwProbeId = Math.random().toString(36).slice(2, 10);
                            }
                            return e.dataset.pwProbeId;
                        }
                    """)
                    print(f"          🔬 [{label} #{_captcha_detection_seq}] bframe outer <iframe>: element_id={probe_id}")
                except Exception:
                    pass
        except Exception as e:
            print(f"          🔬 [{label} #{_captcha_detection_seq}] identity snapshot failed: {e}")

    def _get_current_bframe_probe_id():
        """
        Stamps (once) and reads back a stable identity marker on whichever
        bframe element currently exists — same dataset.pwProbeId technique
        _log_captcha_identity_snapshot already uses. Used by the wait loop
        to tell "still the same challenge node" from "Google swapped in a
        genuinely new one" without needing to re-pin on a blind timer to
        find out.
        """
        for f in list(page.frames):
            if "bframe" not in (f.url or ""):
                continue
            try:
                el = f.frame_element()
                if not el:
                    continue
                return el.evaluate("""
                    (e) => {
                        if (!e.dataset.pwProbeId) {
                            e.dataset.pwProbeId = Math.random().toString(36).slice(2, 10);
                        }
                        return e.dataset.pwProbeId;
                    }
                """)
            except Exception:
                return None
        return None

    def _pin_still_onscreen():
        """
        Cheap health check on the LAST thing _pin_captcha_box() pinned — the
        bframe itself, always the last element appended to _pinned_elements
        — without the full per-element print spam _log_pin_diagnostics does.
        False means our styling got silently overwritten and needs redoing.
        """
        if not _pinned_elements:
            return False
        try:
            _bframe_el = _pinned_elements[-1]
            _rect = _bframe_el.evaluate("(e) => { const r = e.getBoundingClientRect(); return {w: r.width, h: r.height}; }")
            return bool(_rect and _rect.get("w", 0) > 0 and _rect.get("h", 0) > 0)
        except Exception:
            return False

    def _find_captcha_scope_frame():
        """
        Returns the frame that actually EMBEDS the current reCAPTCHA widget
        — the currently-visible bframe's own parent_frame — so token/aria-
        checked reads can be scoped to that ONE specific widget instance
        instead of every frame on the page. See the CONFIRMED BROKEN
        2026-07-13 note above the token check for why this matters: a
        page-wide search can silently pick up a stale, unrelated
        g-recaptcha-response element left over from an earlier step's
        (hidden but still-attached) iframe.

        Returns None if no bframe currently exists at all — callers should
        fall back to a page-wide search in that case (e.g. before any
        challenge has appeared this call).
        """
        for f in list(page.frames):
            if "bframe" not in (f.url or ""):
                continue
            try:
                return f.parent_frame
            except Exception:
                return None
        return None

    def _read_scoped_token(scope_frame):
        """
        Reads g-recaptcha-response from WITHIN the shared DOM container of
        the currently-visible bframe and its sibling anchor iframe — not
        just anywhere in their parent frame's document.

        CONFIRMED BROKEN 2026-07-13, SECOND incident same day, AFTER the
        frame-level fix above already shipped (v1.9.2): scoping to the
        bframe's parent_frame was not enough. debug_logs/run_20260713_173049.log
        lines 588-601 (cycle #1) and 621-634 (cycle #2) show the SAME bframe
        (element_id fn0eqkj9, re-detected twice, not a new challenge) and
        the SAME token textarea (element_id xzaquzgb) reading len=0 via
        _log_captcha_identity_snapshot's simpler query at both DETECTED and
        SOLVED — yet the signal line one row above each "✅ CAPTCHA solved!"
        cited a DIFFERENT reading entirely (len=2382, line 597 and 630).
        Same frame, two different g-recaptcha-response-ish elements — Indeed's
        review-m frame most likely embeds more than one reCAPTCHA instance
        (e.g. a background "invisible" badge alongside the interactive
        challenge widget), and a frame-wide querySelectorAll can't tell them
        apart. That false "solved" auto-clicked Submit ~1s after each pin
        (lines 602, 635), which is what was resetting/reshaping the live
        challenge every cycle.

        Fixed by going one level narrower than the frame: find the bframe
        and anchor's own <iframe> DOM elements (both live in the same
        frame's document), walk up each one's ancestor chain, and take the
        nearest COMMON container — the one wrapper element that actually
        belongs to THIS widget instance. Search for the token ONLY inside
        that container. Returns (token_len, element_id) so the caller can
        log exactly which node was read (the element_id uses the same
        dataset.pwProbeId stamping _log_captcha_identity_snapshot already
        uses, so it's directly comparable against those log lines).

        Returns (0, None) if no bframe/anchor pair or no common container
        can be found in scope_frame's document — the caller falls back to
        the old frame-wide search in that case, e.g. before pinning, when
        the DOM shape this walk assumes may not exist yet.
        """
        if scope_frame is None:
            return 0, None
        try:
            result = scope_frame.evaluate("""
                () => {
                    const iframes = Array.from(document.querySelectorAll('iframe'));
                    const bEl = iframes.find(f => (f.src || '').includes('bframe'));
                    const aEl = iframes.find(f => (f.src || '').includes('anchor'));
                    if (!bEl || !aEl) return null;

                    const ancestorsOf = (el) => {
                        const chain = [];
                        let cur = el;
                        while (cur) { chain.push(cur); cur = cur.parentElement; }
                        return chain;
                    };
                    const bChain = ancestorsOf(bEl);
                    const aSet = new Set(ancestorsOf(aEl));
                    let common = null;
                    for (const node of bChain) {
                        if (aSet.has(node)) { common = node; break; }
                    }
                    if (!common) return null;

                    const tokenEl = common.querySelector(
                        'textarea[name^="g-recaptcha-response"], [id^="g-recaptcha-response"]'
                    );
                    if (!tokenEl) return { token_len: 0, element_id: null };
                    if (!tokenEl.dataset.pwProbeId) {
                        tokenEl.dataset.pwProbeId = Math.random().toString(36).slice(2, 10);
                    }
                    return {
                        token_len: (tokenEl.value && tokenEl.value.length > 10) ? tokenEl.value.length : 0,
                        element_id: tokenEl.dataset.pwProbeId,
                    };
                }
            """)
            if result is None:
                return 0, None
            return result.get("token_len", 0) or 0, result.get("element_id")
        except Exception:
            return 0, None

    def _captcha_actually_visible_core(after_pin: bool = False):
        """
        True only if the reCAPTCHA bframe iframe is currently visible — not just
        attached to the DOM. reCAPTCHA never removes the bframe after a solve, it
        only hides it, so a plain "is this frame present" check stays True forever
        once a CAPTCHA has ever appeared on this page. That staleness was causing
        every subsequent Submit-retry to re-trigger the full alert+email flow even
        when nothing was actually showing.

        `after_pin` distinguishes the two places this is called from, because
        which signal is trustworthy flips once _pin_captcha_box() has run:
          - after_pin=False — the very first, pre-detection gate check (called
            once, before anything has been pinned). The bframe still has
            whatever natural CSS Google gave it, so a rendered-challenge-UI
            check here is trustworthy and is treated as authoritative.
          - after_pin=True — every call inside the wait loop, AFTER pinning.
            _pin_captcha_box() forces the bframe's CSS with !important, which
            can keep it looking "visible" even after Google hides it
            internally post-solve (see CONFIRMED BROKEN 2026-07-09 below) —
            so here the token/aria-checked signals stay authoritative and the
            rendered-UI check only runs as a last-resort fallback.

        CONFIRMED BROKEN 2026-07-08: this used to run a `document.querySelectorAll
        ('iframe')` scan via page.evaluate(), which executes in page.main_frame
        only. Indeed's SmartApply form (and its embedded reCAPTCHA widget) lives
        inside its own nested iframe (the "review-m"/"question" frame seen
        throughout this file's logging), so the bframe challenge iframe is a CHILD
        of THAT iframe, not of the top-level page — a top-level querySelectorAll
        can never see it. Result: this returned False 100% of the time across
        every run tonight (grepped "CAPTCHA DETECTED" across three full debug
        logs — zero matches) despite a real, on-screen CAPTCHA challenge
        (confirmed via a live screenshot Raghav sent). That's why the alert email
        never fired, and very likely why Submit kept failing — a click, real or
        synthetic, can't reach a button that's covered by an unsolved CAPTCHA
        overlay the pipeline never knew was there. This may also be why Indeed's
        server started rejecting the session shortly after: repeatedly hammering
        Submit against an unacknowledged CAPTCHA challenge looks exactly like bot
        behaviour to Indeed's own risk system.

        Fixed by asking each candidate frame for its own owning <iframe> element
        via Playwright's frame_element() — this works regardless of how deeply
        the frame is nested, since it doesn't rely on searching a specific
        document's DOM at all.
        """
        # 2026-07-08, second pass: the frame_element()-only version above (v1.2.9)
        # STILL missed a confirmed, live, on-screen CAPTCHA — Raghav sent a second
        # screenshot showing an active challenge on this exact review-module page
        # with zero "CAPTCHA DETECTED" in the log. One detection method clearly
        # isn't reliable enough here (possibly a frame_element()/is_visible()
        # timing or cross-origin quirk this environment can't be used to debug
        # live). Checking three independent signals now, ORed together, so a
        # single method's blind spot can't hide a real CAPTCHA again:
        #   (a) frame_element() + Playwright's own is_visible() + bounding box
        #   (b) frame.locator('body').is_visible() — Playwright's actionability
        #       engine, a different code path than (a)
        #   (c) asking the bframe's OWN document whether actual challenge UI text
        #       ("select all images", "verify", tile grid) is present, evaluated
        #       FROM INSIDE that frame — sidesteps cross-frame/parent traversal
        #       entirely. Deliberately checks for challenge-specific TEXT, not
        #       just "does this frame have any rendered size" — a hidden-after-
        #       solve iframe can still have a fully laid-out internal document
        #       even while invisible from the outside (hiding is usually done via
        #       the PARENT's CSS on the <iframe> tag, not by collapsing the
        #       child document itself), so a bare size check here would
        #       reintroduce the exact "stays true forever after solving" bug
        #       this function exists to avoid. Requiring real challenge text
        #       keeps this signal specific to an actual, active challenge.
        # CONFIRMED BROKEN 2026-07-09: once a challenge has ever rendered, this
        # function was staying True FOREVER, even minutes after Raghav solved
        # the CAPTCHA and hit Submit — the wait loop always burned the full 10
        # minutes and gave up, exactly matching his report ("pipeline is not
        # understand that I solve the cap... still sitting in that page").
        # Two compounding causes, both traced by re-reading this function and
        # the pin logic above it line by line rather than guessing:
        #
        #  1. The challenge-text check below OR'd in a bare 'recaptcha'
        #     substring match. That word is part of Google's own permanent
        #     widget branding/footer text, present in the bframe's document
        #     whether or not the challenge is solved — so this signal could
        #     never honestly report "resolved." Even the specific phrases
        #     ('select all images', etc.) aren't safe either: reCAPTCHA
        #     typically hides a solved challenge by collapsing/hiding its
        #     container, not by clearing the DOM text inside it, and
        #     f.evaluate() reads a frame's document regardless of whether
        #     that frame is visually hidden on the page — so text-based
        #     checks can't reliably prove "still active" at all.
        #  2. The pin/resize step above forces the bframe's ENTIRE inline
        #     style with `!important` on position/size, applied once when the
        #     challenge is first detected. If Google's own JS later tries to
        #     hide/collapse that same box after a solve (e.g. shrinking width/
        #     height via a plain, non-!important inline style), our earlier
        #     !important values win and the box visually stays put — so even
        #     the size/visibility-based signals could get stuck "visible."
        #
        # CONFIRMED BROKEN 2026-07-13: token-first ordering (the version this
        # replaces) short-circuited on ANY g-recaptcha-response token > 10
        # chars, before ever checking whether a challenge was actually
        # rendered on screen. Two live incidents same day: Coding Macaw
        # Bootcamp LLC (token len=2340, constant across 8 Submit retries) and
        # a "select all images with bicycles / click verify once there are
        # none left" challenge (token len=2404, also constant) — Raghav
        # confirmed the second one live, on screen, unsolved, no red border
        # ever applied. Neither ever printed "CAPTCHA DETECTED" because this
        # function returned False (solved) on the very first check, off a
        # token Enterprise reCAPTCHA had apparently already issued BEFORE the
        # visual challenge was completed — so "token present" is necessary
        # but not sufficient proof of "solved." Reordered below: an actively
        # rendered challenge UI, when we can trust that check (after_pin=
        # False — see docstring), now wins over the token. Token/aria-checked
        # remain authoritative once pinned (after_pin=True), for the reason
        # in CONFIRMED BROKEN 2026-07-09 above — our own forced CSS can make
        # the UI check lie "still visible" after a real solve.
        def _challenge_ui_present():
            """
            Returns (True, reason) if a bframe shows a rendered, active
            challenge UI, else (False, None). Three independent signals,
            checked per-bframe so any one of them can catch a variant the
            others miss:
              (a) frame_element() + Playwright's own is_visible() + bounding box
              (b) frame.locator('body').is_visible() — separate code path
              (c) reCAPTCHA's own challenge-grid class names — stable across
                  object type (buses/bicycles/crosswalks/...) and across the
                  "select N, submit once" vs. "click each match until none
                  remain" variants, so this doesn't depend on wording at all
              (d) instructional phrase text, broadened beyond the original
                  3-phrase list to also catch the "click each match until
                  none remain" variant's wording
            """
            for f in list(page.frames):
                if "bframe" not in (f.url or ""):
                    continue

                try:
                    el = f.frame_element()
                    if el and el.is_visible():
                        box = el.bounding_box()
                        if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
                            return True, f"bframe element visible, box={box}"
                except Exception:
                    pass

                try:
                    if f.locator("body").first.is_visible(timeout=1000):
                        return True, "bframe body visible (Playwright actionability check)"
                except Exception:
                    pass

                try:
                    grid_selector = f.evaluate("""
                        () => {
                            const sels = [
                                '.rc-imageselect-table-33', '.rc-imageselect-table-44',
                                '.rc-imageselect-tile', '.rc-imageselect-target',
                                '.rc-imageselect-challenge', '.rc-imageselect'
                            ];
                            for (const s of sels) {
                                if (document.querySelector(s)) return s;
                            }
                            return null;
                        }
                    """)
                    if grid_selector:
                        return True, f"bframe challenge grid present ({grid_selector})"
                except Exception:
                    pass

                try:
                    matched_phrase = f.evaluate("""
                        () => {
                            const t = (document.body.innerText || '').toLowerCase();
                            const phrases = [
                                'select all images with', 'select all squares with',
                                'click verify once', 'there are none left',
                                'click each matching image', 'skip'
                            ];
                            for (const p of phrases) {
                                if (t.includes(p)) return p;
                            }
                            return null;
                        }
                    """)
                    if matched_phrase:
                        return True, f"bframe contains active challenge text ('{matched_phrase}')"
                except Exception:
                    pass

            return False, None

        if not after_pin:
            _visible, _reason = _challenge_ui_present()
            if _visible:
                _log_signal_transition(True, _reason)
                return True

        # CONFIRMED BROKEN 2026-07-13: this used to search EVERY frame on the
        # page and return the first non-empty g-recaptcha-response found. A
        # live run showed that picking up an unrelated element with a stale,
        # constant len=2361 value while the ACTUAL challenge's own response
        # field (independently confirmed via _log_captcha_identity_snapshot's
        # simpler, correctly-scoped query) stayed len=0 the entire time —
        # Indeed's SmartApply flow can leave earlier step iframes attached
        # (hidden) as the user progresses through steps, and any of them
        # could carry a leftover recaptcha instance. Once pinned, the
        # pipeline was declaring "solved" off that stale field within ~1s
        # and auto-clicking Submit before Raghav had any real chance to
        # solve the actual on-screen challenge — Submit failed, the outer
        # retry loop called this whole function fresh again, and it
        # repeated (6 cycles observed in one run before it was killed).
        #
        # Fixed by scoping the search to the frame that actually EMBEDS the
        # current bframe (its own parent_frame) instead of every frame on
        # the page — ties the token check to the SAME widget instance the
        # rest of this function is looking at, without needing to know
        # Google's internal widget-id scheme.
        _scope_frame = _find_captcha_scope_frame()

        # Token check — the canonical, Google-controlled solved signal.
        # Google populates this element with a long opaque token once the
        # widget considers itself solved — independent of our CSS overrides,
        # and it lives in the SAME document that embeds the widget (Indeed's
        # own SmartApply frame), not inside the cross-origin anchor/bframe
        # iframes, so Playwright can read it directly.
        #
        # Primary read: container-scoped (see _read_scoped_token() for the
        # 2026-07-13 second-incident root cause — frame-level scoping alone
        # wasn't enough, this narrows to the shared DOM container of the
        # bframe+anchor pair). Every "solved" declaration from this signal
        # logs the exact element_id read, per the same incident.
        _container_token_len, _container_token_id = _read_scoped_token(_scope_frame)
        if _container_token_len:
            _log_signal_transition(
                False,
                f"g-recaptcha-response token present (container-scoped), "
                f"len={_container_token_len}, element_id={_container_token_id}"
            )
            return False  # solved — authoritative signal, container-scoped

        # Fallback: the container walk above found no bframe/anchor pair or
        # no common container (e.g. before pinning, when the DOM shape it
        # assumes may not exist yet) — fall back to the frame-wide search
        # rather than silently treating this as unsolved. NOTE: this
        # fallback carries the exact same ambiguity the container-scoping
        # fix above exists to avoid (multiple g-recaptcha-response elements
        # in one frame) — it's a safety net for when the container walk
        # can't run at all, not a substitute for it. If this path starts
        # firing "solved" often, that's worth investigating on its own.
        _token_candidates = (
            [_scope_frame] if _scope_frame is not None
            else [f for f in list(page.frames) if not _is_recaptcha_frame(f)]
        )
        for f in _token_candidates:
            if f is None:
                continue
            try:
                _fallback = f.evaluate("""
                    () => {
                        const el = document.querySelector(
                            'textarea[name^="g-recaptcha-response"], [id^="g-recaptcha-response"]'
                        );
                        if (!el) return { token_len: 0, element_id: null };
                        if (el.value && el.value.length > 10) {
                            if (!el.dataset.pwProbeId) {
                                el.dataset.pwProbeId = Math.random().toString(36).slice(2, 10);
                            }
                            return { token_len: el.value.length, element_id: el.dataset.pwProbeId };
                        }
                        return { token_len: 0, element_id: null };
                    }
                """)
                _fallback_len = (_fallback or {}).get("token_len", 0)
                if _fallback_len:
                    _log_signal_transition(
                        False,
                        f"g-recaptcha-response token present (frame-wide fallback — container walk "
                        f"found nothing), len={_fallback_len}, element_id={(_fallback or {}).get('element_id')}"
                    )
                    return False  # solved — fallback signal, stop here
            except Exception:
                pass

        # Second authoritative signal, independent of the token: the anchor
        # (checkbox) iframe's own checkbox element sets aria-checked="true"
        # the moment Google considers the widget solved. Read from INSIDE
        # that frame's own document — untouched by our bframe CSS override,
        # since we never style the anchor frame's internals, only its
        # z-index on the outer <iframe> tag. Scoped the same way as the
        # token check above: only the anchor iframe that's a SIBLING of the
        # current bframe (same parent_frame) counts, so a stale anchor left
        # over from an earlier step can't win here either.
        for f in list(page.frames):
            if "anchor" not in (f.url or ""):
                continue
            if _scope_frame is not None:
                try:
                    if f.parent_frame is not _scope_frame:
                        continue
                except Exception:
                    continue
            try:
                checked = f.evaluate("""
                    () => {
                        const cb = document.querySelector('#recaptcha-anchor, .recaptcha-checkbox');
                        return !!cb && cb.getAttribute('aria-checked') === 'true';
                    }
                """)
                if checked:
                    _log_signal_transition(False, "anchor checkbox aria-checked=true")
                    return False  # solved — checkbox confirms
            except Exception:
                pass

        if after_pin:
            # Last-resort fallback, post-pin only: neither authoritative
            # signal fired, so fall back to the UI check anyway rather than
            # risk hanging — this can reintroduce the 2026-07-09 "stuck
            # visible forever" bug in rare cases, but only after both real
            # signals came back empty, which itself would be unusual.
            _visible, _reason = _challenge_ui_present()
            if _visible:
                _log_signal_transition(True, _reason)
                return True

        _log_signal_transition(False, "no signal matched (no bframe/anchor found, or no captcha ever appeared)")
        return False

    def _captcha_actually_visible(after_pin: bool = False):
        """
        Thin wrapper around _captcha_actually_visible_core() that enforces a
        minimum-time floor (config.CAPTCHA_MIN_SOLVE_FLOOR_SEC, currently 3s)
        before trusting a "solved" result — independent of WHICH signal
        claimed solved (token, aria-checked, or the no-signal fallthrough).

        Added 2026-07-13 after the container-scoping fix above (also added
        the same day, see _read_scoped_token()) still wasn't proven to close
        every gap by itself — this is a backstop, not a replacement: even if
        some future DOM shape defeats the element-scoping again, a "solved"
        declaration within 3 seconds of first detecting a challenge is not
        physically plausible for a human to have completed an image-
        selection challenge, so it's treated as a false positive on its face
        regardless of cause. Only applies post-pin (after_pin=True) — the
        pre-pin gate check isn't declaring a solve, it's detecting whether a
        challenge exists at all, which the floor has no bearing on.

        Every time this floor actually blocks something, it's logged
        distinctly (⏱) and persisted via _record_premature_solve_block() so
        a recurring pattern shows up as a rolling 7-day count instead of
        disappearing into the log the moment the run ends — if the
        element-scoping fixes above ever regress or a new DOM shape defeats
        them, this is what surfaces it instead of the bug going unnoticed
        until the next live incident.
        """
        _visible = _captcha_actually_visible_core(after_pin=after_pin)
        if after_pin and not _visible and _event["detected_at"] is not None:
            _elapsed = (datetime.now() - _event["detected_at"]).total_seconds()
            _floor = getattr(cfg, "CAPTCHA_MIN_SOLVE_FLOOR_SEC", 3)
            if _elapsed < _floor:
                global _premature_solve_blocks_this_run
                _premature_solve_blocks_this_run += 1
                _rolling_7d = _record_premature_solve_block()
                print(f"          ⏱ Blocked premature solve declaration — only {_elapsed:.1f}s "
                      f"since detection (floor: {_floor}s) — treating as still unsolved "
                      f"[{_premature_solve_blocks_this_run} this run, {_rolling_7d} in the past 7 days]")
                if _rolling_7d > 3:
                    print(f"          🚨 {_rolling_7d} premature-solve blocks in the past 7 days — "
                          f"the element-scoping in _read_scoped_token()/_find_captcha_scope_frame() "
                          f"likely still has a gap. Worth investigating rather than assuming the "
                          f"3s floor alone is enough long-term.")
                return True  # force "still showing" — the floor overrides whatever triggered this
        return _visible

    try:
        captcha_visible = _captcha_actually_visible()
        if not captcha_visible:
            return True  # no CAPTCHA — all good

        # Get the current page URL
        current_url = job_url or ""
        try:
            current_url = page.url or job_url or ""
        except Exception:
            pass

        _captcha_detection_seq += 1
        print(f"\n          🚨🚨🚨  CAPTCHA DETECTED — ACTION REQUIRED  🚨🚨🚨")
        print(f"          👉 Go to your Mac and solve the CAPTCHA in the browser window")
        print(f"          ⚠️  Opening the link on your phone will NOT solve it — wrong session")
        print(f"          ⏳ Waiting up to 10 minutes...")
        _log_captcha_identity_snapshot("DETECTED")

        # Start the consolidated event record — everything from here on
        # (pin result, poll transitions, outcome) gets appended to this same
        # _event dict instead of only living as scattered print() lines.
        _event["detected_at"] = datetime.now()
        _event["detect_reason"] = _last_signal_logged["key"][1] if _last_signal_logged["key"] else "unknown"

        # Email alert — clear Mac-only instructions
        try:
            notifier.send_captcha_alert(
                title=title,
                company=company,
                job_url=current_url,
            )
        except Exception as e:
            print(f"          ⚠  Could not send CAPTCHA email: {e}")

        # Mac system notification — visible even when terminal is behind other windows
        try:
            import subprocess
            safe_title   = (title or "job").replace('"', "'")
            safe_company = (company or "company").replace('"', "'")
            subprocess.run([
                "osascript", "-e",
                f'display notification "Go to your Mac browser and solve the CAPTCHA for {safe_title} @ {safe_company}. You have 10 minutes." with title "🚨 CAPTCHA — Solve on Mac" sound name "Ping"'
            ], timeout=5)
            print(f"          🔔 Mac system notification sent")
        except Exception as e:
            print(f"          ⚠  Mac notification failed: {e}")

        # Fix CAPTCHA window so Verify button is fully visible and clickable
        #
        # CONFIRMED BROKEN 2026-07-08, same bug class as _captcha_actually_visible():
        # this ran `document.querySelectorAll('iframe')` via page.evaluate() (top-
        # level document only), but the bframe is nested inside the SmartApply
        # iframe, so `bframe` was always undefined and `if (!bframe) return;` bailed
        # out immediately, every time — meanwhile the print() below fired
        # unconditionally regardless of whether the JS did anything, so it always
        # claimed "pinned" even when nothing happened. Raghav's screenshots show
        # the real CAPTCHA rendered small/awkwardly positioned on the page,
        # unmodified — consistent with this doing nothing the whole time.
        #
        # Fixed the same way as detection: find the bframe's frame object directly
        # via page.frames, then call ElementHandle.evaluate() on its OWN
        # frame_element() — the callback receives that exact iframe element as its
        # argument, so the parent-chain walk and styling happen in the correct
        # document regardless of nesting depth, instead of guessing from the top.
        # CONFIRMED STILL BROKEN 2026-07-09, second attempt: the pixel-offset
        # math above (compute top-level window size + the SmartApply iframe's
        # bounding box, subtract) was still landing the box off-window.
        # Rather than keep patching that arithmetic — it depends on reading
        # the right ancestor frame, catching viewport-resize timing, and
        # correctly handling however many levels Indeed happens to nest
        # things THIS week, all of which have already changed once — this
        # takes a structurally different approach that doesn't need to
        # compute an offset AT ALL:
        #
        # `position: fixed; top/left: 50%` is only wrong because the
        # bframe's CONTAINING BLOCK (its nearest ancestor iframe's own
        # rendered viewport) isn't the same size as the real browser window.
        # So instead of calculating where the center of the real window
        # falls inside that mismatched containing block, just make the
        # containing block ITSELF the size of the real window first: walk
        # every ancestor iframe between the bframe and the top-level page
        # (there may be one level of nesting or several — doesn't matter,
        # this walks all of them) and force each one to `position:fixed;
        # inset:0; width:100vw; height:100vh` — i.e. make every iframe in the
        # chain a fullscreen overlay of ITS OWN parent document. Once that's
        # done, the bframe's containing block genuinely IS the real window,
        # and the original simple `top:50%; left:50%; transform:translate(
        # -50%,-50%)` trick (the one that worked in the 2026-06-22 code, and
        # is immune to any future re-nesting Indeed does) becomes correct
        # again — no coordinates to compute, nothing to get wrong.
        # CONFIRMED STILL BROKEN 2026-07-10, third attempt: Raghav hit a
        # "select all squares with traffic lights" challenge — a taller 4x4
        # grid variant (vs. the 3x3 "select all images with X" variant this
        # was tuned against), and it got cut off at the bottom again, Verify
        # button unreachable. Two separate causes, both now fixed:
        #  1. This pin only ever ran ONCE, at the moment a CAPTCHA was first
        #     detected. If Google swaps in a taller/different challenge
        #     variant afterward (a retry after a wrong answer, or just a
        #     different challenge type served this time), or renders into a
        #     fresh bframe element, that later content was never re-pinned —
        #     whatever styling we applied at t=0 is all it ever got. Fixed by
        #     turning this into a reusable function and calling it not just
        #     once at detection, but again every few seconds throughout the
        #     wait loop below — cheap (it's idempotent CSS), and self-heals
        #     against any later DOM change instead of assuming the challenge
        #     never changes shape after the first render.
        #  2. The height budget (85vh, capped at 820px) was tuned against the
        #     3x3 grid's natural height and didn't leave enough room for a
        #     4x4 grid (more rows = taller natural content). Raised to 92vh /
        #     960px so taller variants fit too.
        def _pin_captcha_box() -> bool:
            try:
                for _f in list(page.frames):
                    if "bframe" not in (_f.url or ""):
                        continue

                    # Fullscreen every ancestor iframe between the bframe and
                    # the top-level page, from the innermost outward.
                    _ancestor = _f.parent_frame
                    _chain_urls = []
                    while _ancestor is not None:
                        try:
                            _chain_urls.append((_ancestor.url or "")[:60])
                        except Exception:
                            pass
                        try:
                            _anc_el = _ancestor.frame_element()
                            if _anc_el:
                                _anc_el.evaluate("""
                                    (el) => {
                                        el.style.cssText = [
                                            'position: fixed !important',
                                            'top: 0 !important',
                                            'left: 0 !important',
                                            'width: 100vw !important',
                                            'height: 100vh !important',
                                            'margin: 0 !important',
                                            'border: none !important',
                                            'z-index: 2147483000 !important',
                                        ].join(';');
                                    }
                                """)
                                # Track exactly what we pinned so post-solve cleanup
                                # can strip it directly instead of re-matching frames
                                # by URL substring.
                                _pinned_elements.append(_anc_el)
                        except Exception:
                            pass
                        _ancestor = _ancestor.parent_frame

                    try:
                        _bframe_el = _f.frame_element()
                    except Exception:
                        continue
                    if not _bframe_el:
                        continue
                    try:
                        _bframe_el.evaluate("""
                            (bframe) => {
                                // Step 1: Walk up and remove overflow:hidden / clipping on parent chain
                                let el = bframe;
                                for (let i = 0; i < 10; i++) {
                                    el = el.parentElement;
                                    if (!el || el === document.body) break;
                                    el.style.overflow  = 'visible';
                                    el.style.height    = 'auto';
                                    el.style.maxHeight = 'none';
                                    el.style.clip      = 'none';
                                    el.style.clipPath  = 'none';
                                }

                                // Step 2: Now that every ancestor iframe has been
                                // forced to fill the real window (above), this
                                // element's own containing block genuinely IS the
                                // real browser viewport — plain percentage
                                // centering works correctly here. Height budget
                                // widened again 2026-07-13 (95vh / 1000px cap,
                                // up from 92vh / 960px) after a "select all
                                // images with bicycles" challenge still had its
                                // Verify button cut off below the viewport with
                                // no way to scroll to it — confirmed fixed via a
                                // manual DevTools test at these exact values.
                                // overflow-y: auto (already set below) is kept
                                // explicit so any remaining overflow is
                                // scrollable rather than clipped, for whatever
                                // variant is still taller than this budget.
                                bframe.style.cssText = [
                                    'position: fixed !important',
                                    'top: 50% !important',
                                    'left: 50% !important',
                                    'transform: translate(-50%, -50%) !important',
                                    'width: 480px !important',
                                    'height: 95vh !important',
                                    'max-height: 1000px !important',
                                    'min-height: 600px !important',
                                    'z-index: 2147483647 !important',
                                    'border: 4px solid #ff0000 !important',
                                    'border-radius: 10px !important',
                                    'background: white !important',
                                    'box-shadow: 0 8px 32px rgba(0,0,0,0.5) !important',
                                    'overflow-y: auto !important',
                                ].join(';');

                                // Step 3: Also surface any sibling anchor checkbox iframe
                                // in the SAME parent document as this bframe.
                                const parentDoc = bframe.ownerDocument;
                                if (parentDoc) {
                                    parentDoc.querySelectorAll('iframe').forEach(f => {
                                        if (f.src && f.src.includes('anchor')) {
                                            f.style.zIndex = '2147483646';
                                        }
                                    });
                                }
                            }
                        """)
                        # Track the bframe itself too — same reason as the
                        # ancestor tracking above.
                        _pinned_elements.append(_bframe_el)
                        print(f"          🧭 CAPTCHA ancestor chain ({len(_chain_urls)} level(s)): {_chain_urls}")
                        return True
                    except Exception:
                        continue
                return False
            except Exception:
                return False

        def _clear_pinned_captcha_elements():
            """
            Strip the forced cssText styling _pin_captcha_box() applied, using the
            exact element handles it recorded in _pinned_elements — not a URL-based
            re-match. Safe to fully remove the style attribute (rather than
            restoring specific properties) because _pin_captcha_box() only ever
            assigns via cssText, which replaces the whole inline style each time;
            there's no pre-existing style to preserve.

            Called right before the post-solve Submit click: otherwise the pinned
            bframe (z-index 2147483647, forced to a centered 480px x 92vh white
            box) and its ancestor iframes stay fixed in place after Google hides
            the challenge internally, and a coordinate click meant for Submit
            lands on that leftover overlay instead.
            """
            for _el in _pinned_elements:
                try:
                    _el.evaluate("(e) => e.removeAttribute('style')")
                except Exception:
                    pass
            _pinned_elements.clear()

        def _log_pin_diagnostics(pin_succeeded: bool):
            """
            Diagnostic-only, added 2026-07-12 after a real "select all squares
            with buses" CAPTCHA where Raghav couldn't reach the Verify button —
            with no log evidence either way of whether _pin_captcha_box()'s
            styling actually rendered on-screen. _pin_captcha_box() returning
            True only means our JS assignment didn't throw; it says nothing
            about whether the style actually took effect and stayed applied.
            Two things it can't tell us on its own:
              1. The bframe's REAL on-screen bounding box after our cssText
                 assignment — getBoundingClientRect(), read back independently.
                 If this comes back zero-sized, our CSS never actually
                 rendered, regardless of whether the JS call itself succeeded.
              2. Whether each pinned element's COMPUTED style (getComputedStyle,
                 not just "we set el.style.cssText without error") still
                 reflects what we assigned — catches Google's own JS (or a
                 re-render) silently overwriting our styling a moment later,
                 which a boolean return value from _pin_captcha_box() can
                 never detect on its own.
            This turns "did the pin actually work" from something inferred
            after the fact off a screenshot into something the log states
            directly, every time this runs — not just when something looks
            wrong.
            """
            _event["pin_ok"] = pin_succeeded
            if not pin_succeeded or not _pinned_elements:
                print(f"          🔬 pin diagnostics: nothing pinned this cycle "
                      f"(pin_succeeded={pin_succeeded}, tracked_elements={len(_pinned_elements)})")
                return
            for _el in _pinned_elements:
                try:
                    _info = _el.evaluate("""
                        (el) => {
                            const r = el.getBoundingClientRect();
                            const cs = getComputedStyle(el);
                            return {
                                tag: el.tagName,
                                rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                                position: cs.position,
                                zIndex: cs.zIndex,
                                display: cs.display,
                                visibility: cs.visibility,
                            };
                        }
                    """)
                    _rect = _info.get("rect", {}) if _info else {}
                    _onscreen = _rect.get("w", 0) > 0 and _rect.get("h", 0) > 0
                    print(f"          🔬 pin check [{_info.get('tag') if _info else '?'}]: "
                          f"rect={_rect} position={_info.get('position') if _info else '?'} "
                          f"z-index={_info.get('zIndex') if _info else '?'} "
                          f"display={_info.get('display') if _info else '?'} "
                          f"→ {'ON-SCREEN' if _onscreen else '⚠ ZERO-SIZE / NOT ACTUALLY RENDERED'}")
                    # The bframe itself (the actual challenge box, not an
                    # ancestor wrapper) is what the summary block cares
                    # about — reuse this same read instead of a second pass.
                    if _info and _info.get("tag") == "IFRAME" and _onscreen:
                        _event["pin_rect"] = f"{_rect.get('w')}x{_rect.get('h')}"
                except Exception as e:
                    print(f"          🔬 pin check: could not read element state ({e}) — likely detached from DOM already")

        _pinned_bframe_probe_id = {"id": None}
        try:
            _pin_ok = _pin_captcha_box()
            if _pin_ok:
                print(f"          🔲 CAPTCHA window pinned — Verify button is fully visible")
            else:
                print(f"          ⚠  Could not pin CAPTCHA window (bframe element not reachable) — solve it in its default position")
            _log_pin_diagnostics(_pin_ok)
            _pinned_bframe_probe_id["id"] = _get_current_bframe_probe_id()
            print(f"          💡 TIP: If still stuck, click the CAPTCHA area then press Tab → Enter")
        except Exception as e:
            print(f"          ⚠  CAPTCHA resize failed: {e}")

        # Widen viewport so there's more room. Moved BEFORE the pin logic
        # used to be the plan, but since the pin no longer depends on the
        # viewport size at all (no coordinates computed from it — see above),
        # order no longer matters here either way. Left after for minimal
        # diff / lowest risk.
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
        except Exception:
            pass

        # ── Wait loop — up to 10 minutes ─────────────────────────────────────
        CAPTCHA_TIMEOUT = 600
        _halfway_reminder_sent = False
        for i in range(CAPTCHA_TIMEOUT):
            time.sleep(1)
            try:
                frames = list(page.frames)
                still_captcha = _captcha_actually_visible(after_pin=True)

                # Changed 2026-07-13: this used to blind-re-pin every ~3
                # ticks regardless of whether anything had actually changed
                # — Raghav reported the box appearing to "reset/reshape"
                # mid-solve and asked for it to stay exactly as pinned,
                # untouched, while he's actively working on it. The original
                # 2026-07-10 reason for periodic re-pinning is still real
                # (a retry or taller challenge variant can appear later,
                # unpinned) — so this keeps that protection but makes it
                # event-driven instead of a blind timer: only touch the
                # frame if the bframe node has genuinely been swapped for a
                # different one (Google served a new challenge instance) or
                # the existing pin has visibly stopped rendering (something
                # overwrote our styling). Same node, still on-screen at the
                # size we set → left alone, every tick.
                if still_captcha:
                    _current_probe_id = _get_current_bframe_probe_id()
                    _repin_reason = None
                    if _current_probe_id is not None and _current_probe_id != _pinned_bframe_probe_id["id"]:
                        _repin_reason = "challenge instance changed (new bframe node)"
                    elif not _pin_still_onscreen():
                        _repin_reason = "pin styling no longer rendering (possibly overwritten)"
                    if _repin_reason:
                        print(f"          🔁 Re-pinning — {_repin_reason}")
                        _repin_ok = _pin_captcha_box()
                        _log_pin_diagnostics(_repin_ok)
                        _pinned_bframe_probe_id["id"] = _current_probe_id

                # Check if the page already confirmed (user may have clicked Submit
                # manually). Indeed's apply UI runs inside a nested iframe, not the
                # top-level document, so scan every frame — not just page.body.
                page_text = ""
                for _f in frames:
                    try:
                        page_text += " " + (_f.evaluate("() => document.body ? document.body.innerText : ''") or "")
                    except Exception:
                        pass
                page_text = page_text.lower()

                already_confirmed = any(phrase in page_text for phrase in [
                    "application submitted", "successfully applied",
                    "your application has been", "application received",
                    "thanks for applying", "thank you for applying",
                    "application complete",
                ])

                if already_confirmed:
                    print(f"          ✅ Confirmation detected during CAPTCHA wait — application submitted!")
                    _clear_pinned_captcha_elements()
                    _consecutive_captcha_failures = 0  # success — reset streak
                    _print_captcha_summary("solved-and-submitted successfully (confirmed during wait)")
                    return True

                if not still_captcha:
                    print(f"          ✅ CAPTCHA solved! Auto-clicking Submit...")
                    _log_captcha_identity_snapshot("SOLVED")
                    # Remove the forced pin styling BEFORE attempting the click —
                    # otherwise the leftover overlay (still centered, still at
                    # z-index 2147483647) intercepts the coordinate click meant
                    # for the real Submit button underneath it.
                    _clear_pinned_captcha_elements()
                    time.sleep(2)
                    # Fixed 2026-07-09: this used a raw JS b.click() — an
                    # UNTRUSTED synthetic event, the exact same kind already
                    # proven unreliable against Indeed's reCAPTCHA-gated Submit
                    # button in 1.2.7 (_click_nav/_click_submit_btn were fixed
                    # there to use a real page.mouse.click() instead). This
                    # specific auto-click-after-solve path was never updated —
                    # it's a third, separate click implementation that got
                    # missed. Very likely why Raghav couldn't get Submit to
                    # register even after solving the CAPTCHA himself: this
                    # silent background click may have been firing and failing
                    # the whole time. Now tries a real trusted mouse-click at
                    # the button's actual coordinates first, falling back to
                    # the JS click only if no matching button can be located
                    # via Playwright's own locator.
                    _clicked_after_solve = False
                    try:
                        for frame in page.frames:
                            if _is_recaptcha_frame(frame):
                                continue
                            for _label in ["Submit your application", "Submit application",
                                           "Submit", "Apply now"]:
                                try:
                                    _btn = frame.locator(f"button:has-text('{_label}')").first
                                    if _btn.count() > 0 and _btn.is_visible(timeout=800):
                                        _box = _btn.bounding_box()
                                        if _box:
                                            _btn.scroll_into_view_if_needed()
                                            _cx = _box["x"] + _box["width"] / 2
                                            _cy = _box["y"] + _box["height"] / 2
                                            frame.page.mouse.click(_cx, _cy)
                                            print(f"          ✔  Real mouse-click Submit after CAPTCHA: '{_label}'")
                                            _clicked_after_solve = True
                                            break
                                except Exception:
                                    continue
                            if _clicked_after_solve:
                                break
                    except Exception:
                        pass

                    if not _clicked_after_solve:
                        try:
                            for frame in page.frames:
                                if _is_recaptcha_frame(frame):
                                    continue
                                clicked = _safe_eval(frame, """
                                    () => {
                                        const kws = ['submit your application','submit application','submit','apply now'];
                                        const btns = Array.from(document.querySelectorAll('button,[role=button],input[type=submit]'));
                                        for (const b of btns) {
                                            if (!b.offsetParent) continue;
                                            const t = (b.innerText||b.textContent||b.value||'').toLowerCase().trim();
                                            if (kws.some(k=>t.includes(k))) { b.click(); return t; }
                                        }
                                        return null;
                                    }
                                """, None)
                                if clicked:
                                    print(f"          ✔  JS-clicked Submit after CAPTCHA (fallback): '{clicked}'")
                                    break
                        except Exception:
                            pass
                    time.sleep(6)
                    _consecutive_captcha_failures = 0  # success — reset streak

                    # Classify the outcome for the summary block by reusing
                    # the exact same confirm-phrase scan used above for
                    # already_confirmed — this function can't know whether
                    # the OUTER caller's later _is_confirmed() check will
                    # also agree, but it can at least report what its own
                    # click attempt achieved instead of assuming success.
                    _post_click_text = ""
                    for _f in list(page.frames):
                        try:
                            _post_click_text += " " + (_f.evaluate("() => document.body ? document.body.innerText : ''") or "")
                        except Exception:
                            pass
                    _post_click_text = _post_click_text.lower()
                    _submit_confirmed = any(phrase in _post_click_text for phrase in [
                        "application submitted", "successfully applied",
                        "your application has been", "application received",
                        "thanks for applying", "thank you for applying",
                        "application complete",
                    ])
                    if _submit_confirmed:
                        _outcome = "solved-and-submitted successfully"
                    elif _clicked_after_solve:
                        _outcome = "solved-but-submit-failed (clicked Submit, no confirmation yet)"
                    else:
                        _outcome = "solved-but-submit-failed (no Submit button could be clicked)"
                    _print_captcha_summary(_outcome)
                    return True

            except Exception:
                pass

            if i % 60 == 59:
                remaining = CAPTCHA_TIMEOUT - i - 1
                mins = remaining // 60
                secs = remaining % 60
                print(f"          ⏳ Still waiting for CAPTCHA... ({mins}m {secs}s left) — solve in Mac browser")

            # One reminder email at the halfway mark — the initial alert can get
            # buried or missed if you're away from your Mac, and a 10-minute
            # window with zero follow-up means the job silently times out with
            # no second chance to notice. Fires exactly once (guarded by the
            # flag, not by a modulo check), matching the single fire-on-
            # detection alert above rather than the risk of the old plain
            # visibility poll re-alerting on every tick.
            if not _halfway_reminder_sent and i >= CAPTCHA_TIMEOUT // 2:
                _halfway_reminder_sent = True
                _remaining = CAPTCHA_TIMEOUT - i - 1
                try:
                    notifier.send_alert(
                        subject=f"⏳ Still waiting — CAPTCHA for {title} @ {company}",
                        body=(
                            f"Halfway through the 10-minute window and this CAPTCHA is still "
                            f"unsolved ({_remaining // 60}m {_remaining % 60}s left).\n\n"
                            f"Job: {title} @ {company}\n"
                            f"Go to your Mac browser and solve it there — after this window "
                            f"the pipeline will skip this job and move on."
                        ),
                    )
                except Exception as e:
                    print(f"          ⚠  Could not send halfway reminder email: {e}")

        # ── Timeout path ──────────────────────────────────────────────────────
        print(f"          ❌ CAPTCHA not solved in 10 minutes — skipping this job")
        _print_captcha_summary("timed-out-and-skipped (10 minutes, no solve)")

        # RESET browser to Indeed homepage so the next job starts from a clean page
        print(f"          🔄 Resetting browser to Indeed homepage (clearing broken page state)...")
        try:
            page.goto("https://www.indeed.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(random.uniform(3, 5))
        except Exception as _nav_err:
            print(f"          ⚠  Homepage reset failed ({_nav_err}) — pipeline will still continue")

        # Track consecutive failures
        _consecutive_captcha_failures += 1
        print(f"          📊 Consecutive unsolved CAPTCHAs: {_consecutive_captcha_failures}")

        # Cooldown if streak is too high — Indeed is rate-limiting the session
        if _consecutive_captcha_failures >= CAPTCHA_COOLDOWN_THRESHOLD:
            _total_cooldowns_this_run += 1
            _max_cooldowns = getattr(cfg, "CAPTCHA_MAX_COOLDOWNS_PER_RUN", 2)

            if _total_cooldowns_this_run > _max_cooldowns:
                # Already cooled down _max_cooldowns times and still hitting CAPTCHAs
                # right after — nobody's here to solve them (unattended run). This is
                # a block, not noise. Stop instead of repeating the loop for hours.
                print(f"\n          🛑 {_total_cooldowns_this_run} CAPTCHA cooldowns this run with no solve — "
                      f"session is very likely blocked. Stopping Indeed instead of looping for hours.")
                try:
                    notifier.send_alert(
                        subject=f"🛑 Indeed run stopped — {_total_cooldowns_this_run} CAPTCHA cooldowns, likely blocked",
                        body=(
                            f"The Indeed pipeline hit {_total_cooldowns_this_run} CAPTCHA cooldown cycles "
                            f"this run with no successful solve. Stopping early instead of wasting hours.\n"
                            f"Try running while at your Mac to solve CAPTCHAs manually, or check if "
                            f"Indeed has flagged this session."
                        ),
                    )
                except Exception:
                    pass
                _indeed_blocked = True
                return False

            print(f"\n          ⏸  {_consecutive_captcha_failures} CAPTCHAs in a row — taking {CAPTCHA_COOLDOWN_SECS//60}-minute cooldown ({_total_cooldowns_this_run}/{_max_cooldowns} for this run) to let reCAPTCHA settle...")
            try:
                notifier.send_alert(
                    subject=f"⏸ Pipeline cooldown — {_consecutive_captcha_failures} consecutive CAPTCHAs (Indeed)",
                    body=(
                        f"The Indeed pipeline hit {_consecutive_captcha_failures} unsolved CAPTCHAs in a row.\n"
                        f"Taking a {CAPTCHA_COOLDOWN_SECS//60}-minute break to let reCAPTCHA cool down, then resuming automatically.\n"
                        f"({_total_cooldowns_this_run}/{_max_cooldowns} cooldowns used this run before it stops itself.)"
                    ),
                )
            except Exception:
                pass
            time.sleep(CAPTCHA_COOLDOWN_SECS)
            _consecutive_captcha_failures = 0  # reset after cooldown
            print(f"          ▶  Cooldown done — resuming pipeline")

        return False

    except Exception as e:
        print(f"          ⚠  CAPTCHA check error (continuing): {e}")
        return True  # don't crash — assume no CAPTCHA if the check itself blows up


def _safe_eval(ctx, js, default=None):
    """Run JS on any page/frame safely — never raises."""
    try:
        return ctx.evaluate(js)
    except:
        return default

def _wait_for_smartapply_frame(page, timeout_secs=12):
    """
    Wait until a smartapply.indeed.com frame appears — meaning the apply
    form has fully loaded.  Returns True if found, False on timeout.
    """
    for _ in range(timeout_secs * 2):
        for f in page.frames:
            try:
                if "smartapply.indeed.com" in (f.url or ""):
                    return True
            except:
                pass
        time.sleep(0.5)
    return False


def _find_active_form_ctx(page, verbose=True):
    """
    Find which frame has the active Indeed apply form (most visible inputs).
    Skips the Indeed search-bar frame (indeed.com/viewjob with text-input-what/where)
    when a smartapply frame is available — those are NOT the application form.
    Uses page.frames only (main_frame is already included, don't double-count).
    """
    best_ctx   = page
    best_count = 0

    all_frames = list(page.frames)  # main_frame is frames[0] — no duplicate
    if verbose:
        print(f"          🔍 Scanning {len(all_frames)} frame(s) for form inputs...")

    # Check if any smartapply frame exists — if so we must avoid the search-bar frame
    has_smartapply = any("smartapply.indeed.com" in (f.url or "") for f in all_frames)

    for i, frame in enumerate(all_frames):
        frame_url = ""
        try:
            frame_url = frame.url or ""
        except:
            pass

        # Skip the main indeed.com viewjob page (search bar #text-input-what/where)
        # when smartapply is available — those inputs are NOT the application form
        if has_smartapply and "indeed.com/viewjob" in frame_url and "smartapply" not in frame_url:
            if verbose:
                print(f"             Frame[{i}] url={frame_url[:60]!r}  ⏭ SKIP (search-bar frame)")
            continue

        try:
            n = frame.evaluate(
                "() => document.querySelectorAll("
                "'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]),"
                " select, textarea').length"
            )
            if verbose:
                print(f"             Frame[{i}] url={frame_url[:60]!r}  inputs={n or 0}")
            if n and n > best_count:
                best_count = n
                best_ctx   = frame
        except Exception as e:
            if verbose:
                print(f"             Frame[{i}] ⚠ error: {e}")

    if verbose:
        ctx_url = ""
        try:
            ctx_url = best_ctx.url or ""
        except:
            pass
        print(f"          ✔  Active frame: {ctx_url[:60]!r}  ({best_count} inputs)")

    return best_ctx

def _is_confirmed(page):
    """Check any frame for confirmation text.

    CONFIRMED FALSE POSITIVE 2026-07-09: the pipeline reported "Applied:
    submitted" and emailed Raghav a screenshot for GRP Solutions Inc — the
    screenshot itself shows the "Review your application" / "100%" page,
    with the Submit button never even clicked. This function fired True
    while still sitting on the pre-submission review page — most likely some
    hidden/accessibility-only text on that page (e.g. an ARIA live region
    pre-rendered with template text for screen readers, which document.
    innerText can still pick up even when visually hidden) happened to
    contain one of CONFIRM_PHRASES. A false "submitted" is a worse failure
    mode than a false "failed": it means a real job Raghav believes he
    applied to may never have actually gone to the employer.

    Fixed with a negative guard: known pre-submission review-page markers
    (URL still on review-m/review-module/questions-module/resume-selection,
    or the page still literally showing "review your application" /
    "you won't be able to edit your application") now veto any phrase match
    — a confirm-phrase hit on a page that's still clearly the review screen
    is treated as a false positive, not a success.
    """
    REVIEW_PAGE_URL_MARKERS = ("review-m", "review-module", "questions-module", "resume-selection")
    REVIEW_PAGE_TEXT_MARKERS = ("review your application", "you won't be able to edit your application")

    try:
        current_url = (page.url or "").lower()
    except Exception:
        current_url = ""
    on_review_url = any(m in current_url for m in REVIEW_PAGE_URL_MARKERS)

    all_frames = [page.main_frame] + list(page.frames)
    for frame in all_frames:
        body = _safe_eval(frame, "() => document.body.innerText.toLowerCase()", "")
        if not body:
            continue
        if any(p in body for p in CONFIRM_PHRASES):
            if on_review_url or any(m in body for m in REVIEW_PAGE_TEXT_MARKERS):
                # Looks like a confirm-phrase match, but we're still
                # demonstrably on the review page — don't trust it.
                continue

            # CONFIRMED FALSE POSITIVE 2026-07-12: Air Treatment Corporation's
            # "Inside Sales Engineer" application got emailed to Raghav as
            # "✅ Application Submitted" while the actual page — screenshotted
            # by him at the time — was still sitting at 50% progress with 3
            # required HVAC-industry questions left blank, each showing
            # Indeed's own "Answer this question to continue" validation
            # error. Those fields never got answered (the AI fallback needed
            # for them wasn't available that run — see 1.8.6) so the form
            # never actually advanced, yet some CONFIRM_PHRASES text still
            # matched somewhere on that page. The on_review_url /
            # REVIEW_PAGE_TEXT_MARKERS guard above only recognizes Indeed's
            # own final review screen — it has no way to catch a stuck
            # QUESTIONS step, which is a different page entirely, and trying
            # to enumerate every stuck-page URL/text variant Indeed might
            # show is the same whack-a-mole this function already burned two
            # rounds on (see the 2026-07-09 note above). Instead of guessing
            # what kind of page this is, check a property of the page
            # itself: does it still have an unresolved required-field error?
            # Reuses the exact validation-error selectors already proven out
            # for the "form walk ended" retry logic elsewhere in this file.
            has_validation_errors = _safe_eval(frame, """
                () => {
                    const errSelectors = [
                        '[class*="error"]:not([class*="errorText--hidden"])',
                        '[class*="Error"]:not([class*="hidden"])',
                        '[aria-invalid="true"]',
                        '[aria-describedby*="error"]',
                        '.icl-TextInput--error',
                        '[data-testid*="error"]',
                        '[role="alert"]',
                    ];
                    for (const sel of errSelectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (!el.offsetParent) continue;
                            const t = (el.innerText || el.textContent || '').trim();
                            if (t && t.length > 2 && t.length < 300) return true;
                        }
                    }
                    return false;
                }
            """, False)
            if has_validation_errors:
                # A confirm-phrase hit on a page that's still showing an
                # unresolved required-field error is never trustworthy —
                # keep scanning other frames instead of declaring success.
                continue

            return True
    return False

def _get_nav_buttons(page, verbose=True):
    """Find nav buttons across all frames — catches Continue, Next, Submit, Apply, Review."""
    NAV_JS = """
        () => {
            const KWS = [
                'continue', 'next', 'submit', 'submit application',
                'submit your application', 'apply', 'apply now',
                'review your application', 'review application',
                'send application', 'complete application'
            ];
            // Exact phrases that must NOT match even if they contain a KWS word
            const SKIP = [
                'submit feedback', 'report an issue', 'report issue',
                'unable to continue', 'unable to proceed', 'report a problem',
                'give feedback', 'accessibility', 'sign in', 'log in',
                'back', 'previous', 'cancel', 'skip'
            ];
            return Array.from(document.querySelectorAll(
                'button, [role=button], input[type=submit], input[type=button]'
            )).filter(b => {
                if (!b.offsetParent) return false;
                const disabled = b.disabled || b.getAttribute('aria-disabled') === 'true';
                return !disabled;
            }).map(b => ({
                text: (b.innerText || b.textContent || b.value || '').trim().toLowerCase(),
                id: b.id || ''
            })).filter(b =>
                b.text.length > 0
                && KWS.some(k => b.text.includes(k))
                && !SKIP.some(s => b.text.includes(s))
            );
        }
    """
    all_frames = list(page.frames)  # no duplicate main_frame
    for i, frame in enumerate(all_frames):
        if _is_recaptcha_frame(frame):
            continue
        try:
            btns = frame.evaluate(NAV_JS) or []
            if btns:
                frame_url = ""
                try: frame_url = frame.url or ""
                except: pass
                if verbose:
                    btn_labels = [b.get("text","?") for b in btns]
                    print(f"          🔘 Nav buttons in frame[{i}] ({frame_url[:50]}): {btn_labels}")
                return btns, frame
        except:
            pass
    if verbose:
        print(f"          ⚠  No nav buttons found in any frame")
    return [], page.frames[0] if page.frames else page.main_frame

def _click_nav(frame, hint="continue", verbose=True):
    """Click Continue/Next/Submit in a specific frame. Scrolls into view first."""
    frame_url = ""
    try: frame_url = frame.url or ""
    except: pass
    if verbose:
        print(f"          👆 Clicking '{hint}' in frame ({frame_url[:50] or 'main'})...")

    _last_click_err = None
    for label in ["Continue", "Next", "Submit application", "Submit your application",
                  "Submit", "Apply", "Apply now", "Send application",
                  "Review your application", "Complete application"]:
        try:
            btn = frame.locator(f"button:has-text('{label}')").first
            if btn.count() > 0 and btn.is_visible(timeout=1500):
                btn.scroll_into_view_if_needed()
                btn.click(timeout=4000)
                if verbose:
                    print(f"          ✔  Clicked: '{label}'")
                return label
        except Exception as _ce:
            # Added 2026-07-08: this used to be a bare `except: pass`, so every
            # time Playwright's real (trusted) click failed we had zero record of
            # WHY — just fell straight to the JS fallback. The JS fallback's raw
            # element.click() dispatches an UNTRUSTED event, which some of
            # Indeed's buttons (this one is behind reCAPTCHA) silently ignore —
            # the DOM looks clicked but the real submit handler never fires.
            # Keeping the exception text so the next failure is diagnosable
            # instead of a guess.
            _last_click_err = str(_ce)[:200]

    # Real-mouse-click fallback — BEFORE the JS fallback. Uses Playwright's
    # actual input pipeline (a trusted browser-level click), unlike a raw JS
    # element.click(), which some of Indeed's buttons (gated behind invisible
    # reCAPTCHA) appear to silently ignore. Added 2026-07-08 after confirming
    # via data/stuck_submits.json that 8 straight "successful" JS clicks on
    # Submit never actually advanced the page — same review-m URL, same page
    # text, every time.
    try:
        for label in ["Submit your application", "Submit application", "Submit",
                      "Continue", "Next", "Apply now", "Send application"]:
            btn = frame.locator(f"button:has-text('{label}')").first
            if btn.count() > 0 and btn.is_visible(timeout=1000):
                box = btn.bounding_box()
                if box:
                    btn.scroll_into_view_if_needed()
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    frame.page.mouse.click(cx, cy)
                    if verbose:
                        print(f"          ✔  Real mouse-click: '{label}'")
                    return label
    except Exception as _me:
        _last_click_err = str(_me)[:200]

    # JS fallback — scrolls into view, checks aria-disabled (Indeed's pattern)
    if verbose:
        print(f"          ↩  Playwright failed ({_last_click_err or 'unknown'}) — JS click fallback")
    result = _safe_eval(frame, """
        () => {
            // NOTE: bare 'review' as a keyword used to match inside 'preview' (e.g.
            // "preview what the employer sees" contains "review"), causing the JS
            // fallback to click Preview instead of Submit on the final review page.
            // Fixed 2026-07-08: use the full phrase, and explicitly exclude 'preview'.
            const kws = ['continue','next','submit','review your application','apply','send'];
            const BACK = ['back','previous','cancel','unable to','report','feedback',
                          'issue','problem','submit feedback','report an issue',
                          'give feedback','accessibility','skip','preview'];
            const btns = Array.from(document.querySelectorAll(
                'button, input[type=submit], [role=button]'
            ));
            for (const b of btns) {
                if (!b.offsetParent) continue;
                if (b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
                const t = (b.innerText || b.value || b.textContent || '').toLowerCase().trim();
                if (!t || BACK.some(w => t.includes(w))) continue;
                if (kws.some(k => t.includes(k))) {
                    b.scrollIntoView({block:'center'});
                    b.click();
                    return t;
                }
            }
            return null;
        }
    """, None)
    if result:
        if verbose: print(f"          ✔  JS clicked: '{result}'")
        return result
    if verbose: print(f"          ⚠  No clickable nav button found")
    return "none"


def _is_recaptcha_frame(frame) -> bool:
    """
    True if this frame belongs to Google's reCAPTCHA widget (the checkbox
    'anchor' iframe or the image-challenge 'bframe' iframe).

    Added 2026-07-09: confirmed via Raghav's live report that the pipeline
    appeared to be "answering the CAPTCHA wrongly" itself. Root cause found —
    _click_any_forward_button() and _click_submit_btn()'s fallback both used
    to iterate EVERY frame on the page with no awareness that one of them
    could be the reCAPTCHA challenge itself, running a generic "click any
    button" JS query inside whatever frame it landed on. reCAPTCHA's own
    image tiles and its Verify/reload controls are real <button>/[role=button]
    elements, so a generic click scan running inside that frame could select
    a wrong tile or submit the challenge prematurely — exactly the kind of
    interaction Google's own bot-detection is built to catch, and a very
    plausible contributor to the repeated session blocks investigated
    earlier. Every generic "click any button in any frame" fallback now
    skips frames that match this check entirely — the pipeline should never
    interact with CAPTCHA content itself, only detect/wait/pin/resubmit
    around it.
    """
    try:
        url = (frame.url or "").lower()
    except Exception:
        return False
    return "recaptcha" in url or "gstatic.com/recaptcha" in url


def _click_any_forward_button(page, verbose=True):
    """
    Last-resort: click ANY visible enabled non-back button across all frames.
    Excludes: back, previous, cancel, close, exit, discard, skip (accessibility links).
    Based on meteor314/indeed_bot pattern.
    """
    BACK = ['back', 'previous', 'cancel', 'close', 'exit', 'discard',
            'skip', 'skip to', 'accessibility', 'sign in', 'log in',
            'apply on company site', 'apply on employer', 'apply externally',
            'continue to company', 'apply on the company', 'apply on company',
            'external application', 'leaving indeed', "you're leaving",
            'new update', 'updates']
    for frame in list(page.frames):
        if _is_recaptcha_frame(frame):
            continue
        try:
            result = frame.evaluate("""
                (backWords) => {
                    const btns = Array.from(document.querySelectorAll(
                        'button, [role=button], input[type=submit]'
                    ));
                    for (const b of btns) {
                        if (!b.offsetParent) continue;
                        if (b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
                        const t = (b.innerText||b.textContent||b.value||'').toLowerCase().trim();
                        if (!t || t.length > 50) continue;
                        // Use includes for multi-word phrases, startsWith for single words
                        // This catches '1\nnew update' even though it doesn't start with 'new update'
                        if (backWords.some(w => t.includes(w) || t === w)) continue;
                        // Also skip pure-numeric or very short button text (notification badges like '1', '2')
                        if (/^[0-9]+$/.test(t) || t.length <= 1) continue;
                        b.scrollIntoView({block:'center'});
                        b.click();
                        return t;
                    }
                    return null;
                }
            """, BACK)
            if result:
                if verbose: print(f"          ↩  Any-forward fallback clicked: '{result}'")
                return result
        except:
            pass
    return None


def _force_click_continue_on_resume_page(page, verbose=True):
    """
    On resume-m pages, Continue is aria-disabled=true until resume card is selected.
    Strategy:
      1. Click the resume card (select it)
      2. Force-click Continue removing aria-disabled
    """
    for frame in list(page.frames):
        frame_url = ""
        try: frame_url = frame.url or ""
        except: pass
        if "resume-m" not in frame_url:
            continue
        result = _safe_eval(frame, """
            () => {
                // Step 1: try to select the resume card (best-effort — don't abort if not found)
                const cardSelectors = [
                    '[data-testid="FileResumeCardHeader-title"]',
                    '[data-testid="resume-card"]',
                    '[class*="ResumeCard"]', '[class*="resumeCard"]',
                    '[class*="FileCard"]', '[class*="resume-card"]',
                    'li[class*="resume"]', 'div[class*="resume"]',
                    '[class*="fileResume"]', '[class*="uploadedResume"]',
                    '[role="listitem"]', '[role="option"]',
                    'label[class*="resume"]', 'li', 'article'
                ];
                let clicked_card = false;
                for (const sel of cardSelectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        if (!el.offsetParent) continue;
                        const txt = (el.innerText || el.textContent || '').toLowerCase();
                        // Only click if it looks like a resume entry (has .docx/.pdf or resume-ish text)
                        const looksLikeResume = txt.includes('.docx') || txt.includes('.pdf')
                            || txt.includes('resume') || txt.includes('curriculum');
                        if (looksLikeResume) {
                            el.scrollIntoView({block:'center'});
                            el.click();
                            clicked_card = true;
                            break;
                        }
                    }
                    if (clicked_card) break;
                }

                // Step 2: ALWAYS find the bottom Continue/Submit button and force-click it.
                // Indeed's smartapply ALWAYS has a forward button — even if aria-disabled.
                // Scan ALL buttons including aria-disabled ones.
                const kws = ['continue','next','use this resume','use resume',
                             'save and continue','save & continue','submit','apply'];
                const SKIP = ['back','previous','cancel','close','exit','discard',
                              'skip','accessibility','sign in','log in',
                              'unable to continue','unable to proceed',
                              'submit feedback','report an issue','report issue',
                              'give feedback','report a problem'];
                const allBtns = Array.from(document.querySelectorAll('button,[role=button],input[type=submit]'));
                // Sort: visible+enabled first, then aria-disabled, so we prefer the real one
                allBtns.sort((a,b) => {
                    const aD = a.getAttribute('aria-disabled') === 'true' ? 1 : 0;
                    const bD = b.getAttribute('aria-disabled') === 'true' ? 1 : 0;
                    return aD - bD;
                });
                for (const b of allBtns) {
                    const t = (b.innerText||b.textContent||b.value||'').toLowerCase().trim();
                    if (!t || t.length > 60) continue;
                    if (SKIP.some(w => t.includes(w))) continue;
                    if (!kws.some(k => t.includes(k))) continue;
                    // Force-enable and click
                    b.removeAttribute('aria-disabled');
                    b.removeAttribute('disabled');
                    b.scrollIntoView({block:'center'});
                    b.click();
                    return 'card=' + clicked_card + ' btn=' + t;
                }

                // Step 3: absolute last resort — iterate ALL visible buttons bottom-up
                // Indeed always has a forward button; find the first one that isn't a back/junk button.
                const visibleBtns = Array.from(document.querySelectorAll(
                    'button,[role=button],input[type=submit]'
                )).filter(b => b.offsetParent).reverse(); // bottom-up
                const skipLast = ['back','previous','cancel','close','sign in','log in',
                                  'unable to continue','unable to proceed',
                                  'submit feedback','report an issue','report issue',
                                  'give feedback','report a problem','accessibility'];
                for (const btn of visibleBtns) {
                    const t = (btn.innerText||btn.textContent||'').toLowerCase().trim();
                    if (!t || t.length > 80) continue;
                    if (skipLast.some(w => t.includes(w))) continue;
                    btn.removeAttribute('aria-disabled');
                    btn.removeAttribute('disabled');
                    btn.scrollIntoView({block:'center'});
                    btn.click();
                    return 'card=' + clicked_card + ' last-btn=' + t;
                }
                return null;
            }
        """, None)
        if result:
            if verbose: print(f"          📄 resume-m force-click: {result}")
            return True
        if verbose: print(f"          ⚠  resume-m: no button found even in last-resort scan")
    return False

def _upload_resume(page, resume_path, done_flag, cover_letter_path="", cover_done_flag=None):
    """Upload resume (and optional cover letter) — checks all frames."""
    if done_flag[0]:
        # Resume already uploaded — but check for a cover letter file input on this step
        if cover_letter_path and cover_done_flag and not cover_done_flag[0]:
            _upload_cover_letter(page, cover_letter_path, cover_done_flag)
        return

    all_frames = [page.main_frame] + list(page.frames)
    file_inputs_found = 0
    for i, frame in enumerate(all_frames):
        try:
            fi_all = frame.locator('input[type="file"]')
            count = fi_all.count()
            if count == 0:
                continue
            frame_url = ""
            try: frame_url = frame.url or ""
            except: pass

            if not done_flag[0]:
                fi = fi_all.first
                print(f"          📎 File input found in frame[{i}] ({frame_url[:50] or 'main'}) — uploading resume...")
                for _upload_attempt in range(5):
                    # Human-like pre-upload pause — avoids instant robotic file injection
                    time.sleep(random.uniform(1.5, 3.0))
                    fi.set_input_files(str(resume_path))
                    # Give Indeed's reCAPTCHA/upload validator time to respond (needs 4-6s)
                    time.sleep(random.uniform(4.0, 6.0))
                    # Check for Indeed's "couldn't upload" error and retry with escalating delay
                    try:
                        page_txt = page.evaluate("() => document.body.innerText") or ""
                        if "couldn't upload" in page_txt.lower() or "could not upload" in page_txt.lower():
                            wait_t = 12 + _upload_attempt * 10  # 12s, 22s, 32s, 42s, 52s
                            print(f"          ⚠  Upload rejected by Indeed (attempt {_upload_attempt+1}/5) — retrying in {wait_t}s...")
                            time.sleep(wait_t)
                            continue
                    except Exception:
                        pass
                    break  # no error detected — upload accepted
                done_flag[0] = True
                print(f"          📎 Resume uploaded ✅  ({Path(resume_path).name})")
                file_inputs_found += 1

            # If there's a SECOND file input, try uploading cover letter there
            if cover_letter_path and cover_done_flag and not cover_done_flag[0] and count >= 2:
                try:
                    fi2 = fi_all.nth(1)
                    print(f"          📎 Second file input found — uploading cover letter...")
                    fi2.set_input_files(str(cover_letter_path))
                    time.sleep(1)
                    cover_done_flag[0] = True
                    print(f"          📎 Cover letter uploaded ✅  ({Path(cover_letter_path).name})")
                except Exception:
                    pass
        except Exception:
            pass

    if not done_flag[0]:
        print(f"          ℹ  No file input found (resume upload skipped this step)")

    # Final check for cover letter file input on this step
    if cover_letter_path and cover_done_flag and not cover_done_flag[0]:
        _upload_cover_letter(page, cover_letter_path, cover_done_flag)


def _upload_cover_letter(page, cover_letter_path, done_flag):
    """Try to find and upload cover letter to any additional file input."""
    if done_flag[0] or not cover_letter_path:
        return
    all_frames = [page.main_frame] + list(page.frames)
    for frame in all_frames:
        try:
            # Look for file inputs labeled for cover letter
            cl_input = frame.evaluate("""
                () => {
                    const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
                    for (const inp of inputs) {
                        const label = document.querySelector('label[for="' + inp.id + '"]');
                        const lbl_text = (label ? label.innerText : '').toLowerCase();
                        const aria = (inp.getAttribute('aria-label') || '').toLowerCase();
                        if (lbl_text.includes('cover') || aria.includes('cover')) return inp.id || true;
                    }
                    return null;
                }
            """)
            if cl_input:
                fi = frame.locator('input[type="file"]').last
                fi.set_input_files(str(cover_letter_path))
                time.sleep(1)
                done_flag[0] = True
                print(f"          📎 Cover letter file uploaded ✅")
                return
        except Exception:
            pass

def _get_apply_page(page, browser, timeout_secs=10):
    """
    After clicking Apply, Indeed either opens a NEW TAB (modern flow)
    or stays on the same page. Detect which and return the right Page object.
    """
    pages_before = set(id(p) for p in browser.pages)
    for _ in range(timeout_secs * 2):
        time.sleep(0.5)
        for p in browser.pages:
            if id(p) not in pages_before:
                try:
                    p.wait_for_load_state("domcontentloaded", timeout=5000)
                    url = p.url or ""
                    if url and "about:blank" not in url:
                        return p, True   # new tab found
                except:
                    pass
    return page, False   # no new tab — use current page


def apply_to_job(page, browser, job, resume_path, cover_letter_path, profile_text="", dry_run=False):
    """
    Click Indeed Apply → detect where form opened (new tab or current page)
    → walk multi-step form using Claude + cache → submit.
    Returns (success: bool, reason: str).
    """
    title       = job.get("title", "")
    company     = job.get("company", "")
    job_url     = job.get("url", "") or job.get("job_url", "") or ""
    jd_for_fill = ""   # safe default — overwritten below
    jd_for_fill = job.get("description", "") or job.get("jd_text", "") or ""

    # ── Click the Apply button ─────────────────────────────────────────────────
    print(f"          👆 Clicking Indeed Apply button...")
    clicked = click_apply_button(page)
    if not clicked:
        print(f"          ❌ No Apply button found on page")
        return False, "no apply button found"
    print(f"          ✔  Apply button clicked — waiting for form to open...")

    # ── Detect where form opened ───────────────────────────────────────────────
    apply_page, opened_new_tab = _get_apply_page(page, browser, timeout_secs=8)
    time.sleep(2)

    # Check if redirected to external company site — skip
    url = apply_page.url or ""
    if url and "indeed.com" not in url and "indeedapply" not in url and "smartapply" not in url:
        print(f"          ⏭  External site detected: {url[:70]} — skipping")
        if opened_new_tab:
            try: apply_page.close()
            except: pass
        else:
            try: page.go_back()
            except: pass
        return False, f"external site: {url[:60]}"

    print(f"          🌐 Form on: {'new tab' if opened_new_tab else 'same page'}  url={apply_page.url[:70]}")

    # ── Wait for smartapply.indeed.com frame to load (avoid search-bar fill) ──
    if not opened_new_tab:
        print(f"          ⏳ Waiting for Indeed Apply form (smartapply) to load...")
        found = _wait_for_smartapply_frame(apply_page, timeout_secs=12)
        if found:
            print(f"          ✔  smartapply frame ready")
        else:
            # No smartapply loaded → likely external apply or "leaving Indeed" dialog
            # Check for external signals on the page
            page_text = _safe_eval(apply_page, "() => document.body.innerText.toLowerCase()", "") or ""
            external_signals = [
                "apply on company site", "continue to company", "you're leaving indeed",
                "apply externally", "apply on employer", "leaving indeed",
                "apply on the company", "external application"
            ]
            if any(sig in page_text for sig in external_signals):
                print(f"          ⏭  External apply dialog detected — skipping this job")
                return False, "external apply (dialog detected)"
            # Still on viewjob with only search bar inputs = Apply button didn't open a form
            still_search_bar = all(
                "indeed.com/viewjob" in (f.url or "") or not (f.url or "").strip()
                for f in apply_page.frames
                if "smartapply" not in (f.url or "")
                   and "recaptcha" not in (f.url or "")
                   and "about:blank" not in (f.url or "")
                   and (f.url or "").strip()
            )
            if still_search_bar:
                print(f"          ⏭  No apply form opened — this job uses external apply, skipping")
                return False, "external apply (no form loaded)"
            print(f"          ⚠  smartapply frame not detected — continuing anyway")
        time.sleep(1)   # brief extra settle

    # Resume filename for resume-selection page auto-click
    resume_name = Path(resume_path).name if resume_path else ""

    # ── Read cover letter text from .docx for pasting into form fields ────────
    cl_text_for_form = ""
    if cover_letter_path:
        try:
            from docx import Document as _DocxDoc
            _cl_doc = _DocxDoc(cover_letter_path)
            cl_text_for_form = "\n\n".join(
                p.text for p in _cl_doc.paragraphs if p.text.strip()
            )
        except Exception:
            pass

    # ── Walk multi-step form ───────────────────────────────────────────────────
    step           = 0
    max_steps      = 12   # was 20 — 12 steps × ~4s = ~50s max per form, fail fast
    last_btn       = None
    same_btn_count = 0
    submitted      = False
    resume_done    = [False]
    cover_done     = [False]
    last_url       = ""
    same_url_count = 0
    _form_start    = time.time()
    _form_timeout  = 90   # hard 90-second cap per application form

    try:
        while step < max_steps and not submitted:
            if time.time() - _form_start > _form_timeout:
                print(f"          ⏱  Form timeout ({_form_timeout}s) — moving on")
                break
            step += 1
            time.sleep(2)

            # Human-like random pause between steps (reduces CAPTCHA risk)
            time.sleep(random.uniform(1.5, 3.0))

            print(f"\n          {'─'*50}")
            print(f"          ═══ STEP {step} ═══  url={apply_page.url[:70]}")
            print(f"          {'─'*50}")

            # ── Same-URL loop detection ────────────────────────────────────────
            current_step_url = apply_page.url or ""
            # Normalize: strip query params for comparison
            current_step_url_norm = current_step_url.split("?")[0].rstrip("/")
            last_url_norm = last_url.split("?")[0].rstrip("/")
            if current_step_url_norm and current_step_url_norm == last_url_norm:
                same_url_count += 1
                print(f"          ⚠  Same URL repeated {same_url_count}x — possible stuck loop")

                # ── Vision assist: let Claude SEE the page and decide what to do ──
                if same_url_count == 2:
                    print(f"          👁  Calling Claude Vision to inspect stuck page...")
                    try:
                        import claude_engine as _ce
                        _ss_bytes = apply_page.screenshot()
                        _page_txt = apply_page.evaluate("() => document.body.innerText")[:3000]
                        _vision   = _ce.vision_assist(_ss_bytes, _page_txt, title, company)
                        _action   = _vision.get("action", "click_button")

                        if _action == "captcha":
                            print(f"          👁  Vision sees CAPTCHA — handing off to CAPTCHA handler")
                            _check_and_handle_captcha(apply_page, title, company, job_url=job_url)

                        elif _action == "fill_field":
                            print(f"          👁  Vision filling {len(_vision.get('fields', []))} field(s)...")
                            for _fld in _vision.get("fields", []):
                                _lbl = _fld.get("label", "")
                                _val = _fld.get("value", "")
                                if not _lbl or not _val:
                                    continue
                                try:
                                    _filled = False

                                    # ── Radio / checkbox — MUST use click(), not fill() ──────
                                    # .fill() silently no-ops on radio inputs. The DOM value
                                    # never changes, the form stays blocked, and Vision enters
                                    # an infinite retry loop. Use JS click on the matching option.
                                    try:
                                        import json as _json_vf
                                        _radio_clicked = apply_page.evaluate(f"""
                                            () => {{
                                                const lbl = {_json_vf.dumps(_lbl)}.toLowerCase();
                                                const val = {_json_vf.dumps(_val)}.toLowerCase().trim();
                                                const inputs = Array.from(document.querySelectorAll(
                                                    'input[type=radio], input[type=checkbox]'
                                                ));
                                                for (const inp of inputs) {{
                                                    const labelEl = document.querySelector('label[for="' + inp.id + '"]');
                                                    const groupEl = inp.closest('fieldset, [role="group"], div[class*="question"], div[class*="field"]');
                                                    const groupTxt = (groupEl ? groupEl.innerText : '').toLowerCase();
                                                    const optTxt = (labelEl ? labelEl.innerText : (inp.value || '')).toLowerCase().trim();
                                                    const groupMatch = groupTxt.includes(lbl) || lbl.includes(groupTxt.slice(0,30));
                                                    const valMatch = optTxt === val || optTxt.startsWith(val) || val.startsWith(optTxt.slice(0,6));
                                                    if (groupMatch && valMatch && !inp.checked) {{
                                                        inp.click();
                                                        ['input','change'].forEach(ev => inp.dispatchEvent(new Event(ev, {{bubbles:true}})));
                                                        return optTxt;
                                                    }}
                                                }}
                                                return null;
                                            }}
                                        """)
                                        if _radio_clicked:
                                            _filled = True
                                            print(f"             ✔ Vision radio '{_lbl[:40]}' → clicked '{_radio_clicked}'")
                                    except Exception:
                                        pass

                                    # ── Text / select — use Playwright locators ───────────────
                                    if not _filled:
                                        try:
                                            _loc = apply_page.get_by_placeholder(_lbl, exact=False).first
                                            if _loc.count() > 0:
                                                _loc.fill(_val, timeout=5000)
                                                _filled = True
                                        except Exception:
                                            pass
                                    if not _filled:
                                        try:
                                            _loc = apply_page.get_by_label(_lbl, exact=False).first
                                            if _loc.count() > 0:
                                                _tag = ""
                                                try:
                                                    _tag = _loc.evaluate("el => el.type || ''")
                                                except Exception:
                                                    pass
                                                if _tag in ("radio", "checkbox"):
                                                    if _val.lower() in ("yes", "true", "1"):
                                                        _loc.check(timeout=5000)
                                                    else:
                                                        _loc.uncheck(timeout=5000)
                                                else:
                                                    _loc.fill(_val, timeout=5000)
                                                _filled = True
                                        except Exception:
                                            pass
                                    if not _filled:
                                        import re as _re2
                                        _safe = _re2.sub(r"[\"'\\]", "", _lbl)[:60]
                                        if _safe:
                                            try:
                                                _el = apply_page.query_selector(
                                                    f"[placeholder*='{_safe}' i],[aria-label*='{_safe}' i]"
                                                )
                                                if _el:
                                                    _el.fill(_val)
                                                    _filled = True
                                            except Exception:
                                                pass
                                    if _filled:
                                        print(f"             ✔ Vision filled '{_lbl[:50]}' = '{_val}'")
                                    else:
                                        print(f"             · Vision could not find field '{_lbl[:50]}'")
                                except Exception as _fe:
                                    print(f"             ⚠ Vision fill error: {_fe}")
                            # Click the button Vision recommended
                            _btn_text = _vision.get("button", "Continue")
                            if _btn_text:
                                _click_any_forward_button(apply_page)
                                print(f"          👁  Vision: clicked forward button after filling")

                        elif _action == "click_button":
                            _btn_text = _vision.get("button", "Continue")
                            print(f"          👁  Vision says click: '{_btn_text}'")
                            _click_any_forward_button(apply_page)

                        elif _action == "skip":
                            print(f"          👁  Vision sees confirmation — marking as submitted")
                            submitted = True
                            break

                    except Exception as _ve:
                        print(f"          👁  Vision assist error: {_ve}")

                if same_url_count >= 4:
                    print(f"          ❌ Stuck on same URL for {same_url_count} steps — giving up on this job")
                    # ── Save stuck questions for manual review ────────────────
                    try:
                        import json as _json_stuck
                        _stuck_file = cfg.BASE_DIR / "data" / "stuck_questions.json"
                        _stuck_file.parent.mkdir(parents=True, exist_ok=True)
                        _existing_stuck = []
                        if _stuck_file.exists():
                            try:
                                _existing_stuck = _json_stuck.loads(_stuck_file.read_text())
                            except Exception:
                                _existing_stuck = []
                        # Scrape current page fields as the stuck questions
                        _page_txt = ""
                        try:
                            _page_txt = apply_page.evaluate("() => document.body.innerText")
                        except Exception:
                            pass
                        _stuck_entry = {
                            "timestamp":  datetime.now().isoformat(),
                            "company":    company,
                            "job_title":  title,
                            "url":        apply_page.url,
                            # 1000 chars wasn't enough — the 2026-07-08 demographic-questions-
                            # module capture cut off right before the actual required-field
                            # asterisks and options, making the stuck entry undiagnosable.
                            "page_text_snippet": _page_txt[:4000],
                            "fields":     [
                                {"label": f.get("label",""), "type": f.get("type",""), "options": f.get("options",[])}
                                for f in _last_seen_fields
                            ],
                            "status": "stuck — needs manual review"
                        }
                        _existing_stuck.append(_stuck_entry)
                        _stuck_file.write_text(_json_stuck.dumps(_existing_stuck, indent=2))
                        print(f"          📝 Stuck questions saved → data/stuck_questions.json")
                        print(f"          💡 Open that file, add answers to qa_answers.py to fix this next time")
                    except Exception as _se:
                        print(f"          ⚠  Could not save stuck questions: {_se}")
                    break
            else:
                same_url_count = 0
                same_btn_count = 0   # URL changed = new page = reset button repeat counter
                last_url = current_step_url

            # ── CAPTCHA check at start of every step ──────────────────────────
            captcha_ok = _check_and_handle_captcha(apply_page, title, company, job_url=job_url)
            if not captcha_ok:
                return False, "captcha-timeout"  # caller queues for retry

            if _is_confirmed(apply_page):
                print(f"          🎉 Confirmation text detected — application submitted!")
                submitted = True
                break

            # ── AI interview prompt (post-submission) ──────────────────────
            if _handle_ai_interview(apply_page, title, company, job_url=job_url):
                print(f"          ✅ AI interview handled — counting as submitted")
                submitted = True
                break

            # ── Detect review/submit page by URL ──────────────────────────────
            step_url = apply_page.url or ""
            is_review_page = "review" in step_url or "confirm" in step_url
            if is_review_page:
                print(f"          📝 Review/submit page detected (url={step_url[:60]})")

                # Step A: Fill any remaining fields on this page
                # (some review pages have EEOC questions, agreements, or checkboxes)
                form_ctx = _find_active_form_ctx(apply_page, verbose=True)
                filled = smart_fill_step(form_ctx, profile_text, title, company, resume_name,
                                         cover_letter_text=cl_text_for_form, jd_text=jd_for_fill)
                if filled:
                    print(f"          ✏️  Review page: filled {filled} field(s) before submitting")
                else:
                    print(f"          ℹ️  Review page: no fillable fields (read-only review)")

                if dry_run:
                    print(f"          🏁 DRY RUN: Would now click Submit — stopping here")
                    return True, "dry-run: reached review/submit page"

                # ── Submit retry loop — handles multiple CAPTCHAs ────────────
                # Indeed can show CAPTCHA on every Submit attempt.
                # Loop: click Submit → check CAPTCHA → solve → click Submit → repeat
                def _click_submit_btn(pg):
                    """Click submit button across all frames, force-enabling if needed."""
                    nav_btns, nav_frame = _get_nav_buttons(pg)
                    if nav_btns:
                        lbl = _click_nav(nav_frame, nav_btns[0].get("text","submit"))
                        print(f"          ✔  Clicked nav: '{lbl}'")
                        return True
                    # Real-mouse-click attempt first — added 2026-07-08. A raw JS
                    # element.click() dispatches an untrusted event; confirmed via
                    # data/stuck_submits.json that Indeed's Submit button (behind
                    # reCAPTCHA) can silently ignore those — the DOM shows a click
                    # happened but the real submit handler never fires, so all 8
                    # retries land on the exact same review page. Playwright's
                    # bounding-box + page.mouse.click() is a trusted browser-level
                    # click, same fix as in _click_nav().
                    for frame in pg.frames:
                        if _is_recaptcha_frame(frame):
                            continue
                        try:
                            for label in ["Submit your application", "Submit application",
                                          "Submit", "Apply now", "Send application"]:
                                btn = frame.locator(f"button:has-text('{label}')").first
                                if btn.count() > 0 and btn.is_visible(timeout=1000):
                                    box = btn.bounding_box()
                                    if box:
                                        btn.scroll_into_view_if_needed()
                                        cx = box["x"] + box["width"] / 2
                                        cy = box["y"] + box["height"] / 2
                                        frame.page.mouse.click(cx, cy)
                                        print(f"          ✔  Real mouse-click Submit: '{label}'")
                                        return True
                        except Exception:
                            pass

                    for frame in pg.frames:
                        if _is_recaptcha_frame(frame):
                            continue
                        js_clicked = _safe_eval(frame, """
                            () => {
                                const kws = ['submit your application','submit application',
                                             'submit','apply now','apply','send application'];
                                const SKIP = ['submit feedback','report','feedback','accessibility'];
                                const btns = Array.from(document.querySelectorAll(
                                    'button,[role=button],input[type=submit]'
                                ));
                                for (const b of btns) {
                                    if (!b.offsetParent) continue;
                                    const t = (b.innerText||b.textContent||b.value||'').toLowerCase().trim();
                                    if (SKIP.some(s=>t.includes(s))) continue;
                                    if (kws.some(k=>t.includes(k))) {
                                        b.removeAttribute('aria-disabled');
                                        b.removeAttribute('disabled');
                                        b.scrollIntoView({block:'center'});
                                        b.click();
                                        return t;
                                    }
                                }
                                return null;
                            }
                        """, None)
                        if js_clicked:
                            print(f"          ✔  JS Submit: '{js_clicked}'")
                            return True
                    print(f"          ⚠  No submit button found")
                    return False

                MAX_SUBMIT_ATTEMPTS = 8
                def _save_submit_diag(note):
                    try:
                        import json as _json_stuck_submit
                        _ss_file = cfg.BASE_DIR / "data" / "stuck_submits.json"
                        _ss_file.parent.mkdir(parents=True, exist_ok=True)
                        _ss_existing = []
                        if _ss_file.exists():
                            try:
                                _ss_existing = _json_stuck_submit.loads(_ss_file.read_text())
                            except Exception:
                                _ss_existing = []
                        _ss_page_txt = ""
                        try:
                            _ss_page_txt = apply_page.evaluate("() => document.body.innerText")
                        except Exception:
                            pass
                        _ss_existing.append({
                            "timestamp": datetime.now().isoformat(),
                            "company": company,
                            "job_title": title,
                            "url": apply_page.url,
                            "page_text_snippet": _ss_page_txt[:4000],
                            "note": note,
                        })
                        _ss_file.write_text(_json_stuck_submit.dumps(_ss_existing, indent=2))
                        print(f"          📝 Submit give-up diagnostics saved → data/stuck_submits.json")
                    except Exception as _sse:
                        print(f"          ⚠  Could not save submit diagnostics: {_sse}")

                _submit_nav_clicks = 0  # track how many non-submit nav clicks we've made
                _prev_page_sig = None
                _same_sig_count = 0
                for attempt in range(1, MAX_SUBMIT_ATTEMPTS + 1):
                    print(f"          🚀 LIVE: Submit attempt {attempt}/{MAX_SUBMIT_ATTEMPTS}...")

                    # ── Check if form drifted to a non-review page ────────────
                    # After "apply anyway" the form may show extra pages (profile,
                    # confirmation of intent) before the actual submit button appears.
                    # Navigate through "continue" / "save and continue" buttons
                    # but cap at 4 such navigations to avoid infinite loop.
                    _nav_btns, _nav_frame = _get_nav_buttons(apply_page)
                    _first_btn_text = (_nav_btns[0].get("text","") if _nav_btns else "").lower()
                    _SUBMIT_WORDS = {"submit", "apply now", "send application"}
                    _is_submit_btn = any(w in _first_btn_text for w in _SUBMIT_WORDS)

                    if _nav_btns and not _is_submit_btn and _submit_nav_clicks < 6:
                        # Still on a form navigation step — click through it
                        _lbl = _click_nav(_nav_frame, _nav_btns[0].get("text", "continue"))
                        print(f"          ↪  Nav-through (submit pending): '{_lbl}'")
                        _submit_nav_clicks += 1
                        time.sleep(4)
                        # Check if we arrived at confirmation after nav-through
                        if _is_confirmed(apply_page):
                            print(f"          🎉 Application submitted and confirmed!")
                            submitted = True
                            break
                        continue  # try next attempt

                    _click_submit_btn(apply_page)
                    time.sleep(3)  # wait for CAPTCHA or confirmation to appear

                    # Check for CAPTCHA that appeared after clicking Submit
                    captcha_ok = _check_and_handle_captcha(apply_page, title, company, job_url=job_url)
                    if not captcha_ok:
                        print(f"          ❌ CAPTCHA timed out — queuing for retry")
                        return False, "captcha-timeout"  # caller queues for retry

                    # Check for confirmation or AI interview
                    time.sleep(3)
                    if _is_confirmed(apply_page):
                        print(f"          🎉 Application submitted and confirmed!")
                        submitted = True
                        break
                    if _handle_ai_interview(apply_page, title, company, job_url=job_url):
                        print(f"          ✅ AI interview detected post-submit — counting as submitted")
                        submitted = True
                        break

                    current_url = apply_page.url or ""
                    if "review" not in current_url and "smartapply" not in current_url:
                        # One more AI interview check after navigation
                        if _handle_ai_interview(apply_page, title, company, job_url=job_url):
                            print(f"          ✅ AI interview page — counting as submitted")
                            submitted = True
                        else:
                            print(f"          🎉 Navigated away from review — submitted!")
                            submitted = True
                        break

                    # ── Early-exit on a truly unresponsive button ──────────────
                    # Added 2026-07-08: confirmed via data/stuck_submits.json that a
                    # real job (Computer Enterprises Inc) got the EXACT same page
                    # (same URL, same body text) across all 8 attempts — the click
                    # was landing on the DOM but not triggering Indeed's real submit
                    # handler (likely the untrusted-event / reCAPTCHA gate the mouse-
                    # click fix above targets). Grinding 8 rapid clicks at a button
                    # that isn't working wastes ~90s per job AND is very plausibly
                    # what got the whole session flagged: every Indeed search
                    # immediately after that exact job failed with net::ERR_ABORTED
                    # for the rest of the run. If the page is byte-identical for 2
                    # consecutive attempts (3 total), stop now instead of burning
                    # through all 8 — same diagnostics get saved either way.
                    try:
                        _cur_page_sig = (apply_page.url or "") + "|" + (
                            apply_page.evaluate("() => document.body.innerText") or ""
                        )[:2000]
                    except Exception:
                        _cur_page_sig = None
                    if _cur_page_sig is not None and _cur_page_sig == _prev_page_sig:
                        _same_sig_count += 1
                    else:
                        _same_sig_count = 0
                    _prev_page_sig = _cur_page_sig

                    if _same_sig_count >= 2:
                        # Added 2026-07-08: checked a real stuck job's frame scan
                        # right before this exact point fired — no bframe challenge
                        # iframe existed yet, only the unclicked reCAPTCHA checkbox.
                        # Google's risk engine appears to escalate to a visible
                        # image challenge only AFTER a few rapid Submit clicks (the
                        # same ones this early-exit is designed to cut off), and it
                        # can take a few extra seconds to actually render. Without
                        # this, "page looks unchanged" was being read as "click
                        # isn't landing" and this loop would stop watching the page
                        # right as a real CAPTCHA was rendering — leaving Raghav
                        # looking at a live, unsolved challenge with no alert email,
                        # no notification, and no resize, because the code had
                        # already moved on to the next job by the time it appeared.
                        # One more explicit check with extra wait before giving up.
                        # 2026-07-09: a single 5s-then-check (previous version)
                        # still wasn't enough — confirmed live that a real
                        # CAPTCHA rendered even later than that, after this job
                        # had already been abandoned and the code had moved on to
                        # scoring/searching other jobs. The tab sat open with an
                        # unsolved challenge nobody was watching: no email, no
                        # pin, because _check_and_handle_captcha() was never
                        # called again for that specific page. Widened this to a
                        # real repeated-check window (every 3s for up to 24s)
                        # instead of one shot, since evidence shows the challenge
                        # can take longer than 5s to actually appear after the
                        # clicks that triggered it.
                        print(f"          🔎 Page unchanged — watching for a delayed CAPTCHA "
                              f"(up to 24s) before giving up...")
                        _late_captcha_ok = True
                        for _watch_i in range(8):
                            time.sleep(3)
                            _late_captcha_ok = _check_and_handle_captcha(apply_page, title, company, job_url=job_url)
                            if not _late_captcha_ok:
                                print(f"          ❌ CAPTCHA timed out — queuing for retry")
                                return False, "captcha-timeout"
                            if _is_confirmed(apply_page):
                                print(f"          🎉 Application submitted and confirmed (after CAPTCHA solve)!")
                                submitted = True
                                break
                            # If a CAPTCHA WAS visible and got solved inside
                            # _check_and_handle_captcha, its own wait loop already
                            # consumed real time and either returned True (solved)
                            # or already returned False above (timeout). Only
                            # keep polling here if nothing has shown up yet.
                        if submitted:
                            break

                        print(f"          ❌ Page unchanged across {_same_sig_count + 1} attempts — "
                              f"button click isn't landing, stopping early (was attempt {attempt}/{MAX_SUBMIT_ATTEMPTS})")
                        submitted = False
                        _save_submit_diag(
                            "page byte-identical across multiple submit attempts — the click "
                            "is landing on the DOM but not triggering Indeed's real submit "
                            "handler (likely rejected as an untrusted event / reCAPTCHA gate). "
                            "Stopped early instead of grinding through all attempts."
                        )
                        break
                    elif attempt < MAX_SUBMIT_ATTEMPTS:
                        print(f"          🔄 No confirmation yet — waiting 8s then retrying Submit...")
                        time.sleep(8)  # longer wait between attempts reduces CAPTCHA triggers
                        continue
                    else:
                        print(f"          ❌ Gave up after {MAX_SUBMIT_ATTEMPTS} submit attempts")
                        submitted = False
                        # Added 2026-07-08: previously this branch captured NO diagnostics —
                        # when the submit button disappears (possible real success) but
                        # neither _is_confirmed() nor the URL-drift check catches it, we had
                        # zero data to tell "actually failed" apart from "actually succeeded,
                        # confirmation page just used unrecognized wording". Save page state
                        # so this is debuggable from data/ instead of a black box.
                        _save_submit_diag(
                            "submit attempts exhausted — button may have vanished "
                            "(possible real success with unrecognized confirmation "
                            "wording) or a real dead-end. Check page_text_snippet."
                        )

                break

            # ── resume-m page: force-click resume card + remove aria-disabled ──
            # Indeed disables Continue via aria-disabled until resume card is clicked.
            # _force_click_continue_on_resume_page handles both in one shot.
            resume_m_active = any(
                "resume-m" in (f.url or "") and "resume-s" not in (f.url or "")
                for f in list(apply_page.frames)
            )
            if resume_m_active:
                print(f"          📄 resume-m page detected — force-clicking resume card + Continue")
                force_ok = _force_click_continue_on_resume_page(apply_page, verbose=True)
                if force_ok:
                    time.sleep(2)
                    continue  # Skip normal nav click — already advanced
                else:
                    print(f"          ⚠  resume-m force-click found no card/button — trying normal nav")

            # Upload resume (and cover letter if there's a second file input)
            _upload_resume(apply_page, resume_path, resume_done,
                           cover_letter_path=cover_letter_path or "",
                           cover_done_flag=cover_done)

            # Find which frame has the active form this step
            form_ctx = _find_active_form_ctx(apply_page)

            # DRY RUN — fill and walk but stop before submit
            if dry_run:
                nav, nav_frame = _get_nav_buttons(apply_page)
                has_submit = any(
                    any(kw in b.get("text","") for kw in ("submit","apply","send"))
                    for b in nav
                )
                if has_submit:
                    print(f"          🏁 DRY RUN: Submit button found — stopping here (would submit in live mode)")
                    return True, "dry-run: reached submit page"
                filled = smart_fill_step(form_ctx, profile_text, title, company, resume_name,
                                         cover_letter_text=cl_text_for_form, jd_text=jd_for_fill)
                print(f"          ✏️  Step {step} TOTAL: filled {filled} fields")
                if nav:
                    _click_nav(nav_frame, nav[0].get("text","continue"))
                else:
                    # No named nav button — try clicking ANY forward button (meteor314 pattern)
                    fallback = _click_any_forward_button(apply_page)
                    if not fallback:
                        print(f"          ℹ  No forward button found on step {step} — dry run complete")
                        return True, "dry-run complete"
                continue

            # Fill using the correct frame
            # (smart_fill_step sets the module-level _last_seen_fields global
            # for the stuck-questions logger below — form_ctx is a Playwright
            # Frame/Page, never a dict, so the old form_ctx.get(...) here always
            # produced an empty list regardless of what was actually on screen.)
            filled = smart_fill_step(form_ctx, profile_text, title, company, resume_name,
                                     cover_letter_text=cl_text_for_form, jd_text=jd_for_fill)
            print(f"          ✏️  Step {step} TOTAL: filled {filled} fields")
            time.sleep(1)

            nav_btns, nav_frame = _get_nav_buttons(apply_page)

            # Submit
            if any("submit" in b.get("text","") for b in nav_btns):
                print(f"          🏁 Submit button detected — submitting application!")
                _click_nav(nav_frame, "submit")
                time.sleep(4)
                submitted = _is_confirmed(apply_page)
                print(f"          {'🎉 Confirmed!' if submitted else '⚠ No confirmation text found'}")
                break

            # Continue / Next
            if nav_btns:
                btn_text = nav_btns[0].get("text","continue")
                if btn_text == last_btn:
                    same_btn_count += 1
                    print(f"          ⚠  Same button '{btn_text}' repeated {same_btn_count}x")
                else:
                    same_btn_count = 0
                    last_btn = btn_text

                # ── Validation error detection — THE fix for "form walk ended" ─
                # Before clicking Continue again, check if a validation error is
                # blocking the form. If so, try to fix the specific failing field
                # rather than blindly clicking and getting stuck.
                if same_btn_count >= 2:
                    validation_errors = _safe_eval(apply_page, """
                        () => {
                            const errSelectors = [
                                '[class*="error"]:not([class*="errorText--hidden"])',
                                '[class*="Error"]:not([class*="hidden"])',
                                '[aria-invalid="true"]',
                                '[aria-describedby*="error"]',
                                '.icl-TextInput--error',
                                '[data-testid*="error"]',
                                '[role="alert"]',
                            ];
                            const msgs = [];
                            for (const sel of errSelectors) {
                                for (const el of document.querySelectorAll(sel)) {
                                    if (!el.offsetParent) continue;
                                    const t = (el.innerText || el.textContent || '').trim();
                                    if (t && t.length > 2 && t.length < 300) msgs.push(t);
                                }
                            }
                            return [...new Set(msgs)].slice(0, 5);
                        }
                    """, [])

                    if validation_errors:
                        print(f"          🔴 Validation errors blocking Continue:")
                        for ve in validation_errors:
                            print(f"             • {ve}")

                        # Common fixes: salary out of range → try hourly rate
                        err_text = " ".join(validation_errors).lower()
                        if any(w in err_text for w in ["salary", "pay", "wage", "compensation", "amount"]):
                            print(f"          💰 Salary validation — trying hourly rate (45)")
                            _safe_eval(apply_page, """
                                () => {
                                    const inputs = Array.from(document.querySelectorAll('input[type="number"],input[type="text"]'));
                                    const sal = inputs.find(i => {
                                        const l = (document.querySelector('label[for="'+i.id+'"]') || {}).innerText || '';
                                        return l.toLowerCase().includes('salary') || l.toLowerCase().includes('pay')
                                            || i.getAttribute('placeholder','').toLowerCase().includes('salary');
                                    });
                                    if (sal) {
                                        sal.value = '45';
                                        ['input','change'].forEach(ev => sal.dispatchEvent(new Event(ev,{bubbles:true})));
                                        return true;
                                    }
                                    return false;
                                }
                            """, False)
                            time.sleep(0.5)

                        # ── Special case: Indeed "Build an Indeed Resume" upsell ──
                        # Indeed pushes a hosted-resume prompt on resume-selection-m.
                        # The radio click alone doesn't satisfy it — need to click the
                        # card container. Do that here and skip the generic re-fill loop
                        # (which would just click the radio again → infinite loop).
                        if "build" in err_text and ("indeed resume" in err_text or "indeed" in err_text):
                            print(f"          🎯 Indeed Resume upsell detected — clicking uploaded file card directly")
                            _safe_eval(apply_page, """
                                () => {
                                    // Find the uploaded file card and click it (not just its radio)
                                    const fileCardSelectors = [
                                        '[data-testid="FileResumeCard"]',
                                        '[data-testid="resume-card"]',
                                        '[class*="FileCard"]', '[class*="fileCard"]',
                                        '[class*="ResumeCard"]', '[class*="resumeCard"]',
                                        '[class*="uploadedResume"]', '[class*="fileResume"]',
                                    ];
                                    // Find the card whose text contains .pdf or .docx
                                    // NEVER click cards containing "build"/"recommended" without .pdf/.docx
                                    const BAD = ['build', 'create', 'recommended'];
                                    const allCards = Array.from(document.querySelectorAll(
                                        'label, li, article, [role="option"], [role="radio"], div[class*="resume" i]'
                                    ));
                                    const fileCard = allCards.find(el => {
                                        const txt = (el.innerText || el.textContent || '').toLowerCase();
                                        const hasFile = txt.includes('.pdf') || txt.includes('.docx');
                                        const isBad = BAD.some(k => txt.includes(k)) && !hasFile;
                                        return el.offsetParent && hasFile && !isBad;
                                    });
                                    let clicked = null;
                                    // Try explicit selectors first (these target file cards specifically)
                                    for (const sel of fileCardSelectors) {
                                        const el = document.querySelector(sel);
                                        if (el && el.offsetParent) { el.click(); clicked = sel; break; }
                                    }
                                    // Fall back to text-matched card (.pdf/.docx content)
                                    if (!clicked && fileCard) { fileCard.click(); clicked = 'text-match'; }
                                    // Click the radio inside the file card (not the "Build resume" radio)
                                    const radioParent = fileCard || document;
                                    const radios = Array.from(radioParent.querySelectorAll('input[type=radio]'));
                                    const radio = radios.find(r => {
                                        const lbl = (document.querySelector('label[for="'+r.id+'"]') || {}).innerText || '';
                                        return lbl.includes('.pdf') || lbl.includes('.docx') || fileCard;
                                    }) || (fileCard ? fileCard.querySelector('input[type=radio]') : null);
                                    if (radio && !radio.checked) {
                                        radio.click();
                                        ['input','change'].forEach(ev => radio.dispatchEvent(new Event(ev,{bubbles:true})));
                                    }
                                    return clicked;
                                }
                            """, None)
                            time.sleep(0.8)

                        # If field says "required" or "answer this question" — re-run fill
                        # (but NOT for the Indeed Resume upsell — that's handled above)
                        elif any(w in err_text for w in ["required", "answer", "enter", "provide", "select", "must"]):
                            print(f"          🔄 Required field error — re-running fill step")
                            smart_fill_step(form_ctx, profile_text, title, company, resume_name,
                                            cover_letter_text=cl_text_for_form, jd_text=jd_for_fill)
                            time.sleep(1)

                if same_btn_count >= 8:
                    # Stuck for 8 consecutive identical buttons — dump visible fields for diagnosis
                    try:
                        _stuck_fields = _safe_eval(apply_page, """
                            () => {
                                const out = [];
                                document.querySelectorAll('label,[aria-label],legend,[placeholder]').forEach(el => {
                                    if (!el.offsetParent) return;
                                    const t = (el.innerText||el.getAttribute('aria-label')||el.getAttribute('placeholder')||'').trim();
                                    if (t && t.length > 2 && t.length < 200) out.push(t);
                                });
                                return [...new Set(out)].slice(0,15);
                            }
                        """, [])
                        if _stuck_fields:
                            print(f"          🔍 Stuck — visible fields at this step:")
                            for _sf in _stuck_fields:
                                print(f"             • {_sf}")
                    except Exception:
                        pass
                    # Try force-submit once
                    print(f"          ⚠  Stuck on same button 8x — attempting force-submit")
                    _click_nav(nav_frame, "submit")
                    time.sleep(3)
                    submitted = _is_confirmed(apply_page)
                    if not submitted:
                        _click_any_forward_button(apply_page, verbose=False)
                        time.sleep(2)
                        submitted = _is_confirmed(apply_page)
                    break

                _click_nav(nav_frame, btn_text)
            else:
                # No named nav buttons — try any forward button (meteor314 pattern)
                fallback = _click_any_forward_button(apply_page)
                if not fallback:
                    submitted = _is_confirmed(apply_page)
                    if not submitted:
                        print(f"          ⚠  No forward button found — stopping")
                    break

    finally:
        # Close new tab after applying — return to search page
        if opened_new_tab:
            try: apply_page.close()
            except: pass

    return submitted, "submitted" if submitted else "form walk ended"


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=100,   help="Max applications per run (default 100)")
    parser.add_argument("--dry-run", action="store_true",      help="Score + build but don't submit")
    args = parser.parse_args()

    DRY_RUN   = args.dry_run
    MAX_APPLY = args.limit

    print(f"\n{'='*60}")
    print(f"  Indeed Apply Engine  {'[DRY RUN]' if DRY_RUN else ''}")
    print(f"  Target: {MAX_APPLY} applications  |  Fit threshold: {cfg.FIT_THRESHOLD}%")
    print(f"  Pages per query: {getattr(cfg, 'INDEED_PAGES_PER_QUERY', 2)}"
          f"  |  Queries: {len(SEARCH_QUERIES)}"
          f"  |  Potential cards: ~{getattr(cfg, 'INDEED_PAGES_PER_QUERY', 2) * 15 * len(SEARCH_QUERIES)}")
    print(f"{'='*60}\n")

    # ── Clear stale Chromium SingletonLock (left over if prior run crashed) ───
    # Without this, the morning scheduler run fails entirely with ProcessSingleton error.
    # IMPORTANT: only delete these if the owning process is actually dead. Deleting
    # a live process's lock and launching a second Chrome on the same profile
    # doesn't recover anything — Chrome's own instance check rejects the new one,
    # which crashes with "TargetClosedError: browser has been closed" (seen
    # 2026-07-06: an earlier dry-run's Chrome was still alive when a fresh
    # `runjobs` cleared its "stale" lock and launched straight into a collision).
    def _lock_owner_pid(lock_path):
        """Best-effort read of the PID a Singleton* lock file points to.
        These are usually a symlink target formatted 'hostname-pid'."""
        try:
            target = os.readlink(str(lock_path))
        except OSError:
            try:
                target = lock_path.read_text()
            except Exception:
                return None
        _m = re.search(r'-(\d+)\s*$', target.strip())
        return int(_m.group(1)) if _m else None

    def _pid_alive(pid):
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True   # process exists, just owned by someone else
        except Exception:
            return False

    _indeed_session_busy = False
    for _lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        _lock_path = SESSION_DIR / _lock_name
        # NOTE: these are symlinks whose *target* is a plain "hostname-pid" or
        # numeric string, not a real path — Path.exists() follows the symlink
        # and resolves the target, so it's always False for SingletonLock/
        # SingletonCookie even when the symlink itself is sitting right here.
        # That silently disabled this whole cleanup for those two files since
        # v1.1.5 (only SingletonSocket, whose target happens to be a real
        # path, was ever actually being cleared). Use is_symlink() too so a
        # stale SingletonLock left over from a real crash gets cleared, since
        # Chromium's own instance check gets confused (and self-terminates)
        # when Lock is present but Socket is already gone.
        if _lock_path.exists() or _lock_path.is_symlink():
            _owner_pid = _lock_owner_pid(_lock_path)
            if _owner_pid and _pid_alive(_owner_pid):
                print(f"  🚫 {_lock_name} is held by a still-running process (PID {_owner_pid}) "
                      f"— another Indeed session already has this browser profile open.")
                _indeed_session_busy = True
                continue
            try:
                _lock_path.unlink()
                print(f"  🔓 Cleared stale {_lock_name} — prior session didn't exit cleanly")
            except Exception as _le:
                print(f"  ⚠  Could not clear {_lock_name}: {_le}")

    if _indeed_session_busy:
        print("  ❌ Skipping this Indeed run — close the other session first, then run again.")
        try:
            notifier.send_alert(
                subject="⚠️ Indeed run skipped — session already in use",
                body=(
                    "The Indeed pipeline found another process still holding the browser "
                    "profile lock and skipped this run instead of launching a second "
                    "Chrome into a guaranteed collision.\n"
                    "Close the other Indeed window/process, then run Indeed again."
                ),
            )
        except Exception:
            pass
        return

    # ── Clear stale resume-selection cache (filename changes every job) ───────
    try:
        import sqlite3
        db_path = cfg.BASE_DIR / "data" / "answer_cache.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            # Delete any cache key that IS 'resume-selection' or ends in .docx/.pdf
            # Clear stale/wrong address + resume entries on every startup
            conn.execute("""
                DELETE FROM cache WHERE
                    label = 'resume-selection'
                    OR label LIKE '%.docx'
                    OR label LIKE '%.pdf'
                    OR label LIKE 'Address%'
                    OR label LIKE 'City%'
                    OR label LIKE 'State%'
                    OR label LIKE 'Postal%'
                    OR label LIKE 'ZIP%'
                    OR label LIKE 'Zip%'
                    OR label LIKE 'Street%'
                    OR label LIKE 'Desired salary%'
                    OR label LIKE 'Desired Salary%'
                    OR label IN (
                        'Desired Pay','Desired Salary','Expected Salary',
                        'Date Available','Start Date','No'
                    )
            """)
            deleted = conn.total_changes
            conn.commit()
            conn.close()
            if deleted:
                print(f"  🗄  Cleared {deleted} stale resume cache entries")
    except Exception as e:
        pass  # don't block startup if cache clear fails

    # ── Seed real address answers so Claude never invents fake ones ───────────
    # These exact label strings match what Indeed's smartapply form shows.
    REAL_ANSWERS = {
        # Address fields (various label styles seen across jobs)
        "Address *":           os.environ.get("HOME_ADDRESS", ""),
        "Address":             os.environ.get("HOME_ADDRESS", ""),
        "Street address":      os.environ.get("HOME_ADDRESS", ""),
        "City *":              os.environ.get("HOME_CITY", ""),
        "City":                os.environ.get("HOME_CITY", ""),
        "City, State":         os.environ.get("HOME_CITY_STATE", ""),
        "State/Province *":    "Florida",
        "State/Province":      "Florida",
        "State":               "Florida",
        "Postal/ZIP *":        os.environ.get("HOME_ZIP", ""),
        "Postal/ZIP":          os.environ.get("HOME_ZIP", ""),
        "Zip code":            os.environ.get("HOME_ZIP", ""),
        "ZIP Code":            os.environ.get("HOME_ZIP", ""),
        # Contact
        "Phone":               os.environ.get("HOME_PHONE", ""),
        "Phone number":        os.environ.get("HOME_PHONE", ""),
        "Type phone number":   os.environ.get("HOME_PHONE", ""),
        "Mobile number":       os.environ.get("HOME_PHONE", ""),
        "Cell phone":          os.environ.get("HOME_PHONE", ""),
        "LinkedIn URL":        "https://www.linkedin.com/in/yourusername",
        "LinkedIn Profile":    "https://www.linkedin.com/in/yourusername",
        # Work authorization & visa
        "Are you legally authorized to work in the United States?": "Yes",
        "Are you authorized to work in the US?":                    "Yes",
        "Do you require visa sponsorship now or in the future?":    "No",
        "Will you now or in the future require sponsorship?":       "No",
        "Desired Pay":         "70000",
        "Desired Salary":      "70000",
        "Expected Salary":     "70000",
        "Date Available":      (datetime.now() + timedelta(days=14)).strftime("%m/%d/%Y"),
        "Start Date":          (datetime.now() + timedelta(days=14)).strftime("%m/%d/%Y"),
        "Website, Blog or Portfolio": "https://www.linkedin.com/in/yourusername",
    }
    seeded = 0
    for lbl, val in REAL_ANSWERS.items():
        existing = _cache.get(lbl)
        if existing is None:  # only seed if not already cached
            _cache.save(lbl, val)
            seeded += 1
    if seeded:
        print(f"  🗄  Seeded {seeded} real-profile answers into cache")

    # ── Load profile + imports once ───────────────────────────────────────────
    import raghav_profile as rp
    import claude_engine  as ce
    import resume_builder as rb
    import cover_letter   as cl_mod
    import jd_parser      as jdp
    from pipeline_logger import RunLogger
    _run_log = RunLogger("indeed")

    full_profile    = rp.PROFILE
    profile_summary = ce.build_profile_summary(full_profile)

    log = load_log()
    # Cross-platform dedup: merge LinkedIn log so Indeed skips jobs already applied there.
    # IMPORTANT: kept in a SEPARATE dedup_log, never merged into `log` itself.
    # `log` is what gets appended-to and saved back to indeed_applied_log.json — merging
    # LinkedIn's ~8-16k entries into it directly (old behavior) meant every save during
    # an Indeed run rewrote the entire LinkedIn history back out under the wrong schema
    # (LinkedIn logs use a "note" field for the skip/fail reason, Indeed uses "reason"),
    # which is why indeed_applied_log.json ballooned to 119k+ records (only ~3% actually
    # Indeed) and why most "Failed" entries looked reason-less when reviewed.
    dedup_log = list(log)
    _li_log = cfg.BASE_DIR / "data" / "apply_log.json"
    if _li_log.exists():
        import json as _j
        try:
            dedup_log = dedup_log + _j.loads(_li_log.read_text())
        except Exception:
            pass
    applied_count  = 0
    scored_count   = 0
    skipped_count  = 0
    seen_this_run  = set()   # dedup within this session (company+title)

    with sync_playwright() as pw:
        # 2026-07-11: switched from Playwright's bundled Chromium build to the
        # real, installed Google Chrome binary (channel="chrome"). Root cause
        # investigation this week found that Raghav's REGULAR Chrome browser
        # reaches Indeed fine on the same network, while this pipeline's
        # browser gets Cloudflare-blocked immediately — even on a completely
        # fresh, just-reset profile (ruling out stale cookies) on the same IP
        # (ruling out IP reputation). The one remaining difference is the
        # browser build itself: Playwright's bundled "Chromium" has internal
        # differences from a real Google Chrome install (missing proprietary
        # components like Widevine, different internal version/build
        # signatures) that deeper bot-detection fingerprinting can pick up on
        # even with the navigator.webdriver-level stealth patch already in
        # place (v1.3.0). This still uses a completely separate, dedicated
        # profile (SESSION_DIR) — never touches Raghav's actual personal
        # Chrome session/tabs/logins, just uses the same underlying binary.
        # Falls back to the bundled Chromium (previous behaviour) if Chrome
        # isn't installed on this machine, so this can't hard-break the run.
        try:
            browser = pw.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                channel="chrome",
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
                timeout=getattr(cfg, "INDEED_BROWSER_LAUNCH_TIMEOUT_MS", 60000),
            )
            print("  🌐  Using real Google Chrome (channel=chrome)")
        except Exception as _chrome_err:
            print(f"  ⚠  Real Chrome not available ({str(_chrome_err)[:80]}) — falling back to bundled Chromium")
            browser = pw.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
                timeout=getattr(cfg, "INDEED_BROWSER_LAUNCH_TIMEOUT_MS", 60000),
            )

        # Stealth init script — added 2026-07-09 after researching how the most
        # established open-source job-apply bots (e.g. undetected-chromedriver-
        # based projects) reduce CAPTCHA frequency: they patch the handful of
        # JS-visible automation fingerprints that reCAPTCHA Enterprise's risk
        # scoring checks, instead of only fighting the CAPTCHA after it appears
        # (which is all this pipeline did before today). `--disable-blink-
        # features=AutomationControlled` alone does NOT clear `navigator.
        # webdriver` in current Chromium — that flag is still `true` by default,
        # and it's one of the single most common, deterministic bot signals
        # sites check for. Runs before every page/frame's own scripts in this
        # context (context.add_init_script, not page-level), so it applies to
        # every navigation for the life of the browser, not just the first page.
        try:
            browser.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = window.chrome || { runtime: {} };
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                const _origQuery = window.navigator.permissions && window.navigator.permissions.query;
                if (_origQuery) {
                    window.navigator.permissions.query = (params) => (
                        params && params.name === 'notifications'
                            ? Promise.resolve({ state: Notification.permission })
                            : _origQuery(params)
                    );
                }
            """)
        except Exception:
            pass
        # If this process dies for any reason (crash, uncaught exception from the
        # Playwright driver, etc.) before browser.close() runs, the Chromium profile
        # lock (SingletonLock/Cookie/Socket) is left behind and the *next* launch
        # hangs for minutes waiting on a lock nothing still holds. This process owns
        # that lock right now, so on exit — clean or not — release it unconditionally.
        def _release_indeed_session_lock():
            for _lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                _lp = SESSION_DIR / _lock_name
                try:
                    if _lp.exists() or _lp.is_symlink():
                        _lp.unlink()
                except Exception:
                    pass
        atexit.register(_release_indeed_session_lock)

        page = browser.pages[0] if browser.pages else browser.new_page()
        # Auto-dismiss any JS alert/confirm dialogs — prevents ProtocolError crash.
        # Guard against a page that already closed/navigated by the time this fires
        # (that race is what crashes the Playwright driver — see CHANGELOG).
        def _safe_dismiss(d):
            try:
                if not d.page.is_closed():
                    d.dismiss()
            except Exception:
                pass
        browser.on("dialog", _safe_dismiss)

        ensure_login(page)

        _cf_delay_min = getattr(cfg, "INDEED_SEARCH_DELAY_MIN",  5)
        _cf_delay_max = getattr(cfg, "INDEED_SEARCH_DELAY_MAX",  12)
        _pg_delay_min = getattr(cfg, "INDEED_PAGE_DELAY_MIN",    3)
        _pg_delay_max = getattr(cfg, "INDEED_PAGE_DELAY_MAX",    7)
        _cf_retry_wait = getattr(cfg, "INDEED_CF_RETRY_WAIT_SEC", 45)
        _do_scroll    = getattr(cfg, "INDEED_SCROLL_SEARCHES",   True)

        def _is_cloudflare_page():
            """Return True if Indeed is showing a Cloudflare challenge page."""
            try:
                txt = page.evaluate("() => document.body.innerText") or ""
                url = page.url or ""
                return ("additional verification required" in txt.lower()
                        or "ray id" in txt.lower()
                        or "cf-browser-verification" in (page.evaluate("() => document.documentElement.innerHTML") or "").lower())
            except Exception:
                return False

        def _human_scroll():
            """Simulate a few human-like scrolls on the current page."""
            if not _do_scroll:
                return
            try:
                for _ in range(random.randint(2, 4)):
                    page.evaluate(f"window.scrollBy(0, {random.randint(300, 700)})")
                    time.sleep(random.uniform(0.3, 0.8))
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(random.uniform(0.3, 0.6))
            except Exception:
                pass

        global _indeed_blocked, _browser_window_closed
        _consecutive_empty_queries = 0
        _empty_query_bail = getattr(cfg, "INDEED_EMPTY_QUERY_BAIL_THRESHOLD", 4)
        # 2026-07-10: Raghav hit a full Cloudflare "Additional Verification
        # Required" wall and had to cancel manually. Checked the logs — the
        # ONLY existing bail-out (_consecutive_empty_queries, 4 in a row) is
        # keyed off an AMBIGUOUS signal (0 cards, which could mean lots of
        # things). But `_is_cloudflare_page()` below is a DIRECT, confirmed
        # signal — once it fires twice in a row, there's no ambiguity left,
        # yet the old code just waited 45s, retried once, gave up on that one
        # query, and moved on to hammer the NEXT query against the same wall.
        # With up to 3 page-loads per query, that's several minutes of
        # continued hammering per query before even reaching the 4-empty-
        # query threshold — actively making an already-flagged session worse
        # instead of backing off. Tracked separately so a confirmed Cloudflare
        # wall trips the bail much faster than an ambiguous empty result.
        _consecutive_cf_blocks = 0
        _cf_block_bail = getattr(cfg, "INDEED_CF_BLOCK_BAIL_THRESHOLD", 2)
        # Rolling-average low-yield tracking — catches a soft-blocked session
        # that trickles 1-2 cards through occasionally instead of hard-zero
        # every time, which would otherwise keep resetting the counter above
        # and never trip the bail. See config.py for thresholds.
        _total_queries_done = 0
        _total_cards_found = 0
        _low_yield_min_queries = getattr(cfg, "INDEED_LOW_YIELD_MIN_QUERIES", 6)
        _low_yield_avg_cards = getattr(cfg, "INDEED_LOW_YIELD_AVG_CARDS", 5)

        for _qi, query in enumerate(SEARCH_QUERIES):
            if applied_count >= MAX_APPLY:
                break
            if _indeed_blocked:
                print(f"\n  🛑 Stopping remaining searches — session flagged as blocked earlier this run.")
                break
            if _browser_window_closed:
                print(f"\n  🛑 Stopping — the Chrome window closed earlier this run and can't be recovered.")
                print(f"  👉 Re-open the pipeline's Chrome window and run again.")
                break

            # Human-like inter-query delay (skip before very first query)
            if _qi > 0:
                _inter = random.uniform(_cf_delay_min, _cf_delay_max)
                print(f"\n  ⏳ Waiting {_inter:.1f}s before next search (CF mitigation)...")
                time.sleep(_inter)

            print(f"\n🔍  Query: {query}")

            # Scrape up to INDEED_PAGES_PER_QUERY pages per query (default 3 = ~45 cards).
            # Each page has start=0, 15, 30 — Indeed shows 15 results per page.
            _pages_per_query = getattr(cfg, "INDEED_PAGES_PER_QUERY", 3)
            _page_starts = [i * 15 for i in range(_pages_per_query)]
            job_cards = []
            for _page_start in _page_starts:
                if applied_count >= MAX_APPLY:
                    break
                url = build_indeed_url(query, start=_page_start)

                # Retry up to 3 times on network timeout; detect CF challenge and back off
                loaded = False
                for _attempt in range(3):
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        # Check for Cloudflare challenge immediately after load
                        if _is_cloudflare_page():
                            print(f"  🚨 Cloudflare challenge detected — waiting {_cf_retry_wait}s before retry...")
                            time.sleep(_cf_retry_wait)
                            page.goto("https://www.indeed.com/", wait_until="domcontentloaded", timeout=20000)
                            time.sleep(random.uniform(4, 8))
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                            if _is_cloudflare_page():
                                print(f"  ❌ Cloudflare still blocking after retry — skipping this query")
                                _consecutive_cf_blocks += 1
                                break
                            else:
                                _consecutive_cf_blocks = 0
                        else:
                            _consecutive_cf_blocks = 0
                        loaded = True
                        break
                    except Exception as _nav_err:
                        _err_str = str(_nav_err)
                        # The Chrome window itself is gone (closed, crashed) — not a
                        # network blip. Retrying against the same dead page object
                        # will fail identically every time, so stop immediately
                        # instead of burning 3 attempts here and repeating this for
                        # every remaining page and every remaining query.
                        if ("Target page, context or browser has been closed" in _err_str
                                or "Not attached to an active page" in _err_str):
                            print(f"  🛑 Chrome window closed — can't continue (not a network issue, no point retrying)")
                            _browser_window_closed = True
                            break
                        print(f"  ⚠  Search load failed (p{_page_start//15+1}, attempt {_attempt+1}/3): {_err_str[:60]}")
                        if _attempt < 2:
                            time.sleep(5)
                if _browser_window_closed:
                    break
                if not loaded:
                    print(f"  ❌ Could not load page {_page_start//15+1} — skipping")
                    continue

                # Human-like post-load pause + scroll
                _pg_wait = random.uniform(_pg_delay_min, _pg_delay_max)
                time.sleep(_pg_wait)
                _human_scroll()

                _page_cards = page.evaluate("""
                    () => {
                        const cards = Array.from(document.querySelectorAll(
                            '[data-jk], .job_seen_beacon, [class*="jobCard"], .resultContent'
                        ));
                        return cards.map(c => {
                            const jk = c.getAttribute('data-jk') || c.id || '';
                            const titleEl = c.querySelector('h2 a, [data-testid="job-title"], .jobTitle a');
                            const coEl    = c.querySelector('[data-testid="company-name"], .companyName');
                            const locEl   = c.querySelector('[data-testid="text-location"], .companyLocation');
                            const title   = titleEl ? titleEl.innerText.trim() : '';
                            const company = coEl    ? coEl.innerText.trim()    : '';
                            // Short snippet Indeed already shows under each card — used for a
                            // coarse pre-filter before opening the full job page. Selectors
                            // drift over time, so fall back to the card's own text (minus the
                            // bits we already captured) if none of the known ones match.
                            const snipEl = c.querySelector(
                                '.job-snippet, [data-testid="jobsnippet_footer"], ' +
                                '.underShelfFooter, ul.job-snippet, [class*="snippet"]'
                            );
                            let snippet = snipEl ? snipEl.innerText.trim() : '';
                            if (!snippet) {
                                snippet = (c.innerText || '')
                                    .replace(title, '').replace(company, '')
                                    .replace(/\\s+/g, ' ').trim().slice(0, 300);
                            }
                            return {
                                jk:      jk,
                                title:   title,
                                company: company,
                                location:locEl   ? locEl.innerText.trim()   : '',
                                href:    titleEl ? (titleEl.getAttribute('href') || '') : '',
                                snippet: snippet.slice(0, 300),
                            };
                        }).filter(c => c.title.length > 2);
                    }
                """) or []
                job_cards.extend(_page_cards)
                if len(_page_cards) < 10:
                    break   # fewer than 10 results on this page = no point fetching next

            print(f"  Found {len(job_cards)} cards ({_pages_per_query} pages)")
            _total_queries_done += 1
            _total_cards_found += len(job_cards)

            # Confirmed Cloudflare wall, not just an ambiguous empty result —
            # bail fast instead of hammering the next query against the same
            # block. See the comment near _consecutive_cf_blocks above for why
            # this is separate from (and faster than) the empty-query bail.
            if _consecutive_cf_blocks >= _cf_block_bail:
                print(f"\n  🛑 {_consecutive_cf_blocks} confirmed Cloudflare blocks in a row — "
                      f"stopping Indeed run early instead of continuing to hammer a flagged session.")
                try:
                    notifier.send_alert(
                        subject=f"🛑 Indeed run stopped — {_consecutive_cf_blocks} Cloudflare blocks in a row",
                        body=(
                            f"The Indeed pipeline hit {_consecutive_cf_blocks} confirmed Cloudflare "
                            f"'Additional Verification Required' challenges in a row and stopped itself "
                            f"early instead of continuing to retry against an already-flagged session.\n"
                            f"This usually means too many requests too quickly (e.g. several runs back "
                            f"to back). Let it sit for a few hours before running again."
                        ),
                    )
                except Exception as _notify_err:
                    print(f"          ⚠  Could not send Cloudflare-blocked email: {_notify_err}")
                _indeed_blocked = True
                break

            # Repeated zero-card searches almost always mean the session is
            # blocked (e.g. every page load aborting) rather than a real lack
            # of results — bail instead of burning the rest of the run on it.
            if len(job_cards) == 0:
                _consecutive_empty_queries += 1
                if _consecutive_empty_queries >= _empty_query_bail:
                    print(f"\n  🛑 {_consecutive_empty_queries} consecutive searches returned 0 cards — "
                          f"Indeed is likely blocking this session. Stopping Indeed run early.")
                    try:
                        notifier.send_alert(
                            subject=f"🛑 Indeed run stopped — {_consecutive_empty_queries} empty searches in a row, likely blocked",
                            body=(
                                f"The Indeed pipeline hit {_consecutive_empty_queries} consecutive searches "
                                f"returning 0 cards (Cloudflare challenge or other block) and stopped itself "
                                f"early instead of burning the rest of the query list.\n"
                                f"Check the session manually — Indeed may be flagging this browser/IP."
                            ),
                        )
                    except Exception as _notify_err:
                        print(f"          ⚠  Could not send blocked-session email: {_notify_err}")
                    _indeed_blocked = True
                    break
            else:
                _consecutive_empty_queries = 0

            # Low-yield check — a soft-blocked session that trickles a few
            # cards through per query never trips the strict zero-streak bail
            # above, so also bail if the rolling average is far below a
            # healthy query's yield (up to ~45 cards/query by design).
            if (not _indeed_blocked
                    and _total_queries_done >= _low_yield_min_queries
                    and (_total_cards_found / _total_queries_done) < _low_yield_avg_cards):
                _avg = _total_cards_found / _total_queries_done
                print(f"\n  🛑 Only {_avg:.1f} cards/query average over {_total_queries_done} "
                      f"searches (expected ~45) — Indeed is likely soft-blocking this session. "
                      f"Stopping Indeed run early.")
                try:
                    notifier.send_alert(
                        subject=f"🛑 Indeed run stopped — low yield ({_avg:.1f} cards/query avg), likely blocked",
                        body=(
                            f"The Indeed pipeline averaged {_avg:.1f} cards/query over "
                            f"{_total_queries_done} searches (a healthy query returns ~45) and "
                            f"stopped itself early instead of grinding through the rest of the "
                            f"query list for little to no gain.\n"
                            f"Check the session manually — Indeed may be soft-blocking this browser/IP."
                        ),
                    )
                except Exception as _notify_err:
                    print(f"          ⚠  Could not send low-yield-session email: {_notify_err}")
                _indeed_blocked = True
                break

            # Domain keyword set used both by the pre-filter pass below and the
            # per-card loop further down — kept in one place so the two can't
            # silently drift apart.
            _domain_words = {"data", "sql", "python", "analytics", "engineer",
                             "analyst", "scientist", "bi", "etl", "pipeline",
                             "database", "reporting", "intelligence", "ml",
                             "cloud", "spark", "databricks", "snowflake"}

            # ── Batched snippet pre-filter — one Claude call for the whole query
            # instead of opening every surviving card's job page one at a time.
            # Only cards that already pass the free, zero-cost filters (title
            # level/domain/staffing/dedup) are sent — this never spends tokens
            # on cards that would've been skipped anyway. The real, strict
            # score_fit() check on the full JD still runs afterward for anything
            # this doesn't filter out — see claude_engine.prefilter_cards_batch().
            _snippet_skip_keys = set()
            _prefilter_survivors = []
            for _c in job_cards:
                _t   = _c.get("title", "")
                _co  = _c.get("company", "")
                _jk  = _c.get("jk", "")
                _hrf = _c.get("href", "")
                if not _t or not is_good_level(_t) or not is_relevant_domain(_t):
                    continue
                if not any(w in _t.lower() for w in _domain_words):
                    continue
                _is_staff, _ = _staffing.is_staffing_or_consultancy(_co)
                if _is_staff:
                    continue
                if _hrf.startswith("http"):
                    _u = _hrf
                elif _hrf.startswith("/"):
                    _u = "https://www.indeed.com" + _hrf
                elif _jk:
                    _u = f"https://www.indeed.com/viewjob?jk={_jk}"
                else:
                    continue
                if already_applied(_u, dedup_log, _t, _co):
                    continue
                _prefilter_survivors.append({
                    "jk": _jk, "url": _u, "title": _t, "company": _co,
                    "snippet": _c.get("snippet", ""),
                })

            if _prefilter_survivors:
                try:
                    _verdicts = ce.prefilter_cards_batch(_prefilter_survivors)
                    _snippet_skip_keys = {
                        k for k, keep in _verdicts.items() if not keep
                    }
                    if _snippet_skip_keys:
                        print(f"  🔎 Snippet pre-filter: {len(_snippet_skip_keys)}/"
                              f"{len(_prefilter_survivors)} skipped without opening the job page")
                except Exception as _pf_err:
                    print(f"  ⚠  Snippet pre-filter failed (opening all candidates instead): {_pf_err}")
                    _snippet_skip_keys = set()

            for card in job_cards:
                if applied_count >= MAX_APPLY:
                    break
                if _indeed_blocked:
                    break

                title   = card.get("title","")
                company = card.get("company","")
                jk      = card.get("jk","")
                href    = card.get("href","")

                # Blank company name — almost always one of Indeed's own
                # "recommended near you" filler cards padded into the results
                # list when a narrow query (entry-level, last 7 days, exact
                # title) runs out of genuine matches, not a real search hit.
                # These reliably fail JD extraction and score a flat 0% later
                # anyway — skip now instead of paying for a page load + a
                # wasted Claude call to learn that.
                if not company.strip():
                    print(f"  ⏭  Blank company name: '{title}' — likely a filler/recommended card, skipping")
                    skipped_count += 1
                    continue

                # Session-level dedup FIRST — prevents duplicate messages across queries
                session_key = f"{company.lower().strip()}|{title.lower().strip()}"
                if session_key in seen_this_run:
                    continue
                seen_this_run.add(session_key)

                if not title or not is_good_level(title):
                    skipped_count += 1
                    continue

                if not is_relevant_domain(title):
                    print(f"  ⏭  Off-domain title: '{title}' — skipping (not data/analytics)")
                    skipped_count += 1
                    continue

                # ── Local pre-filter — zero token cost ────────────────────────
                # NOTE: only run domain check on title — prefilter needs full JD text
                # to count skill matches accurately. Title-only check was incorrectly
                # skipping valid jobs (e.g. "Junior Data Engineer" only has 1 skill match).
                # The full JD prefilter runs later at line 3015 after page load.
                # (_domain_words is defined once above, before this loop, so it
                # can't drift out of sync with the pre-filter pass that uses it too.)
                if not any(w in title.lower() for w in _domain_words):
                    skipped_count += 1
                    continue

                # ── Staffing / consultancy exclusion — cheap company-name check
                # before spending a page load on it (full JD-text check happens
                # again after the job page loads, further down).
                _is_staffing, _staffing_reason = _staffing.is_staffing_or_consultancy(company)
                if _is_staffing:
                    print(f"  ⏭  {company} — {title} → SKIP staffing/consultancy ({_staffing_reason})")
                    skipped_count += 1
                    continue

                # Build job URL
                if href.startswith("http"):
                    job_url = href
                elif href.startswith("/"):
                    job_url = "https://www.indeed.com" + href
                elif jk:
                    job_url = f"https://www.indeed.com/viewjob?jk={jk}"
                else:
                    continue

                # Quick dedup check
                if already_applied(job_url, dedup_log, title, company):
                    print(f"  ↩  {company} — {title} → already applied (dedup)")
                    skipped_count += 1
                    continue

                # Snippet pre-filter verdict (computed once for the whole query,
                # above) — skip without a page load if Claude flagged this one
                # as an obvious miss from the listing snippet alone.
                if (jk or job_url) in _snippet_skip_keys:
                    print(f"  ⏭  {company} — {title} → SKIP (snippet pre-filter)")
                    skipped_count += 1
                    continue

                # Navigate to job detail
                print(f"\n  📋 {company} — {title}")
                try:
                    page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
                except:
                    print(f"  ⚠  Failed to load job page")
                    skipped_count += 1
                    continue
                time.sleep(3)

                # Extract details
                details = extract_job_panel(page)
                if not details.get("title"):
                    details["title"]   = title
                    details["company"] = company

                # ── External apply check — BEFORE scoring or building resume ──────
                # Check both the panel flag AND a direct button-text scan.
                # Any external signal = skip immediately, zero API calls wasted.
                is_external_panel = details.get("isExternal") and not details.get("hasIndeedApply")
                is_external_text  = page.evaluate("""
                    () => {
                        const EXTERNAL = [
                            'apply on company site', 'apply on employer site',
                            'apply on employer', 'apply externally',
                            'continue to company', 'apply on the company',
                            "you're leaving indeed", 'leaving indeed',
                            'apply on company', 'external application'
                        ];
                        const body = (document.body.innerText || '').toLowerCase();
                        const btns = Array.from(document.querySelectorAll(
                            'button, a, [role="button"]'
                        ));
                        const btnMatch = btns.some(b => {
                            const t = (b.innerText || b.textContent || '').toLowerCase().trim();
                            return EXTERNAL.some(kw => t.includes(kw));
                        });
                        const bodyMatch = EXTERNAL.some(kw => body.includes(kw));
                        return btnMatch || bodyMatch;
                    }
                """) or False
                if is_external_panel or is_external_text:
                    # Check if the external link is a Workday URL — queue it
                    wd_url = page.evaluate("""
                        () => {
                            const WD = ['myworkdayjobs.com', 'workday.com/jobs'];
                            const links = Array.from(document.querySelectorAll('a[href]'));
                            for (const a of links) {
                                const h = a.getAttribute('href') || '';
                                if (WD.some(d => h.includes(d))) return h;
                            }
                            return null;
                        }
                    """) or None
                    if wd_url:
                        try:
                            import workday_apply_now as _wd
                            _wd.add_to_wd_queue({
                                "title": title, "company": company,
                                "url": wd_url, "description": "",
                                "source": "indeed",
                            })
                            print(f"  📥 Workday link detected — queued: {wd_url[:60]}")
                        except Exception as _wde:
                            print(f"  ⚠  Workday queue error: {_wde}")
                    else:
                        print(f"  ⏭  External apply detected — skipping (no resume build, no API call)")
                    skipped_count += 1
                    continue

                jd = details.get("description","")
                live_url = details.get("jobUrl", job_url)

                # ── Security clearance check — kill immediately, no API cost ──
                if any(kw in jd.lower() for kw in cfg.CLEARANCE_KEYWORDS):
                    print(f"  🚫 Clearance required — skipping {company}")
                    skipped_count += 1
                    continue

                # ── Staffing / consultancy exclusion — full JD text this time,
                # catches "on behalf of our client" style postings that don't
                # give it away in the company name (already checked above).
                _is_staffing2, _staffing_reason2 = _staffing.is_staffing_or_consultancy(company, jd)
                if _is_staffing2:
                    print(f"  🚫 {company} — staffing/consultancy ({_staffing_reason2}) — skipping")
                    skipped_count += 1
                    continue

                # ── Full JD pre-filter — runs after page load, before API call ─
                _skip_jd, _jd_matches = ce.local_prefilter(jd, title)
                if _skip_jd:
                    print(f"  ⏭  Local filter: only {_jd_matches} skill matches in JD — skipping Claude score")
                    skipped_count += 1
                    log.append({"status": "Skipped", "title": title, "company": company,
                                "score": 0, "url": live_url, "reason": "local prefilter",
                                "timestamp": datetime.now().isoformat(), "platform": "Indeed"})
                    save_log(log)
                    continue

                # ── Score fit — Claude (paid) or free ATS keyword match ─────────
                # Toggle: config.USE_CLAUDE_SCORING (default False, per Raghav's
                # request 2026-07-06 — no ongoing API cost).
                if getattr(cfg, "USE_CLAUDE_SCORING", True):
                    result = ce.score_fit(profile_summary, jd, title, company)
                else:
                    result = jdp.ats_fit_score(jd, title, company)
                score  = int(result.get("score", 0)) if isinstance(result, dict) else int(result)
                scored_count += 1
                grade  = result.get("grade", "") if isinstance(result, dict) else ""
                _threshold = cfg.FIT_THRESHOLD if getattr(cfg, "USE_CLAUDE_SCORING", True) \
                             else getattr(cfg, "ATS_FIT_THRESHOLD", 60)
                print(f"  🎯 Fit score: {score}%  {grade}  {'✅' if score >= _threshold else '❌'}")

                if score < _threshold:
                    skipped_count += 1
                    log.append({"status": "Skipped", "title": title, "company": company,
                                "score": score, "url": live_url,
                                "timestamp": datetime.now().isoformat(), "platform": "Indeed"})
                    save_log(log)
                    continue

                # ── Build resume ───────────────────────────────────────────────
                print(f"  📄 Building tailored resume...")
                resume_path = ""
                try:
                    parsed = jdp.parse_jd(jd, title)
                    res = rb.build_resume(
                        job_title=title, company=company,
                        jd_keywords=parsed.get("jd_keywords", []),
                        injectable_kws=parsed.get("injectable_keywords", []),
                        initial_score=parsed.get("initial_score", 0),
                        optimized_score=parsed.get("optimized_score", 0),
                        jd_text=jd,
                        profile_summary=full_profile.get("summary", ""),
                    )
                    resume_path = res[0] if isinstance(res, tuple) else str(res)
                    print(f"  ✅ Resume: {Path(resume_path).name}")
                except Exception as e:
                    print(f"  ⚠  Resume build failed: {e}")

                if not resume_path:
                    skipped_count += 1
                    continue

                # Cover letters PAUSED — resume does the heavy lifting.
                cover_letter_path = ""

                # ── Apply ──────────────────────────────────────────────────────
                print(f"  🚀 Applying via Indeed Apply...")
                # Include full JD so apply_to_job can use it for salary + contextual answers
                job_info = {"title": title, "company": company,
                            "description": jd, "jd_text": jd,
                            "url": live_url or job_url}

                try:
                    success, reason = apply_to_job(
                        page, browser, job_info, resume_path, cover_letter_path,
                        profile_text=profile_summary, dry_run=DRY_RUN
                    )
                except Exception as e:
                    success, reason = False, str(e)

                # Queue CAPTCHA-timed-out jobs for a retry pass after the main loop
                if not success and reason == "captcha-timeout":
                    _captcha_retry_queue.append({
                        "title":       title,
                        "company":     company,
                        "job_url":     live_url or job_url,
                        "score":       score,
                        "grade":       grade,
                        "jd":          jd,
                        "resume_path": resume_path,
                    })
                    print(f"  🔁 Queued for CAPTCHA retry ({len(_captcha_retry_queue)} in queue)")

                status = "Applied" if (success and not DRY_RUN) else ("Dry-Run" if DRY_RUN else "Failed")
                icon   = "✅" if success else "❌"
                print(f"  {icon} {status}: {reason}")

                _run_log.job_start(title, company, live_url or job_url, fit_score=score, grade=grade)
                _run_log.job_result(status, reason=reason, resume_file=Path(resume_path).name if resume_path else "")

                # Screenshot
                ss_path = ""
                if success and not DRY_RUN:
                    try:
                        ss_file = SCREENSHOTS / f"indeed_{re.sub(r'[^a-z0-9]', '_', company.lower())}_{re.sub(r'[^a-z0-9]', '_', title.lower())}.png"
                        page.screenshot(path=str(ss_file), full_page=False)
                        ss_path = str(ss_file)
                        print(f"          📸 Screenshot saved")
                    except:
                        pass

                # Email notification
                if success and not DRY_RUN:
                    notifier.notify_applied(
                        title=title, company=company, fit_score=score,
                        resume_path=resume_path or "",
                        cover_letter_path=cover_letter_path or "",
                        platform="Indeed",
                        job_url=live_url or job_url,
                        screenshot_path=ss_path
                    )

                # Log
                log.append({
                    "status":    status,
                    "title":     title,
                    "company":   company,
                    "score":     score,
                    "url":       live_url or job_url,
                    "resume":    resume_path or "",
                    "timestamp": datetime.now().isoformat(),
                    "platform":  "Indeed",
                    "reason":    reason,
                })
                save_log(log)

                if success and not DRY_RUN:
                    applied_count += 1

                time.sleep(2)

        # ── CAPTCHA retry pass ────────────────────────────────────────────────
        # Jobs that timed out on CAPTCHA get one more attempt after the main loop.
        # By then reCAPTCHA has cooled down and the user may be at their Mac.
        if _captcha_retry_queue and applied_count < MAX_APPLY:
            retry_count = min(len(_captcha_retry_queue), MAX_APPLY - applied_count)
            print(f"\n{'='*60}")
            print(f"  🔁 CAPTCHA RETRY PASS — {retry_count} job(s) to retry")
            print(f"  Sleeping 3 minutes to let reCAPTCHA cool down...")
            time.sleep(180)

            for retry_item in _captcha_retry_queue[:retry_count]:
                if applied_count >= MAX_APPLY:
                    break

                r_title       = retry_item["title"]
                r_company     = retry_item["company"]
                r_job_url     = retry_item["job_url"]
                r_score       = retry_item["score"]
                r_grade       = retry_item["grade"]
                r_resume_path = retry_item["resume_path"]
                r_jd          = retry_item["jd"]

                print(f"\n  🔁 Retry: {r_company} — {r_title}")
                try:
                    page.goto(r_job_url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(random.uniform(3, 5))
                except Exception as _re:
                    print(f"  ⚠  Retry nav failed: {_re} — skipping")
                    continue

                r_job_info = {
                    "title": r_title, "company": r_company,
                    "description": r_jd, "jd_text": r_jd,
                    "url": r_job_url,
                }
                try:
                    r_success, r_reason = apply_to_job(
                        page, browser, r_job_info, r_resume_path, "",
                        profile_text=profile_summary, dry_run=DRY_RUN
                    )
                except Exception as _re:
                    r_success, r_reason = False, str(_re)

                r_status = "Applied" if (r_success and not DRY_RUN) else ("Dry-Run" if DRY_RUN else "Failed")
                r_icon   = "✅" if r_success else "❌"
                print(f"  {r_icon} Retry {r_status}: {r_reason}")

                _run_log.job_start(r_title, r_company, r_job_url, fit_score=r_score, grade=r_grade)
                _run_log.job_result(r_status, reason=f"retry:{r_reason}",
                                    resume_file=Path(r_resume_path).name if r_resume_path else "")

                if r_success and not DRY_RUN:
                    notifier.notify_applied(
                        title=r_title, company=r_company, fit_score=r_score,
                        resume_path=r_resume_path or "",
                        cover_letter_path="",
                        platform="Indeed",
                        job_url=r_job_url,
                    )
                    applied_count += 1

                log.append({
                    "status":    r_status,
                    "title":     r_title,
                    "company":   r_company,
                    "score":     r_score,
                    "url":       r_job_url,
                    "resume":    r_resume_path or "",
                    "timestamp": datetime.now().isoformat(),
                    "platform":  "Indeed",
                    "reason":    f"captcha-retry:{r_reason}",
                })
                save_log(log)
                time.sleep(2)

        browser.close()

    # ── Session summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Indeed session done")
    print(f"  ✅ Applied:  {applied_count}")
    print(f"  🎯 Scored:   {scored_count}")
    print(f"  ⏭  Skipped:  {skipped_count}")
    print(f"{'='*60}\n")

    _cost_summary = ce.get_cost_summary()
    _cost_stats   = ce.get_cost_dict()
    print(f"  💰 {_cost_summary}")
    _cache.print_stats()
    _run_log.finish(searches_run=len(SEARCH_QUERIES), jobs_found=scored_count + skipped_count)

    notifier.notify_session_done(applied_count, scored_count, skipped_count,
                                  cost_stats=_cost_stats)


if __name__ == "__main__":
    main()
