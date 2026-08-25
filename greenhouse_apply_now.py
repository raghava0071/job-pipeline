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
# NOT VERIFIED AGAINST A LIVE POSTING YET — build environment has no network
# path to greenhouse.io to inspect real DOM. Selectors below are Greenhouse's
# well-documented, long-stable field IDs (legacy embed) PLUS a generic
# label-based fallback (covers the newer job-boards.greenhouse.io React UI,
# which uses standard <label for> / aria-label associations instead of those
# IDs). Run with --dry-run against a couple of real job URLs first and watch
# the "📋 N field(s) on this step" / "⚠ could not find" print lines before
# trusting this on a live run — see CLAUDE.md safety workflow.
#
# ARCHITECTURE: mirrors workday_apply_now.py's shape (Google-search discovery,
# jd_parser scoring, resume_builder/cover_letter, answer_cache/claude_answers/
# qa_answers layered form filling, notifier + JSON log) but scoped down —
# Greenhouse guest-apply has no login/account-creation/multi-step wizard, so
# none of that machinery is ported here.
#
# USAGE:
#   python greenhouse_apply_now.py --limit 5
#   python greenhouse_apply_now.py --limit 2 --dry-run
# =============================================================================

import os, sys, time, json, argparse, re, random
from pathlib import Path
from datetime import datetime

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
    whatever element[from point] actually covers it, same pattern already
    proven necessary on Workday (see workday_apply_now.py _click) for
    overlay-covered buttons — kept here since Greenhouse's "Attach" dropzone
    buttons are frequently a styled <label> or <div> sitting over the real
    control, the same class of problem."""
    try:
        el = page.locator(sel).first
        el.wait_for(state="visible", timeout=timeout)
        el.scroll_into_view_if_needed()
        time.sleep(random.uniform(0.2, 0.5))
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

def is_good_level(title: str) -> bool:
    return not any(bad in title.lower() for bad in cfg.SENIOR_WORDS)

DATA_KEYWORDS = [
    "data", "analyst", "analytics", "engineer", "scientist", "machine learning",
    "ml", "ai", "etl", "pipeline", "bi", "business intelligence", "sql",
    "python", "tableau", "power bi", "spark", "databricks", "snowflake",
]

def is_relevant_domain(title: str) -> bool:
    return any(kw in title.lower() for kw in DATA_KEYWORDS)

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
    one of label_words (covers the React job-boards UI)."""
    if not value:
        return False
    if _exists(page, css_sel, timeout=1200):
        try:
            el = page.locator(css_sel).first
            el.click()
            page.keyboard.press("Control+A")
            page.keyboard.type(value, delay=random.randint(30, 60))
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
            el = page.locator(sel).first
            el.click()
            page.keyboard.press("Control+A")
            page.keyboard.type(value, delay=random.randint(30, 60))
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

def _log_stuck_fields(fields: list, job_title: str, company: str) -> None:
    """Reuses the same data/stuck_questions.json shape as indeed/workday so
    there's one place to check for fields that need a manual answer added."""
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
            "status": "no cache/PROFILE_FALLBACK match — needs a manual answer "
                      "added to qa_answers.py or PROFILE_FALLBACK",
        })
        stuck_file.write_text(json.dumps(existing, indent=2))
        print(f"          📝 {len(fields)} field(s) with no answer anywhere — "
              f"logged to data/stuck_questions.json: {[f.get('label','') for f in fields]}")
    except Exception as e:
        print(f"          ⚠  Could not log stuck field(s): {e}")

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
    PROFILE_FALLBACK = {
        "work authorization": "Yes", "authorized to work": "Yes", "legally authorized": "Yes",
        "sponsorship": "No", "require sponsorship": "No", "visa": "F-1 STEM OPT",
        "salary": _smart_salary, "compensation": _smart_salary, "hourly rate": "40",
        "start date": "2 weeks", "notice period": "2 weeks", "when can you start": "2 weeks",
        "relocat": "No", "remote": "Yes",
        "gender": "I don't wish to answer", "ethnicity": "I don't wish to answer",
        "race": "I don't wish to answer", "hispanic": "I don't wish to answer",
        "veteran": "I am not a protected veteran", "disability": "I don't wish to answer",
        "years of experience": "3", "background check": "Yes", "drug test": "Yes",
        "18 or older": "Yes", "us citizen": "No", "green card": "No", "permanent resident": "No",
        "linkedin": "https://www.linkedin.com/in/yourusername",
        "github": "https://github.com/raghava0071",
        "how did you hear": "LinkedIn / Online Job Board",
        "pronoun": "He/Him",
    }

    answers, uncached = {}, []
    for f in fields:
        lbl = f.get("label", "")
        lbl_l = lbl.lower().strip().rstrip(" *:?")

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

        matched = False
        for kw, val in PROFILE_FALLBACK.items():
            if kw in lbl_l:
                answers[lbl] = val
                print(f"             ✔ PROFILE '{lbl}' → '{val}'")
                if _claude_ans:
                    _claude_ans.save(lbl, val)
                matched = True
                break
        if not matched:
            uncached.append(f)

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

def _is_confirmed(page) -> bool:
    body = (_safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or "")
    url = (page.url or "").lower()
    return any(s in body for s in [
        "thanks for applying", "thank you for applying",
        "your application has been submitted", "application was sent",
    ]) or any(s in url for s in ["confirmation", "thank-you", "thanks"])

def submit_greenhouse_application(page, dry_run: bool = False) -> bool:
    print(f"          📋 Review & Submit")
    if dry_run:
        print(f"          🏁 DRY RUN — Would click Submit here")
        return True

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
        time.sleep(4)
        if _is_confirmed(page):
            print(f"          🎉 Application submitted!")
            return True

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

    if not _is_confirmed(page):
        _failure_shot(page, "submit_not_confirmed")
    return _is_confirmed(page)

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

# ── Discovery: Google search ───────────────────────────────────────────────────

def _search_greenhouse_on_google(page, query: str) -> list:
    """Search Google for Greenhouse-hosted postings. Same pattern as
    workday_apply_now.py's _search_workday_on_google: load the results page,
    extract links from raw HTML immediately, no clicks/interaction with
    Google itself."""
    import urllib.parse
    search_url = "https://www.google.com/search?" + urllib.parse.urlencode({
        "q": f'(site:boards.greenhouse.io OR site:job-boards.greenhouse.io) "{query}"',
        "num": "10",
        "tbs": "qdr:w",
    })
    try:
        page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
        time.sleep(0.5)
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠  Google search failed: {e}")
        return []

    current_url = page.url or ""
    if "accounts.google.com" in current_url or "sorry/index" in current_url:
        print(f"  ⚠  Google blocked — skipping Google search for '{query}'")
        return []

    links = _safe_eval(page, """
        () => {
            const results = [];
            const seen = new Set();
            for (const a of document.querySelectorAll('a[href]')) {
                let href = a.getAttribute('href') || '';
                if (href.startsWith('/url?')) {
                    const m = href.match(/[?&]q=([^&]+)/);
                    if (m) href = decodeURIComponent(m[1]);
                }
                if (!href.includes('greenhouse.io')) continue;
                if (!/\\/jobs\\//.test(href)) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                const sub = (href.match(/greenhouse\\.io\\/([^\\/?]+)/) || [])[1] || '';
                const company = sub.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                const title = (a.innerText || a.textContent || '').trim().split('\\n')[0];
                results.push({ title: title || '', company, url: href, description: '' });
            }
            return results.slice(0, 10);
        }
    """, []) or []

    print(f"  🔍 Google→Greenhouse: {len(links)} jobs for '{query}'")
    return links

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
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
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1366, "height": 900},
                timeout=60000,
            )
            print("  🌐  Using real Google Chrome (channel=chrome)")
        except Exception as chrome_err:
            print(f"  ⚠  Real Chrome not available ({str(chrome_err)[:80]}) — falling back to bundled Chromium")
            browser = pw.chromium.launch_persistent_context(
                str(SESSION_DIR),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1366, "height": 900},
                timeout=60000,
            )
        page = browser.pages[0] if browser.pages else browser.new_page()

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
            if title and (not is_good_level(title) or not is_relevant_domain(title)):
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

        print(f"\n  🔍 Searching Google for Greenhouse jobs "
              f"(site:boards.greenhouse.io / site:job-boards.greenhouse.io)...")
        queries = getattr(cfg, "GREENHOUSE_QUERIES", cfg.TARGET_ROLES)
        for query in queries:
            if applied >= args.limit:
                break
            jobs = _search_greenhouse_on_google(page, query)
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
