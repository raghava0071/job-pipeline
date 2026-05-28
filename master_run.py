#!/opt/anaconda3/bin/python3
# =============================================================================
# MASTER_RUN.PY — Job Pipeline Orchestrator  (v7 — fixed for LinkedIn/Indeed)
# Powered by Claude claude-sonnet-4-6
#
# DATA SOURCES (priority order):
#   1. linkedin_jobs.csv  — from linkedin_scraper.py  (Easy Apply only)
#   2. indeed_jobs.csv    — from indeed_scraper.py     (In-Portal only)
#   3. raw_jobs.csv       — from pipeline.py / JSearch API (fallback)
#
# USAGE:
#   python master_run.py                  # Full run: fetch → score → resume → track
#   python master_run.py --skip-fetch     # Score existing LinkedIn/Indeed CSVs
#   python master_run.py --limit 20       # Process first 20 jobs only
# =============================================================================

import sys
import time
import copy
import argparse
from pathlib import Path
from datetime import datetime

# ── Install Rich if needed ────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    from rich import box
    from rich.rule import Rule
    HAS_RICH = True
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "rich", "--quiet",
                   "--break-system-packages"], capture_output=True)
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
        from rich import box
        from rich.rule import Rule
        HAS_RICH = True
    except ImportError:
        HAS_RICH = False

try:
    import pandas as pd
except ImportError:
    sys.exit("Run: pip install pandas")

PIPELINE_DIR = Path(__file__).parent
DATA_DIR     = PIPELINE_DIR / "data"
sys.path.insert(0, str(PIPELINE_DIR))

console = Console() if HAS_RICH else None

# ── CSV paths ─────────────────────────────────────────────────────────────────
LINKEDIN_CSV = DATA_DIR / "linkedin_jobs.csv"
INDEED_CSV   = DATA_DIR / "indeed_jobs.csv"
RAW_CSV      = DATA_DIR / "raw_jobs.csv"
FILTERED_CSV = DATA_DIR / "filtered_jobs.csv"


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _find_desc_col(df):
    for exact in ["job_description", "description", "jobDescription",
                  "job_details", "full_description"]:
        if exact in df.columns:
            return exact, []
    for c in df.columns:
        if any(k in c.lower() for k in ["description", "desc", "requirement", "detail"]):
            return c, []
    fallbacks = [c for c in df.columns
                 if any(k in c.lower() for k in ["highlight", "skill", "qualif"])]
    return None, fallbacks

def _get_jd_text(row, desc_col, fallback_cols):
    if desc_col:
        val = str(row.get(desc_col, "") or "").strip()
        if val and val.lower() not in ("nan", "none", ""):
            return val
    parts = []
    for c in fallback_cols:
        v = str(row.get(c, "") or "").strip()
        if v and v.lower() not in ("nan", "none", ""):
            parts.append(v)
    return " | ".join(parts)

def _get(row, *keys, default=""):
    for k in keys:
        try:
            v = row.get(k)
            if v is not None and str(v).strip() not in ("", "nan", "None"):
                return str(v).strip()
        except Exception:
            continue
    return default

def log(msg, style=""):
    if console:
        console.print(msg, style=style)
    else:
        print(msg)

def step(emoji, title):
    if HAS_RICH:
        console.print(f"\n[bold white]{emoji}  {title}[/bold white]")
        console.print(Rule(style="dim"))
    else:
        print(f"\n{'─'*60}\n{emoji}  {title}")

def ok(msg):   log(f"  [green]✓[/green]  {msg}" if HAS_RICH else f"  ✓  {msg}")
def warn(msg): log(f"  [yellow]⚠[/yellow]  {msg}" if HAS_RICH else f"  ⚠  {msg}")
def info(msg): log(f"  [dim]{msg}[/dim]"           if HAS_RICH else f"     {msg}")


# ── HEADER ────────────────────────────────────────────────────────────────────
def print_header():
    if not HAS_RICH:
        print("\n" + "="*60)
        print("  JOB PIPELINE — Raghavendra Karanam")
        print("="*60 + "\n")
        return
    console.print()
    console.print(Panel.fit(
        "[bold cyan]🚀  JOB PIPELINE — Raghavendra Karanam[/bold cyan]\n"
        "[dim]Data Analyst  •  Data Engineer  •  Data Scientist[/dim]\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M')}[/dim]",
        border_style="cyan", padding=(1, 4),
    ))
    console.print()


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — LOAD JOBS
# Priority: linkedin_jobs.csv + indeed_jobs.csv → merged
#           fallback: raw_jobs.csv (JSearch API)
# ═════════════════════════════════════════════════════════════════════════════
def step_fetch(args):
    step("📡", "Loading Jobs")

    # ── Portal CSVs always take priority ─────────────────────────────────────
    frames = []
    if LINKEDIN_CSV.exists():
        try:
            df_li = pd.read_csv(LINKEDIN_CSV)
            if not df_li.empty:
                df_li["_source"] = "linkedin"
                frames.append(df_li)
                ok(f"LinkedIn  : {len(df_li)} Easy Apply jobs ← {LINKEDIN_CSV.name}")
        except Exception as e:
            warn(f"Could not read linkedin_jobs.csv: {e}")

    if INDEED_CSV.exists():
        try:
            df_in = pd.read_csv(INDEED_CSV)
            if not df_in.empty:
                df_in["_source"] = "indeed"
                frames.append(df_in)
                ok(f"Indeed    : {len(df_in)} In-Portal jobs  ← {INDEED_CSV.name}")
        except Exception as e:
            warn(f"Could not read indeed_jobs.csv: {e}")

    if frames:
        df = pd.concat(frames, ignore_index=True)
        # Deduplicate by URL
        url_col = next((c for c in df.columns if any(k in c.lower()
                       for k in ["apply_link", "job_url", "url", "link"])), None)
        if url_col:
            before = len(df)
            df = df.drop_duplicates(subset=[url_col])
            if len(df) < before:
                info(f"Removed {before-len(df)} duplicate URLs")
        info(f"Total: {len(df)} unique portal jobs (LinkedIn + Indeed)")
        return df

    # ── Fallback: raw_jobs.csv from JSearch API ───────────────────────────────
    if args.skip_fetch and RAW_CSV.exists():
        df = pd.read_csv(RAW_CSV)
        warn(f"No LinkedIn/Indeed CSVs found — using cached {len(df)} jobs from raw_jobs.csv")
        warn("Run linkedin_scraper.py and/or indeed_scraper.py first for best results!")
        return df

    if args.skip_fetch:
        warn("No job files found. Run linkedin_scraper.py / indeed_scraper.py first.")
        return pd.DataFrame()

    # ── Live fetch from JSearch API ───────────────────────────────────────────
    try:
        import pipeline as pip_mod
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                      console=console, transient=True) as p:
            t = p.add_task("Fetching via JSearch API...", total=None)
            all_records = []
            for query in pip_mod.SEARCH_QUERIES:
                records = pip_mod.fetch_jobs(query)
                all_records.extend(records)
            df = pip_mod.clean_dataframe(all_records)
            p.update(t, completed=True)
        ok(f"JSearch API: fetched {len(df)} jobs")
        RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(RAW_CSV, index=False)
        return df
    except Exception as e:
        warn(f"JSearch fetch failed: {e}")
        if RAW_CSV.exists():
            df = pd.read_csv(RAW_CSV)
            warn(f"Using cached {len(df)} jobs")
            return df
        return pd.DataFrame()


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — FILTER LEVEL
# ═════════════════════════════════════════════════════════════════════════════
def step_filter(df):
    step("🎯", "Filtering — Entry & Mid Level Data Jobs Only")
    try:
        from claude_engine import is_good_level
    except ImportError:
        warn("claude_engine not found — skipping filter")
        return df

    TARGET = [
        "data analyst", "data engineer", "data scientist", "analytics engineer",
        "business analyst", "bi analyst", "business intelligence", "ml engineer",
        "machine learning", "etl", "reporting analyst", "insights analyst",
        "quantitative analyst", "database", "analytics",
    ]
    title_col = next((c for c in df.columns if "title" in c.lower()), None)
    if not title_col:
        warn("No title column — skipping filter")
        return df

    before = len(df)
    mask_data  = df[title_col].str.lower().apply(lambda t: any(kw in str(t) for kw in TARGET))
    mask_level = df[title_col].apply(lambda t: is_good_level(str(t)))
    result = df[mask_data & mask_level].copy()

    ok(f"Kept {len(result)} / {before} jobs  (entry/mid data roles)")
    return result if not result.empty else df


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — CLAUDE FIT SCORING
# ═════════════════════════════════════════════════════════════════════════════
def step_score(df, limit=0):
    step("🤖", "Claude Scoring — Fit Analysis per Job")

    try:
        import claude_engine as ce
        import raghav_profile as rp
    except ImportError as e:
        warn(f"Import error: {e}")
        df["fit_score"] = 65; df["fit_grade"] = "C"; df["fit_apply"] = True
        return df

    if not ce.CLAUDE_AVAILABLE:
        warn("Claude API unavailable — using default scores (check .env ANTHROPIC_API_KEY)")
        df["fit_score"] = 65; df["fit_grade"] = "C"; df["fit_apply"] = True
        return df

    # Build full profile (ALL fields, not just contact info)
    full_profile = {
        **rp.PROFILE,
        "skills": getattr(rp, "ALL_SKILLS_FLAT", []) or
                  [s for grp in getattr(rp, "SKILLS", {}).values() for s in grp],
        "experience": getattr(rp, "EXPERIENCE", []),
        "education":  getattr(rp, "EDUCATION",  []),
        "summary": (
            "Data professional with hands-on experience in data engineering, "
            "ETL pipelines, SQL, Python, cloud platforms (Azure, AWS, GCP), "
            "Apache Spark, Kafka, and analytics tools."
        ),
    }
    profile_summary = ce.build_profile_summary(full_profile)

    title_col = next((c for c in df.columns if "title" in c.lower()), df.columns[0])
    co_col    = next((c for c in df.columns if any(k in c.lower()
                     for k in ["company", "employer", "organization"])), None)
    desc_col, fallback_cols = _find_desc_col(df)

    info(f"Columns   : {list(df.columns)}")
    info(f"Title col : {title_col}  |  Company col: {co_col}  |  Desc col: {desc_col or f'FALLBACK({fallback_cols})'}")
    info(f"Skills    : {len(full_profile['skills'])}  |  Experience roles: {len(full_profile['experience'])}")

    if desc_col:
        sample = str(df.iloc[0].get(desc_col, "")).strip()
        if not sample or sample.lower() in ("nan", "none", ""):
            warn(f"Column '{desc_col}' appears empty in first row — scores may be low")

    cap = limit if limit > 0 else min(len(df), 60)
    df  = df.head(cap).copy()

    scores, grades, applys, reasons = [], [], [], []

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  BarColumn(bar_width=30), TextColumn("{task.completed}/{task.total}"),
                  TimeElapsedColumn(), console=console) as prog:
        task = prog.add_task("Scoring with Claude...", total=cap)
        for _, row in df.iterrows():
            jd_text = _get_jd_text(row, desc_col, fallback_cols)
            res = ce.score_fit(
                profile_summary,
                jd_text,
                _get(row, title_col, default="Data Role"),
                _get(row, co_col, default="Company") if co_col else "Company",
            )
            scores.append(res.get("score", 65))
            grades.append(res.get("grade", "C"))
            applys.append(res.get("apply", True))
            reasons.append(res.get("reasoning", ""))
            prog.advance(task)
            time.sleep(0.3)

    df["fit_score"]    = scores
    df["fit_grade"]    = grades
    df["fit_apply"]    = applys
    df["fit_reasoning"] = reasons

    good = sum(applys)
    ok(f"Scored {cap} jobs — {good} good fits (≥{ce.FIT_THRESHOLD}%)")

    if HAS_RICH:
        a = sum(1 for s in scores if s >= 85)
        b = sum(1 for s in scores if 70 <= s < 85)
        c = sum(1 for s in scores if 55 <= s < 70)
        d = sum(1 for s in scores if s < 55)
        console.print(
            f"\n  [bold green]A {a}[/bold green]  "
            f"[cyan]B {b}[/cyan]  "
            f"[yellow]C {c}[/yellow]  "
            f"[red]D {d}[/red]"
        )

    # Save scored jobs to filtered_jobs.csv
    FILTERED_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(FILTERED_CSV, index=False)
    info(f"Scores saved → {FILTERED_CSV}")

    # ── CRITICAL: Write scores back to source CSVs so auto_apply can use them ──
    _write_scores_back(df, title_col, co_col, scores, grades, applys, reasons)

    return df


def _write_scores_back(scored_df, title_col, co_col, scores, grades, applys, reasons):
    """
    Write fit_score, fit_grade, fit_apply, fit_reasoning back into
    linkedin_jobs.csv and indeed_jobs.csv so auto_apply.py picks them up
    with correct scores (not the original 0s).
    """
    url_col = next((c for c in scored_df.columns if any(k in c.lower()
                   for k in ["apply_link", "job_url", "url", "link"])), None)
    if not url_col:
        return

    # Build URL → score map
    score_map = {}
    for i, (_, row) in enumerate(scored_df.iterrows()):
        url = str(row.get(url_col, "")).split("?")[0].rstrip("/")
        if url and url.lower() not in ("nan", "none", ""):
            score_map[url] = {
                "fit_score":     scores[i] if i < len(scores) else 65,
                "fit_grade":     grades[i] if i < len(grades) else "C",
                "fit_apply":     applys[i] if i < len(applys) else True,
                "fit_reasoning": reasons[i] if i < len(reasons) else "",
            }

    updated = 0
    for csv_path in [LINKEDIN_CSV, INDEED_CSV]:
        if not csv_path.exists():
            continue
        try:
            df = pd.read_csv(csv_path)
            src_url_col = next((c for c in df.columns if any(k in c.lower()
                               for k in ["apply_link", "job_url", "url", "link"])), None)
            if not src_url_col:
                continue

            changed = 0
            for i, row in df.iterrows():
                url = str(row.get(src_url_col, "")).split("?")[0].rstrip("/")
                if url in score_map:
                    for col, val in score_map[url].items():
                        df.at[i, col] = val
                    changed += 1

            if changed:
                df.to_csv(csv_path, index=False)
                info(f"  Updated {changed} scores in {csv_path.name}")
                updated += changed
        except Exception as e:
            warn(f"Could not update {csv_path.name}: {e}")

    if updated:
        ok(f"Wrote scores back to source CSVs ({updated} jobs updated)")


# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — BUILD RESUMES
# ═════════════════════════════════════════════════════════════════════════════
def step_resumes(df):
    step("📄", "Building Custom Word Resumes")

    apply_df = df[df["fit_apply"]] if "fit_apply" in df.columns else df
    if apply_df.empty:
        warn("No jobs passed the fit gate (fit_apply=True) — no resumes to build")
        warn("Check fit_score column — all may be below the threshold")
        return df

    try:
        import resume_builder as rb
        import jd_parser      as jdp
        import raghav_profile as rp
        import claude_engine  as ce
    except ImportError as e:
        warn(f"Import error: {e}"); return df

    # ── Build full profile (same as step_score) ───────────────────────────────
    full_profile = {
        **rp.PROFILE,
        "skills": getattr(rp, "ALL_SKILLS_FLAT", []) or
                  [s for grp in getattr(rp, "SKILLS", {}).values() for s in grp],
        "experience": getattr(rp, "EXPERIENCE", []),
        "education":  getattr(rp, "EDUCATION",  []),
        "summary": (
            "Data professional with hands-on experience in data engineering, "
            "ETL pipelines, SQL, Python, cloud platforms (Azure, AWS, GCP), "
            "Apache Spark, Kafka, and analytics tools."
        ),
    }
    profile_summary = ce.build_profile_summary(full_profile)
    title_col = next((c for c in df.columns if "title"   in c.lower()), df.columns[0])
    co_col    = next((c for c in df.columns if any(k in c.lower()
                     for k in ["company", "employer", "organization"])), None)
    desc_col, fallback_cols = _find_desc_col(df)

    ok(f"Building resumes for {len(apply_df)} qualifying jobs (≥65% fit score)...")

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  BarColumn(bar_width=30), TextColumn("{task.completed}/{task.total}"),
                  console=console) as prog:
        task = prog.add_task("Building resumes...", total=len(apply_df))

        for idx, row in apply_df.iterrows():
            title   = _get(row, title_col, default="Data Role")
            company = _get(row, co_col, default="Company") if co_col else "Company"
            jd      = _get_jd_text(row, desc_col, fallback_cols)

            try:
                parsed = jdp.parse_jd(jd, title) if jd else {}

                # Tailor resume bullets with Claude (fixed: was 'profile', now 'full_profile')
                if ce.CLAUDE_AVAILABLE and jd:
                    all_bullets = []
                    for exp in full_profile.get("experience", []):
                        all_bullets.extend(exp.get("bullets",
                                           exp.get("responsibilities", [])))
                    tailored = ce.tailor_bullets(all_bullets[:12], jd, title)
                    p_copy = copy.deepcopy(full_profile)   # ← FIXED (was: copy.deepcopy(profile))
                    bi = 0
                    for exp in p_copy.get("experience", []):
                        key = "bullets" if "bullets" in exp else "responsibilities"
                        for i in range(len(exp.get(key, []))):
                            if bi < len(tailored):
                                exp[key][i] = tailored[bi]; bi += 1
                    use_profile = p_copy
                else:
                    use_profile = full_profile

                path = rb.build_resume(use_profile, parsed, title, company)
                df.at[idx, "resume_path"] = path
                info(f"  ✓ {company[:20]} — {title[:25]}  → {Path(path).name}")

            except Exception as e:
                df.at[idx, "resume_path"] = ""
                warn(f"  Resume failed for {company}: {e}")

            prog.advance(task)

    built = int(df["resume_path"].notna().sum()) if "resume_path" in df.columns else 0
    built_real = int((df["resume_path"] != "").sum()) if "resume_path" in df.columns else 0
    ok(f"Built {built_real} custom resumes → ~/job_pipeline/resumes/")

    # Write resume paths back to source CSVs
    _write_resume_paths_back(df)
    return df


def _write_resume_paths_back(scored_df):
    """Write resume_path back into linkedin_jobs.csv and indeed_jobs.csv."""
    url_col = next((c for c in scored_df.columns if any(k in c.lower()
                   for k in ["apply_link", "job_url", "url", "link"])), None)
    if not url_col or "resume_path" not in scored_df.columns:
        return

    path_map = {}
    for _, row in scored_df.iterrows():
        url  = str(row.get(url_col, "")).split("?")[0].rstrip("/")
        path = str(row.get("resume_path", ""))
        if url and path and path.lower() not in ("nan", "none", ""):
            path_map[url] = path

    if not path_map:
        return

    for csv_path in [LINKEDIN_CSV, INDEED_CSV]:
        if not csv_path.exists():
            continue
        try:
            df = pd.read_csv(csv_path)
            src_url_col = next((c for c in df.columns if any(k in c.lower()
                               for k in ["apply_link", "job_url", "url", "link"])), None)
            if not src_url_col:
                continue
            changed = 0
            for i, row in df.iterrows():
                url = str(row.get(src_url_col, "")).split("?")[0].rstrip("/")
                if url in path_map:
                    df.at[i, "resume_path"] = path_map[url]
                    changed += 1
            if changed:
                df.to_csv(csv_path, index=False)
                info(f"  Resume paths written to {csv_path.name} ({changed} jobs)")
        except Exception as e:
            warn(f"Could not update resume paths in {csv_path.name}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — COVER LETTERS
# ═════════════════════════════════════════════════════════════════════════════
def step_cover_letters(df):
    step("✉️ ", "Writing Custom Cover Letters")

    try:
        import cover_letter   as cl
        import raghav_profile as rp
        import claude_engine  as ce
    except ImportError as e:
        warn(f"Import error: {e}"); return df

    apply_df = df[df["fit_apply"]] if "fit_apply" in df.columns else df
    if apply_df.empty:
        warn("No qualifying jobs for cover letters"); return df

    full_profile = {
        **rp.PROFILE,
        "skills": getattr(rp, "ALL_SKILLS_FLAT", []) or
                  [s for grp in getattr(rp, "SKILLS", {}).values() for s in grp],
        "experience": getattr(rp, "EXPERIENCE", []),
        "education":  getattr(rp, "EDUCATION",  []),
        "summary": (
            "Data professional with hands-on experience in data engineering, "
            "ETL pipelines, SQL, Python, cloud platforms (Azure, AWS, GCP), "
            "Apache Spark, Kafka, and analytics tools."
        ),
    }
    name          = full_profile.get("name", "Raghavendra Karanam")
    profile_summary = ce.build_profile_summary(full_profile)
    title_col = next((c for c in df.columns if "title"   in c.lower()), df.columns[0])
    co_col    = next((c for c in df.columns if any(k in c.lower()
                     for k in ["company", "employer", "organization"])), None)
    desc_col, fallback_cols = _find_desc_col(df)

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  BarColumn(bar_width=30), TextColumn("{task.completed}/{task.total}"),
                  console=console) as prog:
        task = prog.add_task("Writing cover letters...", total=len(apply_df))
        for idx, row in apply_df.iterrows():
            title   = _get(row, title_col, default="Data Role")
            company = _get(row, co_col, default="Company") if co_col else "Company"
            jd      = _get_jd_text(row, desc_col, fallback_cols)
            try:
                text = ce.write_cover_letter(name, profile_summary, jd, title, company)
                path = cl.save_cover_letter(text, title, company)
                df.at[idx, "cover_letter_path"] = path
            except Exception as e:
                df.at[idx, "cover_letter_path"] = ""
                warn(f"  Cover letter failed for {company}: {e}")
            prog.advance(task)

    built = int((df["cover_letter_path"] != "").sum()) if "cover_letter_path" in df.columns else 0
    ok(f"Wrote {built} cover letters → ~/job_pipeline/cover_letters/")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# STEP 6 — TRACKER
# ═════════════════════════════════════════════════════════════════════════════
def step_tracker(df):
    step("📊", "Updating Excel Tracker")
    try:
        import tracker as tr
        path = tr.create_tracker(df)
        ok(f"Tracker → {path}")
        import subprocess
        subprocess.Popen(["open", str(path)])
    except Exception as e:
        warn(f"Tracker error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════
def print_summary(df, start):
    elapsed = time.time() - start
    m, s    = int(elapsed // 60), int(elapsed % 60)

    if not HAS_RICH:
        print(f"\n{'='*60}")
        print(f"  Done in {m}m {s}s")
        if "fit_score" in df.columns:
            good = int(df["fit_apply"].sum()) if "fit_apply" in df.columns else 0
            print(f"  Good fits (≥65%): {good}")
        print('='*60)
        return

    t = Table(title="Pipeline Summary", box=box.ROUNDED, border_style="cyan")
    t.add_column("Metric",  style="bold white", width=28)
    t.add_column("Result",  style="cyan",        width=15)

    def count(col):
        if col not in df.columns: return "—"
        return str(int((df[col].notna() & (df[col] != "")).sum()))

    t.add_row("Total jobs scored",    str(len(df)))
    t.add_row("Good fits (≥65%)",     str(int(df["fit_apply"].sum())) if "fit_apply" in df.columns else "—")
    t.add_row("Resumes built",        count("resume_path"))
    t.add_row("Cover letters",        count("cover_letter_path"))
    t.add_row("Time",                 f"{m}m {s}s")

    console.print(); console.print(t); console.print()

    if "fit_score" in df.columns:
        tc = next((c for c in df.columns if "title"   in c.lower()), df.columns[0])
        cc = next((c for c in df.columns if "company" in c.lower()
                   or "employer" in c.lower()), None)
        top = df.nlargest(5, "fit_score")

        top_t = Table(title="🏆 Top 5 Best Fits", box=box.SIMPLE, border_style="green")
        top_t.add_column("Score",   width=8,  style="bold green")
        top_t.add_column("Title",   width=35)
        top_t.add_column("Company", width=25)

        for _, row in top.iterrows():
            sc  = int(row.get("fit_score", 0))
            col = "green" if sc >= 85 else "cyan" if sc >= 70 else "yellow"
            top_t.add_row(
                f"[{col}]{sc}%[/{col}]",
                str(row.get(tc, ""))[:33],
                str(row.get(cc, ""))[:23] if cc else "",
            )
        console.print(top_t)

    console.print(Panel(
        "[bold green]✅  Pipeline complete![/bold green]\n"
        "[dim]linkedin_jobs.csv and indeed_jobs.csv updated with scores + resume paths.\n"
        "Run auto_apply.py --execute to apply to qualifying jobs.[/dim]",
        border_style="green", padding=(0, 2),
    ))
    console.print()


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="Job Pipeline — Raghavendra Karanam")
    p.add_argument("--skip-fetch", action="store_true",
                   help="Skip JSearch API — use existing linkedin/indeed CSVs")
    p.add_argument("--limit",      type=int, default=0,
                   help="Max jobs to score (0 = no limit, cap 60)")
    p.add_argument("--no-resumes", action="store_true",
                   help="Skip resume building")
    p.add_argument("--no-cl",      action="store_true",
                   help="Skip cover letter writing")
    args = p.parse_args()

    start = time.time()
    print_header()

    df = step_fetch(args)
    if df.empty:
        log("[red]No jobs found. Run linkedin_scraper.py or indeed_scraper.py first.[/red]"
            if HAS_RICH else "No jobs found.")
        sys.exit(1)

    df = step_filter(df)
    df = step_score(df, limit=args.limit)

    if not args.no_resumes:
        df = step_resumes(df)
    if not args.no_cl:
        df = step_cover_letters(df)

    step_tracker(df)
    print_summary(df, start)

    # Remind user about applying
    apply_count = int(df["fit_apply"].sum()) if "fit_apply" in df.columns else 0
    if apply_count > 0:
        print()
        print(f"  ⚡  {apply_count} jobs ready to apply to.")
        print("      Run:  python auto_apply.py --dry-run    ← preview")
        print("            python auto_apply.py --execute    ← apply")
        print()


if __name__ == "__main__":
    main()
