#!/usr/bin/env python3
# =============================================================================
# MAIL_READER.PY — Gmail IMAP reader for automatic OTP / verification fetching
#
# Used by workday_apply_now.py to fully automate Workday account verification.
#
# HOW IT WORKS:
#   1. Connects to Gmail via IMAP (SSL, port 993)
#   2. Searches INBOX for recent emails from Workday/verification senders
#   3. Extracts 4-8 digit OTP codes from email body using regex
#   4. Returns the code so the pipeline can auto-type it into the browser
#
# REQUIRES in .env:
#   GMAIL_USER=raghavendrakaranam30@gmail.com
#   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   (same one used by notifier.py)
#
# NO extra packages needed — uses Python's built-in imaplib + email modules.
# =============================================================================

import imaplib
import email
import re
import time
import os
from pathlib import Path
from datetime import datetime, timezone
from email.header import decode_header

PIPELINE_DIR = Path.home() / "job_pipeline"


# ── Credentials ───────────────────────────────────────────────────────────────

def _get_gmail_creds() -> tuple:
    """Return (gmail_user, app_password) from env or .env file."""
    user = os.environ.get("GMAIL_USER", "") or os.environ.get("CANDIDATE_EMAIL", "")
    pwd  = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not user or not pwd:
        env_file = PIPELINE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("GMAIL_USER=") or line.startswith("CANDIDATE_EMAIL="):
                    if not user:
                        user = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("GMAIL_APP_PASSWORD="):
                    if not pwd:
                        pwd = line.split("=", 1)[1].strip().strip('"').strip("'")

    return user, pwd


# ── IMAP connection ───────────────────────────────────────────────────────────

def _connect_imap():
    """Open authenticated IMAP connection to Gmail."""
    user, pwd = _get_gmail_creds()
    if not user or not pwd:
        raise ValueError("Gmail credentials not found. Set GMAIL_USER and GMAIL_APP_PASSWORD in .env")

    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(user, pwd)
    return mail


# ── Email body extraction ─────────────────────────────────────────────────────

def _get_email_body(msg) -> str:
    """Extract plain-text body from email.message.Message object."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    body += part.get_payload(decode=True).decode(charset, errors="replace")
                except:
                    pass
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            body = msg.get_payload(decode=True).decode(charset, errors="replace")
        except:
            pass
    return body


def _decode_subject(msg) -> str:
    subject = msg.get("Subject", "")
    parts = decode_header(subject)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded)


# ── OTP extraction ────────────────────────────────────────────────────────────

# Patterns that extract the actual code from email body text
_OTP_REGEXES = [
    # "Your verification code is 123456"
    r'(?:verification|security|one.time|otp|confirmation|access)\s+code\s+(?:is\s+)?:?\s*([0-9]{4,8})',
    # "Code: 123456" or "Code - 123456"
    r'\bcode[\s:–\-]+([0-9]{4,8})\b',
    # "123456 is your code"
    r'\b([0-9]{4,8})\s+is\s+your\s+(?:verification|security|one.time|otp|confirmation)?\s*code',
    # Large standalone number on its own line (common Workday format)
    r'^\s*([0-9]{6,8})\s*$',
    # "Enter: 123456" or "use code: 123456"
    r'(?:enter|use)\s+(?:code\s*:?\s*)?([0-9]{4,8})',
    # Bold/spaced format: "1 2 3 4 5 6"
    r'\b([0-9]\s){5}[0-9]\b',
]

def extract_otp_from_body(body: str) -> str:
    """Extract OTP code from email body text. Returns '' if not found."""
    for pattern in _OTP_REGEXES:
        m = re.search(pattern, body, re.IGNORECASE | re.MULTILINE)
        if m:
            # Remove spaces if it was a spaced format like "1 2 3 4 5 6"
            code = m.group(1) if m.lastindex else m.group(0)
            code = code.replace(" ", "").strip()
            if 4 <= len(code) <= 8 and code.isdigit():
                return code
    return ""


# ── Verification link extraction ──────────────────────────────────────────────

def extract_verify_link(body: str) -> str:
    """Extract email verification URL from email body."""
    # Look for Workday verification links
    patterns = [
        r'https?://[^\s<>"]+workday[^\s<>"]+(?:verify|confirm|activate)[^\s<>"]*',
        r'https?://[^\s<>"]+(?:verify|confirm|activate)[^\s<>"]+workday[^\s<>"]*',
        r'https?://wd\d+\.myworkdayjobs\.com[^\s<>"]+',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            return m.group(0).rstrip(".,;)")
    return ""


# ── Main polling function ─────────────────────────────────────────────────────

# Senders that Workday verification emails come from
WORKDAY_SENDERS = [
    "workday", "myworkday", "no-reply@myworkday", "noreply@workday",
    "no-reply@wd", "recruiting@", "talent@", "careers@",
    "hr@", "jobs@", "donotreply@workday",
]

VERIFICATION_SUBJECTS = [
    "verify", "verification", "confirm", "activate", "one-time",
    "otp", "code", "security", "access code", "sign in",
]

def wait_for_otp(
    company: str = "",
    timeout_secs: int = 300,
    poll_interval: int = 5,
    since_minutes: int = 5,
) -> dict:
    """
    Poll Gmail INBOX every `poll_interval` seconds for up to `timeout_secs`.
    Returns dict with keys:
        type:  'otp' | 'verify_link' | 'none'
        code:  '123456'  (if type == 'otp')
        link:  'https://...'  (if type == 'verify_link')
        from:  sender email
        subject: email subject

    Usage:
        result = wait_for_otp(company="Amazon", timeout_secs=300)
        if result['type'] == 'otp':
            print(f"Code: {result['code']}")
        elif result['type'] == 'verify_link':
            page.goto(result['link'])
    """
    print(f"          📬 Waiting for Workday email (up to {timeout_secs//60}m {timeout_secs%60}s)...")

    start = time.time()
    last_print = 0

    while time.time() - start < timeout_secs:
        try:
            mail = _connect_imap()
            mail.select("INBOX")

            # Search for recent unseen emails (last `since_minutes` minutes)
            # IMAP date format: "01-Jan-2024"
            from datetime import timedelta
            since_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
            imap_date = since_dt.strftime("%d-%b-%Y")

            # Search for unseen emails since today
            typ, data = mail.search(None, f'(UNSEEN SINCE "{imap_date}")')

            if typ == "OK" and data and data[0]:
                msg_ids = data[0].split()
                # Check newest first
                for msg_id in reversed(msg_ids):
                    typ2, msg_data = mail.fetch(msg_id, "(RFC822)")
                    if typ2 != "OK":
                        continue
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    sender  = msg.get("From", "").lower()
                    subject = _decode_subject(msg).lower()
                    body    = _get_email_body(msg)

                    # Filter: must look like a Workday/verification email
                    is_workday_sender = any(s in sender for s in WORKDAY_SENDERS)
                    is_verification   = any(kw in subject for kw in VERIFICATION_SUBJECTS)

                    # Also accept if company name is in sender or subject
                    if company:
                        co_lower = company.lower()
                        is_workday_sender = is_workday_sender or co_lower in sender
                        is_verification   = is_verification   or co_lower in subject

                    if not (is_workday_sender or is_verification):
                        continue

                    print(f"          📨 Email found: From={sender[:40]}  Subject={subject[:50]}")

                    # Try OTP first
                    otp = extract_otp_from_body(body)
                    if otp:
                        mail.logout()
                        print(f"          🔑 OTP extracted: {otp}")
                        return {"type": "otp", "code": otp, "from": sender, "subject": subject}

                    # Try verify link
                    link = extract_verify_link(body)
                    if link:
                        mail.logout()
                        print(f"          🔗 Verify link extracted: {link[:70]}")
                        return {"type": "verify_link", "link": link, "from": sender, "subject": subject}

                    # Email found but couldn't extract code/link — mark as seen and skip
                    print(f"          ⚠  Found email but couldn't extract OTP or link — waiting for next email")

            mail.logout()

        except imaplib.IMAP4.error as e:
            print(f"          ⚠  IMAP error: {e} — retrying...")
        except Exception as e:
            print(f"          ⚠  Mail check error: {e}")

        # Progress update every 30 seconds
        elapsed = int(time.time() - start)
        if elapsed - last_print >= 30:
            remaining = timeout_secs - elapsed
            print(f"          ⏳ No email yet... {remaining//60}m {remaining%60}s remaining")
            last_print = elapsed

        time.sleep(poll_interval)

    print(f"          ❌ No OTP email received within {timeout_secs//60} minutes")
    return {"type": "none", "code": "", "link": ""}


def test_connection() -> bool:
    """Quick test — returns True if Gmail IMAP connects successfully."""
    try:
        mail = _connect_imap()
        mail.select("INBOX")
        mail.logout()
        print("  ✅ Gmail IMAP connection successful")
        return True
    except Exception as e:
        print(f"  ❌ Gmail IMAP failed: {e}")
        print(f"     Make sure GMAIL_APP_PASSWORD is set in .env")
        print(f"     Get one at: https://myaccount.google.com/apppasswords")
        return False


if __name__ == "__main__":
    # Test mode — run directly to verify credentials work
    print("Testing Gmail IMAP connection...")
    if test_connection():
        print("\nListening for OTP emails (30 second test)...")
        result = wait_for_otp(company="test", timeout_secs=30, since_minutes=60)
        print(f"\nResult: {result}")
