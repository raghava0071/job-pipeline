# Job Pipeline — Afternoon Run Error Report
**Date:** June 10, 2026 — Afternoon scheduled run

## Summary

| Step | Result |
|------|--------|
| Cache seed (`seed_cache.py`) | ✅ Success — 313 answers seeded, 294 cached |
| Indeed (50 apps) | ❌ 0 applications — network blocked |
| LinkedIn (50 apps) | ❌ 0 applications — skipped due to above |
| Summary email | ❌ Could not send — same network issue |
| **Total** | **0 applications submitted** |

## Root Cause

The Cowork scheduled task runs inside a sandboxed Linux environment with **no outbound internet access**. Playwright launched successfully with `xvfb-run`, but both `indeed.com` and `linkedin.com` returned `ERR_EMPTY_RESPONSE`. Gmail SMTP (`smtp.gmail.com`) also failed with DNS resolution errors, which is why this summary could not be emailed.

The answer cache seed step (`seed_cache.py`) succeeded because it only reads local files.

## How to Fix

The apply scripts need to run directly on your Mac. Options:

### Option 1 — Run manually from Terminal
```bash
cd ~/job_pipeline
python -u indeed_apply_now.py --limit 50
python -u linkedin_apply_now.py --limit 50
```

### Option 2 — Set up a native Mac schedule (launchd)
Create a `.plist` in `~/Library/LaunchAgents/` to run the scripts on a schedule directly in macOS (not through Cowork). This gives full internet access.

### Option 3 — Update the Cowork scheduled task
If Cowork schedules can run on your Mac's host Python (outside the sandbox), that would also resolve the issue. Check with the Cowork settings.
