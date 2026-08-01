# Akbots - Don't Remove Credit - @AkBots_Official
#
# lksfy.com shortener bypass — ported from the uploaded app.py (a standalone
# Flask microservice) into an async function that reuses Akbots/headless.py's
# existing Chromium/Playwright setup instead of spinning up its own Flask
# server + sync_playwright + cloudscraper stack.
#
# Turnstile solving is delegated to Akbots/cf_lib/turnstile_solver.py (see
# solve_turnstile() below) — the same in-process Camoufox solver
# Akbots/cf_bypass.py falls back to, ported in from the standalone
# TurnstileSolver/ project (no separate HTTP service to run any more).
# This used to be an empty stub (matching the source app.py's own
# unimplemented get_turnstile_token()); now it's wired in for real. If
# camoufox isn't installed or the solve fails, solve_turnstile() still
# returns None exactly like before, and bypass_lksfy() reports failure
# rather than proceeding with a token that doesn't exist.

import asyncio
import re

try:
    from playwright.async_api import async_playwright
    from Akbots.headless import _ensure_chromium, system_chromium_path
except ImportError:
    async_playwright = None
    _ensure_chromium = None
    system_chromium_path = None

try:
    from Akbots.cf_lib.turnstile_solver import solve_async as _ts_solve_async
except ImportError:
    _ts_solve_async = None

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

LKSFY_SITE_KEY = "0x4AAAAAAA49NnPZwQijgRoi"
LKSFY_DOMAIN = "https://lksfy.com/"

LKSFY_PATTERN = re.compile(r"(https?://)?(www\.)?lksfy\.com/\S+", re.IGNORECASE)


async def solve_turnstile(domain: str, site_key: str) -> str | None:
    """Solves lksfy.com's Turnstile widget via the in-process Camoufox
    solver (Akbots/cf_lib/turnstile_solver.py). Returns None (never raises)
    if camoufox isn't installed or fails to solve — bypass_lksfy() then
    treats it as a hard failure rather than proceeding with a fake token."""
    if _ts_solve_async is None:
        return None
    try:
        return await asyncio.wait_for(
            _ts_solve_async(site_key, domain, req_id="lksfy", timeout=45), timeout=55,
        )
    except Exception:
        return None


async def bypass_lksfy(url: str, referer: str = None) -> str | None:
    """Resolves an lksfy.com shortener link to its final destination URL.
    Returns None (never raises) if Playwright/Chromium isn't available, the
    Turnstile solver isn't configured, or the site's flow doesn't complete
    in time — callers should treat None as "couldn't bypass this link"."""
    if async_playwright is None:
        return None

    url = url.rstrip("/")
    alias = url.split("/")[-1]
    final_url = f"{LKSFY_DOMAIN}{alias}"

    token = await solve_turnstile(LKSFY_DOMAIN, LKSFY_SITE_KEY)
    if not token:
        return None

    if _ensure_chromium is not None:
        try:
            await asyncio.wait_for(_ensure_chromium(), timeout=60)
        except asyncio.TimeoutError:
            return None

    result: dict = {}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path=system_chromium_path(),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox", "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage", "--disable-gpu", "--single-process",
                ],
            )
            try:
                context = await browser.new_context(
                    user_agent=_UA,
                    viewport={"width": 1280, "height": 720},
                    extra_http_headers={"referer": referer or "https://home.wblaxmibhandar.com/"},
                )
                page = await context.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
                    "window.chrome = { runtime: {} };"
                    "Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });"
                )

                def on_response(response):
                    if "/links/go" in response.url and response.request.method == "POST":
                        async def _grab():
                            try:
                                data = await response.json()
                                result["encoded_url"] = data.get("url")
                            except Exception:
                                pass
                        asyncio.ensure_future(_grab())

                def on_framenavigated(frame):
                    if frame == page.main_frame:
                        current = frame.url
                        if ("lksfy.com" not in current and current not in ("about:blank", "")
                                and not current.startswith("data:") and result.get("get_link_clicked")):
                            result["final"] = current

                page.on("response", on_response)
                page.on("framenavigated", on_framenavigated)

                await page.goto(final_url, referer="https://main.24jobalert.com/", wait_until="domcontentloaded")
                await asyncio.sleep(4)

                await page.evaluate(f"""
                    (function() {{
                        const token = {token!r};
                        let input = document.querySelector('input[name="cf-turnstile-response"]');
                        if (!input) {{
                            input = document.createElement('input');
                            input.type = 'hidden';
                            input.name = 'cf-turnstile-response';
                            const div = document.querySelector('#captchaLinksGo');
                            if (div) div.appendChild(input);
                        }}
                        if (input) input.value = token;
                        if (typeof onTurnstileCompletedCallback === 'function') {{
                            onTurnstileCompletedCallback(token);
                        }}
                    }})();
                """)

                for _ in range(3):
                    await asyncio.sleep(1)
                    if result.get("encoded_url"):
                        break

                result["get_link_clicked"] = True
                try:
                    await page.wait_for_selector("#get-link:not([disabled])", timeout=5000)
                    await page.click("#get-link")
                except Exception:
                    try:
                        await page.evaluate("document.getElementById('get-link').click()")
                    except Exception:
                        pass

                for _ in range(10):
                    await asyncio.sleep(1)
                    if result.get("final"):
                        break
            finally:
                await browser.close()
    except Exception:
        return None

    return result.get("final") or None
