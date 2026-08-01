# Akbots
# Custom Channel — lets a user redirect every file the bot delivers them
# into one or more channels/groups of their own (in addition to their
# private chat).
#
# Multi-channel storage lives in database.db's dump_chats list
# (add_dump_chat / remove_dump_chat / get_dump_chats), which also merges in
# the older single-value dump_chat field (set_dump_chat / get_dump_chat)
# used by /setchat and the Settings menu, so both old and new links show up
# together everywhere. The actual forwarding happens in
# Akbots/direct_utils.py -> _forward_to_custom_channel(), which runs once
# per delivered file and now loops over every linked channel.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db
from logger import LOGGER

logger = LOGGER(__name__)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'

USAGE_TXT = (
    f"<blockquote>{E_WARN} <b>Usage:</b> <code>/set_channel_id -100xxxxxxxxxx</code>\n"
    f"<b>Example:</b> <code>/set_channel_id -1001234567890</code>\n\n"
    f"<b>Note:</b> The ID must start with <code>-100</code> and you must make me "
    f"an admin in that channel/group. Get your Channel ID by forwarding any "
    f"message from the channel to @MissRose_bot.\n\n"
    f"<i>You can link more than one channel/group — just run this command again "
    f"with a different ID. Use /channel_id to see everything you've linked, and "
    f"/del_channel_id &lt;id&gt; to unlink one (or /del_channel_id with no id to "
    f"unlink everything).</i></blockquote>"
)

NOT_SET_TXT = (
    f"<blockquote>{E_CROSS} <b>No custom channel set!</b>\n\n"
    f"You don't have any custom channel/group configured.\n"
    f"To set one: /set_channel_id</blockquote>"
)


@Client.on_message(filters.command(["set_channel_id", "set_channel", "add_channel_id"]) & filters.private)
async def set_channel_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if len(message.command) < 2:
        return await message.reply_text(USAGE_TXT, parse_mode=enums.ParseMode.HTML)

    raw = message.command[1].strip()
    try:
        chat_id = int(raw)
    except ValueError:
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid Channel ID</b>\n\n{USAGE_TXT}",
            parse_mode=enums.ParseMode.HTML,
        )

    if not raw.startswith("-100"):
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid Channel ID</b>\n\n{USAGE_TXT}",
            parse_mode=enums.ParseMode.HTML,
        )

    existing = await db.get_dump_chats(user_id)
    if chat_id in existing:
        return await message.reply_text(
            f"<b>{E_INFO} Already Linked</b>\n\n"
            f"<code>{chat_id}</code> is already in your linked channels list.\n"
            f"See all of them with /channel_id.",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        chat = await client.get_chat(chat_id)
    except Exception as e:
        logger.debug(f"set_channel_id: get_chat({chat_id}) failed: {e}")
        return await message.reply_text(
            f"<b>{E_CROSS} Unable to Access That Chat</b>\n\n"
            f"<i>Make sure the bot has already been added to the channel/group "
            f"before linking it.</i>\n\n{USAGE_TXT}",
            parse_mode=enums.ParseMode.HTML,
        )

    try:
        await client.send_message(
            chat_id,
            f"{E_CHECK} <b>Channel Linked</b>\n"
            f"This channel/group has been set by {message.from_user.mention} "
            f"to receive downloaded files.",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        logger.debug(f"set_channel_id: test send to {chat_id} failed: {e}")
        return await message.reply_text(
            f"<b>{E_CROSS} Bot Can't Post There</b>\n\n"
            f"<i>Please make me an admin (with permission to post messages) "
            f"in that channel/group, then try again.</i>\n\n{USAGE_TXT}",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.add_dump_chat(user_id, chat_id)

    channel_title = chat.title or "Private Chat"
    username_line = f" @{chat.username}" if getattr(chat, "username", None) else ""
    total = len(await db.get_dump_chats(user_id))

    await message.reply_text(
        f"<blockquote>{E_CHECK} <b>Custom Channel Linked!</b>\n\n"
        f"<b>Channel Name:</b> {channel_title}{username_line}\n"
        f"<b>Channel ID:</b> <code>{chat_id}</code>\n\n"
        f"All downloaded files will now also be sent here.\n"
        f"<b>Total linked channels:</b> {total}\n\n"
        f"<b>Note:</b> Files won't be auto-deleted from channel/group.\n\n"
        f"Link another: /set_channel_id • See all: /channel_id\n"
        f"To remove this one: <code>/del_channel_id {chat_id}</code></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("del_channel_id") & filters.private)
async def del_channel_command(client: Client, message: Message):
    user_id = message.from_user.id
    current = await db.get_dump_chats(user_id)

    if not current:
        return await message.reply_text(NOT_SET_TXT, parse_mode=enums.ParseMode.HTML)

    if len(message.command) < 2:
        # No id given — unlink everything, same as the old single-channel behaviour.
        for chat_id in list(current):
            await db.remove_dump_chat(user_id, chat_id)
        await db.set_dump_chat(user_id, None)
        return await message.reply_text(
            f"<blockquote>{E_CHECK} <b>All Custom Channels Removed</b>\n\n"
            f"{E_WARN} <i>Downloaded files will no longer be sent to any linked "
            f"channel/group. Files already delivered there were not deleted.</i></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    raw = message.command[1].strip()
    try:
        chat_id = int(raw)
    except ValueError:
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid Channel ID</b>\n\n{USAGE_TXT}",
            parse_mode=enums.ParseMode.HTML,
        )

    if chat_id not in current:
        return await message.reply_text(
            f"<b>{E_CROSS} Not Linked</b>\n\n"
            f"<code>{chat_id}</code> isn't in your linked channels. See /channel_id for the list.",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.remove_dump_chat(user_id, chat_id)
    legacy = await db.get_dump_chat(user_id)
    if legacy == chat_id:
        await db.set_dump_chat(user_id, None)

    await message.reply_text(
        f"<blockquote>{E_CHECK} <b>Channel Removed</b>\n\n"
        f"<code>{chat_id}</code> has been unlinked. "
        f"{E_WARN} <i>Files already delivered there were not deleted.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["channel_id", "get_channel_id", "channel_status"]) & filters.private)
async def channel_status_command(client: Client, message: Message):
    """Lists every channel/group the user currently has linked, or the
    NOT_SET_TXT prompt if none has been linked yet."""
    user_id = message.from_user.id
    current = await db.get_dump_chats(user_id)

    if not current:
        return await message.reply_text(NOT_SET_TXT, parse_mode=enums.ParseMode.HTML)

    lines = []
    for chat_id in current:
        try:
            chat = await client.get_chat(chat_id)
            channel_title = chat.title or "Private Chat"
            username_line = f" @{chat.username}" if getattr(chat, "username", None) else ""
        except Exception:
            channel_title, username_line = "Unknown", ""
        lines.append(f"• <b>{channel_title}</b>{username_line} — <code>{chat_id}</code>")

    body = "\n".join(lines)
    await message.reply_text(
        f"<blockquote>{E_INFO} <b>Linked Channels ({len(current)})</b>\n\n"
        f"{body}\n\n"
        f"Link another: /set_channel_id\n"
        f"Remove one: <code>/del_channel_id &lt;id&gt;</code> • Remove all: /del_channel_id</blockquote>",
        parse_mode=enums.ParseMode.HTML,
    )
