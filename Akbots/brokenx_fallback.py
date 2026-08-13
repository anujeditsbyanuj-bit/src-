# YouTube IP-block-avoidance fallback via the third-party BrokenXAPI
# service (https://t.me/ABOUTBROKENX).
#
# When yt-dlp fails on a YouTube link with something that looks like a
# bot-check/IP-block (403, "Sign in to confirm you're not a bot", rate
# limiting, etc) — even after web/tv_embedded/android clients AND the
# PO-token server (Akbots/bgutil_bootstrap.py) have already been tried —
# that generally means THIS SERVER'S IP has been flagged, not that
# anything is wrong with the video itself. Retrying locally again won't
# help. BrokenXAPI routes around that: their own servers fetch the video
# from YouTube (their IP, not this bot's) and hand back a link to a copy
# of it already uploaded to a Telegram channel — this bot then just
# server-side-copies that Telegram message straight to the user, so it
# never re-downloads anything either.
#
# Entirely optional. If BROKENX_API_KEY isn't set (config.py), or the
# `brokenxapi` package isn't installed, try_brokenx_fallback() is a no-op
# that returns False immediately — the caller's normal yt-dlp error is
# shown exactly as before. Ported from a reference bot's platforms/
# Youtube.py (which used BrokenXAPI as its ONLY download path) into a
# fallback here instead, since this project's yt-dlp+PO-token path is
# already the stronger primary path — BrokenXAPI is just the safety net
# for when that primary path gets IP-blocked.

import re
import logging
from urllib.parse import urlparse

from config import BROKENX_API_KEY
from Akbots.direct_utils import E_ROCKET

logger = logging.getLogger(__name__)

# Matches the kind of yt-dlp error text that indicates THIS SERVER got
# bot-checked/IP-blocked/rate-limited by YouTube, as opposed to the video
# itself being private/deleted/geo-restricted (which BrokenXAPI would
# fail on too, so retrying there would be pointless for those).
_LOOKS_LIKE_IP_BLOCK = re.compile(
    r"sign in to confirm|not a bot|http error 403|403:?\s*forbidden|"
    r"unable to download webpage|precondition failed|429|too many requests|"
    r"rate.?limit",
    re.IGNORECASE,
)


def looks_like_ip_block(error_text: str) -> bool:
    """Heuristic check on a yt-dlp failure message — is this worth retrying
    via BrokenXAPI, or would that just fail the same way (private/deleted/
    region-locked video)?"""
    return bool(_LOOKS_LIKE_IP_BLOCK.search(str(error_text or "")))


def _video_id_from_url(url: str) -> str:
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{6,})", url)
    return m.group(1) if m else url


async def try_brokenx_fallback(client, chat_id: int, reply_to: int, url: str,
                                audio_only: bool, status=None) -> bool:
    """Attempts delivery via BrokenXAPI. Returns True if the file was
    already sent to `chat_id` (caller should stop, nothing more to do).
    Returns False on ANY failure or if the feature isn't configured —
    caller should fall through to showing its original yt-dlp error."""
    if not BROKENX_API_KEY or BROKENX_API_KEY == "PUT_YOUR_KEY_HERE":
        return False

    try:
        from brokenxapi import BrokenXAPI
    except ImportError:
        logger.warning("BROKENX_API_KEY is set but the 'brokenxapi' package isn't installed "
                        "(pip install brokenxapi).")
        return False

    video_id = _video_id_from_url(url)

    if status is not None:
        try:
            await status.edit_text(
                f"<b>{E_ROCKET} yt-dlp got blocked on this server — retrying via a relay service...</b>"
            )
        except Exception:
            pass

    try:
        async with BrokenXAPI(api_key=BROKENX_API_KEY) as api:
            data = await api.download(video_id, "audio" if audio_only else "video")
    except Exception as e:
        logger.warning(f"BrokenXAPI request failed for {video_id}: {e}")
        return False

    if not data or "telegram_url" not in data:
        logger.warning(f"BrokenXAPI returned no telegram_url for {video_id}: {data}")
        return False

    try:
        parsed = urlparse(data["telegram_url"])
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            logger.warning(f"BrokenXAPI returned an unparseable telegram_url: {data['telegram_url']}")
            return False
        channel_name, message_id = parts[0], int(parts[1])
    except Exception as e:
        logger.warning(f"Failed to parse BrokenXAPI telegram_url: {e}")
        return False

    try:
        src_msg = await client.get_messages(channel_name, message_id)
        if not src_msg or not (src_msg.video or src_msg.audio or src_msg.document):
            logger.warning(f"BrokenXAPI's relay message for {video_id} has no media.")
            return False
        # Server-side copy — Telegram moves the file directly between
        # chats, this bot never downloads or re-uploads any bytes itself.
        await src_msg.copy(chat_id, reply_to_message_id=reply_to)
    except Exception as e:
        logger.warning(f"BrokenXAPI relay delivery failed for {video_id}: {e}")
        return False

    if status is not None:
        try:
            await status.delete()
        except Exception:
            pass
    return True
