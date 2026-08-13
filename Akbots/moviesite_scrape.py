# Akbots - Don't Remove Credit - @AkBots_Official
#
# 6 site-specific scrapers ported from the uploaded "JFZBypassBot" project's
# FZBypass/core/bypass_scrape.py. Unlike Akbots/bypassers/*.py (which each
# resolve ONE shortener/mirror link to ONE direct file URL), these scrape a
# forum/blog-style LISTING page — a movie/anime release post with several
# quality options and several mirror links each — and return a formatted
# text summary of everything found on it, since there's no single "the"
# link to hand back.
#
# IMPORTANT PORTING NOTE: the source project's functions are declared
# `async def` but every single one of them calls plain synchronous
# `requests.get()`/cloudscraper internally — none of it is actually
# non-blocking. Calling any of them as-is from a real bot's event loop
# would freeze every other user's commands for however long that scrape
# takes. Ported here with the exact same scraping logic, just wrapped in
# asyncio.to_thread() at the call site so it runs on a worker thread
# instead of blocking the loop — same fix pattern used elsewhere in this
# codebase (e.g. Akbots/aria2_dl.py's rate-limit check).

import re
import logging
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)

try:
    from cloudscraper import create_scraper
    _cf_get = create_scraper().request
except ImportError:
    _cf_get = None

# marker (substring found in the URL) -> which scraper function handles it
MOVIESITE_DOMAINS = (
    "sharespark", "skymovieshd", "cinevood", "kayoanime", "toonworld4all",
    "1tamilmv", "1tamilblasters", "tamilmv",
)


def _sharespark_sync(url: str) -> str | None:
    if _cf_get is None:
        return "cloudscraper isn't installed — can't get past this site's anti-bot check."
    res = _cf_get("GET", "?action=printpage;".join(url.split("?")))
    soup = BeautifulSoup(res.text, "html.parser")
    gd_txt = ""
    for br in soup.findAll("br"):
        next_s = br.nextSibling
        if not (next_s and isinstance(next_s, NavigableString)):
            continue
        next2_s = next_s.nextSibling
        if next2_s and isinstance(next2_s, Tag) and next2_s.name == "br" and str(next_s).strip():
            if re.match(r"^(480p|720p|1080p)(.+)? Links:\Z", next_s):
                gd_txt += f"<b>{next_s.replace('Links:', 'GDToT Links :')}</b>\n\n"
            for s in next_s.split():
                ns = re.sub(r"\(|\)", "", s)
                if re.match(r"https?://.+\.gdtot\.\S+", ns):
                    inner = BeautifulSoup(_cf_get("GET", ns).text, "html.parser")
                    meta = inner.select('meta[property^="og:description"]')
                    if meta:
                        parsed = meta[0]["content"].replace("Download ", "").rsplit("-", maxsplit=1)
                        gd_txt += f"┎ <b>Name :</b> {parsed[0]}\n┠ <b>Size :</b> {parsed[-1]}\n┃\n┖ <b>GDTot :</b> {ns}\n\n"
                elif re.match(r"https?://pastetot\.\S+", ns):
                    nxt = re.sub(r"\(|\)|(https?://pastetot\.\S+)", "", next_s)
                    gd_txt += f"\n<b>{nxt}</b>\n┖ {ns}\n"
        if len(gd_txt) > 4000:
            return gd_txt
    return gd_txt or None


def _skymovieshd_sync(url: str) -> str | None:
    soup = BeautifulSoup(requests.get(url, allow_redirects=False, timeout=30).text, "html.parser")
    t = soup.select('div[class^="Robiul"]')
    if not t:
        return None
    gd_txt = f"<i>{t[-1].text.replace('Download ', '')}</i>"
    seen = []
    for link in soup.select('a[href*="howblogs.xyz"]'):
        href = link.get("href")
        if not href or href in seen:
            continue
        seen.append(href)
        gd_txt += f"\n\n<b>{link.text} :</b> \n"
        nsoup = BeautifulSoup(requests.get(href, allow_redirects=False, timeout=30).text, "html.parser")
        for no, a in enumerate(nsoup.select('div[class="cotent-box"] > a[href]'), start=1):
            gd_txt += f"{no}. {a['href']}\n"
    return gd_txt


def _cinevood_sync(url: str) -> str | None:
    soup = BeautifulSoup(requests.get(url, timeout=30).text, "html.parser")
    titles = soup.select("h6")
    if not titles:
        return None
    links_by_title = {}
    post_title = soup.title.string.strip() if soup.title else url

    wanted = ("gdtot", "multiup", "filepress", "gdflix", "kolop", "zipylink")
    labels = {"gdtot": "GDToT", "multiup": "MultiUp", "filepress": "FilePress",
              "gdflix": "GDFlix", "kolop": "Kolop", "zipylink": "ZipyLink"}
    for title in titles:
        title_text = title.text.strip()
        links = []
        for kind in wanted:
            found = title.find_next("a", href=lambda href, k=kind: href and k in href.lower())
            if found:
                links.append(f'<a href="{found["href"]}"><b>{labels[kind]}</b></a>')
        if links:
            links_by_title[title_text] = links

    prsd = f"<b>🔖 Title:</b> {post_title}\n"
    for title, links in links_by_title.items():
        prsd += f"\n┏<b>🏷️ Name:</b> <code>{title}</code>\n"
        prsd += "┗<b>🔗 Links:</b> " + " | ".join(links) + "\n"
    return prsd if links_by_title else None


def _kayoanime_sync(url: str) -> str | None:
    soup = BeautifulSoup(requests.get(url, timeout=30).text, "html.parser")
    gdlinks = soup.select('a[href*="drive.google.com"], a[href*="tinyurl"]')
    if not gdlinks:
        return None
    prsd = f"<b>{soup.title.string if soup.title else url}</b>"
    for n, gd in enumerate(gdlinks, start=1):
        link = gd.get("href", "")
        gd_txt = "GDrive"
        if "tinyurl" in link:
            resolved = requests.get(link, timeout=30).url
            link = resolved
            domain = urlparse(link).hostname or ""
            gd_txt = "Mega" if "mega" in domain else ("G Group" if "groups" in domain else "Direct Link")
        prsd += f"\n\n{n}. <i><b>{gd.string}</b></i>\n┗ <b>Links :</b> <a href='{link}'><b>{gd_txt}</b></a>"
    return prsd


def _tamilmv_sync(url: str) -> str | None:
    if _cf_get is None:
        return "cloudscraper isn't installed — can't get past this site's anti-bot check."
    resp = _cf_get("GET", url)
    soup = BeautifulSoup(resp.text, "html.parser")
    mags = soup.select('a[href^="magnet:?xt=urn:btih:"]')
    if not mags:
        return None
    title = soup.title.string if soup.title else url
    parsed = f"<code><b><u>{title}</u></b></code>\n\n"
    for m in mags:
        clean = m["href"].split("&")[0]
        parsed += f"<a href='{clean}'>{clean}</a>\n"
    return parsed


# toonworld4all is intentionally NOT ported. The source implementation
# chains through the rocklinks/link1s shortener bypass for every episode
# link on the page (same "wp safelink" technique as
# Akbots/bypassers/wpsafelink.py), meaning a single /toonworld command
# could trigger dozens of sequential shortener-bypass round trips — a
# realistic multi-minute operation with no progress feedback in the
# source code. That's a meaningfully bigger, slower, flakier feature than
# the other 5 (each a single page fetch), so it's left out here rather
# than silently shipping something that looks broken/hung to users. Can
# be added properly (with progress updates + the existing
# bypass_wpsafelink() helper for the per-episode links) on request.


_SCRAPERS = {
    "sharespark": _sharespark_sync,
    "skymovieshd": _skymovieshd_sync,
    "cinevood": _cinevood_sync,
    "kayoanime": _kayoanime_sync,
    "1tamilmv": _tamilmv_sync,
    "1tamilblasters": _tamilmv_sync,
    "tamilmv": _tamilmv_sync,
}


def is_moviesite_url(url: str) -> bool:
    return any(marker in url for marker in MOVIESITE_DOMAINS)


async def scrape_moviesite(url: str):
    """Returns a formatted HTML text summary of everything found on the
    page (multiple quality options / mirror links), or None if nothing
    recognizable was found, or an error string starting with something
    other than the usual formatting on outright failure. Never raises."""
    import asyncio
    marker = next((m for m in MOVIESITE_DOMAINS if m in url), None)
    if marker is None or marker == "toonworld4all":
        return None
    scraper = _SCRAPERS.get(marker)
    if scraper is None:
        return None
    try:
        return await asyncio.to_thread(scraper, url)
    except Exception as e:
        logger.info(f"moviesite_scrape: {marker} scrape failed: {e}")
        return None
