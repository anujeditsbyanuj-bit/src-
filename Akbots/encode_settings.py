# Akbots - Don't Remove Credit - @AkBots_Official
#
# /encode Settings System — the full persistent settings menu from
# ENCODING-BOT-master's utils/settings.py + utils/database/database.py,
# ported into Akbots' own button style (make_button/ButtonStyle from
# Akbots/settings.py) and its own DB (database/db.py: get_encode_settings/
# set_encode_setting/reset_encode_settings). Covers every knob the source
# project exposed: resolution, codec (H.264/H.265), CRF, preset, tune,
# CABAC, 10-bit, aspect ratio, reframe (-refs), FPS, container (MP4/MKV/
# AVI), hardsub, softsub, watermark-burn (reuses /set_watermark's saved
# text+position), metadata tag, and audio codec/bitrate/samplerate/
# channels. /encode reads all of this via build_encode_args() below.

from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import db

from Akbots.settings import make_button, get_back_close_buttons, BUTTON_STYLE_SUPPORTED

try:
    from pyrogram.enums import ButtonStyle
except ImportError:
    ButtonStyle = None

E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'
E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_INFO  = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_TIP   = '<emoji id=5422439311196834318>💡</emoji>'
ON, OFF = "✅", "◻️"

# =====================================================================
# Option tables: (stored_value, display_label)
# =====================================================================
RESOLUTIONS = [("OG", "Source"), ("2160", "4K"), ("1440", "2K"), ("1080", "1080p"),
               ("720", "720p"), ("576", "576p"), ("480", "480p"), ("360", "360p"), ("240", "240p")]
CODECS      = [("h264", "H.264"), ("h265", "H.265/HEVC")]
PRESETS     = [("ultrafast", "Ultrafast"), ("superfast", "Superfast"), ("veryfast", "Veryfast"),
               ("fast", "Fast"), ("medium", "Medium"), ("slow", "Slow"),
               ("slower", "Slower"), ("veryslow", "Veryslow")]
TUNES       = [("film", "Film"), ("animation", "Animation")]
REFRAMES    = [("pass", "Off"), ("4", "4"), ("8", "8"), ("16", "16")]
FPSES       = [("source", "Source"), ("ntsc", "NTSC"), ("pal", "PAL"),
               ("film", "Film"), ("23.976", "23.976"), ("30", "30"), ("60", "60")]
EXTENSIONS  = [("MP4", "MP4"), ("MKV", "MKV"), ("AVI", "AVI")]
A_CODECS    = [("source", "Source/Copy"), ("aac", "AAC"), ("ac3", "AC3 (DD)"),
               ("opus", "OPUS"), ("vorbis", "VORBIS"), ("alac", "ALAC")]
A_BITRATES  = [("source", "Source"), ("128", "128K"), ("160", "160K"), ("192", "192K"),
               ("224", "224K"), ("256", "256K"), ("320", "320K"), ("400", "400K")]
A_SAMPLES   = [("source", "Source"), ("44.1K", "44.1kHz"), ("48K", "48kHz")]
A_CHANNELS  = [("source", "Source"), ("1.0", "Mono"), ("2.0", "Stereo"),
               ("2.1", "2.1"), ("5.1", "5.1"), ("7.1", "7.1")]

CRF_CHOICES = [16, 18, 20, 23, 26, 28, 30, 33]


def _btn(text, cb, style=None):
    return make_button(text, callback_data=cb,
                        style=(style if BUTTON_STYLE_SUPPORTED else None))


def _grid(pairs, cb_prefix, current, cols=3):
    """pairs: list of (value, label). Marks the active one with ✅."""
    rows, row = [], []
    for value, label in pairs:
        mark = f"{ON} " if str(value) == str(current) else ""
        row.append(_btn(f"{mark}{label}", f"{cb_prefix}:{value}"))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _toggle_row(label, cb, is_on):
    style = (ButtonStyle.PRIMARY if (BUTTON_STYLE_SUPPORTED and is_on) else None)
    return [_btn(f"{ON if is_on else OFF} {label}", cb, style)]


async def _menu_root(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    text = (
        f"<blockquote>{E_GEAR} <b>Encoding Settings</b>\n\n"
        f"{E_INFO} These apply automatically the next time you run <code>/encode</code> "
        f"(resolution/codec/quality picked in the wizard still override the video basics; "
        f"everything else here fills in the rest).\n\n"
        f"{E_TIP} Tap a category to configure it.</blockquote>"
    )
    rows = [
        [_btn(" 🎞 Video ", "encset:video"), _btn(" 📜 Subtitles ", "encset:subs")],
        [_btn(" 💧 Watermark ", "encset:wm"), _btn(" 🔊 Audio ", "encset:audio")],
        [_btn(" 📦 Container ", "encset:ext"), _btn(" 📤 Upload & Delivery ", "encset:delivery")],
        [_btn(" 📋 View All ", "encset:view")],
        [_btn(" ♻️ Reset Defaults ", "encset:reset",
              ButtonStyle.DANGER if BUTTON_STYLE_SUPPORTED else None)],
    ]
    rows += get_back_close_buttons()
    return text, InlineKeyboardMarkup(rows)


async def _menu_video(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    text = f"<blockquote>{E_GEAR} <b>Video Settings</b></blockquote>"
    rows = []
    rows.append([_btn(" — Resolution — ", "noop")])
    rows += _grid(RESOLUTIONS, "encv:res", s["enc_resolution"])
    rows.append([_btn(" — Codec — ", "noop")])
    rows += _grid(CODECS, "encv:codec", s["enc_codec"], cols=2)
    rows.append([_btn(f" — CRF: {s['enc_crf']} (lower = better quality) — ", "noop")])
    rows += _grid([(c, str(c)) for c in CRF_CHOICES], "encv:crf", s["enc_crf"], cols=4)
    rows.append([_btn(" — Preset — ", "noop")])
    rows += _grid(PRESETS, "encv:preset", s["enc_preset"], cols=4)
    rows.append([_btn(" — Tune — ", "noop")])
    rows += _grid(TUNES, "encv:tune", s["enc_tune"], cols=2)
    rows.append([_btn(" — Reframe (-refs) — ", "noop")])
    rows += _grid(REFRAMES, "encv:reframe", s["enc_reframe"], cols=4)
    rows.append([_btn(" — FPS — ", "noop")])
    rows += _grid(FPSES, "encv:fps", s["enc_fps"], cols=4)
    rows.append(_toggle_row("10-bit color", "encv:bits10", s["enc_bits10"]))
    rows.append(_toggle_row("Force 16:9 aspect", "encv:aspect169", s["enc_aspect169"]))
    rows.append(_toggle_row("CABAC entropy coding", "encv:cabac", s["enc_cabac"]))
    rows.append([_btn(" ⬅️ Back ", "encset:root"), _btn(" ❌ Close ", "close_btn")])
    return text, InlineKeyboardMarkup(rows)


async def _menu_subs(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    text = (
        f"<blockquote>{E_GEAR} <b>Subtitle Settings</b>\n\n"
        f"{E_TIP} <b>Hardsub</b> burns the first embedded subtitle track into the picture "
        f"(always visible, can't be turned off by the viewer). <b>Softsub</b> keeps subtitle "
        f"tracks as selectable streams (MP4/MKV only, ignored for AVI).</blockquote>"
    )
    rows = [
        _toggle_row("Hardsub (burn-in)", "encv:hardsub", s["enc_hardsub"]),
        _toggle_row("Softsub (keep as track)", "encv:softsub", s["enc_softsub"]),
        [_btn(" ⬅️ Back ", "encset:root"), _btn(" ❌ Close ", "close_btn")],
    ]
    return text, InlineKeyboardMarkup(rows)


async def _menu_watermark(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    wm_text = await db.get_watermark(user_id)
    wm_pos = await db.get_watermark_position(user_id)
    text = (
        f"<blockquote>{E_GEAR} <b>Watermark Settings</b>\n\n"
        f"{E_INFO} <b>Saved text:</b> <code>{wm_text or 'none set'}</code>\n"
        f"{E_INFO} <b>Position:</b> <code>{wm_pos}</code>\n\n"
        f"{E_TIP} Set/change the text with <code>/set_watermark Your Text</code> and the "
        f"position with <code>/watermark_position bottom-right</code>. Turn the toggle "
        f"below on to have <code>/encode</code> automatically burn it into every encode."
        f"</blockquote>"
    )
    rows = [
        _toggle_row("Burn watermark into encodes", "encv:watermark", s["enc_watermark"]),
        _toggle_row("Tag output metadata (Akbots)", "encv:metadata_tag", s["enc_metadata_tag"]),
        [_btn(" ⬅️ Back ", "encset:root"), _btn(" ❌ Close ", "close_btn")],
    ]
    return text, InlineKeyboardMarkup(rows)


async def _menu_audio(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    text = f"<blockquote>{E_GEAR} <b>Audio Settings</b></blockquote>"
    rows = []
    rows.append([_btn(" — Codec — ", "noop")])
    rows += _grid(A_CODECS, "encv:acodec", s["enc_audio_codec"])
    rows.append([_btn(" — Bitrate — ", "noop")])
    rows += _grid(A_BITRATES, "encv:abitrate", s["enc_audio_bitrate"], cols=4)
    rows.append([_btn(" — Sample Rate — ", "noop")])
    rows += _grid(A_SAMPLES, "encv:asample", s["enc_audio_samplerate"])
    rows.append([_btn(" — Channels — ", "noop")])
    rows += _grid(A_CHANNELS, "encv:achannels", s["enc_audio_channels"])
    rows.append([_btn(" ⬅️ Back ", "encset:root"), _btn(" ❌ Close ", "close_btn")])
    return text, InlineKeyboardMarkup(rows)


async def _menu_ext(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    text = f"<blockquote>{E_GEAR} <b>Output Container</b></blockquote>"
    rows = _grid(EXTENSIONS, "encv:ext", s["enc_extension"], cols=3)
    rows.append([_btn(" ⬅️ Back ", "encset:root"), _btn(" ❌ Close ", "close_btn")])
    return text, InlineKeyboardMarkup(rows)


async def _menu_delivery(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    upload_mode = await db.get_upload_mode(user_id)
    thumb_set = bool(await db.get_thumbnail(user_id))
    text = (
        f"<blockquote>{E_GEAR} <b>Upload & Delivery</b>\n\n"
        f"{E_INFO} <b>Upload As</b> and <b>Custom Thumbnail</b> are your account-wide settings "
        f"(same ones every other download/upload in this bot uses) — shown here for convenience, "
        f"the button changes them for everything, not just <code>/encode</code>.\n\n"
        f"{E_INFO} <b>Notifications</b> off sends the finished file silently (no push ping on the "
        f"recipient's end). <b>Auto-Delete Original</b> removes the source message you replied to "
        f"once the encode uploads successfully.</blockquote>"
    )
    rows = [
        [_btn(" — Upload As — ", "noop")],
        [
            _btn(f"{ON if upload_mode == 'auto' else ''} 🎬 Video/Auto", "encv:uploadas:auto"),
            _btn(f"{ON if upload_mode == 'document' else ''} 📄 Document", "encv:uploadas:document"),
        ],
        [_btn(
            f" 🖼 Custom Thumbnail: {'Set ✔️' if thumb_set else 'Not Set ❌'} "
            f"(use /set_thumb) ", "noop"
        )],
        _toggle_row("Notifications", "encv:notify", s["enc_notify"]),
        _toggle_row("Auto-Delete Original", "encv:autodel", s["enc_autodel"]),
        [_btn(" ⬅️ Back ", "encset:root"), _btn(" ❌ Close ", "close_btn")],
    ]
    return text, InlineKeyboardMarkup(rows)


async def _menu_view(user_id) -> tuple[str, InlineKeyboardMarkup]:
    s = await db.get_encode_settings(user_id)
    wm_text = await db.get_watermark(user_id)
    upload_mode = await db.get_upload_mode(user_id)
    thumb_set = bool(await db.get_thumbnail(user_id))
    text = (
        f"<blockquote>{E_GEAR} <b>Current Encode Settings</b>\n\n"
        f"<b>🎞 Video</b>\n"
        f"Resolution: <code>{s['enc_resolution']}</code> | Codec: <code>{s['enc_codec']}</code> | "
        f"CRF: <code>{s['enc_crf']}</code>\n"
        f"Preset: <code>{s['enc_preset']}</code> | Tune: <code>{s['enc_tune']}</code>\n"
        f"10-bit: <code>{s['enc_bits10']}</code> | Aspect 16:9: <code>{s['enc_aspect169']}</code> | "
        f"CABAC: <code>{s['enc_cabac']}</code>\n"
        f"Reframe: <code>{s['enc_reframe']}</code> | FPS: <code>{s['enc_fps']}</code>\n\n"
        f"<b>📜 Subtitles</b>\n"
        f"Hardsub: <code>{s['enc_hardsub']}</code> | Softsub: <code>{s['enc_softsub']}</code>\n\n"
        f"<b>💧 Watermark</b>\n"
        f"Burn-in: <code>{s['enc_watermark']}</code> (text: <code>{wm_text or 'none'}</code>) | "
        f"Metadata tag: <code>{s['enc_metadata_tag']}</code>\n\n"
        f"<b>🔊 Audio</b>\n"
        f"Codec: <code>{s['enc_audio_codec']}</code> | Bitrate: <code>{s['enc_audio_bitrate']}</code>\n"
        f"Sample rate: <code>{s['enc_audio_samplerate']}</code> | Channels: <code>{s['enc_audio_channels']}</code>\n\n"
        f"<b>📦 Container:</b> <code>{s['enc_extension']}</code>\n\n"
        f"<b>📤 Upload & Delivery</b>\n"
        f"Upload As: <code>{'Document' if upload_mode == 'document' else 'Video/Auto'}</code> | "
        f"Custom Thumbnail: <code>{'Set' if thumb_set else 'Not Set'}</code>\n"
        f"Notifications: <code>{s['enc_notify']}</code> | "
        f"Auto-Delete Original: <code>{s['enc_autodel']}</code></blockquote>"
    )
    rows = [[_btn(" ⬅️ Back ", "encset:root"), _btn(" ❌ Close ", "close_btn")]]
    return text, InlineKeyboardMarkup(rows)


_MENUS = {
    "root": _menu_root, "video": _menu_video, "subs": _menu_subs,
    "wm": _menu_watermark, "audio": _menu_audio, "ext": _menu_ext,
    "delivery": _menu_delivery, "view": _menu_view,
}


@Client.on_message(filters.command("encode_settings") & filters.private)
async def encode_settings_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    text, markup = await _menu_root(user_id)
    await message.reply_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^encset:(root|video|subs|wm|audio|ext|delivery|view|reset)$"))
async def encode_settings_nav(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    section = callback_query.matches[0].group(1)

    if section == "reset":
        await db.reset_encode_settings(user_id)
        await callback_query.answer("Encoding settings reset to defaults ✅", show_alert=False)
        text, markup = await _menu_root(user_id)
    else:
        await callback_query.answer()
        text, markup = await _MENUS[section](user_id)

    await callback_query.edit_message_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(
    r"^encv:(res|codec|crf|preset|tune|reframe|fps|bits10|aspect169|cabac|"
    r"hardsub|softsub|watermark|metadata_tag|acodec|abitrate|asample|achannels|ext|"
    r"notify|autodel|uploadas):(.+)$"
))
async def encode_settings_set(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    field, raw_value = callback_query.matches[0].group(1), callback_query.matches[0].group(2)

    # "Upload As" writes to the account-wide upload_mode (database/db.py:
    # set_upload_mode/get_upload_mode) instead of an enc_ prefixed field —
    # it's the same toggle every other plugin's upload_file() call reads,
    # deliberately not duplicated as a separate encode-only setting.
    if field == "uploadas":
        await db.set_upload_mode(user_id, raw_value)
        await callback_query.answer("Updated ✅", show_alert=False)
        text, markup = await _menu_delivery(user_id)
        return await callback_query.edit_message_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)

    key_map = {
        "res": "enc_resolution", "codec": "enc_codec", "crf": "enc_crf",
        "preset": "enc_preset", "tune": "enc_tune", "reframe": "enc_reframe", "fps": "enc_fps",
        "bits10": "enc_bits10", "aspect169": "enc_aspect169", "cabac": "enc_cabac",
        "hardsub": "enc_hardsub", "softsub": "enc_softsub",
        "watermark": "enc_watermark", "metadata_tag": "enc_metadata_tag",
        "acodec": "enc_audio_codec", "abitrate": "enc_audio_bitrate",
        "asample": "enc_audio_samplerate", "achannels": "enc_audio_channels",
        "ext": "enc_extension", "notify": "enc_notify", "autodel": "enc_autodel",
    }
    bool_fields = {"bits10", "aspect169", "cabac", "hardsub", "softsub", "watermark",
                   "metadata_tag", "notify", "autodel"}
    db_key = key_map[field]

    if field in bool_fields:
        s = await db.get_encode_settings(user_id)
        value = not s[db_key]
    elif field == "crf":
        value = int(raw_value)
    else:
        value = raw_value

    await db.set_encode_setting(user_id, db_key, value)
    await callback_query.answer("Updated ✅", show_alert=False)

    # Re-render whichever sub-menu this button lives on.
    section_for_field = {
        "res": "video", "codec": "video", "crf": "video", "preset": "video", "tune": "video",
        "reframe": "video", "fps": "video", "bits10": "video", "aspect169": "video", "cabac": "video",
        "hardsub": "subs", "softsub": "subs",
        "watermark": "wm", "metadata_tag": "wm",
        "acodec": "audio", "abitrate": "audio", "asample": "audio", "achannels": "audio",
        "ext": "ext", "notify": "delivery", "autodel": "delivery",
    }
    section = section_for_field[field]
    text, markup = await _MENUS[section](user_id)
    await callback_query.edit_message_text(text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^noop$"))
async def encode_settings_noop(client: Client, callback_query: CallbackQuery):
    await callback_query.answer()


# ------------------------------------------------------------------
# Shared helper — /encode (and any other plugin) calls this to turn a
# user's saved settings into extra ffmpeg args + the output extension.
# Kept here so the settings schema and the flags that read it never drift
# apart. Mirrors ENCODING-BOT-master's utils/encoding.py `encode()`.
# ------------------------------------------------------------------
async def build_encode_args(user_id: int, in_path: str, has_subs: bool = False,
                             has_audio: bool = True) -> dict:
    """Returns {"pre_vf": [...], "video": [...], "audio": [...], "subs": [...],
    "metadata": [...], "extension": ".mkv"} built purely from the user's
    saved /encode_settings — resolution/codec/CRF from the /encode wizard
    are applied separately by the caller and take priority over
    enc_resolution/enc_codec/enc_crf here."""
    s = await db.get_encode_settings(user_id)

    ext_map = {"MP4": ".mp4", "MKV": ".mkv", "AVI": ".avi"}
    extension = ext_map.get(s["enc_extension"], ".mkv")

    video_extra = []
    if s["enc_preset"]:
        video_extra += ["-preset", s["enc_preset"]]
    video_extra += ["-tune", s["enc_tune"]]
    if s["enc_codec"] == "h264":
        video_extra += ["-coder", "1" if s["enc_cabac"] else "0"]
        if s["enc_reframe"] != "pass":
            video_extra += ["-refs", s["enc_reframe"]]
    video_extra += ["-pix_fmt", "yuv420p10le" if s["enc_bits10"] else "yuv420p"]
    if s["enc_aspect169"]:
        video_extra += ["-aspect", "16:9"]

    fps_map = {"ntsc": "ntsc", "pal": "pal", "film": "film", "23.976": "24000/1001",
               "30": "30", "60": "60"}
    if s["enc_fps"] in fps_map:
        video_extra += ["-r", fps_map[s["enc_fps"]]]

    vf_parts = []
    if s["enc_watermark"]:
        wm_text = await db.get_watermark(user_id)
        if wm_text:
            from Akbots.watermark import _escape_drawtext, POSITIONS
            wm_pos = await db.get_watermark_position(user_id)
            pos_expr = POSITIONS.get(wm_pos, POSITIONS["bottom-right"])
            escaped = _escape_drawtext(wm_text)
            vf_parts.append(
                f"drawtext=text='{escaped}':fontcolor=white:fontsize=28:"
                f"box=1:boxcolor=black@0.5:boxborderw=6:{pos_expr}"
            )
    if s["enc_hardsub"] and has_subs:
        # Burns the first embedded subtitle stream straight from the same
        # input file — no separate extraction step needed.
        safe_path = in_path.replace("\\", "/").replace(":", "\\:").replace("'", "\u2019")
        vf_parts.append(f"subtitles=filename='{safe_path}':si=0")

    metadata_extra = []
    if s["enc_metadata_tag"]:
        metadata_extra = [
            "-metadata", "title=Akbots", "-metadata:s:v", "title=Akbots",
            "-metadata:s:a", "title=Akbots",
        ]

    subs_extra = []
    if has_subs and s["enc_extension"] != "AVI":
        if s["enc_hardsub"]:
            subs_extra = ["-sn"]  # already burned in, don't also carry a soft track
        elif s["enc_softsub"]:
            if s["enc_extension"] == "MP4":
                subs_extra = ["-c:s", "mov_text", "-map", "0:s?"]
            else:
                subs_extra = ["-c:s", "copy", "-map", "0:s?"]
        else:
            subs_extra = ["-sn"]

    audio_extra = []
    if has_audio:
        a = s["enc_audio_codec"]
        codec_flag = {"aac": "aac", "ac3": "ac3", "opus": "libopus",
                      "vorbis": "libvorbis", "alac": "alac"}.get(a)
        if codec_flag:
            audio_extra += ["-c:a", codec_flag]
            if s["enc_audio_bitrate"] != "source":
                audio_extra += ["-b:a", f"{s['enc_audio_bitrate']}k"]
            if s["enc_audio_samplerate"] != "source":
                sr_map = {"44.1K": "44100", "48K": "48000"}
                audio_extra += ["-ar", sr_map.get(s["enc_audio_samplerate"], "44100")]
            if s["enc_audio_channels"] != "source":
                ac_map = {"1.0": "1", "2.0": "2", "2.1": "3", "5.1": "6", "7.1": "8"}
                audio_extra += ["-ac", ac_map[s["enc_audio_channels"]]]
        else:
            audio_extra += ["-c:a", "copy"]

    return {
        "video_extra": video_extra,
        "vf_parts": vf_parts,
        "audio_extra": audio_extra,
        "subs_extra": subs_extra,
        "metadata_extra": metadata_extra,
        "extension": extension,
    }
