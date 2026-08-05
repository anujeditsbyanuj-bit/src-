import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import GEMINI_API_KEY, GEMINI_API_KEYS_EXTRA, GEMINI_MODEL
from database.db import db
from Akbots.direct_utils import safe_edit

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'

# Official google-genai SDK (https://ai.google.dev) — replaces an earlier
# unofficial approach that scraped gemini.google.com's internal web-app
# endpoints (spoofed browser headers, extracted session tokens from HTML)
# to get free replies without an API key. That approach bypasses Google's
# real auth/quota system and breaks the moment their frontend markup
# changes, so it isn't something this bot wires in — this uses Gemini's
# actual public API with a real API key instead.
try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None

_KEYS = [k.strip() for k in ([GEMINI_API_KEY] + GEMINI_API_KEYS_EXTRA.split(",")) if k.strip()]
_key_idx = 0
_clients = {}


def _client_for(key: str):
    if key not in _clients:
        _clients[key] = genai.Client(api_key=key)
    return _clients[key]


def _next_key():
    global _key_idx
    key = _KEYS[_key_idx % len(_KEYS)]
    _key_idx += 1
    return key


SYSTEM_INSTRUCTION = (
    "You are a helpful assistant chatting with a user inside a Telegram bot. "
    "Keep replies concise and use plain text or simple Telegram-safe HTML "
    "(<b>, <i>, <code>) — never Markdown."
)


def _history_to_contents(history: list, prompt: str):
    contents = []
    for turn in history:
        role = "user" if turn.get("role") == "user" else "model"
        contents.append(genai_types.Content(
            role=role, parts=[genai_types.Part.from_text(text=turn.get("text", ""))]
        ))
    contents.append(genai_types.Content(
        role="user", parts=[genai_types.Part.from_text(text=prompt)]
    ))
    return contents


def _ask_gemini_sync(history: list, prompt: str) -> str:
    """Runs the (blocking) genai call, rotating to the next configured key
    if the current one is rate-limited/invalid. Raises on total failure."""
    last_err = None
    for _ in range(len(_KEYS)):
        key = _next_key()
        try:
            client = _client_for(key)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=_history_to_contents(history, prompt),
                config=genai_types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                ),
            )
            text = (resp.text or "").strip()
            if text:
                return text
            last_err = ValueError("Gemini returned an empty response (likely blocked by safety filters).")
        except Exception as e:
            last_err = e
            continue
    raise last_err or ValueError("Gemini request failed.")


async def _reply_with_gemini(client: Client, message: Message, prompt: str):
    if genai is None:
        return await message.reply_text(
            f"<b>{E_CROSS} google-genai not installed.</b>\n"
            f"<i>Ask an admin to run /install gemini.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    if not _KEYS:
        return await message.reply_text(
            f"<b>{E_CROSS} Gemini isn't configured.</b>\n"
            f"<i>Set GEMINI_API_KEY (free at https://aistudio.google.com/apikey) and restart the bot.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(f"<b>{E_ROCKET} Thinking...</b>", parse_mode=enums.ParseMode.HTML)
    user_id = message.from_user.id
    history = await db.get_gemini_history(user_id)

    try:
        answer = await asyncio.to_thread(_ask_gemini_sync, history, prompt)
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Gemini request failed:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.append_gemini_history(user_id, prompt, answer)

    # Telegram caps a single message at 4096 chars — split long answers.
    chunks = [answer[i:i + 4000] for i in range(0, len(answer), 4000)] or [answer]
    await safe_edit(status.edit_text, chunks[0])
    for chunk in chunks[1:]:
        await message.reply_text(chunk)


@Client.on_message(filters.command(["gemini", "ai", "ask"]) & filters.private)
async def gemini_command(client: Client, message: Message):
    prompt = None
    if len(message.command) > 1:
        prompt = message.text.split(None, 1)[1]
    elif message.reply_to_message and message.reply_to_message.text:
        prompt = message.reply_to_message.text

    if not prompt:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/gemini &lt;message&gt;</code>\n"
            f"<i>Or reply to a text message with /gemini. Remembers your last "
            f"{12} exchanges — use /resetai to start fresh.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _reply_with_gemini(client, message, prompt)


@Client.on_message(filters.command(["resetai", "clearai"]) & filters.private)
async def reset_ai_command(client: Client, message: Message):
    await db.clear_gemini_history(message.from_user.id)
    await message.reply_text(f"<b>{E_CHECK} AI chat history cleared.</b>", parse_mode=enums.ParseMode.HTML)
