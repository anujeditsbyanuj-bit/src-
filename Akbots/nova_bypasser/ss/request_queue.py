import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class RequestQueue:
    def __init__(self, max_concurrent: int = 5):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._queue: list[tuple[int, int]] = []
        self._lock = asyncio.Lock()
        self._max_concurrent = max_concurrent

    async def enqueue(self, chat_id: int, user_id: int) -> int:
        async with self._lock:
            self._queue.append((chat_id, user_id))
            return len(self._queue)

    def _remove(self, chat_id: int, user_id: int):
        self._queue = [
            (c, u) for c, u in self._queue
            if not (c == chat_id and u == user_id)
        ]

    def position(self, chat_id: int, user_id: int) -> int:
        for i, (c, u) in enumerate(self._queue):
            if c == chat_id and u == user_id:
                return i + 1
        return 0

    def is_queued(self, chat_id: int, user_id: int) -> bool:
        return self.position(chat_id, user_id) > 0

    async def acquire(self, chat_id: int, user_id: int):
        await self._sem.acquire()
        self._remove(chat_id, user_id)

    def release(self):
        self._sem.release()

    def get_stats(self) -> dict:
        return {
            "queued": len(self._queue),
            "active_slots": self._max_concurrent - self._sem._value,
            "max_concurrent": self._max_concurrent,
        }
