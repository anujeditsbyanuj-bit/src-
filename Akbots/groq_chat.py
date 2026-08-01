# Akbots - Don't Remove Credit - @AkBots_Official
#
# /groq — Groq-powered AI chat, ported from the standalone groq-chatbot-main
# project (https://github.com/.../groq-chatbot), which was built on
# python-telegram-bot + mongopersistence. That framework doesn't exist in
# this bot — Akbots runs on Pyrogram with its own database/db.py — so only
# the actual logic was ported over: the Groq SDK call, the per-user model
# selection, and the rolling chat history. It's wired up exactly like
# Akbots/gemini_chat.py (same key-rotation trick, same history pattern),
# just pointed at Groq's OpenAI-compatible chat.completions endpoint
# instead of Gemini's.

import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import GROQ_API_KEY, GROQ_API_KEYS_EXTRA, GROQ_MODEL
from database.db import db
from Akbots.direct_utils import safe_edit

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

try:
    from groq import Groq
    import groq as _groq_pkg
except ImportError:
    Groq = None
    _groq_pkg = None

_KEYS = [k.strip() for k in ([GROQ_API_KEY] + GROQ_API_KEYS_EXTRA.split(",")) if k.strip()]
_key_idx = 0
_clients = {}

# Selectable models — keep this in sync with https://console.groq.com/docs/models
# (Groq deprecates/renames models fairly often; check that page before adding more).
MODELS = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "qwen/qwen3.6-27b",
]

SYSTEM_PROMPT = (
    "You are a helpful assistant chatting with a user inside a Telegram bot. "
    "Keep replies concise and use plain text or simple Telegram-safe HTML "
    "(<b>, <i>, <code>) — never Markdown."
)


def _client_for(key: str):
    if key not in _clients:
        _clients[key] = Groq(api_key=key)
    return _clients[key]


def _next_key():
    global _key_idx
    key = _KEYS[_key_idx % len(_KEYS)]
    _key_idx += 1
    return key


def _history_to_messages(history: list, prompt: str):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history:
        role = "user" if turn.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": turn.get("text", "")})
    messages.append({"role": "user", "content": prompt})
    return messages


def _ask_groq_sync(history: list, prompt: str, model: str) -> str:
    """Runs the (blocking) groq call, rotating to the next configured key
    if the current one is rate-limited/invalid. Raises on total failure."""
    last_err = None
    for _ in range(len(_KEYS)):
        key = _next_key()
        try:
            client = _client_for(key)
            resp = client.chat.completions.create(
                messages=_history_to_messages(history, prompt),
                model=model,
                stream=False,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
            last_err = ValueError("Groq returned an empty response.")
        except _groq_pkg.GroqError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise last_err or ValueError("Groq request failed.")


async def _reply_with_groq(client: Client, message: Message, prompt: str):
    if Groq is None:
        return await message.reply_text(
            f"<b>{E_CROSS} groq not installed.</b>\n"
            f"<i>Ask an admin to run /install groq.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    if not _KEYS:
        return await message.reply_text(
            f"<b>{E_CROSS} Groq isn't configured.</b>\n"
            f"<i>Set GROQ_API_KEY (free at https://console.groq.com/keys) and restart the bot.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(f"<b>{E_ROCKET} Thinking...</b>", parse_mode=enums.ParseMode.HTML)
    user_id = message.from_user.id
    history = await db.get_groq_history(user_id)
    model = await db.get_groq_model(user_id) or GROQ_MODEL

    try:
        answer = await asyncio.to_thread(_ask_groq_sync, history, prompt, model)
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Groq request failed:</b>\n<code>{e}</code>\n"
            f"<i>Start a new conversation with /resetgroq.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.append_groq_history(user_id, prompt, answer)

    # Telegram caps a single message at 4096 chars — split long answers.
    chunks = [answer[i:i + 4000] for i in range(0, len(answer), 4000)] or [answer]
    await safe_edit(status.edit_text, chunks[0])
    for chunk in chunks[1:]:
        await message.reply_text(chunk)


@Client.on_message(filters.command(["groq", "groqai"]) & filters.private)
async def groq_command(client: Client, message: Message):
    prompt = None
    if len(message.command) > 1:
        prompt = message.text.split(None, 1)[1]
    elif message.reply_to_message and message.reply_to_message.text:
        prompt = message.reply_to_message.text

    if not prompt:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/groq &lt;message&gt;</code>\n"
            f"<i>Or reply to a text message with /groq. Remembers your last 12 "
            f"exchanges — use /resetgroq to start fresh, /groqmodel to switch models.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _reply_with_groq(client, message, prompt)


@Client.on_message(filters.command(["resetgroq", "cleargroq"]) & filters.private)
async def reset_groq_command(client: Client, message: Message):
    await db.clear_groq_history(message.from_user.id)
    await message.reply_text(f"<b>{E_CHECK} Groq chat history cleared.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("groqmodel") & filters.private)
async def groq_model_command(client: Client, message: Message):
    current = await db.get_groq_model(message.from_user.id) or GROQ_MODEL
    buttons = [
        [InlineKeyboardButton(
            f"{'✅ ' if m == current else ''}{m}", callback_data=f"groqmodel_{m}"
        )]
        for m in MODELS
    ]
    await message.reply_text(
        f"<b>{E_GEAR} Select a Groq model:</b>\n<i>Current: <code>{current}</code></i>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^groqmodel_"))
async def groq_model_callback(client: Client, cq: CallbackQuery):
    model = cq.data.split("_", 1)[1]
    if model not in MODELS:
        return await cq.answer("Unknown model.", show_alert=True)
    await db.set_groq_model(cq.from_user.id, model)
    await cq.answer(f"Model set to {model}")
    await safe_edit(cq.edit_message_text, 
        f"<b>{E_CHECK} Model updated:</b> <code>{model}</code>\n<i>Use /resetgroq to refresh the conversation.</i>",
        parse_mode=enums.ParseMode.HTML,
    )
