import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class OnlineBypassFallback:
    def __init__(self):
        self._session = None
        self.timeout = 15

    async def _get_session(self):
        if self._session is None:
            import aiohttp
            connector = aiohttp.TCPConnector(limit=5)
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/135.0.0.0 Safari/537.36"
                    ),
                },
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def bypass(self, url: str) -> Optional[str]:
        session = await self._get_session()

        methods = [
            ("openunfurl", self._try_openunfurl),
            ("unfurler", self._try_unfurler),
            ("http_redirect", self._try_http_redirect),
        ]

        for name, method in methods:
            try:
                result = await method(session, url)
                if result:
                    logger.debug(f"Online fallback {name} succeeded: {result[:100]}")
                    return result
            except Exception as e:
                logger.debug(f"Online fallback {name} failed: {e}")
                continue

        return None

    async def _try_openunfurl(self, session, url: str) -> Optional[str]:
        try:
            async with session.get(
                "https://openunfurl.vercel.app/api/unfurl",
                params={"url": url},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                resolved = data.get("resolvedUrl", "")
                if resolved and resolved != url:
                    return resolved
        except Exception:
            pass
        return None

    async def _try_unfurler(self, session, url: str) -> Optional[str]:
        try:
            async with session.get(
                "https://unfurler.com/api",
                params={"url": url},
            ) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
                import json
                data = json.loads(text)
                resolved = data.get("url") or data.get("target") or data.get("destination", "")
                if resolved and resolved != url:
                    return resolved
        except Exception:
            pass
        return None

    async def _try_http_redirect(self, session, url: str) -> Optional[str]:
        try:
            async with session.get(
                url,
                allow_redirects=True,
                ssl=None,
            ) as resp:
                final = str(resp.url)
                if final and final != url:
                    return final
        except Exception:
            pass
        return None
