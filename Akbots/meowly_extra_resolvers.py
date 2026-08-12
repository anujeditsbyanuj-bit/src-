# Akbots - Don't Remove Credit - @AkBots_Official
#
# meowly_extra_resolvers.py — ports every Move-main scraper NOT already
# covered by Akbots/meowly_resolvers.py (which only had vidsrc, vidrock,
# peachify, videasy). Ported 1:1 from Move-main (Node/fetch) to Python
# (aiohttp/pycryptodome), same as meowly_resolvers.py.
#
# Covered here (22 sources):
#   cinesu, flaxmovies, vaplayer (vidapi.js), vapor (vapor.js — now a
#   separate host from vaplayer), icefy, movsrc, toustream, flixtrz,
#   vixsrc, vidify, fsonic, fsharetv, lookmovie, moviebox, nhdapi, vidzee,
#   02movie, vidnest, miruro, tryembed, cinezo, flixhq,
#   meowtv (gate.flicky.host)
#
# NOT ported: vidfun.js, vidlink.js — both resolve their token/signature
# through a compiled Kotlin/WASM module (extensions/vidfun.wasm,
# extensions/fu.wasm) invoked via WebAssembly.instantiate + libsodium in
# the browser. That's a compiled binary blob with no pycryptodome/aiohttp
# equivalent; doing it properly needs a JS/WASM runtime (e.g. shelling out
# to Node), which this project doesn't otherwise depend on. Left out
# rather than faked — same call meowly_resolvers.py already made for
# vidlink.js.
#
# All resolver functions share one signature so the dispatcher can call
# them uniformly, even though most ignore the extra params:
#   async def resolve_x(tmdb_id, season=None, episode=None, title="", audio="sub")
# and return the same shape as meowly_resolvers.py (or None on failure):
#   {"videoUrl": str, "qualities": [{"quality": str, "url": str}, ...],
#    "headers": {...}, "subtitles": [{"url": str, "label": str}, ...]}

import asyncio
import base64
import gzip
import hashlib
import hmac as hmac_mod
import json
import logging
import random
import re
import time
from urllib.parse import urljoin, urlparse, quote

import aiohttp
from Crypto.Cipher import AES
from Crypto.Hash import SHA256, SHA512
from Crypto.Protocol.KDF import PBKDF2
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS
from Crypto.Util.Padding import unpad

try:
    from config import TMDB_API_KEY
except ImportError:
    TMDB_API_KEY = ""

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/150 Safari/537.36")

_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]


def _rand_ua() -> str:
    return random.choice(_UA_LIST)


# ── generic HTTP helpers ────────────────────────────────────────────────

async def _get_text(session, url, headers=None, timeout=_TIMEOUT):
    try:
        async with session.get(url, headers=headers, timeout=timeout) as r:
            if r.status != 200:
                return None
            return await r.text()
    except Exception:
        return None


async def _get_json(session, url, headers=None, timeout=_TIMEOUT):
    try:
        async with session.get(url, headers=headers, timeout=timeout) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    except Exception:
        return None


async def _post_json(session, url, json_body=None, data=None, headers=None, timeout=_TIMEOUT):
    try:
        async with session.post(url, json=json_body, data=data, headers=headers, timeout=timeout) as r:
            if r.status != 200:
                return None
            return await r.json(content_type=None)
    except Exception:
        return None


def _single(url, headers=None, subtitles=None):
    """Wrap one URL into the standard resolver return shape."""
    return {
        "videoUrl": url,
        "qualities": [{"quality": "Auto", "url": url}],
        "headers": headers or {},
        "subtitles": subtitles or [],
    }


def _multi(urls_with_labels, headers=None, subtitles=None):
    """urls_with_labels: list of (label, url) tuples. First is used as videoUrl."""
    if not urls_with_labels:
        return None
    return {
        "videoUrl": urls_with_labels[0][1],
        "qualities": [{"quality": lbl, "url": u} for lbl, u in urls_with_labels],
        "headers": headers or {},
        "subtitles": subtitles or [],
    }


# ── shared TMDB / anime / AniList helpers (used by vidnest, miruro, tryembed) ──

async def _tmdb_anime_info(session, tmdb_id, season):
    """Mirrors getAnimeInfo() in vidnest.js/tryembed.js."""
    if not TMDB_API_KEY:
        return {"isAnime": False, "titles": [], "year": None}
    try:
        show_task = _get_json(session, f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}")
        season_task = (_get_json(session, f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}?api_key={TMDB_API_KEY}")
                       if season else _none())
        show_data, season_data = await asyncio.gather(show_task, season_task)

        genres = (show_data or {}).get("genres") or []
        origin_country = (show_data or {}).get("origin_country") or []
        original_language = (show_data or {}).get("original_language") or ""
        is_anime = any(g.get("id") == 16 for g in genres) and ("JP" in origin_country or original_language == "ja")

        titles = []
        if season_data and season_data.get("name"):
            titles.append(season_data["name"])
        t = (show_data or {}).get("title") or (show_data or {}).get("name") or ""
        ot = (show_data or {}).get("original_title") or (show_data or {}).get("original_name") or ""
        if t:
            titles.append(t)
        if ot and ot != t:
            titles.append(ot)

        year = None
        date_str = (season_data or {}).get("air_date") or (show_data or {}).get("first_air_date") or ""
        if date_str:
            try:
                year = int(date_str[:4])
            except Exception:
                year = None

        return {"isAnime": is_anime, "titles": list(dict.fromkeys([x for x in titles if x])), "year": year}
    except Exception:
        return {"isAnime": False, "titles": [], "year": None}


async def _none():
    return None


async def _anilist_search(session, search_title):
    query = ('query($s:String){Page(page:1,perPage:10){media(search:$s,type:ANIME){'
             'id title{romaji english native}startDate{year}format}}}')
    data = await _post_json(
        session, "https://graphql.anilist.co",
        json_body={"query": query, "variables": {"s": search_title}},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if not data:
        return []
    return ((data.get("data") or {}).get("Page") or {}).get("media") or []


async def _tmdb_to_anilist(session, tmdb_id, season, info=None, media_type="tv"):
    """Mirrors tmdbToAnilist() in vidnest.js/miruro.js/tryembed.js — tries
    api.ani.zip's TMDB->AniList mapping first, falls back to fuzzy title
    search against the AniList GraphQL API."""
    try:
        mapping = await _get_json(
            session, f"https://api.ani.zip/mappings?tmdb_id={tmdb_id}&type={media_type}&season={season or 1}")
        if mapping:
            aid = ((mapping.get("mappings") or [{}])[0]).get("anilist_id")
            if aid:
                return aid
    except Exception:
        pass

    if not TMDB_API_KEY:
        return None

    titles = list(info.get("titles") or []) if info else []
    year = info.get("year") if info else None

    if not titles:
        info2 = await _tmdb_anime_info(session, tmdb_id, season)
        titles = info2["titles"]
        year = info2["year"]

    titles = list(dict.fromkeys([t for t in titles if t]))
    if not titles:
        return None

    best_id, best_score = None, -1
    for search_title in titles:
        results = await _anilist_search(session, search_title)
        search_lower = search_title.lower()
        for entry in results:
            entry_titles = [x for x in [
                (entry.get("title") or {}).get("romaji"),
                (entry.get("title") or {}).get("english"),
                (entry.get("title") or {}).get("native"),
            ] if x]
            entry_titles = [x.lower() for x in entry_titles]
            score = 0
            if any(t == search_lower for t in entry_titles):
                score += 5
            elif any(search_lower in t or t in search_lower for t in entry_titles):
                score += 3
            else:
                continue
            entry_year = (entry.get("startDate") or {}).get("year")
            if year and entry_year:
                diff = abs(entry_year - year)
                if diff == 0:
                    score += 3
                elif diff == 1:
                    score += 1
                elif diff > 2:
                    score -= 3
            if entry.get("format") in ("TV", "TV_SHORT", "ONA", "OVA"):
                score += 1
            if score > best_score:
                best_score, best_id = score, entry.get("id")
        if best_score >= 8:
            break
    return best_id


# ── cinesu (cine.su) — plain m3u8 URL check, no crypto ──────────────────

_CS_BASE = "https://cine.su"
_CS_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _CS_BASE + "/en/watch",
    "Origin": _CS_BASE,
}


async def resolve_cinesu(tmdb_id, season=None, episode=None, title="", audio="sub"):
    url = (f"{_CS_BASE}/v1/stream/master/tv/{tmdb_id}/{season}/{episode}.m3u8" if season and episode
           else f"{_CS_BASE}/v1/stream/master/movie/{tmdb_id}.m3u8")
    async with aiohttp.ClientSession() as session:
        text = await _get_text(session, url, _CS_HEADERS)
        if not text or not text.strip().startswith("#EXTM3U"):
            return None
        return _single(url, _CS_HEADERS)


# ── flaxmovies (Supabase edge functions) ────────────────────────────────

_FM_API = "https://itjiocunahckqxcnzpoy.supabase.co/functions/v1"
_FM_APIKEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Iml0amlvY3VuYWhja3F4Y256cG95Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzczMzM1MjQsImV4cCI6MjA5MjkwOTUyNH0.9x9ykdHAzrv_GSvnawPeQaOxQeh3sZg0QAh4u9VOF4M")
_FM_HEADERS = {
    "apikey": _FM_APIKEY,
    "authorization": f"Bearer {_FM_APIKEY}",
    "content-type": "application/json",
    "origin": "https://flaxmovies.xyz",
    "referer": "https://flaxmovies.xyz/",
}


async def resolve_flaxmovies(tmdb_id, season=None, episode=None, title="", audio="sub"):
    is_movie = not season
    endpoint = f"{_FM_API}/get-movie" if is_movie else f"{_FM_API}/get-tv"
    body = {"id": str(tmdb_id)} if is_movie else {"id": str(tmdb_id), "season": int(season), "episode": int(episode or 1)}
    async with aiohttp.ClientSession() as session:
        data = await _post_json(session, endpoint, json_body=body, headers=_FM_HEADERS)
        if not data or not data.get("signed_url"):
            return None
        headers = {"Referer": "https://flaxmovies.xyz/", "Origin": "https://flaxmovies.xyz"}
        return _single(data["signed_url"], headers)


# ── vaplayer (vidapi.js — streamdata.vaplayer.ru) ────────────────────────

_VP_IFRAME = "https://brightpathsignals.com"
_VP_API = "https://streamdata.vaplayer.ru/api.php"


def _vp_headers():
    return {"User-Agent": _rand_ua(), "Referer": f"{_VP_IFRAME}/", "Origin": _VP_IFRAME, "Accept": "*/*"}


async def resolve_vaplayer(tmdb_id, season=None, episode=None, title="", audio="sub"):
    headers = _vp_headers()
    params = {"tmdb": str(tmdb_id)}
    if season and episode:
        params.update({"type": "tv", "season": str(season), "episode": str(episode)})
    else:
        params["type"] = "movie"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(_VP_API, params=params, headers=headers, timeout=_TIMEOUT) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
        except Exception:
            return None
        if data.get("status_code") != "200" or not data.get("data"):
            return None
        urls = [u for u in (data["data"].get("stream_urls") or []) if "tmstrd.justhd.tv" not in u]
        if not urls:
            return None
        return _multi([("Auto", u) for u in urls], headers)


# ── vapor (vapor.js — api.dmvdriverseducation.org, plain JSON) ──────────
# NOTE: vapor.js now points at a different host than vidapi.js (used to be
# the same vaplayer.ru scraper, no longer is), so it's registered as its
# own source instead of being folded into resolve_vaplayer.

_VA_BASE = "https://api.dmvdriverseducation.org"
_VA_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json",
}


async def resolve_vapor(tmdb_id, season=None, episode=None, title="", audio="sub"):
    url = (f"{_VA_BASE}/v1/tv/{tmdb_id}/seasons/{season}/episodes/{episode}" if season and episode
           else f"{_VA_BASE}/v1/movies/{tmdb_id}")
    async with aiohttp.ClientSession() as session:
        data = await _get_json(session, url, _VA_HEADERS)
        if not data:
            return None
        stream_url = (data.get("url") or data.get("stream") or data.get("source")
                      or (data.get("data") or {}).get("url") or data.get("file"))
        if not stream_url:
            sources = data.get("sources")
            if isinstance(sources, list) and sources:
                stream_url = sources[0].get("url") or sources[0].get("file")
        if not stream_url:
            return None
        stream_url = stream_url.replace("http://localhost:3030", _VA_BASE)
        return _single(stream_url)


# ── icefy (streams.icefy.top) — plain JSON, no crypto ───────────────────

_IC_BASE = "https://streams.icefy.top"
_IC_HEADERS = {"Referer": _IC_BASE + "/", "Origin": _IC_BASE}


async def resolve_icefy(tmdb_id, season=None, episode=None, title="", audio="sub"):
    url = (f"{_IC_BASE}/tv/{tmdb_id}/{season}/{episode}" if season and episode
           else f"{_IC_BASE}/movie/{tmdb_id}")
    async with aiohttp.ClientSession() as session:
        data = await _get_json(session, url, {"Referer": _IC_BASE + "/"})
        if not data or not data.get("stream"):
            return None
        return _single(data["stream"], _IC_HEADERS)


# ── movsrc (api.dmvdriverseducation.org) — plain JSON, proxy-URL rewrite ─

_MS_API = "https://api.dmvdriverseducation.org"


def _ms_rewrite(raw_url):
    if not raw_url:
        return None
    try:
        u = urlparse(raw_url)
        if u.scheme:
            path = u.path + (("?" + u.query) if u.query else "")
            return _MS_API + path
        if raw_url.startswith("/"):
            return _MS_API + raw_url
        return raw_url
    except Exception:
        return raw_url


def _ms_extract_from_proxy(proxy_url):
    try:
        u = urlparse(proxy_url)
        from urllib.parse import parse_qs, unquote
        qs = parse_qs(u.query)
        data_param = qs.get("data", [None])[0]
        if data_param:
            decoded = json.loads(unquote(data_param))
            return decoded.get("url"), decoded.get("headers")
    except Exception:
        pass
    return proxy_url, None


async def resolve_movsrc(tmdb_id, season=None, episode=None, title="", audio="sub"):
    url = (f"{_MS_API}/v1/tv/{tmdb_id}/seasons/{season}/episodes/{episode}" if season and episode
           else f"{_MS_API}/v1/movies/{tmdb_id}")
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    async with aiohttp.ClientSession() as session:
        data = await _get_json(session, url, headers)
        if not data:
            return None
        sources = data.get("sources")
        if not isinstance(sources, list) or not sources:
            return None

        entries = []
        for q in ("1080p", "720p", "480p", "360p"):
            best = next((s for s in sources if s.get("quality") == q), None)
            if best:
                entries.append((q, best))
        if not entries:
            entries = [("Auto", sources[0])]

        urls_with_labels = []
        for label, src in entries:
            rewritten = _ms_rewrite(src.get("url"))
            if not rewritten:
                continue
            inner_url, _ = _ms_extract_from_proxy(rewritten)
            if inner_url:
                urls_with_labels.append((label, inner_url))
        if not urls_with_labels:
            return None
        return _multi(urls_with_labels, headers)


# ── toustream (toustream.xyz) — server list HTML scrape ─────────────────

_TS_BASE = "https://toustream.xyz"
_TS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_TS_SERVER_RE = re.compile(r'<div[^>]+data-server="([^"]+)"[^>]*>.*?<span class="order-badge">(\d+)</span>', re.S)


async def _ts_fetch_servers(session, tmdb_id, season, episode):
    is_movie = not (season and episode)
    page_path = f"/tou/movies/{tmdb_id}" if is_movie else f"/tou/tv/{tmdb_id}/{season}/{episode}"
    html = await _get_text(session, f"{_TS_BASE}{page_path}", {"User-Agent": _TS_UA})
    if not html:
        return None
    servers = [(m.group(1), int(m.group(2))) for m in _TS_SERVER_RE.finditer(html)]
    servers.sort(key=lambda x: x[1])
    return [s[0] for s in servers] if servers else None


async def _ts_try_server(session, api_path, sv, referer):
    try:
        async with session.get(f"{_TS_BASE}{api_path}", params={"server": sv},
                                headers={"Referer": referer, "Accept": "application/json", "User-Agent": _TS_UA},
                                timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
            set_cookie = r.headers.get("Set-Cookie")
    except Exception:
        return None
    if not data or not data.get("streamUrl"):
        return None
    url = data["streamUrl"] if data["streamUrl"].startswith("http") else f"{_TS_BASE}{data['streamUrl']}"
    headers = {"Referer": f"{_TS_BASE}/", "Origin": _TS_BASE, "User-Agent": _TS_UA}
    if set_cookie:
        headers["Cookie"] = set_cookie.split(";")[0].strip()
    return url, headers


async def resolve_toustream(tmdb_id, season=None, episode=None, title="", audio="sub"):
    is_movie = not (season and episode)
    api_path = f"/tou/get-source/movie/{tmdb_id}" if is_movie else f"/tou/get-source/tv/{tmdb_id}/{season}/{episode}"
    referer = f"{_TS_BASE}/tou/{'movies' if is_movie else 'tv'}/{tmdb_id}" + ("" if is_movie else f"/{season}/{episode}")

    async with aiohttp.ClientSession() as session:
        servers = await _ts_fetch_servers(session, tmdb_id, season, episode)
        if not servers:
            return None
        valid = [sv for sv in servers if re.fullmatch(r"[a-zA-Z]+", sv)]
        if not valid:
            return None
        results = await asyncio.gather(*[_ts_try_server(session, api_path, sv, referer) for sv in valid],
                                        return_exceptions=True)
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            url, headers = res
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r2:
                    if r2.status < 400:
                        return _single(url, headers)
            except Exception:
                continue
    return None


# ── flixtrz (flixtrz.com) — multi-provider aggregator ────────────────────

_FT_BASE = "https://flixtrz.com/v1"
_FT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"


async def _ft_providers(session):
    data = await _get_json(session, f"{_FT_BASE}/providers", {"User-Agent": _FT_UA})
    if not data:
        return None
    return [p.get("id") for p in data if p.get("id")]


def _ft_extract(raw):
    if not raw:
        return None
    if isinstance(raw, str):
        return {"url": raw, "headers": {}}
    url = raw.get("url") or raw.get("stream") or raw.get("src") or raw.get("link")
    if not url:
        return None
    return {"url": url, "headers": raw.get("headers") or {}}


def _ft_collect(data):
    out = []
    if not data:
        return out
    candidates = []
    if isinstance(data.get("sources"), list):
        candidates.extend(data["sources"])
    if isinstance(data.get("streams"), list):
        candidates.extend(data["streams"])
    if data.get("url"):
        candidates.append(data)
    if data.get("stream"):
        candidates.append(data)
    for c in candidates:
        ex = _ft_extract(c)
        if not ex or not (ex["url"] or "").startswith("http"):
            continue
        out.append({"url": ex["url"], "headers": ex["headers"], "quality": c.get("quality") or c.get("resolution") or "auto"})
    return out


async def resolve_flixtrz(tmdb_id, season=None, episode=None, title="", audio="sub"):
    is_tv = bool(season and episode)
    async with aiohttp.ClientSession() as session:
        providers = await _ft_providers(session)
        if not providers:
            return None

        async def fetch_one(pid):
            url = (f"{_FT_BASE}/tv/{tmdb_id}/seasons/{season}/episodes/{episode}/by/{pid}" if is_tv
                   else f"{_FT_BASE}/movies/{tmdb_id}/by/{pid}")
            data = await _get_json(session, url, {"User-Agent": _FT_UA})
            return _ft_collect(data)

        results = await asyncio.gather(*[fetch_one(p) for p in providers], return_exceptions=True)
        all_urls = []
        for res in results:
            if isinstance(res, Exception):
                continue
            all_urls.extend(res)
        if not all_urls:
            return None
        return _multi([(str(u.get("quality") or "Auto"), u["url"]) for u in all_urls], all_urls[0]["headers"])


# ── vixsrc (vixsrc.to) — token-based m3u8, no CDN proxy needed ──────────

_VX_BASE = "https://vixsrc.to"
_VX_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": _VX_BASE + "/",
    "Origin": _VX_BASE,
}
_VX_TOKEN_RE = re.compile(r'token["\']\s*:\s*["\']([^"\']+)')
_VX_EXPIRES_RE = re.compile(r'expires["\']\s*:\s*["\']([^"\']+)')
_VX_PLAYLIST_RE = re.compile(r'url\s*:\s*["\']([^"\']+)')
_VX_LANG_RE = re.compile(r'lang(?:uage)?["\']\s*:\s*["\']([a-z]{2,5})', re.I)


def _vx_extract_token(html):
    token = _VX_TOKEN_RE.search(html)
    expires = _VX_EXPIRES_RE.search(html)
    playlist = _VX_PLAYLIST_RE.search(html)
    lang_m = _VX_LANG_RE.search(html)
    if not (token and expires and playlist):
        return None
    lang = lang_m.group(1) if lang_m else "en"
    try:
        if int(expires.group(1)) * 1000 - 60_000 < time.time() * 1000:
            return None
    except Exception:
        return None
    return {"token": token.group(1), "expires": expires.group(1), "playlist": playlist.group(1), "lang": lang}


def _vx_best_variant(content, master_url):
    lines = content.split("\n")
    best_res, best_url = 0, None
    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        m = re.search(r"RESOLUTION=\d+x(\d+)", line)
        res = int(m.group(1)) if m else 0
        url_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not url_line or url_line.startswith("#") or "localhost" in url_line or "127.0.0.1" in url_line:
            continue
        if res > best_res:
            best_res = res
            best_url = url_line if url_line.startswith("http") else urljoin(master_url, url_line)
    return best_url


async def resolve_vixsrc(tmdb_id, season=None, episode=None, title="", audio="sub"):
    api_url = f"{_VX_BASE}/api/tv/{tmdb_id}/{season}/{episode}" if (season and episode) else f"{_VX_BASE}/api/movie/{tmdb_id}"
    async with aiohttp.ClientSession() as session:
        api_data = await _get_json(session, api_url, _VX_HEADERS)
        if not api_data or not api_data.get("src"):
            return None
        embed_url = api_data["src"] if api_data["src"].startswith("http") else _VX_BASE + api_data["src"]
        html = await _get_text(session, embed_url, _VX_HEADERS)
        if not html:
            return None
        token_data = _vx_extract_token(html)
        if not token_data:
            return None
        sep = "&" if "?" in token_data["playlist"] else "?"
        master_url = (f"{token_data['playlist']}{sep}token={token_data['token']}"
                      f"&expires={token_data['expires']}&h=1&lang={token_data['lang']}")
        playlist_text = await _get_text(session, master_url, _VX_HEADERS)
        if not playlist_text or not playlist_text.strip().startswith("#EXTM3U"):
            return None
        variant = _vx_best_variant(playlist_text.strip(), master_url)
        return _single(variant or master_url, _VX_HEADERS)


# ── vidify (pro.vidify.top) — same rcp/prorcp pattern as vidsrc ─────────

_VF_BASE = "https://pro.vidify.top"
_VF_HEADERS = {"User-Agent": _UA, "Referer": _VF_BASE + "/"}
_VF_PROXY_HEADERS = {"User-Agent": _UA, "Referer": "https://cloudnestra.com/", "Origin": "https://cloudnestra.com", "Accept": "*/*"}
_VF_PLAYER_DOMAINS = {
    "{v1}": "neonhorizonworkshops.com", "{v2}": "wanderlynest.com",
    "{v3}": "orchidpixelgardens.com", "{v4}": "cloudnestra.com",
}
_VF_DATASERVER_RE = re.compile(r'data-server=["\']([A-Za-z0-9+/=\-]+)["\']', re.I)
_VF_PRORCP_RE = re.compile(r'''src:\s*['"]([^'"]*/prorcp/[^'"]+)['"]''', re.I)
_VF_FILE_RE = re.compile(r'''file\s*:\s*["']([^"']+)["']''', re.I)


def _vf_extract_m3u8(html):
    m = _VF_FILE_RE.search(html)
    if not m:
        return None
    urls = []
    for tmpl in re.split(r"\s+or\s+", m.group(1), flags=re.I):
        u = tmpl
        for ph, domain in _VF_PLAYER_DOMAINS.items():
            u = u.replace(ph, domain)
        if "{" not in u and "}" not in u:
            urls.append(u)
    return urls or None


async def resolve_vidify(tmdb_id, season=None, episode=None, title="", audio="sub"):
    page_url = f"{_VF_BASE}/embed/tv/{tmdb_id}/{season}/{episode}" if season else f"{_VF_BASE}/embed/movie/{tmdb_id}"
    async with aiohttp.ClientSession() as session:
        html1 = await _get_text(session, page_url, _VF_HEADERS)
        if not html1:
            return None
        b64 = _VF_DATASERVER_RE.search(html1)
        if not b64:
            return None
        try:
            padded = b64.group(1).replace("-", "+").replace("_", "/")
            padded += "=" * (-len(padded) % 4)
            rcp_url = base64.b64decode(padded).decode("utf-8")
        except Exception:
            return None
        if not rcp_url.startswith("http"):
            return None

        html2 = await _get_text(session, rcp_url, {**_VF_HEADERS, "Referer": "https://cloudnestra.com/"})
        if not html2:
            return None
        prorcp = _VF_PRORCP_RE.search(html2)
        if prorcp:
            base = rcp_url[:rcp_url.index("/", rcp_url.index("//") + 2)]
            player_url = prorcp.group(1) if prorcp.group(1).startswith("http") else base + prorcp.group(1)
        else:
            player_url = rcp_url.replace("/rcp/", "/prorcp/")

        html3 = await _get_text(session, player_url, {**_VF_HEADERS, "Referer": rcp_url})
        if not html3:
            return None
        urls = _vf_extract_m3u8(html3)
        if not urls:
            return None
        return _multi([("HLS", u) for u in urls], _VF_PROXY_HEADERS)


# ── fsonic / fsharetv (fsonic.net, fsharetv.cc — movies only) ───────────

_FS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"


async def _fs_tmdb_details(session, tmdb_id):
    if not TMDB_API_KEY:
        return None
    d = await _get_json(session, f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}",
                         {"User-Agent": _FS_UA})
    if not d:
        return None
    return {"imdbId": d.get("imdb_id"), "title": d.get("title"), "year": (d.get("release_date") or "")[:4]}


async def resolve_fsonic(tmdb_id, season=None, episode=None, title="", audio="sub"):
    if season:
        return None
    base = "https://www.fsonic.net"
    headers = {"User-Agent": _FS_UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    async with aiohttp.ClientSession() as session:
        details = await _fs_tmdb_details(session, tmdb_id)
        if not details or not details.get("title"):
            return None

        search_html = await _get_text(session, f"{base}/movie/search/{quote(details['title'])}", headers)
        if not search_html:
            return None
        matches = re.findall(r'href="(/watch/[^"]+)"', search_html)
        if not matches:
            return None
        watch_slug = next((m for m in matches if details.get("year") and details["year"] in m), matches[0])

        watch_html = await _get_text(session, f"{base}{watch_slug}", headers)
        if not watch_html:
            return None
        init_m = re.search(r"ng-init=\"init\('([^']+)',\s*'[^']+',\s*'([^']+)'", watch_html)
        if not init_m:
            return None
        token, trailer = init_m.group(1), init_m.group(2)

        api_url = f"{base}/api/source/{token}?trailer={trailer}&type=watch"
        json_data = await _get_json(session, api_url, {**headers, "Accept": "application/json, text/plain, */*",
                                                         "Referer": f"{base}{watch_slug}"})
        if not json_data or json_data.get("status") != "ok":
            return None

        groups = []
        sources = ((json_data.get("data") or {}).get("file") or {}).get("sources") or []
        if sources:
            groups.append(sources)
        for grp in ((json_data.get("data") or {}).get("file") or {}).get("alternatives") or []:
            if grp:
                groups.append(grp)

        urls = []
        for grp in groups:
            valid = [s for s in grp if s.get("src")]
            if not valid:
                continue
            valid.sort(key=lambda s: int(re.sub(r"\D", "", str(s.get("quality", "0"))) or 0), reverse=True)
            best = valid[0]
            u = best["src"] if best["src"].startswith("http") else f"https://fsharetv.co{best['src']}"
            urls.append(u)
        urls = list(dict.fromkeys(urls))
        if not urls:
            return None
        ref_headers = {**headers, "Referer": "https://fsharetv.co/"}
        return _multi([("Auto", u) for u in urls], ref_headers)


async def resolve_fsharetv(tmdb_id, season=None, episode=None, title="", audio="sub"):
    if season:
        return None
    base = "https://fsharetv.cc"
    trailer = "Png81APqcxU"
    headers = {"User-Agent": _FS_UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9", "Referer": base}
    async with aiohttp.ClientSession() as session:
        if not TMDB_API_KEY:
            return None
        d = await _get_json(session, f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids")
        imdb_id = (d or {}).get("imdb_id") or ((d or {}).get("external_ids") or {}).get("imdb_id")
        if not imdb_id:
            return None

        watch_html = await _get_text(session, f"{base}/movie/{imdb_id}", headers)
        if not watch_html:
            return None
        m = re.search(r'href="(/w/[^"]+)"', watch_html)
        if not m:
            return None
        watch_path = m.group(1)

        page_html = await _get_text(session, f"{base}{watch_path}", headers)
        if not page_html:
            return None
        source_id = None
        for pattern in (r'Movie\.setSource\("([^"]+)"', r'setSource\("([^"]+)"', r"setSource\('([^']+)'",
                        r'"source_id"\s*:\s*"([^"]+)"', r'source_id\s*=\s*"([^"]+)"',
                        r'file_id\s*=\s*"([^"]+)"', r'"file_id"\s*:\s*"([^"]+)"'):
            m2 = re.search(pattern, page_html)
            if m2:
                source_id = m2.group(1)
                break
        if not source_id:
            return None

        api_headers = {**headers, "Accept": "application/json, */*; q=0.01", "X-Requested-With": "XMLHttpRequest", "Referer": f"{base}/"}
        json_data = await _get_json(session, f"{base}/api/file/{source_id}/source?trailer={trailer}&type=watch", api_headers)
        if not json_data or json_data.get("status") != "ok":
            return None
        sources = ((json_data.get("data") or {}).get("file") or {}).get("sources") or []
        valid = [s for s in sources if s.get("src")]
        if not valid:
            return None
        valid.sort(key=lambda s: int(re.sub(r"\D", "", str(s.get("quality", "0"))) or 0), reverse=True)
        urls = [s["src"] if s["src"].startswith("http") else f"{base}{s['src']}" for s in valid]
        return _multi([("Auto", u) for u in urls], headers)


# ── lookmovie (lookmovie2.to / lookmovie.foundation) ─────────────────────

_LM_DOMAINS = ["https://www.lookmovie2.to", "https://lookmovie2.to", "https://lookmovie.foundation"]
_LM_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
               "Accept-Language": "en-US,en;q=0.9"}


async def _lm_search(session, endpoint, title, year):
    for base in _LM_DOMAINS:
        headers = {**_LM_HEADERS, "Accept": "application/json", "Referer": f"{base}/", "X-Requested-With": "XMLHttpRequest"}
        data = await _get_json(session, f"{base}/api/v1/{endpoint}/do-search/?q={quote(title)}", headers)
        results = (data or {}).get("result") or []
        if not results:
            continue
        match = (next((r for r in results if str(r.get("year")) == str(year)), None)
                 or next((r for r in results if (r.get("title") or "").lower() == title.lower()), None)
                 or results[0])
        if match:
            return match, base
    return None


async def _lm_play_page(session, base, slug, endpoint):
    headers = {**_LM_HEADERS, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Referer": f"{base}/"}
    html = await _get_text(session, f"{base}/{endpoint}/play/{slug}", headers)
    if not html:
        return None
    m = re.search(r"window\[['\"](?:movie|show)_storage['\"]\]\s*=\s*\{([^}]+)\}", html)
    if not m:
        return None
    block = m.group(1)
    hash_m = re.search(r"hash\s*:\s*['\"]([^'\"]+)['\"]", block)
    exp_m = re.search(r"expires\s*:\s*(\d+)", block)
    if not (hash_m and exp_m):
        return None
    return {"html": html, "hash": hash_m.group(1), "expires": exp_m.group(1)}


def _lm_movie_id(html):
    m = re.search(r"window\[['\"]movie_storage['\"]\]\s*=\s*\{([^}]+)\}", html)
    if m:
        idm = re.search(r"id_movie\s*:\s*(\d+)", m.group(1))
        if idm:
            return idm.group(1)
    m2 = re.search(r"['\"]?(?:id_movie|movieId)['\"]?\s*[:=]\s*['\"]?(\d+)['\"]?", html, re.I)
    return m2.group(1) if m2 else None


def _lm_episode_id(html, s, e):
    m = re.search(r"window\[['\"]show_storage['\"]\]\s*=\s*\{([^}]+)\}", html, re.S)
    if m:
        season_m = re.search(r"seasons\s*:\s*(\[[\s\S]+?\])\s*[,}]", m.group(1))
        if season_m:
            try:
                seasons = json.loads(season_m.group(1))
                season = next((x for x in seasons if str(x.get("season", (x.get("meta") or {}).get("season"))) == str(s)), None)
                if season:
                    eps = season.get("episodes")
                    ep = None
                    if isinstance(eps, list):
                        ep = next((x for x in eps if str(x.get("episode")) == str(e)), None)
                    elif isinstance(eps, dict):
                        ep = eps.get(str(e)) or next((x for x in eps.values() if str(x.get("episode")) == str(e)), None)
                    if ep:
                        return str(ep.get("id_episode", ep.get("id")))
            except Exception:
                pass
    attr_m = (re.search(rf'data-season="{s}"[^>]*?data-episode="{e}"[^>]*?data-id="(\d+)"', html, re.I)
              or re.search(rf'data-episode="{e}"[^>]*?data-season="{s}"[^>]*?data-id="(\d+)"', html, re.I))
    return attr_m.group(1) if attr_m else None


async def resolve_lookmovie(tmdb_id, season=None, episode=None, title="", audio="sub"):
    if not TMDB_API_KEY:
        return None
    async with aiohttp.ClientSession() as session:
        is_tv = bool(season and episode)
        tmdb_url = (f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}" if is_tv
                    else f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}")
        tmdb_data = await _get_json(session, tmdb_url)
        if not tmdb_data:
            return None
        t = tmdb_data.get("title") or tmdb_data.get("name")
        year = (tmdb_data.get("first_air_date") or tmdb_data.get("release_date") or "")[:4]
        if not t:
            return None

        endpoint = "shows" if is_tv else "movies"
        search_res = await _lm_search(session, endpoint, t, year)
        if not search_res:
            return None
        match, base = search_res
        slug = match.get("slug")
        if not slug:
            return None

        page_data = await _lm_play_page(session, base, slug, endpoint)
        if not page_data:
            return None

        stream_id = _lm_episode_id(page_data["html"], season, episode) if is_tv else (match.get("id_movie") or match.get("id") or _lm_movie_id(page_data["html"]))
        if not stream_id:
            return None

        access_endpoint = (f"{base}/api/v1/security/episode-access?id_episode={stream_id}&hash={page_data['hash']}&expires={page_data['expires']}" if is_tv
                            else f"{base}/api/v1/security/movie-access?id_movie={stream_id}&hash={page_data['hash']}&expires={page_data['expires']}")
        headers = {**_LM_HEADERS, "Accept": "application/json", "Referer": f"{base}/", "X-Requested-With": "XMLHttpRequest"}
        data = await _get_json(session, access_endpoint, headers)
        streams = (data or {}).get("streams") or ((data or {}).get("result") or {}).get("streams") or ((data or {}).get("data") or {}).get("streams")
        if not isinstance(streams, dict):
            return None
        urls = [(q, u) for q, u in streams.items() if isinstance(u, str) and ".m3u8" in u]
        if not urls:
            return None
        return _multi(urls, _LM_HEADERS)


# ── moviebox (apii.freehandyflix.online) — fuzzy search + TMDB fallback ──

_MB_BASE = "https://apii.freehandyflix.online"


def _mb_similarity(a, b):
    a = re.sub(r"[^a-z0-9 ]", "", a.lower()).strip()
    b = re.sub(r"[^a-z0-9 ]", "", b.lower()).strip()
    if a == b:
        return 1.0
    if b.startswith(a) or a.startswith(b):
        shorter, longer = min(len(a), len(b)), max(len(a), len(b))
        return 0.9 * (shorter / longer) if longer else 0
    a_words = set(a.split(" "))
    b_words = b.split(" ")
    common = sum(1 for w in b_words if w in a_words)
    denom = max(len(a_words), len(b_words))
    return common / denom if denom else 0


async def _mb_search(session, query, title, year, subject_type):
    data = await _get_json(session, f"{_MB_BASE}/api/search/{quote(query)}")
    items = ((data or {}).get("data") or {}).get("items") or (data or {}).get("results") or (data if isinstance(data, list) else [])
    if not items:
        return None
    pools = [[r for r in items if r.get("subjectType") == subject_type], items]
    for pool in pools:
        if not pool:
            continue
        scored = sorted(
            ({"r": r, "titleScore": _mb_similarity(title, r.get("title", "")),
              "score": _mb_similarity(title, r.get("title", "")) + (0.5 if year and (r.get("releaseDate") or "").startswith(year) else 0)}
             for r in pool),
            key=lambda x: x["score"], reverse=True)
        best = scored[0]
        if best["titleScore"] >= 0.7:
            return best["r"].get("subjectId")
    return None


async def _mb_get_id(session, tmdb_id, is_movie):
    direct = await _get_json(session, f"{_MB_BASE}/api/info/{tmdb_id}")
    if direct:
        sid = (direct.get("data") or {}).get("subjectId") or direct.get("subjectId")
        if sid:
            return sid

    if not TMDB_API_KEY:
        return None
    meta = await _get_json(session, f"https://api.themoviedb.org/3/{'movie' if is_movie else 'tv'}/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=external_ids")
    if not meta:
        return None
    t = meta.get("title") or meta.get("name")
    if not t:
        return None
    year = (meta.get("release_date") or meta.get("first_air_date") or "")[:4]
    imdb_id = meta.get("imdb_id") or (meta.get("external_ids") or {}).get("imdb_id")
    subject_type = 1 if is_movie else 2

    queries = [t]
    short_title = re.split(r"[:\-–]", t)[0].strip()
    if short_title and short_title != t and len(short_title) > 2:
        queries.append(short_title)
    if imdb_id:
        queries.append(imdb_id)

    for q in queries:
        result = await _mb_search(session, q, t, year, subject_type)
        if result:
            return result
    return None


async def resolve_moviebox(tmdb_id, season=None, episode=None, title="", audio="sub"):
    is_movie = not season
    async with aiohttp.ClientSession() as session:
        mb_id = await _mb_get_id(session, tmdb_id, is_movie)
        if not mb_id:
            return None
        qs = f"?season={season}&episode={episode}" if season else ""
        data = await _get_json(session, f"{_MB_BASE}/api/sources/{mb_id}{qs}")
        sources = (data or {}).get("data", {}).get("processedSources")
        if not isinstance(sources, list) or not sources:
            return None
        urls = []
        for q in (1080, 720, 480, 360):
            src = next((s for s in sources if s.get("quality") == q), None)
            if src and (src.get("proxyUrl") or src.get("directUrl")):
                urls.append((f"{q}p", src.get("proxyUrl") or src.get("directUrl")))
        if not urls:
            return None
        return _multi(urls)


# ── nhdapi (player.nhdapi.com) — AES-256-GCM ─────────────────────────────

_NH_BASE = "https://player.nhdapi.com"
_NH_TV_PACKAGES = ["Hydra", "Titan", "Nexus", "Inferno", "BKC"]
_NH_KEY = SHA256.new(b"Z9#rL!v2K*5qP&7mXw").digest()


def _nh_decrypt(payload):
    try:
        iv = base64.b64decode(payload["iv"])
        tag = base64.b64decode(payload["tag"])
        data = base64.b64decode(payload["data"])
        cipher = AES.new(_NH_KEY, AES.MODE_GCM, nonce=iv)
        plain = cipher.decrypt_and_verify(data, tag)
        return json.loads(plain.decode("utf-8"))
    except Exception:
        return None


async def _nh_session_cookie(session, headers):
    try:
        async with session.post(f"{_NH_BASE}/api/session", headers=headers) as r:
            return r.headers.get("Set-Cookie") or ""
    except Exception:
        return ""


async def _nh_token(session, headers):
    data = await _get_json(session, f"{_NH_BASE}/api/token", headers)
    return (data or {}).get("token") or ""


async def _nh_fetch_encrypted(session, url, headers):
    return await _get_json(session, url, headers)


async def _nh_resolve_switch(session, sw, headers, proxy_headers):
    encrypted = await _nh_fetch_encrypted(session, f"{_NH_BASE}/api/source/{sw.get('file_code')}", headers)
    if not encrypted:
        return []
    api = _nh_decrypt(encrypted)
    if not api:
        return []
    return _nh_extract_sources(api, proxy_headers)


def _nh_extract_sources(api, proxy_headers):
    sources = []
    stream = api.get("stream") or {}
    if stream.get("hls_streaming"):
        sources.append({"url": stream["hls_streaming"], "headers": proxy_headers})
    for d in stream.get("download") or []:
        if d.get("url"):
            sources.append({"url": d["url"], "headers": proxy_headers})
    return sources


def _nh_extract_fallback(api, proxy_headers):
    return [{"url": api["url"], "headers": proxy_headers}] if api.get("url") else []


async def _nh_build_session(session, tmdb_id):
    ua = _rand_ua()
    headers = {"User-Agent": ua, "Accept": "application/json, text/javascript, */*; q=0.01",
               "Accept-Language": "en-US,en;q=0.9", "Referer": _NH_BASE + "/", "Origin": _NH_BASE,
               "Cookie": "", "x-api-token": "", "x-content-id": str(tmdb_id)}
    cookie = await _nh_session_cookie(session, headers)
    if not cookie:
        return None
    headers["Cookie"] = cookie.split(";")[0] if cookie else (
        "vid_session=" + base64.b64encode(json.dumps({"id": str(tmdb_id), "iat": int(time.time() * 1000)}).encode()).decode())
    await asyncio.sleep(0.1)
    token = await _nh_token(session, headers)
    if not token:
        return None
    headers["x-api-token"] = token
    return headers, ua, token


async def resolve_nhdapi(tmdb_id, season=None, episode=None, title="", audio="sub"):
    async with aiohttp.ClientSession() as session:
        sess = await _nh_build_session(session, tmdb_id)
        if not sess:
            return None
        headers, ua, token = sess
        proxy_headers = {"User-Agent": ua, "Referer": _NH_BASE + "/", "Origin": _NH_BASE,
                          "Cookie": headers["Cookie"], "x-api-token": token, "x-content-id": str(tmdb_id)}

        all_urls = []
        if season:
            async def fetch_pkg(pkg):
                url = f"{_NH_BASE}/api/v3/fallback?type=tv&id={tmdb_id}&s={season}&e={episode}&pkg={pkg}"
                encrypted = await _nh_fetch_encrypted(session, url, headers)
                if not encrypted:
                    return []
                api = _nh_decrypt(encrypted)
                if not api:
                    return []
                return _nh_extract_fallback(api, proxy_headers)
            results = await asyncio.gather(*[fetch_pkg(p) for p in _NH_TV_PACKAGES], return_exceptions=True)
            for res in results:
                if not isinstance(res, Exception):
                    all_urls.extend(res)
        else:
            encrypted = await _nh_fetch_encrypted(session, f"{_NH_BASE}/api/movie/?id={tmdb_id}", headers)
            if not encrypted:
                return None
            api = _nh_decrypt(encrypted)
            if not api:
                return None
            all_urls.extend(_nh_extract_sources(api, proxy_headers))
            switches = api.get("switches") or []
            if switches:
                results = await asyncio.gather(*[_nh_resolve_switch(session, sw, headers, proxy_headers) for sw in switches],
                                                return_exceptions=True)
                for res in results:
                    if not isinstance(res, Exception):
                        all_urls.extend(res)

        seen, deduped = set(), []
        for src in all_urls:
            if src["url"] in seen:
                continue
            seen.add(src["url"])
            deduped.append(src)
        if not deduped:
            return None
        return _multi([("Auto", d["url"]) for d in deduped], deduped[0]["headers"])


# ── vidzee (player.vidzee.wtf) — AES-GCM key derivation + AES-CBC ───────

_VZ_PLAYER = "https://player.vidzee.wtf"
_VZ_CORE = "https://core.vidzee.wtf"
_VZ_HLS_HEADERS = {"User-Agent": _UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                    "Referer": _VZ_PLAYER, "Origin": _VZ_PLAYER}


async def _vz_derive_key(api_key_text):
    if not api_key_text:
        return ""
    try:
        raw = base64.b64decode(api_key_text.replace(" ", "").replace("\n", "") + "=" * (-len(api_key_text) % 4))
        if len(raw) <= 28:
            return ""
        iv, tag_material, payload = raw[:12], raw[12:28], raw[28:]
        # NOTE: JS builds `s = [payload, tag_material].flat()` then GCM-decrypts
        # with a fixed SHA-256 key and 128-bit tag appended to ciphertext.
        key_mat = SHA256.new(b"c4a8f1d7e2b9a6c3d0f5e8a1b7c4d9e2").digest()
        cipher = AES.new(key_mat, AES.MODE_GCM, nonce=iv)
        plain = cipher.decrypt_and_verify(payload, tag_material)
        return plain.decode("utf-8")
    except Exception:
        return ""


def _vz_decrypt(encrypted_data, decryption_key):
    if not encrypted_data or not decryption_key:
        return ""
    try:
        decoded = base64.b64decode(encrypted_data).decode("utf-8")
        iv_str, cipher_str = decoded.split(":", 1)
        iv = base64.b64decode(iv_str)
        key = decryption_key.encode("utf-8").ljust(32, b"\0")[:32]
        ct = base64.b64decode(cipher_str)
        cipher = AES.new(key, AES.MODE_CBC, iv=iv)
        plain = unpad(cipher.decrypt(ct), 16)
        return plain.decode("utf-8")
    except Exception:
        return ""


async def resolve_vidzee(tmdb_id, season=None, episode=None, title="", audio="sub"):
    async with aiohttp.ClientSession() as session:
        api_key_text = await _get_text(session, f"{_VZ_CORE}/api-key", _VZ_HLS_HEADERS)
        if not api_key_text:
            return None
        dec_key = await _vz_derive_key(api_key_text)
        if not dec_key:
            return None

        season_n, episode_n = season or "1", episode or "1"
        for sr in range(14):
            url = f"{_VZ_PLAYER}/api/server?id={tmdb_id}&sr={sr}"
            if season:
                url += f"&ss={season_n}&ep={episode_n}"
            data = await _get_json(session, url, _VZ_HLS_HEADERS)
            if not data or data.get("error") or not isinstance(data.get("url"), list) or not data["url"]:
                continue
            for entry in data["url"]:
                if not entry.get("link"):
                    continue
                decrypted = _vz_decrypt(entry["link"], dec_key)
                if decrypted and decrypted.startswith("http"):
                    return _single(decrypted, _VZ_HLS_HEADERS)
    return None


# ── vidnest (vidnest.fun) — custom base64 alphabet + anime detection ────

_VN_BASE = "https://vidnest.fun"
_VN_API_BASE = "https://new.vidnest.fun"
_VN_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
               "Accept": "application/json, text/javascript, */*; q=0.01", "Accept-Language": "en-US,en;q=0.9",
               "Referer": _VN_BASE + "/", "Origin": _VN_BASE}
_VN_ALPHABET = "RB0fpH8ZEyVLkv7c2i6MAJ5u3IKFDxlS1NTsnGaqmXYdUrtzjwObCgQP94hoeW+/="
_VN_REVERSE = {c: i for i, c in enumerate(_VN_ALPHABET)}
_VN_SERVERS = ["hollymoviehd", "allmovies", "catflix", "purstream", "lamda", "vidlink", "klikxxi"]


def _vn_decode_b64(input_str):
    padded = input_str + "=" * (-len(input_str) % 4)
    out = bytearray()
    for i in range(0, len(padded), 4):
        chunk = padded[i:i + 4]
        c0 = _VN_REVERSE.get(chunk[0], 64)
        c1 = _VN_REVERSE.get(chunk[1], 64)
        c2 = 64 if chunk[2] == "=" else _VN_REVERSE.get(chunk[2], 64)
        c3 = 64 if chunk[3] == "=" else _VN_REVERSE.get(chunk[3], 64)
        out.append(((c0 << 2) | (c1 >> 4)) & 0xFF)
        if c2 != 64:
            out.append((((c1 & 0x0F) << 4) | (c2 >> 2)) & 0xFF)
        if c3 != 64:
            out.append((((c2 & 0x03) << 6) | c3) & 0xFF)
    return bytes(out).decode("utf-8", errors="ignore")


def _vn_decrypt(payload):
    return json.loads(_vn_decode_b64(payload))


async def resolve_vidnest(tmdb_id, season=None, episode=None, title="", audio="sub"):
    ep = int(episode) if episode else 1
    audio_param = "dub" if audio == "dub" else "sub"

    async with aiohttp.ClientSession() as session:
        if season:
            info = await _tmdb_anime_info(session, tmdb_id, season)
            if info["isAnime"]:
                anilist_id = await _tmdb_to_anilist(session, tmdb_id, season, info)
                if anilist_id:
                    try:
                        api_url = f"{_VN_API_BASE}/hianime/anime/{anilist_id}/{ep}/{audio_param}"
                        data = await _get_json(session, api_url, _VN_HEADERS)
                        if data:
                            d = _vn_decrypt(data["data"]) if data.get("encrypted") else data.get("data")
                            file = ((d or {}).get("sources") or [{}])[0].get("file")
                            if file:
                                proxy_headers = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
                                                  "accept": "*/*", "accept-language": "en-US,en;q=0.5",
                                                  "origin": "https://megaplay.buzz", "referer": "https://megaplay.buzz/"}
                                proxied = f"https://megacloud.animanga.fun/proxy?url={quote(file)}&headers={quote(json.dumps(proxy_headers))}"
                                return _single(proxied, _VN_HEADERS)
                    except Exception:
                        pass

        if audio == "dub":
            return None

        segment = f"tv/{tmdb_id}/{season}/{ep}" if season else f"movie/{tmdb_id}"

        async def try_server(server):
            data = await _get_json(session, f"{_VN_API_BASE}/{server}/{segment}", _VN_HEADERS)
            if not data or not data.get("data"):
                return None
            d = _vn_decrypt(data["data"]) if data.get("encrypted") else data.get("data")
            file = (((d or {}).get("sources") or [{}])[0].get("file")
                    or ((d or {}).get("streams") or [{}])[0].get("url")
                    or ((d or {}).get("url") or [{}])[0].get("link")
                    or (((d or {}).get("data") or {}).get("stream") or {}).get("playlist"))
            return file

        results = await asyncio.gather(*[try_server(s) for s in _VN_SERVERS], return_exceptions=True)
        file = next((r for r in results if r and not isinstance(r, Exception)), None)
        if not file:
            return None
        return _single(file, _VN_HEADERS)


# ── miruro (miruro.tv) — XOR + gzip obfuscated "pipe" API, anime only ────

_MR_OBF_KEY = bytes.fromhex("71951034f8fbcf53d89db52ceb3dc22c")
_MR_BASE = "https://www.miruro.tv"
_MR_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
               "Referer": _MR_BASE + "/", "Origin": _MR_BASE}
_MR_DEFAULT_PROVIDER = "kiwi"


def _mr_b64_encode(obj):
    raw = base64.b64encode(json.dumps(obj).encode()).decode()
    return raw.replace("+", "-").replace("/", "_").rstrip("=")


def _mr_decode_obfuscated(text):
    e = text.replace("-", "+").replace("_", "/")
    padded = e + "=" * (-len(e) % 4)
    raw = base64.b64decode(padded)
    xored = bytes(b ^ _MR_OBF_KEY[i % len(_MR_OBF_KEY)] for i, b in enumerate(raw))
    return json.loads(gzip.decompress(xored).decode("utf-8"))


async def _mr_pipe_get(session, path, query=None):
    payload = {"path": path, "method": "GET", "query": query or {}, "body": None, "version": "0.2.0"}
    try:
        async with session.get(f"{_MR_BASE}/api/secure/pipe", params={"e": _mr_b64_encode(payload)},
                                headers=_MR_HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                return None
            text = await r.text()
            if r.headers.get("x-obfuscated") == "2":
                return _mr_decode_obfuscated(text)
            return json.loads(text)
    except Exception:
        return None


async def _mr_is_anime(session, tmdb_id):
    if not TMDB_API_KEY:
        return False
    data = await _get_json(session, f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}")
    if not data:
        return False
    genres = data.get("genres") or []
    origin_country = data.get("origin_country") or []
    lang = data.get("original_language") or ""
    return any(g.get("id") == 16 for g in genres) and ("JP" in origin_country or lang == "ja")


async def resolve_miruro(tmdb_id, season=None, episode=None, title="", audio="sub"):
    if not (season and episode):
        return None
    async with aiohttp.ClientSession() as session:
        try:
            if not await _mr_is_anime(session, tmdb_id):
                return None
        except Exception:
            return None

        anilist_id = await _tmdb_to_anilist(session, tmdb_id, season, media_type="tv")
        if not anilist_id:
            return None

        category = "dub" if audio == "dub" else "sub"
        episode_num = int(episode)

        eps_data = await _mr_pipe_get(session, "episodes", {"anilistId": str(anilist_id)})
        provider_data = ((eps_data or {}).get("providers") or {}).get(_MR_DEFAULT_PROVIDER)
        if not provider_data:
            return None
        ep_list = ((provider_data.get("episodes") or {}).get(category)
                   or (provider_data.get("episodes") or {}).get("sub") or [])
        ep = next((e for e in ep_list if e.get("number") == episode_num), None)
        if not ep:
            return None

        sources_data = await _mr_pipe_get(session, "sources", {
            "episodeId": ep["id"], "provider": _MR_DEFAULT_PROVIDER, "category": category, "anilistId": str(anilist_id)})
        streams = (sources_data or {}).get("streams") or []
        hls = [s for s in streams if s.get("type") == "hls" and s.get("url") and s.get("isActive") is not False]
        if not hls:
            return None
        ref_headers = {"Referer": "https://kwik.cx/", "Origin": "https://kwik.cx", "User-Agent": _MR_HEADERS["User-Agent"]}
        return _multi([(s.get("quality") or "Auto", s["url"]) for s in hls], ref_headers)


# ── tryembed (tryembed.us.cc) — anime-only, token->m3u8 redirect chain ──

_TE_BASE = "https://tryembed.us.cc"
_TE_HEADERS = {"Referer": _TE_BASE + "/", "Origin": _TE_BASE,
               "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"}


async def _te_fetch_tokens(session, anilist_id, season, episode, audio):
    s = int(season) if season else 1
    e = int(episode) if episode else 1
    url = f"{_TE_BASE}/api/stream_data?id={anilist_id}&episode={e}&season={s}&audio={audio}"
    headers = {**_TE_HEADERS, "Referer": f"{_TE_BASE}/embed/anime/{anilist_id}/{e}/{audio}",
               "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    return await _get_json(session, url, headers, aiohttp.ClientTimeout(total=20))


def _te_extract_urls(data):
    urls = []
    for provider in (data or {}).get("providers") or []:
        for q in provider.get("qualities") or []:
            if q.get("token"):
                urls.append(f"{_TE_BASE}/s/{q['token']}.m3u8")
            if q.get("fallbackToken"):
                urls.append(f"{_TE_BASE}/s/{q['fallbackToken']}.m3u8")
    return urls


async def _te_resolve_redirect(session, url, headers):
    try:
        async with session.get(url, headers=headers, allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
            location = r.headers.get("Location")
            if location and r.status in (301, 302, 307, 308):
                return urljoin(url, location)
            return url
    except Exception:
        return None


async def resolve_tryembed(tmdb_id, season=None, episode=None, title="", audio="sub"):
    if not season:
        return None
    async with aiohttp.ClientSession() as session:
        info = await _tmdb_anime_info(session, tmdb_id, season)
        if not info["isAnime"]:
            return None
        anilist_id = await _tmdb_to_anilist(session, tmdb_id, season, info)
        if not anilist_id:
            return None
        data = await _te_fetch_tokens(session, anilist_id, season, episode, "dub" if audio == "dub" else "sub")
        if not data:
            return None
        raw_urls = _te_extract_urls(data)
        if not raw_urls:
            return None

        resolved = await asyncio.gather(*[_te_resolve_redirect(session, u, _TE_HEADERS) for u in raw_urls],
                                         return_exceptions=True)
        good = [u for u in resolved if u and not isinstance(u, Exception)]
        if not good:
            return None
        return _multi([("Auto", u) for u in good], _TE_HEADERS)


# ── meowtv gate (gate.flicky.host) — from Move-main's meowtv.js ─────────

_MT_GATES = ["https://gate.flicky.host/v15", "https://gate.flicky.host/v17", "https://gate.flicky.host/v4"]
_MT_REFERER = "https://meowtv.ru"
_MT_HEADERS = {"User-Agent": _UA, "Accept": "application/json", "Referer": _MT_REFERER, "Origin": _MT_REFERER}
_MT_OUT_HEADERS = {"User-Agent": _UA, "Referer": _MT_REFERER, "Origin": _MT_REFERER}


async def resolve_meowtv_gate(tmdb_id, season=None, episode=None, title="", audio="sub"):
    type_ = "tv" if season else "movie"
    path = f"/{type_}/{tmdb_id}/{season}/{episode}" if season else f"/{type_}/{tmdb_id}"
    urls = []
    async with aiohttp.ClientSession() as session:
        for i, base in enumerate(_MT_GATES):
            try:
                async with session.get(base + path, headers=_MT_HEADERS, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status != 200:
                        continue
                    d = await r.json(content_type=None)
            except Exception:
                continue
            if i < 2:
                stream = d.get("stream")
                url = stream if isinstance(stream, str) else (stream or {}).get("url")
                if isinstance(url, str) and url.startswith("http"):
                    urls.append(("Auto", url))
            else:
                for lang in ("English", "Hindi", "Telugu", "Tamil", "Malayalam"):
                    s2 = next((x for x in (d.get("streams") or []) if (x.get("language") or "").lower() == lang.lower()), None)
                    if s2 and (s2.get("url") or "").startswith("http"):
                        urls.append((lang, s2["url"]))
        if not urls:
            return None
        return _multi(urls, _MT_OUT_HEADERS)


# ── 02movie (02movie.com) — AES-256-GCM + PoW-gated AES-256-CBC ─────────

_OM_BASE = "https://02movie.com"
_OM_DL_BASE = "https://02moviedownloader.site"
_OM_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_OM_KEY_PARTS = ["o2by", "M0v1e", "S3cur", "Ek3y!"]
_OM_KEY = SHA256.new("_".join(_OM_KEY_PARTS).encode()).digest()


def _om_decrypt(encoded):
    try:
        raw = base64.b64decode(encoded)
        iv, tag, ct = raw[:12], raw[12:28], raw[28:]
        cipher = AES.new(_OM_KEY, AES.MODE_GCM, nonce=iv)
        plain = cipher.decrypt_and_verify(ct, tag)
        return json.loads(plain.decode("utf-8"))
    except Exception:
        return None


async def _om_fetch_token(session, headers, tmdb_id, s, e):
    url = (f"{_OM_BASE}/api/tv/download-token?id={tmdb_id}&season={s}&episode={e}" if (s and e)
           else f"{_OM_BASE}/api/movies/download-token?id={tmdb_id}")
    data = await _get_json(session, url, headers)
    return (data or {}).get("token")


async def _om_fetch_decrypted(session, headers, path, token):
    url = f"{_OM_BASE}{path}" + (f"&t={quote(token)}" if token else "")
    data = await _get_json(session, url, headers)
    if not data:
        return None
    if isinstance(data.get("_e"), str):
        return _om_decrypt(data["_e"])
    return data


def _om_format_size(val):
    if not val:
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return str(val) if isinstance(val, str) else None
    if n < 1024:
        return f"{n:.0f} B"
    if n < 1048576:
        return f"{n / 1024:.1f} KB"
    if n < 1073741824:
        return f"{n / 1048576:.2f} MB"
    return f"{n / 1073741824:.2f} GB"


def _om_extract_options(data, server):
    out = []
    if isinstance((data or {}).get("streams"), list) and data["streams"]:
        for stream in data["streams"]:
            for o in stream.get("links") or []:
                if o.get("url"):
                    out.append({"url": o["url"] if not o["url"].startswith("/") else f"{_OM_BASE}{o['url']}",
                                "quality": o.get("quality") or stream.get("quality") or "Unknown",
                                "server": server})
        return out
    if isinstance((data or {}).get("downloadOptions"), list) and data["downloadOptions"]:
        for o in data["downloadOptions"]:
            if o.get("url"):
                out.append({"url": o["url"] if not o["url"].startswith("/") else f"{_OM_BASE}{o['url']}",
                            "quality": o.get("quality") or "Unknown", "server": server})
        return out
    if isinstance((data or {}).get("links"), list) and data["links"]:
        for o in data["links"]:
            if o.get("downloadLink"):
                out.append({"url": o["downloadLink"], "quality": o.get("quality") or "Unknown", "server": server})
        return out
    return out


def _om_extract_downloader_options(data):
    out = []
    for s in (data or {}).get("externalStreams") or []:
        if s.get("url"):
            out.append({"url": s["url"], "quality": s.get("quality") or "HD", "server": 3})
    downloads = ((((data or {}).get("data") or {}).get("downloadData") or {}).get("data") or {}).get("downloads") or []
    for d in downloads:
        if d.get("url"):
            out.append({"url": d["url"], "quality": f"{d['resolution']}p" if d.get("resolution") else "Unknown", "server": 3})
    return out


async def _om_solve_pow(challenge, difficulty):
    prefix = "0" * difficulty
    nonce = 0
    loop = asyncio.get_event_loop()

    def _solve():
        n = 0
        while True:
            h = hashlib.sha256((challenge + str(n)).encode()).hexdigest()
            if h.startswith(prefix):
                return str(n)
            n += 1

    return await loop.run_in_executor(None, _solve)


async def _om_downloader_server3(session, tmdb_id, s, e):
    page_path = f"/api/download/tv/{tmdb_id}/{s}/{e}" if (s and e) else f"/api/download/movie/{tmdb_id}"
    page_url = f"{_OM_DL_BASE}{page_path}"
    html = await _get_text(session, page_url, {"User-Agent": _OM_UA, "Accept": "text/html"})
    if not html:
        return None
    m = re.search(r"const VERIFY_CONFIG\s*=\s*(\{[^;]+\})", html)
    if not m:
        return None
    try:
        config = json.loads(m.group(1))
    except Exception:
        return None
    scope, page_nonce = config.get("scope"), config.get("pageNonce")
    pow_challenge, pow_difficulty = config.get("powChallenge"), config.get("powDifficulty", 4)
    pow_nonce = await _om_solve_pow(pow_challenge, pow_difficulty)

    verify_headers = {"User-Agent": _OM_UA, "Content-Type": "application/json", "Accept": "application/json",
                       "Origin": _OM_DL_BASE, "Referer": page_url}
    verify_data = await _post_json(session, f"{_OM_DL_BASE}/api/verify-robot",
                                    json_body={"scope": scope, "pageNonce": page_nonce,
                                               "powChallenge": pow_challenge, "powNonce": pow_nonce},
                                    headers=verify_headers, timeout=aiohttp.ClientTimeout(total=30))
    token = (verify_data or {}).get("token")
    if not token:
        return None

    dl_headers = {"User-Agent": _OM_UA, "Accept": "application/json", "Origin": _OM_DL_BASE,
                  "Referer": page_url, "x-session-token": token}
    dl_json = await _get_json(session, f"{_OM_DL_BASE}/api/download{page_path}", dl_headers)
    if not dl_json:
        return None
    if dl_json.get("encrypted") and isinstance(dl_json.get("data"), str):
        try:
            iv_b64, cipher_b64 = dl_json["data"].split(":", 1)
            iv_bytes = base64.b64decode(iv_b64)
            cipher_bytes = base64.b64decode(cipher_b64)
            raw_key = SHA256.new(token.encode()).digest()
            cipher = AES.new(raw_key, AES.MODE_CBC, iv=iv_bytes)
            plain = unpad(cipher.decrypt(cipher_bytes), 16)
            return json.loads(plain.decode("utf-8"))
        except Exception:
            return None
    return dl_json


async def _om_verify_download(session, url):
    try:
        async with session.head(url, headers={"User-Agent": _OM_UA}, allow_redirects=True,
                                 timeout=aiohttp.ClientTimeout(total=6)) as r:
            return r.status < 400 or r.status == 405
    except Exception:
        return False


async def resolve_02movie(tmdb_id, season=None, episode=None, title="", audio="sub"):
    headers = {"User-Agent": _OM_UA, "Accept": "application/json", "Referer": _OM_BASE + "/"}
    async with aiohttp.ClientSession() as session:
        token = await _om_fetch_token(session, headers, tmdb_id, season, episode)
        if not token:
            return None

        primary_path = (f"/api/tv/download?id={tmdb_id}&season={season}&episode={episode}" if (season and episode)
                         else f"/api/movies/download?id={tmdb_id}")
        fallback_path = (f"/api/tv/fallback?tmdbId={tmdb_id}&season={season}&episode={episode}" if (season and episode)
                          else f"/api/movies/fallback?tmdbId={tmdb_id}")

        results = await asyncio.gather(
            _om_fetch_decrypted(session, headers, primary_path, token),
            _om_fetch_decrypted(session, headers, fallback_path, token),
            _om_downloader_server3(session, tmdb_id, season, episode),
            return_exceptions=True,
        )
        primary, fallback, downloader = results
        server1 = _om_extract_options(primary, 1) if not isinstance(primary, Exception) and primary else []
        server2 = _om_extract_options(fallback, 2) if not isinstance(fallback, Exception) and fallback else []
        server3 = _om_extract_downloader_options(downloader) if not isinstance(downloader, Exception) and downloader else []

        all_opts = server1 + server2 + server3
        if not all_opts:
            return None

        verified = []
        for o in all_opts:
            ok = True if o["server"] == 3 else await _om_verify_download(session, o["url"])
            if ok:
                verified.append(o)
        if not verified:
            return None
        return _multi([(str(o["quality"]), o["url"]) for o in verified], headers)


# ── vidnest already has its own headers; miruro/tryembed share helpers ──
# ── vidzee also exports VERIFY_HEADERS-equivalent above ─────────────────


# ── cinezo (player.cinezo.live / api.tulnex.com) — 4-layer decrypt ───────
# L1: XOR with PBKDF2-derived key (fallback path)
# L3: AES-256-CBC, key = PBKDF2(L3_KEY, per-payload salt, 100k, SHA-512)
# L4: HMAC-SHA512-authenticated base64 envelope (tries 3 candidate keys)
# Payloads are routed through whichever layer(s) the "v":4 envelope needs.

_CZ_VERIFY_HEADERS = {"Origin": "https://onionplay.io", "Referer": "https://onionplay.io/"}
_CZ_L1_KEY = "Sn00pD0g#L1_X0R_M4st3rK3y!2026sex"
_CZ_L1_SALT = "xK9!mR2@pL5#nQ8sex"
_CZ_L3_KEY = "Sn00pD0g#L3_AES_S3cur3K3y@2026$sex"
_CZ_L4_KEYS = [
    "Sn00pD0g#L4_HMAC_F1n4lW4ll#2026!sex",
    "Sn00pD0g#L4_HMAC_F1n4lW4ll#2026",
    "Sn00pD0g#L4HMAC_S3xur3W4ll#2026!",
]

_CZ_FALLBACK_SOURCES = [
    {"name": "nova", "movieApi": "https://api.tulnex.com/nova/movie/{id}", "tvApi": "https://api.tulnex.com/nova/tv/{id}/{s}/{e}"},
    {"name": "vaplayer", "movieApi": "https://api.tulnex.com/vaplayer/movie/{id}", "tvApi": "https://api.tulnex.com/vaplayer/tv/{id}/{s}/{e}"},
    {"name": "orion", "movieApi": "https://api.tulnex.com/orion/movie/{id}", "tvApi": "https://api.tulnex.com/orion/tv/{id}/{s}/{e}"},
    {"name": "nhdapi", "movieApi": "https://api.tulnex.com/nhdapi/movie/{id}", "tvApi": "https://api.tulnex.com/nhdapi/tv/{id}/{s}/{e}"},
    {"name": "watchflix", "movieApi": "https://api.tulnex.com/watchflix/movie/{id}", "tvApi": "https://api.tulnex.com/watchflix/tv/{id}/{s}/{e}"},
    {"name": "youplex", "movieApi": "https://api.tulnex.com/youplex/movie/{id}", "tvApi": "https://api.tulnex.com/youplex/tv/{id}/{s}/{e}"},
    {"name": "vidzee", "movieApi": "https://api.tulnex.com/vidzee/movie/{id}?server=0", "tvApi": "https://api.tulnex.com/vidzee/tv/{id}/{s}/{e}?server=0"},
    {"name": "moviebox", "movieApi": "https://api.tulnex.com/moviebox/movie/{id}", "tvApi": "https://api.tulnex.com/moviebox/tv/{id}/{s}/{e}"},
]


def _cz_pbkdf2(password, salt, iterations, key_len, hash_mod):
    return PBKDF2(password.encode(), salt.encode(), dkLen=key_len, count=iterations, hmac_hash_module=hash_mod)


def _cz_xor_decrypt(hex_str, key_bytes):
    src = bytes.fromhex(hex_str)
    out = bytes(b ^ key_bytes[i % 32] for i, b in enumerate(src))
    return out.decode("utf-8", errors="ignore")


def _cz_binary_decode(encoded):
    raw = base64.b64decode(encoded).decode("latin-1")
    return "".join(chr(int(x, 2)) for x in raw.split(" ") if x)


def _cz_decode_l3(data):
    parts = data.split(".")
    if len(parts) != 3:
        raise ValueError("L3 invalid")
    iv_b64, salt_b64, ct_b64 = parts
    salt = base64.b64decode(salt_b64).decode("latin-1")
    key_bytes = _cz_pbkdf2(_CZ_L3_KEY, salt, 100000, 32, SHA512)
    iv = base64.b64decode(iv_b64)
    ct = base64.b64decode(ct_b64)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv=iv)
    plain = unpad(cipher.decrypt(ct), 16)
    return plain.decode("utf-8")


def _cz_decode_l4(data, key):
    sep = data.index("|")
    received_hmac = data[:sep]
    payload_b64 = data[sep + 1:]
    payload_str = base64.b64decode(payload_b64).decode("utf-8")
    sig = hmac_mod.new(key.encode(), payload_str.encode(), hashlib.sha512).hexdigest()
    if received_hmac != sig:
        raise ValueError("L4 HMAC mismatch")
    return payload_str


def _cz_decrypt_payload(payload):
    xor_key = _cz_pbkdf2(_CZ_L1_KEY, _CZ_L1_SALT, 50000, 32, SHA256)

    if re.fullmatch(r"[0-9a-fA-F]+", payload) and len(payload) % 2 == 0:
        return json.loads(_cz_xor_decrypt(payload, xor_key))

    if "|" not in payload:
        raise ValueError("L4 no separator and not hex")

    l4out = None
    for key in _CZ_L4_KEYS:
        try:
            l4out = _cz_decode_l4(payload, key)
            break
        except Exception:
            continue
    if l4out is None:
        try:
            sep = payload.index("|")
            l4out = base64.b64decode(payload[sep + 1:]).decode("utf-8")
        except Exception:
            l4out = payload[payload.index("|") + 1:]

    if re.fullmatch(r"[0-9a-fA-F]+", l4out) and len(l4out) % 2 == 0:
        return json.loads(_cz_xor_decrypt(l4out, xor_key))

    l3out = _cz_decode_l3(l4out)
    l2out = _cz_binary_decode(l3out)
    return json.loads(_cz_xor_decrypt(l2out, xor_key))


def _cz_unwrap_proxy(url):
    if not url:
        return url, None
    if "pronhub.tulnex.com/m3u8-proxy" in url or "prxy.tulnex.com" in url:
        try:
            from urllib.parse import parse_qs, unquote
            qs = parse_qs(urlparse(url).query)
            inner = qs.get("url", [None])[0]
            headers_raw = qs.get("headers", [None])[0]
            if inner:
                headers = json.loads(unquote(headers_raw)) if headers_raw else None
                return unquote(inner), headers
        except Exception:
            pass
    return url, None


def _cz_extract_url(data):
    if not data or data.get("success") is False:
        return None

    def wrap(url, headers=None):
        if not url or not isinstance(url, str) or "http" not in url:
            return None
        unwrapped, extracted_headers = _cz_unwrap_proxy(url)
        merged = {**(extracted_headers or {}), **(headers or {})}
        return {"url": unwrapped, "headers": merged or None}

    if isinstance(data, str) and "http" in data:
        return wrap(data)
    headers = data.get("headers")
    for key in ("url", "stream", "playlist", "streamUrl", "stream_url", "streaming_url", "video_url", "m3u8"):
        v = data.get(key)
        if isinstance(v, str) and "http" in v:
            return wrap(v, headers)
    primary = (data.get("sources") or {})
    if isinstance(primary, dict) and primary.get("url"):
        return wrap(primary["url"], primary.get("headers") or headers)
    if isinstance(data.get("sources"), list) and data["sources"]:
        sorted_s = sorted([s for s in data["sources"] if s.get("url") and "http" in s["url"]],
                           key=lambda s: int(re.sub(r"\D", "", str(s.get("quality", ""))) or 0), reverse=True)
        if sorted_s:
            return wrap(sorted_s[0]["url"], sorted_s[0].get("headers") or headers)
    if isinstance(data.get("languages"), list):
        orig = next((l for l in data["languages"] if l.get("original") is True and l.get("sources")), None)
        if orig:
            sorted_s = sorted(orig["sources"], key=lambda s: int(re.sub(r"\D", "", str(s.get("quality", ""))) or 0), reverse=True)
            best = sorted_s[0]
            return wrap(best.get("url") or best.get("file"), best.get("headers") or orig.get("headers") or headers)
    if isinstance(data.get("links"), list):
        link = next((l for l in data["links"] if l.get("url") and "http" in l["url"]), None)
        if link:
            return wrap(link["url"], headers)
    nested = (((data.get("data") or {}).get("data") or {}).get("stream") or {}).get("playlist")
    if nested:
        return wrap(nested, headers)
    nested2 = ((data.get("data") or {}).get("stream") or {}).get("playlist")
    if nested2:
        return wrap(nested2, headers)
    d_url = (data.get("data") or {}).get("url")
    if isinstance(d_url, str) and "http" in d_url:
        return wrap(d_url, (data.get("data") or {}).get("headers") or headers)
    if isinstance((data.get("data") or {}).get("sources"), list):
        src = next((s for s in data["data"]["sources"] if s.get("url") and "http" in s["url"]), None)
        if src:
            return wrap(src["url"], src.get("headers") or headers)
    if isinstance(data.get("streams"), list):
        src = next((s for s in data["streams"] if (s.get("url") or s.get("link")) and "http" in (s.get("url") or s.get("link"))), None)
        if src:
            return wrap(src.get("url") or src.get("link"), src.get("headers") or headers)
    return None


async def _cz_fetch_and_decrypt(session, url):
    data = await _get_json(session, url, {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                            "Accept": "application/json, */*"})
    if not data:
        return None
    if data.get("v") == 4 and data.get("payload"):
        try:
            return _cz_decrypt_payload(data["payload"])
        except Exception:
            return None
    if data.get("success") is False:
        return None
    if isinstance(data, dict) and data:
        return data
    return None


async def _cz_get_sources(session):
    """Best-effort: try to scrape the live source list from cinezo's bundle;
    fall back to the hardcoded snapshot if that fails (mirrors getCinezoSources())."""
    try:
        html = await _get_text(session, "https://player.cinezo.live/",
                                {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if not html:
            return _CZ_FALLBACK_SOURCES
        m = re.search(r'src="(/assets/index-[^"]+\.js)"', html)
        if not m:
            return _CZ_FALLBACK_SOURCES
        js = await _get_text(session, f"https://player.cinezo.live{m.group(1)}",
                              {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if not js:
            return _CZ_FALLBACK_SOURCES
        sources = []
        for em in re.finditer(r'name:"([^"]+)"[^}]*?api:"(https://api\.tulnex\.com/[^"]+)"[^}]*?tvApi:"([^"]*)"', js):
            sources.append({"name": em.group(1), "movieApi": em.group(2),
                             "tvApi": em.group(3).replace("${season}", "{s}").replace("${episode}", "{e}")})
        return sources or _CZ_FALLBACK_SOURCES
    except Exception:
        return _CZ_FALLBACK_SOURCES


async def resolve_cinezo(tmdb_id, season=None, episode=None, title="", audio="sub"):
    ua_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with aiohttp.ClientSession() as session:
        sources = await _cz_get_sources(session)
        for src in sources:
            if season and episode and not src.get("tvApi"):
                continue
            url = (src["tvApi"].replace("${id}", str(tmdb_id)).replace("{id}", str(tmdb_id))
                   .replace("${s}", str(season)).replace("{s}", str(season))
                   .replace("${e}", str(episode)).replace("{e}", str(episode))
                   if (season and episode)
                   else src["movieApi"].replace("${id}", str(tmdb_id)).replace("{id}", str(tmdb_id)))
            if not url:
                continue
            try:
                data = await _cz_fetch_and_decrypt(session, url)
                if not data:
                    continue
                extracted = _cz_extract_url(data)
                if not extracted or not extracted.get("url"):
                    continue
                test_headers = {**ua_headers, **(extracted.get("headers") or {})}
                try:
                    async with session.get(extracted["url"], headers=test_headers,
                                            timeout=aiohttp.ClientTimeout(total=6), allow_redirects=True) as probe:
                        if not probe.ok if hasattr(probe, "ok") else probe.status >= 400:
                            continue
                        text = await probe.text()
                    if not text.strip().startswith("#EXTM3U"):
                        continue
                except Exception:
                    continue
                return _single(extracted["url"], extracted.get("headers") or _CZ_VERIFY_HEADERS)
            except Exception:
                continue
    return None


# ── flixhq (flixhq.one / weneverbeenfree.com) — ECDSA attestation + AES-GCM ──

_FH_BASE = "https://flixhq.one"
_FH_F16PX = "https://weneverbeenfree.com"
_FH_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _fh_slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s


async def _fh_tmdb_slug(session, tmdb_id, is_movie):
    if not TMDB_API_KEY:
        raise ValueError("no TMDB key")
    url = (f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}" if is_movie
           else f"https://api.themoviedb.org/3/tv/{tmdb_id}?api_key={TMDB_API_KEY}")
    data = await _get_json(session, url)
    if not data:
        raise ValueError("TMDB fetch failed")
    name = data.get("title") if is_movie else data.get("name")
    year = (data.get("release_date") if is_movie else data.get("first_air_date")) or ""
    return f"{_fh_slugify(name or '')}-{year[:4]}"


async def _fh_fetch_token(session, slug, is_movie, s, e):
    url = (f"{_FH_BASE}/watch-movie/{slug}-watch-online/" if is_movie
           else f"{_FH_BASE}/episode/{slug}-watch-online/s{int(s):02d}-e{int(e):02d}/")
    html = await _get_text(session, url, {"User-Agent": _FH_UA, "Accept": "text/html"})
    if not html:
        raise ValueError(f"flixhq page fetch failed for {url}")
    m = re.search(r'data-token="([^"]+)"', html)
    if not m:
        raise ValueError("no data-token on " + url)
    return m.group(1), url


async def _fh_fetch_embed_url(session, token, page_url, is_movie):
    form = aiohttp.FormData()
    form.add_field("players" if is_movie else "players_show", token)
    try:
        async with session.post(f"{_FH_BASE}/ajax/ajax.php", data=form,
                                 headers={"User-Agent": _FH_UA, "Referer": page_url, "X-Requested-With": "XMLHttpRequest"},
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                raise ValueError(f"ajax.php {r.status}")
            lst = await r.json(content_type=None)
    except Exception as e:
        raise ValueError(f"ajax.php failed: {e}")
    arr = lst if isinstance(lst, list) else ([lst] if lst else [])
    if arr and arr[0].get("error"):
        raise ValueError(f"ajax.php error: {arr[0]['error']}")
    entry = (next((x for x in arr if x.get("link") and "weneverbeenfree.com" in x["link"]), None)
             or next((x for x in arr if x.get("link") and x.get("name") == "FlixHQ"), None)
             or next((x for x in arr if x.get("link")), None))
    if not entry:
        raise ValueError(f"no usable entry: {arr}")
    return entry["link"]


def _fh_extract_video_id(embed_url):
    m = re.search(r"weneverbeenfree\.com/e/([a-zA-Z0-9_-]+)", embed_url) or re.search(r"f16px\.com/e/([a-zA-Z0-9_-]+)", embed_url)
    if not m:
        raise ValueError("no video id in " + embed_url)
    return m.group(1)


class _FhCookieJar:
    def __init__(self):
        self.store = {}

    def get(self):
        return "; ".join(f"{k}={v}" for k, v in self.store.items())

    def update(self, set_cookie):
        if not set_cookie:
            return
        for entry in set_cookie.split(","):
            part = entry.split(";")[0].strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k.strip():
                self.store[k.strip()] = v.strip()


async def _fh_challenge(session, jar):
    async with session.post(f"{_FH_F16PX}/api/videos/access/challenge",
                             headers={"User-Agent": _FH_UA, "Referer": _FH_F16PX, "Cookie": jar.get()},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
        jar.update(r.headers.get("Set-Cookie"))
        return await r.json(content_type=None)


async def _fh_attest(session, challenge, jar):
    key = ECC.generate(curve="P-256")
    device_id = base64.urlsafe_b64encode(random_bytes(16)).decode().rstrip("=")
    nonce = challenge.get("nonce", "")
    h = SHA256.new(nonce.encode())
    signer = DSS.new(key, "fips-186-3", encoding="der")
    signature_der = signer.sign(h)
    sig_b64 = base64.urlsafe_b64encode(signature_der).decode().rstrip("=")

    pub = key.public_key()
    x_bytes = pub.pointQ.x.to_bytes(32, "big")
    y_bytes = pub.pointQ.y.to_bytes(32, "big")
    pub_jwk = {
        "crv": "P-256", "ext": True, "key_ops": ["verify"], "kty": "EC",
        "x": base64.urlsafe_b64encode(x_bytes).decode().rstrip("="),
        "y": base64.urlsafe_b64encode(y_bytes).decode().rstrip("="),
    }

    viewer_id = challenge.get("viewer_hint")
    body = {
        "viewer_id": viewer_id, "device_id": device_id, "challenge_id": challenge.get("challenge_id"),
        "nonce": nonce, "signature": sig_b64, "public_key": pub_jwk,
        "client": {
            "user_agent": _FH_UA, "architecture": "x86", "bitness": "64", "platform": "Windows",
            "platform_version": "10.0.0", "model": "", "languages": ["en-US", "en"],
            "timezone": "America/New_York", "hardware_concurrency": 8, "device_memory": 8,
            "touch_points": 0, "pixel_ratio": 1, "screen_width": 1920, "screen_height": 1080, "color_depth": 24,
        },
        "storage": {"cookie": viewer_id, "local_storage": viewer_id,
                    "indexed_db": f"{viewer_id}:{device_id}", "cache_storage": f"{viewer_id}:{device_id}"},
        "attributes": {"entropy": "high"},
    }
    async with session.post(f"{_FH_F16PX}/api/videos/access/attest", json=body,
                             headers={"Content-Type": "application/json", "User-Agent": _FH_UA,
                                      "Referer": _FH_F16PX, "Cookie": jar.get()},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
        jar.update(r.headers.get("Set-Cookie"))
        data = await r.json(content_type=None)
    if not data.get("token"):
        raise ValueError("attest failed: " + json.dumps(data))
    return {"token": data["token"], "viewerId": viewer_id, "deviceId": data.get("device_id", device_id),
            "confidence": data.get("confidence", 0.6)}


def random_bytes(n):
    import os
    return os.urandom(n)


async def _fh_playback(session, video_id, attest, jar):
    body = {"fingerprint": {"token": attest["token"], "viewer_id": attest["viewerId"],
                              "device_id": attest["deviceId"], "confidence": attest["confidence"]}}
    async with session.post(f"{_FH_F16PX}/api/videos/{video_id}/embed/playback", json=body,
                             headers={"Content-Type": "application/json", "User-Agent": _FH_UA,
                                      "Referer": f"{_FH_F16PX}/e/{video_id}", "Origin": _FH_F16PX, "Cookie": jar.get()},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
        jar.update(r.headers.get("Set-Cookie"))
        data = await r.json(content_type=None)
    if not data.get("playback"):
        raise ValueError("no playback: " + json.dumps(data))
    return data["playback"]


def _fh_b64url_decode(s):
    padded = s.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    return base64.b64decode(padded)


def _fh_extract_stream_url(text):
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            obj = json.loads(text)
            sources = obj.get("sources") if isinstance(obj, dict) else (obj if isinstance(obj, list) else None)
            if sources:
                best = max(sources, key=lambda s: s.get("height", 0) or 0)
                if best.get("url"):
                    return best["url"]
            if isinstance(obj, dict):
                if obj.get("url"):
                    return obj["url"]
                if obj.get("stream"):
                    return obj["stream"]
        except Exception:
            pass
    if text.startswith("http") or "m3u8" in text or ".mp4" in text:
        return text
    return None


def _fh_decrypt_playback(playback):
    def try_aes_gcm(iv_raw, ct_raw, key_bytes):
        try:
            iv = _fh_b64url_decode(iv_raw)
            ciphertext = _fh_b64url_decode(ct_raw)
            cipher = AES.new(key_bytes, AES.MODE_GCM, nonce=iv)
            # JS's crypto.subtle.decrypt AES-GCM expects the tag appended to
            # the ciphertext (last 16 bytes); pycryptodome wants it split.
            ct, tag = ciphertext[:-16], ciphertext[-16:]
            plain = cipher.decrypt_and_verify(ct, tag)
            return _fh_extract_stream_url(plain.decode("utf-8", errors="ignore"))
        except Exception:
            return None

    parts = [_fh_b64url_decode(p) for p in (playback.get("key_parts") or [])]
    iv = playback.get("iv")
    payload = playback.get("payload")

    candidates = []
    parts32 = [p for p in parts if len(p) == 32]
    parts24 = [p for p in parts if len(p) == 24]
    parts16 = [p for p in parts if len(p) == 16]

    def xor_all(byte_arrays, length):
        out = bytearray(length)
        for b in byte_arrays:
            for i in range(length):
                out[i] ^= b[i] if i < len(b) else 0
        return bytes(out)

    if parts32:
        candidates.append(xor_all(parts32, 32))
    if parts24:
        candidates.append(xor_all(parts24, 32)[:32])
    if parts16:
        candidates.append(xor_all(parts16, 16))
    candidates.append(xor_all(parts, 32))
    candidates.append(xor_all(parts, 16))

    for i, p in enumerate(parts):
        if len(p) == 32:
            candidates.append(p)
        if len(p) == 16:
            candidates.append(p)
        for j in range(i + 1, len(parts)):
            c = p + parts[j]
            if len(c) in (32, 16):
                candidates.append(c)

    for key_bytes in candidates:
        if len(key_bytes) not in (32, 16):
            continue
        result = try_aes_gcm(iv, payload, key_bytes)
        if result:
            return result
    raise ValueError(f"decryption failed — tried {len(candidates)} candidates")


async def resolve_flixhq(tmdb_id, season=None, episode=None, title="", audio="sub"):
    is_movie = not (season and episode)
    async with aiohttp.ClientSession() as session:
        try:
            slug = await _fh_tmdb_slug(session, tmdb_id, is_movie)
            token, page_url = await _fh_fetch_token(session, slug, is_movie, season, episode)
            embed_url = await _fh_fetch_embed_url(session, token, page_url, is_movie)
            video_id = _fh_extract_video_id(embed_url)
            jar = _FhCookieJar()
            challenge = await _fh_challenge(session, jar)
            attest = await _fh_attest(session, challenge, jar)
            playback = await _fh_playback(session, video_id, attest, jar)
            stream_url = _fh_decrypt_playback(playback)
            if not stream_url:
                return None
            headers = {"Referer": f"{_FH_F16PX}/e/{video_id}", "Origin": _FH_F16PX}
            return _single(stream_url, headers)
        except Exception as e:
            logger.debug(f"meowly_extra_resolvers: flixhq failed for {tmdb_id}: {e}")
            return None


# ── dispatcher: all 21 new sources, tried in rough reliability order ────

EXTRA_RESOLVERS = (
    ("VidSrc-Vidify", resolve_vidify),
    ("VixSrc", resolve_vixsrc),
    ("NHDApi", resolve_nhdapi),
    ("VidZee", resolve_vidzee),
    ("VidNest", resolve_vidnest),
    ("Cinezo", resolve_cinezo),
    ("MeowTV-Gate", resolve_meowtv_gate),
    ("Toustream", resolve_toustream),
    ("Flixtrz", resolve_flixtrz),
    ("MovieBox", resolve_moviebox),
    ("VaPlayer", resolve_vaplayer),
    ("Vapor", resolve_vapor),
    ("Icefy", resolve_icefy),
    ("Movsrc", resolve_movsrc),
    ("Cinesu", resolve_cinesu),
    ("FlaxMovies", resolve_flaxmovies),
    ("LookMovie", resolve_lookmovie),
    ("FSonic", resolve_fsonic),
    ("FShareTV", resolve_fsharetv),
    ("Miruro", resolve_miruro),
    ("TryEmbed", resolve_tryembed),
    ("FlixHQ", resolve_flixhq),
    ("02Movie", resolve_02movie),
)


async def resolve_extra(tmdb_id: str, season: int = None, episode: int = None,
                         title: str = "", audio: str = "sub") -> dict | None:
    """Try every EXTRA_RESOLVERS entry in order, return the first success.
    Mirrors meowly_resolvers.resolve()'s dispatcher shape so the two can be
    chained together."""
    for name, fn in EXTRA_RESOLVERS:
        try:
            result = await fn(str(tmdb_id), season, episode, title, audio)
        except Exception as e:
            logger.debug(f"meowly_extra_resolvers: {name} failed for {tmdb_id}: {e}")
            continue
        if result and result.get("videoUrl"):
            logger.info(f"meowly_extra_resolvers: resolved {tmdb_id} via {name}")
            return result
    return None
