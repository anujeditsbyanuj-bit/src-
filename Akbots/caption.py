from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_PENCIL = '<emoji id=5395444784611480792>✏️</emoji>'
E_TRASH  = '<emoji id=5260293700088511294>🗑</emoji>'
E_TIP    = '<emoji id=5422439311196834318>💡</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

@Client.on_message(filters.command("set_caption") & filters.private)
async def set_caption(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<blockquote>{E_WARN} <b>Usage Error</b>\n\n"
            f"{E_TIP} Please provide the caption text after the command.\n\n"
            f"{E_PENCIL} <b>Correct Format:</b>\n"
            f"<code>/set_caption Your Caption Here</code>\n\n"
            f"{E_INFO} <b>Supported Placeholders:</b>\n"
            f"• <code>{{filename}}</code> : Original File Name\n"
            f"• <code>{{size}}</code> : File Size\n\n"
            f"<i>{E_GEAR} Example: /set_caption File: {{filename}} | Size: {{size}}</i></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    caption = message.text.split(" ", 1)[1].strip()
    await db.set_caption(user_id, caption)
    await message.reply_text(
        f"<blockquote>{E_CHECK} <b>Custom Caption Saved!</b>\n\n"
        f"{E_PENCIL} <b>Preview:</b>\n<code>{caption}</code>\n\n"
        f"<i>{E_INFO} This caption will be applied to your future downloads.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("see_caption") & filters.private)
async def see_caption(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    caption = await db.get_caption(user_id)
    if caption:
        await message.reply_text(
            f"<blockquote>{E_PENCIL} <b>Your Custom Caption</b>\n\n"
            f"<code>{caption}</code>\n\n"
            f"<i>{E_TRASH} To delete this, use /del_caption</i></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    else:
        await message.reply_text(
            f"<blockquote>{E_CROSS} <b>No Caption Set</b>\n\n"
            f"{E_INFO} You are currently using the default bot caption.\n"
            f"<i>{E_TIP} Use /set_caption to customize it.</i></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

@Client.on_message(filters.command("del_caption") & filters.private)
async def del_caption(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    caption = await db.get_caption(user_id)
    if not caption:
        return await message.reply_text(
            f"<b>{E_WARN} No Caption Found.</b>\n<i>You don't have a custom caption set.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    await db.del_caption(user_id)
    await message.reply_text(
        f"<blockquote>{E_TRASH} <b>Custom Caption Removed</b>\n\n"
        f"<i>{E_INFO} Your uploads will now use the default bot caption.</i></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )
