# Changelog

All changes to the pipeline are logged here.
Format: `[VERSION] YYYY-MM-DD — What changed and why`

---

[1.0.1] 2026-06-28 — Added citizenship/visa ineligibility keywords to CLEARANCE_KEYWORDS in config.py. Jobs requiring US citizenship, green card, permanent residency, or blocking OPT/CPT/visa holders are now skipped immediately, same as clearance jobs.

[1.0.2] 2026-06-28 — Blocked 5 repeat-failing staffing firms (russell tobin, glocomms, come near, allied resources, bayone solutions) in FAKE_JOB_COMPANY_WORDS. Fixed FIT_THRESHOLD split: claude_engine.py now reads from config.py (60%) instead of a hardcoded default of 65%.

[1.0.3] 2026-06-28 — Cross-platform dedup: Indeed now merges the LinkedIn apply_log at startup so jobs already applied via LinkedIn are skipped by Indeed's existing already_applied() check.

[1.0.4] 2026-06-28 — Fixed ProtocolError/TargetClosedError crash in both linkedin_apply_now.py and indeed_apply_now.py. Dialog handler lambda replaced with _safe_dismiss() that swallows exceptions when a JS dialog fires after the browser/page is already closed. This was killing entire evening runs in under 30 seconds.

[1.0.5] 2026-06-28 — Trimmed LINKEDIN_QUERIES from 53 to 20. Removed narrow tool-specific and overlapping queries (PySpark/Azure/AWS/GCP/Databricks/Snowflake/dbt variants, niche roles). Broad entry-level queries kept. Cuts navigation overhead roughly in half (~150 min vs ~320 min) with no ToS change and minimal job coverage loss.

[1.0.6] 2026-07-02 — Fixed LinkedIn crash that the 1.0.4 dialog-handler fix didn't actually catch: `job_ids = page.evaluate(...)` (search results scrape) and `extract_right_panel(page)` (per-job detail scrape, called once per card) were both unguarded. When the browser/page died mid-run (e.g. a JS dialog firing right after navigation), these raised straight out of the whole run — matches the "LinkedIn: error: Page.evaluate: Target page, context or browser has been closed" failure seen in the 2026-07-02 afternoon run log. Both call sites now catch PWError, reopen the page, and either retry or skip the current job/query instead of ending the entire LinkedIn session.

[1.0.7] 2026-07-02 — Indeed no longer grinds for hours when the session is blocked. Same afternoon run showed a 596m and a 607m stall: CAPTCHA_COOLDOWN logic reset its counter after every 5-min cooldown with no cap, so an unattended (scheduled) run — nobody available to solve CAPTCHAs — could cooldown/retry/fail in a loop all day. Added `CAPTCHA_MAX_COOLDOWNS_PER_RUN` (config.py, default 2): after this many cooldowns with no successful solve, the run stops itself and sends one alert instead of looping. Also added `INDEED_EMPTY_QUERY_BAIL_THRESHOLD` (default 4): the same run showed every search returning "Found 0 cards" back-to-back (net::ERR_ABORTED on every page load) — now Indeed bails after N consecutive empty-result searches instead of burning the rest of the query list against a wall.

[1.0.8] 2026-07-02 — Fixed a real cause of stuck/failed applications: numeric fields ("How many years of X experience?") rejected by the site's own validation with "Answer must be a valid number (no decimals)" whenever Claude answered with a fraction (e.g. "2.5"). Indeed and LinkedIn both now round any number-field answer to the nearest whole number before filling, and the Claude prompts in both scripts now explicitly say whole numbers only. Also fixed data/stuck_questions.json always logging `"fields": []` for every stuck application (182/182 entries checked had this) — the field it read, `form_fields_last_seen`, was either never assigned (Indeed's fallback check `'form_fields_last_seen' in dir()`) or assigned via a broken `isinstance(form_ctx, dict)` check that's always False since form_ctx is a Playwright Frame, not a dict. Replaced with a proper module-level `_last_seen_fields` set inside smart_fill_step() itself, so future stuck entries will actually show which question(s) blocked the form. Known gap not fixed here: Indeed percentage-slider questions (0%/25%/50%/75%/100% style) aren't handled by the fill logic at all — seen in the Flowserve stuck entry (2026-06-30) — flagging for a follow-up since it needs live DOM inspection to fix safely.

[1.0.9] 2026-07-02 — New feature per Raghav's request: skip staffing agencies and consulting firms entirely, on both Indeed and LinkedIn, even legitimate ones (this is a preference, not fraud detection — separate from the existing FAKE_JOB_COMPANY_WORDS/COMPANY_WHITELIST fraud system, and takes priority over the whitelist). New shared module `staffing_filter.py` with `is_staffing_or_consultancy(company, description)`, driven by two new config.py lists: `STAFFING_CONSULTANCY_COMPANY_WORDS` (named staffing agencies + generic keywords like "staffing"/"consulting"/"consultancy" + IT-services/Big-4 firms like TCS, Infosys, Deloitte, Accenture, McKinsey) and `STAFFING_CONSULTANCY_DESC_SIGNALS` (description language like "on behalf of our client", "our client is seeking"). Toggle: `SKIP_STAFFING_CONSULTANCY` (default True). Indeed checks it twice — once cheaply on the company name before loading the job page, again on full JD text after — LinkedIn checks it once with the description it already has. Verified against test cases (Robert Half/TCS/Deloitte/consulting-named companies correctly excluded; Google/Databricks/generic "Solutions"-named companies correctly pass; no false positives on "Turkey Hill", "Money.com", "Keystone Bank").

[1.0.10] 2026-07-02 — Fixed the log-schema bug found during pro-level analysis: Indeed's cross-platform dedup (from 1.0.3) merged LinkedIn's entire apply_log.json directly into the in-memory `log` list that ALSO gets saved back to indeed_applied_log.json after every job. Two problems: (1) LinkedIn's log schema uses a "note" field for the skip/fail reason, Indeed uses "reason" — so every LinkedIn entry that leaked into Indeed's log looked reason-less. (2) Every Indeed run re-saved LinkedIn's entire history back into indeed_applied_log.json, which had ballooned to 119,126 records (49MB) — only 3,438 (2.9%) were actually Indeed's own. Fixed by keeping the merged LinkedIn data in a separate `dedup_log` used only for the already_applied() lookup; `log` (what gets appended-to and saved) now only ever contains Indeed's own entries. Also did a one-time cleanup of the existing indeed_applied_log.json: backed up to indeed_applied_log.json.bak_pre_cleanup_20260702_181631, then filtered out all linkedin.com-URL entries — file dropped from 119,126 records / 49MB to 4,953 records / 3.7MB. Failed-entries-with-empty-reason dropped from 97% to 12.1% (the residual 12% are genuine edge cases like bare exceptions with no message, not a schema issue).

## [1.0.0] 2026-06-28
- Added `PIPELINE_VERSION` to `config.py` — version now prints in every log
- Added `INDEED_ENABLED`, `LINKEDIN_ENABLED`, `WORKDAY_ENABLED` flags to `config.py` — can now turn off a single platform without touching its code
- Created `CHANGELOG.md` — this file

---

## How to log a change

Before making any edit:
```bash
bash safe_update.sh start "describe what you're trying"
```

After the change works:
1. Bump `PIPELINE_VERSION` in `config.py` (e.g. 1.0.0 → 1.0.1 for a fix, → 1.1.0 for a new feature)
2. Add a line here: `## [1.0.1] YYYY-MM-DD — what changed`
3. Run `bash safe_update.sh keep` to merge and save to GitHub

If the change breaks something:
```bash
bash safe_update.sh discard   # back to last working version instantly
```

---

## Version guide
- `1.0.X` — small fixes and tuning (fit threshold, delays, blocked companies)
- `1.X.0` — new feature or filter (new platform, new fraud detection layer)
- `X.0.0` — major rewrite or structural change
