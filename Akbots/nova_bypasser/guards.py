"""
Guards for Akbots/linkbypass.py — ported from the Nova bots'
middleware/rate_limiter.py + middleware/force_sub.py, adapted to
Pyrogram decorators and this repo's existing force-sub helper
(Akbots/forcesub.py) instead of duplicating it.
"""

import time
import logging
from functools import wraps
from typing import Dict, List
from config import ADMINS
from Akbots.direct_utils import E_INFO
from Akbots.forcesub import is_subscribed, send_force_sub_prompt

logger = logging.getLogger(__name__)

# In-memory sliding-window rate limit: {user_id: [timestamps]}. Simple by
# design — this only needs to survive one process's uptime to stop a user
# from hammering /bypass; it doesn't need to be a persistent daily quota
# (this bot already has its own separate bucks/premium economy for that).
_rate_limits: Dict[int, List[float]] = {}


def rate_limited(calls: int = 5, period: int = 60):
    """Decorator for a Pyrogram message handler: allow at most `calls`
    invocations per user per `period` seconds. Admins are exempt."""
    def decorator(func):
        @wraps(func)
        async def wrapper(client, message, *args, **kwargs):
            user_id = message.from_user.id if message.from_user else None
            if user_id is None or user_id in ADMINS:
                return await func(client, message, *args, **kwargs)

            now = time.time()
            hits = [t for t in _rate_limits.get(user_id, []) if now - t < period]

            if len(hits) >= calls:
                remaining = int(period - (now - hits[0]))
                return await message.reply_text(
                    f"<b>⏳ Slow down!</b> You've hit the bypass rate limit "
                    f"({calls} per {period}s).\n<i>Try again in {remaining}s.</i>",
                    parse_mode="html",
                )

            hits.append(now)
            _rate_limits[user_id] = hits
            return await func(client, message, *args, **kwargs)
        return wrapper
    return decorator


def force_sub_required(func):
    """Decorator: block the handler until the user has joined
    FORCE_SUB_CHANNEL (no-op if that env var isn't set — see
    Akbots/forcesub.py::is_subscribed)."""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        user_id = message.from_user.id if message.from_user else None
        if user_id is not None and not await is_subscribed(client, user_id):
            return await send_force_sub_prompt(client, message)
        return await func(client, message, *args, **kwargs)
    return wrapper
