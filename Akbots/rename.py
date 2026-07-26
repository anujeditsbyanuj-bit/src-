# Akbots - Don't Remove Credit - @AkBots_Official
#
# Auto-Rename engine — ported from File-Rename-4GB-Bot-Auto-Rename-Bot
# (plugins/autorename.py + plugins/prefix_and_suffix.py + the autorename
# branch of plugins/file_rename.py), simplified to fit Akbots' straight
# "receive media -> process -> upload" style instead of that bot's
# multi-step button/queue flow (no media-type picker, no TMDb-confirm
# card, no trim/subtitle detour — those are separate features that can be
# layered on top later if wanted).
#
# Flow: user sets a rename template (and/or prefix/suffix) once. After
# that, any document/video/audio they send is automatically renamed
# (template placeholders + prefix/suffix + their existing delete/replace
# word lists from Akbots/words.py) and re-uploaded — reusing
# Akbots/direct_utils.upload_file for the actual upload, so it gets the
# same saved thumbnail/caption, progress bar, and >1.9GB auto-splitting
# as every other downloader in this bot for free.

import os
import time
import shutil
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from database.db import db

from Akbots.autodetect import apply_autorename_template, sanitize_filename
from Akbots.direct_utils import upload_file, format_progress, fmt_bytes, _status_edit

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN   = '<emoji id=5447644880824181073>⚠️</emoji>'
E_PENCIL = '<emoji id=5395444784611480792>✏️</emoji>'
E_TRASH  = '<emoji id=5260293700088511294>🗑</emoji>'
E_TIP    = '<emoji id=5422439311196834318>💡</emoji>'
E_BOLT   = '⚡️'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

AUTORENAME_HELP = (
    f"<blockquote>{E_PENCIL} <b>Auto-Rename Template</b>\n\n"
    "Set a template to automatically rename your files as you send them.\n\n"
    "➢ <code>/autorename [template]</code> — set the template\n"
    "➢ <code>/see_autorename</code> — view your current template\n"
    "➢ <code>/del_autorename</code> — remove your template\n\n"
    "<b>Supported placeholders:</b>\n"
    "<code>{season}</code> <code>{episode}</code> <code>{chapter}</code> "
    "<code>{quality}</code> <code>{audio}</code> <code>{title}</code> "
    "<code>{year}</code> <code>{source}</code> <code>{codec}</code> "
    "<code>{language}</code> <code>{hdr}</code> <code>{release}</code>\n\n"
    "<b>Examples:</b>\n"
    "<code>/autorename [S{season}E{episode}] My Show [{quality}]</code>\n"
    "<code>/autorename {title} ({year}) {quality} {audio}</code>\n\n"
    f"{E_TIP} Don't add the file extension — it's kept automatically.</blockquote>"
)


# ============================================================
# Template commands
# ============================================================

@Client.on_message(filters.private & filters.command("autorename"))
async def set_autorename_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if len(message.command) == 1:
        return await message.reply_text(AUTORENAME_HELP, parse_mode=enums.ParseMode.HTML)

    template = message.text.split(" ", 1)[1]
    await db.set_autorename(user_id, template)
    await message.reply_text(
        f"<blockquote>{E_CHECK} <b>Auto-rename template saved.</b>\n\n"
        f"<b>Template:</b> <code>{template}</code></blockquote>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("see_autorename"))
async def see_autorename_cmd(client: Client, message: Message):
    template = await db.get_autorename(message.from_user.id)
    if template:
        await message.reply_text(
            f"<blockquote>{E_GEAR} <b>Your auto-rename template:</b>\n\n<code>{template}</code></blockquote>",
            parse_mode=enums.ParseMode.HTML
        )
    else:
        await message.reply_text(
            f"<blockquote>{E_WARN} You don't have an auto-rename template set.\n\n"
            f"Use <code>/autorename</code> to set one.</blockquote>",
            parse_mode=enums.ParseMode.HTML
        )


@Client.on_message(filters.private & filters.command("del_autorename"))
async def del_autorename_cmd(client: Client, message: Message):
    template = await db.get_autorename(message.from_user.id)
    if not template:
        return await message.reply_text(
            f"<b>{E_WARN} You don't have any auto-rename template set.</b>",
            parse_mode=enums.ParseMode.HTML
        )
    await db.set_autorename(message.from_user.id, None)
    await message.reply_text(f"<b>{E_TRASH} Auto-rename template deleted.</b>", parse_mode=enums.ParseMode.HTML)


# ============================================================
# Prefix / Suffix commands
# ============================================================

@Client.on_message(filters.private & filters.command("set_prefix"))
async def set_prefix_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/set_prefix Your Prefix</code>",
            parse_mode=enums.ParseMode.HTML
        )
    prefix = message.text.split(" ", 1)[1]
    await db.set_prefix(message.from_user.id, prefix)
    await message.reply_text(
        f"<b>{E_CHECK} Prefix set:</b> <code>{prefix}</code>", parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("see_prefix"))
async def see_prefix_cmd(client: Client, message: Message):
    prefix = await db.get_prefix(message.from_user.id)
    text = f"<b>{E_GEAR} Your prefix:</b> <code>{prefix}</code>" if prefix else f"<b>{E_WARN} No prefix set.</b>"
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("del_prefix"))
async def del_prefix_cmd(client: Client, message: Message):
    await db.set_prefix(message.from_user.id, None)
    await message.reply_text(f"<b>{E_TRASH} Prefix removed.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("set_suffix"))
async def set_suffix_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_WARN} Usage:</b> <code>/set_suffix Your Suffix</code>",
            parse_mode=enums.ParseMode.HTML
        )
    suffix = message.text.split(" ", 1)[1]
    await db.set_suffix(message.from_user.id, suffix)
    await message.reply_text(
        f"<b>{E_CHECK} Suffix set:</b> <code>{suffix}</code>", parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("see_suffix"))
async def see_suffix_cmd(client: Client, message: Message):
    suffix = await db.get_suffix(message.from_user.id)
    text = f"<b>{E_GEAR} Your suffix:</b> <code>{suffix}</code>" if suffix else f"<b>{E_WARN} No suffix set.</b>"
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("del_suffix"))
async def del_suffix_cmd(client: Client, message: Message):
    await db.set_suffix(message.from_user.id, None)
    await message.reply_text(f"<b>{E_TRASH} Suffix removed.</b>", parse_mode=enums.ParseMode.HTML)


# ============================================================
# Incoming media handler
# ============================================================

@Client.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def auto_rename_incoming_media(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    from Akbots.forcesub import is_subscribed, send_force_sub_prompt
    if not await is_subscribed(client, user_id):
        return await send_force_sub_prompt(client, message)

    template = await db.get_autorename(user_id)
    prefix = await db.get_prefix(user_id)
    suffix = await db.get_suffix(user_id)

    # Nothing configured -> don't touch this message at all, leave it for
    # any other plugin (or the user just archiving files in Saved Messages).
    if not (template or prefix or suffix):
        return

    media = message.document or message.video or message.audio
    original_name = media.file_name or (
        f"video_{message.id}.mp4" if message.video else
        f"audio_{message.id}.mp3" if message.audio else
        f"file_{message.id}"
    )

    if template:
        new_name = await apply_autorename_template(original_name, template)
    else:
        new_name = original_name

    base, ext = os.path.splitext(new_name)
    if prefix:
        base = f"{prefix} {base}"
    if suffix:
        base = f"{base} {suffix}"
    new_name = base + ext

    delete_words = await db.get_delete_words(user_id)
    replace_words = await db.get_replace_words(user_id)
    for w in (delete_words or []):
        if w:
            new_name = new_name.replace(w, "")
    for target, repl in (replace_words or {}).items():
        new_name = new_name.replace(target, repl)
    new_name = sanitize_filename(new_name)

    status = await message.reply_text(
        f"<blockquote>{E_BOLT} <b>Auto-Rename Applied</b>\n\n"
        f"<b>Old:</b> <code>{original_name}</code>\n"
        f"<b>New:</b> <code>{new_name}</code>\n\n"
        f"{E_ROCKET} <b>Downloading...</b></blockquote>",
        reply_to_message_id=message.id,
        parse_mode=enums.ParseMode.HTML
    )

    temp_dir = os.path.join("downloads", "rename", f"{user_id}_{message.id}")
    os.makedirs(temp_dir, exist_ok=True)
    dest_path = os.path.join(temp_dir, new_name)

    state = {"last_edit": 0.0, "start": time.monotonic()}

    async def _download_progress(current, total):
        now = time.monotonic()
        finished = total and current >= total
        if not finished and now - state["last_edit"] < 3:
            return
        state["last_edit"] = now
        elapsed = now - state["start"]
        speed = current / elapsed if elapsed > 0 else 0
        pct = (current * 100 / total) if total else 0
        eta = ((total - current) / speed) if (total and speed > 0) else None
        await _status_edit(status, format_progress(
            pct, speed_bps=speed, done_bytes=current, total_bytes=total or None,
            elapsed_secs=elapsed, eta_secs=eta, title="Downloading...",
            file_name=new_name, duration=getattr(media, "duration", None)
        ))

    try:
        path = await client.download_media(message, file_name=dest_path, progress=_download_progress)
    except Exception as e:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        return await status.edit_text(
            f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    if not path:
        return await status.edit_text(f"<b>{E_CROSS} Download failed.</b>", parse_mode=enums.ParseMode.HTML)

    custom_caption = await db.get_caption(user_id)
    if custom_caption:
        try:
            file_size = os.path.getsize(path)
        except OSError:
            file_size = 0
        try:
            caption = custom_caption.format(filename=new_name, size=fmt_bytes(file_size))
        except (KeyError, IndexError):
            caption = custom_caption  # user's template used an unsupported placeholder — send as-is
    else:
        caption = f"<b>{new_name}</b>"

    await upload_file(client, message, path, status, caption, file_name=new_name, duration=getattr(media, "duration", None))

    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass
