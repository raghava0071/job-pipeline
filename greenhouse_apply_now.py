#!/usr/bin/env python3
# =============================================================================
# GREENHOUSE_APPLY_NOW.PY — Automated Greenhouse job application engine
#
# SCOPE (v1 — guest apply only):
#   Greenhouse postings (boards.greenhouse.io/... and job-boards.greenhouse.io/...)
#   can be applied to WITHOUT creating an account — one long single-page form:
#   name/email/phone, resume upload, custom questions, optional EEO section,
#   then Submit. This engine only does that guest-apply flow. Account-creation
#   (some companies force a Greenhouse-hosted account) is NOT handled — those
#   jobs will fail cleanly with a reason and get logged, not silently skipped.
#
# FORM-FILL SELECTORS STILL NOT VERIFIED AGAINST A LIVE POSTING — build
# environment has no network path to greenhouse.io to inspect real DOM (same
# restriction that forced the discovery rewrite below). Selectors are
# Greenhouse's well-documented, long-stable field IDs (legacy embed) PLUS a
# generic label-based fallback (covers the newer job-boards.greenhouse.io
# React UI, which uses standard <label for> / aria-label associations instead
# of those IDs). Run with --dry-run against a couple of real job URLs first
# and watch the "📋 N field(s) on this step" / "⚠ could not find" print lines
# before trusting this on a live run — see CLAUDE.md safety workflow.
#
# DISCOVERY (the part below) IS verified as a real, working API — confirmed
# via live search results showing real job URLs under each configured
# company token — even though this build environment can't reach it directly
# to test the actual HTTP call end-to-end; see the --url test command in the
# CHANGELOG for what to run on a machine with normal network access.
#
# ARCHITECTURE: mirrors workday_apply_now.py's shape (discovery, jd_parser
# scoring, resume_builder/cover_letter, answer_cache/claude_answers/
# qa_answers layered form filling, notifier + JSON log) but scoped down —
# Greenhouse guest-apply has no login/account-creation/multi-step wizard, so
# none of that machinery is ported here.
#
# DISCOVERY — Greenhouse public Job Board API, not Google search (2026-08-25):
# Google started blocking the site:boards.greenhouse.io search this engine
# used to rely on. Per Raghav: don't fight that with stealth/human-mimicry —
# dead end, and risks the IP (Indeed already proved this). Discovery now
# calls Greenhouse's own public, unauthenticated, documented Job Board API
# (https://developers.greenhouse.io/job-board.html) — a plain JSON endpoint
# every embeddable Greenhouse careers widget uses, meant to be read
# programmatically. No login, no scraping, no bot-detection to trigger.
#
# USAGE:
#   python greenhouse_apply_now.py --limit 5
#   python greenhouse_apply_now.py --limit 2 --dry-run
# =============================================================================

import os, sys, time, json, argparse, re, html
from pathlib import Path
from datetime import datetime
import requests

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import config as cfg
import answer_cache as _cache
import notifier

try:
    from salary_helper import pick_salary as _pick_salary
except ImportError:
    _pick_salary = lambda jd, title: "75000"

try:
    import qa_answers as _qa
except ImportError:
    _qa = None

try:
    import claude_answers as _claude_ans
except ImportError:
    _claude_ans = None

try:
    import raghav_profile as rp
except ImportError:
    rp = None

DATA_DIR    = cfg.DATA_DIR
SESSION_DIR = cfg.BASE_DIR / ".greenhouse_session"
LOG_FILE    = cfg.BASE_DIR / "data" / "greenhouse_applied_log.json"
SCREENSHOTS = cfg.BASE_DIR / "screenshots"

for d in [SCREENSHOTS, DATA_DIR, SESSION_DIR]:
    d.mkdir(parents=True, exist_ok=True)
cfg.RESUMES_DIR.mkdir(parents=True, exist_ok=True)
cfg.COVER_DIR.mkdir(parents=True, exist_ok=True)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("pip install playwright && python -m playwright install chromium")

# ── Known Greenhouse selectors (legacy boards.greenhouse.io embed) ────────────
# These IDs have been stable on Greenhouse's own hosted "embed" form for years.
# The newer job-boards.greenhouse.io React UI does NOT use these IDs — for that
# UI, _smart_fill_greenhouse_fields()'s generic label-based filler is what
# actually does the work; these are tried first purely because they're free
# and exact when they do match.
GH = {
    "first_name":    "#first_name",
    "last_name":     "#last_name",
    "email":         "#email",
    "phone":         "#phone",
    "resume_file":   "#resume",
    "resume_file_s3":"#s3_upload_for_resume",
    "cover_file":    "#cover_letter",
    "submit_btn":    "#submit_app",
    "apply_link":    'a#apply_button, a[href="#app_body"], a:has-text("Apply for this job"), a:has-text("Apply Now")',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_eval(ctx, js, default=None):
    try:
        return ctx.evaluate(js)
    except Exception:
        return default

def _failure_shot(page, tag: str) -> str:
    """Screenshot the current page on any fill/submit failure."""
    try:
        safe = re.sub(r'[^a-zA-Z0-9_-]+', '_', tag)[:60]
        p = SCREENSHOTS / f"gh_fail_{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(p))
        print(f"          📸 Failure screenshot: screenshots/{p.name}")
        return str(p)
    except Exception as e:
        print(f"          ⚠  Could not capture screenshot: {e}")
        return ""

def _exists(page, sel, timeout=3000) -> bool:
    try:
        el = page.locator(sel).first
        return el.count() > 0 and el.is_visible(timeout=timeout)
    except Exception:
        return False

def _click(page, sel, timeout=8000) -> bool:
    """Click an element — never hangs. Falls back to a direct JS click on
    whatever element[from point] actually covers it. This is NOT bot evasion
    — it's a fix for styled overlay elements (e.g. a <label> or <div> sitting
    on top of the real control, common on custom "Attach" dropzone buttons)
    physically intercepting the click; same pattern already proven necessary
    on Workday (see workday_apply_now.py _click). No randomized delays here —
    Greenhouse guest-apply has no bot/fingerprint check to evade, so Playwright's
    own actionability auto-wait is used as-is instead of adding artificial
    "human-like" pacing."""
    try:
        el = page.locator(sel).first
        el.wait_for(state="visible", timeout=timeout)
        el.scroll_into_view_if_needed()
        try:
            el.click(timeout=4000)
        except Exception:
            page.evaluate(
                """(s) => {
                    const b = document.querySelector(s);
                    if (!b) return;
                    const r = b.getBoundingClientRect();
                    const top = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
                    (top || b).dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                }""",
                sel
            )
        return True
    except Exception:
        return False

def _wait_for_dom_stable(page, quiet_ms: int = 400, timeout_ms: int = 4000) -> int:
    """Wait until the DOM stops mutating (or timeout). Returns ms actually waited."""
    start = time.time()
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass
    return int((time.time() - start) * 1000)

def _dismiss_cookie_banner(page) -> bool:
    for sel in [
        'button:has-text("Accept")', 'button:has-text("Accept all")',
        'button:has-text("I agree")', 'button:has-text("Got it")',
        '[id*="cookie"] button', '[class*="cookie"] button',
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                btn.click(timeout=2000)
                time.sleep(0.3)
                return True
        except Exception:
            pass
    return False

# ── URL / filtering helpers ────────────────────────────────────────────────────

def is_greenhouse_url(url: str) -> bool:
    return "greenhouse.io" in (url or "")

def is_good_level(title: str, jd_text: str = "") -> bool:
    """Title-level seniority check, PLUS a body-level check for postings
    whose seniority is stated in the JD's own subtitle/overview but not in
    Greenhouse's title field — e.g. GitLab's "Forward Deployed Engineer -
    EMEA" listing has that exact string as its title, but the JD's own first
    line reads "Staff Forward Deployed Engineer, Agentic SDLC". Title-only
    SENIOR_WORDS matching misses that. Only the first 400 chars of the JD are
    scanned (the subtitle/overview area) — scanning the whole JD would
    false-positive on unrelated sentences like "you'll work alongside senior
    engineers" deep in the body. Added 2026-08-25 alongside the role-type and
    location fixes — see config.py's NEGATIVE_ROLE_TITLE_WORDS comment for
    the full context of that dry run."""
    if any(bad in title.lower() for bad in cfg.SENIOR_WORDS):
        return False
    if jd_text and any(bad in jd_text[:400].lower() for bad in cfg.SENIOR_WORDS):
        return False
    return True

def is_relevant_domain(title: str) -> bool:
    """Delegates to config.is_target_role_title() — the shared, word-boundary
    -safe role-type filter also used by linkedin_apply_now.py and
    workday_apply_now.py. This used to be a local DATA_KEYWORDS list of bare
    single words ("engineer", "ai", "sql", "python", "bi"...), which is why
    "Backend Engineer (Ruby)", "Fullstack Engineer (TypeScript)", "Forward
    Deployed Engineer", and "Customer Success Engineer" all passed this check
    in the 2026-08-25 dry run — a single word like "engineer" matches almost
    any tech title. See config.py for the actual matching logic."""
    return cfg.is_target_role_title(title)

# ── Log helpers ───────────────────────────────────────────────────────────────

def load_log():
    try:
        return json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
    except Exception:
        return []

def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2))

def already_applied(url: str, log: list, title="", company="") -> bool:
    key = re.sub(r'\?.*', '', url).rstrip("/")
    for e in log:
        if e.get("status") not in ("Applied", "Already Applied"):
            continue
        if re.sub(r'\?.*', '', e.get("url", "")).rstrip("/") == key:
            return True
        if title and company:
            if (e.get("company", "").lower().strip() == company.lower().strip()
                    and e.get("title", "").lower().strip() == title.lower().strip()):
                return True
    return False

# ── Job page extraction ────────────────────────────────────────────────────────

def extract_greenhouse_job(page) -> dict:
    """Best-effort title/company/description scrape. Works for both the
    legacy embed (div#header, div#content) and the modern job-boards React
    UI (falls back to the page's <h1> and full body text when those specific
    ids aren't present)."""
    info = _safe_eval(page, """
        () => {
            const h1 = document.querySelector('h1, .app-title, [class*="job-title"]');
            const title = h1 ? h1.innerText.trim() : (document.title || '').split(' at ')[0].trim();
            const companyEl = document.querySelector('.company-name, [class*="company-name"]');
            let company = companyEl ? companyEl.innerText.trim() : '';
            if (!company) {
                const m = (document.title || '').match(/ at (.+)$/);
                if (m) company = m[1].trim();
            }
            const contentEl = document.querySelector('#content, [class*="job-post"], main, article');
            const description = (contentEl ? contentEl.innerText : document.body.innerText || '').slice(0, 8000);
            return { title, company, description };
        }
    """, {}) or {}
    if not info.get("company"):
        # Fall back to the URL path segment: boards.greenhouse.io/COMPANY/jobs/ID
        m = re.search(r'greenhouse\.io/(?:embed/job_app\?for=)?([^/?]+)', page.url or "")
        if m:
            info["company"] = m.group(1).replace("-", " ").title()
    return info

# ── Apply entry point ──────────────────────────────────────────────────────────

def click_greenhouse_apply(page) -> bool:
    """Some postings show the form immediately; others require clicking an
    'Apply for this job' / 'Apply Now' link/button first. Returns True either
    way as long as the form is (now) visible."""
    if _exists(page, GH["first_name"], timeout=1500) or _form_fields_visible(page):
        return True
    if _exists(page, GH["apply_link"], timeout=3000):
        _click(page, GH["apply_link"])
        time.sleep(2)
        _wait_for_dom_stable(page)
    return _exists(page, GH["first_name"], timeout=3000) or _form_fields_visible(page)

def _form_fields_visible(page) -> bool:
    return bool(_safe_eval(page, """
        () => !!document.querySelector(
            'input[name*="first_name" i], input[id*="first_name" i], ' +
            'label:has(+ input)'
        ) || Array.from(document.querySelectorAll('label')).some(
            l => /first name/i.test(l.innerText)
        )
    """, False))

def _fill_basic_field(page, css_sel: str, label_words: list, value: str) -> bool:
    """Try the known Greenhouse id first, then fall back to matching any
    visible input whose <label for=...>/aria-label/placeholder text contains
    one of label_words (covers the React job-boards UI). Uses Playwright's
    plain .fill() — no randomized per-keystroke delay. There's no bot check
    on Greenhouse guest-apply to evade, so this is a direct, deterministic
    fill, not a "type like a human" simulation."""
    if not value:
        return False
    if _exists(page, css_sel, timeout=1200):
        try:
            page.locator(css_sel).first.fill(value)
            return True
        except Exception:
            pass
    sel = _safe_eval(page, """
        (words) => {
            function labelFor(inp) {
                if (inp.id) {
                    const l = document.querySelector('label[for="' + CSS.escape(inp.id) + '"]');
                    if (l) return l.innerText.toLowerCase();
                }
                return (inp.getAttribute('aria-label') || inp.getAttribute('placeholder') || '').toLowerCase();
            }
            for (const inp of document.querySelectorAll('input:not([type=hidden]):not([type=file])')) {
                if (!inp.offsetParent) continue;
                const lbl = labelFor(inp);
                if (words.some(w => lbl.includes(w))) {
                    if (!inp.id) inp.setAttribute('data-gh-tmp-id', 'gh_' + Math.random().toString(36).slice(2));
                    return inp.id ? ('#' + CSS.escape(inp.id)) : ('[data-gh-tmp-id="' + inp.getAttribute('data-gh-tmp-id') + '"]');
                }
            }
            return null;
        }
    """, None)
    if sel:
        try:
            page.locator(sel).first.fill(value)
            return True
        except Exception:
            return False
    return False

def upload_greenhouse_resume(page, resume_path: str) -> bool:
    """Resume upload: legacy #resume / #s3_upload_for_resume ids first, then
    the first visible file input whose surrounding text mentions resume/CV
    and NOT cover letter (covers the React dropzone UI, which hides a plain
    <input type=file> behind a styled 'Attach' control)."""
    if not resume_path or not Path(resume_path).exists():
        return False
    for sel in (GH["resume_file"], GH["resume_file_s3"]):
        try:
            inp = page.locator(sel).first
            if inp.count() > 0:
                inp.set_input_files(resume_path)
                return True
        except Exception:
            pass
    sel = _safe_eval(page, """
        () => {
            for (const inp of document.querySelectorAll('input[type=file]')) {
                const ctx = (inp.closest('div,section,fieldset')?.innerText || '').toLowerCase();
                if (ctx.includes('cover letter') && !ctx.includes('resume')) continue;
                if (!inp.id) inp.setAttribute('data-gh-tmp-id', 'gh_resume_' + Math.random().toString(36).slice(2));
                return inp.id ? ('#' + CSS.escape(inp.id)) : ('[data-gh-tmp-id="' + inp.getAttribute('data-gh-tmp-id') + '"]');
            }
            return null;
        }
    """, None)
    if sel:
        try:
            page.locator(sel).first.set_input_files(resume_path)
            return True
        except Exception:
            return False
    return False

# ── Generic question filler (cache layers, no live API — matches Raghav's
#    2026-07-14 decision on Workday: no anthropic calls for Q&A, ever) ────────

def _log_stuck_fields(fields: list, job_title: str, company: str,
                       status: str = "no cache/PROFILE_FALLBACK match — needs a manual answer "
                                     "added to qa_answers.py or PROFILE_FALLBACK") -> None:
    """Reuses the same data/stuck_questions.json shape as indeed/workday so
    there's one place to check for fields that need a manual answer added.
    `status` distinguishes WHY a field landed here — "genuinely unknown" vs
    "essay question, deliberately not auto-filled" are different situations
    and the log should say which."""
    if not fields:
        return
    try:
        stuck_file = cfg.BASE_DIR / "data" / "stuck_questions.json"
        stuck_file.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(stuck_file.read_text()) if stuck_file.exists() else []
        existing.append({
            "timestamp": datetime.now().isoformat(),
            "company": company, "job_title": job_title,
            "source": "greenhouse_apply_now._smart_fill_greenhouse_fields",
            "fields": [{"label": f.get("label", ""), "type": f.get("type", ""),
                        "options": f.get("options", [])} for f in fields],
            "status": status,
        })
        stuck_file.write_text(json.dumps(existing, indent=2))
        print(f"          📝 {len(fields)} field(s) → data/stuck_questions.json "
              f"({status}): {[f.get('label','') for f in fields]}")
    except Exception as e:
        print(f"          ⚠  Could not log stuck field(s): {e}")

# ── Field-type classifier ──────────────────────────────────────────────────
#
# WHY THIS EXISTS: the original version looked up an answer by label text
# alone and typed whatever it found into the field, with no check that the
# answer's SHAPE matched the QUESTION's TYPE. Confirmed real bugs from that:
# a yes/no question ("...applied in the last 6 months?") got a month name
# ("August") because qa_answers.py had a bare 5-char "month" key that
# partial-matched the word "months" inside a completely unrelated question
# (fixed separately in qa_answers.py — see _EXACT_ONLY_KEYS there) — and
# essay-type questions ("describe your approach to testing") were getting
# one-word cache hits that read as a bot answered them, because nothing
# distinguished "this needs a paragraph" from "this needs one word." Every
# field is now classified BEFORE any answer is looked up, and the category
# decides which answer sources are even allowed to apply.

_EEO_SIGNALS = (
    "gender", "ethnicity", "race", "hispanic", "latino", "latina",
    "veteran", "disability", "disabilit", "sexual orientation",
    "self-identif", "self identif", "protected class",
)

_ESSAY_SIGNALS = (
    "describe", "why do you", "why are you", "why anthropic", "why this",
    "why do you want", "tell us about", "walk us through", "approach to",
    "explain how", "explain your", "what interests you", "cover letter",
    "in your own words", "pitch us", "what makes you", "share a time",
    "give an example",
)

_YES_NO_OPTION_WORDS = {"yes", "no", "true", "false"}

_DECLINE_PHRASES = (
    "decline to self-identify", "decline to answer", "prefer not to answer",
    "i don't wish to answer", "do not wish to answer", "prefer not to say",
    "choose not to disclose",
)

def _classify_field(f: dict) -> str:
    """Returns one of: "eeo", "essay", "yes_no", "dropdown", "short_text".
    This decision is made from the field's real DOM type + its options +
    its label — never from what answer happens to be cached for it."""
    label = f.get("label", "").lower().strip()
    ftype = f.get("type", "")
    options = [str(o).strip().lower() for o in f.get("options", [])]

    if any(s in label for s in _EEO_SIGNALS):
        return "eeo"

    if ftype == "textarea" or any(s in label for s in _ESSAY_SIGNALS) or len(label) > 90:
        return "essay"

    if options and len(options) <= 3 and all(o in _YES_NO_OPTION_WORDS for o in options):
        return "yes_no"

    if ftype == "select" and options:
        return "dropdown"

    # A yes/no-phrased question Greenhouse rendered as a plain text/number
    # field instead of a radio group (seen on some companies' custom
    # questions) — still route as yes_no so the answer gets shape-validated
    # before typing, instead of accepting whatever a label-based cache hit
    # happened to return.
    if ftype in ("text", "number") and re.match(
        r'^(are|do|does|is|have|has|will|can|would|did)\b', label
    ):
        return "yes_no"

    return "short_text"

def _looks_yes_no_shaped(answer) -> bool:
    """Does this answer actually look like a yes/no response? Used to reject
    a cache hit that's the WRONG SHAPE for a yes/no field (a month name, a
    country name, a number) instead of typing it in anyway."""
    a = str(answer or "").strip().lower()
    if not a:
        return False
    if a in ("yes", "no", "y", "n", "true", "false"):
        return True
    # Allow short explanatory sentences that still clearly START with a
    # yes/no (e.g. qa_answers.py's W2 answer: "Yes, I am able to work on W2...")
    return a.startswith("yes") or a.startswith("no,") or a.startswith("no ")

def _previously_applied_to_company(company: str) -> bool:
    """Ground truth from THIS pipeline's own apply log — not a guess. Only
    knows about applications made through this pipeline; a False here means
    "not in our records", not an absolute guarantee about every application
    ever made anywhere."""
    if not company:
        return False
    c = company.lower().strip()
    for e in load_log():
        if e.get("status") == "Applied" and e.get("company", "").lower().strip() == c:
            return True
    return False

def _known_factual_yes_no(label: str, company: str) -> str | None:
    """Returns the TRUE 'Yes'/'No' for a fixed set of facts this pipeline
    actually knows — from config.py, raghav_profile.py, or its own apply
    log — never a guess. Returns None for anything outside this list, and
    the caller must NOT invent an answer when this returns None; it falls
    through to the cache layers (still shape-validated) or to
    stuck_questions.json."""
    l = label.lower().strip()

    if any(p in l for p in ("authorized to work", "legally authorized", "eligible to work in")):
        return "Yes" if getattr(cfg, "AUTHORIZED_TO_WORK_NOW", True) else "No"

    if any(p in l for p in ("require sponsorship", "need sponsorship", "visa sponsorship",
                              "sponsorship now or in the future", "require visa",
                              "require any kind of visa")):
        return "Yes" if getattr(cfg, "REQUIRES_SPONSORSHIP", False) else "No"

    if "relocat" in l:
        relocate = bool(rp.PROFILE.get("relocate", False)) if rp else False
        return "Yes" if relocate else "No"

    if "applied" in l and any(p in l for p in ("before", "previously", "prior", "months", "past")):
        return "Yes" if _previously_applied_to_company(company) else "No"

    # "Do you have N+ years of <skill> experience?" — answered HONESTLY
    # against the real years-of-experience figures, never a blind "Yes".
    m = re.search(r'(\d+)\+?\s*years?', l)
    if m and "year" in l and ("experience" in l or "worked with" in l or "working with" in l):
        threshold = float(m.group(1))
        # raghav_profile.SKILL_YEARS is canonical — cfg.SKILL_YEARS is a
        # stale, less-complete duplicate (see config.py's SKILL_YEARS
        # comment). Fixed 2026-08-25.
        skill_years = getattr(rp, "SKILL_YEARS", {}) if rp else {}
        actual_years = None
        for skill, yrs in skill_years.items():
            if skill in l:
                try:
                    actual_years = float(yrs)
                except (TypeError, ValueError):
                    pass
                break
        if actual_years is None:
            actual_years = float(getattr(cfg, "YEARS_EXPERIENCE", 3))
        return "Yes" if actual_years >= threshold else "No"

    return None

def _smart_fill_greenhouse_fields(page, job_title: str, company: str, jd_text: str) -> int:
    """Fills every remaining labeled field on the form: Greenhouse custom
    questions + the standard EEO/voluntary-disclosure block. Ported from
    workday_apply_now.py's _smart_fill_questions — that function is already
    selector-agnostic (label[for] / aria-label / placeholder / fieldset+legend
    for radio-checkbox groups), which is exactly what Greenhouse's standard
    HTML forms use, both the legacy embed and the React job-boards UI."""
    fields = _safe_eval(page, r"""
        () => {
            function getLabel(el) {
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                    if (lbl) return lbl.innerText.trim().split('\n')[0];
                }
                const al = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
                if (al) return al;
                const c = el.closest('fieldset, [role="group"], .field, [class*="question"]');
                if (c) {
                    const h = c.querySelector('label, legend');
                    if (h) return h.innerText.trim().split('\n')[0];
                }
                return el.name || el.id || '';
            }
            function uniqueSel(el) {
                if (el.id) return '#' + CSS.escape(el.id);
                if (el.name) return el.tagName.toLowerCase() + '[name="' + CSS.escape(el.name) + '"]';
                el.setAttribute('data-gh-tmp-id', 'gh_' + Math.random().toString(36).slice(2));
                return '[data-gh-tmp-id="' + el.getAttribute('data-gh-tmp-id') + '"]';
            }
            const KNOWN = new Set(['first_name','last_name','email','phone']);
            const results = [];
            const seen = {};
            for (const inp of document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file])' +
                ':not([type=radio]):not([type=checkbox]), select, textarea'
            )) {
                if (!inp.offsetParent) continue;
                if (KNOWN.has(inp.id) || KNOWN.has(inp.name)) continue;
                const type = inp.tagName === 'SELECT' ? 'select'
                    : inp.tagName === 'TEXTAREA' ? 'textarea'
                    : (inp.getAttribute('type') || 'text').toLowerCase();
                if (type === 'select') {
                    const opt = inp.options[inp.selectedIndex];
                    if (inp.value && opt && opt.text.trim() && !/^(select|choose|--)/i.test(opt.text.trim())) continue;
                } else if (inp.value && inp.value.trim()) {
                    continue;
                }
                const lbl = getLabel(inp);
                if (!lbl || seen[lbl.toLowerCase()]) continue;
                seen[lbl.toLowerCase()] = true;
                const opts = type === 'select'
                    ? Array.from(inp.options).map(o => o.text.trim()).filter(o => o && o !== '--')
                    : [];
                results.push({ label: lbl, type, options: opts, sel: uniqueSel(inp) });
            }
            const groups = {};
            const skippedGroups = new Set();
            for (const inp of document.querySelectorAll('input[type=radio],input[type=checkbox]')) {
                if (!inp.offsetParent) continue;
                const gname = inp.name || '';
                if (!gname || groups[gname] || skippedGroups.has(gname)) continue;
                const already = Array.from(document.querySelectorAll('input[name="'+CSS.escape(gname)+'"]')).some(r => r.checked);
                if (already) { skippedGroups.add(gname); continue; }
                const fs = inp.closest('fieldset,[role="group"],.field');
                let lbl = gname;
                if (fs) {
                    const leg = fs.querySelector('legend,label');
                    if (leg) lbl = leg.innerText.trim().split('\n')[0];
                }
                if (!seen[lbl.toLowerCase()]) {
                    seen[lbl.toLowerCase()] = true;
                    const opts = Array.from(document.querySelectorAll('input[name="'+CSS.escape(gname)+'"]'))
                        .map(r => {
                            const le = document.querySelector('label[for="'+CSS.escape(r.id)+'"]');
                            return le ? le.innerText.trim() : r.value;
                        }).filter(Boolean);
                    groups[gname] = { label: lbl, type: inp.type, options: opts, gname };
                }
            }
            for (const k in groups) results.push(groups[k]);
            return results;
        }
    """, []) or []

    if not fields:
        return 0
    print(f"          📋 {len(fields)} field(s) to fill (custom questions + EEO)")

    _smart_salary = _pick_salary(jd_text, job_title)
    # Defense-in-depth only — the primary answer for every fact below now
    # comes from _known_factual_yes_no() (config.py-backed) BEFORE this dict
    # is ever consulted, for anything classified "yes_no". This dict is what
    # a "short_text"-classified field falls back to if no cache layer has an
    # answer, so a couple of these keys (sponsorship, relocat) are kept as a
    # safety net in case a field with that meaning doesn't get picked up by
    # the yes_no classifier for some reason — pulled from the same config
    # constants, never a separate hardcoded literal.
    PROFILE_FALLBACK = {
        "work authorization": "Yes" if getattr(cfg, "AUTHORIZED_TO_WORK_NOW", True) else "No",
        "authorized to work": "Yes" if getattr(cfg, "AUTHORIZED_TO_WORK_NOW", True) else "No",
        "legally authorized": "Yes" if getattr(cfg, "AUTHORIZED_TO_WORK_NOW", True) else "No",
        "sponsorship": "Yes" if getattr(cfg, "REQUIRES_SPONSORSHIP", False) else "No",
        "visa": "F-1 STEM OPT",
        "salary": _smart_salary, "compensation": _smart_salary, "hourly rate": "40",
        "earliest start date": getattr(cfg, "EARLIEST_START_DATE", "Immediately"),
        "earliest available start date": getattr(cfg, "EARLIEST_START_DATE", "Immediately"),
        "when can you start": getattr(cfg, "EARLIEST_START_DATE", "Immediately"),
        "available to start": getattr(cfg, "EARLIEST_START_DATE", "Immediately"),
        "notice period": "2 weeks",
        "relocat": "Yes" if (rp and rp.PROFILE.get("relocate", False)) else "No",
        "remote": "Yes",
        "years of experience": str(getattr(cfg, "YEARS_EXPERIENCE", 3)),
        "background check": "Yes", "drug test": "Yes", "18 or older": "Yes",
        "us citizen": "No", "green card": "No", "permanent resident": "No",
        "linkedin": "https://www.linkedin.com/in/yourusername",
        "github": "https://github.com/raghava0071",
        "how did you hear": "LinkedIn / Online Job Board",
        "pronoun": "He/Him",
    }

    answers, uncached, essays, eeo_skipped = {}, [], [], []

    for f in fields:
        lbl = f.get("label", "")
        lbl_l = lbl.lower().strip().rstrip(" *:?")
        category = _classify_field(f)

        # ── EEO / self-identification — never auto-filled ────────────────────
        if category == "eeo":
            options = f.get("options", [])
            decline_opt = next(
                (o for o in options if any(p in str(o).lower() for p in _DECLINE_PHRASES)), None
            )
            if decline_opt:
                answers[lbl] = decline_opt
                print(f"             ⏭  EEO      '{lbl}' → '{decline_opt}' "
                      f"(the form's own decline-to-answer option, not a guess)")
            else:
                eeo_skipped.append(f)
                print(f"             ⏭  EEO      '{lbl}' — left blank (self-identification, "
                      f"no decline option found on this form)")
            continue

        # ── Essay / open-ended pitch questions — never auto-filled ───────────
        if category == "essay":
            essays.append(f)
            print(f"             📝 ESSAY    '{lbl}' — needs YOUR real answer, "
                  f"not auto-filled (routing to stuck_questions.json)")
            continue

        # ── Yes/No — real fact first, then a SHAPE-VALIDATED cache lookup ────
        if category == "yes_no":
            fact = _known_factual_yes_no(lbl, company)
            if fact is not None:
                answers[lbl] = fact
                print(f"             ✔ FACT    '{lbl}' → '{fact}' (config.py, not a guess)")
                continue

            found_valid = False
            for source_name, source_val in (
                ("QA",    _qa.get_answer(lbl) if (_qa and lbl) else None),
                ("SAVED", _claude_ans.get(lbl) if (_claude_ans and lbl) else None),
                ("CACHE", _cache.get(lbl) if lbl else None),
            ):
                if source_val is None:
                    continue
                if _looks_yes_no_shaped(source_val):
                    answers[lbl] = source_val
                    print(f"             ✔ {source_name:<6}  '{lbl}' → '{str(source_val)[:60]}'")
                    found_valid = True
                else:
                    print(f"             ⚠  {source_name} had '{str(source_val)[:40]}' for a yes/no "
                          f"question '{lbl}' — wrong shape, discarding instead of using it")
                break  # only trust the first source that actually returned something
            if found_valid:
                continue

            # ── Layer B — Claude API fallback, strictly profile-grounded ──────
            # Only reached once real facts (_known_factual_yes_no) and every
            # cache layer (QA — which itself already tried
            # profile_answers.answer_from_profile() as a fallthrough — SAVED,
            # CACHE) have all come back empty. Grounded ONLY in
            # raghav_profile.py facts; must say UNKNOWN (-> None here) rather
            # than guess. See profile_answers.py's HARD TRUTHFULNESS RULE.
            try:
                import profile_answers as _pa
                claude_fact = _pa.answer_via_claude_fallback(lbl, field_type="yes_no")
            except Exception as e:
                claude_fact = None
                print(f"             ⚠  Claude fallback errored for '{lbl}': {e}")
            if claude_fact is not None and _looks_yes_no_shaped(claude_fact):
                answers[lbl] = claude_fact
                print(f"             ✔ CLAUDE  '{lbl}' → '{claude_fact}' (API, profile-grounded, not a guess)")
                if _claude_ans:
                    _claude_ans.save(lbl, claude_fact)
                continue

            uncached.append(f)
            continue

        # ── Dropdown — answer must match one of the form's REAL options ──────
        if category == "dropdown":
            options_l = [str(o).lower() for o in f.get("options", [])]
            resolved = None
            for source_val in (
                _qa.get_answer(lbl) if (_qa and lbl) else None,
                _claude_ans.get(lbl) if (_claude_ans and lbl) else None,
                _cache.get(lbl) if lbl else None,
            ):
                if source_val and any(str(source_val).lower() in o or o in str(source_val).lower()
                                       for o in options_l):
                    resolved = source_val
                    break
            if resolved is None:
                for kw, val in sorted(PROFILE_FALLBACK.items(), key=lambda kv: -len(kv[0])):
                    if kw in lbl_l and any(str(val).lower() in o or o in str(val).lower() for o in options_l):
                        resolved = val
                        break
            if resolved is not None:
                answers[lbl] = resolved
                print(f"             ✔ DROPDOWN '{lbl}' → '{resolved}' (matches a real option)")
                if _claude_ans:
                    _claude_ans.save(lbl, resolved)
                continue

            # ── Layer B — Claude API fallback, constrained to the form's
            # real options. answer_via_claude_fallback() only accepts an
            # answer that exactly matches one of `options` (or UNKNOWN ->
            # None) — never a free-form guess dressed up as a selection.
            try:
                import profile_answers as _pa
                claude_dd = _pa.answer_via_claude_fallback(
                    lbl, field_type="dropdown", options=f.get("options", [])
                )
            except Exception as e:
                claude_dd = None
                print(f"             ⚠  Claude fallback errored for '{lbl}': {e}")
            if claude_dd is not None:
                answers[lbl] = claude_dd
                print(f"             ✔ CLAUDE  '{lbl}' → '{claude_dd}' (API, profile-grounded, matches a real option)")
                if _claude_ans:
                    _claude_ans.save(lbl, claude_dd)
                continue

            uncached.append(f)
            continue

        # ── Short text — normal cache chain, longest-key-wins fallback ───────
        qa = _qa.get_answer(lbl) if (_qa and lbl) else None
        if qa is not None:
            answers[lbl] = qa
            print(f"             ✔ QA     '{lbl}' → '{str(qa)[:60]}'")
            continue

        ca = _claude_ans.get(lbl) if (_claude_ans and lbl) else None
        if ca is not None:
            answers[lbl] = ca
            print(f"             ✔ SAVED  '{lbl}' → '{str(ca)[:60]}'")
            continue

        cached = _cache.get(lbl) if lbl else None
        if cached is not None:
            answers[lbl] = cached
            print(f"             ✔ CACHE  '{lbl}' → '{str(cached)[:60]}'")
            if _claude_ans:
                _claude_ans.save(lbl, cached)
            continue

        # Longest matching key wins (same principle as qa_answers.get_answer)
        # instead of first-in-dict-order — avoids a short generic key
        # shadowing a more specific one purely because of dict ordering.
        fallback_matches = [(kw, val) for kw, val in PROFILE_FALLBACK.items() if kw in lbl_l]
        if fallback_matches:
            kw, val = max(fallback_matches, key=lambda kv: len(kv[0]))
            answers[lbl] = val
            print(f"             ✔ PROFILE '{lbl}' → '{val}'")
            if _claude_ans:
                _claude_ans.save(lbl, val)
            continue

        # ── Layer B — Claude API fallback, strictly profile-grounded ─────────
        # Only reached once QA (which already tried
        # profile_answers.answer_from_profile() as a fallthrough), SAVED,
        # CACHE, and PROFILE_FALLBACK have all come back empty.
        try:
            import profile_answers as _pa
            claude_txt = _pa.answer_via_claude_fallback(lbl, field_type="short_text")
        except Exception as e:
            claude_txt = None
            print(f"             ⚠  Claude fallback errored for '{lbl}': {e}")
        if claude_txt is not None:
            answers[lbl] = claude_txt
            print(f"             ✔ CLAUDE  '{lbl}' → '{claude_txt}' (API, profile-grounded, not a guess)")
            if _claude_ans:
                _claude_ans.save(lbl, claude_txt)
            continue

        uncached.append(f)

    if essays:
        _log_stuck_fields(essays, job_title, company,
                           status="ESSAY / open-ended question — deliberately NOT auto-filled; "
                                  "write your own real answer here")
    if eeo_skipped:
        _log_stuck_fields(eeo_skipped, job_title, company,
                           status="EEO/self-identification — intentionally left blank, no "
                                  "decline-to-answer option was found on this form to select")
    if uncached:
        _log_stuck_fields(uncached, job_title, company)

    filled = _safe_eval(page, """
        (itemsJson) => {
            const items = JSON.parse(itemsJson);
            let filled = 0;
            function fire(el) {
                ['input','change','blur'].forEach(ev => el.dispatchEvent(new Event(ev, {bubbles:true})));
            }
            for (const item of items) {
                if (!item.answer) continue;
                const ans = item.answer;
                if (item.type === 'radio' || item.type === 'checkbox') {
                    const opts = item.gname ? Array.from(document.querySelectorAll('input[name="'+CSS.escape(item.gname)+'"]')) : [];
                    const ansL = ans.toLowerCase().trim();
                    for (const opt of opts) {
                        let lbl = '';
                        const le = document.querySelector('label[for="'+CSS.escape(opt.id)+'"]');
                        if (le) lbl = le.innerText.toLowerCase().trim();
                        const val = (opt.value||'').toLowerCase();
                        if (val === ansL || lbl === ansL || val.includes(ansL) || ansL.includes(val) ||
                            (lbl && (lbl.includes(ansL) || ansL.includes(lbl)))) {
                            if (!opt.checked) { opt.click(); fire(opt); }
                            filled++; break;
                        }
                    }
                    continue;
                }
                const el = item.sel ? document.querySelector(item.sel) : null;
                if (!el) continue;
                if (el.tagName === 'SELECT') {
                    const match = Array.from(el.options).find(o =>
                        o.text.trim() === ans || o.text.toLowerCase().includes(ans.toLowerCase()));
                    if (match) { el.value = match.value; fire(el); filled++; }
                } else {
                    try {
                        const tag = el.tagName.toUpperCase();
                        const proto = tag === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const ns = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (ns && ns.set) ns.set.call(el, ans); else el.value = ans;
                    } catch(e) { try { el.value = ans; } catch(e2) {} }
                    fire(el); filled++;
                }
            }
            return filled;
        }
    """, json.dumps([{
        "sel": f.get("sel", ""), "type": f.get("type", "text"),
        "gname": f.get("gname", ""), "answer": str(answers.get(f.get("label", ""), ""))
    } for f in fields])) or 0

    return filled

# ── Submit ──────────────────────────────────────────────────────────────────
#
# IMPORTANT — this is the exact bug class Workday hit at v1.8.1: a submit
# click that fires with no exception was being treated as success, when in
# some cases the click landed on nothing (covered element, disabled button,
# handler silently rejected) and the page never actually changed. Workday's
# fix (_submit_progressed()) was to require the page's own state to visibly
# change — not just "the click call didn't raise". _submit_progressed() below
# is the same fix, ported: it snapshots the page (URL + whether the form/
# submit button is still there) BEFORE the click, then only reports success
# if that snapshot is provably different afterward, or explicit confirmation
# text/URL shows up. "The click didn't throw" is never, by itself, a success
# condition anywhere in this function.

def _is_confirmed(page) -> bool:
    """Explicit, positive confirmation signals — safe to trust on their own
    even without a before/after diff, since this text/URL pattern is not
    something a not-yet-submitted Greenhouse job page would ever show."""
    body = (_safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or "")
    url = (page.url or "").lower()
    return any(s in body for s in [
        "thanks for applying", "thank you for applying",
        "your application has been submitted", "application was sent",
    ]) or any(s in url for s in ["confirmation", "thank-you", "thanks"])

def _page_signature(page) -> dict:
    """Snapshot used to detect real progress: current URL + whether the
    submit button/application form is still present on the page."""
    return {
        "url": page.url or "",
        "form_present": _exists(page, GH["submit_btn"], timeout=300) or _form_fields_visible(page),
    }

def _submit_progressed(page, before: dict, timeout_s: float = 6.0) -> bool:
    """Poll for up to timeout_s after a submit click. Only returns True if
    the page's state actually changed relative to `before`:
      - explicit confirmation text/URL appears (_is_confirmed), OR
      - the form/submit button that WAS present before the click is now gone
        AND the URL moved (a same-page validation-error re-render can also
        make a button briefly disappear/reappear, so both signals are
        required together, not just one).
    Returns False — meaning "not proven submitted" — if none of that happens,
    even if the click() call itself reported no error.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _is_confirmed(page):
            return True
        after = _page_signature(page)
        real_navigation = after["url"] != before["url"]
        form_disappeared = before["form_present"] and not after["form_present"]
        if real_navigation and form_disappeared:
            return True
        time.sleep(0.4)
    return False

def submit_greenhouse_application(page, dry_run: bool = False) -> bool:
    print(f"          📋 Review & Submit")
    if dry_run:
        print(f"          🏁 DRY RUN — stopping BEFORE the Submit click. Nothing below this "
              f"line runs in dry-run mode.")
        return True

    before = _page_signature(page)
    submit_sel = GH["submit_btn"] if _exists(page, GH["submit_btn"], timeout=2000) else None
    for attempt in range(1, 4):
        print(f"          🚀 Submit attempt {attempt}/3...")
        clicked = _click(page, submit_sel) if submit_sel else False
        if not clicked:
            try:
                page.get_by_role("button", name=re.compile("submit application", re.I)).first.click(timeout=4000)
                clicked = True
            except Exception:
                pass

        if _submit_progressed(page, before):
            print(f"          🎉 Application submitted — verified: page state actually "
                  f"changed (confirmation text/URL, and the form is gone), not just a click "
                  f"that didn't error")
            return True
        print(f"          ⚠  Submit click fired but page state did not change — not counted as success")

        body = _safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or ""
        captcha = any("bframe" in (f.url or "") for f in list(page.frames)) or \
                  any(s in body for s in ["captcha", "i'm not a robot", "verify you are human"])
        if captcha:
            print(f"\n          🚨🚨 CAPTCHA DETECTED 🚨🚨")
            print(f"          👉 Please SOLVE THE CAPTCHA MANUALLY in the open browser window.")
            print(f"          ⏸  Waiting 60 seconds for you to solve it, then continuing...")
            try:
                notifier.send_alert(subject="🚨 Greenhouse CAPTCHA — solve manually",
                                     body="Solve the CAPTCHA in the open browser window within 60s.")
            except Exception:
                pass
            time.sleep(60)

    _failure_shot(page, "submit_not_confirmed")
    return False

# ── Full apply flow ─────────────────────────────────────────────────────────

def apply_to_greenhouse_job(page, job: dict, resume_path: str, cover_letter_path: str,
                             dry_run: bool = False):
    """Guest-apply flow, single page. Returns (success: bool, reason: str)."""
    title   = job.get("title", "")
    company = job.get("company", "")
    job_url = job.get("url", "")
    jd_text = job.get("description", "")

    print(f"          🌐 {job_url[:70]}")
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(2)
    except Exception as e:
        return False, f"navigation failed: {e}"

    _dismiss_cookie_banner(page)

    if not title or not jd_text:
        info = extract_greenhouse_job(page)
        title   = title   or info.get("title", "")
        company = company or info.get("company", "")
        jd_text = jd_text or info.get("description", "")

    print(f"          👆 Opening application form...")
    if not click_greenhouse_apply(page):
        _failure_shot(page, f"no_apply_form_{company}")
        return False, "could not find/open the application form"
    time.sleep(1)
    _wait_for_dom_stable(page)

    filled_first = _fill_basic_field(page, GH["first_name"], ["first name"], "Raghavendra")
    filled_last  = _fill_basic_field(page, GH["last_name"],  ["last name"],  "Karanam")
    filled_email = _fill_basic_field(page, GH["email"],      ["email"],      cfg.CANDIDATE_EMAIL)
    filled_phone = _fill_basic_field(page, GH["phone"],      ["phone"],      cfg.CANDIDATE_PHONE)
    if not (filled_first and filled_last and filled_email):
        print(f"          ⚠  Could not fill all basic fields "
              f"(first={filled_first} last={filled_last} email={filled_email} phone={filled_phone})")

    uploaded = upload_greenhouse_resume(page, resume_path)
    print(f"          {'✅' if uploaded else '⚠ '} Resume upload: {'attached' if uploaded else 'no upload field found — will need manual attach'}")
    time.sleep(1)

    _smart_fill_greenhouse_fields(page, title, company, jd_text)
    time.sleep(1)

    ok = submit_greenhouse_application(page, dry_run=dry_run)
    if ok:
        return True, "Dry-Run" if dry_run else "confirmed after submit click"
    return False, "submit not confirmed (see failure screenshot)"

# ── Discovery: Greenhouse public Job Board API ─────────────────────────────────
#
# https://developers.greenhouse.io/job-board.html — plain, unauthenticated,
# documented JSON API every embeddable Greenhouse careers widget uses to pull
# its own listing. This is the intended, sanctioned way to read a company's
# postings programmatically: no login, no HTML scraping, no rate-limit dance,
# and nothing here is trying to look like a browser or evade detection — it's
# a GET request to an API meant to answer exactly this GET request.

GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"

def _strip_html(raw_html: str) -> str:
    """Greenhouse's `content` field is the company's own HTML-formatted job
    description. jd_parser's keyword/skills matching works on word-boundary
    regexes over plain text, so tags just need to become whitespace/newlines
    — no need for a full HTML-parsing dependency for that."""
    if not raw_html:
        return ""
    text = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', raw_html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|li|div|h[1-6])>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()

def _fetch_greenhouse_jobs_for_company(company_token: str) -> list:
    """One GET per company, `content=true` so the full HTML job description
    comes back in the same response — no separate per-job detail call needed.
    Returns [] (with a printed reason) on any failure instead of raising, so
    one bad/retired token in config.GREENHOUSE_COMPANIES doesn't stop the
    other companies in the list from being checked."""
    url = f"{GREENHOUSE_API_BASE}/{company_token}/jobs"
    try:
        resp = requests.get(url, params={"content": "true"}, timeout=15)
    except requests.RequestException as e:
        print(f"  ⚠  {company_token}: API request failed ({e})")
        return []

    if resp.status_code == 404:
        print(f"  ⚠  {company_token}: no Greenhouse board at this token (HTTP 404) — "
              f"check the slug in config.GREENHOUSE_COMPANIES")
        return []
    if resp.status_code != 200:
        print(f"  ⚠  {company_token}: API returned HTTP {resp.status_code}")
        return []

    try:
        data = resp.json()
    except ValueError:
        print(f"  ⚠  {company_token}: API returned non-JSON response")
        return []

    company_name = company_token.replace("-", " ").replace("_", " ").title()
    jobs = []
    for j in data.get("jobs", []):
        title = (j.get("title") or "").strip()
        absolute_url = j.get("absolute_url") or ""
        if not title or not absolute_url:
            continue
        location = ((j.get("location") or {}).get("name") or "").strip()
        jobs.append({
            "title":       title,
            "company":     company_name,
            "url":         absolute_url,
            "description": _strip_html(j.get("content") or ""),
            "location":    location,
            "job_id":      j.get("id"),
        })

    print(f"  🔍 Greenhouse API: {len(jobs)} live posting(s) for '{company_token}'")
    return jobs

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--url",     type=str, default=None,
                         help="Test one exact Greenhouse job URL directly — skips API-based "
                              "discovery and the fit-score gate entirely, goes straight to "
                              "apply_to_greenhouse_job() for this one posting. Still fully respects "
                              "--dry-run (stops before the Submit click, same as the normal path).")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Greenhouse Apply Engine  {'[DRY RUN]' if args.dry_run else ''}")
    print(f"  Limit: {args.limit} applications")
    print(f"{'='*60}\n")

    import raghav_profile as rp
    import claude_engine  as ce
    import resume_builder as rb
    import cover_letter   as cl_mod
    import jd_parser      as jdp
    try:
        from staffing_filter import is_staffing_or_consultancy
    except ImportError:
        is_staffing_or_consultancy = lambda c, d="": (False, "")

    full_profile    = rp.PROFILE
    profile_summary = ce.build_profile_summary(full_profile)

    log     = load_log()
    applied = 0
    scored  = 0
    skipped = 0
    seen    = set()

    # ── Clear stale Chromium SingletonLock — see workday_apply_now.py main()
    # for why this exact check exists (leftover lock from a session that
    # didn't exit cleanly crashes the NEXT launch with a confusing error). ──
    def _lock_owner_pid(lock_path):
        try:
            target = os.readlink(str(lock_path))
        except OSError:
            try:
                target = lock_path.read_text()
            except Exception:
                return None
        m = re.search(r'-(\d+)\s*$', target.strip())
        return int(m.group(1)) if m else None

    def _pid_alive(pid):
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    _session_busy = False
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = SESSION_DIR / lock_name
        if lock_path.exists() or lock_path.is_symlink():
            owner_pid = _lock_owner_pid(lock_path)
            if owner_pid and _pid_alive(owner_pid):
                print(f"  🚫 {lock_name} held by a still-running process (PID {owner_pid}) "
                      f"— another Greenhouse session already has this browser profile open.")
                _session_busy = True
                continue
            try:
                lock_path.unlink()
                print(f"  🔓 Cleared stale {lock_name} — prior session didn't exit cleanly")
            except Exception as le:
                print(f"  ⚠  Could not clear {lock_name}: {le}")
    if _session_busy:
        print("  ❌ Skipping this Greenhouse run — close the other session first, then run again.")
        return

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                channel="chrome",
                viewport={"width": 1366, "height": 900},
                timeout=60000,
            )
            print("  🌐  Using real Google Chrome (channel=chrome) — for launch stability, "
                  "not evasion; no automation-hiding flags are set")
        except Exception as chrome_err:
            print(f"  ⚠  Real Chrome not available ({str(chrome_err)[:80]}) — falling back to bundled Chromium")
            browser = pw.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                viewport={"width": 1366, "height": 900},
                timeout=60000,
            )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # ── --url override: one exact posting, no discovery, no score gate ──
        # Skips API-based discovery entirely and every pre-apply filter (senior/
        # domain/blocked-company/staffing/already-applied/fit-score) — those
        # all exist to pick WHICH jobs to apply to, and here you're telling it
        # exactly which job. Still builds a real tailored resume/cover letter
        # (so the resume-upload step has an actual file to test) and still
        # calls the exact same apply_to_greenhouse_job() the normal discovery
        # path uses, with dry_run passed straight through — --dry-run stops
        # it before the Submit click exactly like every other path.
        if args.url:
            print(f"  🎯 --url override: testing exactly one posting — "
                  f"skipping API-based discovery and the fit-score gate\n")
            job = {"title": "", "company": "", "url": args.url, "description": ""}
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=25000)
                time.sleep(2)
                info = extract_greenhouse_job(page)
                job["title"]       = info.get("title", "")
                job["company"]     = info.get("company", "")
                job["description"] = info.get("description", "")
            except Exception as e:
                print(f"  ⚠  Could not load the job page to extract title/company/JD: {e}")

            print(f"  📋 {job['company'] or '(unknown company)'} — {job['title'] or '(unknown title)'}")

            resume_path = ""
            try:
                parsed = jdp.parse_jd(job["description"], job["title"])
                res = rb.build_resume(
                    job_title=job["title"], company=job["company"],
                    jd_keywords=parsed.get("jd_keywords", []),
                    injectable_kws=parsed.get("injectable_keywords", []),
                    initial_score=parsed.get("initial_score", 0),
                    optimized_score=parsed.get("optimized_score", 0),
                    jd_text=job["description"],
                    profile_summary=full_profile.get("summary", ""),
                )
                resume_path = res[0] if isinstance(res, tuple) else str(res)
                print(f"  ✅ Resume: {Path(resume_path).name}")
            except Exception as e:
                print(f"  ⚠  Resume build failed ({e}) — continuing with no resume file so you can "
                      f"still see the rest of the form-fill behavior; the upload step will report "
                      f"'no upload field found' or fail, which is expected with no file to attach.")

            cover_path = ""
            try:
                cl_text = ce.write_cover_letter(
                    full_profile.get("name", "Your Name"),
                    profile_summary, job["description"], job["title"], job["company"]
                )
                cover_path = cl_mod.save_cover_letter(cl_text, job["title"], job["company"])
            except Exception:
                pass

            print(f"  🚀 Running apply_to_greenhouse_job() "
                  f"({'DRY RUN — will stop before Submit' if args.dry_run else '⚠️  LIVE — will submit for real'})...")
            try:
                success, reason = apply_to_greenhouse_job(page, job, resume_path, cover_path, dry_run=args.dry_run)
            except Exception as e:
                success, reason = False, str(e)

            status = "Dry-Run" if args.dry_run else ("Applied" if success else "Failed")
            print(f"\n  {'✅' if success else '❌'} {status}: {reason}")

            browser.close()
            return

        def _process_job(job):
            nonlocal applied, scored, skipped
            title   = job.get("title", "")
            company = job.get("company", "")
            url     = job.get("url", "")
            jd      = job.get("description", "")

            if not url or not is_greenhouse_url(url):
                return
            sk = f"{company.lower().strip()}|{title.lower().strip()}"
            if sk in seen:
                return
            seen.add(sk)
            if title and (not is_good_level(title, jd) or not is_relevant_domain(title)):
                skipped += 1; return

            location = job.get("location", "")
            if not cfg.is_us_location(location):
                print(f"  🚫 Non-US location ({location or 'unknown'}) — skipping: {title} @ {company}")
                skipped += 1; return

            blocked = getattr(cfg, "BLOCKED_COMPANIES", set())
            if any(b in company.lower() or b in url.lower() for b in blocked):
                print(f"  🚫 Blocked company — skipping: {company}")
                skipped += 1; return

            staffing, s_reason = is_staffing_or_consultancy(company, jd)
            if staffing:
                print(f"  ⏭  SKIP staffing/consultancy ({s_reason}): {company}")
                skipped += 1; return

            if already_applied(url, log, title, company):
                print(f"  ↩  Already applied: {company} — {title}")
                skipped += 1; return

            if not jd:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(2)
                    info = extract_greenhouse_job(page)
                    title   = title   or info.get("title", "")
                    company = company or info.get("company", "")
                    jd      = info.get("description", "")
                    job.update({"title": title, "company": company, "description": jd})
                except Exception as e:
                    print(f"  ⚠  Could not load job: {e}")

            print(f"\n  📋 {company} — {title}")

            if getattr(cfg, "USE_CLAUDE_SCORING", True):
                result = ce.score_fit(profile_summary, jd, title, company)
            else:
                result = jdp.ats_fit_score(jd, title, company)
            score  = result.get("score", 0) if isinstance(result, dict) else int(result)
            scored += 1
            threshold = cfg.FIT_THRESHOLD if getattr(cfg, "USE_CLAUDE_SCORING", True) \
                        else getattr(cfg, "ATS_FIT_THRESHOLD", 60)
            print(f"  🎯 Fit: {score}%  {'✅' if score >= threshold else '❌'}")
            if score < threshold:
                skipped += 1; return

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
                print(f"  ⚠  Resume failed: {e}"); skipped += 1; return

            cover_path = ""
            try:
                cl_text = ce.write_cover_letter(
                    full_profile.get("name", "Your Name"),
                    profile_summary, jd, title, company
                )
                cover_path = cl_mod.save_cover_letter(cl_text, title, company)
            except Exception:
                pass

            print(f"  🚀 Applying to {company}...")
            try:
                success, reason = apply_to_greenhouse_job(
                    page, job, resume_path, cover_path, dry_run=args.dry_run
                )
            except Exception as e:
                success, reason = False, str(e)

            status = "Applied" if (success and not args.dry_run) else ("Dry-Run" if args.dry_run else "Failed")
            print(f"  {'✅' if success else '❌'} {status}: {reason}")

            if success and not args.dry_run:
                notifier.notify_applied(
                    title=title, company=company, fit_score=score,
                    resume_path=resume_path, cover_letter_path=cover_path,
                    platform="Greenhouse", job_url=url,
                )
                applied += 1

            log.append({
                "status": status, "title": title, "company": company,
                "score": score, "url": url, "resume": resume_path,
                "timestamp": datetime.now().isoformat(), "platform": "Greenhouse", "reason": reason,
            })
            save_log(log)
            time.sleep(cfg.APPLY_DELAY_SEC if hasattr(cfg, "APPLY_DELAY_SEC") else 2)

        companies = getattr(cfg, "GREENHOUSE_COMPANIES", [])
        print(f"\n  🔍 Pulling live jobs from Greenhouse's public Job Board API "
              f"for {len(companies)} compan{'y' if len(companies) == 1 else 'ies'}...")
        if not companies:
            print("  ⚠  config.GREENHOUSE_COMPANIES is empty — nothing to check. "
                  "Add company board tokens (the slug in their Greenhouse URL).")
        for company_token in companies:
            if applied >= args.limit:
                break
            jobs = _fetch_greenhouse_jobs_for_company(company_token)
            for job in jobs:
                if applied >= args.limit:
                    break
                try:
                    _process_job(job)
                except Exception as e:
                    print(f"  ⚠  Job error (continuing): {e}")

        browser.close()

    print(f"\n{'='*60}")
    print(f"  Greenhouse session done")
    print(f"  ✅ Applied:  {applied}")
    print(f"  🎯 Scored:   {scored}")
    print(f"  ⏭  Skipped:  {skipped}")
    print(f"{'='*60}\n")

    _cache.print_stats()
    notifier.notify_session_done(applied, scored, skipped)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹  Stopped by user (Ctrl+C)")
    except Exception:
        # Same crash-to-file safety net as the other three engines (see
        # workday_apply_now.py __main__, added 2026-07-12) — every crash gets
        # saved to a file automatically regardless of how the script was
        # launched, so it can be read back directly instead of needing the
        # terminal scrollback pasted in.
        import traceback
        crash_dir = cfg.BASE_DIR / "data" / "crash_logs"
        crash_dir.mkdir(parents=True, exist_ok=True)
        crash_file = crash_dir / f"crash_greenhouse_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        crash_file.write_text(traceback.format_exc())
        print(f"\n❌ Crashed — full traceback saved to data/crash_logs/{crash_file.name}")
        try:
            import error_log
            error_log.record("greenhouse", "CRASH", "Greenhouse engine crashed before finishing.",
                              context=traceback.format_exc()[-1500:])
        except Exception:
            pass
