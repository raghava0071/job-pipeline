#!/usr/bin/env python3
"""
salary_helper.py — Smart salary answer for job application forms.

STRATEGY:
  Goal: Pass initial screening — don't get rejected for asking too much.
  Personal range: $60,000 – $90,000+ depending on role and location.

  1. Posted salary range in JD → answer at the LOWER THIRD of that range
     (safe, within budget, won't trigger rejection — negotiate up at offer stage)
  2. No range posted → use profile role defaults (conservative mid-level targets)
  3. Always clamp to personal min/max ($60k–$90k)

USED BY: workday_apply_now.py, linkedin_apply_now.py, indeed_apply_now.py

Usage:
    from salary_helper import pick_salary
    salary = pick_salary(jd_text, job_title)   # returns e.g. "75000"
"""

import re
from pathlib import Path
import sys
sys.path.insert(0, str(Path.home() / "job_pipeline"))

# ── Personal bounds (from raghav_profile.py) ──────────────────────────────────
try:
    from raghav_profile import SALARY as _SALARY_PROFILE
    _MIN_SALARY = _SALARY_PROFILE.get("min", 60000)
    _MAX_SALARY = _SALARY_PROFILE.get("max", 90000)
except Exception:
    _MIN_SALARY = 60000
    _MAX_SALARY = 90000

# ── Role-based defaults when no JD range is posted ────────────────────────────
# Conservative targets — get through screening, negotiate at offer stage

_ROLE_DEFAULTS = {
    "data engineer":         78000,
    "analytics engineer":    76000,
    "azure data engineer":   78000,
    "cloud data engineer":   78000,
    "data scientist":        80000,
    "machine learning":      80000,
    "ml engineer":           80000,
    "data analyst":          68000,
    "bi analyst":            67000,
    "business analyst":      67000,
    "business intelligence": 67000,
    "reporting analyst":     65000,
    "etl developer":         75000,
    "etl engineer":          75000,
    "pipeline engineer":     76000,
}

_FALLBACK = 72000   # generic fallback when role not matched


# ── Salary range extraction ────────────────────────────────────────────────────

def extract_range(jd_text: str) -> tuple[int, int] | None:
    """
    Parse the posted salary range from a job description.
    Returns (low, high) as integers, or None if no range found.

    Handles formats like:
      $75,000 - $90,000    $75k - $90k    75000-90000/yr
      $80,000+ per year    USD 70,000 – 85,000    70K–85K
    """
    if not jd_text:
        return None

    text = jd_text.replace("–", "-").replace("—", "-")  # normalize dashes

    # Pattern 1: $75,000 - $90,000  or  $75k - $90k
    m = re.search(
        r'\$\s*([\d,]+)\s*[kK]?\s*[-–to]+\s*\$\s*([\d,]+)\s*[kK]?'
        r'(?:\s*(?:a year|per year|annually|/yr|/year|USD|usd))?',
        text, re.IGNORECASE
    )
    if m:
        lo = _parse_num(m.group(1))
        hi = _parse_num(m.group(2))
        if lo and hi and lo < hi:
            return (lo, hi)

    # Pattern 2: 75,000 - 90,000 per year (no $ sign)
    m = re.search(
        r'([\d,]{5,})\s*[-–to]+\s*([\d,]{5,})'
        r'\s*(?:per year|a year|annually|/yr|USD)',
        text, re.IGNORECASE
    )
    if m:
        lo = _parse_num(m.group(1))
        hi = _parse_num(m.group(2))
        if lo and hi and lo < hi:
            return (lo, hi)

    # Pattern 3: $80,000+ (single value with +)
    m = re.search(r'\$\s*([\d,]+)\s*[kK]?\s*\+', text)
    if m:
        base = _parse_num(m.group(1))
        if base:
            return (base, int(base * 1.15))

    # Pattern 4: Up to $90,000
    m = re.search(r'up\s+to\s+\$\s*([\d,]+)\s*[kK]?', text, re.IGNORECASE)
    if m:
        hi = _parse_num(m.group(1))
        if hi:
            return (int(hi * 0.88), hi)

    return None


def _parse_num(s: str) -> int | None:
    """Parse '75,000' or '75k' or '75' into an integer."""
    s = s.replace(",", "").strip()
    try:
        n = int(float(s))
        # If it looks like thousands shorthand (e.g. 75 meaning 75k)
        if n < 500:
            n *= 1000
        return n
    except Exception:
        return None


# ── Job level detection ────────────────────────────────────────────────────────

def detect_level(job_title: str) -> float:
    """Return a salary multiplier based on the job title's seniority level."""
    t = job_title.lower()
    for keyword, mult in _LEVEL_MULTIPLIERS.items():
        if keyword in t:
            return mult
    return 1.0   # mid-level default


def detect_remote(job_title: str, jd_text: str = "") -> bool:
    """True if the role appears to be remote."""
    combined = (job_title + " " + (jd_text or "")).lower()
    return "remote" in combined


# ── Main function ──────────────────────────────────────────────────────────────

def pick_salary(jd_text: str, job_title: str, as_string: bool = True) -> str | int:
    """
    Return the best salary number to enter in a form field.

    Strategy (avoid screening rejection):
    - If JD has a posted range: answer at the LOWER THIRD of that range
      → safely within budget, won't trigger rejection, room to negotiate up
    - If no range: use conservative role-based default from profile
    - Always clamp to personal range ($62,000–$95,000)

    Returns a plain integer string e.g. "75000"
    """
    salary = _from_jd(jd_text) or _from_title(job_title)

    # Clamp to personal min/max
    salary = max(_MIN_SALARY, min(_MAX_SALARY, salary))

    # Round to nearest 1000
    salary = int(round(salary / 1000) * 1000)

    return str(salary) if as_string else salary


def _from_jd(jd_text: str) -> int | None:
    """
    Pick salary from posted JD range.
    Answer at the LOWER THIRD — comfortably within the company's budget,
    which avoids rejection at the screening stage.
    Example: $75k–$95k → answer $82k (not $93k which risks rejection).
    """
    rng = extract_range(jd_text)
    if not rng:
        return None
    lo, hi = rng
    # Lower third of the range = lo + 33% of the spread
    target = int(lo + (hi - lo) * 0.33)
    # But never go below our personal minimum
    target = max(target, _MIN_SALARY)
    print(f"          💰 Posted range: ${lo:,}–${hi:,} → answering ${target:,} "
          f"(lower third — safe for screening)")
    return target


def _from_title(job_title: str) -> int:
    """Pick conservative salary from profile defaults based on job title."""
    title_l = (job_title or "").lower()
    base = _FALLBACK

    for kw, amount in _ROLE_DEFAULTS.items():
        if kw in title_l:
            base = amount
            break

    print(f"          💰 No posted range — using role default for '{job_title}': ${base:,}")
    return base


# ── Salary rule string for Claude prompts ─────────────────────────────────────

def salary_rule_for_prompt(jd_text: str, job_title: str) -> str:
    """
    Returns a salary instruction line to insert into a Claude prompt.
    e.g. "- salary: job posts $75,000–$90,000 → answer 85000 (plain number only)"
    """
    rng = extract_range(jd_text)
    target = pick_salary(jd_text, job_title)

    if rng:
        lo, hi = rng
        return (
            f"- salary / compensation: the job posts ${lo:,}–${hi:,}. "
            f"Answer {target} (plain number, no $ or commas). "
            f"Never exceed the posted maximum."
        )
    else:
        return (
            f"- salary / compensation: no range posted. "
            f"Based on role '{job_title}', answer {target} "
            f"(plain number, no $ or commas)."
        )


# ── CLI — test it ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("Data Engineer",   "$75,000 - $92,000 per year"),
        ("Senior Data Analyst", "Salary: $65k–$80k annually"),
        ("Data Scientist",  "Compensation up to $110,000"),
        ("Junior BI Analyst", ""),
        ("Analytics Engineer", "Remote role. No salary listed."),
        ("Data Engineer III", "$90,000 - $115,000 USD"),
    ]
    print(f"{'Job Title':<30} {'JD snippet':<35} → Salary")
    print("-" * 80)
    for title, jd in tests:
        s = pick_salary(jd, title)
        rng = extract_range(jd)
        rng_str = f"${rng[0]:,}–${rng[1]:,}" if rng else "no range"
        print(f"  {title:<28} [{rng_str}] → ${int(s):,}")
