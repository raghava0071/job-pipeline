#!/opt/anaconda3/bin/python3
"""
check_csv.py — Quick diagnostic: shows columns + sample row from raw_jobs.csv
Run: /opt/anaconda3/bin/python3 ~/job_pipeline/check_csv.py
"""
from pathlib import Path
import sys

try:
    import pandas as pd
except ImportError:
    sys.exit("Run: /opt/anaconda3/bin/pip install pandas")

CSV = Path(__file__).parent / "data" / "raw_jobs.csv"
if not CSV.exists():
    sys.exit(f"❌  {CSV} not found — run the pipeline first to generate it.")

df = pd.read_csv(CSV)

print("\n" + "═"*60)
print("  RAW_JOBS.CSV DIAGNOSTIC")
print("═"*60)
print(f"  Rows    : {len(df)}")
print(f"  Columns : {len(df.columns)}")
print()
print("  Column names:")
for i, c in enumerate(df.columns, 1):
    sample = str(df.iloc[0][c])[:60].replace('\n', ' ')
    print(f"    {i:>2}. {c:<35} → {sample}")

# Specifically check for description column
print()
desc_candidates = [c for c in df.columns if any(k in c.lower()
                   for k in ["description", "desc", "detail", "highlight", "skill", "require"])]
print(f"  Description-like columns: {desc_candidates}")

if desc_candidates:
    col = desc_candidates[0]
    sample_text = str(df.iloc[0][col])
    non_empty = df[col].notna().sum()
    print(f"\n  '{col}' → {non_empty}/{len(df)} rows non-empty")
    print(f"  Sample (first 300 chars):")
    print(f"    {sample_text[:300]}")
print()
print("═"*60 + "\n")
