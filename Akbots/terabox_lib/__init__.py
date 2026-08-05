# Akbots - Don't Remove Credit - @AkBots_Official
#
# Public entry point for the direct-API fallback tier — Akbots/terabox.py
# only ever imports `resolve` and `is_configured` from here, never touches
# direct_client.py directly. Keeps a single lazily-created client instance
# so short_url_info's jsToken/logid don't get re-fetched from scratch on
# every call within the same process.

import logging

from .direct_client import TeraBoxDirectClient, TeraBoxDirectClientError

logger = logging.getLogger(__name__)

try:
    from config import TERABOX_NDUS, TERABOX_DIRECT_CLEANUP_MINUTES
except ImportError:
    TERABOX_NDUS = ""
    TERABOX_DIRECT_CLEANUP_MINUTES = 30

_client: TeraBoxDirectClient | None = None


def is_configured() -> bool:
    return bool(TERABOX_NDUS)


def _get_client() -> TeraBoxDirectClient:
    global _client
    if _client is None:
        _client = TeraBoxDirectClient(TERABOX_NDUS, cleanup_minutes=TERABOX_DIRECT_CLEANUP_MINUTES)
    return _client


async def resolve(share_url: str, password: str | None = None) -> list[dict]:
    """Third/last-resort fallback used by Akbots/terabox.py after xAPIverse
    and terabox.beer both fail. Raises TeraBoxDirectClientError (or
    propagates any aiohttp error) on failure — callers should catch broadly,
    same as the other two resolvers."""
    if not is_configured():
        raise TeraBoxDirectClientError("TERABOX_NDUS not set — direct-API tier disabled.")
    return await _get_client().resolve(share_url, password=password)


__all__ = ["resolve", "is_configured", "TeraBoxDirectClientError"]
