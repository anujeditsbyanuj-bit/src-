"""
/linkbatch — generate Stream + Download HTTP links for a whole range of
messages in one go (instead of one file at a time via /link).

Usage:
    /linkbatch https://t.me/channelusername/10 https://t.me/channelusername/30
    /linkbatch https://t.me/c/1234567890/10 https://t.me/c/1234567890/30   (private chat, numeric id)
    /linkbatch <first_link> <last_link> --protect   (protect forwarded copies from re-forwarding)

The bot must already be a member (admin, for private chats) of the source
chat so it can read those messages. Each media message (document / video /
audio) in the range gets forwarded into STREAM_BIN_CHANNEL and turned into
a Stream + Download link pair, same as the single-file /link command.

Adding --protect marks each forwarded copy that lands in STREAM_BIN_CHANNEL
as protected content, so Telegram blocks forwarding/saving it out of that
channel. This only affects the internal copies in STREAM_BIN_CHANNEL — the
Stream/Download HTTP links handed back are unaffected (their lifetime is
controlled separately by STREAM_LINK_EXPIRY / /set_expiry).

This is intentionally separate from filestore.py's /batch (which builds a
single deep-link "open in bot" batch) — /linkbatch instead hands back a
plain list of ready-to-click HTTP links, useful for sharing outside
Telegram.
"""

import re
import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from config import ADMINS, STREAM_BATCH_MAX_FREE, STREAM_BATCH_MAX_ADMIN
from database.db import db
from Akbots.filetolink.link_builder import links_ready, get_media, forward_and_link
from Akbots.filetolink.rate_limit import link_limiter

logger = logging.getLogger(__name__)

LINK_RE = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(c/)?([\w\d_]+)/(\d+)")

MAX_TEXT_LEN = 3500  # stay comfortably under Telegram's 4096 char cap


def _parse_msg_link(link: str):
    m = LINK_RE.match(link.strip())
    if not m:
        return None, None
    is_private, chat_ref, msg_id = m.group(1), m.group(2), int(m.group(3))
    if is_private or chat_ref.isdigit():
        chat_id = int(f"-100{chat_ref}")
    else:
        chat_id = f"@{chat_ref}"
    return chat_id, msg_id


async def _fetch_messages_in_range(client: Client, chat_id, first_id: int, last_id: int):
    """Yields messages for ids [first_id, last_id], fetched in chunks of
    200 (Telegram's per-request limit for get_messages)."""
    ids = list(range(first_id, last_id + 1))
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        try:
            messages = await client.get_messages(chat_id, chunk)
        except Exception as e:
            logger.warning(f"[linkbatch] get_messages failed for chunk starting {chunk[0]}: {e}")
            continue
        if not messages:
            continue
        if isinstance(messages, Message):
            messages = [messages]
        for msg in messages:
            if msg:
                yield msg


@Client.on_message(filters.command(["linkbatch"]) & filters.private)
async def linkbatch_cmd(client: Client, message: Message):
    is_admin = bool(message.from_user and message.from_user.id in ADMINS)

    if not is_admin and message.from_user:
        try:
            if await db.is_banned(message.from_user.id):
                return
        except Exception:
            pass  # fail open — a DB hiccup shouldn't block a legit user

        if not link_limiter.check(str(message.from_user.id)):
            retry_after = link_limiter.retry_after(str(message.from_user.id))
            await message.reply_text(
                f"⏳ You're generating links too quickly — please wait "
                f"~{retry_after}s and try again.",
                quote=True,
            )
            return

    if not links_ready():
        await message.reply_text(
            "⚠️ File-to-Link isn't configured yet. Set STREAM_BIN_CHANNEL "
            "(and STREAM_FQDN) first — see FILE_TO_LINK_SETUP.md.",
            quote=True,
        )
        return

    parts = message.text.strip().split()
    protect = False
    if "--protect" in parts:
        protect = True
        parts = [p for p in parts if p != "--protect"]

    if len(parts) != 3:
        await message.reply_text(
            "Use: <code>/linkbatch first_message_link last_message_link [--protect]</code>\n"
            "Example: <code>/linkbatch https://t.me/mychannel/10 https://t.me/mychannel/30</code>\n"
            "Add <code>--protect</code> to block forwarding/saving of the copies stored "
            "in STREAM_BIN_CHANNEL.",
            quote=True,
        )
        return

    _, first_link, last_link = parts
    f_chat, f_id = _parse_msg_link(first_link)
    l_chat, l_id = _parse_msg_link(last_link)

    if f_chat is None or l_chat is None:
        await message.reply_text("One of those links doesn't look like a valid t.me message link.", quote=True)
        return
    if f_chat != l_chat:
        await message.reply_text("Both links must be from the same chat.", quote=True)
        return
    if l_id < f_id:
        f_id, l_id = l_id, f_id

    max_span = STREAM_BATCH_MAX_ADMIN if is_admin else STREAM_BATCH_MAX_FREE
    span = l_id - f_id + 1
    if span > max_span:
        await message.reply_text(
            f"That range covers {span} messages — the cap is {max_span} "
            f"{'for admins' if is_admin else 'for regular users'} per /linkbatch. "
            f"Try a smaller range.",
            quote=True,
        )
        return

    try:
        await client.get_chat(f_chat)
    except Exception as e:
        await message.reply_text(
            f"Couldn't access that chat ({e}). If it's private, make sure this bot is a member/admin there.",
            quote=True,
        )
        return

    protect_note = " (protected — forwarding disabled)" if protect else ""
    status = await message.reply_text(
        f"⏳ Generating links for {span} messages{protect_note}...", quote=True
    )

    results = []
    processed = 0
    async for msg in _fetch_messages_in_range(client, f_chat, f_id, l_id):
        processed += 1
        if msg.empty or msg.service or not get_media(msg):
            continue
        try:
            links = await forward_and_link(client, msg, protect=protect)
            results.append(links)
        except Exception as e:
            logger.warning(f"[linkbatch] Failed on message {msg.id}: {e}")
        await asyncio.sleep(0.3)  # be gentle on STREAM_BIN_CHANNEL / flood limits

        if processed % 10 == 0:
            try:
                await status.edit(f"⏳ Processed {processed}/{span} — {len(results)} link(s) so far...")
            except Exception:
                pass

    if not results:
        await status.edit("No document/video/audio files found in that range.")
        return

    header = f"✅ <b>{len(results)} link(s) generated{protect_note}:</b>\n"
    lines = [header]
    chunks = []
    current = lines[0]
    for i, r in enumerate(results, 1):
        entry = f"{i}. <b>{r['file_name']}</b>\n   ▶️ {r['stream']}\n   ⬇️ {r['download']}\n"
        if len(current) + len(entry) > MAX_TEXT_LEN:
            chunks.append(current)
            current = entry
        else:
            current += entry
    chunks.append(current)

    await status.edit(chunks[0])
    for chunk in chunks[1:]:
        await message.reply_text(chunk, disable_web_page_preview=True)
        await asyncio.sleep(0.3)
