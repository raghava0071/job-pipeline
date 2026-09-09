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
Per-attempted-application log: fields filled, resume version, timestamp, success/fail. This is
what tells us *why* something broke instead of guessing from screenshots/error logs after the
fact. Build this before Lever/Ashby so those handlers get receipts from day one.

## 4. Add Lever
Sourced from Raghav's own Gmail applications this week. Mirrors the Greenhouse handler pattern.

## 5. Add Ashby
Same — sourced from Gmail, mirrors the Greenhouse pattern. Together, Greenhouse + Lever + Ashby
+ Workday cover most of what's actually being applied to.

## 6. Move the automation runtime off the coding sandbox onto real hosting
Currently local Mac + launchd. Needs a hosting decision (VPS vs. cloud vs. hardened local) before
migration work starts.

## 7. Workday — re-approach as canary rollout
Currently `WORKDAY_ENABLED = False` (binary off since the auto-apply issues). Instead of flipping
it back to a binary on/off switch, roll it out to a small % of matches first and expand based on
observed success/error rates.

---
See `CLAUDE.md` for standing project rules (safe_update workflow, config-only tunables, etc.).
This file is the current priority order — check it before picking up new work on this project.
