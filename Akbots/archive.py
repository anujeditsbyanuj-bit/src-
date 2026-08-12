# Akbots - Don't Remove Credit - @AkBots_Official
#
# Archive tools — /unzip and /zip.
#
# /unzip  — reply to any archive (zip/rar/7z/tar/gz/bz2/xz/...) and the bot
#           extracts it with the `7z` CLI (same approach as the standalone
#           Unzipper-Bot project) and uploads every extracted file back,
#           reusing direct_utils.upload_file for the actual send (so it
#           gets the same progress bar / >1.9GB auto-split as every other
#           downloader here). Password-protected archives: /unzip <password>
#           as a reply.
#
# /zip    — batch archive creation (ported from Zip-Maker-Bot's zipmaker
#           module). /zip starts a collection session; every document/
#           video/audio/photo the user sends afterwards gets downloaded and
#           queued. /zipname sets a custom archive name, /zippass sets a
#           password (via pyzipper — falls back to a plain zip if pyzipper
#           isn't installed), /zipencryption picks the ZIP encryption mode
#           (aes256/aes128/zipcrypto), /zipformat picks the archive format
#           (zip/7z/tar/tar.gz/tar.bz2/rar), /zipfolder organizes queued
#           files into a folder structure inside the archive (ported from
#           Zip-Maker-Bot's folder-structure module — either set a default
#           folder for everything sent from now on, or override per file
#           via caption, e.g. "[Photos] pic.jpg" or "Docs/Work/file.pdf"),
#           /zipfiles lists everything queued so far (name/folder/size),
#           /donezip builds + uploads the archive, /zipcancel aborts and
#           wipes the temp files.
#
# Needs `7z` on PATH for /unzip (p7zip-full, + p7zip-rar or unrar for RAR
# support — see Dockerfile). /zip itself has no system dependency for
# zip/tar/tar.gz/tar.bz2 (zipfile + tarfile, both stdlib, optionally
# pyzipper for zip passwords) — the "7z" format uses py7zr, a pure-Python
# library (no external `7z`/`p7zip` binary needed, unlike /unzip's
# extraction path), so it works even on hosts without p7zip installed.
# Only the "rar" format needs the proprietary `rar` CLI (WinRAR's
# rar/rar-nonfree package) on PATH, since unlike 7z, RAR *creation* isn't
# something any open-source tool can do; /zipformat rar clearly reports
# when that binary isn't installed instead of silently producing a broken
# file.
#
# ZIP password protection supports three encryption modes via pyzipper:
#   aes256    — AES-256, strongest, default.
#   aes128    — AES-128, still strong, marginally faster.
#   zipcrypto — the original "legacy" ZIP encryption. Weak (crackable) but
#               kept for compatibility with old unzip tools that don't
#               understand WinZip's AES extension.
# 7z archives always get 7-Zip's own strong AES-256 when a password is set
# (that's the only mode py7zr/7-Zip supports), so /zipencryption only
# applies when the format is zip.
#
# Every queued file carries its own in-archive path (arcname) rather than
# just a flat basename, computed once at collection time from the caption
# / current /zipfolder setting; that arcname is what every _build_* helper
# writes under, so the folder structure shows up in zip/7z/tar/rar alike.

import os
import re
import time
import shutil
import asyncio
from Akbots.direct_utils import safe_edit
import zipfile
import tarfile
from pyrogram import Client, filters, enums, ContinuePropagation
from pyrogram.types import Message, InlineKeyboardMarkup
from database.db import db

from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

from Akbots.direct_utils import (
    make_output_folder, safe_filename, upload_file, fmt_bytes, draw_bar,
    E_CHECK, E_CROSS, E_INFO, E_BOLT, E_ROCKET, wait_for_reply, make_download_progress
)
from Akbots import task_manager

try:
    import pyzipper
    PYZIPPER_AVAILABLE = True
except ImportError:
    PYZIPPER_AVAILABLE = False

try:
    import py7zr
    PY7ZR_AVAILABLE = True
except ImportError:
    PY7ZR_AVAILABLE = False

E_PACK   = '📦'
E_WARN   = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_TRASH  = '<tg-emoji emoji-id="5260293700088511294">🗑</tg-emoji>'
E_FILE   = '📄'

# format key -> (file extension, human label)
ARCHIVE_FORMATS = {
    "zip":     (".zip",     "ZIP"),
    "7z":      (".7z",      "7Z"),
    "tar":     (".tar",     "TAR (uncompressed)"),
    "tar.gz":  (".tar.gz",  "TAR + gzip"),
    "tar.bz2": (".tar.bz2", "TAR + bzip2"),
    "rar":     (".rar",     "RAR"),
}

# formats that support a password at all
ENCRYPTABLE_FORMATS = ("zip", "7z")

# ZIP-only encryption mode key -> human label. 7z always uses its own
# AES-256 when a password is set, so this table only applies to fmt=="zip".
ZIP_ENCRYPTION_MODES = {
    "aes256":    "AES-256 (strong, default)",
    "aes128":    "AES-128 (strong)",
    "zipcrypto": "ZipCrypto (legacy, weak — max compatibility with old tools)",
}

# user_id -> {"paths": [(disk_path, arcname), ...], "name": str|None,
#             "password": str|None, "format": str, "encryption": str,
#             "current_folder": str, "started": float}
# "paths" entries are (disk_path, arcname) tuples rather than bare paths so
# every _build_* helper writes each file under its intended in-archive
# folder path (arcname) instead of a flat basename.
_ZIP_SESSIONS = {}


def _media_of(message: Message):
    return message.document or message.video or message.audio or message.photo or message.voice


def _media_name(message: Message, fallback: str) -> str:
    media = _media_of(message)
    name = getattr(media, "file_name", None)
    if name:
        return safe_filename(name, fallback)
    ext = ".jpg" if message.photo else ".ogg" if message.voice else ".bin"
    return fallback + ext


# ============================================================
# /zipfolder — folder-structure helpers (Folder Manager)
# ============================================================

def _sanitize_folder_component(name: str) -> str:
    name = re.sub(r'[<>:"|?*\\]', '', name)
    name = name.strip('. ')
    return name[:50] if name else "folder"


def _sanitize_folder_path(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p.strip()]
    parts = [_sanitize_folder_component(p) for p in parts]
    return "/".join(p for p in parts if p)


def _parse_folder_from_caption(caption: str) -> str:
    """Pull a folder path out of a file's caption, if any. Supports:
       "[Folder/Sub] filename.ext", "{Folder/Sub} filename.ext",
       "Folder/Sub/filename.ext", or a bare "Folder/Sub/" path — same
       formats as Zip-Maker-Bot's folder-structure module. Returns ""
       (root) if the caption doesn't encode a folder."""
    if not caption:
        return ""
    caption = caption.strip()

    bracket = re.match(r'\[([^\]]+)\]', caption)
    if bracket:
        return _sanitize_folder_path(bracket.group(1))
    brace = re.match(r'\{([^}]+)\}', caption)
    if brace:
        return _sanitize_folder_path(brace.group(1))

    if "/" in caption:
        parts = caption.strip("/").split("/")
        if len(parts) > 1:
            # last segment with a dot looks like a filename -> drop it,
            # otherwise treat the whole caption as a folder path
            folder_path = "/".join(parts[:-1]) if "." in parts[-1] else "/".join(parts)
            return _sanitize_folder_path(folder_path)
    return ""


def _folder_tree_summary(session, limit_per_folder: int = 5) -> str:
    folders = {}
    for _, arcname in session["paths"]:
        folder = os.path.dirname(arcname)
        folders.setdefault(folder, []).append(os.path.basename(arcname))
    if not folders:
        return ""
    lines = ["\n\n<b>ǫᴜᴇᴜᴇᴅ sᴛʀᴜᴄᴛᴜʀᴇ:</b>"]
    for folder, files in folders.items():
        lines.append(f"📁 <code>{folder or '(root)'}/</code>")
        for f in files[:limit_per_folder]:
            lines.append(f"   └ <code>{f}</code>")
        if len(files) > limit_per_folder:
            lines.append(f"   └ …and {len(files) - limit_per_folder} more")
    return "\n".join(lines)


# ============================================================
# /unzip — extraction
# ============================================================

async def _run_7z_extract(archive_path: str, out_dir: str, password: str = "", status=None):
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["7z", "x", f"-o{out_dir}", "-y", "-bb1"]  # -bb1: log each extracted file's name
    cmd.append(f"-p{password}" if password else "-p-")  # -p- => no prompt, empty pw if none given
    cmd.append(archive_path)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    out_lines = []
    count = 0
    last_edit = 0.0

    async def _read_stdout():
        nonlocal count, last_edit
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip("\n")
            out_lines.append(text)
            if text.startswith("- "):
                count += 1
                if status:
                    now = time.time()
                    if now - last_edit >= 1.5:
                        last_edit = now
                        await safe_edit(
                            status.edit_text,
                            f"<b>🗜️ Extracting...</b>\n"
                            f"📦 {count} file(s) extracted so far\n"
                            f"📄 <code>{text[2:]}</code>",
                            parse_mode=enums.ParseMode.HTML,
                        )

    stderr_task = asyncio.ensure_future(proc.stderr.read())
    await _read_stdout()
    err = await stderr_task
    await proc.wait()
    out = "\n".join(out_lines).encode("utf-8")
    # 7z exit codes: 0=OK, 1=Warning (still usable), 2+=fatal
    return proc.returncode, out.decode("utf-8", "replace"), (err or b"").decode("utf-8", "replace")


@Client.on_message(filters.private & filters.command("unzip"))
async def unzip_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    reply = message.reply_to_message
    if not reply or not _media_of(reply):
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> reply to an archive file (zip/rar/7z/tar/gz/...) with "
            f"<code>/unzip</code> — add a password after it if the archive needs one, e.g. "
            f"<code>/unzip mypassword</code>.",
            parse_mode=enums.ParseMode.HTML
        )

    password = message.text.split(" ", 1)[1].strip() if len(message.command) > 1 else ""

    if shutil.which("7z") is None:
        return await message.reply_text(
            f"<b>{E_CROSS} The <code>7z</code> tool isn't installed on this server.</b> "
            f"Install <code>p7zip-full</code> (and <code>p7zip-rar</code>/<code>unrar</code> for RAR) "
            f"and redeploy.",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_INFO} Downloading archive...</b>", parse_mode=enums.ParseMode.HTML)

    session_dir = os.path.join(make_output_folder("unzip"), f"{user_id}_{message.id}")
    os.makedirs(session_dir, exist_ok=True)
    arc_name = _media_name(reply, "archive")
    arc_path = os.path.join(session_dir, arc_name)
    out_dir = os.path.join(session_dir, "extracted")

    async def _job():
        try:
            await reply.download(file_name=arc_path, progress=make_download_progress(status, file_name=arc_name))
            await safe_edit(status.edit_text, f"<b>{E_BOLT} Extracting...</b>", parse_mode=enums.ParseMode.HTML)

            code, out, err = await _run_7z_extract(arc_path, out_dir, password, status=status)

            # Archive turned out to be password-protected but no password was
            # given upfront (/unzip with no argument) — ask for it inline
            # instead of just failing, same flow as Unzipper-Bot's
            # ask_password step (send prompt, client.listen() for the reply,
            # retry extraction once with what the user sends).
            needs_password = not password and code not in (0, 1) and (
                "Wrong password" in err or "Wrong password" in out or
                "Can not open encrypted archive" in err
            )
            if needs_password:
                try:
                    await safe_edit(status.edit_text, 
                        f"<b>{E_INFO} This archive is password-protected. Send the password:</b>",
                        parse_mode=enums.ParseMode.HTML
                    )
                    pwd_msg = await wait_for_reply(
                        client, chat_id=message.chat.id, user_id=user_id, timeout=60
                    )
                    entered_password = (pwd_msg.text or "").strip()
                    try:
                        await pwd_msg.delete()
                    except Exception:
                        pass
                    await safe_edit(status.edit_text, f"<b>{E_BOLT} Extracting...</b>", parse_mode=enums.ParseMode.HTML)
                    code, out, err = await _run_7z_extract(arc_path, out_dir, entered_password, status=status)
                except asyncio.TimeoutError:
                    return await safe_edit(status.edit_text, 
                        f"<b>{E_CROSS} Timed out waiting for the password.</b> Try again with "
                        f"<code>/unzip &lt;password&gt;</code> as a reply.",
                        parse_mode=enums.ParseMode.HTML
                    )

            if code not in (0, 1):
                hint = ""
                if "Wrong password" in err or "Wrong password" in out:
                    hint = " (looks like a wrong/missing password — retry with <code>/unzip &lt;password&gt;</code> as a reply)"
                return await safe_edit(status.edit_text, 
                    f"<b>{E_CROSS} Extraction failed.</b>{hint}\n<code>{(err or out)[-500:]}</code>",
                    parse_mode=enums.ParseMode.HTML
                )

            files = []
            for root, _, names in os.walk(out_dir):
                for n in names:
                    files.append(os.path.join(root, n))

            if not files:
                return await safe_edit(status.edit_text, 
                    f"<b>{E_WARN} Archive extracted but no files were found inside.</b>",
                    parse_mode=enums.ParseMode.HTML
                )

            await safe_edit(status.edit_text, 
                f"<b>{E_ROCKET} Extracted {len(files)} file(s) — uploading...</b>",
                parse_mode=enums.ParseMode.HTML
            )
            for i, fpath in enumerate(files, start=1):
                rel = os.path.relpath(fpath, out_dir)
                cap = f"<b>{E_FILE} {rel}</b>\n<i>({i}/{len(files)} from {arc_name})</i>"
                up_status = await message.reply_text(f"<b>{E_ROCKET} Uploading {rel}...</b>", parse_mode=enums.ParseMode.HTML)
                try:
                    await upload_file(client, message, fpath, up_status, cap, file_name=os.path.basename(fpath))
                except Exception as e:
                    await safe_edit(up_status.edit_text, f"<b>{E_CROSS} Failed to upload {rel}:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

            await safe_edit(status.edit_text, f"<b>{E_CHECK} Done — all files sent.</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            await safe_edit(status.edit_text, f"<b>{E_CROSS} Error:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        finally:
            shutil.rmtree(session_dir, ignore_errors=True)

    task = asyncio.ensure_future(_job())
    task_id = task_manager.register(user_id, task, f"Unzip: {arc_name}")
    task.add_done_callback(lambda t: task_manager.unregister(user_id, task_id))


# ============================================================
# /zip — batch creation
# ============================================================

# ============================================================
# /zipcreate — one-shot zip (no session)
# ============================================================
# Reply to a single video/photo/document/audio/voice with /zipcreate and
# the bot downloads that one file, zips it right away, and uploads the
# archive back — no /zip -> send files -> /donezip session needed.
#
# Password: either give it straight away as an argument
# (/zipcreate mypassword123, unchanged for power users who already know
# this), or — if no argument is given — the bot now asks with two buttons
# ("🔐 Add Password" / "⏭️ Skip (No Password)"), same choice-first UX as
# reference bots like RoxyBasicNeedBot's /create flow, instead of silently
# always making an unprotected zip when nothing was typed.
_ZIPCREATE_PENDING = {}  # user_id -> {"reply": Message, "message": Message, "fname": str}


async def _zipcreate_run(client: Client, message: Message, reply: Message, password: str,
                          fname: str, status: Message):
    user_id = message.from_user.id
    staging_dir = make_output_folder(f"zipcreate/{user_id}")
    dest = os.path.join(staging_dir, fname)
    archive_path = None
    try:
        await reply.download(file_name=dest, progress=make_download_progress(status, file_name=fname))
        await safe_edit(status.edit_text, f"<b>{E_BOLT} Building ZIP archive...</b>", parse_mode=enums.ParseMode.HTML)

        archive_name = os.path.splitext(fname)[0] + ".zip"
        archive_dir = make_output_folder(f"zip_out/{user_id}")
        archive_path = os.path.join(archive_dir, archive_name)

        await _build_zip(archive_path, [(dest, fname)], password, "aes256", status=status)

        size = os.path.getsize(archive_path)
        note = " (AES-256 encrypted)" if password and PYZIPPER_AVAILABLE else ""
        await safe_edit(status.edit_text, f"<b>{E_ROCKET} Uploading archive ({fmt_bytes(size)})...</b>", parse_mode=enums.ParseMode.HTML)
        await upload_file(
            client, message, archive_path, status,
            f"<b>{E_PACK} {archive_name}</b>{note}\n"
            f"<b>sᴏᴜʀᴄᴇ:</b> {fname} | <b>sɪᴢᴇ:</b> {fmt_bytes(size)}",
            file_name=archive_name
        )
    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to create archive:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
        try:
            if archive_path and os.path.exists(archive_path):
                os.remove(archive_path)
        except OSError:
            pass


@Client.on_message(filters.private & filters.command("zipcreate"))
async def zip_create_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    reply = message.reply_to_message
    media = _media_of(reply) if reply else None
    if not media:
        return await message.reply_text(
            f"<b>{E_WARN} Reply to a video, photo, document, audio, or voice file with</b> <code>/zipcreate</code>"
            f"<b> ᴛᴏ ᴢɪᴘ ɪᴛ ɪɴsᴛᴀɴᴛʟʏ.</b>\n"
            f"<i>Optional password:</i> <code>/zipcreate mypassword</code>\n\n"
            f"<i>Need multiple files in one archive? Use /zip instead.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    if user_id in _ZIP_SESSIONS:
        return await message.reply_text(
            f"<b>{E_WARN} You have an active /zip session.</b> Finish it with /donezip or /zipcancel first.",
            parse_mode=enums.ParseMode.HTML
        )

    inline_password = message.text.split(None, 1)[1].strip() if len(message.command) > 1 else ""
    fname = _media_name(reply, "file")

    if inline_password:
        status = await message.reply_text(f"<b>{E_BOLT} Downloading file...</b>", parse_mode=enums.ParseMode.HTML)
        task = asyncio.ensure_future(_zipcreate_run(client, message, reply, inline_password, fname, status))
        task_id = task_manager.register(user_id, task, f"Zip create: {fname}")
        task.add_done_callback(lambda t: task_manager.unregister(user_id, task_id))
        return

    # No password given upfront — ask first instead of silently making an
    # unprotected zip.
    _ZIPCREATE_PENDING[user_id] = {"reply": reply, "message": message, "fname": fname}
    keyboard = InlineKeyboardMarkup([
        [make_button("🔐 ᴀᴅᴅ ᴘᴀssᴡᴏʀᴅ", callback_data="zcpwd_yes", style=_BS.PRIMARY if _BS else None)],
        [make_button("⏭️ sᴋɪᴘ (ɴᴏ ᴘᴀssᴡᴏʀᴅ)", callback_data="zcpwd_no", style=_BS.PRIMARY if _BS else None)],
        [make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data="zcpwd_cancel", style=_BS.DANGER if _BS else None)],
    ])
    await message.reply_text(
        f"<b>{E_INFO} Password-protect this ZIP?</b>\n\n"
        f"<i>File:</i> <code>{fname}</code>",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=keyboard
    )


@Client.on_callback_query(filters.regex(r"^zcpwd_(yes|no|cancel)$"))
async def zip_create_password_callback(client: Client, callback_query):
    user_id = callback_query.from_user.id
    pending = _ZIPCREATE_PENDING.get(user_id)
    if not pending:
        return await callback_query.answer("Session expired — run /zipcreate again.", show_alert=True)

    data = callback_query.data

    if data == "zcpwd_cancel":
        _ZIPCREATE_PENDING.pop(user_id, None)
        await callback_query.answer("Cancelled")
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)

    reply = pending["reply"]
    orig_message = pending["message"]
    fname = pending["fname"]
    status = callback_query.message

    if data == "zcpwd_no":
        _ZIPCREATE_PENDING.pop(user_id, None)
        await callback_query.answer("Creating ZIP...")
        await safe_edit(status.edit_text, f"<b>{E_BOLT} Downloading file...</b>", parse_mode=enums.ParseMode.HTML)
        task = asyncio.ensure_future(_zipcreate_run(client, orig_message, reply, "", fname, status))
        task_id = task_manager.register(user_id, task, f"Zip create: {fname}")
        task.add_done_callback(lambda t: task_manager.unregister(user_id, task_id))
        return

    # zcpwd_yes
    await callback_query.answer()
    await safe_edit(
        status.edit_text,
        f"<b>{E_INFO} Send the password for this ZIP</b> (minimum 4 characters).\n\n"
        f"<i>Use /zipcancel to abort instead.</i>",
        parse_mode=enums.ParseMode.HTML
    )
    try:
        pwd_msg = await wait_for_reply(client, chat_id=orig_message.chat.id, user_id=user_id, timeout=60)
    except asyncio.TimeoutError:
        _ZIPCREATE_PENDING.pop(user_id, None)
        return await safe_edit(
            status.edit_text,
            f"<b>{E_CROSS} Timed out waiting for the password.</b> Run <code>/zipcreate</code> again, "
            f"or use <code>/zipcreate mypassword</code> directly next time.",
            parse_mode=enums.ParseMode.HTML
        )

    _ZIPCREATE_PENDING.pop(user_id, None)
    password = (pwd_msg.text or "").strip()
    try:
        await pwd_msg.delete()
    except Exception:
        pass

    if password.startswith("/"):
        return await safe_edit(
            status.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML
        )

    if len(password) < 4:
        return await safe_edit(
            status.edit_text,
            f"<b>{E_CROSS} Password too short</b> (minimum 4 characters). Run <code>/zipcreate</code> again.",
            parse_mode=enums.ParseMode.HTML
        )

    await safe_edit(status.edit_text, f"<b>{E_BOLT} Downloading file...</b>", parse_mode=enums.ParseMode.HTML)
    task = asyncio.ensure_future(_zipcreate_run(client, orig_message, reply, password, fname, status))
    task_id = task_manager.register(user_id, task, f"Zip create: {fname}")
    task.add_done_callback(lambda t: task_manager.unregister(user_id, task_id))


@Client.on_message(filters.private & filters.command("zip"))
async def zip_start_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if user_id in _ZIP_SESSIONS:
        return await message.reply_text(
            f"<b>{E_WARN} You already have a zip session running</b> "
            f"({len(_ZIP_SESSIONS[user_id]['paths'])} file(s) queued).\n"
            f"Send more files, or use <code>/donezip</code> / <code>/zipcancel</code>.",
            parse_mode=enums.ParseMode.HTML
        )

    _ZIP_SESSIONS[user_id] = {
        "paths": [], "name": None, "password": None,
        "format": "zip", "encryption": "aes256", "current_folder": "",
        "started": time.time()
    }
    await message.reply_text(
        f"<b>{E_PACK} Zip session started.</b>\n\n"
        f"Send me the files you want archived (document/video/audio/photo), then:\n"
        f"➢ <code>/zipname mybackup</code> — set archive name (optional)\n"
        f"➢ <code>/zippass secret</code> — password-protect, ZIP/7Z only (optional)\n"
        f"➢ <code>/zipencryption aes256</code> — ZIP encryption mode: aes256/aes128/zipcrypto (optional)\n"
        f"➢ <code>/zipformat 7z</code> — pick zip/7z/tar/tar.gz/tar.bz2/rar (default: zip)\n"
        f"➢ <code>/zipfolder Photos</code> — put files sent from now on inside a folder (optional)\n"
        f"➢ <code>/zipfiles</code> — view everything queued so far\n"
        f"➢ <code>/donezip</code> — build and send the archive\n"
        f"➢ <code>/zipcancel</code> — abort\n\n"
        f"<i>Tip: you can also set a per-file folder via caption, e.g.</i> "
        f"<code>[Photos] pic.jpg</code> <i>or</i> <code>Docs/Work/file.pdf</code>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("zipname"))
async def zip_name_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.get(user_id)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b> Start one with <code>/zip</code>.", parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text(f"<b>{E_INFO} Usage:</b> <code>/zipname mybackup</code>", parse_mode=enums.ParseMode.HTML)
    session["name"] = safe_filename(message.text.split(" ", 1)[1].strip(), "archive")
    ext = ARCHIVE_FORMATS.get(session.get("format", "zip"), ARCHIVE_FORMATS["zip"])[0]
    await message.reply_text(f"<b>{E_CHECK} Archive name set:</b> <code>{session['name']}{ext}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("zipformat"))
async def zip_format_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.get(user_id)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b> Start one with <code>/zip</code>.", parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        opts = ", ".join(f"<code>{k}</code>" for k in ARCHIVE_FORMATS)
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/zipformat tar.gz</code>\nOptions: {opts}",
            parse_mode=enums.ParseMode.HTML
        )
    fmt = message.command[1].strip().lower().lstrip(".")
    if fmt not in ARCHIVE_FORMATS:
        opts = ", ".join(f"<code>{k}</code>" for k in ARCHIVE_FORMATS)
        return await message.reply_text(
            f"<b>{E_CROSS} Unknown format.</b> Options: {opts}",
            parse_mode=enums.ParseMode.HTML
        )
    if fmt == "rar" and shutil.which("rar") is None:
        return await message.reply_text(
            f"<b>{E_WARN} The <code>rar</code> CLI isn't installed on this server</b> — RAR "
            f"creation needs the proprietary WinRAR/rar-nonfree tool (unlike extraction, no "
            f"open-source tool can create .rar files). Install it and redeploy, or pick another "
            f"format.",
            parse_mode=enums.ParseMode.HTML
        )
    if fmt == "7z" and not PY7ZR_AVAILABLE:
        return await message.reply_text(
            f"<b>{E_WARN} <code>py7zr</code> isn't installed on this server</b> — 7z creation "
            f"isn't available. Add <code>py7zr</code> to requirements.txt and redeploy, or pick "
            f"another format.",
            parse_mode=enums.ParseMode.HTML
        )
    if fmt not in ENCRYPTABLE_FORMATS and session.get("password"):
        session["password"] = None
        note = " (any password you set was cleared — only ZIP/7Z support encryption here)"
    else:
        note = ""
    session["format"] = fmt
    label = ARCHIVE_FORMATS[fmt][1]
    await message.reply_text(f"<b>{E_CHECK} Archive format set:</b> {label}{note}", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("zippass"))
async def zip_pass_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.get(user_id)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b> Start one with <code>/zip</code>.", parse_mode=enums.ParseMode.HTML)
    if len(message.command) < 2:
        return await message.reply_text(f"<b>{E_INFO} Usage:</b> <code>/zippass secret</code>", parse_mode=enums.ParseMode.HTML)
    fmt = session.get("format", "zip")
    if fmt == "zip" and not PYZIPPER_AVAILABLE:
        return await message.reply_text(
            f"<b>{E_WARN} <code>pyzipper</code> isn't installed</b> — password-protected zips aren't "
            f"available. Add <code>pyzipper</code> to requirements.txt and redeploy.",
            parse_mode=enums.ParseMode.HTML
        )
    if fmt == "7z" and not PY7ZR_AVAILABLE:
        return await message.reply_text(
            f"<b>{E_WARN} <code>py7zr</code> isn't installed</b> — password-protected 7z isn't "
            f"available. Add <code>py7zr</code> to requirements.txt and redeploy.",
            parse_mode=enums.ParseMode.HTML
        )
    if fmt not in ENCRYPTABLE_FORMATS:
        return await message.reply_text(
            f"<b>{E_WARN} Password-protection is only supported for ZIP and 7Z.</b> "
            f"Run <code>/zipformat zip</code> or <code>/zipformat 7z</code> first.",
            parse_mode=enums.ParseMode.HTML
        )
    session["password"] = message.text.split(" ", 1)[1].strip()
    if fmt == "7z":
        await message.reply_text(f"<b>{E_CHECK} Password set.</b> Archive will use 7-Zip's AES-256 encryption.", parse_mode=enums.ParseMode.HTML)
    else:
        label = ZIP_ENCRYPTION_MODES[session.get("encryption", "aes256")]
        await message.reply_text(
            f"<b>{E_CHECK} Password set.</b> Archive will use {label}.\n"
            f"<i>Change with /zipencryption if needed.</i>",
            parse_mode=enums.ParseMode.HTML
        )


@Client.on_message(filters.private & filters.command("zipencryption"))
async def zip_encryption_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.get(user_id)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b> Start one with <code>/zip</code>.", parse_mode=enums.ParseMode.HTML)
    if session.get("format", "zip") != "zip":
        return await message.reply_text(
            f"<b>{E_WARN} Encryption mode selection only applies to the ZIP format.</b> "
            f"7z always uses 7-Zip's own AES-256 when password-protected.",
            parse_mode=enums.ParseMode.HTML
        )
    if len(message.command) < 2:
        opts = "\n".join(f"➢ <code>{k}</code> — {v}" for k, v in ZIP_ENCRYPTION_MODES.items())
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/zipencryption aes256</code>\n\n{opts}",
            parse_mode=enums.ParseMode.HTML
        )
    mode = message.command[1].strip().lower()
    if mode not in ZIP_ENCRYPTION_MODES:
        opts = ", ".join(f"<code>{k}</code>" for k in ZIP_ENCRYPTION_MODES)
        return await message.reply_text(f"<b>{E_CROSS} Unknown mode.</b> Options: {opts}", parse_mode=enums.ParseMode.HTML)
    if not PYZIPPER_AVAILABLE:
        return await message.reply_text(
            f"<b>{E_WARN} <code>pyzipper</code> isn't installed</b> — encryption mode can't be applied "
            f"without it. Add <code>pyzipper</code> to requirements.txt and redeploy.",
            parse_mode=enums.ParseMode.HTML
        )
    session["encryption"] = mode
    await message.reply_text(f"<b>{E_CHECK} Encryption mode set:</b> {ZIP_ENCRYPTION_MODES[mode]}", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("zipfolder"))
async def zip_folder_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.get(user_id)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b> Start one with <code>/zip</code>.", parse_mode=enums.ParseMode.HTML)

    if len(message.command) < 2:
        current = session.get("current_folder", "")
        tree = _folder_tree_summary(session)
        return await message.reply_text(
            f"<b>{E_INFO} Default folder:</b> <code>{current or '(root)'}</code>\n\n"
            f"<code>/zipfolder Photos/Summer</code> — files sent from now on go inside this folder\n"
            f"<code>/zipfolder off</code> — clear it, back to archive root\n\n"
            f"<i>Per-file override: send a file with caption</i> <code>[Photos] pic.jpg</code> "
            f"<i>or</i> <code>Docs/Work/file.pdf</code> — <i>that beats the default folder for just that file.</i>"
            f"{tree}",
            parse_mode=enums.ParseMode.HTML
        )

    arg = message.text.split(" ", 1)[1].strip()
    if arg.lower() in ("off", "clear", "root", "none"):
        session["current_folder"] = ""
        return await message.reply_text(f"<b>{E_CHECK} Default folder cleared</b> — new files go to the archive root.", parse_mode=enums.ParseMode.HTML)

    folder = _sanitize_folder_path(arg)
    if not folder:
        return await message.reply_text(f"<b>{E_CROSS} Invalid folder name.</b>", parse_mode=enums.ParseMode.HTML)
    session["current_folder"] = folder
    await message.reply_text(
        f"<b>{E_CHECK} Default folder set:</b> <code>{folder}/</code>\n"
        f"<i>Every file you send now goes inside this folder in the archive. "
        f"Use /zipfolder off to go back to root, or override per file via caption.</i>",
        parse_mode=enums.ParseMode.HTML
    )


def _file_type_label(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return "📸 Photo"
    if ext in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
        return "🎥 Video"
    if ext in (".mp3", ".flac", ".wav", ".ogg", ".m4a"):
        return "🎵 Audio"
    if ext == ".pdf":
        return "📄 PDF"
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"):
        return "📦 Archive"
    return "📁 File"


@Client.on_message(filters.private & filters.command("zipfiles"))
async def zip_files_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.get(user_id)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b> Start one with <code>/zip</code>.", parse_mode=enums.ParseMode.HTML)
    entries = session["paths"]
    if not entries:
        return await message.reply_text(
            f"<b>{E_PACK} Your file queue is empty.</b>\n"
            f"<i>Send documents/videos/audio/photos to add them.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    grand_total = sum(os.path.getsize(p) for p, _ in entries if os.path.exists(p))
    # Cap the listing so it can't blow past Telegram's message length on
    # huge sessions — same idea as Zip-Maker-Bot's /files "last 10" cap,
    # just a bit more generous since we're plain text, not buttons.
    show = entries[-20:] if len(entries) > 20 else entries
    offset = len(entries) - len(show)

    lines = []
    for i, (disk_path, arcname) in enumerate(show, start=offset + 1):
        size = os.path.getsize(disk_path) if os.path.exists(disk_path) else 0
        label = _file_type_label(arcname)
        folder = os.path.dirname(arcname)
        loc = f" (<code>{folder}/</code>)" if folder else ""
        lines.append(f"{i}. {label} <code>{os.path.basename(arcname)}</code>{loc} — {fmt_bytes(size)}")

    header = f"<b>{E_PACK} Queued files:</b> {len(entries)} | <b>ᴛᴏᴛᴀʟ sɪᴢᴇ:</b> {fmt_bytes(grand_total)}"
    if len(show) != len(entries):
        header += f"\n<i>(showing last {len(show)} of {len(entries)})</i>"

    await message.reply_text(
        header + "\n\n" + "\n".join(lines) + f"\n\n<i>/donezip to build, /zipcancel to discard.</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.command("zipcancel"))
async def zip_cancel_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.pop(user_id, None)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b>", parse_mode=enums.ParseMode.HTML)
    for p, _ in session["paths"]:
        try:
            os.remove(p)
        except OSError:
            pass
    await message.reply_text(f"<b>{E_TRASH} Zip session cancelled — {len(session['paths'])} file(s) discarded.</b>", parse_mode=enums.ParseMode.HTML)


# group=-1 so this runs before rename.py's catch-all document handler, but
# only ever fires (and only ever stops propagation) when a /zip session is
# actually active for this user — any other file upload passes straight
# through untouched, exactly like cookies_manager.py's pending-flow pattern.
@Client.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.photo | filters.voice), group=-1)
async def zip_collect_file(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.get(user_id)
    if not session:
        # No active /zip session — this file isn't ours. Must raise
        # ContinuePropagation (not a plain return) so other group=-1
        # handlers on the same update (e.g. cookies_manager.py's cookies.txt
        # catcher) still get a chance; a plain return here would silently
        # swallow every private document/video/audio/photo/voice upload
        # whenever no /zip session is active, since Pyrogram stops trying
        # further handlers in the same group once one's filter matched.
        raise ContinuePropagation

    staging_dir = make_output_folder(f"zip_session/{user_id}")
    idx = len(session["paths"]) + 1
    fname = _media_name(message, f"file_{idx}")
    dest = os.path.join(staging_dir, f"{idx}_{fname}")

    # Per-file caption ("[Photos] pic.jpg", "Docs/Work/file.pdf") wins over
    # the session-wide default set by /zipfolder; neither means root.
    folder = _parse_folder_from_caption(message.caption) or session.get("current_folder", "")
    arcname = f"{folder}/{fname}" if folder else fname

    status = await message.reply_text(f"<b>{E_BOLT} Adding {fname} to archive...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        await message.download(file_name=dest, progress=make_download_progress(status, file_name=fname))
        session["paths"].append((dest, arcname))
        loc = f" → <code>{folder}/</code>" if folder else ""
        await safe_edit(status.edit_text, 
            f"<b>{E_CHECK} Added:</b> <code>{fname}</code>{loc}\n"
            f"<b>ǫᴜᴇᴜᴇᴅ:</b> {len(session['paths'])} file(s)\n\n"
            f"<i>Send more, or use /donezip when ready.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to add file:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    message.stop_propagation()


async def _report_build_progress(status, state, idx, total, arcname, label="ᴢɪᴘ"):
    """Throttled status-message updater for the archive-building loop below.
    Compression itself has no meaningful byte-progress (zipfile/py7zr/tarfile
    don't expose one), so this shows a file-count bar instead — still real
    feedback instead of a single static line for archives with many files.
    """
    now = time.time()
    finished = idx >= total
    if not finished and now - state["last_edit"] < 1.5:
        return
    state["last_edit"] = now
    pct = (idx * 100 / total) if total else 100
    bar = draw_bar(pct, length=10, filled="⬢", empty="⬡")
    await safe_edit(
        status.edit_text,
        f"<b>🗜️ Building {label} archive...</b>\n"
        f"[{bar}]\n"
        f"✅ {idx}/{total} files\n"
        f"📄 <code>{arcname}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


async def _build_zip(archive_path, entries, password, encryption="aes256", status=None):
    state = {"last_edit": 0.0}
    total = len(entries)

    def _open_zip():
        if password and PYZIPPER_AVAILABLE:
            if encryption == "zipcrypto":
                # Legacy WinZip-standard encryption — weak, but readable by
                # basically every unzip tool ever made, including ones that
                # don't understand the AES extension.
                zf = pyzipper.AESZipFile(
                    archive_path, "w",
                    compression=pyzipper.ZIP_DEFLATED,
                    encryption=pyzipper.WZ_ZIPCRYPTO
                )
                zf.setpassword(password.encode("utf-8"))
            else:
                nbits = 128 if encryption == "aes128" else 256
                zf = pyzipper.AESZipFile(
                    archive_path, "w",
                    compression=pyzipper.ZIP_DEFLATED,
                    encryption=pyzipper.WZ_AES
                )
                zf.setpassword(password.encode("utf-8"))
                zf.setencryption(pyzipper.WZ_AES, nbits=nbits)
        else:
            zf = zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED)
        return zf

    zf = await asyncio.to_thread(_open_zip)
    try:
        for i, (disk_path, arcname) in enumerate(entries, start=1):
            if os.path.exists(disk_path):
                await asyncio.to_thread(zf.write, disk_path, arcname)
            if status:
                await _report_build_progress(status, state, i, total, arcname, label="ᴢɪᴘ")
    finally:
        await asyncio.to_thread(zf.close)


async def _build_7z(archive_path, entries, password, status=None):
    # py7zr is pure Python — no `7z`/`p7zip` binary needed for creation
    # (unlike /unzip's extraction path, which shells out to the 7z CLI).
    # A password, if given, always gets 7-Zip's own AES-256; py7zr has no
    # weaker mode to pick, unlike pyzipper's zip encryption options.
    state = {"last_edit": 0.0}
    total = len(entries)
    kwargs = {"password": password} if password else {}
    zf = await asyncio.to_thread(py7zr.SevenZipFile, archive_path, "w", **kwargs)
    try:
        for i, (disk_path, arcname) in enumerate(entries, start=1):
            if os.path.exists(disk_path):
                await asyncio.to_thread(zf.write, disk_path, arcname)
            if status:
                await _report_build_progress(status, state, i, total, arcname, label="7ᴢ")
    finally:
        await asyncio.to_thread(zf.close)


async def _build_tar(archive_path, entries, fmt, status=None):
    # fmt is "tar" / "tar.gz" / "tar.bz2" -> tarfile mode "w" / "w:gz" / "w:bz2"
    mode = {"tar": "w", "tar.gz": "w:gz", "tar.bz2": "w:bz2"}[fmt]
    state = {"last_edit": 0.0}
    total = len(entries)
    tf = await asyncio.to_thread(tarfile.open, archive_path, mode)
    try:
        for i, (disk_path, arcname) in enumerate(entries, start=1):
            if os.path.exists(disk_path):
                await asyncio.to_thread(tf.add, disk_path, arcname)
            if status:
                await _report_build_progress(status, state, i, total, arcname, label="ᴛᴀʀ")
    finally:
        await asyncio.to_thread(tf.close)


async def _build_rar(archive_path, entries):
    """Shells out to the `rar` CLI (the only way to *create* .rar — see the
    module docstring). Files are staged under their arcname (which may
    include subfolders from /zipfolder or a caption) inside a temp dir,
    then `rar a` is run with that dir as cwd so the relative paths — and
    therefore the folder structure — land in the archive as-is."""
    stage_dir = archive_path + "_stage"
    os.makedirs(stage_dir, exist_ok=True)
    staged_rel = []
    try:
        for disk_path, arcname in entries:
            if not os.path.exists(disk_path):
                continue
            dest = os.path.join(stage_dir, arcname)
            os.makedirs(os.path.dirname(dest) or stage_dir, exist_ok=True)
            shutil.copy2(disk_path, dest)
            staged_rel.append(arcname)
        cmd = ["rar", "a", "-ep1", "-inul", os.path.abspath(archive_path)] + staged_rel
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=stage_dir, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError((err or out or b"").decode("utf-8", "replace")[-300:])
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


@Client.on_message(filters.private & filters.command("donezip"))
async def zip_done_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ZIP_SESSIONS.pop(user_id, None)
    if not session:
        return await message.reply_text(f"<b>{E_WARN} No active zip session.</b> Start one with <code>/zip</code>.", parse_mode=enums.ParseMode.HTML)
    if not session["paths"]:
        return await message.reply_text(f"<b>{E_WARN} No files were added.</b> Session cancelled.", parse_mode=enums.ParseMode.HTML)

    fmt = session.get("format", "zip")
    ext, fmt_label = ARCHIVE_FORMATS.get(fmt, ARCHIVE_FORMATS["zip"])

    if fmt == "rar" and shutil.which("rar") is None:
        # /zipformat already checks this, but the binary could've been
        # available then and gone missing since (redeploy mid-session etc).
        return await message.reply_text(
            f"<b>{E_CROSS} The <code>rar</code> CLI isn't installed on this server.</b> "
            f"Run <code>/zipformat zip</code> (or 7z/tar/tar.gz/tar.bz2) and try <code>/donezip</code> again.",
            parse_mode=enums.ParseMode.HTML
        )
    if fmt == "7z" and not PY7ZR_AVAILABLE:
        # Same deal — /zipformat checks this too, but guard here as well.
        return await message.reply_text(
            f"<b>{E_CROSS} <code>py7zr</code> isn't installed on this server.</b> "
            f"Run <code>/zipformat zip</code> and try <code>/donezip</code> again.",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_BOLT} Building {fmt_label} archive...</b>", parse_mode=enums.ParseMode.HTML)
    archive_name = (session["name"] or f"AkbotsArchive_{int(time.time())}") + ext
    archive_dir = make_output_folder(f"zip_out/{user_id}")
    archive_path = os.path.join(archive_dir, archive_name)

    async def _job():
        try:
            if fmt == "rar":
                await _build_rar(archive_path, session["paths"])
            elif fmt == "7z":
                await _build_7z(archive_path, session["paths"], session["password"], status=status)
            elif fmt in ("tar", "tar.gz", "tar.bz2"):
                await _build_tar(archive_path, session["paths"], fmt, status=status)
            else:
                await _build_zip(
                    archive_path, session["paths"],
                    session["password"], session.get("encryption", "aes256"), status=status
                )

            size = os.path.getsize(archive_path)
            if fmt == "7z" and session["password"] and PY7ZR_AVAILABLE:
                note = " (AES-256 encrypted)"
            elif fmt == "zip" and session["password"] and PYZIPPER_AVAILABLE:
                enc_label = ZIP_ENCRYPTION_MODES.get(session.get("encryption", "aes256"), "AES-256")
                note = f" ({enc_label.split(' (')[0]} encrypted)"
            else:
                note = ""
            await safe_edit(status.edit_text, f"<b>{E_ROCKET} Uploading archive ({fmt_bytes(size)})...</b>", parse_mode=enums.ParseMode.HTML)
            await upload_file(
                client, message, archive_path, status,
                f"<b>{E_PACK} {archive_name}</b>{note}\n"
                f"<b>ғᴏʀᴍᴀᴛ:</b> {fmt_label} | <b>ғɪʟᴇs:</b> {len(session['paths'])} | <b>sɪᴢᴇ:</b> {fmt_bytes(size)}",
                file_name=archive_name
            )
        except Exception as e:
            await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to build archive:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        finally:
            for p, _ in session["paths"]:
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                if os.path.exists(archive_path):
                    os.remove(archive_path)
            except OSError:
                pass

    task = asyncio.ensure_future(_job())
    task_id = task_manager.register(user_id, task, f"Archive ({fmt_label}): {archive_name}")
    task.add_done_callback(lambda t: task_manager.unregister(user_id, task_id))
