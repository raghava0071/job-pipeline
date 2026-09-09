# Roadmap (updated 2026-09-09)

Strategic pivot, in priority order. Supersedes any earlier "add more platforms" framing.

## 1. LinkedIn/Indeed automation — dropped as a goal, for now
Not worth the arms race (Cloudflare/anti-bot on Indeed, LinkedIn's own detection). Both are
already `False` in `config.py` (`INDEED_ENABLED`, `LINKEDIN_ENABLED`). No further engineering
investment here until this decision is revisited. Existing code stays in the repo but is not a
priority for fixes or hardening.

## 2. Greenhouse — the real foundation. Harden it, ship it.
DONE 2026-09-09. `greenhouse_apply_now.py` (guest-apply, honesty-gated: auto dry-run only, live
submission requires explicit `--greenhouse-only --live`) hardened (2.11.3 — never lose a record on
a submit-exception, tag `Unverified`, block auto-retry) and extended with real account creation +
Gmail OTP/verify-link completion (2.12.0).

## 3. Receipts log — before adding more ATS handlers
DONE 2026-09-09. `receipts.py` — shared, platform-agnostic, one `data/receipts.json` for every
handler. Greenhouse writes one on every attempt (dry-run included, exceptions included — never
silently skipped). Lever/Ashby call the same `receipts.write_receipt()`, no new log format to invent.

## 4. Workday — get it fully working, THEN move to Lever/Ashby
REPRIORITIZED 2026-09-09: Raghav wants Workday actually working end-to-end before picking up new
ATS handlers, ahead of Lever/Ashby in the original order.

Real status (from `workday_pipeline_diagnostic_report.md`): 0 successful applications out of 133+
logged attempts, ever. Root blocker — Create Account's button click registers (real, uncovered,
enabled button, no exception) but the page doesn't progress; a fix (widen post-click wait to 10s,
v1.8.3) was written but never confirmed live because Workday got paused first. Email/OTP
verification code is real and not a stub (Gmail IMAP polling, 3 call sites, and the false-positive
sender-matching bug it had was just fixed in 2.13.1) but has never been exercised end-to-end
either, since account creation fails before most companies would even send a verification email.

Also learned from watching Tsenta (Raghav's own observation, 2026-09-09): for low-stakes,
no-right-answer custom dropdowns like "How did you hear about us?", it just picks any available
option rather than carefully scraping/matching real options — worth adopting for that narrow
question class once real DOM evidence is back.

First live test run 2026-09-09: `python3 run_all.py --workday-only --wd-limit 1 --dry-run` found 1
job (across 9 Google search queries) and skipped it — but printed zero reason why, because the
title/domain filter gate was silent (fixed in 2.13.2 — now prints title/company/filter booleans).
Never even reached account creation, so still no fresh evidence on the actual button-click bug.

Concrete next step: re-run the exact same command now that the diagnostic print is in place, see
whether it was a real mismatch or a noisy Google-search title tripping the filter on a good job. If
it clears that gate, it'll finally reach account creation and give the first real signal on v1.8.3's
fix in 2+ months.

## 5. Add Lever
Sourced from Raghav's own Gmail applications. Mirrors the Greenhouse handler pattern. Picked up
after Workday is confirmed working.

## 6. Add Ashby
Same — sourced from Gmail, mirrors the Greenhouse pattern. Together, Greenhouse + Lever + Ashby
+ Workday cover most of what's actually being applied to.

## 7. Move the automation runtime off the coding sandbox onto real hosting
DECIDED 2026-09-09 (reversed same day): staying on the Mac. Raghav is fine carrying the laptop and
keeping it awake for launchd's scheduled runs — no VPS. `VPS_MIGRATION_PLAN.md` is kept on disk,
unexecuted, in case this gets revisited later; nothing in it has been acted on. This item is closed
for now — no ongoing work here.

---
See `CLAUDE.md` for standing project rules (safe_update workflow, config-only tunables, etc.).
This file is the current priority order — check it before picking up new work on this project.
