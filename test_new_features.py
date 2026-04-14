"""Playwright tests for new features: filters, search history, resume feedback, logging."""
import os
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:7778"
SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def ss(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"new_{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  -> {name}")

def test_new_features():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector(".session-card", timeout=10000)
        page.wait_for_timeout(1000)
        print("Page loaded.\n")

        # --- 1. Session length filter ---
        print("1. Session length filter...")
        length_filters = page.locator("#length-filters")
        print(f"   Length filter section exists: {length_filters.count() > 0}")

        if length_filters.count() > 0:
            # Click "Long (200+)"
            long_label = page.locator('.filter-option:has(input[name="length"][value="long"])')
            if long_label.count() > 0:
                long_label.click()
                page.wait_for_timeout(500)
                cards = page.query_selector_all(".session-card")
                print(f"   Long sessions: {len(cards)}")
                ss(page, "01_length_long")

            # Click "Tiny (1-5)"
            tiny_label = page.locator('.filter-option:has(input[name="length"][value="tiny"])')
            if tiny_label.count() > 0:
                tiny_label.click()
                page.wait_for_timeout(500)
                cards = page.query_selector_all(".session-card")
                print(f"   Tiny sessions: {len(cards)}")
                ss(page, "01b_length_tiny")

            # Reset
            page.locator('.filter-option:has(input[name="length"][value="all"])').click()
            page.wait_for_timeout(300)

        # --- 2. Composable filtering ---
        print("\n2. Composable filtering (Active + Long)...")
        # Set Active status
        page.locator('.filter-option:has(input[name="status"][value="active"])').click()
        page.wait_for_timeout(300)

        # Also set Long length
        long_label = page.locator('.filter-option:has(input[name="length"][value="long"])')
        if long_label.count() > 0:
            long_label.click()
            page.wait_for_timeout(500)

        cards = page.query_selector_all(".session-card")
        print(f"   Active + Long: {len(cards)} results")

        # Check filter banner shows both
        banner = page.locator("#filter-banner")
        if banner.count() > 0 and banner.is_visible():
            print(f"   Banner: {banner.inner_text()[:80]}")
        ss(page, "02_composable")

        # Reset all
        page.evaluate("document.dispatchEvent(new CustomEvent('clear-all-filters'))")
        page.wait_for_timeout(500)

        # --- 3. Search history ---
        print("\n3. Search history...")
        search = page.locator("#search-input")

        # Do a few searches to populate history
        for query in ["proagent", "marketplace", "plugin.json"]:
            search.fill(query)
            page.wait_for_timeout(1500)

        # Clear search and focus to show history
        search.fill("")
        page.wait_for_timeout(300)
        search.focus()
        page.wait_for_timeout(500)

        history = page.locator("#search-history")
        history_visible = history.count() > 0 and history.is_visible()
        print(f"   History dropdown visible: {history_visible}")

        if history_visible:
            items = page.locator(".search-history-item")
            print(f"   History items: {items.count()}")
            for i in range(min(items.count(), 3)):
                print(f"     - {items.nth(i).inner_text()}")
            ss(page, "03_search_history")

            # Click first history item
            if items.count() > 0:
                items.first.click()
                page.wait_for_timeout(1000)
                print(f"   After click, search input: '{search.input_value()}'")
                cards = page.query_selector_all(".session-card")
                print(f"   Results: {len(cards)}")

        # Clear search
        search.fill("")
        page.wait_for_timeout(500)
        page.locator("#search-clear").click() if page.locator("#search-clear").is_visible() else None
        page.wait_for_timeout(300)

        # --- 4. Inline resume feedback ---
        print("\n4. Inline resume feedback...")
        # Go to detail view
        page.evaluate("document.dispatchEvent(new CustomEvent('clear-all-filters'))")
        page.wait_for_timeout(500)
        page.locator(".session-card").first.click()
        page.wait_for_selector("#detail-view:not(.hidden)", timeout=5000)
        page.wait_for_timeout(800)

        # Check resume-status element exists
        status_el = page.locator("#resume-status")
        print(f"   Resume status element exists: {status_el.count() > 0}")

        # Click resume and check inline feedback
        resume_btn = page.locator("#resume-btn")
        resume_btn.click()
        page.wait_for_timeout(1000)

        if status_el.count() > 0:
            is_visible = status_el.is_visible()
            text = status_el.inner_text() if is_visible else "(hidden)"
            print(f"   Status visible: {is_visible}")
            print(f"   Status text: {text[:80]}")
        ss(page, "04_resume_feedback")

        # Go back
        page.locator("#back-btn").click()
        page.wait_for_timeout(300)

        # --- 5. Card-level resume feedback ---
        print("\n5. Card resume feedback...")
        card_resume = page.locator(".card-resume-btn").first
        if card_resume.count() > 0:
            card_resume.click()
            page.wait_for_timeout(1000)
            card_status = page.locator(".card-resume-status")
            print(f"   Card status elements: {card_status.count()}")
            ss(page, "05_card_resume")

        # --- 6. Final state ---
        ss(page, "06_final")

        browser.close()
        print("\nAll new feature tests complete!")


if __name__ == "__main__":
    test_new_features()
