# Phase 4 Error Log — indeed_apply_now.py

---

## ERROR 001
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Search bar filled instead of smartapply form
**What happened:** Step 1 wrote address into Indeed search bar instead of the application form fields
**Fix:** Added `_wait_for_smartapply_frame()` — waits 12s for smartapply iframe. Skip `indeed.com/viewjob` frames when smartapply frame exists in `_find_active_form_ctx`

---

## ERROR 002
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Stale cache selected wrong resume for every job
**What happened:** Cache had `resume-selection → Squeeze_Technology.docx` from prior run. Every job used that old filename
**Fix:** Resume-selection page bypasses cache entirely. Startup SQL deletes all `resume-selection`, `*.docx`, `*.pdf` cache entries on every run

---

## ERROR 003
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Duplicate frame scanning — main frame counted twice
**What happened:** `[page.main_frame] + list(page.frames)` doubled the main frame since it is already `frames[0]`
**Fix:** Changed all frame iteration to `list(page.frames)` only across all functions

---

## ERROR 004
**Date:** 2026-05-30
**Status:** FIXED
**Error:** "Skip to main content" accessibility link clicked in infinite loop
**What happened:** `_click_any_forward_button` matched the accessibility skip link as a valid forward button and clicked it ~15 times
**Fix:** Added `skip`, `skip to`, `accessibility` to BACK exclusion list. Changed match from `includes` to `startsWith`

---

## ERROR 005
**Date:** 2026-05-30
**Status:** FIXED
**Error:** resume-m Continue button has `aria-disabled=true` — never clicked, bot stuck
**What happened:** Indeed sets `aria-disabled` on Continue until resume card is selected. Nav detection skips aria-disabled buttons so Continue was never found
**Fix:** New function `_force_click_continue_on_resume_page()` — clicks resume card, then force-removes `aria-disabled` and `disabled` from Continue and clicks it. Wired into step loop

---

## ERROR 006
**Date:** 2026-05-30
**Status:** FIXED
**Error:** RULE VIOLATION — bot clicked "Apply on company site" external redirect button
**What happened:** `_click_any_forward_button` treated "Apply on company site" as a valid forward button and clicked it. Rule: Indeed in-portal ONLY, never external
**Fix:** Added to BACK exclusion list: `apply on company site`, `apply on employer`, `apply externally`, `continue to company`, `apply on the company`, `apply on company`, `external application`, `leaving indeed`, `you're leaving`

---

## ERROR 007
**Date:** 2026-05-30
**Status:** FIXED
**Error:** resume-module/stru loop — bot stuck on same URL for steps 3–13
**What happened:** `_click_any_forward_button` kept clicking notification badge button `'1\nnew update'` which did nothing. Bot looped 11 times
**Fix:** Added `new update`, `updates` to BACK exclusion. Added same-URL loop detection — breaks after 4 consecutive identical URLs

---

## ERROR 008
**Date:** 2026-05-30
**Status:** FIXED
**Error:** questions-module/q infinite loop — form rejected Continue silently, steps 3–15 same page
**What happened:** Required fields `Address *`, `City *`, `State/Province *`, `Postal/ZIP *` filled as empty. Claude invented wrong address (Chicago IL). Indeed silently rejected Continue and stayed on same page
**Fix:** Seeded real answers into cache at startup: `Address * → 7330 W Atlantic Ave`, `City * → Delray Beach`, `State/Province * → Florida`, `Postal/ZIP * → 33444`. Cleared wrong cached values. Same-URL detector breaks loop after 4 repeats

---

## ERROR 009
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Off-domain job titles passing fit gate and getting full resume built
**What happened:** `Torc Robotics — Software Engineer I` scored 82% and went through full resume + cover letter. No domain filter existed, only senior-level filter
**Fix:** Added `is_relevant_domain(title)` with 30+ data/analytics keywords. Any title with none of these keywords skipped before scoring — zero API calls used

---

## ERROR 010
**Date:** 2026-05-30
**Status:** FIXED
**Error:** `'1\nnew update'` badge button still clicked after adding `new update` to exclusion list
**What happened:** JS used `t.startsWith(w)`. Button text starts with `'1\n'` not `'new update'` so exclusion check failed
**Fix:** Changed JS exclusion from `startsWith` to `includes`. Added guard: skip buttons whose text is pure digits or length <= 1

---

## ERROR 011
**Date:** 2026-05-30
**Status:** FIXED
**Error:** External apply jobs consumed Claude API + resume build before being skipped
**What happened:** Torc, Allruva, Murj all scored 78–82% and had full resume + cover letter built before external apply detected at click time
**Fix:** External check moved to immediately after page load before any Claude call. Added live JS scan of all button text and page body for external phrases. Skip on first match — zero API calls

---

## ERROR 012
**Date:** 2026-05-30
**Status:** FIXED
**Error:** `SyntaxWarning: invalid escape sequence '\d'` on every run
**What happened:** JS regex `/^\d+$/` inside Python string — Python 3.12+ treats `\d` as invalid escape
**Fix:** Changed to `/^[0-9]+$/` — same meaning, no backslash needed

---

## ERROR 013
**Date:** 2026-05-30
**Status:** FIXED
**Error:** resume-m page abandoned with "No forward button found" — whole application dropped
**What happened:** `_force_click_continue_on_resume_page` only tried Continue button if card click succeeded first. No card found → returned None → step loop exited. Indeed always has a button at the bottom
**Fix:** Card click is now best-effort only. Button click always attempted regardless of card result. 3-layer fallback: (1) broad card selectors checking element text for .docx/.pdf, (2) scan all buttons including aria-disabled and force-click, (3) last resort — click the last visible button on the page

---

## ERROR 014
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Same skipped job printed multiple times across queries
**What happened:** `ISSO Vulnerability Management`, `Computer Technician` each printed 4 times. Session dedup ran after domain filter so same job repeated per query
**Fix:** Moved session dedup to run first before all filters. Every job added to `seen_this_run` on first encounter regardless of skip reason

---

## ERROR 015
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Last-resort button picker clicked "report an issue" link
**What happened:** `_force_click_continue_on_resume_page` Step 3 last-resort fallback clicked the last visible button on the page which was "Report an issue" — a feedback link, not a nav button
**Fix:** Added `report`, `feedback`, `issue`, `problem`, `report an issue`, `give feedback`, `submit feedback`, `accessibility`, `skip` to `skipLast` exclusion list in the last-resort button section

---

## ERROR 016
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Button "unable to continue to the next step" matched keyword `continue` and got clicked
**What happened:** Step 4 — `_force_click_continue_on_resume_page` keyword list includes `continue`. The error message button "unable to continue to the next step" matched and was clicked
**Fix:** Added `unable to`, `report`, `feedback`, `issue`, `problem`, `submit feedback`, `report an issue`, `give feedback` to SKIP list in `_force_click_continue_on_resume_page` Step 2. Same exclusions added to `_click_nav` JS fallback BACK list

---

## ERROR 017
**Date:** 2026-05-30
**Status:** FIXED
**Error:** "submit feedback" matched as Submit nav button — dry-run stopped early thinking it reached submit page
**What happened:** Step 5 — `_get_nav_buttons` found "submit feedback" because it contains the word `submit`. Dry-run printed "Submit button found — stopping here" and returned success without completing the form
**Fix:** Added SKIP list to `_get_nav_buttons` NAV_JS: `submit feedback`, `report an issue`, `report issue`, `unable to continue`, `unable to`, `report a problem`, `give feedback`, `feedback`, `report`, `accessibility`, `skip`, `sign in`, `log in`, `back`, `previous`, `cancel`. Any button matching a KWS word AND a SKIP phrase is excluded

---

## ERROR 018
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Over-aggressive SKIP list blocked all buttons on resume-m — "no button found even in last-resort scan"
**What happened:** After ERROR 015-017 fixes, standalone words `report`, `feedback`, `issue`, `problem` were added to skipLast. These are too broad — they blocked buttons that happened to contain those substrings in unrelated text
**Fix:** Replaced standalone words with full exact phrases only: `submit feedback`, `report an issue`, `report issue`, `report a problem`, `give feedback`, `unable to continue`, `unable to proceed`. Removed `report`, `feedback`, `issue`, `problem` as standalone exclusions

---

## ERROR 019
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Last-resort only checked the very last button — if it was excluded, returned null without trying others
**What happened:** Last-resort picked `visibleBtns[visibleBtns.length - 1]`, checked it, and if skipLast matched returned null immediately without looking at other buttons
**Fix:** Changed to iterate ALL visible buttons in reverse order (bottom-up). Tries each one, skips if in exclusion list, clicks the first valid one found

---

## NOTE — "Why didn't it click Submit"
**Date:** 2026-05-30
**Answer:** Running with `--dry-run` flag intentionally stops before Submit. AUTOCONTENT correctly reached the review page. To actually submit, run: `python indeed_apply_now.py --limit 1` (no --dry-run flag)

---

## ERROR 020
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Pipeline filling optional fields and calling Claude API for them unnecessarily
**What happened:** Fields like "Get email updates for jobs in Irvine CA" (optional checkbox) and "Website, Blog or Portfolio" (optional text) were being sent to Claude API. User rule: fill ONLY required fields marked with *
**Fix:** Added `is_required_field(f)` filter in `smart_fill_step` — a field is required if `f.get("required")==True` (HTML required attribute) OR `"*"` appears in the label text. All optional fields are logged as `[optional-skip]` and excluded before cache lookup or Claude call

---

## ERROR 021
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Phone number field `'Type phone number'` left blank — Claude returned empty string
**What happened:** IT Heroes Step 1 had `[tel] 'Type phone number'` field. Claude returned blank for it. Field ended up empty in the form
**Fix:** Added `'Type phone number'`, `'Mobile number'`, `'Cell phone'` to the startup real-answer seed cache with value `5613017799`

---

## ERROR 022
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Bot actions too fast — risk of CAPTCHA trigger on Indeed smartapply
**What happened:** User noticed incomplete applications and suspected "are you a robot" CAPTCHA. Indeed Enterprise reCAPTCHA scores based on user behavior speed. Bot was clicking with zero delay between steps
**Fix:** Added `time.sleep(random.uniform(1.5, 3.0))` at the start of every form step — random 1.5–3 second human-like pause. This reduces behavior pattern detection risk

---

## ERROR 023
**Date:** 2026-05-30
**Status:** FIXED
**Error:** CAPTCHA appeared at submit — bot couldn't solve it, application not submitted, but code falsely logged "Applied" and sent email
**What happened:** Indeed showed image CAPTCHA ("select all bicycles") at submit step. Bot clicked Submit but CAPTCHA blocked it. Code had `submitted = True` optimistic fallback which marked the job as applied even with no confirmation. False email was sent
**Fix:** Added CAPTCHA detection before confirmation check — scans for `bframe` frame URL (recaptcha visual challenge). If detected: prints large warning, pauses up to 90 seconds, polls every second for CAPTCHA to disappear. If user solves it, continues. If timeout — marks as FAILED, no false log. Removed the optimistic `submitted = True` line entirely

---

## ERROR 024
**Date:** 2026-05-30
**Status:** FIXED
**Error:** "Get email updates" marketing checkbox treated as required and called Claude API for it
**What happened:** Indeed's marketing email checkbox had `required=true` in HTML. Code treated it as a required application field and called Claude to answer it. It's not a required application field — it's a marketing opt-in
**Fix:** Added `NEVER_FILL` set with marketing/alert keywords: `get email updates`, `email updates`, `job alerts`, `email alert`, `notify me`, `subscribe`. Fields matching any of these are always skipped regardless of HTML required attribute

---

## ERROR 025
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Street address field seeded with wrong value "Delray Beach, FL" instead of actual street address
**What happened:** Startup seed cache had `Street address → Delray Beach, FL` which is a city name, not a street address. Forms filled with wrong data
**Fix:** Corrected seed value to `Street address → 7330 W Atlantic Ave Apt 215`

---

## ERROR 026
**Date:** 2026-05-30
**Status:** FIXED
**Error:** CAPTCHA window appears but user cannot click Verify — browser window too small, Verify button cut off
**What happened:** Indeed showed image CAPTCHA ("select all bicycles") at submit time. User tried to solve it manually but the browser window opened too small and the Verify button was below the visible area. Resizing the page didn't help. Application failed
**Fix:** Two changes: (1) Added email notification — the moment CAPTCHA is detected, pipeline immediately sends an email to raghavendrakaranam30@gmail.com with subject "⚠️ CAPTCHA Required" and the job title/company. (2) Added JavaScript to force-resize the CAPTCHA iframe to full visible size so the Verify button is always accessible. (3) Pipeline pauses 90 seconds waiting for manual solve before marking as failed

---

## ERROR 027
**Date:** 2026-05-30
**Status:** NOTED
**Error:** 4 jobs from master_run.py marked "Failed — Reached form end but couldn't confirm submission"
**What happened:** Jobs Robert Half, Unity Design, JARDIN Joint Action, Squeeze Technology all reached the review/submit page but couldn't confirm submission. Likely CAPTCHA blocked all 4 or submission confirmation text wasn't detected
**Fix:** CAPTCHA pause + email alert (ERROR 026 fix) covers this. Also confirmed these were LinkedIn jobs run through master_run.py's auto_apply.py, not indeed_apply_now.py

---

## ERROR 028
**Date:** 2026-05-30
**Status:** FIXED
**Error:** Cache miss + Claude API failure = job application silently closed with no fields filled
**What happened:** When cache didn't have an answer for a required field and Claude API failed (network error, timeout, or rate limit), `answers` dict stayed empty. Code hit `if not answers: return 0` which returned early with zero fills. Required fields stayed blank, form validation failed, application closed
**Fix:** Two changes: (1) Removed the `if not answers: return 0` early exit — pipeline always continues even with no answers. (2) Added `FALLBACK` dict — after Claude call (success or fail), any field still without an answer gets a safe default applied automatically. Common fallbacks: `work authorization → Yes`, `salary → 85000`, `start date → 2 weeks`, `sponsorship → No`, `years of experience → 2`, `gender/ethnicity/disability → Prefer not to say`. Text fields with no match get empty string rather than blocking the form

---

## ERROR 029
**Date:** 2026-05-31
**Status:** FIXED
**Error:** `BrowserType.launch_persistent_context: Failed to create a ProcessSingleton` — browser profile already locked
**What happened:** Previous pipeline run was still running (or crashed and left a lock file). Trying to start a new run with `tee` failed immediately because `.indeed_session/SingletonLock` file still existed from the old process
**Fix:** Kill the old process and delete the lock file before starting a new run: `pkill -f indeed_apply_now.py && rm -f ~/job_pipeline/.indeed_session/SingletonLock` then run again. This is a one-time manual fix each time a run crashes mid-session

---

## ERROR 030
**Date:** 2026-05-31
**Status:** FIXED
**Error:** Application questions (W2, Visa, Rate, Yes/No) skipped because they have no `*` — form stuck, force-submit triggered, application failed
**What happened:** IT Heroes Step 4 had `Will you be able to work on our W2?`, `What is your Visa?`, `What is the best rate you are looking for?` — all with no `*`. Code classified them as optional and skipped all 3. Zero fields filled. Same button repeated 3x → force-submit → form ended. Same happened for NBI Yes/No work authorization radio and scoutability page
**Fix:** On `questions-module` URL pages, fill ALL fields regardless of required status — question pages only contain real application questions, never optional marketing fields. Non-question pages (contact, profile) still use required-only filter

---

## ERROR 031
**Date:** 2026-05-31
**Status:** FIXED
**Error:** No master Q&A file — every new question went to Claude API, wasting tokens and time
**What happened:** Common questions like W2, Visa, Rate, authorization were being sent to Claude API on every job even though the answers never change
**Fix:** Created `qa_answers.py` — master Q&A file with 60+ pre-written answers covering work authorization, visa, salary, availability, relocation, EEO, LinkedIn, background check, and more. Pipeline checks this file FIRST before cache and before Claude. Adding a new answer to this file applies to all future applications instantly

---

## ERROR 032
**Date:** 2026-05-31
**Status:** FIXED
**Error:** CAPTCHA email not sent, 90s wait skipped, process killed instead
**What happened:** Three problems: (1) `notifier.send_email()` does not exist — the function name was wrong so the call failed silently and email was never sent. (2) CAPTCHA was only detected at the submit step, not at every step — if CAPTCHA appeared mid-form the code didn't catch it. (3) If the CAPTCHA check threw any exception, it crashed the whole process
**Fix:** Three changes: (1) Added `send_alert(subject, body)` function to `notifier.py` — a proper plain-text alert email function that works. (2) Created `_check_and_handle_captcha(page, title, company)` — a standalone crash-proof function that detects `bframe`, sends email via `notifier.send_alert`, resizes the CAPTCHA iframe so Verify button is visible, and waits 90 seconds polling every second. Wrapped in full try/except so any error inside is logged but never crashes the pipeline. (3) Called `_check_and_handle_captcha` at the START of every step loop iteration — CAPTCHA is now detected the moment it appears, not just at submit

---

## ERROR 033
**Date:** 2026-05-31
**Status:** FIXED
**Error:** `same_btn_count` triggered force-submit prematurely — "continue" on steps 2, 3, 4 = 3x = force-submit even though form was advancing normally
**What happened:** IT Heroes and NBI both failed because steps 2, 3, 4 all show "continue" as the nav button. Counter hit 3 → force-submit triggered at step 4 even though URL was changing each step (form was working correctly)
**Fix:** Added `same_btn_count = 0` reset whenever the URL changes. Button repeat counter now only counts consecutive same button ON THE SAME URL. If URL changes, it's a new page and the counter resets

---

## ERROR 034
**Date:** 2026-05-31
**Status:** FIXED
**Error:** Scoutability radio page (Employers can find you on Indeed) skipped — frame URL is `resume-s` not `questions-module`
**What happened:** The question-page fill-all logic checked for `question` or `questions` in frame URL. The scoutability page uses frame URL `resume-s` so it wasn't caught. All 3 fields skipped as optional
**Fix:** Added `resume-s` to the `is_question_page` check. Also added `Employers can't find you on Indeed` to qa_answers so both radio options map to the correct answer

---

## ERROR 035
**Date:** 2026-05-31
**Status:** FIXED
**Error:** NBI Yes/No radio group — label is a hash ID `q_c5f64fa31efee6ca83f42779e68d8f29`, options are just "Yes"/"No" with no context. Claude API called for meaningless labels, only 1/3 filled
**What happened:** The radio group question ("Are you authorized...") wasn't extracted as a label. The group got a hash ID label and the individual options got "Yes"/"No" as labels. Claude answered "Yes" → "Yes" and "No" → "No" literally, only matching 1 field
**Fix:** Added `q_` prefix match in qa_answers (any hash-ID group defaults to "Yes") and added `yes` → "Yes" as a direct answer. Also fixed by scoutability filling all fields on `resume-s` pages (ERROR 034 fix)

---

## ERROR 036
**Date:** 2026-05-31
**Status:** FIXED
**Error:** CAPTCHA appeared at submit time but was not detected — Submit blocked, no email sent, application failed
**What happened:** IT Heroes and NBI both reached review page. When Submit was clicked, CAPTCHA appeared (bframe in Frame[7]). But `_check_and_handle_captcha` only ran at the START of the step — before Submit was clicked. CAPTCHA appeared AFTER the click so it was missed. Submit button was blocked, no nav buttons found, application failed silently
**Fix:** Added CAPTCHA check AFTER the submit click in the review page handler (Step C). If CAPTCHA appears post-submit: sends email alert, waits 90s for manual solve, then retries Submit. Also force-removes `aria-disabled` from all submit buttons before clicking — CAPTCHA can temporarily disable the button

---

## ERROR 037
**Date:** 2026-05-31
**Status:** FIXED
**Error:** CAPTCHA timeout 90 seconds was too short — user couldn't solve it in time
**What happened:** POD Health and IT Heroes both had CAPTCHA appear at submit. Email was sent and browser window was repositioned correctly. But 90 seconds was not enough time for user to switch to the browser, read the CAPTCHA, and solve it
**Fix:** Increased CAPTCHA timeout from 90 seconds to 300 seconds (5 minutes). Progress logged every 30 seconds instead of every 10. Added Mac OS system notification popup via `osascript` — a banner appears with sound ("Ping") on the Mac desktop even when the terminal is hidden behind other windows

---

## ERROR 038
**Date:** 2026-05-31
**Status:** FIXED
**Error:** Yes/No radio buttons DOM fill returned 0/3 — radio click not triggering React state update
**What happened:** NBI Step 2 had Yes/No radio group. Radio found, answer matched, `opt.click()` called but React state didn't update. `DOM fill result: 0/3`. Form saw radio as still unselected, rejected Continue, `No nav buttons found`, application stopped
**Fix:** Added React synthetic event dispatch after `opt.click()`: fires `click`, `change`, `input` events with `bubbles:true`. This triggers React's synthetic event system so the radio selection is registered in component state, not just in the DOM

---

## ERROR 039
**Date:** 2026-05-31
**Status:** FIXED
**Error:** Second CAPTCHA appeared after first was solved — pipeline didn't retry Submit, application failed
**What happened:** User solved first CAPTCHA. Pipeline detected it solved, clicked Submit, Indeed showed a SECOND CAPTCHA. The second one "closed" (dismissed) before user could solve it. Pipeline didn't have a retry loop — after one Submit attempt with one CAPTCHA check it gave up
**Fix:** Replaced single Submit attempt with a retry loop (up to 5 attempts). Each attempt: click Submit → wait 3 seconds → check for CAPTCHA → if CAPTCHA appears send alert + wait 5 minutes for user to solve → check confirmation → if no confirmation retry. Loop continues until confirmed OR 5 attempts exhausted. Each CAPTCHA that appears triggers a fresh email + Mac notification. User just needs to solve each CAPTCHA as it appears and the pipeline handles the rest automatically

---

## ERROR 040
**Date:** 2026-05-31
**Status:** FIXED
**Error:** CAPTCHA Verify button hidden/inaccessible — user cannot click it to submit CAPTCHA
**What happened:** CAPTCHA bframe iframe appeared but Verify button at the bottom was cut off and inaccessible. Previous CSS fix only resized the iframe but parent containers had `overflow:hidden` which clipped the bottom of the CAPTCHA window
**Fix:** Updated CAPTCHA window fix to: (1) Walk up 10 parent elements from the bframe and remove `overflow:hidden`, `max-height`, `clip`, `clipPath` on each — unclips the container chain. (2) Position the bframe as `position:fixed` centered on screen at 310×500px with high z-index and red border. (3) Maximize browser viewport to 1440×900. (4) Print keyboard shortcut hint: click CAPTCHA area then Tab+Enter to reach Verify without clicking
**Workaround for immediate use:** Click anywhere in the CAPTCHA image grid → press Tab a few times until Verify button shows a blue focus outline → press Enter

---

## ERROR 041
**Date:** 2026-05-31
**Status:** FIXED
**Error:** Second CAPTCHA immediately after solving first — pipeline retried Submit too fast
**What happened:** NBI — user solved first CAPTCHA. Pipeline detected bframe gone, waited 1 second, then immediately retried Submit. Indeed fired a second CAPTCHA immediately. User couldn't solve it in time
**Fix:** After CAPTCHA solved, wait 5 seconds before returning (letting Indeed settle). Between Submit retry attempts, wait 8 seconds instead of 2. Gives Indeed time to process the solved CAPTCHA before another action triggers a new challenge

---

## ERROR 042
**Date:** 2026-05-31
**Status:** FIXED
**Error:** `What city and state do you currently reside?` — Claude answered wrong city (Salt Lake City UT instead of Delray Beach FL)
**What happened:** Ultradent Step 4 — Claude was asked for city/state and invented `Salt Lake City, UT`. Wrong data in the application
**Fix:** Added to `qa_answers.py`: `what city and state do you currently reside` → `Delray Beach, FL`, plus `city and state`, `current city and state`, `current location` all mapping to `Delray Beach, FL`

---

## ERROR 043
**Date:** 2026-05-31
**Status:** FIXED
**Error:** `Reason for leaving previous employers` — Claude answered but fallback showed blank, form rejected
**What happened:** Ultradent Step 4 — Claude answered correctly but there was a label mismatch between Claude's answer key and the actual field label. Field got filled blank. Also `No` label sent to Claude API every time
**Fix:** Added to `qa_answers.py`: `reasons for leaving previous employers`, `reason for leaving`, `why did you leave`, `if no previous employment` — all with appropriate pre-written answers. Added `no` → `No` to avoid Claude API calls for the No radio option label

---

## ERROR 044
**Date:** 2026-05-31
**Status:** FIXED
**Error:** `City *` filled with 'Chicago', `Address *` with '123 Main Street', `State` with 'New Jersey' — wrong cached values reused across jobs
**What happened:** On a previous job Claude was asked for `City *` and answered 'Chicago'. This got saved to SQLite cache. On all subsequent jobs `City *` → 'Chicago' was returned from cache. qa_answers.py had `city and state` key but NOT `city *` key, so the cache (wrong) won over qa_answers (correct)
**Fix:** Added all address field label patterns to qa_answers.py: `city *` → `Delray Beach`, `address *` → `7330 W Atlantic Ave Apt 215`, `state/province *` → `Florida`, `postal/zip *` → `33444`, `postal code *` → `33444`, etc. Also expanded startup SQL DELETE to clear ALL cache keys matching `Address%`, `City%`, `State%`, `Postal%`, `ZIP%`, `Street%` patterns on every run

---

## ERROR 045
**Date:** 2026-05-31
**Status:** FIXED
**Error:** Sponsorship question answered 'F-1 STEM OPT' instead of 'No'
**What happened:** `Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?` — this label contains the phrase `visa status`. The qa_answers partial match found `"visa status"` → `"F-1 STEM OPT"` before it found `"require sponsorship"` → `"No"` because visa status came first in the dict. Answer was factually wrong — F-1 STEM OPT does not require employer sponsorship
**Fix:** Added `"will you now or in the future require sponsorship for employment visa status"` as an exact key BEFORE `"visa status"` in qa_answers. Also added `"require sponsorship"` and `"sponsorship for employment"` as specific keys that all return `"No"`

---

## RULE VIOLATIONS

| Date | Error | Violation | Status |
|------|-------|-----------|--------|
| 2026-05-30 | ERROR 006 | Clicked "Apply on company site" external button | Fixed |

---

## KNOWN GOOD FLOWS

| Job | Steps | Result |
|-----|-------|--------|
| AUTOCONTENT — AWS Data Engineer | profile → resume-s → review | dry-run reached submit |
| IT Heroes — Data Engineer | profile → resume-s → questions → review | dry-run reached submit |
