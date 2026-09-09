# Roadmap (updated 2026-09-09)

Strategic pivot, in priority order. Supersedes any earlier "add more platforms" framing.

## 1. LinkedIn/Indeed automation — dropped as a goal, for now
Not worth the arms race (Cloudflare/anti-bot on Indeed, LinkedIn's own detection). Both are
already `False` in `config.py` (`INDEED_ENABLED`, `LINKEDIN_ENABLED`). No further engineering
investment here until this decision is revisited. Existing code stays in the repo but is not a
priority for fixes or hardening.

## 2. Greenhouse — the real foundation. Harden it, ship it.
`greenhouse_apply_now.py` (guest-apply, honesty-gated: auto dry-run only, live submission
requires explicit `--greenhouse-only --live`) is the working core. Before adding any other ATS,
find and fix its reliability gaps — this is the pattern every other handler will copy.

## 3. Receipts log — before adding more ATS handlers
DONE 2026-09-09. `receipts.py` — shared, platform-agnostic, one `data/receipts.json` for every
handler. Greenhouse writes one on every attempt (dry-run included, exceptions included — never
silently skipped). Lever/Ashby call the same `receipts.write_receipt()`, no new log format to invent.

## 4. Add Lever
Sourced from Raghav's own Gmail applications this week. Mirrors the Greenhouse handler pattern.

## 5. Add Ashby
Same — sourced from Gmail, mirrors the Greenhouse pattern. Together, Greenhouse + Lever + Ashby
+ Workday cover most of what's actually being applied to.

## 6. Move the automation runtime off the coding sandbox onto real hosting
DECIDED 2026-09-09 (reversed same day): staying on the Mac. Raghav is fine carrying the laptop and
keeping it awake for launchd's scheduled runs — no VPS. `VPS_MIGRATION_PLAN.md` is kept on disk,
unexecuted, in case this gets revisited later; nothing in it has been acted on. This item is closed for
now — no ongoing work here.

## 7. Workday — re-approach as canary rollout
Currently `WORKDAY_ENABLED = False`. Real status (from `workday_pipeline_diagnostic_report.md`,
2026-09-09 review): 0 successful applications out of 133+ logged attempts, ever. Root blocker —
Create Account's button click registers (real, uncovered, enabled button, no exception) but the
page doesn't progress; a fix (widen post-click wait to 10s, v1.8.3) was written but never confirmed
live because Workday got paused first. Email/OTP verification code is real and not a stub (Gmail
IMAP polling, 3 call sites) but has never been exercised end-to-end either, since account creation
fails before most companies would even send a verification email.

Concrete next step, before writing any new code: get ONE fresh, logged, live data point —
`python3 run_all.py --workday-only --wd-limit 1 --dry-run` — and read `data/crash_logs/submit_debug_*.json`
+ the new screenshot. That single data point (does v1.8.3's fix actually work now?) is worth more
than any further guessing, and is what "canary" should mean here in practice: one company, one
attempt, read the evidence, THEN decide whether to expand.

---
See `CLAUDE.md` for standing project rules (safe_update workflow, config-only tunables, etc.).
This file is the current priority order — check it before picking up new work on this project.
