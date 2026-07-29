import os
import re
import json
import time
import random
import asyncio
import subprocess
import http.cookiejar
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import FB_COOKIES
from Akbots.direct_utils import make_upload_progress

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'

OUTPUT_FOLDER = "downloads/facebook"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

FB_PATTERN = re.compile(
    r"(https?://)?(www\.)?(facebook\.com|fb\.watch|fb\.com)/\S+", re.IGNORECASE
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


def _load_cookies():
    """Load Netscape-format cookies.txt into a jar requests can use.
    Returns None (no cookies) if the file is missing, empty, or unreadable —
    public videos still work fine without it."""
    if not FB_COOKIES or not os.path.exists(FB_COOKIES):
        return None
    try:
        # Some exporters (curl/yt-dlp style) prefix HttpOnly cookie lines with
        # "#HttpOnly_" which Python's cookiejar doesn't understand by default.
        with open(FB_COOKIES, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        cleaned = raw.replace("#HttpOnly_", "")
        tmp_path = FB_COOKIES + ".parsed.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(cleaned)

        jar = http.cookiejar.MozillaCookieJar(tmp_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        os.remove(tmp_path)

        if len(jar) == 0:
            return None  # placeholder file with header only, no real cookies
        return jar
    except Exception:
        return None


def extract_fb_url(text: str):
    m = FB_PATTERN.search(text)
    return m.group(0) if m else None


DASH_RE = re.compile(r'"dash_prefetch_experimental":\[(.*?)\]')
VIDEO_ID_RE = re.compile(r'"(?:video_id|videoID)":"(\d+)"')


def _extract_fb_links_sync(link: str):
    """Returns (video_url, audio_url, video_id). Raises ValueError on failure.

    Handles classic /videos/<id>/, /watch/?v=<id>, fb.watch/<token> AND the
    newer /share/r/<token>/ /share/v/<token>/ links, where the share token in
    the URL is NOT the numeric video id — so instead of relying on the URL
    shape we scan the fetched page's HTML directly for the dash stream data,
    which is present regardless of which URL format got us there.
    """
    cookies = _load_cookies()
    try:
        resp = requests.get(link, headers=FETCH_HEADERS, cookies=cookies, timeout=30, allow_redirects=True)
    except Exception as e:
        raise ValueError(f"Failed to fetch page: {e}")

    resolved_url = resp.url.split('?')[0]
    html = resp.text

    # Best-effort numeric id — only used for the caption/filename, extraction
    # below no longer depends on it.
    video_id = ''
    for seg in resolved_url.split('/'):
        if seg.isdigit():
            video_id = seg
            break
    if not video_id:
        raw_params = resp.url.split('?')
        if len(raw_params) > 1:
            for param in raw_params[1].split('&'):
                if param.startswith('v=') and param[2:].isdigit():
                    video_id = param[2:]
                    break
    if not video_id:
        m = VIDEO_ID_RE.search(html)
        if m:
            video_id = m.group(1)

    dash_matches = DASH_RE.findall(html)
    if not dash_matches:
        hint = "" if cookies else " If it's a private/friends-only reel or story, set up facebook/fb_cookies.txt."
        raise ValueError(
            "Could not find video stream data. The link may be private, age-restricted, "
            f"expired, or Facebook served a login wall instead of the video page.{hint}"
        )
    target = dash_matches[0].strip()

    if not video_id:
        video_id = f"fb_{abs(hash(link)) % 10**10}"

    try:
        sources = json.loads(f"[{target}]")
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse stream sources: {e}")
    if len(sources) < 2:
        raise ValueError(f"Expected at least 2 stream sources, got {len(sources)}.")

    try:
        video_link = html.split(f'"representation_id":"{sources[0]}"')[1].split('"base_url":"')[1].split('"')[0].replace('\\', '')
    except IndexError:
        raise ValueError("Could not extract video stream URL.")
    try:
        audio_link = html.split(f'"representation_id":"{sources[1]}"')[1].split('"base_url":"')[1].split('"')[0].replace('\\', '')
    except IndexError:
        raise ValueError("Could not extract audio stream URL.")

    return video_link, audio_link, video_id


HTML_SIGNATURES = (b"<!doctype", b"<html", b"<head", b"<body")


def _download_file_sync(url: str, dest: str) -> tuple[bool, str]:
    """Downloads url -> dest. Returns (success, error_message).

    Previously this only checked raise_for_status() and wrote whatever came
    back to disk. When a dash stream URL had expired or Facebook served a
    login/error page instead of media, that HTML page got saved with a
    .mp4 extension — producing an unplayable "video". This now checks the
    Content-Type header, sniffs the first chunk for an HTML signature, and
    rejects suspiciously small payloads before treating the file as good.
    """
    try:
        resp = requests.get(url, headers=DOWNLOAD_HEADERS, stream=True, timeout=60)
        resp.raise_for_status()
    except Exception as e:
        return False, f"Request failed: {e}"

    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type or "text/plain" in content_type:
        return False, f"Server returned '{content_type}' instead of media (stream link likely expired)."

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
            return False, "Downloaded file is an HTML page, not media (stream URL expired or blocked)."

    if total_written < 10 * 1024:
        try:
            os.remove(dest)
        except Exception:
            pass
        return False, f"Downloaded file too small ({total_written} bytes) — likely an error page, not real media."

    return True, ""


def _validate_media_file(path: str, need_video: bool = False) -> tuple[bool, str]:
    """Runs the file through ffprobe to confirm it's actually decodable media
    (catches cases that slip past the HTML-sniffing check above, and catches
    ffmpeg merges that exit 0 but produce a broken/empty container)."""
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
        return False, "File is not readable as media (corrupt or not actual video/audio)."
    if need_video and "video" not in streams:
        return False, "File has no video stream."
    return True, ""


def _merge_streams(video_path: str, audio_path: str, out_path: str) -> tuple[bool, str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video_path,
           "-i", audio_path, "-c", "copy", "-y", out_path]
    try:
        result = subprocess.run(cmd, timeout=300, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out after 300s."
    except FileNotFoundError:
        return False, "ffmpeg is not installed on this host."

    if result.returncode != 0:
        return False, (result.stderr or "unknown ffmpeg error").strip()[-500:]

    ok, err = _validate_media_file(out_path, need_video=True)
    if not ok:
        return False, f"Merge produced a bad file: {err}"

    return True, ""


def _merge_streams_reencode(video_path: str, audio_path: str, out_path: str) -> tuple[bool, str]:
    """Fallback for when -c copy fails (a common cause of Facebook 'merge
    issues': the dash audio/video codecs aren't both mp4-container-safe to
    stream-copy together). Re-encodes audio to AAC and keeps video as copy,
    which fixes most of these failures without a full video re-encode."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", video_path,
           "-i", audio_path, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
           "-y", out_path]
    try:
        result = subprocess.run(cmd, timeout=300, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg (fallback) timed out after 300s."
    except FileNotFoundError:
        return False, "ffmpeg is not installed on this host."

    if result.returncode != 0:
        return False, (result.stderr or "unknown ffmpeg error").strip()[-500:]

    ok, err = _validate_media_file(out_path, need_video=True)
    if not ok:
        return False, f"Fallback merge produced a bad file: {err}"

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
    seek = random.uniform(duration * 0.10, duration * 0.80)
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
        width, height = 1280, 720
    return duration, width, height


async def _handle_fb_download(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} Facebook link detected — extracting...</b>", parse_mode=enums.ParseMode.HTML
    )
    from Akbots.link_cache import try_send_cached
    if await try_send_cached(client, message, url, status):
        return

    try:
        video_url, audio_url, video_id = await asyncio.to_thread(_extract_fb_links_sync, url)
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
            f"Facebook: {video_id}"
        )
    except Exception:
        task_id = None

    try:
        await _download_and_merge_fb(client, message, status, video_url, audio_url, video_id)
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(message.from_user.id, task_id)
            except Exception:
                pass


async def _download_and_merge_fb(client: Client, message: Message, status, video_url, audio_url, video_id):
    raw_video = os.path.join(OUTPUT_FOLDER, f"{video_id}_v.mp4")
    raw_audio = os.path.join(OUTPUT_FOLDER, f"{video_id}_a.mp4")
    merged    = os.path.join(OUTPUT_FOLDER, f"{video_id}.mp4")
    thumb     = os.path.join(OUTPUT_FOLDER, f"{video_id}.jpg")

    try:
        await status.edit_text(f"<b>{E_ROCKET} Downloading video stream...</b>", parse_mode=enums.ParseMode.HTML)
        ok, err = await asyncio.to_thread(_download_file_sync, video_url, raw_video)
        if not ok:
            return await status.edit_text(f"<b>{E_CROSS} Video stream download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

        await status.edit_text(f"<b>{E_ROCKET} Downloading audio stream...</b>", parse_mode=enums.ParseMode.HTML)
        ok, err = await asyncio.to_thread(_download_file_sync, audio_url, raw_audio)
        if not ok:
            return await status.edit_text(f"<b>{E_CROSS} Audio stream download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

        # Sanity-check both downloaded pieces are real decodable media before
        # burning time on a merge attempt that's doomed to fail.
        ok, err = await asyncio.to_thread(_validate_media_file, raw_video, True)
        if not ok:
            return await status.edit_text(f"<b>{E_CROSS} Video stream is invalid:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
        ok, err = await asyncio.to_thread(_validate_media_file, raw_audio, False)
        if not ok:
            return await status.edit_text(f"<b>{E_CROSS} Audio stream is invalid:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

        await status.edit_text(f"<b>⚙️ Merging streams (ffmpeg)...</b>", parse_mode=enums.ParseMode.HTML)
        ok, err = await asyncio.to_thread(_merge_streams, raw_video, raw_audio, merged)
        if not ok:
            # -c copy failed (usually a codec/container mismatch) — retry once
            # with the audio re-encoded to AAC instead of giving up outright.
            await status.edit_text(f"<b>⚙️ Fast merge failed, retrying with re-encode...</b>", parse_mode=enums.ParseMode.HTML)
            ok, err2 = await asyncio.to_thread(_merge_streams_reencode, raw_video, raw_audio, merged)
            if not ok:
                return await status.edit_text(
                    f"<b>{E_CROSS} Merge failed.</b>\n<code>{err}</code>\n<i>Retry also failed:</i>\n<code>{err2}</code>",
                    parse_mode=enums.ParseMode.HTML
                )

        has_thumb = await asyncio.to_thread(_extract_thumbnail, merged, thumb)
        duration, width, height = await asyncio.to_thread(_get_video_metadata, merged)

        await status.edit_text(f"<b>{E_ROCKET} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
        sent = await client.send_video(
            chat_id=message.chat.id,
            video=merged,
            thumb=thumb if has_thumb else None,
            duration=duration, width=width, height=height,
            caption=f"<b>{E_CHECK} Facebook Video</b>\n🆔 <code>{video_id}</code>",
            reply_to_message_id=message.id,
            supports_streaming=True,
            parse_mode=enums.ParseMode.HTML,
            progress=make_upload_progress(status, file_name=f"facebook_{video_id}.mp4")
        )
        try:
            from Akbots.backup import backup_message
            await backup_message(client, sent)
        except Exception:
            pass
        try:
            from Akbots.link_cache import store as _cache_store
            await _cache_store(url, sent)
        except Exception:
            pass
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        for f in (raw_video, raw_audio, merged, thumb):
            try:
                os.remove(f)
            except Exception:
                pass


# Registered in a SEPARATE handler group (1) so it runs independently of the
# main t.me link-saving handler in start.py — both get a chance to process
# the same message instead of one silently swallowing the other.
async def _route_fb_download(client: Client, message: Message, url: str):
    """Try the shared yt-dlp quality picker first (gives resolution choices,
    same as YouTube). Falls back to the dedicated HTML-scraping downloader
    (single best-quality, no picker) if yt-dlp can't handle this link —
    which is common for Facebook since its extractor is often unreliable."""
    from Akbots.ytdl import has_quality_formats, _show_quality_picker
    if await has_quality_formats(url):
        return await _show_quality_picker(client, message, url)
    await _handle_fb_download(client, message, url)


@Client.on_message(filters.text & filters.private & filters.regex(FB_PATTERN), group=1)
async def facebook_auto_detect(client: Client, message: Message):
    url = extract_fb_url(message.text)
    if url:
        await _route_fb_download(client, message, url)


@Client.on_message(filters.command("fb") & filters.private)
async def fb_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/fb &lt;facebook video URL&gt;</code>\n"
            f"<i>Or just paste a facebook.com / fb.watch link directly.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_fb_url(message.command[1]) or message.command[1]
    await _route_fb_download(client, message, url)
