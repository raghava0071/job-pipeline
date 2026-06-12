# Job Pipeline — Afternoon Run Error Report
**Date:** June 11, 2026 — Afternoon scheduled run

## Summary

| Step | Result |
|------|--------|
| Cache seed (`seed_cache.py`) | ✅ Success — 313 answers seeded, 294 cached |
| Indeed (50 apps) | ❌ 0 applications — network blocked |
| LinkedIn (50 apps) | ❌ 0 applications — skipped (Indeed failed) |
| Summary email | ❌ Could not send — same network issue |
| **Total** | **0 applications submitted** |

## Root Cause

Same issue as the June 10 afternoon run. The Cowork scheduled task runs inside a sandboxed Linux environment with **no outbound internet access to indeed.com or linkedin.com**. Playwright launched successfully with `xvfb-run`, but both sites returned `ERR_EMPTY_RESPONSE`. Gmail SMTP is also blocked, preventing the summary email.

The answer cache seed step succeeded (local file reads only).

## How to Fix

The apply scripts must run directly on your Mac — not inside the Cowork sandbox.

### Option 1 — Run manually from Terminal
```bash
cd ~/job_pipeline
python -u indeed_apply_now.py --limit 50
python -u linkedin_apply_now.py --limit 50
```

### Option 2 — Native Mac schedule (launchd)
Create a `~/Library/LaunchAgents/com.raghav.jobpipeline.plist` that runs the scripts on a schedule directly in macOS. This gives full internet + display access.

### Option 3 — Ask me to run the scripts interactively
Start a Cowork session and ask me to "run the job pipeline now" — I can use computer use to open Terminal on your Mac and execute the scripts there with full network access.
