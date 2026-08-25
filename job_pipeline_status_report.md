# Job Pipeline — Status Report
**As of:** Monday, August 10, 2026
**Current version:** 2.1.0 (`config.py`)

---

## Summary in plain terms

This is a personal tool that automatically applies to jobs on Raghav's behalf. It's supposed to search three job sites (LinkedIn, Indeed, Workday) every day at 8am, 12pm, and 6pm, pick out jobs that are a good fit, customize a resume for each one, and submit the application — all without a person clicking anything.

**Right now, two of the three job sites aren't producing any applications, and the third is unreliable:**

- **LinkedIn** (the main one that still works end-to-end) is crashing on most of its daily runs, including both runs earlier today, because it can't log in. Nobody has fixed this yet.
- **Indeed** stopped applying automatically three weeks ago. The website LinkedIn/Indeed use to block robots (a security check called Cloudflare) figured out the tool was a robot and won't let it through, even when a human tries to solve the "prove you're human" step. So now the tool just prepares a list of search links and hands them to Raghav to click through himself in his own browser — but there's no record of whether that's actually happening.
- **Workday** has been switched off entirely for about a month. The reason given ("email verification loops") is outdated — the real bug was actually found and fixed back on July 14, and a test right after the fix showed it working on 3 different companies. But nobody ever turned it back on, so it's sitting disabled for no current reason.

On top of that, the last three weeks of code changes have never been saved to the project's backup system (git), so if something on Raghav's computer breaks, that recent work could be lost.

**Bottom line: the tool has essentially stopped submitting new job applications on its own right now,** and needs someone to (1) fix LinkedIn's login crash, (2) decide whether to turn Workday back on now that its bug is fixed, and (3) check whether the Indeed hand-off is actually being used.

Everything below this point is the same information in much more technical depth, for reference.

---

## What this project is

An automated job-application system (`~/job_pipeline`) that scrapes live listings from LinkedIn, Indeed, and Workday, scores fit with either Claude or a free keyword-matching engine, builds a tailored ATS resume per job, fills out the application form with Playwright browser automation, and submits. It's scheduled via macOS `launchd` to run three times a day — 8am, 12pm, 6pm — orchestrated by `run_all.py`, which launches the three platform engines (`linkedin_apply_now.py`, `indeed_apply_now.py`, `workday_apply_now.py`) and emails a summary after each run.

---

## Where things stand right now, platform by platform

### LinkedIn — primary channel, but crashing on most runs
LinkedIn is the only platform that still fully automates end-to-end (search → score → fill → submit). It's also the one currently breaking most often.

Every LinkedIn crash traces to the exact same line: `linkedin_apply_now.py:1766`, inside `ensure_login()`, on `page.goto("https://www.linkedin.com/feed/", timeout=20000)`. It's been failing intermittently since at least **July 25** and is still happening **today**:

| Date | Crashes logged |
|---|---|
| Jul 25 | 3 (8:20am, 12:11pm, 6:34pm — all three runs that day) |
| Jul 26 | 2 |
| Jul 27 | 2 |
| Aug 3 | 1 |
| Aug 5 | 3 (all three runs) |
| **Aug 10 (today)** | **2 so far** (8:17am, 12:16pm) — same failure both times |

This has never been root-caused or fixed in the CHANGELOG — there's no entry addressing it. It's an open, unaddressed, recurring bug on the pipeline's main working channel.

Actual applied volume has dropped off as a result. Recent daily "Applied" counts from `apply_log.json`:

| Date | Applied |
|---|---|
| Jul 31 | 4 |
| Aug 1 | 2 |
| Aug 2 | 2 |
| Aug 3 | 0 |
| Aug 4 | 5 |
| Aug 6 | 0 |
| Aug 7 | 0 |
| Aug 8 | 4 |
| Aug 9 | 0 |
| Aug 10 (today) | 0 so far |

Across 8,433 total logged LinkedIn attempts (all time): 956 Applied, 6,894 Below fit-score Gate, 437 Failed, 71 Needs Manual Apply. The 72% fit threshold (raised from 60% on 2026-07-10 to trade volume for match quality) is filtering out the large majority of scraped jobs by design — that's expected, separate from the crash issue.

### Indeed — automation abandoned on purpose, now a manual hand-off
As of **v2.1.0 (Jul 21)**, Indeed no longer applies automatically at all. Root cause, confirmed with real evidence: Indeed's Cloudflare Turnstile wall blocks any automated browser outright — Raghav opened indeed.com in his real Chrome and it worked fine, while the pipeline's browser stayed stuck on "Verify you are human" every time, even after weeks of stealth attempts (real-Chrome channel spoofing in v1.3.0, stripping the `--enable-automation` flag in v2.0.0). Both failed. The team's conclusion: this is unbeatable, not a bug to keep chasing.

The pipeline now generates `data/indeed_handoff.html` — a dashboard of ~54 pre-built search links (same entry-level/last-7-days filters the old automation used), grouped by category, with tailored resumes linked — and opens it in the real default browser for Raghav to click through by hand. It regenerates fresh every run (confirmed: `indeed_handoff.html` was rebuilt again at 12:05pm today).

One consequence worth flagging: since Indeed no longer applies automatically, `indeed_applied_log.json` — the file that used to track real submissions — has had **zero new entries since July 14**, before the switch. There's currently no logging of what happens after Raghav clicks through the dashboard, so there's no visibility from the pipeline's side into how many Indeed applications are actually going out via the manual hand-off, or whether it's happening at all.

### Workday — hard-disabled by a stale, three-week-old flag
Workday is currently forced off in code. `run_all.py` only runs it if you explicitly pass `--workday-only`; every scheduled run prints `"Workday PAUSED — 0/116 success rate, email verification loops"`.

That label is misleading. A diagnostic review of the code (`workday_pipeline_diagnostic_report.md`) traced it to a **hardcoded comment from June 11** that has never been updated — it isn't computed from any live check. The actual log (`workday_applied_log.json`) now shows 133 failures / 0 applied out of 162 attempts, and the real, current root cause (as of the most recent evidence, July 12–14) isn't "email verification loops" at all — it's that the Create Account button's click wasn't registering because an invisible overlay `<div>` was intercepting the click instead of the real button underneath it.

That overlay-click bug was actually **fixed in v1.9.5/v1.9.6 (Jul 14)**, and the changelog reports a live run right after the fix got 3 companies (Boeing, Relx, Spgi) successfully through Create Account. But nobody has flipped the pause off since — the code still hard-stops Workday every scheduled run regardless of whether the underlying fix holds up. This is worth a manual test (`python3 run_all.py --workday-only --wd-limit 1 --dry-run`) to see if it's actually usable again, rather than staying paused indefinitely on an outdated assumption.

---

## Complete Workday issue history (from CHANGELOG.md, 89 entries reviewed)

Workday went through a full rewrite (2,677 lines, based on two reference GitHub projects) but has **0 successful applications out of 162 attempts, all time**. Here is every distinct bug found along the way, in order:

- **Jun 4–9:** first 137 attempts — 0 applied, 131 failed, 6 dry-run. Paused after this with the comment still shown today: "0/116 success rate, email verification loops."
- **v1.5.1 (Jul 11):** `_get_wd_password()` was missing its `return` statement — every sign-in and account-creation attempt, on every company, ran with password = `None`. Fixed.
- **v1.5.2 (Jul 11):** credentials were stored in **plaintext** despite the code's own header comment claiming encryption — the Fernet/AES encryption existed but was only ever used to *downgrade* an old encrypted file to plaintext, never to actually encrypt on save. Fixed.
- **v1.5.3 (Jul 11) — security:** `.gitignore` rules meant to keep `.env` and Workday credential files out of git were silently no-ops, because git doesn't support trailing `# comment` text on a pattern line, and nearly every security-relevant line in the file was written that way. Fixed.
- **v1.6.1 (Jul 11):** `workday_apply_now.py` wasn't in the pipeline's startup syntax pre-flight check, which is exactly why a real syntax error from a June 20 commit went undetected for three weeks. Fixed.
- **v1.6.4 (Jul 11):** the consent checkbox on Create Account logged "checked" unconditionally, without ever verifying the click actually took. Fixed (3-method escalation + real verification).
- **v1.6.5 (Jul 11):** sign-in's Email field was silently empty — a DOM-scoping bug was filling the *hidden* Create-Account form's email input instead of the visible Sign-In one (Workday renders both forms in the DOM at once). Fixed.
- **v1.6.6 (Jul 11):** `CANDIDATE_EMAIL`/`CANDIDATE_PHONE` were never actually being read from `.env` (nothing in the pipeline loads `.env` into the process environment), so Workday auth had been silently using the literal placeholder `"your.email@gmail.com"` this entire time. Fixed.
- **v1.6.7 (Jul 11):** Amgen's contact-info page wasn't recognized as a "contact page" and fell through to a generic fallback, leaving 3 required fields blank/wrong plus a radio question wrongly defaulted to "Yes". Fallback hardened; the actual misdetection was never root-caused (no live DOM access available to confirm why).
- **v1.7.0 (Jul 11):** three structural gaps — the form was re-typing fields that were already correctly filled on every retry loop; custom dropdowns were guessed via hardcoded keyword lists instead of reading the real on-page options; Workday's own validation-error panel was never read after a failed submit. All three fixed.
- **v1.7.1 (Jul 11):** even after 1.7.0, the same Amgen fields were still broken — traced to `_select_dropdown()` never checking whether its own click succeeded, which silently defeated every fallback chain in the file (they all believed the first attempt worked). Fixed, plus a new label-based fallback added.
- **v1.7.2 (Jul 11):** "Verify New Password" wasn't filling via Playwright's `.fill()` on some portals, permanently breaking sign-in for that company; separately, a failed Create Account attempt could still get saved as a "success," poisoning every future sign-in for that company. Both fixed.
- **v1.7.3 (Jul 11):** applied a DOM-stability wait (in case React hadn't finished rendering before the page-type scan ran) as a plausible fix for the still-unexplained Amgen misdetection from 1.6.7. Not confirmed live.
- **v1.8.0 (Jul 12):** two blockers found together — every Workday job was scoring exactly 0% because `anthropic` silently failed to import under the Python interpreter actually being used (environment mismatch, zero visible error); and the browser was crashing on launch (same instability bug already fixed for Indeed, ported over). Both addressed.
- **v1.8.1 (Jul 12):** the Create Account button was visibly clicked (confirmed by screenshot — focus ring and all) but had zero effect — the 3-method submit fallback declared success the instant a method ran without throwing, so methods 2 and 3 never got a real chance to run. Fixed with a real "did the page actually change" check.
- **v1.8.2 (Jul 12):** diagnostic-only pass — added button-state and network-request logging after the click still had zero effect on all 4 companies tested.
- **v1.8.3 (Jul 12):** widened the wait after the first click attempt from 3s to 10s, reasoning Workday's backend needed more time to respond before piling on fallback methods.
- **v1.9.4 (Jul 14):** confirmed Workday had never had the free-scoring-fallback toggle that Indeed/LinkedIn already had, so it had zero protection against Claude being unreachable — this is the same root cause as 1.8.0's 0% scores, just fixed properly this time with the shared toggle.
- **v1.9.5 / v1.9.6 (Jul 14) — the real fix:** found the actual root cause of the Create Account click failure — an invisible overlay `<div>` (`aria-hidden`, pulled out of the tab order) was sitting on top of the real button and intercepting every click. Fixed generically by clicking whatever element is actually covering the button's center point. **Live-verified working** — the very next run got 3 companies (Boeing, Relx, Spgi) successfully through Create Account.
- **v1.9.7 (Jul 14):** one company's "check your email to verify" state was handled correctly, another's identical state (different wording) was wrongly treated as a hard failure. Fixed by broadening the phrase match.
- **v1.9.8 (Jul 14):** Boeing crashed mid-form after passing Create Account — an unconditional `import anthropic` with no error handling on any machine that doesn't have it installed. Fixed by removing the live-Claude form-filling call entirely (Raghav's explicit call: no ongoing API cost for this).

**Where it stands:** the specific bug that was blocking every Create Account attempt (the overlay-click interception) was fixed and live-verified on July 14 — 3/3 companies tested succeeded right after. Despite that, `run_all.py` still hard-disables Workday on every scheduled run behind the original, never-updated June 11 comment ("0/116... email verification loops"), which a later diagnostic review confirmed is describing an outdated failure mode, not the current code. Nobody has re-tested it since July 14.

---

## Complete Indeed issue history (from CHANGELOG.md, 89 entries reviewed)

Indeed's automated applying is now fully retired (as of v2.1.0, Jul 21) in favor of a manual click-through dashboard. Here's the path that led there:

- **Late June:** healthy volume, 376–465 total scraped/attempted per day.
- **v1.0.4 (Jun 28):** a JS dialog firing after the browser/page had already closed was killing entire runs in under 30 seconds. Fixed.
- **v1.0.7 (Jul 2):** a CAPTCHA-cooldown loop with no cap could run all day unattended with nobody there to solve it; separately, runs were grinding for 596–607 minutes against searches returning 0 results. Fixed with a cooldown cap and an empty-query bail threshold.
- **v1.0.8 (Jul 2):** decimal-number answers (e.g. "2.5 years") were being rejected by the site's own form validation; separately, the stuck-question logger had been recording an empty field list on every single stuck entry, so the diagnostic tool for this exact class of bug was itself broken. Both fixed.
- **v1.0.10 (Jul 2):** a log-merge bug had been polluting `indeed_applied_log.json` with LinkedIn's entire application history — 119,126 records / 49MB, of which only 2.9% were actually Indeed's own. Fixed and cleaned up (dropped to 4,953 records / 3.7MB).
- **v1.0.11 / v1.0.12 (Jul 3):** the pipeline kept waiting the full 10 minutes after a CAPTCHA was already solved, because reCAPTCHA never removes its iframe from the page, just hides it, and the check was presence-based, not visibility-based. Fixed — then a second bug from the same root cause caused CAPTCHA-detected alert emails to spam every ~8 seconds, since the fix wasn't applied to all the places that checked it. Also fixed.
- **v1.1.1 (Jul 6):** applied volume had dropped from ~20-30/day to near 0 — root cause was the Indeed session being logged out and Cloudflare-blocked, requiring a fresh manual login (not fixable in code).
- **v1.1.2 / v1.1.3 (Jul 6):** search queries had no experience-level filter, so entry-level searches were returning senior/irrelevant roles; separately, a soft-blocked session wasn't always returning exactly 0 cards (sometimes 1-2 trickled through), which reset the existing block-detection counter and let a blocked run grind for hours. Both fixed.
- **v1.1.5 (Jul 6):** startup cleanup was deleting Chromium's lock files even when the process holding them was still alive, causing a real launch collision. Fixed with a PID-liveness check.
- **v1.2.0 (Jul 6):** switched default scoring from Claude to a free keyword-based scorer to cut ongoing API cost — a deliberate tradeoff, with a known, tested gap (it can score an obviously mismatched job, like a Registered Nurse posting, as a 61% "apply").
- **v1.2.1 (Jul 6):** a deeper version of the same dialog-crash bug from v1.0.4 recurred (this time inside Playwright's own driver, uncatchable by the earlier fix); crashed sessions were also leaving stale lock files that blocked the *next* launch for a full 3-minute timeout. Fixed with a crash-safe cleanup handler and a shorter launch timeout.
- **v1.2.2 (Jul 8):** discovered the stale-lock cleanup had never actually worked since it was written — a symlink-vs-target bug meant `Path.exists()` always returned False on the lock files it was supposed to be checking. This was very likely the real cause behind a large fraction of "launch fails right after a crash" incidents over the prior weeks. Fixed.
- **Ops note (Jul 8):** the Indeed browser profile itself was concluded to be corrupted beyond a code fix (accumulated broken lock files from every unclean shutdown since June 28) — backed up and reset to a fresh profile, requiring a one-time manual re-login.
- **v1.2.3 – v1.2.5 (Jul 8):** a cluster of form-answering bugs — opaque hash-labeled fields were defaulting to "Yes" regardless of the actual question (silently answering education-level and clearance questions wrong and looping forever); a resume-upload failure was being reported as a success; an EEO/demographic single-checkbox "Agree" widget had no matching label so it could never be checked. All three diagnosed and patched; not all were confirmed against a live run at the time.
- **v1.3.0 (Jul 9):** added stealth patching (real Chrome, `navigator.webdriver` spoofing) specifically to reduce how often CAPTCHAs triggered in the first place, based on how other open-source job-application bots handle this (nobody seriously tries to auto-solve reCAPTCHA v2 — they avoid triggering it).
- **v1.3.1 / v1.3.2 (Jul 10):** the CAPTCHA popup-pinning logic didn't account for taller challenge-grid variants, leaving the Verify button unreachable below the viewport — required manual cancellation twice.
- **v1.3.6 (Jul 10):** the staffing/consultancy filter was silently excluding TCS, Infosys, Wipro, Cognizant, HCL, and Tech Mahindra — unblocked at Raghav's request for US-based roles at those specific large firms; smaller IT-services firms remain blocked.
- **v1.3.7 / v1.3.8 (Jul 11):** the login-wait loop had zero Cloudflare-awareness and was blindly re-requesting a blocked page up to 60 times (every 5 seconds, for 5 minutes) on every pipeline start — actively making an existing block worse. Fixed (Cloudflare detection added, recheck interval widened to 1 minute).
- **v1.4.0 (Jul 11):** confirmed via a direct side-by-side test (regular Chrome worked fine on the same IP/network while a freshly-reset pipeline session was blocked immediately) that this rules out stale cookies and IP reputation as the cause — pointing at the automated browser build itself.
- **v1.6.2 (Jul 11):** two compounding bugs found from one log — `ensure_login()`'s give-up paths never actually stopped the run, so a failed login still led into a full 54-query search loop; and the search retry loop couldn't tell "the browser window closed" from "a network blip," retrying uselessly for up to 40 minutes against a dead browser. Both fixed.
- **v1.9.0 – v1.9.3 (Jul 13):** a multi-round investigation into CAPTCHAs appearing to "solve" themselves in ~1 second and then immediately reshaping — eventually traced to the solved-check reading an unrelated, wrong token element elsewhere on the page. Fixed with container-scoped token matching plus a hard 3-second minimum-solve-time floor as a backstop. Also found (separately, same week): two `run_all.py` processes were firing simultaneously from an unidentified scheduler entry outside this project's own 3 launchd jobs — never located or removed (this environment can't inspect `~/Library/LaunchAgents` directly). A process-level lock was added as a structural fix regardless.
- **v1.9.9 (Jul 21):** after weeks stuck at Indeed's "are you a robot?" Cloudflare checkbox — even solving it by hand didn't pass, confirming a classic automation-fingerprint block rather than anything solvable from the user side. Added a single centralized human-readable error log (`data/pipeline_errors.log`).
- **v2.0.0 (Jul 21):** last stealth attempt — stripped the `--enable-automation` Chrome flag. Described by its own author as "explicitly a long shot" even before trying it.
- **v2.1.0 (Jul 21) — the decisive test:** real Chrome reached Indeed fine from the same machine/IP while the pipeline's automated browser stayed stuck on the Cloudflare wall — proof the automation itself is what's blocked, not the account or network. Concluded unbeatable after this. Automated Indeed applying was retired entirely in favor of the current hand-off dashboard.

**Where it stands:** since the switch, `indeed_applied_log.json` (the file that tracks real submissions) has had zero new entries — it stopped updating July 14, a week *before* the switch even shipped, meaning there's no data on Indeed applications from either the last week of automation or the three weeks of hand-off mode since.

---

## What's actually broken right now (ranked by impact)

1. **LinkedIn's login step is timing out/crashing on most runs, including both of today's** — this is the pipeline's only fully-working platform, and it's currently unreliable. No fix has been attempted in the changelog. This is the highest-priority item.
2. **Workday is disabled by a stale flag, not a current diagnosis** — the code comment describing why it's off predates the actual fix that may have solved the problem. Nobody has re-tested it in ~4 weeks.
3. **Indeed has a visibility gap** — the pipeline builds the hand-off dashboard correctly, but has no way to know whether Raghav is actually clicking through it or how many applications result. It could be working great or not happening at all; there's no data either way.
4. **Three weeks of local work has never been committed to git.** `config.py`, `run_all.py`, and `CHANGELOG.md` have carried the entire v2.1.0 Indeed hand-off feature as uncommitted working-directory changes since **July 21** — the project's own `CLAUDE.md` rules state "local git is the primary safety net," but that safety net currently doesn't cover the most recent feature. Separately, the local repo is 98 commits ahead of `origin/main` (GitHub), so the secondary/offsite backup is also stale.

---

## What's working / not broken

- The 6-layer fake-job filter, staffing/consultancy filter, and CAPTCHA-handling logic (after several rounds of fixes through v1.9.x) all appear stable — no recent errors tied to them.
- Resume tailoring and cover-letter generation are untouched and not implicated in any current issue.
- The `caffeinate`-based Mac-sleep fix (v2.0.0) appears to have worked — no repeat of the 46-hour runaway run since it shipped.
- The singleton lock system correctly blocks overlapping runs (seen twice in the older logs, working as designed).

---

## Suggested next steps

1. Get a live LinkedIn login trace (watch one run, or check whether the LinkedIn session cookie needs a fresh manual login) — this is the one actively costing the most applications right now.
2. Run `python3 run_all.py --workday-only --wd-limit 1 --dry-run` once to see if the July 14 overlay-click fix actually holds, then decide whether to lift the pause and update the stale comment either way.
3. Decide whether Indeed's hand-off dashboard is being used — if not, it's not producing any applications at all right now.
4. Commit the three weeks of uncommitted local changes (`git add -A && git commit`), per the project's own safety workflow, then push to GitHub to restore the offsite backup.
