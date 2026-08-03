# Akbots - Don't Remove Credit - @AkBots_Official
#
# Ported from the uploaded "Link-Bypass-Api-main" project (developer:
# @FarhanXMods) — that project was a standalone Flask microservice meant to
# be deployed separately (Vercel, per its vercel.json) and called over
# HTTP with an API key. Only its actual bypass logic (app.py's /api route)
# is ported here, running in-process — no separate server, no API key,
# nothing to deploy.
#
# TWO TIERS, tried in order:
#   1. Fast/cheap plain-HTTP tier (ported as-is from the source project):
#      one hardcoded link/answer pair, then redirect-follow, then a plain
#      regex scan of the landing page's HTML for a handful of common
#      "here's the real link" patterns. Works for pages that redirect
#      outright or expose the link in plain HTML — does nothing for a
#      page gated behind a JS countdown timer or a click-through button,
#      since there's no JS engine running here.
#   2. Headless-browser tier (_browser_bypass below, added on top of the
#      source project — not present in the original Flask app): opens the
#      page in a real headless Chromium (reusing the shared browser
#      instance Akbots/playwright_bypass.py already manages, so this
#      doesn't spin up a second one), waits out any JS countdown timer via
#      real navigation events, and tries clicking a handful of common
#      "Continue" / "Get Link" / "Skip Ad" button patterns if one shows up
#      instead of an auto-redirect. Only reached if tier 1 didn't resolve
#      anything.
#
# WHAT NEITHER TIER CAN DO: solve a CAPTCHA. If a page is actually gated
# behind one, tier 2 will sit there until it times out and return None —
# same as a real browser would with nobody there to solve it. Adding real
# CAPTCHA solving would mean wiring up a paid third-party solver service
# (e.g. 2captcha) and is intentionally left as a stub-by-design, same as
# Akbots/bypassers/lksfy.py's Turnstile solver.

import re
import time
import asyncio
import logging
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

LINKBYPASS_DOMAINS = ("vplink.in", "adsfly.in", "linksgo.in", "just2earn.com")

# The one link/answer pair hardcoded in the source project itself.
ORIGINAL_LINK = "https://just2earn.com/godx1767110927511"
BYPASSED_LINK = "https://mypdftools.site/ankitkey?deviceId=rand-1767110927483-g5lxha5&verify=57c4582f6cfcb79f955337dca51dca674b98eef8788c6e7419c62db4"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_HTML_URL_PATTERNS = (
    r'window\.location\.href\s*=\s*["\']([^"\']+)["\']',
    r'<meta.*?url=([^"\']+)["\']',
    r'<a[^>]*href=["\']([^"\']+)"[^>]*>Continue',
    r'data-url=["\']([^"\']+)["\']',
)

# Best-effort selectors for the "click through" style of ad-wall — tried
# in order, first one found on the page gets clicked. Not exhaustive (no
# selector list can be, across arbitrary shortener sites), just the common
# id/class/text patterns these pages tend to use.
_CONTINUE_SELECTORS = (
    "text=Continue", "text=Get Link", "text=Get link", "text=Skip Ad",
    "text=Skip ad", "text=Proceed", "text=Click Here", "text=Click here",
    "a#continue", "button#continue", "a.continue", "button.continue",
    "[id*=continue i]", "[class*=continue i]",
    "[id*=skip i]", "[class*=skip i]",
    "[id*=proceed i]", "[class*=proceed i]",
)


def is_linkbypass_url(url: str) -> bool:
    return any(domain in url for domain in LINKBYPASS_DOMAINS)


async def _http_bypass(url: str) -> str | None:
    """Tier 1 — plain HTTP redirect-follow + HTML regex scan. Fast, cheap,
    no browser — but can't do anything JS-driven (countdown timers,
    click-through buttons)."""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
            async with session.get(
                url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                final_url = str(resp.url)
                if final_url != url:
                    return final_url
                html = await resp.text(errors="ignore")

        for pattern in _HTML_URL_PATTERNS:
            match = re.search(pattern, html)
            if match:
                extracted = match.group(1)
                if not extracted.startswith("http"):
                    extracted = "https://" + extracted.lstrip("/")
                return extracted
    except Exception:
        pass
    return None


async def _browser_bypass(url: str, timeout: int = 25) -> str | None:
    """Tier 2 — real headless Chromium (shared instance from
    Akbots/playwright_bypass.py). Waits out any JS countdown timer via
    real frame-navigation events, and tries clicking a "Continue"-style
    button if one appears instead. Returns None (never raises) if
    Playwright/Chromium isn't available, nothing resolves in time, or the
    page never navigates to a different host — including if it's actually
    CAPTCHA-gated (this does not attempt to solve one)."""
    try:
        from Akbots.playwright_bypass import _get_browser
    except Exception:
        return None

    browser = await _get_browser()
    if browser is None:
        return None

    original_host = urlparse(url).netloc
    captured = {"url": None}

    context = None
    try:
        context = await browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 800})
        page = await context.new_page()

        def _on_nav(frame):
            if frame == page.main_frame:
                new_host = urlparse(frame.url).netloc
                if new_host and new_host != original_host and not captured["url"]:
                    captured["url"] = frame.url

        page.on("framenavigated", _on_nav)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        except Exception as e:
            logger.info(f"linkbypass: goto({url}) raised (continuing to poll anyway): {e}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not captured["url"]:
            await asyncio.sleep(0.5)
            if captured["url"]:
                break
            # A countdown timer alone doesn't need this — this is for the
            # sites that show a "Continue"/"Get Link" button once the
            # timer finishes (or instead of a timer at all).
            for sel in _CONTINUE_SELECTORS:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click(timeout=1000)
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"linkbypass: browser tier failed for {url}: {e}")
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass

    result = captured["url"]
    if result and urlparse(result).netloc and urlparse(result).netloc != original_host:
        return result
    return None


async def bypass_linkbypass(url: str) -> str | None:
    """Resolves a vplink.in / adsfly.in / linksgo.in / just2earn.com link.
    Tries the fast HTTP tier first, falls back to the headless-browser tier
    (handles countdown timers / click-through buttons, not CAPTCHAs) only
    if that didn't resolve anything. Returns None (never raises) if both
    tiers fail."""
    if ORIGINAL_LINK in url or "godx1767110927511" in url:
        return BYPASSED_LINK

    result = await _http_bypass(url)
    if result:
        return result

    return await _browser_bypass(url)
