# =============================================================================
# TRACKER.PY — PRO v4  (5-Dimension ATS Breakdown + Honest Status)
#
# COLUMNS  (A–U):
#   A  #            B  Date Applied    C  Company         D  Job Title
#   E  Location     F  Salary          G  ATS BEFORE%     H  ATS AFTER%
#   I  IMPROVEMENT  J  Status          K  Apply Link      L  Follow-Up
#   M  Notes        N  Resume File     O  Cover Letter
#   --- ATS DIMENSION BREAKDOWN (pro scoring) ---
#   P  KW%  (Keyword Coverage  35%)    Q  Skills%  (Skills Alignment 25%)
#   R  Exp% (Experience Match  20%)    S  Edu%     (Education Match  10%)
#   T  Title% (Title Relevance 10%)   U  YOE Req'd / Edu Req'd
#
# STATUS values (honest):
#   "Applied"            — confirmed LinkedIn Easy Apply submission
#   "Needs Manual Apply" — external job, browser tab opened
#   "Already Applied"    — LinkedIn detected prior submission
#   "Phone Screen" / "Interview" / "Offer" / "Rejected" / "No Response"
# =============================================================================

from __future__ import annotations

import os
import sys
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import CellIsRule
except ImportError:
    print("openpyxl not installed.  Run:  pip install openpyxl --break-system-packages")
    sys.exit(1)

TRACKER_PATH = Path(__file__).parent / "data" / "Application_Tracker.xlsx"

# ── palette ───────────────────────────────────────────────────────────────────
BLUE_HEADER  = "1F5C99"
WHITE        = "FFFFFF"
GRAY_LIGHT   = "F2F2F2"
DARK_TEXT    = "222222"

STATUS_COLORS = {
    "Applied":             "FFF2CC",   # warm yellow
    "Needs Manual Apply":  "FFE0B2",   # orange tint  — external jobs
    "Already Applied":     "E3F2FD",   # light blue
    "Phone Screen":        "DDEBF7",
    "Interview":           "E2EFDA",
    "Offer":               "C6EFCE",   # green
    "Rejected":            "FFCCCC",
    "No Response":         "F2F2F2",
    "Withdrawn":           "EDEDED",
    "Failed — Check Manually": "FCE4D6",
}

# ── column definitions ────────────────────────────────────────────────────────
# (letter, header label, width)
COLUMNS = [
    ("A",  "#",                      5),
    ("B",  "Date Applied",          14),
    ("C",  "Company",               22),
    ("D",  "Job Title",             28),
    ("E",  "Location",              18),
    ("F",  "Salary Range",          17),
    ("G",  "ATS BEFORE %",          13),
    ("H",  "ATS AFTER %",           13),
    ("I",  "IMPROVEMENT",           13),
    ("J",  "Status",                20),
    ("K",  "Apply Link",            28),
    ("L",  "Follow-Up",             14),
    ("M",  "Notes",                 35),
    ("N",  "Resume File",           32),
    ("O",  "Cover Letter",          32),
    # ── dimension breakdown ───────────────────────────────────────────────────
    ("P",  "KW Coverage % [35%]",   17),
    ("Q",  "Skills Align % [25%]",  17),
    ("R",  "Exp Match %   [20%]",   17),
    ("S",  "Edu Match %   [10%]",   17),
    ("T",  "Title Score % [10%]",   17),
    ("U",  "YOE Req / Edu Req",     20),
]

COL_BEFORE      = "G"
COL_AFTER       = "H"
COL_IMPROVEMENT = "I"
COL_STATUS      = "J"
COL_LINK        = "K"
DIM_COLS        = ("P", "Q", "R", "S", "T")   # dimension score columns


# ── style helpers ─────────────────────────────────────────────────────────────
def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)

def _border(color: str = "CCCCCC") -> Border:
    side = Side(style="hair", color=color)
    return Border(bottom=side, right=side)

def _header_style(ws) -> None:
    for col_letter, col_name, col_width in COLUMNS:
        cell = ws[f"{col_letter}1"]
        cell.value     = col_name
        cell.font      = Font(name="Calibri", bold=True, color=WHITE, size=10)
        cell.fill      = _fill(BLUE_HEADER)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = Border(
            bottom=Side(style="medium", color=WHITE),
            right= Side(style="thin",   color="4472C4"),
        )
        ws.column_dimensions[col_letter].width = col_width
    ws.row_dimensions[1].height = 36

def _row_style(ws, row: int, status: str) -> None:
    bg = STATUS_COLORS.get(status, WHITE)
    for col_letter, _, _ in COLUMNS:
        cell = ws[f"{col_letter}{row}"]
        cell.fill      = _fill(bg)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border    = _border()
        cell.font      = Font(name="Calibri", size=10, color=DARK_TEXT)
    ws.row_dimensions[row].height = 22


def _apply_cf(ws, num_rows: int) -> None:
    if num_rows < 1:
        return
    last = num_rows + 1

    # ATS BEFORE (G)
    ws.conditional_formatting.add(f"G2:G{last}", CellIsRule("lessThan",           ["50"],       fill=_fill("FCE4D6")))
    ws.conditional_formatting.add(f"G2:G{last}", CellIsRule("between",            ["50", "70"],  fill=_fill("FFEB9C")))
    ws.conditional_formatting.add(f"G2:G{last}", CellIsRule("greaterThanOrEqual", ["70"],        fill=_fill("EBF5EB")))

    # ATS AFTER (H)
    ws.conditional_formatting.add(f"H2:H{last}", CellIsRule("lessThan",           ["80"],       fill=_fill("FFEB9C")))
    ws.conditional_formatting.add(f"H2:H{last}", CellIsRule("between",            ["80", "94"],  fill=_fill("C6EFCE")))
    ws.conditional_formatting.add(f"H2:H{last}", CellIsRule("greaterThanOrEqual", ["95"],        fill=_fill("00B050")))

    # IMPROVEMENT (I)
    ws.conditional_formatting.add(f"I2:I{last}", CellIsRule("greaterThan",        ["0"],         fill=_fill("DDEEFF")))

    # Dimension cols (P–T): heat-map <60=red, 60-79=yellow, 80-89=light-green, >=90=green
    for col in DIM_COLS:
        ws.conditional_formatting.add(f"{col}2:{col}{last}", CellIsRule("lessThan",           ["60"],       fill=_fill("FCE4D6")))
        ws.conditional_formatting.add(f"{col}2:{col}{last}", CellIsRule("between",            ["60", "79"],  fill=_fill("FFEB9C")))
        ws.conditional_formatting.add(f"{col}2:{col}{last}", CellIsRule("between",            ["80", "89"],  fill=_fill("C6EFCE")))
        ws.conditional_formatting.add(f"{col}2:{col}{last}", CellIsRule("greaterThanOrEqual", ["90"],        fill=_fill("00B050")))


# =============================================================================
# MAIN TRACKER BUILDER
# =============================================================================

def create_tracker(jobs_df: pd.DataFrame = None) -> str:
    print("\n" + "═" * 68)
    print("  JOB PIPELINE — Building Application Tracker  PRO v4")
    print("  5-Dimension ATS Breakdown: KW · Skills · Exp · Edu · Title")
    print("═" * 68)

    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()

    # ── Sheet 1: Applications ─────────────────────────────────────────────────
    ws = wb.active
    ws.title = "Applications"
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False
    _header_style(ws)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}1"

    today      = datetime.now()
    rows_added = 0
    score_pairs: list[tuple[float, float]] = []

    # Dimension averages collectors
    dim_sums: dict[str, float] = {"kw": 0, "sk": 0, "ex": 0, "ed": 0, "tt": 0}
    dim_counts = 0

    if jobs_df is not None and not jobs_df.empty:
        for i, (_, row) in enumerate(jobs_df.iterrows(), start=2):
            follow_up = (today + timedelta(days=7)).strftime("%Y-%m-%d")

            # ── ATS composite scores ───────────────────────────────────────────
            before = _safe_float(row, ["ats_score_before", "initial_score",   "ats_score"])
            after  = _safe_float(row, ["ats_score_after",  "optimized_score", "ats_score"])
            delta  = round(after - before, 1)
            score_pairs.append((before, after))

            # ── Dimension scores (from jd_parser PRO v4) ──────────────────────
            kw_after  = _safe_float(row, ["dim_keyword_after",  "dim_keyword"])
            sk_after  = _safe_float(row, ["dim_skills_after",   "dim_skills"])
            exp_score = _safe_float(row, ["dim_experience"])
            edu_score = _safe_float(row, ["dim_education"])
            ttl_score = _safe_float(row, ["dim_title"])

            dim_sums["kw"] += kw_after
            dim_sums["sk"] += sk_after
            dim_sums["ex"] += exp_score
            dim_sums["ed"] += edu_score
            dim_sums["tt"] += ttl_score
            dim_counts += 1

            yoe_req  = str(row.get("yoe_required", ""))
            edu_req  = str(row.get("edu_required", ""))
            req_cell = f"{yoe_req} yrs / {edu_req}".strip(" /")

            # ── Status (honest — from data, never hardcoded "Applied") ─────────
            status = str(row.get("status", "Needs Manual Apply")).strip()
            if status in ("", "nan", "None", "Pending"):
                status = "Needs Manual Apply"

            company   = str(row.get("company", ""))
            title_val = str(row.get("title",   ""))
            safe_co   = company[:20].replace(" ", "_")
            safe_ti   = title_val[:15].replace(" ", "_")

            resume_file = f"Raghavendra_Karanam_{safe_co}_{safe_ti}.docx"
            cl_file     = f"CoverLetter_{safe_co}_{safe_ti}.docx"

            data = [
                i - 1,                                   # A: #
                today.strftime("%Y-%m-%d"),               # B: Date
                company,                                  # C: Company
                title_val,                                # D: Title
                str(row.get("location", "")),             # E: Location
                str(row.get("salary",   "TBD")),          # F: Salary
                round(before, 1),                         # G: ATS BEFORE
                round(after,  1),                         # H: ATS AFTER
                f"+{delta:.0f}%" if delta >= 0 else f"{delta:.0f}%",  # I: IMPROVEMENT
                status,                                   # J: Status
                str(row.get("apply_link", "")),           # K: Apply Link
                follow_up,                                # L: Follow-Up
                str(row.get("apply_note", "")),           # M: Notes (from auto_apply)
                resume_file,                              # N: Resume
                cl_file,                                  # O: Cover Letter
                round(kw_after,  1),                      # P: KW Coverage
                round(sk_after,  1),                      # Q: Skills Align
                round(exp_score, 1),                      # R: Exp Match
                round(edu_score, 1),                      # S: Edu Match
                round(ttl_score, 1),                      # T: Title Score
                req_cell,                                 # U: YOE/Edu requirements
            ]

            for col_idx, value in enumerate(data, start=1):
                cl = get_column_letter(col_idx)
                cell = ws[f"{cl}{i}"]
                cell.value = value

                # Hyperlink for apply link
                if cl == COL_LINK and str(value).startswith("http"):
                    cell.hyperlink = str(value)
                    cell.value     = "Apply →"
                    cell.font      = Font(name="Calibri", size=10,
                                         color=BLUE_HEADER, underline="single")

                # Number format for score columns
                if cl in (COL_BEFORE, COL_AFTER) + DIM_COLS:
                    cell.alignment    = Alignment(horizontal="center", vertical="center")
                    cell.number_format = '0.0"%"'

                # Bold improvement
                if cl == COL_IMPROVEMENT:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.font      = Font(name="Calibri", size=10, bold=True, color=BLUE_HEADER)

                # Status — center
                if cl == COL_STATUS:
                    cell.alignment = Alignment(horizontal="center", vertical="center")

            _row_style(ws, i, status)
            rows_added += 1

    else:
        # Placeholder row showing all columns
        sample = [
            1, today.strftime("%Y-%m-%d"), "Example Corp", "Data Engineer",
            "Remote, FL", "$85,000–$95,000",
            42.0, 97.5, "+55%",
            "Applied",
            "https://linkedin.com/jobs/view/123",
            (today + timedelta(days=7)).strftime("%Y-%m-%d"),
            "LinkedIn Easy Apply — confirmed",
            "Raghavendra_Karanam_ExampleCorp_Data_Engineer.docx",
            "CoverLetter_ExampleCorp_Data_Engineer.docx",
            97.0, 95.0, 80.0, 100.0, 100.0,
            "2 yrs / bachelor",
        ]
        for col_idx, value in enumerate(sample, start=1):
            cell = ws[f"{get_column_letter(col_idx)}2"]
            cell.value = value
        _row_style(ws, 2, "Applied")
        rows_added = 1

    _apply_cf(ws, rows_added)

    # ── Sheet 2: Dashboard ────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Dashboard")
    ws2.sheet_view.showGridLines = False
    for col, w in [("A", 30), ("B", 18), ("D", 30), ("F", 22)]:
        ws2.column_dimensions[col].width = w

    ws2["A1"] = "APPLICATION DASHBOARD"
    ws2["A1"].font      = Font(name="Calibri", bold=True, size=16, color=BLUE_HEADER)
    ws2["A2"] = f"Raghavendra Karanam  |  Data Engineer  |  Updated {today:%b %d, %Y}"
    ws2["A2"].font      = Font(name="Calibri", size=10, color="888888")
    ws2.row_dimensions[1].height = 28

    if score_pairs:
        avg_before = sum(b for b, a in score_pairs) / len(score_pairs)
        avg_after  = sum(a for b, a in score_pairs) / len(score_pairs)
        avg_delta  = avg_after - avg_before
        max_after  = max(a for b, a in score_pairs)
        min_after  = min(a for b, a in score_pairs)
    else:
        avg_before = avg_after = avg_delta = max_after = min_after = 0.0

    if dim_counts:
        avg_kw  = dim_sums["kw"] / dim_counts
        avg_sk  = dim_sums["sk"] / dim_counts
        avg_exp = dim_sums["ex"] / dim_counts
        avg_edu = dim_sums["ed"] / dim_counts
        avg_ttl = dim_sums["tt"] / dim_counts
    else:
        avg_kw = avg_sk = avg_exp = avg_edu = avg_ttl = 0.0

    # Count status types
    status_counts: dict[str, int] = {}
    if jobs_df is not None and not jobs_df.empty and "status" in jobs_df.columns:
        for s in jobs_df["status"].fillna("Needs Manual Apply"):
            s = str(s).strip()
            if s in ("", "nan", "None", "Pending"):
                s = "Needs Manual Apply"
            status_counts[s] = status_counts.get(s, 0) + 1

    confirmed_applied = status_counts.get("Applied", 0)
    manual_needed     = status_counts.get("Needs Manual Apply", 0)

    stats_left = [
        ("── PIPELINE STATS ──",           ""),
        ("Total Jobs",                      rows_added),
        ("Confirmed Applied (LinkedIn)",     confirmed_applied),
        ("Needs Manual Apply",               manual_needed),
        ("Phone Screen",                     status_counts.get("Phone Screen", 0)),
        ("Interview",                        status_counts.get("Interview",    0)),
        ("Offer",                            status_counts.get("Offer",        0)),
        ("Rejected",                         status_counts.get("Rejected",     0)),
        ("",                                ""),
        ("── ATS COMPOSITE ──",             ""),
        ("Avg ATS BEFORE",                  f"{avg_before:.1f}%"),
        ("Avg ATS AFTER",                   f"{avg_after:.1f}%"),
        ("Avg Improvement",                  f"+{avg_delta:.1f}%"),
        ("Best ATS Score",                  f"{max_after:.1f}%"),
        ("Lowest ATS Score",                f"{min_after:.1f}%"),
        ("",                                ""),
        ("── DIMENSION AVERAGES ──",        ""),
        ("Keyword Coverage  [35%]",          f"{avg_kw:.1f}%"),
        ("Skills Alignment  [25%]",          f"{avg_sk:.1f}%"),
        ("Experience Match  [20%]",          f"{avg_exp:.1f}%"),
        ("Education Match   [10%]",          f"{avg_edu:.1f}%"),
        ("Title Relevance   [10%]",          f"{avg_ttl:.1f}%"),
        ("",                                ""),
        ("Last Updated",                     today.strftime("%Y-%m-%d")),
    ]

    row_num = 4
    for label, value in stats_left:
        if not label:
            row_num += 1
            continue
        cl = ws2[f"A{row_num}"]
        cv = ws2[f"B{row_num}"]
        cl.value = label
        cv.value = value
        is_header = label.startswith("──")
        cl.font   = Font(name="Calibri", bold=True, size=11,
                         color=BLUE_HEADER if is_header else DARK_TEXT)
        cv.font   = Font(name="Calibri", size=11,
                         color=("00B050" if any(k in label for k in
                                ["AFTER", "Improvement", "Best"]) else BLUE_HEADER))
        bg = "E8F0F9" if is_header else (GRAY_LIGHT if row_num % 2 == 0 else WHITE)
        cl.fill = _fill(bg)
        cv.fill = _fill(bg)
        row_num += 1

    # ── Scoring Guide ─────────────────────────────────────────────────────────
    ws2["D4"] = "ATS Score Color Guide"
    ws2["D4"].font = Font(name="Calibri", bold=True, size=11, color=BLUE_HEADER)
    guide = [
        ("AFTER ≥ 95%  → GREAT",         "00B050"),
        ("AFTER 80–94% → GOOD",           "C6EFCE"),
        ("AFTER < 80%  → NEEDS WORK",     "FFEB9C"),
        ("Dimension ≥ 90% → Excellent",   "00B050"),
        ("Dimension 80–89% → Good",       "C6EFCE"),
        ("Dimension 60–79% → Moderate",   "FFEB9C"),
        ("Dimension < 60%  → Gap",        "FCE4D6"),
        ("BEFORE < 50% → Poor baseline",  "FCE4D6"),
        ("BEFORE 50–70% → Moderate base", "FFEB9C"),
        ("BEFORE ≥ 70% → Strong base",    "EBF5EB"),
    ]
    for j, (lbl, color) in enumerate(guide, start=5):
        c = ws2[f"D{j}"]
        c.value = lbl
        c.fill  = _fill(color)
        c.font  = Font(name="Calibri", size=10)
        c.alignment = Alignment(horizontal="left", indent=1)

    # ── Status Key ────────────────────────────────────────────────────────────
    ws2["F4"] = "Status Key"
    ws2["F4"].font = Font(name="Calibri", bold=True, size=11, color=BLUE_HEADER)
    for j, (status, color) in enumerate(STATUS_COLORS.items(), start=5):
        c = ws2[f"F{j}"]
        c.value = status
        c.fill  = _fill(color)
        c.font  = Font(name="Calibri", size=10)
        c.alignment = Alignment(horizontal="center")

    wb.save(str(TRACKER_PATH))

    print(f"\n  Tracker saved → {TRACKER_PATH}")
    print(f"  Rows: {rows_added}  |  Confirmed Applied: {confirmed_applied}  |  Needs Manual: {manual_needed}")
    if score_pairs:
        print(f"  ATS: {avg_before:.1f}% BEFORE → {avg_after:.1f}% AFTER  (+{avg_delta:.1f}%)")
        print(f"  Dimensions: KW={avg_kw:.0f}%  Skills={avg_sk:.0f}%  Exp={avg_exp:.0f}%  Edu={avg_edu:.0f}%  Title={avg_ttl:.0f}%")
    print("═" * 68 + "\n")
    return str(TRACKER_PATH)


# =============================================================================
# STATUS UPDATER  (called by auto_apply.py after each application)
# =============================================================================

def update_status(company_or_id: str, new_status: str, notes: str = "") -> None:
    """Update status and notes for a row matching company name."""
    if not TRACKER_PATH.exists():
        print(f"  Tracker not found at {TRACKER_PATH} — skipping status update")
        return

    wb = openpyxl.load_workbook(str(TRACKER_PATH))
    ws = wb["Applications"]

    # Column positions (1-based) — must match COLUMNS list above
    COL_IDX = {c: i for i, (c, _, _) in enumerate(COLUMNS, start=1)}
    status_col  = COL_IDX["J"]
    company_col = COL_IDX["C"]
    notes_col   = COL_IDX["M"]

    matched = False
    for row in ws.iter_rows(min_row=2):
        company_val = str(row[company_col - 1].value or "").lower()
        if company_or_id.lower() in company_val:
            # Status
            row[status_col - 1].value = new_status
            row[status_col - 1].alignment = Alignment(horizontal="center", vertical="center")

            # Notes (append, don't overwrite)
            if notes:
                existing = str(row[notes_col - 1].value or "")
                row[notes_col - 1].value = (
                    f"{existing}\n{notes}".strip() if existing else notes
                )

            # Re-color the whole row
            bg = STATUS_COLORS.get(new_status, WHITE)
            for cell in row:
                if cell.value is not None:
                    cell.fill = PatternFill("solid", fgColor=bg)

            print(f"  Tracker updated: '{row[company_col-1].value}' → {new_status}")
            matched = True
            break

    if not matched:
        print(f"  [Warning] Company '{company_or_id}' not found in tracker")

    wb.save(str(TRACKER_PATH))


# ── helpers ───────────────────────────────────────────────────────────────────
def _safe_float(row, keys: list, default: float = 0.0) -> float:
    for k in keys:
        try:
            v = row.get(k)
            if v is not None and str(v) not in ("", "nan", "None"):
                return float(v)
        except Exception:
            continue
    return default


# =============================================================================
# STANDALONE
# =============================================================================
if __name__ == "__main__":
    filtered_csv = Path(__file__).parent / "data" / "filtered_jobs.csv"
    if filtered_csv.exists():
        df = pd.read_csv(filtered_csv)
        create_tracker(df)
    else:
        print("No filtered_jobs.csv found — creating tracker with placeholder row")
        create_tracker()
