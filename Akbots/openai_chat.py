# Akbots - Don't Remove Credit - @AkBots_Official
#
# /gpt — GPT-family (and Claude) chat, ported from karfly/chatgpt_telegram_bot
# (bot/openai_utils.py + config/models.yml + config/chat_modes.yml). That
# repo runs on python-telegram-bot/aiogram with its own Mongo dialog schema,
# plus voice transcription (Whisper) and image generation (gpt-image-1) —
# none of which exist in this bot. Only the core text-chat logic was
# ported, wired up exactly like Akbots/groq_chat.py and gemini_chat.py:
# same key/history pattern, same plugin shape.
#
# One deliberate change from the original: instead of talking to OpenAI
# directly (needs its own billing) this port routes every model —
# including Anthropic's — through OpenRouter, since that's a single
# OpenAI-compatible endpoint. One OPENROUTER_API_KEY covers GPT-4o,
# GPT-5.5, and Claude Opus/Sonnet/Haiku all at once. If you'd rather hit
# OpenAI directly, set OPENAI_API_KEY too — non openrouter-only models
# will prefer it automatically.

import asyncio
import base64
import os
import tempfile
import time
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import (
    OPENROUTER_API_KEY, OPENROUTER_SITE_URL, OPENROUTER_SITE_NAME,
    OPENROUTER_MODEL, OPENAI_API_KEY, ADMINS, GPT_ALLOWED_USERS,
)
from database.db import db

try:
    from pyrogram.enums import ButtonStyle as _BS
except ImportError:
    _BS = None
from Akbots.direct_utils import safe_edit, make_download_progress

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'


def _is_allowed(user_id: int) -> bool:
    """Access control: if GPT_ALLOWED_USERS is empty, everyone can use
    /gpt/imagine/voice/vision. If it's set, only those IDs (plus ADMINS)
    can."""
    if not GPT_ALLOWED_USERS:
        return True
    return user_id in GPT_ALLOWED_USERS or user_id in ADMINS


async def _deny(message: Message):
    await message.reply_text(
        f"<b>{E_CROSS} You're not authorized to use this.</b>\n"
        f"<i>Ask a bot admin to add your ID to GPT_ALLOWED_USERS.</i>",
        parse_mode=enums.ParseMode.HTML,
    )

# /gpt, /gptmodel, /gptmode, /resetgpt, /imagine all work in private chats
# *and* groups (unlike the private-only Groq/Gemini plugins) — matching
# the original repo, which supported both. Voice auto-transcription stays
# private-only below, to avoid the bot listening to every voice note in a
# group.
CHAT_SCOPE = filters.private | filters.group

try:
    from openai import AsyncOpenAI, APIError
except ImportError:
    AsyncOpenAI = None
    APIError = Exception

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

# Direct OpenAI client — only for Whisper transcription and gpt-image-1,
# which OpenRouter doesn't proxy. None of the /gpt chat flow uses this.
_direct_client = None
if AsyncOpenAI is not None and OPENAI_API_KEY:
    _direct_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# label -> OpenRouter model slug. Keep in sync with the original repo's
# config/models.yml; check https://openrouter.ai/models for current ids.
MODELS = {
    "GPT-4o mini":      "openai/gpt-4o-mini",
    "GPT-4o":           "openai/gpt-4o",
    "GPT-5.5":          "openai/gpt-5.5",
    "Claude Opus 4.8":  "anthropic/claude-opus-4.8",
    "Claude Sonnet":    "anthropic/claude-sonnet-latest",
    "Claude Haiku":     "anthropic/claude-haiku-latest",
}
MODEL_SLUGS = list(MODELS.values())

# USD per 1000 tokens — copied from the original repo's config/models.yml.
# Used only to log estimated spend via /gptusage & /gptstats; OpenRouter's
# own dashboard (openrouter.ai/activity) is the source of truth for billing.
PRICING = {
    "openai/gpt-4o-mini":              (0.00015, 0.0006),
    "openai/gpt-4o":                   (0.0025, 0.01),
    "openai/gpt-5.5":                  (0.005, 0.03),
    "anthropic/claude-opus-4.8":       (0.005, 0.025),
    "anthropic/claude-sonnet-latest":  (0.003, 0.015),
    "anthropic/claude-haiku-latest":   (0.001, 0.005),
}
IMAGE_COST_PER_IMAGE = 0.042   # gpt-image-1, 1024x1024, medium quality
WHISPER_COST_PER_MINUTE = 0.006

# name -> (label, system prompt), ported from the original repo's
# config/chat_modes.yml — all 15 modes. "artist" has no text prompt; it's
# special-cased in gpt_command() below to redirect to image generation.
CHAT_MODES = {
    "assistant": (
        "👩🏼‍🎓 General Assistant",
        "As an advanced chatbot Assistant, your primary goal is to assist users to "
        "the best of your ability. Be detailed and thorough, and use examples where "
        "helpful. Always prioritize the needs and satisfaction of the user.",
    ),
    "code_assistant": (
        "👩🏼‍💻 Code Assistant",
        "As an advanced chatbot Code Assistant, your primary goal is to help write, "
        "edit, and explain code. Provide correct, runnable examples. Format output "
        "in Markdown.",
    ),
    "english_tutor": (
        "🇬🇧 English Tutor",
        "You're an advanced chatbot English Tutor. Help the user practice grammar, "
        "vocabulary, pronunciation, and conversation skills, and suggest study "
        "resources.",
    ),
    "motivator": (
        "🌟 Motivator",
        "You're an advanced chatbot Motivator. Inspire and motivate the user with "
        "encouragement, support, and concrete advice for setting and reaching goals.",
    ),
    "travel_guide": (
        "🧳 Travel Guide",
        "You're an advanced chatbot Travel Guide. Give helpful, specific "
        "recommendations about destinations, attractions, accommodation, transport, "
        "and local customs.",
    ),
    "sql_assistant": (
        "📊 SQL Assistant",
        "You're an advanced chatbot SQL Assistant. Help with SQL queries, database "
        "design, and data analysis. Format output in Markdown.",
    ),
    "psychologist": (
        "🧠 Psychologist",
        "You're an advanced chatbot Psychologist Assistant. You can provide emotional "
        "support, guidance, and advice to users facing personal challenges such as "
        "stress, anxiety, and relationships. You are not a licensed professional, and "
        "your assistance should not replace professional help — say so when it's "
        "relevant.",
    ),
    "elon_musk": (
        "🚀 Elon Musk",
        "You're roleplaying as Elon Musk — use his tone, manner and vocabulary. Stay "
        "in character; don't break it to add explanations.",
    ),
    "rick_sanchez": (
        "🥒 Rick Sanchez",
        "You're roleplaying as Rick Sanchez from Rick and Morty — use his tone, "
        "manner and vocabulary. Stay in character; don't break it to add "
        "explanations.",
    ),
    "money_maker": (
        "💰 Money Maker",
        "You are Money Maker Assistant, an entrepreneurial AI. Your goal is to help "
        "the user turn their initial capital into as much money as possible in the "
        "shortest reasonable time, without doing anything illegal. Ask about their "
        "capital, business type preference (online/offline), then give concrete, "
        "specific, ready-to-act steps — not abstract ideas.",
    ),
    "movie_expert": (
        "🎬 Movie Expert",
        "As an advanced chatbot Movie Expert Assistant, answer questions about "
        "movies, actors, and directors, and recommend movies based on the user's "
        "preferences. Be detailed and use examples to support recommendations.",
    ),
    "text_improver": (
        "📝 Text Improver",
        "As an advanced chatbot Text Improver Assistant, correct spelling, fix "
        "mistakes, and improve text the user sends without changing its meaning. "
        "Reply using this exact structure (keep the HTML tags):\n"
        "<b>ᴇᴅɪᴛᴇᴅ ᴛᴇxᴛ:</b>\n{EDITED TEXT}\n\n<b>ᴄᴏʀʀᴇᴄᴛɪᴏɴ:</b>\n{NUMBERED LIST OF CORRECTIONS}",
    ),
    "startup_idea_generator": (
        "💡 Startup Idea Generator",
        "You're an advanced chatbot Startup Idea Generator. Help the user brainstorm "
        "innovative, viable startup ideas based on market trends, their interests, "
        "and growth potential.",
    ),
    "accountant": (
        "🧮 Accountant",
        "You're an advanced chatbot Accountant Assistant. Help with accounting and "
        "financial questions, tax and budgeting advice, and financial planning. "
        "Prioritize accurate, current information and flag when something needs a "
        "licensed professional.",
    ),
    "artist": (
        "👩‍🎨 Artist",
        "",  # special-cased below — Artist mode generates an image instead of text
    ),
}
DEFAULT_MODE = "assistant"


def _history_to_messages(history: list, prompt: str, mode: str, image_b64: str = None):
    system_prompt = CHAT_MODES.get(mode, CHAT_MODES[DEFAULT_MODE])[1] or CHAT_MODES[DEFAULT_MODE][1]
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history:
        role = "user" if turn.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": turn.get("text", "")})

    if image_b64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt or "Describe this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"}},
            ],
        })
    else:
        messages.append({"role": "user", "content": prompt})
    return messages


async def _stream_gpt(history: list, prompt: str, model: str, mode: str, image_b64: str = None):
    """Async generator yielding the growing answer text as it streams in,
    then a final (answer, input_tokens, output_tokens) tuple. OpenRouter
    doesn't send token usage on streamed chunks, so tokens are estimated
    (~4 chars/token) for cost tracking — good enough for /gptusage."""
    stream = await _client.chat.completions.create(
        model=model,
        messages=_history_to_messages(history, prompt, mode, image_b64),
        temperature=0.7,
        max_tokens=1000,
        stream=True,
    )
    answer = ""
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            answer += delta.content
            yield "chunk", answer

    prompt_chars = sum(len(str(m.get("content", ""))) for m in _history_to_messages(history, prompt, mode))
    in_tok = max(1, prompt_chars // 4)
    out_tok = max(1, len(answer) // 4)
    yield "done", (answer, in_tok, out_tok)


def _estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    in_price, out_price = PRICING.get(model, (0.0, 0.0))
    return (in_tok / 1000) * in_price + (out_tok / 1000) * out_price


async def _reply_with_gpt(client: Client, message: Message, prompt: str, image_b64: str = None):
    if AsyncOpenAI is None:
        return await message.reply_text(
            f"<b>{E_CROSS} openai package not installed.</b>\n"
            f"<i>Ask an admin to run /install gpt.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    if _client is None:
        return await message.reply_text(
            f"<b>{E_CROSS} GPT chat isn't configured.</b>\n"
            f"<i>Set OPENROUTER_API_KEY (get one with credits at "
            f"https://openrouter.ai/keys) and restart the bot.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(f"<b>{E_ROCKET} Thinking...</b>", parse_mode=enums.ParseMode.HTML)
    user_id = message.from_user.id
    history = await db.get_gpt_history(user_id)
    model = await db.get_gpt_model(user_id) or OPENROUTER_MODEL
    mode = await db.get_gpt_mode(user_id) or DEFAULT_MODE

    # Artist mode redirects text prompts straight to image generation,
    # matching the original repo's behaviour.
    if mode == "artist" and not image_b64:
        return await _generate_image(client, message, prompt, status=status)

    answer = None
    in_tok = out_tok = 0
    last_edit = 0.0
    try:
        async for kind, payload in _stream_gpt(history, prompt, model, mode, image_b64):
            if kind == "chunk":
                now = time.monotonic()
                if now - last_edit >= 1.0:  # throttle to ~1 edit/sec, Telegram's comfortable rate
                    last_edit = now
                    try:
                        await safe_edit(status.edit_text, payload[:4000] + " ▍")
                    except Exception:
                        pass  # message-not-modified or flood-wait — just skip this tick
            else:  # "done"
                answer, in_tok, out_tok = payload
        if not answer:
            raise ValueError("Empty response from model.")
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Request failed:</b>\n<code>{e}</code>\n"
            f"<i>Start a new conversation with /resetgpt, or switch models with /gptmodel.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.append_gpt_history(user_id, prompt, answer)
    await db.add_gpt_usage(user_id, in_tok, out_tok, _estimate_cost(model, in_tok, out_tok))

    # Telegram caps a single message at 4096 chars — split long answers.
    # Markdown parse_mode here (not on the interim streaming edits above)
    # so fenced code blocks render as proper monospaced code — doing this
    # only on the complete answer avoids "invalid markdown entity" errors
    # from an unclosed ``` mid-stream.
    chunks = [answer[i:i + 4000] for i in range(0, len(answer), 4000)] or [answer]
    try:
        await safe_edit(status.edit_text, chunks[0], parse_mode=enums.ParseMode.MARKDOWN)
    except Exception:
        await safe_edit(status.edit_text, chunks[0])  # fall back to plain text if the model's output isn't valid Markdown
    for chunk in chunks[1:]:
        try:
            await message.reply_text(chunk, parse_mode=enums.ParseMode.MARKDOWN)
        except Exception:
            await message.reply_text(chunk)


async def _reply_with_vision(client: Client, message: Message, photo_message: Message, caption_prompt: str):
    """Vision entry point: triggered by sending a photo with /gpt as the
    caption, or replying to a photo with /gpt. (Not a blanket photo
    handler — that would collide with other plugins' own photo flows,
    e.g. Akbots/imgtolink.py's pending-input handler.)"""
    status = await message.reply_text(f"<b>{E_ROCKET} Looking at the image...</b>", parse_mode=enums.ParseMode.HTML)
    photo_path = None
    try:
        photo_path = await photo_message.download(progress=make_download_progress(status, file_name="image"))
        with open(photo_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't read that image:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
    finally:
        if photo_path and os.path.exists(photo_path):
            os.remove(photo_path)

    prompt = caption_prompt or photo_message.caption or "Describe this image."
    await status.delete()
    await _reply_with_gpt(client, message, prompt, image_b64=image_b64)


@Client.on_message(filters.command(["gpt", "chatgpt"]) & CHAT_SCOPE)
async def gpt_command(client: Client, message: Message):
    if not _is_allowed(message.from_user.id):
        return await _deny(message)

    prompt = None
    if len(message.command) > 1:
        prompt = (message.text or message.caption).split(None, 1)[1]

    target = message if message.photo else (message.reply_to_message if message.reply_to_message else None)
    if target and target.photo:
        return await _reply_with_vision(client, message, target, prompt)

    if not prompt and message.reply_to_message and message.reply_to_message.text:
        prompt = message.reply_to_message.text

    if not prompt:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/gpt &lt;message&gt;</code>\n"
            f"<i>Or reply to a text message with /gpt, send a photo with /gpt as "
            f"the caption (vision), or just send a voice note in private chat. "
            f"Remembers your last 12 exchanges — /resetgpt to start fresh, "
            f"/gptmodel to switch models, /gptmode to switch persona, /imagine to "
            f"generate an image, /gptusage to see your spend.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _reply_with_gpt(client, message, prompt)


@Client.on_message(filters.command(["resetgpt", "cleargpt"]) & CHAT_SCOPE)
async def reset_gpt_command(client: Client, message: Message):
    await db.clear_gpt_history(message.from_user.id)
    await message.reply_text(f"<b>{E_CHECK} GPT chat history cleared.</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("gptmodel") & CHAT_SCOPE)
async def gpt_model_command(client: Client, message: Message):
    current = await db.get_gpt_model(message.from_user.id) or OPENROUTER_MODEL
    buttons = [
        [InlineKeyboardButton(
            f"{'✅ ' if slug == current else ''}{label}", callback_data=f"gptmodel_{slug}",
            style=(_BS.PRIMARY if slug == current else _BS.PRIMARY) if _BS else None
        )]
        for label, slug in MODELS.items()
    ]
    await message.reply_text(
        f"<b>{E_GEAR} Select a model:</b>\n<i>Current: <code>{current}</code></i>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^gptmodel_"))
async def gpt_model_callback(client: Client, cq: CallbackQuery):
    model = cq.data.split("_", 1)[1]
    if model not in MODEL_SLUGS:
        return await cq.answer("Unknown model.", show_alert=True)
    await db.set_gpt_model(cq.from_user.id, model)
    await cq.answer(f"Model set to {model}")
    await safe_edit(cq.edit_message_text, 
        f"<b>{E_CHECK} Model updated:</b> <code>{model}</code>\n<i>Use /resetgpt to refresh the conversation.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("gptmode") & CHAT_SCOPE)
async def gpt_mode_command(client: Client, message: Message):
    current = await db.get_gpt_mode(message.from_user.id) or DEFAULT_MODE
    buttons = [
        [InlineKeyboardButton(
            f"{'✅ ' if key == current else ''}{label}", callback_data=f"gptmode_{key}",
            style=(_BS.PRIMARY if key == current else _BS.PRIMARY) if _BS else None
        )]
        for key, (label, _prompt) in CHAT_MODES.items()
    ]
    await message.reply_text(
        f"<b>{E_GEAR} Select a persona:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^gptmode_"))
async def gpt_mode_callback(client: Client, cq: CallbackQuery):
    mode = cq.data.split("_", 1)[1]
    if mode not in CHAT_MODES:
        return await cq.answer("Unknown mode.", show_alert=True)
    await db.set_gpt_mode(cq.from_user.id, mode)
    label = CHAT_MODES[mode][0]
    await cq.answer(f"Persona set to {label}")
    await safe_edit(cq.edit_message_text, 
        f"<b>{E_CHECK} Persona updated:</b> {label}\n<i>Use /resetgpt to refresh the conversation.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


# ------------------------------------------------------------------
# /imagine — image generation (gpt-image-1). Needs a *direct* OpenAI key
# (OPENAI_API_KEY) since OpenRouter doesn't proxy the images endpoint.
# Also reused by Artist chat mode (see _reply_with_gpt above).
# ------------------------------------------------------------------
async def _generate_image(client: Client, message: Message, prompt: str, status: Message = None):
    if _direct_client is None:
        text = (
            f"<b>{E_CROSS} Image generation isn't configured.</b>\n"
            f"<i>This needs a direct OpenAI key (OPENROUTER_API_KEY doesn't cover "
            f"image generation) — set OPENAI_API_KEY and restart the bot.</i>"
        )
        if status:
            return await safe_edit(status.edit_text, text, parse_mode=enums.ParseMode.HTML)
        return await message.reply_text(text, parse_mode=enums.ParseMode.HTML)

    if status is None:
        status = await message.reply_text(f"<b>{E_ROCKET} Generating image...</b>", parse_mode=enums.ParseMode.HTML)
    else:
        await safe_edit(status.edit_text, f"<b>{E_ROCKET} Generating image...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        resp = await _direct_client.images.generate(
            model="gpt-image-1", prompt=prompt, n=1, size="1024x1024"
        )
        image_bytes = base64.b64decode(resp.data[0].b64_json)
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Image generation failed:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    await db.add_gpt_usage(message.from_user.id, 0, 0, IMAGE_COST_PER_IMAGE)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        await message.reply_photo(tmp_path, caption=f"<blockquote><b>🎨 {prompt[:900]}</b></blockquote>", parse_mode=enums.ParseMode.HTML)
        await status.delete()
    finally:
        os.remove(tmp_path)


@Client.on_message(filters.command(["imagine", "image"]) & CHAT_SCOPE)
async def imagine_command(client: Client, message: Message):
    if not _is_allowed(message.from_user.id):
        return await _deny(message)
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/imagine &lt;description&gt;</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    await _generate_image(client, message, message.text.split(None, 1)[1])


# ------------------------------------------------------------------
# Voice message auto-transcription (Whisper) — private chats only, so
# the bot doesn't listen in on every voice note sent in a group. Reply
# comes from the same /gpt pipeline, using the transcript as the prompt.
# Needs OPENAI_API_KEY (direct) — OpenRouter doesn't proxy audio.
# ------------------------------------------------------------------
@Client.on_message(filters.voice & filters.private, group=5)
async def voice_to_gpt(client: Client, message: Message):
    if _direct_client is None:
        return  # silently skip — don't nag every voice note if this isn't set up
    if not _is_allowed(message.from_user.id):
        return  # silent, same as above — don't nag unauthorized users on every voice note
    status = await message.reply_text(f"<b>{E_ROCKET} Transcribing...</b>", parse_mode=enums.ParseMode.HTML)
    ogg_path = None
    try:
        ogg_path = await message.download(progress=make_download_progress(status, file_name="voice note"))
        with open(ogg_path, "rb") as f:
            transcript = await _direct_client.audio.transcriptions.create(model="whisper-1", file=f)
        text = (transcript.text or "").strip()
    except Exception as e:
        return await safe_edit(status.edit_text, 
            f"<b>{E_CROSS} Transcription failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
        )
    finally:
        if ogg_path and os.path.exists(ogg_path):
            os.remove(ogg_path)

    minutes = (message.voice.duration or 0) / 60
    await db.add_gpt_usage(message.from_user.id, 0, 0, minutes * WHISPER_COST_PER_MINUTE)

    if not text:
        return await safe_edit(status.edit_text, f"<b>{E_CROSS} Couldn't make out any speech in that voice note.</b>", parse_mode=enums.ParseMode.HTML)

    await safe_edit(status.edit_text, f"<b>🎙 ʜᴇᴀʀᴅ:</b> <i>{text}</i>", parse_mode=enums.ParseMode.HTML)
    await _reply_with_gpt(client, message, text)


# ------------------------------------------------------------------
# Usage/cost tracking — /gptusage (self, any user), /gptstats (admins).
# Costs are estimates computed from each provider's published per-token
# pricing; OpenRouter's own dashboard is the source of truth for billing.
# ------------------------------------------------------------------
@Client.on_message(filters.command("gptusage") & filters.private)
async def gpt_usage_command(client: Client, message: Message):
    usage = await db.get_gpt_usage(message.from_user.id)
    await message.reply_text(
        f"<b>{E_INFO} Your GPT usage:</b>\n"
        f"Input tokens: <code>{usage.get('input_tokens', 0):,}</code>\n"
        f"Output tokens: <code>{usage.get('output_tokens', 0):,}</code>\n"
        f"Estimated cost: <code>${usage.get('cost', 0):.4f}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("gptstats") & filters.private & filters.user(ADMINS))
async def gpt_stats_command(client: Client, message: Message):
    stats = await db.get_all_gpt_usage()
    lines = [
        f"<b>{E_GEAR} GPT usage — all users</b>",
        f"Total input tokens: <code>{stats['total_input_tokens']:,}</code>",
        f"Total output tokens: <code>{stats['total_output_tokens']:,}</code>",
        f"Total estimated cost: <code>${stats['total_cost']:.4f}</code>",
        "",
        "<b>ᴛᴏᴘ sᴘᴇɴᴅᴇʀs:</b>",
    ]
    for row in stats["users"][:15]:
        lines.append(f"<code>{row['user_id']}</code> — ${row['cost']:.4f}")
    if not stats["users"]:
        lines.append("<i>No usage recorded yet.</i>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("help_group_chat") & CHAT_SCOPE)
async def help_group_chat_command(client: Client, message: Message):
    await message.reply_text(
        f"<b>{E_INFO} Using /gpt in a group</b>\n\n"
        f"No special setup needed — unlike the original bot (which required "
        f"@mentioning it or replying to its messages), this port responds to "
        f"plain slash-commands anywhere the bot is a member:\n\n"
        f"• <code>/gpt &lt;message&gt;</code> — ask something\n"
        f"• Reply to a text message with <code>/gpt</code> to discuss it\n"
        f"• <code>/imagine &lt;description&gt;</code> — generate an image\n"
        f"• Send a photo with <code>/gpt</code> as the caption for vision\n\n"
        f"<i>Conversation history, model, and persona are per-user, so each "
        f"member's /gpt conversation in the group is separate from the "
        f"others'.</i>\n\n"
        f"If GPT_ALLOWED_USERS is set on the bot, only those IDs (plus admins) "
        f"can use these commands here too.",
        parse_mode=enums.ParseMode.HTML,
    )

