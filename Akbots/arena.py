# Akbots - Don't Remove Credit - @AkBots_Official
#
# /arena — side-by-side model comparison, ported from AiBoT
# (github.com/Suryanshu-Nabheet/AiBoT)'s "Arena" chat mode
# (components/chat/arena-interface.tsx), where the same prompt is sent to
# two LLMs at once so you can compare their answers.
#
# Telegram can't show two panes side by side like a web UI, so both
# answers are sent back in one message, clearly labelled, one after the
# other. Reuses the same OPENROUTER_API_KEY / MODELS dict already wired up
# in Akbots/openai_chat.py — no new config needed.
#
# Usage:
#   /arena <prompt>                        -> default pair (GPT-4o mini vs Claude Sonnet)
#   /arena <model1> vs <model2> :: <prompt> -> pick any two labels from /arenamodels
#   /arenamodels                            -> list available model labels

import asyncio

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import OPENROUTER_API_KEY, OPENROUTER_SITE_URL, OPENROUTER_SITE_NAME, ADMINS, GPT_ALLOWED_USERS
from logger import LOGGER
from Akbots.direct_utils import safe_edit
from Akbots.openai_chat import MODELS

logger = LOGGER(__name__)

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_GEAR   = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

ARENA_SCOPE = filters.private | filters.group

DEFAULT_PAIR = ("GPT-4o mini", "Claude Sonnet")

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


def _is_allowed(user_id: int) -> bool:
    if not GPT_ALLOWED_USERS:
        return True
    return user_id in GPT_ALLOWED_USERS or user_id in ADMINS


def _resolve_label(text: str) -> str | None:
    """Loose match a user-typed label against MODELS' keys, e.g. 'gpt4o',
    'claude', 'sonnet' all resolve sensibly."""
    text = text.strip().lower()
    for label in MODELS:
        if label.lower() == text:
            return label
    for label in MODELS:
        if text in label.lower().replace(" ", "").replace("-", ""):
            return label
    return None


def _parse_args(raw: str):
    """Returns (model_label_1, model_label_2, prompt). Falls back to
    DEFAULT_PAIR if no 'X vs Y ::' prefix is given."""
    if "::" in raw and " vs " in raw.split("::", 1)[0].lower():
        head, prompt = raw.split("::", 1)
        left, right = head.lower().split(" vs ", 1)
        l1, l2 = _resolve_label(left), _resolve_label(right)
        if l1 and l2:
            return l1, l2, prompt.strip()
    return DEFAULT_PAIR[0], DEFAULT_PAIR[1], raw.strip()


async def _ask(model_slug: str, prompt: str) -> str:
    try:
        resp = await _client.chat.completions.create(
            model=model_slug,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.7,
        )
        return (resp.choices[0].message.content or "").strip() or "(khaali jawab aaya)"
    except Exception as e:
        logger.error(f"arena: {model_slug} failed: {e}")
        return f"⚠️ error: {e}"


@Client.on_message(filters.command("arenamodels") & ARENA_SCOPE)
async def arena_models_command(client: Client, message: Message):
    lines = "\n".join(f"• <b>{label}</b>" for label in MODELS)
    await message.reply_text(
        f"<b>{E_GEAR} Arena mein available models:</b>\n{lines}\n\n"
        f"<i>Usage: /arena claude vs gpt4o :: kaun sa language seekhna chahiye?</i>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command(["arena", "compare"]) & ARENA_SCOPE)
async def arena_command(client: Client, message: Message):
    if _client is None:
        await message.reply_text(
            f"<b>{E_CROSS} Arena isn't configured.</b>\n<i>Set OPENROUTER_API_KEY to enable /arena.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not message.from_user or not _is_allowed(message.from_user.id):
        await message.reply_text(
            f"<b>{E_CROSS} You're not authorized to use this.</b>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if len(message.command) < 2:
        await message.reply_text(
            f"<b>{E_GEAR} Usage:</b>\n"
            f"<code>/arena &lt;prompt&gt;</code> — default: {DEFAULT_PAIR[0]} vs {DEFAULT_PAIR[1]}\n"
            f"<code>/arena claude vs gpt4o :: &lt;prompt&gt;</code> — pick your own pair\n"
            f"<i>/arenamodels to see all labels.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    raw = message.text.split(None, 1)[1]
    label1, label2, prompt = _parse_args(raw)
    if not prompt:
        await message.reply_text(f"<b>{E_CROSS} Prompt khaali hai.</b>", parse_mode=enums.ParseMode.HTML)
        return

    status = await message.reply_text(
        f"<b>{E_GEAR} Dono models se poochh raha hoon:</b> <i>{label1}</i> vs <i>{label2}</i>...",
        parse_mode=enums.ParseMode.HTML,
    )

    answer1, answer2 = await asyncio.gather(
        _ask(MODELS[label1], prompt),
        _ask(MODELS[label2], prompt),
    )

    def _clip(text: str, limit: int = 1500) -> str:
        return text if len(text) <= limit else text[:limit] + "\n...[truncated]"

    reply_text = (
        f"<b>{E_CHECK} Arena result</b> — <i>{prompt[:120]}</i>\n\n"
        f"<b>🅰️ {label1}</b>\n{_clip(answer1)}\n\n"
        f"<b>🅱️ {label2}</b>\n{_clip(answer2)}"
    )
    try:
        await safe_edit(status.edit_text, reply_text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await safe_edit(status.edit_text, reply_text)
