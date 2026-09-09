# Tsenta comparison (2026-09-09)

Tsenta ([tsenta.com](https://tsenta.com/), [AI disclosure page](https://tsenta.com/ai-disclosure)) is a
YC-backed, paid SaaS doing the same core thing job_pipeline does: watch job postings, tailor a resume,
auto-fill and submit applications across ATSes, using AI for the open-ended answers. Worth studying for
concepts, not for code — everything below is my own read of their *public* marketing/disclosure pages,
not their actual implementation (which is closed-source and I have no access to). Ideas and feature
concepts aren't owned by anyone; their specific text, UI, and code are — nothing here copies those.

## What they do that validates your roadmap

- **Receipts.** Their FAQ: "A successful application can produce a receipt with the submitted fields,
  open-ended answers, résumé, any cover letter used, and confirmation details." Their dashboard mockup
  shows the shape concretely: every field + the value used, a completion tally ("6 of 6 fields · 0
  skipped · 0 generic answers"), platform, and time-to-complete. This is almost exactly task #3 on your
  roadmap — good sign it's the right thing to build next, and gives a concrete schema to start from (see
  below).
- **Gmail for verification codes.** Their FAQ says connecting email lets them "enter verification codes
  some employers email mid-application" — the same thing you just had me build into
  `greenhouse_apply_now.py` via `mail_reader.py`. Independent confirmation this is a normal, expected
  feature for this category of tool, not an edge case.
- **19 ATSes, same top 4.** Their supported list starts with Workday, Greenhouse, Lever, Ashby — exactly
  your priority order. Beyond those four they also cover Rippling, iCIMS, BambooHR, Workable, JazzHR,
  Jobvite, BreezyHR, Oracle Cloud, SmartRecruiters, Paylocity, UltiPro, ADP, Dover, Gem, Zoho Recruit — a
  reasonable "what's next after Ashby" reference list, though your own rule (sourced from your actual
  Gmail application history, not a generic top-19) is the better prioritization method for a personal
  tool and I'd keep using it rather than working down their list blindly.
- **No evasion stance.** Their FAQ: "Tsenta does not promise that automated assistance is undetectable."
  Matches your own documented position in `greenhouse_apply_now.py`/`workday_apply_now.py` ("no
  automation-hiding flags... for launch stability, not evasion"). You're not behind industry practice
  here — you're aligned with the most credible player in the category.

## A feature they have that you don't (worth a decision, not a default yes)

- **Inbound reply routing.** Beyond reading email for OTPs, they parse recruiter replies/confirmations/
  interview invites/rejections and move the application's status automatically — "the inbox stops being
  a graveyard and starts being a pipeline you can read." `notifier.py` today is outbound-only (it tells
  you things; it doesn't read what comes back). This is a real, scoped feature you could add on top of
  the `mail_reader.py` you already have — but it's new scope, not on your roadmap yet, so flagging it
  rather than building it. Say if you want it added as a task.

## Where I'd deliberately NOT copy them

- **Approve-before-send diffs.** Their whole flow shows a resume/cover-letter diff and asks the user to
  approve before anything sends. Your pipeline is built to run unattended on a schedule — that's the
  point of it. Adding a human-approval gate would undercut that; your honesty-gate (never submit an
  incomplete/fabricated field) is the automated equivalent of their manual review, just enforced by code
  instead of a click. I'd keep it that way.
- **Multi-surface (iOS/Android/iMessage/WhatsApp/Chrome/MCP), paid tiers, 50k-page discovery crawler.**
  All of that is SaaS-business scaffolding for serving thousands of paying strangers. None of it is
  relevant to a single-user local tool — including it would be pure scope creep against CLAUDE.md's own
  rule ("does the pipeline break without this? If no, skip it").

## Suggested receipt schema for task #3 (draft, based on the above)

Per attempted application, one JSON record:

```
{
  "timestamp": "...", "platform": "greenhouse", "company": "...", "title": "...", "url": "...",
  "resume_version": "alex_morgan_v6.pdf", "cover_letter_version": "...",
  "fields": { "first_name": "Raghavendra", "email": "...", "why_company": "..." , ... },
  "fields_total": 6, "fields_skipped": 0, "fields_generic": 0,
  "seconds_to_complete": 47,
  "outcome": "submitted" | "failed" | "skipped_incomplete" | "unverified",
  "reason": "..."
}
```

This is a superset of what `submit_greenhouse_application`'s `record` dict already builds in memory
(2.11.3/2.12.0) — the actual task #3 work is mostly making that existing in-memory record durable,
per-field-typed, and shared across handlers, not inventing a new shape from scratch.

---
Sources: [tsenta.com](https://tsenta.com/), [tsenta.com/ai-disclosure](https://tsenta.com/ai-disclosure)
