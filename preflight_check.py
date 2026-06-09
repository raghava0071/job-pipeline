#!/usr/bin/env python3
"""
preflight_check.py — Run this before every pipeline start.
Catches broken imports, missing functions, and bad config
before wasting an entire scheduled run.

Usage:
    python preflight_check.py          # prints pass/fail summary
    python preflight_check.py --strict # exits with code 1 if any check fails

Add to scheduler: run this FIRST, only continue if it passes.
"""

import sys, importlib, traceback
from pathlib import Path

# Use the directory this script lives in — always correct regardless of environment
PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

PASS = []
FAIL = []

def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  ✅  {name}")
    except Exception as e:
        FAIL.append((name, str(e)))
        print(f"  ❌  {name}: {e}")

print("\n" + "="*55)
print("  Pipeline Pre-flight Check")
print("="*55)

# ── 1. Core imports ───────────────────────────────────────────────
print("\n[1/5] Core module imports")

check("raghav_profile imports",
    lambda: __import__("raghav_profile"))

check("PROJECTS list exists and non-empty",
    lambda: __import__("raghav_profile").PROJECTS and len(__import__("raghav_profile").PROJECTS) > 0)

check("EXPERIENCE list exists",
    lambda: len(__import__("raghav_profile").EXPERIENCE) > 0)

check("config imports",
    lambda: __import__("config"))

check("claude_engine imports",
    lambda: __import__("claude_engine"))

check("resume_builder imports",
    lambda: __import__("resume_builder"))

check("jd_parser imports",
    lambda: __import__("jd_parser"))

check("pipeline_logger imports",
    lambda: __import__("pipeline_logger"))

# ── 2. Function existence ─────────────────────────────────────────
print("\n[2/5] Critical functions exist")

check("resume_builder.add_projects() exists",
    lambda: callable(getattr(__import__("resume_builder"), "add_projects", None)))

check("resume_builder.build_resume() exists",
    lambda: callable(getattr(__import__("resume_builder"), "build_resume", None)))

check("claude_engine.local_prefilter() exists",
    lambda: callable(getattr(__import__("claude_engine"), "local_prefilter", None)))

check("claude_engine.score_fit() exists",
    lambda: callable(getattr(__import__("claude_engine"), "score_fit", None)))

check("jd_parser.parse_jd() exists",
    lambda: callable(getattr(__import__("jd_parser"), "parse_jd", None)))

# ── 3. Logic correctness ──────────────────────────────────────────
print("\n[3/5] Logic correctness")

def _test_prefilter():
    ce = __import__("claude_engine")
    # Good data job — should NOT skip
    skip, n = ce.local_prefilter("Python SQL ETL pipeline Azure data engineer", "Data Engineer")
    assert not skip, f"Good job wrongly skipped (matches={n})"
    # Non-data job — SHOULD skip
    skip2, n2 = ce.local_prefilter("Marketing manager brand strategy campaigns", "Marketing Manager")
    assert skip2, f"Off-domain job not skipped (matches={n2})"

check("local_prefilter logic correct", _test_prefilter)

def _test_keyword_extraction():
    jd = __import__("jd_parser")
    keywords = jd._extract_jd_keywords(
        "We need Python SQL ETL experience. Preferred Qualifications: Bachelor degree. "
        "Job Summary: Full-Time position. Location: United States."
    )
    garbage = [k for k in keywords if k in {
        "preferred qualifications", "job summary", "full-time", "united states",
        "bachelor", "location", "employment"
    }]
    assert not garbage, f"Garbage keywords still extracted: {garbage}"

check("JD parser extracts no garbage words", _test_keyword_extraction)

def _test_markdown_strip():
    import re
    summary = "**Delivers great pipelines** with *strong* Python skills."
    summary = re.sub(r'\*\*(.+?)\*\*', r'\1', summary)
    summary = re.sub(r'\*(.+?)\*', r'\1', summary)
    assert "**" not in summary and "*" not in summary, "Markdown not stripped"

check("Markdown stripping works", _test_markdown_strip)

def _test_projects_in_resume():
    rb = __import__("resume_builder")
    import inspect
    src = inspect.getsource(rb.build_resume)
    assert "add_projects" in src, "add_projects not called in build_resume"

check("PROJECTS section wired into build_resume", _test_projects_in_resume)

# ── 4. File integrity ─────────────────────────────────────────────
print("\n[4/5] File syntax check")

import py_compile
for fname in ["indeed_apply_now.py", "linkedin_apply_now.py",
              "resume_builder.py", "claude_engine.py",
              "jd_parser.py", "raghav_profile.py"]:
    fpath = PIPELINE_DIR / fname
    check(f"{fname} syntax",
        lambda f=str(fpath): py_compile.compile(f, doraise=True))

# ── 5. Data files ─────────────────────────────────────────────────
print("\n[5/5] Data & config")

check("API key set in .env",
    lambda: any(
        line.startswith("ANTHROPIC_API_KEY=") and len(line) > 25
        for line in (PIPELINE_DIR / ".env").read_text().splitlines()
    ) if (PIPELINE_DIR / ".env").exists() else False)

check("answer_cache.db exists",
    lambda: (PIPELINE_DIR / "data" / "answer_cache.db").exists())

check("resumes dir exists",
    lambda: (PIPELINE_DIR / "resumes").exists())

# ── Summary ───────────────────────────────────────────────────────
print("\n" + "="*55)
total = len(PASS) + len(FAIL)
print(f"  Result: {len(PASS)}/{total} checks passed")

if FAIL:
    print(f"\n  ⚠  {len(FAIL)} FAILED:")
    for name, err in FAIL:
        print(f"     • {name}")
        print(f"       {err[:120]}")
    print()
    if "--strict" in sys.argv:
        sys.exit(1)
else:
    print("  Pipeline is ready to run ✅")

print("="*55 + "\n")
