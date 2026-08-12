# Akbots - Don't Remove Credit - @AkBots_Official
#
# Public entry point for the direct-API fallback tier — Akbots/terabox.py
# only ever imports `resolve` and `is_configured` from here, never touches
# direct_client.py directly. Keeps a lazily-created client per cookie so
# each account's jsToken/logid don't get re-fetched from scratch on every
# call within the same process.
#
# Multi-cookie rotation (ported from the TeraBox-Video-Downloader repo's
# COOKIES1/COOKIES2/.../COOKIESn pattern): a single Terabox account's
# cookie gets rate-limited fast under real traffic. Set TERABOX_NDUS_POOL
# to a comma-separated list of ndus cookies (throwaway accounts, not your
# personal one) and this tier round-robins between them on every call,
# retrying the next cookie if one comes back rate-limited/invalid.
# TERABOX_NDUS (singular) still works exactly as before if you only have
# one — it's treated as a 1-cookie pool.
#
# Auto-login (Akbots/terabox_lib/auto_login.py, ported from terabot-main):
# if every cookie in the pool fails and TERABOX_EMAIL + TERABOX_PASSWORD
# are also set, this logs back in headlessly to mint a fresh ndus cookie
# and adds it to the pool for this run, instead of just failing outright.
# Both are optional — leave them unset and nothing changes from before.

import logging

from .direct_client import TeraBoxDirectClient, TeraBoxDirectClientError

logger = logging.getLogger(__name__)

try:
    from config import TERABOX_NDUS, TERABOX_DIRECT_CLEANUP_MINUTES
except ImportError:
    TERABOX_NDUS = ""
    TERABOX_DIRECT_CLEANUP_MINUTES = 30

try:
    from config import TERABOX_NDUS_POOL
except ImportError:
    TERABOX_NDUS_POOL = ""

try:
    from config import TERABOX_EMAIL, TERABOX_PASSWORD
except ImportError:
    TERABOX_EMAIL = ""
    TERABOX_PASSWORD = ""

_COOKIE_POOL = [c.strip() for c in TERABOX_NDUS_POOL.split(",") if c.strip()] or ([TERABOX_NDUS] if TERABOX_NDUS else [])
_clients: dict[str, TeraBoxDirectClient] = {}
_pool_idx = 0
_login_lock = None  # created lazily — avoids requiring a running event loop at import time


def is_configured() -> bool:
    """True if there's at least one cookie to start with, OR credentials
    to mint one on demand via auto-login."""
    return bool(_COOKIE_POOL) or bool(TERABOX_EMAIL and TERABOX_PASSWORD)


def _get_client(cookie: str) -> TeraBoxDirectClient:
    if cookie not in _clients:
        _clients[cookie] = TeraBoxDirectClient(cookie, cleanup_minutes=TERABOX_DIRECT_CLEANUP_MINUTES)
    return _clients[cookie]


async def _try_auto_login() -> str | None:
    """Guarded by a lock so concurrent resolve() calls that all hit an
    expired pool don't each launch their own login attempt."""
    global _login_lock
    import asyncio
    if _login_lock is None:
        _login_lock = asyncio.Lock()

    async with _login_lock:
        # Another call may have already refreshed the pool while we were
        # waiting for the lock — nothing to do if so.
        if _COOKIE_POOL:
            return _COOKIE_POOL[-1]
        if not (TERABOX_EMAIL and TERABOX_PASSWORD):
            return None
        from .auto_login import login_and_get_ndus
        ndus = await login_and_get_ndus(TERABOX_EMAIL, TERABOX_PASSWORD)
        if ndus:
            _COOKIE_POOL.append(ndus)
            logger.info("terabox_lib: auto-login minted a fresh ndus cookie, added to pool.")
        return ndus


async def resolve(share_url: str, password: str | None = None) -> list[dict]:
    """Third/last-resort fallback used by Akbots/terabox.py after xAPIverse
    and terabox.beer both fail. Tries every cookie in the pool before
    giving up, so one rate-limited account doesn't take this whole tier
    down. If every cookie fails AND TERABOX_EMAIL/TERABOX_PASSWORD are
    set, attempts one auto-login to mint a fresh cookie and retries with
    that before finally raising."""
    global _pool_idx
    if not is_configured():
        raise TeraBoxDirectClientError("TERABOX_NDUS / TERABOX_NDUS_POOL not set — direct-API tier disabled.")

    last_error: Exception | None = None
    for _ in range(len(_COOKIE_POOL)):
        if not _COOKIE_POOL:
            break
        cookie = _COOKIE_POOL[_pool_idx % len(_COOKIE_POOL)]
        _pool_idx += 1
        try:
            return await _get_client(cookie).resolve(share_url, password=password)
        except Exception as e:
            last_error = e
            logger.debug(f"terabox_lib: cookie ...{cookie[-6:]} failed ({e}), trying next in pool")
            continue

    if TERABOX_EMAIL and TERABOX_PASSWORD:
        fresh = await _try_auto_login()
        if fresh:
            try:
                return await _get_client(fresh).resolve(share_url, password=password)
            except Exception as e:
                last_error = e
        elif last_error is None:
            last_error = TeraBoxDirectClientError(
                "No cookie configured and auto-login failed (wrong credentials, "
                "2FA/verification-code wall, or the login page layout changed)."
            )

    raise last_error or TeraBoxDirectClientError("All cookies in TERABOX_NDUS_POOL failed, and auto-login is not configured.")


__all__ = ["resolve", "is_configured", "TeraBoxDirectClientError"]
