# Workday Pipeline — Diagnostic Report

Sources: `workday_apply_now.py` (current, v1.9.3), `CHANGELOG.md`, `data/workday_applied_log.json`, `data/stuck_questions.json`, `screenshots/`, `data/debug_logs/`, `run_all.py`, `mail_reader.py`, `git log`. No code changed.

---

## Addendum — Reconciling "click timing" vs. "email verification loops"

### Does the pipeline handle email verification, or is it blind to it?

It is **not** blind to it — there's real handling, in two places in `workday_apply_now.py`:

1. **`_detect_intervention()` (lines 940–967)** scans page text for `_EMAIL_VERIFY_SIGNALS = ["click the link", "verification link", "check your inbox", "confirm your account", "activate your account"]` and returns `"email_verify"`. **`handle_intervention()` (lines 999–1009)** then calls `mail_reader.wait_for_otp(company=..., timeout_secs=300, since_minutes=10)`, which polls the user's real Gmail inbox over IMAP every few seconds for up to 5 minutes, finds the verification email, extracts the link, and navigates to it automatically (`mail_reader.py`, confirmed — real IMAP connection to `imap.gmail.com`, needs `GMAIL_USER`/`GMAIL_APP_PASSWORD` in `.env`).
2. **Inside `workday_create_account()` itself (lines 1658–1680)**, after a successful submit, it checks for `SUCCESS_PHRASES = ["check your email", "verify your email", ...]` and, if found, calls the same `mail_reader.wait_for_otp()` polling and auto-clicks the link.
3. **In `ensure_workday_auth()` (lines 1794–1809)**, if account creation reports success (`created == True`) but the immediate follow-up sign-in fails, the code explicitly labels this "portal needs email verification," prints instructions, and returns `False` to skip-and-retry-later — it does not loop here; it exits cleanly for this job.

So: there is a real, working, polling-based email-verification handler. It is not a stub.

### Where "email verification loops" actually came from

`run_all.py` lines 344–353:
```python
# ── Workday PAUSED ────────────────────────────────────────────────────────
# Workday has 0 successful applications out of 116 attempts (all-time).
# Every run hits email verification loops that require manual intervention.
# Re-enable by passing --workday-only when the auth issues are fixed.
_run_workday = args.workday_only  # only if explicitly requested
...
print(f"  (Workday PAUSED — 0/116 success rate, email verification loops)")
```
This is a **hardcoded comment and a hardcoded print string** — not a computed diagnosis. Nothing in `run_all.py` parses a log, counts a repeated pattern, or evaluates a live condition to produce this phrase; it's a static label that always prints, regardless of current state. `git log -S "email verification loops" -- run_all.py` shows it was introduced in a single large commit dated **2026-06-11** — a full month before the July 11–12 session that actually root-caused the verify-password fill bug, the button-click-registers-but-doesn't-progress bug, the page-misrouting bug, and the dropdown-guessing bug. It has never been updated since, even though `data/workday_applied_log.json` now shows 133 failures (not 116) and the pipeline went through 1.5.0 → 1.9.3 in between.

### Which explanation the evidence actually supports

**The click-timing/no-progress theory (Problem 1's own diagnosis) is what the current evidence supports — not an active email-verification loop.** Reasoning, traced through the actual control flow:

- For "email verification loops" to be the live cause, `workday_create_account()` would need to reach its `SUCCESS_PHRASES` check (line 1660) — i.e., the click would need to have *worked* well enough for Workday to render a "check your email" message. But the July 12 17:29–17:42 evidence (screenshots + changelog v1.8.1–1.8.3) shows the click never gets that far: `_submit_progressed()` reports no page change, no error, nothing — the function returns `False` at line 1653 ("Could not click Create Account button — skipping") **before ever reaching the success-phrase / email-verification branch.**
- With `created` = `False`, `ensure_workday_auth()` takes the `elif not created:` branch (line 1828: "Account creation failed — skipping job") — not the email-verification branch. There is no loop here; the job is simply skipped and the next job proceeds. Any "looping" in the historical sense would be the pipeline re-attempting the *same* company again on a *later run* (since no account was ever saved to `secure_store`) — not a live retry loop within one run.
- The "0/116... email verification loops" label most plausibly describes an **earlier era of the bug** (pre-June 11, before the current file even existed in its current form) where accounts may have been created successfully enough to reach a real "verify your email" wall that then genuinely got stuck (e.g., a slow/missing Gmail poll, or a dead-end after `handle_intervention` returned without a further retry path). That's a plausible real symptom **at some point in this project's history**, but it's not what's happening in the most recent (July 12) evidence, and the comment was never revisited to check.

**Verdict: both can be true, but describe different points in time — they're not the same live bug.** The click/no-progress issue is what's supported by the most recent, dated evidence (screenshots, `_submit_debug` reasoning, changelog v1.8.1–1.8.3). The "email verification loop" description is a stale, unverified label from a month earlier that was never re-checked against current behavior, and the current code path doesn't structurally support a *loop* at that stage anymore — a failed click now cleanly returns "skip," not a repeat cycle. Treat the June 11 comment as historical context, not current-state evidence.

### Live Create Account test — not run, and here's why

I attempted this and could not do it from here:
```
$ python3 -c "import playwright"          → ModuleNotFoundError: No module named 'playwright'
$ curl https://amgen.wd1.myworkdayjobs.com → 403 from proxy (domain not reachable from this sandbox)
```
This sandbox has no Playwright/Chrome install and no network path to `myworkdayjobs.com` (the outbound proxy blocks it). More fundamentally, even if it could reach the site, a real Create Account attempt needs things that only exist on your Mac: the real persistent Chrome profile (`.workday_session/`), and `mail_reader.py`'s real Gmail IMAP access (`GMAIL_APP_PASSWORD` in your `.env`) to catch the verification email. This isn't something to fake from a sandbox — it would either fail for the wrong (environmental) reasons or, if it somehow succeeded, would create a real account on Amgen's/whichever company's actual recruiting system using your real email.

**What I'd suggest running yourself, with the diagnostics already built into the code:**
```
cd ~/job_pipeline
python3 run_all.py --workday-only --wd-limit 1 --dry-run
```
- `--wd-limit 1` keeps it to a single company/job, matching "one live Create Account attempt."
- `--dry-run` only gates the final Submit step (`step_review_and_submit`, confirmed at line 2038) — account creation still runs for real either way, so this doesn't weaken the test, it just stops short of actually submitting a job application if it somehow gets that far.
- Screenshots (`fail_createacct_btn_failed_*.png` / `fail_createacct_stuck_*.png` / a success-path shot if it gets past the click) and `data/crash_logs/submit_debug_<company>_<timestamp>.json` (button diagnosis, network requests seen, before/after page markers) will be written automatically — no extra flags needed, that instrumentation is already in `workday_create_account()`.

Once you've run it, send me the new screenshot(s) and the `submit_debug_*.json` file (or just tell me to go read them from `data/crash_logs/` and `screenshots/`) and I'll read the actual outcome — success-then-awaiting-verification, silent failure, or something new — instead of guessing.

---

## Addendum 2 — Today's manual Workday run (2026-07-13, ~19:42–19:44 EDT): bot-detection evidence

You ran Workday manually today, but not through `run_pipeline.sh` (confirmed by reading it — that script only calls `indeed_apply_now.py` and `linkedin_apply_now.py`, hardcoded, no Workday, no log capture for Workday at all). So there's no `debug_logs/`, no `crash_logs/submit_debug_*.json`, no new screenshot, and no `workday_applied_log.json` entry for this run — none of the pipeline's own diagnostics fired. Everything below is reconstructed from Chrome's own History database inside `.workday_session/Default/History`, which still updates regardless of whether the pipeline logs anything.

**Reconstructed timeline (23:42:00–23:43:50 UTC, 110 seconds, 33 pages):**
- The pipeline discovers Workday jobs via Google: `site:myworkdayjobs.com "<job title>"` searches, one query per title, then clicks through several of that query's results before the next query.
- Pages loaded roughly every **3.4 seconds**, non-stop, across 24 distinct company portals — a company-to-company, page-to-page cadence with almost no variance, which reads as scripted rather than human browsing.
- **5 of those 24 job pages (~1 in 5) show a generic "Careers" tab title instead of the real job title** (Boeing x2, Brown Health, Ultra, Altera) — meaning Workday's React app hadn't finished rendering the actual job content before the script moved on to the next tab. This is direct, dated evidence of "jobs not opening properly": the pipeline is outrunning the page's own load time.
- Exactly **one** Create Account page was reached — DXC Technology's "Informatica ETL Developer" posting, at 23:43:20 — and the very next history entry, 3 seconds later, is already a fresh Google search. It didn't linger on Create Account, retry, or report a hard failure anywhere I can see — it just moved on. That's consistent with Problem 1's diagnosis (the click registers but the page doesn't visibly progress): whatever happened, it wasn't a successful account creation, and the pipeline had no way to tell the difference between "still processing" and "dead," so it gave up after one attempt and kept discovering new jobs instead of retrying or surfacing the failure clearly.

**Is Google/Workday actually detecting this as a bot? Yes — with a dated, confirmed incident, just not from today.** `History` also has this, from **yesterday, 2026-07-12 21:28:08–21:28:16 (8 seconds, 4 occurrences)**:
```
https://www.google.com/sorry/index?continue=https://www.google.com/search%3Fq%3Dsite%253Amyworkdayjobs.com...
```
`google.com/sorry/index` is Google's own "unusual traffic from your computer network" interstitial — the same wall a browser hits after firing near-identical automated searches too quickly. It fired 4 times in 8 seconds (once per back-to-back `site:myworkdayjobs.com "<title>"` query), confirming Google's systems flagged this exact search pattern as bot traffic at least once, one day before today's run. I found no equivalent `/sorry/` hit in today's history, but today's queries were spaced further apart than yesterday's near-back-to-back ones — that gap is a plausible reason today avoided tripping the same Google wall, not proof the pattern is safe.

**Net for "why didn't I apply to a single job":** account creation has never succeeded once across 133+ logged attempts (see Problem 1 above), and today's session shows the same shape — one account-creation attempt, abandoned within 3 seconds, no error surfaced. Since account creation is the gate, zero applications can go through regardless of anything else. The fast, uniform page-to-page pacing is a second, compounding issue: it's both why some job pages visibly don't finish loading (the "not opening properly" symptom) and a plausible reason Google/Workday's bot-detection has been triggered before.

No code changed. If useful next: re-run piping output to a file yourself (e.g. `python3 -u run_all.py --workday-only --wd-limit 1 --dry-run 2>&1 | tee data/debug_logs/run_manual_$(date +%Y%m%d_%H%M%S).log`) so the next attempt leaves real pipeline diagnostics instead of only a Chrome history trail — that's the gap that made today's run harder to read.

## Problem 1 — Create Account button doesn't get clicked

### 1. The code

`workday_create_account()` — `workday_apply_now.py`, lines 1292–1739. Relevant pieces:

**Filling the form (lines 1354–1370):**
```python
_fill_visible(
    ['input[data-automation-id="email"]', 'input[type="email"]'],
    email, "📧 Email"
)
_fill_visible(
    ['input[data-automation-id="password"]', 'input[type="password"]'],
    password, "🔑 Password"
)
_fill_visible(
    ['input[data-automation-id="verifyPassword"]'],
    password, "🔑 Verify password"
)
```
Checkboxes: every visible unchecked `input[type=checkbox]` / `[role=checkbox]` gets `.check()` → `.click()` → label-click fallback, each verified with `is_checked()` afterward (lines 1383–1417).

**Finding and clicking the button (lines 1438–1483, 1582–1633):**
```python
SUBMIT_SELS = [
    'button[data-automation-id="createAccountSubmitButton"]',
    'button[data-automation-id="createAccount"]',
    'button[data-automation-id="registerButton"]',
    'button[data-automation-id="submitButton"]',
]
...
# Method 1: Playwright click
submit_btn.click(timeout=5000)
submitted = _submit_progressed(patience_s=10.0)
# Method 2: JS click with disabled-attr removal (fallback)
# Method 3: Enter key on verify-password field (fallback)
```
`_submit_progressed()` (1543–1580) polls for the page to actually change, the create-account form to disappear, or a Workday error to render — not just "click didn't throw."

### 2. What the most recent logs show

There is no live evidence more recent than **2026-07-12, 17:29–17:42** — four companies tested (Amgen, Ryan Specialty, Vizient, NBA), all on the same run:

| Time | Company | Screenshot | Outcome |
|---|---|---|---|
| 17:29–17:30 | Vizient, NBA | `fail_createacct_stuck_*` | Form fully filled (incl. Verify Password), checkbox checked, button visibly enabled — click had zero effect |
| 17:38 | Amgen | `fail_createacct_btn_failed_amgen_20260712_173839.png` | Same |
| 17:40 | Ryan Specialty | `fail_createacct_btn_failed_ryansg_20260712_174015.png` | Same |
| 17:41 | Vizient | `fail_createacct_btn_failed_vizient_20260712_174148.png` | Same |
| 17:42 | NBA | `fail_createacct_btn_failed_nba_20260712_174250.png` | Same |

`data/workday_applied_log.json` (152 entries total): **133 `Failed`, 19 `Dry-Run`, 0 successes, ever.** The last entries are all `Failed` for Cni / Onemagnify / Nadara / Ryansg / Amgen. No `run_*_workday.json` file exists in `data/runs/`, and no `data/debug_logs/*.log` file since has any Workday content — meaning **Workday has not actually run since that July 12 17:29–17:42 session.** It's been sitting auto-paused (`run_all.py`'s own note: "0/116 success rate, email verification loops"), so there is no more recent data to pull — this is genuinely the latest.

### 3. Which blocker is it — answered with evidence, not guessed

Ruled out, with direct evidence in `CHANGELOG.md`:

- **Button disabled by unmet validation** — ruled out. NBA's screenshot shows a visible blue focus ring on the button (proof the click landed on it), and the button was confirmed enabled by `_btn_disabled()` before any click was attempted (v1.8.1 entry).
- **Validation error rendered but unread** — ruled out for this specific symptom. `_read_workday_errors()` scrapes the error panel and the code checks for it; on the 4 companies tested, no error text appeared (v1.8.2/1.8.3 notes: "the click genuinely has zero effect... not just a timing issue").
- **Overlay/cookie banner/spinner covering the button (Indeed-CAPTCHA-style)** — this was checked directly via a JS diagnostic (`_diagnose_button()`, lines 1500–1526) that reads `document.elementFromPoint()` at the button's center and reports `coveredBySomethingElse`. Per the code comments, Playwright's own click already "passed all of its actionability checks (element visible, stable, not covered, not disabled) and threw no exception on all 4 companies" — so nothing is sitting on top of it. This is **not** the Indeed-CAPTCHA pattern.

**What it actually is, per the pipeline's own historical diagnosis:** the click is a real, unintercepted mouse event that lands on a real, enabled button — but the page doesn't advance. The working theory as of the last fix (v1.8.3) is a timing/response issue: the original code waited only ~3s after the real click before firing two rougher fallback methods (JS click, Enter key) at what might still be an in-flight, server-side account-creation request (email dispatch + validation can plausibly take longer than 3s) — and firing 2–3 conflicting submits into a live request is a plausible way to get "nothing visibly happens." v1.8.3 widened that wait to 10s and made a Workday error/toast count as "real progress" too (so a genuine rejection doesn't get piled on with more clicks).

**This fix has never been confirmed live.** No `data/crash_logs/submit_debug_*.json` file exists (the diagnostic file the code writes when a click still fails), and there's no debug log or screenshot after 17:42 on July 12 — Workday auto-paused itself before v1.8.3 could be tested against a real Create Account attempt. So the honest state is: **root cause is diagnosed (click lands, response is either slow or silently rejected) and a fix is written, but whether it actually works is unverified** — all attempts since have been Indeed CAPTCHA work (v1.9.0–1.9.3), not Workday.

---

## Problem 2 — Can't correctly answer per-application custom questions

### 1. Current approach — walkthrough

Two functions, both in `workday_apply_now.py`, both using the **same layered fallback chain**:

**`_smart_fill_questions()`** (line 2102) handles native `<input>`/`<select>`/`<textarea>` and radio/checkbox groups. It:
1. Scrapes every visible, not-already-filled field via a JS query, resolving each field's label from `data-automation-id` → associated `<label>` → `aria-label`/`placeholder` → a `formField`/`fieldset` ancestor walk (lines 2132–2231).
2. Filters out auth/honeypot fields (`NEVER_FILL_LABELS`).
3. For each field, tries answers in order:
   - **Layer 1** — `qa_answers.py` (manually curated, no API call)
   - **Layer 2** — `claude_answers.py` (Claude's own past answers, saved automatically — a growing cache)
   - **Layer 3** — SQLite cache (`answer_cache.py`, legacy)
   - **Layer 4** — a live Claude call (`cfg.CLAUDE_MODEL_FAST`) for whatever's still uncached, given the field label + type + options + a candidate-profile block, with hardcoded rules (work auth → "Yes", sponsorship → "No", EEO/demographic → "I don't wish to answer", etc.)
   - **Layer 5** — `PROFILE_FALLBACK`, a keyword→value dict (e.g. `"relocat"` → `"No"`), used only if Claude fails or is unavailable, so a field is never left silently blank.
4. Fills the DOM: native `<select>` by matching option text, radio/checkbox groups by clicking the matching `<label>`/option, text/textarea via a React-safe property-setter (`Object.getOwnPropertyDescriptor(...).set.call(el, ans)`), then a second Playwright `.type()` pass for text-like fields specifically for React-controlled inputs.

**`_smart_fill_custom_dropdowns()`** (line 2498) is a **separate** function for Workday's non-native button+listbox widgets (e.g. "How Did You Hear About Us?"), which `_smart_fill_questions()`'s `input, select, textarea` selector can't see at all. It:
1. Finds required buttons via `aria-required` or a `*` in a nearby label, **plus** any field name Workday's own "Errors Found" panel names explicitly (ground truth over heuristic).
2. Opens each dropdown for real and scrapes the actual rendered option text (not a guessed list).
3. Resolves an answer via the same QA → claude_answers → cache → live Claude call chain, with a first-option fallback if literally nothing else works — a required field is never left on "Select One."

### 2. Recent failed-application evidence

**This is the key finding: there is no recent live-run evidence for Problem 2.** `data/stuck_questions.json` (193 entries) has **zero Workday-related entries** — it's populated only by Indeed/LinkedIn runs. And per Problem 1's evidence, Workday hasn't successfully created an account (let alone reached a questions page) since **2026-07-11 22:34–22:38** — the last window before account-creation became the hard blocker. Everything below is from that window and earlier, not from "the last 5–10 failed applications" in a fresh sense — those don't exist yet, because Problem 1 is upstream of Problem 2 and has been blocking it for over 36 hours of attempts.

From that last reachable window (Amgen, 2026-07-11 22:32–22:38, screenshots `step_before_unknown-step-1` / `step_stuck_unknown-step-1_223254.png`):

| Field | What Workday rendered | What the pipeline did |
|---|---|---|
| "How Did You Hear About Us?" | Custom button+listbox, real options like "Company Website" / "Employee Referral" | Left on "Select One" — the only options ever tried were a hardcoded guess list ("LinkedIn"/"Indeed"/"Job Board"/"Online") that matched nothing |
| "Phone Device Type" | Custom button+listbox | Left on "Select One" — no handler at all fired on this page (see category below) |
| "State" | Custom button+listbox | Same — untouched |
| "Have you worked at Amgen before?" | Radio group | Auto-defaulted to "Yes" (first DOM option), opening unwanted follow-up fields — wrong answer, not a blank one |

### 3. Failure-pattern categories

This is **not one recurring root cause** — the changelog documents (and fixes) three genuinely distinct categories, in order of discovery:

**Category A — Page-type misrouting (fixed v1.6.7, then hardened v1.7.0).** Amgen's contact-info-equivalent page never matched `is_contact = _exists(page, WD["contact_page"], ...)`, so it fell into a generic "unknown step" fallback that had no field handlers wired into it at all — not a wrong answer, but *no attempt made*. Fixed by adding redundant field handlers directly into the unknown-step path. Root cause of the misdetection itself (why Amgen's markup didn't match `contact_page`) was never confirmed — no live DOM access from this sandbox — the fallback was hardened instead of the detector fixed.

**Category B — Custom dropdown treated as free text / guessed blind (fixed v1.6.7→v1.7.0).** Example: "How Did You Hear About Us?" is a Workday button+listbox component, structurally invisible to `_smart_fill_questions()`'s `input, select, textarea` query — this is exactly the "custom dropdown doesn't match standard `<select>` handling" pattern you asked about. Before the fix, whatever handler existed used a hardcoded keyword guess-list per field, which broke the instant a company's real options didn't match the guess (Amgen's options were nothing like the guessed ones). Fixed by building `_smart_fill_custom_dropdowns()`, which reads the real rendered options before answering instead of guessing.

**Category C — Silent success-reporting from a helper that never checked whether its own click worked (fixed v1.7.1).** `_select_dropdown()` — the function underlying State/Phone-Device-Type/gender/veteran/degree selection — called `_click()` without checking its return value, typed into whatever had keyboard focus (even if the target button was never actually found), and unconditionally returned `True`. Every "if this failed, try a fallback selector" chain in the file was built on top of this and could never actually trigger, because the first attempt always reported success even when it silently did nothing. This is a different bug from Category B: B is "the code doesn't understand a custom widget," C is "the code lies about whether it filled *any* field, custom or not."

**Category D — Wrong answer chosen for a Yes/No radio group (fixed v1.6.7).** Distinct from A/B/C — this isn't a missing handler or a mis-clicked button, it's a *wrong default*: the radio-group filler picked whatever option Workday put first in the DOM for any unanswered group, which happened to be "Yes" for "Have you worked at Amgen before?" — usually the wrong answer, and one that opens a cascade of new required fields. Fixed with a targeted regex on the question text to force "No" for this specific question shape.

No evidence exists in this codebase's logs for the other categories you asked about by name — numeric fields, multi-select checkboxes, free-text essay questions, or EEO/demographic questions specifically on **Workday** — those failure types are documented for **Indeed** (`indeed_apply_now.py`, e.g. the Air Treatment Corporation false-"submitted" bug in v1.8.7, essay questions left blank) but not for Workday. Don't assume they don't exist on Workday — they've simply never been observed there, because no Workday run has gotten past account creation long enough to hit them.

### 4. What's unverified vs. confirmed

Categories A–D above all have applied fixes (v1.6.7–v1.7.1). **None of them have been confirmed live since** — v1.7.1's own changelog entry ends with "Still not confirmed with a live re-run — that's next," and the very next version (v1.7.2) pivoted to the account-creation Verify-Password bug because sign-in/account-creation became the new blocker before a fresh end-to-end Amgen run could happen. So Problem 2's fixes are sitting in the code, reasoned from real screenshots, syntax-checked — but genuinely untested against a live page since 2026-07-11.

---

## The one finding that connects both problems

**Problem 1 is now fully upstream of Problem 2.** Since no Workday account has been created successfully in the current logs (0 for 133), no run can reach the custom-question-filling code at all — so even if Category A–D's fixes are wrong or incomplete, there's no way to find out without fixing Problem 1 first. Any next step should treat Create Account as the literal first gate to clear before Problem 2 can be re-diagnosed with fresh evidence.
