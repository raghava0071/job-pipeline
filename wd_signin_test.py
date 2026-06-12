#!/usr/bin/env python3
# Live test of the FIXED workday_sign_in(). Navigates to a job, clicks Apply,
# calls the real sign-in function, reports result + final page state.
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path.home() / "job_pipeline"))
import config as cfg
import workday_apply_now as W

JOB_URL = "https://bdx.wd1.myworkdayjobs.com/en-US/EXTERNAL_CAREER_SITE_USA/job/Product-Data-Analyst-III_R-546737-1"

def main():
    email, password = cfg.CANDIDATE_EMAIL, W._get_wd_password()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch_persistent_context(
            str(W.SESSION_DIR), headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width":1366,"height":900})
        page = b.pages[0] if b.pages else b.new_page()
        page.goto(JOB_URL, wait_until="domcontentloaded", timeout=30000); time.sleep(3)
        W.click_workday_apply(page); time.sleep(3)
        if W._exists(page, W.WD["apply_manually"], timeout=3000):
            W._click(page, W.WD["apply_manually"]); time.sleep(2)
        print("\n--- calling workday_sign_in() ---")
        ok = W.workday_sign_in(page, email, password)
        print(f"\n=== RESULT: workday_sign_in returned {ok} ===")
        print("final URL:  ", (page.url or "")[:120])
        print("final title:", page.title())
        on_form = any(W._exists(page, s, timeout=1500) for s in [
            W.WD["contact_page"], W.WD["experience_page"], W.WD["app_questions_page"],
            W.WD["voluntary_page"], W.WD["review_page"]])
        print("on application form:", on_form)
        page.screenshot(path=str(W.SCREENSHOTS / "signin_test_result.png"))
        print("📸 screenshots/signin_test_result.png")
        time.sleep(6)
        b.close()

if __name__ == "__main__":
    main()
