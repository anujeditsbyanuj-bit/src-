import logging
import re
import os
import shlex
import traceback
from functools import wraps
from contextlib import suppress
import time
import math
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
import subprocess
import asyncio

from Akbots.crunchyroll_dl.crunchyroll import (
    Crunchyroll, CrunchyrollAuth, CrunchyrollLicense,
    parse_mpd_content, get_segment_link_list, download_segment,
    get_filter_complex, convert_vtt_to_srt_custom
)
from Akbots.mp4decrypt_util import find_mp4decrypt
from config import *

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
)
from pyrogram.errors import MessageNotModified, QueryIdInvalid

# ── async/sync bridge ────────────────────────────────────────────────────
# This whole plugin (ported from a standalone bot) is written in the
# classic synchronous Pyrogram style: plain `def` handlers, bare
# `message.reply_text(...)` calls with no `await`. That's actually the
# right shape for it, since the download/decrypt/merge/ffmpeg pipeline
# below is blocking, CPU- and network-heavy work that should never run on
# goon_updated's main event loop. Every Pyrogram entry point here is
# therefore dispatched via asyncio.to_thread(), and any Pyrogram API call
# made from inside that worker thread is routed back onto the bot's event
# loop with _run_sync() below instead of the (unmaintained/unavailable in
# kurigram) pyrogram.sync compatibility shim the original code assumed.
_BOT_LOOP = None

def _run_sync(coro):
    fut = asyncio.run_coroutine_threadsafe(coro, _BOT_LOOP)
    return fut.result()

# Per-user "is a download already running" gate (in-memory only, matching
# the original bot's design — resets on restart).
active_downloads = {}
# Per-user multi-step conversation state (quality/audio/subtitle picker
# flow) — also in-memory only, same as the original.
user_states = {}

try:
    auth = CrunchyrollAuth()
    if use_watermark and original_quality:
        print("WARNING: Original quality is set to True, but watermarking is enabled. Watermarking will be disabled.")
        use_watermark = False
    if use_watermark:
        encoding_code = "libx264"
        original_quality = False
        audio_codec = "aac"
    if original_quality:
       encoding_code = "copy"
       audio_codec = "copy"
       use_watermark = False

except Exception as e:
    # A broken Crunchyroll auth setup shouldn't take the whole multi-plugin
    # bot down — log it and let this plugin's handlers fail individually
    # instead of calling exit(1) on the entire process.
    print(f"ERROR setting up Crunchyroll plugin: {e}")
    auth = None
    encoding_code = "copy"
    audio_codec = "copy"


class tgUploader:
    def __init__(self, app, msg):
        self.app = app
        self.msg = msg
    def upload_file(self, file_path):
        try:
            file_name = os.path.basename(file_path)  
            duration = get_duration(file_path)
            thumb = get_thumbnail(file_path, "", duration / 2)
            caption = '''<code>{}</code>'''.format(file_name)
            progress_args_text = "<code>[+]</code> <b>{}</b>\n<code>{}</code>".format("Uploading", file_name)
            _run_sync(self.app.send_video(
                video=file_path, 
                chat_id=self.msg.chat.id, 
                caption=caption, 
                progress=progress_for_pyrogram, 
                progress_args=(
                        progress_args_text,
                        self.msg, 
                        time.time()
                ), thumb=thumb, duration=duration, width=1280, height=720
            ))
            os.remove(thumb)
        except Exception as e:
            print(e)
            _run_sync(self.msg.edit(f"`{e}`"))

def get_duration(filepath):
    metadata = extractMetadata(createParser(filepath))
    if metadata and metadata.has("duration"):
        return metadata.get('duration').seconds
    return 0

def get_thumbnail(in_filename, path, ttl):
    out_filename = os.path.join(path, f"{time.time()}.jpg")
    
    try:
        command = [
            "ffmpeg",
            "-ss", str(ttl),
            "-i", in_filename,
            "-frames:v", "1",
            "-q:v", "2", 
            "-y",      
            out_filename
        ]

        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if result.returncode == 0:
            return out_filename
        else:
            print(f"FFmpeg error:\n{result.stderr}")
            return None

    except Exception as e:
        print(f"Error generating thumbnail: {e}")
        return None
    
def progress_for_pyrogram(
    current,
    total,
    ud_type,
    message,
    start
):
    now = time.time()
    diff = now - start

    if round(diff % 10.00) == 0 or current == total:
        percentage = current * 100 / total
        speed = current / diff
        elapsed_time = get_readable_time(round(diff))
        time_to_completion = get_readable_time(round((total - current) / speed))
        
        progress = "[{0}{1}]".format(
            ''.join(["█" for _ in range(math.floor(percentage / 5))]),
            ''.join(["░" for _ in range(20 - math.floor(percentage / 5))])
        )

        tmp = (
            f"{progress} `{round(percentage, 2)}%`\n"
            f"`{humanbytes(current)} of {humanbytes(total)}`\n"
            f"**Speed:** `{humanbytes(speed)}/s`\n"
            f"**ETA:** `{time_to_completion}`"
        )

        try:
            _run_sync(message.edit(
                text=f"{ud_type}\n\n{tmp}"
            ))
        except Exception as e:
            print(f"Error updating progress: {e}")
def humanbytes(size):
    """Convert bytes to a human-readable format."""
    if not size:
        return "0B"
    power = 2 ** 10
    n = 0
    labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {labels[n]}"

def get_readable_time(seconds: int) -> str:
    result = ''
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f'{days}d'
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f'{hours}h'
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f'{minutes}m'
    seconds = int(seconds)
    result += f'{seconds}s'
    return result
def check_active_download(func):
    @wraps(func)
    def wrapper(client, update):
        user_id = update.from_user.id
        if active_downloads.get(user_id):
            if isinstance(update, CallbackQuery):
                _run_sync(update.answer("Please wait for your previous download to complete.", show_alert=True))
            elif isinstance(update, Message):
                _run_sync(update.reply_text("Please wait for your previous download to complete."))
            return
        return func(client, update)
    return wrapper

def run_shell_command(command):
    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate()
    return stdout.decode(), stderr.decode(), process.returncode

def edit_message(message: Message, text: str, keyboard: InlineKeyboardMarkup = None):
    with suppress(MessageNotModified, QueryIdInvalid):
       _run_sync(message.edit_text(text, reply_markup=keyboard, parse_mode=enums.ParseMode.MARKDOWN))

@Client.on_message(filters.command("crstart"))
async def start_command(client, message: Message):
    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()
    await asyncio.to_thread(_start_command_worker, client, message)

def _start_command_worker(client, message: Message):
    _run_sync(message.reply_text(
        "Hello! Welcome to the Crunchyroll Downloader Bot.\n"
        "Send me a Crunchyroll video or series URL using /download.\n"
        "Example: `/download https://www.crunchyroll.com/watch/GXXXXXXX/episode-title`\n"
        "Use /crhelp for more commands."
    ))

@Client.on_message(filters.command("crhelp"))
async def help_command(client, message: Message):
    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()
    await asyncio.to_thread(_help_command_worker, client, message)

def _help_command_worker(client, message: Message):
    help_text = """
**Crunchyroll Downloader Bot Help**

**/crstart**: Shows the welcome message.
**/crhelp**: Shows this help message.
**/download <url> or /dl <url>**: Starts the download process for a Crunchyroll video or series URL.
    - Example (Episode): `/download https://www.crunchyroll.com/watch/GXXXXXXX/episode-title`
    - Example (Series): `/download https://www.crunchyroll.com/series/GXXXXXXX/series-title`
**/cancel**: Cancels the current selection process (if any).
    """
    _run_sync(message.reply_text(help_text, parse_mode=enums.ParseMode.MARKDOWN))

@Client.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_states and user_id not in active_downloads:
        # Nothing Crunchyroll-related to cancel for this user — let the
        # message fall through to the bot's global /cancel handler
        # (Akbots/start.py's send_cancel -> _stop_everything_for), which
        # stops ALL of a user's active downloads (ytdl, terabox, m3u8,
        # etc.), not just a Crunchyroll one.
        #
        # Without this, this handler silently swallowed every /cancel for
        # every user: Pyrogram loads plugins alphabetically ("crunchyroll"
        # < "start"), both register at the same default group (0), and
        # this handler never used to call continue_propagation() — so it
        # always won the race and start.py's handler never even ran, even
        # for users who'd never touched Crunchyroll.
        message.continue_propagation()

    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()
    await asyncio.to_thread(_cancel_command_worker, client, message)

def _cancel_command_worker(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_states:
        state = user_states.pop(user_id)
        if state.get("message"):
            edit_message(state["message"], "Download process cancelled.")
        else:
            _run_sync(message.reply_text("Download process cancelled."))
        if user_id in active_downloads:
            del active_downloads[user_id]
    else:
        _run_sync(message.reply_text("No active download process to cancel."))


@Client.on_message(filters.command(["download", "dl"]) & filters.chat(AUTHORIZED_USERS))
async def download_command(client, message: Message):
    # /dl is a SHARED alias with Akbots/ytdl.py's own /dl (general yt-dlp
    # downloader) — both match for any admin (AUTHORIZED_USERS = ADMINS),
    # and this handler used to swallow every /dl unconditionally, so a
    # non-Crunchyroll link (e.g. a YouTube URL) typed as /dl by an admin
    # never reached ytdl.py's handler at all. /download is unambiguous
    # (only this file registers it) and is always handled here; /dl only
    # stays here if the argument actually looks like a Crunchyroll URL —
    # otherwise it falls through so ytdl.py's /dl gets a shot at it.
    if message.command[0].lower() == "dl":
        arg = message.command[1] if len(message.command) >= 2 else ""
        if "crunchyroll.com" not in arg.lower():
            message.continue_propagation()

    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()
    await asyncio.to_thread(_download_command_worker, client, message)

@check_active_download 
def _download_command_worker(client, message: Message):
    user_id = message.from_user.id
    global crunchyroll, license_handler, vid_token
    if use_account:
        if not Email or not Password:
            print("ERROR: Email and Password required in config.py for account login.")
            vid_token = auth.get_guest_token()
            if not vid_token:
                print("Failed to get guest token.")
            print("WARNING: Using guest token due to missing credentials.")
        
        else:
            vid_token = auth.get_user_token(Email, Password)
            if not vid_token:
                print("WARNING: Invalid email or password, falling back to guest token.")
                vid_token = auth.get_guest_token()
                if not vid_token:
                    print("Failed to get guest token after login failure.")
    else:
        vid_token = auth.get_guest_token()
        if not vid_token:
            raise Exception("Failed to get guest token.")

    crunchyroll = Crunchyroll(vid_token)
    license_handler = CrunchyrollLicense()
    print("Crunchyroll Authentication successful.")
    if len(message.command) < 2:
        _run_sync(message.reply_text(
              "Please provide a Crunchyroll URL.\nExample: `/download <url>` or `/dl <url>`",
               parse_mode=enums.ParseMode.MARKDOWN
            ))
        return

    video_url = message.command[1]
    status_msg = _run_sync(message.reply_text("Processing URL..."))

    user_states[user_id] = {
        "step": "initial",
        "url": video_url,
        "data": {},
        "message": status_msg,
        "is_series": False
    }

    try:
        if "watch" in video_url:
            match = re.search(r'"?https?://www\.crunchyroll\.com/(?:watch)/([^/?"\']+)', video_url)
            if not match:
                edit_message(status_msg, "Invalid episode URL format.")
                del user_states[user_id]
                return
            content_id = match.group(1)

            edit_message(status_msg, "Fetching video information...")
            video_info = crunchyroll.get_video_info(content_id)
            if not video_info:
                edit_message(status_msg, "Video not found or not accessible.")
                del user_states[user_id]
                return

            user_states[user_id]["data"]["video_info"] = video_info
            user_states[user_id]["data"]["id"] = content_id
            user_states[user_id]["is_series"] = False

            pssh, mpd_content, token = crunchyroll.get_pssh(video_info)
            if not mpd_content:
                 edit_message(status_msg, "Could not fetch MPD content.")
                 del user_states[user_id]
                 return

            user_states[user_id]["data"]["pssh"] = pssh
            user_states[user_id]["data"]["mpd_content"] = mpd_content
            user_states[user_id]["data"]["drm_token"] = token

            video_list, _ = parse_mpd_content(mpd_content) 
            if not video_list:
                edit_message(status_msg, "No video streams found in MPD.")
                del user_states[user_id]
                return

            user_states[user_id]["data"]["available_video_qualities"] = video_list

            ask_video_quality(client, user_id)

        elif "series" in video_url:
            match = re.search(r'"?https?://www\.crunchyroll\.com/(?:series)/([^/?"\']+)', video_url)
            if not match:
                edit_message(status_msg, "Invalid series URL format.")
                del user_states[user_id]
                return
            series_id = match.group(1)

            edit_message(status_msg, "Fetching series information...")
            series_data, _ = crunchyroll.get_content_info(url=video_url)
            if not series_data or 'data' not in series_data or not series_data['data']:
                edit_message(status_msg, "Series not found or no episodes available.")
                del user_states[user_id]
                return

            episodes = [
                {'episode_no': ep.get('episode_number', idx + 1), 'guid': ep['id'], 'title': ep.get('title', f'Episode {idx+1}')}
                for idx, ep in enumerate(series_data['data'])
            ]

            user_states[user_id]["data"]["episodes"] = episodes
            user_states[user_id]["data"]["series_title"] = series_data['data'][0].get('series_title', 'Unknown Series')
            user_states[user_id]["is_series"] = True
            user_states[user_id]["data"]["total_episodes"] = len(episodes)

            user_states[user_id]["step"] = "ask_episode_count"
            edit_message(
                status_msg,
                f"Found series '{user_states[user_id]['data']['series_title']}' with {len(episodes)} episodes.\n"
                "How many episodes do you want to download (from the beginning)? Enter a number:",
                keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel")]])
             )

        else:
            edit_message(status_msg, "Invalid URL. Please provide a Crunchyroll 'watch' or 'series' URL.")
            if user_id in user_states: del user_states[user_id]
            return

    except Exception as e:
        print(f"Error during initial processing for user {user_id}: {e}")
        traceback.print_exc()
        edit_message(status_msg, f"An error occurred: {e}")
        if user_id in user_states: del user_states[user_id]


@Client.on_message(filters.text & ~filters.command(["start", "help", "download"]) & filters.private)
async def handle_text_reply(client, message: Message):
    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()
    await asyncio.to_thread(_handle_text_reply_worker, client, message)

def _handle_text_reply_worker(client, message: Message):
    user_id = message.from_user.id
    if user_id in user_states and user_states[user_id]["step"] == "ask_episode_count":
        state = user_states[user_id]
        status_msg = state["message"]
        try:
            count = int(message.text.strip())
            total_episodes = state["data"]["total_episodes"]
            if 0 < count <= total_episodes:
                state["data"]["episodes_to_download_count"] = count
                state["data"]["episodes"] = state["data"]["episodes"][:count]

                first_episode_id = state["data"]["episodes"][0]['guid']
                edit_message(status_msg, f"Fetching info for first episode to determine options...")

                video_info = crunchyroll.get_video_info(first_episode_id)
                if not video_info:
                    edit_message(status_msg, "Could not fetch info for the first episode.")
                    del user_states[user_id]
                    return

                _, mpd_content, _ = crunchyroll.get_pssh(video_info)
                if not mpd_content:
                    edit_message(status_msg, "Could not fetch MPD for the first episode.")
                    del user_states[user_id]
                    return

                video_list, _ = parse_mpd_content(mpd_content)
                if not video_list:
                    edit_message(status_msg, "No video streams found for the first episode.")
                    del user_states[user_id]
                    return

                state["data"]["video_info"] = video_info
                state["data"]["available_video_qualities"] = video_list

                ask_video_quality(client, user_id)

            else:
                _run_sync(message.reply_text(f"Invalid number. Please enter a number between 1 and {total_episodes}."))
        except ValueError:
            _run_sync(message.reply_text("Invalid input. Please enter a number."))
        except Exception as e:
            print(f"Error processing episode count for user {user_id}: {e}")
            traceback.print_exc()
            edit_message(status_msg, f"An error occurred: {e}")
            del user_states[user_id]


def ask_video_quality(client, user_id):
    if user_id not in user_states: return
    state = user_states[user_id]
    status_msg = state["message"]

    video_list = state["data"]["available_video_qualities"]
    max_quality_p = float('inf')

    buttons = []
    for i, video in enumerate(video_list):
         height = int(video.get('height', 0))
         if height <= max_quality_p:
             label = f"{height}p ({int(video.get('bandwidth', 0)) // 1000} kbps)"
             buttons.append([InlineKeyboardButton(label, callback_data=f"quality_{i}")])

    if not buttons:
         edit_message(status_msg, "No video qualities available within your limits.")
         del user_states[user_id]
         return

    buttons.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    keyboard = InlineKeyboardMarkup(buttons)
    state["step"] = "select_quality"
    edit_message(status_msg, "Select video quality:", keyboard=keyboard)

def ask_audio_language(client, user_id):
    if user_id not in user_states: return
    state = user_states[user_id]
    status_msg = state["message"]
    video_info = state["data"]["video_info"]

    if 'versions' not in video_info or not video_info['versions']:
        edit_message(status_msg, "No audio versions found for this video.")
        state["data"]["selected_audios"] = []
        ask_subtitles(client, user_id)
        return

    max_audios = float('inf')
    state["data"]["selected_audios"] = []

    buttons = []
    audio_options = {}
    for index, version in enumerate(video_info['versions']):
        locale = version['audio_locale']
        guid = version['guid']
        lang_name = locale_map.get(locale, locale)
        audio_options[guid] = locale 
        buttons.append([InlineKeyboardButton(lang_name, callback_data=f"audio_{guid}")])

    state["data"]["available_audio_options"] = audio_options

    buttons.append([
        InlineKeyboardButton(f"Done (0/{max_audios if max_audios != float('inf') else '∞'})", callback_data="audio_done"),
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ])
    keyboard = InlineKeyboardMarkup(buttons)
    state["step"] = "select_audio"
    edit_message(
        status_msg,
        f"Select audio language(s) (Max: {'unlimited' if max_audios == float('inf') else max_audios}). Press 'Done' when finished.",
        keyboard=keyboard
    )


def ask_subtitles(client, user_id):
    if user_id not in user_states: return
    state = user_states[user_id]
    status_msg = state["message"]
    video_info = state["data"]["video_info"] 
    available_tracks = []
    track_info_list = []

    
    def add_track(lang, data, track_type):
        if lang == "none" or 'url' not in data or not data['url']:
            return
        label_prefix = "[CC]" if track_type == 'caption' else "[SUB]"
        lang_display = locale_map.get(lang, lang)
        label = f"{label_prefix} {lang_display}"
        available_tracks.append(label)
        track_info_list.append({
            'type': track_type,
            'language': lang, 
            'url': data['url'],
            'format': data.get('format', 'vtt'), 
            'display_name': lang_display 
        })

    if 'captions' in video_info and video_info['captions']:
        for lang, data in video_info['captions'].items():
            add_track(lang, data, 'caption')

    if 'subtitles' in video_info and video_info['subtitles']:
        for lang, data in video_info['subtitles'].items():
            add_track(lang, data, 'subtitle')

    if not track_info_list:
        edit_message(status_msg, "No subtitles/captions available. Proceeding to download.")
        state["data"]["selected_subtitles"] = []
        state["data"]["available_subtitle_options"] = []
        state["step"] = "confirm_download" 
        confirm_download(client, user_id)
        return

    state["data"]["selected_subtitles"] = [] 
    state["data"]["available_subtitle_options"] = track_info_list

    buttons = []
    for i, track in enumerate(track_info_list):
        buttons.append([InlineKeyboardButton(available_tracks[i], callback_data=f"sub_{i}")])

    buttons.append([
        InlineKeyboardButton("Done (0 selected)", callback_data="sub_done"),
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ])
    keyboard = InlineKeyboardMarkup(buttons)
    state["step"] = "select_subtitles"
    edit_message(
        status_msg,
        "Select subtitle/caption track(s). Press 'Done' when finished.",
        keyboard=keyboard
    )

def confirm_download(client, user_id):
    """Shows final selections and asks for confirmation."""
    if user_id not in user_states: return
    state = user_states[user_id]
    status_msg = state["message"]

    sel_quality = state["data"]["selected_video_quality"]
    sel_audios = state["data"]["selected_audios"]
    sel_subs = state["data"]["selected_subtitles"] 

    summary = "**Download Summary:**\n\n"
    if state["is_series"]:
        summary += f"**Series:** {state['data']['series_title']}\n"
        summary += f"**Episodes:** {state['data']['episodes_to_download_count']}\n"
    else:
    
        id = state["data"]["id"]
        anime = re.sub(r"\s*\([^)]*\)", "", crunchyroll.get_single_info(id)["data"][0]["episode_metadata"]["season_title"])
        Epno = "S" + str(crunchyroll.get_single_info(id)["data"][0]["episode_metadata"]["season_number"]).zfill(2) + "E" +  str(crunchyroll.get_single_info(id)["data"][0]["episode_metadata"]["episode_number"]).zfill(2)
        Title = crunchyroll.get_single_info(id)["data"][0]["title"]
        summary += f"**Anime:** {anime}\n"
        summary += f"**Episode Title:** {Title}\n"
        summary += f"**Episode No:** {Epno}\n"



    summary += f"**Quality:** {sel_quality['height']}p\n"
    summary += f"**Audio:** {', '.join([a['audio_locale'] for a in sel_audios]) if sel_audios else 'None'}\n"
    subs_text = ', '.join([f"{s['language']} ({s['format']})" for s in sel_subs]) if sel_subs else 'None'
    summary += f"Subtitles: {subs_text}\n"
    buttons = [
        [InlineKeyboardButton("Start Download", callback_data="confirm_start")],
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    state["step"] = "confirm_download"
    edit_message(status_msg, summary, keyboard=keyboard)


@Client.on_callback_query()
async def handle_callback_query(client, query: CallbackQuery):
    global _BOT_LOOP
    _BOT_LOOP = asyncio.get_running_loop()
    await asyncio.to_thread(_handle_callback_query_worker, client, query)

@check_active_download 
def _handle_callback_query_worker(client, query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data

    if user_id not in user_states:
        _run_sync(query.answer("Session expired or invalid state. Please start again with /download.", show_alert=True))
        with suppress(Exception): _run_sync(query.message.delete())
        return

    state = user_states[user_id]
    status_msg = state["message"]
    step = state["step"]

    try:
        if data == "cancel":
            _run_sync(query.answer("Cancelled"))
            edit_message(status_msg, "Download process cancelled.")
            del user_states[user_id]
            if user_id in active_downloads: del active_downloads[user_id]
            return

        if step == "select_quality" and data.startswith("quality_"):
            quality_index = int(data.split("_")[1])
            selected_quality = state["data"]["available_video_qualities"][quality_index]
            state["data"]["selected_video_quality"] = selected_quality
            _run_sync(query.answer(f"Selected: {selected_quality['height']}p"))
            ask_audio_language(client, user_id)

        elif step == "select_audio":
            max_audios = float('inf')

            if data == "audio_done":
                if not state["data"]["selected_audios"]:

                     _run_sync(query.answer("Proceeding without audio selection."))
                else:
                    _run_sync(query.answer("Audio selection complete."))
                ask_subtitles(client, user_id)
                return 

            elif data.startswith("audio_"):
                guid = data.split("_")[1]
                locale_name = locale_map.get(state["data"]["available_audio_options"][guid], state["data"]["available_audio_options"][guid])
                
                already_selected = False
                for i, audio in enumerate(state["data"]["selected_audios"]):
                    if audio['guid'] == guid:
                        state["data"]["selected_audios"].pop(i)
                        already_selected = True
                        _run_sync(query.answer(f"Removed: {locale_name}"))
                        break
                
                if not already_selected:
                    if len(state["data"]["selected_audios"]) < max_audios:
                        state["data"]["selected_audios"].append({'audio_locale': locale_name, 'guid': guid})
                        _run_sync(query.answer(f"Added: {locale_name}"))
                    else:
                        _run_sync(query.answer(f"You can select max {max_audios} audio tracks.", show_alert=True))
                        return 

                current_selection_count = len(state["data"]["selected_audios"])
                buttons = []
                selected_guids = {a['guid'] for a in state["data"]["selected_audios"]}

                for btn_guid, btn_locale in state["data"]["available_audio_options"].items():
                    btn_lang_name = locale_map.get(btn_locale, btn_locale)
                    prefix = "✅ " if btn_guid in selected_guids else ""
                    buttons.append([InlineKeyboardButton(f"{prefix}{btn_lang_name}", callback_data=f"audio_{btn_guid}")])

                buttons.append([
                    InlineKeyboardButton(f"Done ({current_selection_count}/{max_audios if max_audios != float('inf') else '∞'})", callback_data="audio_done"),
                    InlineKeyboardButton("Cancel", callback_data="cancel")
                ])
                query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

        elif step == "select_subtitles":
             
             if data == "sub_done":
                 _run_sync(query.answer("Subtitle selection complete."))
                 confirm_download(client, user_id)
                 return
             elif data.startswith("sub_"):
                 sub_index = int(data.split("_")[1])
                 track_info = state["data"]["available_subtitle_options"][sub_index]
                 
                 already_selected = False
                 for i, sub in enumerate(state["data"]["selected_subtitles"]):
                     if sub['url'] == track_info['url']:
                         state["data"]["selected_subtitles"].pop(i)
                         already_selected = True
                         _run_sync(query.answer(f"Removed: {track_info['display_name']}"))
                         break
                 
                 if not already_selected:
                     state["data"]["selected_subtitles"].append({
                         'language': track_info['display_name'],
                         'url': track_info['url'],
                         'format': track_info['format'],
                         'type': track_info['type'],
                         'original_locale': track_info['language']
                     })
                     _run_sync(query.answer(f"Added: {track_info['display_name']}"))

                 current_selection_count = len(state["data"]["selected_subtitles"])
                 buttons = []
                 selected_urls = {s['url'] for s in state["data"]["selected_subtitles"]}
                 type_labels = {
                  "subtitle": "SUB",
                  "caption": "CC",
                }

                 for i, track in enumerate(state["data"]["available_subtitle_options"]):
                     type_tag = type_labels.get(track['type'], track['type'].upper())
                     label = f"[{type_tag}] {track['display_name']}"
                     prefix = "✅ " if track['url'] in selected_urls else ""
                     buttons.append([InlineKeyboardButton(f"{prefix}{label}", callback_data=f"sub_{i}")])

                 buttons.append([
                     InlineKeyboardButton(f"Done ({current_selection_count} selected)", callback_data="sub_done"),
                     InlineKeyboardButton("Cancel", callback_data="cancel")
                 ])
                 query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))

        elif step == "confirm_download" and data == "confirm_start":
            _run_sync(query.answer("Starting download..."))
            state["step"] = "downloading"
            active_downloads[user_id] = True

            process_download(client, user_id)


    except MessageNotModified:
        _run_sync(query.answer()) 
    except QueryIdInvalid:
         _run_sync(query.answer("Button expired or invalid.", show_alert=True))
    except Exception as e:
        print(f"Error in callback query handler for user {user_id}: {e}")
        traceback.print_exc()
        _run_sync(query.answer("An error occurred.", show_alert=True))
        if user_id in user_states: del user_states[user_id]
        if user_id in active_downloads: del active_downloads[user_id]
        with suppress(Exception): _run_sync(status_msg.edit_text(f"An error occurred: {e}"))



def process_download(client, user_id):
    state = user_states.get(user_id)
    if not state or state["step"] != "downloading":
        print(f"Error: Invalid state for download process (User: {user_id})")
        if user_id in active_downloads: del active_downloads[user_id]
        return

    status_msg = state["message"]
    is_series = state["is_series"]
    final_output_files = [] 
    try:
        if is_series:
            episodes_to_download = state["data"]["episodes"]
            series_anime_title = re.sub(r'[<>:\"\'/\\|?*]', '', state["data"]["series_title"])
            series_output_dir = series_anime_title 
            os.makedirs(series_output_dir, exist_ok=True)
            total_episodes = len(episodes_to_download)

            edit_message(status_msg, f"Starting batch download for '{series_anime_title}' ({total_episodes} episodes)...")

            for i, episode_info in enumerate(episodes_to_download):
                ep_num_str = f"Episode {i+1}/{total_episodes}"
                ep_id = episode_info['guid']
                edit_message(status_msg, f"Processing {ep_num_str}: Fetching info...")

                video_info = crunchyroll.get_video_info(ep_id) 
                if not video_info:
                    edit_message(status_msg, f"{ep_num_str}: Failed to fetch video info. Skipping.")
                    continue

                pssh, mpd_content, token = crunchyroll.get_pssh(video_info)
                if not mpd_content:
                     edit_message(status_msg, f"{ep_num_str}: Failed to fetch MPD. Skipping.")
                     continue

                license_data = license_handler.get_license(pssh, token, ep_id, vid_token)
                license_data = license_data["key"]
                if not license_data:
                     edit_message(status_msg, f"{ep_num_str}: Failed to get video license key. Skipping.")
                     continue
                for key in license_data:
                    video_key_str = "{}:{}".format(key["kid_hex"], key["key_hex"]) 
                
                selected_video_quality = state["data"]["selected_video_quality"]
                episode_video_list, episode_audio_list = parse_mpd_content(mpd_content)
                selected_video_quality = next((video for video in episode_video_list if video['height'] == selected_video_quality['height']), None)
            
                vidseg = get_segment_link_list(mpd_content, selected_video_quality['name'], selected_video_quality['base_url'])
                if not vidseg or "all" not in vidseg:
                    edit_message(status_msg, f"{ep_num_str}: Failed to get video segments. Skipping.")
                    continue

                episode_selected_audios_details = []
                for selected_audio in state["data"]["selected_audios"]: 
                    target_guid = None
                    
                    for version in video_info.get('versions', []):
                        
                        if locale_map.get(version.get('audio_locale'), version.get('audio_locale')) == selected_audio['audio_locale']:
                             target_guid = version.get('guid')
                             break
                    
                    if not target_guid:
                        print(f"Warning: Audio locale {selected_audio['audio_locale']} not found in episode {ep_id}. Skipping audio track.")
                        continue

                    audio_ver_info = crunchyroll.get_video_info(target_guid)
                    if not audio_ver_info: continue 

                    audio_pssh, audio_mpd, audio_token = crunchyroll.get_pssh(audio_ver_info)
                    if not audio_mpd: continue 

                    audio_license_data = license_handler.get_license(audio_pssh, audio_token, target_guid, vid_token)
                    audio_license_data = audio_license_data["key"]
                    for key in audio_license_data:
                        audio_keys = "{}:{}".format(key["kid_hex"], key["key_hex"])
                    if not audio_keys: continue 
                    _, audio_mpd_audio_list = parse_mpd_content(audio_mpd)
                    highest_audio = max(audio_mpd_audio_list, key=lambda x: int(x.get('bandwidth', 0)), default=None)
                    if not highest_audio: continue 

                    audseg = get_segment_link_list(audio_mpd, highest_audio['name'], highest_audio['base_url'])
                    if not audseg or "all" not in audseg: continue 

                    episode_selected_audios_details.append({
                        'audio_locale': selected_audio['audio_locale'], 
                        'key': audio_keys,
                        'segment': audseg
                    })


                episode_selected_subtitles = []
                available_episode_subs = {}
                if 'subtitles' in video_info and video_info['subtitles']:
                    for lang, data in video_info['subtitles'].items():
                        if lang != 'none' and 'url' in data:
                             available_episode_subs[lang] = data
                if 'captions' in video_info and video_info['captions']:
                     for lang, data in video_info['captions'].items():
                          if 'url' in data:
                              available_episode_subs[f"{lang}_caption"] = data 
                
                for sel_sub in state["data"]["selected_subtitles"]: 
                     found_sub_data = None
                     original_locale = sel_sub.get('original_locale')
                     sub_type = sel_sub.get('type')

                     key_to_find = f"{original_locale}_caption" if sub_type == 'caption' else original_locale

                     if key_to_find in available_episode_subs:
                          found_sub_data = available_episode_subs[key_to_find]
                     elif original_locale in available_episode_subs:
                         found_sub_data = available_episode_subs[original_locale]

                     if found_sub_data and found_sub_data.get('url'):
                         episode_selected_subtitles.append({
                            'language': sel_sub['language'], 
                            'url': found_sub_data['url'],
                            'format': found_sub_data.get('format', 'vtt'),
                            'type': sel_sub['type'] 
                         })
                     else:
                          print(f"Warning: Subtitle {sel_sub['language']} not found in episode {ep_id}. Skipping subtitle track.")

            
                ep_meta = crunchyroll.get_single_info(ep_id)["data"][0]["episode_metadata"]
                season_num = str(ep_meta["season_number"]).zfill(2)
                ep_num = str(ep_meta.get("episode_number", i + 1)).zfill(2) 
                ep_title_str = crunchyroll.get_single_info(ep_id)["data"][0]["title"]

                base_anime_title = series_anime_title 
                if not base_anime_title and ep_meta.get("season_title"):
                     base_anime_title = re.sub(r"\s*\([^)]*\)", "", ep_meta["season_title"])

                title = f"{base_anime_title}.S{season_num}E{ep_num}-{ep_title_str}"
                if use_custom_title:
                    try:
                        title = custom_title.format(
                            Title=base_anime_title,
                            Episode=ep_num,
                            Season=season_num,
                            EpTitle=ep_title_str
                        )
                    except AttributeError:
                        print("Warning: `custom_title` object does not have `.format` method or config is missing.")
                    except KeyError as ke:
                        print(f"Warning: Missing key in custom_title format: {ke}")

                title = re.sub(r'[<>:\"\'/\\|?*]', '', title)

                temp_files = []
                final_ep_output = download_decrypt_merge_single(
                    client, user_id, status_msg,
                    title, vidseg, video_key_str,
                    episode_selected_audios_details, 
                    episode_selected_subtitles,      
                    selected_video_quality,          
                    temp_files,                      
                    output_directory=series_output_dir, 
                    progress_prefix=f"{ep_num_str}: "
                )

                if final_ep_output:
                    final_output_files.append(final_ep_output)
                    edit_message(status_msg, f"{ep_num_str}📤 **Uploading...**")
                    uploader = tgUploader(app, status_msg)
                    uploader.upload_file(final_ep_output)
                else:
                    edit_message(status_msg, f"{ep_num_str}: Failed to process episode.")

                edit_message(status_msg, f"{ep_num_str}: Cleaning up temporary files...")
                cleanup_files(temp_files)

            if final_output_files:
                 edit_message(status_msg, f"Batch download complete for '{series_anime_title}'.\nDownloaded {len(final_output_files)} episodes.")
            else:
                 edit_message(status_msg, f"Batch download finished for '{series_anime_title}', but no episodes were successfully processed.")


        else:
            video_info = state["data"]["video_info"]
            content_id = state["data"]["id"]
            pssh = state["data"]["pssh"]
            mpd_content = state["data"]["mpd_content"]
            token = state["data"]["drm_token"]
            selected_video_quality = state["data"]["selected_video_quality"]
            selected_audios = state["data"]["selected_audios"]
            selected_subtitles = state["data"]["selected_subtitles"]

            edit_message(status_msg, "Starting download...")

            license_key = license_handler.get_license(pssh, token, content_id, vid_token)
            license_key = license_key["key"]
            for i in license_key:
                video_key_str = "{}:{}".format(i["kid_hex"], i["key_hex"])  
            if not video_key_str:
                raise Exception("Failed to get video license key.")

            vidseg = get_segment_link_list(mpd_content, selected_video_quality['name'], selected_video_quality['base_url'])
            if not vidseg or "all" not in vidseg:
                 raise Exception("Failed to get video segments.")

            detailed_audios = []
            edit_message(status_msg, "Fetching audio details...")
            for audio in selected_audios:
                guid = audio['guid']
                locale_name = audio['audio_locale']
                info = crunchyroll.get_video_info(guid)
                if not info: continue 

                audio_pssh, audio_mpd, audio_token = crunchyroll.get_pssh(info)
                if not audio_mpd: continue

                audio_license_data = license_handler.get_license(audio_pssh, audio_token, guid, vid_token)
                audio_license_data = audio_license_data["key"]
                for i in audio_license_data:
                    audio_keys = "{}:{}".format(i["kid_hex"], i["key_hex"])

                _, audio_mpd_audio_list = parse_mpd_content (audio_mpd)
                highest_audio = max(audio_mpd_audio_list, key=lambda x: int(x.get('bandwidth', 0)), default=None)
                if not highest_audio: continue

                audseg = get_segment_link_list(audio_mpd, highest_audio['name'], highest_audio['base_url'])
                if not audseg or "all" not in audseg: continue

                detailed_audios.append({
                    'audio_locale': locale_name,
                    'key': audio_keys,
                    'segment': audseg
                })

            id = state["data"]["id"]
            ep_meta = crunchyroll.get_single_info(id)["data"][0]["episode_metadata"]
            anime = re.sub(r"\s*\([^)]*\)", "", ep_meta["season_title"])
            season_num = str(ep_meta["season_number"]).zfill(2)
            ep_num = str(ep_meta.get("episode_number", 0)).zfill(2)
            ep_title_str = crunchyroll.get_single_info(id)["data"][0]["title"]

            title = f"{anime}.S{season_num}E{ep_num}-{ep_title_str}"
            if use_custom_title:
                 try:
                     title = custom_title.format(Title=anime, Episode=ep_num, Season=season_num, EpTitle=ep_title_str)
                 except AttributeError: pass 
                 except KeyError: pass
            title = re.sub(r'[<>:\"\'/\\|?*]', '', title)

            temp_files = []
            final_output_path = download_decrypt_merge_single(
                client, user_id, status_msg,
                title, vidseg, video_key_str,
                detailed_audios, selected_subtitles,
                selected_video_quality, temp_files
            )

            if final_output_path:
                edit_message(status_msg, f"📤 **Uploading...**")
                uploader = tgUploader(app, status_msg)
                uploader.upload_file(final_output_path)
                edit_message(status_msg, f"Upload complete: `{os.path.basename(final_output_path)}`")
            else:
                edit_message(status_msg, "Download failed during processing.")

            print("Cleaning up temporary files...")
            cleanup_files(temp_files)


    except Exception as e:
        print(f"Error during download process for user {user_id}: {e}")
        traceback.print_exc()
        edit_message(status_msg, f"An error occurred during download: {e}")
        cleanup_files(temp_files if 'temp_files' in locals() else [])


    finally:
        if user_id in user_states: del user_states[user_id]
        if user_id in active_downloads: del active_downloads[user_id]


def download_decrypt_merge_single(
    client, user_id, status_msg, title, vidseg, video_key_str,
    detailed_audios, selected_subtitles, video_quality_info, temp_files,
    output_directory="", progress_prefix=""
    ):
    
    base_filename = os.path.join(output_directory, title)
    enc_video_path = f"enc_{title}.mp4"
    dec_video_path = f"{base_filename}.mp4"
    audio_files = [] 
    subtitle_files = []

    os.makedirs("Downloads", exist_ok=True) 
    temp_files.extend([f"Downloads/{enc_video_path}", dec_video_path]) 
    try:
        edit_message(status_msg, f"{progress_prefix}Downloading video... \n\n `{dec_video_path}` \n\n P.S: It takes few miniuits to download depending on the quality of the video selected.")
        download_segment(vidseg["all"], os.path.splitext(enc_video_path)[0], "mp4")
        edit_message(status_msg, f"{progress_prefix}Video download complete.")

        for i, audio in enumerate(detailed_audios):
            locale = audio['audio_locale']
            edit_message(status_msg, f"{progress_prefix}Downloading audio {i+1}/{len(detailed_audios)} ({locale})... \n\n `{title}_{locale}.m4a` \n\n P.S: It takes few miniuits to download depending on the number of audio selected.")
            enc_audio_base = f"enc_{title}_{locale}"
            enc_audio_path = f"{enc_audio_base}.m4a"
            download_segment(audio['segment']["all"], enc_audio_base, "m4a")
            temp_files.append(enc_audio_path) 
            edit_message(status_msg, f"{progress_prefix}Audio '{locale}' download complete.")


        if selected_subtitles:
            edit_message(status_msg, f"{progress_prefix}Downloading subtitles...")
            for i, sub in enumerate(selected_subtitles):
                sub_lang = sub['language']
                sub_format = sub['format']
                sub_url = sub['url']
                sub_temp_path = f"{base_filename}_{sub_lang}.{sub_format}"
                sub_final_path = f"{base_filename}_{sub_lang}.srt" 

                
                curl_cmd = f"curl -fsSL -o {shlex.quote(sub_temp_path)} {shlex.quote(sub_url)}"
                _, stderr, retcode = run_shell_command(curl_cmd)
                if retcode != 0:
                    print(f"Subtitle download failed for {sub_lang}: {stderr}")
                    continue

                temp_files.append(sub_temp_path)

                if sub_format.lower() == 'vtt':
                     try:
                         convert_vtt_to_srt_custom(sub_temp_path, sub_final_path)
                         subtitle_files.append({'path': sub_final_path, 'lang': sub['language'], 'title_lang': sub_lang, 'type': '.srt'})
                         temp_files.append(sub_final_path) 
                     except Exception as conv_e:
                         print(f"VTT to SRT conversion failed for {sub_lang}: {conv_e}")
                else:
                    
                    subtitle_files.append({'path': sub_temp_path, 'lang': sub['language'], 'title_lang': sub_lang, 'type': sub['type']})

            edit_message(status_msg, f"{progress_prefix}Subtitle downloads complete.")


        edit_message(status_msg, f"{progress_prefix}Decrypting video...")
        _mp4decrypt_bin = find_mp4decrypt()
        if not _mp4decrypt_bin:
            raise Exception("mp4decrypt binary not found (expected at Akbots/bin/mp4decrypt).")
        decrypt_cmd = f"{shlex.quote(_mp4decrypt_bin)} {shlex.quote(f'Downloads/enc_{title}.mp4')} {shlex.quote(f'{base_filename}.mp4')} --show-progress --key {video_key_str}"
        _, stderr, retcode = run_shell_command(decrypt_cmd)
        if retcode != 0:
            raise Exception(f"Video decryption failed: {stderr}")
        edit_message(status_msg, f"{progress_prefix}Video decryption complete.")

        for i, audio in enumerate(detailed_audios):

            locale = audio['audio_locale']
            edit_message(status_msg, f"{progress_prefix}Decrypting audio {i+1}/{len(detailed_audios)} ({locale})...")
            enc_audio_path = f"Downloads/enc_{title}_{locale}.m4a"
            dec_audio_path = f"{base_filename}_{locale}.m4a"
            temp_files.append(enc_audio_path)
            decrypt_cmd = f"{shlex.quote(_mp4decrypt_bin)} {shlex.quote(enc_audio_path)} {shlex.quote(dec_audio_path)} --show-progress --key {audio['key']}"
            _, stderr, retcode = run_shell_command(decrypt_cmd)
            if retcode != 0:
                print(f"Warning: Audio decryption failed for {locale}: {stderr}. Skipping audio track.")
                continue

            audio_files.append({'path': dec_audio_path, 'lang': locale_map.get(locale), 'title_lang': locale}) 
            temp_files.append(dec_audio_path) 
            edit_message(status_msg, f"{progress_prefix}Audio '{locale}' decryption complete.")

        edit_message(status_msg, f"{progress_prefix}Merging files...")

        ffmpeg_cmd_list = [
            ffmpeg_path if ffmpeg_path else "ffmpeg", 
            "-i", dec_video_path
        ]

        for audio in audio_files:
            ffmpeg_cmd_list.extend(["-i", audio['path']])

        for sub in subtitle_files:
            ffmpeg_cmd_list.extend(["-i", sub['path']])

        map_commands = []
        metadata_commands = []
        Watermark_Name = globals().get("Watermark_Name", "")
        if use_watermark:
            filter_complex = get_filter_complex()
            ffmpeg_cmd_list.extend(["-filter_complex", filter_complex])
            map_commands.extend(["-map", "[v]"]) 
        else:
            map_commands.extend(["-map", "0:v?"]) 

        output_audio_langs = []
        for i, audio in enumerate(audio_files):
            stream_index = i + 1 
            map_commands.extend(["-map", f"{stream_index}:a?"])
            lang_code = LANGUAGE_NAME_TO_ISO639_2B.get(audio['title_lang'], audio['title_lang']) if audio['title_lang'] else "und" 
            title_lang = audio['title_lang']
            output_audio_langs.append(title_lang)
            if use_watermark:
                 metadata_commands.extend([
                 f"-metadata:s:a:{i}", f"language={lang_code}",
                 f"-metadata:s:a:{i}", f'title={Watermark_Name} - [{title_lang}]'
                  ])
            else:
                 metadata_commands.extend([
                 f"-metadata:s:a:{i}", f"language={lang_code}",
                 f"-metadata:s:a:{i}", f'title=[{title_lang}]'
                  ])
                
        output_sub_langs = []
        for i, sub in enumerate(subtitle_files):
            stream_index = len(audio_files) + i + 1 
            map_commands.extend(["-map", f"{stream_index}:s?"])
            lang_code = LANGUAGE_NAME_TO_ISO639_2B.get(sub['title_lang'], sub['title_lang']) if sub['title_lang'] else "und" 
            title_lang = sub['title_lang']
            sub_type = sub['type']
            output_sub_langs.append(f"{title_lang} ({os.path.splitext(sub['path'])[1][1:]})")
            if use_watermark:
                  metadata_commands.extend([
                     f"-metadata:s:s:{i}", f"language={lang_code}",
                     f"-metadata:s:s:{i}", f'title={Watermark_Name} - [{title_lang}] [{sub_type}]'
                  ])
            else:
                metadata_commands.extend([
                     f"-metadata:s:s:{i}", f"language={lang_code}",
                     f"-metadata:s:s:{i}", f'title=[{title_lang}] [{sub_type}]'
                  ])

        quality_str = f"{video_quality_info['height']}p"
        audio_str = "+".join(output_audio_langs) if output_audio_langs else "NoAudio"
        sub_str = "+".join(output_sub_langs) if output_sub_langs else "NoSubs"
        watermark_suffix = f".{Watermark_Name}" if use_watermark else ""
        
        
        output_filename = f"{base_filename}.{quality_str}.[{audio_str}].[{sub_str}]{watermark_suffix}.{output_format}"
        temp_files.append(output_filename)


        
        ffmpeg_cmd_list.extend(map_commands)
        ffmpeg_cmd_list.extend(metadata_commands)
        ffmpeg_cmd_list.extend([
            "-c:v", encoding_code, 
            "-c:a", audio_codec,   
            "-c:s", "copy",        
            output_filename
        ])
        
        edit_message(status_msg, f"{progress_prefix}Merging files... \n\n `{output_filename}` \n\n P.S: It takes few minutes to merge depending on the number of audio and subtitle selected.")

        full_ffmpeg_command = shlex.join(ffmpeg_cmd_list) 

        _, stderr, retcode = run_shell_command(full_ffmpeg_command)
        if retcode != 0:
             
            error_log_path = f"{base_filename}_ffmpeg_error.log"
            with open(error_log_path, "w") as f:
                 f.write(f"Command: {full_ffmpeg_command}\n\n")
                 f.write(stderr)
            raise Exception(f"FFmpeg merging failed. See {error_log_path} for details.")

        edit_message(status_msg, f"{progress_prefix}Merging complete.")
        return output_filename 

    except Exception as e:
        print(f"Error in download_decrypt_merge_single for {title}: {e}")
        traceback.print_exc()
        edit_message(status_msg, f"{progress_prefix}Error during processing: {e}")
        return None 


def cleanup_files(file_paths):
    
    for file_path in file_paths:
        if file_path and os.path.exists(file_path): 
             try:
                 if os.path.isdir(file_path):
                      pass
                 else:
                    os.remove(file_path)
                    if debug:
                         print(f"Deleted file: {file_path}")
             except OSError as e:
                 print(f"Error deleting file {file_path}: {e}")
    downloads_dir = "Downloads"
    if os.path.exists(downloads_dir) and not os.listdir(downloads_dir):
         try:
             os.rmdir(downloads_dir)
             if debug:
                print(f"Removed empty Downloads directory: {downloads_dir}")
         except OSError as e:
             print(f"Could not remove empty Downloads directory: {e}")
