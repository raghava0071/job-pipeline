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
from datetime import datetime

PIPELINE_DIR = Path.home() / "job_pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import config as cfg
import answer_cache as _cache   # SQLite answer cache — avoids repeat Claude calls
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
    t = title.lower()
    return not any(bad in t for bad in cfg.SENIOR_WORDS)

def build_url(kw):
    import urllib.parse
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode({
        "keywords": kw, "location": "United States",
        "sortBy": "DD", "f_TPR": "r604800", "f_LF": "f_AL",
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
    print("\n  🔐  Log in to LinkedIn then press ENTER...")
    input("  → ")

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

            return { title, company, location, description, hasEasyApply, jobUrl };
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
Email: {p.get('email','raghavendrakaranam30@gmail.com')}
Phone: {p.get('phone','7038529618')}
Location: Boca Raton, FL 33431
LinkedIn: {p.get('linkedin_url','https://www.linkedin.com/in/raghavendra-karanam')}
GitHub: {p.get('github_url','https://github.com/raghavendrakaranam')}
Portfolio: {p.get('portfolio_url','https://www.linkedin.com/in/raghavendra-karanam')}

Education:
  Master of Science, Data Science & Analytics — Florida Atlantic University (May 2025), GPA 3.5
  B.Tech, Computer Science & Engineering — JNTU (2022)

Work Authorization: F-1 OPT/STEM OPT — legally authorized to work in USA, NO sponsorship needed
Total Professional Experience: 3+ years (including undergrad projects, internships, grad research)
Expected Salary: $75,000–$90,000 (negotiable)
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
- For textarea: return a professional 1–2 sentence response relevant to the question and the job
- For select: return the EXACT option value (from the "value" key in options), pick the most appropriate
- For radio: return the EXACT option id to click
- For checkbox: return "check" if it should be checked (e.g. agreement/terms), else "skip"
- Work authorization: candidate IS authorized (OPT), does NOT need sponsorship → answer Yes/authorized
- For "years of X experience" questions: use the per-skill years in the profile above (Python=4, SQL=4, etc.)
- Salary: 75000 (or 75000-90000 if a range is needed)
- For unknown/unclear questions: use best professional judgment from the profile context
- NEVER answer less than 2 for any data/programming/analytics skill
- Do NOT add any explanation — return raw JSON only"""

        # ── Check cache for each field individually ─────────────────────────────
        cached_answers = {}
        uncached_fields = []
        for f in fields:
            lbl = f.get("label", f.get("name", ""))
            cached = _cache.get(lbl)
            if cached is not None:
                fid = f.get("id","") or f.get("name","")
                cached_answers[fid] = cached
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
                max_tokens=1500,   # 9 fields with textarea can need 1000+ tokens
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
            # Save new answers to cache
            for f in uncached_fields:
                lbl = f.get("label", f.get("name",""))
                fid = f.get("id","") or f.get("name","")
                if fid in new_answers:
                    _cache.save(lbl, new_answers[fid])
            merged = {**cached_answers, **new_answers}
            return merged
        except Exception as e:
            print(f"        ⚠ Claude field-fill error: {e}")
            return cached_answers  # return whatever we got from cache at least

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
                        sel.select_option(value=str(ans))
                        print(f"          📋 '{field.get('label','?')[:40]}' → '{str(ans)[:50]}'")
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

RESUME UPLOADED: {'✅ YES — ' + str(resume_path).split('/')[-1] if resume_uploaded else '❌ NO — resume may NOT have uploaded'}

REVIEW PAGE TEXT (what LinkedIn shows before Submit):
{review_text[:2000]}

IMPORTANT RULES FOR YOUR REVIEW:
- The email shown is the LinkedIn account email — it CANNOT be changed. Do NOT flag email as an issue.
- The phone shown is from LinkedIn's saved profile — acceptable, do NOT flag.
- Only flag REAL blocking issues: resume not uploaded, completely blank required fields, or obvious wrong answers.
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

    for step in range(cfg.FORM_MAX_STEPS):
        if confirmed():
            return True, "confirmed via page text"
        if step > 0 and not modal_open():
            return True, "modal closed after submit"

        time.sleep(1.5)  # let DOM settle

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
                        print(f"          ⚠  No upload trigger — LinkedIn using profile resume")
                        uploaded = True   # stop retrying

                if uploaded:
                    resume_uploaded = True

            except Exception as e:
                print(f"          ⚠  Resume upload error: {e}")
                resume_uploaded = True
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

        # ── Claude reads all fields on this step and answers them ──────────
        fields = extract_form_fields()
        if fields:
            print(f"        🤖 Claude answering {len(fields)} field(s) on step {step}...")
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

                    # ── PRE-SUBMIT REVIEW before hitting Submit ────────────
                    if "Submit" in btn_text and "Review" not in btn_text:
                        safe = claude_pre_submit_review()
                        if not safe:
                            print(f"        ❌ Claude says DO NOT SUBMIT — skipping this application")
                            return False, "Claude review blocked submission"

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
            wait = 4 if "Submit" in btn_clicked or "Done" in btn_clicked else 2
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

    full_profile = {
        **rp.PROFILE,
        "skills":     getattr(rp, "ALL_SKILLS_FLAT", []) or
                      [s for grp in getattr(rp, "SKILLS", {}).values() for s in grp],
        "experience": getattr(rp, "EXPERIENCE", []),
        "education":  getattr(rp, "EDUCATION",  []),
    }
    profile_summary = ce.build_profile_summary(full_profile)

    log = load_log()
    applied_count = 0
    total_processed = 0

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

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
                    continue

                if not has_ea:
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

                if score < ce.FIT_THRESHOLD:
                    print(f"      Below gate ({ce.FIT_THRESHOLD}%) — skip")
                    log.append({"timestamp": datetime.now().isoformat(), "company": company,
                                "title": title, "url": live_url, "fit_score": score,
                                "status": "Below Gate", "note": f"{score:.0f}% < {ce.FIT_THRESHOLD}%"})
                    save_log(log)
                    continue

                # Build resume
                print(f"      Building resume...")
                resume_path = ""
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

                # Cover letter
                try:
                    cl_text = ce.write_cover_letter(rp.PROFILE.get("name","Raghavendra Karanam"),
                                                    profile_summary, description, title, company)
                    cl_mod.save_cover_letter(cl_text, title, company)
                    print(f"      ✅ Cover letter saved")
                except Exception as e:
                    print(f"      ⚠️  Cover letter error: {e}")

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
                    # Screenshot confirmation page
                    try:
                        safe_co = re.sub(r'[^\w]', '_', company)[:30]
                        safe_ti = re.sub(r'[^\w]', '_', title)[:30]
                        ss_path = SCREENSHOTS / f"{safe_co}_{safe_ti}.png"
                        page.screenshot(path=str(ss_path), full_page=False)
                    except Exception:
                        pass
                    # Email notification — attaches resume + cover letter
                    notifier.notify_applied(
                        title=title,
                        company=company,
                        fit_score=score,
                        resume_path=resume_path or "",
                        cover_letter_path=cover_letter_path or "",
                        platform="LinkedIn",
                        job_url=live_url or job_url or "",
                        screenshot_path=str(ss_path) if "ss_path" in dir() else ""
                    )
                    time.sleep(3)

                # Go back to search results
                page.go_back()
                time.sleep(2)

            time.sleep(2)

        browser.close()

    applied = sum(1 for e in log if e.get("status") == "Applied")
    print(f"\n  ── Done: {applied_count} applied this session | {total_processed} scored ──")
    _cache.print_stats()
    notifier.notify_session_done(applied=applied_count,
                                  scored=total_processed,
                                  skipped=total_processed - applied_count)


if __name__ == "__main__":
    main()
