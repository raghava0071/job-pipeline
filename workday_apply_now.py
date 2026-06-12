#!/usr/bin/env python3
# =============================================================================
# WORKDAY_APPLY_NOW.PY — Automated Workday job application engine
#
# REWRITTEN based on reference projects:
#   - jasonchen270/workday-autofill  (Python + Playwright + CDP)
#   - ubangura/Workday-Application-Automator  (Puppeteer, 67 stars)
#
# KEY FIXES vs previous version:
#   1. Uses data-automation-id selectors throughout (never CSS classes)
#   2. Per-step handlers that wait for the correct page div to appear
#   3. Human-like typing delays (50-150ms per key) to avoid bot detection
#   4. Waits for resume parse to complete before continuing
#   5. Job source: queue from LinkedIn/Indeed + curated company list
#   6. Proper account creation flow matching Workday's exact button IDs
#
# WORKDAY FORM STEPS (data-automation-id for each page):
#   contactInformationPage  → name, address, phone
#   myExperiencePage        → work history, education, resume upload
#   applicationQuestions    → custom Q&A per company (Claude fills)
#   voluntaryDisclosuresPage → gender, ethnicity, veteran
#   selfIdentificationPage  → disability
#   reviewPage              → review + submit
#
# USAGE:
#   python workday_apply_now.py --limit 5
#   python workday_apply_now.py --limit 2 --dry-run
#   python workday_apply_now.py --queue-only   (only process LinkedIn/Indeed queue)
# =============================================================================

import os, sys, time, json, argparse, re, random
from pathlib import Path
from datetime import datetime

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import config as cfg
import answer_cache as _cache
import notifier
import mail_reader
import secure_store
try:
    from salary_helper import pick_salary as _pick_salary, salary_rule_for_prompt as _salary_rule
except ImportError:
    _pick_salary = lambda jd, title: "75000"
    _salary_rule = lambda jd, title: "- salary: answer 75000 (plain number only)"

try:
    import qa_answers as _qa
except ImportError:
    _qa = None

try:
    import claude_answers as _claude_ans
except ImportError:
    _claude_ans = None

DATA_DIR      = cfg.DATA_DIR
SESSION_DIR   = cfg.BASE_DIR / ".workday_session"
LOG_FILE      = cfg.BASE_DIR / "data" / "workday_applied_log.json"
SCREENSHOTS   = cfg.BASE_DIR / "screenshots"
WD_QUEUE_FILE = cfg.BASE_DIR / "data" / "workday_queue.json"

for d in [SCREENSHOTS, DATA_DIR, SESSION_DIR]:
    d.mkdir(parents=True, exist_ok=True)
cfg.RESUMES_DIR.mkdir(parents=True, exist_ok=True)
cfg.COVER_DIR.mkdir(parents=True, exist_ok=True)

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("pip install playwright && python -m playwright install chromium")

# ── Known Workday selectors (proven from reference projects) ──────────────────
# These data-automation-id values are consistent across ALL Workday portals.

WD = {
    # Auth
    "sign_in_btn":        'button[data-automation-id="utilityButtonSignIn"]',
    "email_input":        'input[data-automation-id="email"]',
    "password_input":     'input[data-automation-id="password"]',
    "sign_in_submit":     'button[data-automation-id="signInSubmitButton"]',
    "create_acct_link":   'button[data-automation-id="createAccountLink"]',
    "verify_password":    'input[data-automation-id="verifyPassword"]',
    "create_acct_check":  'input[data-automation-id="createAccountCheckbox"]',
    "create_acct_submit": 'button[data-automation-id="createAccountSubmitButton"]',
    "error_msg":          'div[data-automation-id="errorMessage"]',

    # Apply flow
    "apply_btn":          'a[data-automation-id="adventureButton"]',
    "apply_manually":     'a[data-automation-id="applyManually"]',
    "next_btn":           'button[data-automation-id="bottom-navigation-next-button"]',
    "save_continue":      'button[data-automation-id="bottom-navigation-saveAndNext-button"]',
    "submit_btn":         'button[data-automation-id="bottom-navigation-next-button"]',  # same ID on submit page

    # Page detection
    "contact_page":       'div[data-automation-id="contactInformationPage"]',
    "experience_page":    'div[data-automation-id="myExperiencePage"]',
    "voluntary_page":     'div[data-automation-id="voluntaryDisclosuresPage"]',
    "self_id_page":       'div[data-automation-id="selfIdentificationPage"]',
    "review_page":        'div[data-automation-id="reviewPage"]',
    "app_questions_page": 'div[data-automation-id="questionsPage"]',

    # My Information fields
    "first_name":         'input[data-automation-id="legalNameSection_firstName"]',
    "last_name":          'input[data-automation-id="legalNameSection_lastName"]',
    "address_line1":      'input[data-automation-id="addressSection_addressLine1"]',
    "city":               'input[data-automation-id="addressSection_city"]',
    "state_btn":          'button[data-automation-id="addressSection_countryRegion"]',
    "state_btn_alt":      'button[data-automation-id="addressSection_stateProvince"]',
    "postal_code":        'input[data-automation-id="addressSection_postalCode"]',
    "phone_type_btn":     'button[data-automation-id="phone-device-type"]',
    "phone_number":       'input[data-automation-id="phone-number"]',

    # My Experience fields
    "resume_upload":      'input[data-automation-id="file-upload-input-ref"]',
    "linkedin_url":       'input[data-automation-id="linkedinQuestion"]',
    "add_website":        'div[data-automation-id="websiteSection"] button[data-automation-id="Add"]',

    # Work experience
    "add_work_first":     'div[data-automation-id="workExperienceSection"] button[data-automation-id*="add"]',
    "add_work_more":      'div[data-automation-id="workExperienceSection"] button[data-automation-id*="Add"]',

    # Education
    "add_education":      'div[data-automation-id="educationSection"] button[data-automation-id="Add"]',
    "degree_btn":         'button[data-automation-id="degree"]',
    "gpa_input":          'input[data-automation-id="gpa"]',

    # Voluntary disclosures
    "gender_btn":         'button[data-automation-id="gender"]',
    "hispanic_btn":       'button[data-automation-id="hispanicOrLatino"]',
    "ethnicity_btn":      'button[data-automation-id="ethnicityDropdown"]',
    "veteran_btn":        'button[data-automation-id="veteranStatus"]',
    "agreement_check":    'input[data-automation-id="agreementCheckbox"]',

    # Self identification
    "full_name_input":    'input[data-automation-id="name"]',
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_eval(ctx, js, default=None):
    try:
        return ctx.evaluate(js)
    except:
        return default

def _failure_shot(page, tag: str) -> str:
    """Screenshot the current page on any sign-in / submission failure."""
    try:
        safe = re.sub(r'[^a-zA-Z0-9_-]+', '_', tag)[:60]
        p = SCREENSHOTS / f"fail_{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
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
    except:
        return False

def _click(page, sel, timeout=8000):
    """Click a Workday element — never hangs. Falls back to JS click."""
    try:
        el = page.locator(sel).first
        el.wait_for(state="visible", timeout=timeout)
        el.scroll_into_view_if_needed()
        time.sleep(random.uniform(0.3, 0.7))
        # Use force=True + timeout so click never hangs on navigation/overlay
        try:
            el.click(timeout=4000, force=False)
        except Exception:
            # JS fallback — bypasses overlays and disabled state
            page.evaluate(
                "(s)=>{const b=document.querySelector(s);"
                "if(b){b.removeAttribute('disabled');b.removeAttribute('aria-disabled');"
                "b.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));}}",
                sel
            )
        return True
    except Exception:
        return False

def _fill(page, sel, value: str, timeout=8000, human_delay=True):
    """Fill a Workday input — clears then types. Uses fill() which already clears."""
    try:
        el = page.locator(sel).first
        el.wait_for(state="visible", timeout=timeout)
        el.scroll_into_view_if_needed()
        time.sleep(0.2)
        if human_delay and value:
            # Click, select all, delete, then type character by character
            el.click()
            time.sleep(0.1)
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(0.1)
            page.keyboard.type(value, delay=random.randint(50, 100))
        else:
            # fill() clears the field first automatically
            el.fill(value)
        return True
    except:
        return False

def _select_dropdown(page, btn_sel, value: str, timeout=8000):
    """Click a Workday dropdown button then type to filter and press Enter."""
    try:
        _click(page, btn_sel, timeout)
        time.sleep(0.5)
        page.keyboard.type(value, delay=80)
        time.sleep(0.5)
        page.keyboard.press("Enter")
        time.sleep(0.3)
        return True
    except:
        return False

def _click_next(page):
    """Click the Next/Save and Continue button — uses short timeouts + force-click fallback."""
    time.sleep(random.uniform(0.5, 1.0))

    # Try all known Workday next-button selectors with short timeouts
    for sel in [
        'button[data-automation-id="bottom-navigation-next-button"]',
        'button[data-automation-id="bottom-navigation-saveAndNext-button"]',
        'button[data-automation-id="bottom-navigation-save-button"]',
    ]:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                el.scroll_into_view_if_needed()
                # Remove aria-disabled if set (Workday pattern)
                page.evaluate(f"""
                    () => {{
                        const b = document.querySelector('{sel}');
                        if (b) {{
                            b.removeAttribute('aria-disabled');
                            b.removeAttribute('disabled');
                        }}
                    }}
                """)
                time.sleep(0.3)
                el.click(timeout=3000)
                print(f"          ✔  Clicked: {sel.split('=')[1].strip(chr(34))}")
                return True
        except:
            pass

    # JS exhaustive fallback — finds any forward button
    result = _safe_eval(page, """
        () => {
            const kws = ['save and continue','save & continue','next','submit','continue','apply'];
            const SKIP = ['back','previous','cancel','close','exit','discard'];
            // Sort: visible+enabled first
            const btns = Array.from(document.querySelectorAll('button,[role=button],input[type=submit]'))
                .filter(b => b.offsetParent);
            for (const b of btns) {
                const t = (b.innerText||b.textContent||b.value||'').toLowerCase().trim();
                if (!t || SKIP.some(w => t.includes(w))) continue;
                if (kws.some(k => t.includes(k))) {
                    b.removeAttribute('aria-disabled');
                    b.removeAttribute('disabled');
                    b.scrollIntoView({block:'center'});
                    b.click();
                    return t;
                }
            }
            // Last resort: find a forward navigation button — skip all non-navigation
            const SKIP_WORDS = ['forgot','sign in','log in','create account','cancel','close',
                                 'privacy','terms','back','previous','read more','read less',
                                 'show more','show less','expand','collapse','help','about',
                                 'already have','sign up','register'];
            const FORWARD_WORDS = ['next','continue','save','submit','apply','proceed','done'];
            const all = Array.from(document.querySelectorAll('button,[role=button],input[type=submit]'))
                .filter(b => b.offsetParent);
            // First pass: find a button with a forward word
            for (const b of all) {
                const t = (b.innerText||b.textContent||b.value||'').toLowerCase().trim();
                if (!t || SKIP_WORDS.some(w => t.includes(w))) continue;
                if (FORWARD_WORDS.some(w => t.includes(w))) {
                    b.removeAttribute('aria-disabled');
                    b.removeAttribute('disabled');
                    b.scrollIntoView({block:'center'});
                    b.click();
                    return 'forward-btn: ' + t;
                }
            }
            return null;
        }
    """, None)
    if result:
        print(f"          ✔  JS next: '{result}'")
    return bool(result)

CONFIRM_PHRASES = [
    'application submitted', 'successfully submitted', 'thank you for applying',
    'thanks for applying', 'your application has been received',
    'you have applied', 'application complete',
]

def _is_confirmed(page) -> bool:
    body = _safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or ""
    return any(p in body for p in CONFIRM_PHRASES)

# ── Form-page navigation helpers (progression + required-field fixes) ──────────

_NEXT_SELECTORS = [
    'button[data-automation-id="bottom-navigation-next-button"]',
    'button[data-automation-id="bottom-navigation-saveAndNext-button"]',
    'button[data-automation-id="bottom-navigation-save-button"]',
]

def _btn_disabled(page, sel) -> bool:
    try:
        return page.evaluate(
            "(s)=>{const b=document.querySelector(s);"
            "return b?(b.disabled||b.getAttribute('aria-disabled')==='true'):true;}", sel)
    except Exception:
        return True

def _js_click(page, sel) -> bool:
    try:
        return page.evaluate(
            "(s)=>{const b=document.querySelector(s);if(b){b.removeAttribute('disabled');"
            "b.removeAttribute('aria-disabled');b.scrollIntoView({block:'center'});b.click();return true;}return false;}", sel)
    except Exception:
        return False

def _next_disabled(page) -> bool:
    for s in _NEXT_SELECTORS:
        try:
            if page.locator(s).first.count() > 0:
                return _btn_disabled(page, s)
        except Exception:
            pass
    return False

def _get_page_marker(page) -> str:
    """A signature of the current form page (active step + heading + path) used
    to verify real page progression (BUG 3)."""
    return _safe_eval(page, r"""
        () => {
            let step='';
            const a=document.querySelector('[data-automation-id="progressBarActiveStep"],[aria-current="step"]');
            if(a) step=(a.innerText||'').trim().slice(0,50);
            let h='';
            for(const s of ['[data-automation-id="pageHeader"]','h1','h2','[role=heading]']){
                const el=document.querySelector(s);
                if(el&&(el.innerText||'').trim()){h=el.innerText.trim().split('\n')[0].slice(0,80);break;}
            }
            return (step+'|'+h+'|'+location.pathname).slice(0,200);
        }
    """, "") or ""

def _transition_shot(page, tag):
    """Screenshot at a page transition."""
    try:
        safe = re.sub(r'[^a-zA-Z0-9_-]+', '_', tag)[:50]
        p = SCREENSHOTS / f"step_{safe}_{datetime.now().strftime('%H%M%S')}.png"
        page.screenshot(path=str(p))
        print(f"          📸 screenshots/{p.name}")
    except Exception:
        pass

def _scroll_page(page):
    """Scroll slowly top→bottom→top to trigger lazy-loaded fields."""
    _safe_eval(page, """
        () => { const h=document.body.scrollHeight; for(let y=0;y<=h;y+=300){window.scrollTo(0,y);} window.scrollTo(0,0); }
    """)
    time.sleep(0.8)

def _fill_required_fields(page) -> list:
    """BUG 2: scroll to trigger lazy fields, fill empty selects/radios/required
    checkboxes, then return the list of required fields still empty."""
    _scroll_page(page)
    # Workday custom dropdown that commonly blocks Next on the contact page
    _select_country_phone_code(page)
    filled = _safe_eval(page, r"""
        () => {
            function fire(el){['input','change','blur'].forEach(ev=>el.dispatchEvent(new Event(ev,{bubbles:true})));}
            const out={selects:0,radios:0,checks:0,customDropdowns:0};

            // 1. Native <select> — pick first valid option
            for(const sel of document.querySelectorAll('select')){
                if(!sel.offsetParent||sel.value) continue;
                const opt=Array.from(sel.options).find(o=>o.value&&o.text.trim()&&!/^(select|choose|--)/i.test(o.text.trim()));
                if(opt){sel.value=opt.value;fire(sel);out.selects++;}
            }

            // 2. Radio groups — select first option in any unset group
            const seen={};
            for(const r of document.querySelectorAll('input[type=radio]')){
                if(!r.offsetParent) continue;
                const g=r.name||r.getAttribute('data-automation-id')||'';
                if(!g||seen[g]) continue; seen[g]=true;
                const grp=Array.from(document.querySelectorAll('input[type=radio]'))
                    .filter(x=>(x.name||x.getAttribute('data-automation-id'))===g&&x.offsetParent);
                if(grp.length&&!grp.some(x=>x.checked)){grp[0].click();fire(grp[0]);out.radios++;}
            }

            // 3. Required checkboxes — check them
            for(const c of document.querySelectorAll('input[type=checkbox]')){
                if(!c.offsetParent) continue;
                if((c.required||c.getAttribute('aria-required')==='true')&&!c.checked){c.click();fire(c);out.checks++;}
            }

            // 4. Workday custom button-dropdowns with * label that have no value set
            //    (these are <button> elements that open a listbox — not native <select>)
            for(const btn of document.querySelectorAll('button[data-automation-id]')){
                if(!btn.offsetParent) continue;
                // Find parent label with *
                let lbl=''; let p=btn.parentElement;
                for(let i=0;i<6&&p;i++,p=p.parentElement){
                    const h=p.querySelector('label,legend,[data-automation-id*="Label"]');
                    if(h&&(h.innerText||'').includes('*')){lbl=h.innerText;break;}
                }
                if(!lbl.includes('*')) continue;
                // If button text looks like a placeholder (empty or "Select"), open + pick first option
                const t=(btn.innerText||'').trim();
                if(t&&!/^(select|choose|--|please)/i.test(t)) continue;
                btn.click();
                setTimeout(()=>{
                    const opt=Array.from(document.querySelectorAll('[role=option],[data-automation-id="promptOption"]'))
                        .filter(o=>o.offsetParent)[0];
                    if(opt){opt.click();out.customDropdowns++;}
                },400);
            }

            return out;
        }
    """, {}) or {}
    if filled and any(filled.get(k) for k in ("selects", "radios", "checks", "customDropdowns")):
        print(f"          🧩 Auto-filled required → selects={filled.get('selects',0)} "
              f"radios={filled.get('radios',0)} checks={filled.get('checks',0)} "
              f"customDropdowns={filled.get('customDropdowns',0)}")
    empties = _safe_eval(page, r"""
        () => {
            const out=[]; const seen=new Set();

            // Helper: get a human-readable label for a field
            function getLabel(el) {
                // aria-label first
                const al = el.getAttribute('aria-label') || '';
                if (al) return al.replace('*','').trim();
                // associated <label> tag
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) return (lbl.innerText||'').replace('*','').trim().split('\n')[0];
                }
                // data-automation-id
                return el.getAttribute('data-automation-id') || el.name || el.id || 'unknown';
            }

            // Collect ALL visible input/select/textarea elements
            const allEls = Array.from(document.querySelectorAll('input,select,textarea'))
                .filter(el => el.offsetParent && el.type !== 'hidden');

            for (const el of allEls) {
                // Determine if this field is required by ANY signal:
                // 1. aria-required="true"
                // 2. required attribute
                // 3. associated label contains "*"
                // 4. ancestor div label contains "*"
                let isRequired = el.required || el.getAttribute('aria-required') === 'true';

                if (!isRequired) {
                    // Check label for asterisk (Workday marks required fields with *)
                    let labelText = '';
                    if (el.id) {
                        const lbl = document.querySelector('label[for="' + el.id + '"]');
                        if (lbl) labelText = lbl.innerText || '';
                    }
                    if (!labelText) {
                        // Walk up to find a label/legend containing *
                        let p = el.parentElement;
                        for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
                            const h = p.querySelector('label,legend,[data-automation-id*="Label"]');
                            if (h && (h.innerText||'').includes('*')) {
                                labelText = h.innerText;
                                break;
                            }
                        }
                    }
                    if (labelText.includes('*')) isRequired = true;
                }

                if (!isRequired) continue;

                // Check if the field has a value
                let v = el.value;
                if (el.type === 'checkbox') v = el.checked ? 'x' : '';
                if (el.type === 'radio') {
                    const g = el.name;
                    v = g ? (document.querySelector('input[name="' + g + '"]:checked') ? 'x' : '') : (el.checked ? 'x' : '');
                }

                if (!v || !String(v).trim()) {
                    const key = getLabel(el) + (el.offsetParent ? '' : '(hidden)');
                    if (!seen.has(key)) { seen.add(key); out.push(key); }
                }
            }
            return out.slice(0, 25);
        }
    """, []) or []
    return empties

def _select_country_phone_code(page) -> bool:
    """Select 'United States' in Workday's Country/Province Phone Code dropdown.
    It is a custom button+listbox widget (not a native <select>), so it needs a
    click → type-ahead → option-click, not a value fill."""
    sel_used = None
    btn = None
    for s in [
        'button[data-automation-id="countryPhoneCode"]',
        'button[data-automation-id="phoneNumber--countryPhoneCode"]',
        '[data-automation-id="phoneNumber--countryPhoneCode"] button',
        'button[data-automation-id*="ountryPhoneCode"]',
    ]:
        try:
            loc = page.locator(s).first
            if loc.count() > 0 and loc.is_visible(timeout=800):
                btn, sel_used = loc, s
                break
        except Exception:
            pass
    if btn is None:
        return False

    # Already set correctly? Button text should look like a country name or dial code.
    # Re-select if it looks like a phone number was stuffed in (all digits).
    try:
        cur = (btn.inner_text() or "").strip()
        looks_like_phone = bool(cur) and re.match(r'^[\d\s\(\)\-\+]{5,}$', cur)
        looks_like_country = bool(cur) and not looks_like_phone and "select" not in cur.lower()
        if looks_like_country:
            return True
        # If it looks like a phone number, fall through and re-select
    except Exception:
        pass

    try:
        btn.scroll_into_view_if_needed()
        btn.click()
        time.sleep(0.8)
    except Exception:
        return False

    # Type-ahead to filter the listbox
    try:
        page.keyboard.type("United States", delay=60)
        time.sleep(0.9)
    except Exception:
        pass

    # Click the matching option
    chosen = _safe_eval(page, r"""
        () => {
            const opts = Array.from(document.querySelectorAll(
                '[role=option],li[role="option"],[data-automation-id="promptOption"],div[data-automation-id*="romptOption"]'
            )).filter(o => o.offsetParent);
            let want = opts.find(o => /united states/i.test(o.innerText||''));
            if (!want) want = opts.find(o => /\(\+?1\)|\bUSA\b/.test(o.innerText||''));
            if (want) { want.click(); return (want.innerText||'').trim().slice(0,50); }
            return '';
        }
    """, "")
    if not chosen:
        try:
            page.keyboard.press("Enter")
            time.sleep(0.4)
        except Exception:
            pass
    time.sleep(0.5)

    # Verify it took
    try:
        cur = (page.locator(sel_used).first.inner_text() or "").strip()
        ok = bool(cur) and "select" not in cur.lower()
        print(f"          🌐 Country Phone Code → '{cur[:40]}'" if ok
              else f"          ⚠  Country Phone Code still unset (chose='{chosen}')")
        return ok
    except Exception:
        return bool(chosen)

def _advance_page(page, label="page") -> bool:
    """Fill required fields (BUG 2), click Next, and verify the page actually
    changed (BUG 3). Screenshots before and after the transition."""
    before = _get_page_marker(page)
    _transition_shot(page, f"before_{label}")

    empties = _fill_required_fields(page)
    if empties:
        print(f"          ⚠  Empty required field(s): {empties}")
    if _next_disabled(page):
        print(f"          ⚠  Next button still disabled after filling")
    else:
        print(f"          ✅ Next button is clickable")

    _click_next(page)
    for _ in range(10):                       # up to ~5s
        time.sleep(0.5)
        if _get_page_marker(page) != before:
            print(f"          ➡  Advanced past '{label}' — Next click succeeded")
            _transition_shot(page, f"nextOK_{label}")
            return True

    # Did not progress — try once more (BUG 3)
    print(f"          ⛔ Stuck on same page ('{label}') — refilling required fields")
    empties2 = _fill_required_fields(page)
    if empties2:
        print(f"          ❌ Still-empty required field(s) blocking Next: {empties2}")
    if not _next_disabled(page):
        print(f"          ✅ Next button is clickable (retry)")
    _click_next(page)
    for _ in range(10):
        time.sleep(0.5)
        if _get_page_marker(page) != before:
            print(f"          ➡  Advanced past '{label}' (after retry) — Next click succeeded")
            _transition_shot(page, f"nextOK_{label}")
            return True

    print(f"          ❌ Did not progress past '{label}'")
    _transition_shot(page, f"stuck_{label}")
    return False

def _submit_with_fallbacks(page, sel, label="submit") -> str:
    """BUG 1: scroll the button into view, wait until enabled, then try three
    methods in order — Playwright click, Enter on password, JS click — and
    return the method that actually changed the page."""
    before = _get_page_marker(page)
    try:
        b = page.locator(sel).first
        if b.count() == 0:
            return "not-found"
        b.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    # wait up to ~4s until enabled (strip disabled attrs proactively)
    for _ in range(8):
        if not _btn_disabled(page, sel):
            break
        try:
            page.evaluate("(s)=>{const b=document.querySelector(s);if(b){b.removeAttribute('disabled');"
                          "b.removeAttribute('aria-disabled');}}", sel)
        except Exception:
            pass
        time.sleep(0.5)
    # method 1 — Playwright click
    try:
        page.locator(sel).first.click(timeout=3000)
        time.sleep(2)
        if _get_page_marker(page) != before:
            return "playwright-click"
    except Exception:
        pass
    # method 2 — Enter on password field
    try:
        page.locator('input[type="password"]').last.press("Enter")
        time.sleep(2)
        if _get_page_marker(page) != before:
            return "enter-key"
    except Exception:
        pass
    # method 3 — JS click
    if _js_click(page, sel):
        time.sleep(2)
        if _get_page_marker(page) != before:
            return "js-click"
    return "tried-all(no-change)"

# ── Job filtering ─────────────────────────────────────────────────────────────

def is_workday_url(url: str) -> bool:
    return "myworkdayjobs.com" in url or "myworkday.com" in url

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
    except:
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
            if (e.get("company","").lower().strip() == company.lower().strip()
                    and e.get("title","").lower().strip() == title.lower().strip()):
                return True
    return False

# ── Queue ─────────────────────────────────────────────────────────────────────

def load_wd_queue() -> list:
    try:
        return json.loads(WD_QUEUE_FILE.read_text()) if WD_QUEUE_FILE.exists() else []
    except:
        return []

def save_wd_queue(queue: list):
    WD_QUEUE_FILE.write_text(json.dumps(queue, indent=2))

def add_to_wd_queue(job: dict):
    queue = load_wd_queue()
    url = job.get("url", "")
    if not any(q.get("url","") == url for q in queue):
        queue.append({**job, "queued_at": datetime.now().isoformat(), "status": "pending"})
        save_wd_queue(queue)

# ── OTP / intervention detection ──────────────────────────────────────────────

_OTP_SIGNALS = [
    "enter the code", "verification code", "one-time", "otp",
    "we sent a code", "6-digit", "security code", "authentication code",
]
_EMAIL_VERIFY_SIGNALS = [
    "click the link", "verification link", "check your inbox",
    "confirm your account", "activate your account",
]
_SECURITY_Q_SIGNALS = [
    "security question", "what was your", "what is the name",
    "what city were you born",
]

def _detect_intervention(page) -> str:
    body = _safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or ""
    if any(p in body for p in _OTP_SIGNALS):
        has_input = _safe_eval(page, """
            () => !!document.querySelector(
                'input[type="text"][maxlength], input[type="number"], input[inputmode="numeric"]'
            )
        """, False)
        if has_input:
            return "otp"
    if any(p in body for p in _EMAIL_VERIFY_SIGNALS):
        return "email_verify"
    if any(p in body for p in _SECURITY_Q_SIGNALS):
        return "security_question"
    return ""

def handle_intervention(page, kind: str, company: str, job_title: str) -> bool:
    labels = {
        "otp": "🔐 OTP Code Required",
        "email_verify": "📧 Email Verification Required",
        "security_question": "❓ Security Question",
    }
    print(f"\n          {labels.get(kind,'⚠ Auth Wall')}  ({company})")

    if kind == "otp":
        print(f"          🤖 Reading OTP from Gmail...")
        result = mail_reader.wait_for_otp(company=company, timeout_secs=300, since_minutes=10)
        if result["type"] == "otp":
            code = result["code"]
            print(f"          ✅ Got OTP: {code}")
            try:
                inp = page.locator(
                    'input[type="text"][maxlength], input[type="number"], input[inputmode="numeric"]'
                ).first
                inp.fill(code)
                time.sleep(1)
                _click_next(page)
                time.sleep(3)
                return True
            except:
                pass
        elif result["type"] == "verify_link":
            page.goto(result["link"], wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            return True

    elif kind == "email_verify":
        print(f"\n          📧 EMAIL VERIFICATION REQUIRED for {company}")
        print(f"          👉 Please CHECK GMAIL (raghavendrakaranam30@gmail.com),")
        print(f"             click the Workday verification link, then return here.")
        print(f"          🤖 Meanwhile, auto-searching Gmail for the verify link...")
        result = mail_reader.wait_for_otp(company=company, timeout_secs=300, since_minutes=10)
        if result["type"] == "verify_link":
            print(f"          ✅ Found verification link — opening it")
            page.goto(result["link"], wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            return True

    elif kind == "security_question":
        q_text = _safe_eval(page, """
            () => {
                const els = document.querySelectorAll('label, legend, p, span, [data-automation-id]');
                for (const el of els) {
                    const t = (el.innerText || '').trim();
                    if (t.length > 5 && t.length < 200 && el.offsetParent) return t;
                }
                return '';
            }
        """, "") or ""
        print(f"          ❓ Question: '{q_text[:80]}'")

        # Try qa_answers first (work email, city born, etc.)
        answer = None
        if _qa:
            answer = _qa.get_answer(q_text)
        # Then try secure_store
        if not answer:
            answer = secure_store.get_security_answer(q_text)
        # Fallback for common patterns
        if not answer:
            q_l = q_text.lower()
            if "work email" in q_l or "email" in q_l:
                answer = "raghavendrakaranam30@gmail.com"
            elif "city" in q_l and "born" in q_l:
                answer = "Hyderabad"
            elif "pet" in q_l:
                answer = "Tommy"
            elif "school" in q_l:
                answer = "St. Mary's"
            elif "street" in q_l or "grew up" in q_l:
                answer = "Military Trl"
            elif "mother" in q_l or "maiden" in q_l:
                answer = "Karanam"

        if answer:
            print(f"          ✅ Answering security question: '{answer}'")
            try:
                # Find the input field for this security question
                inp = page.locator('input[type="text"]:visible, input[type="email"]:visible').first
                if inp.count():
                    inp.click()
                    inp.fill(answer)
                    time.sleep(0.5)
                    _click_next(page)
                    time.sleep(3)
                    return True
            except Exception as e:
                print(f"          ⚠  Could not fill security answer: {e}")
        else:
            # Unknown security question — skip the job, don't wait 10 min
            print(f"          ❌ Unknown security question — skipping job (add answer to qa_answers.py)")
            return False

    # Fallback — alert + wait
    print(f"          🚨 Could not auto-handle — sending alert, waiting 10 min...")
    try:
        notifier.send_alert(
            subject=f"🚨 Workday {labels.get(kind,'Auth')} — {company}",
            body=f"Please handle {kind} in the browser for {company} ({job_title}).\nPipeline paused 10 minutes."
        )
        import subprocess
        subprocess.run(["osascript", "-e",
            f'display notification "Handle {kind} in browser — {company}" with title "🚨 Workday" sound name "Ping"'
        ], timeout=5)
    except:
        pass

    # Wait for page to move past the block
    NORMAL = ["my information", "my experience", "application questions",
              "voluntary", "self identify", "review", "application submitted"]
    for i in range(300):
        time.sleep(2)
        body = _safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or ""
        if any(s in body for s in NORMAL):
            print(f"          ✅ Cleared!")
            return True
        if i % 30 == 29:
            print(f"          ⏳ {(300-(i+1))*2}s remaining...")
    print(f"          ❌ Timed out")
    return False

# ── Workday account management ────────────────────────────────────────────────

def _get_company_key(url: str) -> str:
    m = re.search(r'https?://([^.]+)\.', url)
    return m.group(1).lower() if m else url[:30]

def _get_wd_password() -> str:
    pwd = os.environ.get("WORKDAY_PASSWORD", "")
    if not pwd:
        env = cfg.BASE_DIR / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("WORKDAY_PASSWORD="):
                    pwd = line.split("=", 1)[1].strip().strip('"').strip("'")
    return pwd or "Raghava@2025!"

def workday_sign_in(page, email: str, password: str) -> bool:
    """Sign in to Workday. Returns True once we've left the sign-in page —
    whether we land on the application form, the job posting, or the candidate
    dashboard. Workday frequently redirects to the job page (not the form)
    after login, so requiring a form container is a false negative."""
    print(f"          🔑 Signing in...")
    try:
        # The Workday auth page usually has a "Sign In" link/button to switch
        # from the default Create Account view to the Sign In form.
        if _exists(page, 'button[data-automation-id="signInLink"]', timeout=1500):
            _click(page, 'button[data-automation-id="signInLink"]')
            time.sleep(1.0)
        elif _exists(page, WD["sign_in_btn"], timeout=2000):
            _click(page, WD["sign_in_btn"])
            time.sleep(1.5)

        # IMPORTANT: Workday renders BOTH the Sign-In and Create-Account forms in
        # the DOM at once, with Create-Account first. A bare querySelector hits
        # the Create-Account email/password (leaving Sign-In empty → submit
        # silently no-ops). So scope the fill to the form that actually contains
        # signInSubmitButton — the smallest ancestor with a password field that
        # does NOT contain the Create-Account button.
        fill_report = page.evaluate("""
            (args) => {
                const email = args[0], password = args[1];
                function reactFill(el, value) {
                    if (!el) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value');
                    if (setter && setter.set) setter.set.call(el, value);
                    else el.value = value;
                    ['input','change','blur','keyup'].forEach(ev =>
                        el.dispatchEvent(new Event(ev, {bubbles:true})));
                    return true;
                }
                const submit = document.querySelector('button[data-automation-id="signInSubmitButton"]');
                let scope = submit ? submit.parentElement : null;
                while (scope && scope !== document.body) {
                    const hasPwd = scope.querySelector('input[type="password"]');
                    const hasCreate = scope.querySelector('button[data-automation-id="createAccountSubmitButton"]');
                    if (hasPwd && !hasCreate) break;
                    scope = scope.parentElement;
                }
                if (!scope || scope === document.body)
                    scope = (submit && submit.closest('form')) || document;

                const em = scope.querySelector('input[data-automation-id="email"], input[type="email"]')
                        || document.querySelector('input[data-automation-id="email"], input[type="email"]');
                const pw = scope.querySelector('input[data-automation-id="password"], input[type="password"]')
                        || document.querySelector('input[data-automation-id="password"], input[type="password"]');
                const r = [];
                r.push(reactFill(em, email) ? 'email:ok' : 'email:MISS');
                r.push(reactFill(pw, password) ? 'pwd:ok' : 'pwd:MISS');
                r.push('emailLen:' + (em ? em.value.length : -1));
                r.push('pwdLen:'   + (pw ? pw.value.length : -1));
                return r.join(', ');
            }
        """, [email, password])
        print(f"          📝 Sign-in fill: {fill_report}")
        time.sleep(0.6)

        # Dismiss any browser dialog before clicking (Restore pages popup etc.)
        try:
            page.keyboard.press("Escape")
            time.sleep(0.3)
        except Exception:
            pass

        # Submit sign-in — try multiple methods in order
        submitted_signin = False

        # Method 1: Enter key on password field (most reliable for React forms)
        try:
            pwd_loc = page.locator('input[data-automation-id="password"], input[type="password"]').last
            if pwd_loc.count() and pwd_loc.is_visible(timeout=1000):
                pwd_loc.click()
                time.sleep(0.2)
                pwd_loc.press("Enter")
                submitted_signin = True
                print(f"          ↪  Sign-in submitted (Enter key)")
        except Exception:
            pass

        # Method 2: Playwright click on sign-in button by data-automation-id
        if not submitted_signin:
            try:
                btn = page.locator('button[data-automation-id="signInSubmitButton"]').first
                if btn.count() and btn.is_visible(timeout=1000):
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=3000)
                    submitted_signin = True
                    print(f"          ↪  Sign-in submitted (button click)")
            except Exception:
                pass

        # Method 3: Text-based button search ("Sign In", "Log In", etc.)
        if not submitted_signin:
            for txt in ["Sign In", "Log In", "Login", "Sign in"]:
                try:
                    btn = page.locator(f"button:has-text('{txt}')").first
                    if btn.count() and btn.is_visible(timeout=500):
                        btn.scroll_into_view_if_needed()
                        btn.click(timeout=3000)
                        submitted_signin = True
                        print(f"          ↪  Sign-in submitted (text: '{txt}')")
                        break
                except Exception:
                    pass

        # Method 4: JS click fallback
        if not submitted_signin:
            page.evaluate("""
                () => {
                    const sels = [
                        'button[data-automation-id="signInSubmitButton"]',
                        'button[type="submit"]',
                    ];
                    for (const s of sels) {
                        const b = document.querySelector(s);
                        if (b && b.offsetParent) {
                            b.removeAttribute('disabled');
                            b.removeAttribute('aria-disabled');
                            b.scrollIntoView({block:'center'});
                            b.click();
                            return;
                        }
                    }
                }
            """)
            print(f"          ↪  Sign-in submitted (JS fallback)")

        # Success = we left the sign-in page with no error. Accept the
        # application form, the job posting, OR the candidate dashboard.
        ERR_PHRASES = [
            "wrong email", "incorrect", "does not match", "account might be locked",
            "account is locked", "too many", "no longer valid", "invalid",
        ]
        for _ in range(20):
            time.sleep(0.5)

            # Credentials rejected → real failure (Workday's error can take a few
            # seconds to render and does not always use the errorMessage div).
            body = _safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or ""
            if _exists(page, WD["error_msg"], timeout=200) or any(p in body for p in ERR_PHRASES):
                print(f"          ❌ Sign-in rejected — bad credentials or locked account")
                _failure_shot(page, "signin_rejected")
                return False

            # Application form visible → definitely signed in
            if any(_exists(page, sel, timeout=300) for sel in [
                WD["contact_page"], WD["experience_page"],
                WD["app_questions_page"], WD["voluntary_page"], WD["review_page"],
            ]):
                print(f"          ✅ Signed in — application form visible")
                return True

            # Sign-in form is gone (no email/password input, no submit button) →
            # we navigated away to the job page or candidate dashboard = success
            still_on_signin = (
                _exists(page, WD["sign_in_submit"], timeout=300) or
                (_exists(page, WD["email_input"],    timeout=200) and
                 _exists(page, WD["password_input"], timeout=200))
            )
            if not still_on_signin:
                print(f"          ✅ Signed in — left auth page (job page / dashboard)")
                return True

        print(f"          ⚠  Sign-in — still on auth page after 10s")
        _failure_shot(page, "signin_timeout")
        return False
    except Exception as e:
        print(f"          ⚠  Sign-in failed: {e}")
        _failure_shot(page, "signin_error")
        return False

def workday_create_account(page, email: str, password: str, company_key: str) -> bool:
    """
    Create a Workday account using Playwright native interactions.
    Uses fill()/press() instead of JS so React state updates correctly,
    which is required for the Create Account button to become enabled.
    """
    print(f"          ✨ Creating account for {company_key}...")
    try:
        # Navigate to Create Account form if not already there
        if _exists(page, WD["create_acct_link"], timeout=3000):
            _click(page, WD["create_acct_link"])
            time.sleep(1.5)

        # Wait for verifyPassword field — confirms Create Account form is loaded
        if not _exists(page, WD["verify_password"], timeout=6000):
            print(f"          ⚠  Create Account form not visible")
            return False

        # ── Fill using Playwright native fill() scoped to VISIBLE fields only ──
        # IMPORTANT: Workday renders Sign-In AND Create Account forms in the DOM
        # at once. We must fill only the VISIBLE fields to avoid filling the
        # hidden sign-in form which leaves Create Account email empty.

        def _fill_visible(selectors: list, value: str, label: str) -> bool:
            """Fill the first VISIBLE input matching any of the selectors."""
            for sel in selectors:
                try:
                    els = page.locator(sel).all()
                    for el in els:
                        if el.is_visible(timeout=500):
                            el.scroll_into_view_if_needed()
                            el.click(timeout=2000)
                            time.sleep(0.15)
                            el.fill(value)
                            time.sleep(0.15)
                            el.press("Tab")
                            time.sleep(0.2)
                            # Verify it actually filled
                            val = el.input_value()
                            if val and len(val) > 0:
                                print(f"          {label} filled (len={len(val)})")
                                return True
                except Exception:
                    continue
            print(f"          ⚠  Could not fill {label}")
            return False

        # Email — only the visible one (Create Account form)
        _fill_visible(
            ['input[data-automation-id="email"]', 'input[type="email"]'],
            email, "📧 Email"
        )

        # Password — first visible password field
        _fill_visible(
            ['input[data-automation-id="password"]', 'input[type="password"]'],
            password, "🔑 Password"
        )

        # Verify Password — the verify field (only exists in Create Account)
        _fill_visible(
            ['input[data-automation-id="verifyPassword"]'],
            password, "🔑 Verify password"
        )

        # Checkbox — agree to terms (check if visible)
        try:
            for chk_sel in ['input[data-automation-id="createAccountCheckbox"]',
                             'input[type="checkbox"]']:
                chks = page.locator(chk_sel).all()
                for chk in chks:
                    if chk.is_visible(timeout=400):
                        if not chk.is_checked():
                            chk.click()
                            time.sleep(0.3)
                        print(f"          ☑  Checkbox checked")
                        break
                else:
                    continue
                break
        except Exception as e:
            print(f"          ⚠  Checkbox: {e}")

        time.sleep(1)

        # Wait up to 5s for the Create Account button to become enabled
        btn_enabled = False
        for _ in range(10):
            disabled = _btn_disabled(page, WD["create_acct_submit"])
            if not disabled:
                btn_enabled = True
                break
            time.sleep(0.5)

        if not btn_enabled:
            print(f"          ⚠  Button still disabled — trying Enter key on verify password")
            try:
                page.locator('input[data-automation-id="verifyPassword"]').first.press("Enter")
                time.sleep(2)
            except Exception:
                pass

        # Find the Create Account / Register / Submit button — try many selectors
        SUBMIT_SELS = [
            'button[data-automation-id="createAccountSubmitButton"]',
            'button[data-automation-id="createAccount"]',
            'button[data-automation-id="registerButton"]',
            'button[data-automation-id="submitButton"]',
        ]
        # Also search by text content for any visible submit-style button
        SUBMIT_TEXTS = [
            "create account", "create my account", "register", "sign up",
            "submit", "continue", "get started",
        ]

        # Scroll to bottom to reveal the Create Account button
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)

        # Find the submit button — visible only
        submit_btn = None
        for sel in SUBMIT_SELS:
            try:
                els = page.locator(sel).all()
                for el in els:
                    if el.is_visible(timeout=600):
                        submit_btn = el
                        print(f"          🔍 Found submit button: {sel}")
                        break
                if submit_btn:
                    break
            except Exception:
                pass

        if submit_btn is None:
            for txt in SUBMIT_TEXTS:
                try:
                    els = page.locator(f"button:has-text('{txt}')").all()
                    for el in els:
                        if el.is_visible(timeout=400):
                            submit_btn = el
                            print(f"          🔍 Found button by text: '{txt}'")
                            break
                    if submit_btn:
                        break
                except Exception:
                    pass

        submitted = False

        # Method 1: Playwright click (most reliable — triggers React onClick)
        if submit_btn:
            try:
                submit_btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                submit_btn.click(timeout=5000)
                submitted = True
                print(f"          🖱  Create Account clicked (playwright click)")
                time.sleep(3)
            except Exception as e:
                print(f"          ⚠  Playwright click failed: {e}")

        # Method 2: JS click with disabled-attr removal
        if not submitted:
            for sel in SUBMIT_SELS:
                try:
                    result = page.evaluate(f"""
                        () => {{
                            const b = document.querySelector('{sel}');
                            if (!b) return false;
                            b.removeAttribute('disabled');
                            b.removeAttribute('aria-disabled');
                            b.scrollIntoView({{block:'center'}});
                            b.click();
                            return true;
                        }}
                    """)
                    if result:
                        submitted = True
                        print(f"          🖱  Create Account clicked (JS): {sel}")
                        time.sleep(3)
                        break
                except Exception:
                    pass

        # Method 3: Enter key on verify password field
        if not submitted:
            try:
                vp = page.locator('input[data-automation-id="verifyPassword"]').first
                if vp.count() and vp.is_visible(timeout=500):
                    vp.click()
                    time.sleep(0.2)
                    vp.press("Enter")
                    submitted = True
                    print(f"          🖱  Create Account submitted (Enter key)")
                    time.sleep(3)
            except Exception:
                pass

        if not submitted:
            _failure_shot(page, f"createacct_btn_failed_{company_key}")
            print(f"          ❌ Could not click Create Account button — skipping")
            return False

        time.sleep(5)  # Wait for Workday to process

        # Check for success — Workday shows "check your email" on same page
        body = _safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or ""
        SUCCESS_PHRASES = ["check your email", "verify your email", "verification email",
                           "sent you an email", "please check", "confirm your email"]
        if any(p in body for p in SUCCESS_PHRASES):
            print(f"          ✅ Account created — waiting for Gmail verification link...")
            print(f"          📧 Check Gmail (raghavendrakaranam30@gmail.com) for verification email")
            # Auto-fetch and click the verification link from Gmail
            try:
                result = mail_reader.wait_for_otp(
                    company=company_key, timeout_secs=120, since_minutes=5
                )
                if result.get("type") == "verify_link":
                    print(f"          ✅ Found verification link — opening it")
                    page.goto(result["link"], wait_until="domcontentloaded", timeout=20000)
                    time.sleep(4)
                    print(f"          ✅ Email verified — account is ready")
                else:
                    print(f"          ⚠  No verification link found in Gmail yet")
                    print(f"          👉 Please check Gmail manually and click the link")
                    time.sleep(5)
            except Exception as e:
                print(f"          ⚠  Gmail check failed: {e}")
        # Also check for intervention detection (OTP, security question etc)
        kind = _detect_intervention(page)
        if kind and kind != "email_verify":
            handle_intervention(page, kind, company_key, "account creation")

        # Check for errors
        errors = page.evaluate("""
            () => Array.from(document.querySelectorAll(
                '[data-automation-id="errorMessage"], [class*="error-message"], ' +
                '[class*="validationError"], .wd-error, [role="alert"]'
            )).filter(e => e.offsetParent && e.innerText.trim())
              .map(e => e.innerText.trim())
              .filter(t => t.length > 2)
        """) or []

        if errors:
            print(f"          ❌ Errors after submit: {errors}")
            blob = " ".join(errors).lower()
            EXISTS = ["already", "in use", "exists", "associated with an account",
                      "an account with this", "already registered"]
            if any(p in blob for p in EXISTS):
                print(f"          ↩  Email already registered for {company_key}")
                return "exists"
            _failure_shot(page, f"createacct_{company_key}")
            return False

        # Check for OTP/verification after account creation
        kind = _detect_intervention(page)
        if kind:
            print(f"          🔐 Post-registration {kind} detected")
            ok = handle_intervention(page, kind, company_key, "account creation")
            if not ok:
                _failure_shot(page, f"createacct_verify_{company_key}")
                return False

        print(f"          ✅ Account created for {company_key}")
        secure_store.save_account(company_key, email, password,
                                  extra={"portal_url": page.url or ""})
        return True

    except Exception as e:
        print(f"          ⚠  Account creation failed: {e}")
        _failure_shot(page, f"createacct_err_{company_key}")
        return False

def ensure_workday_auth(page, job_url: str) -> bool:
    """
    Ensure authentication on this Workday portal before starting the form.
    Uses data-automation-id to detect page state — never body text.
    """
    time.sleep(2)
    company_key = _get_company_key(job_url)
    email    = cfg.CANDIDATE_EMAIL
    password = _get_wd_password()

    # Check if we're already on the application form using data-automation-id
    on_form = any(_exists(page, sel, timeout=1000) for sel in [
        WD["contact_page"], WD["experience_page"],
        WD["app_questions_page"], WD["voluntary_page"],
        WD["self_id_page"], WD["review_page"],
    ])
    if on_form:
        print(f"          ✅ Already on application form — no auth needed")
        return True

    # Check if Sign In button is visible (Workday auth wall)
    sign_in_visible     = _exists(page, WD["sign_in_btn"],       timeout=3000)
    create_acct_visible = _exists(page, WD["create_acct_link"],  timeout=2000)
    # Also detect if we're on a sign-in form (email + password inputs visible)
    on_signin_form = (
        _exists(page, WD["email_input"],    timeout=1000) and
        _exists(page, WD["password_input"], timeout=1000)
    )

    existing = secure_store.get_account(company_key)

    if sign_in_visible or on_signin_form or create_acct_visible:
        # Order: try sign-in → if rejected (wrong password / no account / locked)
        # → create a new account for this company → if creation also fails → skip.
        if existing:
            print(f"          🔑 Account exists for {company_key} — signing in")
        else:
            print(f"          🔑 No stored account for {company_key} — trying sign-in first")

        # 1. Try sign-in with stored/default credentials.
        signed_in = workday_sign_in(page, email, password)
        if signed_in:
            if not existing:
                secure_store.save_account(company_key, email, password,
                                          extra={"portal_url": job_url})
            secure_store.mark_logged_in(company_key, job_url)
            time.sleep(3)
        else:
            # 2. Sign-in rejected / no account → create a new account.
            print(f"          🔁 Sign-in rejected — creating a new account for {company_key}")
            created = workday_create_account(page, email, password, company_key)
            time.sleep(3)

            if created == True:
                # Account created — try sign-in directly (some portals allow
                # immediate sign-in; others need email verification first)
                print(f"          🔑 Account created — trying sign-in")
                time.sleep(3)
                signed_in = workday_sign_in(page, email, password)
                if signed_in:
                    secure_store.mark_logged_in(company_key, job_url)
                    time.sleep(2)
                    return True
                else:
                    print(f"          📧 Sign-in failed — portal needs email verification")
                    print(f"          👉 Open Gmail → find verification email from Workday/{company_key}")
                    print(f"          👉 Click the verification link → then re-run the script")
                    print(f"          ⏭  Skipping now — will work on re-run after verification\n")
                    return False

            if created == "exists":
                # 3. Email already registered → the stored password may be wrong.
                #    Try signing in once more.
                print(f"          🔁 Email already exists — retrying sign-in")
                signed_in = workday_sign_in(page, email, password)
                if signed_in:
                    secure_store.mark_logged_in(company_key, job_url)
                    time.sleep(3)
                else:
                    # 4. Everything failed → account is locked. Tell user exactly what to do.
                    print(f"\n          🔒 ACCOUNT LOCKED for {company_key}")
                    print(f"          👉 ACTION REQUIRED — Go to the Workday portal and reset your password:")
                    print(f"             1. Open the job URL in your browser")
                    print(f"             2. Click 'Sign In' → 'Forgot Password'")
                    print(f"             3. Enter raghavendrakaranam30@gmail.com")
                    print(f"             4. Check Gmail and reset password to Raghava@2025!")
                    print(f"          ⏭  Skipping this job — run continues with next company\n")
                    return False
            elif not created:
                # 4. Creation failed for another reason → skip this job.
                print(f"          ⏭  Account creation failed — skipping job")
                return False

    # After auth, verify we're on the application form
    # If still on auth page, it may need another Apply click
    on_form = any(_exists(page, sel, timeout=2000) for sel in [
        WD["contact_page"], WD["experience_page"], WD["app_questions_page"],
        WD["voluntary_page"], WD["self_id_page"], WD["review_page"],
    ])
    if not on_form:
        # Try clicking Apply/Start Application button one more time
        for sel in [
            'a[data-automation-id="adventureButton"]',
            'button:has-text("Apply")', 'a:has-text("Apply")',
            'button:has-text("Start Application")',
        ]:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(3)
                    break
            except:
                pass

    return True

# ── Step handlers (one function per Workday step) ────────────────────────────
# Pattern: wait for the page div, fill fields, click Next.

def _wait_for_page(page, sel: str, timeout_secs=30) -> bool:
    """Wait for a Workday step page to appear."""
    for _ in range(timeout_secs * 2):
        if _exists(page, sel, timeout=500):
            return True
        time.sleep(0.5)
    return False

def step_contact_information(page):
    """Fill My Information step: name, address, phone."""
    print(f"          📋 Step: My Information")

    # Previous worker check — click "No" if asked
    _safe_eval(page, """
        () => {
            const radios = Array.from(document.querySelectorAll(
                'div[data-automation-id="previousWorker"] input[type="radio"]'
            ));
            const no = radios.find(r => {
                const lbl = document.querySelector('label[for="' + r.id + '"]');
                return lbl && lbl.innerText.toLowerCase().includes('no');
            });
            if (no && !no.checked) no.click();
        }
    """)
    time.sleep(0.5)

    # Name
    _fill(page, WD["first_name"], "Raghavendra")
    time.sleep(0.2)
    _fill(page, WD["last_name"], "Karanam")
    time.sleep(0.2)

    # Address — line 1 must be a street address (not city/state)
    _fill(page, WD["address_line1"], "14401 S Military Trl")
    time.sleep(0.2)
    _fill(page, WD["city"], "Delray Beach")
    time.sleep(0.2)

    # State dropdown — try countryRegion first (most portals), fall back to stateProvince
    state_filled = _select_dropdown(page, WD["state_btn"], "Florida")
    if not state_filled:
        _select_dropdown(page, 'button[data-automation-id="addressSection_stateProvince"]', "Florida")
    time.sleep(0.3)
    time.sleep(0.3)

    _fill(page, WD["postal_code"], "33484")
    time.sleep(0.2)

    # Phone
    _select_dropdown(page, WD["phone_type_btn"], "Mobile")
    time.sleep(0.3)
    _select_country_phone_code(page)   # required dropdown → United States
    time.sleep(0.3)
    _fill(page, WD["phone_number"], "5618160256")
    time.sleep(0.3)

    _advance_page(page, "My Information")
    print(f"          ✅ My Information step done")

def step_my_experience(page, resume_path: str):
    """Fill My Experience step: resume upload + LinkedIn URL."""
    print(f"          📋 Step: My Experience")

    # Upload resume
    if resume_path and _exists(page, WD["resume_upload"], timeout=5000):
        try:
            fi = page.locator(WD["resume_upload"]).first
            fi.set_input_files(str(resume_path))
            print(f"          📎 Resume uploaded: {Path(resume_path).name}")
            # Wait for Workday to parse the uploaded resume
            print(f"          ⏳ Waiting for resume parse...")
            time.sleep(5)
            # Check for parse-complete indicator
            for _ in range(20):
                parsing = _safe_eval(page, """
                    () => !!document.querySelector(
                        '[data-automation-id="fileUploadLoading"], [class*="loading"], [class*="spinner"]'
                    )
                """, False)
                if not parsing:
                    break
                time.sleep(1)
            print(f"          ✅ Resume parse complete")
        except Exception as e:
            print(f"          ⚠  Resume upload error: {e}")

    # LinkedIn URL
    if _exists(page, WD["linkedin_url"], timeout=3000):
        _fill(page, WD["linkedin_url"], "https://www.linkedin.com/in/raghavendra-karanam")
        time.sleep(0.3)

    _advance_page(page, "My Experience")
    print(f"          ✅ My Experience step done")

def step_application_questions(page, profile_text: str, job_title: str,
                                company: str, jd_text: str, cover_letter_text: str):
    """Fill application-specific Q&A using Claude + cache."""
    print(f"          📋 Step: Application Questions")
    filled = _smart_fill_questions(page, profile_text, job_title, company,
                                   jd_text, cover_letter_text)
    print(f"          ✅ Application Questions: {filled} field(s) filled")
    _advance_page(page, "Application Questions")

def step_voluntary_disclosures(page):
    """Fill voluntary disclosure step: gender, ethnicity, veteran."""
    print(f"          📋 Step: Voluntary Disclosures")

    _select_dropdown(page, WD["gender_btn"], "I don't wish to answer")
    time.sleep(0.2)
    _select_dropdown(page, WD["hispanic_btn"], "I don't wish to answer")
    time.sleep(0.2)
    _select_dropdown(page, WD["ethnicity_btn"], "I don't wish to answer")
    time.sleep(0.2)
    _select_dropdown(page, WD["veteran_btn"], "I am not a protected veteran")
    time.sleep(0.2)

    # Agreement checkbox
    if _exists(page, WD["agreement_check"], timeout=3000):
        _click(page, WD["agreement_check"])
        time.sleep(0.3)

    _advance_page(page, "Voluntary Disclosures")
    print(f"          ✅ Voluntary Disclosures step done")

def step_self_identification(page):
    """Fill self-identification (disability) step."""
    print(f"          📋 Step: Self Identification")

    # Full name field
    if _exists(page, WD["full_name_input"], timeout=3000):
        _fill(page, WD["full_name_input"], "Raghavendra Karanam")
        time.sleep(0.3)

    # Date — click today
    if _exists(page, 'div[data-automation-id="dateIcon"]', timeout=2000):
        _click(page, 'div[data-automation-id="dateIcon"]')
        time.sleep(0.5)
        if _exists(page, 'button[data-automation-id="datePickerSelectedToday"]', timeout=2000):
            _click(page, 'button[data-automation-id="datePickerSelectedToday"]')
            time.sleep(0.3)

    # Disability — choose "I don't wish to answer" (3rd radio option)
    _safe_eval(page, """
        () => {
            const radios = Array.from(document.querySelectorAll(
                'input[type="radio"]'
            )).filter(r => r.offsetParent);
            // Find "don't wish" or pick last option (abstain)
            const abstain = radios.find(r => {
                const lbl = document.querySelector('label[for="' + r.id + '"]');
                const t = lbl ? lbl.innerText.toLowerCase() : '';
                return t.includes("don't") || t.includes("not") || t.includes("abstain")
                    || t.includes("choose not");
            });
            if (abstain) { abstain.click(); return; }
            // Fallback: click last radio (usually abstain)
            if (radios.length > 0) radios[radios.length - 1].click();
        }
    """)
    time.sleep(0.3)

    _advance_page(page, "Self Identification")
    print(f"          ✅ Self Identification step done")

def step_review_and_submit(page, dry_run=False) -> bool:
    """Review page — submit the application."""
    print(f"          📋 Step: Review & Submit")

    if dry_run:
        print(f"          🏁 DRY RUN — Would click Submit here")
        return True

    # Click Submit (same data-automation-id as Next on review page)
    for attempt in range(1, 4):
        print(f"          🚀 Submit attempt {attempt}/3...")
        _click(page, WD["submit_btn"])
        time.sleep(4)

        if _is_confirmed(page):
            print(f"          🎉 Application submitted!")
            return True

        # CAPTCHA → pause for manual solving, then continue
        captcha = any("bframe" in (f.url or "") for f in list(page.frames)) or \
                  any(s in (_safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or "")
                      for s in ["captcha", "i'm not a robot", "verify you are human"])
        if captcha:
            print(f"\n          🚨🚨 CAPTCHA DETECTED 🚨🚨")
            print(f"          👉 Please SOLVE THE CAPTCHA MANUALLY in the open browser window.")
            print(f"          ⏸  Waiting 60 seconds for you to solve it, then continuing...")
            try:
                notifier.send_alert(subject="🚨 Workday CAPTCHA — solve manually",
                                    body="Solve the CAPTCHA in the open browser window within 60s.")
            except Exception:
                pass
            time.sleep(60)

    # Submission did not confirm — capture evidence for review
    if not _is_confirmed(page):
        _failure_shot(page, "submit_not_confirmed")
    return _is_confirmed(page)

# ── Generic question filler (Claude + cache) ──────────────────────────────────

def _smart_fill_questions(page, profile_text: str, job_title: str, company: str,
                           jd_text: str, cover_letter_text: str) -> int:
    """
    For application questions step and any unrecognized steps.
    Uses data-automation-id where possible, falls back to label-based filling.
    """
    import anthropic, json as _json

    COVER_LABELS = {
        "cover letter", "cover note", "why are you interested",
        "why do you want to work", "why this role", "additional information",
        "message to hiring", "tell us about yourself",
    }

    # Fields to NEVER fill — auth fields, honeypots, system fields
    NEVER_FILL_LABELS = {
        "password", "verifypassword", "verify password", "confirm password",
        "beecatcher", "honeypot", "trap", "username",
        "forgot", "sign in", "create account",
    }
    NEVER_FILL_TYPES = {"password"}

    # Extract all visible form fields
    fields = _safe_eval(page, r"""
        () => {
            function getLabel(el) {
                // 1. data-automation-id on label
                const aid = el.getAttribute('data-automation-id') || '';
                if (aid && !['input','button','select','textarea'].includes(aid)) return aid;
                // 2. Associated label
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) return lbl.innerText.trim().split('\n')[0];
                }
                // 3. aria-label
                const al = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
                if (al) return al;
                // 4. Walk up
                const c = el.closest('[data-automation-id*="formField"], fieldset, [role="group"]');
                if (c) {
                    const h = c.querySelector('label, legend, [data-automation-id*="label"]');
                    if (h) return h.innerText.trim().split('\n')[0];
                }
                return el.name || el.id || '';
            }

            function uniqueSel(el) {
                const aid = el.getAttribute('data-automation-id');
                if (aid) return '[data-automation-id="' + CSS.escape(aid) + '"]';
                if (el.id) return '#' + CSS.escape(el.id);
                if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                return '';
            }

            const results = [];
            const seen = {};

            // Text / select / textarea
            for (const inp of document.querySelectorAll(
                'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=file]),' +
                'select, textarea'
            )) {
                if (!inp.offsetParent) continue;
                const lbl = getLabel(inp);
                if (!lbl || seen[lbl.toLowerCase()]) continue;
                seen[lbl.toLowerCase()] = true;
                const type = inp.tagName === 'SELECT' ? 'select'
                    : inp.tagName === 'TEXTAREA' ? 'textarea'
                    : (inp.getAttribute('type') || 'text').toLowerCase();
                const opts = type === 'select'
                    ? Array.from(inp.options).map(o => o.text.trim()).filter(o => o && o !== '--')
                    : [];
                results.push({ label: lbl, type, options: opts,
                    required: inp.required, sel: uniqueSel(inp) });
            }

            // Radio/checkbox groups
            const groups = {};
            for (const inp of document.querySelectorAll('input[type=radio],input[type=checkbox]')) {
                if (!inp.offsetParent) continue;
                const gname = inp.name || inp.getAttribute('data-automation-id') || '';
                if (!gname || groups[gname]) continue;
                const fs = inp.closest('fieldset,[role="group"],[data-automation-id*="formField"]');
                let lbl = gname;
                if (fs) {
                    const leg = fs.querySelector('legend,label,[data-automation-id*="label"]');
                    if (leg) lbl = leg.innerText.trim().split('\n')[0];
                }
                if (!seen[lbl.toLowerCase()]) {
                    seen[lbl.toLowerCase()] = true;
                    const opts = Array.from(document.querySelectorAll('input[name="'+gname+'"]'))
                        .map(r => {
                            const le = document.querySelector('label[for="'+r.id+'"]');
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

    # Filter out auth/honeypot fields
    fields = [
        f for f in fields
        if f.get("label","").lower().strip().rstrip(" *:?") not in NEVER_FILL_LABELS
        and f.get("type","") not in NEVER_FILL_TYPES
    ]

    if not fields:
        print(f"          ℹ  All fields are auth/system fields — skipping fill")
        return 0

    print(f"          📋 {len(fields)} field(s) on this step")

    answers = {}
    uncached = []

    for f in fields:
        lbl = f.get("label", "")
        lbl_lower = lbl.lower().strip().rstrip(" *:?")

        # Cover letter fields — use pre-built cover letter text
        if cover_letter_text and f.get("type") in ("textarea", "text"):
            if any(kw in lbl_lower for kw in COVER_LABELS):
                answers[lbl] = cover_letter_text
                print(f"             📝 Cover letter → '{lbl}'")
                continue

        # LAYER 1 — qa_answers.py (manually curated, fastest, no API)
        qa = _qa.get_answer(lbl) if (_qa and lbl) else None
        if qa is not None:
            answers[lbl] = qa
            print(f"             ✔ QA     '{lbl}' → '{str(qa)[:60]}'")
            continue

        # LAYER 2 — claude_answers.py (Claude's past answers, saved automatically)
        ca = _claude_ans.get(lbl) if (_claude_ans and lbl) else None
        if ca is not None:
            answers[lbl] = ca
            print(f"             ✔ SAVED  '{lbl}' → '{str(ca)[:60]}'")
            continue

        # LAYER 3 — SQLite cache (legacy fallback)
        cached = _cache.get(lbl) if lbl else None
        if cached is not None:
            answers[lbl] = cached
            print(f"             ✔ CACHE  '{lbl}' → '{str(cached)[:60]}'")
            # Promote to claude_answers.py so it's visible and editable
            if _claude_ans:
                _claude_ans.save(lbl, cached)
        else:
            uncached.append(f)

    if uncached:
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.get_api_key())
        fields_desc = "\n".join(
            f'{i+1}. label="{f["label"]}" type={f["type"]}'
            + (f' options={f["options"]}' if f.get("options") else '')
            for i, f in enumerate(uncached)
        )

        # Build full profile context for Claude
        from raghav_profile import PROFILE, EDUCATION, EXPERIENCE, COMMON_QA as _CQA, SKILL_YEARS as _SY
        profile_context = f"""
CANDIDATE PROFILE:
Name:          {PROFILE.get('name')}
Email:         {PROFILE.get('email')}
Phone:         {PROFILE.get('phone')}
Location:      {PROFILE.get('location')}
Address:       14401 S Military Trl, Delray Beach, FL 33484
Work Auth:     {PROFILE.get('work_auth')}
Visa:          F-1 STEM OPT — no sponsorship needed
LinkedIn:      https://{PROFILE.get('linkedin')}
GitHub:        https://{PROFILE.get('github')}

CURRENT JOB:   {_CQA.get('current_employer')} — {_CQA.get('current_title')} (April 2026–Present)
EDUCATION:     {EDUCATION[0]['degree']} — {EDUCATION[0]['school']} (May 2025) GPA 3.8
SALARY WANT:   ${_CQA.get('salary_expected')} / year  |  ${_CQA.get('hourly_rate')} / hour
NOTICE:        {_CQA.get('notice_period')}
RELOCATE:      No
REMOTE:        Yes

SKILLS & YEARS: {_json.dumps(_SY)}

EXTRA CONTEXT (resume snippet):
{profile_text[:600]}
"""

        prompt = f"""You are filling a Workday job application form for:
Job:     {job_title}
Company: {company}

{profile_context}

FORM FIELDS TO FILL:
{fields_desc}

Return ONLY a JSON object: {{"field label": "answer"}}

RULES (follow exactly):
- Radio / checkbox / select: copy ONE option text EXACTLY as shown
- Work authorization / legally authorized: "Yes"
- Sponsorship now or in future: "No"
- Visa type: "F-1 STEM OPT"
- {_salary_rule(jd_text, job_title)}
- Hourly rate: "40"
- Notice period / start date: "2 weeks"
- Willing to relocate: "No"
- Open to remote: "Yes"
- Gender / race / ethnicity / disability: "I don't wish to answer" (or closest option)
- Veteran: "I am not a protected veteran" (or closest option)
- Any "do you have experience with X": "Yes"
- Years of experience (generic): "2"
- Years of experience with a specific skill: look it up in SKILLS & YEARS above
- Cover letter / why interested: write 2 sentences using the candidate profile
- NEVER mention Community Dreams Foundation or Mobile Stage Pros
- If unsure, make the safest choice based on the profile above"""

        try:
            resp = client.messages.create(
                model=cfg.CLAUDE_MODEL_FAST, max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                claude_ans_map = _json.loads(m.group(0))
                def _n(s): return re.sub(r'[\s\*\?\:]+$', '', s).strip().lower()
                for f_unc in uncached:
                    orig = f_unc.get("label", "")
                    matched = claude_ans_map.get(orig)
                    if matched is None:
                        for ck, cv in claude_ans_map.items():
                            if _n(ck) == _n(orig) or _n(orig) in _n(ck) or _n(ck) in _n(orig):
                                matched = cv; break
                    if matched is not None and str(matched).strip():
                        answers[orig] = matched
                        # Save to claude_answers.py (permanent, human-readable)
                        if _claude_ans:
                            _claude_ans.save(orig, str(matched))
                        # Also save to SQLite cache (legacy)
                        _cache.save(orig, str(matched))
                        print(f"             ✔ Claude → '{orig}': '{str(matched)[:60]}'")
        except Exception as e:
            print(f"          ⚠  Claude API error: {e}")

    # LAYER 4 — Profile-based fallback (no API, decided from profile data)
    # Used when Claude fails or is unavailable — better than leaving fields blank.
    _smart_salary = _pick_salary(jd_text, job_title)
    PROFILE_FALLBACK = {
        "work authorization":       "Yes",
        "authorized to work":       "Yes",
        "legally authorized":       "Yes",
        "sponsorship":              "No",
        "require sponsorship":      "No",
        "visa":                     "F-1 STEM OPT",
        "salary":                   _smart_salary,
        "compensation":             _smart_salary,
        "hourly rate":              "40",
        "start date":               "2 weeks",
        "notice period":            "2 weeks",
        "when can you start":       "2 weeks",
        "relocat":                  "No",
        "remote":                   "Yes",
        "gender":                   "I don't wish to answer",
        "ethnicity":                "I don't wish to answer",
        "race":                     "I don't wish to answer",
        "veteran":                  "I am not a protected veteran",
        "disability":               "I don't wish to answer",
        "years of experience":      "2",
        "background check":         "Yes",
        "drug test":                "Yes",
        "18 or older":              "Yes",
        "us citizen":               "No",
        "green card":               "No",
        "permanent resident":       "No",
        "linkedin":                 "https://www.linkedin.com/in/raghavendra-karanam",
        "github":                   "https://github.com/raghava0071",
        "phone":                    "5618160256",
        "city":                     "Delray Beach",
        "state":                    "FL",
        "zip":                      "33484",
        "country":                  "United States of America",
    }
    for f in uncached:
        lbl = f.get("label", "")
        if answers.get(lbl):
            continue
        lbl_l = lbl.lower()
        for kw, val in PROFILE_FALLBACK.items():
            if kw in lbl_l:
                answers[lbl] = val
                print(f"             ✔ PROFILE '{lbl}' → '{val}'")
                # Save profile fallback to claude_answers.py too
                if _claude_ans:
                    _claude_ans.save(lbl, val)
                break

    # Fill DOM
    filled = _safe_eval(page, """
        (itemsJson) => {
            const items = JSON.parse(itemsJson);
            let filled = 0;
            function fire(el) {
                ['input','change','blur'].forEach(ev =>
                    el.dispatchEvent(new Event(ev, {bubbles:true}))
                );
            }
            for (const item of items) {
                if (!item.answer) continue;
                const ans = item.answer;

                if (item.type === 'radio' || item.type === 'checkbox') {
                    const opts = item.gname
                        ? Array.from(document.querySelectorAll('input[name="'+item.gname+'"]'))
                        : [];
                    const ansL = ans.toLowerCase().trim();
                    for (const opt of opts) {
                        let lbl = '';
                        const le = document.querySelector('label[for="'+opt.id+'"]');
                        if (le) lbl = le.innerText.toLowerCase().trim();
                        const val = (opt.value||'').toLowerCase();
                        if (val === ansL || lbl === ansL ||
                            val.includes(ansL) || ansL.includes(val) ||
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
                        o.text.trim() === ans ||
                        o.text.toLowerCase().includes(ans.toLowerCase())
                    );
                    if (match) { el.value = match.value; fire(el); filled++; }
                } else if (el.getAttribute('contenteditable')) {
                    el.focus();
                    document.execCommand('selectAll', false, null);
                    document.execCommand('insertText', false, ans);
                    fire(el); filled++;
                } else {
                    try {
                        const tag = el.tagName.toUpperCase();
                        const proto = tag === 'TEXTAREA'
                            ? window.HTMLTextAreaElement.prototype
                            : window.HTMLInputElement.prototype;
                        const ns = Object.getOwnPropertyDescriptor(proto, 'value');
                        if (ns && ns.set) ns.set.call(el, ans); else el.value = ans;
                    } catch(e) { try { el.value = ans; } catch(e2) {} }
                    fire(el); filled++;
                }
            }
            return filled;
        }
    """, _json.dumps([{
        "sel": f.get("sel",""), "label": f.get("label",""),
        "type": f.get("type","text"), "options": f.get("options",[]),
        "gname": f.get("gname",""), "answer": str(answers.get(f.get("label",""), ""))
    } for f in fields])) or 0

    # Playwright fill pass for React-controlled inputs
    for f in fields:
        sel = f.get("sel","")
        ans = str(answers.get(f.get("label",""), ""))
        if sel and ans and f.get("type") in ("text","number","textarea","tel","email"):
            try:
                el = page.query_selector(sel)
                if el:
                    el.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.type(ans, delay=random.randint(40, 80))
            except:
                pass

    return filled or 0

# ── Job extraction ────────────────────────────────────────────────────────────

def extract_workday_job(page) -> dict:
    return _safe_eval(page, """
        () => {
            let title = '';
            for (const sel of [
                '[data-automation-id="jobPostingHeader"]',
                'h1', 'h2'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 2) {
                    title = el.innerText.trim().split('\\n')[0]; break;
                }
            }
            // Company from page title "Role | Company"
            let company = '';
            const pt = document.title || '';
            if (pt.includes('|')) company = pt.split('|').pop().trim();

            let description = '';
            for (const sel of [
                '[data-automation-id="jobPostingDescription"]',
                '[class*="jobPosting"]', 'section', '[class*="description"]'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.length > 100) {
                    description = el.innerText.substring(0, 4000); break;
                }
            }
            return { title, company, description, url: window.location.href };
        }
    """, {}) or {}

def click_workday_apply(page) -> bool:
    """Click the Apply button on a Workday job posting — handles all locales."""
    for sel in [
        'a[data-automation-id="adventureButton"]',
        'a[data-automation-id="jobPostingApplyButton"]',
        'button[data-automation-id="jobPostingApplyButton"]',
        "a:has-text('Apply')", "button:has-text('Apply')",
        "a:has-text('Apply Now')", "button:has-text('Apply Now')",
        "a:has-text('Postuler')", "button:has-text('Postuler')",  # French
        "a:has-text('Bewerben')", "button:has-text('Bewerben')",  # German
        "a:has-text('Solicitar')", "button:has-text('Solicitar')", # Spanish
        "a:has-text('Start Application')", "button:has-text('Start Application')",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible(timeout=3000):
                btn.click()
                time.sleep(3)
                return True
        except:
            pass
    return False

# ── Main apply function ───────────────────────────────────────────────────────

def apply_to_workday_job(page, job: dict, resume_path: str, cover_letter_path: str,
                          profile_text: str = "", dry_run: bool = False):
    """
    Full Workday application flow using proven per-step handlers.
    Returns (success: bool, reason: str).
    """
    title   = job.get("title", "")
    company = job.get("company", "")
    job_url = job.get("url", "")
    jd_text = job.get("description", "")

    print(f"          🌐 {job_url[:70]}")
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(3)
    except Exception as e:
        return False, f"navigation failed: {e}"

    # Extract job info if missing
    if not title or not jd_text:
        info = extract_workday_job(page)
        title   = title   or info.get("title", "")
        company = company or info.get("company", "")
        jd_text = jd_text or info.get("description", "")

    # Click Apply
    print(f"          👆 Clicking Apply...")
    if not click_workday_apply(page):
        return False, "no Apply button found"
    time.sleep(2)

    # Handle "Apply with LinkedIn" vs "Apply Manually" choice
    if _exists(page, WD["apply_manually"], timeout=3000):
        _click(page, WD["apply_manually"])
        time.sleep(2)

    # Auth — handle sign-in / account creation
    ok = ensure_workday_auth(page, page.url)
    if not ok:
        return False, "auth failed"
    time.sleep(2)

    # After auth, Workday often redirects back to the JOB POSTING (not the form).
    # We need to click Apply again to get into the actual application form.
    on_form_now = any(_exists(page, sel, timeout=1500) for sel in [
        WD["contact_page"], WD["experience_page"], WD["app_questions_page"],
        WD["voluntary_page"], WD["review_page"],
    ])
    if not on_form_now:
        print(f"          🔄 Not yet on form — clicking Apply again after auth...")
        if not click_workday_apply(page):
            # Try navigating directly to the applyManually URL
            apply_url = job_url if "applyManually" in job_url else job_url + "/apply/applyManually"
            try:
                page.goto(apply_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(3)
            except:
                pass
        time.sleep(3)

    # Read cover letter text
    cl_text = ""
    if cover_letter_path:
        try:
            from docx import Document as _D
            cl_text = "\n\n".join(p.text for p in _D(cover_letter_path).paragraphs if p.text.strip())
        except:
            pass

    # ── Walk each step by waiting for its page div ────────────────────────────
    step = 0
    max_steps = 12
    submitted = False
    last_step_body = ""
    same_step_count = 0
    auth_retries = 0
    max_auth_retries = 2

    while step < max_steps and not submitted:
        step += 1
        time.sleep(random.uniform(1.5, 2.5))

        # Check for OTP / security question mid-flow
        kind = _detect_intervention(page)
        if kind:
            ok = handle_intervention(page, kind, company, title)
            if not ok:
                return False, f"intervention {kind} timed out"
            time.sleep(2)
            continue

        if _is_confirmed(page):
            submitted = True
            break

        # Wait for page to settle after navigation/click
        time.sleep(2)
        body = _safe_eval(page, "() => document.body.innerText.toLowerCase()", "") or ""

        # Detect infinite loop (same page body repeated 3x = stuck)
        body_sig = body[:300]  # first 300 chars as signature
        if body_sig == last_step_body:
            same_step_count += 1
            if same_step_count >= 3:
                print(f"          ❌ Stuck on same step for 3 attempts — giving up")
                break
        else:
            same_step_count = 0
            last_step_body = body_sig
        url  = page.url or ""

        print(f"\n          ─── Step {step} ─── url={url[-60:]}")

        # Detect step using data-automation-id ONLY (most reliable — never changes across Workday)
        # Use very short timeouts so detection is fast
        is_contact   = _exists(page, WD["contact_page"],    timeout=1500)
        is_experience= _exists(page, WD["experience_page"], timeout=1500)
        is_questions = _exists(page, WD["app_questions_page"], timeout=1500)
        is_voluntary = _exists(page, WD["voluntary_page"],  timeout=1500)
        is_self_id   = _exists(page, WD["self_id_page"],    timeout=1500)
        is_review    = _exists(page, WD["review_page"],     timeout=1500)

        # Body text fallback ONLY for review/submit (very distinctive words)
        if not is_review:
            is_review = any(s in body for s in ["review and submit", "submit your application"])

        print(f"          Step detect: contact={is_contact} exp={is_experience} q={is_questions} vol={is_voluntary} self={is_self_id} review={is_review}")

        # Detect if still on auth page — re-run auth instead of filling fields
        still_on_auth = (
            _exists(page, WD["sign_in_btn"], timeout=1000) or
            _exists(page, WD["create_acct_submit"], timeout=1000) or
            (_exists(page, WD["email_input"], timeout=500) and
             _exists(page, WD["password_input"], timeout=500))
        )
        if still_on_auth:
            auth_retries += 1
            if auth_retries > max_auth_retries:
                print(f"\n          ❌ Auth failed after {max_auth_retries} attempts — skipping job")
                print(f"          🔒 The account for this portal is likely locked.")
                print(f"          👉 Fix: open the portal in your browser → Forgot Password")
                print(f"             → enter raghavendrakaranam30@gmail.com → reset to Raghava@2025!")
                print(f"          ⏭  Moving to next job...\n")
                return False, "auth_failed_locked"
            print(f"          🔐 Still on auth page (attempt {auth_retries}/{max_auth_retries})")
            auth_ok = ensure_workday_auth(page, job_url)
            if not auth_ok:
                print(f"          ⏭  Auth returned False — skipping job")
                return False, "auth_failed"
            time.sleep(3)
            continue

        if is_contact:
            step_contact_information(page)

        elif is_experience:
            step_my_experience(page, resume_path)

        elif is_questions:
            step_application_questions(page, profile_text, title, company, jd_text, cl_text)

        elif is_voluntary:
            step_voluntary_disclosures(page)

        elif is_self_id:
            step_self_identification(page)

        elif is_review:
            submitted = step_review_and_submit(page, dry_run=dry_run)
            break

        else:
            # Unknown step — fill any visible fields then advance
            print(f"          ⚠  Unknown step — filling visible fields")
            _smart_fill_questions(page, profile_text, title, company, jd_text, cl_text)
            time.sleep(0.5)

            # Explicitly handle Workday dropdown widgets that QA can't fill via text
            # Country Phone Code — custom button+listbox widget
            _select_country_phone_code(page)

            # "How Did You Hear About Us?" — common dropdown on many portals
            for hear_sel in [
                'button[data-automation-id="hearAboutUs"]',
                'button[data-automation-id="How Did You Hear About Us"]',
                '[data-automation-id*="hearAbout"] button',
                '[data-automation-id*="HearAbout"] button',
            ]:
                try:
                    btn = page.locator(hear_sel).first
                    if btn.count() and btn.is_visible(timeout=500):
                        cur = (btn.inner_text() or "").strip()
                        if not cur or "select" in cur.lower():
                            btn.click(); time.sleep(0.5)
                            # Pick "LinkedIn" or "Indeed" or first option
                            for opt_txt in ["LinkedIn", "Indeed", "Job Board", "Online"]:
                                try:
                                    opt = page.locator(f'[role=option]:has-text("{opt_txt}")').first
                                    if opt.count() and opt.is_visible(timeout=400):
                                        opt.click()
                                        print(f"          🎯 Heard about us → '{opt_txt}'")
                                        break
                                except Exception:
                                    pass
                        break
                except Exception:
                    pass

            # State/Province dropdown if visible and empty
            for state_sel in [WD["state_btn"], WD["state_btn_alt"]]:
                try:
                    btn = page.locator(state_sel).first
                    if btn.count() and btn.is_visible(timeout=400):
                        cur = (btn.inner_text() or "").strip()
                        if not cur or "select" in cur.lower():
                            _select_dropdown(page, state_sel, "Florida")
                        break
                except Exception:
                    pass

            time.sleep(0.5)
            _advance_page(page, f"unknown-step-{step}")

        time.sleep(2)
        if _is_confirmed(page):
            submitted = True
            break

    return submitted, "submitted" if submitted else "form walk ended"

# ── Job search: LinkedIn queue + known company Workday portals ────────────────

# Companies known to use Workday — search their portals directly
KNOWN_WORKDAY_COMPANIES = [
    ("Amazon",         "https://amazon.jobs/en/search?base_query={query}&loc_query=United+States"),
    ("Microsoft",      "https://jobs.careers.microsoft.com/global/en/search?q={query}&l=en_us"),
    ("Google",         "https://careers.google.com/jobs/results/?q={query}"),
    ("Meta",           "https://www.metacareers.com/jobs?q={query}"),
    ("Apple",          "https://jobs.apple.com/en-us/search?search={query}"),
    ("IBM",            "https://ibm.wd12.myworkdayjobs.com/en-US/BNZEXT/jobs?q={query}"),
    ("Deloitte",       "https://deloitte.wd1.myworkdayjobs.com/en-US/DTUSCareers/jobs?q={query}"),
    ("EY",             "https://eyglobal.yello.co/jobs?keywords={query}"),
    ("Accenture",      "https://www.accenture.com/us-en/careers/jobsearch?jk={query}"),
    ("Cognizant",      "https://careers.cognizant.com/global/en/search-results?keywords={query}"),
    ("Capgemini",      "https://www.capgemini.com/careers/job-search/?query={query}"),
    ("Target",         "https://target.wd1.myworkdayjobs.com/en-US/targetcareers/jobs?q={query}"),
    ("Walmart",        "https://careers.walmart.com/results?q={query}&page=1&sort=rank&expand=department,brand,type,rate&jobCity=&jobState=&jobCountry=US"),
    ("Bank of America","https://bankcareers.wd1.myworkdayjobs.com/en-US/BankofAmerica_Careers/jobs?q={query}"),
    ("JPMorgan Chase", "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions?keyword={query}"),
    ("Wells Fargo",    "https://wellsfargojobs.com/search-jobs/?keyword={query}&location=United+States"),
    ("Cigna",          "https://cigna.wd1.myworkdayjobs.com/en-US/Cigna_Careers/jobs?q={query}"),
    ("Anthem",         "https://careers.elevancehealth.com/jobs?keywords={query}"),
    ("UnitedHealth",   "https://careers.unitedhealthgroup.com/job-search-results/?keyword={query}"),
    ("Nike",           "https://jobs.nike.com/job?q={query}"),
    ("Lowe's",         "https://talent.lowes.com/en/US/jobs?keywords={query}"),
]

def _search_workday_on_google(page, query: str) -> list:
    """
    Search Google for site:myworkdayjobs.com jobs.
    Extracts Workday URLs immediately from page HTML on load —
    before Google can trigger any bot-detection redirect.
    No clicks on Google, no interaction — just load and extract.
    """
    import urllib.parse
    search_url = "https://www.google.com/search?" + urllib.parse.urlencode({
        "q": f'site:myworkdayjobs.com "{query}"',
        "num": "10",
        "tbs": "qdr:w",   # last week
    })

    # Use a fresh about:blank page so there's no existing Google cookie state
    try:
        page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
        time.sleep(0.5)
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        # Extract links IMMEDIATELY — before any JS redirect can fire
        time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠  Google search failed: {e}")
        return []

    # Check if Google blocked us
    current_url = page.url or ""
    if "accounts.google.com" in current_url or "sorry/index" in current_url:
        print(f"  ⚠  Google blocked — skipping Google search for '{query}'")
        return []

    # Extract all myworkdayjobs.com links from the result page
    links = _safe_eval(page, """
        () => {
            const results = [];
            const seen = new Set();
            for (const a of document.querySelectorAll('a[href]')) {
                let href = a.getAttribute('href') || '';
                // Google wraps links as /url?q=https://...
                if (href.startsWith('/url?')) {
                    const m = href.match(/[?&]q=([^&]+)/);
                    if (m) href = decodeURIComponent(m[1]);
                }
                if (!href.includes('myworkdayjobs.com')) continue;
                if (seen.has(href)) continue;
                seen.add(href);
                // Only actual job posting URLs (at least 4 path segments)
                const parts = href.replace(/https?:\\/\\/[^/]+/, '').split('/').filter(Boolean);
                if (parts.length < 3) continue;
                // Extract company from subdomain: amazon.wd5.myworkdayjobs.com → Amazon
                const sub = (href.match(/https?:\\/\\/([^.]+)\\./) || [])[1] || '';
                const company = sub.replace(/-/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                // Get link text as title
                const title = (a.innerText || a.textContent || query).trim().split('\\n')[0];
                results.push({ title: title || query, company, url: href, description: '' });
            }
            return results.slice(0, 10);
        }
    """, []) or []

    print(f"  🔍 Google→Workday: {len(links)} jobs for '{query}'")
    return links

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",      type=int, default=5)
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--queue-only", action="store_true")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Workday Apply Engine  {'[DRY RUN]' if args.dry_run else ''}")
    print(f"  Limit: {args.limit} applications")
    print(f"{'='*60}\n")

    secure_store.ensure_cryptography()

    import raghav_profile as rp
    import claude_engine  as ce
    import resume_builder as rb
    import cover_letter   as cl_mod
    import jd_parser      as jdp

    full_profile    = rp.PROFILE
    profile_summary = ce.build_profile_summary(full_profile)

    log      = load_log()
    accounts = secure_store.load_accounts()
    applied  = 0
    scored   = 0
    skipped  = 0
    seen     = set()

    # Seed cache with Workday-specific field labels
    SEED = {
        "First Name":         "Raghavendra",
        "Last Name":          "Karanam",
        "Email Address":      cfg.CANDIDATE_EMAIL,
        "Phone Number":       cfg.CANDIDATE_PHONE,
        "Phone":              cfg.CANDIDATE_PHONE,
        "City":               "Delray Beach",
        "State":              "Florida",
        "Postal Code":        "33484",
        "Zip Code":           "33484",
        "Country":            "United States of America",
        "LinkedIn URL":       "https://www.linkedin.com/in/raghavendra-karanam",
        "Are you legally authorized to work in the United States?": "Yes",
        "Do you require sponsorship now or in the future?":         "No",
        "Will you now or in the future require sponsorship?":       "No",
        "Desired Salary":     "70000",
        "How did you hear about this position?": "LinkedIn",
        "Are you 18 years of age or older?": "Yes",
    }
    seeded = sum(1 for lbl, val in SEED.items() if _cache.get(lbl) is None and not _cache.save(lbl, val) is False)
    if seeded: print(f"  🗄  Seeded {seeded} Workday answers into cache")

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            str(SESSION_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1366, "height": 900},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # Dismiss Chromium "Restore pages?" popup — it blocks all clicks if left open
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
            # Press Escape to close any dialog/popup
            page.keyboard.press("Escape")
            time.sleep(0.5)
            # Also try clicking any "Don't restore" or close button
            for sel in [
                'button:has-text("Don\'t restore")',
                'button:has-text("Cancel")',
                '[aria-label="Close"]',
            ]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=500):
                        btn.click()
                        break
                except Exception:
                    pass
        except Exception:
            pass

        def _process_job(job):
            nonlocal applied, scored, skipped
            title   = job.get("title", "")
            company = job.get("company", "")
            url     = job.get("url", "")
            jd      = job.get("description", "")

            if not url or not is_workday_url(url):
                return
            sk = f"{company.lower().strip()}|{title.lower().strip()}"
            if sk in seen: return
            seen.add(sk)
            if title and (not is_good_level(title) or not is_relevant_domain(title)):
                skipped += 1; return
            # Blocked companies — skip entirely
            blocked = getattr(cfg, "BLOCKED_COMPANIES", set())
            if any(b in company.lower() or b in url.lower() for b in blocked):
                print(f"  🚫 Blocked company — skipping: {company}")
                skipped += 1; return
            if already_applied(url, log, title, company):
                print(f"  ↩  Already applied: {company} — {title}")
                skipped += 1; return

            # Load job page to get description if missing
            if not jd:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(3)
                    info = extract_workday_job(page)
                    title   = title   or info.get("title", "")
                    company = company or info.get("company", "")
                    jd      = info.get("description", "")
                    job.update({"title": title, "company": company, "description": jd})
                except Exception as e:
                    print(f"  ⚠  Could not load job: {e}")

            print(f"\n  📋 {company} — {title}")

            # Score
            result = ce.score_fit(profile_summary, jd, title, company)
            score  = result.get("score", 0) if isinstance(result, dict) else int(result)
            scored += 1
            print(f"  🎯 Fit: {score}%  {'✅' if score >= cfg.FIT_THRESHOLD else '❌'}")
            if score < cfg.FIT_THRESHOLD:
                skipped += 1; return

            # Build resume
            resume_path = ""
            try:
                parsed = jdp.parse_jd(jd, title)
                res = rb.build_resume(
                    job_title=title, company=company,
                    jd_keywords=parsed.get("jd_keywords",[]),
                    injectable_kws=parsed.get("injectable_keywords",[]),
                    initial_score=parsed.get("initial_score",0),
                    optimized_score=parsed.get("optimized_score",0),
                    jd_text=jd,
                    profile_summary=full_profile.get("summary",""),
                )
                resume_path = res[0] if isinstance(res, tuple) else str(res)
                print(f"  ✅ Resume: {Path(resume_path).name}")
            except Exception as e:
                print(f"  ⚠  Resume failed: {e}"); skipped += 1; return

            # Build cover letter
            cover_path = ""
            try:
                cl_text = ce.write_cover_letter(
                    full_profile.get("name","Raghavendra Karanam"),
                    profile_summary, jd, title, company
                )
                cover_path = cl_mod.save_cover_letter(cl_text, title, company)
            except: pass

            # Apply
            print(f"  🚀 Applying to {company}...")
            try:
                success, reason = apply_to_workday_job(
                    page, job, resume_path, cover_path,
                    profile_text=profile_summary, dry_run=args.dry_run
                )
            except Exception as e:
                success, reason = False, str(e)

            status = "Applied" if (success and not args.dry_run) else ("Dry-Run" if args.dry_run else "Failed")
            print(f"  {'✅' if success else '❌'} {status}: {reason}")

            if success and not args.dry_run:
                notifier.notify_applied(
                    title=title, company=company, fit_score=score,
                    resume_path=resume_path, cover_letter_path=cover_path,
                    platform="Workday", job_url=url,
                )
                applied += 1

            log.append({
                "status": status, "title": title, "company": company,
                "score": score, "url": url, "resume": resume_path,
                "timestamp": datetime.now().isoformat(), "platform": "Workday", "reason": reason,
            })
            save_log(log)
            time.sleep(2)

        # ── Source 1: LinkedIn/Indeed queue ───────────────────────────────────
        queue = load_wd_queue()
        pending = [j for j in queue if j.get("status") == "pending"]
        print(f"  📥 Queue: {len(pending)} pending job(s)")
        for job in pending:
            if applied >= args.limit: break
            try:
                _process_job(job)
            except Exception as e:
                print(f"  ⚠  Job error (continuing): {e}")
            job["status"] = "processed"
            save_wd_queue(queue)

        # ── Source 2: Google search for Workday jobs ──────────────────────────
        if not args.queue_only and applied < args.limit:
            print(f"\n  🔍 Searching Google for Workday jobs (site:myworkdayjobs.com)...")
            queries = getattr(cfg, "WORKDAY_QUERIES", cfg.TARGET_ROLES)
            for query in queries:
                if applied >= args.limit: break
                jobs = _search_workday_on_google(page, query)
                for job in jobs:
                    if applied >= args.limit: break
                    try:
                        _process_job(job)
                    except Exception as e:
                        print(f"  ⚠  Job error (continuing): {e}")

        browser.close()

    print(f"\n{'='*60}")
    print(f"  Workday session done")
    print(f"  ✅ Applied:  {applied}")
    print(f"  🎯 Scored:   {scored}")
    print(f"  ⏭  Skipped:  {skipped}")
    print(f"{'='*60}\n")

    _cache.print_stats()
    notifier.notify_session_done(applied, scored, skipped)


if __name__ == "__main__":
    main()
