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
#        NOTIFY_EMAIL=your_notify_email@gmail.com
#        GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
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
        # Score visuals — cast to int so string multiplication works for both int and float scores
        fit_score = int(fit_score)
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
        import traceback
        print(f"          📧 ❌ Email FAILED: {e}")
        print(f"          📧    {traceback.format_exc().splitlines()[-1]}")
        return False


def notify_session_done(applied: int, scored: int, skipped: int,
                        api_cost_summary: str = "") -> bool:
    """Send session summary email when pipeline finishes."""
    if not ENABLED or applied == 0:
        return False
    try:
        now     = datetime.now().strftime("%b %d, %Y at %I:%M %p")
        subject = f"📊 Pipeline done — {applied} applied, {scored} scored · {now}"

        cost_row = ""
        if api_cost_summary:
            cost_row = f"""
  <tr style="background:#f0fdf4;">
    <td colspan="2" style="padding:10px 14px;font-size:13px;color:#166534;">
      💰 <b>API cost this run:</b> {api_cost_summary}
    </td>
  </tr>"""

        body    = f"""
<html><body style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;">
<div style="background:white;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">

  <h2 style="color:#0a66c2;margin:0 0 18px;">📊 Pipeline Session Done</h2>

  <table style="width:100%;border-collapse:collapse;font-size:15px;">
    <tr style="background:#f3f4f6;">
      <td style="padding:10px 14px;color:#6b7280;width:160px;"><b>Applied</b></td>
      <td style="padding:10px 14px;font-size:20px;font-weight:bold;color:#0a66c2;">{applied}</td>
    </tr>
    <tr>
      <td style="padding:10px 14px;color:#6b7280;"><b>Scored by Claude</b></td>
      <td style="padding:10px 14px;color:#374151;">{scored}</td>
    </tr>
    <tr style="background:#f3f4f6;">
      <td style="padding:10px 14px;color:#6b7280;"><b>Skipped</b></td>
      <td style="padding:10px 14px;color:#374151;">{skipped}
        <span style="font-size:12px;color:#9ca3af;">(below threshold or senior role)</span>
      </td>
    </tr>
    {cost_row}
    <tr>
      <td style="padding:10px 14px;color:#6b7280;"><b>Finished</b></td>
      <td style="padding:10px 14px;color:#9ca3af;font-size:13px;">{now}</td>
    </tr>
  </table>

  <p style="margin-top:16px;font-size:13px;color:#9ca3af;text-align:center;">
    Check your inbox for per-job emails with resumes attached ·
    <a href="https://github.com/raghawa0071/job-pipeline" style="color:#0a66c2;">GitHub</a>
  </p>

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


def send_captcha_alert(title: str, company: str, job_url: str = "") -> bool:
    """
    Send a CAPTCHA alert email.
    The CAPTCHA lives in the Playwright browser on the Mac — it cannot be solved
    from a phone or any other device. The email tells the user which job triggered
    it and instructs them to switch to the Mac browser to solve it there.
    Pipeline waits up to 10 minutes.
    """
    if not ENABLED:
        return False
    try:
        _load_env()
        now = datetime.now().strftime("%b %d at %I:%M %p")
        subject_line = f"🚨 CAPTCHA — {title} @ {company} — Open your Mac browser NOW"

        # Job reference block (read-only — do NOT tap to solve, just for context)
        job_ref = ""
        if job_url:
            job_ref = f"""
  <div style="background:#f3f4f6;border-radius:8px;padding:12px;margin:16px 0;">
    <p style="margin:0 0 6px;font-size:13px;color:#6b7280;font-weight:bold;">JOB REFERENCE (for context only):</p>
    <p style="margin:0;font-size:12px;color:#374151;word-break:break-all;">{job_url}</p>
  </div>"""

        html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:20px;">

<div style="background:#fff3cd;border:3px solid #f59e0b;border-radius:14px;padding:24px;">

  <h2 style="color:#92400e;margin:0 0 12px;">🚨 CAPTCHA Detected — Action Required on Mac</h2>

  <table style="width:100%;font-size:15px;border-collapse:collapse;">
    <tr><td style="padding:6px 0;color:#6b7280;width:90px;"><b>Job</b></td>
        <td style="padding:6px 0;color:#111;"><b>{title}</b></td></tr>
    <tr><td style="padding:6px 0;color:#6b7280;"><b>Company</b></td>
        <td style="padding:6px 0;color:#111;">{company}</td></tr>
    <tr><td style="padding:6px 0;color:#6b7280;"><b>Time</b></td>
        <td style="padding:6px 0;color:#6b7280;">{now}</td></tr>
  </table>

  {job_ref}

  <div style="background:#fef9c3;border-radius:8px;padding:14px;margin-top:14px;">
    <p style="margin:0;font-size:15px;color:#713f12;">
      <b>⚠️ You MUST solve this on your Mac — not your phone.</b><br><br>
      The CAPTCHA is running inside the pipeline's browser window on your Mac.
      Opening the link on your phone opens a separate session and does <u>nothing</u>
      to the CAPTCHA the pipeline is waiting on.<br><br>
      <b>Steps:</b><br>
      1. Go to your Mac<br>
      2. Open the Chromium/pipeline browser window (check Dock)<br>
      3. Solve the reCAPTCHA checkbox that's on screen<br>
      4. Pipeline will continue automatically<br><br>
      <span style="color:#92400e;">⏳ If not solved in 10 minutes, this job is skipped and the pipeline moves on.</span>
    </p>
  </div>

</div>
</body></html>"""

        msg = MIMEMultipart("alternative")
        msg["From"]    = NOTIFY_EMAIL
        msg["To"]      = NOTIFY_EMAIL
        msg["Subject"] = subject_line
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(NOTIFY_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(NOTIFY_EMAIL, NOTIFY_EMAIL, msg.as_string())

        print(f"          📧 CAPTCHA alert sent → {NOTIFY_EMAIL} (with job link ✅)")
        return True
    except Exception as e:
        print(f"          📧 CAPTCHA alert failed: {e}")
        return False


def send_ai_interview_alert(title: str, company: str, interview_url: str, job_url: str = "") -> bool:
    """
    Alert email when Indeed shows an AI interview prompt after application submission.
    Application is already submitted — AI interview is optional/extra.
    Sends the direct interview link so the user can complete it later.
    """
    if not ENABLED:
        return False
    try:
        _load_env()
        now = datetime.now().strftime("%b %d at %I:%M %p")
        subject_line = f"🎤 AI Interview Ready — {title} @ {company} — Complete when you can"

        interview_btn = ""
        if interview_url:
            interview_btn = f"""
  <a href="{interview_url}"
     style="display:block;margin:20px 0;padding:16px;background:#16a34a;color:white;
            text-decoration:none;border-radius:10px;font-size:17px;font-weight:bold;
            text-align:center;">
    🎤 Start AI Interview
  </a>
  <p style="font-size:12px;color:#6b7280;word-break:break-all;">
    Direct link: {interview_url}
  </p>"""

        job_ref = ""
        if job_url and job_url != interview_url:
            job_ref = f'<p style="font-size:12px;color:#6b7280;">Job posting: <a href="{job_url}">{job_url[:80]}</a></p>'

        html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:20px;">

<div style="background:#f0fdf4;border:3px solid #16a34a;border-radius:14px;padding:24px;">

  <h2 style="color:#15803d;margin:0 0 12px;">✅ Applied + 🎤 AI Interview Pending</h2>

  <table style="width:100%;font-size:15px;border-collapse:collapse;">
    <tr><td style="padding:6px 0;color:#6b7280;width:90px;"><b>Job</b></td>
        <td style="padding:6px 0;color:#111;"><b>{title}</b></td></tr>
    <tr><td style="padding:6px 0;color:#6b7280;"><b>Company</b></td>
        <td style="padding:6px 0;color:#111;">{company}</td></tr>
    <tr><td style="padding:6px 0;color:#6b7280;"><b>Time</b></td>
        <td style="padding:6px 0;color:#6b7280;">{now}</td></tr>
  </table>

  {interview_btn}
  {job_ref}

  <div style="background:#dcfce7;border-radius:8px;padding:14px;margin-top:14px;">
    <p style="margin:0;font-size:14px;color:#14532d;">
      <b>✅ Your application was submitted successfully.</b><br><br>
      Indeed also wants you to complete a short AI interview (recorded video/audio responses).
      This is optional but increases your chances — complete it when you have 10–15 minutes.<br><br>
      The pipeline saved the link above and skipped the interview so it could keep applying to more jobs.
    </p>
  </div>

</div>
</body></html>"""

        msg = MIMEMultipart("alternative")
        msg["From"]    = NOTIFY_EMAIL
        msg["To"]      = NOTIFY_EMAIL
        msg["Subject"] = subject_line
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(NOTIFY_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(NOTIFY_EMAIL, NOTIFY_EMAIL, msg.as_string())

        print(f"          📧 AI interview alert sent → {NOTIFY_EMAIL}")
        return True
    except Exception as e:
        print(f"          📧 AI interview alert failed: {e}")
        return False


def send_alert(subject: str, body: str) -> bool:
    """Send a plain-text alert email — used for CAPTCHA and other urgent pipeline events."""
    if not ENABLED:
        print(f"          📧 Email not configured — alert skipped: {subject}")
        return False
    try:
        _load_env()
        msg = MIMEMultipart("alternative")
        msg["From"]    = NOTIFY_EMAIL
        msg["To"]      = NOTIFY_EMAIL
        msg["Subject"] = subject

        html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:24px;">
<div style="background:#fff3cd;border:2px solid #ffc107;border-radius:12px;padding:24px;">
  <h2 style="color:#856404;">⚠️ Pipeline Alert</h2>
  <pre style="font-family:Arial,sans-serif;white-space:pre-wrap;font-size:14px;">{body}</pre>
</div>
</body></html>"""

        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(NOTIFY_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(NOTIFY_EMAIL, NOTIFY_EMAIL, msg.as_string())

        print(f"          📧 Alert sent → {NOTIFY_EMAIL}: {subject}")
        return True
    except Exception as e:
        print(f"          📧 Alert email failed: {e}")
        return False


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not ENABLED:
        print()
        print("  ⚠  Email notifications are NOT configured yet.")
        print()
        print("  To enable, add these 2 lines to ~/job_pipeline/.env:")
        print()
        print("    NOTIFY_EMAIL=your_notify_email@gmail.com")
        print("    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx")
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
