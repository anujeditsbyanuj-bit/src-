# Akbots - Don't Remove Credit - @AkBots_Official
#
# /mp4decrypt — reply to an encrypted MP4/fragmented-MP4 with one or more
# KID:KEY pairs to get the decrypted file back. Uses the bundled Bento4
# mp4decrypt binary (Akbots/bin/mp4decrypt) via Akbots/mp4decrypt_util.py.
# Same reply-to-media -> download -> subprocess -> upload -> cleanup shape
# as Akbots/sample_video.py and screenshots.py.
#
# This only DECRYPTS with a key you already have — it has no capability
# to extract, derive, or crack a key itself.

import os
import shutil
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import safe_edit, make_download_progress
from Akbots.mp4decrypt_util import decrypt_mp4, find_mp4decrypt, valid_key

E_CHECK = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN  = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_TIP   = '<tg-emoji emoji-id="5422439311196834318">💡</tg-emoji>'
E_GEAR  = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'


@Client.on_message(filters.private & filters.command("mp4decrypt"))
async def mp4decrypt_cmd(client: Client, message: Message):
    replied = message.reply_to_message
    media = replied and (replied.video or replied.document)
    if not media or len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to an encrypted MP4/fragmented-MP4 with:\n"
            f"<code>/mp4decrypt KID:KEY [KID:KEY ...]</code>\n\n"
            f"{E_TIP} KID and KEY are each 32 hex characters (128-bit AES), "
            f"separated by a colon — one <code>--key</code> pair per audio/video track "
            f"if the file has separately-keyed tracks.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    keys = message.command[1:]
    bad = [k for k in keys if not valid_key(k)]
    if bad:
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid key format:</b> <code>{bad[0]}</code>\n"
            f"<i>Expected KID:KEY, 32 hex characters each.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    if not find_mp4decrypt():
        return await message.reply_text(
            f"<b>{E_CROSS} mp4decrypt binary not found</b> — expected at "
            f"<code>Akbots/bin/mp4decrypt</code>.", parse_mode=enums.ParseMode.HTML,
        )

    user_id = message.from_user.id
    status = await message.reply_text(f"<b>{E_GEAR} Downloading file...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join("downloads", "mp4decrypt", f"{user_id}_{replied.id}")
    os.makedirs(temp_dir, exist_ok=True)
    orig_name = (replied.document and replied.document.file_name) or "input.mp4"
    in_path = os.path.join(temp_dir, "in_" + orig_name)
    out_name = os.path.splitext(orig_name)[0] + "_decrypted" + (os.path.splitext(orig_name)[1] or ".mp4")
    out_path = os.path.join(temp_dir, out_name)

    try:
        await client.download_media(replied, file_name=in_path, progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Decrypting with {len(keys)} key(s)...</b>", parse_mode=enums.ParseMode.HTML)
    ok, err = await decrypt_mp4(in_path, out_path, keys)

    if not ok:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Decryption failed:</b>\n<code>{err or 'unknown error'}</code>\n\n"
            f"<i>Check the KID:KEY pair(s) are correct and match this file's tracks.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        if replied.video or out_name.lower().endswith((".mp4", ".mkv", ".mov", ".m4v")):
            await client.send_video(
                chat_id=message.chat.id, video=out_path,
                caption=f"<blockquote>{E_CHECK} <b>ᴅᴇᴄʀʏᴘᴛᴇᴅ:</b> {out_name}</blockquote>",
                reply_to_message_id=message.id, supports_streaming=True, parse_mode=enums.ParseMode.HTML,
            )
        else:
            await client.send_document(
                chat_id=message.chat.id, document=out_path,
                caption=f"<blockquote>{E_CHECK} <b>ᴅᴇᴄʀʏᴘᴛᴇᴅ:</b> {out_name}</blockquote>",
                reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML,
            )
        await status.delete()
    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Decrypted OK but upload failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
