#!/usr/bin/env python3
# =============================================================================
# NOTIFIER.PY — Gmail notification + resume attachment after each application
#
# Each email includes:
#   - Company name, job title, fit score, platform
#   - The tailored resume .docx attached so you can review it instantly
#   - Cover letter attached if available
#   - Job URL link
#   - Screenshot attachment if available
#
# SETUP (one time only):
#   1. Go to https://myaccount.google.com/apppasswords
#   2. Sign in → Select App: Mail → Select Device: Mac → Generate
#   3. Copy the 16-character password (format: xxxx xxxx xxxx xxxx)
#   4. Add these two lines to ~/job_pipeline/.env:
#        NOTIFY_EMAIL=raghavendrakaranam30@gmail.com
#        GMAIL_APP_PASSWORD=zrqq mkrt movj zekm
#
# Test:  python ~/job_pipeline/notifier.py
# =============================================================================

import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from datetime import datetime

# ── Load .env ──────────────────────────────────────────────────────────────────
def _load_env():
    env = Path.home() / "job_pipeline" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

NOTIFY_EMAIL       = os.environ.get("NOTIFY_EMAIL", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ENABLED = bool(NOTIFY_EMAIL and GMAIL_APP_PASSWORD)


def _attach_file(msg: MIMEMultipart, file_path: str, label: str = "") -> bool:
    """Attach a file to an email. Returns True if attached."""
    try:
        p = Path(file_path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        with open(p, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = label or p.name
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)
        return True
    except Exception:
        return False


def notify_applied(title: str, company: str, fit_score: int,
                   resume_path: str = "", cover_letter_path: str = "",
                   platform: str = "LinkedIn", job_url: str = "",
                   screenshot_path: str = "") -> bool:
    """
    Send Gmail notification with resume + cover letter attached.
    Returns True if sent successfully.
    """
    if not ENABLED:
        return False

    try:
        # Score visuals
        score_bar = "█" * (fit_score // 10) + "░" * (10 - fit_score // 10)
        grade     = "A" if fit_score >= 85 else "B" if fit_score >= 70 else "C" if fit_score >= 65 else "D"
        color     = "#22c55e" if fit_score >= 85 else "#3b82f6" if fit_score >= 70 else "#f59e0b"
        now       = datetime.now().strftime("%b %d, %Y at %I:%M %p")
        resume_name = Path(resume_path).name if resume_path else "Profile resume"

        subject = f"✅ Applied: {title} @ {company}  [{fit_score}% {grade}]"

        url_row = f"""
  <tr>
    <td style="padding:8px 12px;font-weight:bold;color:#6b7280;width:130px;">Job URL</td>
    <td style="padding:8px 12px;"><a href="{job_url}" style="color:#0a66c2;">{job_url[:60]}...</a></td>
  </tr>""" if job_url else ""

        body_html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;background:#f9fafb;">

<div style="background:white;border-radius:12px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

  <h2 style="margin:0 0 20px;color:#111827;">✅ Application Submitted</h2>

  <table style="width:100%;border-collapse:collapse;font-size:15px;">
    <tr style="background:#f3f4f6;">
      <td style="padding:10px 12px;font-weight:bold;color:#6b7280;width:130px;">Role</td>
      <td style="padding:10px 12px;font-size:17px;font-weight:bold;color:#111827;">{title}</td>
    </tr>
    <tr>
      <td style="padding:10px 12px;font-weight:bold;color:#6b7280;">Company</td>
      <td style="padding:10px 12px;color:#111827;">{company}</td>
    </tr>
    <tr style="background:#f3f4f6;">
      <td style="padding:10px 12px;font-weight:bold;color:#6b7280;">Platform</td>
      <td style="padding:10px 12px;color:#111827;">{platform}</td>
    </tr>
    <tr>
      <td style="padding:10px 12px;font-weight:bold;color:#6b7280;">Fit Score</td>
      <td style="padding:10px 12px;">
        <span style="font-family:monospace;color:{color};font-size:13px;">[{score_bar}]</span>
        &nbsp;<strong style="color:{color};">{fit_score}% &nbsp;Grade: {grade}</strong>
      </td>
    </tr>
    <tr style="background:#f3f4f6;">
      <td style="padding:10px 12px;font-weight:bold;color:#6b7280;">Resume</td>
      <td style="padding:10px 12px;color:#374151;font-size:13px;">📎 {resume_name}<br>
        <span style="color:#9ca3af;font-size:12px;">(attached to this email)</span>
      </td>
    </tr>
    {url_row}
    <tr style="background:#f3f4f6;">
      <td style="padding:10px 12px;font-weight:bold;color:#6b7280;">Applied at</td>
      <td style="padding:10px 12px;color:#6b7280;">{now}</td>
    </tr>
  </table>

  <div style="margin-top:20px;padding:14px;background:#f0fdf4;border-radius:8px;border-left:4px solid #22c55e;">
    <p style="margin:0;color:#166534;font-size:14px;">
      📎 Your tailored resume is attached — open it to see exactly what was submitted.
    </p>
  </div>

  <p style="margin-top:24px;color:#9ca3af;font-size:12px;text-align:center;">
    Sent by your Job Pipeline ·
    <a href="https://github.com/raghawa0071/job-pipeline" style="color:#0a66c2;">GitHub</a>
  </p>
</div>

</body></html>
"""

        msg = MIMEMultipart("mixed")
        msg["Subject"] = subject
        msg["From"]    = NOTIFY_EMAIL
        msg["To"]      = NOTIFY_EMAIL
        msg.attach(MIMEText(body_html, "html"))

        # Attach resume
        if resume_path:
            _attach_file(msg, resume_path, Path(resume_path).name)

        # Attach cover letter if exists
        if cover_letter_path:
            _attach_file(msg, cover_letter_path, Path(cover_letter_path).name)

        # Attach screenshot if exists
        if screenshot_path:
            _attach_file(msg, screenshot_path, Path(screenshot_path).name)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(NOTIFY_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(NOTIFY_EMAIL, NOTIFY_EMAIL, msg.as_string())

        print(f"          📧 Email sent → {NOTIFY_EMAIL}  (resume attached ✅)")
        return True

    except Exception as e:
        print(f"          📧 Email skipped: {e}")
        return False


def notify_session_done(applied: int, scored: int, skipped: int) -> bool:
    """Send session summary email when pipeline finishes."""
    if not ENABLED or applied == 0:
        return False
    try:
        now     = datetime.now().strftime("%b %d, %Y at %I:%M %p")
        subject = f"📊 Pipeline done — {applied} applied, {scored} scored · {now}"
        body    = f"""
<html><body style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:24px;">
<div style="background:white;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
  <h2 style="color:#0a66c2;">📊 Pipeline Session Done</h2>
  <p style="font-size:18px;"><strong>{applied}</strong> applications submitted</p>
  <p><strong>{scored}</strong> jobs scored by Claude</p>
  <p><strong>{skipped}</strong> jobs skipped (below 65% gate or senior role)</p>
  <p style="color:#9ca3af;font-size:13px;">Finished {now}</p>
</div>
</body></html>
"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = NOTIFY_EMAIL
        msg["To"]      = NOTIFY_EMAIL
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(NOTIFY_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(NOTIFY_EMAIL, NOTIFY_EMAIL, msg.as_string())
        return True
    except Exception:
        return False


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not ENABLED:
        print()
        print("  ⚠  Email notifications are NOT configured yet.")
        print()
        print("  To enable, add these 2 lines to ~/job_pipeline/.env:")
        print()
        print("    NOTIFY_EMAIL=raghavendrakaranam30@gmail.com")
        print("    GMAIL_APP_PASSWORD=zrqq mkrt movj zekm")
        print()
        print("  Get your App Password here:")
        print("  → https://myaccount.google.com/apppasswords")
        print("  → App: Mail  |  Device: Mac  |  Click Generate")
        print()
    else:
        print(f"  Testing notification to {NOTIFY_EMAIL} ...")
        ok = notify_applied(
            title="Data Engineer",
            company="Test Company Inc.",
            fit_score=82,
            platform="LinkedIn",
            job_url="https://linkedin.com/jobs/view/test"
        )
        print("  ✅ Sent!" if ok else "  ❌ Failed — check App Password in .env")
