#!/opt/anaconda3/bin/python3
# =============================================================================
# INDEED_SCRAPER.PY — Fetch Indeed In-Portal Apply jobs directly
#
# WHAT IT DOES:
#   - Searches Indeed for data roles (entry/mid level only)
#   - Collects ONLY jobs where apply stays inside Indeed portal
#   - SKIPS "Apply on Company Site" (those are external, not in-portal)
#   - Saves to ~/job_pipeline/data/indeed_jobs.csv
#   - auto_apply.py reads this to apply automatically
#
# USAGE:
#   python indeed_scraper.py              # Scrape with login
#   python indeed_scraper.py --limit 30   # Max 30 jobs per query
#   python indeed_scraper.py --dry-run    # Preview without saving
# =============================================================================

import sys
import time
import argparse
import urllib.parse
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

# ── Config ─────────────────────────────────────────────────────────────────────
PIPELINE_DIR  = Path.home() / "job_pipeline"
DATA_DIR      = PIPELINE_DIR / "data"
OUTPUT_CSV    = DATA_DIR / "indeed_jobs.csv"
SESSION_DIR   = Path.home() / ".indeed_session"

DATA_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_QUERIES = [
    {"q": "data engineer entry level",        "l": "United States"},
    {"q": "junior data engineer",             "l": "United States"},
    {"q": "data analyst entry level",         "l": "United States"},
    {"q": "ETL developer entry level",        "l": "United States"},
    {"q": "azure data engineer",              "l": "United States"},
    {"q": "cloud data engineer entry level",  "l": "United States"},
    {"q": "business intelligence analyst",    "l": "United States"},
]

REJECT_TITLES = [
    "senior", "sr.", "lead", "principal", "staff", "director",
    "manager", "vp ", "vice president", "head of", "chief", "architect"
]

def is_good_level(title: str) -> bool:
    t = title.lower()
    return not any(bad in t for bad in REJECT_TITLES)

def build_indeed_url(q: str, l: str) -> str:
    params = {
        "q":      q,
        "l":      l,
        "sort":   "date",
        "fromage": "7",    # Last 7 days
        "sc":     "0kf%3Aattr(DSQF7)%3B",  # Indeed Apply filter
    }
    return "https://www.indeed.com/jobs?" + urllib.parse.urlencode(params)

def ensure_login(page):
    try:
        page.goto("https://www.indeed.com/", wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        if page.locator("[data-gnav-element-name='SignIn']").count() == 0:
            print("  ✅  Indeed: logged in")
            return
    except Exception:
        pass

    print()
    print("  🔐  Indeed login required.")
    print("      The browser opened. Please log in to Indeed.")
    print("      Press ENTER once you're logged in...")
    input("  → ")
    print("  ✅  Continuing...\n")

def is_indeed_portal_apply(page) -> bool:
    """
    Returns True ONLY if the job uses Indeed's in-portal apply.
    Returns False if it says 'Apply on company site' (external).
    """
    # Indeed Apply button IDs/classes
    portal_selectors = [
        "#indeedApplyButton",
        ".ia-IndeedApplyButton",
        "button[data-tn-element='indeedApplyButton']",
        "span.indeed-apply-button",
        "button:has-text('Apply now'):not(:has-text('company site'))",
    ]
    for sel in portal_selectors:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                txt = el.inner_text().lower()
                if "company site" not in txt and "company website" not in txt:
                    return True
        except Exception:
            continue
    return False

def scrape_indeed_page(page, url: str, max_jobs: int = 20) -> list[dict]:
    jobs = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(3)

        # Find job cards
        cards = page.locator(
            "div.job_seen_beacon, "
            "div.slider_container, "
            "li.css-5lfssm"
        ).all()

        print(f"    Found {len(cards)} job cards")

        for card in cards[:max_jobs]:
            try:
                # Click card to load detail
                card.click()
                time.sleep(2.5)

                # Title
                title_el = page.locator(
                    "h2.jobsearch-JobInfoHeader-title span, "
                    "h1.jobTitle, "
                    "[data-testid='simpleTitle']"
                ).first
                title = title_el.inner_text().strip() if title_el.count() else ""

                # Company
                company_el = page.locator(
                    "[data-company-name='true'], "
                    ".jobsearch-InlineCompanyRating a, "
                    "[data-testid='inlineHeader-companyName'] a"
                ).first
                company = company_el.inner_text().strip() if company_el.count() else ""

                # Location
                loc_el = page.locator(
                    "[data-testid='job-location'], "
                    ".jobsearch-JobInfoHeader-subtitle div:nth-child(2)"
                ).first
                location = loc_el.inner_text().strip() if loc_el.count() else ""

                # ── STRICT: must be in-portal, not external ──────────────
                if not is_indeed_portal_apply(page):
                    continue  # "Apply on company site" — skip

                if not title or not is_good_level(title):
                    continue

                if not company:
                    continue

                # Get job URL
                job_url = page.url

                # Description
                desc_el = page.locator(
                    "#jobDescriptionText, "
                    ".jobsearch-jobDescriptionText"
                ).first
                description = desc_el.inner_text()[:3000] if desc_el.count() else ""

                # Parse location
                city  = location.split(",")[0].strip() if "," in location else location
                state = location.split(",")[-1].strip() if "," in location else ""

                jobs.append({
                    "job_title":       title,
                    "employer_name":   company,
                    "job_city":        city,
                    "job_state":       state,
                    "job_apply_link":  job_url,
                    "job_description": description,
                    "is_portal_apply": True,
                    "platform":        "Indeed",
                    "fetched_at":      datetime.now().isoformat(),
                    "fit_score":       0,
                    "fit_grade":       "",
                    "fit_apply":       False,
                })

                print(f"      ✅  {company} — {title}")

            except Exception:
                continue

    except PWTimeout:
        print(f"    ⚠️  Timed out: {url[:80]}")
    except Exception as e:
        print(f"    ⚠️  Error: {e}")

    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Scrape Indeed in-portal apply jobs")
    parser.add_argument("--limit",   type=int, default=20,  help="Max jobs per search")
    parser.add_argument("--dry-run", action="store_true",   help="Preview only, don't save")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  INDEED IN-PORTAL SCRAPER                                    ║")
    print("║  Entry & Mid Level Data Roles  •  In-Portal Apply ONLY       ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Searches : {len(SEARCH_QUERIES)}")
    print(f"  Max/query: {args.limit}")
    print()

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    all_jobs = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        ensure_login(page)

        for i, q in enumerate(SEARCH_QUERIES, 1):
            print(f"  [{i}/{len(SEARCH_QUERIES)}]  Searching: '{q['q']}'...")
            url  = build_indeed_url(q["q"], q["l"])
            jobs = scrape_indeed_page(page, url, max_jobs=args.limit)
            print(f"    → {len(jobs)} in-portal jobs found")
            all_jobs.extend(jobs)
            time.sleep(2)

        browser.close()

    # Deduplicate
    seen, unique = set(), []
    for j in all_jobs:
        key = j["job_apply_link"].split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(j)

    print()
    print(f"  ── Total unique Indeed in-portal jobs: {len(unique)} ──")

    if args.dry_run:
        for j in unique:
            print(f"    {j['employer_name']:<25}  {j['job_title']}")
        print("\n  Dry run — not saved.\n")
        return

    if not unique:
        print("  ⚠️  No in-portal jobs found. Indeed may need login refresh.")
        return

    df = pd.DataFrame(unique)
    df.to_csv(str(OUTPUT_CSV), index=False)
    print(f"\n  ✅  Saved {len(unique)} jobs → {OUTPUT_CSV}")
    print(f"  Next: Run master_run.py --skip-fetch to score & build resumes\n")


if __name__ == "__main__":
    main()
