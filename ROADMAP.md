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

Live test #1 (2026-09-09): found 1 job across 9 Google queries, skipped it silently (fixed in
2.13.2 — diagnostic print added).

Live test #2 (2026-09-09, same day): ALL 9 Google queries came back "Google blocked" — confirmed
Google is hard-blocking this search pattern as bot traffic. Per the same "don't fight bot detection
with stealth" rule already applied to Indeed/Greenhouse, replaced Google-search discovery entirely
(2.14.0) — `WORKDAY_COMPANIES` (42 companies, URLs reconstructed from real history in
`data/workday_applied_log.json`) + `_fetch_workday_jobs_direct()` visit each company's own Workday
page directly, no Google involved at all.

Also noted from watching Tsenta handle Workday (Raghav's own observation): low-stakes dropdowns
like "How did you hear about us?" don't need careful real-options matching — any available option
is fine, since there's no wrong answer for that question class. Worth adopting once there's a live
DOM to confirm Workday's actual rendered options — not yet implemented.

Live test #3 (2026-09-09/10): confirmed root cause of the post-account-creation "Unknown step"
stall by opening the actual screenshots directly (`step_before_unknown-step-1_170621.png` /
`step_stuck_unknown-step-1_170635.png`, Tsys run) instead of guessing. The "Start Your Application"
modal (Autofill with Resume / Apply Manually / Use My Last Application / Apply With LinkedIn)
reopens after auth when Apply is clicked a second time, but only the pre-auth click had a check for
it — the step-walk loop had no case for this modal at all, so it fell into the generic "unknown
step" fallback (fill fields, click Next) which does nothing on this modal, and `_get_page_marker()`
never changes because it reads the underlying page's own `h1`/pathname, not the modal. Fixed in
2.14.3: added an explicit `WD["apply_manually"]` check both right after the post-auth re-click and
as a first-class recognized state inside the step-walk loop.

Concrete next step: `python3 workday_apply_now.py --companies Tsys,Vizient --dry-run --limit 2` to
confirm the modal is actually clicked through now and the walk reaches a real form step (contact
info / experience / etc). If it does, run the same command with `--limit` raised, then drop
`--companies` and go back to the full 42-company sweep per Raghav's own stated plan.

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
