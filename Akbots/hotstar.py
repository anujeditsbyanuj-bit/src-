# /hotstar — resolve a Hotstar content_id (or full hotstar.com URL) to its
# raw M3U8 stream, then optionally download it and send the finished file
# straight to the chat.
#
# The actual scraping/HLS-download work happens in a separate FastAPI
# service (services/hotstar-api/ — deploy it with its Dockerfile to
# Railway or anywhere else, then set HOTSTAR_API_URL to the deployed base
# URL). This plugin is just a thin client: it calls that service's
# /api/resolve, /api/download, /api/status and /api/file endpoints.
#
# Usage:
#   /hotstar <content_id or hotstar.com URL>                 (uses HOTSTAR_USER_TOKEN)
#   /hotstar <content_id or hotstar.com URL> <x-hs-usertoken> (explicit token)

import re
import os
import time
import asyncio
import logging

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import safe_edit, E_CHECK, E_CROSS, draw_bar, upload_file, smallcaps
from Akbots.link_cache import try_send_cached
from Akbots import hls_proxy
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
# Shared with goon_provider.py so both use identical Netscape-vs-pasted
# cookie-string detection instead of duplicating the parsing logic.
from Akbots.cookie_utils import parse_cookies
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_GEAR = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

try:
    from config import HOTSTAR_API_URL, HOTSTAR_USER_TOKEN
except ImportError:
    HOTSTAR_API_URL = ""
    HOTSTAR_USER_TOKEN = ""

logger = logging.getLogger(__name__)

# content_id from a hotstar.com URL, e.g. .../in/sports/cricket/live/1271635183
URL_ID_PATTERN = re.compile(r"hotstar\.com/.*?(\d{6,})", re.IGNORECASE)

# in-memory: short callback token -> {"stream_url": .., "content_id": ..}
_RESOLVED = {}
_TOKEN_SEQ = 0


def is_available() -> bool:
    return bool(HOTSTAR_API_URL)


def _base_url() -> str:
    return HOTSTAR_API_URL.rstrip("/")


def _extract_content_id(raw: str) -> str:
    m = URL_ID_PATTERN.search(raw)
    if m:
        return m.group(1)
    return raw.strip()


async def _resolve(content_id: str, user_token: str = None, cookies: dict = None) -> dict:
    url = f"{_base_url()}/api/resolve"
    payload = {"content_id": content_id}
    if user_token:
        payload["user_token"] = user_token
    if cookies:
        payload["cookies"] = cookies
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload,
                                 timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or not data.get("stream_url"):
                raise RuntimeError(data.get("error") or data.get("detail") or f"resolve failed ({r.status})")
            return data


async def _resolve_browser(content_id: str, page_url: str, cookies: dict = None) -> dict:
    """Fallback for when _resolve() fails outright — see
    services/hotstar-api/main.py's /api/resolve_browser docstring. Only
    worth calling if page_url is an actual hotstar.com watch link (a
    browser needs something navigable, not a bare content_id)."""
    url = f"{_base_url()}/api/resolve_browser"
    payload = {"content_id": content_id, "page_url": page_url, "cookies": cookies or {}}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload,
                                 timeout=aiohttp.ClientTimeout(total=90)) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or not data.get("stream_url"):
                raise RuntimeError(data.get("error") or data.get("detail") or f"browser resolve failed ({r.status})")
            return data


async def _start_download(m3u8_url: str, output_name: str, license_url: str = None) -> str:
    url = f"{_base_url()}/api/download"
    payload = {"m3u8_url": m3u8_url, "output_name": output_name, "workers": 6}
    if license_url:
        payload["license_url"] = license_url
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload,
                                 timeout=aiohttp.ClientTimeout(total=30)) as r:
            data = await r.json(content_type=None)
            if r.status != 200 or not data.get("job_id"):
                raise RuntimeError(data.get("detail") or f"download queue failed ({r.status})")
            return data["job_id"]


async def _job_status(job_id: str) -> dict:
    url = f"{_base_url()}/api/status/{job_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                raise RuntimeError(data.get("detail") or f"status check failed ({r.status})")
            return data


async def _delete_job(job_id: str):
    url = f"{_base_url()}/api/jobs/{job_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, timeout=aiohttp.ClientTimeout(total=15)):
                pass
    except Exception:
        pass  # best-effort cleanup


def _remember(stream_url: str, content_id: str, content_type: str = "", qualities: list = None,
              is_dash: bool = False, license_url: str = None) -> str:
    global _TOKEN_SEQ
    _TOKEN_SEQ += 1
    key = f"hs{_TOKEN_SEQ}"
    _RESOLVED[key] = {
        "stream_url": stream_url,
        "content_id": content_id,
        "content_type": content_type,
        "qualities": qualities or [],
        "is_dash": is_dash,
        "license_url": license_url,
    }
    # keep the map small
    if len(_RESOLVED) > 200:
        oldest = next(iter(_RESOLVED))
        _RESOLVED.pop(oldest, None)
    return key


def _fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d} sec"


def _fmt_size(num_bytes: float) -> str:
    n = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def _build_caption(content_id: str, content_type: str, quality: str,
                    size_bytes: int, download_elapsed: float, user_id: int,
                    source_url: str, upload_elapsed: float = None, file_ext: str = "ts") -> str:
    lines = [
        "<blockquote>",
        f"📄 <b>{smallcaps('file name')}:</b> <code>{content_id}.{file_ext}</code>\n",
        f"📦 <b>{smallcaps('size')}:</b> {_fmt_size(size_bytes)}\n",
        f"🎞️ <b>{smallcaps('quality')}:</b> {quality or smallcaps('streaming quality')}\n",
        f"🎬 <b>{smallcaps('type')}:</b> {content_type or 'unknown'}\n",
        f"⬇️ <b>{smallcaps('downloaded in')}:</b> {_fmt_dur(download_elapsed)}\n",
    ]
    if upload_elapsed is not None:
        lines.append(f"⬆️ <b>{smallcaps('uploaded in')}:</b> {_fmt_dur(upload_elapsed)}\n")
    lines += [
        f"🙋 <b>{smallcaps('uploaded by')}:</b> <code>{user_id}</code>\n",
        f"🔗 <b>{smallcaps('source')}:</b> <a href=\"{source_url}\">{smallcaps('hotstar link')}</a>\n\n",
        f"⚡ {smallcaps('powered by')} <a href=\"https://t.me/anujedits76\">{smallcaps('anuj kumar')}</a>",
        "</blockquote>",
    ]
    return "".join(lines)


def _hotstar_url(content_id: str) -> str:
    return f"https://www.hotstar.com/in/search?q={content_id}"


def _build_keyboard(token: str, qualities: list = None, direct_link: str = None) -> InlineKeyboardMarkup:
    rows = []
    if qualities:
        for i, q in enumerate(qualities):
            label = f"🎞 {q['resolution']}"
            if q.get("bandwidth_mbps"):
                label += f" ({q['bandwidth_mbps']} Mbps)"
            rows.append([make_button(label, callback_data=f"hsdl_{token}_{i}",
                                      style=_BS.PRIMARY if _BS else None)])
        rows.append([make_button("⚡ Auto (best)", callback_data=f"hsdl_{token}_-1",
                                  style=_BS.PRIMARY if _BS else None)])
    else:
        rows.append([make_button("⬇️ Download & Send", callback_data=f"hsdl_{token}_-1",
                                  style=_BS.PRIMARY if _BS else None)])
    if direct_link:
        rows.append([make_button("🔗 Direct Link (play in VLC/browser)", url=direct_link)])
    return InlineKeyboardMarkup(rows)


@Client.on_message(filters.command(["hotstar"]))
async def hotstar_command(client: Client, message: Message):
    if not is_available():
        return await message.reply_text(
            f"<b>{E_CROSS} Hotstar resolver not configured.</b>\n"
            f"Deploy <code>services/hotstar-api</code> and set "
            f"<code>HOTSTAR_API_URL</code>.",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(message.command) < 2 and not message.document and not (message.reply_to_message and message.reply_to_message.document):
        return await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/hotstar &lt;content_id or hotstar.com link&gt; [cookies]</code>\n\n"
            f"<b>Cookies</b> — either works:\n"
            f"• Paste a Netscape <code>cookies.txt</code> export as the 2nd argument, "
            f"or send/reply with the <code>.txt</code> file itself\n"
            f"• Or paste <code>document.cookie</code> output from DevTools Console\n\n"
            f"No manual token needed — it's pulled out of the cookies automatically.",
            parse_mode=enums.ParseMode.HTML,
        )

    doc_message = message if message.document else (message.reply_to_message if message.reply_to_message and message.reply_to_message.document else None)

    source_text = message.text or message.caption or "/hotstar"
    parts = source_text.split(None, 2)
    raw_id = parts[1] if len(parts) > 1 else ""
    raw_cookies = parts[2].strip() if len(parts) > 2 else ""

    cookies = None
    if doc_message:
        try:
            file_path = await client.download_media(doc_message.document, file_name=f"/tmp/{doc_message.document.file_unique_id}.txt")
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                cookies = parse_cookies(f.read())
        except Exception as e:
            return await message.reply_text(
                f"<b>{E_CROSS} Couldn't read the cookies file.</b>\n<i>{e}</i>",
                parse_mode=enums.ParseMode.HTML,
            )
        finally:
            try:
                import os as _os
                _os.remove(file_path)
            except Exception:
                pass
    elif raw_cookies:
        cookies = parse_cookies(raw_cookies)

    user_token = None if cookies else (HOTSTAR_USER_TOKEN or None)

    if not raw_id:
        return await message.reply_text(
            f"<b>{E_CROSS} No content_id/link given.</b> Add it before the cookies, "
            f"e.g. <code>/hotstar 1271635183</code> (as the file caption, or as text before a pasted cookies.txt).",
            parse_mode=enums.ParseMode.HTML,
        )

    if not cookies and not user_token:
        return await message.reply_text(
            f"<b>{E_CROSS} No cookies given.</b> Paste a cookies.txt export (or send the "
            f"file), or set <code>HOTSTAR_USER_TOKEN</code> in config as a fallback.",
            parse_mode=enums.ParseMode.HTML,
        )

    content_id = _extract_content_id(raw_id)

    # Same content_id downloaded before? Re-send the cached file instantly —
    # no need to resolve/download/re-encrypt again.
    if await try_send_cached(client, message, content_id, status=None):
        return

    status = await message.reply_text(f"<b>{E_GEAR} Resolving stream...</b>",
                                       parse_mode=enums.ParseMode.HTML)
    try:
        data = await _resolve(content_id, user_token=user_token, cookies=cookies)
    except Exception as e:
        # Browser fallback only makes sense if the user actually pasted a
        # navigable hotstar.com URL (not a bare content_id) — a browser
        # has nothing to open otherwise, so don't even attempt it.
        if "http" not in raw_id.lower():
            return await safe_edit(status.edit_text,
                f"<b>{E_CROSS} Couldn't resolve this content.</b>\n<i>{e}</i>",
                parse_mode=enums.ParseMode.HTML,
            )

        await safe_edit(status.edit_text,
            f"<b>{E_GEAR} Normal resolve failed, trying browser fallback...</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        try:
            data = await _resolve_browser(content_id, raw_id, cookies=cookies)
        except Exception as e2:
            return await safe_edit(status.edit_text,
                f"<b>{E_CROSS} Couldn't resolve this content.</b>\n"
                f"<i>Normal resolve: {e}</i>\n<i>Browser fallback: {e2}</i>",
                parse_mode=enums.ParseMode.HTML,
            )

    token = _remember(data["stream_url"], content_id, data.get("content_type") or "",
                       qualities=data.get("qualities") or [], is_dash=data.get("is_dash", False),
                       license_url=data.get("license_url"))
    qualities = data.get("qualities") or []
    is_dash = data.get("is_dash", False)

    if is_dash and not data.get("dash_supported", True):
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} This content is DASH/Widevine-protected</b> and no plain "
            f"HLS version is available — but DASH support isn't set up on this bot "
            f"(missing <code>Akbots/bin/mp4decrypt</code> or the resolver isn't running "
            f"in-process). Ask whoever set this bot up to enable it.",
            parse_mode=enums.ParseMode.HTML,
        )

    direct_link = None
    if not is_dash and (hls_proxy.is_enabled() or getattr(hls_proxy, "MEOW_LOCAL_PROXY", False)):
        try:
            direct_link = hls_proxy.build_hls_url(
                data["stream_url"], referer="https://www.hotstar.com/", kind="playlist",
            )
        except Exception as e:
            logger.warning(f"hls_proxy.build_hls_url failed: {e}")
            direct_link = None

    caption = (
        f"<b>🎬 Hotstar content:</b> <code>{content_id}</code>\n"
        f"<b>Type:</b> {data.get('content_type') or 'unknown'}\n"
        + (f"<b>Protection:</b> DRM (Widevine) — will be decrypted after download\n" if is_dash else "")
        + (f"<b>Qualities found:</b> {len(qualities)}\n" if qualities else "")
        + f"<i>Tap a quality below to download and get the file sent here"
        + (", or open the direct link to stream without downloading." if direct_link else ".") + "</i>"
    )
    await safe_edit(status.edit_text, caption, reply_markup=_build_keyboard(token, qualities, direct_link),
                     parse_mode=enums.ParseMode.HTML)


async def _run_download_job(client: Client, status, content_id: str, content_type: str,
                             stream_url: str, license_url: str, is_dash: bool,
                             selected_quality: str, user_id, source_url: str,
                             token: str = None):
    """Shared download -> poll -> upload pipeline, extracted out of the
    quality-selection callback below so it's not duplicated if another
    caller (e.g. a future resolver that already has a stream_url/
    license_url in hand) needs the same download/decrypt/upload steps
    without going through /api/resolve again."""
    download_start = time.time()
    try:
        job_id = await _start_download(stream_url, content_id, license_url=license_url)
    except Exception as e:
        return await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Couldn't start download.</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    _DASH_STAGE_LABELS = {
        "extracting_key": "Extracting decryption key (Widevine)...",
        "downloading": "Downloading video/audio tracks...",
        "decrypting": "Decrypting...",
        "muxing": "Merging video + audio...",
    }

    last_pct = -1
    while True:
        await asyncio.sleep(3)
        try:
            job = await _job_status(job_id)
        except Exception as e:
            return await safe_edit(status.edit_text,
                f"<b>{E_CROSS} Lost track of the job.</b>\n<i>{e}</i>",
                parse_mode=enums.ParseMode.HTML,
            )

        st = job.get("status")
        if st == "error":
            return await safe_edit(status.edit_text,
                f"<b>{E_CROSS} Download failed.</b>\n<i>{job.get('error')}</i>",
                parse_mode=enums.ParseMode.HTML,
            )

        if is_dash and st in _DASH_STAGE_LABELS:
            pct = job.get("progress", 0)
            if pct != last_pct:
                last_pct = pct
                bar = draw_bar(pct)
                await safe_edit(status.edit_text,
                    f"<b>{E_GEAR} {_DASH_STAGE_LABELS[st]}</b>\n{bar} {pct}%",
                    parse_mode=enums.ParseMode.HTML,
                )
        elif st == "downloading":
            selected_quality = job.get("selected_quality", selected_quality)
            pct = job.get("progress", 0)
            if pct != last_pct:
                last_pct = pct
                bar = draw_bar(pct)
                downloaded = job.get("downloaded", 0)
                total = job.get("total", 0)
                await safe_edit(status.edit_text,
                    f"<b>{E_GEAR} Downloading...</b>\n{bar} {pct}%\n"
                    f"Segments: {downloaded}/{total}"
                    + (f"\nQuality: {selected_quality}" if selected_quality else ""),
                    parse_mode=enums.ParseMode.HTML,
                )
        elif st == "done":
            break

    download_elapsed = time.time() - download_start
    file_url = f"{_base_url()}/api/file/{job_id}"
    file_ext = "mp4" if is_dash else "ts"
    await safe_edit(status.edit_text, f"<b>{E_GEAR} Fetching file from server...</b>",
                     parse_mode=enums.ParseMode.HTML)

    out_path = f"/tmp/{content_id}_{job_id}.{file_ext}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url, timeout=aiohttp.ClientTimeout(total=1800)) as r:
                if r.status != 200:
                    raise RuntimeError(f"file fetch failed ({r.status})")
                total = int(r.headers.get("Content-Length", 0))
                fetched = 0
                last_edit = 0.0
                with open(out_path, "wb") as f:
                    async for chunk in r.content.iter_chunked(1024 * 1024):
                        f.write(chunk)
                        fetched += len(chunk)
                        now = asyncio.get_event_loop().time()
                        if total and now - last_edit >= 2.5:
                            last_edit = now
                            pct = int(fetched * 100 / total)
                            bar = draw_bar(pct)
                            await safe_edit(status.edit_text,
                                f"<b>{E_GEAR} Fetching file from server...</b>\n{bar} {pct}%\n"
                                f"{fetched // (1024*1024)}MB / {total // (1024*1024)}MB",
                                parse_mode=enums.ParseMode.HTML,
                            )

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Uploading to Telegram...</b>",
                         parse_mode=enums.ParseMode.HTML)
        size_bytes = os.path.getsize(out_path)
        caption = _build_caption(content_id, content_type, selected_quality, size_bytes,
                                  download_elapsed, user_id, source_url, file_ext=file_ext)

        def _final_caption(upload_elapsed):
            return _build_caption(content_id, content_type, selected_quality, size_bytes,
                                   download_elapsed, user_id, source_url,
                                   upload_elapsed=upload_elapsed, file_ext=file_ext)

        # upload_file() (Akbots/direct_utils.py) auto-splits if size_bytes
        # exceeds Telegram's bot upload limit (SPLIT_SIZE, 1.9GB) instead
        # of just failing/refusing, and sends as a streamable video vs a
        # plain document based on out_path's extension — .mp4 (DASH
        # output) streams, .ts (HLS output) doesn't get mistakenly
        # offered as one. Also handles the file_id caching this used to
        # do manually before via a separate try_send_cached/_cache_store round-trip.
        await upload_file(
            client, status, out_path, status, caption,
            file_name=f"{content_id}.{file_ext}",
            cache_url=content_id,
            caption_after_upload=_final_caption,
        )
    except Exception as e:
        await safe_edit(status.edit_text,
            f"<b>{E_CROSS} Couldn't send the file.</b>\n<i>{e}</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    finally:
        try:
            os.remove(out_path)
        except Exception:
            pass
        await _delete_job(job_id)
        if token:
            _RESOLVED.pop(token, None)


@Client.on_callback_query(filters.regex(r"^hsdl_(\w+)_(-?\d+)$"))
async def hotstar_download_cb(client: Client, callback_query: CallbackQuery):
    token, qidx = callback_query.matches[0].group(1), int(callback_query.matches[0].group(2))
    entry = _RESOLVED.get(token)
    if not entry:
        return await callback_query.answer("Expired — resolve again with /hotstar.", show_alert=True)

    await callback_query.answer()
    status = callback_query.message
    content_id = entry["content_id"]
    content_type = entry.get("content_type") or ""
    user_id = callback_query.from_user.id if callback_query.from_user else "unknown"

    qualities = entry.get("qualities") or []
    selected_quality = ""
    stream_url = entry["stream_url"]
    is_dash = entry.get("is_dash", False)
    if qidx >= 0 and qidx < len(qualities):
        chosen = qualities[qidx]
        stream_url = chosen["url"]
        selected_quality = chosen.get("resolution", "")

    source_url = _hotstar_url(content_id)
    await _run_download_job(client, status, content_id, content_type, stream_url,
                             entry.get("license_url"), is_dash, selected_quality,
                             user_id, source_url, token=token)
