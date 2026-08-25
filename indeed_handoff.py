#!/usr/bin/env python3
# =============================================================================
# INDEED_HANDOFF.PY — "Hand-off" mode for Indeed (bypasses the Cloudflare wall)
#
# WHY THIS EXISTS:
#   Indeed sits behind Cloudflare, which fingerprints and blocks any
#   *automated* browser — the pipeline's Chrome window hits "Additional
#   Verification Required / Verify you are human" and the checkbox will NOT
#   pass no matter what (confirmed for weeks; real Chrome on the same machine
#   works perfectly). So automated apply on Indeed is a dead end.
#
#   Instead of fighting that, this hands the last step to a human:
#     • The pipeline builds a clean HTML dashboard of your Indeed searches
#       (your existing INDEED_QUERIES, with the entry-level + last-7-days
#       filters already applied).
#     • You open it in your REAL browser (where Indeed works fine) and click
#       through — a real human in a real browser is the one thing Cloudflare
#       can't block.
#     • Your tailored resumes are linked right there for quick attach.
#
#   No automated browser is launched here, so there is nothing for Cloudflare
#   to block. This can never get you flagged.
#
# USAGE:
#   python indeed_handoff.py            → build dashboard + open in real browser
#   python indeed_handoff.py --no-open  → build only (don't auto-open)
# =============================================================================

import sys
import urllib.parse
import subprocess
from datetime import datetime
from pathlib import Path

import config as cfg

# Same search list the automated Indeed engine used.
QUERIES = getattr(cfg, "INDEED_QUERIES", getattr(cfg, "LINKEDIN_QUERIES", []))
HTML_PATH = Path(getattr(cfg, "INDEED_HANDOFF_HTML",
                         cfg.DATA_DIR / "indeed_handoff.html"))
RESUMES_DIR = Path(getattr(cfg, "RESUMES_DIR", cfg.BASE_DIR / "resumes"))


def build_indeed_url(kw: str) -> str:
    """Direct Indeed search URL with the same filters the auto-engine used
    (entry level, last 7 days, newest first)."""
    return "https://www.indeed.com/jobs?" + urllib.parse.urlencode({
        "q": kw,
        "l": "United States",
        "sort": "date",
        "fromage": "7",
        "explvl": "entry_level",
    })


def _category(q: str) -> str:
    ql = q.lower()
    if any(w in ql for w in ("scien", " ml", "machine learning", "ai ")) or ql.endswith(" ml"):
        return "Data Science / ML"
    if any(w in ql for w in ("analyst", "analytics", "bi ", "tableau", "power bi",
                             "insights", "reporting", "intelligence")):
        return "Data Analysis / BI"
    if "engineer" in ql or "etl" in ql or "pipeline" in ql or "warehouse" in ql:
        return "Data Engineering"
    return "Other"


# Fixed display order for the category sections.
_CAT_ORDER = ["Data Engineering", "Data Analysis / BI", "Data Science / ML", "Other"]


def _resume_files():
    try:
        return sorted([p for p in RESUMES_DIR.glob("*.docx")], key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return []


def build_html() -> str:
    grouped = {c: [] for c in _CAT_ORDER}
    for q in QUERIES:
        grouped.setdefault(_category(q), []).append(q)

    now = datetime.now().strftime("%A, %b %d %Y · %I:%M %p")
    total = len(QUERIES)
    resumes = _resume_files()

    cards = []
    for cat in _CAT_ORDER:
        qs = grouped.get(cat, [])
        if not qs:
            continue
        cards.append(f'<h2 class="cat">{cat} <span class="count">{len(qs)}</span></h2>')
        cards.append('<div class="grid">')
        for q in qs:
            url = build_indeed_url(q)
            cards.append(
                f'<a class="job" href="{url}" target="_blank" rel="noopener">'
                f'<span class="q">{q}</span>'
                f'<span class="go">Open on Indeed →</span></a>'
            )
        cards.append('</div>')
    cards_html = "\n".join(cards)

    if resumes:
        resume_folder_uri = "file://" + urllib.parse.quote(str(RESUMES_DIR))
        resume_items = "".join(
            f'<li><a href="file://{urllib.parse.quote(str(r))}" target="_blank">{r.name}</a></li>'
            for r in resumes[:12]
        )
        resume_block = (
            f'<div class="resumes"><h2 class="cat">Your tailored resumes '
            f'<a class="folderlink" href="{resume_folder_uri}" target="_blank">open folder →</a></h2>'
            f'<ul>{resume_items}</ul></div>'
        )
    else:
        resume_block = (
            f'<div class="resumes"><h2 class="cat">Resumes</h2>'
            f'<p class="muted">No tailored resumes found in {RESUMES_DIR}.</p></div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Indeed Apply Dashboard</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; background: #f4f6f9; color: #1a1f2b; }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 28px 20px 80px; }}
  header {{ background: #2557a7; color: #fff; border-radius: 14px; padding: 22px 24px; }}
  header h1 {{ margin: 0 0 6px; font-size: 22px; }}
  header .meta {{ opacity: .9; font-size: 14px; }}
  .note {{ background: #fff8e1; border: 1px solid #ffe08a; border-radius: 12px;
          padding: 14px 18px; margin: 18px 0 8px; font-size: 15px; line-height: 1.5; }}
  .note b {{ color: #8a6d00; }}
  h2.cat {{ font-size: 16px; margin: 26px 0 12px; color: #2e3a4d; display: flex;
           align-items: center; gap: 10px; }}
  h2.cat .count {{ background: #e2e8f2; color: #4a5a72; font-size: 12px;
                  padding: 2px 9px; border-radius: 20px; font-weight: 600; }}
  .folderlink {{ font-size: 13px; font-weight: 500; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }}
  a.job {{ display: flex; flex-direction: column; gap: 6px; text-decoration: none;
          background: #fff; border: 1px solid #e3e8f0; border-radius: 12px;
          padding: 14px 16px; transition: .12s; }}
  a.job:hover {{ border-color: #2557a7; box-shadow: 0 3px 12px rgba(37,87,167,.12);
                transform: translateY(-1px); }}
  a.job .q {{ font-weight: 600; font-size: 15px; color: #1a1f2b; }}
  a.job .go {{ font-size: 13px; color: #2557a7; font-weight: 500; }}
  .resumes {{ margin-top: 30px; }}
  .resumes ul {{ list-style: none; padding: 0; margin: 0; display: grid;
                grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 6px; }}
  .resumes li a {{ display: block; background: #fff; border: 1px solid #e3e8f0;
                  border-radius: 10px; padding: 10px 14px; text-decoration: none;
                  color: #2557a7; font-size: 13px; }}
  .muted {{ color: #7a8698; }}
  footer {{ margin-top: 36px; font-size: 13px; color: #7a8698; text-align: center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Indeed Apply Dashboard</h1>
    <div class="meta">{total} searches · generated {now}</div>
  </header>

  <div class="note">
    <b>How to use:</b> Click any search below — it opens on Indeed in <b>this</b> browser
    (your real Chrome, where Indeed works). Apply to what fits, and attach the matching
    tailored resume from the list at the bottom. No robot browser, no verification wall.
  </div>

  {cards_html}

  {resume_block}

  <footer>Generated by the job pipeline · hand-off mode · v{getattr(cfg, "PIPELINE_VERSION", "?")}</footer>
</div>
</body>
</html>"""


def generate(open_browser: bool = True) -> Path:
    """Write the dashboard and (optionally) open it in the real default browser."""
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(build_html(), encoding="utf-8")
    print(f"  📄 Indeed hand-off dashboard: {HTML_PATH}")
    print(f"     {len(QUERIES)} searches ready — open it in your real browser and click through.")
    if open_browser and sys.platform == "darwin":
        try:
            # 'open' launches the user's DEFAULT browser (their real Chrome),
            # never the pipeline's automated one.
            subprocess.run(["open", str(HTML_PATH)], timeout=10)
            print("  🌐 Opened in your default browser.")
        except Exception as e:
            print(f"  ⚠  Could not auto-open ({str(e)[:60]}) — open the file above manually.")
    return HTML_PATH


if __name__ == "__main__":
    generate(open_browser=("--no-open" not in sys.argv))
