# Akbots - Don't Remove Credit - @AkBots_Official
#
# Spoiler Effects — Telegram's native "has_spoiler" blur (Bot API 7.0+),
# applied to every video/photo this bot sends.
#
# Two ways to use it:
#   /spoiler on|off   — toggle setting: every future upload (any
#                       downloader/plugin, since it's applied in
#                       direct_utils._send_one) goes out blurred until
#                       manually revealed by the recipient.
#   /spoiler          — reply to a video/photo already in the chat to
#                       re-send just that one file with the blur applied,
#                       without touching the toggle.

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
E_EYE   = '🙈'


@Client.on_message(filters.private & filters.command("spoiler"))
async def spoiler_cmd(client: Client, message: Message):
    user_id = message.from_user.id

    # Reply-to-media mode: re-send just this file with the blur applied,
    # regardless of the saved toggle.
    replied = message.reply_to_message
    if replied and (replied.video or replied.photo):
        status = await message.reply_text(f"<b>{E_EYE} Applying spoiler...</b>", parse_mode=enums.ParseMode.HTML)
        try:
            if replied.video:
                await client.send_video(
                    chat_id=message.chat.id, video=replied.video.file_id,
                    caption=replied.caption or "", reply_to_message_id=message.id,
                    has_spoiler=True, parse_mode=enums.ParseMode.HTML,
                )
            else:
                await client.send_photo(
                    chat_id=message.chat.id, photo=replied.photo.file_id,
                    caption=replied.caption or "", reply_to_message_id=message.id,
                    has_spoiler=True, parse_mode=enums.ParseMode.HTML,
                )
            await status.delete()
        except Exception as e:
            await status.edit_text(f"<b>{E_CROSS} Failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return

    # Toggle mode: /spoiler on|off
    if len(message.command) < 2:
        current = await db.get_spoiler_mode(user_id)
        return await message.reply_text(
            f"<blockquote>{E_EYE} <b>Spoiler Mode:</b> <code>{'ON' if current else 'OFF'}</code>\n\n"
            f"<b>Usage:</b> <code>/spoiler on</code> or <code>/spoiler off</code>\n"
            f"{E_TIP} When ON, every video/photo this bot uploads for you (any downloader) "
            f"goes out blurred until tapped. Or reply to a video/photo with just "
            f"<code>/spoiler</code> to blur that one file only, without changing this setting."
            f"</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    choice = message.command[1].lower()
    if choice not in ("on", "off"):
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/spoiler on</code> or <code>/spoiler off</code>",
            parse_mode=enums.ParseMode.HTML
        )

    await db.set_spoiler_mode(user_id, choice == "on")
    await message.reply_text(
        f"<b>{E_CHECK} Spoiler Mode {'enabled' if choice == 'on' else 'disabled'}.</b>\n"
        f"{E_TIP} {'Every upload will now be blurred.' if choice == 'on' else 'Uploads will no longer be blurred.'}",
        parse_mode=enums.ParseMode.HTML
    )
