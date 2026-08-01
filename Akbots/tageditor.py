# Akbots - Don't Remove Credit - @AkBots_Official
#
# Audio Tag Editor — /edittag, /setart
#
#   /edittag title=Song artist=Someone album=MyAlbum year=2024 genre=Pop
#       (reply to an audio file) — rewrites the given ID3/MP4 tags and
#       re-uploads the file. Any subset of keys can be given.
#
#   /setart (reply to an audio file) — bot asks for a cover image next;
#       send/reply a photo and it's embedded as album art.
#
# Supports .mp3 (ID3 via mutagen.easyid3/id3) and .m4a/.mp4 audio
# (mutagen.mp4), which covers the two formats this bot deals in most.

import os
import re
import shutil
import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.direct_utils import upload_file, make_output_folder, safe_filename, AUDIO_EXTS, safe_edit

try:
    from mutagen.mp3 import MP3
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3, APIC, ID3NoHeaderError
    from mutagen.mp4 import MP4, MP4Cover
except ImportError:
    MP3 = EasyID3 = ID3 = APIC = ID3NoHeaderError = MP4 = MP4Cover = None

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_TAG   = '🏷️'

SESSION_TIMEOUT = 300
KV_PATTERN = re.compile(r'(\w+)=("([^"]*)"|(\S+))')

TAG_ALIASES = {
    "title": "title", "name": "title",
    "artist": "artist", "singer": "artist",
    "album": "album",
    "year": "date", "date": "date",
    "genre": "genre",
    "track": "tracknumber", "tracknumber": "tracknumber",
    "albumartist": "albumartist",
}

# user_id -> {"replied_id": id, "name": str, "ts": time}  (for /setart)
_ART_PENDING = {}


def _replied_audio(message: Message):
    replied = message.reply_to_message
    if not replied:
        return None, None
    if replied.audio:
        return replied.audio, replied.audio.file_name or f"audio_{replied.id}.mp3"
    if replied.document:
        name = replied.document.file_name or ""
        if name.lower().endswith(AUDIO_EXTS):
            return replied.document, name
    return None, None


def _write_tags_mp3(path: str, tags: dict):
    try:
        audio = EasyID3(path)
    except ID3NoHeaderError:
        audio = MP3(path, ID3=ID3)
        audio.add_tags()
        audio = EasyID3(path)
    for k, v in tags.items():
        try:
            audio[k] = v
        except Exception:
            pass
    audio.save()


def _write_tags_mp4(path: str, tags: dict):
    audio = MP4(path)
    mp4_map = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
               "date": "\xa9day", "genre": "\xa9gen", "albumartist": "aART"}
    for k, v in tags.items():
        atom = mp4_map.get(k)
        if atom:
            audio[atom] = [v]
    audio.save()


def _embed_art_mp3(path: str, img_path: str):
    try:
        audio = ID3(path)
    except ID3NoHeaderError:
        audio = ID3()
    audio.delall("APIC")
    with open(img_path, "rb") as f:
        data = f.read()
    audio.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=data))
    audio.save(path)


def _embed_art_mp4(path: str, img_path: str):
    audio = MP4(path)
    with open(img_path, "rb") as f:
        data = f.read()
    audio["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()


@Client.on_message(filters.command("edittag") & filters.private)
async def edittag_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if MP3 is None:
        return await message.reply_text(f"<b>{E_CROSS} mutagen isn't installed on this bot.</b>",
                                         parse_mode=enums.ParseMode.HTML)

    media, orig_name = _replied_audio(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to an <b>audio file</b> (.mp3/.m4a) with "
            f"<code>/edittag</code>.\n\n{E_INFO} <b>Usage:</b>\n"
            f'<code>/edittag title=Song artist=Name album=Album year=2024 genre=Pop</code>\n\n'
            f"Use <code>/setart</code> separately to change the cover image.</blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    raw = message.text.split(None, 1)
    kv_text = raw[1] if len(raw) > 1 else ""
    matches = KV_PATTERN.findall(kv_text)
    if not matches:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/edittag title=Song artist=Name album=Album year=2024</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    tags = {}
    for key, _, quoted, plain in matches:
        value = quoted or plain
        norm = TAG_ALIASES.get(key.lower())
        if norm:
            tags[norm] = value

    if not tags:
        return await message.reply_text(
            f"<b>{E_CROSS} No recognised tag keys.</b> Supported: title, artist, album, year, genre, "
            f"track, albumartist.", parse_mode=enums.ParseMode.HTML)

    replied = message.reply_to_message
    status = await message.reply_text(f"<b>{E_GEAR} Downloading & updating tags...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("edittag"), f"{user_id}_{replied.id}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    orig_name = safe_filename(orig_name, f"audio_{replied.id}.mp3")
    in_path = os.path.join(temp_dir, orig_name)

    try:
        await client.download_media(replied, file_name=in_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    ext = os.path.splitext(orig_name)[1].lower()
    try:
        if ext == ".mp3":
            _write_tags_mp3(in_path, tags)
        elif ext in (".m4a", ".mp4"):
            _write_tags_mp4(in_path, tags)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} Only .mp3 and .m4a are supported for tag editing.</b>",
                parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't write tags:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    tag_summary = ", ".join(f"<b>{k}</b>=<code>{v}</code>" for k, v in tags.items())
    await upload_file(
        client, message, in_path, status,
        f"<b>{orig_name}</b>\n\n{E_TAG} Tags updated: {tag_summary}",
        file_name=orig_name, quality="Tags edited",
    )

    shutil.rmtree(temp_dir, ignore_errors=True)


@Client.on_message(filters.command("setart") & filters.private)
async def setart_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if MP3 is None:
        return await message.reply_text(f"<b>{E_CROSS} mutagen isn't installed on this bot.</b>",
                                         parse_mode=enums.ParseMode.HTML)

    media, orig_name = _replied_audio(message)
    if not media:
        return await message.reply_text(
            f"<blockquote>{E_WARN} Reply to an <b>audio file</b> with <code>/setart</code>, "
            f"then send the cover image.</blockquote>", parse_mode=enums.ParseMode.HTML)

    replied = message.reply_to_message
    _ART_PENDING[user_id] = {"replied_id": replied.id,
                              "name": safe_filename(orig_name, f"audio_{replied.id}.mp3"),
                              "ts": time.time()}
    await message.reply_text(f"<b>{E_TAG} Got it.</b> Now send the <b>cover image</b> (as a photo).",
                              parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.photo, group=4)
async def setart_receive(client: Client, message: Message):
    user_id = message.from_user.id
    session = _ART_PENDING.get(user_id)
    if not session:
        return
    if time.time() - session["ts"] > SESSION_TIMEOUT:
        _ART_PENDING.pop(user_id, None)
        return

    _ART_PENDING.pop(user_id, None)
    orig_name = session["name"]
    status = await message.reply_text(f"<b>{E_GEAR} Downloading audio + cover...</b>", parse_mode=enums.ParseMode.HTML)

    temp_dir = os.path.join(make_output_folder("setart"), f"{user_id}_{session['replied_id']}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    a_path = os.path.join(temp_dir, orig_name)
    img_path = os.path.join(temp_dir, "cover.jpg")

    try:
        audio_msg = await client.get_messages(message.chat.id, session["replied_id"])
        await client.download_media(audio_msg, file_name=a_path)
        await client.download_media(message, file_name=img_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    ext = os.path.splitext(orig_name)[1].lower()
    try:
        if ext == ".mp3":
            _embed_art_mp3(a_path, img_path)
        elif ext in (".m4a", ".mp4"):
            _embed_art_mp4(a_path, img_path)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} Only .mp3 and .m4a are supported for cover art.</b>",
                parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't embed cover art:</b> <code>{e}</code>",
                                       parse_mode=enums.ParseMode.HTML)

    await upload_file(
        client, message, a_path, status,
        f"<b>{orig_name}</b>\n\n{E_TAG} Cover art updated",
        file_name=orig_name, quality="Art updated",
    )

    shutil.rmtree(temp_dir, ignore_errors=True)
