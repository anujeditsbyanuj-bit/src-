"""
urlking (viku.urlking.in) dedicated bypass — ported in from
SHUVO-BYPASS-API's bypass/urlking.py.

Genuinely a gap the rest of nova_bypasser/ couldn't cover: unlike the
vplink/earnlinks/arolinks/babylinks/shrinkme cluster (handled generically
by ss/nicktrick.py) or the AdLinkFly-family sites (sites/adlinkfly.py),
urlking sits behind an *interactive* Cloudflare Turnstile challenge
("Verify you are human"), so a plain curl_cffi/cloudscraper session (see
../cloudflare.py) gets 403 on every request — solving Turnstile needs an
actual browser rendering + interacting with the widget.

Two stages:
  1. A real (headless by default) Chromium clears the Turnstile challenge
     and hands off its cookies + exact User-Agent.
  2. Those cookies feed a curl_cffi session that walks the usual blog/
     countdown ad-gate chain and POSTs the go-link form to /links/go —
     much cheaper than doing every hop inside the browser itself.

If the browser alone already lands on the destination (some urlking links
just redirect once the challenge clears), stage 2 is skipped entirely.

Adapted from the original standalone script to this project's conventions:
  - Reuses Akbots/headless.py's system_chromium_path()/_ensure_chromium()
    (same self-installing Chromium as Akbots/hotstar_browser.py) instead
    of assuming `playwright install chromium` was run separately.
  - Returns this package's {"success":, "bypassed_url":, "type":, "error":}
    shape (see sites/gplinks.py, sites/adlinkfly.py) instead of a bare
    string / raising on failure.
  - print() -> logger.

NOTE (kept from the original): Cloudflare hard-blocks most datacenter/VPS
IP ranges. If this bot runs on a cloud VPS (the common case), the Turnstile
challenge may simply never clear no matter what — that's a Cloudflare-side
IP reputation block, not a bug here. A residential proxy would be the real
fix; there's no code-level workaround for that.
"""

import re
import asyncio
import logging
from typing import Dict, Optional
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession

from Akbots.headless import system_chromium_path, _ensure_chromium

logger = logging.getLogger(__name__)

_DOMAINS = ("urlking.in",)

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_CF_TIMEOUT = 90    # seconds to wait for the Turnstile challenge to clear
_GATE_WAIT = 12     # seconds to sit out each ad-gate countdown timer


def is_urlking_domain(url: str) -> bool:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    return any(d in domain for d in _DOMAINS)


# --------------------------------------------------------------------------- #
# parsing helpers (same shape as ../ss/nicktrick.py's shrinkme-cluster parser)
# --------------------------------------------------------------------------- #
def _extract_gt_link(html: str) -> Optional[str]:
    m = re.search(r'<a[^>]*id=["\'](?:gt-link|dl-link)["\'][^>]*>', html, re.I)
    if m:
        mh = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.I)
        if mh and "javascript" not in mh.group(1):
            return mh.group(1)
    return None


def _parse_form(html: str) -> Optional[dict]:
    f = re.search(r'<form[^>]*id="go-link"[\s\S]*?</form>', html)
    if not f:
        return None
    data = {}
    for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', f.group(0)):
        data[m.group(1)] = m.group(2)
    return data or None


def _next_hop(html: str, code: str, seen: set) -> Optional[str]:
    m = re.search(
        r'<meta[^>]+http-equiv=["\']refresh["\'][^>]*content=["\'][^"\']*URL=[\'"]?(https?://[^"\'>]+)',
        html, re.I)
    if m and m.group(1) not in seen:
        return m.group(1)
    m = re.search(r'window\.location\.href\s*=\s*["\'](https?://[^"\']+)', html)
    if m and "example.com" not in m.group(1) and m.group(1) not in seen:
        return m.group(1)
    for c in re.findall(r'https?://[^\s"\'<>]+\?[a-z]+=' + re.escape(code), html):
        if c not in seen:
            return c
    return None


# --------------------------------------------------------------------------- #
# stage 1: clear Cloudflare with a real browser
# --------------------------------------------------------------------------- #
async def _clear_cloudflare(url: str, headless: bool = True, timeout: int = _CF_TIMEOUT):
    """Returns (cookies dict, user_agent, final_url, final_html). Raises
    RuntimeError if the Turnstile challenge never clears within timeout."""
    from playwright.async_api import async_playwright

    try:
        await asyncio.wait_for(_ensure_chromium(), timeout=45)
    except asyncio.TimeoutError:
        pass  # best-effort — launch() below fails cleanly if this didn't work

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            executable_path=system_chromium_path(),
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage"],
        )
        try:
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=_DEFAULT_UA,
                locale="en-US",
            )
            page = await ctx.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            logger.info(f"urlking: opening {url} to clear Cloudflare...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            waited = 0
            while waited < timeout:
                title = await page.title()
                if "Just a moment" not in title and "Attention Required" not in title:
                    break
                # click the Turnstile checkbox if the widget is rendered
                box = await page.evaluate(
                    "()=>{const d=[...document.querySelectorAll('div')]"
                    ".find(e=>e.shadowRoot);if(!d)return null;"
                    "const r=d.getBoundingClientRect();"
                    "return {x:r.x,y:r.y,h:r.height};}"
                )
                if box:
                    await page.mouse.move(box["x"] + 30, box["y"] + box["h"] / 2, steps=12)
                    await asyncio.sleep(0.4)
                    await page.mouse.click(box["x"] + 30, box["y"] + box["h"] / 2)
                    logger.info("urlking: clicked the Turnstile checkbox...")
                await asyncio.sleep(4)
                waited += 4
            else:
                raise RuntimeError(
                    "Cloudflare Turnstile never cleared (waited "
                    f"{timeout}s). Common cause: this server's IP is a "
                    "datacenter/VPS range Cloudflare hard-blocks — a "
                    "residential proxy is usually the only real fix."
                )

            await asyncio.sleep(4)  # let any post-challenge redirect settle
            cookies = {c["name"]: c["value"] for c in await ctx.cookies()}
            final_url, html = page.url, await page.content()
            logger.info(f"urlking: Cloudflare cleared, landed on {final_url}")
            return cookies, _DEFAULT_UA, final_url, html
        finally:
            await browser.close()


# --------------------------------------------------------------------------- #
# stage 2: walk the ad-gate chain with the cleared session
# --------------------------------------------------------------------------- #
async def _bypass_urlking(url: str, wait: int = _GATE_WAIT, headless: bool = True) -> str:
    code = url.rstrip("/").split("/")[-1].split("?")[0]
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    cookies, ua, landed, html = await _clear_cloudflare(url, headless=headless)

    # the browser may already have been redirected straight to the destination
    if urlparse(landed).netloc not in (urlparse(url).netloc, ""):
        if not _parse_form(html) and "Just a moment" not in html:
            gt = _extract_gt_link(html)
            if gt:
                return gt

    s = AsyncSession(verify=False, impersonate="chrome120", cookies=cookies)
    H = {**_HEADERS, "User-Agent": ua}

    async def try_unlock(page_html: str, referer: str) -> Optional[str]:
        form = _parse_form(page_html)
        if not form:
            return _extract_gt_link(page_html)
        logger.info("urlking: reached the unlock page, waiting out the timer...")
        await asyncio.sleep(6)
        post = await s.post(
            f"{base}/links/go",
            data=form,
            headers={**H, "Referer": referer, "Origin": base,
                     "X-Requested-With": "XMLHttpRequest"},
            timeout=25,
        )
        try:
            j = post.json()
        except Exception:
            j = {}
        return j.get("url") or _extract_gt_link(post.text)

    try:
        r = await s.get(url, headers=H, timeout=30)
        if r.status_code == 403:
            raise RuntimeError("Session still blocked by Cloudflare after clearance.")
        ref = str(r.url)
        seen = {url}

        for step in range(12):
            target = await try_unlock(r.text, url)
            if target:
                return target

            nxt = _next_hop(r.text, code, seen)
            if nxt is None:
                break
            seen.add(nxt)
            logger.info(f"urlking: hop {step}: {nxt}")
            if "?" in nxt or ".php" in nxt:
                await asyncio.sleep(wait)
            r = await s.get(nxt, headers={**H, "Referer": ref}, timeout=30)
            ref = nxt

        for attempt in range(3):
            rr = await s.get(url, headers={**H, "Referer": ref}, timeout=25)
            target = await try_unlock(rr.text, url)
            if target:
                return target
            await asyncio.sleep(6)

        raise ValueError("Target link not found; the ad-gate chain did not unlock.")
    finally:
        await s.close()


async def bypass(url: str) -> Dict:
    """Matches sites/gplinks.py's, sites/adlinkfly.py's etc. return shape
    for core.py's dispatch table — {"success":, "bypassed_url":, "type":}
    on success, {"success": False, "error":} otherwise. Never raises."""
    try:
        target = await _bypass_urlking(url)
        return {"success": True, "bypassed_url": target, "type": "urlking"}
    except Exception as e:
        logger.error(f"urlking bypass failed for {url}: {e}")
        return {"success": False, "error": str(e)}
