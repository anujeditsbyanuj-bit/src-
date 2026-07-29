import re
import os
import glob
import shutil
import asyncio
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

    await status.edit_text(f"<b>{E_INFO} Downloading gallery...</b>", parse_mode=enums.ParseMode.HTML)

    cmd = ["gallery-dl", "--directory", gallery_dir, "--no-mtime", url]
    returncode, tail = await run_subprocess_with_progress(
        cmd, status, "Downloading gallery", _make_gallery_line_parser(), interval=3.0,
        user_id=message.from_user.id, queue_label="Gallery download",
    )

    if returncode != 0:
        err = tail[:300] or f"gallery-dl exited with code {returncode}"
        return await status.edit_text(f"<b>{E_CROSS} Gallery download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)

    exts = ("*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.mp4", "*.webm", "*.mkv")
    files = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(gallery_dir, "**", ext), recursive=True))
    files.sort()

    if not files:
        return await status.edit_text(f"<b>{E_CROSS} No media found at this link.</b>", parse_mode=enums.ParseMode.HTML)

    total = len(files)
    for i, path in enumerate(files, 1):
        fname = os.path.basename(path)
        await upload_file(client, message, path, status, f"<b>{E_CHECK} Gallery ({i}/{total})</b>\n<code>{fname}</code>", file_name=fname)
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
