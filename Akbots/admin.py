# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official

from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db
from config import ADMINS, DB_URI

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_STOP  = '<emoji id=5260293700088511294>⛔️</emoji>'
E_BOLT  = '<emoji id=5456140674028019486>⚡️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'

@Client.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/ban user_id</code>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id = int(message.command[1])
        await db.ban_user(user_id)
        await message.reply_text(
            f"<b>{E_STOP} User <code>{user_id}</code> Banned Successfully.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(
            f"<b>{E_CROSS} Error banning user:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML
        )

@Client.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/unban user_id</code>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id = int(message.command[1])
        await db.unban_user(user_id)
        await message.reply_text(
            f"<b>{E_CHECK} User <code>{user_id}</code> Unbanned Successfully.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(
            f"<b>{E_CROSS} Error unbanning user:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML
        )

@Client.on_message(filters.command("set_dump") & filters.user(ADMINS))
async def set_dump(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/set_dump user_id chat_id</code>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id = int(message.command[1])
        chat_id = int(message.command[2])
        await db.set_dump_chat(user_id, chat_id)
        await message.reply_text(
            f"<b>{E_CHECK} Dump chat set for user <code>{user_id}</code>.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await message.reply_text(
            f"<b>{E_CROSS} Error:</b> <code>{e}</code>",
            parse_mode=enums.ParseMode.HTML
        )

@Client.on_message(filters.command("dblink") & filters.user(ADMINS))
async def dblink(client: Client, message: Message):
    await message.reply_text(
        f"<b>{E_BOLT} DB URI:</b> <code>{DB_URI}</code>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command(["add_unsubscribe", "del_unsubscribe"]) & filters.user(ADMINS))
async def manage_force_subscribe(client: Client, message: Message):
    from config import FORCE_SUB_CHANNEL
    status = f"<code>{FORCE_SUB_CHANNEL}</code>" if FORCE_SUB_CHANNEL else "<i>not set</i>"
    await message.reply_text(
        f"<b>{E_GEAR} Force Subscribe is configured via the FORCE_SUB_CHANNEL environment "
        f"variable/secret</b> (channel ID or @username), not a runtime command.\n\n"
        f"<b>Current value:</b> {status}\n"
        f"Set it in Replit Secrets and restart the bot to enable/disable this feature.",
        parse_mode=enums.ParseMode.HTML
    )
