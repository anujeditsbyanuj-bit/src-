# Akbots - Don't Remove Credit - @AkBots_Official
#
# Automated TeraBox login — ported from terabot-main/scraper/main.py's
# login_and_get_cookie()/check_cookie_valid(), adapted to reuse this repo's
# shared Playwright browser (Akbots/playwright_bypass.py) instead of that
# project's own persistent_context browser, so this doesn't launch a
# second Chromium process.
#
# Why this exists: TERABOX_NDUS is a session cookie — it eventually
# expires, and until now the only fix was an admin manually re-exporting
# it from a browser (see config.py's TERABOX_NDUS comment). If
# TERABOX_EMAIL + TERABOX_PASSWORD are also set, terabox_lib can instead
# log back in on its own and keep going unattended. Both are optional —
# leave them unset and nothing changes from before (falls straight
# through to the "every cookie failed" error, same as always).
#
# Not tested against a live TeraBox account in this environment (no
# network here) — ported faithfully from a real, working implementation
# (exact selectors/click sequence, not guesswork), but TeraBox's login UI
# can change, and any account with 2FA/verification-code enabled will hit
# that wall the same way the source project's own comments say it does —
# this falls back to "couldn't auto-login" rather than trying to solve a
# verification code, same as the source.

import logging

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")


async def check_cookie_valid(ndus: str) -> bool:
    """Cheap check — is this ndus cookie still accepted, or has it expired?
    Tries both known TeraBox frontend hosts since either can redirect to
    a login/passport page once a cookie goes stale."""
    if not ndus:
        return False
    import aiohttp
    headers = {"User-Agent": _UA, "Cookie": f"ndus={ndus}", "Referer": "https://www.terabox.app/"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            for check_url in ("https://www.terabox.app/main", "https://www.1024tera.com/main"):
                async with session.get(check_url, allow_redirects=True, timeout=15) as resp:
                    final_url = str(resp.url).lower()
                    if resp.status == 200 and "login" not in final_url and "passport" not in final_url:
                        return True
    except Exception as e:
        logger.debug(f"terabox_lib.auto_login: cookie validity check failed: {e}")
    return False


async def login_and_get_ndus(email: str, password: str) -> str | None:
    """Headless Playwright login to www.1024tera.com, returns a fresh ndus
    cookie value on success or None on any failure (wrong password, UI
    changed, hit a verification-code wall, etc — never raises, callers
    just treat None as "auto-login didn't work, nothing else to try")."""
    if not email or not password:
        return None

    try:
        from Akbots.playwright_bypass import _get_browser
    except ImportError:
        logger.info("terabox_lib.auto_login: Akbots.playwright_bypass unavailable.")
        return None

    browser = await _get_browser()
    if browser is None:
        logger.info("terabox_lib.auto_login: shared Chromium unavailable — can't attempt auto-login.")
        return None

    context = None
    try:
        context = await browser.new_context(
            user_agent=_UA, viewport={"width": 1280, "height": 800}, has_touch=True,
        )
        page = await context.new_page()

        for attempt in range(2):
            try:
                await page.goto("https://www.1024tera.com/wap/outside/login",
                                 wait_until="domcontentloaded", timeout=30_000)
                break
            except Exception:
                if attempt == 1:
                    raise
                await page.wait_for_timeout(4000)
        await page.wait_for_timeout(3000)

        try:
            await page.wait_for_selector(
                ".icon-arrow, .other-item, input[placeholder='Enter your email']", timeout=15_000)
        except Exception:
            pass

        email_input = await page.query_selector('input[placeholder="Enter your email"]')
        if not (email_input and await email_input.is_visible()):
            arrow = await page.query_selector(".icon-arrow")
            if arrow and await arrow.is_visible():
                await arrow.tap()
                await page.wait_for_timeout(1000)

            other_items = []
            for _ in range(10):
                other_items = await page.query_selector_all(".other-item .logo")
                if len(other_items) >= 1:
                    break
                await page.wait_for_timeout(500)

            # Prefer the second button (Mail) when there are two (Phone +
            # Mail); fall back to whichever single one exists.
            target = other_items[1] if len(other_items) >= 2 else (other_items[0] if other_items else None)
            if target:
                for _ in range(3):
                    for action in ("click", "tap"):
                        try:
                            await getattr(target, action)(timeout=1000)
                        except Exception:
                            pass
                    await page.wait_for_timeout(1500)
                    email_input = await page.query_selector('input[placeholder="Enter your email"]')
                    if email_input and await email_input.is_visible():
                        break

        email_input = await page.query_selector('input[placeholder="Enter your email"]')
        if not email_input:
            logger.info("terabox_lib.auto_login: email input never appeared — login page layout may have changed.")
            return None

        await page.fill('input[placeholder="Enter your email"]', email)
        await page.fill('input[placeholder="Enter your new password."]', password)
        await page.wait_for_timeout(1000)

        login_btn = await page.wait_for_selector(".btn-class-login", timeout=5000)
        if not login_btn:
            logger.info("terabox_lib.auto_login: login button not found.")
            return None
        await login_btn.tap()
        await page.wait_for_timeout(2000)

        for _ in range(15):
            cookies = await context.cookies()
            ndus_cookie = next((c for c in cookies if c["name"] == "ndus"), None)
            if ndus_cookie:
                logger.info("terabox_lib.auto_login: automated login succeeded.")
                return ndus_cookie["value"]
            await page.wait_for_timeout(1000)

        needs_verification = await page.evaluate("""() => {
            const text = document.body ? document.body.innerText.toLowerCase() : "";
            return text.includes("verification code");
        }""")
        if needs_verification:
            logger.info("terabox_lib.auto_login: hit a verification-code challenge — "
                        "credentials alone can't get past this, TERABOX_NDUS must be set manually.")
        else:
            logger.info("terabox_lib.auto_login: login submitted but no ndus cookie appeared.")
        return None

    except Exception as e:
        logger.info(f"terabox_lib.auto_login: failed ({e}).")
        return None
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
