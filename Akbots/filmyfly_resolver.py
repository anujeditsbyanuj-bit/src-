# Akbots - Don't Remove Credit - @AkBots_Official
#
# In-process port of workers/filmyfly-resolver/src/worker.js — same
# scrape/resolve logic (filmyfly.luxe detail page -> linkmake.in ->
# new1.filesdl.in direct link), just running directly inside the bot
# instead of a separately-deployed Cloudflare Worker. Mirrors what
# Akbots/hls_proxy_routes.py does for the HLS proxy: no
# FILMYFLY_WORKER_URL / `wrangler deploy` step required — Akbots/filmyfly.py
# uses this by default and only falls back to a configured
# FILMYFLY_WORKER_URL if this in-process path fails or finds nothing.
#
# Bypass strategy for blocked/WAF'd fetches: try a direct request first,
# then a chain of free keyless CORS proxies (corsproxy.io, allorigins.win,
# thingproxy) — same as the worker's ScraperAPI fallback was meant to do,
# but without needing a paid/quota-limited API key.

import re
import random
import logging
from urllib.parse import quote, urljoin

import aiohttp

logger = logging.getLogger(__name__)

FILMYFLY_BASE = "https://filmyfly.luxe"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://filmyfly.luxe/",
}

_FREE_PROXIES = [
    lambda u: f"https://corsproxy.io/?url={quote(u, safe='')}",
    lambda u: f"https://api.allorigins.win/raw?url={quote(u, safe='')}",
    lambda u: f"https://thingproxy.freeboard.io/fetch/{u}",
]


# ─── HTTP helpers ────────────────────────────────────────────────────────

def _filesdl_headers(referer_url: str) -> dict:
    return {
        "User-Agent": _HEADERS["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer_url,
        "Upgrade-Insecure-Requests": "1",
    }


async def _fetch_via_proxy_chain(session: aiohttp.ClientSession, target_url: str) -> str:
    last_err = None
    for build_proxy_url in _FREE_PROXIES:
        proxy_url = build_proxy_url(target_url)
        try:
            async with session.get(proxy_url, headers={"User-Agent": _HEADERS["User-Agent"]},
                                    timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return await r.text(errors="replace")
                last_err = RuntimeError(f"proxy {proxy_url.split('?')[0]} returned {r.status}")
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("all proxies failed")


async def fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    """Direct GET with manual redirect handling (mirrors worker.js's
    fetchHtml/fetchFilesdlHtml); falls back to the free proxy chain if the
    direct request is blocked or errors out."""
    try:
        async with session.get(url, headers=_HEADERS, allow_redirects=True,
                                timeout=aiohttp.ClientTimeout(total=25)) as r:
            if r.ok:
                return await r.text(errors="replace")
            raise RuntimeError(f"blocked [{r.status}]")
    except Exception as direct_err:
        logger.info(f"filmyfly_resolver: direct fetch failed for {url} ({direct_err}), trying proxy chain")
        return await _fetch_via_proxy_chain(session, url)


async def fetch_filesdl_html(session: aiohttp.ClientSession, target_url: str) -> str:
    try:
        async with session.get(target_url, headers=_filesdl_headers("https://google.com/"),
                                allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=25)) as r:
            if 300 <= r.status < 400:
                location = r.headers.get("Location")
                if location:
                    resolved_url = urljoin(target_url, location)
                    async with session.get(resolved_url, headers=_filesdl_headers(target_url),
                                            allow_redirects=True,
                                            timeout=aiohttp.ClientTimeout(total=25)) as r2:
                        if r2.ok:
                            return await r2.text(errors="replace")
                        raise RuntimeError(f"blocked after redirect [{r2.status}]")
            if r.ok:
                return await r.text(errors="replace")
            raise RuntimeError(f"blocked [{r.status}]")
    except Exception as direct_err:
        logger.info(f"filmyfly_resolver: filesdl direct fetch failed ({direct_err}), trying proxy chain")
        return await _fetch_via_proxy_chain(session, target_url)


# ─── FileDL resolver ─────────────────────────────────────────────────────

_FILESDL_URL_RE = re.compile(r"https://new1\.filesdl\.in/(drive|cloud)/([^?]+)")


async def resolve_filesdl_link(session: aiohttp.ClientSession, filesdl_url: str) -> str:
    try:
        m = _FILESDL_URL_RE.search(filesdl_url)
        if not m:
            return filesdl_url

        kind, raw_id = m.group(1), m.group(2).split("?")[0]
        target_url = f"https://new1.filesdl.in/{kind}/{raw_id}"

        html = await fetch_filesdl_html(session, target_url)
        download_url = None

        # Priority 1: "Direct Download" (Fast/10Gbps) — zdownload.php or fdownload.php
        fast_match = (
            re.search(r'<a[^>]+href=["\']([^"\']*(?:zdownload|fdownload)\.php[^"\']*)["\'][^>]*>\s*Direct Download',
                       html, re.IGNORECASE)
            or re.search(r'<a[^>]+href=["\']([^"\']*(?:zdownload|fdownload)\.php[^"\']*)["\']', html, re.IGNORECASE)
        )
        if fast_match:
            download_url = fast_match.group(1)

        # Priority 2: "Cloud Direct" — r2.dev link
        if not download_url:
            cloud_match = (
                re.search(r'<a[^>]+href=["\']([^"\']*r2\.dev[^"\']*)["\'][^>]*>\s*Cloud Direct', html, re.IGNORECASE)
                or re.search(r'<a[^>]+href=["\']([^"\']*r2\.dev[^"\']*)["\']', html, re.IGNORECASE)
            )
            if cloud_match:
                download_url = cloud_match.group(1)

        # Priority 3: bbbdownload/bbdownload fallback (older page structure)
        if not download_url:
            bbb_match = (
                re.search(r'<a[^>]+href=["\'](https://bbbdownload\.filesdl\.in/[^"\']+)["\']', html, re.IGNORECASE)
                or re.search(r'<a[^>]+href=["\'](https://bbdownload\.filesdl\.in/[^"\']+)["\']', html, re.IGNORECASE)
            )
            if bbb_match:
                download_url = bbb_match.group(1)

        if not download_url:
            return filesdl_url

        download_url = download_url.replace("&amp;", "&").replace("&#038;", "&")

        # Token append for r2.dev / expired links
        if "r2.dev" in download_url or "expired=" in download_url:
            token = random.randint(1_000_000_000, 9_999_999_999)
            download_url += f"&token={token}"

        return download_url
    except Exception as e:
        logger.warning(f"filmyfly_resolver: resolve_filesdl_link failed for {filesdl_url}: {e}")
        return filesdl_url


# ─── Detail-page + linkmake scrapers ─────────────────────────────────────

def parse_detail_page(html: str) -> dict:
    result = {
        "title": None, "genre": [], "duration": None, "releaseYear": None,
        "language": None, "starcast": [], "description": None,
        "posterImage": None, "linkmakeUrl": None, "category": None,
    }

    m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if m:
        result["posterImage"] = m.group(1)

    ld_match = re.search(r'<script type="application/ld\+json">([\s\S]*?)</script>', html)
    if ld_match:
        try:
            import json
            ld = json.loads(ld_match.group(1))
            if ld.get("genre"):
                genre = ld["genre"]
                result["genre"] = [g.strip() for g in genre.split(",")] if isinstance(genre, str) else genre
            if ld.get("duration"):
                result["duration"] = ld["duration"]
            if ld.get("datePublished"):
                result["releaseYear"] = ld["datePublished"]
            if ld.get("description"):
                result["description"] = ld["description"]
            if ld.get("name"):
                result["title"] = ld["name"]
        except Exception:
            pass

    m = re.search(r'<strong>Language:</strong>\s*<span[^>]*>([^<]+)</span>', html)
    if m:
        result["language"] = m.group(1).strip()

    m = re.search(r'<strong>Starcast:</strong>\s*<span[^>]*>([^<]+)</span>', html)
    if m:
        result["starcast"] = [s.strip() for s in m.group(1).split(",") if s.strip()]

    m = re.search(r'<div class="dlbtn">\s*<a[^>]+href="(https://linkmake\.in[^"]+)"', html)
    if m:
        result["linkmakeUrl"] = m.group(1)

    m = re.search(r'»\s*<a href="[^"]+">([^<]+)</a>\s*»', html)
    if m:
        result["category"] = m.group(1).strip()

    return result


def scrape_linkmake_title(html: str):
    m = re.search(r'<title>\s*([^<]*)\s*</title>', html)
    if m:
        title = m.group(1).strip()
        title = re.sub(r'\s*[-|]\s*(LinkMake\.in|JioLink|Link Protect).*$', '', title, flags=re.IGNORECASE).strip()
        if title:
            return title

    m = re.search(r'<span style="color:#f44336;">([^<]+)</span>', html)
    if m:
        return m.group(1).strip()

    m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if m:
        return m.group(1).strip()

    return None


_INVALID_TITLE_KEYWORDS = (
    'margin', 'style', 'color', 'width', 'text-url', 'font', 'display',
    'position', 'padding', 'border', 'background', 'font-size',
    'line-height', 'important', 'url', 'css', 'media', 'max-width', 'min-width',
)


def _is_invalid_title(title: str) -> bool:
    low = title.lower()
    return any(k in low for k in _INVALID_TITLE_KEYWORDS)


_SIZE_RE = re.compile(r'(\d+(?:\.\d+)?(?:Mb|Gb|MB|GB))', re.IGNORECASE)


def _extract_size(label: str) -> str:
    m = _SIZE_RE.search(label)
    return m.group(1) if m else label.strip()


_DLINK_RE = re.compile(
    r'<div class="dlink dl"><a href="(https://[^"]+)"[^>]*><div class="dll">\s*([^<]+)</div></a></div>')
_SIMPLE_DLINK_RE = re.compile(r'<a href="(https://new1\.filesdl\.in[^"]+)"[^>]*>([^<]*)</a>')


def _extract_links(html: str) -> list:
    links = [{"filesdlUrl": u, "size": _extract_size(label)} for u, label in _DLINK_RE.findall(html)]
    if not links:
        links = [{"filesdlUrl": u, "size": _extract_size(label)} for u, label in _SIMPLE_DLINK_RE.findall(html)]
    return links


def _parse_linkmake_fallback(html: str) -> list:
    links = _extract_links(html)
    if not links:
        return []
    return [{"groupTitle": "Download Links", "links": links}]


# Group headers look like "🔰~••{HD Rip}••~🔰" / "🔰~~×××(WEB-DL)×××~~🔰" etc.
_GROUP_HEADER_RE = re.compile(r'🔰.*?\{[^}]+\}.*?🔰|🔰.*?\([^)]+\).*?🔰|🔰.*?\[[^\]]+\].*?🔰')
_GROUP_TITLE_EXTRACT_RES = (
    re.compile(r'\{([^}]+)\}'),
    re.compile(r'\(([^)]+)\)'),
    re.compile(r'\[([^\]]+)\]'),
)


def _detect_group_positions(html: str) -> list:
    """Finds each 🔰...🔰 group header in document order and pulls the
    title out of whichever bracket style ({...}, (...), [...]) it used —
    a direct port of worker.js's detectGroupPatterns()+matching loop."""
    positions = []
    for header_match in _GROUP_HEADER_RE.finditer(html):
        header_text = header_match.group(0)
        title = None
        for extractor in _GROUP_TITLE_EXTRACT_RES:
            tm = extractor.search(header_text)
            if tm:
                title = tm.group(1).strip()
                break
        if title and not _is_invalid_title(title):
            positions.append({
                "title": title,
                "start": header_match.start(),
                "end": header_match.end(),
            })
    return positions


def parse_linkmake_grouped_auto(html: str) -> list:
    positions = _detect_group_positions(html)
    if not positions:
        return _parse_linkmake_fallback(html)

    groups = []
    for i, pos in enumerate(positions):
        start = pos["end"]
        end = positions[i + 1]["start"] if i + 1 < len(positions) else len(html)
        section_html = html[start:end]
        links = _extract_links(section_html)
        if links:
            groups.append({"groupTitle": pos["title"], "links": links})

    return groups if groups else _parse_linkmake_fallback(html)


# ─── Orchestration ────────────────────────────────────────────────────────

_BAD_GROUP_HINTS = ("margin", "style", "color", "width", "text-url", "font", "Download Links")


def _build_movie_object(detail: dict, download_links: list, linkmake_title, has_groups: bool) -> dict:
    if has_groups:
        all_sizes = " ".join(l.get("size", "") for g in download_links for l in g.get("links", []))
    else:
        all_sizes = " ".join(l.get("size", "") for l in download_links)

    if re.search(r'UHD|4K', all_sizes, re.IGNORECASE):
        quality = "4K"
    elif re.search(r'1080', all_sizes):
        quality = "1080p"
    elif re.search(r'720', all_sizes):
        quality = "720p"
    elif re.search(r'480', all_sizes):
        quality = "480p"
    else:
        quality = "480p, 720p, 1080p"

    return {
        "title": linkmake_title or detail.get("title") or "Unknown Title",
        "releaseYear": detail.get("releaseYear"),
        "duration": detail.get("duration"),
        "language": detail.get("language"),
        "quality": quality,
        "genre": detail.get("genre") or [],
        "starcast": detail.get("starcast") or [],
        "posterImage": detail.get("posterImage"),
        "description": detail.get("description"),
        "isPremium": True,
        "downloadLinks": download_links,
    }


async def resolve(page_url: str) -> dict:
    """Full pipeline: detail page -> linkmake.in -> resolved direct links.
    Returns the same movie-object shape the Cloudflare Worker's /?url=
    endpoint used to, so Akbots/filmyfly.py needs no changes downstream.
    Raises RuntimeError with a short message on failure."""
    async with aiohttp.ClientSession() as session:
        detail_html = await fetch_html(session, page_url)
        detail = parse_detail_page(detail_html)

        if not detail.get("linkmakeUrl"):
            raise RuntimeError("No download link found")

        linkmake_html = await fetch_html(session, detail["linkmakeUrl"])
        linkmake_title = scrape_linkmake_title(linkmake_html)
        grouped_links = parse_linkmake_grouped_auto(linkmake_html)

        if not grouped_links:
            raise RuntimeError("No download links found")

        has_valid_groups = any(
            g.get("groupTitle") and not any(hint in g["groupTitle"] for hint in _BAD_GROUP_HINTS)
            for g in grouped_links
        )

        download_links = []
        if has_valid_groups:
            for group in grouped_links:
                title = group.get("groupTitle") or ""
                if any(hint in title for hint in ("margin", "style", "width", "text-url")):
                    continue
                links = []
                for link in group.get("links", []):
                    resolved = await resolve_filesdl_link(session, link["filesdlUrl"])
                    links.append({"size": link.get("size"), "url": resolved})
                download_links.append({"groupTitle": title, "links": links})
        else:
            for group in grouped_links:
                for link in group.get("links", []):
                    resolved = await resolve_filesdl_link(session, link["filesdlUrl"])
                    download_links.append({"size": link.get("size"), "url": resolved})

        return _build_movie_object(detail, download_links, linkmake_title, has_valid_groups)
