"""
HLS Proxy helper (Akbots/hls_proxy.py)
=======================================

Thin Python wrapper around a proxied-playback endpoint for .m3u8/.mp4
links. By default this rides Akbots/hls_proxy_routes.py, which is mounted
directly on the bot's own public web server (config.STREAM_URL /
config.STREAM_PORT — auto-detected on Render/Railway/Fly/Koyeb/Heroku/
Replit, see config.py's _detect_platform_domain()). That means no extra
setup: whatever makes /link (file-to-link) work on your host already
makes /hotstar's "Direct Link" button work too.

HLS_WORKER_URL remains a manual override for anyone who'd rather run the
separate `workers/hls-proxy` Cloudflare Worker instead (e.g. to offload
proxying traffic away from the bot's own host) — set it and it takes
priority over the auto-detected in-process route.
"""

from urllib.parse import urlencode

try:
    from config import HLS_WORKER_URL, MEOW_LOCAL_PROXY, STREAM_URL
except ImportError:
    HLS_WORKER_URL = ""
    MEOW_LOCAL_PROXY = False
    STREAM_URL = ""


def _auto_base() -> str:
    """The bot's own hls_proxy_routes.py endpoint, built from the same
    auto-detected public URL file-to-link links already use. Empty if
    config.py couldn't detect a public host AND didn't fall back to
    localhost either (shouldn't normally happen — STREAM_URL always has
    at least a localhost fallback)."""
    if not STREAM_URL:
        return ""
    return STREAM_URL.rstrip("/") + "/hls"


def is_enabled() -> bool:
    """Whether a proxy endpoint (manual worker, or the bot's own
    auto-mounted route) is available at all."""
    return bool(HLS_WORKER_URL) or bool(_auto_base())


def _local_fallback(url: str, referer: str = None, cookie: str = None,
                     ua: str = None, kind: str = "playlist") -> str:
    if not MEOW_LOCAL_PROXY:
        return url
    try:
        from Akbots import meow_proxy
        return meow_proxy.build_local_url(url, referer=referer, cookie=cookie, ua=ua, kind=kind)
    except Exception:
        return url


def _base() -> str:
    # Manual override always wins if set; otherwise use the bot's own
    # auto-mounted route.
    return HLS_WORKER_URL.rstrip("/") if HLS_WORKER_URL else _auto_base()


def build_hls_url(
    url: str,
    referer: str = None,
    cookie: str = None,
    ua: str = None,
    kind: str = None,
    decrypt: str = None,
    proxy_segments: bool = True,
) -> str:
    """
    Build a proxied playback URL for an .m3u8 playlist or media segment via
    the worker's /api/hls endpoint. Rewrites nested playlist URIs so the
    whole stream (playlist + segments + subtitles) plays back through the
    worker.

    Falls back to the local proxy (Akbots/meow_proxy.py) if
    MEOW_LOCAL_PROXY=true, else returns `url` unchanged.
    """
    if not is_enabled():
        return _local_fallback(url, referer=referer, cookie=cookie, ua=ua, kind=kind or "playlist")

    params = {"url": url}
    if referer:
        params["referer"] = referer
    if cookie:
        params["cookie"] = cookie
    if ua:
        params["ua"] = ua
    if kind:
        params["kind"] = kind
    if decrypt:
        params["decrypt"] = decrypt
    if not proxy_segments:
        params["proxy_segments"] = "false"

    return f"{_base()}/api/hls?{urlencode(params)}"


def build_proxy_url(
    url: str,
    referer: str = None,
    cookie: str = None,
    ua: str = None,
) -> str:
    """
    Build a plain pass-through proxy URL via the worker's /api/proxy
    endpoint (forwards auth headers, exposes X-Proxied-Set-Cookie).

    Falls back to the local proxy (Akbots/meow_proxy.py) if
    MEOW_LOCAL_PROXY=true, else returns `url` unchanged.
    """
    if not is_enabled():
        return _local_fallback(url, referer=referer, cookie=cookie, ua=ua, kind="segment")

    params = {"url": url}
    if referer:
        params["referer"] = referer
    if cookie:
        params["cookie"] = cookie
    if ua:
        params["ua"] = ua

    return f"{_base()}/api/proxy?{urlencode(params)}"
