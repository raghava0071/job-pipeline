#!/bin/bash
cd ~/job_pipeline
python3 - <<'EOF'
import smtplib, os, json, glob
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime

def load_env():
    env = Path.home() / "job_pipeline" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
load_env()

NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "raghavendrakaranam30@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# Read today's run logs
runs_dir = Path.home() / "job_pipeline" / "data" / "runs"
today = "20260610"
indeed_applied = 0
linkedin_applied = 0
linkedin_jobs = []
notes = []

for f in sorted(runs_dir.glob(f"run_{today}_*.json")):
    with open(f) as fh:
        d = json.load(fh)
    if "indeed" in f.name:
        indeed_applied += d.get("applied", 0)
        if d.get("total_jobs", 0) == 0 and d.get("meta", {}).get("jobs_found", 0) > 0:
            notes.append(f"Indeed: {d['meta']['jobs_found']} jobs found but all already applied (dedup pool exhausted)")
    elif "linkedin" in f.name:
        linkedin_applied += d.get("applied", 0)
        for j in d.get("jobs", []):
            if j.get("status") == "Applied":
                linkedin_jobs.append(j)

total = indeed_applied + linkedin_applied
now = datetime.now().strftime("%b %d, %Y at %I:%M %p")

jobs_html = ""
for j in linkedin_jobs:
    jobs_html += f"""
    <tr>
      <td style="padding:6px 12px;">{j.get('title','')}</td>
      <td style="padding:6px 12px;">{j.get('company','')}</td>
      <td style="padding:6px 12px;text-align:center;">{j.get('fit_score','')}%</td>
      <td style="padding:6px 12px;">LinkedIn</td>
    </tr>"""

notes_html = ""
for n in notes:
    notes_html += f'<li style="color:#6b7280;">{n}</li>'

subject = f"📊 Job Pipeline Summary — Jun 10, 2026 | {total} apps submitted"

body_html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#f9fafb;">
<div style="background:white;border-radius:12px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
  <h2 style="margin:0 0 20px;color:#111827;">📊 Job Pipeline — Daily Summary</h2>
  <p style="color:#6b7280;margin:0 0 20px;">{now}</p>

  <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
    <tr style="background:#f3f4f6;">
      <th style="padding:10px 12px;text-align:left;">Platform</th>
      <th style="padding:10px 12px;text-align:center;">Applied</th>
      <th style="padding:10px 12px;text-align:left;">Notes</th>
    </tr>
    <tr>
      <td style="padding:10px 12px;font-weight:bold;">Indeed</td>
      <td style="padding:10px 12px;text-align:center;">{indeed_applied}</td>
      <td style="padding:10px 12px;color:#6b7280;">Dedup pool exhausted (all prior jobs)</td>
    </tr>
    <tr style="background:#f3f4f6;">
      <td style="padding:10px 12px;font-weight:bold;">LinkedIn</td>
      <td style="padding:10px 12px;text-align:center;">{linkedin_applied}</td>
      <td style="padding:10px 12px;color:#6b7280;">Morning run completed</td>
    </tr>
    <tr style="border-top:2px solid #e5e7eb;">
      <td style="padding:10px 12px;font-weight:bold;">Total</td>
      <td style="padding:10px 12px;text-align:center;font-weight:bold;font-size:18px;">{total}</td>
      <td></td>
    </tr>
  </table>

  {'<h3 style="margin:20px 0 10px;">✅ Applications Submitted</h3><table style="width:100%;border-collapse:collapse;"><tr style="background:#f3f4f6;"><th style="padding:8px 12px;text-align:left;">Title</th><th style="padding:8px 12px;text-align:left;">Company</th><th style="padding:8px 12px;text-align:center;">Fit</th><th style="padding:8px 12px;text-align:left;">Platform</th></tr>' + jobs_html + '</table>' if linkedin_jobs else ''}

  {'<h3 style="margin:20px 0 10px;">⚠️ Notes</h3><ul>' + notes_html + '</ul>' if notes else ''}

  <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:16px;margin-top:20px;">
    <p style="margin:0;color:#374151;font-size:14px;"><strong>Indeed pool exhausted:</strong> All matching Easy Apply jobs have been applied to in prior runs. Consider expanding search keywords or waiting for new postings.</p>
  </div>
</div>
</body></html>
"""

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"] = NOTIFY_EMAIL
msg["To"] = NOTIFY_EMAIL
msg.attach(MIMEText(body_html, "html"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(NOTIFY_EMAIL, GMAIL_APP_PASSWORD)
    server.sendmail(NOTIFY_EMAIL, NOTIFY_EMAIL, msg.as_string())

print(f"✅ Summary email sent to {NOTIFY_EMAIL}")
print(f"   Indeed: {indeed_applied} | LinkedIn: {linkedin_applied} | Total: {total}")
EOF
