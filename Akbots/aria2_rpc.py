# Akbots
# aria2 RPC daemon — persistent queue with pause / resume / edit
#
# Akbots/aria2_dl.py already shells out to aria2c, but it spawns a fresh
# throwaway process per download and just parses its stdout — once it's
# running there's no way to pause it, change its URL, or swap its proxy
# mid-flight, because there's no live process to talk back to.
#
# This module instead boots ONE long-lived `aria2c --enable-rpc` daemon
# for the whole bot and talks to it over JSON-RPC (the same protocol
# aria2's own aria2p/WebUI/qBittorrent-style front-ends use). That gets us:
#   - /rpcadd  — queue a URL on the persistent daemon
#   - /rpctasks + inline buttons — Pause / Resume / Edit (URL or proxy) /
#     Remove, all while the transfer is in flight
#   - optionally (config.ARIA2_RPC_EXTERNAL), the RPC port itself becomes
#     reachable so a real external aria2-compatible client (Ghost
#     Downloader, aria2p, etc.) can push tasks into the same queue — see
#     config.py's comment block on that flag before turning it on.
#
# LIMITATION (documented on purpose, not hidden): task bookkeeping
# (_TASKS below) is in-memory only. If the bot process restarts, the
# aria2c daemon it owns restarts too, so any in-flight tasks are gone
# either way — there is nothing to "reattach" to. Completed downloads
# already uploaded to Telegram before a restart are unaffected.
#
# Don't Remove Credit
# Telegram Channel @AkBots_Official

import os
import time
import uuid
import shutil
import asyncio
import contextlib
import aiohttp
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup

from config import ARIA2_RPC_PORT, ARIA2_RPC_SECRET, ARIA2_RPC_EXTERNAL, ADMINS
from Akbots.direct_utils import (
    upload_file, fmt_bytes, draw_bar,
    E_CHECK, E_CROSS, E_INFO, E_ROCKET, E_BOLT,
    _looks_like_html_error, _extract_html_reason, wait_for_reply,
)
from Akbots.link_cache import try_send_cached
from Akbots.start import make_button, BUTTON_STYLE_SUPPORTED
if BUTTON_STYLE_SUPPORTED:
    from pyrogram.enums import ButtonStyle as _BS
else:
    _BS = None
from logger import LOGGER
from Akbots.direct_utils import safe_edit

logger = LOGGER(__name__)

RPC_URL = f"http://127.0.0.1:{ARIA2_RPC_PORT}/jsonrpc"
RPC_DIR = os.path.join("downloads", "rpc")
POLL_INTERVAL = 3  # seconds between tellStatus polls for an active task


# =========================================================
# Daemon lifecycle
# =========================================================

class _Aria2RpcDaemon:
    """Owns the single long-lived aria2c --enable-rpc process for this bot."""

    def __init__(self):
        # Random per-boot secret unless the admin pinned one in config —
        # either way it's never logged anywhere except /rpcinfo, which is
        # admin-gated.
        self.secret = ARIA2_RPC_SECRET or uuid.uuid4().hex
        self._boot_task = None
        self.ready = False

    def boot(self):
        """Fire-and-forget: starts the boot/watch as a background task,
        mirroring Akbots/jdownloader_core.py's boot() pattern."""
        if self._boot_task is None or self._boot_task.done():
            self._boot_task = asyncio.create_task(self._boot_loop())
        return self._boot_task

    async def _boot_loop(self):
        os.makedirs(RPC_DIR, exist_ok=True)

        if not shutil.which("aria2c"):
            logger.warning("aria2 RPC daemon not started — 'aria2c' isn't installed on this host.")
            return

        cmd = [
            "aria2c",
            "--enable-rpc=true",
            f"--rpc-listen-port={ARIA2_RPC_PORT}",
            f"--rpc-secret={self.secret}",
            f"--rpc-listen-all={'true' if ARIA2_RPC_EXTERNAL else 'false'}",
            "--continue=true",
            "--max-connection-per-server=4",
            "--split=4",
            "--min-split-size=1M",
            f"--dir={RPC_DIR}",
            "--quiet=true",
            "--summary-interval=0",
        ]
        try:
            # Deliberately not awaited/communicate()'d — this process is
            # meant to keep running for the bot's entire lifetime, not
            # exit. It's killed implicitly when the bot's own process
            # group dies.
            await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning(f"aria2 RPC daemon failed to start: {e}")
            return

        # Give aria2c a moment to actually bind the RPC port before
        # anything (including our own /rpcadd) tries to use it.
        for _ in range(20):
            try:
                await _rpc_call("aria2.getVersion", secret=self.secret)
                self.ready = True
                logger.info(
                    f"aria2 RPC daemon ready on 127.0.0.1:{ARIA2_RPC_PORT}"
                    f"{' (external access enabled)' if ARIA2_RPC_EXTERNAL else ''}."
                )
                return
            except Exception:
                await asyncio.sleep(0.5)
        logger.warning("aria2 RPC daemon process started but never became reachable over RPC.")


daemon = _Aria2RpcDaemon()


async def _rpc_call(method: str, params: list = None, secret: str = None):
    """Raw JSON-RPC 2.0 call against the daemon. Raises on any failure
    (network error, daemon not up, or an RPC-level {"error": ...})."""
    payload = {
        "jsonrpc": "2.0", "id": "akbots",
        "method": method,
        "params": [f"token:{secret or daemon.secret}", *(params or [])],
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json(content_type=None)
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "aria2 RPC error"))
    return data.get("result")


# =========================================================
# Task registry — gid -> bookkeeping (see LIMITATION note up top)
# =========================================================

_TASKS = {}


def _owns(gid: str, user_id: int) -> bool:
    t = _TASKS.get(gid)
    return bool(t and (t["user_id"] == user_id or user_id in ADMINS))


def _task_kb(gid: str, paused: bool = False) -> InlineKeyboardMarkup:
    toggle = (
        make_button("▶️ ʀᴇsᴜᴍᴇ", callback_data=f"rpcresume#{gid}", style=_BS.PRIMARY if _BS else None)
        if paused else
        make_button("⏸ ᴘᴀᴜsᴇ", callback_data=f"rpcpause#{gid}", style=_BS.PRIMARY if _BS else None)
    )
    return InlineKeyboardMarkup([
        [toggle, make_button("✏️ ᴇᴅɪᴛ", callback_data=f"rpcedit#{gid}", style=_BS.PRIMARY if _BS else None)],
        [make_button("❌ ʀᴇᴍᴏᴠᴇ", callback_data=f"rpcremove#{gid}", style=_BS.DANGER if _BS else None)],
    ])


async def _rpc_add(client: Client, message: Message, url: str,
                    headers: dict = None, proxy: str = None):
    if not daemon.ready:
        return await message.reply_text(
            f"<b>{E_CROSS} aria2 RPC daemon isn't running.</b>\n"
            f"<i>'aria2c' may not be installed on this host, or it hasn't finished starting yet.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    if await try_send_cached(client, message, url, None):
        return

    opts = {}
    if headers:
        opts["header"] = [f"{k}: {v}" for k, v in headers.items()]
    if proxy:
        opts["all-proxy"] = proxy

    try:
        gid = await _rpc_call("aria2.addUri", [[url], opts])
    except Exception as e:
        return await message.reply_text(
            f"<b>{E_CROSS} Couldn't queue download:</b>\n<code>{e}</code>",
            parse_mode=enums.ParseMode.HTML,
        )

    status = await message.reply_text(
        f"<b>{E_ROCKET} Queued on aria2 RPC</b>\n<code>GID: {gid}</code>",
        reply_markup=_task_kb(gid), parse_mode=enums.ParseMode.HTML,
    )
    _TASKS[gid] = {
        "user_id": message.from_user.id, "chat_id": message.chat.id,
        "status": status, "message": message, "url": url, "added": time.time(),
    }
    asyncio.create_task(_watch_task(client, gid))


async def _watch_task(client: Client, gid: str):
    """Polls tellStatus every POLL_INTERVAL seconds, keeping the status
    message's progress bar live, until the task completes/errors/is
    removed — then uploads the finished file (or reports the failure)."""
    last_edit = 0.0
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        task = _TASKS.get(gid)
        if not task:
            return  # removed elsewhere (rpcremove callback etc.)

        try:
            info = await _rpc_call("aria2.tellStatus", [gid, [
                "status", "completedLength", "totalLength",
                "downloadSpeed", "files", "errorMessage",
            ]])
        except Exception:
            continue  # daemon hiccup — just try again next tick

        st = info.get("status")
        status = task["status"]

        if st == "active":
            now = time.time()
            if now - last_edit < 3:
                continue
            last_edit = now
            done = int(info.get("completedLength", 0) or 0)
            total = int(info.get("totalLength", 0) or 0)
            speed = int(info.get("downloadSpeed", 0) or 0)
            pct = (done / total * 100) if total else 0.0
            with contextlib.suppress(Exception):
                await safe_edit(status.edit_text, 
                    f"<b>{E_BOLT} Downloading (aria2 RPC)</b>\n"
                    f"{draw_bar(pct)} {pct:.1f}%\n"
                    f"💾 {fmt_bytes(done)} / {fmt_bytes(total)}\n"
                    f"⚡ {fmt_bytes(speed)}/s\n"
                    f"<code>GID: {gid}</code>",
                    reply_markup=_task_kb(gid), parse_mode=enums.ParseMode.HTML,
                )

        elif st == "complete":
            _TASKS.pop(gid, None)
            files = info.get("files", [])
            path = files[0]["path"] if files else None
            if not path or not os.path.exists(path):
                with contextlib.suppress(Exception):
                    await safe_edit(status.edit_text, 
                        f"<b>{E_CROSS} aria2 reported complete but the file is missing on disk.</b>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                return
            name = os.path.basename(path)
            try:
                with open(path, "rb") as f:
                    head = f.read(4096)
            except Exception:
                head = b""
            if _looks_like_html_error(head):
                try:
                    os.remove(path)
                except Exception:
                    pass
                with contextlib.suppress(Exception):
                    await safe_edit(status.edit_text, 
                        f"<b>{E_CROSS} Download failed:</b>\n<code>{_extract_html_reason(head)}.</code>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                with contextlib.suppress(Exception):
                    await _rpc_call("aria2.removeDownloadResult", [gid])
                return
            try:
                await upload_file(
                    client, task["message"], path, status,
                    f"<b>{E_CHECK} Downloaded via aria2 RPC</b>\n<code>{name}</code>",
                    file_name=name, cache_url=task["url"],
                )
            except Exception as e:
                with contextlib.suppress(Exception):
                    await safe_edit(status.edit_text, f"<b>{E_CROSS} Upload failed:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML)
            finally:
                with contextlib.suppress(Exception):
                    await _rpc_call("aria2.removeDownloadResult", [gid])
            return

        elif st in ("error", "removed"):
            _TASKS.pop(gid, None)
            err = info.get("errorMessage") or "unknown error"
            with contextlib.suppress(Exception):
                await safe_edit(status.edit_text, f"<b>{E_CROSS} Download failed:</b>\n<code>{err}</code>", parse_mode=enums.ParseMode.HTML)
            with contextlib.suppress(Exception):
                await _rpc_call("aria2.removeDownloadResult", [gid])
            return
        # "paused"/"waiting": nothing to do here, the pause/resume
        # callbacks already updated the message's keyboard themselves.


# =========================================================
# Pause / Resume / Remove callbacks
# =========================================================

@Client.on_callback_query(filters.regex(r"^rpcpause#"))
async def rpc_pause_callback(client: Client, callback_query: CallbackQuery):
    gid = callback_query.data.split("#", 1)[1]
    if not _owns(gid, callback_query.from_user.id):
        return await callback_query.answer("Not your task.", show_alert=True)
    try:
        await _rpc_call("aria2.pause", [gid])
    except Exception as e:
        return await callback_query.answer(f"Couldn't pause: {e}"[:200], show_alert=True)
    await callback_query.answer("Paused")
    with contextlib.suppress(Exception):
        await callback_query.message.edit_reply_markup(_task_kb(gid, paused=True))


@Client.on_callback_query(filters.regex(r"^rpcresume#"))
async def rpc_resume_callback(client: Client, callback_query: CallbackQuery):
    gid = callback_query.data.split("#", 1)[1]
    if not _owns(gid, callback_query.from_user.id):
        return await callback_query.answer("Not your task.", show_alert=True)
    try:
        await _rpc_call("aria2.unpause", [gid])
    except Exception as e:
        return await callback_query.answer(f"Couldn't resume: {e}"[:200], show_alert=True)
    await callback_query.answer("Resumed")
    with contextlib.suppress(Exception):
        await callback_query.message.edit_reply_markup(_task_kb(gid, paused=False))


@Client.on_callback_query(filters.regex(r"^rpcremove#"))
async def rpc_remove_callback(client: Client, callback_query: CallbackQuery):
    gid = callback_query.data.split("#", 1)[1]
    if not _owns(gid, callback_query.from_user.id):
        return await callback_query.answer("Not your task.", show_alert=True)
    with contextlib.suppress(Exception):
        await _rpc_call("aria2.forceRemove", [gid])
    with contextlib.suppress(Exception):
        await _rpc_call("aria2.removeDownloadResult", [gid])
    _TASKS.pop(gid, None)
    await callback_query.answer("Removed")
    with contextlib.suppress(Exception):
        await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Task removed.</b>", parse_mode=enums.ParseMode.HTML)


# =========================================================
# Edit (URL / proxy) — pause, ask via client.listen(), apply, unpause
# =========================================================

@Client.on_callback_query(filters.regex(r"^rpcedit#"))
async def rpc_edit_menu_callback(client: Client, callback_query: CallbackQuery):
    gid = callback_query.data.split("#", 1)[1]
    if not _owns(gid, callback_query.from_user.id):
        return await callback_query.answer("Not your task.", show_alert=True)
    await callback_query.answer()
    kb = InlineKeyboardMarkup([
        [make_button("🔗 ᴄʜᴀɴɢᴇ ᴜʀʟ", callback_data=f"rpcediturl#{gid}", style=_BS.PRIMARY if _BS else None),
         make_button("🌐 ᴄʜᴀɴɢᴇ ᴘʀᴏxʏ", callback_data=f"rpceditproxy#{gid}", style=_BS.PRIMARY if _BS else None)],
        [make_button("⬅️ ʙᴀᴄᴋ", callback_data=f"rpceditback#{gid}", style=_BS.PRIMARY if _BS else None)],
    ])
    with contextlib.suppress(Exception):
        await callback_query.message.edit_reply_markup(kb)


@Client.on_callback_query(filters.regex(r"^rpceditback#"))
async def rpc_edit_back_callback(client: Client, callback_query: CallbackQuery):
    gid = callback_query.data.split("#", 1)[1]
    await callback_query.answer()
    with contextlib.suppress(Exception):
        await callback_query.message.edit_reply_markup(_task_kb(gid))


async def _apply_new_url(gid: str, new_url: str):
    info = await _rpc_call("aria2.tellStatus", [gid, ["files"]])
    old_uris = [u["uri"] for u in info["files"][0]["uris"]] if info.get("files") else []
    # fileIndex is 1-based in aria2's API, hence the literal 1 — this
    # module only ever adds single-file URI downloads via addUri.
    await _rpc_call("aria2.changeUri", [gid, 1, old_uris, [new_url]])
    if gid in _TASKS:
        _TASKS[gid]["url"] = new_url


async def _apply_new_proxy(gid: str, new_proxy: str):
    await _rpc_call("aria2.changeOption", [gid, {"all-proxy": new_proxy}])


async def _prompt_and_apply(client: Client, callback_query: CallbackQuery, gid: str,
                             prompt_text: str, apply_fn):
    """Shared pause -> ask (client.listen) -> apply -> unpause flow.
    aria2 only guarantees changeUri/changeOption take effect cleanly while
    the task isn't actively transferring, so this pauses first regardless
    of whether the task was already paused, and restores its prior state
    afterwards."""
    if not _owns(gid, callback_query.from_user.id):
        return await callback_query.answer("Not your task.", show_alert=True)
    await callback_query.answer()
    if gid not in _TASKS:
        return await safe_edit(callback_query.message.edit_text, f"<b>{E_CROSS} Task no longer exists.</b>", parse_mode=enums.ParseMode.HTML)

    was_active = False
    with contextlib.suppress(Exception):
        info = await _rpc_call("aria2.tellStatus", [gid, ["status"]])
        was_active = info.get("status") == "active"
        if was_active:
            await _rpc_call("aria2.pause", [gid])

    ask = await callback_query.message.reply_text(prompt_text, parse_mode=enums.ParseMode.HTML)
    try:
        resp = await wait_for_reply(
            client, chat_id=callback_query.message.chat.id,
            user_id=callback_query.from_user.id, timeout=60,
        )
    except Exception:
        with contextlib.suppress(Exception):
            await safe_edit(ask.edit_text, f"<b>{E_CROSS} Timed out — nothing changed.</b>", parse_mode=enums.ParseMode.HTML)
        if was_active:
            with contextlib.suppress(Exception):
                await _rpc_call("aria2.unpause", [gid])
        return

    value = (resp.text or "").strip()
    with contextlib.suppress(Exception):
        await ask.delete()

    if value and value != "/cancel":
        try:
            await apply_fn(gid, value)
        except Exception as e:
            await callback_query.message.reply_text(
                f"<b>{E_CROSS} Couldn't apply change:</b>\n<code>{e}</code>", parse_mode=enums.ParseMode.HTML
            )

    if was_active:
        with contextlib.suppress(Exception):
            await _rpc_call("aria2.unpause", [gid])
    with contextlib.suppress(Exception):
        await callback_query.message.edit_reply_markup(_task_kb(gid, paused=not was_active))


@Client.on_callback_query(filters.regex(r"^rpcediturl#"))
async def rpc_edit_url_callback(client: Client, callback_query: CallbackQuery):
    gid = callback_query.data.split("#", 1)[1]
    await _prompt_and_apply(client, callback_query, gid, f"{E_INFO} Send the new URL (or /cancel):", _apply_new_url)


@Client.on_callback_query(filters.regex(r"^rpceditproxy#"))
async def rpc_edit_proxy_callback(client: Client, callback_query: CallbackQuery):
    gid = callback_query.data.split("#", 1)[1]
    await _prompt_and_apply(
        client, callback_query, gid,
        f"{E_INFO} Send the new proxy (e.g. <code>http://host:port</code>, or /cancel):",
        _apply_new_proxy,
    )


# =========================================================
# Commands
# =========================================================

@Client.on_message(filters.command("rpcadd") & filters.private)
async def rpcadd_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            f"<b>{E_INFO} Usage:</b> <code>/rpcadd &lt;url&gt;</code>\n"
            f"<i>Unlike a normal download, this one lives on the persistent aria2 "
            f"RPC queue — pause, resume, or edit its URL/proxy anytime before it finishes.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
    url = message.command[1]
    await _rpc_add(client, message, url)


@Client.on_message(filters.command("rpctasks") & filters.private)
async def rpctasks_command(client: Client, message: Message):
    mine = [gid for gid, t in _TASKS.items() if t["user_id"] == message.from_user.id]
    if not mine:
        return await message.reply_text(f"<b>{E_INFO} No active aria2 RPC tasks.</b>", parse_mode=enums.ParseMode.HTML)
    lines = [f"<b>{E_ROCKET} Your active aria2 RPC tasks:</b>"]
    lines += [f"• <code>{gid}</code>" for gid in mine]
    lines.append("\n<i>Tap a task's own status message to pause/resume/edit it.</i>")
    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("rpcinfo") & filters.user(ADMINS))
async def rpcinfo_command(client: Client, message: Message):
    scope = "0.0.0.0 — external clients CAN reach this" if ARIA2_RPC_EXTERNAL else "127.0.0.1 only (internal to this host)"
    await message.reply_text(
        f"<b>{E_INFO} aria2 RPC daemon</b>\n"
        f"Status: {'🟢 running' if daemon.ready else '🔴 not running'}\n"
        f"Port: <code>{ARIA2_RPC_PORT}</code>\n"
        f"Listening: {scope}\n"
        f"Secret: <code>{daemon.secret}</code>\n\n"
        f"<i>Third-party aria2-compatible clients can push tasks to "
        f"<code>http://&lt;this host&gt;:{ARIA2_RPC_PORT}/jsonrpc</code> using this secret, "
        f"but only if ARIA2_RPC_EXTERNAL=true in config — see config.py's comment on that "
        f"flag before enabling it on a publicly reachable host.</i>",
        parse_mode=enums.ParseMode.HTML,
    )
