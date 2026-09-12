# Roadmap (updated 2026-09-11)

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

### STANDING FACT, confirmed by Raghav 2026-09-11: Greenhouse CAN require Gmail OTP entry
Correction to this file's own long-standing assumption ("Greenhouse guest-apply — no account
creation, no login, no OTP" — stated as fact since the 2026-08-24 original build and repeated
throughout this session). Raghav directly observed a real Greenhouse application prompt for an OTP
sent to the Gmail address given in the application, near the END of the flow — the pipeline had no
handling for this at all and the application failed. This is NOT the same as Workday's account-
creation flow; it appears to be a lighter verification step Greenhouse itself can add on some
postings. No log/screenshot evidence of this exact case captured yet (it happened before
`greenhouse_apply_now.py` had run-output logging, and/or on a run not yet cross-checked against
`data/debug_logs/`). Next occurrence: capture the URL/company and a screenshot before attempting any
fix — same rule as everything else this session, evidence before code.

UPDATE 2026-09-11 (v2.14.19): fix shipped, but NOT yet live-verified. Code investigation found the
pipeline already had a full Gmail-OTP mechanism (`mail_reader.wait_for_otp()` +
`_complete_greenhouse_email_verification()`) — it was just gated to check ONLY once, right after
page load, so an OTP appearing after Submit (Raghav's exact case) was invisible to it.
`submit_greenhouse_application()` now also checks for the verification prompt after a failed submit
click and re-uses the same Gmail-read/code-entry path. Still no real screenshot/DOM evidence of the
actual post-submit prompt's markup — watch the next live run closely; the phrase-list/selector may
need a same-night follow-up fix once real evidence exists, same pattern as Location/Ack.

### Live-fire session, 2026-09-10/11 (v2.14.7 → v2.14.18)
First real `--live` attempts ever made against Greenhouse. Root-caused and fixed, in order: a
cache-chain bug that broke early instead of trying the next answer source; EEO combobox clicks
that missed without retry; `mail_reader.py`'s IMAP search excluding already-seen mail (plausible
Workday OTP-loop cause, unconfirmed); RunLogger wired into Greenhouse/Workday so run data can be
trusted; two explicit policy changes at Raghav's instruction (residency/location questions answer
"Yes"; Acknowledgement-type questions get answered, not skipped on detection uncertainty); the
"Location (City)" field's real bug — it's a react-select combobox requiring a real typed-then-
clicked suggestion, not a `.value=` set — found by live-testing the actual DOM, first attempt
(2.14.13) shipped with two of its own bugs (wrong option selector, wrong success check) caught and
fixed same night (2.14.14) by testing again rather than trusting the first fix; full run-output
logging to `data/debug_logs/` so browser-tier/behavior questions never again require guessing;
opinion/engagement essay questions (not factual claims) now get an API-drafted, auto-filled answer;
and the "Applicant Privacy Acknowledgement" field's real shape was finally found (also a
react-select, with real options `Yes`/`No`, read directly off the page's own React state) and fixed
properly, replacing the 2.14.12 guess. Full detail in `CHANGELOG.md`.

Recurring lesson this session: every fix uncovered the next problem once actually tested against a
live page. Don't treat any "fixed" claim as final until a real `--live` run confirms it.

### Next-phase plan of action (adopted 2026-09-11, informed by comparing against similar open-source
projects — `claude-apply`/`career-ops` on GitHub)
- **Phase 0 (immediate)**: clear the `.git` lock, commit the v2.14.17 baseline, then run
  `python3 greenhouse_apply_now.py --live --limit 3` and read that run's actual log/receipts/
  screenshots — the only way to know if tonight's fixes hold under real automation.
- **Phase 1 (right after, only once Phase 0 shows real results)**: fix whatever Phase 0 actually
  reveals, nothing preemptive. Add a Playwright "liveness check" pass on discovered postings before
  they enter the pipeline (drop expired/stale listings) — borrowed from `claude-apply`'s `--verify`
  flag.
- **Phase 2 (bigger effort, once Phase 0/1 are stable)**: switch Greenhouse job discovery from
  Google-search queries to Greenhouse's own public per-company job-board API
  (`boards-api.greenhouse.io/v1/boards/<company>/jobs`) — more complete, not Google-rate-limited,
  same approach every comparable project uses. Also investigate CDP-attach to a real (but
  dedicated, not daily-driver) Chrome profile instead of launching a separate automated browser —
  addresses the unresolved "--no-sandbox banner" question and the Tier1/2/3 launch complexity at
  the root, the way `claude-apply` does ("no stealth, runs in your own Chrome, as you").
- **Phase 3 (optional, lower priority)**: ghost-job/reposted-listing detection (`career-ops` has
  this); a human-review checkpoint between scoring and auto-apply (both comparable projects have
  one, ours deliberately doesn't — full autonomy is the stated goal here, so this is noted, not
  recommended).

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

Live test #4 (2026-09-10): discovery fix (2.14.5) confirmed working — Tsys found 18 jobs, Vizient
25, both scored/resume-built fine. New blocker one step further in: account creation succeeds every
time now, but sign-in right after always failed (needs email verification), and the auth code had
two real bugs — it never called the Gmail auto-verify flow that already exists elsewhere
(`handle_intervention`), and worse, it created a brand-new duplicate account instead of reusing one
already pending verification from earlier in the same run. Both fixed in 2.14.6.

Concrete next step: `python3 workday_apply_now.py --companies Tsys,Vizient --dry-run --limit 2`
again. This time watch for "🤖 Checking Gmail for a ... verification link" — if a real Workday
verification email lands within 5 minutes, auth should complete and the run should finally reach
the actual application form (contact info / experience / etc). If it does, raise `--limit`, then
drop `--companies` and go back to the full 42-company sweep per Raghav's own stated plan.

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
