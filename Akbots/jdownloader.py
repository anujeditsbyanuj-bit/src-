"""
JDownloader (/jd) — covers hundreds of file hosts yt-dlp and gallery-dl
don't know about: premium one-click hosters (rapidgator, etc.), click'n'load
(.dlc) containers, and generic crawled links.

Two ways in:
1. Manual: /jd <link> — always tries JDownloader directly.
2. Automatic fallback: urluploader.py calls try_jd_fallback() as the true
   last resort, AFTER yt-dlp, gallery-dl, aria2c, and plain HTTP have all
   already failed on a link. This keeps JD from being hit on every random
   pasted link (it's one shared background process) while still covering
   sites nothing else in the bot understands.

Adapted from mirror-leech-telegram-bot's jd_download.py, stripped down from
their task-queue/listener architecture to a single self-contained
add-link -> wait -> download -> upload flow that fits this bot's plain
per-message handler style.
"""

import os
import shutil
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.direct_utils import upload_file, format_progress, E_CHECK, E_CROSS, E_INFO, E_ROCKET
from Akbots.jdownloader_core import jdownloader
from config import JD_DOWNLOAD_DIR, ADMINS

_COLLECT_TIMEOUT = 90       # seconds to wait for JD to "grab" link metadata
_DOWNLOAD_TIMEOUT = 6 * 3600  # 6h safety cap so a stuck task doesn't hang forever


async def _get_online_packages(uuids_hint=None):
    """Polls the linkgrabber until the freshly-added link(s) resolve to at
    least one online package, or the collect timeout elapses."""
    loop = asyncio.get_event_loop()
    start = loop.time()
    while loop.time() - start < _COLLECT_TIMEOUT:
        packs = await jdownloader.device.linkgrabber.query_packages([
            {"bytesTotal": True, "availableOnlineCount": True, "availableOfflineCount": True}
        ])
        online = [p["uuid"] for p in packs if p.get("onlineCount", 1) != 0]
        if online:
            return online
        await asyncio.sleep(1)
    return []


async def _run_jd_download(client: Client, message: Message, status, link: str):
    """Core add-link -> wait -> download -> upload flow. Raises on any
    failure; reuses an existing status message so callers (like the /jd
    command, or another module's own fallback chain) can share one message
    across attempts instead of spamming a new one per method tried."""
    dest_folder = os.path.join(JD_DOWNLOAD_DIR, f"task_{message.chat.id}_{message.id}")
    online_packages = []

    try:
        # Clear out any stray queued packages from a previous crashed/aborted
        # task first, so they don't get swept up as "ours" below.
        if existing := await jdownloader.device.linkgrabber.query_packages([{}]):
            stray_ids = [p["uuid"] for p in existing]
            if stray_ids:
                await jdownloader.device.linkgrabber.remove_links(package_ids=stray_ids)

        await jdownloader.device.linkgrabber.add_links([
            {"autoExtract": False, "links": link, "deepDecrypt": True}
        ])
        await asyncio.sleep(1)
        while await jdownloader.device.linkgrabber.is_collecting():
            await asyncio.sleep(0.5)

        online_packages = await _get_online_packages()
        if not online_packages:
            raise RuntimeError(
                "JDownloader couldn't resolve this link (offline, unsupported, "
                "or needs a premium account linked in JDownloader)."
            )

        await jdownloader.device.linkgrabber.set_download_directory(dest_folder, online_packages)
        await jdownloader.device.linkgrabber.move_to_downloadlist(package_ids=online_packages)
        await asyncio.sleep(0.5)
        await jdownloader.device.downloads.force_download(package_ids=online_packages)

        loop = asyncio.get_event_loop()
        poll_start = loop.time()
        last_edit = 0.0
        await status.edit_text(f"<b>{E_ROCKET} Downloading via JDownloader...</b>", parse_mode=enums.ParseMode.HTML)

        while True:
            await asyncio.sleep(3)
            packs = await jdownloader.device.downloads.query_packages([
                {"bytesTotal": True, "bytesLoaded": True, "finished": True, "speed": True}
            ])
            mine = [p for p in packs if p["uuid"] in online_packages]
            if not mine:
                if loop.time() - poll_start > 30:
                    raise RuntimeError("Download vanished from JDownloader's queue (removed manually?).")
                continue

            total = sum(p.get("bytesTotal", 0) for p in mine)
            done = sum(p.get("bytesLoaded", 0) for p in mine)
            speed = sum(p.get("speed", 0) for p in mine)
            now = loop.time()
            if now - last_edit >= 3:
                last_edit = now
                elapsed = now - poll_start
                pct = (done / total * 100) if total else 0
                eta = ((total - done) / speed) if speed else None
                try:
                    await status.edit_text(
                        format_progress(pct, speed_bps=speed, done_bytes=done, total_bytes=total or None,
                                         elapsed_secs=elapsed, eta_secs=eta, title="Downloading via JDownloader"),
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass

            if all(p.get("finished") for p in mine):
                break
            if now - poll_start > _DOWNLOAD_TIMEOUT:
                raise RuntimeError("Download timed out.")

    except Exception:
        if online_packages:
            try:
                await jdownloader.device.downloads.remove_links(package_ids=online_packages)
            except Exception:
                pass
        shutil.rmtree(dest_folder, ignore_errors=True)
        raise

    files = []
    for root, _, fnames in os.walk(dest_folder):
        for f in fnames:
            files.append(os.path.join(root, f))
    files.sort()

    if not files:
        shutil.rmtree(dest_folder, ignore_errors=True)
        raise RuntimeError("No file was downloaded.")

    for i, path in enumerate(files):
        fname = os.path.basename(path)
        await upload_file(client, message, path, status, f"<b>{E_CHECK} JDownloader File</b>\n<code>{fname}</code>")
        if i < len(files) - 1:
            status = await message.reply_text(f"<b>{E_INFO} Uploading next file...</b>", parse_mode=enums.ParseMode.HTML)

    shutil.rmtree(dest_folder, ignore_errors=True)


async def try_jd_fallback(client: Client, message: Message, status, url: str) -> bool:
    """For OTHER modules' fallback chains (currently urluploader.py's raw-
    download last resort). Returns True if JDownloader picked up the link
    and successfully delivered it — status is already updated in that case.
    Returns False silently (status untouched) if JD isn't connected or
    can't handle this particular link, so the caller can show its own
    error instead."""
    if not jdownloader.is_connected:
        return False
    try:
        await status.edit_text(f"<b>{E_INFO} Trying JDownloader...</b>", parse_mode=enums.ParseMode.HTML)
        await _run_jd_download(client, message, status, url)
        return True
    except Exception:
        return False


async def _handle(client: Client, message: Message, link: str):
    if not jdownloader.is_connected:
        return await message.reply_text(
            f"<b>{E_CROSS} JDownloader isn't connected.</b>\n<code>{jdownloader.error}</code>\n"
            f"<i>Ask an admin to check /jdstatus.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    status = await message.reply_text(f"<b>{E_INFO} Sending link to JDownloader...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        await _run_jd_download(client, message, status, link)
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} JDownloader error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("jd") & filters.private)
async def jd_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/jd &lt;link&gt;</code>\n"
            f"<i>Downloads via JDownloader — covers hundreds of file hosts yt-dlp and "
            f"gallery-dl don't (premium hosters, click'n'load containers, etc).</i>",
            parse_mode=enums.ParseMode.HTML
        )
    link = message.text.split(None, 1)[1].strip()
    await _handle(client, message, link)


@Client.on_message(filters.command("jdstatus") & filters.user(ADMINS))
async def jd_status_command(client: Client, message: Message):
    if jdownloader.is_connected:
        text = f"<b>{E_CHECK} JDownloader is connected and ready.</b>"
    else:
        text = f"<b>{E_CROSS} JDownloader is not connected.</b>\n<code>{jdownloader.error}</code>"
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
