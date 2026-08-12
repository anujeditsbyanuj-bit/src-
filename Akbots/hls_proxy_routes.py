# Akbots - Don't Remove Credit - @AkBots_Official
#
# HLS proxy — aiohttp version of Akbots/meow_proxy.py's Flask logic, but
# mounted directly onto the SAME aiohttp app + port the file-to-link
# feature already publicly exposes (config.STREAM_PORT / config.STREAM_URL
# — auto-detected on Render/Railway/Fly/Koyeb/Heroku/Replit, see
# config.py's _detect_platform_domain()).
#
# Why this exists: meow_proxy.py binds 127.0.0.1-only in its own thread,
# so it only ever worked for same-machine playback. This version rides
# the bot's existing public web server, so /hotstar's (and any other
# plugin's) "Direct Link" button works out of the box on a hosted bot —
# no HLS_WORKER_URL / MEOW_LOCAL_PROXY / separate Cloudflare Worker
# deploy required. Those two still work as manual overrides (see
# Akbots/hls_proxy.py) for people who'd rather run a dedicated Worker.
#
# Routes (mounted at /hls/... by Akbots/filetolink/web_server.py):
#   GET /hls/api/hls   — playlist-aware: rewrites nested .m3u8 URIs and
#                         segment lines to point back through this proxy,
#                         so the whole stream (playlist+segments+subs)
#                         plays with the Referer/Cookie/UA the upstream
#                         host requires, without the player ever needing
#                         to send those headers itself.
#   GET /hls/api/proxy — plain pass-through (single file/segment).

import re
import logging
from urllib.parse import urlparse, urljoin, urlencode

import aiohttp
from aiohttp import web

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_EXCLUDED_HEADERS = {"content-encoding", "content-length", "transfer-encoding",
                     "connection", "access-control-allow-origin"}


def _resolve_url(base_url: str, maybe_relative: str) -> str:
    """Mirrors meow_proxy.py's _resolve_url() — handles the malformed
    `https:///path` URLs some providers (and Hotstar's CDN, sometimes)
    return for sub-playlists/segments."""
    ref = maybe_relative.strip()
    if ref.startswith("https:///"):
        try:
            parsed = urlparse(base_url)
            return ref.replace("https:///", f"{parsed.scheme}://{parsed.netloc}/")
        except Exception:
            pass
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref
    try:
        return urljoin(base_url, ref)
    except Exception:
        return ref


def _kind_for(u: str) -> str:
    low = u.lower()
    return "playlist" if (".m3u8" in low or "playlist" in low) else "segment"


def _make_proxy_url(request: web.Request, absolute_url: str, referer: str,
                     cookie: str, ua: str, kind: str) -> str:
    params = {"url": absolute_url, "referer": referer or "", "cookie": cookie or "",
              "ua": ua or "", "kind": kind}
    return f"{_own_base(request)}/hls/api/hls?{urlencode(params)}"


def _own_base(request: web.Request) -> str:
    """Base URL of this server as seen by the client — trusts the
    reverse-proxy's Forwarded/X-Forwarded-Proto headers (Render/Railway/
    Fly/etc. all sit behind one), same trust model config.py already uses
    for STREAM_TRUST_PROXY-style detection elsewhere in this repo."""
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{scheme}://{host}"


def _rewrite_playlist(content: str, base_url: str, request: web.Request,
                       referer: str, cookie: str, ua: str,
                       limit_variants: int = 6) -> str:
    """Mirrors meow_proxy.py's _rewrite_playlist()."""
    lines = content.splitlines()
    result = []
    variant_count = 0
    skip_next = False
    is_master = "#EXT-X-STREAM-INF" in content

    def _replace_uri(match):
        key, val = match.group(1), match.group(2)
        resolved = _resolve_url(base_url, val)
        return f'{key}="{_make_proxy_url(request, resolved, referer, cookie, ua, _kind_for(resolved))}"'

    for line in lines:
        line = line.strip()
        if not line:
            result.append("")
            continue

        if line.startswith("#"):
            if is_master and "#EXT-X-STREAM-INF" in line:
                if variant_count >= limit_variants:
                    skip_next = True
                    continue
                variant_count += 1
            line = re.sub(r'([A-Z-]*URI)="([^"]+)"', _replace_uri, line, flags=re.IGNORECASE)
            result.append(line)
        else:
            if skip_next:
                skip_next = False
                continue
            resolved = _resolve_url(base_url, line)
            result.append(_make_proxy_url(request, resolved, referer, cookie, ua, _kind_for(resolved)))

    return "\n".join(result)


async def _fetch(url: str, referer: str, cookie: str, ua: str, range_header: str = None):
    headers = {"User-Agent": ua or _DEFAULT_UA}
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    if range_header:
        headers["Range"] = range_header
    session = aiohttp.ClientSession()
    try:
        resp = await session.get(url, headers=headers, ssl=False,
                                  timeout=aiohttp.ClientTimeout(total=None, sock_connect=20))
        return session, resp
    except Exception:
        await session.close()
        raise


@routes.get("/hls/api/hls")
async def hls_proxy_playlist(request: web.Request):
    url = request.query.get("url")
    if not url:
        return web.Response(text="Missing url", status=400)

    referer = request.query.get("referer", "")
    cookie = request.query.get("cookie", "")
    ua = request.query.get("ua") or _DEFAULT_UA
    kind = request.query.get("kind", "segment")

    range_header = request.headers.get("Range") if kind != "playlist" else None

    try:
        session, upstream = await _fetch(url, referer, cookie, ua, range_header)
    except Exception as e:
        logger.warning(f"hls_proxy_routes: upstream fetch failed for {url}: {e}")
        return web.Response(text=f"Upstream error: {e}", status=502)

    try:
        content_type = upstream.headers.get("Content-Type", "")
        is_playlist = (kind == "playlist" or ".m3u8" in url.lower()
                        or "mpegurl" in content_type.lower())

        if is_playlist:
            text = await upstream.text(errors="replace")
            rewritten = _rewrite_playlist(text, url, request, referer, cookie, ua)
            return web.Response(text=rewritten, status=upstream.status,
                                 content_type="application/vnd.apple.mpegurl")

        resp_headers = {k: v for k, v in upstream.headers.items()
                         if k.lower() not in _EXCLUDED_HEADERS}
        response = web.StreamResponse(status=upstream.status, headers=resp_headers)
        await response.prepare(request)
        async for chunk in upstream.content.iter_chunked(256 * 1024):
            await response.write(chunk)
        await response.write_eof()
        return response
    finally:
        upstream.close()
        await session.close()


@routes.get("/hls/api/proxy")
async def hls_proxy_passthrough(request: web.Request):
    # Plain pass-through — same handler; defaults to segment/binary mode
    # since no "kind" query param is set (see hls_proxy_playlist above).
    return await hls_proxy_playlist(request)
