# Akbots - Don't Remove Credit - @AkBots_Official
#
# File Store — merged in from the File-Store project.
#   • File sharing: send/forward a file to the DB channel, get back a
#     unique link (new secure hybrid token; old base64-style links from a
#     migrated File-Store DB still resolve too).
#   • Batch links: /batch a first..last range from the DB channel into one
#     link, with a Cancel button mid-flow.
#   • Auto-batch: Akbots/auto_batch.py watches DB-channel posts and groups
#     quality variants uploaded within a time window (default 30s) into an
#     automatic batch link.
#   • Multi-DB channels: round-robins uploads across several storage
#     channels so no single channel fills up.
#   • URL shortener gate: optional — wrap share links behind a shortener
#     redirect before the file is handed over (monetization).
#
# All commands below are ADMINS-only, same convention as broadcast.py /
# add_premium etc. Regular users only ever click the resulting links.

import re
import os
import shutil
import secrets
import string as _string
import base64
import asyncio

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from Akbots.direct_utils import wait_for_reply, safe_edit

from config import (
    ADMINS, DB_CHANNEL, FILESTORE_ENABLED, FILESTORE_EXTRA_DB_CHANNELS,
    FILESTORE_AUTO_BATCH_WINDOW, FILESTORE_ACCESS_TOKEN_MINUTES,
    FILESTORE_AUTO_DELETE_SECONDS, FILESTORE_AUTO_GENERATE_QUALITIES,
    FILESTORE_SHORTENER_NAME, FILESTORE_SHORTENER_API_URL, FILESTORE_SHORTENER_API_TOKEN,
)
from database.db import db
from Akbots.quality_detector import extract_quality, get_base_name, get_quality_priority
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

try:
    from Akbots.direct_utils import get_video_metadata
except Exception:
    get_video_metadata = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_LINK   = '<emoji id=5271604874419647061>🔗</emoji>'
E_BATCH  = '<emoji id=5341498088408234504>💯</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'
E_TIP    = '<emoji id=5422439311196834318>💡</emoji>'


# =========================================================
# Small helpers
# =========================================================

def readable_time(seconds: int) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    parts = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value}{label}")
    return " ".join(parts) if parts else "0s"


def is_token_format(s: str) -> bool:
    """New-style hybrid token: alphanumeric, 12-16 chars."""
    return s.isalnum() and 12 <= len(s) <= 16


async def encode_b64(string: str) -> str:
    b = string.encode("utf-8")
    return base64.urlsafe_b64encode(b).decode("ascii").strip("=")


async def decode_b64(s: str) -> str:
    s = s.strip("=")
    padded = (s + "=" * (-len(s) % 4)).encode("ascii")
    return base64.urlsafe_b64decode(padded).decode("utf-8")


async def shorten_url(long_url: str) -> str:
    """Wrap a link behind the configured shortener. Returns the original
    link untouched if no shortener token is set or the API call fails —
    so misconfiguration never blocks file delivery, it just skips the
    monetization step."""
    if not FILESTORE_SHORTENER_API_TOKEN:
        return long_url
    try:
        params = {"api": FILESTORE_SHORTENER_API_TOKEN, "url": long_url, "format": "text"}
        async with aiohttp.ClientSession() as session:
            async with session.get(FILESTORE_SHORTENER_API_URL, params=params, timeout=10) as resp:
                if resp.status == 200:
                    short = (await resp.text()).strip()
                    if short.startswith("http"):
                        return short
    except Exception:
        pass
    return long_url


async def shortener_gate_enabled() -> bool:
    if not FILESTORE_SHORTENER_API_TOKEN:
        return False
    return bool(await db.get_fs_config('fs_shortener_enabled', True))


async def all_db_channel_ids() -> set:
    extra_env = set(FILESTORE_EXTRA_DB_CHANNELS)
    extra_db = set(await db.get_db_channels())
    return {int(DB_CHANNEL)} | extra_env | extra_db


async def get_message_id(client: Client, message: Message):
    """If `message` is forwarded from one of our DB channels (or is a
    t.me link into one), return (channel_id, msg_id). Else (None, None)."""
    known = await all_db_channel_ids()

    fwd_chat_id = None
    fwd_msg_id = None
    if getattr(message, "forward_from_chat", None):
        fwd_chat_id = message.forward_from_chat.id
        fwd_msg_id = message.forward_from_message_id
    elif getattr(message, "forward_origin", None) and getattr(message.forward_origin, "type", None) == "channel":
        fwd_chat_id = message.forward_origin.chat.id
        fwd_msg_id = message.forward_origin.message_id

    if fwd_chat_id is not None:
        if fwd_chat_id in known:
            return fwd_chat_id, fwd_msg_id
        return None, None

    if message.text:
        m = re.match(r"https://t\.me/(?:c/)?(.+)/(\d+)", message.text.strip())
        if m:
            ch_str, msg_id = m.group(1), int(m.group(2))
            if ch_str.isdigit():
                cid = int(f"-100{ch_str}")
                if cid in known:
                    return cid, msg_id
            else:
                try:
                    chat = await client.get_chat(ch_str)
                    if chat.id in known:
                        return chat.id, msg_id
                except Exception:
                    pass
    return None, None


async def get_messages_range(client: Client, chat_id: int, ids):
    out = []
    ids = list(ids)
    i = 0
    while i < len(ids):
        chunk = ids[i:i + 200]
        try:
            msgs = await client.get_messages(chat_id=chat_id, message_ids=chunk)
        except Exception:
            msgs = []
        out.extend(msgs)
        i += 200
    return out


async def find_quality_siblings(client: Client, channel_id: int, msg_id: int, base_name: str, radius: int = 30):
    """Scan message IDs around `msg_id` in the same DB channel for other
    quality variants of the same file (same base_name, different
    quality tag). Used so a single /genlink (or an upload) auto-upgrades
    to a batch link when the other qualities are already sitting nearby
    in the channel — mirrors what auto_batch.py does for fresh uploads,
    but works retroactively on anything already posted."""
    candidate_ids = [i for i in range(max(1, msg_id - radius), msg_id + radius + 1)]
    msgs = await get_messages_range(client, channel_id, candidate_ids)

    found = {}
    for m in msgs:
        if not m or not getattr(m, "document", None) or not m.document.file_name:
            continue
        fname = m.document.file_name
        quality = extract_quality(fname)
        if not quality:
            continue
        if get_base_name(fname) != base_name:
            continue
        # Keep the first (lowest message id) match per quality.
        found.setdefault(quality, {
            'file_id': str(m.id), 'filename': fname, 'quality': quality, 'channel_id': channel_id,
        })

    files = sorted(found.values(), key=lambda f: get_quality_priority(f['quality']))
    return files


QUALITY_HEIGHTS = {
    '144p': 144, '240p': 240, '360p': 360, '480p': 480,
    '720p': 720, '1080p': 1080, '4K': 2160,
}


async def generate_missing_qualities(client: Client, src_msg: Message, base_name: str, have_qualities: set, status: Message):
    """Re-encode `src_msg`'s video into whichever of FILESTORE_AUTO_GENERATE_QUALITIES
    aren't already in `have_qualities` (and aren't bigger than the source),
    upload each to the DB channel round robin, and return the new sibling
    entries (same shape find_quality_siblings returns). Best-effort — skips
    a resolution silently on any ffmpeg/upload failure rather than failing
    the whole link."""
    if get_video_metadata is None:
        return []
    targets = [q for q in FILESTORE_AUTO_GENERATE_QUALITIES if q not in have_qualities and q in QUALITY_HEIGHTS]
    if not targets:
        return []

    temp_dir = os.path.join("downloads", "filestore_autogen", f"{src_msg.chat.id}_{src_msg.id}")
    os.makedirs(temp_dir, exist_ok=True)
    orig_name = None
    if src_msg.document:
        orig_name = src_msg.document.file_name
    elif src_msg.video:
        orig_name = src_msg.video.file_name
    orig_name = orig_name or f"{base_name}.mp4"
    in_path = os.path.join(temp_dir, orig_name)

    try:
        await safe_edit(status.edit_text, f"{E_GEAR} Downloading source to generate missing qualities...")
        await client.download_media(src_msg, file_name=in_path)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return []

    try:
        duration, src_w, src_h = await asyncio.to_thread(get_video_metadata, in_path)
    except Exception:
        duration, src_w, src_h = 0, 1280, 720

    new_files = []
    for q in targets:
        height = QUALITY_HEIGHTS[q]
        if src_h and height >= src_h:
            continue  # never upscale past the source's own resolution

        out_name = f"{base_name}.{q}.mp4"
        out_path = os.path.join(temp_dir, out_name)
        cmd = [
            "ffmpeg", "-hide_banner", "-y", "-i", in_path,
            "-vf", f"scale=-2:{height}",
            "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
            "-c:a", "copy",
            out_path,
        ]
        try:
            await safe_edit(status.edit_text, f"{E_GEAR} Generating {q}...")
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
        except Exception:
            continue

        if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            continue

        target_channel = await db.get_next_db_channel(int(DB_CHANNEL))
        try:
            sent = await client.send_video(target_channel, out_path, file_name=out_name, caption=f"<blockquote>{out_name}</blockquote>", parse_mode=enums.ParseMode.HTML)
        except Exception:
            try:
                sent = await client.send_document(target_channel, out_path, file_name=out_name, caption=f"<blockquote>{out_name}</blockquote>", parse_mode=enums.ParseMode.HTML)
            except Exception:
                try:
                    os.remove(out_path)
                except Exception:
                    pass
                continue

        new_files.append({'file_id': str(sent.id), 'filename': out_name, 'quality': q, 'channel_id': target_channel})
        try:
            os.remove(out_path)
        except Exception:
            pass

    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    return new_files


async def delete_after_delay(client: Client, chat_id: int, message_ids: list, warn_msg: Message):
    if FILESTORE_AUTO_DELETE_SECONDS <= 0:
        return
    await asyncio.sleep(FILESTORE_AUTO_DELETE_SECONDS)
    try:
        await client.delete_messages(chat_id=chat_id, message_ids=message_ids)
    except Exception:
        pass
    try:
        await safe_edit(warn_msg.edit_text, f"<b>{E_CHECK} Shared file(s) auto-deleted.</b>")
    except Exception:
        pass


def cancel_kb():
    return InlineKeyboardMarkup([[make_button(f"{E_CROSS} ᴄᴀɴᴄᴇʟ", callback_data="fs_cancel", style=_BS.PRIMARY if _BS else None)]])


_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".flv", ".wmv", ".m4v", ".ts"}


def _looks_like_video(file_name: str) -> bool:
    return bool(file_name) and os.path.splitext(file_name)[1].lower() in _VIDEO_EXTS


async def build_share_link(client: Client, channel_id: int, msg_id: int, file_name: str, notify_chat_id: int = None):
    """Build a share link for one DB-channel message.
    1. Looks for other quality variants of the same file already sitting
       nearby in the channel — if 2+ are found, upgrades to a batch link.
    2. If fewer than 2 are found and /autogenerate is ON, re-encodes the
       source itself (via ffmpeg) into the missing qualities from
       FILESTORE_AUTO_GENERATE_QUALITIES and batches those in too.
    So a single /genlink (or upload) can end up handing back every
    quality regardless of what was already in the channel."""
    me = await client.get_me()
    quality = extract_quality(file_name) if file_name else None
    base_name = get_base_name(file_name) if file_name else None

    if base_name and _looks_like_video(file_name):
        siblings = await find_quality_siblings(client, channel_id, msg_id, base_name)
        have = {f['quality'] for f in siblings}
        if quality and quality not in have:
            siblings.append({'file_id': str(msg_id), 'filename': file_name, 'quality': quality, 'channel_id': channel_id})
            have.add(quality)

        if len(siblings) < 2 and await db.get_fs_config('fs_auto_generate_enabled', False):
            status = await client.send_message(notify_chat_id or channel_id, f"{E_GEAR} Generating missing qualities for {base_name}...")
            try:
                src_msg = await client.get_messages(channel_id, msg_id)
                generated = await generate_missing_qualities(client, src_msg, base_name, have, status)
                siblings.extend(generated)
            finally:
                try:
                    await status.delete()
                except Exception:
                    pass

        if len(siblings) >= 2:
            siblings.sort(key=lambda f: get_quality_priority(f['quality']))
            batch_id = await db.create_batch(base_name, siblings)
            link = f"https://t.me/{me.username}?start=batch_{batch_id}"
            qualities = " | ".join(f['quality'] for f in siblings)
            extra = f"<b>ǫᴜᴀʟɪᴛɪᴇs:</b> {qualities}\n"
            return link, extra, True

    token = await db.create_file_token(channel_id, msg_id)
    link = f"https://t.me/{me.username}?start={token}"
    return link, "", False


# =========================================================
# /genlink — single file share link (reply to a file, or interactive)
# =========================================================

@Client.on_message(filters.command("genlink") & filters.private & filters.user(ADMINS))
async def genlink_cmd(client: Client, message: Message):
    if not FILESTORE_ENABLED:
        return await message.reply_text(f"{E_WARN} File Store is disabled (FILESTORE_ENABLED=False).")

    if message.reply_to_message:
        replied = message.reply_to_message
        status = await message.reply_text(f"{E_GEAR} Processing...")

        channel_id, msg_id = await get_message_id(client, replied)
        if not msg_id:
            # Not already in a DB channel — copy it there via round robin.
            target = await db.get_next_db_channel(int(DB_CHANNEL))
            try:
                posted = await replied.copy(chat_id=target, caption=replied.caption)
            except Exception as e:
                return await safe_edit(status.edit_text, f"{E_CROSS} Couldn't store file: {e}")
            channel_id, msg_id = target, posted.id

        file_name = ""
        if replied.document:
            file_name = replied.document.file_name or ""
        elif replied.caption:
            file_name = replied.caption.split("\n")[0][:50]

        link, quality_line, is_batch = await build_share_link(client, channel_id, msg_id, file_name, message.chat.id)

        text = "<blockquote>"
        if is_batch:
            text += f"<b>{E_BATCH} Batch (all qualities found)</b>\n"
        if file_name:
            text += f"<b>📂 {file_name}</b>\n"
        text += quality_line
        if FILESTORE_AUTO_DELETE_SECONDS > 0:
            text += f"⏳ <b>ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ:</b> {readable_time(FILESTORE_AUTO_DELETE_SECONDS)}\n"
        text += "</blockquote>\n\n" if text != "<blockquote>" else ""
        text += f"<b>{E_LINK} Here is your link:</b>\n\n<code>{link}</code>"

        buttons = InlineKeyboardMarkup([[make_button("🔁 sʜᴀʀᴇ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}", style=_BS.PRIMARY if _BS else None)]])
        return await safe_edit(status.edit_text, text, reply_markup=buttons)

    # Interactive flow — ask them to forward the DB channel post
    ask = await message.reply_text(
        f"{E_TIP} Forward the message from the DB channel (or send its t.me link)...",
        reply_markup=cancel_kb(),
    )
    try:
        resp = await wait_for_reply(client, chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
    except Exception:
        return await safe_edit(ask.edit_text, f"{E_CROSS} Timed out.")

    if isinstance(resp, CallbackQuery):
        await resp.answer("Cancelled", show_alert=True)
        return await safe_edit(ask.edit_text, f"{E_CROSS} Cancelled.")

    channel_id, msg_id = await get_message_id(client, resp)
    if not msg_id:
        return await resp.reply_text(f"{E_CROSS} That's not from a configured DB channel.")
    await ask.delete()

    file_name = ""
    try:
        f_msg = await client.get_messages(channel_id, msg_id)
        if f_msg and f_msg.document:
            file_name = f_msg.document.file_name or ""
        elif f_msg and f_msg.caption:
            file_name = f_msg.caption.split("\n")[0][:50]
    except Exception:
        pass

    link, quality_line, is_batch = await build_share_link(client, channel_id, msg_id, file_name, message.chat.id)
    text = ""
    if is_batch:
        text += f"<b>{E_BATCH} Batch (all qualities found)</b>\n"
    if file_name:
        text += f"<b>📂 {file_name}</b>\n"
    text += quality_line
    if text:
        text += "\n"
    text += f"<b>{E_LINK} Here is your link:</b>\n\n<code>{link}</code>"
    buttons = InlineKeyboardMarkup([[make_button("🔁 sʜᴀʀᴇ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}", style=_BS.PRIMARY if _BS else None)]])
    await resp.reply_text(text, quote=True, reply_markup=buttons)


# =========================================================
# /batch — first..last range from the DB channel, with Cancel
# =========================================================

@Client.on_message(filters.command("batch") & filters.private & filters.user(ADMINS))
async def batch_cmd(client: Client, message: Message):
    if not FILESTORE_ENABLED:
        return await message.reply_text(f"{E_WARN} File Store is disabled (FILESTORE_ENABLED=False).")

    async def ask_one(prompt):
        ask = await message.reply_text(prompt, reply_markup=cancel_kb())
        try:
            resp = await wait_for_reply(client, chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        except Exception:
            return None, None, None
        if isinstance(resp, CallbackQuery):
            await resp.answer("Cancelled", show_alert=True)
            await ask.delete()
            return "CANCELLED", None, None
        cid, mid = await get_message_id(client, resp)
        if not mid:
            await resp.reply_text(f"{E_CROSS} That's not from a configured DB channel. Try again or hit Cancel.", quote=True)
            return None, None, None
        await ask.delete()
        return "OK", cid, mid

    first_channel = first_id = None
    while True:
        status, cid, mid = await ask_one(f"{E_TIP} Forward the **first message** from the DB channel...")
        if status == "CANCELLED":
            return await message.reply_text(f"{E_CROSS} Batch cancelled.")
        if status == "OK":
            first_channel, first_id = cid, mid
            break

    last_id = None
    while True:
        status, cid, mid = await ask_one(f"{E_TIP} Forward the **last message** from the DB channel...")
        if status == "CANCELLED":
            return await message.reply_text(f"{E_CROSS} Batch cancelled.")
        if status == "OK":
            last_id = mid
            break

    batch_name = ""
    try:
        first_msg = await client.get_messages(first_channel, first_id)
        if first_msg and first_msg.document:
            batch_name = first_msg.document.file_name or ""
        elif first_msg and first_msg.caption:
            batch_name = first_msg.caption.split("\n")[0][:50]
    except Exception:
        pass

    lo, hi = (first_id, last_id) if last_id >= first_id else (last_id, first_id)
    episode_count = hi - lo + 1

    auto_gen_on = await db.get_fs_config('fs_auto_generate_enabled', False)
    if auto_gen_on and episode_count > 1:
        proceed = await message.reply_text(
            f"<b>{E_GEAR} Auto-generate is ON</b> (<code>/autogenerate</code>).\n\n"
            f"This range has <b>{episode_count} episode(s)</b>. Generating missing "
            f"qualities for <i>every</i> episode means up to {episode_count} re-encodes — "
            f"could take a long time (each one is a full ffmpeg pass).\n\n"
            f"Proceed, or just make the plain range-batch instead (whatever quality's "
            f"already there for each episode)?",
            reply_markup=InlineKeyboardMarkup([[
                make_button("✅ ɢᴇɴᴇʀᴀᴛᴇ ᴀʟʟ ǫᴜᴀʟɪᴛɪᴇs", callback_data="fs_batch_genall", style=_BS.PRIMARY if _BS else None),
                make_button("➡️ ᴘʟᴀɪɴ ʙᴀᴛᴄʜ ɪɴsᴛᴇᴀᴅ", callback_data="fs_batch_plain", style=_BS.PRIMARY if _BS else None),
            ]]),
        )
        try:
            choice = await wait_for_reply(client, chat_id=message.chat.id, user_id=message.from_user.id, timeout=60)
        except Exception:
            choice = None
        want_genall = isinstance(choice, CallbackQuery) and choice.data == "fs_batch_genall"
        if isinstance(choice, CallbackQuery):
            await choice.answer()
        try:
            await proceed.delete()
        except Exception:
            pass

        if want_genall:
            return await _batch_generate_all_qualities(client, message, first_channel, lo, hi, batch_name)

    token = await db.create_file_token(first_channel, first_id, end_msg_id=last_id)
    me = await client.get_me()
    link = f"https://t.me/{me.username}?start={token}"

    text = f"<blockquote><b>{E_BATCH} Batch created</b>\n"
    if batch_name:
        text += f"📄 {batch_name}\n"
    text += f"🔢 Range: {first_id} - {last_id} ({abs(last_id - first_id) + 1} files)\n"
    if FILESTORE_AUTO_DELETE_SECONDS > 0:
        text += f"⏳ Auto delete: {readable_time(FILESTORE_AUTO_DELETE_SECONDS)}\n"
    text += "</blockquote>\n\n"
    text += f"<b>{E_LINK} Here is your link:</b>\n\n<code>{link}</code>"

    buttons = InlineKeyboardMarkup([[make_button("🔁 sʜᴀʀᴇ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}", style=_BS.PRIMARY if _BS else None)]])
    await message.reply_text(text, reply_markup=buttons)


async def _batch_generate_all_qualities(client: Client, message: Message, channel_id: int, lo: int, hi: int, batch_name: str):
    """The heavy version of /batch: walks every message id in [lo, hi],
    and for each video file makes sure every quality in
    FILESTORE_AUTO_GENERATE_QUALITIES exists for it — either found already
    sitting nearby in the channel, or re-encoded on the spot — then bundles
    every file (every episode × every quality) into one batch link. Fully
    sequential and best-effort per episode: one episode failing to encode
    doesn't stop the rest."""
    status = await message.reply_text(f"{E_GEAR} Starting — this will take a while for a big range...")
    all_files = []
    episode_ids = list(range(lo, hi + 1))
    total = len(episode_ids)

    for i, msg_id in enumerate(episode_ids, start=1):
        try:
            src_msg = await client.get_messages(channel_id, msg_id)
        except Exception:
            continue
        if not src_msg or not src_msg.document or not src_msg.document.file_name:
            continue

        file_name = src_msg.document.file_name
        if not _looks_like_video(file_name):
            continue

        base_name = get_base_name(file_name)
        quality = extract_quality(file_name)
        if not base_name:
            continue

        await safe_edit(status.edit_text, f"{E_GEAR} Episode {i}/{total}: checking existing qualities for {base_name}...")
        siblings = await find_quality_siblings(client, channel_id, msg_id, base_name)
        have = {f['quality'] for f in siblings}
        if quality and quality not in have:
            siblings.append({'file_id': str(msg_id), 'filename': file_name, 'quality': quality, 'channel_id': channel_id})
            have.add(quality)

        missing = [q for q in FILESTORE_AUTO_GENERATE_QUALITIES if q not in have]
        if missing:
            await safe_edit(status.edit_text, f"{E_GEAR} Episode {i}/{total}: generating {', '.join(missing)} for {base_name}...")
            try:
                generated = await generate_missing_qualities(client, src_msg, base_name, have, status)
                siblings.extend(generated)
            except Exception:
                pass  # this episode just keeps whatever qualities it already had

        all_files.extend(siblings)

    if not all_files:
        return await safe_edit(status.edit_text, f"{E_CROSS} No video files found in that range.")

    all_files.sort(key=lambda f: (f['file_id'], get_quality_priority(f['quality'])))
    batch_id = await db.create_batch(batch_name or f"batch_{lo}_{hi}", all_files)
    me = await client.get_me()
    link = f"https://t.me/{me.username}?start=batch_{batch_id}"

    text = (
        f"<blockquote><b>{E_BATCH} Batch created — all qualities per episode</b>\n"
        f"📄 {batch_name or f'{total} episodes'}\n"
        f"🔢 {total} episode(s), {len(all_files)} file(s) total\n"
        f"</blockquote>\n\n"
        f"<b>{E_LINK} Here is your link:</b>\n\n<code>{link}</code>"
    )
    buttons = InlineKeyboardMarkup([[make_button("🔁 sʜᴀʀᴇ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}", style=_BS.PRIMARY if _BS else None)]])
    await safe_edit(status.edit_text, text, reply_markup=buttons)


@Client.on_callback_query(filters.regex("^fs_cancel$"))
async def fs_cancel_cb(client: Client, cq: CallbackQuery):
    await cq.answer("Cancelled")


@Client.on_callback_query(filters.regex("^fs_batch_(genall|plain)$"))
async def fs_batch_choice_fallback_cb(client: Client, cq: CallbackQuery):
    # client.listen() in batch_cmd() normally intercepts this tap directly —
    # this only fires if that listen() call already timed out, so the
    # button press has nowhere else to go.
    await cq.answer("This timed out — run /batch again.", show_alert=True)


# =========================================================
# Multi-DB channel management
# =========================================================

@Client.on_message(filters.command("dbchannels") & filters.private & filters.user(ADMINS))
async def list_db_channels(client: Client, message: Message):
    extra = await db.get_db_channels()
    multi_on = await db.is_multi_db_enabled()
    lines = [f"<blockquote><b>{E_GEAR} Multi-DB Channels</b>", f"Round robin: {'ON ✅' if multi_on else 'OFF ❌'}",
             f"Primary: <code>{DB_CHANNEL}</code>"]
    if FILESTORE_EXTRA_DB_CHANNELS:
        lines.append("From ENV: " + ", ".join(f"<code>{c}</code>" for c in FILESTORE_EXTRA_DB_CHANNELS))
    if extra:
        lines.append("Added via /adddbchannel: " + ", ".join(f"<code>{c}</code>" for c in extra))
    lines.append("</blockquote>")
    await message.reply_text("\n".join(lines))


@Client.on_message(filters.command("adddbchannel") & filters.private & filters.user(ADMINS))
async def add_db_channel_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(f"{E_WARN} Usage: <code>/adddbchannel -1001234567890</code>")
    try:
        cid = int(message.command[1])
    except ValueError:
        return await message.reply_text(f"{E_CROSS} Invalid channel ID.")
    try:
        member = await client.get_chat_member(cid, "me")
        if member.status.name not in ("ADMINISTRATOR", "OWNER"):
            return await message.reply_text(f"{E_CROSS} Bot must be an admin in that channel first.")
    except Exception as e:
        return await message.reply_text(f"{E_CROSS} Couldn't verify bot membership: {e}")
    await db.add_db_channel(cid)
    await message.reply_text(f"{E_CHECK} Added <code>{cid}</code> to the multi-DB rotation.")


@Client.on_message(filters.command("deldbchannel") & filters.private & filters.user(ADMINS))
async def del_db_channel_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(f"{E_WARN} Usage: <code>/deldbchannel -1001234567890</code>")
    try:
        cid = int(message.command[1])
    except ValueError:
        return await message.reply_text(f"{E_CROSS} Invalid channel ID.")
    await db.remove_db_channel(cid)
    await message.reply_text(f"{E_CHECK} Removed <code>{cid}</code> from the rotation.")


@Client.on_message(filters.command("multidb") & filters.private & filters.user(ADMINS))
async def toggle_multidb_cmd(client: Client, message: Message):
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        current = await db.is_multi_db_enabled()
        return await message.reply_text(
            f"{E_GEAR} Multi-DB round robin is currently <b>{'ON' if current else 'OFF'}</b>.\n"
            f"Usage: <code>/multidb on</code> or <code>/multidb off</code>"
        )
    want_on = message.command[1].lower() == "on"
    current = await db.is_multi_db_enabled()
    if current != want_on:
        await db.toggle_multi_db()
    await message.reply_text(f"{E_CHECK} Multi-DB round robin turned <b>{'ON' if want_on else 'OFF'}</b>.")


# =========================================================
# Auto-batch on/off + time window
# =========================================================

@Client.on_message(filters.command("autobatch") & filters.private & filters.user(ADMINS))
async def autobatch_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        enabled = await db.get_fs_config('fs_auto_batch_enabled', False)
        window = await db.get_fs_config('fs_auto_batch_window', FILESTORE_AUTO_BATCH_WINDOW)
        return await message.reply_text(
            f"<blockquote><b>{E_GEAR} Auto-Batch</b>\n"
            f"Status: {'ON ✅' if enabled else 'OFF ❌'}\n"
            f"Window: {window}s</blockquote>\n\n"
            f"Usage:\n<code>/autobatch on</code> / <code>/autobatch off</code>\n"
            f"<code>/autobatch window 45</code>"
        )

    if args[0].lower() in ("on", "off"):
        await db.set_fs_config('fs_auto_batch_enabled', args[0].lower() == "on")
        return await message.reply_text(f"{E_CHECK} Auto-batch turned <b>{args[0].upper()}</b>.")

    if args[0].lower() == "window" and len(args) > 1:
        try:
            seconds = max(5, int(args[1]))
        except ValueError:
            return await message.reply_text(f"{E_CROSS} Give a number of seconds.")
        await db.set_fs_config('fs_auto_batch_window', seconds)
        return await message.reply_text(f"{E_CHECK} Auto-batch window set to <b>{seconds}s</b>.")

    await message.reply_text(f"{E_WARN} Unknown option. See <code>/autobatch</code> for usage.")


# =========================================================
# Auto-link-on-upload — admin sends a video/file directly, bot stores it
# in the DB channel (round-robin) and replies with the share link.
# Off by default (opt-in via /uploadmode on) to stay clear of every other
# feature in this bot that also reacts to a raw document/video upload
# (rename, watermark, unzip, gdrive, cookies...).
# =========================================================

@Client.on_message(filters.command("uploadmode") & filters.private & filters.user(ADMINS))
async def uploadmode_cmd(client: Client, message: Message):
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        enabled = await db.get_fs_config('fs_auto_upload_enabled', False)
        return await message.reply_text(
            f"{E_GEAR} Auto-link-on-upload is currently <b>{'ON' if enabled else 'OFF'}</b>.\n"
            f"When ON, any video/document/audio you send me directly gets stored in the DB "
            f"channel and you get a share link back immediately (auto-merged with other "
            f"qualities of the same file if found).\n\n"
            f"Usage: <code>/uploadmode on</code> or <code>/uploadmode off</code>"
        )
    want_on = message.command[1].lower() == "on"
    await db.set_fs_config('fs_auto_upload_enabled', want_on)
    await message.reply_text(f"{E_CHECK} Auto-link-on-upload turned <b>{'ON' if want_on else 'OFF'}</b>.")


@Client.on_message(filters.private & filters.user(ADMINS) & (filters.document | filters.video | filters.audio))
async def auto_link_on_upload(client: Client, message: Message):
    if not FILESTORE_ENABLED:
        return
    if not await db.get_fs_config('fs_auto_upload_enabled', False):
        return

    status = await message.reply_text(f"{E_GEAR} Storing & generating link...", quote=True)

    target = await db.get_next_db_channel(int(DB_CHANNEL))
    try:
        posted = await message.copy(chat_id=target, caption=message.caption)
    except Exception as e:
        return await safe_edit(status.edit_text, f"{E_CROSS} Couldn't store file: {e}")

    file_name = ""
    if message.document:
        file_name = message.document.file_name or ""
    elif message.video:
        file_name = message.video.file_name or ""
    elif message.caption:
        file_name = message.caption.split("\n")[0][:50]

    link, quality_line, is_batch = await build_share_link(client, target, posted.id, file_name, message.chat.id)

    text = "<blockquote>"
    if is_batch:
        text += f"<b>{E_BATCH} Batch (all qualities found)</b>\n"
    if file_name:
        text += f"<b>📂 {file_name}</b>\n"
    text += quality_line
    if FILESTORE_AUTO_DELETE_SECONDS > 0:
        text += f"⏳ <b>ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ:</b> {readable_time(FILESTORE_AUTO_DELETE_SECONDS)}\n"
    text += "</blockquote>\n\n"
    text += f"<b>{E_LINK} Here is your link:</b>\n\n<code>{link}</code>"

    buttons = InlineKeyboardMarkup([[make_button("🔁 sʜᴀʀᴇ ᴜʀʟ", url=f"https://telegram.me/share/url?url={link}", style=_BS.PRIMARY if _BS else None)]])
    await safe_edit(status.edit_text, text, reply_markup=buttons)


# =========================================================
# Auto-generate missing qualities on/off
# =========================================================

@Client.on_message(filters.command("autogenerate") & filters.private & filters.user(ADMINS))
async def autogenerate_cmd(client: Client, message: Message):
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        enabled = await db.get_fs_config('fs_auto_generate_enabled', False)
        return await message.reply_text(
            f"{E_GEAR} Auto-generate-missing-qualities is currently <b>{'ON' if enabled else 'OFF'}</b>.\n"
            f"When ON, if /genlink or an upload can't find other qualities of a video "
            f"already in the DB channel, the bot re-encodes it with ffmpeg into "
            f"<code>{', '.join(FILESTORE_AUTO_GENERATE_QUALITIES)}</code> (skipping anything bigger "
            f"than the source) and batches those in too. Costs real time/CPU per link — off by default.\n\n"
            f"Usage: <code>/autogenerate on</code> or <code>/autogenerate off</code>"
        )
    want_on = message.command[1].lower() == "on"
    await db.set_fs_config('fs_auto_generate_enabled', want_on)
    await message.reply_text(f"{E_CHECK} Auto-generate-missing-qualities turned <b>{'ON' if want_on else 'OFF'}</b>.")


# =========================================================
# URL shortener gate on/off
# =========================================================

@Client.on_message(filters.command("shortener") & filters.private & filters.user(ADMINS))
async def shortener_cmd(client: Client, message: Message):
    if not FILESTORE_SHORTENER_API_TOKEN:
        return await message.reply_text(
            f"{E_WARN} No shortener configured — set FILESTORE_SHORTENER_API_TOKEN "
            f"(and optionally FILESTORE_SHORTENER_API_URL / FILESTORE_SHORTENER_NAME) "
            f"in the environment first."
        )
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        enabled = await shortener_gate_enabled()
        return await message.reply_text(
            f"{E_GEAR} Shortener ({FILESTORE_SHORTENER_NAME}) gate is <b>{'ON' if enabled else 'OFF'}</b>.\n"
            f"Usage: <code>/shortener on</code> or <code>/shortener off</code>"
        )
    want_on = message.command[1].lower() == "on"
    await db.set_fs_config('fs_shortener_enabled', want_on)
    await message.reply_text(f"{E_CHECK} Shortener gate turned <b>{'ON' if want_on else 'OFF'}</b>.")


# =========================================================
# Deep-link resolver — called from Akbots/start.py's /start handler
# =========================================================

async def handle_filestore_start(client: Client, message: Message, payload: str) -> bool:
    """Returns True if this /start payload was a file-store link and has
    been fully handled (caller should stop processing further)."""
    if not FILESTORE_ENABLED or not payload:
        return False

    user_id = message.from_user.id
    is_admin = user_id in ADMINS

    # ---- shortener round-trip: "<original>_<access_token>" ----
    original = payload
    access_token = None
    if "_" in payload:
        if payload.startswith("batch_"):
            parts = payload.split("_")
            if len(parts) >= 3:
                original = f"{parts[0]}_{parts[1]}"
                access_token = parts[2]
        else:
            head, _, tail = payload.rpartition("_")
            if head and is_token_format(head) and len(tail) >= 8:
                original, access_token = head, tail

    if access_token and not is_admin and await shortener_gate_enabled():
        await db.increment_access_clicks(user_id, access_token)
        result = await db.verify_access_token(user_id, access_token, original)
        if result == "EXPIRED":
            await message.reply_text(f"{E_CROSS} <b>ʟɪɴᴋ ᴇxᴘɪʀᴇᴅ.</b> Please open the share link again.")
            return True
        if result == "ALREADY_USED":
            await message.reply_text(f"{E_CROSS} <b>ᴛʜɪs ʟɪɴᴋ ᴡᴀs ᴀʟʀᴇᴀᴅʏ ᴜsᴇᴅ.</b> Get a fresh one from the share link.")
            return True
        if result != "OK":
            await message.reply_text(f"{E_CROSS} <b>ɪɴᴠᴀʟɪᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ ʟɪɴᴋ.</b>")
            return True
        # verified — fall through to delivery below

    is_batch_link = original.startswith("batch_")

    if not is_batch_link and not is_token_format(original) and not original.startswith("get-"):
        return False  # not a file-store payload at all

    # ---- resolve target(s) ----
    channel_id = None
    ids = []
    legacy_batch_doc = None

    if is_batch_link:
        batch_id = original[len("batch_"):]
        legacy_batch_doc = await db.get_batch(batch_id)
        if not legacy_batch_doc:
            await message.reply_text(f"{E_CROSS} <b>ʙᴀᴛᴄʜ ɴᴏᴛ ғᴏᴜɴᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ.</b>")
            return True
    elif is_token_format(original):
        if await db.is_token_rate_limited(user_id):
            await message.reply_text(f"{E_WARN} <b>ᴛᴏᴏ ᴍᴀɴʏ ɪɴᴠᴀʟɪᴅ ᴀᴛᴛᴇᴍᴘᴛs.</b> Wait a minute and try again.")
            return True
        token_doc = await db.resolve_file_token(original)
        if not token_doc:
            await db.record_invalid_token_attempt(user_id)
            await message.reply_text(f"{E_CROSS} <b>ɪɴᴠᴀʟɪᴅ ᴏʀ ᴇxᴘɪʀᴇᴅ ʟɪɴᴋ.</b>")
            return True
        channel_id = token_doc["channel_id"]
        start_id = token_doc["msg_id"]
        end_id = token_doc.get("end_msg_id")
        ids = list(range(start_id, end_id + 1)) if end_id else [start_id]
    else:
        # legacy base64 "get-..." link
        try:
            decoded = await decode_b64(original)
            parts = decoded.split("-")
        except Exception:
            return False
        try:
            if len(parts) == 3:
                channel_id = int(f"-100{int(parts[1])}")
                ids = [int(parts[2])]
            elif len(parts) == 2:
                channel_id = int(DB_CHANNEL)
                ids = [int(int(parts[1]) / abs(int(DB_CHANNEL)))]
            else:
                return False
        except Exception:
            return False

    # ---- shortener gate for fresh (non-round-trip) requests ----
    if not is_admin and not access_token and await shortener_gate_enabled():
        content_name = ""
        try:
            if legacy_batch_doc:
                content_name = f"📦 <b>{legacy_batch_doc.get('base_name', 'Batch Pack')}</b>\n\n"
            elif ids:
                f_msg = await client.get_messages(channel_id, ids[0])
                if f_msg and f_msg.document:
                    content_name = f"🎬 <b>{f_msg.document.file_name}</b>\n\n"
        except Exception:
            pass

        new_token = secrets.token_hex(16)
        await db.create_access_token(user_id, original, new_token, expiry_minutes=FILESTORE_ACCESS_TOKEN_MINUTES)
        me = await client.get_me()
        gated_link = f"https://t.me/{me.username}?start={original}_{new_token}"
        shortened = await shorten_url(gated_link)

        buttons = InlineKeyboardMarkup([[make_button("🔓 ɢᴇᴛ ғɪʟᴇ", url=shortened, style=_BS.PRIMARY if _BS else None)]])
        await message.reply_text(
            f"{content_name}<b>{E_LINK} Your file is ready.</b>\n\n"
            f"<blockquote>👉 Solve the shortener below to unlock it.</blockquote>",
            reply_markup=buttons,
        )
        return True

    # ---- deliver ----
    status = await message.reply_text(f"{E_GEAR} Fetching...")
    delivered = []

    if legacy_batch_doc:
        for f in legacy_batch_doc.get("files", []):
            try:
                msg = await client.get_messages(f["channel_id"], int(f["file_id"]))
                caption = msg.caption.html if msg.caption else (msg.document.file_name if msg.document else "")
                copied = await msg.copy(chat_id=user_id, caption=caption)
                delivered.append(copied.id)
            except Exception:
                continue
    else:
        msgs = await get_messages_range(client, channel_id, ids)
        for msg in msgs:
            if not msg:
                continue
            try:
                caption = msg.caption.html if msg.caption else (msg.document.file_name if msg.document else "")
                copied = await msg.copy(chat_id=user_id, caption=caption)
                delivered.append(copied.id)
            except Exception:
                continue

    if not delivered:
        await safe_edit(status.edit_text, f"{E_CROSS} Couldn't find the file(s) — they may have been deleted from the DB channel.")
        return True

    await status.delete()
    if FILESTORE_AUTO_DELETE_SECONDS > 0:
        warn = await client.send_message(
            user_id, f"<b>⚠️ File(s) will be deleted in {readable_time(FILESTORE_AUTO_DELETE_SECONDS)}.</b>"
        )
        asyncio.create_task(delete_after_delay(client, user_id, delivered, warn))
    return True
