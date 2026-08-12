# Akbots - Don't Remove Credit - @AkBots_Official
#
# /summarize — dedicated document-intelligence agent, ported from AiBoT
# (github.com/Suryanshu-Nabheet/AiBoT)'s Summarizer Agent
# (app/api/agent/summarize/route.ts, docs/features.md "Document
# Intelligence"). Distinct from /aiulta's generic single-document reading:
# this one is built specifically for research-grade, long-form synthesis
# across MULTIPLE documents at once, with a PDF export of the result —
# exactly AiBoT's "Multi-Format Extraction / Batch Processing / PDF
# Export" trio.
#
# Multi-document input: send 2+ files together as a Telegram album (select
# multiple in the file picker, they arrive as one media group) with
# "/summarize <optional task>" as the caption on one of them — or reply to
# an album/single file with /summarize. Supports PDF, TXT, MD, DOCX, JSON.
#
# Output: a long, detailed text reply (chunked to fit Telegram's 4096-char
# limit) AND the same result exported as a proper PDF document (using the
# Mukta.ttf font already bundled in Akbots/fonts/ for Hindi/Devanagari
# support), matching AiBoT's "PDF Export" feature.

import json
import os
import tempfile

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import OPENROUTER_API_KEY, OPENROUTER_SITE_URL, OPENROUTER_SITE_NAME, OPENROUTER_MODEL, ADMINS, GPT_ALLOWED_USERS
from logger import LOGGER
from Akbots.direct_utils import safe_edit, make_download_progress

logger = LOGGER(__name__)

E_CHECK  = '<tg-emoji emoji-id="5206607081334906820">✔️</tg-emoji>'
E_CROSS  = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'
E_GEAR   = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'

SUMMARIZE_SCOPE = filters.private | filters.group
AGENT_MODEL = OPENROUTER_MODEL or "openai/gpt-4o-mini"
FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "Mukta.ttf")

SUMMARIZER_SYSTEM_PROMPT = (
    "You are a research-grade document analyst. You'll be given the "
    "extracted text of one or more documents (each labelled with its "
    "filename) plus a task describing what the user wants. Produce a "
    "thorough, detailed, well-organized synthesis — not a shallow "
    "one-paragraph summary. When there are multiple documents, actively "
    "connect and compare information across them rather than summarizing "
    "each in isolation. Use plain paragraph prose with occasional short "
    "section labels (no markdown # headers, no HTML) since this will be "
    "sent as plain text and exported to PDF. Match the language style "
    "(English/Hindi/Hinglish) of the task instruction if one was given."
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


async def _extract_document_text(path: str, max_chars: int = 15000) -> str:
    """Same extraction convention as Akbots/aiulta.py's helper, plus JSON
    support (AiBoT's Summarizer explicitly lists JSON as a supported
    format the Telegram-side aiulta reader doesn't handle)."""
    lower = path.lower()
    try:
        if lower.endswith(".pdf"):
            import fitz
            doc = fitz.open(path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
        elif lower.endswith((".txt", ".md")):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        elif lower.endswith(".json"):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = json.dumps(json.load(f), indent=2, ensure_ascii=False)
        elif lower.endswith(".docx"):
            try:
                import docx
            except ImportError:
                return "[couldn't read .docx: python-docx isn't installed]"
            d = docx.Document(path)
            text = "\n".join(p.text for p in d.paragraphs)
        else:
            return f"[unsupported document type: {os.path.splitext(path)[1]}]"
    except Exception as e:
        return f"[couldn't extract text: {e}]"
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text or "[document had no extractable text]"


def _build_pdf(title: str, body: str) -> str | None:
    """Renders the synthesis to a PDF, using Mukta.ttf for Hindi/Unicode
    support if available. Returns a temp file path, or None on failure
    (PDF export is a bonus, never blocks the text reply)."""
    try:
        from fpdf import FPDF
    except ImportError:
        logger.warning("summarizer: fpdf2 not installed, skipping PDF export")
        return None
    try:
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        font_name = "Helvetica"
        if os.path.exists(FONT_PATH):
            try:
                pdf.add_font("Mukta", "", FONT_PATH)
                font_name = "Mukta"
            except Exception as e:
                logger.warning(f"summarizer: couldn't load Mukta.ttf, falling back to Helvetica: {e}")
        pdf.set_font(font_name, size=14)
        pdf.multi_cell(0, 10, title)
        pdf.ln(2)
        pdf.set_font(font_name, size=11)
        for line in body.split("\n"):
            pdf.multi_cell(0, 7, line if line.strip() else " ")
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        pdf.output(path)
        return path
    except Exception as e:
        logger.error(f"summarizer: PDF build failed: {e}")
        return None


async def _collect_doc_messages(client: Client, message: Message, reply: Message | None):
    """Gathers every document in this message's/reply's Telegram album
    (media group) plus a lone document if not part of an album."""
    sources = []
    for m in (message, reply):
        if not m:
            continue
        if m.media_group_id:
            try:
                sources.extend(await client.get_media_group(m.chat.id, m.id))
            except Exception as e:
                logger.warning(f"summarizer: get_media_group failed: {e}")
                if m.document:
                    sources.append(m)
        elif m.document:
            sources.append(m)

    seen_ids, doc_msgs = set(), []
    for m in sources:
        if m.document and m.id not in seen_ids:
            seen_ids.add(m.id)
            doc_msgs.append(m)
    return doc_msgs


def _chunk_text(text: str, limit: int = 3800):
    chunks, current = [], ""
    for para in text.split("\n"):
        if len(current) + len(para) + 1 > limit:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks or [text]


@Client.on_message(filters.command(["summarize", "summarise"]) & SUMMARIZE_SCOPE)
async def summarize_command(client: Client, message: Message):
    if _client is None:
        await message.reply_text(
            f"<b>{E_CROSS} Summarizer isn't configured.</b>\n<i>Set OPENROUTER_API_KEY to enable /summarize.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not message.from_user or not _is_allowed(message.from_user.id):
        await message.reply_text(f"<b>{E_CROSS} You're not authorized to use this.</b>", parse_mode=enums.ParseMode.HTML)
        return

    task = ""
    if len(message.command) > 1:
        task = message.text.split(None, 1)[1].strip()
    elif message.caption and len(message.caption.split(None, 1)) > 1:
        task = message.caption.split(None, 1)[1].strip()
    task = task or "Provide a thorough, detailed summary and synthesis of the following document(s)."

    reply = message.reply_to_message
    doc_msgs = await _collect_doc_messages(client, message, reply)

    if not doc_msgs:
        await message.reply_text(
            f"<b>{E_GEAR} Usage:</b>\n"
            f"Ek ya zyada documents (PDF/TXT/MD/DOCX/JSON) bhejo — akele ya\n"
            f"multiple ek saath album/multi-select se — caption ya reply mein\n"
            f"<code>/summarize &lt;optional task&gt;</code>.\n"
            f"<i>e.g. select 3 PDFs together -&gt; caption \"/summarize compare these\"</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    status = await message.reply_text(
        f"<b>{E_GEAR} {len(doc_msgs)} document(s) padh raha hoon...</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    parts = []
    for doc_msg in doc_msgs:
        fname = doc_msg.document.file_name or "document"
        if not fname.lower().endswith((".pdf", ".txt", ".md", ".docx", ".json")):
            parts.append(f"=== {fname} ===\n[skipped: unsupported type]")
            continue
        path = None
        try:
            path = await doc_msg.download(progress=make_download_progress(status, file_name=fname))
            text = await _extract_document_text(path)
        finally:
            if path and os.path.exists(path):
                os.remove(path)
        parts.append(f"=== {fname} ===\n{text}")

    combined = "\n\n".join(parts)
    await safe_edit(status.edit_text, f"<b>{E_GEAR} Synthesize kar raha hoon...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        resp = await _client.chat.completions.create(
            model=AGENT_MODEL,
            messages=[
                {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Task: {task}\n\n{combined}"},
            ],
            max_tokens=4000,
            temperature=0.3,
        )
        result = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.exception("summarizer: LLM call failed")
        await safe_edit(status.edit_text, f"<b>{E_CROSS} AI se baat nahi ho paayi.</b>\n<i>{e}</i>", parse_mode=enums.ParseMode.HTML)
        return

    if not result:
        await safe_edit(status.edit_text, f"<b>{E_CROSS} Khaali jawab aaya, dobara try karo.</b>", parse_mode=enums.ParseMode.HTML)
        return

    await status.delete()
    chunks = _chunk_text(result)
    for i, chunk in enumerate(chunks):
        prefix = f"<b>{E_CHECK} Summary</b> ({i + 1}/{len(chunks)})\n\n" if i == 0 else ""
        try:
            await message.reply_text(f"{prefix}{chunk}", parse_mode=enums.ParseMode.HTML)
        except Exception:
            await message.reply_text(chunk)

    pdf_path = _build_pdf("Summary", result)
    if pdf_path:
        try:
            await message.reply_document(pdf_path, caption=f"<b>{E_CHECK} PDF export</b>", parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            logger.warning(f"summarizer: couldn't send PDF: {e}")
        finally:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
