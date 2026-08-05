"""
HLS Proxy helper (Akbots/hls_proxy.py)
=======================================

Thin Python wrapper around the `workers/hls-proxy` Cloudflare Worker, which
was ported from the meowtv project's `workers/hls-worker.js`.

Deploy the worker (see workers/hls-proxy/README.md), set HLS_WORKER_URL in
config.py / .env, then any plugin can call `build_hls_url()` /
`build_proxy_url()` below to get a CORS-safe, header-repaired playback link
instead of handing the raw upstream URL to the user/player.

This mirrors the existing FILMYFLY_WORKER_URL / Akbots/filmyfly.py pattern.

If HLS_WORKER_URL isn't set but MEOW_LOCAL_PROXY=true is, falls back to the
local in-process proxy (Akbots/meow_proxy.py, ported from the meowtv CLI's
proxy.py). That fallback is localhost-only — see meow_proxy.py's docstring
— so it's meant for self-hosted/same-machine setups, not remote users.
"""

from urllib.parse import urlencode

try:
    from config import HLS_WORKER_URL, MEOW_LOCAL_PROXY
except ImportError:
    HLS_WORKER_URL = ""
    MEOW_LOCAL_PROXY = False


def is_enabled() -> bool:
    """Whether HLS_WORKER_URL has been configured."""
    return bool(HLS_WORKER_URL)


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
    return HLS_WORKER_URL.rstrip("/")


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
