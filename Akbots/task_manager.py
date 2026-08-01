# Developed by: LastPerson07 × AkBots
# Telegram: @AkBots_Official | @THEUPDATEDGUYS
#
# Lightweight, in-memory registry of active download/upload asyncio.Tasks,
# keyed by user_id. Any plugin can register a running task here so it shows
# up in /queue and can be stopped in bulk with /cancel_all, instead of every
# plugin having to build its own cancellation bookkeeping.
#
# This does NOT replace each plugin's existing single-task /cancel handling
# (e.g. start.py's batch cancel, ytdl.py's per-session cancel button) — it's
# an additive, best-effort layer on top: register() is a no-op-safe helper,
# so a plugin that forgets to call it just won't show up in /queue, it won't
# break.

import time
import uuid
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from config import ADMINS

# user_id -> {task_id: {"task": asyncio.Task, "label": str, "started": float}}
_ACTIVE = {}

# --------------------------------------------------------
# Queue system: caps how many downloads run at once per user. Extra
# requests wait their turn (shown a "queued" status) instead of every
# download firing off in parallel and fighting over bandwidth/CPU.
# --------------------------------------------------------
MAX_CONCURRENT_DOWNLOADS = 2  # per user, not global

_SEMAPHORES = {}  # user_id -> asyncio.Semaphore
_QUEUED_COUNT = {}  # user_id -> int, how many are currently waiting for a slot


def _sem_for(user_id: int) -> asyncio.Semaphore:
    sem = _SEMAPHORES.get(user_id)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        _SEMAPHORES[user_id] = sem
    return sem


def queued_for(user_id: int) -> int:
    """How many of this user's downloads are currently waiting for a slot."""
    return _QUEUED_COUNT.get(user_id, 0)


class queue_slot:
    """Async context manager — limits a user to MAX_CONCURRENT_DOWNLOADS
    downloads running at once. Usage:

        async with task_manager.queue_slot(user_id, status_msg=status):
            ... do the actual download/upload ...

    If the user already has MAX_CONCURRENT_DOWNLOADS running, this waits
    (and, if a status message was given, edits it to say so) before letting
    the download proceed — that's the actual "queue" behavior.
    """

    def __init__(self, user_id: int, status_msg: Message = None,
                 waiting_text: str = "<b>⏳ Queued — waiting for a free download slot...</b>"):
        self.user_id = user_id
        self.status_msg = status_msg
        self.waiting_text = waiting_text
        self.sem = _sem_for(user_id)

    async def __aenter__(self):
        if self.sem.locked():
            _QUEUED_COUNT[self.user_id] = _QUEUED_COUNT.get(self.user_id, 0) + 1
            if self.status_msg is not None:
                try:
                    await self.status_msg.edit_text(self.waiting_text, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
        try:
            await self.sem.acquire()
        finally:
            if self.user_id in _QUEUED_COUNT:
                _QUEUED_COUNT[self.user_id] = max(0, _QUEUED_COUNT[self.user_id] - 1)
                if _QUEUED_COUNT[self.user_id] == 0:
                    _QUEUED_COUNT.pop(self.user_id, None)
        return self

    async def __aexit__(self, *exc):
        self.sem.release()


def register(user_id: int, task: "asyncio.Task", label: str) -> str:
    """Registers a running task for a user. Returns a task_id to pass to
    unregister() once the task finishes (success, failure, or cancellation)."""
    task_id = uuid.uuid4().hex[:8]
    _ACTIVE.setdefault(user_id, {})[task_id] = {
        "task": task, "label": label, "started": time.time()
    }
    return task_id


def unregister(user_id: int, task_id: str):
    bucket = _ACTIVE.get(user_id)
    if not bucket:
        return
    bucket.pop(task_id, None)
    if not bucket:
        _ACTIVE.pop(user_id, None)


def tasks_for(user_id: int):
    """Returns [(task_id, label, started_ts), ...] for one user, oldest first."""
    bucket = _ACTIVE.get(user_id) or {}
    items = [(tid, v["label"], v["started"]) for tid, v in bucket.items()]
    return sorted(items, key=lambda x: x[2])


def all_tasks():
    """Returns {user_id: [(task_id, label, started_ts), ...]} for every user
    with at least one active task. Used by admins for a global /queue view."""
    return {uid: tasks_for(uid) for uid in list(_ACTIVE.keys()) if _ACTIVE.get(uid)}


def cancel_all_for(user_id: int) -> int:
    """Cancels every registered task for a user. Returns how many were
    cancelled. The tasks remove themselves from the registry via their
    own finally-block unregister() call once CancelledError propagates."""
    bucket = _ACTIVE.get(user_id) or {}
    count = 0
    for entry in list(bucket.values()):
        t = entry["task"]
        if not t.done():
            t.cancel()
            count += 1
    return count


def cancel_everyone() -> int:
    """Admin-only nuclear option: cancels every active task for every user."""
    count = 0
    for uid in list(_ACTIVE.keys()):
        count += cancel_all_for(uid)
    return count


def _fmt_elapsed(started: float) -> str:
    secs = int(time.time() - started)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


@Client.on_message(filters.command("queue") & filters.private)
async def queue_command(client: Client, message: Message):
    user_id = message.from_user.id

    if user_id in ADMINS and len(message.command) > 1 and message.command[1].lower() == "all":
        everyone = all_tasks()
        if not everyone:
            return await message.reply_text("<b>📭 No active tasks for anyone right now.</b>")
        lines = ["<b>📋 Active tasks (all users):</b>", ""]
        for uid, items in everyone.items():
            lines.append(f"<b>👤 {uid}</b> — {len(items)} task(s)")
            for _, label, started in items:
                lines.append(f"  • {label} — <i>{_fmt_elapsed(started)} ago</i>")
        return await message.reply_text("\n".join(lines))

    items = tasks_for(user_id)
    queued = queued_for(user_id)
    if not items and not queued:
        return await message.reply_text("<b>📭 You have no active tasks right now.</b>")
    lines = [f"<b>📋 Your active tasks ({len(items)}):</b>", ""]
    for _, label, started in items:
        lines.append(f"• {label} — <i>running {_fmt_elapsed(started)}</i>")
    if queued:
        lines.append("")
        lines.append(f"<i>⏳ {queued} more waiting in queue for a free slot...</i>")
    lines.append("")
    lines.append("<i>Use /cancel_all to stop all of these.</i>")
    await message.reply_text("\n".join(lines))


@Client.on_message(filters.command("cancel_all") & filters.private)
async def cancel_all_command(client: Client, message: Message):
    user_id = message.from_user.id

    if user_id in ADMINS and len(message.command) > 1 and message.command[1].lower() == "all":
        count = cancel_everyone()
        return await message.reply_text(
            f"<b>🚫 Cancelled {count} task(s) across all users.</b>" if count else
            "<b>📭 Nothing was running.</b>"
        )

    count = cancel_all_for(user_id)
    await message.reply_text(
        f"<b>🚫 Cancelled {count} task(s).</b>" if count else
        "<b>📭 You have no active tasks to cancel.</b>"
    )
