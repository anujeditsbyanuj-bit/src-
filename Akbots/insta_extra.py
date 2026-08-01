"""
Instagram Profile Picture / Stories / Highlights.

Unlike posts and reels (public HTML scraping, see instagram.py), these three
need Instagram's private web-app JSON endpoints — the same ones the
instagram.com website itself calls. Stories and Highlights ALWAYS require an
authenticated session cookie (sessionid) since they aren't served to logged
-out requests; Profile Picture usually works without login for public
accounts but still benefits from cookies for accounts near the rate limit.

Set cookies first via /setcookies instagram.com (upload a Netscape-format
cookies.txt exported while logged into instagram.com in a browser) — the
same cookie store instagram.py already uses for posts/reels.
"""

import os
import re
import asyncio
import requests
from pyrogram import Client, filters, enums
from pyrogram.types import Message

from Akbots.instagram import (
    _load_cookies, _download_insta_media, _build_media_preview, FETCH_HEADERS,
)
from Akbots.direct_utils import safe_edit

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO = '<emoji id=5334544901428229844>ℹ️</emoji>'

# Instagram's own web client's public app id — required by these endpoints
# to distinguish "real" API calls from generic page requests. Widely known
# / not a secret, ships in every instagram.com page's client-side bundle.
IG_APP_ID = "936619743392459"

STORY_LINK_RE = re.compile(r"instagram\.com/stories/([A-Za-z0-9_.]+)/?", re.IGNORECASE)
HIGHLIGHT_LINK_RE = re.compile(r"instagram\.com/stories/highlights/(\d+)", re.IGNORECASE)


def _ig_headers() -> dict:
    h = dict(FETCH_HEADERS)
    h["x-ig-app-id"] = IG_APP_ID
    return h


def _clean_username(raw: str) -> str:
    raw = raw.strip().lstrip("@")
    m = re.search(r"instagram\.com/([^/?#]+)/?", raw, re.IGNORECASE)
    if m:
        return m.group(1)
    return raw


def _get_profile_sync(username: str, cookies):
    """Returns dict with id, profile_pic_url_hd, is_private. Raises
    ValueError on failure."""
    url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}"
    try:
        resp = requests.get(url, headers=_ig_headers(), cookies=cookies, timeout=20)
    except Exception as e:
        raise ValueError(f"Request failed: {e}")
    if resp.status_code != 200:
        raise ValueError(
            f"Instagram returned HTTP {resp.status_code} — the account may not exist, "
            "or Instagram is login-walling this request. Try /setcookies instagram.com with a fresh, logged-in cookies.txt."
        )
    try:
        user = resp.json()["data"]["user"]
    except Exception:
        raise ValueError("Unexpected response from Instagram (couldn't find profile data).")
    if not user:
        raise ValueError("Account not found.")
    return user


def _get_reel_media_sync(reel_id: str, cookies):
    """reel_id is either a numeric user id (active stories) or
    'highlight:<id>' (a highlight reel). Returns list of ('video'|'photo',
    url) tuples, oldest-first. Raises ValueError on failure."""
    url = f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={reel_id}"
    try:
        resp = requests.get(url, headers=_ig_headers(), cookies=cookies, timeout=20)
    except Exception as e:
        raise ValueError(f"Request failed: {e}")
    if resp.status_code != 200:
        raise ValueError(f"Instagram returned HTTP {resp.status_code}.")
    try:
        reels = resp.json().get("reels_media") or []
    except Exception:
        raise ValueError("Unexpected response from Instagram.")
    if not reels:
        return []

    items = []
    for it in reels[0].get("items", []):
        video_versions = it.get("video_versions")
        if video_versions:
            items.append(("video", video_versions[0]["url"]))
            continue
        candidates = (it.get("image_versions2") or {}).get("candidates") or []
        if candidates:
            items.append(("photo", candidates[0]["url"]))
    return items


def _get_highlights_tray_sync(user_id: str, cookies):
    """Returns list of (reel_id, title) tuples, e.g. ('highlight:123', 'Travel')."""
    url = f"https://i.instagram.com/api/v1/highlights/{user_id}/highlights_tray/"
    try:
        resp = requests.get(url, headers=_ig_headers(), cookies=cookies, timeout=20)
    except Exception as e:
        raise ValueError(f"Request failed: {e}")
    if resp.status_code != 200:
        raise ValueError(f"Instagram returned HTTP {resp.status_code}.")
    try:
        tray = resp.json().get("tray") or []
    except Exception:
        raise ValueError("Unexpected response from Instagram.")
    return [(t.get("id"), t.get("title") or "Highlight") for t in tray if t.get("id")]


_COOKIE_HINT = (
    "\n\n<i>Stories and Highlights need a logged-in session — set one up with "
    "<code>/setcookies instagram.com</code> (upload a fresh cookies.txt exported "
    "while logged into instagram.com in a browser).</i>"
)


async def _download_profile_picture(client: Client, message: Message, username_or_url: str):
    username = _clean_username(username_or_url)
    status = await message.reply_text(f"<b>{E_INFO} Fetching @{username}'s profile picture...</b>", parse_mode=enums.ParseMode.HTML)
    cookies = _load_cookies()
    try:
        user = await asyncio.to_thread(_get_profile_sync, username, cookies)
    except ValueError as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    pic_url = user.get("profile_pic_url_hd") or user.get("profile_pic_url")
    if not pic_url:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Could not find a profile picture for @{username}.</b>", parse_mode=enums.ParseMode.HTML)

    media_items = [("photo", pic_url)]
    preview = await _build_media_preview(media_items, f"{E_INFO} Profile Picture", f"@{username}")
    await safe_edit(status.edit_text, preview, parse_mode=enums.ParseMode.HTML)
    await asyncio.sleep(1.2)

    await _download_insta_media(
        client, message, status,
        media_items,
        f"pfp_{username}",
    )


async def _download_user_stories(client: Client, message: Message, username_or_url: str):
    username = _clean_username(username_or_url)
    status = await message.reply_text(f"<b>{E_INFO} Fetching @{username}'s stories...</b>", parse_mode=enums.ParseMode.HTML)
    cookies = _load_cookies()
    if not cookies:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Login required for Stories.</b>{_COOKIE_HINT}", parse_mode=enums.ParseMode.HTML)

    try:
        user = await asyncio.to_thread(_get_profile_sync, username, cookies)
        items = await asyncio.to_thread(_get_reel_media_sync, user["id"], cookies)
    except ValueError as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not items:
        return await safe_edit(status.edit_text, 
            f"<b>{E_INFO} No active stories for @{username} right now</b>\n"
            f"<i>Stories expire after 24h, or the account is private and you don't have access.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    preview = await _build_media_preview(items, f"{E_INFO} Instagram Stories", f"@{username}")
    await safe_edit(status.edit_text, preview, parse_mode=enums.ParseMode.HTML)
    await asyncio.sleep(1.2)

    await _download_insta_media(client, message, status, items, f"story_{username}")


async def _download_highlight_by_id(client: Client, message: Message, highlight_id: str, title_hint: str = None):
    status = await message.reply_text(f"<b>{E_INFO} Fetching highlight...</b>", parse_mode=enums.ParseMode.HTML)
    cookies = _load_cookies()
    if not cookies:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Login required for Highlights.</b>{_COOKIE_HINT}", parse_mode=enums.ParseMode.HTML)

    try:
        items = await asyncio.to_thread(_get_reel_media_sync, f"highlight:{highlight_id}", cookies)
    except ValueError as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not items:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} This highlight is empty, private, or the link is invalid.</b>", parse_mode=enums.ParseMode.HTML)

    tag = f"highlight_{title_hint}" if title_hint else f"highlight_{highlight_id}"
    preview = await _build_media_preview(items, f"{E_INFO} Instagram Highlight", title_hint)
    await safe_edit(status.edit_text, preview, parse_mode=enums.ParseMode.HTML)
    await asyncio.sleep(1.2)

    await _download_insta_media(client, message, status, items, tag)


MAX_HIGHLIGHTS = 10  # safety cap so one command can't trigger a huge batch


async def _download_all_highlights(client: Client, message: Message, username_or_url: str):
    username = _clean_username(username_or_url)
    status = await message.reply_text(f"<b>{E_INFO} Fetching @{username}'s highlights...</b>", parse_mode=enums.ParseMode.HTML)
    cookies = _load_cookies()
    if not cookies:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Login required for Highlights.</b>{_COOKIE_HINT}", parse_mode=enums.ParseMode.HTML)

    try:
        user = await asyncio.to_thread(_get_profile_sync, username, cookies)
        tray = await asyncio.to_thread(_get_highlights_tray_sync, user["id"], cookies)
    except ValueError as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not tray:
        return await safe_edit(status.edit_text, f"<b>{E_INFO} @{username} has no highlights.</b>", parse_mode=enums.ParseMode.HTML)

    if len(tray) > MAX_HIGHLIGHTS:
        await safe_edit(status.edit_text, 
            f"<b>{E_INFO} @{username} has {len(tray)} highlights — downloading the first {MAX_HIGHLIGHTS}.</b>\n"
            f"<i>Paste a single highlight's link instead to grab just that one.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        await asyncio.sleep(2)
        tray = tray[:MAX_HIGHLIGHTS]

    for idx, (reel_id, title) in enumerate(tray, start=1):
        hl_status = await message.reply_text(
            f"<b>{E_ROCKET} Highlight {idx}/{len(tray)}: {title}</b>", parse_mode=enums.ParseMode.HTML
        )
        try:
            items = await asyncio.to_thread(_get_reel_media_sync, reel_id, cookies)
        except ValueError as e:
            await safe_edit(hl_status.edit_text, f"<b>{E_CROSS} '{title}' failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            continue
        if not items:
            await safe_edit(hl_status.edit_text, f"<b>{E_CROSS} '{title}' is empty.</b>", parse_mode=enums.ParseMode.HTML)
            continue
        preview = await _build_media_preview(items, f"{E_INFO} {title}", include_sizes=False)
        await safe_edit(hl_status.edit_text, preview, parse_mode=enums.ParseMode.HTML)
        safe_title = re.sub(r"[^A-Za-z0-9_-]+", "_", title)[:40] or "highlight"
        await _download_insta_media(client, message, hl_status, items, f"hl_{safe_title}_{idx}")

    try:
        await status.delete()
    except Exception:
        pass


async def route_special_insta_url(client: Client, message: Message, url: str):
    """Called from instagram.py's auto-detect (and /insta redirect) for any
    instagram.com link that isn't a plain post/reel/tv permalink."""
    m = HIGHLIGHT_LINK_RE.search(url)
    if m:
        return await _download_highlight_by_id(client, message, m.group(1))

    m = STORY_LINK_RE.search(url)
    if m:
        return await _download_user_stories(client, message, m.group(1))

    return await _download_profile_picture(client, message, url)


@Client.on_message(filters.command(["pfp", "profilepic"]) & filters.private)
async def pfp_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/pfp &lt;username or profile URL&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _download_profile_picture(client, message, message.command[1])


@Client.on_message(filters.command(["story", "stories"]) & filters.private)
async def story_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/story &lt;username or story URL&gt;</code>\n"
            f"<i>Needs a logged-in session — see /setcookies.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1]
    m = STORY_LINK_RE.search(arg)
    await _download_user_stories(client, message, m.group(1) if m else arg)


@Client.on_message(filters.command(["highlights", "highlight"]) & filters.private)
async def highlights_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/highlights &lt;username&gt;</code> — downloads all highlights\n"
            f"<i>Or paste a single highlight link directly to grab just that one. Needs a logged-in session — see /setcookies.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    arg = message.command[1]
    m = HIGHLIGHT_LINK_RE.search(arg)
    if m:
        return await _download_highlight_by_id(client, message, m.group(1))
    await _download_all_highlights(client, message, arg)
