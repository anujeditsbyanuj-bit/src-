# Akbots - Don't Remove Credit - @AkBots_Official
#
# /enhance — prompt engineer, ported from AiBoT
# (github.com/Suryanshu-Nabheet/AiBoT)'s standalone /api/enhance route: you
# give it a rough, vague prompt and it rewrites it into a clearer, more
# specific, more effective one — for use with /gpt, /aiulta, /coach, or
# anywhere else. Uses the same OPENROUTER_API_KEY as the rest of the bot's
# AI commands.
#
# Usage: /enhance <rough prompt>

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import OPENROUTER_API_KEY, OPENROUTER_SITE_URL, OPENROUTER_SITE_NAME, ADMINS, GPT_ALLOWED_USERS
from logger import LOGGER
from Akbots.direct_utils import safe_edit

logger = LOGGER(__name__)

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_GEAR   = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

ENHANCE_SCOPE = filters.private | filters.group
ENHANCE_MODEL = "openai/gpt-4o-mini"  # fast/cheap is plenty for prompt rewriting

PROMPT_ENGINEER_SYSTEM = (
    "You are an elite prompt engineer. Given a user's rough, vague, or "
    "underspecified prompt, rewrite it into a clear, specific, well-"
    "structured prompt that will get a much better result from an AI "
    "model.\n\n"
    "Do this by: naming the concrete task and desired output format, "
    "filling in reasonable implied context (audience, tone, scope, "
    "length) instead of leaving it vague, and removing ambiguity — but "
    "never invent facts or requirements the user didn't imply.\n\n"
    "Reply with ONLY the rewritten prompt, nothing else — no preamble, "
    "no explanation, no markdown code fences, no 'Here's your enhanced "
    "prompt:'. If the input is in Hindi/Hinglish, keep the rewritten "
    "prompt in the same language style."
)

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


@Client.on_message(filters.command(["enhance", "enhanceprompt"]) & ENHANCE_SCOPE)
async def enhance_command(client: Client, message: Message):
    if _client is None:
        await message.reply_text(
            f"<b>{E_CROSS} Enhancer isn't configured.</b>\n<i>Set OPENROUTER_API_KEY to enable /enhance.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not message.from_user or not _is_allowed(message.from_user.id):
        await message.reply_text(f"<b>{E_CROSS} You're not authorized to use this.</b>", parse_mode=enums.ParseMode.HTML)
        return

    raw = ""
    if len(message.command) > 1:
        raw = message.text.split(None, 1)[1].strip()
    elif message.reply_to_message and message.reply_to_message.text:
        raw = message.reply_to_message.text.strip()

    if not raw:
        await message.reply_text(
            f"<b>{E_GEAR} Usage:</b> <code>/enhance &lt;rough prompt&gt;</code>\n"
            f"<i>Ya kisi text message pe reply karke /enhance bhejo.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    status = await message.reply_text(f"<b>{E_GEAR} Prompt improve kar raha hoon...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        resp = await _client.chat.completions.create(
            model=ENHANCE_MODEL,
            messages=[
                {"role": "system", "content": PROMPT_ENGINEER_SYSTEM},
                {"role": "user", "content": raw},
            ],
            max_tokens=800,
            temperature=0.4,
        )
        enhanced = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.exception("enhance: LLM call failed")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} AI se baat nahi ho paayi.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        return

    if not enhanced:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Khaali jawab aaya, dobara try karo.</b>", parse_mode=enums.ParseMode.HTML)
        return

    reply_text = f"<b>{E_CHECK} Enhanced prompt:</b>\n\n<code>{enhanced}</code>"
    try:
        await safe_edit(status.edit_text, reply_text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        await safe_edit(status.edit_text, enhanced)
