#!/opt/anaconda3/bin/python3
# =============================================================================
# LINKEDIN_SCRAPER.PY — Fetch Easy Apply jobs directly from LinkedIn
#
# WHAT IT DOES:
#   - Searches LinkedIn Jobs for data roles (entry/mid level only)
#   - Collects ONLY jobs that have "Easy Apply" button (never external)
#   - Saves to ~/job_pipeline/data/linkedin_jobs.csv
#   - auto_apply.py reads this CSV to know exactly which jobs to apply to
#
# USAGE:
#   python linkedin_scraper.py             # Scrape with login prompt
#   python linkedin_scraper.py --limit 30  # Max 30 jobs
#   python linkedin_scraper.py --dry-run   # List found jobs, don't save
# =============================================================================

import sys
import time
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

# ── Config ─────────────────────────────────────────────────────────────────────
PIPELINE_DIR     = Path.home() / "job_pipeline"
DATA_DIR         = PIPELINE_DIR / "data"
OUTPUT_CSV       = DATA_DIR / "linkedin_jobs.csv"
SESSION_DIR      = Path.home() / ".linkedin_session"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Job searches to run on LinkedIn
SEARCH_QUERIES = [
    {"keywords": "Data Engineer Entry Level",        "location": "United States", "f_AL": "true"},
    {"keywords": "Junior Data Engineer",              "location": "United States", "f_AL": "true"},
    {"keywords": "Data Analyst Entry Level",          "location": "United States", "f_AL": "true"},
    {"keywords": "Data Engineer Azure",               "location": "United States", "f_AL": "true"},
    {"keywords": "Cloud Data Engineer Entry Level",   "location": "United States", "f_AL": "true"},
    {"keywords": "ETL Developer Entry Level",         "location": "United States", "f_AL": "true"},
    {"keywords": "Business Intelligence Analyst",     "location": "United States", "f_AL": "true"},
]

# Titles to REJECT (senior/lead roles)
REJECT_TITLE_KEYWORDS = [
    "senior", "sr.", "lead", "principal", "staff", "director",
    "manager", "vp ", "vice president", "head of", "chief", "architect",
    "distinguished", "fellow"
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def is_good_level(title: str) -> bool:
    t = title.lower()
    return not any(bad in t for bad in REJECT_TITLE_KEYWORDS)

def build_linkedin_search_url(keywords: str, location: str, easy_apply: bool = True) -> str:
    """Build LinkedIn Jobs search URL with Easy Apply filter."""
    import urllib.parse
    params = {
        "keywords": keywords,
        "location": location,
        "sortBy": "DD",          # Date Descending (newest first)
        "f_TPR": "r604800",      # Posted in last 7 days
    }
    if easy_apply:
        params["f_LF"] = "f_AL"  # Easy Apply filter
    base = "https://www.linkedin.com/jobs/search/?"
    return base + urllib.parse.urlencode(params)

def ensure_login(page):
    """Make sure we're logged into LinkedIn."""
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
    print("      The browser opened — please log in to LinkedIn.")
    print("      Press ENTER once you can see your LinkedIn home feed...")
    input("  → ")
    print("  ✅  Logged in. Starting scrape...\n")

def scrape_search_page(page, url: str, max_per_query: int = 20) -> list[dict]:
    """Scrape one LinkedIn search results page for Easy Apply jobs."""
    jobs = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(3)

        # Scroll to load more jobs
        for _ in range(3):
            page.keyboard.press("End")
            time.sleep(1.5)

        # Find job cards
        job_cards = page.locator(
            ".jobs-search__results-list li, "
            ".scaffold-layout__list-container li, "
            "li.ember-view.jobs-search-results__list-item"
        ).all()

        print(f"    Found {len(job_cards)} job cards on page")

        for card in job_cards[:max_per_query]:
            try:
                # Click card to load job detail panel
                card.click()
                time.sleep(2)

                # Get job details from right panel
                detail = page.locator(".jobs-details, .job-view-layout, .jobs-search__job-details")

                # Title
                title_el = page.locator(
                    ".jobs-details-top-card__job-title, "
                    "h1.t-24, "
                    ".job-details-jobs-unified-top-card__job-title h1"
                ).first
                title = title_el.inner_text().strip() if title_el.count() else ""

                # Company
                company_el = page.locator(
                    ".jobs-details-top-card__company-url, "
                    ".jobs-unified-top-card__company-name a, "
                    ".job-details-jobs-unified-top-card__company-name a"
                ).first
                company = company_el.inner_text().strip() if company_el.count() else ""

                # Location
                loc_el = page.locator(
                    ".jobs-details-top-card__bullet, "
                    ".jobs-unified-top-card__bullet, "
                    ".job-details-jobs-unified-top-card__primary-description-without-tagline .tvm__text"
                ).first
                location = loc_el.inner_text().strip() if loc_el.count() else ""

                # Check for Easy Apply button (strict)
                easy_btn = page.locator(
                    "button.jobs-apply-button:has-text('Easy Apply'), "
                    "button[aria-label*='Easy Apply']"
                ).first
                is_easy_apply = easy_btn.count() > 0 and easy_btn.is_visible()

                if not is_easy_apply:
                    continue  # Skip non-Easy-Apply jobs

                # Get job URL
                job_url = page.url
                # Try to get direct job URL from card
                link = card.locator("a.job-card-list__title, a.job-card-container__link").first
                if link.count():
                    href = link.get_attribute("href") or ""
                    if href:
                        job_url = "https://www.linkedin.com" + href if href.startswith("/") else href
                        job_url = job_url.split("?")[0]  # clean URL

                # Skip if title doesn't match level filter
                if not title or not is_good_level(title):
                    continue

                # Skip if company missing
                if not company:
                    continue

                # Job description (for Claude scoring later)
                desc_el = page.locator(
                    ".jobs-description__content, "
                    "#job-details, "
                    ".jobs-description-content__text"
                ).first
                description = desc_el.inner_text()[:3000] if desc_el.count() else ""

                jobs.append({
                    "job_title":        title,
                    "employer_name":    company,
                    "job_city":         location.split(",")[0].strip() if "," in location else location,
                    "job_state":        location.split(",")[-1].strip() if "," in location else "",
                    "job_apply_link":   job_url,
                    "job_description":  description,
                    "is_easy_apply":    True,
                    "platform":         "LinkedIn",
                    "fetched_at":       datetime.now().isoformat(),
                    "fit_score":        0,   # Claude will score this
                    "fit_grade":        "",
                    "fit_apply":        False,
                })

                print(f"      ✅  {company} — {title}")

            except Exception as e:
                continue

    except PWTimeout:
        print(f"    ⚠️  Page timed out: {url[:80]}")
    except Exception as e:
        print(f"    ⚠️  Error: {e}")

    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Scrape LinkedIn Easy Apply jobs")
    parser.add_argument("--limit",   type=int, default=20,  help="Max jobs per search query")
    parser.add_argument("--dry-run", action="store_true",   help="Show results, don't save CSV")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  LINKEDIN EASY APPLY SCRAPER                                 ║")
    print("║  Entry & Mid Level Data Roles  •  Easy Apply ONLY            ║")
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

        for i, query in enumerate(SEARCH_QUERIES, 1):
            kw  = query["keywords"]
            loc = query["location"]
            print(f"  [{i}/{len(SEARCH_QUERIES)}]  Searching: '{kw}' in {loc}...")
            url  = build_linkedin_search_url(kw, loc, easy_apply=True)
            jobs = scrape_search_page(page, url, max_per_query=args.limit)
            print(f"    → {len(jobs)} Easy Apply jobs found")
            all_jobs.extend(jobs)
            time.sleep(2)  # polite pause between searches

        browser.close()

    # Deduplicate by URL
    seen = set()
    unique = []
    for j in all_jobs:
        key = j["job_apply_link"].split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(j)

    print()
    print(f"  ── Total unique Easy Apply jobs found: {len(unique)} ──")

    if args.dry_run:
        for j in unique:
            print(f"    {j['employer_name']:<25}  {j['job_title']}")
        print("\n  Dry run — not saved. Remove --dry-run to save.\n")
        return

    if not unique:
        print("  ⚠️  No Easy Apply jobs found.")
        print("       LinkedIn may need fresh login or rate limited.")
        return

    # Save to CSV
    df = pd.DataFrame(unique)
    df.to_csv(str(OUTPUT_CSV), index=False)
    print(f"\n  ✅  Saved {len(unique)} jobs → {OUTPUT_CSV}")
    print(f"  Next step: Run master_run.py --skip-fetch to score & build resumes\n")


if __name__ == "__main__":
    main()
