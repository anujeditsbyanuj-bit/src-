"""
Shared "forward one media message -> stream/download link pair" logic,
used by both the single-file flow (Akbots/filetolink.py) and the batch
flow (Akbots/filetolink_batch.py).
"""

import urllib.parse

from pyrogram.types import Message
from pyrogram.errors import FloodWait
import asyncio

from config import STREAM_BIN_CHANNEL, STREAM_URL
from database.db import db
from .file_properties import get_hash
from . import stream_routes as _routes


def links_ready() -> bool:
    return bool(STREAM_BIN_CHANNEL) and bool(STREAM_URL)


def get_media(message: Message):
    return message.document or message.video or message.audio


async def build_links_for_forwarded(forwarded: Message) -> dict:
    """Given an already-forwarded (into STREAM_BIN_CHANNEL) message, build
    its Stream/Download URLs, register it for in-process expiry tracking,
    and persist it to Mongo so expiry survives a restart."""
    hash_str = get_hash(forwarded)
    base = STREAM_URL if STREAM_URL.endswith("/") else STREAM_URL + "/"
    media = get_media(forwarded)
    name = getattr(media, "file_name", None) or f"file_{forwarded.id}"
    size = getattr(media, "file_size", 0) or 0
    stream = f"{base}watch/{forwarded.id}/{urllib.parse.quote(name)}?hash={hash_str}"
    download = f"{base}{forwarded.id}?hash={hash_str}"
    _routes.note_link_created(forwarded.id)
    try:
        await db.save_stream_link(forwarded.id, name, hash_str, size)
    except Exception:
        pass  # link still works via the in-memory cache for this process's lifetime
    return {"stream": stream, "download": download, "hash": hash_str, "file_name": name}


async def forward_and_link(client, media_msg: Message, protect: bool = False) -> dict:
    """Forwards `media_msg` (must contain document/video/audio) to
    STREAM_BIN_CHANNEL and returns its stream/download links. Retries once
    on FloodWait. Raises on any other failure (caller decides how to
    report it).

    protect=True marks the forwarded copy sitting in STREAM_BIN_CHANNEL as
    protected content (Telegram disables forwarding/saving it from there),
    for batches that shouldn't be re-shareable straight out of Telegram.
    It has no effect on the HTTP Stream/Download links themselves — those
    are governed by STREAM_LINK_EXPIRY / /set_expiry instead."""
    async def _do_protected_forward():
        result = await client.forward_messages(
            chat_id=STREAM_BIN_CHANNEL,
            from_chat_id=media_msg.chat.id,
            message_ids=media_msg.id,
            protect_content=True,
        )
        return result[0] if isinstance(result, list) else result

    try:
        if protect:
            forwarded = await _do_protected_forward()
        else:
            forwarded = await media_msg.forward(chat_id=STREAM_BIN_CHANNEL)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        if protect:
            forwarded = await _do_protected_forward()
        else:
            forwarded = await media_msg.forward(chat_id=STREAM_BIN_CHANNEL)

    links = await build_links_for_forwarded(forwarded)
    links["forwarded"] = forwarded
    return links
