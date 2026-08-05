# Akbots - Don't Remove Credit - @AkBots_Official
#
# /aiulta — natural-language action agent. Where /gpt, /gemini, /groq and
# /chat only *talk*, /aiulta *does* things: it reads your instruction in
# plain Hindi/English, decides which of the bot's ~380 existing commands
# (terabox, ytdl, forward, broadcast, gdrive, rename, thumbnail, ...) fit,
# and runs them for you — e.g. "is terabox link ko download karke gdrive
# pe daal do" actually calls /terabox then /gdrive with the right args.
#
# Modeled after AiBoT (github.com/Suryanshu-Nabheet/AiBoT)'s multi-LLM
# orchestration idea, but ported into a single Pyrogram plugin instead of a
# separate Next.js service — everything AiBoT's "Coder/Chat agent" gives
# you (natural-language -> action, via OpenRouter's 20+ models) now lives
# right here as one more Akbots command, reusing the same OPENROUTER_API_KEY
# already wired up in openai_chat.py.
#
# How it works (OpenAI-style tool-calling loop):
#   1. LLM gets your instruction + tools: search_commands()/run_command()
#      to find and trigger existing bot commands, generate_code() to write
#      (never execute) a whole project as a zip, and speak() to reply by
#      voice instead of text.
#   2. Every run_command() call goes through the *same* permission checks
#      the real command already has (admin-only stays admin-only, private-
#      only stays private-only) — aiulta is a dispatcher, not a bypass.
#   3. /eval, /shell, /sh and other raw-code-execution commands are
#      hard-excluded from the registry (see aiulta_commands.py) — never
#      reachable from here, even for admins, since an LLM tool call is the
#      wrong place for arbitrary code execution.
#   4. Loop continues (search -> run -> search -> run ...) up to
#      MAX_STEPS times, then the model's final plain-text reply is sent
#      back to you.
#
# Command registry (Akbots/aiulta_commands.py) is a static, pre-generated
# scan of every plugin's @Client.on_message(filters.command(...)) handler —
# see that file's header for how to regenerate it after adding plugins.
#
# Multimodal input (mirroring AiBoT's other agents, all through this same
# /aiulta command instead of separate ones):
#   - Photo (sent with /aiulta as caption, or replied to)  -> vision, same
#     image_url content-block pattern as Akbots/openai_chat.py's /gpt.
#   - PDF/TXT/MD document reply -> text extracted (pymupdf for PDFs, same
#     lib Akbots/dub.py already uses) and given to the model as context —
#     "document intelligence" without a separate command.
#   - Voice note (sent or replied to) -> transcribed with Whisper (direct
#     OpenAI client, needs OPENAI_API_KEY — same requirement /gpt's voice
#     auto-transcription already has) and used as the instruction text.
#   - A "speak" tool the model can call to send its final answer back as a
#     voice note (OpenAI TTS, also needs OPENAI_API_KEY) instead of text.
#
# Deliberately NOT ported from AiBoT: live code *execution* / an
# "Integrated Preview" sandbox. generate_code() below writes files the same
# way AiBoT's Coder Agent does — it just never runs what it writes. An LLM
# tool call is never a safe place to execute arbitrary code, even for
# admins, since a crafted instruction (or forwarded text the agent is
# asked to act on) could trick the model into running something
# destructive. Writing code to disk / a zip / a GitHub repo carries none of
# that risk — nothing on the bot's own server ever executes it.

import asyncio
import base64
import json
import copy
import os
import tempfile
import zipfile

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from config import (
    OPENROUTER_API_KEY, OPENROUTER_SITE_URL, OPENROUTER_SITE_NAME,
    OPENROUTER_MODEL, OPENAI_API_KEY, ADMINS, GPT_ALLOWED_USERS,
)
from logger import LOGGER
from Akbots.direct_utils import safe_edit, make_download_progress
from Akbots.aiulta_commands import COMMAND_INDEX, search_commands

logger = LOGGER(__name__)

E_CHECK  = '<emoji id=5206607081334906820>✔️</emoji>'
E_CROSS  = '<emoji id=5210952531676504517>❌</emoji>'
E_ROCKET = '<emoji id=5456140674028019486>🚀</emoji>'
E_INFO   = '<emoji id=5334544901428229844>ℹ️</emoji>'
E_GEAR   = '<emoji id=5341715473882955310>⚙️</emoji>'

AIULTA_SCOPE = filters.private | filters.group

MAX_STEPS = 6
PER_COMMAND_TIMEOUT = 300  # seconds — some plugins download large files

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

AGENT_MODEL = OPENROUTER_MODEL or "openai/gpt-4o-mini"
# Vision needs a model that actually accepts image_url content blocks —
# don't assume whatever OPENROUTER_MODEL is set to supports it.
VISION_MODEL = "openai/gpt-4o-mini"

# Whisper (voice -> text) and TTS (text -> voice) aren't proxied by
# OpenRouter, same limitation Akbots/openai_chat.py already documents for
# gpt-image-1 — need a *direct* OpenAI key for these two.
_direct_client = None
if AsyncOpenAI is not None and OPENAI_API_KEY:
    _direct_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = (
    "You are aiulta, the action-agent inside a Telegram bot called Akbots. "
    "The user gives you an instruction in Hindi/English/Hinglish describing "
    "something they want the bot to DO (download a video/file, forward or "
    "broadcast a message, rename something, fetch info, etc.) — not just a "
    "question to answer in chat. Sometimes instead of typed text you'll get "
    "a transcribed voice note, an image to look at, or document text pasted "
    "below the instruction — treat all of these the same as typed text.\n\n"
    "You have three tools:\n"
    "- search_commands(query): look up which of the bot's existing slash "
    "commands matches what the user wants. Call this first with a few "
    "different short queries if you're unsure which command fits.\n"
    "- run_command(command, args): actually invoke one of the bot's real "
    "commands with the given argument string, exactly like the user typing "
    "\"/command args\" themselves.\n"
    "- speak(text): send your answer back as a voice note instead of text — "
    "only use this if the user spoke to you by voice note or explicitly "
    "asked for audio.\n"
    "- generate_code(project_name, description): write a complete code "
    "project and send it as a .zip. Use this when asked to build/create/"
    "likho/banao code, a script, a bot, an app, etc. This only WRITES code, "
    "it never runs it. If the user also wants it on GitHub, call "
    "generate_code first, then use run_command with 'create_repo' and "
    "'uploadrepo' (search_commands for exact usage) to push the generated "
    "files — only do this if a GitHub repo was actually requested.\n\n"
    "Rules:\n"
    "- Only call run_command with a command name you found via "
    "search_commands (or one you're already certain exists in this bot).\n"
    "- If a command needs a link/file/text the user didn't give you, ask "
    "them for it in your final reply instead of guessing.\n"
    "- If run_command reports a permission or scope error, tell the user "
    "plainly why it didn't run (e.g. admin-only, or private-chat only) — "
    "don't retry it.\n"
    "- After you're done (or if nothing matches), reply with a short plain "
    "text summary of what you did or why you couldn't, in the same "
    "language style the user used. No markdown headers, Telegram-safe HTML "
    "only if needed (<b>, <i>, <code>)."
)

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_commands",
            "description": "Search the bot's existing commands by keyword to find which one performs a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Short keyword phrase, e.g. 'terabox download' or 'broadcast message'."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one of the bot's existing commands on behalf of the user, in the current chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command name without the leading slash, e.g. 'terabox'."},
                    "args": {"type": "string", "description": "Everything that would follow the command, e.g. a link or text. Empty string if none."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": "Send your final answer back to the user as a voice note instead of text. Only call this if the user asked for voice/audio, or spoke to you by voice note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "What to say, in plain sentences (no markdown/HTML)."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_code",
            "description": (
                "Write a complete code project (a script, a small app, a bot, a website — anything) "
                "from a natural-language description and send it back as a .zip file. This WRITES "
                "code only — it never runs, installs, or executes anything on this server. Use this "
                "when the user asks you to build/create/likho/banao code or a project, not to fix a "
                "running command."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string", "description": "Short filesystem-safe name, e.g. 'youtube-downloader-bot'."},
                    "description": {"type": "string", "description": "Full description of what to build, in as much detail as you have from the user."},
                },
                "required": ["project_name", "description"],
            },
        },
    },
]


def _is_allowed(user_id: int) -> bool:
    """Same access-control convention as /gpt: empty GPT_ALLOWED_USERS ->
    everyone can use it; otherwise only listed users + admins."""
    if not GPT_ALLOWED_USERS:
        return True
    return user_id in GPT_ALLOWED_USERS or user_id in ADMINS


async def _deny(message: Message):
    await message.reply_text(
        f"<b>{E_CROSS} You're not authorized to use this.</b>\n"
        f"<i>Ask a bot admin to add your ID to GPT_ALLOWED_USERS.</i>",
        parse_mode=enums.ParseMode.HTML,
    )


async def _extract_document_text(path: str, max_chars: int = 12000) -> str:
    """PDF -> pymupdf (already a dependency, see Akbots/dub.py). TXT/MD ->
    read directly. DOCX -> python-docx if installed, else a plain error
    telling the user why it couldn't be read."""
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
        elif lower.endswith(".docx"):
            try:
                import docx
            except ImportError:
                return "[couldn't read .docx: python-docx isn't installed — pip install python-docx]"
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


async def _transcribe_voice(path: str) -> str:
    if _direct_client is None:
        return ""
    try:
        with open(path, "rb") as f:
            transcript = await _direct_client.audio.transcriptions.create(model="whisper-1", file=f)
        return (transcript.text or "").strip()
    except Exception as e:
        logger.error(f"aiulta: transcription failed: {e}")
        return ""


async def _text_to_speech(text: str) -> str | None:
    """Returns a path to a generated mp3, or None if TTS isn't configured
    or fails."""
    if _direct_client is None:
        return None
    try:
        resp = await _direct_client.audio.speech.create(model="tts-1", voice="alloy", input=text[:4000])
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        resp.write_to_file(path)
        return path
    except Exception as e:
        logger.error(f"aiulta: TTS failed: {e}")
        return None


CODE_GEN_SYSTEM_PROMPT = (
    "You are a senior software engineer. Given a project description, output "
    "a complete, working codebase as STRICT JSON only — no markdown fences, "
    "no commentary before or after. Schema:\n"
    '{"files": [{"path": "relative/path.ext", "content": "full file text"}], '
    '"readme": "short setup/usage instructions as plain text"}\n'
    "Include every file needed to run the project (source files, "
    "requirements.txt/package.json, a README). Write real, complete code — "
    "no '...' placeholders, no TODOs standing in for actual logic. Keep "
    "individual files reasonably sized; split large projects into sensible "
    "modules rather than one giant file."
)


def _parse_code_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    return json.loads(raw)


async def _generate_code_project(description: str, project_name: str) -> dict:
    """Text-only code generation — writes files to a temp dir and zips them.
    Nothing here ever executes the generated code."""
    if _client is None:
        return {"ok": False, "error": "AI not configured"}
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in project_name).strip("-") or "project"
    try:
        resp = await _client.chat.completions.create(
            model=AGENT_MODEL,
            messages=[
                {"role": "system", "content": CODE_GEN_SYSTEM_PROMPT},
                {"role": "user", "content": description},
            ],
            max_tokens=8000,
            temperature=0.2,
        )
        raw = resp.choices[0].message.content or ""
        data = _parse_code_json(raw)
        files = data.get("files", [])
        if not files:
            return {"ok": False, "error": "model returned no files"}
    except Exception as e:
        logger.error(f"aiulta: code generation failed: {e}")
        return {"ok": False, "error": f"code generation failed: {e}"}

    workdir = tempfile.mkdtemp(prefix="aiulta_")
    project_dir = os.path.join(workdir, safe_name)
    os.makedirs(project_dir, exist_ok=True)
    written = []
    for f in files:
        rel_path = f.get("path", "").lstrip("/\\")
        if not rel_path or ".." in rel_path.split("/"):
            continue  # never write outside the project dir
        content = f.get("content", "")
        full_path = os.path.join(project_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as out:
            out.write(content)
        written.append(rel_path)

    readme = data.get("readme", "")
    if readme and "README.md" not in written:
        with open(os.path.join(project_dir, "README.md"), "w", encoding="utf-8") as out:
            out.write(readme)
        written.append("README.md")

    zip_path = os.path.join(workdir, f"{safe_name}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in written:
            zf.write(os.path.join(project_dir, rel_path), arcname=os.path.join(safe_name, rel_path))

    return {"ok": True, "zip_path": zip_path, "project_dir": project_dir, "files": written}


async def _run_command(client: Client, orig_message: Message, command: str, args: str) -> dict:
    """Dispatch to an existing plugin's command handler, respecting that
    command's own admin/private/group restrictions exactly as if the user
    had typed it directly."""
    command = (command or "").strip().lstrip("/").lower()
    entry = COMMAND_INDEX.get(command)
    if not entry:
        return {"ok": False, "error": f"no such command: /{command}. Use search_commands first."}

    user_id = orig_message.from_user.id if orig_message.from_user else 0
    if entry["admin_only"] and user_id not in ADMINS:
        return {"ok": False, "error": f"/{command} is admin-only; this user isn't an admin."}
    if entry["private_only"] and orig_message.chat.type != enums.ChatType.PRIVATE:
        return {"ok": False, "error": f"/{command} only works in a private chat with the bot."}
    if entry["group_only"] and orig_message.chat.type == enums.ChatType.PRIVATE:
        return {"ok": False, "error": f"/{command} only works inside a group."}

    try:
        module = __import__(f"Akbots.{entry['module']}", fromlist=[entry["function"]])
        handler = getattr(module, entry["function"])
    except Exception as e:
        logger.error(f"aiulta: couldn't load handler for /{command}: {e}")
        return {"ok": False, "error": f"internal error loading /{command}"}

    fake = copy.copy(orig_message)
    args = (args or "").strip()
    fake.text = f"/{command} {args}".strip()
    fake.command = [command] + (args.split() if args else [])

    try:
        await asyncio.wait_for(handler(client, fake), timeout=PER_COMMAND_TIMEOUT)
        return {"ok": True, "result": f"/{command} executed."}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"/{command} took too long and was cancelled."}
    except Exception as e:
        logger.exception(f"aiulta: /{command} raised")
        return {"ok": False, "error": f"/{command} failed: {e}"}


@Client.on_message(filters.command(["aiulta", "aido", "aiagent"]) & AIULTA_SCOPE)
async def aiulta_command(client: Client, message: Message):
    if _client is None:
        await message.reply_text(
            f"<b>{E_CROSS} AI agent isn't configured.</b>\n"
            f"<i>Set OPENROUTER_API_KEY (get one with credits at "
            f"openrouter.ai) to enable /aiulta.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if not message.from_user or not _is_allowed(message.from_user.id):
        await _deny(message)
        return

    instruction = ""
    if len(message.command) > 1:
        instruction = message.text.split(None, 1)[1].strip()

    reply = message.reply_to_message

    # --- voice note as instruction (this message or the replied-to one) ---
    voice_source = message.voice or (reply.voice if reply else None)
    if not instruction and voice_source:
        if _direct_client is None:
            await message.reply_text(
                f"<b>{E_CROSS} Voice input needs OPENAI_API_KEY set (for Whisper).</b>",
                parse_mode=enums.ParseMode.HTML,
            )
            return
        vstatus = await message.reply_text(f"<b>{E_GEAR} Sun raha hoon...</b>", parse_mode=enums.ParseMode.HTML)
        voice_path = None
        try:
            voice_path = await (message if message.voice else reply).download(
                progress=make_download_progress(vstatus, file_name="voice note")
            )
            instruction = await _transcribe_voice(voice_path)
        finally:
            if voice_path and os.path.exists(voice_path):
                os.remove(voice_path)
        await vstatus.delete()
        if not instruction:
            await message.reply_text(f"<b>{E_CROSS} Kuch samajh nahi aaya voice note mein.</b>", parse_mode=enums.ParseMode.HTML)
            return

    if not instruction and reply and reply.text:
        instruction = reply.text.strip()

    # --- photo -> vision ---
    photo_source = message.photo or (reply.photo if reply else None)
    image_b64 = None
    if photo_source:
        photo_msg = message if message.photo else reply
        pstatus = await message.reply_text(f"<b>{E_GEAR} Image dekh raha hoon...</b>", parse_mode=enums.ParseMode.HTML)
        photo_path = None
        try:
            photo_path = await photo_msg.download(progress=make_download_progress(pstatus, file_name="image"))
            with open(photo_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            await safe_edit(pstatus.edit_text, f"<b>{E_CROSS} Couldn't read that image:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            return
        finally:
            if photo_path and os.path.exists(photo_path):
                os.remove(photo_path)
        await pstatus.delete()
        if not instruction:
            instruction = photo_msg.caption or "Describe this image and tell me if any bot action fits it."

    # --- document -> extracted text as context ---
    doc_source = message.document or (reply.document if reply else None)
    doc_context = ""
    if doc_source and doc_source.file_name and doc_source.file_name.lower().endswith((".pdf", ".txt", ".md", ".docx")):
        doc_msg = message if message.document else reply
        dstatus = await message.reply_text(f"<b>{E_GEAR} Document padh raha hoon...</b>", parse_mode=enums.ParseMode.HTML)
        doc_path = None
        try:
            doc_path = await doc_msg.download(progress=make_download_progress(dstatus, file_name=doc_source.file_name))
            doc_context = await _extract_document_text(doc_path)
        finally:
            if doc_path and os.path.exists(doc_path):
                os.remove(doc_path)
        await dstatus.delete()
        if not instruction:
            instruction = "Summarize this document."

    if not instruction:
        await message.reply_text(
            f"<b>{E_INFO} Kya karna hai bata do.</b>\n"
            f"<i>Usage: /aiulta &lt;instruction&gt;</i>\n"
            f"<i>e.g. /aiulta is terabox link ko download karke bhej do &lt;link&gt;</i>\n"
            f"<i>Ya photo/voice note/PDF ke saath reply karke /aiulta bhejo.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    status = await message.reply_text(
        f"<b>{E_GEAR} Samajh raha hoon...</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    user_content = instruction
    if doc_context:
        user_content = f"{instruction}\n\n[Document content below]\n{doc_context}"

    if image_b64:
        user_content = [
            {"type": "text", "text": user_content},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"}},
        ]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    model_for_this_call = VISION_MODEL if image_b64 else AGENT_MODEL

    final_text = None
    actions_taken = []

    for step in range(MAX_STEPS):
        try:
            resp = await _client.chat.completions.create(
                model=model_for_this_call,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.3,
            )
        except Exception as e:
            logger.exception("aiulta: LLM call failed")
            await safe_edit(
                status.edit_text,
                f"<b>{E_CROSS} AI se baat nahi ho paayi.</b>\n<i>{e}</i>",
                parse_mode=enums.ParseMode.HTML,
            )
            return

        choice = resp.choices[0].message
        tool_calls = getattr(choice, "tool_calls", None)

        if not tool_calls:
            final_text = (choice.content or "").strip() or "Ho gaya."
            break

        messages.append({
            "role": "assistant",
            "content": choice.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            if name == "search_commands":
                result = search_commands(args.get("query", ""))
                await safe_edit(
                    status.edit_text,
                    f"<b>{E_GEAR} Searching:</b> <i>{args.get('query', '')}</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
            elif name == "run_command":
                cmd = args.get("command", "")
                cmd_args = args.get("args", "")
                await safe_edit(
                    status.edit_text,
                    f"<b>{E_ROCKET} Running:</b> <code>/{cmd} {cmd_args}</code>",
                    parse_mode=enums.ParseMode.HTML,
                )
                result = await _run_command(client, message, cmd, cmd_args)
                actions_taken.append((cmd, result.get("ok", False)))
            elif name == "speak":
                text = args.get("text", "")
                if _direct_client is None:
                    result = {"ok": False, "error": "TTS needs OPENAI_API_KEY, not configured."}
                else:
                    mp3_path = await _text_to_speech(text)
                    if mp3_path:
                        try:
                            await message.reply_voice(mp3_path)
                            result = {"ok": True, "result": "sent as voice message"}
                        except Exception as e:
                            result = {"ok": False, "error": f"couldn't send voice: {e}"}
                        finally:
                            if os.path.exists(mp3_path):
                                os.remove(mp3_path)
                    else:
                        result = {"ok": False, "error": "TTS generation failed"}
            elif name == "generate_code":
                proj = args.get("project_name", "project")
                desc = args.get("description", "")
                await safe_edit(
                    status.edit_text,
                    f"<b>{E_GEAR} Code likh raha hoon:</b> <i>{proj}</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
                gen = await _generate_code_project(desc, proj)
                if gen.get("ok"):
                    try:
                        await message.reply_document(
                            gen["zip_path"],
                            caption=f"<b>{E_CHECK} {proj}</b> — {len(gen['files'])} files.",
                            parse_mode=enums.ParseMode.HTML,
                        )
                        result = {"ok": True, "result": f"generated and sent {len(gen['files'])} files as {proj}.zip", "files": gen["files"], "local_zip_path": gen["zip_path"]}
                    except Exception as e:
                        result = {"ok": False, "error": f"generated but couldn't send zip: {e}"}
                    finally:
                        import shutil
                        shutil.rmtree(os.path.dirname(gen["zip_path"]), ignore_errors=True)
                else:
                    result = gen
            else:
                result = {"ok": False, "error": "unknown tool"}

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False)[:4000],
            })
    else:
        final_text = "Bahut saare steps ho gaye, ruk raha hoon. Thoda specific instruction try karo."

    summary_prefix = f"<b>{E_CHECK} Done</b>\n" if any(ok for _, ok in actions_taken) else ""
    try:
        await safe_edit(
            status.edit_text,
            f"{summary_prefix}{final_text}",
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        # final_text might contain characters that break HTML parse_mode —
        # fall back to plain text rather than losing the answer.
        await safe_edit(status.edit_text, final_text)
