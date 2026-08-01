# Akbots - Don't Remove Credit - @AkBots_Official
#
# /chat — general-purpose conversational AI, ported from DAXXCHATBOT's
# ai_handler.py. That source file mixed two unrelated things: (1) a real
# HuggingFace conversational pipeline (facebook/blenderbot-400M-distill),
# and (2) a hardcoded dictionary of crude Hindi-slang "comeback" replies
# the bot would fire at anyone who swore at it. Only (1) was ported —
# (2) isn't something this bot should say to people, so it's gone
# entirely, along with the swear-word detector that triggered it.
#
# Triggers: explicit `/chat <message>` anywhere, or replying to one of the
# bot's own messages (so it doesn't hijack plain text used by other
# plugins' pending-input flows elsewhere in Akbotz — see titanium.py's
# replace-token catch for why that matters).
#
# Heavy dependency note: this needs transformers + torch (CPU wheel) —
# see the comment in requirements.txt. The model loads lazily on first
# use, not at import time, so bot startup isn't affected either way.

import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from logger import LOGGER

logger = LOGGER(__name__)

_model = None
_model_lock = asyncio.Lock()
_load_failed = False


async def _get_model():
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    async with _model_lock:
        if _model is not None or _load_failed:
            return _model
        try:
            from transformers import pipeline
            loop = asyncio.get_event_loop()
            _model = await loop.run_in_executor(
                None,
                lambda: pipeline("conversational", model="facebook/blenderbot-400M-distill", device=-1),
            )
        except Exception as e:
            logger.warning(f"chatbot: model load failed, /chat will be unavailable: {e}")
            _load_failed = True
    return _model


async def _generate(text: str) -> str:
    model = await _get_model()
    if model is None:
        return "Chat model isn't available right now — ask the bot owner to check the logs."
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: model(text))
        return result[-1]["generated_text"]
    except Exception as e:
        logger.debug(f"chatbot: generation failed: {e}")
        return "Sorry, couldn't come up with a reply to that — try rephrasing?"


@Client.on_message(filters.command("chat"))
async def chat_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>Usage:</b> <code>/chat your message</code>\n"
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
