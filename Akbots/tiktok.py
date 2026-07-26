import os
import re
import random
import asyncio
import subprocess
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InputMediaPhoto
from Akbots.direct_utils import make_upload_progress

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'

OUTPUT_FOLDER = "downloads/tiktok"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

TIKTOK_PATTERN = re.compile(
    r"(https?://)?(www\.|vm\.|vt\.|m\.)?tiktok\.com/\S+", re.IGNORECASE
)

FETCH_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
}
DOWNLOAD_HEADERS = {
    'user-agent': 'Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/86.0.4240.193 Safari/537.36'
}

# tikwm.com is a free public API that resolves a tiktok.com/vm.tiktok.com/
# vt.tiktok.com link (video AND photo/slideshow posts) into direct, no-login,
# no-watermark media URLs — no cookies needed for public posts, which covers
# the vast majority of TikTok links. This avoids re-implementing TikTok's own
# heavily obfuscated SIGI_STATE/UNIVERSAL_DATA page-JSON parsing, which
# breaks every few weeks as TikTok changes its web bundle.
TIKWM_API = "https://www.tikwm.com/api/"


def extract_tiktok_url(text: str):
    m = TIKTOK_PATTERN.search(text)
    return m.group(0) if m else None


def _extract_tiktok_data_sync(link: str):
    """Returns a dict: {kind: 'video'|'images', id, title, video_url, images}.
    Raises ValueError on failure."""
    try:
        resp = requests.get(
            TIKWM_API, params={"url": link, "hd": 1},
            headers=FETCH_HEADERS, timeout=30
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        raise ValueError(f"Failed to resolve TikTok link: {e}")

    if payload.get("code") != 0 or not payload.get("data"):
        msg = payload.get("msg", "unknown error")
        raise ValueError(
            f"Could not extract media ({msg}). The post may be private, "
            "age-restricted, or removed."
        )

    data = payload["data"]
    tiktok_id = str(data.get("id") or (abs(hash(link)) % 10**10))
    title = (data.get("title") or "").strip()

    images = data.get("images") or []
    if images:
        return {"kind": "images", "id": tiktok_id, "title": title, "images": images}

    video_url = data.get("hdplay") or data.get("play") or data.get("wmplay")
    if not video_url:
        raise ValueError("No playable video URL found for this TikTok link.")
    return {"kind": "video", "id": tiktok_id, "title": title, "video_url": video_url}


HTML_SIGNATURES = (b"<!doctype", b"<html", b"<head", b"<body")


def _download_file_sync(url: str, dest: str) -> tuple[bool, str]:
    """Downloads url -> dest. Returns (success, error_message). Rejects an
    HTML error/login page saved with a media extension, same defensive
    checks as facebook.py / instagram.py."""
    try:
        resp = requests.get(url, headers=DOWNLOAD_HEADERS, stream=True, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        return False, f"Request failed: {e}"

    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type or "text/plain" in content_type:
        return False, f"Server returned '{content_type}' instead of media (link likely expired)."

    total_written = 0
    first_chunk = None
    try:
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=512 * 1024):
                if not chunk:
                    continue
                if first_chunk is None:
                    first_chunk = chunk
                f.write(chunk)
                total_written += len(chunk)
    except Exception as e:
        try:
            os.remove(dest)
        except Exception:
            pass
        return False, f"Download interrupted: {e}"

    if total_written == 0:
        try:
            os.remove(dest)
        except Exception:
            pass
        return False, "Downloaded 0 bytes."

    if first_chunk:
        head = first_chunk[:300].lstrip().lower()
        if any(head.startswith(sig) or sig in head[:150] for sig in HTML_SIGNATURES):
            try:
                os.remove(dest)
            except Exception:
                pass
            return False, "Downloaded file is an HTML page, not media (link expired or blocked)."

    if total_written < 10 * 1024:
        try:
            os.remove(dest)
        except Exception:
            pass
        return False, f"Downloaded file too small ({total_written} bytes) — likely an error page, not real media."

    return True, ""


def _validate_media_file(path: str) -> tuple[bool, str]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False, "File missing or empty."
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        return False, "ffprobe is not installed on this host."
    except Exception as e:
        return False, f"ffprobe check failed: {e}"

    streams = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]
    if not streams:
        return False, "File is not readable as media (corrupt or not actual video)."
    if "video" not in streams:
        return False, "File has no video stream."
    return True, ""


def _extract_thumbnail(video_path: str, thumb_path: str) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = float(probe.stdout.strip() or "10")
    except ValueError:
        duration = 10.0
    seek = random.uniform(duration * 0.10, duration * 0.80) if duration > 1 else 0
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(seek),
             "-i", video_path, "-vframes", "1", "-vf", "scale=320:-1", "-y", thumb_path],
            timeout=30, check=True
        )
        return os.path.exists(thumb_path)
    except Exception:
        return False


def _get_video_metadata(video_path: str):
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        duration = int(float(dur.stdout.strip() or "0"))
    except ValueError:
        duration = 0
    dim = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, timeout=30
    )
    try:
        w, h = dim.stdout.strip().split(",")
        width, height = int(w), int(h)
    except Exception:
        width, height = 720, 1280
    return duration, width, height


async def _handle_tiktok_download(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} TikTok link detected — extracting...</b>", parse_mode=enums.ParseMode.HTML
    )
    from Akbots.link_cache import try_send_cached
    if await try_send_cached(client, message, url, status):
        return

    try:
        data = await asyncio.to_thread(_extract_tiktok_data_sync, url)
    except ValueError as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Extraction failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    # Registers this handler's own task with task_manager so /cancel and
    # /cancel_all can stop it even though the actual download runs in
    # background threads (asyncio.to_thread) rather than through
    # direct_utils.stream_download.
    task_id = None
    try:
        from Akbots import task_manager
        task_id = task_manager.register(
            message.from_user.id, asyncio.current_task(),
            f"TikTok: {data['id']}"
        )
    except Exception:
        task_id = None

    try:
        if data["kind"] == "images":
            await _download_tiktok_images(client, message, status, data, cache_url=url)
        else:
            await _download_tiktok_video(client, message, status, data, cache_url=url)
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(message.from_user.id, task_id)
            except Exception:
                pass


async def _download_tiktok_video(client: Client, message: Message, status, data, cache_url=None):
    tiktok_id = data["id"]
    raw   = os.path.join(OUTPUT_FOLDER, f"{tiktok_id}.mp4")
    thumb = os.path.join(OUTPUT_FOLDER, f"{tiktok_id}.jpg")

    try:
        await status.edit_text(f"<b>{E_ROCKET} Downloading video...</b>", parse_mode=enums.ParseMode.HTML)
        ok, err = await asyncio.to_thread(_download_file_sync, data["video_url"], raw)
        if not ok:
            return await status.edit_text(f"<b>{E_CROSS} Download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

        ok, err = await asyncio.to_thread(_validate_media_file, raw)
        if not ok:
            return await status.edit_text(f"<b>{E_CROSS} Invalid file:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

        has_thumb = await asyncio.to_thread(_extract_thumbnail, raw, thumb)
        duration, width, height = await asyncio.to_thread(_get_video_metadata, raw)

        caption = f"<b>{E_CHECK} TikTok Video</b>"
        if data.get("title"):
            caption += f"\n📝 {data['title'][:200]}"
        caption += f"\n🆔 <code>{tiktok_id}</code>"

        await status.edit_text(f"<b>{E_ROCKET} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
        sent = await client.send_video(
            chat_id=message.chat.id,
            video=raw,
            thumb=thumb if has_thumb else None,
            duration=duration, width=width, height=height,
            caption=caption,
            reply_to_message_id=message.id,
            supports_streaming=True,
            parse_mode=enums.ParseMode.HTML,
            progress=make_upload_progress(status, file_name=f"tiktok_{tiktok_id}.mp4")
        )
        try:
            from Akbots.backup import backup_message
            await backup_message(client, sent)
        except Exception:
            pass
        if cache_url:
            try:
                from Akbots.link_cache import store as _cache_store
                await _cache_store(cache_url, sent)
            except Exception:
                pass
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        for f in (raw, thumb):
            try:
                os.remove(f)
            except Exception:
                pass


async def _download_tiktok_images(client: Client, message: Message, status, data, cache_url=None):
    """TikTok photo/slideshow posts — download each image and send as an
    album (media group) instead of a video."""
    tiktok_id = data["id"]
    image_urls = data["images"]
    local_paths = []

    try:
        await status.edit_text(
            f"<b>{E_ROCKET} Downloading {len(image_urls)} photo(s)...</b>", parse_mode=enums.ParseMode.HTML
        )
        for i, img_url in enumerate(image_urls, start=1):
            path = os.path.join(OUTPUT_FOLDER, f"{tiktok_id}_{i}.jpg")
            ok, err = await asyncio.to_thread(_download_file_sync, img_url, path)
            if ok:
                local_paths.append(path)

        if not local_paths:
            return await status.edit_text(
                f"<b>{E_CROSS} Failed to download any photos from this post.</b>", parse_mode=enums.ParseMode.HTML
            )

        caption = f"<b>{E_CHECK} TikTok Photos</b>"
        if data.get("title"):
            caption += f"\n📝 {data['title'][:200]}"
        caption += f"\n🆔 <code>{tiktok_id}</code>"

        await status.edit_text(f"<b>{E_ROCKET} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
        media = [
            InputMediaPhoto(p, caption=caption if i == 0 else None, parse_mode=enums.ParseMode.HTML)
            for i, p in enumerate(local_paths)
        ]
        sent = await client.send_media_group(
            chat_id=message.chat.id, media=media, reply_to_message_id=message.id
        )
        try:
            from Akbots.backup import backup_message
            for s in sent:
                await backup_message(client, s)
        except Exception:
            pass
        if cache_url and sent:
            try:
                from Akbots.link_cache import store as _cache_store
                await _cache_store(cache_url, sent[0])
            except Exception:
                pass
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        for p in local_paths:
            try:
                os.remove(p)
            except Exception:
                pass


# Registered in a SEPARATE handler group (1) so it runs independently of the
# main t.me link-saving handler in start.py — both get a chance to process
# the same message instead of one silently swallowing the other.
async def _route_tiktok_download(client: Client, message: Message, url: str):
    """Try the shared yt-dlp quality picker first (gives resolution choices,
    same as YouTube/Facebook/Instagram). Falls back to the dedicated
    tikwm-based downloader (single best-quality, no-watermark, no picker) if
    yt-dlp can't handle this link — which happens whenever TikTok rate-limits
    or blocks the request yt-dlp makes, or for photo/slideshow posts that
    yt-dlp doesn't extract at all."""
    from Akbots.ytdl import has_quality_formats, _show_quality_picker
    try:
        if await has_quality_formats(url):
            return await _show_quality_picker(client, message, url)
    except Exception:
        pass
    await _handle_tiktok_download(client, message, url)


@Client.on_message(filters.text & filters.private & filters.regex(TIKTOK_PATTERN) & ~filters.regex(r"^/"), group=1)
async def tiktok_auto_detect(client: Client, message: Message):
    url = extract_tiktok_url(message.text)
    if url:
        await _route_tiktok_download(client, message, url)


@Client.on_message(filters.command(["tiktok", "tt"]) & filters.private)
async def tiktok_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/tiktok &lt;tiktok video URL&gt;</code>\n"
            f"<i>Or just paste a tiktok.com link directly.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_tiktok_url(message.command[1]) or message.command[1]
    await _route_tiktok_download(client, message, url)
