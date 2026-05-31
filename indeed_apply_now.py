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

import sys, time, json, argparse, re, random
from pathlib import Path
from datetime import datetime

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import config as cfg
import answer_cache as _cache
import notifier
try:
    import qa_answers as _qa
except ImportError:
    _qa = None

DATA_DIR      = cfg.DATA_DIR
SESSION_DIR   = cfg.BASE_DIR / ".indeed_session"
LOG_FILE      = cfg.BASE_DIR / "data" / "indeed_applied_log.json"
SCREENSHOTS   = cfg.BASE_DIR / "screenshots"

SCREENSHOTS.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
cfg.RESUMES_DIR.mkdir(parents=True, exist_ok=True)
cfg.COVER_DIR.mkdir(parents=True, exist_ok=True)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("pip install playwright && python -m playwright install chromium")

# ── Search queries (reuse from config) ────────────────────────────────────────
SEARCH_QUERIES = cfg.LINKEDIN_QUERIES  # same keywords work for Indeed

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
    # fromage=7 = last 7 days, iafilter=1 = Indeed Apply only
    return "https://www.indeed.com/jobs?" + urllib.parse.urlencode({
        "q": kw,
        "l": "United States",
        "sort": "date",
        "fromage": "7",
        "iafilter": "1",   # Indeed Apply only
        "start": start,
    })

def load_log():
    try:
        return json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
    except:
        return []

def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2))

def already_applied(url, log, title="", company=""):
    """Dedup by URL (normalized) AND by company+title pair."""
    key = re.sub(r'\?.*', '', url).rstrip("/")
    for e in log:
        if e.get("status") not in ("Applied", "Already Applied"):
            continue
        if re.sub(r'\?.*', '', e.get("url","")).rstrip("/") == key:
            return True
        if title and company:
            if (e.get("company","").lower().strip() == company.lower().strip()
                    and e.get("title","").lower().strip() == title.lower().strip()):
                return True
    return False

def ensure_login(page):
    """Check Indeed login; prompt user if not logged in."""
    page.goto("https://www.indeed.com/", wait_until="domcontentloaded", timeout=20000)
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
    print("\n  🔐  Log in to Indeed then press ENTER...")
    input("  → ")

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
    """Click the Indeed Apply button — tries every known selector + JS fallback."""
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
    time.sleep(2)
    return bool(clicked)

def smart_fill_step(page, profile_text, job_title, company, resume_filename=""):
    """
    Extract → answer (cache/Claude) → fill by CSS selector (no re-labeling).

    FLOW:
      1. JS extracts fields: label, type, options, CSS selector (unique per element)
      2. Cache lookup by label → instant answer if cached
      3. Uncached → Claude API → save to cache
      4. JS fills each element by its stored CSS selector — no label re-detection
    """
    import anthropic, os, json as _json

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
        clicked = page.evaluate("""
            (target) => {
                // Find radio group for resume selection
                var radios = Array.from(document.querySelectorAll('input[type=radio]'))
                    .filter(function(r){ return r.offsetParent; });
                if (!radios.length) return 0;
                // Try to match target filename, else click first
                var pick = target
                    ? (radios.find(function(r){
                          var lbl = '';
                          if (r.id) {
                              var le = document.querySelector('label[for="'+r.id+'"]');
                              if (le) lbl = le.innerText;
                          }
                          return lbl.includes(target) || (r.value || '').includes(target);
                      }) || radios[0])
                    : radios[0];
                if (pick && !pick.checked) { pick.click(); }
                ['input','change','blur'].forEach(function(ev){
                    pick.dispatchEvent(new Event(ev, {bubbles:true}));
                });
                return 1;
            }
        """, target)
        print(f"          ✔  Resume radio {'clicked' if clicked else 'already selected'}")
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
    answers = {}       # label → answer string
    uncached = []

    print(f"          🗄  Cache lookup...")
    for f in fields:
        lbl = f.get("label","")

        # 1. Check qa_answers.py FIRST (master Q&A file — highest priority)
        qa_hit = _qa.get_answer(lbl) if (_qa and lbl) else None
        if qa_hit is not None:
            print(f"             ✔ QA FILE    '{lbl}' → '{qa_hit}'")
            answers[lbl] = qa_hit
            continue

        # 2. Check SQLite cache
        cached = _cache.get(lbl) if lbl else None
        if cached is not None:
            print(f"             ✔ CACHE HIT  '{lbl}' → '{cached}'")
            answers[lbl] = cached
        else:
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

        _claude = anthropic.Anthropic(api_key=_api_key)

        fields_desc = "\n".join(
            f'{i+1}. label="{f["label"]}" type={f["type"]}'
            + (f' options={f["options"]}' if f.get("options") else '')
            + (' [REQUIRED]' if f.get("required") else '')
            for i, f in enumerate(uncached)
        )

        prompt = f"""Fill out this job application form step on Indeed for: {job_title} at {company}

CANDIDATE PROFILE:
{profile_text}

FIELDS TO FILL:
{fields_desc}

Return ONLY a JSON object keyed by the EXACT label text:
{{"label text": "answer", ...}}

Rules:
- select/radio/checkbox: copy one option EXACTLY as written
- years of experience questions: answer with a number
- work authorization: "Yes"
- salary expectation: "85000"
- notice period / start date: "2 weeks" or "Immediately"
- relocation: "No"
- cover letter / additional info: write 2 sentences about the candidate
- Never mention Community Dreams Foundation or Mobile Stage Pros
- For text fields with no obvious answer: leave blank ("")"""

        print(f"          🤖 Calling Claude API for {len(uncached)} field(s)...")
        try:
            resp = _claude.messages.create(
                model=os.environ.get("CLAUDE_MODEL","claude-haiku-4-5-20251001"),
                max_tokens=1500,
                messages=[{"role":"user","content":prompt}]
            )
            raw = resp.content[0].text.strip()
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            claude_answers = {}
            if m:
                claude_answers = _json.loads(m.group(0))
                for lbl, ans in claude_answers.items():
                    answers[lbl] = ans
                    if ans:
                        _cache.save(lbl, ans)
                        print(f"             ✔ Claude → '{lbl}': '{ans}'  (saved to cache)")
                    else:
                        print(f"             · Claude → '{lbl}': (blank)")
            print(f"          🤖 Claude answered {len(claude_answers)}/{len(uncached)} uncached fields")
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
            "salary": "85000",
            "compensation": "85000",
            "expected pay": "85000",
            "desired pay": "85000",
            "desired salary": "85000",
            "start date": "2 weeks",
            "notice period": "2 weeks",
            "available": "2 weeks",
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
        }
        for f in uncached:
            lbl = f.get("label", "")
            if lbl in answers:
                continue  # already answered by Claude
            lbl_l = lbl.lower()
            for kw, val in FALLBACK.items():
                if kw in lbl_l:
                    answers[lbl] = val
                    print(f"             ↩  Fallback → '{lbl}': '{val}'")
                    break
            else:
                # Last resort: leave text fields as empty string rather than skip
                if f.get("type") in ("text","textarea","tel","email","number"):
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
        fill_items.append({
            "sel":     f.get("sel", ""),
            "label":   lbl,
            "type":    f.get("type", "text"),
            "options": f.get("options", []),
            "gname":   f.get("gname", ""),
            "answer":  str(ans) if ans is not None else "",
        })
        print(f"             → fill '{lbl}' = '{ans}'  sel={f.get('sel','')[:50]}")

    # ── Step 4: Fill DOM elements by CSS selector (live refs, no re-labeling) ──
    filled = page.evaluate("""
        (itemsJson) => {
            var items = JSON.parse(itemsJson);
            var filled = 0;

            function fireEvents(el) {
                ['input','change','blur'].forEach(function(ev) {
                    el.dispatchEvent(new Event(ev, {bubbles:true}));
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
                            if (!opt.checked) {
                                opt.click();
                                // Fire React synthetic events so state updates
                                ['click','change','input'].forEach(function(ev) {
                                    opt.dispatchEvent(new Event(ev, {bubbles:true}));
                                });
                            }
                            fireEvents(opt);
                            filled++;
                            break;
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
                } else {
                    el.value = ans;
                    fireEvents(el);
                    filled++;
                }
            }
            return filled;
        }
    """, _json.dumps(fill_items))

    filled_n = filled or 0
    print(f"          ✏️  DOM fill result: {filled_n}/{len(fields)} field(s) written to page")
    return filled_n

CONFIRM_PHRASES = [
    'application submitted', 'successfully applied',
    'your application has been', 'application received',
    'thanks for applying', 'thank you for applying',
    'application complete',
]

def _check_and_handle_captcha(page, title="", company=""):
    """
    Detect CAPTCHA (recaptcha bframe) on the page and handle it:
      1. Send email alert immediately
      2. Resize CAPTCHA iframe so Verify button is visible
      3. Wait up to 90 seconds for user to solve it
      4. Return True if solved, False if timed out
    Call this at the START of every step loop iteration.
    """
    try:
        captcha_visible = any(
            "bframe" in (f.url or "") for f in list(page.frames)
        )
        if not captcha_visible:
            return True  # no CAPTCHA, all good

        print(f"\n          🚨🚨🚨  CAPTCHA DETECTED — ACTION REQUIRED  🚨🚨🚨")
        print(f"          👉 Open the browser on your Mac and solve the CAPTCHA NOW")
        print(f"          ⏳ Waiting up to 90 seconds...")

        # Send email alert
        try:
            notifier.send_alert(
                subject=f"🚨 CAPTCHA — {title} @ {company} — Solve NOW",
                body=(
                    f"CAPTCHA appeared on job application:\n\n"
                    f"  Job:     {title}\n"
                    f"  Company: {company}\n\n"
                    f"Open the browser on your Mac and solve the image CAPTCHA.\n"
                    f"Pipeline is paused for 5 minutes waiting for you.\n\n"
                    f"If you don't solve it in time, this job will be skipped."
                )
            )
        except Exception as e:
            print(f"          ⚠  Could not send CAPTCHA email: {e}")

        # Mac system notification popup — visible even when terminal is behind other windows
        try:
            import subprocess
            subprocess.run([
                "osascript", "-e",
                f'display notification "Solve CAPTCHA for {title} @ {company}. Browser is open. You have 5 minutes." with title "🚨 Pipeline CAPTCHA Alert" sound name "Ping"'
            ], timeout=5)
            print(f"          🔔 Mac system notification sent")
        except Exception as e:
            print(f"          ⚠  Mac notification failed: {e}")

        # Fix CAPTCHA window so Verify button is fully visible and clickable
        try:
            page.evaluate("""
                () => {
                    // Step 1: Find the bframe iframe
                    const bframe = Array.from(document.querySelectorAll('iframe'))
                        .find(f => f.src && f.src.includes('bframe'));
                    if (!bframe) return;

                    // Step 2: Walk up to parent elements and remove overflow:hidden / clipping
                    let el = bframe;
                    for (let i = 0; i < 10; i++) {
                        el = el.parentElement;
                        if (!el || el === document.body) break;
                        el.style.overflow = 'visible';
                        el.style.height = 'auto';
                        el.style.maxHeight = 'none';
                        el.style.clip = 'none';
                        el.style.clipPath = 'none';
                    }

                    // Step 3: Position the iframe itself — large, centered, always on top
                    bframe.style.cssText = [
                        'position: fixed !important',
                        'top: 40px !important',
                        'left: 50% !important',
                        'transform: translateX(-50%) !important',
                        'width: 310px !important',
                        'height: 500px !important',
                        'z-index: 2147483647 !important',
                        'border: 4px solid #ff0000 !important',
                        'border-radius: 10px !important',
                        'background: white !important',
                        'box-shadow: 0 8px 32px rgba(0,0,0,0.5) !important',
                    ].join(';');

                    // Step 4: Also fix the reCAPTCHA anchor checkbox if present
                    document.querySelectorAll('iframe').forEach(f => {
                        if (f.src && f.src.includes('anchor')) {
                            f.style.zIndex = '2147483646';
                        }
                    });
                }
            """)
            print(f"          🔲 CAPTCHA window fixed — Verify button is now fully visible")
            print(f"          💡 TIP: If still stuck, click CAPTCHA area then press Tab+Enter")
        except Exception as e:
            print(f"          ⚠  CAPTCHA resize failed: {e}")

        # Also maximize the browser viewport so there's more room
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
        except:
            pass

        # Wait up to 5 minutes for CAPTCHA to be solved
        CAPTCHA_TIMEOUT = 300
        for i in range(CAPTCHA_TIMEOUT):
            time.sleep(1)
            try:
                still_captcha = any(
                    "bframe" in (f.url or "") for f in list(page.frames)
                )
                if not still_captcha:
                    print(f"          ✅ CAPTCHA solved! Waiting 5s before continuing...")
                    time.sleep(5)  # let Indeed settle before next action
                    return True
            except:
                pass
            if i % 30 == 29:
                remaining = CAPTCHA_TIMEOUT - i - 1
                print(f"          ⏳ Still waiting for CAPTCHA... ({remaining}s left)")

        print(f"          ❌ CAPTCHA not solved in 5 minutes — skipping this job")
        return False

    except Exception as e:
        print(f"          ⚠  CAPTCHA check error (continuing): {e}")
        return True  # don't crash — assume no CAPTCHA if check itself fails


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
    """Check any frame for confirmation text."""
    all_frames = [page.main_frame] + list(page.frames)
    for frame in all_frames:
        body = _safe_eval(frame, "() => document.body.innerText.toLowerCase()", "")
        if any(p in body for p in CONFIRM_PHRASES):
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
        except:
            pass

    # JS fallback — scrolls into view, checks aria-disabled (Indeed's pattern)
    if verbose:
        print(f"          ↩  Playwright failed — JS click fallback")
    result = _safe_eval(frame, """
        () => {
            const kws = ['continue','next','submit','review','apply','send'];
            const BACK = ['back','previous','cancel','unable to','report','feedback',
                          'issue','problem','submit feedback','report an issue',
                          'give feedback','accessibility','skip'];
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

def _upload_resume(page, resume_path, done_flag):
    """Upload resume once — checks all frames."""
    if done_flag[0]:
        return
    all_frames = [page.main_frame] + list(page.frames)
    for i, frame in enumerate(all_frames):
        try:
            fi = frame.locator('input[type="file"]').first
            if fi.count() > 0:
                frame_url = ""
                try: frame_url = frame.url or ""
                except: pass
                print(f"          📎 File input found in frame[{i}] ({frame_url[:50] or 'main'}) — uploading resume...")
                fi.set_input_files(str(resume_path))
                time.sleep(1)
                done_flag[0] = True
                print(f"          📎 Resume uploaded ✅  ({Path(resume_path).name})")
                return
        except Exception as e:
            pass
    print(f"          ℹ  No file input found (resume upload skipped this step)")

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
    title   = job.get("title", "")
    company = job.get("company", "")

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

    # ── Walk multi-step form ───────────────────────────────────────────────────
    step           = 0
    max_steps      = 20
    last_btn       = None
    same_btn_count = 0
    submitted      = False
    resume_done    = [False]
    last_url       = ""
    same_url_count = 0

    try:
        while step < max_steps and not submitted:
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
                if same_url_count >= 4:
                    print(f"          ❌ Stuck on same URL for {same_url_count} steps — giving up on this job")
                    break
            else:
                same_url_count = 0
                same_btn_count = 0   # URL changed = new page = reset button repeat counter
                last_url = current_step_url

            # ── CAPTCHA check at start of every step ──────────────────────────
            captcha_ok = _check_and_handle_captcha(apply_page, title, company)
            if not captcha_ok:
                break  # skip this job — CAPTCHA timed out

            if _is_confirmed(apply_page):
                print(f"          🎉 Confirmation text detected — application submitted!")
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
                filled = smart_fill_step(form_ctx, profile_text, title, company, resume_name)
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
                    for frame in pg.frames:
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

                MAX_SUBMIT_ATTEMPTS = 5
                for attempt in range(1, MAX_SUBMIT_ATTEMPTS + 1):
                    print(f"          🚀 LIVE: Submit attempt {attempt}/{MAX_SUBMIT_ATTEMPTS}...")

                    _click_submit_btn(apply_page)
                    time.sleep(3)  # wait for CAPTCHA or confirmation to appear

                    # Check for CAPTCHA that appeared after clicking Submit
                    captcha_ok = _check_and_handle_captcha(apply_page, title, company)
                    if not captcha_ok:
                        print(f"          ❌ CAPTCHA timed out — skipping job")
                        submitted = False
                        break

                    # Check for confirmation
                    time.sleep(3)
                    if _is_confirmed(apply_page):
                        print(f"          🎉 Application submitted and confirmed!")
                        submitted = True
                        break

                    current_url = apply_page.url or ""
                    if "review" not in current_url and "smartapply" not in current_url:
                        print(f"          🎉 Navigated away from review — submitted!")
                        submitted = True
                        break

                    if attempt < MAX_SUBMIT_ATTEMPTS:
                        print(f"          🔄 No confirmation yet — waiting 8s then retrying Submit...")
                        time.sleep(8)  # longer wait between attempts reduces CAPTCHA triggers
                    else:
                        print(f"          ❌ Gave up after {MAX_SUBMIT_ATTEMPTS} submit attempts")
                        submitted = False

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

            # Upload resume once — checks all frames
            _upload_resume(apply_page, resume_path, resume_done)

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
                filled = smart_fill_step(form_ctx, profile_text, title, company, resume_name)
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
            filled = smart_fill_step(form_ctx, profile_text, title, company, resume_name)
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

                if same_btn_count >= 3:
                    print(f"          ⚠  Stuck on same button 3x — force-submit")
                    _click_nav(nav_frame, "submit")
                    time.sleep(3)
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
    parser.add_argument("--limit",   type=int, default=5,     help="Max applications per run")
    parser.add_argument("--dry-run", action="store_true",      help="Score + build but don't submit")
    args = parser.parse_args()

    DRY_RUN   = args.dry_run
    MAX_APPLY = args.limit

    print(f"\n{'='*60}")
    print(f"  Indeed Apply Engine  {'[DRY RUN]' if DRY_RUN else ''}")
    print(f"  Limit: {MAX_APPLY} applications")
    print(f"{'='*60}\n")

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
        "Address *":           "7330 W Atlantic Ave",
        "Address":             "7330 W Atlantic Ave",
        "Street address":      "7330 W Atlantic Ave Apt 215",
        "City *":              "Delray Beach",
        "City":                "Delray Beach",
        "City, State":         "Delray Beach, FL",
        "State/Province *":    "Florida",
        "State/Province":      "Florida",
        "State":               "Florida",
        "Postal/ZIP *":        "33444",
        "Postal/ZIP":          "33444",
        "Zip code":            "33444",
        "ZIP Code":            "33444",
        # Contact
        "Phone":               "5613017799",
        "Phone number":        "5613017799",
        "Type phone number":   "5613017799",
        "Mobile number":       "5613017799",
        "Cell phone":          "5613017799",
        "LinkedIn URL":        "https://www.linkedin.com/in/raghavendra-karanam",
        "LinkedIn Profile":    "https://www.linkedin.com/in/raghavendra-karanam",
        # Work authorization & visa
        "Are you legally authorized to work in the United States?": "Yes",
        "Are you authorized to work in the US?":                    "Yes",
        "Do you require visa sponsorship now or in the future?":    "No",
        "Will you now or in the future require sponsorship?":       "No",
        "Desired Pay":         "85000",
        "Desired Salary":      "85000",
        "Expected Salary":     "85000",
        "Date Available":      "2 weeks",
        "Start Date":          "2 weeks",
        "Website, Blog or Portfolio": "https://www.linkedin.com/in/raghavendra-karanam",
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

    full_profile    = rp.PROFILE
    profile_summary = ce.build_profile_summary(full_profile)

    log = load_log()
    applied_count  = 0
    scored_count   = 0
    skipped_count  = 0
    seen_this_run  = set()   # dedup within this session (company+title)

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            str(SESSION_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        ensure_login(page)

        for query in SEARCH_QUERIES:
            if applied_count >= MAX_APPLY:
                break

            print(f"\n🔍  Query: {query}")
            url = build_indeed_url(query)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except:
                print(f"  ⚠  Failed to load search page")
                continue
            time.sleep(3)

            # Get all job cards on the page
            job_cards = page.evaluate("""
                () => {
                    const cards = Array.from(document.querySelectorAll(
                        '[data-jk], .job_seen_beacon, [class*="jobCard"], .resultContent'
                    ));
                    return cards.map(c => {
                        const jk = c.getAttribute('data-jk') || c.id || '';
                        const titleEl = c.querySelector('h2 a, [data-testid="job-title"], .jobTitle a');
                        const coEl    = c.querySelector('[data-testid="company-name"], .companyName');
                        const locEl   = c.querySelector('[data-testid="text-location"], .companyLocation');
                        return {
                            jk:      jk,
                            title:   titleEl ? titleEl.innerText.trim() : '',
                            company: coEl    ? coEl.innerText.trim()    : '',
                            location:locEl   ? locEl.innerText.trim()   : '',
                            href:    titleEl ? (titleEl.getAttribute('href') || '') : '',
                        };
                    }).filter(c => c.title.length > 2);
                }
            """) or []

            print(f"  Found {len(job_cards)} cards")

            for card in job_cards:
                if applied_count >= MAX_APPLY:
                    break

                title   = card.get("title","")
                company = card.get("company","")
                jk      = card.get("jk","")
                href    = card.get("href","")

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
                if already_applied(job_url, log, title, company):
                    print(f"  ↩  {company} — {title} → already applied (dedup)")
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
                    print(f"  ⏭  External apply detected — skipping (no resume build, no API call)")
                    skipped_count += 1
                    continue

                jd = details.get("description","")
                live_url = details.get("jobUrl", job_url)

                # ── Score with Claude ──────────────────────────────────────────
                result = ce.score_fit(profile_summary, jd, title, company)
                score  = result.get("score", 0) if isinstance(result, dict) else int(result)
                scored_count += 1
                grade  = result.get("grade", "") if isinstance(result, dict) else ""
                print(f"  🎯 Fit score: {score}%  {grade}  {'✅' if score >= cfg.FIT_THRESHOLD else '❌'}")

                if score < cfg.FIT_THRESHOLD:
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

                # ── Build cover letter ─────────────────────────────────────────
                cover_letter_path = ""
                try:
                    cl_text = ce.write_cover_letter(
                        full_profile.get("name", "Raghavendra Karanam"),
                        profile_summary, jd, title, company
                    )
                    cover_letter_path = cl_mod.save_cover_letter(cl_text, title, company)
                    print(f"  ✅ Cover letter saved")
                except Exception as e:
                    print(f"  ⚠  Cover letter skipped: {e}")

                # ── Apply ──────────────────────────────────────────────────────
                print(f"  🚀 Applying via Indeed Apply...")
                job_info = {"title": title, "company": company}

                try:
                    success, reason = apply_to_job(
                        page, browser, job_info, resume_path, cover_letter_path,
                        profile_text=profile_summary, dry_run=DRY_RUN
                    )
                except Exception as e:
                    success, reason = False, str(e)

                status = "Applied" if (success and not DRY_RUN) else ("Dry-Run" if DRY_RUN else "Failed")
                icon   = "✅" if success else "❌"
                print(f"  {icon} {status}: {reason}")

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

        browser.close()

    # ── Session summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Indeed session done")
    print(f"  ✅ Applied:  {applied_count}")
    print(f"  🎯 Scored:   {scored_count}")
    print(f"  ⏭  Skipped:  {skipped_count}")
    print(f"{'='*60}\n")

    _cache.print_stats()

    notifier.notify_session_done(applied_count, scored_count, skipped_count)


if __name__ == "__main__":
    main()
