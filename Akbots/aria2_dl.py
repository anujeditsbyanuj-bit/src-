# FTP Support + Resumable Direct-HTTP Downloads (aria2c)
#
# Two related gaps in one module:
#
#   - FTP (ftp:// / ftps://) links — Akbots/direct_utils.py's stream_download()
#     is built on aiohttp, which only speaks HTTP(S). aria2c speaks FTP natively,
#     so that's what this module shells out to for ftp(s):// links.
#
#   - Resume for direct HTTP(S) downloads — stream_download() always starts a
#     retry from byte 0, since it has no concept of a partial-file marker.
#     aria2c keeps a `.aria2` control file next to the partial download and,
#     when re-invoked with --continue=true on the same output path, picks up
#     exactly where a failed/interrupted attempt left off instead of
#     re-downloading everything.
#
# Both cases reuse the exact same aria2c invocation, since aria2c handles
# HTTP(S)/FTP/FTPS through one unified interface — this file is effectively
# Akbots/torrent.py's aria2c plumbing, minus the BitTorrent-specific flags,
# reused for plain file transfers.

import os
import re
import shutil
from urllib.parse import urlparse, unquote
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import (
    safe_filename, upload_file, run_subprocess_with_progress,
    E_CHECK, E_CROSS, E_INFO,
)
from Akbots.torrent import _parse_aria2_line, _aria2c_available
from Akbots.link_cache import try_send_cached

FTP_PATTERN = re.compile(r"ftps?://\S+", re.IGNORECASE)


def extract_ftp_link(text: str):
    m = FTP_PATTERN.search(text)
    return m.group(0) if m else None


def _guess_name(url: str) -> str:
    path = urlparse(url).path
    name = unquote(os.path.basename(path.rstrip("/")))
    return safe_filename(name, "downloaded_file")


_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def aria2c_download(url: str, task_folder: str, status, label: str = "Downloading",
                           out_name: str = None, user_id: int = None, queue_label: str = None,
                           username: str = None, password: str = None) -> str:
    """Downloads `url` (http(s):// or ftp(s)://) into `task_folder` via
    aria2c, with resume enabled. If aria2c is re-run against the same
    task_folder after a partial/failed attempt, it continues from the
    existing .aria2 control file rather than restarting.

    username/password, if given, are sent as HTTP Basic Auth (or FTP
    credentials for ftp(s):// URLs) — for private/login-walled direct links
    (e.g. a seedbox's HTTP file browser) that 401/403 without them.

    Returns the path to the downloaded file. Raises RuntimeError on failure.
    """
    os.makedirs(task_folder, exist_ok=True)
    name = out_name or _guess_name(url)

    cmd = [
        "aria2c",
        f"--dir={task_folder}",
        f"--out={name}",
        "--continue=true",             # resume support — the whole point of this module
        "--max-tries=5",
        "--retry-wait=3",
        "--max-connection-per-server=4",
        "--split=4",
        "--min-split-size=1M",
        "--summary-interval=5",
        "--console-log-level=warn",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",      # needed so a resumed run can overwrite/extend the .aria2 partial
        # aria2c's own default User-Agent ("aria2/x.x.x") gets flatly
        # rejected (403/errorCode=22) by a lot of CDNs and hotlink-protected
        # hosts that only check for *a* normal-looking browser UA - a
        # same-origin Referer covers the rest that check that too.
        f"--user-agent={_DESKTOP_UA}",
    ]
    if url.lower().startswith("http"):
        try:
            p = urlparse(url)
            if p.scheme and p.netloc:
                cmd.append(f"--referer={p.scheme}://{p.netloc}/")
        except Exception:
            pass
    if username:
        if url.lower().startswith("ftp"):
            cmd.append(f"--ftp-user={username}")
            cmd.append(f"--ftp-passwd={password or ''}")
        else:
            cmd.append(f"--http-user={username}")
            cmd.append(f"--http-passwd={password or ''}")
    cmd.append(url)

    returncode, tail = await run_subprocess_with_progress(
        cmd, status, label, _parse_aria2_line,
        user_id=user_id, queue_label=queue_label,
    )
    if returncode != 0 and "errorCode=22" in (tail or "") and any(a.startswith("--referer=") for a in cmd):
        # errorCode=22 = "the response status is not successful" - often the
        # ORIGINAL host redirected to a different CDN/S3 host that rejects
        # our same-origin Referer as unexpected on its presigned URL. Retry
        # once with the Referer stripped entirely.
        cmd = [a for a in cmd if not a.startswith("--referer=")]
        returncode, tail = await run_subprocess_with_progress(
            cmd, status, label, _parse_aria2_line,
            user_id=user_id, queue_label=queue_label,
        )
    if returncode != 0:
        err = tail[:300] or f"aria2c exited with code {returncode}"
        raise RuntimeError(err)

    path = os.path.join(task_folder, name)
    if not os.path.exists(path):
        # Server-provided filename (Content-Disposition) can differ from our
        # guess — fall back to whatever aria2c actually produced.
        candidates = [f for f in os.listdir(task_folder) if not f.endswith(".aria2")]
        if not candidates:
            raise RuntimeError("aria2c reported success but no file was found")
        path = os.path.join(task_folder, candidates[0])
    return path


async def _handle(client: Client, message: Message, url: str, label_prefix: str = "File"):
    status = await message.reply_text(
        f"<b>{E_INFO} Link detected, downloading via aria2c (resumable)...</b>",
        parse_mode=enums.ParseMode.HTML,
    )
    if await try_send_cached(client, message, url, status):
        return

    if not _aria2c_available():
        return await status.edit_text(
            f"<b>{E_CROSS} 'aria2c' is not installed on this host.</b>\n"
            f"<i>Install it first (Debian/Ubuntu: <code>apt install aria2</code>) "
            f"then FTP links and resumable downloads will work.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    # message.id is only unique WITHIN a single chat, not globally, so two
    # users whose messages happen to share an id would otherwise collide;
    # include chat.id to keep folders globally unique.
    folder = os.path.join("downloads", "direct", f"task_{message.chat.id}_{message.id}")
    try:
        path = await aria2c_download(
            url, folder, status, label="Downloading (resumable)",
            user_id=message.from_user.id, queue_label="Direct/FTP download",
        )
        fname = os.path.basename(path)
        await upload_file(client, message, path, status, f"<b>{E_CHECK} {label_prefix}</b>\n<code>{fname}</code>", file_name=fname, cache_url=url)
    except Exception as e:
        msg = str(e)
        status_m = re.search(r"status=(\d{3})", msg)
        hint = ""
        if status_m:
            code = status_m.group(1)
            hint = {
                "403": "\n<i>Server is blocking the download (hotlink/anti-bot protection). Link may need cookies, or the source has geo/IP restrictions.</i>",
                "429": "\n<i>Rate-limited — too many requests to this server. Wait a bit and retry, or it'll auto-retry with fewer connections next time.</i>",
                "451": "\n<i>Blocked for legal reasons (geographic/DMCA restriction) — aria2c can't get around this.</i>",
                "421": "\n<i>Server is rejecting connections from this IP, likely due to too many parallel requests — try again in a few minutes.</i>",
            }.get(code, "")
        await status.edit_text(f"<b>{E_CROSS} Error:</b>\n<code>{msg[:300]}</code>{hint}", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@Client.on_message(
    filters.text & filters.private & filters.regex(FTP_PATTERN) & ~filters.regex(r"^/"),
    group=1,  # unambiguous scheme — handle before any generic http(s) fallback
)
async def ftp_auto_detect(client: Client, message: Message):
    link = extract_ftp_link(message.text)
    if link:
        await _handle(client, message, link, label_prefix="FTP File")


@Client.on_message(filters.command(["ftp"]) & filters.private)
async def ftp_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/ftp &lt;ftp://... or ftps://... link&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    raw = message.text.split(None, 1)[1].strip()
    link = extract_ftp_link(raw) or raw
    await _handle(client, message, link, label_prefix="FTP File")
