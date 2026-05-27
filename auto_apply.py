#!/opt/anaconda3/bin/python3
# =============================================================================
# AUTO_APPLY.PY — Simple v5
#
# WHAT IT DOES:
#   - Opens ONE browser window (no 50-tab explosion)
#   - Applies to LinkedIn Easy Apply jobs one at a time
#   - Applies to Indeed Easy Apply jobs one at a time
#   - Skips external/direct jobs entirely (no RAM kill)
#   - Takes a screenshot after each successful apply
#   - Updates tracker with honest status
#
# USAGE:
#   python auto_apply.py --dry-run     # Preview which jobs qualify, don't apply
#   python auto_apply.py --execute     # Actually apply
#   python auto_apply.py --limit 5     # Apply to first 5 jobs only
# =============================================================================

import sys
import time
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    sys.exit("❌  Run:  pip install pandas   then try again.")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("❌  Run:  pip install playwright && python -m playwright install chromium   then try again.")

# ── Paths ─────────────────────────────────────────────────────────────────────
PIPELINE_DIR   = Path.home() / "job_pipeline"
DATA_DIR       = PIPELINE_DIR / "data"
JOBS_CSV       = DATA_DIR / "filtered_jobs.csv"
APPLY_LOG      = DATA_DIR / "apply_log.json"
SCREENSHOTS    = DATA_DIR / "screenshots"
SESSION_DIR    = Path.home() / ".linkedin_session"   # persistent Chrome profile

SCREENSHOTS.mkdir(parents=True, exist_ok=True)

# ── Platform detection ────────────────────────────────────────────────────────
def detect_platform(url: str) -> str:
    url = (url or "").lower()
    if "linkedin.com" in url:
        return "LinkedIn"
    if "indeed.com" in url:
        return "Indeed"
    return "External"

def is_automatable(platform: str) -> bool:
    return platform in ("LinkedIn", "Indeed")

# ── Load jobs ─────────────────────────────────────────────────────────────────
def load_apply_queue(limit: int = 0) -> list[dict]:
    if not JOBS_CSV.exists():
        sys.exit(f"❌  {JOBS_CSV} not found. Run the pipeline first.")

    df = pd.read_csv(JOBS_CSV)

    # Normalize column names
    url_col = next((c for c in df.columns if "url" in c.lower() or "link" in c.lower()), None)
    if not url_col:
        sys.exit("❌  No URL/link column found in filtered_jobs.csv")

    jobs = []
    for _, row in df.iterrows():
        url      = str(row.get(url_col, "")).strip()
        company  = str(row.get("company", row.get("Company", "Unknown"))).strip()
        title    = str(row.get("title", row.get("Title", row.get("job_title", "")))).strip()
        status   = str(row.get("status", row.get("Status", ""))).strip().lower()
        platform = detect_platform(url)

        # Skip already applied or non-automatable
        if status in ("applied", "already applied"):
            continue
        if not is_automatable(platform):
            continue
        if not url or url == "nan":
            continue

        jobs.append({
            "company":  company,
            "title":    title,
            "url":      url,
            "platform": platform,
        })

    if limit > 0:
        jobs = jobs[:limit]

    return jobs

# ── Logging ───────────────────────────────────────────────────────────────────
def log_result(job: dict, status: str, note: str = "", screenshot: str = ""):
    log = []
    if APPLY_LOG.exists():
        try:
            log = json.loads(APPLY_LOG.read_text())
        except Exception:
            log = []

    log.append({
        "timestamp": datetime.now().isoformat(),
        "company":   job["company"],
        "title":     job["title"],
        "platform":  job["platform"],
        "url":       job["url"],
        "status":    status,
        "note":      note,
        "screenshot": screenshot,
    })
    APPLY_LOG.write_text(json.dumps(log, indent=2))

def take_screenshot(page, job: dict) -> str:
    name = f"{job['company']}_{job['title']}".replace(" ", "_").replace("/", "-")[:60]
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS / f"{ts}_{name}.png"
    try:
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return ""

# ── LinkedIn Easy Apply ───────────────────────────────────────────────────────
def apply_linkedin(page, job: dict, dry_run: bool) -> tuple[str, str]:
    """Returns (status, note)"""
    try:
        page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)

        # Check if already applied
        if page.locator("text=Applied").count() > 0:
            return "Already Applied", "LinkedIn shows already applied"

        # Find Easy Apply button
        btn = page.locator("button.jobs-apply-button, button[aria-label*='Easy Apply']").first
        if not btn.is_visible():
            return "Skipped", "No Easy Apply button — external application required"

        if dry_run:
            return "Dry Run", "Would click Easy Apply"

        btn.click()
        time.sleep(2)

        # Walk through multi-step form
        for step in range(10):
            # Fill phone if empty
            phone_field = page.locator("input[id*='phoneNumber'], input[name*='phone']").first
            if phone_field.is_visible():
                if not phone_field.input_value():
                    phone_field.fill("7038529618")

            # Answer "Yes" to work authorization radio buttons
            for radio in page.locator("input[type='radio']").all():
                label = page.locator(f"label[for='{radio.get_attribute('id')}']").first
                if label.is_visible() and any(w in label.inner_text().lower() for w in ["authorized", "sponsor", "legally"]):
                    if "yes" in label.inner_text().lower():
                        radio.click()

            # Click Next / Review / Submit
            for btn_text in ["Submit application", "Review", "Next"]:
                btn = page.locator(f"button:has-text('{btn_text}')").first
                if btn.is_visible():
                    btn.click()
                    time.sleep(1.5)
                    break

            # Check for confirmation
            page_text = page.content().lower()
            if any(s in page_text for s in ["application was sent", "your application was submitted", "application submitted"]):
                return "Applied", "LinkedIn Easy Apply confirmed ✅"

            # Dismiss if stuck
            dismiss = page.locator("button[aria-label='Dismiss']").first
            if dismiss.is_visible():
                break

        return "Failed", "Could not confirm submission"

    except PWTimeout:
        return "Failed", "Page timed out"
    except Exception as e:
        return "Failed", str(e)[:120]

# ── Indeed Apply ──────────────────────────────────────────────────────────────
def apply_indeed(page, job: dict, dry_run: bool) -> tuple[str, str]:
    """Returns (status, note)"""
    try:
        page.goto(job["url"], wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)

        # Find Indeed Apply button (not "Apply on company site" which opens external)
        btn = page.locator("button#indeedApplyButton, button:has-text('Apply now')").first
        if not btn.is_visible():
            return "Skipped", "No Indeed Easy Apply — external link only"

        # Make sure it's not an external redirect
        btn_text = btn.inner_text().lower()
        if "company site" in btn_text or "external" in btn_text:
            return "Skipped", "Redirects to external site — skipping"

        if dry_run:
            return "Dry Run", "Would click Apply on Indeed"

        btn.click()
        time.sleep(2)

        # Walk through Indeed application steps
        for step in range(8):
            page_text = page.content().lower()

            # Confirmation check
            if any(s in page_text for s in ["application submitted", "you applied", "resume submitted"]):
                return "Applied", "Indeed application confirmed ✅"

            # Click Continue / Next / Submit
            for btn_text in ["Submit your application", "Continue", "Next"]:
                b = page.locator(f"button:has-text('{btn_text}')").first
                if b.is_visible():
                    b.click()
                    time.sleep(1.5)
                    break

        return "Failed", "Could not confirm submission on Indeed"

    except PWTimeout:
        return "Failed", "Page timed out"
    except Exception as e:
        return "Failed", str(e)[:120]

# ── Ensure LinkedIn login ─────────────────────────────────────────────────────
def ensure_linkedin_login(page):
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
    time.sleep(2)

    if "feed" in page.url and page.locator("div.global-nav").is_visible():
        print("  ✅  Already logged into LinkedIn")
        return

    print()
    print("  🔐  LinkedIn login required.")
    print("      The browser is open — log in manually, then come back here.")
    print("      Press ENTER once you're logged in and can see your LinkedIn feed...")
    input()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Simple auto-apply: LinkedIn + Indeed Easy Apply only")
    parser.add_argument("--dry-run",  action="store_true", help="Preview jobs without applying")
    parser.add_argument("--execute",  action="store_true", help="Actually apply")
    parser.add_argument("--limit",    type=int, default=0,  help="Max number of jobs to apply to")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.print_help()
        print("\n  Use --dry-run to preview, or --execute to apply.")
        sys.exit(0)

    dry_run = args.dry_run

    # ── Load queue ────────────────────────────────────────────────────────────
    jobs = load_apply_queue(limit=args.limit)

    if not jobs:
        print("\n  ✅  No automatable jobs found in queue.")
        print("      (All LinkedIn/Indeed jobs may already be applied, or none exist yet.)")
        sys.exit(0)

    linkedin_jobs = [j for j in jobs if j["platform"] == "LinkedIn"]
    indeed_jobs   = [j for j in jobs if j["platform"] == "Indeed"]

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  AUTO-APPLY  —  LinkedIn Easy Apply + Indeed Apply           ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  LinkedIn jobs : {len(linkedin_jobs)}")
    print(f"  Indeed jobs   : {len(indeed_jobs)}")
    print(f"  Mode          : {'DRY RUN (no actual applying)' if dry_run else '🚀 EXECUTE — will apply!'}")
    print()

    results = {"Applied": 0, "Already Applied": 0, "Skipped": 0, "Failed": 0, "Dry Run": 0}

    # ── One browser window for everything ────────────────────────────────────
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # ── LinkedIn jobs ─────────────────────────────────────────────────────
        if linkedin_jobs:
            ensure_linkedin_login(page)
            print(f"\n  🔵  Processing {len(linkedin_jobs)} LinkedIn Easy Apply job(s)...\n")

            for i, job in enumerate(linkedin_jobs, 1):
                print(f"  [{i}/{len(linkedin_jobs)}]  {job['company']} — {job['title']}")
                status, note = apply_linkedin(page, job, dry_run)
                shot = take_screenshot(page, job) if status == "Applied" else ""
                log_result(job, status, note, shot)
                results[status] = results.get(status, 0) + 1

                icon = {"Applied": "✅", "Already Applied": "🔵", "Skipped": "⏭️", "Failed": "❌", "Dry Run": "👁️"}.get(status, "•")
                print(f"         {icon}  {status}  —  {note}")
                time.sleep(2)  # polite pause between jobs

        # ── Indeed jobs ───────────────────────────────────────────────────────
        if indeed_jobs:
            print(f"\n  🟡  Processing {len(indeed_jobs)} Indeed Easy Apply job(s)...\n")

            for i, job in enumerate(indeed_jobs, 1):
                print(f"  [{i}/{len(indeed_jobs)}]  {job['company']} — {job['title']}")
                status, note = apply_indeed(page, job, dry_run)
                shot = take_screenshot(page, job) if status == "Applied" else ""
                log_result(job, status, note, shot)
                results[status] = results.get(status, 0) + 1

                icon = {"Applied": "✅", "Already Applied": "🔵", "Skipped": "⏭️", "Failed": "❌", "Dry Run": "👁️"}.get(status, "•")
                print(f"         {icon}  {status}  —  {note}")
                time.sleep(2)

        browser.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("  ══════════════════════════════════════════════════════")
    print(f"  ✅  Applied         : {results.get('Applied', 0)}")
    print(f"  🔵  Already Applied : {results.get('Already Applied', 0)}")
    print(f"  ⏭️   Skipped         : {results.get('Skipped', 0)}")
    print(f"  ❌  Failed          : {results.get('Failed', 0)}")
    if dry_run:
        print(f"  👁️   Dry Run jobs   : {results.get('Dry Run', 0)}")
    print(f"\n  📋  Log saved to: {APPLY_LOG}")
    if results.get("Applied", 0) > 0:
        print(f"  📸  Screenshots : {SCREENSHOTS}")
    print("  ══════════════════════════════════════════════════════")
    print()

if __name__ == "__main__":
    main()
