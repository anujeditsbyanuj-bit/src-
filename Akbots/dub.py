# /dub <lang_code> [voice] — reply to a video with this command to get it
# back dubbed into another language: extract audio, transcribe with
# faster-whisper, translate the transcript, re-voice it — either with a
# CLONED voice (male_1/male_2/female_1, from Akbots/voices/, via Coqui
# XTTS-v2) or, if that's not installed, with a preset edge-tts voice —
# then mux the new audio track onto the original video.
#
# /dubpdf — reply to a PDF with this command to get it back with its text
# translated in place (digital text via PyMuPDF, OCR fallback via
# pdf2image+pytesseract for scanned/flattened PDFs).
#
# Voice cloning (XTTS-v2) is a heavier, separate opt-in on top of the
# lightweight faster-whisper transcription: it needs PyTorch (unlike
# faster-whisper/edge-tts), same as VideoDubbing/'s own openai-whisper did
# — there's no good lightweight option for real voice cloning today. If
# it's not installed, /dub still works, just with a stock edge-tts voice
# instead of a cloned one (see _generate_dub_audio).
#
# This is an in-process re-take on the bundled VideoDubbing/ project's own
# pipeline — same core ideas (transcribe -> translate -> TTS -> ffmpeg mux
# for video; OCR -> translate -> rebuild for PDFs), ported over as plain
# functions/commands on THIS bot's own Client instead of VideoDubbing/'s
# separate Client + bot token, with a few deliberate swaps:
#   - transcribes with faster-whisper (CTranslate2, tens of MB, CPU-only
#     is fine) instead of openai-whisper (needs multi-GB PyTorch).
#   - triggered only by explicit /dub or /dubpdf commands, instead of
#     hooking every incoming video/document.
#   - the speech/voice-clone models load lazily on first use (see
#     _get_model / _get_xtts), not at import time, so they don't slow
#     down or block Akbots' own startup.
#   - the review/regenerate menu (see dub_review_callback) reuses the
#     already-transcribed+translated text on "regenerate" — only the TTS
#     step reruns, not the whole pipeline.
#
# Handles a single spoken track -> single dubbed track (no multi-track
# subtitle extraction/AI-subtitle-video modes from VideoDubbing/'s own
# menu — /dub's own SRT button below covers the common case of "I just
# want the transcript/translation as a subtitle file").

import os
import re
import time
import shutil
import asyncio
import logging

import ffmpeg
import edge_tts
from deep_translator import GoogleTranslator
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
from Akbots.direct_utils import safe_edit, make_download_progress
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None

logger = logging.getLogger(__name__)

E_CHECK = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS = '<emoji id=5210952531676504517>❌</emoji>'
E_GEAR  = '<emoji id=5341715473882955310>⚙️</emoji>'

_MODEL = None
_MODEL_LOCK = asyncio.Lock()
_XTTS = None
_XTTS_LOCK = asyncio.Lock()

_VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
# Reference clips for cloning, bundled at Akbots/voices/ — 60-90s samples
# are plenty for XTTS-v2's zero-shot cloning; longer doesn't help much.
_CLONE_VOICES = {
    "male1": os.path.join(_VOICES_DIR, "male_1.mp3"),
    "male2": os.path.join(_VOICES_DIR, "male_2.mp3"),
    "female": os.path.join(_VOICES_DIR, "female_1.mp3"),
}
_DEFAULT_CLONE_VOICE = "male1"

# XTTS-v2's supported language codes — passing anything else raises, so
# _generate_dub_audio falls back to edge-tts automatically for those.
_XTTS_LANGS = {"en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
               "cs", "ar", "zh-cn", "hu", "ko", "ja", "hi"}

# A handful of common languages with a matching neural voice; anything
# else falls back to English. Used when cloning isn't available/applicable
# — add more from https://speechservices.microsoft.com > Language support.
_VOICE_MAP = {
    "hi": "hi-IN-MadhurNeural", "en": "en-US-ChristopherNeural",
    "es": "es-ES-AlvaroNeural", "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural", "ar": "ar-SA-HamedNeural",
    "ru": "ru-RU-DmitryNeural", "pt": "pt-BR-AntonioNeural",
    "ja": "ja-JP-KeitaNeural", "ko": "ko-KR-InJoonNeural",
    "zh-CN": "zh-CN-YunxiNeural", "ta": "ta-IN-ValluvarNeural",
    "te": "te-IN-MohanNeural", "bn": "bn-IN-BashkarNeural",
    "mr": "mr-IN-ManoharNeural", "gu": "gu-IN-NiranjanNeural",
}

# Sessions for the review/regenerate menu (see dub_review_callback) — keyed
# by the status message's (chat_id, message_id) so multiple users/chats can
# have a dub in review at once without clobbering each other. Cleared on
# accept/cancel/error; nothing persists across a restart, same as
# VideoDubbing/'s own in-memory SESSIONS.
_REVIEW_SESSIONS = {}

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Mukta.ttf")


def is_pdf_available() -> bool:
    try:
        import fitz  # noqa: F401
        return True
    except ImportError:
        return False


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def is_cloning_available() -> bool:
    try:
        import TTS  # noqa: F401
        return True
    except ImportError:
        return False


def _voice_for(lang: str) -> str:
    return _VOICE_MAP.get(lang, "en-US-ChristopherNeural")


def _load_model():
    from faster_whisper import WhisperModel
    # "base" on CPU with int8 quantization — a good speed/accuracy/RAM
    # tradeoff for a bot process; bump to "small"/"medium" if the host has
    # the RAM and accuracy matters more than turnaround time.
    return WhisperModel("base", device="cpu", compute_type="int8")


async def _get_model():
    global _MODEL
    if _MODEL is None:
        async with _MODEL_LOCK:
            if _MODEL is None:
                _MODEL = await asyncio.to_thread(_load_model)
    return _MODEL


def _load_xtts():
    from TTS.api import TTS
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)


async def _get_xtts():
    global _XTTS
    if _XTTS is None:
        async with _XTTS_LOCK:
            if _XTTS is None:
                _XTTS = await asyncio.to_thread(_load_xtts)
    return _XTTS


def _transcribe_sync(model, audio_path: str) -> dict:
    """Returns {"text": full transcript, "segments": [{"start","end","text"}]}
    — segments are kept (not just the joined text) so /dub can offer the
    original-language transcript as an SRT file too, same as VideoDubbing/'s
    own subtitle-extraction option."""
    raw_segments, _info = model.transcribe(audio_path)
    segments = [{"start": seg.start, "end": seg.end, "text": seg.text.strip()} for seg in raw_segments]
    text = " ".join(seg["text"] for seg in segments).strip()
    return {"text": text, "segments": segments}


def _format_srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: list) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        if not seg.get("text"):
            continue
        lines.append(str(i))
        lines.append(f"{_format_srt_timestamp(seg['start'])} --> {_format_srt_timestamp(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


def _extract_audio(video_path: str, audio_path: str) -> None:
    ffmpeg.input(video_path).output(
        audio_path, ac=1, ar=16000
    ).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)


def _mux_audio(video_path: str, audio_path: str, out_path: str) -> None:
    video = ffmpeg.input(video_path)
    audio = ffmpeg.input(audio_path)
    ffmpeg.output(
        video.video, audio.audio, out_path,
        vcodec="copy", acodec="aac", shortest=None,
    ).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)


def _chunk_text(text: str, limit: int = 250) -> list:
    """Split on sentence boundaries (., !, ?, Devanagari ।) so no chunk
    exceeds `limit` chars — both edge-tts and XTTS-v2 degrade or error on
    very long single calls."""
    sentences = re.split(r'(?<=[.!?।])\s+', text)
    chunks, current = [], ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 > limit:
            if current:
                chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks or [text]


def _concat_audio_sync(paths: list, out_path: str) -> None:
    """Concatenate several audio files into one, via ffmpeg's concat
    demuxer (needs a plain newline-delimited file list, not the filter
    graph API — simplest reliable way to join N same-format clips)."""
    if len(paths) == 1:
        shutil.copy(paths[0], out_path)
        return
    list_path = out_path + ".txt"
    with open(list_path, "w") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    try:
        ffmpeg.input(list_path, format="concat", safe=0).output(
            out_path, c="copy"
        ).run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def _generate_xtts_sync(xtts, text: str, lang: str, speaker_wav: str, folder: str,
                         temperature: float = 0.75, speed: float = 1.0) -> str:
    """Blocking worker: chunk -> synthesize each chunk with the cloned
    voice -> concatenate. Returns the path to the final combined clip.

    temperature: how expressive/varied the delivery is (XTTS default 0.75).
        Lower (~0.4-0.6) = flatter, steadier, more monotone/stable.
        Higher (~0.8-1.0) = more expressive/emotive, but less predictable —
        pushing much past 1.0 risks garbled or unstable output.
    speed: playback-rate multiplier XTTS applies during synthesis itself
        (not a post-hoc speedup/slowdown of the audio, so pitch doesn't
        shift). ~0.9-1.15 covers "a bit slower/more deliberate" to
        "a bit snappier" without sounding unnatural.
    """
    chunks = _chunk_text(text)
    part_paths = []
    for i, chunk in enumerate(chunks):
        part_path = os.path.join(folder, f"dub_part_{i}.wav")
        xtts.tts_to_file(
            text=chunk, speaker_wav=speaker_wav, language=lang, file_path=part_path,
            temperature=temperature, speed=speed,
        )
        part_paths.append(part_path)
    combined_path = os.path.join(folder, "dub_cloned.wav")
    _concat_audio_sync(part_paths, combined_path)
    return combined_path


async def _generate_dub_audio(text: str, lang: str, voice: str, folder: str,
                               temperature: float = 0.75, speed: float = 1.0) -> str:
    """
    Returns the path to the generated dub audio. Tries a cloned voice
    first (if voice is a known clone name, XTTS-v2 is installed, and the
    target language is one XTTS-v2 supports); falls back to a preset
    edge-tts voice for anything else — never raises, always produces
    *some* audio if the TTS call itself succeeds.
    """
    if voice in _CLONE_VOICES and is_cloning_available() and lang in _XTTS_LANGS:
        try:
            xtts = await _get_xtts()
            return await asyncio.to_thread(
                _generate_xtts_sync, xtts, text, lang, _CLONE_VOICES[voice], folder,
                temperature, speed,
            )
        except Exception as e:
            logger.warning(f"dub: voice cloning failed, falling back to edge-tts: {e}")

    dub_audio_path = os.path.join(folder, "dub.mp3")
    # edge-tts has its own, differently-scaled rate knob (percentage string
    # like "+15%"/"-10%"), not a plain multiplier — convert speed to match.
    rate_pct = round((speed - 1.0) * 100)
    rate_str = f"{'+' if rate_pct >= 0 else ''}{rate_pct}%"
    await edge_tts.Communicate(text, _voice_for(lang), rate=rate_str).save(dub_audio_path)
    return dub_audio_path


async def _translate_chunked(text: str, lang: str) -> str:
    if lang == "en" or not text.strip():
        return text
    # deep-translator's free GoogleTranslator caps out well under ~5000
    # chars per call — chunk on whitespace boundaries so words don't split.
    chunks, current = [], ""
    for word in text.split():
        if len(current) + len(word) + 1 > 4000:
            chunks.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        chunks.append(current)

    out = []
    for chunk in chunks:
        try:
            t = await asyncio.to_thread(GoogleTranslator(source="auto", target=lang).translate, chunk)
            out.append(t or chunk)
        except Exception as e:
            logger.warning(f"dub: translation chunk failed, keeping original text: {e}")
            out.append(chunk)
    return " ".join(out)


@Client.on_message(filters.command(["dub"]))
async def dub_command(client: Client, message: Message):
    video_msg = message.reply_to_message
    has_video = video_msg and (video_msg.video or (video_msg.document and (video_msg.document.mime_type or "").startswith("video/")))
    if not has_video:
        return await message.reply_text(
            f"<b>{E_GEAR} Reply to a video with:</b> <code>/dub &lt;lang_code&gt; [voice] [speed] [expressiveness]</code>\n\n"
            f"e.g. <code>/dub hi</code>, <code>/dub hi male1</code>, <code>/dub hi female 1.1 0.8</code>\n\n"
            f"<b>ᴄʟᴏɴᴇᴅ ᴠᴏɪᴄᴇs:</b> <code>male1</code>, <code>male2</code>, <code>female</code> "
            f"(falls back to a stock voice if omitted or cloning isn't installed)\n"
            f"<b>sᴘᴇᴇᴅ:</b> 0.8–1.3 (default 1.0, only affects pacing, not pitch)\n"
            f"<b>ᴇxᴘʀᴇssɪᴠᴇɴᴇss:</b> 0.4–1.0 (default 0.75 — higher = more emotive/varied, "
            f"lower = flatter/steadier; cloned voices only)",
            parse_mode=enums.ParseMode.HTML,
        )
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_GEAR} Specify a target language code,</b> e.g. <code>/dub hi</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    if not is_available():
        return await message.reply_text(
            f"<b>{E_CROSS} Dubbing isn't available.</b> Run <code>pip install faster-whisper</code> "
            f"(and make sure ffmpeg is installed on the host).",
            parse_mode=enums.ParseMode.HTML,
        )

    lang = message.command[1].lower()
    voice = message.command[2].lower() if len(message.command) > 2 else _DEFAULT_CLONE_VOICE

    def _parse_float(idx: int, default: float, lo: float, hi: float) -> float:
        if len(message.command) <= idx:
            return default
        try:
            return max(lo, min(hi, float(message.command[idx])))
        except ValueError:
            return default

    speed = _parse_float(3, 1.0, 0.8, 1.3)
    temperature = _parse_float(4, 0.75, 0.4, 1.0)

    status = await message.reply_text(f"<b>{E_GEAR} Downloading video...</b>", parse_mode=enums.ParseMode.HTML)

    folder = os.path.join("downloads", "dub", f"{message.chat.id}_{message.id}")
    os.makedirs(folder, exist_ok=True)
    try:
        video_path = await video_msg.download(file_name=os.path.join(folder, "input.mp4"), progress=make_download_progress(status, file_name="input video"))

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Extracting audio...</b>", parse_mode=enums.ParseMode.HTML)
        audio_path = os.path.join(folder, "audio.wav")
        await asyncio.to_thread(_extract_audio, video_path, audio_path)

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Transcribing (can take a bit for longer videos)...</b>", parse_mode=enums.ParseMode.HTML)
        model = await _get_model()
        transcription = await asyncio.to_thread(_transcribe_sync, model, audio_path)
        text = transcription["text"]
        if not text:
            return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't detect any speech in this video.</b>", parse_mode=enums.ParseMode.HTML)

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Translating to '{lang}'...</b>", parse_mode=enums.ParseMode.HTML)
        translated = await _translate_chunked(text, lang)

        # Session for the review/regenerate menu below — "regenerate" only
        # reruns _generate_and_send (TTS + mux + preview), not the
        # transcribe/translate steps above, since those don't change.
        session = {
            "folder": folder, "video_path": video_path, "translated": translated,
            "segments": transcription["segments"], "lang": lang, "voice": voice,
            "speed": speed, "temperature": temperature, "status": status,
            "chat_id": message.chat.id, "reply_to": message.id,
        }
        await _generate_and_send(client, session)
    except Exception as e:
        logger.warning(f"dub: pipeline failed: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Dubbing failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
        shutil.rmtree(folder, ignore_errors=True)


async def _generate_and_send(client: Client, session: dict):
    """TTS -> mux -> send as a preview with a review/regenerate/cancel/SRT
    menu attached. Called both from dub_command (first pass) and from
    dub_review_callback's "regenerate" button (reruns just this part)."""
    folder, video_path = session["folder"], session["video_path"]
    translated, lang, voice = session["translated"], session["lang"], session["voice"]
    speed, temperature = session["speed"], session["temperature"]
    status = session["status"]

    cloning = voice in _CLONE_VOICES and is_cloning_available() and lang in _XTTS_LANGS
    voice_label = f"cloned voice '{voice}'" if cloning else "stock voice"
    await safe_edit(status.edit_text, f"<b>{E_GEAR} Generating dubbed voice ({voice_label})...</b>", parse_mode=enums.ParseMode.HTML)
    dub_audio_path = await _generate_dub_audio(translated, lang, voice, folder, temperature=temperature, speed=speed)

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Merging into video...</b>", parse_mode=enums.ParseMode.HTML)
    out_path = os.path.join(folder, "dubbed.mp4")
    await asyncio.to_thread(_mux_audio, video_path, dub_audio_path, out_path)
    session["out_path"] = out_path

    await safe_edit(status.edit_text, f"<b>{E_GEAR} Uploading preview...</b>", parse_mode=enums.ParseMode.HTML)
    keyboard = InlineKeyboardMarkup([[
        make_button("✅ ᴀᴄᴄᴇᴘᴛ", callback_data="dubreview_accept", style=_BS.SUCCESS if _BS else None),
        make_button("🔄 ʀᴇɢᴇɴᴇʀᴀᴛᴇ", callback_data="dubreview_regen", style=_BS.PRIMARY if _BS else None),
        make_button("❌ ᴄᴀɴᴄᴇʟ", callback_data="dubreview_cancel", style=_BS.DANGER if _BS else None),
    ], [
        make_button("📝 ɢᴇᴛ sʀᴛ (ᴏʀɪɢɪɴᴀʟ ᴛᴇxᴛ)", callback_data="dubreview_srt", style=_BS.PRIMARY if _BS else None),
    ]])
    preview = await client.send_video(
        session["chat_id"], out_path,
        caption=f"<blockquote><b>{E_CHECK} Dubbed preview ({lang}, {voice_label})</b>\n"
                f"Accept to keep it, Regenerate for a new take on the same text, or Cancel.</blockquote>",
        reply_to_message_id=session["reply_to"],
        parse_mode=enums.ParseMode.HTML,
        reply_markup=keyboard,
    )
    _REVIEW_SESSIONS[(preview.chat.id, preview.id)] = session
    try:
        await status.delete()
    except Exception:
        pass


@Client.on_callback_query(filters.regex("^dubreview_"))
async def dub_review_callback(client: Client, callback_query: CallbackQuery):
    key = (callback_query.message.chat.id, callback_query.message.id)
    session = _REVIEW_SESSIONS.get(key)
    if not session:
        return await callback_query.answer("Session expired.", show_alert=True)

    choice = callback_query.data.split("_", 1)[1]

    if choice == "accept":
        await callback_query.answer("Kept.")
        await safe_edit(callback_query.message.edit_reply_markup, reply_markup=None)
        _REVIEW_SESSIONS.pop(key, None)
        shutil.rmtree(session["folder"], ignore_errors=True)

    elif choice == "cancel":
        await callback_query.answer("Canceled.")
        try:
            await callback_query.message.delete()
        except Exception:
            pass
        _REVIEW_SESSIONS.pop(key, None)
        shutil.rmtree(session["folder"], ignore_errors=True)

    elif choice == "regen":
        await callback_query.answer("Regenerating...")
        await safe_edit(callback_query.message.edit_reply_markup, reply_markup=None)
        # New status message for the regen pass — the preview message
        # itself is a video (can't edit_text on it); old preview is left
        # as-is above (buttons removed) and a fresh one gets sent below.
        session["status"] = await callback_query.message.reply_text(
            f"<b>{E_GEAR} Regenerating...</b>", parse_mode=enums.ParseMode.HTML,
        )
        _REVIEW_SESSIONS.pop(key, None)
        await _generate_and_send(client, session)

    elif choice == "srt":
        await callback_query.answer()
        srt_path = os.path.join(session["folder"], "original.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(_segments_to_srt(session["segments"]))
        await client.send_document(
            callback_query.message.chat.id, srt_path,
            caption=f"<blockquote><b>{E_CHECK} Original-language transcript (SRT)</b></blockquote>",
            reply_to_message_id=callback_query.message.id,
            parse_mode=enums.ParseMode.HTML,
        )


# --- /dubpdf — PDF translation (ported from VideoDubbing/'s pdflang_
# callback flow) --------------------------------------------------------
# Same approach: pull text blocks with PyMuPDF (digital text) or, if a
# page has none (scanned/flattened PDF), fall back to pdf2image + OCR;
# translate each block; whiteout the original + draw the translation back
# in at the same position with the bundled Mukta font (covers Devanagari
# scripts that a default PDF font can't render).

def _pdf_extract_blocks_sync(pdf_path: str) -> list:
    import fitz
    doc = fitz.open(pdf_path)
    all_blocks = []
    for page_num in range(len(doc)):
        for b in doc[page_num].get_text("blocks"):
            if b[6] == 0:  # text block (not an image block)
                text = b[4].strip()
                if text:
                    all_blocks.append({"page_num": page_num, "rect": (b[0], b[1], b[2], b[3]), "text": text})

    if not all_blocks:
        # No digital text anywhere in the doc — OCR fallback.
        from pdf2image import convert_from_path
        import pytesseract
        images = convert_from_path(pdf_path)
        scale = 72 / 200  # pdf2image's default 200dpi -> PyMuPDF's 72dpi points
        for page_num, img in enumerate(images):
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            blocks_dict = {}
            for i in range(len(data["text"])):
                if int(data["conf"][i]) > 30 and data["text"][i].strip():
                    block_num = data["block_num"][i]
                    b = blocks_dict.setdefault(block_num, {"text": [], "x0": data["left"][i], "y0": data["top"][i], "x1": 0, "y1": 0})
                    b["text"].append(data["text"][i])
                    b["x0"] = min(b["x0"], data["left"][i])
                    b["y0"] = min(b["y0"], data["top"][i])
                    b["x1"] = max(b["x1"], data["left"][i] + data["width"][i])
                    b["y1"] = max(b["y1"], data["top"][i] + data["height"][i])
            for b in blocks_dict.values():
                text = " ".join(b["text"]).strip()
                if text:
                    all_blocks.append({
                        "page_num": page_num,
                        "rect": (b["x0"] * scale, b["y0"] * scale, b["x1"] * scale, b["y1"] * scale),
                        "text": text,
                    })
    doc.close()
    return all_blocks


def _pdf_rebuild_sync(pdf_path: str, out_path: str, blocks: list) -> None:
    import fitz
    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        doc[page_num].insert_font(fontname="Mukta", fontfile=_FONT_PATH)
    for b in blocks:
        if b.get("translated"):
            page = doc[b["page_num"]]
            rect = fitz.Rect(*b["rect"])
            page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
            page.insert_textbox(rect, b["translated"], fontsize=11, fontname="Mukta", color=(0, 0, 0), align=0)
    doc.save(out_path)
    doc.close()


@Client.on_message(filters.command(["dubpdf"]))
async def dubpdf_command(client: Client, message: Message):
    doc_msg = message.reply_to_message
    is_pdf = doc_msg and doc_msg.document and (doc_msg.document.mime_type == "application/pdf")
    if not is_pdf:
        return await message.reply_text(
            f"<b>{E_GEAR} Reply to a PDF with:</b> <code>/dubpdf &lt;lang_code&gt;</code>\n\ne.g. <code>/dubpdf hi</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_GEAR} Specify a target language code,</b> e.g. <code>/dubpdf hi</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    if not is_pdf_available():
        return await message.reply_text(
            f"<b>{E_CROSS} PDF translation isn't available.</b> Run "
            f"<code>pip install pymupdf pdf2image pytesseract</code> (and make sure "
            f"<code>poppler-utils</code> + <code>tesseract-ocr</code> are installed on the host).",
            parse_mode=enums.ParseMode.HTML,
        )

    lang = message.command[1].lower()
    status = await message.reply_text(f"<b>{E_GEAR} Downloading PDF...</b>", parse_mode=enums.ParseMode.HTML)

    folder = os.path.join("downloads", "dub", f"pdf_{message.chat.id}_{message.id}")
    os.makedirs(folder, exist_ok=True)
    pdf_path = os.path.join(folder, "input.pdf")
    out_path = os.path.join(folder, "translated.pdf")
    try:
        await doc_msg.download(file_name=pdf_path, progress=make_download_progress(status, file_name="input PDF"))

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Reading PDF text (OCR fallback if scanned)...</b>", parse_mode=enums.ParseMode.HTML)
        blocks = await asyncio.to_thread(_pdf_extract_blocks_sync, pdf_path)
        if not blocks:
            return await safe_edit(status.edit_text, 
                f"<b>{E_CROSS} No text found to translate</b> — this PDF may be image-only and OCR found nothing readable.",
                parse_mode=enums.ParseMode.HTML,
            )

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Translating {len(blocks)} block(s) to '{lang}'...</b>", parse_mode=enums.ParseMode.HTML)
        sem = asyncio.Semaphore(10)

        async def _translate_block(b):
            async with sem:
                b["translated"] = await _translate_chunked(b["text"], lang)

        await asyncio.gather(*(_translate_block(b) for b in blocks), return_exceptions=True)

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Rebuilding PDF...</b>", parse_mode=enums.ParseMode.HTML)
        await asyncio.to_thread(_pdf_rebuild_sync, pdf_path, out_path, blocks)

        await safe_edit(status.edit_text, f"<b>{E_GEAR} Uploading...</b>", parse_mode=enums.ParseMode.HTML)
        await client.send_document(
            message.chat.id, out_path,
            caption=f"<blockquote><b>{E_CHECK} PDF translated ({lang})</b></blockquote>",
            reply_to_message_id=message.id,
            parse_mode=enums.ParseMode.HTML,
        )
        await status.delete()
    except Exception as e:
        logger.warning(f"dubpdf: pipeline failed: {e}")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} PDF translation failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
