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
# of those IDs). The script ALWAYS dry-runs (stops before the Submit click)
# unless you pass --live — run without --live against a couple of real job
# URLs first and watch the "📋 N field(s) on this step" / "⚠ could not find"
# print lines before ever adding --live — see CLAUDE.md safety workflow.
#
# LIVE-SUBMIT MODE (2026-08-26, --live): before every Submit click, the form
# is checked for any REQUIRED field with no truthful answer (still routed to
# essays/EEO/uncached with nothing resolved) — that job is skipped, never
# submitted incomplete, and never given a fabricated answer to force it
# through. Every job --live actually reaches this gate for (submitted or
# skipped) gets a full record — every field + the value resolved for it,
# and the outcome — in data/submitted_applications.json.
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
#   python greenhouse_apply_now.py --limit 5              # dry-run (default — no --live, nothing is ever submitted)
#   python greenhouse_apply_now.py --limit 2 --dry-run     # same as above, explicit
#   python greenhouse_apply_now.py --live --limit 3        # LIVE — actually submits, small batch first
#   python greenhouse_apply_now.py --live --dry-run        # --dry-run wins: still just a dry-run
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
# Live-submit audit log (added 2026-08-26 with --live) — one record per job
# that actually reached the post-fill gate in LIVE mode: every field the form
# had + the value (if any) resolved for it + whether it was submitted and why/
# why not. Separate from LOG_FILE (greenhouse_applied_log.json), which stays
# the terse per-run summary used for already_applied() dedup; this is the
# detailed record for auditing what a live submission actually contained.
SUBMITTED_LOG_FILE = cfg.BASE_DIR / "data" / "submitted_applications.json"
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

def _log_submitted_application(record: dict) -> None:
    """Appends one full audit record to data/submitted_applications.json.
    Called for every job that reaches the post-fill gate while running with
    --live — both jobs that got skipped for having an unresolved required
    field AND jobs that were actually submitted (success or fail) — so this
    file is a complete account of what --live did and why, not just a
    success tally. Never called in dry-run mode (nothing was actually
    attempted, so there's nothing to audit)."""
    try:
        SUBMITTED_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(SUBMITTED_LOG_FILE.read_text()) if SUBMITTED_LOG_FILE.exists() else []
        existing.append(record)
        SUBMITTED_LOG_FILE.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        print(f"          ⚠  Could not write data/submitted_applications.json: {e}")

def already_applied(url: str, log: list, title="", company="") -> bool:
    key = re.sub(r'\?.*', '', url).rstrip("/")
    for e in log:
        # "Unverified" (2026-09-09 hardening) means a prior run's Submit
        # click may have actually gone through before an exception hid the
        # outcome — block auto-retry here too, same as a confirmed "Applied",
        # so the pipeline never fires a second real submission at the same
        # posting on a guess. Requires a human to check and re-run --url
        # deliberately once they know what really happened.
        if e.get("status") not in ("Applied", "Already Applied", "Unverified"):
            continue
        if re.sub(r'\?.*', '', e.get("url", "")).rstrip("/") == key:
            return True
        if title and company:
            if (e.get("company", "").lower().strip() == company.lower().strip()
                    and e.get("title", "").lower().strip() == title.lower().strip()):
                return True
    return False

# ── Skip-cache — remember a genuinely-unresolvable job so re-runs don't
#    redo the expensive work (see the block comment on
#    config.GREENHOUSE_SKIP_RECHECK_DAYS for the full root-cause story:
#    without this, the SAME doomed DoorDash postings were fully re-scored/
#    re-resumed/re-filled — including live Claude essay-draft calls — on
#    every single run, which is the actual mechanism behind "every run
#    stalls on DoorDash"). Keyed by normalized URL, same normalization
#    already_applied() uses. Only ever written for the "no truthful answer
#    available" skip reason — never for a transient UI-automation gap. ────
SKIP_CACHE_FILE = cfg.BASE_DIR / "data" / "gh_skip_cache.json"

def _skip_cache_key(url: str) -> str:
    return re.sub(r'\?.*', '', url or '').rstrip("/")

def _load_skip_cache() -> dict:
    try:
        return json.loads(SKIP_CACHE_FILE.read_text()) if SKIP_CACHE_FILE.exists() else {}
    except Exception:
        return {}

def _save_skip_cache(cache: dict) -> None:
    try:
        SKIP_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SKIP_CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception as e:
        print(f"          ⚠  Could not write data/gh_skip_cache.json: {e}")

def _skip_cache_entry_valid(entry: dict) -> bool:
    """A cached skip is still trusted (safe to skip fast, no re-check) only
    if BOTH: the pipeline version hasn't changed since it was recorded (a
    version bump means the logic that produced the skip may now behave
    differently — always worth one fresh look), and it's younger than
    cfg.GREENHOUSE_SKIP_RECHECK_DAYS (a backstop for the form itself
    changing with no pipeline change at all)."""
    if not entry:
        return False
    if entry.get("pipeline_version") != getattr(cfg, "PIPELINE_VERSION", None):
        return False
    try:
        age_days = (datetime.now() - datetime.fromisoformat(entry.get("timestamp", ""))).days
    except Exception:
        return False
    return age_days < getattr(cfg, "GREENHOUSE_SKIP_RECHECK_DAYS", 14)

def _remember_skip(url: str, company: str, title: str, reason: str) -> None:
    cache = _load_skip_cache()
    cache[_skip_cache_key(url)] = {
        "company": company, "title": title, "reason": reason,
        "pipeline_version": getattr(cfg, "PIPELINE_VERSION", None),
        "timestamp": datetime.now().isoformat(),
    }
    _save_skip_cache(cache)

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

# ── Country field ─────────────────────────────────────────────────────────────
#
# ROOT CAUSE of the 2026-08-26 Affirm --live failure (screenshots/
# gh_fail_submit_not_confirmed_20260826_175845.png — 3 submit attempts, all
# rejected): Country was never explicitly handled anywhere in this file —
# not in GH{} (no known id), not in the _fill_basic_field() calls above (only
# first/last/email/phone), and not excluded from _smart_fill_greenhouse_fields()'s
# generic scrape either. So it fell to that generic scrape's SELECT-tag
# handling — a raw `el.value = matchedOption.value` plus a synthetic `change`
# event (see the "filled" JS in _smart_fill_greenhouse_fields()) — which
# _smart_fill_greenhouse_fields() then genuinely believed had worked (it was
# logged as `"Country*": "United States of America"` in
# data/submitted_applications.json for that exact failed run) with NO
# read-back check that the value actually took. The screenshot shows why
# that belief was wrong: Greenhouse's job-boards.greenhouse.io React UI
# renders Country as a custom combobox widget, not a real <select> — setting
# a DOM `.value` and firing a plain synthetic event doesn't reach a
# React-controlled widget's own internal selection state, so the widget kept
# showing its "Select a country" placeholder AND a red "Select a country"
# validation error, Greenhouse's own client-side validation correctly
# blocked the form from advancing on every one of the 3 submit clicks, and
# _submit_progressed() (page-state-diff — see its own comment below) correctly
# reported "not confirmed" each time. That detector was never the bug; it
# was working exactly as designed. The actual gap was upstream: nothing
# verified the Country fill actually stuck before Submit was ever clicked.
#
# Fix, below: (1) an explicit, dedicated Country filler tried BEFORE the
# generic scrape gets anywhere near it, that tries a real <select> first
# (Playwright's own .select_option() — fires the events frameworks actually
# listen for, unlike a raw JS .value= assignment) and only falls back to a
# click-open-then-click-the-option combobox interaction if no real <select>
# is found; (2) it NEVER reports success without reading the field's own
# selected/displayed text back and confirming it actually shows the
# country, not the placeholder — same "the click/call not throwing is not
# success" rule _submit_progressed() already applies to the Submit button.
# apply_to_greenhouse_job() below uses that verified result to add "Country"
# to the required-field gate if it's present, required, and still couldn't
# be filled — the same "never submit an incomplete required field" rule
# that already existed for every other field, just applied here for the
# first time.

_COUNTRY_WANTED = ("united states",)   # substring match — real option text on
                                        # different Greenhouse boards varies
                                        # ("United States" vs "United States
                                        # of America"); an exact-text match
                                        # against qa_answers.py's canonical
                                        # "United States of America" would
                                        # miss the shorter, more common form.

def _select_native_option(page, select_sel: str, wanted_substrings) -> bool:
    """Selects an option on a REAL <select> whose visible text contains any
    of wanted_substrings (case-insensitive), via Playwright's own
    .select_option() — not a raw JS .value= assignment, which is what
    silently failed to register on Greenhouse's React Country widget (see
    the block comment above). Returns True only after reading the select's
    OWN post-selection text back and confirming it matches — never assumes
    success just because select_option() didn't raise."""
    try:
        loc = page.locator(select_sel).first
        if loc.count() == 0 or not loc.is_visible(timeout=800):
            return False
        texts = loc.evaluate("el => Array.from(el.options).map(o => o.text)") or []
        match = next((t for t in texts if any(w in t.lower() for w in wanted_substrings)), None)
        if not match:
            return False
        loc.select_option(label=match)
        after = loc.evaluate("el => (el.options[el.selectedIndex] || {}).text || ''") or ""
        return any(w in after.lower() for w in wanted_substrings)
    except Exception:
        return False


def _select_combobox_option(page, trigger_selectors: list, wanted_text: str, wanted_substrings) -> bool:
    """For a custom (non-<select>) combobox/listbox widget: click whatever
    trigger element opens it, then click the visible option matching
    wanted_substrings — the actual UI interaction the widget expects,
    instead of trying to poke its internal state directly from JS. Tried
    only after _select_native_option() finds no real <select> to work with.
    Only reached from _fill_country_field(), never from the generic
    dropdown-answer path, so a wrong guess here can't touch any other
    field. Verifies the click landed on a real, visible option before
    returning True."""
    for trig_sel in trigger_selectors:
        try:
            trig = page.locator(trig_sel).first
            if trig.count() == 0 or not trig.is_visible(timeout=800):
                continue
            trig.click(timeout=3000)
            time.sleep(0.3)

            def _find_option():
                opt = page.get_by_role("option", name=re.compile(re.escape(wanted_text), re.I)).first
                if opt.count() > 0 and opt.is_visible(timeout=1500):
                    return opt
                opt = page.locator(f'[role="listbox"] >> text=/{re.escape(wanted_text)}/i').first
                if opt.count() > 0 and opt.is_visible(timeout=1500):
                    return opt
                return None

            opt = _find_option()
            if opt is None:
                # Some comboboxes need the country typed into a search box
                # before the matching option renders at all.
                try:
                    page.keyboard.type(wanted_text, delay=20)
                    time.sleep(0.4)
                    opt = _find_option()
                except Exception:
                    opt = None
            if opt is not None:
                opt.click(timeout=3000)
                time.sleep(0.3)
                return True
        except Exception:
            continue
    return False


def _has_country_field(page) -> dict:
    """Returns {"present": bool, "required": bool} — checked BEFORE any fill
    attempt so a form with no Country field at all (some configurations
    omit it) is never wrongly treated as a fill failure."""
    return _safe_eval(page, r"""
        () => {
            const sel = document.querySelector('select[id*="country" i], select[name*="country" i]');
            const lbl = Array.from(document.querySelectorAll('label')).find(l => /^country\b/i.test(l.innerText.trim()));
            const el = sel || (lbl && lbl.htmlFor ? document.getElementById(lbl.htmlFor) : null);
            const present = !!(sel || lbl);
            const labelText = lbl ? lbl.innerText.trim() : '';
            const required = !!(el && (el.required || el.getAttribute('aria-required') === 'true'))
                || /\*\s*$/.test(labelText);
            return { present, required };
        }
    """, {"present": False, "required": False}) or {"present": False, "required": False}


def _fill_country_field(page, value: str = "United States of America") -> bool:
    """See the block comment above this section for the full root-cause
    story. Tries a real <select> first (legacy embed UI), then a
    click-and-choose combobox interaction (the React job-boards UI's actual
    widget). Returns True only if verified — see both helper functions."""
    if _select_native_option(page, 'select[id*="country" i], select[name*="country" i]', _COUNTRY_WANTED):
        return True

    trigger_candidates = [
        '[aria-label*="country" i]',
        'label:has-text("Country") ~ * [role="combobox"]',
        'label:has-text("Country") ~ * button',
        'label:has-text("Country") + * [role="combobox"]',
        'label:has-text("Country") + * button',
        '#country', '[id*="country" i][role="combobox"]',
    ]
    return _select_combobox_option(page, trigger_candidates, "United States", _COUNTRY_WANTED)


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
                                     "added to qa_answers.py or PROFILE_FALLBACK",
                       drafts: dict | None = None) -> None:
    """Reuses the same data/stuck_questions.json shape as indeed/workday so
    there's one place to check for fields that need a manual answer added.
    `status` distinguishes WHY a field landed here — "genuinely unknown" vs
    "essay question, deliberately not auto-filled" are different situations
    and the log should say which.

    `drafts` (added 2026-08-25): optional {label: draft_text} map — for
    "why this role/company" essay fields, profile_answers.draft_motivation_essay()
    may have produced a grounded draft. It's attached to the logged field as
    "draft_answer" so it's visible for Raghav's review, but it is NEVER used
    to fill the form field itself — this function only logs, it never types
    anything."""
    if not fields:
        return
    try:
        stuck_file = cfg.BASE_DIR / "data" / "stuck_questions.json"
        stuck_file.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(stuck_file.read_text()) if stuck_file.exists() else []
        field_entries = []
        for f in fields:
            lbl = f.get("label", "")
            entry = {"label": lbl, "type": f.get("type", ""), "options": f.get("options", [])}
            if drafts and lbl in drafts:
                entry["draft_answer"] = drafts[lbl]
                entry["draft_note"] = ("Grounded draft from profile_answers.draft_motivation_essay() "
                                        "— REVIEW before using; never auto-submitted.")
            field_entries.append(entry)
        existing.append({
            "timestamp": datetime.now().isoformat(),
            "company": company, "job_title": job_title,
            "source": "greenhouse_apply_now._smart_fill_greenhouse_fields",
            "fields": field_entries,
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
    # Added 2026-08-31 — "I identify as a first-generation professional"
    # (seen live on a real Gusto posting) matched none of the signals
    # above, so it was never even classified as "eeo" — it fell through to
    # whatever the generic short_text/dropdown path decided, with no real
    # answer available there, and the job got skipped as incomplete.
    "first-generation", "first generation",
)

_ESSAY_SIGNALS = (
    "describe", "why do you", "why are you", "why anthropic", "why this",
    "why do you want", "tell us about", "walk us through", "approach to",
    "explain how", "explain your", "what interests you", "cover letter",
    "in your own words", "pitch us", "what makes you", "share a time",
    "give an example",
)

_YES_NO_OPTION_WORDS = {"yes", "no", "true", "false"}

# ── Pure acknowledgment/consent checkboxes ────────────────────────────────
#
# Added 2026-08-29. THE RULE (per Raghav's explicit spec): a checkbox is
# safe to auto-tick ONLY if it does nothing but confirm "I have read /
# acknowledge / agree to / consent to [a company policy document]" — it
# asserts NOTHING about Raghav himself. The moment a checkbox asserts a
# FACT or QUALIFICATION about him ("I certify this is true", "I am at
# least 18", "I am eligible to work"), it stops being pure boilerplate and
# becomes a truthfulness claim — those must go through the normal
# fact-checked yes/no path (or stay stuck if unknown), never a blanket
# auto-tick. _ACK_EXCLUDE_SIGNALS is checked FIRST and wins over
# _ACK_CONSENT_SIGNALS if both match, so a checkbox can never be
# miscategorized as pure boilerplate just because it also contains a
# policy-sounding word.
#
# This also intentionally excludes demographic/EEO-linked consent (e.g. a
# GDPR "I consent to my demographic data being processed" checkbox) — that
# stays with the EEO bucket below (declined via the form's own decline
# option, or routed to Raghav), never auto-ticked, per his explicit "do not
# store or auto-fill my actual demographic values" instruction. A generic
# "Applicant Privacy Acknowledgement" (required of every applicant, not
# specifically about voluntary demographic disclosure) is the case this IS
# meant to catch.
_ACK_CONSENT_SIGNALS = (
    "acknowledge", "acknowledgement", "acknowledgment",
    "i have read", "i've read", "have read and understood",
    "agree to the privacy", "privacy policy", "privacy notice",
    "applicant privacy", "i consent to", "consent to the",
    "terms of service", "terms and conditions",
)

_ACK_EXCLUDE_SIGNALS = (
    # demographic/EEO-linked consent — handled by the EEO bucket instead
    "demographic", "race", "ethnicity", "gender", "disability", "veteran",
    "sexual orientation", "self-identif", "self identif",
    # factual/qualification assertions about Raghav himself — not a mere
    # "I read this document" acknowledgment, so this must go through the
    # normal truthfulness-checked path instead of a blanket auto-tick
    "i certify", "i confirm that i", "i am at least", "i am eligible",
    "i am currently eligible", "i am authorized", "i have not been",
    "i possess", "i hold a", "under penalty of perjury", "true and accurate",
    "true and correct", "to the best of my knowledge",
)

def _is_pure_ack_consent(label: str) -> bool:
    """See the block comment above — the ONLY things this may return True
    for are checkboxes that do nothing but acknowledge/consent to reading a
    policy document. Never a fact or qualification claim about Raghav."""
    l = label.lower()
    if any(s in l for s in _ACK_EXCLUDE_SIGNALS):
        return False
    return any(s in l for s in _ACK_CONSENT_SIGNALS)

_DECLINE_PHRASES = (
    "decline to self-identify", "decline to self identify",
    "declined to self-identify", "declined to self identify",
    "decline to answer", "declined to answer",
    "decline to identify", "decline to disclose",
    "prefer not to answer", "prefer not to disclose", "prefer not to say",
    "i prefer not to answer", "i prefer not to say",
    "i don't wish to answer", "i do not wish to answer",
    "do not wish to answer", "don't wish to answer",
    "choose not to disclose", "not specified", "not wish to identify",
)

def _normalize_decline_text(s: str) -> str:
    """Curly quotes and non-breaking hyphens are a common real-world reason
    a literal substring match on 'don't'/'self-identify' silently misses —
    Greenhouse's actual option text sometimes uses a Unicode apostrophe
    (') or non-breaking hyphen instead of the plain ASCII ' and -. Without
    this, _DECLINE_PHRASES could fail to match a decline option that IS
    genuinely present in the scraped options list — exactly the "the scrape
    has it, the phrase-matching missed it" gap Raghav flagged."""
    return (s.replace("’", "'").replace("‘", "'")
             .replace("–", "-").replace("—", "-")
             .replace("‑", "-"))

def _find_decline_option(options: list) -> str | None:
    """Returns the form's OWN decline-to-self-identify option text if one
    exists among the real scraped options, or None if it genuinely doesn't
    — never a guess, never invented text. Checked against a normalized copy
    of each option (see _normalize_decline_text) so punctuation-variant
    phrasing of the same real option isn't missed."""
    for o in options:
        norm = _normalize_decline_text(str(o).strip().lower())
        if any(p in norm for p in _DECLINE_PHRASES):
            return o
    return None

# ── EEO — Raghav's own real answers (added 2026-08-29, given directly by
# him in chat) ────────────────────────────────────────────────────────────
#
# Everything above this point (_find_decline_option et al.) is still the
# fallback for anything NOT covered here. This layer only ever fires for
# the six specific questions raghav_profile.EEO_ANSWERS has a real value
# for, and only when that value genuinely matches one of THIS form's real
# options — never typed in blind, never assumed present.

def _eeo_subcategory(label: str) -> str | None:
    """Which specific EEO question is this — one of the seven Raghav has
    given a real answer for, or None (sexual orientation, or anything else
    not covered — falls through to the existing decline/route behavior
    unchanged). Order matters: 'transgender' and 'sexual orientation' are
    checked BEFORE the generic 'gender' signal so neither is misread as the
    plain gender question — 'transgender' contains the substring 'gender',
    and Raghav has given no answer for sexual orientation."""
    l = label.lower()
    if "sexual orientation" in l:
        return None
    if "first-generation" in l or "first generation" in l:
        return "first_generation"
    if "transgender" in l:
        return "transgender"
    if "veteran" in l:
        return "veteran"
    if "disabilit" in l:
        return "disability"
    if "hispanic" in l or "latino" in l or "latinx" in l:
        return "hispanic_latino"
    if "race" in l or "ethnicity" in l:
        return "race"
    if "gender" in l or re.search(r'\bsex\b', l):
        return "gender"
    return None

def _find_eeo_answer_option(options: list, wanted: str) -> str | None:
    """Matches Raghav's real EEO_ANSWERS value against THIS form's ACTUAL
    options — returns None (never a guess) if his answer doesn't correspond
    to any real option here. Exact match first (most forms use short exact
    text: "Male", "No", "Asian"); then a prefix-of-a-full-sentence-option
    match for forms that phrase the same answer as a longer sentence ("No,
    I do not have a disability and have not had one in the past") — never a
    bare substring-anywhere match, which would risk a short word like "no"
    matching the wrong option."""
    w = wanted.lower().strip()
    for o in options:
        if str(o).strip().lower() == w:
            return o
    for o in options:
        ol = str(o).strip().lower()
        if ol.startswith(w + ",") or ol.startswith(w + " ") or ol.startswith(w + "."):
            return o
    return None

# ── EEO react-select combobox reading (added 2026-08-30) ───────────────────
#
# Real evidence from data/submitted_applications.json (today's --live run,
# 2026-08-30 20:36-20:37): all six EEO fields (Gender, transgender,
# Hispanic/Latinx, Race, Veteran, Disability) still come back with EMPTY
# options on DoorDash's real form — "Real options seen: (none captured)" —
# even after v2.9.0 added real-answer matching. Same root cause already
# proven for Country above: job-boards.greenhouse.io's React UI renders
# these as react-select comboboxes, not real <select> elements, and their
# real option text only exists in the DOM once the widget is OPENED (a
# portal-rendered listbox) — the static field-scrape in
# _smart_fill_greenhouse_fields() runs once, before anything is clicked, so
# it can only ever see an empty options list for these.
#
# Fix: when an EEO field's scraped options are empty, open its combobox
# (same click-the-real-widget approach as _select_combobox_option(), proven
# on Country) and read back the REAL rendered option text — then hand that
# list to the EXISTING, UNCHANGED _find_eeo_answer_option()/
# _find_decline_option() matching functions to decide, and only then click
# the decided option. This file's honesty rule doesn't change: still "the
# form's own real option or nothing", now just able to actually see what
# those real options are on this UI.

def _eeo_trigger_candidates(label_text: str) -> list:
    """Candidate selectors for the clickable element that opens a
    react-select-style EEO combobox — the same trigger-discovery idiom
    already proven for Country (_fill_country_field), generalized to any
    field label via Playwright's :has-text(). Quotes/backslashes are
    stripped from the label before embedding it in the selector string —
    EEO label text is Greenhouse's own wording and shouldn't contain them,
    but this is defense-in-depth against a malformed selector, not a
    trust boundary."""
    esc = re.sub(r'["\\]', '', (label_text or "").split("*")[0].strip())[:40]
    if not esc:
        return []
    return [
        f'label:has-text("{esc}") ~ * [role="combobox"]',
        f'label:has-text("{esc}") ~ * button',
        f'label:has-text("{esc}") + * [role="combobox"]',
        f'label:has-text("{esc}") + * button',
        f'[aria-label*="{esc}" i]',
    ]


def _scoped_listbox(page, trig):
    """Returns a Locator scoped to the SPECIFIC listbox associated with
    `trig` (the trigger element that was just clicked to open a combobox)
    — or None if it can't be determined. Added 2026-08-30 after a real
    live run showed EEO fields reading back PHONE COUNTRY-CALLING-CODE
    options ("Afghanistan +93", "Åland Islands +358", ...) instead of
    Gender/Race/Veteran/Disability's own choices: the previous reader
    queried '[role="option"]' across the WHOLE PAGE with no scoping at
    all, so whichever combobox's options happened to come first in DOM
    order (the phone field's country-code list, rendered earlier in the
    form and apparently large enough to fill the entire 30-item cap) won
    over the EEO widget actually just opened. This fixes that at the
    root: scope to ONLY the popup this specific trigger controls.

    Prefers the WAI-ARIA combobox pattern — trig's aria-controls/aria-owns
    attribute names the listbox's real element id — since that's the
    precise, unambiguous link a real accessible combobox implementation
    sets between a trigger and its OWN popup, not a guess. Falls back to
    the most-recently-added, currently-visible [role="listbox"] in the DOM
    (React portals are appended, so the widget just opened is normally
    last) only when aria-controls/aria-owns isn't present — still scoped
    to ONE specific listbox element, never a flat page-wide option query."""
    try:
        listbox_id = trig.get_attribute("aria-controls") or trig.get_attribute("aria-owns")
    except Exception:
        listbox_id = None
    if listbox_id:
        try:
            scoped = page.locator(f'[id="{listbox_id}"]')
            if scoped.count() > 0:
                return scoped.first
        except Exception:
            pass
    try:
        boxes = page.locator('[role="listbox"]')
        n = boxes.count()
        for i in range(n - 1, -1, -1):   # most-recently-added first
            box = boxes.nth(i)
            if box.is_visible(timeout=300):
                return box
    except Exception:
        pass
    return None


def _read_scoped_listbox_options(page, trig) -> list:
    """After `trig` was just clicked open, reads every visible option's
    REAL text from ITS OWN listbox (see _scoped_listbox() above for why
    this must be scoped, not a global page-wide query). Never invents an
    option — only returns what's actually rendered in this specific popup
    right now. Returns [] if this trigger's listbox can't be identified at
    all — the caller treats that exactly like "no options found"."""
    box = _scoped_listbox(page, trig)
    if box is None:
        return []
    try:
        opts = box.locator('[role="option"]')
        n = min(opts.count(), 30)
        out = []
        for i in range(n):
            try:
                t = opts.nth(i).inner_text(timeout=500).strip()
                if t:
                    out.append(t)
            except Exception:
                continue
        return out
    except Exception:
        return []


def _open_eeo_combobox(page, label_text: str):
    """For an EEO field whose static scrape found zero options: opens its
    combobox and reads back the REAL rendered option text — scoped to that
    SPECIFIC widget only (see _scoped_listbox()). Returns
    (trigger_selector, options) so the caller can decide (via the existing,
    unchanged _find_eeo_answer_option()/_find_decline_option()) and then
    re-use trigger_selector to click the decided option without
    re-searching for the widget. Returns (None, []) if no combobox could be
    found/opened for this label — the caller falls through to the existing
    "no options captured, route to Raghav" behavior, now after a real
    attempt was made rather than none at all."""
    for trig_sel in _eeo_trigger_candidates(label_text):
        try:
            trig = page.locator(trig_sel).first
            if trig.count() == 0 or not trig.is_visible(timeout=500):
                continue
            trig.click(timeout=2500)
            time.sleep(0.25)
            opts = _read_scoped_listbox_options(page, trig)
            if opts:
                return trig_sel, opts
            page.keyboard.press("Escape")
        except Exception:
            continue
    return None, []


def _click_open_combobox_option(page, trigger_sel: str, option_text: str) -> bool:
    """Clicks option_text inside the listbox opened by trigger_sel — SCOPED
    to that specific widget (see _scoped_listbox()), never a page-wide
    "the first option anywhere matching this text" search, which could
    click into a different, unrelated open widget if its options happen to
    share text with this one (e.g. a plain "No"). option_text is always a
    value just read back from THIS SAME widget by _open_eeo_combobox() —
    never invented. Re-opens the widget first if it already closed itself
    (some implementations close on blur/Escape)."""
    try:
        trig = page.locator(trigger_sel).first
        box = _scoped_listbox(page, trig) if trig.count() else None
        pattern = re.compile(f"^{re.escape(option_text)}$", re.I)
        target = box.get_by_role("option", name=pattern).first if box is not None else None

        if target is None or target.count() == 0 or not target.is_visible(timeout=500):
            if trig.count() and trig.is_visible(timeout=500):
                trig.click(timeout=2000)
                time.sleep(0.2)
            box = _scoped_listbox(page, trig)
            target = box.get_by_role("option", name=pattern).first if box is not None else None

        if target is None or target.count() == 0:
            return False
        target.click(timeout=2000)
        time.sleep(0.2)
        return True
    except Exception:
        return False


def _classify_field(f: dict) -> str:
    """Returns one of: "ack_consent", "eeo", "essay", "yes_no", "dropdown",
    "short_text". This decision is made from the field's real DOM type +
    its options + its label — never from what answer happens to be cached
    for it."""
    label = f.get("label", "").lower().strip()
    ftype = f.get("type", "")
    options = [str(o).strip().lower() for o in f.get("options", [])]

    if ftype == "checkbox" and _is_pure_ack_consent(label):
        return "ack_consent"

    if any(s in label for s in _EEO_SIGNALS):
        return "eeo"

    # Yes/No shape checks run BEFORE the essay/length heuristic below — a
    # field's REAL shape (an actual Yes/No radio/select, or plain text/
    # number field phrased as a yes/no question) must never be miscategorized
    # as an essay just because the question is dressed in a long sentence.
    # FIXED 2026-08-26: this was the root cause of a live run applying to
    # ZERO jobs — DoorDash/Affirm/Chime's real sponsorship questions are
    # genuine Yes/No questions but phrased as 150+ character legal-boilerplate
    # sentences ("Will you now require immigration sponsorship by our company
    # to attain or maintain your employment eligibility (e.g., H-1B, E-3,
    # TN, O-1, STEM OPT...)?"), so the OLD `len(label) > 90` check below fired
    # first and routed them to "essay" — which is NEVER auto-filled by design
    # — before they ever reached the yes_no branch where a truthful "Yes"
    # (from config.REQUIRES_SPONSORSHIP) was available. Combined with the
    # required-field completeness gate added the same day, that meant every
    # job with one of these long-but-answerable required questions got
    # skipped, even though the honest answer was known.
    if options and len(options) <= 3 and all(o in _YES_NO_OPTION_WORDS for o in options):
        return "yes_no"

    # SAME bug class as the sponsorship fix above, found live 2026-08-31:
    # DoorDash's "We would like to contact you via SMS or WhatsApp to
    # provide updates on your progress. Mark yes if you agree..." is a real
    # Yes/No question (Raghav has a real answer on file —
    # raghav_profile.PROFILE["sms_whatsapp_optin"]) but has no "?" and is
    # well over 90 characters, so it was falling into the essay bucket
    # below and getting skipped as an unanswerable required field on every
    # DoorDash posting that used this exact phrasing (a shorter phrasing —
    # "Would you like to receive communications via SMS and/or WhatsApp...?"
    # — already worked, because it has a "?" and starts with "Would"; this
    # one doesn't). "sms" + "whatsapp" together is specific enough to never
    # false-positive on an unrelated question.
    if "sms" in label and "whatsapp" in label:
        return "yes_no"

    if ftype in ("text", "number", "radio", "checkbox", "select") and "?" in label and re.match(
        r'^(are|do|does|is|have|has|will|can|would|did)\b', label
    ) and not any(s in label for s in _ESSAY_SIGNALS):
        return "yes_no"

    if ftype == "textarea" or any(s in label for s in _ESSAY_SIGNALS) or len(label) > 90:
        return "essay"

    if ftype == "select" and options:
        return "dropdown"

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

    # Added 2026-08-31, paired with the "sms"+"whatsapp" classification fix
    # above — self-contained to Greenhouse (reads raghav_profile.PROFILE
    # directly), never touches qa_answers.py, per Raghav's explicit "only
    # Greenhouse for now" scope. qa_answers.py's own SMS/WhatsApp keys are
    # still separately used by Indeed/LinkedIn and are untouched.
    if "sms" in l and "whatsapp" in l:
        optin = bool(rp.PROFILE.get("sms_whatsapp_optin", False)) if rp else False
        return "Yes" if optin else "No"

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
    # FIXED 2026-08-26 — two real overclaiming risks found while diagnosing
    # a live run that got blocked by the (now-fixed) essay-misclassification
    # bug above; once that bug stopped hiding these questions from ever
    # being evaluated, both of these would otherwise have started producing
    # real false "Yes" answers on real applications:
    #   1. MULTI-CLAUSE questions like "1+ years of industry experience post
    #      PhD OR 3+ years post graduate degree of developing ML models..."
    #      have TWO distinct thresholds for two different degree levels.
    #      The old code took re.search()'s FIRST match (here, "1+", the
    #      post-PhD threshold — which doesn't even apply, Raghav has a
    #      Master's, not a PhD) and compared it against his real ML years
    #      (2), producing "Yes" — but the clause that actually applies to
    #      him (3+ years post-grad-degree) he does NOT meet. Now: if more
    #      than one DISTINCT number appears, this pattern is genuinely
    #      ambiguous — return None rather than guess which clause applies.
    #   2. UNMATCHED SPECIFIC DOMAINS silently fell back to comparing against
    #      cfg.YEARS_EXPERIENCE (total professional years) even when the
    #      question named a specific skill/domain not in SKILL_YEARS (e.g.
    #      "years analyzing product data... in a SaaS environment" — not a
    #      real SKILL_YEARS entry, but the old code still answered "Yes"
    #      because total years (3) happened to clear the threshold). Now:
    #      if the named domain isn't a real, matched skill, return None —
    #      the QA -> profile_answers.py fallthrough chain that runs next
    #      already has the correct, narrower logic for this (only falls
    #      back to total years for a genuinely GENERIC "years of experience"
    #      question with no specific domain named at all).
    numbers = re.findall(r'(\d+)\+?\s*years?', l)
    if numbers and "year" in l and ("experience" in l or "worked with" in l or "working with" in l):
        distinct = set(numbers)
        if len(distinct) > 1:
            return None   # multiple distinct thresholds in one question — ambiguous, don't guess
        threshold = float(numbers[0])
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
            # A specific domain may still be named even though it's not a
            # SKILL_YEARS key — don't assume it's generic, let the more
            # careful downstream logic (profile_answers.answer_from_profile,
            # which distinguishes truly-generic phrasing from a named-but-
            # unmatched domain) decide instead of guessing here.
            return None
        return "Yes" if actual_years >= threshold else "No"

    return None

def _smart_fill_greenhouse_fields(page, job_title: str, company: str, jd_text: str) -> dict:
    """Fills every remaining labeled field on the form: Greenhouse custom
    questions + the standard EEO/voluntary-disclosure block. Ported from
    workday_apply_now.py's _smart_fill_questions — that function is already
    selector-agnostic (label[for] / aria-label / placeholder / fieldset+legend
    for radio-checkbox groups), which is exactly what Greenhouse's standard
    HTML forms use, both the legacy embed and the React job-boards UI.

    Returns a dict, not just a fill count (changed 2026-08-26 for live-submit
    gating — see apply_to_greenhouse_job()):
        "filled"              — count of DOM fields actually written to
        "total_fields"        — count of fields found on the form
        "answers"             — {label: value} for every field that got a
                                 real, truthful answer (from any layer —
                                 fact/QA/SAVED/CACHE/PROFILE/CLAUDE)
        "unresolved_required" — labels of REQUIRED fields that got NO answer
                                 (essay/EEO-no-decline-option/uncached fields
                                 that also happen to be required). Non-empty
                                 means this application cannot be honestly
                                 completed — the caller must not submit it."""
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
                'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]), select, textarea'
            )) {
                // Use the LIVE `.type` DOM property, not `getAttribute('type')`
                // — added 2026-09-01, evidence: DoorDash's "Applicant Privacy
                // Acknowledgement" checkbox was showing up here as
                // type='text', options=[] (see the DIAG log added 2026-08-31
                // right where this field's answer gets filled below) instead
                // of reaching the checkbox-specific loop a few lines down,
                // which already has the real ack-consent handling AND the
                // 2026-08-30 offsetParent visibility fix for this exact
                // field. A checkbox whose "checkbox-ness" is set as a DOM
                // property (common for React/custom-widget forms) rather
                // than a literal `type="checkbox"` HTML attribute still
                // reports correctly via the live `.type` property (the
                // browser always normalizes it) but is invisible to both
                // `getAttribute('type')` and the old CSS attribute selector
                // that excluded checkboxes/radios from this loop — so it
                // fell through to here and got misclassified as plain text.
                // Skip it here (by live type, not attribute) so the
                // checkbox-specific loop below picks it up instead.
                const liveType = inp.tagName === 'SELECT' ? 'select'
                    : inp.tagName === 'TEXTAREA' ? 'textarea'
                    : (inp.type || inp.getAttribute('type') || 'text').toLowerCase();
                if (liveType === 'checkbox' || liveType === 'radio') continue;
                if (!inp.offsetParent) continue;
                if (KNOWN.has(inp.id) || KNOWN.has(inp.name)) continue;
                const type = liveType;
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
                const required = inp.required || inp.getAttribute('aria-required') === 'true'
                    || /\*\s*$/.test((lbl || '').trim());
                results.push({ label: lbl, type, options: opts, sel: uniqueSel(inp), required });
            }
            const groups = {};
            const skippedGroups = new Set();
            for (const inp of document.querySelectorAll('input')) {
                // Same live-`.type`-vs-attribute fix as the loop above:
                // query ALL inputs and filter by the live property here,
                // rather than `input[type=radio],input[type=checkbox]`
                // (an attribute selector — misses a checkbox whose type is
                // set as a DOM property instead of a literal HTML attribute).
                if (inp.type !== 'radio' && inp.type !== 'checkbox') continue;
                // Visibility: check the input's own box first, then fall back
                // to its associated <label> via the native .labels API (this
                // covers BOTH label[for=id] AND the input being a CHILD of
                // its label — <label><input type=checkbox> I acknowledge...
                // </label> — which offsetParent-on-the-input-alone misses
                // whenever the raw input is visually hidden behind a styled
                // sibling, a very common custom-checkbox pattern). Added
                // 2026-08-30: root cause of DoorDash's "Applicant Privacy
                // Acknowledgement" never being found/ticked — its native
                // input had offsetParent === null (display:none, replaced by
                // a styled visual box) and the old code dropped it outright.
                const assocLabel = (inp.labels && inp.labels.length) ? inp.labels[0] : null;
                if (!inp.offsetParent && !(assocLabel && assocLabel.offsetParent)) continue;

                const gname = inp.name || '';

                // Standalone checkbox with NO name/group — e.g. a single
                // required "I acknowledge..." box, not a radio-style group.
                // The old code required a non-empty `gname` and silently
                // dropped every nameless checkbox — also a real cause of the
                // same DoorDash symptom (React forms commonly key a lone
                // checkbox by id only, no name attribute). Give it a group
                // of one, keyed by a real selector rather than a shared name
                // — soloSel:true tells the fill step below (and the Python
                // side) to query that selector directly instead of
                // input[name=...].
                if (inp.type === 'checkbox' && !gname) {
                    if (inp.checked) continue;
                    let lbl = assocLabel ? assocLabel.innerText.trim().split('\n')[0] : '';
                    if (!lbl) {
                        const fs = inp.closest('fieldset,[role="group"],.field');
                        if (fs) { const h = fs.querySelector('legend,label'); if (h) lbl = h.innerText.trim().split('\n')[0]; }
                    }
                    if (!lbl || seen[lbl.toLowerCase()]) continue;
                    seen[lbl.toLowerCase()] = true;
                    const sel = uniqueSel(inp);
                    const required = inp.required || inp.getAttribute('aria-required') === 'true'
                        || /\*\s*$/.test(lbl.trim());
                    groups[sel] = { label: lbl, type: 'checkbox', options: [lbl], gname: sel, soloSel: true, required };
                    continue;
                }

                if (!gname || groups[gname] || skippedGroups.has(gname)) continue;
                const already = Array.from(document.querySelectorAll('input[name="'+CSS.escape(gname)+'"]')).some(r => r.checked);
                if (already) { skippedGroups.add(gname); continue; }
                const fs = inp.closest('fieldset,[role="group"],.field');
                let lbl = gname;
                if (fs) {
                    const leg = fs.querySelector('legend,label');
                    if (leg) lbl = leg.innerText.trim().split('\n')[0];
                } else if (assocLabel) {
                    lbl = assocLabel.innerText.trim().split('\n')[0] || lbl;
                }
                if (!seen[lbl.toLowerCase()]) {
                    seen[lbl.toLowerCase()] = true;
                    const opts = Array.from(document.querySelectorAll('input[name="'+CSS.escape(gname)+'"]'))
                        .map(r => {
                            const le = (r.labels && r.labels.length) ? r.labels[0]
                                : document.querySelector('label[for="'+CSS.escape(r.id)+'"]');
                            return le ? le.innerText.trim() : r.value;
                        }).filter(Boolean);
                    const groupRequired = Array.from(document.querySelectorAll(
                        'input[name="'+CSS.escape(gname)+'"]'
                    )).some(r => r.required || r.getAttribute('aria-required') === 'true')
                        || /\*\s*$/.test((lbl || '').trim());
                    groups[gname] = { label: lbl, type: inp.type, options: opts, gname, required: groupRequired };
                }
            }
            for (const k in groups) results.push(groups[k]);
            return results;
        }
    """, []) or []

    if not fields:
        return {"filled": 0, "total_fields": 0, "answers": {}, "unresolved_required": []}
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
    essay_drafts = {}   # {label: grounded draft text} — for review only, never auto-filled
    eeo_live_filled = set()   # labels resolved via _open_eeo_combobox() + a direct
                               # Playwright click — must be excluded from the generic
                               # blast-fill pass below (see its itemsJson comment)

    for f in fields:
        lbl = f.get("label", "")
        lbl_l = lbl.lower().strip().rstrip(" *:?")
        category = _classify_field(f)

        # ── Diagnostic only — added 2026-08-31 ────────────────────────────────
        # A real dry-run against DoorDash (Raghav, 2026-08-31) showed
        # "Applicant Privacy Acknowledgement *" landing in the generic
        # uncached/stuck bucket on every single posting, despite
        # `_is_pure_ack_consent()` correctly returning True for that exact
        # label text when tested directly — confirmed by running it here,
        # not assumed. That means the Python decision logic is fine and the
        # real cause is upstream: whatever this field's `type` actually is
        # in the live DOM scrape, it isn't the literal string "checkbox" by
        # the time it reaches `_classify_field()` here — something this
        # sandbox has no way to see without a live browser. Rather than
        # guess at DOM structure blind, this logs the field's ACTUAL raw
        # type/options the next time this exact mismatch happens, so the
        # real fix can be made from real evidence instead of a guess.
        if category != "ack_consent" and _is_pure_ack_consent(lbl):
            print(f"             🔍 DIAG     '{lbl}' looks like a pure ack/consent "
                  f"checkbox by its label but classified as '{category}', not "
                  f"'ack_consent' — raw type={f.get('type')!r} options={f.get('options')!r} "
                  f"required={f.get('required')!r}. Needs a look before this can be fixed "
                  f"for real; falling through to the normal unresolved-field handling below.")

        # ── Pure acknowledgment/consent checkbox — safe to auto-tick ─────────
        # See _is_pure_ack_consent()'s block comment for the exact rule. This
        # is deliberately the ONLY checkbox category ever auto-checked here —
        # never a fact/qualification checkbox, never a demographic-consent
        # checkbox (both excluded before classification ever reaches this
        # branch). The answer is set to the checkbox's OWN real label text so
        # it self-matches in the DOM-filling step below (same idiom already
        # used for "i consent" / "save my answers for pre-filling" in
        # qa_answers.py), not an invented generic "checked" token.
        if category == "ack_consent":
            answers[lbl] = lbl.rstrip(" *:")
            print(f"             ✔ ACK      '{lbl}' → auto-checked "
                  f"(privacy/consent acknowledgment — reading/agreeing to a "
                  f"policy document, not a claim about Raghav)")
            continue

        # ── EEO / self-identification ─────────────────────────────────────────
        # Raghav's own real answers (raghav_profile.EEO_ANSWERS) are tried
        # first for the six specific questions he's given one for — but ONLY
        # if that answer matches one of THIS form's actual options
        # (_find_eeo_answer_option never invents a match). Everything else
        # (sexual orientation, or a mismatched option wording on any of the
        # six) keeps the original behavior: the form's own decline option,
        # or routed to Raghav — never guessed.
        #
        # Added 2026-08-30: if the static scrape found zero options (the
        # react-select-combobox symptom — see the block comment above
        # _open_eeo_combobox()), open the real widget and read its real
        # options before giving up. This ONLY changes where the options list
        # comes from; the matching rules below (_find_eeo_answer_option /
        # _find_decline_option) are untouched.
        if category == "eeo":
            options = f.get("options", [])
            live_trigger = None
            if not options:
                live_trigger, live_opts = _open_eeo_combobox(page, lbl)
                if live_opts:
                    options = live_opts
                    f["options"] = live_opts   # so it shows up correctly in the audit record too
            subcat = _eeo_subcategory(lbl)
            wanted = rp.EEO_ANSWERS.get(subcat) if (subcat and rp) else None
            real_opt = _find_eeo_answer_option(options, wanted) if wanted else None
            chosen, chosen_kind, click_failed = None, None, False
            if real_opt:
                chosen, chosen_kind = real_opt, "real"
            elif (
                # Free-text fallback — added 2026-08-31, per Raghav's explicit
                # ask that self-ID questions be answered whether the form is a
                # selectable widget (dropdown/radio/combobox — the "real"
                # branch above) OR a genuine fill-in-the-blank field. Only
                # taken when there is truly nothing to select from: no
                # options in the static scrape AND opening a live combobox
                # found no trigger to open at all (so this isn't a
                # react-select widget just hiding its options — see
                # _open_eeo_combobox()'s block comment for that pattern) AND
                # the DOM element itself really is a text/textarea input, not
                # a radio/checkbox group. In that case there is no "form's
                # actual option" to match against — the field IS the answer,
                # typed directly, same as it types into any other text field.
                wanted and not options and not live_trigger
                and f.get("type") in ("text", "textarea")
            ):
                chosen, chosen_kind = wanted, "real_text"
            else:
                decline_opt = _find_decline_option(options)
                if decline_opt:
                    chosen, chosen_kind = decline_opt, "decline"

            if chosen and live_trigger:
                # Resolved via the live-opened combobox — the generic
                # blast-fill pass can't drive a react-select widget (see the
                # Country block comment), so click the real option directly,
                # right now, through Playwright. Never claim success without
                # verifying the click actually landed — same rule
                # _submit_progressed()/_fill_country_field() already apply
                # everywhere else in this file.
                if _click_open_combobox_option(page, live_trigger, chosen):
                    answers[lbl] = chosen
                    eeo_live_filled.add(lbl)
                else:
                    click_failed = True
                    print(f"             ⚠  EEO      '{lbl}' — found the real "
                          f"'{chosen}' ({chosen_kind}) option in the opened widget but the "
                          f"click didn't land; leaving unresolved rather than claiming success.")
                    chosen = None

            if chosen and lbl not in answers:
                # Not filled via the live-click path above — either the
                # static scrape already had real options (no combobox-open
                # needed), or this ran with no live_trigger at all. Record
                # the same decision either way; the generic blast-fill pass
                # further down does the actual DOM click for these.
                answers[lbl] = chosen

            if chosen and chosen_kind == "real":
                print(f"             ✔ EEO      '{lbl}' → '{chosen}' "
                      f"(Raghav's own real answer, matched to this form's actual option)")
                continue
            if chosen and chosen_kind == "real_text":
                print(f"             ✔ EEO      '{lbl}' → '{chosen}' "
                      f"(Raghav's own real answer, typed directly — this field is free text, "
                      f"not a selectable option)")
                continue
            if chosen and chosen_kind == "decline":
                print(f"             ⏭  EEO      '{lbl}' → '{chosen}' "
                      f"(the form's own decline-to-answer option, not a guess)")
                continue

            if live_trigger:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
            eeo_skipped.append(f)
            if click_failed:
                print(f"             ⏭  EEO      '{lbl}' — left blank; a real option was found "
                      f"but the widget click failed (see the ⚠ line above), not a missing-option case.")
            else:
                print(f"             ⏭  EEO      '{lbl}' — left blank (self-identification, "
                      f"no decline option found on this form). Real options seen: "
                      f"{options if options else '(none captured)'}")
            continue

        # ── Essay / open-ended pitch questions ────────────────────────────────
        # "Why this role/company" motivation questions get a GROUNDED DRAFT
        # (real JD text + real profile facts, via
        # profile_answers.draft_motivation_essay()). Added 2026-08-30, per
        # Raghav's explicit request: a PURE motivation essay (checked via
        # profile_answers.is_pure_motivation_question() — is_motivation_
        # question() plus a check that no factual/qualification claim is
        # mixed into the same prompt) now gets that draft typed directly
        # into the field — it's grounded in his real JD + real background,
        # not fabricated. Every other essay prompt (a mixed motivation+
        # factual question, or a non-motivation prompt like "describe a
        # project you're proud of") is UNCHANGED: no auto-fill, routes to
        # stuck_questions.json exactly as before, with the draft attached
        # for review if one was generated.
        if category == "essay":
            draft = None
            try:
                import profile_answers as _pa
                draft = _pa.draft_motivation_essay(lbl, jd_text, job_title, company)
            except Exception as e:
                print(f"             ⚠  Essay draft generation errored for '{lbl}': {e}")

            if draft and _pa.is_pure_motivation_question(lbl):
                answers[lbl] = draft
                print(f"             ✔ ESSAY    '{lbl}' → auto-filled with a grounded "
                      f"motivation draft (real JD + real profile facts, not fabricated):")
                for line in draft.splitlines():
                    print(f"                          {line}")
                continue

            essays.append(f)
            if draft:
                essay_drafts[lbl] = draft
                print(f"             ✏️  DRAFT   '{lbl}' — grounded draft generated "
                      f"(motivation-shaped but mixed with a factual/qualification ask — "
                      f"review before using, not auto-filled):")
                for line in draft.splitlines():
                    print(f"                          {line}")
            else:
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
                                  "write your own real answer here (a grounded draft is attached "
                                  "under 'draft_answer' for motivation/why-this-role questions — "
                                  "review it before using, it is never submitted automatically)",
                           drafts=essay_drafts)
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
                    // soloSel (added 2026-08-30, see the scraper above): a
                    // standalone nameless checkbox — item.gname holds a
                    // real CSS selector (its own #id, or a generated
                    // data-attr) instead of a shared `name`, so it must be
                    // queried directly rather than via input[name=...],
                    // which would never match a nameless input.
                    const opts = !item.gname ? []
                        : item.soloSel ? Array.from(document.querySelectorAll(item.gname))
                        : Array.from(document.querySelectorAll('input[name="'+CSS.escape(item.gname)+'"]'));
                    const ansL = ans.toLowerCase().trim();
                    for (const opt of opts) {
                        let lbl = '';
                        const le = (opt.labels && opt.labels.length) ? opt.labels[0]
                            : document.querySelector('label[for="'+CSS.escape(opt.id)+'"]');
                        if (le) lbl = le.innerText.toLowerCase().trim();
                        const val = (opt.value||'').toLowerCase();
                        if (item.soloSel || val === ansL || lbl === ansL || val.includes(ansL) || ansL.includes(val) ||
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
        "gname": f.get("gname", ""), "soloSel": bool(f.get("soloSel", False)),
        # EEO fields resolved via the live-combobox path (see the "eeo"
        # branch above) were already clicked directly through Playwright —
        # forcing their answer blank here keeps this generic blast-fill pass
        # from also touching them (a react-select widget doesn't respond to
        # a raw .value= assignment; see the Country block comment for why
        # that already failed once for a different field).
        "answer": "" if f.get("label", "") in eeo_live_filled else str(answers.get(f.get("label", ""), ""))
    } for f in fields])) or 0

    # ── Required-field completeness check — for live-submit gating ───────────
    # A field lands here (no key in `answers`) only if it was routed to
    # essays/eeo_skipped/uncached above — i.e. exactly the fields already
    # logged to stuck_questions.json. If any of THOSE are also required,
    # this application cannot be honestly completed; apply_to_greenhouse_job()
    # uses this list to refuse to submit rather than leave a required field
    # blank. Never computed from a guess — straight from this same fill pass.
    unresolved_required = [
        f.get("label", "") for f in fields
        if f.get("required") and f.get("label", "") not in answers
    ]

    return {
        "filled": filled,
        "total_fields": len(fields),
        "answers": dict(answers),
        "unresolved_required": unresolved_required,
    }

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
    """Guest-apply flow, single page. Returns (success: bool, reason: str, record: dict).

    `record` is the full audit record for this job — job identity, every
    field the form had and the value (if any) resolved for it, and the
    submit outcome — always returned regardless of what happened, so the
    caller can log it. In LIVE mode (dry_run=False) the caller is expected
    to write `record` to data/submitted_applications.json via
    _log_submitted_application(); this function doesn't write it itself so
    dry-run callers (which pass dry_run=True) don't have to special-case
    skipping that write.

    Required-field completeness gate (added 2026-08-26, --live): after
    filling, if any REQUIRED field has no truthful answer (still an
    essay/EEO/uncached question with nothing resolved for it), Submit is
    NEVER clicked for this job, live or not — the honest-answer rules do
    not get relaxed just because --live is on. The job is skipped and the
    reason is both printed and included in `record`."""
    title   = job.get("title", "")
    company = job.get("company", "")
    job_url = job.get("url", "")
    jd_text = job.get("description", "")

    record = {
        "timestamp": datetime.now().isoformat(),
        "title": title, "company": company, "url": job_url,
        "dry_run": dry_run,
        "submitted": False, "success": False, "skipped_incomplete": False,
        "fields": {}, "unresolved_required": [], "reason": "",
    }

    print(f"          🌐 {job_url[:70]}")
    # Retry on transient network-class errors only (ERR_NETWORK_CHANGED,
    # ERR_CONNECTION_RESET, ERR_INTERNET_DISCONNECTED, ERR_NAME_NOT_RESOLVED,
    # ERR_TIMED_OUT, ...) — added 2026-09-01, evidence: two DoorDash jobs in
    # one run (2026-09-01, ~13:28) both failed with the SAME
    # "net::ERR_NETWORK_CHANGED" on page.goto and were skipped outright with
    # no retry, even though this class of error is normally a brief,
    # one-off blip (wifi handoff, VPN reconnect) that a plain reload clears.
    # A non-network exception (timeout waiting on a selector, a real 404,
    # etc.) is NOT retried — those aren't transient and retrying them would
    # just burn 2x the time for the same outcome.
    _NETWORK_ERR_SIGNALS = (
        "err_network_changed", "err_internet_disconnected", "err_connection_reset",
        "err_connection_closed", "err_connection_refused", "err_name_not_resolved",
        "err_timed_out", "err_connection_timed_out", "err_socket_not_connected",
    )
    last_exc = None
    for attempt in range(3):
        try:
            if attempt > 0:
                print(f"          🔁 navigation retry {attempt}/2 after network error...")
                time.sleep(4)
            page.goto(job_url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(2)
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if not any(sig in str(e).lower() for sig in _NETWORK_ERR_SIGNALS):
                break   # not a transient network error — don't retry, fail now
    if last_exc is not None:
        record["reason"] = f"navigation failed: {last_exc}"
        return False, record["reason"], record

    _dismiss_cookie_banner(page)

    if not title or not jd_text:
        info = extract_greenhouse_job(page)
        title   = title   or info.get("title", "")
        company = company or info.get("company", "")
        jd_text = jd_text or info.get("description", "")
        record["title"], record["company"] = title, company

    print(f"          👆 Opening application form...")
    if not click_greenhouse_apply(page):
        _failure_shot(page, f"no_apply_form_{company}")
        record["reason"] = "could not find/open the application form"
        return False, record["reason"], record
    time.sleep(1)
    _wait_for_dom_stable(page)

    filled_first = _fill_basic_field(page, GH["first_name"], ["first name"], "Raghavendra")
    filled_last  = _fill_basic_field(page, GH["last_name"],  ["last name"],  "Karanam")
    filled_email = _fill_basic_field(page, GH["email"],      ["email"],      cfg.CANDIDATE_EMAIL)
    filled_phone = _fill_basic_field(page, GH["phone"],      ["phone"],      cfg.CANDIDATE_PHONE)
    if not (filled_first and filled_last and filled_email):
        print(f"          ⚠  Could not fill all basic fields "
              f"(first={filled_first} last={filled_last} email={filled_email} phone={filled_phone})")

    # ── Country — see the block comment above _fill_country_field() for why
    # this needs its own dedicated, verified fill instead of the generic
    # dropdown path. Checked/filled BEFORE the generic smart-fill scrape so
    # that scrape sees a genuinely-selected value (or an honestly-still-empty
    # one) rather than a value it wrongly believes already stuck.
    country_missing_required = False
    country_status = _has_country_field(page)
    if country_status.get("present"):
        filled_country = _fill_country_field(page, "United States of America")
        print(f"          {'✅' if filled_country else '⚠ '} Country: "
              f"{'United States of America' if filled_country else 'COULD NOT confirm the widget accepted a selection'}")
        if not filled_country and country_status.get("required"):
            country_missing_required = True
            _failure_shot(page, f"country_fill_failed_{company}")

    uploaded = upload_greenhouse_resume(page, resume_path)
    print(f"          {'✅' if uploaded else '⚠ '} Resume upload: {'attached' if uploaded else 'no upload field found — will need manual attach'}")
    time.sleep(1)

    fill_result = _smart_fill_greenhouse_fields(page, title, company, jd_text)
    time.sleep(1)

    record["fields"] = fill_result["answers"]
    record["unresolved_required"] = fill_result["unresolved_required"]

    # ── Required-field completeness gate — never submit an incomplete form,
    # never fabricate an answer to force one through. This check runs
    # regardless of dry_run, but it only actually changes anything in LIVE
    # mode: dry-run already stops before Submit for every job anyway.
    #
    # Country is checked separately from fill_result["unresolved_required"]
    # and given its own honest reason string, deliberately NOT folded into
    # the "no truthful answer available" message below: that phrasing is
    # for essay/EEO/uncached fields where the real gap is Raghav hasn't
    # given an answer to save. Country's true answer (United States of
    # America) IS known — 2026-08-26's Affirm failure was a UI-automation
    # gap (the widget wouldn't accept the value), not a missing-fact one,
    # and the skip reason should say that plainly rather than implying a
    # question needs manual review it doesn't actually need.
    if country_missing_required:
        reason = ("SKIPPED — Country is required but the page would not accept "
                   "a selection (tried a real <select> and a combobox click-and-choose "
                   "interaction, neither verified — see the country_fill_failed "
                   "screenshot). The answer is known (United States of America); this "
                   "is a form-automation gap, not a missing-fact one — report this "
                   "back rather than retrying, the same failure will repeat.")
        print(f"          🚫 {reason}")
        record["skipped_incomplete"] = True
        record["reason"] = reason
        # Deliberately NOT tagged "no_truthful_answer" — a transient UI-
        # automation gap could well succeed on a plain retry, so this must
        # never be persisted to the skip-cache (see config.py's
        # GREENHOUSE_SKIP_RECHECK_DAYS comment for why).
        record["unresolved_reason_type"] = "country_automation_gap"
        return False, reason, record

    if fill_result["unresolved_required"]:
        reason = ("SKIPPED — required field(s) with no truthful answer available: "
                   + "; ".join(fill_result["unresolved_required"]))
        print(f"          🚫 {reason}")
        print(f"          🚫 Never submitting an incomplete required field, and never "
              f"fabricating an answer to force it through — this job needs your review "
              f"in stuck_questions.json first.")
        record["skipped_incomplete"] = True
        record["reason"] = reason
        # This IS the structural, likely-to-recur reason (an essay/EEO/
        # uncached field genuinely has no truthful answer available) — safe
        # to skip-cache so a re-run doesn't redo all this work on the exact
        # same posting under the exact same pipeline logic.
        record["unresolved_reason_type"] = "no_truthful_answer"
        return False, reason, record

    try:
        ok = submit_greenhouse_application(page, dry_run=dry_run)
    except Exception as e:
        # Hardening (2026-09-09): an exception here — e.g. the browser/page
        # crashing mid-poll right after the REAL Submit click fired — must
        # never turn into a lost record. Without this, the caller's generic
        # `except Exception: record = None` would silently drop an
        # application that may have gone through for real; already_applied()
        # would then never see it and the exact same job could get submitted
        # TWICE on the next run. Tagged "unverified" (not "failed") so the
        # caller logs it distinctly and already_applied() blocks a silent
        # auto-retry until a human checks what actually happened.
        _failure_shot(page, "submit_exception")
        record["submitted"] = not dry_run
        record["success"] = False
        record["unverified"] = True
        record["reason"] = (f"UNVERIFIED — exception during/after the Submit click: {e}. "
                              f"Outcome unknown (may have actually submitted) — check "
                              f"screenshots/browser state manually before retrying.")
        return False, record["reason"], record
    record["submitted"] = not dry_run
    record["success"] = ok
    if ok:
        record["reason"] = "Dry-Run" if dry_run else "confirmed after submit click"
        return True, record["reason"], record
    record["reason"] = "submit not confirmed (see failure screenshot)"
    return False, record["reason"], record

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
    parser.add_argument("--dry-run", action="store_true",
                         help="Stop before the Submit click. This is the DEFAULT behavior "
                              "with or without this flag — only --live turns real submission "
                              "on. Kept as an explicit, harmless flag for backward "
                              "compatibility with existing scripts/schedules that already "
                              "pass it, and as a safety override: if both --live and "
                              "--dry-run are given, --dry-run wins.")
    parser.add_argument("--live", action="store_true",
                         help="Actually click Submit and apply for real. REQUIRED to submit "
                              "anything — without it, every run stops before the Submit click "
                              "no matter what. Before each submit, the form is checked for any "
                              "REQUIRED field with no truthful answer; a job with one is "
                              "skipped (never submitted incomplete, never given a fabricated "
                              "answer) and logged to data/submitted_applications.json with the "
                              "reason. Test on a small batch first: --live --limit 3.")
    parser.add_argument("--url",     type=str, default=None,
                         help="Test one exact Greenhouse job URL directly — skips API-based "
                              "discovery and the fit-score gate entirely, goes straight to "
                              "apply_to_greenhouse_job() for this one posting. Still fully respects "
                              "--dry-run/--live (stops before the Submit click unless --live is set, "
                              "same as the normal path).")
    args = parser.parse_args()
    # Safe-by-default: dry-run unless --live was explicitly passed, and
    # --dry-run (if given) always wins even over --live. `args.dry_run` stays
    # the single flag every downstream check already reads, so nothing else
    # in this file needs to change to get the safer default.
    args.dry_run = args.dry_run or not args.live

    print(f"\n{'='*60}")
    print(f"  Greenhouse Apply Engine  {'[DRY RUN]' if args.dry_run else '[LIVE — will submit for real]'}")
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
        browser = None
        # ── Tier 1 — your real, logged-in Chrome profile (opt-in only) ───────
        # config.GREENHOUSE_CHROME_USER_DATA_DIR is blank by default (see its
        # comment in config.py for why: Chrome allows only one process per
        # user-data-dir, and this pipeline runs on a schedule — launching
        # against your everyday profile while it's already open elsewhere
        # can fail or force-close your own Chrome windows). Only attempted
        # at all if you've explicitly set that value.
        if getattr(cfg, "GREENHOUSE_CHROME_USER_DATA_DIR", ""):
            try:
                browser = pw.chromium.launch_persistent_context(
                    cfg.GREENHOUSE_CHROME_USER_DATA_DIR,
                    headless=False,
                    channel="chrome",
                    viewport={"width": 1366, "height": 900},
                    timeout=60000,
                )
                print(f"  🌐  Using your real Chrome profile: {cfg.GREENHOUSE_CHROME_USER_DATA_DIR}")
            except Exception as real_chrome_err:
                print(f"  ⚠  Could not open your real Chrome profile "
                      f"({str(real_chrome_err)[:100]}) — is Chrome already running on it? "
                      f"Falling back to the pipeline's own separate profile.")
                browser = None

        # ── Tier 2 — the pipeline's own separate, persistent Chrome profile ──
        if browser is None:
            try:
                browser = pw.chromium.launch_persistent_context(
                    str(SESSION_DIR),
                    headless=False,
                    channel="chrome",
                    viewport={"width": 1366, "height": 900},
                    timeout=60000,
                )
                print("  🌐  Using real Google Chrome (channel=chrome), pipeline's own profile "
                      "— for launch stability, not evasion; no automation-hiding flags are set")
            except Exception as chrome_err:
                # ── Tier 3 — bundled Chromium, last resort ────────────────────
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
                success, reason, record = apply_to_greenhouse_job(page, job, resume_path, cover_path, dry_run=args.dry_run)
            except Exception as e:
                success, reason, record = False, str(e), None

            if record and record.get("unverified"):
                status = "Unverified"
            elif record and record.get("skipped_incomplete"):
                status = "Skipped-Incomplete"
            elif args.dry_run:
                status = "Dry-Run"
            else:
                status = "Applied" if success else "Failed"
            print(f"\n  {'🚨' if status == 'Unverified' else '✅' if success else '❌'} {status}: {reason}")
            if status == "Unverified":
                try:
                    notifier.send_alert(
                        subject=f"🚨 Greenhouse — unverified submit: {job['company']}",
                        body=f"{job['title']} @ {job['company']}\n{args.url}\n\n{reason}",
                    )
                except Exception:
                    pass

            if record and not args.dry_run:
                _log_submitted_application(record)

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

            # ── Skip-cache fast path — see config.GREENHOUSE_SKIP_RECHECK_DAYS
            # and _skip_cache_entry_valid()'s comments for the full story:
            # this is what stops a run from re-doing the full score/resume/
            # cover-letter/fill pass (including live Claude essay-draft
            # calls) on a posting that's already known, under this EXACT
            # pipeline version, to have no truthful answer for a required
            # field — which was the actual mechanism behind "every run
            # stalls on DoorDash" (the loop was always structurally able to
            # reach Affirm/Sigma/Chime; it just took far too long re-proving
            # the same doomed jobs every time). No browser/API calls here —
            # nothing but a dict lookup.
            cached_skip = _load_skip_cache().get(_skip_cache_key(url))
            if _skip_cache_entry_valid(cached_skip):
                print(f"  ⏭  SKIP (previously unresolved, unchanged since v{cached_skip['pipeline_version']}, "
                      f"{(datetime.now() - datetime.fromisoformat(cached_skip['timestamp'])).days}d ago): "
                      f"{company} — {title}: {cached_skip['reason'][:110]}")
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
                success, reason, record = apply_to_greenhouse_job(
                    page, job, resume_path, cover_path, dry_run=args.dry_run
                )
            except Exception as e:
                success, reason, record = False, str(e), None

            if record and record.get("unverified"):
                status = "Unverified"
            elif record and record.get("skipped_incomplete"):
                status = "Skipped-Incomplete"
            elif args.dry_run:
                status = "Dry-Run"
            else:
                status = "Applied" if success else "Failed"
            print(f"  {'🚨 UNVERIFIED' if status == 'Unverified' else '✅ SUBMITTED' if (success and not args.dry_run) else '🧪 would submit' if (success and args.dry_run) else '⏭  SKIPPED' if status == 'Skipped-Incomplete' else '❌ FAILED'} "
                  f"— {company} — {title}: {reason}")
            if status == "Unverified":
                try:
                    notifier.send_alert(
                        subject=f"🚨 Greenhouse — unverified submit: {company}",
                        body=f"{title} @ {company}\n{url}\n\n{reason}",
                    )
                except Exception:
                    pass

            # Remember a genuinely-structural skip (never a transient
            # UI-automation gap — see apply_to_greenhouse_job()'s comments)
            # so a later run doesn't redo all this work on the exact same
            # posting under the exact same pipeline version. Written in
            # BOTH dry-run and live mode — the required-field gate computes
            # the same answer either way, and a --dry-run preview run
            # should benefit from the fast-skip too, not just --live.
            if record and record.get("unresolved_reason_type") == "no_truthful_answer":
                _remember_skip(url, company, title, reason)

            if record and not args.dry_run:
                _log_submitted_application(record)

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
