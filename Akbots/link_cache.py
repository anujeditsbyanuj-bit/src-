# Akbots
# Link Cache — once a URL has been downloaded and uploaded once, its
# Telegram file_id is remembered so the exact same link never has to be
# re-downloaded: it's re-sent straight from Telegram's own servers via
# send_cached_media(), which is near-instant regardless of file size.
#
# Only used for single-file downloads (one URL -> one file). Playlists,
# folders and anything split into multiple parts aren't cached, since
# there's no single file_id that represents the whole thing.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import hashlib
import logging

from pyrogram import enums
from pyrogram.errors import RPCError
from pyrogram.types import Message

logger = logging.getLogger("Akbots.link_cache")


def url_hash(url: str) -> str:
    """Normalizes a URL (strip whitespace/trailing slash, lowercase the
    scheme+host part is skipped for simplicity — case differences in the
    path are rare and a false cache-miss just costs a normal re-download,
    never a wrong result) and hashes it for use as the Mongo key."""
    normalized = (url or "").strip().rstrip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _extract_cache_fields(sent: Message):
    """Pulls (file_id, media_kind) out of a just-sent Message. Returns
    (None, None) if the message doesn't carry cacheable media (shouldn't
    normally happen for our use, but never raise over it)."""
    if sent is None:
        return None, None
    for kind in ("video", "audio", "document", "photo", "voice", "animation"):
        media = getattr(sent, kind, None)
        if media is not None:
            file_id = getattr(media, "file_id", None)
            if file_id:
                return file_id, kind
    return None, None


async def store(url: str, sent: Message, caption: str = None):
    """Call after a successful single-file upload to remember it for next
    time. Best-effort — a caching failure must never break the upload that
    already succeeded."""
    if not url or sent is None:
        return
    try:
        file_id, kind = _extract_cache_fields(sent)
        if not file_id:
            return
        from database.db import db
        await db.set_cached_link(url_hash(url), {
            "file_id": file_id,
            "kind": kind,
            "caption": caption if caption is not None else (sent.caption.html if sent.caption else None),
        })
    except Exception as e:
        logger.debug(f"link_cache.store: failed for {url}: {e}")


async def try_send_cached(client, message: Message, url: str, status: Message = None,
                           delete_status: bool = True) -> bool:
    """Checks if `url` was downloaded before; if so, re-sends the cached
    file instantly and returns True. Returns False (no side effects) on a
    cache miss, or if the cached file_id no longer works (file deleted from
    Telegram's servers, etc.) — in either case the caller should fall
    through to a normal download.

    delete_status: set False when `status` is a single message reused
    across a batch/folder loop — deleting it after one cache hit would
    crash every later status.edit_text() call in that loop.
    """
    if not url:
        return False
    try:
        from database.db import db
        entry = await db.get_cached_link(url_hash(url))
        if not entry or not entry.get("file_id"):
            return False

        if status is not None:
            try:
                await status.edit_text(
                    "<b>⚡ Found in cache — sending instantly...</b>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass

        await client.send_cached_media(
            chat_id=message.chat.id,
            file_id=entry["file_id"],
            caption=entry.get("caption"),
            reply_to_message_id=message.id,
            parse_mode=enums.ParseMode.HTML,
        )
        if status is not None and delete_status:
            try:
                await status.delete()
            except Exception:
                pass
        return True
    except RPCError as e:
        # file_id no longer valid (Telegram purged it, wrong bot, etc.) —
        # drop the stale entry and let the caller re-download normally.
        logger.debug(f"try_send_cached: stale cache entry for {url}: {e}")
        try:
            from database.db import db
            await db.delete_cached_link(url_hash(url))
        except Exception:
            pass
        return False
    except Exception as e:
        logger.debug(f"try_send_cached: failed for {url}: {e}")
        return False
