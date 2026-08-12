# Akbots
# /info — user info card (first name, last name, Telegram ID, username,
# clickable profile link). Works on yourself, on a replied-to message's
# sender, or on a passed user_id/@username.
# Don't Remove Credit
# Telegram Channel @AkBots_Official

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from pyrogram.errors import PeerIdInvalid, UsernameNotOccupied, UsernameInvalid

E_CROSS = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'


async def _resolve_target(client: Client, message: Message):
    """Returns the pyrogram User to show info for: replied-to sender,
    else a passed id/@username argument, else the caller themself."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        target = args[1].strip()
        return await client.get_users(target)

    return message.from_user


@Client.on_message(filters.command(["info", "userinfo"]))
async def info_cmd(client: Client, message: Message):
    try:
        user = await _resolve_target(client, message)
    except (PeerIdInvalid, UsernameNotOccupied, UsernameInvalid):
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't find that user.</b>\n"
            f"<i>Reply to their message, or pass a valid user ID / @username.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    except IndexError:
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't resolve a user from that message.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    first_name = user.first_name or "None"
    last_name = user.last_name or "None"
    username = f"@{user.username}" if user.username else "None"
    user_link = f'<a href="tg://user?id={user.id}">Click Here</a>'

    text = (
        "👤 ᴜsᴇʀ ɪɴғᴏʀᴍᴀᴛɪᴏɴ 👤\n\n"
        f"➲ ғɪʀsᴛ ɴᴀᴍᴇ: {first_name}\n"
        f"➲ ʟᴀsᴛ ɴᴀᴍᴇ: {last_name}\n"
        f"➲ ᴛᴇʟᴇɢʀᴀᴍ ɪᴅ: <code>{user.id}</code>\n"
        f"➲ ᴜsᴇʀ ɴᴀᴍᴇ: {username}\n"
        f"➲ ᴜsᴇʀ ʟɪɴᴋ: {user_link}"
    )

    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
