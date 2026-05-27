# =============================================================================
# PIPELINE.PY — Job Fetcher (JSearch / RapidAPI)
# Fetches job listings for Raghavendra's target roles and saves to CSV
# =============================================================================

import json
import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime

# ── API KEY — paste your RapidAPI key between the quotes below ────────────────
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY") or "REPLACE_WITH_YOUR_KEY"

SEARCH_QUERIES = [
    "Data Engineer",
    "Azure Data Engineer",
    "Cloud Data Engineer",
    "Data Analyst",
    "Analytics Engineer",
]

PAGES_PER_QUERY = 2          # 10 results per page → 20 per role
COUNTRY        = "us"
DATE_POSTED    = "month"     # today / 3days / week / month
OUTPUT_DIR     = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_FILE    = os.path.join(OUTPUT_DIR, "raw_jobs.csv")

KEEP_COLUMNS = [
    "job_id", "job_title", "employer_name", "employer_logo",
    "job_employment_type", "job_city", "job_state", "job_country",
    "job_is_remote", "job_posted_at_datetime_utc",
    "job_description", "job_apply_link",
    "job_min_salary", "job_max_salary", "job_salary_currency",
    "job_required_skills", "job_highlights",
]

# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_jobs(query: str, pages: int = PAGES_PER_QUERY) -> list[dict]:
    """Fetch jobs from JSearch API for a given query using requests library."""
    if not RAPIDAPI_KEY or RAPIDAPI_KEY == "REPLACE_WITH_YOUR_KEY":
        print("❌ ERROR: Set your RAPIDAPI_KEY in pipeline.py or as: export RAPIDAPI_KEY='your_key'")
        sys.exit(1)

    headers = {
        "X-RapidAPI-Key":  RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    base_url = "https://jsearch.p.rapidapi.com/search"

    all_jobs = []
    for page in range(1, pages + 1):
        params = {
            "query":      query,
            "page":       str(page),
            "num_pages":  "1",
            "country":    COUNTRY,
            "date_posted": DATE_POSTED,
        }
        try:
            resp = requests.get(base_url, headers=headers, params=params, timeout=15)

            if resp.status_code == 401:
                print(f"  ❌ HTTP 401 — API key invalid or not set correctly.")
                sys.exit(1)
            elif resp.status_code == 429:
                print(f"  ⚠ HTTP 429 rate-limit on '{query}' p{page} — waiting 10s...")
                time.sleep(10)
                resp = requests.get(base_url, headers=headers, params=params, timeout=15)
            elif resp.status_code != 200:
                print(f"  ⚠ HTTP {resp.status_code} for '{query}' page {page} — skipping")
                continue

            data = resp.json()
            jobs = data.get("data", [])
            print(f"  ✓ '{query}' page {page}: {len(jobs)} jobs fetched")
            all_jobs.extend(jobs)
            time.sleep(0.8)   # polite delay between requests

        except requests.exceptions.Timeout:
            print(f"  ⚠ Timeout on '{query}' page {page} — skipping")
            continue
        except Exception as e:
            print(f"  ⚠ Error fetching '{query}' page {page}: {e}")
            continue

    return all_jobs


def clean_dataframe(records: list[dict]) -> pd.DataFrame:
    """Keep only useful columns, deduplicate by job_id."""
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Keep only columns that exist
    cols = [c for c in KEEP_COLUMNS if c in df.columns]
    df   = df[cols].copy()

    # Deduplicate
    if "job_id" in df.columns:
        before = len(df)
        df.drop_duplicates(subset="job_id", inplace=True)
        print(f"  ✓ Deduplicated: {before} → {len(df)} unique jobs")

    # Parse highlights dict → plain text
    if "job_highlights" in df.columns:
        df["job_highlights"] = df["job_highlights"].apply(
            lambda h: json.dumps(h) if isinstance(h, dict) else str(h or "")
        )

    # Parse required_skills list → comma string
    if "job_required_skills" in df.columns:
        df["job_required_skills"] = df["job_required_skills"].apply(
            lambda s: ", ".join(s) if isinstance(s, list) else str(s or "")
        )

    df["fetched_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return df


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "═" * 60)
    print("  JOB PIPELINE — Step 1: Fetching Jobs")
    print("═" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_records = []
    for query in SEARCH_QUERIES:
        print(f"\n🔍 Searching: {query}")
        jobs = fetch_jobs(query)
        all_records.extend(jobs)

    print(f"\n📦 Total raw records: {len(all_records)}")
    df = clean_dataframe(all_records)

    if df.empty:
        print("❌ No jobs fetched. Check your API key and connection.")
        sys.exit(1)

    df.to_csv(OUTPUT_FILE, index=False)
    print(f"\n✅ Saved {len(df)} jobs → {OUTPUT_FILE}")
    print("═" * 60 + "\n")
    return df


if __name__ == "__main__":
    main()
