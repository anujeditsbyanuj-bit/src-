# Akbots - Don't Remove Credit - @AkBots_Official
#
# KukuTV / KukuFM downloader — /kuku <query|show_id|show_url>
#
# Ported from a standalone script (Kuku_Tv_Script_Spideerio.py). KukuFM's
# API is private and needs a real account's JWT bearer token — same idea as
# /setcookies for yt-dlp sites, just a bearer token instead of a cookie
# jar. Set these as env vars / Replit Secrets:
#   KUKU_JWT_TOKEN      — from a logged-in KukuFM app/account
#   KUKU_REFRESH_TOKEN  — paired refresh token, used to silently renew the
#                         JWT via KukuFM's own session-refresh endpoint
#                         when it expires (same flow the source script used).
# Without these set, /kuku just tells the user the integration isn't
# configured instead of failing with a confusing 401.
#
# Episodes are downloaded with ffmpeg (stream copy, no re-encode) using the
# app's own User-Agent + Authorization header against the working CDN
# domain — same approach the source script used, reusing this bot's shared
# upload_file()/run_subprocess_with_progress() pipeline instead of the
# script's own bespoke printing/subprocess.run() calls.
#
# NOT ported: the source script's "unlock all episodes" option, which POSTs
# a fake `no_of_ads_watched` count to KukuFM's reward-coin API to
# fraudulently unlock coin-gated episodes without actually watching ads or
# paying for them. That's abuse of a rewards API, not a real download
# method, and was deliberately left out — only genuinely free (already
# unlocked) episodes are supported here.

import os
import re
import time
import json
import base64
import asyncio
import requests

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from Akbots.direct_utils import (
    upload_file, make_output_folder, safe_filename,
    get_video_metadata, run_subprocess_with_progress, make_ffmpeg_progress_parser,
    E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_BOLT, E_CLOCK,
)
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

APP_HEADERS_BASE = {
    "client-country": "IN",
    "lang": "english",
    "app-version": "50804",
    "user-agent": "kukufm-android-reels/5.8.4",
    "package-name": "com.vlv.aravali.reels",
    "build-number": "5080401",
}

DOWNLOAD_DIR = make_output_folder("kuku")

_jwt_token = os.environ.get("KUKU_JWT_TOKEN", "")
_refresh_token = os.environ.get("KUKU_REFRESH_TOKEN", "")

_active_kuku_jobs = {}     # user_id -> True while a batch download is running
_pending_search = {}       # message_id -> [{"id":..., "title":..., "n_episodes":...}, ...]
_awaiting_range = {}       # user_id -> {"show_id", "show_title", "free_by_num": {num: ep}}

_KUKU_URL_RE = re.compile(r"https?://(?:www\.)?kukufm\.com/\S+", re.IGNORECASE)


def _configured() -> bool:
    return bool(_jwt_token and _refresh_token)


def _headers() -> dict:
    h = dict(APP_HEADERS_BASE)
    h["authorization"] = f"jwt {_jwt_token}"
    return h


def _token_expiry(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload)).get("exp", 0)
    except Exception:
        return 0


def _token_expired() -> bool:
    if not _jwt_token:
        return True
    exp = _token_expiry(_jwt_token)
    return exp == 0 or time.time() > exp


def _refresh_jwt() -> bool:
    """Blocking — call via asyncio.to_thread. Mirrors the source script's
    refresh_jwt_token(); updates the module-level tokens in place on success."""
    global _jwt_token, _refresh_token
    try:
        resp = requests.post(
            "https://api.kukufm.com/api/v1.1/users/get-session-token/",
            headers={
                "install-source": "google_play", "app-version": "50804",
                "user-agent": APP_HEADERS_BASE["user-agent"],
                "package-name": APP_HEADERS_BASE["package-name"],
                "build-number": APP_HEADERS_BASE["build-number"],
                "content-type": "application/x-www-form-urlencoded",
            },
            data={
                "app_name": APP_HEADERS_BASE["package-name"], "os_type": "android",
                "app_build_number": "50804", "installed_version": "5.8.4",
                "access_token": _jwt_token, "refresh_token": _refresh_token,
                "is_upi_app_installed": "true",
            },
            verify=False, timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "access_token" in data:
                _jwt_token = data["access_token"]
                _refresh_token = data.get("refresh_token", _refresh_token)
                return True
    except Exception:
        pass
    return False


def _ensure_token() -> bool:
    if not _jwt_token or not _refresh_token:
        return False
    if _token_expired():
        return _refresh_jwt()
    return True


def _convert_cdn_url(url):
    if url and "media.cdn.kukufm.com" in url:
        return url.replace("media.cdn.kukufm.com", "d1l07mcd18xic4.cloudfront.net")
    return url


# --- Blocking API helpers (always call via asyncio.to_thread) -----------

def _search_shows(query: str):
    if not _ensure_token():
        return []
    try:
        resp = requests.get(
            "https://d31ntp24xvh0tq.cloudfront.net/api/v4/search/",
            headers=_headers(),
            params={"q": query, "language_ids": "1", "is_kid_profile": "false",
                    "click_analytics": "true", "lang": "english", "user_set": "4",
                    "has_premium": "false"},
            verify=False, timeout=30,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        shows = []
        for group in data.get("items", []):
            for item in group.get("items", []):
                show = item.get("show")
                if show:
                    shows.append({"id": show.get("id"), "title": show.get("title") or "Untitled",
                                  "n_episodes": show.get("n_episodes", 0)})
        return shows
    except Exception:
        return []


def _get_popular_shows():
    if not _ensure_token():
        return []
    try:
        resp = requests.get(
            "https://api.kukufm.com/api/v3/home/all/",
            headers=_headers(), params={"page": 1, "selected_tab": "popular"},
            verify=False, timeout=30,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        shows = []
        for group in data.get("items", []):
            for item in group.get("items", []):
                show = item.get("show")
                if show:
                    shows.append({"id": show.get("id"), "title": show.get("title") or "Untitled",
                                  "n_episodes": show.get("n_episodes", 0)})
        return shows
    except Exception:
        return []


def _get_show_info(show_id):
    if not _ensure_token():
        return None
    try:
        resp = requests.get(f"https://api.kukufm.com/api/v2.3/channels/{show_id}",
                             headers=_headers(), verify=False, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        channel = data.get("channel") or data.get("show") or data.get("data", {})
        if not channel:
            return None
        return {"title": channel.get("title") or f"Show_{show_id}",
                "n_episodes": channel.get("n_episodes", 0)}
    except Exception:
        return None


def _get_all_episodes(show_id):
    if not _ensure_token():
        return []
    all_eps = []
    page = 1
    while True:
        try:
            resp = requests.get(
                f"https://api.kukufm.com/api/v2.3/channels/{show_id}/episodes/",
                headers=_headers(),
                params={"page": page, "lang": "english",
                        "is_coin_based_monetization": "false", "page_size": 50},
                verify=False, timeout=30,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            episodes = data.get("episodes", [])
            if not episodes:
                break
            all_eps.extend(episodes)
            if not data.get("has_more", False):
                break
            page += 1
            time.sleep(0.3)
        except Exception:
            break
    return all_eps


def _split_free_locked(episodes):
    free, locked = [], []
    for ep in episodes:
        ep_num, ep_id = ep.get("index"), ep.get("id")
        if not ep_num or not ep_id:
            continue
        content = ep.get("content", {})
        video_url = content.get("video_hls_url") or content.get("hls_url")
        if video_url:
            free.append({"ep_num": ep_num, "ep_id": ep_id, "url": _convert_cdn_url(video_url)})
        else:
            locked.append({"ep_num": ep_num, "ep_id": ep_id})
    return sorted(free, key=lambda e: e["ep_num"]), sorted(locked, key=lambda e: e["ep_num"])


# --- Download / upload one episode ---------------------------------------

async def _download_one_episode(client: Client, message: Message, ep: dict, show_title: str):
    ep_num, video_url = ep["ep_num"], ep["url"]
    safe_show = safe_filename(show_title, "KukuTV")
    file_name = f"{safe_show}_EP{ep_num:03d}.mp4"
    out_path = os.path.join(DOWNLOAD_DIR, file_name)

    status = await message.reply_text(
        f"<b>{E_ROCKET} Downloading Episode {ep_num}...</b>", parse_mode=enums.ParseMode.HTML
    )

    cmd = [
        "ffmpeg", "-y",
        "-user_agent", APP_HEADERS_BASE["user-agent"],
        "-headers", f"authorization: jwt {_jwt_token}\r\n",
        "-i", video_url,
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        out_path,
    ]
    parse_line = make_ffmpeg_progress_parser(0, title=f"Episode {ep_num}")
    returncode, _tail = await run_subprocess_with_progress(
        cmd, status, f"Episode {ep_num}", parse_line,
        user_id=message.from_user.id if message.from_user else None,
        queue_label=f"KukuTV: {safe_show} EP{ep_num}",
    )

    if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024 * 1024:
        try:
            os.remove(out_path)
        except OSError:
            pass
        await status.edit_text(f"<b>{E_CROSS} Episode {ep_num} failed to download.</b>",
                                parse_mode=enums.ParseMode.HTML)
        return False

    duration, _w, _h = get_video_metadata(out_path)
    await upload_file(
        client, message, out_path, status,
        caption=f"<b>{safe_show}</b> — Episode {ep_num}",
        file_name=file_name, duration=duration, quality="Original",
    )
    return True


# --- Commands -------------------------------------------------------------

@Client.on_message(filters.private & filters.command("kuku"))
async def kuku_cmd(client: Client, message: Message):
    if not _configured():
        return await message.reply_text(
            f"<b>{E_WARN} KukuTV isn't configured on this bot.</b>\n\n"
            f"<i>An admin needs to set the <code>KUKU_JWT_TOKEN</code> and "
            f"<code>KUKU_REFRESH_TOKEN</code> secrets from a logged-in KukuFM account.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    text = message.text.split(None, 1)
    if len(text) < 2:
        status = await message.reply_text(f"<b>🔥 Fetching popular KukuTV shows...</b>", parse_mode=enums.ParseMode.HTML)
        shows = await asyncio.to_thread(_get_popular_shows)
        if not shows:
            return await status.edit_text(
                f"<b>{E_WARN} Usage:</b> <code>/kuku &lt;show name, show id, or kukufm.com link&gt;</code>\n\n"
                f"<i>Couldn't fetch popular shows right now — pass a show name/id/link instead.</i>",
                parse_mode=enums.ParseMode.HTML
            )
        return await _show_pick_list(status, shows, title="🔥 Popular Shows")

    arg = text[1].strip()

    url_m = _KUKU_URL_RE.search(arg)
    if url_m:
        id_m = re.search(r"(\d+)(?:/)?$", url_m.group(0).rstrip("/"))
        show_id = id_m.group(1) if id_m else None
    elif arg.isdigit():
        show_id = arg
    else:
        show_id = None

    if show_id:
        return await _open_kuku_show(client, message, show_id)

    status = await message.reply_text(f"<b>🔎 Searching KukuTV for \"{arg}\"...</b>", parse_mode=enums.ParseMode.HTML)
    shows = await asyncio.to_thread(_search_shows, arg)
    if not shows:
        return await status.edit_text(f"<b>{E_CROSS} No shows found for that search.</b>", parse_mode=enums.ParseMode.HTML)

    await _show_pick_list(status, shows, title=f"📺 Found {len(shows[:10])} show(s)")


async def _show_pick_list(status: Message, shows: list, title: str):
    shows = shows[:10]
    _pending_search[status.id] = shows
    buttons = [
        [make_button(f"{s['title'][:45]} ({s['n_episodes']} ep)", callback_data=f"kuku_pick:{status.id}:{i}", style=_BS.PRIMARY if _BS else None)]
        for i, s in enumerate(shows)
    ]
    buttons.append([make_button("❌ Close", callback_data="kuku_close", style=_BS.DANGER if _BS else None)])
    await status.edit_text(
        f"<b>{title}:</b>", reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML
    )


async def _open_kuku_show(client: Client, message: Message, show_id: str):
    status = await message.reply_text(f"<b>🔎 Looking up show {show_id}...</b>", parse_mode=enums.ParseMode.HTML)
    info = await asyncio.to_thread(_get_show_info, show_id)
    if not info:
        return await status.edit_text(f"<b>{E_CROSS} Show not found or API error.</b>", parse_mode=enums.ParseMode.HTML)

    episodes = await asyncio.to_thread(_get_all_episodes, show_id)
    free, locked = _split_free_locked(episodes)

    lines = [
        f"<b>{E_ROCKET} {info['title']}</b>",
        "",
        f"🔓 <b>Free episodes:</b> {len(free)}",
        f"🔒 <b>Locked episodes:</b> {len(locked)} <i>(not downloadable — no ad-fraud unlock here)</i>",
    ]
    buttons = []
    if free:
        buttons.append([make_button(
            f"📥 Download {len(free)} free episode(s)", callback_data=f"kuku_dl:{show_id}", style=_BS.PRIMARY if _BS else None)])
        buttons.append([make_button(
            "🎯 Pick specific episodes", callback_data=f"kuku_range:{show_id}", style=_BS.PRIMARY if _BS else None)])
    buttons.append([make_button("❌ Close", callback_data="kuku_close", style=_BS.DANGER if _BS else None)])

    await status.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.private & filters.command("kukucancel"))
async def kuku_cancel_cmd(client: Client, message: Message):
    uid = message.from_user.id
    had_job = _active_kuku_jobs.pop(uid, None)
    had_prompt = _awaiting_range.pop(uid, None)
    if had_job or had_prompt:
        await message.reply_text(f"<b>{E_WARN} KukuTV action cancelled.</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await message.reply_text(f"<b>{E_WARN} No active KukuTV download.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^kuku_pick:(\d+):(\d+)$"))
async def kuku_pick_callback(client: Client, callback_query: CallbackQuery):
    status_id, idx = int(callback_query.matches[0].group(1)), int(callback_query.matches[0].group(2))
    shows = _pending_search.get(status_id)
    if not shows or idx >= len(shows):
        return await callback_query.answer("This search expired — run /kuku again.", show_alert=True)
    await callback_query.answer()
    show_id = shows[idx]["id"]
    _pending_search.pop(status_id, None)
    await _open_kuku_show(client, callback_query.message, show_id)


@Client.on_callback_query(filters.regex(r"^kuku_dl:(\d+)$"))
async def kuku_download_callback(client: Client, callback_query: CallbackQuery):
    show_id = callback_query.matches[0].group(1)
    uid = callback_query.from_user.id
    if _active_kuku_jobs.get(uid):
        return await callback_query.answer("You already have a KukuTV download running. Use /kukucancel first.", show_alert=True)

    await callback_query.answer("Starting download...")
    message = callback_query.message

    info = await asyncio.to_thread(_get_show_info, show_id)
    show_title = info["title"] if info else f"Show_{show_id}"
    episodes = await asyncio.to_thread(_get_all_episodes, show_id)
    free, _locked = _split_free_locked(episodes)
    if not free:
        return await message.reply_text(f"<b>{E_WARN} No free episodes to download.</b>", parse_mode=enums.ParseMode.HTML)

    _active_kuku_jobs[uid] = True
    await message.reply_text(
        f"<b>🎬 Downloading {len(free)} episode(s) of {show_title}.</b> Use /kukucancel to stop.",
        parse_mode=enums.ParseMode.HTML
    )
    try:
        for ep in free:
            if not _active_kuku_jobs.get(uid):
                break
            try:
                await _download_one_episode(client, message, ep, show_title)
            except Exception:
                continue
    finally:
        _active_kuku_jobs.pop(uid, None)


@Client.on_callback_query(filters.regex(r"^kuku_range:(\d+)$"))
async def kuku_range_prompt_callback(client: Client, callback_query: CallbackQuery):
    show_id = callback_query.matches[0].group(1)
    uid = callback_query.from_user.id
    if _active_kuku_jobs.get(uid):
        return await callback_query.answer("You already have a KukuTV download running. Use /kukucancel first.", show_alert=True)

    await callback_query.answer()
    info = await asyncio.to_thread(_get_show_info, show_id)
    show_title = info["title"] if info else f"Show_{show_id}"
    episodes = await asyncio.to_thread(_get_all_episodes, show_id)
    free, _locked = _split_free_locked(episodes)
    if not free:
        return await callback_query.message.reply_text(
            f"<b>{E_WARN} No free episodes available to pick from.</b>", parse_mode=enums.ParseMode.HTML
        )

    _awaiting_range[uid] = {
        "show_id": show_id, "show_title": show_title,
        "free_by_num": {ep["ep_num"]: ep for ep in free},
    }
    nums = sorted(_awaiting_range[uid]["free_by_num"].keys())
    await callback_query.message.reply_text(
        f"<b>🎯 {show_title}</b> — free episodes available: <code>{nums[0]}–{nums[-1]}</code> "
        f"({len(nums)} total)\n\n"
        f"Reply with which ones to download — e.g. <code>1-10</code> or <code>5,12,15</code>.\n"
        f"<i>Send /kukucancel to abort.</i>",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=2)
async def kuku_range_reply(client: Client, message: Message):
    uid = message.from_user.id
    pending = _awaiting_range.get(uid)
    if not pending:
        return
    raw = message.text.strip()
    if not re.fullmatch(r"[\d,\-\s]+", raw):
        return  # not a range-looking reply — leave it for other handlers/ignore

    selected_nums = set()
    try:
        if "-" in raw and "," not in raw:
            start, end = (int(x.strip()) for x in raw.split("-", 1))
            selected_nums = set(range(start, end + 1))
        else:
            selected_nums = {int(x.strip()) for x in raw.split(",") if x.strip()}
    except ValueError:
        return await message.reply_text(f"<b>{E_WARN} Couldn't parse that range.</b> Try <code>1-10</code> or <code>5,12,15</code>.",
                                         parse_mode=enums.ParseMode.HTML)

    free_by_num = pending["free_by_num"]
    to_download = [free_by_num[n] for n in sorted(selected_nums) if n in free_by_num]
    missing = sorted(n for n in selected_nums if n not in free_by_num)

    _awaiting_range.pop(uid, None)
    if not to_download:
        return await message.reply_text(f"<b>{E_CROSS} None of those episode numbers are free/available.</b>",
                                         parse_mode=enums.ParseMode.HTML)

    _active_kuku_jobs[uid] = True
    note = f" <i>(skipped {len(missing)} not-free/invalid)</i>" if missing else ""
    await message.reply_text(
        f"<b>🎬 Downloading {len(to_download)} episode(s) of {pending['show_title']}.</b>{note} Use /kukucancel to stop.",
        parse_mode=enums.ParseMode.HTML
    )
    try:
        for ep in to_download:
            if not _active_kuku_jobs.get(uid):
                break
            try:
                await _download_one_episode(client, message, ep, pending["show_title"])
            except Exception:
                continue
    finally:
        _active_kuku_jobs.pop(uid, None)


@Client.on_callback_query(filters.regex(r"^kuku_close$"))
async def kuku_close_callback(client: Client, callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await callback_query.answer()


# --- Bare-link auto-detect --------------------------------------------
@Client.on_message(
    filters.text & filters.private & filters.regex(_KUKU_URL_RE) & ~filters.regex(r"^/"),
    group=1,
)
async def kuku_auto_detect(client: Client, message: Message):
    m = _KUKU_URL_RE.search(message.text)
    if not m:
        return
    if not _configured():
        return  # silently ignore — same as any unconfigured optional integration
    id_m = re.search(r"(\d+)(?:/)?$", m.group(0).rstrip("/"))
    if id_m:
        await _open_kuku_show(client, message, id_m.group(1))
