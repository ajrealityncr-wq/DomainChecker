"""Record of every domain name the app has generated, so no name is ever produced twice.

Every name is stored once. A search or a generator run only uses names that are not in the
record yet, and then adds them. A name is released again only if its availability check failed,
so that it can be tried later.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import settings

CHUNK = 500


class DomainHistory:
    def __init__(self, path: Path | None = None) -> None:
        path = path or settings.APP_DATABASE
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS seen_domains (domain TEXT PRIMARY KEY, first_seen REAL NOT NULL)")
            db.commit()
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def unseen(self, domains: list[str]) -> list[str]:
        """The domains that have never been generated, in their original order."""
        seen: set[str] = set()
        with self._lock:
            db = self._connect()
            try:
                for start in range(0, len(domains), CHUNK):
                    chunk = domains[start:start + CHUNK]
                    marks = ",".join("?" * len(chunk))
                    rows = db.execute(f"SELECT domain FROM seen_domains WHERE domain IN ({marks})", chunk).fetchall()
                    seen.update(row[0] for row in rows)
            finally:
                db.close()
        return [domain for domain in domains if domain not in seen]

    def mark_seen(self, domains: list[str]) -> None:
        if not domains:
            return
        now = time.time()
        with self._lock:
            db = self._connect()
            try:
                db.executemany("INSERT OR IGNORE INTO seen_domains (domain, first_seen) VALUES (?, ?)", [(d, now) for d in domains])
                db.commit()
            finally:
                db.close()

    def release(self, domains: list[str]) -> None:
        """Make domains available again, for example after their availability check failed."""
        if not domains:
            return
        with self._lock:
            db = self._connect()
            try:
                for start in range(0, len(domains), CHUNK):
                    chunk = domains[start:start + CHUNK]
                    marks = ",".join("?" * len(chunk))
                    db.execute(f"DELETE FROM seen_domains WHERE domain IN ({marks})", chunk)
                db.commit()
            finally:
                db.close()
