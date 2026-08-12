# Akbots - Don't Remove Credit - @AkBots_Official
#
# MP4 Encryption / DRM packaging — /encryptmp4 (reply to a video/mp4 file).
#
# Wraps Bento4's `mp4encrypt` CLI to CENC-encrypt (AES-128-CTR, MPEG-CENC
# method) an mp4 the user owns/controls, for legitimate DRM packaging —
# e.g. before uploading to a licensed player/CDN that expects encrypted
# segments. This is content *encryption*, not decryption: it does not
# strip, bypass, or work around anyone else's DRM. It will not touch a
# file that's already DRM-protected (mp4encrypt only writes new
# encryption, and Telegram-downloaded DRM streams are typically fragmented
# CENC already — see the guard below).
#
# Flow:
#   /encryptmp4              -> reply to a video/mp4; bot auto-generates a
#                                random 128-bit KID + Key (CENC/MPEG-CENC,
#                                single track) and returns the encrypted
#                                file + the KID:KEY pair (needed to play it
#                                back in any CENC-aware player/packager).
#   /encryptmp4 <kid>:<key>  -> same, but using a caller-supplied KID/KEY
#                                pair (both 32 hex chars / 16 bytes) —
#                                for wiring into an existing license
#                                server / key rotation instead of a
#                                one-off random key.
#
# Needs the bundled Akbots/bin/mp4encrypt (Bento4) binary — see MP4ENCRYPT_BIN
# below. Falls back to reporting "not installed" instead of crashing if the
# binary is missing/not executable, same fail-soft pattern as /zipformat rar
# in archive.py.

import os
import re
import shutil
import asyncio
import secrets

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    upload_file, make_output_folder, safe_filename, VIDEO_EXTS,
)
from Akbots.direct_utils import safe_edit, make_download_progress

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_WARN   = '<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>'
E_LOCK   = '🔐'
E_GEAR   = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

MP4ENCRYPT_BIN = os.path.join(os.path.dirname(__file__), "bin", "mp4encrypt")

_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _mp4encrypt_available() -> bool:
    return os.path.isfile(MP4ENCRYPT_BIN) and os.access(MP4ENCRYPT_BIN, os.X_OK)


def _replied_mp4(message: Message):
    replied = message.reply_to_message
    if not replied:
        return None, None
    if replied.video:
        name = replied.video.file_name or f"video_{replied.id}.mp4"
        return replied.video, name
    if replied.document:
        name = replied.document.file_name or ""
        if name.lower().endswith(".mp4"):
            return replied.document, name
    return None, None


@Client.on_message(filters.private & filters.command("encryptmp4"))
async def encryptmp4_cmd(client: Client, message: Message):
    if not _mp4encrypt_available():
        return await message.reply_text(
            f"<blockquote>{E_CROSS} <b>mp4encrypt</b> binary isn't installed on this "
            f"host — DRM packaging is unavailable.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    media, orig_name = _replied_mp4(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to an <b>.mp4</b> file with "
            f"<code>/encryptmp4</code> to CENC-encrypt it (AES-128-CTR).\n\n"
            f"Optional: <code>/encryptmp4 &lt;kid&gt;:&lt;key&gt;</code> to supply your "
            f"own 32-hex-char KID and Key instead of a random one.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    # Parse optional caller-supplied KID:KEY, else generate a random pair.
    args = message.text.split(maxsplit=1)
    kid_hex = key_hex = None
    if len(args) > 1 and ":" in args[1]:
        kid_hex, key_hex = args[1].strip().split(":", 1)
        if not (_HEX32_RE.match(kid_hex) and _HEX32_RE.match(key_hex)):
            return await message.reply_text(
                f"<blockquote>{E_CROSS} KID and Key must each be exactly 32 hex "
                f"characters (16 bytes). Example:\n"
                f"<code>/encryptmp4 000102030405060708090a0b0c0d0e0f:"
                f"101112131415161718191a1b1c1d1e1f</code></blockquote>",
                parse_mode=enums.ParseMode.HTML,
            )
        kid_hex, key_hex = kid_hex.lower(), key_hex.lower()
    else:
        kid_hex = secrets.token_hex(16)
        key_hex = secrets.token_hex(16)

    user_id = message.from_user.id
    temp_dir = os.path.join(make_output_folder("mp4encrypt"), f"{user_id}_{message.id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)

    orig_name = safe_filename(orig_name, f"video_{message.reply_to_message.id}.mp4")
    base_name, ext = os.path.splitext(orig_name)
    ext = ext if ext.lower() in VIDEO_EXTS else ".mp4"
    in_path = os.path.join(temp_dir, f"{base_name}_src{ext}")
    frag_path = os.path.join(temp_dir, f"{base_name}_frag{ext}")
    out_path = os.path.join(temp_dir, f"{base_name}_encrypted{ext}")

    status = await message.reply_text(
        f"<b>{E_GEAR} Downloading...</b>", parse_mode=enums.ParseMode.HTML
    )
    try:
        await client.download_media(message.reply_to_message, file_name=in_path,
                                     progress=make_download_progress(status, file_name=orig_name))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                parse_mode=enums.ParseMode.HTML)

    # MPEG-CENC only applies to *fragmented* mp4 (mp4encrypt warns and
    # produces a non-standard file otherwise) — remux to fragmented mp4
    # with ffmpeg first, stream-copy only (no re-encode), before handing
    # off to mp4encrypt.
    await safe_edit(status.edit_text, f"<b>{E_GEAR} Preparing (fragmenting mp4)...</b>",
                     parse_mode=enums.ParseMode.HTML)
    frag_cmd = [
        "ffmpeg", "-hide_banner", "-y", "-i", in_path,
        "-c", "copy", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        frag_path,
    ]
    frag_proc = await asyncio.create_subprocess_exec(
        *frag_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    frag_out, _ = await frag_proc.communicate()
    if frag_proc.returncode != 0 or not os.path.exists(frag_path) or os.path.getsize(frag_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Fragmenting failed (needs a valid mp4 with standard "
            f"video/audio codecs).</b>\n\n<code>{frag_out.decode(errors='replace').strip()[-500:]}</code>",
            parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_LOCK} Encrypting (MPEG-CENC, AES-128-CTR)...</b>",
                     parse_mode=enums.ParseMode.HTML)

    cmd = [
        MP4ENCRYPT_BIN,
        "--method", "MPEG-CENC",
        "--key", f"1:{key_hex}:{kid_hex}",
        "--property", f"1:KID:{kid_hex}",
        frag_path, out_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    out_bytes, _ = await proc.communicate()
    tail = out_bytes.decode(errors="replace").strip()

    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Encryption failed.</b>\n\n<code>{tail[-500:]}</code>",
            parse_mode=enums.ParseMode.HTML)

    try:
        os.remove(in_path)
    except OSError:
        pass

    caption = (
        f"<b>{E_CHECK} Encrypted (MPEG-CENC / AES-128-CTR)</b>\n\n"
        f"<b>KID:</b> <code>{kid_hex}</code>\n"
        f"<b>Key:</b> <code>{key_hex}</code>\n\n"
        f"<i>Save this KID:Key pair — you need it to configure playback/decryption "
        f"on your own player or license server. It is not stored anywhere.</i>"
    )

    await upload_file(
        client, message, out_path, status, caption,
        file_name=os.path.basename(out_path), force_document=True,
    )
    shutil.rmtree(temp_dir, ignore_errors=True)
