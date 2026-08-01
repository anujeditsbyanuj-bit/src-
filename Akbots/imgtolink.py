# Akbots
# Image → Direct Link uploader (ImgBB), ported from the standalone
# "IMG-TO-LINK" bot into akbotz's own plugin/callback conventions.
#
# Unlike the standalone bot, this plugin does NOT hijack every incoming
# photo/document globally — akbotz already has a dozen other plugins
# (tageditor, archive/zip, rename, set_thumb, filestore, gdrive, ...) that
# care about photos/documents. Instead this only ever acts when a user has
# explicitly opted in via /imgtolink (or replied to a photo/doc with it, or
# sent /imgurl <url>) — exactly the same "pending-session, skip silently if
# none, stop_propagation once consumed" pattern already used by
# cookies_manager.py / gdrive.py / archive.py's zip session.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import base64
import time
import aiohttp
from datetime import datetime, timezone

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from config import IMGBB_API_KEY
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_IMAGE  = '<emoji id=5395444784611480792>🖼</emoji>'
E_LINK   = '<emoji id=5271604874419647061>🔗</emoji>'
E_CLOCK  = '<emoji id=5386367538735104399>⌛</emoji>'
E_EXPIRY = '⏰'
E_PENCIL = '<emoji id=5395444784611480792>✏️</emoji>'
E_TIP    = '<emoji id=5422439311196834318>💡</emoji>'

IMGBB_URL = "https://api.imgbb.com/1/upload"

EXPIRY_LABEL_MAP = {
    3600:    "1 Hour",
    86400:   "1 Day",
    604800:  "7 Days",
    2592000: "30 Days",
    None:    "Never",
}

# user_id -> "image" | "url"   (which kind of input we're waiting for)
_PENDING = {}
# user_id -> expiry seconds (or None == never)
_USER_EXPIRY = {}


def get_expiry(uid: int):
    return _USER_EXPIRY.get(uid)


def expiry_str(uid: int) -> str:
    return EXPIRY_LABEL_MAP.get(get_expiry(uid), "Never")


# ─────────────────────── Keyboards ──────────────────────────
def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            make_button(" 🖼 Upload Image ", callback_data="i2l_menu_image",
                        style=_BS.PRIMARY if _BS else None),
            make_button(" 🔗 Upload URL ", callback_data="i2l_menu_url",
                        style=_BS.PRIMARY if _BS else None),
        ],
        [make_button(" ⏰ Set Expiry ", callback_data="i2l_menu_expiry",
                     style=_BS.PRIMARY if _BS else None)],
    ])


def _expiry_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            make_button(" ⏰ 1 Hour ",  callback_data="i2l_exp_3600",    style=_BS.PRIMARY if _BS else None),
            make_button(" 📅 1 Day ",   callback_data="i2l_exp_86400",   style=_BS.PRIMARY if _BS else None),
        ],
        [
            make_button(" 📅 7 Days ",  callback_data="i2l_exp_604800",  style=_BS.PRIMARY if _BS else None),
            make_button(" 📅 30 Days ", callback_data="i2l_exp_2592000", style=_BS.PRIMARY if _BS else None),
        ],
        [make_button(" ∞ Never ", callback_data="i2l_exp_never", style=_BS.PRIMARY if _BS else None)],
        [make_button(" ⬅️ Back ", callback_data="i2l_back", style=_BS.DANGER if _BS else None)],
    ])


def _result_kb(direct_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        make_button(" 📋 Open Link ", url=direct_url, style=_BS.PRIMARY if _BS else None),
        make_button(" ↗️ Share Link ", url=f"https://t.me/share/url?url={direct_url}",
                    style=_BS.PRIMARY if _BS else None),
    ]])


# ─────────────────────── ImgBB upload ───────────────────────
async def upload_to_imgbb(image_data: str, name: str = None, expiration: int = None) -> dict:
    """image_data: base64 string OR a plain image URL — imgbb accepts both
    via the same 'image' field."""
    req_url = f"{IMGBB_URL}?key={IMGBB_API_KEY}"
    if expiration:
        req_url += f"&expiration={expiration}"

    payload = {"image": image_data}
    if name:
        payload["name"] = name

    async with aiohttp.ClientSession() as session:
        async with session.post(req_url, data=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            text_body = await resp.text()
            if resp.status != 200:
                try:
                    import json
                    err = json.loads(text_body).get("error", {}).get("message", text_body[:200])
                except Exception:
                    err = text_body[:200]
                raise RuntimeError(f"ImgBB {resp.status}: {err}")
            import json
            data = json.loads(text_body)

    if not data.get("success"):
        raise RuntimeError(data.get("error", {}).get("message", "Upload failed"))

    d   = data["data"]
    img = d["image"]
    thr = d.get("thumb", {})
    total_bytes = int(d.get("size", 0))
    exp_label = EXPIRY_LABEL_MAP.get(expiration if expiration else None, "Never")
    ts = int(d.get("time", 0))
    now_utc = (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y/%m/%d - %H:%M:%S")
        if ts else datetime.now(timezone.utc).strftime("%Y/%m/%d - %H:%M:%S")
    )
    return {
        "url":         d["url"],
        "viewer":      d["url_viewer"],
        "thumb":       thr.get("url", d["url"]),
        "width":       d.get("width", "?"),
        "height":      d.get("height", "?"),
        "size_kb":     total_bytes // 1024,
        "filename":    img["filename"],
        "ext":         img["extension"],
        "expiry":      exp_label,
        "upload_time": now_utc,
        "name":        d.get("title", img.get("name", img["filename"])),
    }


def _build_result_text(r: dict, user_name: str, user_id: int, elapsed: float) -> str:
    return (
        "<blockquote>"
        f"{E_CHECK} <b>Image Uploaded Successfully!</b>\n\n"
        f"{E_IMAGE} <b>Name:</b> {r['name']}\n"
        f"{E_LINK} <b>Direct URL:</b> <a href=\"{r['url']}\">{r['url']}</a>\n"
        f"{E_INFO} <b>Viewer:</b> <a href=\"{r['viewer']}\">{r['viewer']}</a>\n"
        f"{E_INFO} <b>Size:</b> {r['width']}x{r['height']} | {r['size_kb']} KB\n"
        f"{E_PENCIL} <b>File:</b> {r['filename']} ({r['ext']})\n"
        f"{E_EXPIRY} <b>Expiry:</b> {r['expiry']}\n"
        f"{E_CLOCK} <b>Time:</b> {r['upload_time']} UTC\n\n"
        f"{E_ROCKET} <b>Took:</b> {elapsed:.1f}s"
        "</blockquote>"
    )


async def _do_upload_image(client: Client, message: Message, source: Message = None):
    """source is the message actually carrying the photo/document (may be a
    reply target, may be `message` itself)."""
    src = source or message
    uid = message.from_user.id
    uname = message.from_user.first_name
    name = src.caption or None
    _PENDING.pop(uid, None)

    status = await message.reply_text(f"<b>{E_CLOCK} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
    t_start = time.monotonic()
    try:
        file_id = src.document.file_id if src.document else src.photo.file_id
        media = await client.download_media(file_id, in_memory=True)
        if hasattr(media, "seek") and hasattr(media, "read"):
            media.seek(0)
            raw = media.read()
        elif hasattr(media, "getvalue"):
            raw = media.getvalue()
        else:
            with open(str(media), "rb") as f:
                raw = f.read()
        if not raw:
            raise RuntimeError("Downloaded file is empty")

        b64 = base64.b64encode(raw).decode()
        result = await upload_to_imgbb(b64, name=name, expiration=get_expiry(uid))
        elapsed = time.monotonic() - t_start

        await status.delete()
        await client.send_photo(
            chat_id=message.chat.id,
            photo=result["url"],
            caption=_build_result_text(result, uname, uid, elapsed),
            reply_markup=_result_kb(result["url"]),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        try:
            await status.delete()
        except Exception:
            pass
        await message.reply_text(f"<b>{E_CROSS} Upload failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


async def _do_upload_url(client: Client, message: Message, url: str):
    uid = message.from_user.id
    uname = message.from_user.first_name
    _PENDING.pop(uid, None)

    if not url.startswith("http"):
        return await message.reply_text(
            f"<b>{E_CROSS} Invalid URL.</b> Send a valid http/https image URL.",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_CLOCK} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
    t_start = time.monotonic()
    try:
        result = await upload_to_imgbb(url, expiration=get_expiry(uid))
        elapsed = time.monotonic() - t_start
        await status.delete()
        await client.send_photo(
            chat_id=message.chat.id,
            photo=result["url"],
            caption=_build_result_text(result, uname, uid, elapsed),
            reply_markup=_result_kb(result["url"]),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception as e:
        try:
            await status.delete()
        except Exception:
            pass
        await message.reply_text(f"<b>{E_CROSS} Upload failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


# ─────────────────────── /imgtolink ─────────────────────────
@Client.on_message(filters.command(["imgtolink", "img2link", "i2l"]) & filters.private)
async def imgtolink_cmd(client: Client, message: Message):
    uid = message.from_user.id

    # /imgtolink <url> — direct one-shot.
    if len(message.command) > 1:
        return await _do_upload_url(client, message, message.command[1].strip())

    # Reply to an existing photo/document — convert immediately, no extra step.
    reply = message.reply_to_message
    if reply and (reply.photo or reply.document):
        return await _do_upload_image(client, message, source=reply)

    _PENDING[uid] = "menu"
    await message.reply_text(
        f"{E_IMAGE} <b>Image To Link</b>\n\n"
        f"<blockquote>{E_ROCKET} Upload any image and get a permanent direct link — "
        f"powered by ImgBB.</blockquote>\n\n"
        f"{E_INFO} <b>Current Expiry:</b> {expiry_str(uid)}\n\n"
        f"{E_TIP} <i>Tip: reply to a photo with /imgtolink, or use /imgurl &lt;url&gt; "
        f"for a one-shot upload.</i>",
        reply_markup=_menu_kb(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("imgurl") & filters.private)
async def imgurl_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/imgurl &lt;image URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    await _do_upload_url(client, message, message.command[1].strip())


@Client.on_message(filters.command("cancel") & filters.private, group=-3)
async def imgtolink_cancel(client: Client, message: Message):
    uid = message.from_user.id
    if uid in _PENDING:
        _PENDING.pop(uid, None)
        await message.reply_text(f"<b>{E_CHECK} Image-to-Link cancelled.</b>", parse_mode=enums.ParseMode.HTML)
        # Don't stop_propagation — other plugins may have their own /cancel
        # handling to run for unrelated sessions too.


# ─────────────────────── Menu callbacks ─────────────────────
@Client.on_callback_query(filters.regex("^i2l_menu_image$"))
async def i2l_menu_image_cb(client: Client, cb: CallbackQuery):
    uid = cb.from_user.id
    _PENDING[uid] = "image"
    await cb.edit_message_text(
        f"{E_IMAGE} <b>Send me an image now</b> (photo or file).\n"
        f"{E_TIP} Send /cancel to abort.",
        parse_mode=enums.ParseMode.HTML,
    )
    await cb.answer()


@Client.on_callback_query(filters.regex("^i2l_menu_url$"))
async def i2l_menu_url_cb(client: Client, cb: CallbackQuery):
    uid = cb.from_user.id
    _PENDING[uid] = "url"
    await cb.edit_message_text(
        f"{E_LINK} <b>Send me an image URL now.</b>\n"
        f"{E_TIP} Send /cancel to abort.",
        parse_mode=enums.ParseMode.HTML,
    )
    await cb.answer()


@Client.on_callback_query(filters.regex("^i2l_menu_expiry$"))
async def i2l_menu_expiry_cb(client: Client, cb: CallbackQuery):
    uid = cb.from_user.id
    await cb.edit_message_text(
        f"{E_EXPIRY} <b>Select Auto-Delete Timer</b>\n\n"
        f"{E_INFO} Current: <b>{expiry_str(uid)}</b>",
        reply_markup=_expiry_kb(),
        parse_mode=enums.ParseMode.HTML,
    )
    await cb.answer()


@Client.on_callback_query(filters.regex("^i2l_exp_"))
async def i2l_expiry_cb(client: Client, cb: CallbackQuery):
    uid = cb.from_user.id
    data = cb.data
    if data == "i2l_exp_never":
        _USER_EXPIRY[uid] = None
        label = "Never"
    else:
        secs = int(data.split("_")[-1])
        _USER_EXPIRY[uid] = secs
        label = EXPIRY_LABEL_MAP.get(secs, "Never")
    await cb.answer(f"✅ Expiry set to {label}", show_alert=True)
    await cb.edit_message_text(
        f"{E_IMAGE} <b>Image To Link</b>\n\n"
        f"{E_INFO} <b>Current Expiry:</b> {expiry_str(uid)}",
        reply_markup=_menu_kb(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex("^i2l_back$"))
async def i2l_back_cb(client: Client, cb: CallbackQuery):
    uid = cb.from_user.id
    await cb.edit_message_text(
        f"{E_IMAGE} <b>Image To Link</b>\n\n"
        f"{E_INFO} <b>Current Expiry:</b> {expiry_str(uid)}",
        reply_markup=_menu_kb(),
        parse_mode=enums.ParseMode.HTML,
    )
    await cb.answer()


# ─────────────────────── Incoming media / text ───────────────
# group=-3: runs early, but ONLY ever acts (and only ever stops propagation)
# when this specific user has an active /imgtolink session — any other
# photo/document/text passes straight through untouched, same pending-
# session pattern as cookies_manager.py / gdrive.py / archive.py's zip.
@Client.on_message(filters.private & (filters.photo | filters.document), group=-3)
async def i2l_incoming_media(client: Client, message: Message):
    uid = message.from_user.id
    if _PENDING.get(uid) != "image":
        return
    await _do_upload_image(client, message)
    message.stop_propagation()


@Client.on_message(filters.private & filters.text & ~filters.command([
    "imgtolink", "img2link", "i2l", "imgurl", "cancel", "start", "help"
]), group=-3)
async def i2l_incoming_url(client: Client, message: Message):
    uid = message.from_user.id
    if _PENDING.get(uid) != "url":
        return
    await _do_upload_url(client, message, message.text.strip())
    message.stop_propagation()
