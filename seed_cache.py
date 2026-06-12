#!/usr/bin/env python3
"""
seed_cache.py — Pre-populate the SQLite answer cache from qa_answers.py.

Run this ONCE before starting the application bot to ensure every
standard Workday/LinkedIn/Indeed question is answered instantly from
the cache — no Claude API call needed for common fields.

Usage:
    python seed_cache.py          # seed and show stats
    python seed_cache.py --clear  # clear then re-seed
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / "job_pipeline"))

import answer_cache as cache
import qa_answers   as qa

def seed():
    seeded = 0
    skipped = 0
    for question, answer in qa.QA.items():
        if not question or not answer:
            skipped += 1
            continue
        # Only seed non-empty answers
        if str(answer).strip():
            cache.save(question, str(answer))
            seeded += 1
        else:
            skipped += 1

    print(f"✅ Seeded {seeded} answers into cache  ({skipped} empty/skipped)")
    cache.print_stats()

if __name__ == "__main__":
    if "--clear" in sys.argv:
        cache.clear()
        print("🗑  Cache cleared — re-seeding...")

    seed()
    print("\nTop cached entries:")
    conn = cache._get_conn()
    rows = conn.execute(
        "SELECT question_key, answer FROM answer_cache ORDER BY rowid DESC LIMIT 20"
    ).fetchall()
    for q, a in rows:
        print(f"  {q[:55]:55} → {str(a)[:35]}")
