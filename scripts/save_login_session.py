"""One-time LOCAL capture of a logged-in browser session for the
login-gated job boards (Handshake, Jobright, Simplify). Never run this in
CI — it needs a human to type a real password into a real browser window.

Usage: cd backend && python ../scripts/save_login_session.py

Opens a real (non-headless) Chromium window to each board's login page in
turn. Log in by hand in that window, then return to the terminal and press
Enter to move to the next board. After the last one, the combined session
(cookies + localStorage for all three) is written to data/auth_state.json.

Re-run this whenever a login board's scrape starts coming back empty
(state.record_health's consecutive_zero_jobs will flag it — see the
SourceHealth dashboard widget) — that means the saved session expired.

Note: Handshake's login page sits behind a Cloudflare bot challenge for
automated requests; running non-headless with a real person present
(as this script does) normally clears it without extra steps.
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

BOARDS = [
    ("Handshake", "https://app.joinhandshake.com/login"),
    ("Jobright", "https://jobright.ai/login"),
    ("Simplify", "https://simplify.jobs/auth/login"),
]
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "auth_state.json")


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        for name, url in BOARDS:
            await page.goto(url)
            input(f"Log into {name} in the opened browser window, then press Enter here to continue...")
        await context.storage_state(path=OUTPUT)
        await browser.close()
    print(f"Saved session to {OUTPUT}")
    print("Next: upload its contents as the AUTH_STATE GitHub Actions secret.")


if __name__ == "__main__":
    if sys.stdin is None or not sys.stdin.isatty():
        raise SystemExit("This script needs an interactive terminal — run it locally, not in CI.")
    asyncio.run(main())
