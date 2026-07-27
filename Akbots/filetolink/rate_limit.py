"""
Rate limiting / abuse protection for File-to-Link — ported over from the
TGFiletoLinkBot (Thunder) project, which had this but Akbots' filetolink
didn't. Kept intentionally simple (in-memory sliding windows) rather than
porting Thunder's full priority-queue system, since Akbots doesn't have
an "authorized users" tier to prioritize — admins simply bypass both
limits entirely, everyone else shares one window.

Two independent limiters:
  - `link_limiter`  — per-user, guards /link, /linkbatch and the
    auto-generate-on-upload handler in Akbots/filetolink.py /
    filetolink_batch.py.
  - `http_limiter`  — per-client-IP, guards the actual aiohttp
    stream/download routes in Akbots/filetolink/stream_routes.py.

Both no-op (always allow) if their STREAM_*_RATE_LIMIT_ENABLED config
flag is off, so this is safe to import even in setups that don't want it.
"""

import time
from collections import deque
from typing import Dict

from aiohttp import web

from config import (
    STREAM_RATE_LIMIT_ENABLED,
    STREAM_RATE_LIMIT_MAX_LINKS,
    STREAM_RATE_LIMIT_PERIOD_SECONDS,
    STREAM_HTTP_RATE_LIMIT_ENABLED,
    STREAM_HTTP_RATE_LIMIT_MAX,
    STREAM_HTTP_RATE_LIMIT_PERIOD_SECONDS,
    STREAM_TRUST_PROXY,
)


class SlidingWindowLimiter:
    """Minimal per-key (user id or IP) sliding-window request counter."""

    def __init__(self, enabled: bool, max_requests: int, period_seconds: int):
        self.enabled = enabled
        self.max_requests = max(1, max_requests)
        self.period_seconds = max(1, period_seconds)
        self._hits: Dict[str, deque] = {}

    def check(self, key: str) -> bool:
        """Returns True if `key` is allowed to proceed right now (and
        records the hit). Returns False if it's currently over the
        limit (does NOT record — so it doesn't get worse the more they
        retry)."""
        if not self.enabled:
            return True

        now = time.time()
        window = self._hits.setdefault(key, deque())
        cutoff = now - self.period_seconds
        while window and window[0] <= cutoff:
            window.popleft()

        if len(window) >= self.max_requests:
            return False

        window.append(now)
        return True

    def retry_after(self, key: str) -> int:
        """Seconds until `key` would be allowed again, for a Retry-After
        header / friendly message. 0 if not currently limited."""
        window = self._hits.get(key)
        if not window:
            return 0
        oldest = window[0]
        wait = (oldest + self.period_seconds) - time.time()
        return max(0, int(wait) + 1)


# Per-Telegram-user limiter for link *generation* (bot-side).
link_limiter = SlidingWindowLimiter(
    STREAM_RATE_LIMIT_ENABLED, STREAM_RATE_LIMIT_MAX_LINKS, STREAM_RATE_LIMIT_PERIOD_SECONDS
)

# Per-client-IP limiter for the HTTP stream/download server (web-side).
http_limiter = SlidingWindowLimiter(
    STREAM_HTTP_RATE_LIMIT_ENABLED, STREAM_HTTP_RATE_LIMIT_MAX, STREAM_HTTP_RATE_LIMIT_PERIOD_SECONDS
)


def get_client_ip(request: web.Request) -> str:
    """Best-effort real client IP for the incoming HTTP request.

    Only trusts the X-Forwarded-For header when STREAM_TRUST_PROXY is
    explicitly enabled (i.e. you actually run behind a reverse proxy you
    control) — otherwise a client could just set that header themselves
    to dodge the per-IP limit below.
    """
    if STREAM_TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Left-most entry is the original client; the rest are the
            # chain of proxies it passed through.
            return forwarded.split(",")[0].strip()
    peer = request.remote
    return peer or "unknown"


def check_http_rate_limit(request: web.Request):
    """Raises web.HTTPTooManyRequests if this client's IP is over the
    HTTP-layer limit; otherwise returns silently."""
    ip = get_client_ip(request)
    if not http_limiter.check(ip):
        retry_after = http_limiter.retry_after(ip)
        raise web.HTTPTooManyRequests(
            text="Too many requests — please slow down and try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )
