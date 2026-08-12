# Akbots - Don't Remove Credit - @AkBots_Official
#
# Local, in-process fallback for Akbots/hls_proxy.py — ported from the
# meowtv CLI project's meowtv/proxy.py: a small Flask app that rewrites
# .m3u8 playlists to point back at itself and streams segments/playlists
# with the Referer/Cookie the upstream host requires.
#
# IMPORTANT — local-only: this binds to 127.0.0.1 and is only reachable
# from the machine the bot itself runs on. It's a straight port of the
# CLI's design (where the player and the proxy share one machine), so on a
# hosted bot (Render/Railway/etc.) links built from this are NOT reachable
# by a user's phone/VLC. Use it only when the bot and the player are on
# the same host/network (e.g. self-hosted + local playback/testing), or as
# an internal helper for Akbots/meow_downloader.py. For playback links
# handed to remote Telegram users, deploy the public
# workers/hls-proxy Cloudflare Worker and set HLS_WORKER_URL instead (see
# Akbots/hls_proxy.py) — that remains the primary/recommended proxy path.
#
# Enabled only when MEOW_LOCAL_PROXY=true is set (see config.py); requires
# flask + flask-cors (added to requirements.txt).

import re
import socket
import logging
import threading
from urllib.parse import urlparse, urljoin, urlencode

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from flask import Flask, request, Response, stream_with_context
    from flask_cors import CORS
    _FLASK_AVAILABLE = True
except ImportError:
    _FLASK_AVAILABLE = False

log = logging.getLogger(__name__)

_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=100)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_state = {"port": 0, "started": False}
_lock = threading.Lock()

if _FLASK_AVAILABLE:
    app = Flask(__name__)
    CORS(app)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    def _resolve_url(base_url: str, maybe_relative: str) -> str:
        """Mirrors the CLI's resolve_url() — handles the malformed
        `https:///path` URLs some providers return."""
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

    def _make_proxy_url(absolute_url: str, referer: str, cookie: str, ua: str, kind: str) -> str:
        params = {"url": absolute_url, "referer": referer or "", "cookie": cookie or "",
                  "ua": ua or "", "kind": kind}
        return f"http://127.0.0.1:{_state['port']}/api/hls?{urlencode(params)}"

    def _rewrite_playlist(content: str, base_url: str, referer: str, cookie: str,
                           ua: str, limit_variants: int = 6) -> str:
        """Mirrors the CLI's rewrite_playlist() — rewrites both #EXT-X-...
        URI="..." tag references and plain segment/sub-playlist lines."""
        lines = content.splitlines()
        result = []
        variant_count = 0
        skip_next = False
        is_master = "#EXT-X-STREAM-INF" in content

        def _kind_for(u: str) -> str:
            low = u.lower()
            return "playlist" if (".m3u8" in low or "playlist" in low) else "segment"

        def _replace_uri(match):
            key, val = match.group(1), match.group(2)
            resolved = _resolve_url(base_url, val)
            return f'{key}="{_make_proxy_url(resolved, referer, cookie, ua, _kind_for(resolved))}"'

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
                result.append(_make_proxy_url(resolved, referer, cookie, ua, _kind_for(resolved)))

        return "\n".join(result)

    @app.route("/api/hls")
    def _proxy_hls():
        url = request.args.get("url")
        referer = request.args.get("referer", "")
        cookie = request.args.get("cookie", "")
        ua = request.args.get("ua") or _DEFAULT_UA
        kind = request.args.get("kind", "segment")

        if not url:
            return "Missing url", 400

        headers = {"User-Agent": ua, "Referer": referer, "Cookie": cookie}
        # Range handling only for segments, not playlists (mirrors the CLI).
        if "Range" in request.headers and kind != "playlist":
            headers["Range"] = request.headers["Range"]

        try:
            upstream = _session.get(url, headers=headers, stream=True, timeout=20, verify=False)
        except Exception as e:
            return f"Upstream error: {e}", 502

        excluded = {"content-encoding", "content-length", "transfer-encoding",
                    "connection", "access-control-allow-origin"}
        resp_headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in excluded]

        is_playlist = (kind == "playlist" or ".m3u8" in url.lower()
                        or "mpegurl" in upstream.headers.get("Content-Type", "").lower())

        if is_playlist:
            text = upstream.content.decode("utf-8", errors="replace")
            rewritten = _rewrite_playlist(text, url, referer, cookie, ua)
            return Response(rewritten, status=upstream.status_code, headers=resp_headers,
                             content_type="application/vnd.apple.mpegurl")

        return Response(stream_with_context(upstream.iter_content(chunk_size=128 * 1024)),
                         status=upstream.status_code, headers=resp_headers,
                         content_type=upstream.headers.get("Content-Type"))


# ── Public API ─────────────────────────────────────────────────────────

def is_available() -> bool:
    """Whether flask + flask-cors are installed (the local proxy can run
    at all)."""
    return _FLASK_AVAILABLE


def ensure_started() -> int:
    """Starts the local proxy on a free localhost port, idempotently.
    Returns the bound port, or 0 if Flask isn't installed."""
    if not _FLASK_AVAILABLE:
        return 0

    with _lock:
        if _state["started"]:
            return _state["port"]

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        _state["port"] = port

        def _run():
            try:
                import flask.cli
                flask.cli.show_server_banner = lambda *a, **k: None
            except Exception:
                pass
            app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

        threading.Thread(target=_run, daemon=True, name="meow-local-hls-proxy").start()
        _state["started"] = True
        log.info("Local HLS proxy fallback (meow_proxy) started on 127.0.0.1:%s", port)
        return port


def build_local_url(url: str, referer: str = None, cookie: str = None,
                     ua: str = None, kind: str = "playlist") -> str:
    """Builds a proxied http://127.0.0.1:<port>/api/hls?... URL, starting
    the local server on first use. Falls back to returning `url` unchanged
    if Flask isn't installed."""
    port = ensure_started()
    if not port:
        return url
    params = {"url": url, "referer": referer or "", "cookie": cookie or "",
              "ua": ua or "", "kind": kind}
    return f"http://127.0.0.1:{port}/api/hls?{urlencode(params)}"
