# Akbots - Don't Remove Credit - @AkBots_Official
#
# "go-link form" shortener bypass — covers a small family of sites
# (lopteapi.com, 3link.co, exeygo.com, vuotlink.vip, and others using the
# same template) that gate a link behind a <form id="go-link"> whose
# fields, once POSTed to their own action URL, return {"url": "..."} with
# the real destination.
#
# Ported from the "Bypass All Shortlinks" GreasyFork userscript — only
# this one narrow, site-specific piece. That script also bundles a large
# general-purpose anti-bot-detection evasion framework (fake "trusted"
# click events, tab-visibility spoofing, console/debugger hijacking,
# anti-adblock-detection removal) that is NOT ported here: it's not scoped
# to a specific known flow the way this is, so it's out of scope for this
# bot.

import re

import aiohttp
from bs4 import BeautifulSoup

GOLINK_DOMAINS = ("lopteapi.com", "3link.co", "exeygo.com", "vuotlink.vip")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def is_golink_url(url: str) -> bool:
    return any(domain in url for domain in GOLINK_DOMAINS)


async def bypass_golink(url: str) -> str | None:
    """Fetches `url`, finds its <form id="go-link">, POSTs the form's own
    fields to the form's action URL, and returns the "url" field from the
    JSON response. Returns None (never raises) if the page doesn't have
    that form, the POST fails, or the response has no usable url."""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": _UA}) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                html = await resp.text(errors="ignore")

            soup = BeautifulSoup(html, "html.parser")
            form = soup.find("form", id="go-link")
            if not form:
                return None

            action = form.get("action")
            if not action:
                return None
            if action.startswith("/"):
                base = re.match(r"https?://[^/]+", url)
                action = (base.group(0) if base else "") + action

            data = {}
            for field in form.find_all(["input", "textarea", "select"]):
                name = field.get("name")
                if name:
                    data[name] = field.get("value", "")

            async with session.post(
                action, data=data,
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                result = await resp.json(content_type=None)
                final_url = result.get("url")
                if final_url and "swiftcut.xyz" in final_url:
                    # Same cleanup the source script applies for this one
                    # known redirector: strip its tracking query params.
                    final_url = re.sub(r"[?&]i=[^&]*", "", final_url)
                return final_url or None
    except Exception:
        return None
