"""Playwright GUI tests for Claude Session Hub."""
import os
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:7778"
SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)


def screenshot(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  Screenshot: {path}")
    return path


def test_all():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # 1. Load the page
        print("1. Loading page...")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector(".session-card", timeout=10000)
        screenshot(page, "01_initial_load")
        cards = page.query_selector_all(".session-card")
        print(f"   Session cards visible: {len(cards)}")

        # 2. Test status filter — click the label for "Active"
        print("2. Testing status filter (Active)...")
        # The radio is hidden; click the parent label element
        active_label = page.locator('.filter-option:has(input[value="active"])')
        active_label.click()
        page.wait_for_timeout(800)
        screenshot(page, "02_active_filter")

        # Check computed styles on the selected filter option
        active_bg = active_label.evaluate("el => getComputedStyle(el).backgroundColor")
        active_border = active_label.evaluate("el => getComputedStyle(el).borderLeftWidth")
        active_label_color = page.locator('.filter-option:has(input[value="active"]) .filter-label').evaluate(
            "el => getComputedStyle(el).color"
        )
        print(f"   Active filter bg: {active_bg}")
        print(f"   Active filter border-left-width: {active_border}")
        print(f"   Active label color: {active_label_color}")

        # Check if filter banner appears
        banner = page.locator("#filter-banner")
        if banner.count() > 0 and banner.is_visible():
            print(f"   Filter banner: {banner.inner_text()}")
        else:
            print("   Filter banner: NOT VISIBLE")

        cards_active = page.query_selector_all(".session-card")
        print(f"   Cards shown: {len(cards_active)}")

        # 3. Switch to Idle filter
        print("3. Testing status filter (Idle)...")
        idle_label = page.locator('.filter-option:has(input[value="idle"])')
        idle_label.click()
        page.wait_for_timeout(500)
        screenshot(page, "03_idle_filter")
        cards_idle = page.query_selector_all(".session-card")
        print(f"   Cards shown: {len(cards_idle)}")

        # 4. Project filter
        print("4. Testing project filter...")
        # Reset status to All first
        page.locator('.filter-option:has(input[value="all"])').click()
        page.wait_for_timeout(300)

        project_labels = page.locator('.project-filter')
        if project_labels.count() > 0:
            project_labels.first.click()
            page.wait_for_timeout(500)
            screenshot(page, "04_project_filter")

            proj_bg = project_labels.first.evaluate("el => getComputedStyle(el).backgroundColor")
            proj_border = project_labels.first.evaluate("el => getComputedStyle(el).borderLeftWidth")
            print(f"   Project filter bg: {proj_bg}")
            print(f"   Project filter border-left-width: {proj_border}")

            # Uncheck it
            project_labels.first.click()
            page.wait_for_timeout(300)

        # 5. Search test
        print("5. Testing search...")
        search = page.locator("#search-input")
        search.fill("proagent")
        page.wait_for_timeout(1500)  # debounce + fetch
        screenshot(page, "05_search_proagent")
        search_cards = page.query_selector_all(".session-card")
        print(f"   Search results for 'proagent': {len(search_cards)}")

        # 6. Special character search
        print("6. Testing special character search...")
        search.fill("skill-publisher")
        page.wait_for_timeout(1500)
        screenshot(page, "06_search_special")
        error_toasts = page.query_selector_all(".toast.error")
        search_cards = page.query_selector_all(".session-card")
        print(f"   Error toasts: {len(error_toasts)}")
        print(f"   Results for 'skill-publisher': {len(search_cards)}")

        # 7. Reindex persistence test — THE CRITICAL BUG
        print("7. Testing search persistence across reindex...")
        search.fill("marketplace")
        page.wait_for_timeout(1500)
        results_before = len(page.query_selector_all(".session-card"))
        search_val_before = search.input_value()
        print(f"   Before reindex: {results_before} results, search='{search_val_before}'")
        screenshot(page, "07_before_reindex")

        # Trigger reindex via API
        page.evaluate("fetch('/api/reindex', {method: 'POST'})")
        # Wait for SSE event to propagate
        page.wait_for_timeout(4000)

        results_after = len(page.query_selector_all(".session-card"))
        search_val_after = search.input_value()
        print(f"   After reindex: {results_after} results, search='{search_val_after}'")
        survived = search_val_after == "marketplace" and results_after > 0
        print(f"   Search survived reindex: {survived}")
        screenshot(page, "08_after_reindex")

        # 8. Session detail view
        print("8. Testing session detail...")
        search.fill("")
        page.wait_for_timeout(500)
        page.locator(".session-card").first.click()
        page.wait_for_selector("#detail-view:not(.hidden)", timeout=5000)
        page.wait_for_timeout(1000)
        screenshot(page, "09_session_detail")
        messages = page.query_selector_all(".message")
        print(f"   Messages rendered: {len(messages)}")
        resume_visible = page.locator("#resume-btn").is_visible()
        print(f"   Resume button visible: {resume_visible}")

        # Go back
        page.locator("#back-btn").click()
        page.wait_for_timeout(300)
        screenshot(page, "10_back_to_list")

        browser.close()
        print("\nDone! Screenshots in ./screenshots/")


if __name__ == "__main__":
    test_all()
