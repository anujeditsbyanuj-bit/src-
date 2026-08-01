# Akbots - Don't Remove Credit - @AkBots_Official
#
# hblinks.co "archives" page bypass — hblinks is part of the HubCloud
# family of link-locker/generator sites: an /archives/ID page lists several
# quality/size options, each pointing at a HubCloud-family URL (hubcloud.*,
# another hblinks link, or hubdrive) that Akbots/bypassers/hubcloud.py
# already knows how to resolve to real download links.
#
# IMPORTANT — best-effort, NOT verified against a live page: this was
# written without network access to actually fetch and inspect a real
# hblinks.co/archives/ page, based on the same common "archives listing"
# template gdflix/hubcloud-family sites use (reusing hubcloud.py's proven
# fetch_html/clean_link helpers rather than guessing those from scratch).
# The one part that's a genuine guess is _find_quality_options()'s
# selectors — if hblinks.co's real markup differs, this will return no
# options rather than wrong ones (it only matches links that both mention
# a HubCloud-family domain AND have a number in their link text, so it
# fails closed). Test against a real link and adjust the selector if it
# comes back empty.

import re
import asyncio
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup

from Akbots.bypassers.hubcloud import fetch_html, clean_link

HBLINKS_PATTERN = re.compile(r"(https?://)?[\w.-]*hblinks[\w.-]*\.\w+/archives/\S+", re.IGNORECASE)


def _find_quality_options(html: str, base_url: str) -> list:
    """Returns [(label, url), ...] for each quality/size option on an
    archives page — every <a> whose href mentions a HubCloud-family
    domain and whose visible text contains a digit (quality/size labels
    like "720p", "1.2GB" always do; nav/footer links generally don't)."""
    soup = BeautifulSoup(html, "html.parser")
    options = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = clean_link(a["href"], base_url)
        text = a.get_text(strip=True)
        if not href or href in seen or href.startswith("javascript"):
            continue
        if not re.search(r"\d", text):
            continue
        if any(k in href.lower() for k in ("hubcloud", "hblinks", "hubdrive")):
            seen.add(href)
            options.append((text or "Option", href))
    return options


def scrape_hblinks(url: str):
    """Returns {"title": str, "options": [(label, url), ...]} or None on
    failure. Each option URL is meant to be handed to
    Akbots.bypassers.hubcloud.scrape_hubcloud() (or just auto-detected —
    HUBCLOUD_PATTERN in premiumlinks.py already matches hubcloud.*/drive|
    video|packs/ URLs) to get the actual final download links."""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Reuses hubcloud.py's fetch_html: same multi-proxy fallback chain and
    # Cloudflare-interstitial detection, since hblinks sits behind the same
    # kind of protection hubcloud does.
    from curl_cffi import requests as curl_requests
    session = curl_requests.Session(impersonate="chrome120")
    html = fetch_html(url, session)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.text.strip() if title_tag else "hblinks File"

    options = _find_quality_options(html, base_url)
    if not options:
        return None
    return {"title": title, "options": options}


async def async_scrape_hblinks(url: str):
    return await asyncio.to_thread(scrape_hblinks, url)
