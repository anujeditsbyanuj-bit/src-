# Akbots
# Spotify music downloader — ported from the standalone Spotify-Music-Bot
# (its spotify_music_bot.py, which scrapes spotidown.app since Spotify's
# own API doesn't serve audio). Supports:
#   - Pasting an open.spotify.com track/playlist/album link — auto-detected,
#     same as every other link-based plugin in this bot
#   - /spotify <link> as the explicit command form
#   - /spotify <song name> — text search, same idea as /saavn and /search
#     (YouTube), via Spotify's own official Web API search endpoint (needs
#     SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET + SPOTIFY_REFRESH_TOKEN in
#     config.py; downloads themselves still go through spotidown.app same
#     as ever, search is the only thing that needs them)
#
# Getting SPOTIFY_REFRESH_TOKEN (one-time, by the app owner):
#   Since Spotify's Feb 2026 Dev Mode changes, plain Client Credentials
#   (app-only, no login) 403s on /search for Development Mode apps unless
#   the owner is Premium — so this now uses the Authorization Code flow
#   instead, same as the app owner logging in once and reusing that login
#   forever via a refresh token:
#     1. In the Spotify dashboard, add a Redirect URI to the app, e.g.
#        https://example.com/callback (doesn't need to be a real live page)
#     2. Open in a browser (fill in your own client_id + redirect_uri):
#        https://accounts.spotify.com/authorize?client_id=<SPOTIFY_CLIENT_ID>
#        &response_type=code&redirect_uri=<your redirect_uri, url-encoded>
#     3. Log in / approve — you'll land on your redirect_uri with
#        ?code=... in the URL; copy that code
#     4. Exchange it for a refresh token (curl, or Postman etc.):
#        curl -X POST https://accounts.spotify.com/api/token \
#          -H "Authorization: Basic <base64(client_id:client_secret)>" \
#          -d grant_type=authorization_code -d code=<code from step 3> \
#          -d redirect_uri=<same redirect_uri as step 2>
#     5. The response's "refresh_token" is what goes in
#        SPOTIFY_REFRESH_TOKEN (config.py / env var) — it doesn't expire
#        the way access tokens do, so this is a one-time setup.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import re
import time
import json
import html
import base64
import asyncio
import requests
import aiohttp
from bs4 import BeautifulSoup
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN
from Akbots.direct_utils import (
    make_output_folder, safe_filename, make_upload_progress, stream_download,
    E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_BOLT,
)
from Akbots.link_cache import try_send_cached, store as _cache_store
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

SPOTI_BASE = "https://spotidown.app"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"
MAX_SEARCH_RESULTS = 8

# intl-en/, intl-hi/, etc. show up on links shared from the mobile app —
# the original bot's regex didn't account for that locale segment.
SPOTIFY_PATTERN = re.compile(
    r"https?://open\.spotify\.com/(?:intl-\w+/)?(track|playlist|album)/[A-Za-z0-9]+",
    re.IGNORECASE,
)

# session_id -> {"kind": "playlist"|"album", "url": str, "chat_id": int, "message": Message}
_SESSIONS = {}

# Client Credentials access token — app-level auth, not tied to any user;
# valid ~1hr, cached and refreshed only once it's actually about to expire.
_token_cache = {"token": None, "expires_at": 0}


def _extract_url(text: str):
    m = SPOTIFY_PATTERN.search(text)
    return m.group(0) if m else None


def _spotify_kind(url: str) -> str:
    if "/track/" in url:
        return "track"
    if "/playlist/" in url:
        return "playlist"
    if "/album/" in url:
        return "album"
    return "unknown"


# ------------------------------------------------------------------ scraper
# All of this talks to spotidown.app over plain requests+bs4, exactly like
# the original bot — it's all blocking I/O, so every entry point below is
# only ever called through asyncio.to_thread().

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": SPOTI_BASE + "/en2",
        "X-Requested-With": "XMLHttpRequest",
    })
    r = s.get(SPOTI_BASE + "/en2", timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    hidden = soup.find("input", {"type": "hidden", "name": re.compile(r"^_")})
    s._csrf = {hidden["name"]: hidden["value"]} if hidden else {}
    return s


def _fetch_action(s: requests.Session, spotify_url: str) -> str:
    r = s.post(SPOTI_BASE + "/action", data={
        "url": spotify_url, "g-recaptcha-response": "faketoken", **s._csrf,
    }, timeout=20)
    resp = r.json()
    if resp.get("error"):
        raise RuntimeError(resp.get("message", "unknown error"))
    return resp["data"]


def _parse_forms(html: str):
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form", {"name": "submitspurl"})
    result = []
    for form in forms:
        fields = {inp["name"]: inp.get("value", "") for inp in form.find_all("input") if inp.get("name")}
        result.append(fields)
    img = soup.find("img")
    fallback_thumb = img["src"] if img else None
    return result, fallback_thumb


def _resolve_track(s: requests.Session, form_data: dict, index: int, fallback_thumb=None):
    """Resolves one track's title/artist/thumbnail + a direct download href
    (does NOT download it — that part goes through this bot's own
    stream_download() for a proper progress bar and consistent behaviour
    with every other plugin)."""
    try:
        info = json.loads(base64.b64decode(form_data.get("data", "")).decode())
        title = info.get("name", f"Track {index + 1}")
        artist = info.get("artist", "")
        name = f"{title} - {artist}" if artist else title
        thumb_url = info.get("cover") or info.get("image") or info.get("thumb") or fallback_thumb
    except Exception:
        title, artist, name, thumb_url = f"Track {index + 1}", "", f"Track {index + 1}", fallback_thumb

    r = s.post(SPOTI_BASE + "/action/track", data=form_data, timeout=30)
    resp = r.json()
    if resp.get("error"):
        return name, title, artist, None, thumb_url, resp.get("message")

    soup = BeautifulSoup(resp["data"], "html.parser")
    img = soup.find("img")
    if img and not thumb_url:
        thumb_url = img.get("src")

    href = None
    a = soup.find("a", href=re.compile(r"/dl\?token=|rapid\.spotidown"))
    if a:
        href = a["href"]
        if href.startswith("/"):
            href = SPOTI_BASE + href
    else:
        a = soup.find("a", href=re.compile(r"https?://"))
        if a:
            href = a["href"]

    if not href:
        return name, title, artist, None, thumb_url, "no download link found"
    return name, title, artist, href, thumb_url, None


def _spotify_get_track_blocking(spotify_url: str):
    s = _make_session()
    html = _fetch_action(s, spotify_url)
    forms, fallback_thumb = _parse_forms(html)
    if not forms:
        raise RuntimeError("no track found")
    name, title, artist, href, thumb_url, err = _resolve_track(s, forms[0], 0, fallback_thumb)
    if err:
        raise RuntimeError(err)
    return name, title, artist, href, thumb_url


def _spotify_get_set_blocking(spotify_url: str):
    """Playlist/album: resolves every track sequentially and returns the
    full list up front (name, title, artist, href, thumb_url, err)."""
    s = _make_session()
    html = _fetch_action(s, spotify_url)
    forms, fallback_thumb = _parse_forms(html)
    results = []
    for i, form in enumerate(forms):
        results.append(_resolve_track(s, form, i, fallback_thumb))
    return results


# ------------------------------------------------------------------ helpers

async def _download_thumb(url: str, dest: str):
    if not url or not url.startswith("http"):
        return None
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, headers={"User-Agent": UA}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                with open(dest, "wb") as f:
                    f.write(await r.read())
        return dest if os.path.exists(dest) and os.path.getsize(dest) > 0 else None
    except Exception:
        return None


async def _send_track(client: Client, message: Message, status: Message, name: str, title: str, artist: str, href: str, thumb_url: str, user_id: int = None):
    if await try_send_cached(client, message, href, status):
        return True

    folder = make_output_folder("spotify")
    safe_title = safe_filename(name, "track")
    unique = f"{safe_title}_{abs(hash(href)) % 100000}"
    dest = os.path.join(folder, f"{unique}.mp3")
    thumb_path = os.path.join(folder, f"{unique}.jpg")
    user_id = user_id or message.from_user.id

    try:
        await stream_download(
            href, dest, status, f"Downloading {name}",
            headers={"User-Agent": UA}, user_id=user_id, file_name=f"{unique}.mp3",
        )
        thumb = await _download_thumb(thumb_url, thumb_path)

        progress = make_upload_progress(status, file_name=name, quality="mp3")
        sent = await client.send_audio(
            chat_id=message.chat.id, audio=dest, thumb=thumb,
            title=title, performer=artist, caption=f"<blockquote><b>🎵 {name}</b></blockquote>",
            reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML, progress=progress,
        )
        try:
            from Akbots.backup import backup_message
            await backup_message(client, sent)
        except Exception:
            pass
        try:
            await _cache_store(href, sent, caption=f"<blockquote><b>🎵 {name}</b></blockquote>")
        except Exception:
            pass
        return True
    except Exception as e:
        await message.reply_text(f"<b>{E_CROSS} Failed:</b> {name}\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        return False
    finally:
        for p in (dest, thumb_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# ------------------------------------------------------------------ flow

async def _start_track(client: Client, message: Message, url: str, user_id: int = None):
    status = await message.reply_text(f"<b>{E_INFO} Fetching track...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        name, title, artist, href, thumb_url = await asyncio.to_thread(_spotify_get_track_blocking, url)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not href:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No downloadable stream found.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>{E_BOLT} Downloading...</b>", parse_mode=enums.ParseMode.HTML)
    ok = await _send_track(client, message, status, name, title, artist, href, thumb_url, user_id=user_id)
    if ok:
        try:
            await status.delete()
        except Exception:
            pass


def _confirm_kb(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        make_button("✅ sᴛᴀʀᴛ ᴅᴏᴡɴʟᴏᴀᴅ", callback_data=f"spotifygo#{session_id}", style=_BS.PRIMARY if _BS else None),
        make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"spotifycancel#{session_id}", style=_BS.DANGER if _BS else None),
    ]])


async def _start_set(client: Client, message: Message, kind: str, url: str):
    session_id = f"{message.chat.id}_{message.id}"
    _SESSIONS[session_id] = {"kind": kind, "url": url, "chat_id": message.chat.id, "message": message}
    await message.reply_text(
        f"<b>{E_ROCKET} Spotify {kind} detected.</b>\n"
        f"<i>This will fetch and download every track — could take a while for large ones.</i>",
        reply_markup=_confirm_kb(session_id),
        parse_mode=enums.ParseMode.HTML,
    )


async def _run_set_download(client: Client, message: Message, kind: str, url: str, status: Message):
    await safe_edit(status.edit_text, f"<b>{E_INFO} Fetching {kind} track list...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        results = await asyncio.to_thread(_spotify_get_set_blocking, url)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Failed to fetch {kind}:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not results:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No tracks found.</b>", parse_mode=enums.ParseMode.HTML)

    total = len(results)
    done = failed = 0
    for i, (name, title, artist, href, thumb_url, err) in enumerate(results, start=1):
        if err or not href:
            failed += 1
            continue
        await safe_edit(status.edit_text, 
            f"<b>{E_BOLT} Track {i}/{total}:</b> {name}\n✅ {done}   ❌ {failed}",
            parse_mode=enums.ParseMode.HTML,
        )
        ok = await _send_track(client, message, status, name, title, artist, href, thumb_url)
        if ok:
            done += 1
        else:
            failed += 1

    await safe_edit(status.edit_text, 
        f"<b>{E_CHECK} Done — {done}/{total} track(s) sent</b>" + (f", {failed} failed." if failed else "."),
        parse_mode=enums.ParseMode.HTML,
    )


# ------------------------------------------------------------------ search

async def _get_spotify_token(session: aiohttp.ClientSession) -> str:
    """Returns a cached/fresh access token via the refresh_token grant.

    Client Credentials (app-only, no user login) used to work fine here,
    but Spotify's Feb 2026 Dev Mode changes restrict it on metadata
    endpoints like /search for Development Mode apps (403 unless the app
    owner is Premium — see the ValueError message in _search_tracks()
    below). Using SPOTIFY_REFRESH_TOKEN instead — obtained once via the
    Authorization Code flow — sidesteps that restriction the same way the
    JS reference snippet's already-authorized Bearer token did, except
    this refreshes itself automatically instead of expiring in ~1hr.
    """
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    if not SPOTIFY_REFRESH_TOKEN:
        raise ValueError(
            "SPOTIFY_REFRESH_TOKEN isn't set. Client Credentials alone no longer "
            "works for /search since Spotify's Feb 2026 Dev Mode changes — see "
            "Akbots/spotify.py's module docstring for how to generate one."
        )

    creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "refresh_token", "refresh_token": SPOTIFY_REFRESH_TOKEN}
    async with session.post(SPOTIFY_TOKEN_URL, headers=headers, data=data) as resp:
        resp.raise_for_status()
        resp_data = await resp.json()

    token = resp_data["access_token"]
    # Spotify only returns a new refresh_token occasionally (rotation) —
    # keep using the configured one unless a replacement is actually sent.
    _token_cache.update(token=token, expires_at=now + resp_data.get("expires_in", 3600))
    return token


async def _search_tracks(query: str) -> list:
    async with aiohttp.ClientSession() as session:
        token = await _get_spotify_token(session)
        params = {"q": query, "type": "track", "limit": MAX_SEARCH_RESULTS}
        headers = {"Authorization": f"Bearer {token}"}
        async with session.get(SPOTIFY_SEARCH_URL, headers=headers, params=params) as resp:
            if resp.status == 403:
                # Not a code bug: as of Spotify's Feb 2026 "Developer Access
                # and Platform Security" change (enforced for all existing
                # Development Mode apps from March 9, 2026), every endpoint
                # 403s unless the app OWNER'S Spotify account has an active
                # Premium subscription — this is an account-level
                # requirement, not something any request header/param can
                # work around. A stale/rotated client secret has also been
                # reported to trigger this even on Premium-owned apps.
                # See: https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
                raise ValueError(
                    "Spotify returned 403 Forbidden. Since March 9, 2026, Spotify requires "
                    "the app OWNER'S account (the one that created this Client ID on "
                    "developer.spotify.com) to have an active Premium subscription — every "
                    "endpoint 403s otherwise, this isn't a bug in the bot. If the owner is "
                    "already Premium, try rotating the Client Secret in the dashboard once "
                    "(reported to clear stuck 403s) and update SPOTIFY_CLIENT_SECRET here."
                )
            resp.raise_for_status()
            data = await resp.json()
    return (data.get("tracks") or {}).get("items") or []


async def _do_spotify_search(client: Client, message: Message, query: str, status: Message = None):
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET and SPOTIFY_REFRESH_TOKEN):
        text = (
            f"<b>{E_INFO} Spotify search isn't set up yet.</b>\n"
            f"<i>Pasting an open.spotify.com link still works fine — for search by "
            f"song name, an admin needs <code>SPOTIFY_CLIENT_ID</code>/"
            f"<code>SPOTIFY_CLIENT_SECRET</code> from "
            f"https://developer.spotify.com/dashboard PLUS a "
            f"<code>SPOTIFY_REFRESH_TOKEN</code> (Client Credentials alone no "
            f"longer works for search since Spotify's Feb 2026 Dev Mode changes) "
            f"— see Akbots/spotify.py's module docstring for how to generate one.</i>"
        )
        if status is None:
            return await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        return await safe_edit(status.edit_text, text, parse_mode=enums.ParseMode.HTML)

    if status is None:
        status = await message.reply_text(f"<b>{E_INFO} Searching Spotify for:</b> {query}", parse_mode=enums.ParseMode.HTML)
    else:
        await safe_edit(status.edit_text, f"<b>{E_INFO} Searching Spotify for:</b> {query}", parse_mode=enums.ParseMode.HTML)
    try:
        tracks = await _search_tracks(query)
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Search failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if not tracks:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No results for:</b> {query}", parse_mode=enums.ParseMode.HTML)

    buttons = []
    for t in tracks:
        track_id = t.get("id")
        if not track_id:
            continue
        title = html.unescape(t.get("name", "Unknown"))
        artists = ", ".join(a.get("name", "") for a in t.get("artists", []) if a.get("name"))
        label = f"🎧 {title} — {artists}" if artists else f"🎧 {title}"
        buttons.append([make_button(label[:64], callback_data=f"spotifypick#{track_id}", style=_BS.PRIMARY if _BS else None)])

    if not buttons:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} No results for:</b> {query}", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, 
        f"<b>{E_ROCKET} Results for:</b> {query}\n<i>Tap a song to download it.</i>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


# ------------------------------------------------------------------ handlers

@Client.on_message(filters.text & filters.private & filters.regex(SPOTIFY_PATTERN) & ~filters.regex(r"^/"), group=1)
async def spotify_auto_detect(client: Client, message: Message):
    url = _extract_url(message.text)
    if not url:
        return
    kind = _spotify_kind(url)
    if kind == "track":
        await _start_track(client, message, url)
    elif kind in ("playlist", "album"):
        await _start_set(client, message, kind, url)
    else:
        await message.reply_text(f"<b>{E_CROSS} Unsupported Spotify link type.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("spotify") & filters.private)
async def spotify_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b>\n"
            f"<code>/spotify &lt;song name&gt;</code> — search Spotify\n"
            f"<code>/spotify &lt;open.spotify.com link&gt;</code> — track/playlist/album link also works",
            parse_mode=enums.ParseMode.HTML,
        )
    query = message.text.split(None, 1)[1].strip()

    if SPOTIFY_PATTERN.search(query):
        url = _extract_url(query)
        kind = _spotify_kind(url)
        if kind == "track":
            return await _start_track(client, message, url)
        elif kind in ("playlist", "album"):
            return await _start_set(client, message, kind, url)
        else:
            return await message.reply_text(f"<b>{E_CROSS} Unsupported Spotify link type.</b>", parse_mode=enums.ParseMode.HTML)

    await _do_spotify_search(client, message, query)


@Client.on_callback_query(filters.regex(r"^spotifypick#"))
async def spotify_pick_callback(client: Client, callback_query: CallbackQuery):
    track_id = callback_query.data.split("#", 1)[1]
    await callback_query.answer()
    url = f"https://open.spotify.com/track/{track_id}"
    await _start_track(client, callback_query.message, url, user_id=callback_query.from_user.id)


@Client.on_callback_query(filters.regex(r"^spotifygo#"))
async def spotify_go_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    session = _SESSIONS.pop(session_id, None)
    await callback_query.answer()
    if not session:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Session expired — send the link again.</b>", parse_mode=enums.ParseMode.HTML)
    await _run_set_download(client, session["message"], session["kind"], session["url"], callback_query.message)


@Client.on_callback_query(filters.regex(r"^spotifycancel#"))
async def spotify_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)
