# Akbots
# PocketFM (pocketfm.com) — audio-drama/audiobook platform.
#
# Uses the REAL PocketFM app API (see Akbots/pocketfm_api.py) — a free
# guest/anonymous access token plus the show.get_details endpoint — ported
# from a reverse-engineered CLI script the user provided. This replaced an
# earlier version of this plugin that leaned on headless-Chromium rendering
# (guessing at the audio URL from network traffic) because PocketFM's API
# wasn't known to be reachable at the time; now that we have it, this is
# faster and far more reliable.
#
# Two ways to use it:
#   /pocketfm <link>              — show link lists episodes; episode link
#                                    goes straight to the quality picker
#   Pasting a pocketfm.com show/episode link — auto-detected, same as every
#                                    other link-based plugin in this bot
#
# Known limitation: only FREE episodes work. Locked/coins-gated episodes
# need a real logged-in (not guest) PocketFM account with purchased coins —
# this plugin only ever uses the free anonymous guest token, it doesn't
# support phone/OTP login or spending coins.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import re
import uuid
from urllib.parse import urlparse, parse_qs, unquote

import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from Akbots.direct_utils import (
    make_output_folder, safe_filename, make_upload_progress, stream_download,
    E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_BOLT,
)
from Akbots.pocketfm_api import PocketFM, PocketFMError
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

E_WARN = '<emoji id=5447644880824181073>⚠️</emoji>'

SHOW_URL_RE = re.compile(r"https?://(?:www\.)?pocketfm\.com/show/[\w-]+(?:/[\w-]+)?", re.IGNORECASE)
EPISODE_URL_RE = re.compile(r"https?://(?:www\.)?pocketfm\.com/episode/[\w-]+", re.IGNORECASE)
ONELINK_RE = re.compile(r"https?://\S*onelink\.me/\S+", re.IGNORECASE)

_PAGE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# Lazily-generated, process-wide free guest token — regenerated on demand
# if a call ever comes back looking like an auth failure. PocketFM's guest
# tokens are free/anonymous (no login), so there's no benefit to a fancier
# per-user or persisted cache here.
_guest_token = None
_guest_device_id = None

# session_id -> {"show_id": str, "episodes": [(story_id, title)], "message": Message}
_SHOW_SESSIONS = {}
# session_id -> {"show_id": str, "story_id": str, "title": str, "show_title": str,
#                "audio_options": [...], "video_options": [...], "message": Message}
_QUALITY_SESSIONS = {}


async def _get_token():
    global _guest_token, _guest_device_id
    if not _guest_token:
        _guest_token, _guest_device_id = await PocketFM().generate_guest_token()
    return _guest_token


def _last_path_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else ""


async def _resolve_onelink(url: str) -> str:
    """onelink.me links are AppsFlyer smart-link redirects — resolve the
    301 without following it all the way (the final app-store/deep-link
    target isn't useful; the redirect Location header itself carries the
    af_sub4/af_sub5 show/story id params we need)."""
    headers = {"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, allow_redirects=False,
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
            location = r.headers.get("Location")
            return location or url


async def _find_show_id_from_episode_page(episode_url: str):
    """A bare /episode/<id> link doesn't carry its show_id in the URL —
    only the API's show.get_details needs it, so best-effort scrape the
    episode's own page for a link back to its parent /show/<id>."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(episode_url, headers=_PAGE_HEADERS,
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                html = await r.text()
    except Exception:
        return None
    m = re.search(r'/show/([\w-]+)', html)
    return m.group(1).rsplit("/", 1)[-1] if m else None


async def resolve_ids(url: str):
    """Returns (show_id, story_id). story_id is None for a show-only link.
    Raises PocketFMError if a show_id couldn't be determined at all."""
    if ONELINK_RE.search(url):
        url = await _resolve_onelink(url)

    decoded = unquote(url)
    parsed = urlparse(decoded)
    params = parse_qs(parsed.query)

    show_id = (params.get("af_sub4") or params.get("show_id") or [None])[0]
    story_id = (params.get("af_sub5") or params.get("entity_id") or [None])[0]

    if "deep_link_value" in params:
        dl_params = parse_qs(urlparse(params["deep_link_value"][0]).query)
        story_id = story_id or (dl_params.get("entity_id") or [None])[0]

    m_episode = EPISODE_URL_RE.search(url)
    if m_episode and not story_id:
        story_id = _last_path_id(m_episode.group(0))

    m_show = SHOW_URL_RE.search(url)
    if m_show and not show_id:
        show_id = _last_path_id(m_show.group(0))

    if not show_id and m_episode:
        show_id = await _find_show_id_from_episode_page(m_episode.group(0))

    if not show_id:
        raise PocketFMError(
            "Couldn't determine the show for this link. If this was a bare episode link, "
            "try pasting the show's link instead (pocketfm.com/show/...) and pick the "
            "episode from the list."
        )
    return show_id, story_id


def _quality_kb(session_id: str, audio_count: int, video_count: int) -> InlineKeyboardMarkup:
    rows = []
    for i in range(audio_count):
        rows.append([make_button(f"Audio option {i + 1}", callback_data=f"pfmq#{session_id}#a{i}", style=_BS.PRIMARY if _BS else None)])
    for i in range(video_count):
        rows.append([make_button(f"Video option {i + 1}", callback_data=f"pfmq#{session_id}#v{i}", style=_BS.PRIMARY if _BS else None)])
    rows.append([make_button("❌ Cancel", callback_data=f"pfmcancel#{session_id}", style=_BS.DANGER if _BS else None)])
    return InlineKeyboardMarkup(rows)


async def _start_episode(client: Client, message: Message, show_id: str, story_id: str, status: Message = None):
    status = status or await message.reply_text(f"<b>{E_ROCKET} Fetching episode details...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        token = await _get_token()
        show_details = await PocketFM().get_show_details(show_id, token, story_id)
        story = PocketFM.find_story(show_details, story_id)
        if not story:
            return await status.edit_text(f"<b>{E_CROSS} Episode not found in this show's story list.</b>", parse_mode=enums.ParseMode.HTML)

        show_title = show_details.get("show_title", "PocketFM")
        story_title = story.get("story_title", "Episode")
        media_url = story.get("media_url")
        video_url = story.get("video_url")

        await status.edit_text(f"<b>{E_INFO} Checking available qualities...</b>", parse_mode=enums.ParseMode.HTML)
        pfm = PocketFM()
        audio_options = await pfm.get_audio_options(media_url, video_url, token)
        video_options = await pfm.get_video_options(video_url, token)

        if not audio_options and not video_options:
            return await status.edit_text(
                f"<b>{E_CROSS} No downloadable stream found for this episode.</b>\n"
                f"<i>It may be coins-locked — this bot only uses a free guest session, which can't "
                f"unlock paid episodes.</i>",
                parse_mode=enums.ParseMode.HTML,
            )

        session_id = uuid.uuid4().hex[:10]
        _QUALITY_SESSIONS[session_id] = {
            "show_title": show_title, "story_title": story_title,
            "audio_options": audio_options, "video_options": video_options,
            "message": message,
        }

        lines = [f"<b>{E_ROCKET} {show_title} — {story_title}</b>", "<i>Choose a quality to download:</i>", ""]
        for i, opt in enumerate(audio_options):
            lines.append(f"🅰{i + 1}. {opt['label']}")
        for i, opt in enumerate(video_options):
            lines.append(f"🅥{i + 1}. {opt['label']}")

        await status.edit_text(
            "\n".join(lines),
            reply_markup=_quality_kb(session_id, len(audio_options), len(video_options)),
            parse_mode=enums.ParseMode.HTML,
        )
    except PocketFMError as e:
        await status.edit_text(f"<b>{E_CROSS} {e}</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)


async def _show_episode_list(client: Client, message: Message, show_id: str):
    status = await message.reply_text(f"<b>{E_INFO} Fetching show details...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        token = await _get_token()
        show_details = await PocketFM().get_show_details(show_id, token)
    except PocketFMError as e:
        return await status.edit_text(f"<b>{E_CROSS} {e}</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    show_title = show_details.get("show_title", "PocketFM")
    stories = show_details.get("stories", [])
    if not stories:
        return await status.edit_text(f"<b>{E_CROSS} No episodes found for this show.</b>", parse_mode=enums.ParseMode.HTML)

    episodes = [(s.get("story_id"), s.get("story_title", "Episode")) for s in stories if s.get("story_id")]
    session_id = uuid.uuid4().hex[:10]
    _SHOW_SESSIONS[session_id] = {"show_id": show_id, "episodes": episodes, "message": message}

    buttons = [
        [make_button(f"🎧 {title[:60]}", callback_data=f"pfmep#{session_id}#{i}", style=_BS.PRIMARY if _BS else None)]
        for i, (_, title) in enumerate(episodes[:25])
    ]
    buttons.append([make_button("❌ Cancel", callback_data=f"pfmcancel#{session_id}", style=_BS.DANGER if _BS else None)])

    await status.edit_text(
        f"<b>{E_ROCKET} {show_title}</b>\n"
        f"<i>Tap an episode to download it. Only free episodes will work — coins-locked ones "
        f"need a real paid/logged-in account, which this bot doesn't support.</i>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


async def _download_and_send(client: Client, message: Message, status: Message,
                              show_title: str, story_title: str, url: str, label: str, kind: str):
    token = await _get_token()
    folder = make_output_folder("pocketfm")
    safe_title = safe_filename(f"{show_title}_{story_title}", "pocketfm")
    ext = ".mp4" if kind == "video" else (os.path.splitext(url.split("?")[0])[1] or ".mp3")
    dest = os.path.join(folder, f"{safe_title}{ext}")

    try:
        await stream_download(
            url, dest, status, f"Downloading {story_title}",
            headers={"User-Agent": "com.radio.pocketfm", "Authorization": token},
            user_id=message.from_user.id, file_name=os.path.basename(dest),
        )
        caption = f"<blockquote><b>🎧 {story_title}</b>\n<i>{show_title} — via Pocket FM</i></blockquote>"
        progress = make_upload_progress(status, file_name=story_title)
        if kind == "video":
            await client.send_video(
                chat_id=message.chat.id, video=dest, caption=caption,
                reply_to_message_id=message.id, supports_streaming=True,
                parse_mode=enums.ParseMode.HTML, progress=progress,
            )
        else:
            await client.send_audio(
                chat_id=message.chat.id, audio=dest, caption=caption,
                reply_to_message_id=message.id,
                parse_mode=enums.ParseMode.HTML, progress=progress,
            )
        await status.delete()
    except Exception as e:
        await status.edit_text(f"<b>{E_CROSS} Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass


async def _handle_url(client: Client, message: Message, url: str):
    status = await message.reply_text(f"<b>{E_INFO} Resolving PocketFM link...</b>", parse_mode=enums.ParseMode.HTML)
    try:
        show_id, story_id = await resolve_ids(url)
    except PocketFMError as e:
        return await status.edit_text(f"<b>{E_CROSS} {e}</b>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        return await status.edit_text(f"<b>{E_CROSS} Failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    if story_id:
        await status.delete()
        await _start_episode(client, message, show_id, story_id)
    else:
        await status.delete()
        await _show_episode_list(client, message, show_id)


@Client.on_message(
    filters.text & filters.private
    & (filters.regex(SHOW_URL_RE) | filters.regex(EPISODE_URL_RE) | filters.regex(ONELINK_RE))
    & ~filters.regex(r"^/"),
    group=1,
)
async def pocketfm_auto_detect(client: Client, message: Message):
    text = message.text
    m = EPISODE_URL_RE.search(text) or SHOW_URL_RE.search(text) or ONELINK_RE.search(text)
    if m:
        await _handle_url(client, message, m.group(0))


@Client.on_message(filters.command("pocketfm") & filters.private)
async def pocketfm_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/pocketfm &lt;pocketfm.com show or episode link&gt;</code>\n"
            f"{E_WARN} <i>Only free episodes can be downloaded — this bot uses a free guest session, "
            f"which can't unlock coins-locked/premium episodes.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = message.text.split(None, 1)[1].strip()
    await _handle_url(client, message, url)


@Client.on_callback_query(filters.regex(r"^pfmep#"))
async def pocketfm_episode_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, idx = callback_query.data.split("#")
    session = _SHOW_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await callback_query.message.edit_text(f"<b>{E_CROSS} Session expired — send the link again.</b>", parse_mode=enums.ParseMode.HTML)
    story_id, _ = session["episodes"][int(idx)]
    await _start_episode(client, session["message"], session["show_id"], story_id, status=callback_query.message)


@Client.on_callback_query(filters.regex(r"^pfmq#"))
async def pocketfm_quality_callback(client: Client, callback_query: CallbackQuery):
    _, session_id, choice = callback_query.data.split("#")
    session = _QUALITY_SESSIONS.get(session_id)
    await callback_query.answer()
    if not session:
        return await callback_query.message.edit_text(f"<b>{E_CROSS} Session expired — send the link again.</b>", parse_mode=enums.ParseMode.HTML)

    kind = "video" if choice.startswith("v") else "audio"
    idx = int(choice[1:])
    opt_list = session["video_options"] if kind == "video" else session["audio_options"]
    if idx >= len(opt_list):
        return await callback_query.message.edit_text(f"<b>{E_CROSS} Invalid selection.</b>", parse_mode=enums.ParseMode.HTML)
    opt = opt_list[idx]

    # A separate reply, not an edit of the picker — keeps the quality menu
    # intact and re-postable below so audio AND video (or several bitrates)
    # can all be grabbed from the same episode without resending the link.
    status = await session["message"].reply_text(f"<b>{E_BOLT} Starting download...</b>", parse_mode=enums.ParseMode.HTML)
    await _download_and_send(
        client, session["message"], status,
        session["show_title"], session["story_title"], opt["url"], opt["label"], kind,
    )

    audio_options, video_options = session["audio_options"], session["video_options"]
    lines = [f"<b>{E_ROCKET} {session['show_title']} — {session['story_title']}</b>",
              f"<i>{E_CHECK} Grabbed: {opt['label']}. Pick another quality if you want it too, "
              f"or tap Done.</i>", ""]
    for i, o in enumerate(audio_options):
        lines.append(f"🅰{i + 1}. {o['label']}")
    for i, o in enumerate(video_options):
        lines.append(f"🅥{i + 1}. {o['label']}")

    rows = [[make_button(f"🎧 Audio {i + 1}", callback_data=f"pfmq#{session_id}#a{i}", style=_BS.PRIMARY if _BS else None)] for i in range(len(audio_options))]
    rows += [[make_button(f"🎬 Video {i + 1}", callback_data=f"pfmq#{session_id}#v{i}", style=_BS.PRIMARY if _BS else None)] for i in range(len(video_options))]
    rows.append([make_button("✅ Done", callback_data=f"pfmcancel#{session_id}", style=_BS.SUCCESS if _BS else None)])

    try:
        await callback_query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^pfmcancel#"))
async def pocketfm_cancel_callback(client: Client, callback_query: CallbackQuery):
    session_id = callback_query.data.split("#", 1)[1]
    _SHOW_SESSIONS.pop(session_id, None)
    _QUALITY_SESSIONS.pop(session_id, None)
    await callback_query.answer("Cancelled")
    await callback_query.message.edit_text(f"<b>{E_CROSS} Cancelled.</b>", parse_mode=enums.ParseMode.HTML)
