# Akbots - Don't Remove Credit - @AkBots_Official
#
# /coach — conversation-practice agent, ported from AiBoT
# (github.com/Suryanshu-Nabheet/AiBoT)'s Coach Agent
# (app/api/agent/coach/route.ts): a voice-friendly partner for casual chat,
# deep discussion, mock interviews, or debate practice.
#
# Unlike /aiulta (which is stateless, one instruction per call) or /gpt
# (plain single-turn chat), /coach keeps a short rolling per-user memory of
# the session so it's an actual back-and-forth conversation — this also
# covers the "multi-turn memory" gap /aiulta doesn't have.
#
# Voice: if you send/reply with a voice note, it's transcribed (Whisper)
# and the reply comes back as a voice note too (TTS) — matching AiBoT's
# Coach being fundamentally a voice conversation partner. Plain text in ->
# plain text out otherwise.
#
# Usage:
#   /coach <mode>              -> start/switch session. modes: chat, discuss,
#                                  interview, debate
#   /coach <message>           -> continue the current session (default
#                                  mode "chat" if none started yet)
#   /coach reset               -> clear your session history
#   (or just send a voice note directly with /coach as caption/reply)

import os
import tempfile

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import OPENROUTER_API_KEY, OPENROUTER_SITE_URL, OPENROUTER_SITE_NAME, OPENROUTER_MODEL, OPENAI_API_KEY, ADMINS, GPT_ALLOWED_USERS
from logger import LOGGER
from Akbots.direct_utils import safe_edit, make_download_progress

logger = LOGGER(__name__)

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

COACH_SCOPE = filters.private | filters.group
AGENT_MODEL = OPENROUTER_MODEL or "openai/gpt-4o-mini"
MAX_TURNS_KEPT = 12  # user+assistant messages kept per session (~6 exchanges)

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

_client = None
if AsyncOpenAI is not None and OPENROUTER_API_KEY:
    _client = AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": OPENROUTER_SITE_URL,
            "X-Title": OPENROUTER_SITE_NAME,
        },
    )

_direct_client = None
if AsyncOpenAI is not None and OPENAI_API_KEY:
    _direct_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# in-memory per-user sessions: user_id -> {"mode": str, "history": [...]}
# Process-local by design (same tradeoff as other in-memory session state
# in this bot, e.g. Akbots/audio_select.py's pending-selection dict) —
# resets on restart, which is fine for a lightweight practice session.
_sessions: dict[int, dict] = {}

MODE_PROMPTS = {
    "chat": (
        "You are a friendly, knowledgeable conversation partner having a "
        "casual chat. Keep replies natural and conversational, 1-4 "
        "sentences, no markdown headers or bullet lists — just talk."
    ),
    "discuss": (
        "You are an expert discussion partner for deep, thoughtful "
        "conversation on any topic the user raises. Give substantive, "
        "well-reasoned responses, but stay conversational — no markdown "
        "headers or bullet lists, write in flowing prose."
    ),
    "interview": (
        "You are a professional, rigorous job interviewer conducting a "
        "mock interview. Ask one focused question at a time, follow up on "
        "weak or vague answers, and after the user answers give brief, "
        "honest feedback before your next question. Stay in character as "
        "the interviewer throughout — plain conversational sentences, no "
        "markdown."
    ),
    "debate": (
        "You are a skilled debate opponent. Take the counter-position to "
        "whatever the user argues and defend it with clear, respectful, "
        "logical reasoning — never simply agree. Keep it conversational, "
        "1-4 sentences, no markdown."
    ),
}
MODE_ALIASES = {
    "chat": "chat", "casual": "chat",
    "discuss": "discuss", "discussion": "discuss", "deep": "discuss",
    "interview": "interview", "mock": "interview", "job": "interview",
    "debate": "debate", "argue": "debate",
}


def _is_allowed(user_id: int) -> bool:
    if not GPT_ALLOWED_USERS:
        return True
    return user_id in GPT_ALLOWED_USERS or user_id in ADMINS


async def _transcribe_voice(path: str) -> str:
    if _direct_client is None:
        return ""
    try:
        with open(path, "rb") as f:
            transcript = await _direct_client.audio.transcriptions.create(model="whisper-1", file=f)
        return (transcript.text or "").strip()
    except Exception as e:
        logger.error(f"coach: transcription failed: {e}")
        return ""


async def _text_to_speech(text: str) -> str | None:
    if _direct_client is None:
        return None
    try:
        resp = await _direct_client.audio.speech.create(model="tts-1", voice="alloy", input=text[:4000])
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        resp.write_to_file(path)
        return path
    except Exception as e:
        logger.error(f"coach: TTS failed: {e}")
        return None


@Client.on_message(filters.command("coach") & COACH_SCOPE)
async def coach_command(client: Client, message: Message):
    if _client is None:
        await message.reply_text(
            f"<b>{E_CROSS} Coach isn't configured.</b>\n<i>Set OPENROUTER_API_KEY to enable /coach.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not message.from_user or not _is_allowed(message.from_user.id):
        await message.reply_text(f"<b>{E_CROSS} You're not authorized to use this.</b>", parse_mode=enums.ParseMode.HTML)
        return

    user_id = message.from_user.id
    reply = message.reply_to_message

    arg = message.text.split(None, 1)[1].strip() if len(message.command) > 1 else ""

    if arg.lower() == "reset":
        _sessions.pop(user_id, None)
        await message.reply_text(f"<b>{E_CHECK} Session reset ho gaya.</b>", parse_mode=enums.ParseMode.HTML)
        return

    session = _sessions.setdefault(user_id, {"mode": "chat", "history": []})

    # first word alone matching a mode name -> just switch/start mode
    first_word = arg.split(None, 1)[0].lower() if arg else ""
    if first_word in MODE_ALIASES and len(arg.split(None, 1)) == 1:
        session["mode"] = MODE_ALIASES[first_word]
        session["history"] = []
        mode_label = session["mode"]
        await message.reply_text(
            f"<b>{E_CHECK} Coach mode:</b> <i>{mode_label}</i>\n"
            f"<i>Ab /coach &lt;message&gt; se baat shuru karo, ya voice note bhejo.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return
    if first_word in MODE_ALIASES:
        mode_key = first_word
        session["mode"] = MODE_ALIASES[mode_key]
        arg = arg.split(None, 1)[1]

    user_text = arg
    voice_source = message.voice or (reply.voice if reply else None)
    used_voice = False
    if not user_text and voice_source:
        if _direct_client is None:
            await message.reply_text(
                f"<b>{E_CROSS} Voice input needs OPENAI_API_KEY (for Whisper).</b>",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        vstatus = await message.reply_text(f"<b>{E_GEAR} Sun raha hoon...</b>", parse_mode=enums.ParseMode.HTML)
        voice_path = None
        try:
            voice_path = await (message if message.voice else reply).download(
                progress=make_download_progress(vstatus, file_name="voice note")
            )
            user_text = await _transcribe_voice(voice_path)
        finally:
            if voice_path and os.path.exists(voice_path):
                os.remove(voice_path)
        await vstatus.delete()
        used_voice = True
        if not user_text:
            await message.reply_text(f"<b>{E_CROSS} Kuch samajh nahi aaya voice note mein.</b>", parse_mode=enums.ParseMode.HTML)
            return

    if not user_text:
        await message.reply_text(
            f"<b>{E_GEAR} Usage:</b>\n"
            f"<code>/coach chat|discuss|interview|debate</code> — start a mode\n"
            f"<code>/coach &lt;message&gt;</code> — continue the conversation\n"
            f"<code>/coach reset</code> — clear session\n"
            f"<i>Current mode: {session['mode']}</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    status = await message.reply_text(f"<b>{E_GEAR} Soch raha hoon...</b>", parse_mode=enums.ParseMode.HTML)

    system_prompt = MODE_PROMPTS[session["mode"]]
    history = session["history"]
    history.append({"role": "user", "content": user_text})
    history[:] = history[-MAX_TURNS_KEPT:]

    llm_messages = [{"role": "system", "content": system_prompt}] + history

    try:
        resp = await _client.chat.completions.create(
            model=AGENT_MODEL,
            messages=llm_messages,
            max_tokens=600,
            temperature=0.8,
        )
        reply_text = (resp.choices[0].message.content or "").strip() or "Hmm, kuch keh nahi paaya."
    except Exception as e:
        logger.exception("coach: LLM call failed")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} AI se baat nahi ho paayi.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        return

    history.append({"role": "assistant", "content": reply_text})
    history[:] = history[-MAX_TURNS_KEPT:]

    if used_voice and _direct_client is not None:
        mp3_path = await _text_to_speech(reply_text)
        if mp3_path:
            try:
                await status.delete()
                await message.reply_voice(mp3_path)
            except Exception:
                await safe_edit(status.edit_text, reply_text)
            finally:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            return

    try:
        await safe_edit(status.edit_text, reply_text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await safe_edit(status.edit_text, reply_text)
