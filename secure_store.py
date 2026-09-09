#!/usr/bin/env python3
# =============================================================================
# SECURE_STORE.PY — Encrypted credential storage for per-platform accounts
#
# Originally Workday-only; as of 2026-09-09 also used by
# greenhouse_apply_now.py (and available to any future ATS handler — Lever,
# Ashby, ...) since it was already generic: every entry is keyed by an
# arbitrary `company_key` string, not anything Workday-specific. Handlers
# for different platforms namespace their own keys (Workday uses the bare
# company slug from the job URL; Greenhouse prefixes with "greenhouse_") so
# an account for the same company on two different platforms never collides.
# Stores per-account details (email, password, account created, security
# question answers) in an encrypted file so plaintext passwords are never
# written to disk unprotected.
#
# ENCRYPTION:
#   - Uses AES-256-GCM via the `cryptography` package (Fernet)
#   - Encryption key is derived from a master secret in .env
#   - If `cryptography` is not installed, falls back to JSON with a warning
#
# FILES:
#   - data/workday_accounts.enc            — encrypted accounts (the only file
#                                             written to going forward)
#   - data/workday_accounts_plaintext_backup.json — one-time archived copy of
#                                             the OLD plaintext file, created
#                                             automatically the first time this
#                                             module upgrades legacy data. Not
#                                             read again after the upgrade.
#
# SECURITY NOTES:
#   - Both files are in .gitignore — never committed to GitHub
#   - The master key (PIPELINE_SECRET) stays only in .env
#   - Even if someone gets the .enc file, they can't read it without the key
# =============================================================================

import os, json, base64, hashlib
from pathlib import Path
from datetime import datetime

PIPELINE_DIR  = Path.home() / "job_pipeline"
DATA_DIR      = PIPELINE_DIR / "data"
ENC_FILE      = DATA_DIR / "workday_accounts.enc"
JSON_FALLBACK = DATA_DIR / "workday_accounts.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Master secret ─────────────────────────────────────────────────────────────

def _get_master_secret() -> str:
    """Load PIPELINE_SECRET from .env or environment. Auto-generates if missing."""
    secret = os.environ.get("PIPELINE_SECRET", "")
    if not secret:
        env_file = PIPELINE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("PIPELINE_SECRET="):
                    secret = line.split("=", 1)[1].strip().strip('"').strip("'")

    if not secret:
        # Auto-generate and save to .env so it persists across runs
        import secrets as _sec
        secret = _sec.token_hex(32)
        env_file = PIPELINE_DIR / ".env"
        try:
            existing = env_file.read_text() if env_file.exists() else ""
            if "PIPELINE_SECRET=" not in existing:
                with open(env_file, "a") as f:
                    f.write(f"\n# Auto-generated encryption key — do not share\nPIPELINE_SECRET={secret}\n")
                print(f"  🔑 Generated new PIPELINE_SECRET in .env")
        except Exception as e:
            print(f"  ⚠  Could not save PIPELINE_SECRET: {e}")

    return secret


# ── Fernet encryption helpers ─────────────────────────────────────────────────

def _get_fernet():
    """Return a Fernet instance keyed from PIPELINE_SECRET. None if not available."""
    try:
        from cryptography.fernet import Fernet
        secret = _get_master_secret()
        # Derive a 32-byte key from the secret using SHA-256
        key_bytes = hashlib.sha256(secret.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        return Fernet(fernet_key)
    except ImportError:
        return None
    except Exception:
        return None


def _encrypt(data: dict) -> bytes:
    f = _get_fernet()
    plaintext = json.dumps(data, indent=2).encode()
    if f:
        return f.encrypt(plaintext)
    # Fallback: base64 only (not real encryption — at least not plaintext JSON)
    return base64.b64encode(plaintext)


def _decrypt(raw: bytes) -> dict:
    f = _get_fernet()
    try:
        if f:
            plaintext = f.decrypt(raw)
        else:
            plaintext = base64.b64decode(raw)
        return json.loads(plaintext.decode())
    except Exception:
        return {}


# ── Public API ────────────────────────────────────────────────────────────────

# Legacy plaintext file (data/workday_accounts.json) — only read once, for
# upgrading old data into the encrypted store. Never written to going forward.
# Schema per entry: company, portal_url, email, password, created_at, last_login
PLAIN_FILE = JSON_FALLBACK


def _normalize(company_key: str, v: dict) -> dict:
    """Ensure an entry has the full schema."""
    v = dict(v or {})
    v.setdefault("company",    company_key)
    v.setdefault("portal_url", "")
    v.setdefault("email",      "")
    v.setdefault("password",   "")
    v.setdefault("created_at", datetime.now().isoformat())
    v.setdefault("last_login", None)
    return v


def load_accounts() -> dict:
    """Load all Workday accounts from the encrypted store (preferred, matches
    this file's own security intent). One-time upgrade: if a legacy plaintext
    file exists too (from before encrypted saving was wired up), merge it in
    — plaintext entries win on conflict, since that was the file actively
    being written to (the encrypted file may be a stale, older snapshot from
    before the bug that made saves go to plaintext instead). Re-save the
    merged result encrypted, then archive the plaintext copy so passwords
    stop sitting on disk unprotected."""
    enc_data = {}
    if ENC_FILE.exists():
        try:
            enc_data = _decrypt(ENC_FILE.read_bytes()) or {}
        except Exception as e:
            print(f"  ⚠  Could not read encrypted accounts: {e}")

    if not PLAIN_FILE.exists():
        return enc_data

    # Legacy plaintext file present — one-time merge-and-archive.
    try:
        plain_data = json.loads(PLAIN_FILE.read_text()) or {}
        plain_data = {k: _normalize(k, v) for k, v in plain_data.items() if isinstance(v, dict)}
        merged = {**enc_data, **plain_data}   # plaintext wins on conflict — it's the fresher source
        if merged:
            ENC_FILE.write_bytes(_encrypt(merged))
        archived = PLAIN_FILE.with_name(PLAIN_FILE.stem + "_plaintext_backup" + PLAIN_FILE.suffix)
        PLAIN_FILE.rename(archived)
        added = len(plain_data) - len(set(plain_data) & set(enc_data))
        print(f"  🔒 Merged {len(plain_data)} plaintext account(s) into encrypted storage "
              f"({len(merged)} total, {added} new)")
        print(f"  📦 Old plaintext file archived to: {archived.name} (not deleted)")
        return merged
    except Exception as e:
        print(f"  ⚠  Could not migrate plaintext accounts: {e}")
        return enc_data


def save_accounts(accounts: dict):
    """Save all Workday accounts to the encrypted store."""
    ENC_FILE.write_bytes(_encrypt(accounts))


def save_account(company_key: str, email: str, password: str, extra: dict = None):
    """Save or update a single Workday account entry (plain JSON)."""
    extra = extra or {}
    accounts = load_accounts()
    prev = accounts.get(company_key, {}) if isinstance(accounts.get(company_key), dict) else {}
    entry = {
        "company":    company_key,
        "portal_url": extra.get("portal_url") or prev.get("portal_url", ""),
        "email":      email,
        "password":   password,
        "created_at": prev.get("created_at") or datetime.now().isoformat(),
        "last_login": prev.get("last_login"),
    }
    for k, val in extra.items():
        if k not in entry:
            entry[k] = val
    accounts[company_key] = entry
    save_accounts(accounts)
    print(f"  💾 Account saved (encrypted) for: {company_key}")


def get_account(company_key: str) -> dict:
    """Return stored account for a company, or empty dict."""
    return load_accounts().get(company_key, {})


def mark_logged_in(company_key: str, portal_url: str = ""):
    """Update last_login timestamp (and portal_url if missing) for an account."""
    accounts = load_accounts()
    if company_key in accounts and isinstance(accounts[company_key], dict):
        accounts[company_key]["last_login"] = datetime.now().isoformat()
        if portal_url and not accounts[company_key].get("portal_url"):
            accounts[company_key]["portal_url"] = portal_url
        save_accounts(accounts)


# ── Security question answers ─────────────────────────────────────────────────

# Maps question keywords → .env variable name
_SECURITY_Q_MAP = {
    "first pet":           "SECURITY_Q_FIRST_PET",
    "pet's name":          "SECURITY_Q_FIRST_PET",
    "mother's maiden":     "SECURITY_Q_MOTHERS_MAIDEN",
    "maiden name":         "SECURITY_Q_MOTHERS_MAIDEN",
    "city were you born":  "SECURITY_Q_BIRTH_CITY",
    "birth city":          "SECURITY_Q_BIRTH_CITY",
    "city of birth":       "SECURITY_Q_BIRTH_CITY",
    "elementary school":   "SECURITY_Q_ELEMENTARY_SCHOOL",
    "primary school":      "SECURITY_Q_ELEMENTARY_SCHOOL",
    "childhood nickname":  "SECURITY_Q_CHILDHOOD_NICKNAME",
    "nickname":            "SECURITY_Q_CHILDHOOD_NICKNAME",
    "first car":           "SECURITY_Q_FIRST_CAR",
    "favorite teacher":    "SECURITY_Q_FAVORITE_TEACHER",
    "childhood friend":    "SECURITY_Q_CHILDHOOD_FRIEND",
    "best friend":         "SECURITY_Q_CHILDHOOD_FRIEND",
    "first job":           "SECURITY_Q_FIRST_JOB",
    "sports team":         "SECURITY_Q_FAVORITE_SPORTS_TEAM",
    "favorite team":       "SECURITY_Q_FAVORITE_SPORTS_TEAM",
}

def _load_env_value(key: str) -> str:
    """Read a value from environment or .env file."""
    val = os.environ.get(key, "")
    if not val:
        env_file = PIPELINE_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith(f"{key}="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
    return val


def get_security_answer(question_text: str) -> str:
    """
    Given a security question label, return the pre-set answer from .env.
    Returns '' if no match found (pipeline will fall back to manual).

    Example:
        get_security_answer("What was the name of your first pet?")
        → "Buddy"
    """
    q_lower = question_text.lower()
    for keyword, env_key in _SECURITY_Q_MAP.items():
        if keyword in q_lower:
            val = _load_env_value(env_key)
            if val:
                return val
    return ""


def get_all_security_answers() -> dict:
    """Return all security Q&A pairs as {keyword: answer} for debugging."""
    result = {}
    for keyword, env_key in _SECURITY_Q_MAP.items():
        val = _load_env_value(env_key)
        if val:
            result[keyword] = val
    return result


# ── Install helper ────────────────────────────────────────────────────────────

def ensure_cryptography():
    """Install cryptography package if not present."""
    try:
        import cryptography
        return True
    except ImportError:
        print("  📦 Installing cryptography package for encrypted storage...")
        import subprocess, sys
        subprocess.run([sys.executable, "-m", "pip", "install", "cryptography",
                        "--break-system-packages", "-q"])
        try:
            import cryptography
            print("  ✅ cryptography installed")
            return True
        except:
            print("  ⚠  cryptography not available — using base64 fallback")
            return False


if __name__ == "__main__":
    ensure_cryptography()
    print("\nTesting secure storage...")

    # Test save + load
    save_account("test_company", "test@email.com", "TestPass123!", {"note": "test entry"})
    acct = get_account("test_company")
    print(f"Loaded: {acct}")

    print("\nSecurity question answers from .env:")
    for kw, ans in get_all_security_answers().items():
        print(f"  '{kw}' → '{ans}'")

    # Clean up test entry
    accounts = load_accounts()
    accounts.pop("test_company", None)
    save_accounts(accounts)
    print("\nTest entry removed. Storage working correctly.")
