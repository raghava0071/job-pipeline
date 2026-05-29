#!/opt/anaconda3/bin/python3
"""
DIAGNOSTIC SCRIPT — runs ONCE on the Unity job to see exactly what LinkedIn shows.
Run: python test_apply.py
Watch the browser AND the terminal output.
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

SESSION_DIR = Path.home() / ".linkedin_session"
# Unity Design job URL from previous run
JOB_URL = "https://www.linkedin.com/jobs/view/4234218085"

with sync_playwright() as pw:
    browser = pw.chromium.launch_persistent_context(
        user_data_dir=str(SESSION_DIR),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1440, "height": 900},
    )
    page = browser.pages[0] if browser.pages else browser.new_page()

    print(f"\n→ Navigating to job: {JOB_URL}")
    page.goto(JOB_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)

    # ── STEP 1: What's on the page? ─────────────────────────────────────────
    title = page.evaluate("() => document.title")
    body  = page.evaluate("() => document.body.innerText.substring(0,300)")
    print(f"\n[PAGE TITLE] {title}")
    print(f"[BODY START] {body[:200]}")

    # ── STEP 2: What buttons exist? ─────────────────────────────────────────
    buttons = page.evaluate("""
        () => Array.from(document.querySelectorAll('button, a[role="button"]'))
            .filter(b => { const r=b.getBoundingClientRect(); return r.width>0&&r.height>0; })
            .map(b => ({
                text: b.textContent.trim().substring(0,40),
                aria: b.getAttribute('aria-label') || '',
                disabled: b.disabled || b.getAttribute('aria-disabled')==='true',
                tag: b.tagName
            }))
            .filter(b => b.text.length > 0)
            .slice(0, 15)
    """)
    print(f"\n[VISIBLE BUTTONS]")
    for b in buttons:
        print(f"  {'DISABLED' if b['disabled'] else 'enabled '} | {b['text'][:35]} | aria={b['aria'][:30]}")

    # ── STEP 3: Find Easy Apply button ──────────────────────────────────────
    ea_info = page.evaluate("""
        () => {
            const all = Array.from(document.querySelectorAll('button, a, [role="button"]'));
            for (const el of all) {
                const t  = (el.textContent||'').toLowerCase().trim();
                const al = (el.getAttribute('aria-label')||'').toLowerCase();
                if ((t.includes('easy apply') || al.includes('easy apply')) && !al.includes('filter')) {
                    const r = el.getBoundingClientRect();
                    return {
                        found: true, text: el.textContent.trim(),
                        aria: el.getAttribute('aria-label'),
                        disabled: el.disabled,
                        visible: r.width > 0 && r.height > 0,
                        x: Math.round(r.x), y: Math.round(r.y)
                    };
                }
            }
            return { found: false };
        }
    """)
    print(f"\n[EASY APPLY BUTTON] {ea_info}")

    if not ea_info.get('found'):
        print("\n❌ Easy Apply button NOT FOUND. Job may have changed or page didn't load.")
        input("\nPress Enter to close...")
        browser.close()
        exit()

    # ── STEP 4: Click Easy Apply (native Playwright click) ──────────────────
    print("\n→ Clicking Easy Apply button (native Playwright click)...")
    try:
        btn = page.locator("button[aria-label*='Easy Apply']").first
        if btn.count() > 0 and btn.is_visible():
            btn.click()
            print("  ✅ Clicked via aria-label selector")
        else:
            btn2 = page.locator("button:has-text('Easy Apply')").first
            if btn2.count() > 0:
                btn2.click()
                print("  ✅ Clicked via text selector")
            else:
                print("  ❌ Could not find button with Playwright selector")
    except Exception as e:
        print(f"  ❌ Click error: {e}")

    time.sleep(3)

    # ── STEP 5: What's in the modal? ────────────────────────────────────────
    modal_info = page.evaluate("""
        () => {
            const modal = document.querySelector(
                '[data-test-modal], .jobs-easy-apply-modal, [role="dialog"], '
                '[aria-label*="Easy Apply"], [class*="easy-apply"]'
            );
            if (!modal) return { found: false };

            const btns = Array.from(modal.querySelectorAll('button:not([disabled])'))
                .map(b => ({text: b.textContent.trim().substring(0,40), aria: b.getAttribute('aria-label')||''}))
                .filter(b => b.text.length > 0);

            return {
                found: true,
                tag: modal.tagName,
                className: modal.className.substring(0,60),
                aria: modal.getAttribute('aria-label') || '',
                text: modal.innerText.substring(0, 300),
                buttons: btns
            };
        }
    """)

    print(f"\n[MODAL] found={modal_info.get('found')}")
    if modal_info.get('found'):
        print(f"  class: {modal_info.get('className')}")
        print(f"  aria:  {modal_info.get('aria')}")
        print(f"  text:  {modal_info.get('text','')[:200]}")
        print(f"  buttons: {modal_info.get('buttons')}")
    else:
        # Check page-level buttons after click
        post_btns = page.evaluate("""
            () => Array.from(document.querySelectorAll('button:not([disabled])'))
                .filter(b => { const r=b.getBoundingClientRect(); return r.width>0&&r.height>0; })
                .map(b => b.textContent.trim().substring(0,40))
                .filter(t => t.length > 0).slice(0,10)
        """)
        print(f"  ⚠️  No modal found. Page buttons after click: {post_btns}")

    print("\n→ Browser staying open so you can see it. Press Enter to close...")
    input()
    browser.close()
