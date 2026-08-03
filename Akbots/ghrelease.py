# Akbots
# GitHub Releases + HuggingFace Downloader
#
# Fills the "dedicated GitHub/HuggingFace parser with mirror acceleration"
# gap. Three link shapes are handled per site:
#
#   GitHub:
#     - a direct release ASSET link (.../releases/download/<tag>/<file>)
#       -> download it straight away
#     - a releases/repo page with NO specific asset picked
#       (.../releases, .../releases/tag/<tag>, .../releases/latest, or a
#       bare github.com/<owner>/<repo> link) -> hit the GitHub API for that
#       release's asset list and let the user pick one via buttons
#     - a /blob/ source-file link -> rewritten to the raw.githubusercontent
#       URL and downloaded directly
#
#   HuggingFace:
#     - a /resolve/<rev>/<path> link -> direct download
#     - a /blob/<rev>/<path> link -> rewritten to /resolve/ and downloaded
#     - a bare repo link (model or dataset) -> HF API file listing,
#       same pick-one-via-buttons flow as GitHub releases
#
# Every actual transfer is handed off to Akbots.aria2_dl's aria2c wrapper
# instead of plain stream_download() — that's what gives this the same
# "IDM-style" multi-connection + resume behaviour every other big-file
# downloader in this bot already has, for free.
#
# Mirror acceleration: GitHub's own servers are throttled/blocked from a
# lot of networks. If a direct github.com/objects.githubusercontent.com
# transfer fails outright, this retries once through a public gh-proxy
# mirror before giving up — same idea as Ghost Downloader's "mirror
# acceleration", just implemented as a fallback instead of a race.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import re
import shutil
import uuid
import contextlib
import aiohttp
from urllib.parse import unquote
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from Akbots.direct_utils import (
    safe_filename, upload_file, fmt_bytes, DEFAULT_HEADERS,
    E_CHECK, E_CROSS, E_INFO, E_ROCKET,
)
from Akbots.aria2_dl import aria2c_download
from Akbots.torrent import _aria2c_available
from Akbots.link_cache import try_send_cached
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

# ---------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------

GH_ASSET_PATTERN = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)/releases/download/([^/\s]+)/([^\s?#]+)",
    re.IGNORECASE,
)
GH_BLOB_PATTERN = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)/blob/([^/\s]+)/([^\s?#]+)",
    re.IGNORECASE,
)
# Direct branch/tag source-code zip, e.g.
# github.com/owner/repo/archive/refs/heads/master.zip (or /tags/v1.0.zip),
# plus the older short form github.com/owner/repo/archive/master.zip —
# both are what GitHub's own green "Code > Download ZIP" button links to.
GH_ARCHIVE_PATTERN = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)/archive/"
    r"(?:refs/(?:heads|tags)/)?([^/\s?#]+?)\.(zip|tar\.gz)",
    re.IGNORECASE,
)
# releases list/tag/latest page OR a bare repo link — anything github-repo
# shaped that ISN'T already an asset or blob link (checked in that order).
# The trailing lookahead requires the match to end at end-of-string,
# whitespace, or a query/fragment — this is what stops it from also
# swallowing unrelated repo sub-pages like /issues/5, /pulls, /wiki, etc.
# (those just fail to match this pattern entirely and fall through).
GH_RELEASES_OR_REPO_PATTERN = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)"
    r"(?:/releases(?:/tag/([^/\s?#]+)|/latest)?)?/?"
    r"(?=$|[\s?#])",
    re.IGNORECASE,
)

HF_RESOLVE_PATTERN = re.compile(
    r"https?://huggingface\.co/(datasets/)?([\w.-]+)/([\w.-]+)/resolve/([^/\s]+)/([^\s?#]+)",
    re.IGNORECASE,
)
HF_BLOB_PATTERN = re.compile(
    r"https?://huggingface\.co/(datasets/)?([\w.-]+)/([\w.-]+)/blob/([^/\s]+)/([^\s?#]+)",
    re.IGNORECASE,
)
HF_REPO_PATTERN = re.compile(
    r"https?://huggingface\.co/(datasets/)?([\w.-]+)/([\w.-]+)/?"
    r"(?=$|[\s?#])",
    re.IGNORECASE,
)

GITHUB_API = "https://api.github.com"
MAX_LIST_ITEMS = 30   # keep the button list on one screen

# gh-proxy style mirrors — prefixed directly in front of the original
# github.com / objects.githubusercontent.com URL.
GITHUB_MIRRORS = ["https://ghfast.top/", "https://gh-proxy.com/"]

# session_id -> {"items": [(label, url, size), ...], "chat_id", "message", "kind"}
_PICK_SESSIONS = {}


# ---------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------

async def _gh_api_get(path: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{GITHUB_API}{path}",
            headers={**DEFAULT_HEADERS, "Accept": "application/vnd.github+json"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status == 404:
                return None
            if r.status != 200:
                raise ValueError(f"GitHub API returned HTTP {r.status}")
            return await r.json()


async def _gh_release_assets(owner: str, repo: str, tag: str = None):
    """Returns (release_label, [(name, url, size), ...]) for the given tag,
    or the latest release if tag is None. None if the repo has no
    releases at all (caller falls back to the source-code zip)."""
    path = f"/repos/{owner}/{repo}/releases/{'tags/' + tag if tag else 'latest'}"
    data = await _gh_api_get(path)
    if not data:
        return None
    assets = [
        (a["name"], a["browser_download_url"], a.get("size", 0))
        for a in data.get("assets", [])
    ]
    return data.get("tag_name", tag or "latest"), assets


async def _gh_default_branch(owner: str, repo: str) -> str:
    data = await _gh_api_get(f"/repos/{owner}/{repo}")
    return (data or {}).get("default_branch", "main")


# ---------------------------------------------------------------------
# HuggingFace API helpers
# ---------------------------------------------------------------------

async def _hf_file_list(is_dataset: bool, owner: str, name: str):
    """Returns [(path, url, size), ...] for every file in the repo, via
    HF's own tree API (works for public repos without auth)."""
    kind = "datasets" if is_dataset else "models"
    repo_id = f"{owner}/{name}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://huggingface.co/api/{kind}/{repo_id}",
            headers=DEFAULT_HEADERS, timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()

    prefix = "datasets/" if is_dataset else ""
    out = []
    for sib in data.get("siblings", []):
        p = sib.get("rfilename")
        if not p:
            continue
        url = f"https://huggingface.co/{prefix}{owner}/{name}/resolve/main/{p}"
        out.append((p, url, sib.get("size", 0) or 0))
    return out


# ---------------------------------------------------------------------
# Mirror-fallback download
# ---------------------------------------------------------------------

def _is_github_host(url: str) -> bool:
    return "github.com" in url or "githubusercontent.com" in url


async def _download_with_mirror_fallback(url: str, folder: str, status, label: str,
                                          out_name: str, user_id: int, queue_label: str):
    """aria2c_download(), retried once through a gh-proxy mirror if the
    direct GitHub host fails outright (blocked/reset connection etc.) —
    this is the 'mirror acceleration' piece. Non-GitHub URLs (plain HF
    links) never hit the mirror branch."""
    try:
        return await aria2c_download(
            url, folder, status, label=label, out_name=out_name,
            user_id=user_id, queue_label=queue_label,
        )
    except Exception as first_err:
        if not _is_github_host(url):
            raise
        last_err = first_err
        for mirror in GITHUB_MIRRORS:
            try:
                await safe_edit(status.edit_text, 
                    f"<b>{E_INFO} Direct GitHub download failed — retrying via mirror...</b>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            try:
                return await aria2c_download(
                    f"{mirror}{url}", folder, status, label=label, out_name=out_name,
                    user_id=user_id, queue_label=queue_label,
                )
            except Exception as mirror_err:
                last_err = mirror_err
                continue
        raise last_err


# ---------------------------------------------------------------------
# Shared "download one picked file" flow
# ---------------------------------------------------------------------

async def _download_and_send(client: Client, message: Message, status: Message,
                              url: str, hint_name: str, label_prefix: str,
                              delete_status: bool = True):
    if await try_send_cached(client, message, url, status, delete_status=delete_status):
        return True

    if not _aria2c_available():
        await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} 'aria2c' is not installed on this host.</b>\n"
            f"<i>Install it first (Debian/Ubuntu: <code>apt install aria2</code>).</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return False

    name = safe_filename(unquote(hint_name), "downloaded_file")
    folder = os.path.join("downloads", "ghhf", f"task_{message.chat.id}_{message.id}_{uuid.uuid4().hex[:6]}")
    try:
        path = await _download_with_mirror_fallback(
            url, folder, status, f"Downloading {name}", name,
            message.from_user.id, f"{label_prefix} download",
        )
        await upload_file(
            client, message, path, status,
            f"<b>{E_CHECK} {label_prefix}</b>\n<code>{name}</code>",
            file_name=name, cache_url=url, delete_status=delete_status,
        )
        return True
    except Exception as e:
        await message.reply_text(f"<b>{E_CROSS} Failed:</b> {name}\n<code>{str(e)[:300]}</code>", parse_mode=enums.ParseMode.HTML)
        return False
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _pick_keyboard(session_id: str, items, prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for i, (label, _url, size) in enumerate(items):
        text = f"{label}" + (f" ({fmt_bytes(size)})" if size else "")
        if len(text) > 60:
            text = text[:57] + "..."
        rows.append([make_button(text, callback_data=f"{prefix}#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)])
    rows.append([make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"{prefix}cancel#{session_id}",
                              style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------
# GitHub handlers
# ---------------------------------------------------------------------

async def _handle_gh_asset(client: Client, message: Message, m: re.Match):
    owner, repo, tag, asset = m.groups()
    url = m.group(0)
    status = await message.reply_text(f"<b>{E_INFO} GitHub release asset detected...</b>", parse_mode=enums.ParseMode.HTML)
    await _download_and_send(client, message, status, url, asset, "GitHub Release")


async def _handle_gh_blob(client: Client, message: Message, m: re.Match):
    owner, repo, branch, path = m.groups()
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    status = await message.reply_text(f"<b>{E_INFO} GitHub file detected...</b>", parse_mode=enums.ParseMode.HTML)
    await _download_and_send(client, message, status, raw_url, os.path.basename(path), "GitHub File")


async def _handle_gh_archive(client: Client, message: Message, m: re.Match):
    owner, repo, ref, ext = m.groups()
    url = m.group(0)
    status = await message.reply_text(f"<b>{E_INFO} GitHub source archive detected...</b>", parse_mode=enums.ParseMode.HTML)
    name = f"{repo}-{ref}.{ext}"
    await _download_and_send(client, message, status, url, name, "GitHub Source")


async def _handle_gh_repo_or_releases(client: Client, message: Message, m: re.Match):
    owner, repo, tag = m.groups()
    status = await message.reply_text(f"<b>{E_INFO} Reading GitHub release info...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        result = await _gh_release_assets(owner, repo, tag)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} GitHub API error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not result or not result[1]:
        # No releases (or a release with zero uploaded assets) — offer the
        # source-code zip of the default branch as a fallback, same as
        # what GitHub's own green "Code" button would give you.
        try:
            branch = await _gh_default_branch(owner, repo)
        except Exception:
            branch = "main"
        zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
        await safe_edit(status.edit_text, 
            f"<b>{E_INFO} No release assets found for {owner}/{repo}.</b>\n"
            f"<i>Downloading source code ({branch} branch) instead...</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return await _download_and_send(client, message, status, zip_url, f"{repo}-{branch}.zip", "GitHub Source")

    release_label, assets = result
    items = [(name, url, size) for name, url, size in assets[:MAX_LIST_ITEMS]]
    session_id = uuid.uuid4().hex[:10]
    _PICK_SESSIONS[session_id] = {"items": items, "chat_id": message.chat.id, "message": message}
    capped = f" (showing first {MAX_LIST_ITEMS})" if len(assets) > MAX_LIST_ITEMS else ""
    await safe_edit(status.edit_text, 
        f"<b>{E_ROCKET} {owner}/{repo} — release {release_label}</b>\n"
        f"<i>{len(assets)} asset(s){capped}. Pick one to download:</i>",
        reply_markup=_pick_keyboard(session_id, items, "ghasset"),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^ghasset#"))
async def gh_asset_pick_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#", 2)
    session = _PICK_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the link again.</b>", parse_mode=enums.ParseMode.HTML)

    name, url, _size = session["items"][int(idx)]
    status = callback_query.message
    await safe_edit(status.edit_text, f"<b>{E_INFO} Downloading {name}...</b>", parse_mode=enums.ParseMode.HTML)
    ok = await _download_and_send(client, session["message"], status, url, name, "GitHub Release", delete_status=False)
    _PICK_SESSIONS.pop(session_id, None)
    if ok:
        with contextlib.suppress(Exception):
            await status.delete()


@Client.on_callback_query(filters.regex(r"^ghassetcancel#"))
async def gh_asset_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _PICK_SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)


# ---------------------------------------------------------------------
# HuggingFace handlers
# ---------------------------------------------------------------------

async def _handle_hf_resolve(client: Client, message: Message, m: re.Match):
    is_dataset, owner, name, rev, path = m.groups()
    url = m.group(0)
    status = await message.reply_text(f"<b>{E_INFO} HuggingFace file detected...</b>", parse_mode=enums.ParseMode.HTML)
    await _download_and_send(client, message, status, url, os.path.basename(path), "HuggingFace File")


async def _handle_hf_blob(client: Client, message: Message, m: re.Match):
    is_dataset, owner, name, rev, path = m.groups()
    prefix = "datasets/" if is_dataset else ""
    resolve_url = f"https://huggingface.co/{prefix}{owner}/{name}/resolve/{rev}/{path}"
    status = await message.reply_text(f"<b>{E_INFO} HuggingFace file detected...</b>", parse_mode=enums.ParseMode.HTML)
    await _download_and_send(client, message, status, resolve_url, os.path.basename(path), "HuggingFace File")


async def _handle_hf_repo(client: Client, message: Message, m: re.Match):
    is_dataset, owner, name = m.groups()
    status = await message.reply_text(f"<b>{E_INFO} Reading HuggingFace repo file list...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        files = await _hf_file_list(bool(is_dataset), owner, name)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} HuggingFace API error:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not files:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No files found (repo may be private or gated).</b>", parse_mode=enums.ParseMode.HTML)

    items = [(path, url, size) for path, url, size in files[:MAX_LIST_ITEMS]]
    session_id = uuid.uuid4().hex[:10]
    _PICK_SESSIONS[session_id] = {"items": items, "chat_id": message.chat.id, "message": message}
    capped = f" (showing first {MAX_LIST_ITEMS} of {len(files)})" if len(files) > MAX_LIST_ITEMS else ""
    await safe_edit(status.edit_text, 
        f"<b>{E_ROCKET} {owner}/{name}</b>\n"
        f"<i>{len(files)} file(s){capped}. Pick one to download:</i>",
        reply_markup=_pick_keyboard(session_id, items, "hffile"),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^hffile#"))
async def hf_file_pick_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#", 2)
    session = _PICK_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the link again.</b>", parse_mode=enums.ParseMode.HTML)

    path, url, _size = session["items"][int(idx)]
    name = os.path.basename(path)
    status = callback_query.message
    await safe_edit(status.edit_text, f"<b>{E_INFO} Downloading {name}...</b>", parse_mode=enums.ParseMode.HTML)
    ok = await _download_and_send(client, session["message"], status, url, name, "HuggingFace File", delete_status=False)
    _PICK_SESSIONS.pop(session_id, None)
    if ok:
        with contextlib.suppress(Exception):
            await status.delete()


@Client.on_callback_query(filters.regex(r"^hffilecancel#"))
async def hf_file_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _PICK_SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)


# ---------------------------------------------------------------------
# Dispatch — auto-detect + explicit commands
# ---------------------------------------------------------------------

async def _dispatch(client: Client, message: Message, text: str) -> bool:
    """Tries every pattern in most-specific-first order (asset/resolve/blob
    before the bare-repo fallback, since a repo URL is a substring-shaped
    prefix of the more specific ones). Returns True if something matched."""
    if m := GH_ASSET_PATTERN.search(text):
        await _handle_gh_asset(client, message, m); return True
    if m := GH_BLOB_PATTERN.search(text):
        await _handle_gh_blob(client, message, m); return True
    if m := GH_ARCHIVE_PATTERN.search(text):
        await _handle_gh_archive(client, message, m); return True
    if m := HF_RESOLVE_PATTERN.search(text):
        await _handle_hf_resolve(client, message, m); return True
    if m := HF_BLOB_PATTERN.search(text):
        await _handle_hf_blob(client, message, m); return True
    if m := GH_RELEASES_OR_REPO_PATTERN.search(text):
        await _handle_gh_repo_or_releases(client, message, m); return True
    if m := HF_REPO_PATTERN.search(text):
        await _handle_hf_repo(client, message, m); return True
    return False


@Client.on_message(
    filters.text & filters.private &
    filters.regex(r"github\.com|huggingface\.co") & ~filters.regex(r"^/"),
    group=1,
)
async def ghhf_auto_detect(client: Client, message: Message):
    await _dispatch(client, message, message.text)


@Client.on_message(filters.command(["ghdl", "hfdl"]) & filters.private)
async def ghhf_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/ghdl &lt;GitHub release/repo/file URL&gt;</code>\n"
            f"<code>/hfdl &lt;HuggingFace repo/file URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    raw = message.text.split(None, 1)[1].strip()
    if not await _dispatch(client, message, raw):
        await message.reply_text(f"<b>{E_CROSS} Couldn't recognize that as a GitHub or HuggingFace link.</b>", parse_mode=enums.ParseMode.HTML)
