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
from Akbots.direct_utils import make_upload_progress, _extract_html_reason

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


# Stories, Highlights and bare profile links aren't posts/reels — they need
# the authenticated private-API path in insta_extra.py, not this file's
# public-HTML-scraping post/reel extractor. Recognised here so this file's
# own auto-detect steps aside and lets insta_extra.py's own auto-detect
# claim them instead.
_SPECIAL_INSTA_RE = re.compile(
    r"instagram\.com/(stories/|reel/[A-Za-z0-9_-]+/?$)", re.IGNORECASE
)
_RESERVED_IG_PATHS = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
    "direct_v2", "about", "developer", "legal", "web", "api", "graphql",
    "challenge", "emails", "session", "accounts_recovery",
}


def _is_special_insta_url(url: str) -> bool:
    """True for /stories/... links (stories + highlights) and for bare
    profile links like instagram.com/username/ — anything that isn't a
    post/reel/tv permalink."""
    if "/stories/" in url.lower():
        return True
    if SHORTCODE_RE.search(url):
        return False  # a normal /p/, /reel/, /reels/ or /tv/ permalink
    m = re.search(r"instagram\.com/([^/?#]+)/?", url, re.IGNORECASE)
    if not m:
        return False
    first_segment = m.group(1).lower()
    return first_segment not in _RESERVED_IG_PATHS and bool(first_segment)


SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)
# Instagram embeds the actual playable file(s) as "video_url":"..." inside
# the page's inline JSON (works for single posts/reels AND carousels with
# more than one video — every video item in a carousel gets its own
# video_url key, so this naturally picks up all of them).
VIDEO_URL_RE = re.compile(r'"video_url":"([^"]+)"')
# Fallback for whatever page shape doesn't have the JSON above (older embed
# markup still renders this meta tag server-side for single-video posts).
OG_VIDEO_RE = re.compile(r'<meta property="og:video(?::secure_url)?" content="([^"]+)"', re.IGNORECASE)
# Single-photo posts render the canonical image here — far more reliable
# than grabbing any random "display_url" match on the page (those also
# appear for the poster's avatar, related-post thumbnails, etc.).
OG_IMAGE_RE = re.compile(r'<meta property="og:image" content="([^"]+)"', re.IGNORECASE)


def _unescape_url(u: str) -> str:
    return u.replace('\\u0026', '&').replace('\\/', '/').replace('&amp;', '&')


def _find_matching_brace(s: str, open_idx: int) -> int:
    """Given the index of a '{', returns the index of its matching '}', or
    -1. Needed because a flat regex can't respect JSON nesting."""
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_carousel_items(html: str):
    """Returns an ordered list of ('video'|'photo', url) tuples for a
    carousel ("edge_sidecar_to_children") post, or None if this isn't a
    carousel. Slices out each {"node": {...}} block individually
    (brace-matched, not a flat regex) so a video item's is_video flag is
    correctly paired with ITS OWN url instead of possibly bleeding into a
    neighbouring photo item."""
    idx = html.find('"edge_sidecar_to_children"')
    if idx == -1:
        return None
    edges_idx = html.find('"edges"', idx)
    if edges_idx == -1:
        return None
    arr_start = html.find("[", edges_idx)
    if arr_start == -1:
        return None
    depth, arr_end = 0, -1
    for i in range(arr_start, len(html)):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
            if depth == 0:
                arr_end = i
                break
    if arr_end == -1:
        return None
    blob = html[arr_start:arr_end + 1]

    items = []
    for nm in re.finditer(r'\{"node":\{', blob):
        brace_start = nm.end() - 1
        brace_end = _find_matching_brace(blob, brace_start)
        if brace_end == -1:
            continue
        node = blob[brace_start:brace_end + 1]
        if '"is_video":true' in node:
            vm = re.search(r'"video_url":"([^"]+)"', node)
            if vm:
                items.append(("video", _unescape_url(vm.group(1))))
        else:
            dm = re.search(r'"display_url":"([^"]+)"', node)
            if dm:
                items.append(("photo", _unescape_url(dm.group(1))))
    return items or None


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


def _media_from_html(html: str):
    """Returns an ordered list of ('video'|'photo', url) tuples found in a
    single HTML page, or [] if nothing was found. Tries, in order: a
    carousel's typed nodes, single-video JSON/meta, single-photo meta."""
    items = _extract_carousel_items(html)
    if items:
        return items

    for vm in VIDEO_URL_RE.finditer(html):
        u = _unescape_url(vm.group(1))
        return [("video", u)]  # single video post — first match is enough

    vm = OG_VIDEO_RE.search(html)
    if vm:
        return [("video", _unescape_url(vm.group(1)))]

    im = OG_IMAGE_RE.search(html)
    if im:
        return [("photo", _unescape_url(im.group(1)))]

    return []


def _extract_insta_links_sync(link: str):
    """Returns (media_items: list[('video'|'photo', url)], shortcode: str).
    Raises ValueError on failure. media_items has more than one entry for
    carousel posts — each item is sent as a separate message, in order,
    correctly tagged as photo or video."""
    cookies = _load_cookies()
    try:
        resp = requests.get(link, headers=FETCH_HEADERS, cookies=cookies, timeout=30, allow_redirects=True)
    except Exception as e:
        raise ValueError(f"Failed to fetch page: {e}")

    html = resp.text

    m = SHORTCODE_RE.search(resp.url) or SHORTCODE_RE.search(link)
    shortcode = m.group(1) if m else f"insta_{abs(hash(link)) % 10**10}"

    media_items = _media_from_html(html)

    if not media_items:
        # Second attempt: the embed page, for public posts the normal page
        # login-walled/JS-shelled us on. Deliberately WITHOUT cookies —
        # sending a logged-in session cookie to the embed endpoint has been
        # observed to make Instagram redirect IT to a login wall too, where
        # an anonymous request to the embed page alone often still works.
        embed_url = _embed_url_for(link, shortcode)
        if embed_url:
            try:
                eresp = requests.get(embed_url, headers=FETCH_HEADERS, timeout=30, allow_redirects=True)
                media_items = _media_from_html(eresp.text)
            except Exception:
                pass  # fall through to the error below if this also fails

    if not media_items:
        hint = "" if cookies else " If it's a private post/reel, set up instagram/insta_cookies.txt (or /setcookies instagram.com)."
        raise ValueError(
            "Could not find any media in this post. It may be private, "
            f"or Instagram served a login wall instead of the post page.{hint}"
        )

    return media_items, shortcode


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
        try:
            peek = resp.raw.read(4096, decode_content=True)
        except Exception:
            peek = b""
        return False, f"Download failed — {_extract_html_reason(peek or content_type.encode())}."

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
            return False, f"Download failed — {_extract_html_reason(first_chunk)}."

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


def _head_size_sync(url: str):
    """Best-effort Content-Length via HEAD (falls back to a tiny ranged GET
    if the host doesn't answer HEAD requests properly, which some CDNs
    don't). Returns None if size genuinely can't be determined — the
    preview just omits it rather than blocking the download over this."""
    try:
        r = requests.head(url, headers=DOWNLOAD_HEADERS, timeout=10, allow_redirects=True)
        cl = r.headers.get("Content-Length")
        if cl:
            return int(cl)
    except Exception:
        pass
    try:
        r = requests.get(url, headers={**DOWNLOAD_HEADERS, "Range": "bytes=0-0"}, timeout=10, stream=True)
        cr = r.headers.get("Content-Range")  # "bytes 0-0/12345"
        if cr and "/" in cr:
            return int(cr.rsplit("/", 1)[1])
    except Exception:
        pass
    return None


async def _build_media_preview(media_items, title: str, subtitle: str = None, include_sizes: bool = True) -> str:
    """Builds the same kind of <blockquote> info panel ytdl.py's quality
    picker shows before a video download — item count, type, and size —
    but for flows that don't go through yt-dlp at all (photos, stories,
    highlights, profile pictures)."""
    from Akbots.direct_utils import fmt_bytes

    sizes = [None] * len(media_items)
    if include_sizes:
        sizes = await asyncio.gather(*[asyncio.to_thread(_head_size_sync, u) for _, u in media_items])

    lines = [f"<b>{title}</b>"]
    if subtitle:
        lines.append(subtitle)
    lines.append("")

    total = 0
    for i, ((kind, _), size) in enumerate(zip(media_items, sizes), start=1):
        icon = "🎥" if kind == "video" else "🖼️"
        noun = "Video" if kind == "video" else "Photo"
        size_txt = f" — {fmt_bytes(size)}" if size else ""
        prefix = "" if len(media_items) == 1 else f"{i}. "
        lines.append(f"{icon} {prefix}{noun}{size_txt}")
        if size:
            total += size

    if len(media_items) > 1:
        lines.append("")
        lines.append(f"📦 {len(media_items)} item(s)" + (f" • ~{fmt_bytes(total)} total" if total else ""))

    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


async def _handle_insta_download(client: Client, message: Message, url: str):
    status = await message.reply_text(
        f"<b>{E_INFO} Instagram link detected — extracting...</b>", parse_mode=enums.ParseMode.HTML
    )
    from Akbots.link_cache import try_send_cached
    if await try_send_cached(client, message, url, status):
        return

    try:
        media_items, shortcode = await asyncio.to_thread(_extract_insta_links_sync, url)
    except ValueError as e:
        return await status.edit_text(
            f"<b>{E_CROSS} Extraction failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )

    kinds = {k for k, _ in media_items}
    if len(media_items) > 1:
        title = f"{E_INFO} Instagram Carousel"
    elif kinds == {"video"}:
        title = f"{E_INFO} Instagram Video"
    else:
        title = f"{E_INFO} Instagram Photo"
    preview = await _build_media_preview(media_items, title)
    await status.edit_text(preview, parse_mode=enums.ParseMode.HTML)
    await asyncio.sleep(1.2)

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
        await _download_insta_media(client, message, status, media_items, shortcode, cache_url=url)
    finally:
        if task_id is not None:
            try:
                from Akbots import task_manager
                task_manager.unregister(message.from_user.id, task_id)
            except Exception:
                pass


IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),  # WEBP is RIFF....WEBP, good enough as a sanity check
)


def _looks_like_image(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except Exception:
        return False
    return any(head.startswith(sig) for sig, _ in IMAGE_SIGNATURES)


async def _download_insta_media(client: Client, message: Message, status, media_items, shortcode, cache_url=None):
    """media_items: list of ('video'|'photo', url) tuples, in post order.
    Each is downloaded and sent as its own message (send_video for videos,
    send_photo for photos) — mirrors how carousels already worked for
    videos, just extended to cover photo and mixed carousels too."""
    total = len(media_items)
    single_sent = None
    for i, (kind, media_url) in enumerate(media_items, start=1):
        tag = shortcode if total == 1 else f"{shortcode}_{i}"
        ext = "mp4" if kind == "video" else "jpg"
        raw   = os.path.join(OUTPUT_FOLDER, f"{tag}.{ext}")
        thumb = os.path.join(OUTPUT_FOLDER, f"{tag}_thumb.jpg")
        noun  = "video" if kind == "video" else "photo"
        label = f"Downloading {noun}..." if total == 1 else f"Downloading {noun} {i}/{total}..."

        try:
            await status.edit_text(f"<b>{E_ROCKET} {label}</b>", parse_mode=enums.ParseMode.HTML)
            ok, err = await asyncio.to_thread(_download_file_sync, media_url, raw)
            if not ok:
                await status.edit_text(f"<b>{E_CROSS} Download failed ({i}/{total}):</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
                continue

            if kind == "video":
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
                    caption=f"<blockquote><b>{E_CHECK} Instagram Video</b>\n🆔 <code>{tag}</code></blockquote>",
                    reply_to_message_id=message.id,
                    supports_streaming=True,
                    parse_mode=enums.ParseMode.HTML,
                    progress=make_upload_progress(status, file_name=f"instagram_{tag}.mp4")
                )
            else:
                if not await asyncio.to_thread(_looks_like_image, raw):
                    await status.edit_text(f"<b>{E_CROSS} Invalid file ({i}/{total}):</b>\n<code>Downloaded file is not a valid image.</code>", parse_mode=enums.ParseMode.HTML)
                    continue

                await status.edit_text(f"<b>{E_ROCKET} Uploading{'' if total == 1 else f' ({i}/{total})'}...</b>", parse_mode=enums.ParseMode.HTML)
                sent = await client.send_photo(
                    chat_id=message.chat.id,
                    photo=raw,
                    caption=f"<blockquote><b>{E_CHECK} Instagram Photo</b>\n🆔 <code>{tag}</code></blockquote>",
                    reply_to_message_id=message.id,
                    parse_mode=enums.ParseMode.HTML,
                )

            single_sent = sent
            try:
                from Akbots.backup import backup_message
                await backup_message(client, sent)
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

    if total == 1 and cache_url and single_sent is not None:
        try:
            from Akbots.link_cache import store as _cache_store
            await _cache_store(cache_url, single_sent)
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
    if not url:
        return
    if _is_special_insta_url(url):
        # Stories, Highlights and bare profile links need the authenticated
        # private-API path, not this file's public-HTML post/reel scraper.
        from Akbots.insta_extra import route_special_insta_url
        return await route_special_insta_url(client, message, url)
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
    if _is_special_insta_url(url):
        from Akbots.insta_extra import route_special_insta_url
        return await route_special_insta_url(client, message, url)
    await _route_insta_download(client, message, url)
