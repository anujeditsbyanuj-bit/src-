import asyncio
import logging
from datetime import datetime, timezone, timedelta

from .db import DomainDB
from .sources import fetch_all_sources

logger = logging.getLogger(__name__)


FALLBACK_DOMAINS = [
    "bit.ly", "tinyurl.com", "t.co", "is.gd", "v.gd", "ow.ly", "cutt.ly",
    "rb.gy", "shorturl.at", "rebrand.ly", "t.ly", "tiny.cc", "adf.ly",
    "shorte.st", "sh.st", "bc.vc", "za.gl", "fc.lc", "ouo.io", "ouo.press",
    "exe.io", "linkvertise.com", "shrinkme.io", "shrinkme.click",
    "gplinks.co", "try2link.com", "tnlink.in", "droplink.co",
    "rocklinks.net", "earn4link.in", "tnlink.in", "xpshort.com",
    "adrinolinks.in", "linkpays.in", "shortzzy.in", "ez4short.com",
    "gtlinks.me", "bitlinks.life", "vplink.in", "babylinks.in",
    "earnlinks.in", "get2short.com", "nowshort.com", "arolinks.com",
    "hypershort.com", "techymozo.com", "open2get.in", "linkbnao.com",
    "linksxyz.in", "short-jambo.com", "pi-l.ink", "whatsgrouphub.com",
    "gdtot.com", "hubdrive.tips", "hubdrive.in", "sharer.pw",
    "appdrive.in", "drivefire.net", "kolop.net", "katdrive.net",
    "jiodrive.in", "gadrive.in", "drivebuzz.in",
    "anonfiles.com", "mediafire.com", "gofile.io", "krakenfiles.com",
    "mdisk.me", "pixeldrain.com", "send.cm", "sfile.mobi",
    "wetransfer.com", "zippyshare.com",
    "fembed.com", "mp4upload.com", "streamtape.com", "streamsb.com",
    "sub2unlock.com", "work.ink", "boost.ink",
]


def _fallback_domains() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {"domain": d, "status": "active", "type": "shortener", "notes": "built-in fallback", "source": "fallback"}
        for d in FALLBACK_DOMAINS
    ]


class DomainUpdater:
    def __init__(self, db: DomainDB, refresh_days: int = 7):
        self.db = db
        self.refresh_days = refresh_days
        self._last_refresh: datetime | None = None

    def needs_refresh(self) -> bool:
        if self._last_refresh is None:
            return True
        elapsed = datetime.now(timezone.utc) - self._last_refresh
        return elapsed > timedelta(days=self.refresh_days)

    async def refresh(self, force: bool = False) -> dict:
        if not force and not self.needs_refresh():
            return {"refreshed": False, "reason": "within_ttl"}

        logger.info("Refreshing domain database from community sources...")
        try:
            domains = await fetch_all_sources()
        except Exception as e:
            logger.error(f"Failed to fetch domain sources: {e}")
            domains = []

        if not domains:
            existing = self.db.get_all_domains_by_status("active")
            if not existing:
                logger.warning("No domains from community or DB. Using built-in fallback.")
                domains = _fallback_domains()
            else:
                logger.warning("No domains from community, but DB has existing entries. Skipping refresh.")
                return {"refreshed": False, "reason": "community_failed_db_has_data"}

        self.db.set_domains_bulk(domains)
        self._last_refresh = datetime.now(timezone.utc)

        active = sum(1 for d in domains if d["status"] == "active")
        inactive = sum(1 for d in domains if d["status"] == "inactive")
        logger.info(
            f"Domain database refreshed: {active} active, {inactive} inactive "
            f"({len(domains)} total from community)"
        )
        return {
            "refreshed": True,
            "total": len(domains),
            "active": active,
            "inactive": inactive,
        }
