# Akbots
# /getemoji — reply to any message containing Telegram custom (premium)
# emoji to get back each one's document ID, ready to paste anywhere else
# in the bot's code as '<emoji id=...>❤️</emoji>'.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

from pyrogram import Client, filters, enums
from pyrogram.types import Message

E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'


@Client.on_message(filters.command(["getemoji", "emojiid"]) & filters.private)
async def get_emoji_id_cmd(client: Client, message: Message):
    reply = message.reply_to_message
    if not reply:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> Reply to a message containing a custom/premium "
            f"emoji with <code>/getemoji</code>.\n"
            f"<i>Works for messages you send yourself, or any message forwarded to this chat.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    text = reply.text or reply.caption or ""
    entities = list(reply.entities or []) + list(reply.caption_entities or [])
    found = [e for e in entities if e.type == enums.MessageEntityType.CUSTOM_EMOJI]

    if not found:
        return await message.reply_text(
            f"<b>{E_CROSS} No custom emoji found in that message.</b>\n"
            f"<i>Plain Unicode emoji don't have an ID — only Telegram Premium "
            f"'custom emoji' (the kind from packs like fStikBot) do.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    rows = []
    snippets = []
    for e in found:
        glyph = text[e.offset: e.offset + e.length]
        rows.append(f"{glyph} → <code>{e.custom_emoji_id}</code>")
        snippets.append(f"'&lt;emoji id={e.custom_emoji_id}&gt;{glyph}&lt;/emoji&gt;'")

    await message.reply_text(
        f"<b>{E_INFO} Found {len(found)} custom emoji:</b>\n\n"
        + "\n".join(rows)
        + "\n\n<b>Ready to paste in code:</b>\n"
        + "\n".join(f"<code>{s}</code>" for s in snippets),
        parse_mode=enums.ParseMode.HTML,
    )
