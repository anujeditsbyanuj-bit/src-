# Akbots - Don't Remove Credit - @AkBots_Official
#
# /mp4encrypt — reply to a plain MP4/fragmented-MP4 with a method and one
# or more TRACK:KID:KEY:IV units to get a CENC-protected copy back.
# Counterpart to Akbots/mp4decrypt.py — same reply-to-media shape as
# Akbots/sample_video.py.

import os
import shutil
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import safe_edit, make_download_progress
from Akbots.mp4encrypt_util import encrypt_mp4, find_mp4encrypt, parse_unit, METHODS, DEFAULT_METHOD

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN  = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_TIP   = '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji>'
E_GEAR  = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'


@Client.on_message(filters.private & filters.command("mp4encrypt"))
async def mp4encrypt_cmd(client: Client, message: Message):
    replied = message.reply_to_message
    media = replied and (replied.video or replied.document)
    args = message.command[1:]

    if not media or len(args) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to a plain (unencrypted) MP4/fragmented-MP4 with:\n"
            f"<code>/mp4encrypt METHOD TRACK:KID:KEY:IV [TRACK:KID:KEY:IV ...]</code>\n\n"
            f"{E_TIP} METHOD is usually <code>MPEG-CENC</code> (standard Widevine/PlayReady-style "
            f"CENC — default if you skip it). TRACK is the numeric track ID (1, 2, ...), "
            f"KID/KEY are 32 hex chars each, IV is 16 or 32 hex chars — one unit per track.\n\n"
            f"Methods: <code>{', '.join(METHODS)}</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    # METHOD is optional — if the first arg isn't a recognised method name,
    # treat it as the first TRACK:KID:KEY:IV unit and fall back to the default.
    if args[0].upper() in METHODS:
        method, units = args[0].upper(), args[1:]
    else:
        method, units = DEFAULT_METHOD, args

    if not units:
        return await message.reply_text(f"<b>{E_CROSS} No TRACK:KID:KEY:IV unit(s) given.</b>", parse_mode=enums.ParseMode.HTML)

    bad = [u for u in units if not parse_unit(u)]
    if bad:
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid unit:</b> <code>{bad[0]}</code>\n"
            f"<i>Expected TRACK:KID:KEY:IV (KID/KEY = 32 hex chars, IV = 16 or 32 hex chars).</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    if not find_mp4encrypt():
        return await message.reply_text(
            f"<b>{E_CROSS} mp4encrypt binary not found</b> — expected at "
            f"<code>Akbots/bin/mp4encrypt</code>.", parse_mode=enums.ParseMode.HTML,
        )

    user_id = message.from_user.id
    status = await message.reply_text(f"<b>{E_GEAR} Downloading file...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "mp4encrypt", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    orig_name = (replied.document and replied.document.file_name) or "input.mp4"
    in_path = os.path.join(temp_dir, "in_" + orig_name)
    out_name = os.path.splitext(orig_name)[0] + "_encrypted" + (os.path.splitext(orig_name)[1] or ".mp4")
    out_path = os.path.join(temp_dir, out_name)

    try:
        await client.download_media(replied, file_name=in_path, progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Encrypting ({method}, {len(units)} track(s))...</b>", parse_mode=enums.ParseMode.HTML)
    ok, err = await encrypt_mp4(in_path, out_path, units, method=method)

    if not ok:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Encryption failed:</b>\n<code>{err or 'unknown error'}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        if replied.video or out_name.lower().endswith((".mp4", ".mkv", ".mov", ".m4v")):
            await client.send_video(
                chat_id=message.chat.id, video=out_path,
                caption=f"<blockquote>{E_CHECK} <b>ᴇɴᴄʀʏᴘᴛᴇᴅ ({method}):</b> {out_name}</blockquote>",
                reply_to_message_id=message.id, supports_streaming=True, parse_mode=enums.ParseMode.HTML,
            )
        else:
            await client.send_document(
                chat_id=message.chat.id, document=out_path,
                caption=f"<blockquote>{E_CHECK} <b>ᴇɴᴄʀʏᴘᴛᴇᴅ ({method}):</b> {out_name}</blockquote>",
                reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
            )
        await status.delete()
    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Encrypted OK but upload failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
