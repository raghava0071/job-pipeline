#!/opt/anaconda3/bin/python3
# =============================================================================
# LINKEDIN_SCRAPER.PY  v4  — Broad Data Science scope + debug mode
#
# STRATEGY:
#   1. Go to search page with Easy Apply filter (f_LF=f_AL)
#   2. Click each job card → right panel loads title/company/description
#   3. Extract via JS page.evaluate() — CSS-class independent
#   4. Prints EVERY title found (before filtering) — debug mode
#
# USAGE:
#   python linkedin_scraper.py             # Full run
#   python linkedin_scraper.py --limit 10  # Max 10 jobs per query
# =============================================================================

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    sys.exit("pip install pandas --break-system-packages")

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("pip install playwright && python -m playwright install chromium")

PIPELINE_DIR = Path.home() / "job_pipeline"
DATA_DIR     = PIPELINE_DIR / "data"
OUTPUT_CSV   = DATA_DIR / "linkedin_jobs.csv"
SESSION_DIR  = Path.home() / ".linkedin_session"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Full data science field — not just exact titles
SEARCH_QUERIES = [
    "Data Engineer Entry Level",
    "Junior Data Engineer",
    "Data Analyst Entry Level",
    "Data Scientist Entry Level",
    "Junior Data Scientist",
    "Machine Learning Engineer Entry Level",
    "Analytics Engineer Entry Level",
    "Business Intelligence Analyst",
    "BI Developer Entry Level",
    "ETL Developer Entry Level",
    "Cloud Data Engineer Entry Level",
    "Data Engineer Azure",
    "Data Engineer AWS",
    "Data Pipeline Engineer",
    "Data Warehouse Engineer Entry Level",
    "SQL Developer Data",
    "Python Data Engineer",
    "Spark Engineer Entry Level",
    "AI Engineer Entry Level",
    "Junior ML Engineer",
]

REJECT_TITLE_KEYWORDS = [
    "senior", "sr.", " sr ", "lead", "principal", "staff", "director",
    "manager", "vp ", "vice president", "head of", "chief", "architect",
    "distinguished", "fellow", "executive",
]

def is_good_level(title: str) -> bool:
    t = title.lower()
    return not any(bad in t for bad in REJECT_TITLE_KEYWORDS)

def build_search_url(keywords: str, location: str = "United States") -> str:
    import urllib.parse
    params = {
        "keywords": keywords,
        "location": location,
        "sortBy":   "DD",
        "f_TPR":    "r604800",
        "f_LF":     "f_AL",
    }
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)

def ensure_login(page):
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)
        if "feed" in page.url and page.locator("nav").count() > 0:
            print("  ✅  LinkedIn: already logged in")
            return
    except Exception:
        pass
    print("\n  🔐  LinkedIn login required. Log in then press ENTER...")
    input("  → ")
    print("  ✅  Logged in.\n")


def extract_details(page) -> dict:
    """Extract title/company/location/description from current page via JS."""
    return page.evaluate("""
        () => {
            let title = '';
            for (const sel of [
                '.job-details-jobs-unified-top-card__job-title h1',
                '.jobs-unified-top-card__job-title h1',
                'h1.t-24', 'h2.t-24', 'h1'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 2) {
                    title = el.innerText.trim(); break;
                }
            }
            if (!title) {
                const el = document.querySelector('[class*="job-title"]');
                if (el) title = el.innerText.trim();
            }

            let company = '';
            for (const sel of [
                '.job-details-jobs-unified-top-card__company-name a',
                '.jobs-unified-top-card__company-name a',
                '.jobs-unified-top-card__subtitle a',
                '.topcard__org-name-link',
                '[class*="company-name"]'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim().length > 1) {
                    company = el.innerText.trim(); break;
                }
            }

            let location = '';
            for (const sel of [
                '.jobs-unified-top-card__bullet',
                '.job-details-jobs-unified-top-card__primary-description-without-tagline .tvm__text',
                '.topcard__flavor--bullet'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.trim()) {
                    location = el.innerText.trim(); break;
                }
            }

            let description = '';
            for (const sel of [
                '#job-details',
                '.jobs-description__content',
                '.jobs-description-content__text',
                '.description__text',
                '[class*="job-description"]'
            ]) {
                const el = document.querySelector(sel);
                if (el && el.innerText.length > 50) {
                    description = el.innerText.substring(0, 3500); break;
                }
            }

            return { title, company, location, description };
        }
    """) or {"title": "", "company": "", "location": "", "description": ""}


def scrape_search_page(page, search_url: str, max_jobs: int = 20) -> list[dict]:
    jobs = []
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # Scroll to load all cards
        for _ in range(6):
            page.keyboard.press("End")
            time.sleep(1.5)
        page.keyboard.press("Home")
        time.sleep(2)

        job_links = page.evaluate("""
            () => {
                const seen = new Set();
                const result = [];
                for (const a of document.querySelectorAll('a[href*="/jobs/view/"]')) {
                    const url = a.href.split('?')[0].replace(/\\/$/, '');
                    if (url && !seen.has(url)) {
                        seen.add(url);
                        result.push({ url });
                    }
                }
                return result;
            }
        """) or []

        print(f"    Found {len(job_links)} job links on page")

        if not job_links:
            pg_title = page.title()
            print(f"    ⚠️  Page title: '{pg_title}' — may be rate-limited or login wall")
            return jobs

        seen_urls = set()
        for idx, link_info in enumerate(job_links[:max_jobs]):
            job_url = link_info.get("url", "")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)

            try:
                # Click the card link to load right panel (no URL navigation)
                job_id = job_url.split("/jobs/view/")[1].split("?")[0] if "/jobs/view/" in job_url else ""
                clicked_card = False
                if job_id:
                    card = page.locator(f'a[href*="{job_id}"]').first
                    if card.count() > 0:
                        card.click()
                        time.sleep(3.5)
                        clicked_card = True

                if not clicked_card:
                    page.goto(job_url, wait_until="domcontentloaded", timeout=25000)
                    time.sleep(4)

                details     = extract_details(page)
                title       = details["title"].strip()
                company     = details["company"].strip()
                location    = details["location"].strip()
                description = details["description"].strip()

                # ── Debug: show what we got ──────────────────────────────
                print(f"      [{idx+1}] '{title[:45]}' | '{company[:25]}'", end="")

                if not title:
                    print("  → SKIP: no title (right panel may not have loaded)")
                    continue
                if not is_good_level(title):
                    print(f"  → SKIP: senior/lead role")
                    continue
                if not company:
                    print(f"  → SKIP: no company")
                    continue

                print("  → ✅ ADDED")

                city  = location.split(",")[0].strip() if "," in location else location
                state = location.split(",")[1].strip() if location.count(",") >= 1 else ""

                jobs.append({
                    "job_title":       title,
                    "employer_name":   company,
                    "job_city":        city,
                    "job_state":       state,
                    "job_apply_link":  job_url,
                    "job_description": description,
                    "is_easy_apply":   True,
                    "platform":        "LinkedIn",
                    "fetched_at":      datetime.now().isoformat(),
                    "fit_score":       0,
                    "fit_grade":       "",
                    "fit_apply":       False,
                })

            except PWTimeout:
                print(f"  → SKIP: timeout")
            except Exception as e:
                print(f"  → SKIP: {str(e)[:50]}")

    except PWTimeout:
        print(f"    ⚠️  Timed out loading search page")
    except Exception as e:
        print(f"    ⚠️  Error: {e}")

    return jobs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  LINKEDIN EASY APPLY SCRAPER  v4 — Full Data Science Scope   ║")
    print("║  Entry & Mid Level  •  Easy Apply ONLY  •  Debug Mode ON     ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Searches  : {len(SEARCH_QUERIES)}")
    print(f"  Max/query : {args.limit}")
    print()

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    all_jobs, seen_urls = [], set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        ensure_login(page)

        for i, kw in enumerate(SEARCH_QUERIES, 1):
            print(f"\n  [{i}/{len(SEARCH_QUERIES)}]  '{kw}'...")
            jobs = scrape_search_page(page, build_search_url(kw), max_jobs=args.limit)
            for job in jobs:
                key = job["job_apply_link"].split("?")[0].rstrip("/")
                if key not in seen_urls:
                    seen_urls.add(key)
                    all_jobs.append(job)
            print(f"    → {len(jobs)} jobs collected this query")
            time.sleep(3)

        browser.close()

    seen, unique = set(), []
    for j in all_jobs:
        key = j["job_apply_link"].split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(j)

    print(f"\n  ── Total unique Easy Apply jobs: {len(unique)} ──\n")

    if args.dry_run:
        for j in unique:
            print(f"    {j['employer_name']:<25}  {j['job_title']}")
        return

    if not unique:
        print("  ⚠️  0 jobs found. Check the debug output above to see WHY each was skipped.")
        print("  Common causes:")
        print("    • 'no title' → LinkedIn right panel not loading (rate limit)")
        print("    • 'senior/lead' → all 7 results are senior roles for that query")
        print("    • Try running again after 5 minutes, or log out/in on LinkedIn\n")
        return

    pd.DataFrame(unique).to_csv(str(OUTPUT_CSV), index=False)
    print(f"  ✅  Saved {len(unique)} jobs → {OUTPUT_CSV}\n")


if __name__ == "__main__":
    main()
