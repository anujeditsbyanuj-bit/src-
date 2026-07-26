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
from pyrogram import Client, filters
from pyrogram.types import Message
from config import ADMINS

# user_id -> {task_id: {"task": asyncio.Task, "label": str, "started": float}}
_ACTIVE = {}


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
    if not items:
        return await message.reply_text("<b>📭 You have no active tasks right now.</b>")
    lines = [f"<b>📋 Your active tasks ({len(items)}):</b>", ""]
    for _, label, started in items:
        lines.append(f"• {label} — <i>running {_fmt_elapsed(started)}</i>")
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
