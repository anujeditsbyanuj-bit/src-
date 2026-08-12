# Akbots - Don't Remove Credit - @AkBots_Official
#
# /chat — general-purpose conversational AI.
#
# Originally ported from DAXXCHATBOT's ai_handler.py, which used a local
# HuggingFace "conversational" pipeline (facebook/blenderbot-400M-distill).
# That pipeline task was deprecated and removed from the `transformers`
# library itself (transformers>=4.42 has no "conversational" task and no
# Conversation class any more) — with requirements.txt pinning no version,
# every fresh install pulls the latest transformers and pipeline(...) just
# raises immediately, so /chat permanently fell back to the "model isn't
# available" message. On top of that it needed a multi-GB torch install
# for a small, dated model with mediocre replies.
#
# Fixed by reusing the same hosted backend Akbots/groq_chat.py already
# talks to (Groq's OpenAI-compatible chat.completions API) instead of
# running anything locally — same GROQ_API_KEY(s)/model already configured
# for /groq, no extra setup, no torch/transformers dependency at all.
#
# Triggers: explicit `/chat <message>` anywhere, or replying to one of the
# bot's own messages (so it doesn't hijack plain text used by other
# plugins' pending-input flows elsewhere in Akbotz — see titanium.py's
# replace-token catch for why that matters).

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import GROQ_API_KEY, GROQ_API_KEYS_EXTRA, GROQ_MODEL
from logger import LOGGER

logger = LOGGER(__name__)

try:
    from groq import Groq
    import groq as _groq_pkg
except ImportError:
    Groq = None
    _groq_pkg = None

_KEYS = [k.strip() for k in ([GROQ_API_KEY] + GROQ_API_KEYS_EXTRA.split(",")) if k.strip()]
_key_idx = 0
_clients = {}

SYSTEM_PROMPT = (
    "You are a friendly, casual conversational partner chatting with someone "
    "inside a Telegram bot's /chat command. Keep replies short and natural "
    "(a sentence or two, like a real chat), use plain text or simple "
    "Telegram-safe HTML (<b>, <i>, <code>) — never Markdown."
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


def _ask_groq_sync(text: str) -> str:
    last_err = None
    for _ in range(len(_KEYS)):
        key = _next_key()
        try:
            client = _client_for(key)
            resp = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                model=GROQ_MODEL,
                stream=False,
            )
            reply = (resp.choices[0].message.content or "").strip()
            if reply:
                return reply
            last_err = ValueError("Groq returned an empty response.")
        except _groq_pkg.GroqError as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise last_err or ValueError("Groq request failed.")


async def _generate(text: str) -> str:
    if Groq is None:
        return "Chat model isn't available right now — ask the bot owner to run <code>pip install groq</code>."
    if not _KEYS:
        return "Chat model isn't available right now — ask the bot owner to set GROQ_API_KEY."
    try:
        import asyncio
        return await asyncio.to_thread(_ask_groq_sync, text)
    except Exception as e:
        logger.debug(f"chatbot: generation failed: {e}")
        return "Sorry, couldn't come up with a reply to that — try rephrasing?"


@Client.on_message(filters.command("chat"))
async def chat_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>ᴜsᴀɢᴇ:</b> <code>/chat your message</code>\n"
            "<i>Or just reply to one of my messages to keep the conversation going.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    text = message.text.split(None, 1)[1]
    reply = await _generate(text)
    await message.reply_text(reply)


@Client.on_message(filters.reply & filters.text, group=10)
async def chat_continue(client: Client, message: Message):
    replied = message.reply_to_message
    if not replied or not replied.from_user or not replied.from_user.is_self:
        return
    reply = await _generate(message.text)
    await message.reply_text(reply)
