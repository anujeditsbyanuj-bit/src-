# Akbots - Don't Remove Credit - @AkBots_Official
#
# turnstile_solver.py — in-process port of the "Violetics Solver" /
# TurnstileSolver pattern (a Camoufox-driven headed-browser Turnstile
# solver), reachable as a submodule instead of the standalone project's
# own separate FastAPI microservice (nothing here runs a server; both
# functions below are called directly, in-process, from
# Akbots/cf_bypass.py and Akbots/bypassers/lksfy.py).
#
# Camoufox is a hardened Firefox build with a Playwright-compatible async
# API (real Firefox fingerprint, not a bare headless flag), which is why
# real Cloudflare deployments are more willing to hand out a passing
# Turnstile token to it than to a vanilla headless Chromium — the whole
# reason this fallback exists alongside Akbots/cf_lib/CloudflareBypasser.py.
#
# Two entry points, matching what each caller actually needs:
#   solve_async(site_key, url, ...)      -> just a Turnstile token (str)
#       Used when the caller already knows the widget's siteKey and target
#       domain (e.g. lksfy.py) and only needs the token itself to feed into
#       its own follow-up API/form-submit flow — never navigates to the
#       real site at all, renders a throwaway page with just the widget.
#   solve_challenge_async(url, ...)      -> {"cookies", "html",
#                                             "user_agent", "url"}
#       Used when the caller wants the REAL protected page rendered and
#       passed (e.g. cf_bypass.py's fallback path) — navigates straight to
#       `url` and waits for Cloudflare's own interstitial to clear.
#
# NOTE: not exercised against a live Cloudflare Turnstile challenge in
# this environment (no network here to install camoufox + a real browser
# + Xvfb) — the API calls (AsyncCamoufox, page.route, page.evaluate) match
# Camoufox's documented Playwright-compatible async surface, but if a
# real run behaves differently, report the exact exception/timeout and
# this can be adjusted.

import asyncio
import logging

logger = logging.getLogger(__name__)

try:
    from camoufox.async_api import AsyncCamoufox
    _CAMOUFOX_AVAILABLE = True
except ImportError:
    AsyncCamoufox = None
    _CAMOUFOX_AVAILABLE = False

_FAKE_ORIGIN = "https://fake-turnstile-host.local/"

_WIDGET_PAGE = """<!DOCTYPE html>
<html><head>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
</head><body>
<div class="cf-turnstile" data-sitekey="{site_key}" data-callback="__onTurnstileToken"></div>
<script>
window.__turnstileToken = null;
function __onTurnstileToken(token) {{ window.__turnstileToken = token; }}
</script>
</body></html>"""


def is_available() -> bool:
    return _CAMOUFOX_AVAILABLE


async def solve_async(site_key: str, url: str, req_id: str = "", timeout: int = 45) -> str | None:
    """Renders a throwaway page containing just the Turnstile widget for
    (site_key, url)'s domain in a real Camoufox browser, and returns the
    token Cloudflare's own script hands to the page once it decides the
    session looks human. Returns None (never raises) on any failure —
    matches every other function in this file/cf_bypass.py's
    degrade-don't-break contract."""
    if not _CAMOUFOX_AVAILABLE:
        logger.info(f"turnstile_solver.solve_async[{req_id}]: camoufox not installed.")
        return None

    try:
        async with AsyncCamoufox(headless="virtual") as browser:
            page = await browser.new_page()

            async def _serve_widget(route):
                if route.request.url.startswith(_FAKE_ORIGIN):
                    await route.fulfill(status=200, content_type="text/html",
                                         body=_WIDGET_PAGE.format(site_key=site_key))
                else:
                    await route.continue_()

            await page.route("**/*", _serve_widget)
            # The widget's data-sitekey is scoped to `url`'s domain by
            # Cloudflare server-side, so the page it renders on must share
            # that origin for the token to come back valid — hence serving
            # our synthetic markup AT that url rather than a truly fake one.
            target = url if url.startswith("http") else _FAKE_ORIGIN
            await page.goto(target, timeout=timeout * 1000)

            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                token = await page.evaluate("window.__turnstileToken")
                if token:
                    return token
                await asyncio.sleep(1)

            logger.info(f"turnstile_solver.solve_async[{req_id}]: timed out after {timeout}s.")
            return None
    except Exception as e:
        logger.info(f"turnstile_solver.solve_async[{req_id}]: failed ({e}).")
        return None


async def solve_challenge_async(url: str, req_id: str = "", timeout: int = 30) -> dict:
    """Navigates straight to `url` (a page that itself presents a
    Cloudflare interstitial) and waits up to `timeout` seconds for it to
    clear on its own — Camoufox's realer fingerprint is often enough for
    Cloudflare to auto-pass the challenge without any click needed.
    Raises on failure (unlike solve_async) — Akbots/cf_bypass.py's
    _solver_bypass() wraps this call and catches broadly, treating any
    exception as "this fallback tier didn't work" and returning None to
    its own caller.

    Returns {"cookies": [...], "html": str, "user_agent": str, "url": str}."""
    if not _CAMOUFOX_AVAILABLE:
        raise RuntimeError("camoufox is not installed")

    async with AsyncCamoufox(headless="virtual") as browser:
        page = await browser.new_page()
        await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            title = (await page.title() or "").lower()
            if "just a moment" not in title and "attention required" not in title:
                break
            await asyncio.sleep(1)
        else:
            logger.info(f"turnstile_solver.solve_challenge_async[{req_id}]: interstitial never cleared within {timeout}s.")

        cookies = await page.context.cookies()
        html = await page.content()
        try:
            user_agent = await page.evaluate("navigator.userAgent")
        except Exception:
            user_agent = ""

        return {"cookies": cookies, "html": html, "user_agent": user_agent, "url": page.url}
