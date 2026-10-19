"""Per-domain answer cache in SQLite. Entries expire after settings.CACHE_TTL_SECONDS."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import settings


class DomainCache:
    def __init__(self, path: Path = settings.APP_DATABASE, ttl_seconds: float = settings.CACHE_TTL_SECONDS) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS domain_cache ("
                " kind TEXT NOT NULL, domain TEXT NOT NULL, body TEXT NOT NULL, cached_at REAL NOT NULL,"
                " PRIMARY KEY (kind, domain))"
            )
            db.commit()
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def get_many(self, kind: str, domains: list[str], now: float | None = None) -> dict[str, Any]:
        """Fresh entries only, keyed by domain."""
        now = time.time() if now is None else now
        found: dict[str, Any] = {}
        if not domains:
            return found
        with self._lock:
            db = self._connect()
            try:
                for start in range(0, len(domains), 500):
                    chunk = domains[start:start + 500]
                    marks = ",".join("?" * len(chunk))
                    rows = db.execute(
                        f"SELECT domain, body, cached_at FROM domain_cache WHERE kind = ? AND domain IN ({marks})",
                        [kind, *chunk],
                    ).fetchall()
                    for domain, body, cached_at in rows:
                        if now - cached_at <= self._ttl:
                            found[domain] = json.loads(body)
            finally:
                db.close()
        return found

    def put_many(self, kind: str, entries: dict[str, Any], now: float | None = None) -> None:
        if not entries:
            return
        now = time.time() if now is None else now
        with self._lock:
            db = self._connect()
            try:
                db.executemany(
                    "INSERT OR REPLACE INTO domain_cache (kind, domain, body, cached_at) VALUES (?, ?, ?, ?)",
                    [(kind, domain, json.dumps(value), now) for domain, value in entries.items()],
                )
                db.commit()
            finally:
                db.close()
