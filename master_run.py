#!/opt/anaconda3/bin/python3
# =============================================================================
# MASTER_RUN.PY — Job Pipeline Orchestrator with Live Dashboard
# Powered by Claude claude-sonnet-4-6
#
# USAGE:
#   python master_run.py                  # Full run: fetch + score + resume + track
#   python master_run.py --apply          # Full run + auto-apply LinkedIn/Indeed
#   python master_run.py --skip-fetch     # Use existing jobs, rebuild resumes
#   python master_run.py --dry-run        # Score + preview, no apply
#   python master_run.py --limit 20       # Process first 20 jobs only
# =============================================================================

import sys
import time
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
sys.path.insert(0, str(PIPELINE_DIR))

console = Console() if HAS_RICH else None

# ── DESCRIPTION EXTRACTOR ─────────────────────────────────────────────────────
def _find_desc_col(df):
    """
    Robustly find the job description column.
    Priority: explicit names → keyword match → highlights/skills fallback.
    Returns (col_name_or_None, fallback_cols_list)
    """
    # 1. Explicit exact names (from JSearch / common APIs)
    for exact in ["job_description", "description", "jobDescription",
                  "job_details", "full_description"]:
        if exact in df.columns:
            return exact, []

    # 2. Substring match
    for c in df.columns:
        cl = c.lower()
        if any(k in cl for k in ["description", "desc", "requirement", "detail", "summary"]):
            return c, []

    # 3. Fallback — combine highlights + skills (better than nothing)
    fallbacks = [c for c in df.columns
                 if any(k in c.lower() for k in ["highlight", "skill", "qualif"])]
    return None, fallbacks

def _get_jd_text(row, desc_col, fallback_cols):
    """
    Return the best available job description text from a row.
    Never returns empty string if any text source exists.
    """
    if desc_col:
        val = str(row.get(desc_col, "") or "").strip()
        if val and val.lower() not in ("nan", "none", ""):
            return val

    # Try fallback columns
    parts = []
    for c in fallback_cols:
        v = str(row.get(c, "") or "").strip()
        if v and v.lower() not in ("nan", "none", ""):
            parts.append(v)
    return " | ".join(parts)

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
        print(f"\n{'─'*50}\n{emoji}  {title}")

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

# ── STEP 1: FETCH ─────────────────────────────────────────────────────────────
def step_fetch(args):
    step("📡", "Fetching Jobs")
    RAW_CSV = PIPELINE_DIR / "data" / "raw_jobs.csv"

    if args.skip_fetch and RAW_CSV.exists():
        df = pd.read_csv(RAW_CSV)
        ok(f"Using cached {len(df)} jobs from raw_jobs.csv")
        return df

    try:
        import pipeline as pip_mod
        with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                      console=console, transient=True) as p:
            t = p.add_task("Searching LinkedIn, Indeed, RapidAPI...", total=None)
            # Fetch all configured queries and combine into one DataFrame
            all_records = []
            for query in pip_mod.SEARCH_QUERIES:
                records = pip_mod.fetch_jobs(query)
                all_records.extend(records)
            df = pip_mod.clean_dataframe(all_records)
            p.update(t, completed=True)
        ok(f"Fetched {len(df)} jobs")
        RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(RAW_CSV, index=False)
        return df
    except Exception as e:
        warn(f"Fetch error: {e}")
        if RAW_CSV.exists():
            df = pd.read_csv(RAW_CSV)
            warn(f"Using cached {len(df)} jobs")
            return df
        return pd.DataFrame()

# ── STEP 2: FILTER LEVEL ──────────────────────────────────────────────────────
def step_filter(df):
    step("🎯", "Filtering — Entry & Mid Level Data Jobs Only")
    from claude_engine import is_good_level

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
    return result if len(result) > 0 else df

# ── STEP 3: CLAUDE FIT SCORING ────────────────────────────────────────────────
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
        warn("Claude API unavailable — using default scores")
        df["fit_score"] = 65; df["fit_grade"] = "C"; df["fit_apply"] = True
        return df

    # Build full profile by merging all raghav_profile module variables
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
    summary   = ce.build_profile_summary(full_profile)
    title_col = next((c for c in df.columns if "title" in c.lower()), df.columns[0])
    co_col    = next((c for c in df.columns if any(k in c.lower()
                     for k in ["company", "employer", "organization"])), None)
    desc_col, fallback_cols = _find_desc_col(df)

    # Debug: show what columns we found
    info(f"Columns in CSV: {list(df.columns)}")
    info(f"Using → title: {title_col}  |  company: {co_col}  |  desc: {desc_col or f'FALLBACK({fallback_cols})'}")
    info(f"Profile skills count: {len(full_profile['skills'])}  |  experience roles: {len(full_profile['experience'])}")

    # Sanity check: warn if descriptions look empty
    if desc_col:
        sample = str(df.iloc[0].get(desc_col, "")).strip()
        if not sample or sample.lower() in ("nan", "none", ""):
            warn(f"⚠  Column '{desc_col}' appears empty — scores may be low. Check raw_jobs.csv.")

    cap = limit if limit > 0 else min(len(df), 50)
    df  = df.head(cap).copy()

    scores, grades, applys, reasons = [], [], [], []

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  BarColumn(bar_width=30), TextColumn("{task.completed}/{task.total}"),
                  TimeElapsedColumn(), console=console) as prog:
        task = prog.add_task("Scoring with Claude...", total=cap)
        for _, row in df.iterrows():
            jd_text = _get_jd_text(row, desc_col, fallback_cols)
            res = ce.score_fit(
                summary,
                jd_text,
                str(row.get(title_col, "Data Role")),
                str(row.get(co_col, "Company")) if co_col else "Company",
            )
            scores.append(res.get("score", 65))
            grades.append(res.get("grade", "C"))
            applys.append(res.get("apply", True))
            reasons.append(res.get("reasoning", ""))
            prog.advance(task)
            time.sleep(0.3)

    df["fit_score"] = scores
    df["fit_grade"] = grades
    df["fit_apply"] = applys
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

    # Save scored jobs
    scored_csv = PIPELINE_DIR / "data" / "filtered_jobs.csv"
    df.to_csv(scored_csv, index=False)
    info(f"Scored jobs saved → {scored_csv}")
    return df

# ── STEP 4: BUILD RESUMES ─────────────────────────────────────────────────────
def step_resumes(df):
    step("📄", "Building Custom Word Resumes")

    apply_df  = df[df["fit_apply"]] if "fit_apply" in df.columns else df
    if apply_df.empty:
        warn("No jobs to build resumes for"); return df

    try:
        import resume_builder as rb
        import jd_parser      as jdp
        import raghav_profile as rp
        import claude_engine  as ce
        import copy
    except ImportError as e:
        warn(f"Import error: {e}"); return df

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
    summary   = ce.build_profile_summary(full_profile)
    title_col = next((c for c in df.columns if "title"   in c.lower()), df.columns[0])
    co_col    = next((c for c in df.columns if any(k in c.lower()
                     for k in ["company", "employer", "organization"])), None)
    desc_col, fallback_cols = _find_desc_col(df)

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  BarColumn(bar_width=30), TextColumn("{task.completed}/{task.total}"),
                  console=console) as prog:
        task = prog.add_task("Building resumes...", total=len(apply_df))

        for idx, row in apply_df.iterrows():
            title   = str(row.get(title_col, "Data Role"))
            company = str(row.get(co_col, "Company")) if co_col else "Company"
            jd      = _get_jd_text(row, desc_col, fallback_cols)

            try:
                parsed = jdp.parse_jd(jd, title) if jd else {}

                if ce.CLAUDE_AVAILABLE and jd:
                    all_bullets = []
                    for exp in full_profile.get("experience", []):
                        all_bullets.extend(exp.get("bullets",
                                           exp.get("responsibilities", [])))
                    tailored = ce.tailor_bullets(all_bullets[:12], jd, title)
                    p_copy = copy.deepcopy(profile)
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
                df.loc[idx, "resume_path"] = path
            except Exception as e:
                df.loc[idx, "resume_path"] = ""

            prog.advance(task)

    built = df["resume_path"].notna().sum() if "resume_path" in df.columns else 0
    ok(f"Built {built} custom resumes in ~/job_pipeline/resumes/")
    return df

# ── STEP 5: COVER LETTERS ─────────────────────────────────────────────────────
def step_cover_letters(df):
    step("✉️ ", "Writing Custom Cover Letters")

    try:
        import cover_letter   as cl
        import raghav_profile as rp
        import claude_engine  as ce
    except ImportError as e:
        warn(f"Import error: {e}"); return df

    apply_df  = df[df["fit_apply"]] if "fit_apply" in df.columns else df
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
    name      = full_profile.get("name", "Raghavendra Karanam")
    summary   = ce.build_profile_summary(full_profile)
    title_col = next((c for c in df.columns if "title"   in c.lower()), df.columns[0])
    co_col    = next((c for c in df.columns if any(k in c.lower()
                     for k in ["company", "employer", "organization"])), None)
    desc_col, fallback_cols = _find_desc_col(df)

    with Progress(SpinnerColumn(), TextColumn("[cyan]{task.description}"),
                  BarColumn(bar_width=30), TextColumn("{task.completed}/{task.total}"),
                  console=console) as prog:
        task = prog.add_task("Writing cover letters...", total=len(apply_df))
        for idx, row in apply_df.iterrows():
            title   = str(row.get(title_col, "Data Role"))
            company = str(row.get(co_col, "Company")) if co_col else "Company"
            jd      = _get_jd_text(row, desc_col, fallback_cols)
            try:
                text = ce.write_cover_letter(name, summary, jd, title, company)
                path = cl.save_cover_letter(text, title, company)
                df.loc[idx, "cover_letter_path"] = path
            except Exception:
                df.loc[idx, "cover_letter_path"] = ""
            prog.advance(task)

    built = df["cover_letter_path"].notna().sum() if "cover_letter_path" in df.columns else 0
    ok(f"Wrote {built} cover letters in ~/job_pipeline/cover_letters/")
    return df

# ── STEP 6: AUTO APPLY ───────────────────────────────────────────────────────
def step_apply(df, dry_run=False, limit=0):
    step("🚀", "Auto Applying — LinkedIn Easy Apply + Indeed Portal")
    if dry_run:
        info("DRY RUN — previewing only, not applying")
        apply_df = df[df["fit_apply"]] if "fit_apply" in df.columns else df
        info(f"{len(apply_df)} jobs queued for application")
        return
    try:
        import auto_apply as aa
        aa.main_from_df(df, limit=limit)
    except Exception as e:
        warn(f"Auto-apply error: {e}")

# ── STEP 7: TRACKER ───────────────────────────────────────────────────────────
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

# ── SUMMARY ───────────────────────────────────────────────────────────────────
def print_summary(df, start):
    elapsed = time.time() - start
    m, s    = int(elapsed // 60), int(elapsed % 60)

    if not HAS_RICH:
        print(f"\n{'='*50}\n  Done in {m}m {s}s\n{'='*50}")
        return

    t = Table(title="Pipeline Summary", box=box.ROUNDED, border_style="cyan")
    t.add_column("Metric",  style="bold white", width=28)
    t.add_column("Result",  style="cyan",        width=15)

    def count(col): return str(df[col].notna().sum()) if col in df.columns else "—"

    t.add_row("Total jobs scored",    str(len(df)))
    t.add_row("Good fits",            str(int(df["fit_apply"].sum())) if "fit_apply" in df.columns else "—")
    t.add_row("Resumes built",        count("resume_path"))
    t.add_row("Cover letters",        count("cover_letter_path"))
    t.add_row("Time",                 f"{m}m {s}s")

    console.print(); console.print(t); console.print()

    if "fit_score" in df.columns:
        tc = next((c for c in df.columns if "title"   in c.lower()), df.columns[0])
        cc = next((c for c in df.columns if "company" in c.lower()), None)
        top = df.nlargest(5, "fit_score")

        top_t = Table(title="🏆 Top 5 Best Fits", box=box.SIMPLE, border_style="green")
        top_t.add_column("Score", width=8, style="bold green")
        top_t.add_column("Title", width=35)
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
        "[dim]Open Application_Tracker.xlsx to see all results.[/dim]",
        border_style="green", padding=(0, 2),
    ))
    console.print()

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Job Pipeline — Raghavendra Karanam")
    p.add_argument("--skip-fetch", action="store_true")
    p.add_argument("--apply",      action="store_true")
    p.add_argument("--dry-run",    action="store_true")
    p.add_argument("--limit",      type=int, default=0)
    p.add_argument("--no-resumes", action="store_true")
    p.add_argument("--no-cl",      action="store_true")
    args = p.parse_args()

    start = time.time()
    print_header()

    df = step_fetch(args)
    if df.empty:
        log("[red]No jobs found.[/red]" if HAS_RICH else "No jobs found.")
        sys.exit(1)

    df = step_filter(df)
    df = step_score(df, limit=args.limit)

    if not args.no_resumes:
        df = step_resumes(df)
    if not args.no_cl:
        df = step_cover_letters(df)

    if args.apply:
        step_apply(df, dry_run=args.dry_run, limit=args.limit)
    else:
        info("Tip: Add --apply to auto-apply to LinkedIn + Indeed Easy Apply jobs")

    step_tracker(df)
    print_summary(df, start)

if __name__ == "__main__":
    main()
