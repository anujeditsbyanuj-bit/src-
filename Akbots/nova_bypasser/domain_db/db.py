import sqlite3
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shortener_domains (
    domain TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status IN ('active','inactive','deprecated','unknown','malicious')),
    type TEXT NOT NULL DEFAULT 'shortener' CHECK(type IN ('shortener','redirector','tracking','drive')),
    last_seen_active TEXT,
    last_checked TEXT NOT NULL,
    notes TEXT DEFAULT '',
    source TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS bypass_cache (
    original_url TEXT PRIMARY KEY,
    result_url TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    checked_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bypass_cache_expires ON bypass_cache(expires_at);
"""


class DomainDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- Domain status ---

    def get_domain_status(self, domain: str) -> Optional[dict]:
        conn = self.get_connection()
        row = conn.execute(
            "SELECT * FROM shortener_domains WHERE domain = ?", (domain,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def set_domain_status(
        self,
        domain: str,
        status: str,
        type_: str = "shortener",
        notes: str = "",
        source: str = "manual",
    ):
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO shortener_domains
               (domain, status, type, last_seen_active, last_checked, notes, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                domain,
                status,
                type_,
                now if status == "active" else None,
                now,
                notes,
                source,
            ),
        )
        conn.commit()
        conn.close()

    def set_domains_bulk(self, domains: list[dict]):
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        conn.execute("BEGIN")
        for d in domains:
            conn.execute(
                """INSERT OR REPLACE INTO shortener_domains
                   (domain, status, type, last_seen_active, last_checked, notes, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    d["domain"],
                    d.get("status", "active"),
                    d.get("type", "shortener"),
                    now if d.get("status", "active") == "active" else None,
                    now,
                    d.get("notes", ""),
                    d.get("source", "community"),
                ),
            )
        conn.commit()
        conn.close()

    def get_all_domains_by_status(self, status: str) -> list[str]:
        conn = self.get_connection()
        rows = conn.execute(
            "SELECT domain FROM shortener_domains WHERE status = ?", (status,)
        ).fetchall()
        conn.close()
        return [r["domain"] for r in rows]

    def domain_exists(self, domain: str) -> bool:
        conn = self.get_connection()
        row = conn.execute(
            "SELECT 1 FROM shortener_domains WHERE domain = ?", (domain,)
        ).fetchone()
        conn.close()
        return row is not None

    # --- Bypass cache ---

    def get_cached_bypass(self, url: str) -> Optional[str]:
        now = datetime.now(timezone.utc).isoformat()
        conn = self.get_connection()
        row = conn.execute(
            """SELECT result_url FROM bypass_cache
               WHERE original_url = ? AND success = 1 AND expires_at > ?""",
            (url, now),
        ).fetchone()
        conn.close()
        return row["result_url"] if row else None

    def set_bypass_cache(self, original_url: str, result_url: str, ttl_hours: int = 24):
        now = datetime.now(timezone.utc).isoformat()
        from datetime import timedelta

        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        conn = self.get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO bypass_cache
               (original_url, result_url, success, checked_at, expires_at)
               VALUES (?, ?, 1, ?, ?)""",
            (original_url, result_url, now, expires),
        )
        conn.execute("DELETE FROM bypass_cache WHERE expires_at < ?", (now,))
        conn.commit()
        conn.close()

    def get_stats(self) -> dict:
        conn = self.get_connection()
        active = conn.execute(
            "SELECT COUNT(*) FROM shortener_domains WHERE status='active'"
        ).fetchone()[0]
        inactive = conn.execute(
            "SELECT COUNT(*) FROM shortener_domains WHERE status='inactive'"
        ).fetchone()[0]
        total_domains = conn.execute(
            "SELECT COUNT(*) FROM shortener_domains"
        ).fetchone()[0]
        cached = conn.execute(
            "SELECT COUNT(*) FROM bypass_cache"
        ).fetchone()[0]
        conn.close()
        return {
            "active_shorteners": active,
            "inactive_shorteners": inactive,
            "total_domains": total_domains,
            "cached_bypasses": cached,
        }
