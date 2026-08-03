# Akbots
# Don't Remove Credit
# Telegram Channel @AkBots_Official
#
# Misc utility commands, ported from the "GITHUB-HEROKU" repo's daxxop bot
# (daxxop/modules/misc.py) into Akbotz's own plugin system.
#
# Commands:
#   /deploy <repo_url>          - "Deploy to Heroku" button for a repo (public)
#   /mongochk <mongodb_url>     - test a MongoDB connection string (public)
#   /leavegroup                 - make the bot leave the current chat (ADMINS)
#   /html <url>                 - download a page's HTML source as a file (public)
#   /pypi <package>             - look up a package on PyPI (public)
#   /tgm  /telegraph            - upload a replied-to photo/video to telegra.ph (public)
#   /table <number>             - print a multiplication table (public)
#   /id                         - show your user ID and the current chat ID (public)
#
# NOT ported from the source misc.py:
#   /repo      - printed the ORIGINAL bot's own GitHub contributors list;
#                hardcoded to that repo, not relevant to Akbotz.
#   /downloadrepo, /github, /git, /allrepo
#              - already covered by Akbots/github_tools.py (downloadrepo)
#                and the /github, /git, /allrepo additions made there.
#   /info      - superseded by the richer version below (/id) plus
#                whatever userinfo command Akbotz already ships elsewhere.

import os
import re

import requests
from pymongo import MongoClient
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaVideo,
    Message,
)

from config import ADMINS
from Akbots.direct_utils import safe_edit, make_download_progress

try:
    from pyrogram.enums import ButtonStyle as _BS
except ImportError:
    _BS = None

try:
    from telegraph import upload_file as telegraph_upload_file
except ImportError:
    telegraph_upload_file = None

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

MONGO_URL_PATTERN = re.compile(r"mongodb(?:\+srv)?://[^\s]+")


# ---------------------------------------------------------------------------
# /deploy <repo_url>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("deploy"))
async def deploy_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/deploy &lt;github_repo_url&gt;</code>"
        )

    repo_url = message.command[1]
    heroku_url = f"https://dashboard.heroku.com/new?template={repo_url}"
    await message.reply_text(
        "Click the button below to deploy this repo to Heroku:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🚀 ᴅᴇᴘʟᴏʏ ᴛᴏ ʜᴇʀᴏᴋᴜ", url=heroku_url,
                                    style=_BS.SUCCESS if _BS else None)]]
        ),
    )


# ---------------------------------------------------------------------------
# /mongochk <mongodb_url>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("mongochk"))
async def mongo_check_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(
            f"{E_WARN} Usage: <code>/mongochk &lt;mongodb_url&gt;</code>"
        )

    mongo_url = message.command[1]
    if not MONGO_URL_PATTERN.match(mongo_url):
        return await message.reply_text(f"{E_CROSS} That doesn't look like a valid MongoDB URL.")

    try:
        mongo_client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
        mongo_client.server_info()
        await message.reply_text(f"{E_CHECK} MongoDB URL is valid and the connection succeeded.")
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Failed to connect to MongoDB: {e}")


# ---------------------------------------------------------------------------
# /leavegroup
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("leavegroup") & filters.user(ADMINS))
async def leave_group_command(client: Client, message: Message):
    chat_id = message.chat.id
    await message.reply_text(f"{E_CHECK} Left the group.")
    await client.leave_chat(chat_id, delete=True)


# ---------------------------------------------------------------------------
# /html <url>
# ---------------------------------------------------------------------------
def _download_website_source(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.text
        return f"Failed to download source code. Status code: {resp.status_code}"
    except Exception as e:
        return f"An error occurred: {e}"


@Client.on_message(filters.command("html"))
async def html_download_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(f"{E_WARN} Usage: <code>/html &lt;url&gt;</code>")

    url = message.command[1]
    source = _download_website_source(url)

    if source.startswith(("Failed to download", "An error occurred")):
        return await message.reply_text(f"{E_CROSS} {source}")

    path = "/tmp/website.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    try:
        await message.reply_document(path, caption=f"<blockquote>Source code of {url}</blockquote>", parse_mode=enums.ParseMode.HTML)
    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# /pypi <package>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("pypi"))
async def pypi_info_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(f"{E_WARN} Usage: <code>/pypi &lt;package_name&gt;</code>")

    package_name = message.command[1]
    try:
        resp = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=15)
        if resp.status_code != 200:
            return await message.reply_text(f"{E_CROSS} No PyPI package named \"{package_name}\".")

        info = resp.json()["info"]
        homepage = (info.get("project_urls") or {}).get("Homepage") or info.get("home_page") or "-"
        await message.reply_text(
            f"<b>ᴘᴀᴄᴋᴀɢᴇ:</b> {info.get('name')}\n"
            f"<b>ʟᴀᴛᴇsᴛ ᴠᴇʀsɪᴏɴ:</b> {info.get('version')}\n"
            f"<b>sᴜᴍᴍᴀʀʏ:</b> {info.get('summary') or '-'}\n"
            f"<b>ᴘʀᴏᴊᴇᴄᴛ ᴜʀʟ:</b> {homepage}"
        )
    except Exception as e:
        await message.reply_text(f"{E_CROSS} Failed to fetch PyPI info: {e}")


# ---------------------------------------------------------------------------
# /tgm  /telegraph  - upload a replied-to photo/video to telegra.ph
# ---------------------------------------------------------------------------
@Client.on_message(filters.command(["tgm", "telegraph"]))
async def telegraph_upload_command(client: Client, message: Message):
    if telegraph_upload_file is None:
        return await message.reply_text(
            f"{E_WARN} The <code>telegraph</code> package isn't installed. "
            "Run <code>pip install telegraph</code>."
        )

    reply = message.reply_to_message
    if not reply or not reply.media:
        return await message.reply_text(
            f"{E_WARN} Reply to a photo or video with /tgm to get a telegra.ph link."
        )

    status_msg = await message.reply_text("Uploading to telegra.ph, please wait…")
    path = None
    try:
        path = await reply.download(progress=make_download_progress(status_msg))
        uploaded = telegraph_upload_file(path)
        link = "https://graph.org" + uploaded[0]
        await safe_edit(status_msg.edit_text, f"{E_CHECK} Uploaded: {link}")
    except Exception as e:
        await safe_edit(status_msg.edit_text, f"{E_CROSS} Upload failed: {e}")
    finally:
        if path and os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# /table <number>
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("table"))
async def multiplication_table_command(client: Client, message: Message):
    if len(message.command) != 2:
        return await message.reply_text(f"{E_WARN} Usage: <code>/table &lt;number&gt;</code>")

    try:
        number = int(message.command[1])
    except ValueError:
        return await message.reply_text(f"{E_CROSS} Please enter a valid whole number.")

    table = "\n".join(f"{number} x {i} = {number * i}" for i in range(1, 11))
    await message.reply_text(f"Multiplication table of {number}:\n\n{table}")


# ---------------------------------------------------------------------------
# /id
# ---------------------------------------------------------------------------
@Client.on_message(filters.command("id"))
async def id_command(client: Client, message: Message):
    await message.reply_text(
        f"<b>ʏᴏᴜʀ ɪᴅ:</b> <code>{message.from_user.id}</code>\n"
        f"<b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{message.chat.id}</code>"
    )


# ---------------------------------------------------------------------------
# Voice/video-chat notices (public, no command — just event handlers)
# ---------------------------------------------------------------------------
@Client.on_message(filters.video_chat_started)
async def video_chat_started_notice(client: Client, message: Message):
    await message.reply_text(f"{E_CHECK} Voice chat started!")


@Client.on_message(filters.video_chat_ended)
async def video_chat_ended_notice(client: Client, message: Message):
    await message.reply_text("🔇 Voice chat ended. Thanks for joining!")


@Client.on_message(filters.video_chat_members_invited)
async def video_chat_members_invited_notice(client: Client, message: Message):
    invited = message.video_chat_members_invited.users
    mentions = " ".join(f"[{u.first_name}](tg://user?id={u.id})" for u in invited)
    if mentions:
        try:
            await message.reply_text(f"{message.from_user.mention} invited {mentions} ☄️")
        except Exception:
            pass
