"""Playwright tests for management features."""
import os
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:7778"
SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

def ss(page, name):
    path = os.path.join(SCREENSHOTS_DIR, f"mgmt_{name}.png")
    page.screenshot(path=path, full_page=False)
    print(f"  -> {path}")

def test_management():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector(".session-card", timeout=10000)
        page.wait_for_timeout(1000)
        print("Page loaded.")

        # --- 1. Context Menu ---
        print("\n1. Context menu...")
        page.locator(".session-card").first.click(button="right")
        page.wait_for_timeout(500)
        ctx = page.locator("#context-menu")
        print(f"   Visible: {ctx.is_visible()}")
        items = page.locator("#context-menu .context-menu-item")
        print(f"   Items: {items.count()}")
        for i in range(items.count()):
            print(f"     - {items.nth(i).inner_text()}")
        ss(page, "01_context_menu")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # --- 2. Star ---
        print("\n2. Star session...")
        page.locator(".session-card").first.click(button="right")
        page.wait_for_timeout(300)
        page.locator("#context-menu .context-menu-item", has_text="Star").first.click()
        page.wait_for_timeout(500)
        ss(page, "02_starred")

        # --- 3. Rename ---
        print("\n3. Rename...")
        page.locator(".session-card").first.click(button="right")
        page.wait_for_timeout(300)
        page.locator("#context-menu .context-menu-item", has_text="Rename").first.click()
        page.wait_for_timeout(500)
        rename_dialog = page.locator("#rename-dialog")
        print(f"   Rename dialog visible: {rename_dialog.is_visible()}")
        ss(page, "03_rename_dialog")
        page.locator("#rename-input").fill("My Important Session")
        page.locator("#rename-save").click()
        page.wait_for_timeout(500)
        ss(page, "03b_renamed")

        # --- 4. Detail view buttons ---
        print("\n4. Detail view actions...")
        page.locator(".session-card").first.click()
        page.wait_for_selector("#detail-view:not(.hidden)", timeout=5000)
        page.wait_for_timeout(800)
        ss(page, "04_detail_view")
        for bid in ["detail-star-btn", "detail-export-btn", "detail-archive-btn", "detail-delete-btn"]:
            el = page.locator(f"#{bid}")
            print(f"   #{bid}: visible={el.is_visible() if el.count() > 0 else 'N/A'}")
        page.locator("#back-btn").click()
        page.wait_for_timeout(300)

        # --- 5. Select mode ---
        print("\n5. Select mode...")
        select_btn = page.locator("#select-mode-btn")
        print(f"   Select button found: {select_btn.count() > 0}")
        select_btn.click()
        page.wait_for_timeout(500)
        ss(page, "05_select_mode")

        # Click 3 cards to select them
        cards = page.locator(".session-card")
        for i in range(min(3, cards.count())):
            cards.nth(i).click()
            page.wait_for_timeout(200)
        page.wait_for_timeout(300)
        ss(page, "05b_selected")

        # Check bulk bar
        bulk_bar = page.locator("#bulk-bar")
        if bulk_bar.count() > 0:
            print(f"   Bulk bar visible: {bulk_bar.is_visible()}")
        else:
            print("   Bulk bar: not found")

        # Exit select mode
        select_btn.click()
        page.wait_for_timeout(300)

        # --- 6. Cleanup dialog ---
        print("\n6. Cleanup dialog...")
        # Scroll sidebar to show cleanup button
        sidebar = page.locator("#sidebar")
        sidebar.evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(300)
        cleanup_btn = page.locator("#cleanup-btn")
        print(f"   Cleanup button visible: {cleanup_btn.is_visible()}")
        cleanup_btn.click()
        page.wait_for_timeout(500)
        cleanup_dialog = page.locator("#cleanup-dialog")
        print(f"   Cleanup dialog visible: {cleanup_dialog.is_visible()}")
        ss(page, "06_cleanup_dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # --- 7. Trash dialog ---
        print("\n7. Trash dialog...")
        trash_btn = page.locator("#trash-btn")
        print(f"   Trash button visible: {trash_btn.is_visible()}")
        trash_btn.click()
        page.wait_for_timeout(500)
        trash_dialog = page.locator("#trash-dialog")
        print(f"   Trash dialog visible: {trash_dialog.is_visible()}")
        ss(page, "07_trash_dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # --- 8. Delete confirmation ---
        print("\n8. Delete confirmation...")
        page.locator(".session-card").first.click(button="right")
        page.wait_for_timeout(300)
        page.locator("#context-menu .context-menu-item", has_text="Delete").first.click()
        page.wait_for_timeout(500)
        confirm = page.locator("#confirm-dialog")
        print(f"   Confirm dialog visible: {confirm.is_visible()}")
        ss(page, "08_confirm_delete")
        page.locator("#confirm-cancel").click()
        page.wait_for_timeout(300)

        # --- 9. Export in context menu ---
        print("\n9. Export...")
        page.locator(".session-card").first.click(button="right")
        page.wait_for_timeout(300)
        export_item = page.locator("#context-menu .context-menu-item", has_text="Export")
        print(f"   Export item present: {export_item.count() > 0}")
        page.keyboard.press("Escape")

        # --- Final screenshot ---
        ss(page, "10_final")

        browser.close()
        print("\nAll management tests complete!")


if __name__ == "__main__":
    test_management()
