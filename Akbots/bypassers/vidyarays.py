# Akbots - Don't Remove Credit - @AkBots_Official
#
# vidyarays.com shortener bypass — ported from a GreasyFork userscript
# ("Vidyarays Backend Timer Bypass" by Chat G) that polls the same
# prolink.php page repeatedly until the server-side validation completes
# and injects the real destination link into the page. That script is
# pure DOM scanning + repeated fetch(), no browser-only trick involved, so
# it ports directly to plain aiohttp — no headless Chromium/Playwright
# needed for this one (unlike lksfy.py, which needs a real browser for its
# Turnstile widget).
#
# NOTE: only covers the ?id= "prolink.php" flow the source script targets.
# Other Vidyarays link formats/campaigns may use a different flow this
# doesn't handle — see is it in the same family before assuming this
# will work for every vidyarays.com link.

import asyncio
import re

import aiohttp

VIDYARAYS_PATTERN = re.compile(r"(https?://)?(www\.)?vidyarays\.com/prolink\.php\?", re.IGNORECASE)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# The final destination is any <a href="..."> on the page that ISN'T
# another prolink.php link — same check the source userscript uses.
_HREF_RE = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)


async def bypass_vidyarays(url: str, max_polls: int = 40, poll_delay: float = 1.5) -> str | None:
    """Repeatedly re-fetches `url` (a vidyarays.com/prolink.php?id=... link)
    until the server-side timer validation completes and a real
    destination link appears in the page, then returns that link.
    Returns None (never raises) if it doesn't resolve within max_polls —
    callers should treat that as "couldn't bypass this link".

    max_polls/poll_delay default to ~60s total, matching the userscript's
    own 120-iteration loop scaled down for a server context (no user
    sitting there watching a tab — no need to poll as aggressively)."""
    if "id=" not in url:
        return None

    try:
        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
            for _ in range(max_polls):
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        html = await resp.text(errors="ignore")
                except Exception:
                    await asyncio.sleep(poll_delay)
                    continue

                for href in _HREF_RE.findall(html):
                    if "prolink.php" not in href:
                        return href

                await asyncio.sleep(poll_delay)
    except Exception:
        return None

    return None
