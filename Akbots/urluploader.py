# Generic URL Uploader
# Ported from Url-uploader-Bot-V4 (Plugin/echo.py + Plugin/dl_button.py), rewritten
# to fit this bot's plugin style and reuse Akbots/direct_utils.py.
#
# Every other downloader plugin in this bot (catbox, gofile, pixeldrain, mediafire,
# streamtape, terabox, mega, gdrive, ytdl's yt-dlp fallback, etc.) already claims
# its own domains. This plugin is the LAST-RESORT fallback: any bare http(s) link
# that isn't one of those known hosts, and that yt-dlp itself doesn't recognise as
# a media site, gets treated as a plain direct-download link — downloaded as-is
# and uploaded to Telegram. This is the one capability the original bot didn't
# have (it could only pull from Telegram channels / specific hosts, not from an
# arbitrary raw file URL).

import os
import re
import shutil
from urllib.parse import urlparse
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    make_output_folder, safe_filename, stream_download, upload_file,
    DEFAULT_HEADERS, E_CHECK, E_CROSS, E_INFO
)
from Akbots.torrent import _aria2c_available
from Akbots.terabox import TERABOX_DOMAINS
from Akbots.premiumlinks import BYPASS_DOMAINS
from Akbots.link_cache import try_send_cached

GENERIC_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

# Matches a trailing "-n <name>" (or "-n \"name with spaces\"") flag used to
# rename the downloaded file, e.g. "https://x.com/video.mp4 -n MyVideo".
CUSTOM_NAME_PATTERN = re.compile(r'(?:^|\s)-n\s+(".+?"|\S+)', re.IGNORECASE)

# Anything already owned by another plugin (or that shouldn't be treated as a
# raw file, like t.me links which are handled by start.py's save handler).
# ftp(s):// links never match GENERIC_URL_PATTERN at all (it's http(s)-only)
# and are handled separately by Akbots/aria2_dl.py — listed here too just
# so a stray "ftp://..." pasted alongside an http(s) link in the same
# message can't be picked up as a raw file by mistake.
_EXCLUDED_DOMAINS = (
    "t.me", "telegram.me",
    "youtube.com", "youtu.be", "instagram.com", "instagr.am",
    "pinterest.", "pin.it",
    "facebook.com", "fb.watch", "fb.com",
    "mega.nz", "drive.google.com", "gofile.io", "mediafire.com",
    "pixeldrain.com", "streamtape.", "stape.", "catbox.moe",
    *TERABOX_DOMAINS,
    *BYPASS_DOMAINS,
    "magnet:", ".torrent",
    "twitter.com", "x.com", "pixiv.net", "deviantart.com", "artstation.com",
    "flickr.com", "tumblr.com", "reddit.com", "imgur.com",
    "danbooru.donmai.us", "gelbooru.com", "konachan.com", "yande.re",
    "safebooru.org", "zerochan.net", "furaffinity.net", "bsky.app",
    "mxplayer.in", "mxplay.com",
    "fembed.com", "fembed-hd.com", "femax20.com", "vanfem.com", "suzihaza.com",
    "embedsito.com", "owodeuwu.xyz", "plusto.link", "watchse.icu", "feurl.com",
    "vk.com", "vk.ru", ".mpd", "ftp://", "ftps://",
)


def _normalize_direct_url(url: str) -> str:
    """Dropbox and OneDrive share links default to serving an HTML preview
    page, not the file itself — a plain GET on them returns 200 OK with a
    small HTML document, which then gets saved (and fails to upload) as if
    it were the real file. Both hosts document a query-param toggle to get
    the raw bytes instead, so rewrite the URL before it ever reaches
    stream_download/aria2c."""
    lower = url.lower()
    if "dropbox.com" in lower:
        # dl=0 (or missing) -> preview page; dl=1 -> raw file.
        if "dl=0" in url:
            return url.replace("dl=0", "dl=1")
        if "dl=1" not in url:
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}dl=1"
        return url
    if "1drv.ms" in lower or "onedrive.live.com" in lower:
        if "download=1" not in lower:
            sep = "&" if "?" in url else "?"
            return f"{url}{sep}download=1"
        return url
    return url


def extract_url(text: str):
    m = GENERIC_URL_PATTERN.search(text)
    if not m:
        return None
    url = m.group(0)
    lower = url.lower()
    if any(d in lower for d in _EXCLUDED_DOMAINS):
        return None
    return _normalize_direct_url(url)


def extract_custom_name(text: str):
    """Pulls the name out of a trailing '-n <name>' flag, if present.
    Quotes are optional and only needed for names containing spaces."""
    m = CUSTOM_NAME_PATTERN.search(text or "")
    if not m:
        return None
    name = m.group(1).strip().strip('"').strip()
    return name or None


def extract_pipe_parts(text: str):
    """Parses the 'url|name' / 'url|name|user|pass' pipe-separated syntax
    (ported from Uploader-Bot-V4) — mainly for private/login-walled direct
    links like a seedbox's HTTP file browser, which need HTTP Basic Auth
    credentials to download at all, e.g.:
        https://box.example.com:8080/file.mkv|MyFile.mkv|myuser|mypass
    A bare 'url|name' (no credentials) also works as an alternative to the
    '-n name' flag. No spaces are expected around the pipes, since
    GENERIC_URL_PATTERN's \\S+ would otherwise swallow the whole pipe group
    as a single "URL" anyway (pipes aren't whitespace).
    Returns (url, name_or_None, user_or_None, pass_or_None) or None."""
    if not text or "|" not in text:
        return None
    token = text.strip().split()[0]
    if "|" not in token:
        return None
    parts = [p.strip() for p in token.split("|")]
    if len(parts) == 2:
        return parts[0], (parts[1] or None), None, None
    if len(parts) == 4:
        return parts[0], (parts[1] or None), (parts[2] or None), (parts[3] or None)
    return None


def apply_custom_name(original_filename: str, custom_name: str) -> str:
    """Renames the auto-detected filename to the user's custom name, while
    keeping the original file extension unless the custom name supplies its
    own (e.g. '-n MyVideo.mkv' overrides the source's .mp4)."""
    _, orig_ext = os.path.splitext(original_filename)
    cust_base, cust_ext = os.path.splitext(custom_name)
    ext = cust_ext if cust_ext else orig_ext
    return safe_filename(f"{cust_base}{ext}", original_filename)


async def _try_jd_then_error(client: Client, message: Message, status, url: str, original_error: Exception) -> bool:
    """Last resort before giving up: JDownloader covers hundreds of hosts
    (premium one-click hosters, click'n'load containers, etc.) neither
    aria2c nor plain HTTP can. If it's connected and picks this link up
    successfully, it already finished uploading by the time this returns
    True. Otherwise shows the ORIGINAL error (not JD's) since that's the
    one that actually describes what this link needed."""
    try:
        from Akbots.jdownloader import try_jd_fallback
        if await try_jd_fallback(client, message, status, url):
            return True
    except Exception:
        pass
    await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{original_error}</code>", parse_mode=enums.ParseMode.HTML)
    return False


async def _handle(client: Client, message: Message, url: str, custom_name: str = None,
                   username: str = None, password: str = None):
    status = await message.reply_text(
        f"<b>{E_INFO} Link detected, downloading...</b>", parse_mode=enums.ParseMode.HTML
    )
    # A cached upload was saved under the *original* filename — reusing it
    # for a custom-named request would silently ignore the rename, so skip
    # the cache lookup (and, further down, the cache store) whenever the
    # user asked for a custom name. Same for credentialed links: caching
    # would let a later, different request reuse a private file's upload.
    skip_cache = bool(custom_name or username)
    if not skip_cache and await try_send_cached(client, message, url, status):
        return
    filename = safe_filename(url.split("/")[-1].split("?")[0], "downloaded_file")
    if custom_name:
        filename = apply_custom_name(filename, custom_name)
    cache_url = url if not skip_cache else None

    # Prefer aria2c when it's installed — unlike the aiohttp streamer below,
    # it keeps a .aria2 control file next to the partial download, so if the
    # connection drops mid-transfer, retrying continues from where it left
    # off instead of starting the whole file over from byte 0.
    if _aria2c_available():
        from Akbots.aria2_dl import aria2c_download
        # message.id is only unique WITHIN a single chat, not globally, so two
        # users whose messages happen to share an id would otherwise collide;
        # include chat.id to keep folders globally unique.
        folder = os.path.join("downloads", "urluploader", f"task_{message.chat.id}_{message.id}")
        try:
            path = await aria2c_download(url, folder, status, label="Downloading (resumable)",
                                          out_name=f"{message.id}_{filename}",
                                          user_id=message.from_user.id, queue_label="URL upload",
                                          username=username, password=password)
            await upload_file(
                client, message, path, status,
                f"<b>{E_CHECK} Uploaded</b>\n<code>{filename}</code>",
                file_name=filename, cache_url=cache_url
            )
        except Exception as e:
            await _try_jd_then_error(client, message, status, url, e)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        return

    # Fallback: aria2c not installed on this host — old aiohttp streamer,
    # no resume, but still works for a plain one-shot download.
    folder = make_output_folder("urluploader")
    dest = f"{folder}/{message.id}_{filename}"

    # Many direct-file hosts/CDNs use hotlink protection that checks the
    # Referer header — without one, they serve an HTML "access denied" or
    # redirect page instead of the actual file, which is what surfaces as
    # "Server returned 'text/html'..." even for otherwise-valid links.
    # A same-origin Referer satisfies most of these checks and is a no-op
    # for hosts that don't care about it.
    parsed = urlparse(url)
    headers = dict(DEFAULT_HEADERS)
    if parsed.scheme and parsed.netloc:
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

    auth = (username, password) if username else None
    try:
        await stream_download(url, dest, status, "Downloading File", headers=headers,
                               user_id=message.from_user.id, file_name=filename, auth=auth)
        await upload_file(
            client, message, dest, status,
            f"<b>{E_CHECK} Uploaded</b>\n<code>{filename}</code>",
            file_name=filename, cache_url=cache_url
        )
    except Exception as e:
        await _try_jd_then_error(client, message, status, url, e)


@Client.on_message(
    filters.text & filters.private & filters.regex(GENERIC_URL_PATTERN) & ~filters.regex(r"^/"),
    group=4,  # absolute last resort: after specific-host handlers (1), yt-dlp's generic
              # fallback (2), and gallery-dl's generic fallback (3)
)
async def generic_url_auto_detect(client: Client, message: Message):
    pipe = extract_pipe_parts(message.text)
    if pipe:
        raw_url, custom_name, username, password = pipe
        url = extract_url(raw_url) or raw_url
    else:
        url = extract_url(message.text)
        custom_name = extract_custom_name(message.text)
        username = password = None
    if not url:
        return

    # If yt-dlp or gallery-dl already recognises this as a media page, their
    # own generic fallbacks (group=2 / group=3) already handled it — don't
    # double-handle it here as a raw file. Skipped for credentialed pipe
    # links, since those are private links yt-dlp/gallery-dl can't probe
    # anyway (no auth) and would just misreport as "unsupported".
    if not username:
        try:
            from Akbots.ytdl import has_quality_formats
            if await has_quality_formats(url):
                return
        except Exception:
            pass
        try:
            from Akbots.gallery import _gallery_supports
            if await _gallery_supports(url):
                return
        except Exception:
            pass

    await _handle(client, message, url, custom_name=custom_name, username=username, password=password)


@Client.on_message(filters.command(["url", "direct"]) & filters.private)
async def url_upload_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/url &lt;direct download link&gt; [-n name]</code>\n"
            f"<i>Downloads any direct link and uploads it to Telegram.</i>\n\n"
            f"<b>Example:</b> <code>/url https://example.com/video.mp4 -n MyVideo</code>\n\n"
            f"<b>Private/login-walled link (HTTP Basic Auth):</b>\n"
            f"<code>/url https://host:port/file.mkv|MyFile.mkv|user|pass</code>",
            parse_mode=enums.ParseMode.HTML
        )
    raw = message.text.split(None, 1)[1].strip()
    pipe = extract_pipe_parts(raw)
    if pipe:
        raw_url, custom_name, username, password = pipe
        url = extract_url(raw_url) or raw_url
    else:
        custom_name = extract_custom_name(raw)
        url = extract_url(raw) or raw
        username = password = None
    await _handle(client, message, url, custom_name=custom_name, username=username, password=password)
