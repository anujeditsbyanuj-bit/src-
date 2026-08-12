import asyncio
import socket
import ssl
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from .db import DomainDB

PROBE_TIMEOUT = 15
STALE_CHECK_HOURS = 24


class DomainChecker:
    def __init__(self, db: DomainDB):
        self.db = db
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=10, force_close=False, enable_cleanup_closed=True
            )
            timeout = aiohttp.ClientTimeout(total=PROBE_TIMEOUT)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _extract_domain(self, url: str) -> Optional[str]:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            parsed = urlparse(url)
            return parsed.hostname.lower()
        except Exception:
            return None

    def check_domain_in_db(self, domain: str) -> Optional[dict]:
        result = self.db.get_domain_status(domain)
        if result:
            return {
                "domain": result["domain"],
                "status": result["status"],
                "type": result["type"],
                "notes": result["notes"],
                "source": result.get("source", "database"),
            }
        return None

    async def probe_domain(self, domain: str) -> dict:
        result = {
            "domain": domain,
            "status": "unknown",
            "dns_resolves": False,
            "http_status": None,
            "error": None,
        }

        try:
            await asyncio.get_event_loop().getaddrinfo(domain, 443)
            result["dns_resolves"] = True
        except OSError as e:
            result["error"] = f"DNS: {e}"
            result["status"] = "inactive"
            return result

        session = await self._get_session()
        for scheme in ("https", "http"):
            try:
                async with session.get(
                    f"{scheme}://{domain}/",
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT),
                    ssl=None,
                ) as resp:
                    result["http_status"] = resp.status
                    if resp.status == 200:
                        result["status"] = "active"
                    elif resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location", "")
                        result["status"] = "active"
                        result["redirect"] = location
                    elif resp.status in (403, 404, 410):
                        result["status"] = "inactive"
                    elif resp.status in (429, 503):
                        result["status"] = "active"
                        result["note"] = "rate_limited"
                    else:
                        result["status"] = "unknown"
                    return result
            except (aiohttp.ClientError, asyncio.TimeoutError, ssl.SSLError) as e:
                result["error"] = f"{scheme}: {type(e).__name__}"
                continue

        result["status"] = "inactive"
        if not result["error"]:
            result["error"] = "connection_failed"
        return result

    def _is_stale(self, last_checked: str) -> bool:
        from datetime import timedelta
        try:
            checked = datetime.fromisoformat(last_checked)
            return (datetime.now(timezone.utc) - checked) > timedelta(hours=STALE_CHECK_HOURS)
        except Exception:
            return True

    async def check_url(self, url: str) -> dict:
        domain = self._extract_domain(url)
        if not domain:
            return {"domain": None, "status": "invalid", "message": "Could not parse URL"}

        db_result = self.check_domain_in_db(domain)
        if db_result:
            last_checked = db_result.get("last_checked", "")
            if not self._is_stale(last_checked):
                return {"domain": domain, **db_result}

        probe_result = await self.probe_domain(domain)
        self.db.set_domain_status(
            domain=probe_result["domain"],
            status=probe_result["status"],
            type_="shortener",
            notes=probe_result.get("error") or probe_result.get("note", ""),
            source="self_probed",
        )
        return {"domain": domain, **probe_result}

    def get_stats(self) -> dict:
        return self.db.get_stats()
