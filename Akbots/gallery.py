import re
import os
import glob
import shutil
import asyncio
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import make_output_folder, upload_file, run_subprocess_with_progress, E_CHECK, E_CROSS, E_INFO

GALLERY_SITES = [
    "twitter.com", "x.com", "pinterest.com", "pixiv.net", "deviantart.com",
    "artstation.com", "flickr.com", "tumblr.com", "reddit.com", "imgur.com",
    "danbooru.donmai.us", "gelbooru.com", "konachan.com", "yande.re",
    "safebooru.org", "zerochan.net", "furaffinity.net", "bsky.app",
]

# Pinterest is deliberately excluded here — ytdl.py already has its own
# Pinterest auto-detect that probes each link first (video pin -> yt-dlp,
# image pin/board -> gallery._handle here). Matching pinterest.com again in
# this file's own auto-detect would double-fire both handlers on one message.
_AUTO_DETECT_SITES = [s for s in GALLERY_SITES if s != "pinterest.com"]
GALLERY_PATTERN = re.compile(
    r"(https?://)?(www\.)?(" + "|".join(re.escape(s) for s in _AUTO_DETECT_SITES) + r")/\S+",
    re.IGNORECASE,
)


def extract_url(text: str):
    text = text.strip()
    if not text.startswith("http"):
        return None
    lower = text.lower()
    return text if any(site in lower for site in GALLERY_SITES) else None


def _gallery_dl_available() -> bool:
    return shutil.which("gallery-dl") is not None


async def _gallery_supports(url: str) -> bool:
    """Silent probe: does gallery-dl recognise this URL at all? Uses
    --simulate so it only lists what it would grab without downloading
    anything, letting the generic auto-detect handler decide whether to
    claim the message *before* posting any reply (so a link gallery-dl
    doesn't support can fall through to the next fallback instead of
    getting a dead-end 'download failed' reply)."""
    if not _gallery_dl_available():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "gallery-dl", "--simulate", "-q", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        return proc.returncode == 0 and bool(stdout.strip())
    except Exception:
        return False


def _fetch_reddit_post_sync(url: str) -> dict:
    """Reddit's public JSON API — no login needed for public subreddits.
    Just needs a real User-Agent or reddit answers with 429s."""
    json_url = url.split("?")[0].rstrip("/") + ".json"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AkbotRedditFetch/1.0)"}
    resp = requests.get(json_url, headers=headers, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    return data[0]["data"]["children"][0]["data"]


async def _try_reddit_text_post(message: Message, url: str, status) -> bool:
    """gallery-dl only ever grabs media (images/gifs/videos) — text-only
    Reddit posts (is_self=True, no attached media) land here as a fallback
    instead of a dead-end 'no media found' error. Returns True if this WAS
    a text post and was handled (message already edited/sent)."""
    try:
        post = await asyncio.to_thread(_fetch_reddit_post_sync, url)
    except Exception:
        return False
    if not post or not post.get("is_self"):
        return False  # link post / media post gallery-dl should have handled

    title = post.get("title") or "(no title)"
    selftext = (post.get("selftext") or "").strip()
    sub = post.get("subreddit_name_prefixed") or ""
    author = post.get("author") or "unknown"
    body = selftext if selftext else "<i>(no body text)</i>"

    header = f"<blockquote><b>{E_CHECK} Reddit Text Post</b>\n{sub} • u/{author}</blockquote>\n\n<b>{title}</b>\n\n"
    MAX = 4000  # stay under Telegram's 4096-char single-message cap

    full = header + body
    if len(full) <= MAX:
        await status.edit_text(full, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    else:
        await status.edit_text(header[:MAX], parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
        remaining = body
        while remaining:
            chunk, remaining = remaining[:MAX], remaining[MAX:]
            await message.reply_text(chunk, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)
    return True


def _guess_gallery_kind(url: str) -> str:
    path = url.split("?")[0].lower()
    if path.endswith((".mp4", ".webm", ".mkv", ".mov", ".m3u8")):
        return "video"
    if path.endswith(".gif"):
        return "gif"
    return "photo"


def _head_size_sync(url: str):
    """Best-effort Content-Length via HEAD, falling back to a 1-byte
    ranged GET for hosts that don't answer HEAD properly. None if it
    genuinely can't be determined — the preview just omits it."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.head(url, headers=headers, timeout=10, allow_redirects=True)
        cl = r.headers.get("Content-Length")
        if cl:
            return int(cl)
    except Exception:
        pass
    try:
        r = requests.get(url, headers={**headers, "Range": "bytes=0-0"}, timeout=10, stream=True)
        cr = r.headers.get("Content-Range")  # "bytes 0-0/12345"
        if cr and "/" in cr:
            return int(cr.rsplit("/", 1)[1])
    except Exception:
        pass
    return None


async def _gallery_probe_urls(url: str):
    """gallery-dl -g prints the URL(s) it would download without actually
    downloading anything — lets a size/type preview be shown up front, the
    same idea as ytdl.py's quality picker but for gallery sites."""
    if not _gallery_dl_available():
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            "gallery-dl", "-g", "-q", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        if proc.returncode != 0:
            return []
        return [line.strip() for line in stdout.decode(errors="ignore").splitlines() if line.strip()]
    except Exception:
        return []


async def _build_gallery_preview(urls, title: str, subtitle: str = None) -> str:
    from Akbots.direct_utils import fmt_bytes
    kinds = [_guess_gallery_kind(u) for u in urls]
    sizes = await asyncio.gather(*[asyncio.to_thread(_head_size_sync, u) for u in urls])

    icon = {"video": "🎥", "gif": "🎞️", "photo": "🖼️"}
    noun = {"video": "Video", "gif": "GIF", "photo": "Photo"}

    lines = [f"<b>{title}</b>"]
    if subtitle:
        lines.append(subtitle)
    lines.append("")

    total = 0
    for i, (kind, size) in enumerate(zip(kinds, sizes), start=1):
        size_txt = f" — {fmt_bytes(size)}" if size else ""
        prefix = "" if len(urls) == 1 else f"{i}. "
        lines.append(f"{icon[kind]} {prefix}{noun[kind]}{size_txt}")
        if size:
            total += size

    if len(urls) > 1:
        lines.append("")
        lines.append(f"📦 {len(urls)} item(s)" + (f" • ~{fmt_bytes(total)} total" if total else ""))

    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def _make_gallery_line_parser():
    """gallery-dl prints one line per downloaded file by default (its
    destination path). There's no single percentage for a whole gallery
    (total count isn't known upfront), so we report a running file count
    instead of a percentage — still gives live feedback instead of a frozen
    message, in the same visual style as the other downloaders."""
    from Akbots.direct_utils import E_BOLT, E_CLOCK, fmt_duration
    state = {"count": 0}

    def parse(line: str, elapsed: float):
        if not line or line.startswith(("[", "#")):
            return None  # skip gallery-dl's own log/warning lines
        state["count"] += 1
        return (
            f"<b>{E_BOLT} Downloading gallery...</b>\n\n"
            f"<b>Progress:</b> {state['count']} file(s) downloaded so far\n"
            f"{E_CLOCK} <b>Elapsed:</b> {fmt_duration(elapsed)}"
        )

    return parse


async def _handle(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} Gallery link detected...</b>", parse_mode=enums.ParseMode.HTML)

    if not _gallery_dl_available():
        return await status.edit_text(
            f"<b>{E_CROSS} 'gallery-dl' is not installed.</b>\n"
            f"<i>Install it first: <code>pip install gallery-dl</code></i>",
            parse_mode=enums.ParseMode.HTML
        )

    base = make_output_folder("gallery")
    # message.id is only unique WITHIN a single chat, not globally, so two
    # users whose messages happen to share an id would otherwise collide;
    # include chat.id to keep folders globally unique.
    gallery_dir = os.path.join(base, f"g_{message.chat.id}_{message.id}")
    os.makedirs(gallery_dir, exist_ok=True)

    probe_urls = await _gallery_probe_urls(url)
    if probe_urls:
        site = next((s.split(".")[0].capitalize() for s in GALLERY_SITES if s in url.lower()), "Gallery")
        preview = await _build_gallery_preview(probe_urls, f"{E_INFO} {site} Post")
        await status.edit_text(preview, parse_mode=enums.ParseMode.HTML)
        await asyncio.sleep(1.2)

    await status.edit_text(f"<b>{E_INFO} Downloading gallery...</b>", parse_mode=enums.ParseMode.HTML)

    cmd = ["gallery-dl", "--directory", gallery_dir, "--no-mtime", url]
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, "Downloading gallery", _make_gallery_line_parser(), interval=3.0,
        user_id=message.from_user.id, queue_label="Gallery download",
    )

    if returncode != 0:
        if "reddit.com" in url.lower() and await _try_reddit_text_post(message, url, status):
            shutil.rmtree(gallery_dir, ignore_errors=True)
            return
        err = tail[:300] or f"gallery-dl exited with code {returncode}"
        return await status.edit_text(f"<b>{E_CROSS} Gallery download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

    exts = ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.mp4", "*.webm", "*.mkv")
    files = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(gallery_dir, "**", ext), recursive=True))
    files.sort()

    if not files:
        if "reddit.com" in url.lower() and await _try_reddit_text_post(message, url, status):
            shutil.rmtree(gallery_dir, ignore_errors=True)
            return
        return await status.edit_text(f"<b>{E_CROSS} No media found at this link.</b>", parse_mode=enums.ParseMode.HTML)

    total = len(files)
    for i, path in enumerate(files, 1):
        fname = os.path.basename(path)
        await upload_file(client, message, path, status, f"<blockquote><b>{E_CHECK} Gallery ({i}/{total})</b>\n<code>{fname}</code></blockquote>", file_name=fname)
        if i < total:
            status = await message.reply_text(f"<b>{E_INFO} Uploading {i + 1}/{total}...</b>", parse_mode=enums.ParseMode.HTML)

    shutil.rmtree(gallery_dir, ignore_errors=True)


def _extract_auto_url(text: str):
    m = GALLERY_PATTERN.search(text)
    return m.group(0) if m else None


async def _route(client: Client, message: Message, url: str):
    """Several gallery sites (Twitter/X, Reddit, Tumblr, Bluesky, ...) host
    both images AND video posts. gallery-dl handles the image case well but
    for video posts it often only grabs the static poster/preview frame,
    not the actual video — so probe with yt-dlp first and hand genuine
    video links off to the proper quality-picker/video downloader. Only
    falls through to gallery-dl for actual image/gallery content."""
    try:
        from Akbots.ytdl import has_quality_formats, _show_quality_picker
        if await has_quality_formats(url):
            return await _show_quality_picker(client, message, url)
    except Exception:
        pass
    await _handle(client, message, url)


# Bare gallery-site link (Twitter/X, Reddit, Tumblr, Pixiv, DeviantArt, etc.)
# pasted with no /gallery command. Same pattern as the other auto-detect
# handlers (mega.py, gdrive.py, terabox.py, ...). Pinterest excluded — see
# note on GALLERY_PATTERN above.
@Client.on_message(
    filters.text & filters.private & filters.regex(GALLERY_PATTERN) & ~filters.regex(r"^/"),
    group=1,
)
async def gallery_auto_detect(client: Client, message: Message):
    url = _extract_auto_url(message.text)
    if url:
        await _route(client, message, url)


@Client.on_message(filters.command("gallery") & filters.private)
async def gallery_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/gallery &lt;link&gt;</code>\n"
            f"<i>Twitter/X, Pinterest, Reddit, Tumblr, Pixiv, DeviantArt and a few others "
            f"already auto-detect when just pasted — this command is only needed for the "
            f"other 200+ gallery-dl-supported sites without their own auto-detect.</i>",
            parse_mode=enums.ParseMode.HTML
        )
    url = extract_url(message.command[1]) or message.command[1]
    await _route(client, message, url)


# Generic gallery-dl fallback: any link NOT already claimed by the specific
# host handlers above (group=1) or yt-dlp's own generic fallback (group=2 in
# ytdl.py) gets silently probed with gallery-dl — it supports 200+ sites,
# far more than the hardcoded GALLERY_SITES list above. If gallery-dl
# doesn't recognise it either, nothing is posted here and the message falls
# through to urluploader.py's raw-file last resort (group=4).
_GENERIC_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)


def _non_gallery_domains():
    from Akbots.ytdl import _EXCLUDED_DOMAINS as _ytdl_excluded
    return set(_ytdl_excluded) | set(GALLERY_SITES) | {"t.me", "telegram.me"}


def _dedicated_gallery_extractor(url: str):
    """Cheap, offline check (no network call) for whether gallery-dl has a
    NAMED extractor for this URL - not its generic/directlink fallback.
    Mirrors ytdl.py's _dedicated_extractor_ie(): lets each tool claim only
    what it actually owns by name, instead of one tool's generic scraper
    shadowing the other tool's proper, dedicated support for a site."""
    try:
        import gallery_dl.extractor as gdl_extractor
        extr = gdl_extractor.find(url)
        if extr is None:
            return None
        category = getattr(extr, "category", None) or extr.__class__.__name__
        if category in ("generic", "directlink"):
            return None
        return category
    except Exception:
        return None


@Client.on_message(
    filters.text & filters.private & filters.regex(_GENERIC_URL_PATTERN) & ~filters.regex(r"^/"),
    group=3,  # after specific-host handlers (1) and yt-dlp's generic fallback (2)
)
async def gallery_generic_auto_detect(client: Client, message: Message):
    m = _GENERIC_URL_PATTERN.search(message.text)
    if not m:
        return
    url = m.group(0)
    lower = url.lower()
    if any(d in lower for d in _non_gallery_domains()):
        return  # already owned by another handler

    # Ownership-first routing: if yt-dlp has a NAMED extractor for this
    # domain, it's a yt-dlp site full stop - ytdl.py's group=2 either
    # already showed the quality picker or surfaced the real error and
    # stopped propagation, so this is just a safety net in case that
    # didn't run. Never hand a known yt-dlp site to gallery-dl.
    try:
        from Akbots.ytdl import _dedicated_extractor_ie
        if _dedicated_extractor_ie(url):
            return
    except Exception:
        pass

    # If gallery-dl has a NAMED extractor for this domain, it's unambiguously
    # gallery-dl's site - go straight to it, no need to waste time re-probing
    # yt-dlp first (yt-dlp's generic HTML scraper has no business here).
    if _dedicated_gallery_extractor(url):
        return await _handle(client, message, url)

    # Neither tool recognises this domain by name - truly unknown site.
    # Last-resort trial: has yt-dlp's generic extractor already grabbed it
    # in group=2? (redundant re-check, cheap safety net) then does
    # gallery-dl's own probe find anything?
    try:
        from Akbots.ytdl import has_quality_formats
        if await has_quality_formats(url):
            return  # ytdl.py's group=2 already offered a quality picker for this
    except Exception:
        pass

    if not await _gallery_supports(url):
        return  # gallery-dl doesn't know this site either — let the raw-file fallback try

    await _handle(client, message, url)
