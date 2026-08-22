# Akbots - Don't Remove Credit - @AkBots_Official
#
# Thin bypasser-interface wrapper around Akbots/wpsafelink.py's reusable
# WP-Safelink gate resolver (resolve_known_gate/KNOWN_GATES) — that module
# already has the actual GET-the-gate/POST-the-form logic (built so
# Akbots/toonworld4all.py could call it directly); this file just exposes
# it with the same bypass_X(url) / is_X_url(url) / X_DOMAINS shape every
# other Akbots/bypassers/*.py uses, so Akbots/premiumlinks.py can treat it
# like any other paste-a-link bypasser (auto-detect + /premiumlinks flow).
#
# Domains here are the gate's user-facing/pasted hostnames — see
# Akbots/wpsafelink.py's KNOWN_GATES for the actual backing domain each
# one resolves through (rocklinks.net's gate runs on a different backend
# host than the link a user pastes; link1s.com is its own backend too).

import logging

import aiohttp

from Akbots.wpsafelink import resolve_known_gate, WPSafelinkError, KNOWN_GATES

logger = logging.getLogger(__name__)

WPSAFELINK_DOMAINS = ("rocklinks.net", "link1s.com")


def is_wpsafelink_url(url: str) -> bool:
    return any(domain in url for domain in WPSAFELINK_DOMAINS)


async def bypass_wpsafelink(url: str) -> str | None:
    """Resolves a rocklinks.net / link1s.com WP-Safelink gate URL to its
    final target. Returns None (never raises) on failure — same
    convention as every other Akbots/bypassers/*.py — so callers like
    Akbots/premiumlinks.py can just check for a falsy result."""
    try:
        async with aiohttp.ClientSession() as session:
            return await resolve_known_gate(session, url)
    except WPSafelinkError as e:
        logger.info(f"wpsafelink bypass: {url} -> {e}")
        return None
    except Exception as e:
        logger.warning(f"wpsafelink bypass: unexpected error for {url}: {e}")
        return None
