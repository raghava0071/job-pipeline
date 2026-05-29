#!/opt/anaconda3/bin/python3
# =============================================================================
# AUTO_APPLY.PY — v6  (LinkedIn Easy Apply + Indeed In-Portal ONLY)
#
# STRICT RULES:
#   ✅ LinkedIn  → ONLY jobs with "Easy Apply" button (never external redirect)
#   ✅ Indeed    → ONLY jobs that apply inside Indeed portal (never company site)
#   ❌ Everything else → SKIP, logged as "External — manual apply"
#   ✅ Fit gate  → ONLY jobs with Claude fit_score >= 65%
#   ✅ Resume    → Attaches the job-specific custom resume (.docx) per job
#
# USAGE:
#   python auto_apply.py --dry-run          # Preview queue, no applying
#   python auto_apply.py --execute          # Actually apply
#   python auto_apply.py --execute --limit 3  # Test on 3 jobs first
#   python auto_apply.py --source linkedin  # LinkedIn only
#   python auto_apply.py --source indeed    # Indeed only
# =============================================================================

import sys
import time
import json
import argparse
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    sys.exit("❌  pip install pandas --break-system-packages")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("❌  pip install playwright && python -m playwright install chromium")

# ── Paths ──────────────────────────────────────────────────────────────────────
PIPELINE_DIR  = Path.home() / "job_pipeline"
DATA_DIR      = PIPELINE_DIR / "data"
RESUMES_DIR   = PIPELINE_DIR / "resumes"
COVER_DIR     = PIPELINE_DIR / "cover_letters"
SCREENSHOTS   = DATA_DIR / "screenshots"
APPLY_LOG     = DATA_DIR / "apply_log.json"

# Job sources (in priority order)
JOBS_CSVS = [
    DATA_DIR / "linkedin_jobs.csv",   # from linkedin_scraper.py  (Easy Apply guaranteed)
    DATA_DIR / "indeed_jobs.csv",     # from indeed_scraper.py    (in-portal guaranteed)
    DATA_DIR / "filtered_jobs.csv",   # fallback: JSearch jobs (may have LinkedIn/Indeed URLs)
]

# Persistent browser sessions
LINKEDIN_SESSION = Path.home() / ".linkedin_session"
INDEED_SESSION   = Path.home() / ".indeed_session"

SCREENSHOTS.mkdir(parents=True, exist_ok=True)

# Min Claude fit score to apply
FIT_GATE = 65

# Personal info for form fill
PHONE      = "7038529618"
FIRST_NAME = "Raghavendra"
LAST_NAME  = "Karanam"
EMAIL      = "raghavendrakaranam30@gmail.com"


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get(row, *keys, default=""):
    for k in keys:
        try:
            v = row.get(k)
            if v is not None and str(v).strip() not in ("", "nan", "None"):
                return str(v).strip()
        except Exception:
            continue
    return default

def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "linkedin.com/jobs" in u or "linkedin.com/comm/jobs" in u:
        return "LinkedIn"
    if "indeed.com" in u:
        return "Indeed"
    return "External"

def find_resume(company: str, title: str) -> str:
    """Find the job-specific custom resume in ~/job_pipeline/resumes/"""
    if not RESUMES_DIR.exists():
        return ""
    safe_co = company[:20].replace(" ", "_")
    safe_ti = title[:15].replace(" ", "_")
    name    = f"Raghavendra_Karanam_{safe_co}_{safe_ti}.docx"
    path    = RESUMES_DIR / name
    if path.exists():
        return str(path)
    # Fallback: find any resume for this company
    for f in RESUMES_DIR.glob(f"*{safe_co[:10]}*.docx"):
        return str(f)
    # Last resort: first resume in folder
    files = list(RESUMES_DIR.glob("*.docx"))
    return str(files[0]) if files else ""

def take_screenshot(page, label: str) -> str:
    safe = label.replace(" ", "_").replace("/", "-")[:60]
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS / f"{ts}_{safe}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return ""

def log_result(job: dict, status: str, note: str = "", screenshot: str = ""):
    log = []
    if APPLY_LOG.exists():
        try:
            log = json.loads(APPLY_LOG.read_text())
        except Exception:
            log = []
    log.append({
        "timestamp":  datetime.now().isoformat(),
        "company":    job.get("company", ""),
        "title":      job.get("title", ""),
        "platform":   job.get("platform", ""),
        "url":        job.get("url", ""),
        "fit_score":  job.get("fit_score", 0),
        "status":     status,
        "note":       note,
        "screenshot": screenshot,
    })
    APPLY_LOG.write_text(json.dumps(log, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# LOAD APPLY QUEUE
# ══════════════════════════════════════════════════════════════════════════════

def load_apply_queue(limit: int = 0, source_filter: str = "all") -> list[dict]:
    """
    Load jobs from CSVs, enforce:
      - fit_score >= 65 (Claude gate)
      - platform == LinkedIn or Indeed (no external)
      - not already applied
    """
    all_jobs = []

    for csv_path in JOBS_CSVS:
        if not csv_path.exists():
            continue
        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            print(f"  ⚠️  Could not read {csv_path.name}: {e}")
            continue

        # Find URL column
        url_col = next(
            (c for c in df.columns if any(k in c.lower() for k in
             ["apply_link", "job_url", "url", "link"])),
            None
        )
        if not url_col:
            print(f"  ⚠️  No URL column in {csv_path.name} — skipping")
            continue

        for _, row in df.iterrows():
            url      = _get(row, url_col, "job_apply_link", "apply_link", "url")
            company  = _get(row, "employer_name", "company", "Company")
            title    = _get(row, "job_title", "title", "Title", "job_position")
            fit_score= float(str(row.get("fit_score", 0) or 0))
            fit_grade= _get(row, "fit_grade", "grade", default="")
            fit_reason = _get(row, "fit_reasoning", "reasoning", default="")
            status   = _get(row, "status", "Status", default="").lower()
            platform = detect_platform(url)

            # ── STRICT FILTERS ─────────────────────────────────────────────
            # 1. Must be LinkedIn or Indeed
            if platform == "External":
                continue

            # 2. Must pass Claude fit gate
            if fit_score < FIT_GATE:
                continue

            # 3. Skip if already applied
            if status in ("applied", "already applied"):
                continue

            # 4. Must have a real URL
            if not url or url.lower() in ("nan", "none", ""):
                continue

            # 5. Source filter
            if source_filter == "linkedin" and platform != "LinkedIn":
                continue
            if source_filter == "indeed" and platform != "Indeed":
                continue

            # Find custom resume for this job
            resume_path = _get(row, "resume_path", default="")
            if not resume_path:
                resume_path = find_resume(company, title)

            all_jobs.append({
                "company":    company,
                "title":      title,
                "url":        url,
                "platform":   platform,
                "fit_score":  fit_score,
                "fit_grade":  fit_grade,
                "fit_reason": fit_reason,
                "resume":     resume_path,
                "source_csv": csv_path.name,
            })

    # Deduplicate by URL
    seen = set()
    unique = []
    for j in all_jobs:
        key = j["url"].split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(j)

    # Sort: highest fit score first
    unique.sort(key=lambda x: x["fit_score"], reverse=True)

    if limit > 0:
        unique = unique[:limit]

    return unique


# ══════════════════════════════════════════════════════════════════════════════
# LINKEDIN EASY APPLY
# ══════════════════════════════════════════════════════════════════════════════

def ensure_linkedin_login(page):
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        if "feed" in page.url and page.locator("div.global-nav").count() > 0:
            print("  ✅  LinkedIn: already logged in")
            return
    except Exception:
        pass

    print()
    print("  🔐  LinkedIn login required.")
    print("      The browser opened — please log in manually.")
    print("      Press ENTER here once you see your LinkedIn feed...")
    input("  → ")
    print("  ✅  Continuing...")

def _fill_linkedin_form_step(page, resume_path: str, step: int):
    """Fill one step of the LinkedIn Easy Apply multi-step form."""
    # Upload resume (only on first step)
    if step == 0 and resume_path:
        upload = page.locator("input[type='file']").first
        if upload.count() > 0:
            try:
                upload.set_input_files(resume_path)
                time.sleep(1)
            except Exception:
                pass  # Resume upload failed, continue anyway

    # Fill text inputs
    for inp in page.locator("input[type='text'], input[type='tel']").all():
        try:
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            aria_label  = (inp.get_attribute("aria-label") or "").lower()
            hint        = placeholder + " " + aria_label

            val = inp.input_value() or ""
            if val.strip():
                continue  # Already filled

            if any(w in hint for w in ["phone", "mobile", "contact"]):
                inp.fill(PHONE)
            elif any(w in hint for w in ["first name", "firstname"]):
                inp.fill(FIRST_NAME)
            elif any(w in hint for w in ["last name", "lastname", "surname"]):
                inp.fill(LAST_NAME)
            elif any(w in hint for w in ["email"]):
                inp.fill(EMAIL)
            elif any(w in hint for w in ["city", "location"]):
                inp.fill("Boca Raton, FL")
            elif any(w in hint for w in ["year", "experience", "salary", "compensation"]):
                inp.fill("2")  # safe default for experience/years
        except Exception:
            continue

    # Handle number/numeric inputs
    for inp in page.locator("input[type='number']").all():
        try:
            if not inp.input_value().strip():
                inp.fill("2")
        except Exception:
            continue

    # Handle radio buttons — answer Yes to auth/sponsorship questions
    for radio in page.locator("input[type='radio']").all():
        try:
            rid   = radio.get_attribute("id") or ""
            label = page.locator(f"label[for='{rid}']").first
            if not label.count():
                continue
            ltext = label.inner_text().lower()
            if any(w in ltext for w in ["authorized", "sponsor", "legally", "eligible", "legally allowed"]):
                if "yes" in ltext or "i am" in ltext:
                    radio.click()
                    time.sleep(0.3)
        except Exception:
            continue

    # Handle select/dropdowns
    for sel in page.locator("select").all():
        try:
            aria = (sel.get_attribute("aria-label") or "").lower()
            options = sel.locator("option").all_text_contents()
            if any(w in aria for w in ["year", "experience"]):
                # Pick "2 years" or similar middle option
                for opt in options:
                    if "2" in opt or "1-2" in opt or "1 to 2" in opt.lower():
                        sel.select_option(label=opt)
                        break
            elif any(w in aria for w in ["country", "nation"]):
                sel.select_option(label="United States") if "United States" in options else None
        except Exception:
            continue

def apply_linkedin(page, job: dict, dry_run: bool) -> tuple[str, str]:
    try:
        page.goto(job["url"], wait_until="domcontentloaded", timeout=25000)
        time.sleep(4)   # Wait for React components to fully render

        # Check if already applied
        body_text = page.evaluate("() => document.body.innerText.toLowerCase()")
        if "you applied" in body_text or "application was sent" in body_text:
            return "Already Applied", "LinkedIn shows already applied"

        # ── STRICT: only Easy Apply, never external Apply ──────────────────
        # Use JS evaluation — resilient to LinkedIn DOM/class changes
        easy_apply_info = page.evaluate("""
            () => {
                const elems = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], [role="link"]'
                ));
                for (const el of elems) {
                    const t  = (el.textContent  || '').toLowerCase().trim();
                    const al = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (t.includes('easy apply') || al.includes('easy apply')) {
                        // Return the element's identifying info so we can click it
                        return {
                            found: true,
                            ariaLabel: el.getAttribute('aria-label') || '',
                            text: el.textContent.trim().substring(0, 60),
                            tagName: el.tagName
                        };
                    }
                }
                // Check if there's an external Apply button (non-Easy Apply)
                for (const el of elems) {
                    const t = (el.textContent || '').toLowerCase().trim();
                    if (t === 'apply' || t.startsWith('apply on')) {
                        return { found: false, external: true };
                    }
                }
                return { found: false, external: false };
            }
        """)

        if not easy_apply_info or not easy_apply_info.get("found"):
            if easy_apply_info and easy_apply_info.get("external"):
                return "Skipped", "External Apply only — not Easy Apply. Skip per rules."
            return "Skipped", "No Easy Apply button found (job may be expired or filled)"

        if dry_run:
            return "Dry Run", f"Would click Easy Apply | Resume: {Path(job['resume']).name if job['resume'] else 'none found'}"

        # Click Easy Apply button — use Playwright native click (not JS click)
        # Native click dispatches real mouse events that LinkedIn's handlers respond to
        ea_clicked = False
        for sel in [
            "button[aria-label*='Easy Apply']",
            "button:has-text('Easy Apply')",
            "[data-control-name='jobdetails_topcard_inapply']",
        ]:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    ea_clicked = True
                    break
            except Exception:
                continue

        if not ea_clicked:
            # Fallback: JS click as last resort
            ea_clicked = page.evaluate("""
                () => {
                    const elems = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    for (const el of elems) {
                        const t  = (el.textContent || '').toLowerCase().trim();
                        const al = (el.getAttribute('aria-label') || '').toLowerCase();
                        if ((t.includes('easy apply') || al.includes('easy apply'))
                            && !al.includes('filter')) {
                            el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                            return true;
                        }
                    }
                    return false;
                }
            """)

        if not ea_clicked:
            return "Failed", "Could not click Easy Apply button"
        time.sleep(3)

        # ── Walk multi-step form ───────────────────────────────────────────
        CONFIRM_STRINGS = [
            "application was sent", "your application was submitted",
            "application submitted", "applied to", "successfully applied",
            "your application has been", "application complete",
            "application was sent to", "sent your application",
            "you've applied", "you applied", "application received",
            "thank you for applying", "thanks for applying",
        ]

        def _check_confirmed():
            txt = page.evaluate("() => document.body.innerText.toLowerCase()")
            return any(s in txt for s in CONFIRM_STRINGS), txt

        def _modal_still_open():
            """Returns True if Easy Apply modal is still visible."""
            return page.evaluate("""
                () => {
                    const modal = document.querySelector(
                        '[data-test-modal], .jobs-easy-apply-modal, '
                        '[aria-label*="Easy Apply"], [aria-labelledby*="easy-apply"]'
                    );
                    return !!modal;
                }
            """)

        for step in range(20):
            # Check confirmation before each step
            confirmed, page_text = _check_confirmed()
            if confirmed:
                print(f"      ✅ Form step {step}: confirmation detected")
                return "Applied", "LinkedIn Easy Apply submitted ✅"

            # Check if modal closed — means application was submitted
            if step > 0 and not _modal_still_open():
                print(f"      ✅ Form step {step}: modal closed — application submitted")
                return "Applied", "LinkedIn Easy Apply — modal closed after submit ✅"

            _fill_linkedin_form_step(page, job["resume"], step)
            time.sleep(0.5)

            # Click primary action button using Playwright native click
            # Native click fires real mouse events — required for LinkedIn's JS handlers
            btn_clicked = False
            btn_label_clicked = ""
            for btn_text in ["Submit application", "Review", "Next", "Continue", "Done"]:
                try:
                    btn = page.locator(f"button:has-text('{btn_text}')").first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click()
                        btn_clicked = True
                        btn_label_clicked = btn_text
                        print(f"      Step {step}: clicked '{btn_text}'")
                        break
                except Exception:
                    continue

            if btn_clicked:
                if "Submit" in btn_label_clicked or "Done" in btn_label_clicked:
                    time.sleep(4)
                    confirmed, _ = _check_confirmed()
                    if confirmed:
                        return "Applied", "LinkedIn Easy Apply submitted ✅"
                    if not _modal_still_open():
                        return "Applied", "LinkedIn Easy Apply — modal closed after submit ✅"
                else:
                    time.sleep(2)
            else:
                # No button found — log visible buttons for debug
                visible = page.evaluate("""
                    () => Array.from(document.querySelectorAll('button:not([disabled])'))
                        .filter(b => { const r=b.getBoundingClientRect(); return r.width>0&&r.height>0; })
                        .map(b => b.textContent.trim().substring(0,25))
                        .filter(t => t.length > 0).slice(0,6)
                """)
                print(f"      Step {step}: no action button. Visible: {visible}")
                confirmed, _ = _check_confirmed()
                if confirmed:
                    return "Applied", "LinkedIn Easy Apply submitted ✅"
                if not _modal_still_open():
                    return "Applied", "LinkedIn Easy Apply — submitted (modal gone) ✅"
                break

        # Final check
        confirmed, _ = _check_confirmed()
        if confirmed:
            return "Applied", "LinkedIn Easy Apply submitted ✅"
        if not _modal_still_open():
            return "Applied", "LinkedIn Easy Apply — submitted (modal gone) ✅"

        return "Failed", "Reached form end but couldn't confirm submission"

    except PWTimeout:
        return "Failed", "Page timed out (LinkedIn)"
    except Exception as e:
        return "Failed", f"Error: {str(e)[:120]}"


# ══════════════════════════════════════════════════════════════════════════════
# INDEED IN-PORTAL APPLY
# ══════════════════════════════════════════════════════════════════════════════

def ensure_indeed_login(page):
    try:
        page.goto("https://www.indeed.com/", wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        # Check if logged in (profile icon visible)
        if page.locator("[aria-label*='Account'], .gnav-profile-icon").count() > 0:
            print("  ✅  Indeed: already logged in")
            return
    except Exception:
        pass

    print()
    print("  🔐  Indeed login required.")
    print("      The browser opened — please log in to Indeed.")
    print("      Press ENTER here once you're logged in...")
    input("  → ")
    print("  ✅  Continuing...")

def apply_indeed(page, job: dict, dry_run: bool) -> tuple[str, str]:
    try:
        page.goto(job["url"], wait_until="domcontentloaded", timeout=25000)
        time.sleep(2.5)

        page_text = page.content().lower()

        # Check already applied
        if "you applied" in page_text or "already applied" in page_text:
            return "Already Applied", "Indeed shows already applied"

        # ── STRICT: only in-portal apply, never "Apply on Company Site" ────
        # Indeed's in-portal button has id="indeedApplyButton" or class "ia-IndeedApplyButton"
        portal_btn = page.locator(
            "#indeedApplyButton, "
            ".ia-IndeedApplyButton, "
            "button[data-tn-element='indeedApplyButton'], "
            "span.indeed-apply-button"
        ).first

        if not portal_btn.count() or not portal_btn.is_visible():
            # Check if only "Apply on Company Site" exists
            ext_btn = page.locator("a[href*='apply'], button:has-text('Apply on')").first
            if ext_btn.count():
                return "Skipped", "Apply on Company Site only — not in-portal. Skip per rules."
            return "Skipped", "No apply button found (job may be expired)"

        if dry_run:
            return "Dry Run", f"Would click Indeed Apply | Resume: {Path(job['resume']).name if job['resume'] else 'none'}"

        portal_btn.click()
        time.sleep(2.5)

        # ── Walk Indeed application steps ─────────────────────────────────
        for step in range(10):
            page_text = page.content().lower()

            # Confirmation
            if any(s in page_text for s in [
                "application submitted", "you applied", "resume submitted",
                "your application has been submitted", "successfully submitted"
            ]):
                return "Applied", "Indeed in-portal application confirmed ✅"

            # Upload resume if field appears
            upload = page.locator("input[type='file']").first
            if upload.count() and job["resume"]:
                try:
                    upload.set_input_files(job["resume"])
                    time.sleep(1)
                except Exception:
                    pass

            # Fill text fields
            for inp in page.locator("input[type='text'], input[type='tel'], input[type='email']").all():
                try:
                    hint = ((inp.get_attribute("placeholder") or "") + " " +
                            (inp.get_attribute("aria-label") or "") +
                            (inp.get_attribute("name") or "")).lower()
                    val = inp.input_value() or ""
                    if val.strip():
                        continue
                    if "phone" in hint or "tel" in hint:
                        inp.fill(PHONE)
                    elif "first" in hint:
                        inp.fill(FIRST_NAME)
                    elif "last" in hint or "surname" in hint:
                        inp.fill(LAST_NAME)
                    elif "email" in hint:
                        inp.fill(EMAIL)
                    elif "city" in hint or "location" in hint:
                        inp.fill("Boca Raton, FL")
                except Exception:
                    continue

            # JS-based button click for Indeed — handles any label/CSS variation
            clicked = page.evaluate("""
                () => {
                    const priority = ['submit your application', 'submit', 'continue', 'next', 'apply now'];
                    const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
                    for (const label of priority) {
                        for (const btn of buttons) {
                            const t = (btn.textContent || '').toLowerCase().trim();
                            const al = (btn.getAttribute('aria-label') || '').toLowerCase();
                            if ((t.includes(label) || al.includes(label)) && !btn.disabled) {
                                btn.click();
                                return label;
                            }
                        }
                    }
                    return null;
                }
            """)
            if clicked:
                time.sleep(2)

            if not clicked:
                break

        return "Failed", "Could not confirm Indeed submission"

    except PWTimeout:
        return "Failed", "Page timed out (Indeed)"
    except Exception as e:
        return "Failed", f"Error: {str(e)[:120]}"


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Auto-apply: LinkedIn Easy Apply + Indeed In-Portal ONLY"
    )
    parser.add_argument("--dry-run",  action="store_true",
                        help="Show which jobs qualify — no actual applying")
    parser.add_argument("--execute",  action="store_true",
                        help="Actually apply to jobs")
    parser.add_argument("--limit",    type=int, default=0,
                        help="Max jobs to apply to (0 = no limit)")
    parser.add_argument("--source",   choices=["all", "linkedin", "indeed"],
                        default="all", help="Which platform to apply to")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.print_help()
        print("\n  Use --dry-run to preview, or --execute to apply.\n")
        sys.exit(0)

    dry_run = args.dry_run

    # ── Load queue ─────────────────────────────────────────────────────────
    print()
    print("  Loading apply queue...")
    jobs = load_apply_queue(limit=args.limit, source_filter=args.source)

    linkedin_jobs = [j for j in jobs if j["platform"] == "LinkedIn"]
    indeed_jobs   = [j for j in jobs if j["platform"] == "Indeed"]

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  AUTO-APPLY  —  LinkedIn Easy Apply + Indeed In-Portal       ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Fit gate     : ≥ {FIT_GATE}% Claude score (strict)")
    print(f"  LinkedIn     : {len(linkedin_jobs)} jobs")
    print(f"  Indeed       : {len(indeed_jobs)} jobs")
    print(f"  Mode         : {'DRY RUN — preview only' if dry_run else '🚀 EXECUTE — will apply!'}")
    print()

    if not jobs:
        print("  ℹ️   No qualifying jobs found.")
        print(f"      Need: LinkedIn Easy Apply or Indeed In-Portal URLs + fit_score ≥ {FIT_GATE}%")
        print()
        print("  → Run linkedin_scraper.py first to get fresh Easy Apply jobs.")
        print("  → Run indeed_scraper.py  first to get fresh Indeed portal jobs.")
        print()
        sys.exit(0)

    # Show queue
    print("  ── Queue ──────────────────────────────────────────────────────")
    for i, j in enumerate(jobs, 1):
        resume_name = Path(j["resume"]).name if j["resume"] else "⚠️ no resume"
        print(f"  {i:>2}. [{j['platform']:8}] {j['fit_score']:>3.0f}%  {j['company'][:22]:<22}  {j['title'][:30]}")
        print(f"       Resume: {resume_name}")
    print()

    if dry_run:
        print("  ✅  Dry run complete. Use --execute to actually apply.\n")
        sys.exit(0)

    results = {"Applied": 0, "Already Applied": 0, "Skipped": 0, "Failed": 0}

    # ── LinkedIn ───────────────────────────────────────────────────────────
    if linkedin_jobs:
        LINKEDIN_SESSION.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=str(LINKEDIN_SESSION),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            ensure_linkedin_login(page)

            print(f"\n  🔵  LinkedIn Easy Apply — {len(linkedin_jobs)} job(s)\n")
            for i, job in enumerate(linkedin_jobs, 1):
                print(f"  [{i}/{len(linkedin_jobs)}]  {job['company']} — {job['title']}  ({job['fit_score']:.0f}%)")
                status, note = apply_linkedin(page, job, dry_run=False)
                shot = take_screenshot(page, f"{job['company']}_{job['title']}") if status == "Applied" else ""
                log_result(job, status, note, shot)
                results[status] = results.get(status, 0) + 1

                icon = {"Applied":"✅","Already Applied":"🔵","Skipped":"⏭️","Failed":"❌"}.get(status,"•")
                print(f"         {icon}  {status}  —  {note}")
                time.sleep(3)  # polite pause

            browser.close()

    # ── Indeed ─────────────────────────────────────────────────────────────
    if indeed_jobs:
        INDEED_SESSION.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                user_data_dir=str(INDEED_SESSION),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            ensure_indeed_login(page)

            print(f"\n  🟡  Indeed In-Portal — {len(indeed_jobs)} job(s)\n")
            for i, job in enumerate(indeed_jobs, 1):
                print(f"  [{i}/{len(indeed_jobs)}]  {job['company']} — {job['title']}  ({job['fit_score']:.0f}%)")
                status, note = apply_indeed(page, job, dry_run=False)
                shot = take_screenshot(page, f"{job['company']}_{job['title']}") if status == "Applied" else ""
                log_result(job, status, note, shot)
                results[status] = results.get(status, 0) + 1

                icon = {"Applied":"✅","Already Applied":"🔵","Skipped":"⏭️","Failed":"❌"}.get(status,"•")
                print(f"         {icon}  {status}  —  {note}")
                time.sleep(3)

            browser.close()

    # ── Summary ─────────────────────────────────────────────────────────────
    print()
    print("  ══════════════════════════════════════════════════════")
    print(f"  ✅  Applied         : {results.get('Applied', 0)}")
    print(f"  🔵  Already Applied : {results.get('Already Applied', 0)}")
    print(f"  ⏭️   Skipped (ext)  : {results.get('Skipped', 0)}")
    print(f"  ❌  Failed          : {results.get('Failed', 0)}")
    print(f"\n  📋  Log → {APPLY_LOG}")
    if results.get("Applied", 0):
        print(f"  📸  Screenshots → {SCREENSHOTS}")
    print("  ══════════════════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
