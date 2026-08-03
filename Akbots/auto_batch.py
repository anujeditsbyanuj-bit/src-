# Akbots - Don't Remove Credit - @AkBots_Official
#
# Auto-Batch — watches posts made to the configured DB channels and, when
# two or more quality variants of the same file land within a time window
# (default 30s, see /autobatch), automatically groups them into one batch
# link and drops the link back in the channel.
#
# Off by default — turn on with /autobatch on.

import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from database.db import db
from Akbots.quality_detector import extract_quality, get_base_name, get_series_name, get_quality_priority
from Akbots.filestore import all_db_channel_ids, readable_time
from config import FILESTORE_ENABLED, FILESTORE_AUTO_BATCH_WINDOW, FILESTORE_AUTO_DELETE_SECONDS
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_BATCH = '<emoji id=5341498088408234504>💯</emoji>'


@Client.on_message(filters.channel & filters.document)
async def auto_batch_handler(client: Client, message: Message):
    if not FILESTORE_ENABLED:
        return

    known = await all_db_channel_ids()
    if message.chat.id not in known:
        return

    if not await db.get_fs_config('fs_auto_batch_enabled', False):
        return

    filename = message.document.file_name
    if not filename:
        return

    quality = extract_quality(filename)
    if not quality:
        return

    base_name = get_base_name(filename)
    if not base_name:
        return

    await db.add_pending_file(
        file_id=str(message.id),
        filename=filename,
        base_name=base_name,
        quality=quality,
        user_id=(message.from_user.id if message.from_user else 0),
        channel_id=message.chat.id,
    )

    time_window = await db.get_fs_config('fs_auto_batch_window', FILESTORE_AUTO_BATCH_WINDOW)
    # Give a couple seconds' grace for near-simultaneous uploads to land.
    await asyncio.sleep(2)

    pending = await db.get_pending_files(time_window)

    groups = {}
    for f in pending:
        key = f['base_name']
        groups.setdefault(key, []).append(f)

    current_group_key = base_name
    if current_group_key not in groups or len(groups[current_group_key]) < 2:
        return

    files = groups[current_group_key]
    files.sort(key=lambda f: get_quality_priority(f['quality']))

    batch_id = await db.create_batch(current_group_key, [
        {'file_id': f['file_id'], 'filename': f['filename'], 'quality': f['quality'], 'channel_id': f['channel_id']}
        for f in files
    ])

    qualities = " | ".join(f['quality'] for f in files)
    timer_line = f"⏳ <b>ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ:</b> {readable_time(FILESTORE_AUTO_DELETE_SECONDS)}\n" if FILESTORE_AUTO_DELETE_SECONDS > 0 else ""

    text = (
        f"<blockquote><b>{E_BATCH} Batch available</b>\n\n"
        f"<b>ᴛɪᴛʟᴇ:</b> {current_group_key}\n"
        f"<b>ǫᴜᴀʟɪᴛɪᴇs:</b> {qualities}\n"
        f"{timer_line}"
        f"Click below to get all files.</blockquote>"
    )
    me = await client.get_me()
    button = InlineKeyboardMarkup([[make_button(f"{E_BATCH} ɢᴇᴛ ʙᴀᴛᴄʜ", url=f"https://t.me/{me.username}?start=batch_{batch_id}", style=_BS.PRIMARY if _BS else None)]])

    try:
        await message.reply_text(text, reply_markup=button, quote=False)
    except Exception:
        pass
