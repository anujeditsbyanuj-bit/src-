import os
import re
import json
import random
import asyncio
import subprocess
import http.cookiejar
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import YT_COOKIES
from Akbots.direct_utils import make_upload_progress

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'

OUTPUT_FOLDER = "downloads/youtube_fallback"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# NOTE: this module is NOT the primary YouTube handler. ytdl.py's
# youtube_auto_detect (group=1) already owns every youtube.com/youtu.be link
# and gives it the full yt-dlp quality-picker treatment, including yt-dlp's
# own headless-render fallback. This file is only invoked by ytdl.py as a
# LAST-RESORT fallback, after BOTH of those have already failed. It is not
# registered as its own auto-detect handler, to avoid double-firing on every
# YouTube link — see _handle_youtube_fallback below and the hook in
# ytdl.py's _show_quality_picker.

YOUTUBE_PATTERN = re.compile(
    r"(https?://)?(www\.|m\.)?(youtube\.com/(watch|shorts|live)\S+|youtu\.be/\S+)",
    re.IGNORECASE,
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
HTML_SIGNATURES = (b"<!doctype", b"<html", b"<head", b"<body")


def extract_youtube_url(text: str):
    text = text.strip()
    if not text.startswith("http"):
        return None
    m = YOUTUBE_PATTERN.search(text)
    return m.group(0) if m else None


def _video_id_from_url(url: str):
    m = re.search(r"[?&]v=([^&]+)", url) or re.search(r"youtu\.be/([^?&/]+)", url) \
        or re.search(r"/(?:shorts|live)/([^?&/]+)", url)
    return m.group(1) if m else None


def _load_cookies():
    """Builds a cookie jar for requests. Always sets CONSENT so a fresh
    fetch doesn't get swapped for the EU cookie-consent interstitial page
    instead of the real watch page; layers the user's own yt_cookies.txt
    on top if present (needed for age-restricted / sign-in-required videos)."""
    jar = requests.cookies.RequestsCookieJar()
    jar.set("CONSENT", "YES+42", domain=".youtube.com")
    if YT_COOKIES and os.path.exists(YT_COOKIES):
        try:
            with open(YT_COOKIES, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            cleaned = raw.replace("#HttpOnly_", "")
            tmp_path = YT_COOKIES + ".parsed.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            mjar = http.cookiejar.MozillaCookieJar(tmp_path)
            mjar.load(ignore_discard=True, ignore_expires=True)
            os.remove(tmp_path)
            for c in mjar:
                jar.set_cookie(c)
        except Exception:
            pass
    return jar


def _extract_balanced_json(html: str, marker: str):
    """Finds `marker` in html, then walks forward from the next '{' counting
    brace depth (respecting quoted strings/escapes) to pull out the complete
    JSON object, regardless of how deeply it's nested. A simple non-greedy
    regex can't do this safely since the object contains its own nested
    braces and quoted braces inside string values."""
    idx = html.find(marker)
    if idx == -1:
        return None
    brace_start = html.find("{", idx)
    if brace_start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(brace_start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[brace_start:i + 1]
    return None


def _extract_youtube_links_sync(link: str):
    """Returns a dict describing what to download:
      {"mode": "progressive"|"adaptive", "video_url": ..., "audio_url": ...,
       "video_id": ..., "title": ...}
    Raises ValueError on failure.

    EXPERIMENTAL, best-effort ONLY. This deliberately does NOT implement
    YouTube's signature-cipher / "n" throttling-parameter decoding the way
    yt-dlp does (that requires executing the page's own obfuscated JS to
    reverse the cipher, which yt-dlp maintains as a constantly-updated,
    dedicated subsystem). This scraper only picks formats that already
    expose a direct, unsigned "url" field in the player response. On
    current YouTube fewer and fewer formats qualify for that, so this will
    often find nothing — that's expected, since it's only ever reached
    after yt-dlp (including yt-dlp's own headless-render fallback) has
    already tried and failed."""
    cookies = _load_cookies()
    vid = _video_id_from_url(link)
    watch_url = f"https://www.youtube.com/watch?v={vid}" if vid else link

    try:
        resp = requests.get(watch_url, headers=FETCH_HEADERS, cookies=cookies, timeout=30)
    except Exception as e:
        raise ValueError(f"Failed to fetch watch page: {e}")

    html = resp.text
    raw = _extract_balanced_json(html, "var ytInitialPlayerResponse")
    if not raw:
        hint = "" if (YT_COOKIES and os.path.exists(YT_COOKIES)) else " Try setting up youtube/yt_cookies.txt."
        raise ValueError(f"Could not find player data on the page (consent wall or bot-check likely).{hint}")

    try:
        player = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse player data: {e}")

    status = (player.get("playabilityStatus") or {}).get("status")
    if status and status != "OK":
        reason = (player.get("playabilityStatus") or {}).get("reason", status)
        raise ValueError(f"Video not playable ({status}): {reason}")

    details = player.get("videoDetails") or {}
    title = details.get("title") or "video"
    video_id = details.get("videoId") or vid or f"yt_{abs(hash(link)) % 10**10}"

    streaming = player.get("streamingData") or {}
    progressive = streaming.get("formats") or []
    adaptive = streaming.get("adaptiveFormats") or []

    # Progressive first — a single combined video+audio stream needs no
    # merge step, so it's simplest and most reliable when one is available
    # with a direct (unciphered) url.
    prog_candidates = [
        f for f in progressive
        if f.get("url") and f.get("mimeType", "").startswith("video/")
    ]
    prog_candidates.sort(key=lambda f: f.get("height", 0), reverse=True)
    if prog_candidates:
        best = prog_candidates[0]
        return {
            "mode": "progressive", "video_url": best["url"], "audio_url": None,
            "video_id": video_id, "title": title,
        }

    # Adaptive fallback — separate best video-only + audio-only streams,
    # only used if BOTH happen to expose a direct url.
    video_only = [f for f in adaptive if f.get("url") and f.get("mimeType", "").startswith("video/")]
    audio_only = [f for f in adaptive if f.get("url") and f.get("mimeType", "").startswith("audio/")]
    video_only.sort(key=lambda f: f.get("height", 0), reverse=True)
    audio_only.sort(key=lambda f: f.get("bitrate", 0), reverse=True)

    if video_only and audio_only:
        return {
            "mode": "adaptive", "video_url": video_only[0]["url"], "audio_url": audio_only[0]["url"],
            "video_id": video_id, "title": title,
        }

    raise ValueError(
        "No unsigned stream URL is available for this video — every format YouTube "
        "returned is signature-ciphered, which this fallback deliberately doesn't "
        "attempt to decode (yt-dlp already tried and failed too)."
    )


def _download_file_sync(url: str, dest: str) -> tuple[bool, str]:
    """Downloads url -> dest. Returns (success, error_message). Rejects a
    login-wall / expired-link HTML page saved with a .mp4 extension instead
    of being treated as a valid video — same defensive checks as facebook.py
    / instagram.py."""
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


def _validate_media_file(path: str, need_video: bool = False) -> tuple[bool, str]:
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
        width, height = 1280, 720
    return duration, width, height


async def _download_and_deliver(client: Client, message: Message, status, url: str, data: dict):
    video_id = data["video_id"]
    title = data["title"]
    mode = data["mode"]
    raw_video = os.path.join(OUTPUT_FOLDER, f"{video_id}_v.mp4")
    raw_audio = os.path.join(OUTPUT_FOLDER, f"{video_id}_a.mp4")
    merged    = os.path.join(OUTPUT_FOLDER, f"{video_id}.mp4")
    thumb     = os.path.join(OUTPUT_FOLDER, f"{video_id}.jpg")
    cleanup = set()

    try:
        await status.edit_text(f"<b>{E_ROCKET} Downloading video stream...</b>", parse_mode=enums.ParseMode.HTML)
        ok, err = await asyncio.to_thread(_download_file_sync, data["video_url"], raw_video)
        if not ok:
            return await status.edit_text(f"<b>{E_CROSS} Video stream download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
        cleanup.add(raw_video)

        if mode == "adaptive":
            await status.edit_text(f"<b>{E_ROCKET} Downloading audio stream...</b>", parse_mode=enums.ParseMode.HTML)
            ok, err = await asyncio.to_thread(_download_file_sync, data["audio_url"], raw_audio)
            if not ok:
                return await status.edit_text(f"<b>{E_CROSS} Audio stream download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
            cleanup.add(raw_audio)

            ok, err = await asyncio.to_thread(_validate_media_file, raw_video, True)
            if not ok:
                return await status.edit_text(f"<b>{E_CROSS} Video stream is invalid:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
            ok, err = await asyncio.to_thread(_validate_media_file, raw_audio, False)
            if not ok:
                return await status.edit_text(f"<b>{E_CROSS} Audio stream is invalid:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

            await status.edit_text(f"<b>⚙️ Merging streams (ffmpeg)...</b>", parse_mode=enums.ParseMode.HTML)
            ok, err = await asyncio.to_thread(_merge_streams, raw_video, raw_audio, merged)
            if not ok:
                await status.edit_text(f"<b>⚙️ Fast merge failed, retrying with re-encode...</b>", parse_mode=enums.ParseMode.HTML)
                ok, err2 = await asyncio.to_thread(_merge_streams_reencode, raw_video, raw_audio, merged)
                if not ok:
                    return await status.edit_text(
                        f"<b>{E_CROSS} Merge failed.</b>\n<code>{err}</code>\n<i>Retry also failed:</i>\n<code>{err2}</code>",
                        parse_mode=enums.ParseMode.HTML
                    )
            cleanup.add(merged)
        else:
            ok, err = await asyncio.to_thread(_validate_media_file, raw_video, True)
            if not ok:
                return await status.edit_text(f"<b>{E_CROSS} Downloaded stream is invalid:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
            merged = raw_video  # progressive stream is already a single combined file

        has_thumb = await asyncio.to_thread(_extract_thumbnail, merged, thumb)
        duration, width, height = await asyncio.to_thread(_get_video_metadata, merged)
        if has_thumb:
            cleanup.add(thumb)

        await status.edit_text(f"<b>{E_ROCKET} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
        sent = await client.send_video(
            chat_id=message.chat.id,
            video=merged,
            thumb=thumb if has_thumb else None,
            duration=duration, width=width, height=height,
            caption=f"<b>{E_CHECK} {title}</b>\n🆔 <code>{video_id}</code>\n<i>(fallback extractor)</i>",
            reply_to_message_id=message.id,
            supports_streaming=True,
            parse_mode=enums.ParseMode.HTML,
            progress=make_upload_progress(status, file_name=f"{video_id}.mp4")
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
        for f in cleanup:
            try:
                os.remove(f)
            except Exception:
                pass


async def _handle_youtube_fallback(client: Client, message: Message, url: str, status=None) -> bool:
    """Last-resort raw scraper. Called by ytdl.py ONLY after yt-dlp itself
    (direct extraction + yt-dlp's own headless-render fallback) has already
    failed on a YouTube link. Returns True if it took over (success OR a
    definitive failure message was already shown), False if the caller
    should show its own error instead (e.g. this module blew up
    unexpectedly). Reuses the caller's status message if given, so the user
    sees one continuous progress message instead of a second one appearing."""
    if status is None:
        status = await message.reply_text(f"<b>{E_INFO} Trying fallback extractor...</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await status.edit_text(
            f"<b>{E_INFO} yt-dlp couldn't get this one — trying a raw fallback extractor "
            f"(experimental, works only when YouTube exposes an unsigned stream url)...</b>",
            parse_mode=enums.ParseMode.HTML
        )

    from Akbots.link_cache import try_send_cached
    if await try_send_cached(client, message, url, status):
        return True

    try:
        data = await asyncio.to_thread(_extract_youtube_links_sync, url)
    except ValueError as e:
        await status.edit_text(f"<b>{E_CROSS} Fallback extractor also failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return True
    except Exception:
        return False

    task_id = None
    try:
        from Akbots import task_manager
        task_id = task_manager.register(
            message.from_user.id, asyncio.current_task(), f"YouTube (fallback): {data['video_id']}"
        )
    except Exception:
        task_id = None
    try:
        await _download_and_deliver(client, message, status, url, data)
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(message.from_user.id, task_id)
            except Exception:
                pass
    return True


# Manual/testing entry point — forces this fallback directly, skipping
# yt-dlp entirely. Normal YouTube links never hit this command; they go
# through ytdl.py's quality picker first and only reach this module
# automatically if that fails.
@Client.on_message(filters.command(["ytraw", "ytfallback"]) & filters.private)
async def youtube_fallback_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/ytraw &lt;YouTube URL&gt;</code>\n"
            f"<i>Forces the experimental raw fallback extractor directly, skipping yt-dlp — "
            f"for testing. Normal pasted links only use this automatically after yt-dlp fails.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_youtube_url(message.command[1]) or message.command[1]
    await _handle_youtube_fallback(client, message, url)
