# Akbots - Don't Remove Credit - @AkBots_Official
#
# Reusable core for the "WP Safelink" gate pattern — a common WordPress
# ad-gate plugin template used by a bunch of unrelated shortlink domains
# (rocklinks.net, link1s.com, and others), all sharing the exact same
# flow: GET the gate page -> the real target is hidden in a form's
# <input> values -> wait out the timer -> POST that form to
# {DOMAIN}/links/go -> JSON response has the final url.
#
# Ported/generalized from JFZBypassBot's FZBypass/core/bypass_ddl.py
# transcript() — same request shape, but pulled out as a reusable
# function (domain/referer/sleep_time all parameters) instead of one
# function hardcoded for whichever site called it, so
# Akbots/toonworld4all.py can call it with ITS site's values instead of
# duplicating this logic.

import asyncio
import logging

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"


class WPSafelinkError(Exception):
    pass


async def resolve_wpsafelink(session: aiohttp.ClientSession, gate_url: str, domain: str,
                              referer: str, sleep_time: float = 5) -> str:
    """Resolves one WP-Safelink gate URL to its final target.

    gate_url: the full gate link (e.g. https://rocklinks.net/abc123)
    domain: the gate site's root, e.g. "https://rocklinks.net" — used to
        build the /links/go POST endpoint. Not necessarily the same host
        as gate_url (some sites route the initial GET through a CDN/alias
        but POST back to the canonical domain).
    referer: Referer header the gate expects on both requests.
    sleep_time: how long to wait between GET and POST — these gates
        show a countdown timer client-side; POSTing before it elapses
        gets rejected, so this just waits it out server-side instead.

    Raises WPSafelinkError with a human-readable reason on any failure
    (Cloudflare-blocked, no form found, non-JSON response, etc.) — callers
    decide how to surface that, same convention as the rest of this
    project's bypassers (e.g. Akbots/bypassers/*.py).
    """
    code = gate_url.rstrip("/").split("/")[-1]
    headers = {"Referer": referer, "User-Agent": _UA}

    async with session.get(f"{domain}/{code}", headers=headers) as res:
        html = await res.text()
        cookies = res.cookies

    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    if title_tag and title_tag.text.strip() == "Just a moment...":
        raise WPSafelinkError("Blocked by Cloudflare")

    form_data = {inp.get("name"): inp.get("value")
                 for inp in soup.find_all("input") if inp.get("name") and inp.get("value")}
    if not form_data:
        raise WPSafelinkError("No gate form found on the page — layout may have changed")

    await asyncio.sleep(sleep_time)

    post_headers = {"Referer": f"{domain}/{code}", "X-Requested-With": "XMLHttpRequest", "User-Agent": _UA}
    async with session.post(f"{domain}/links/go", data=form_data, headers=post_headers, cookies=cookies) as resp:
        content_type = resp.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise WPSafelinkError(f"Unexpected response type from gate: {content_type or 'none'}")
        data = await resp.json(content_type=None)
        final_url = data.get("url")
        if not final_url:
            raise WPSafelinkError("Gate responded but gave no url")
        return final_url


# Known WP-Safelink gate configurations, keyed by the domain fragment
# that shows up in the redirect chain leading to them (see
# Akbots/toonworld4all.py's _chase_redirect, which follows a source link
# until it lands on one of these before calling resolve_wpsafelink).
KNOWN_GATES = {
    "rocklinks": {"domain": "https://insurance.techymedies.com", "referer": "https://highkeyfinance.com/", "sleep_time": 5},
    "link1s": {"domain": "https://link1s.com", "referer": "https://anhdep24.com/", "sleep_time": 9},
}


async def resolve_known_gate(session: aiohttp.ClientSession, gate_url: str) -> str:
    """Same as resolve_wpsafelink, but auto-picks the domain/referer/
    sleep_time from KNOWN_GATES based on which known gate name appears in
    gate_url, instead of the caller having to know those values itself."""
    for name, cfg in KNOWN_GATES.items():
        if name in gate_url:
            return await resolve_wpsafelink(session, gate_url, cfg["domain"], cfg["referer"], cfg["sleep_time"])
    raise WPSafelinkError(f"No known gate config matches this url: {gate_url}")
