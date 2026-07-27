import os
import re
import random
import asyncio
import subprocess
import http.cookiejar
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import INSTA_COOKIES
from Akbots.direct_utils import make_upload_progress

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'

OUTPUT_FOLDER = "downloads/instagram"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

INSTA_PATTERN = re.compile(
    r"(https?://)?(www\.)?(instagram\.com|instagr\.am)/\S+", re.IGNORECASE
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


def _cookies_path_for_insta() -> str | None:
    """Custom per-domain cookies (set via /setcookies instagram.com) take
    priority since Instagram sessions go stale in hours/days — an admin
    re-uploading through /setcookies is far more likely to be fresh than
    the static INSTA_COOKIES file path in config.py."""
    try:
        from Akbots.cookies_manager import get_cookies_for_url
        custom = get_cookies_for_url("https://www.instagram.com/")
        if custom:
            return custom
    except Exception:
        pass
    if INSTA_COOKIES and os.path.exists(INSTA_COOKIES):
        return INSTA_COOKIES
    return None


def _load_cookies():
    """Load Netscape-format cookies.txt into a jar requests can use.
    Returns None (no cookies) if missing/empty/unreadable — public posts
    still work fine without it, but Instagram increasingly serves a login
    wall to anonymous requests, so cookies significantly improve the
    success rate here."""
    path = _cookies_path_for_insta()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        cleaned = raw.replace("#HttpOnly_", "")
        tmp_path = path + ".parsed.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(cleaned)

        jar = http.cookiejar.MozillaCookieJar(tmp_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        os.remove(tmp_path)

        if len(jar) == 0:
            return None
        return jar
    except Exception:
        return None


def extract_insta_url(text: str):
    m = INSTA_PATTERN.search(text)
    return m.group(0) if m else None


SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
# Instagram embeds the actual playable file(s) as "video_url":"..." inside
# the page's inline JSON (works for single posts/reels AND carousels with
# more than one video — every video item in a carousel gets its own
# video_url key, so this naturally picks up all of them).
VIDEO_URL_RE = re.compile(r'"video_url":"([^"]+)"')
# Fallback for whatever page shape doesn't have the JSON above (older embed
# markup still renders this meta tag server-side for single-video posts).
OG_VIDEO_RE = re.compile(r'<meta property="og:video(?::secure_url)?" content="([^"]+)"', re.IGNORECASE)


def _unescape_url(u: str) -> str:
    return u.replace('\\u0026', '&').replace('\\/', '/').replace('&amp;', '&')


def _embed_url_for(link: str, shortcode: str) -> str | None:
    """Instagram's normal post/reel page increasingly serves a login-wall or
    client-side-only JS shell (with no video_url/og:video anywhere in the
    initial HTML) even when a session cookie is attached — a widespread,
    ongoing anti-bot tightening that yt-dlp's own Instagram extractor hits
    too. The lighter-weight /embed/captioned/ page for the same shortcode
    is far more likely to still server-render the actual video for a
    PUBLIC post, without needing login — this mirrors yt-dlp's own
    documented "retrying with embed webpage" fallback for exactly this
    failure mode."""
    if not shortcode or shortcode.startswith("insta_"):
        return None
    return f"https://www.instagram.com/p/{shortcode}/embed/captioned/"


def _extract_insta_links_sync(link: str):
    """Returns (video_urls: list[str], shortcode: str). Raises ValueError on
    failure. video_urls has more than one entry for carousel posts that
    contain multiple videos — each is sent as a separate message."""
    cookies = _load_cookies()
    try:
        resp = requests.get(link, headers=FETCH_HEADERS, cookies=cookies, timeout=30, allow_redirects=True)
    except Exception as e:
        raise ValueError(f"Failed to fetch page: {e}")

    html = resp.text

    m = SHORTCODE_RE.search(resp.url) or SHORTCODE_RE.search(link)
    shortcode = m.group(1) if m else f"insta_{abs(hash(link)) % 10**10}"

    video_urls = []
    for vm in VIDEO_URL_RE.finditer(html):
        u = _unescape_url(vm.group(1))
        if u not in video_urls:
            video_urls.append(u)

    if not video_urls:
        vm = OG_VIDEO_RE.search(html)
        if vm:
            video_urls.append(_unescape_url(vm.group(1)))

    if not video_urls:
        # Second attempt: the embed page, for public posts the normal page
        # login-walled/JS-shelled us on. Deliberately WITHOUT cookies —
        # sending a logged-in session cookie to the embed endpoint has been
        # observed to make Instagram redirect IT to a login wall too, where
        # an anonymous request to the embed page alone often still works.
        embed_url = _embed_url_for(link, shortcode)
        if embed_url:
            try:
                eresp = requests.get(embed_url, headers=FETCH_HEADERS, timeout=30, allow_redirects=True)
                ehtml = eresp.text
                for vm in VIDEO_URL_RE.finditer(ehtml):
                    u = _unescape_url(vm.group(1))
                    if u not in video_urls:
                        video_urls.append(u)
                if not video_urls:
                    vm = OG_VIDEO_RE.search(ehtml)
                    if vm:
                        video_urls.append(_unescape_url(vm.group(1)))
            except Exception:
                pass  # fall through to the error below if this also fails

    if not video_urls:
        hint = "" if cookies else " If it's a private post/reel or age-restricted, set up instagram/insta_cookies.txt (or /setcookies instagram.com)."
        raise ValueError(
            "Could not find video in this post. It may be private, a photo-only post, "
            f"or Instagram served a login wall instead of the post page.{hint}"
        )

    return video_urls, shortcode


HTML_SIGNATURES = (b"<!doctype", b"<html", b"<head", b"<body")


def _download_file_sync(url: str, dest: str) -> tuple[bool, str]:
    """Downloads url -> dest. Returns (success, error_message). Rejects a
    login-wall / expired-link HTML page saved with a .mp4 extension instead
    of treating it as a valid video, same defensive checks as facebook.py."""
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
        return False, "File is not readable as media (corrupt or not actual video/audio)."
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
        width, height = 1280, 720
    return duration, width, height


async def _handle_insta_download(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} Instagram link detected — extracting...</b>", parse_mode=enums.ParseMode.HTML
    )
    from Akbots.link_cache import try_send_cached
    if await try_send_cached(client, message, url, status):
        return

    try:
        video_urls, shortcode = await asyncio.to_thread(_extract_insta_links_sync, url)
    except ValueError as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Extraction failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    # Registers this handler's own task with task_manager so /cancel and
    # /cancel_all can stop it even though the actual download runs in a
    # background thread (asyncio.to_thread) rather than through
    # direct_utils.stream_download.
    task_id = None
    try:
        from Akbots import task_manager
        task_id = task_manager.register(
            message.from_user.id, asyncio.current_task(),
            f"Instagram: {shortcode}"
        )
    except Exception:
        task_id = None

    try:
        await _download_insta_videos(client, message, status, video_urls, shortcode, cache_url=url)
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(message.from_user.id, task_id)
            except Exception:
                pass


async def _download_insta_videos(client: Client, message: Message, status, video_urls, shortcode, cache_url=None):
    total = len(video_urls)
    for i, video_url in enumerate(video_urls, start=1):
        tag = shortcode if total == 1 else f"{shortcode}_{i}"
        raw   = os.path.join(OUTPUT_FOLDER, f"{tag}.mp4")
        thumb = os.path.join(OUTPUT_FOLDER, f"{tag}.jpg")
        label = "Downloading video..." if total == 1 else f"Downloading video {i}/{total}..."

        try:
            await status.edit_text(f"<b>{E_ROCKET} {label}</b>", parse_mode=enums.ParseMode.HTML)
            ok, err = await asyncio.to_thread(_download_file_sync, video_url, raw)
            if not ok:
                await status.edit_text(f"<b>{E_CROSS} Download failed ({i}/{total}):</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
                continue

            ok, err = await asyncio.to_thread(_validate_media_file, raw)
            if not ok:
                await status.edit_text(f"<b>{E_CROSS} Invalid file ({i}/{total}):</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
                continue

            has_thumb = await asyncio.to_thread(_extract_thumbnail, raw, thumb)
            duration, width, height = await asyncio.to_thread(_get_video_metadata, raw)

            await status.edit_text(f"<b>{E_ROCKET} Uploading{'' if total == 1 else f' ({i}/{total})'}...</b>", parse_mode=enums.ParseMode.HTML)
            sent = await client.send_video(
                chat_id=message.chat.id,
                video=raw,
                thumb=thumb if has_thumb else None,
                duration=duration, width=width, height=height,
                caption=f"<b>{E_CHECK} Instagram Video</b>\n🆔 <code>{tag}</code>",
                reply_to_message_id=message.id,
                supports_streaming=True,
                parse_mode=enums.ParseMode.HTML,
                progress=make_upload_progress(status, file_name=f"instagram_{tag}.mp4")
            )
            try:
                from Akbots.backup import backup_message
                await backup_message(client, sent)
            except Exception:
                pass
            if total == 1 and cache_url:
                try:
                    from Akbots.link_cache import store as _cache_store
                    await _cache_store(cache_url, sent)
                except Exception:
                    pass
        except Exception as e:
            await status.edit_text(f"<b>{E_CROSS} Error ({i}/{total}):</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        finally:
            for f in (raw, thumb):
                try:
                    os.remove(f)
                except Exception:
                    pass

    try:
        await status.delete()
    except Exception:
        pass


# Registered in a SEPARATE handler group (1) so it runs independently of the
# main t.me link-saving handler in start.py — both get a chance to process
# the same message instead of one silently swallowing the other.
async def _route_insta_download(client: Client, message: Message, url: str):
    """Try the shared yt-dlp quality picker first (gives resolution choices,
    same as YouTube/Facebook). Falls back to the dedicated HTML-scraping
    downloader (single best-quality, no picker) if yt-dlp can't handle this
    link — which happens whenever Instagram rate-limits or login-walls the
    request yt-dlp makes."""
    from Akbots.ytdl import has_quality_formats, _show_quality_picker
    if await has_quality_formats(url):
        return await _show_quality_picker(client, message, url)
    await _handle_insta_download(client, message, url)


@Client.on_message(filters.text & filters.private & filters.regex(INSTA_PATTERN) & ~filters.regex(r"^/"), group=1)
async def instagram_auto_detect(client: Client, message: Message):
    url = extract_insta_url(message.text)
    if url:
        await _route_insta_download(client, message, url)


@Client.on_message(filters.command(["insta", "ig"]) & filters.private)
async def insta_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/insta &lt;instagram post/reel URL&gt;</code>\n"
            f"<i>Or just paste an instagram.com link directly.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_insta_url(message.command[1]) or message.command[1]
    await _route_insta_download(client, message, url)
