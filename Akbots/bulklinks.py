# Akbots - Don't Remove Credit - @AkBots_Official
#
# Bulk Links Downloader — /bulk
#
#   /bulk
#   https://example.com/file1.zip
#   https://example.com/file2.zip MyCustomName
#   https://example.com/file3.zip
#
# One link per line (message text after the command, or reply to a
# message containing multiple links). Optionally add a custom filename
# after a link, separated by a space. Each link is downloaded and
# uploaded one at a time — sequentially, not in parallel — reusing
# urluploader.py's existing _handle() so aria2c resumable downloads,
# link caching, and upload_file()'s auto-split all just work, same as a
# single /url call.
#
# Existing url.py-style link detection just shows a "Bulk URL Downloader"
# placeholder with no real implementation; this is the actual worker.

import re
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from database.db import db
from Akbots.urluploader import _handle as _download_one

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN  = '<emoji id=5447644880824181073>⚠️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_PKG   = '📦'

MAX_BULK_LINKS = 25
URL_RE = re.compile(r"^(https?://\S+)(?:\s+(.+))?$")


def _extract_links(text: str):
    links = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = URL_RE.match(line)
        if m:
            url, custom_name = m.group(1), (m.group(2) or "").strip() or None
            links.append((url, custom_name))
    return links


@Client.on_message(filters.command("bulk") & filters.private)
async def bulk_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    raw = message.text.split(None, 1)
    body = raw[1] if len(raw) > 1 else ""
    if not body.strip() and message.reply_to_message:
        body = message.reply_to_message.text or message.reply_to_message.caption or ""

    links = _extract_links(body)
    if not links:
        return await message.reply_text(
            f"<blockquote>{E_INFO} <b>Bulk Links Downloader</b>\n\n"
            f"Send multiple links (one per line) after <code>/bulk</code>, or reply to a "
            f"message containing them. Optionally add a custom name after each link:\n\n"
            f"<code>/bulk\n"
            f"https://example.com/file1.zip MyFile1\n"
            f"https://example.com/file2.zip</code></blockquote>",
            parse_mode=enums.ParseMode.HTML,
        )

    if len(links) > MAX_BULK_LINKS:
        links = links[:MAX_BULK_LINKS]
        await message.reply_text(
            f"<b>{E_WARN} Only the first {MAX_BULK_LINKS} links will be processed.</b>",
            parse_mode=enums.ParseMode.HTML,
        )

    summary = await message.reply_text(
        f"<b>{E_PKG} Bulk download started — {len(links)} link(s) queued.</b>\n"
        f"<i>{E_INFO} Downloading one at a time, each will be uploaded as it finishes.</i>",
        parse_mode=enums.ParseMode.HTML,
    )

    done, failed = 0, 0
    for idx, (url, custom_name) in enumerate(links, 1):
        try:
            await _download_one(client, message, url, custom_name=custom_name)
            done += 1
        except Exception as e:
            failed += 1
            await message.reply_text(
                f"<b>{E_CROSS} Link {idx} failed:</b> <code>{url[:100]}</code>\n<code>{e}</code>",
                parse_mode=enums.ParseMode.HTML,
            )

    await summary.edit_text(
        f"<b>{E_CHECK} Bulk download finished.</b>\n\n"
        f"✅ Done: <b>{done}</b>   ❌ Failed: <b>{failed}</b>   Total: <b>{len(links)}</b>",
        parse_mode=enums.ParseMode.HTML,
    )
