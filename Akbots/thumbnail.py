from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_IMAGE  = '<emoji id=5395444784611480792>🖼</emoji>'
E_TRASH  = '<emoji id=5260293700088511294>🗑</emoji>'
E_TIP    = '<emoji id=5422439311196834318>💡</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GREEN  = '<emoji id=5416081784641168838>🟢</emoji>'
E_RED    = '<emoji id=5411225014148014586>🔴</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

@Client.on_message(filters.command("set_thumb") & filters.private)
async def set_custom_thumbnail(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply_text(
            f"<blockquote>{E_IMAGE} <b>Set Custom Thumbnail</b>\n\n"
            f"{E_TIP} Reply to any photo with /set_thumb to use it as your default thumbnail.\n\n"
            f"{E_INFO} <b>Usage:</b> Reply to a photo → <code>/set_thumb</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    file_id = message.reply_to_message.photo.file_id
    await db.set_thumbnail(user_id, file_id)
    await message.reply_photo(
        photo=file_id,
        caption=(
            f"<b>{E_CHECK} Custom Thumbnail Set Successfully!</b>\n\n"
            f"<i>{E_IMAGE} This thumbnail will be used for all your future uploads.</i>\n"
            f"<i>{E_INFO} Use /view_thumb to preview • /del_thumb to remove</i>"
        ),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command(["view_thumb", "see_thumb"]) & filters.private)
async def view_custom_thumbnail(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    thumb_id = await db.get_thumbnail(user_id)
    if thumb_id:
        try:
            await message.reply_photo(
                photo=thumb_id,
                caption=(
                    f"<b>{E_IMAGE} Your Current Custom Thumbnail</b>\n\n"
                    f"<i>{E_INFO} This is applied to all uploads.</i>\n"
                    f"<i>{E_TRASH} To delete, use /del_thumb</i>"
                ),
                parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            await message.reply_text(
                f"<b>{E_CROSS} Error loading thumbnail:</b> {e}\n<i>Please set a new one.</i>",
                parse_mode=enums.ParseMode.HTML
            )
    else:
        await message.reply_text(
            f"<blockquote>{E_CROSS} <b>No Custom Thumbnail Found</b>\n\n"
            f"{E_TIP} Reply to a photo with /set_thumb to add one.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

@Client.on_message(filters.command(["del_thumb", "delete_thumb"]) & filters.private)
async def delete_custom_thumbnail(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    thumb_id = await db.get_thumbnail(user_id)
    if not thumb_id:
        return await message.reply_text(
            f"<b>{E_WARN} You don't have a custom thumbnail set.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    await db.del_thumbnail(user_id)
    await message.reply_text(
        f"<blockquote>{E_TRASH} <b>Custom Thumbnail Deleted</b>\n\n"
        f"{E_INFO} Your uploads will now use the default video/file thumbnail.</blockquote>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("thumb_mode") & filters.private)
async def thumbnail_status(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    thumb_id = await db.get_thumbnail(user_id)
    if thumb_id:
        status = f"{E_GREEN} <b>Custom Thumbnail Active</b>"
        extra  = f"<i>{E_IMAGE} Use /view_thumb to preview</i>"
    else:
        status = f"{E_RED} <b>No Custom Thumbnail</b>"
        extra  = f"<i>{E_TIP} Use /set_thumb (reply to photo) to enable</i>"
    await message.reply_text(
        f"<blockquote>{E_GEAR} <b>Thumbnail Status</b>\n\n{status}\n{extra}</blockquote>",
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.command("change_thumb") & filters.private)
async def change_existing_thumbnail(client: Client, message: Message):
    """Reply to an already-sent video/document with /change_thumb to swap
    just its thumbnail using the user's saved custom thumbnail. Uses the
    original file_id directly — no re-download of the media itself."""
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    reply = message.reply_to_message
    if not reply or not (reply.video or reply.document):
        return await message.reply_text(
            f"<blockquote>{E_IMAGE} <b>Change Thumbnail</b>\n\n"
            f"{E_TIP} Reply to a video or document with /change_thumb to swap its thumbnail.\n\n"
            f"{E_INFO} <b>Usage:</b> Reply to a video/document → <code>/change_thumb</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )

    custom_thumb = await db.get_thumbnail(user_id)
    if not custom_thumb:
        return await message.reply_text(
            f"<b>{E_WARN} No custom thumbnail set.</b>\n"
            f"<i>{E_TIP} Reply to a photo with /set_thumb first, then try /change_thumb again.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(
        f"<b>{E_GEAR} Changing thumbnail…</b>", parse_mode=enums.ParseMode.HTML
    )
    try:
        caption = reply.caption.html if reply.caption else ""
        if reply.video:
            await client.send_video(
                chat_id=message.chat.id,
                video=reply.video.file_id,
                thumb=custom_thumb,
                caption=caption,
                supports_streaming=True,
                reply_to_message_id=reply.id,
                parse_mode=enums.ParseMode.HTML
            )
        else:
            await client.send_document(
                chat_id=message.chat.id,
                document=reply.document.file_id,
                thumb=custom_thumb,
                caption=caption,
                reply_to_message_id=reply.id,
                parse_mode=enums.ParseMode.HTML
            )
        await status.edit_text(
            f"<b>{E_CHECK} Thumbnail changed successfully!</b>", parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await status.edit_text(
            f"<b>{E_CROSS} Failed to change thumbnail:</b> {e}", parse_mode=enums.ParseMode.HTML
        )
