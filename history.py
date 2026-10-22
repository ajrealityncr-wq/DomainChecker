"""Record of every domain name the app has generated or checked, kept in the app's SQLite file.

Every name is stored once. A search or a generator run only uses names that are not in the
record yet, and then adds them. Once GoDaddy has answered for a name (available or taken),
the answer is stored with it and the name is never generated or sent to GoDaddy again.
A name is released only when it was generated but never answered, so it can be tried later.
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
            db.execute(
                "CREATE TABLE IF NOT EXISTS seen_domains (domain TEXT PRIMARY KEY, first_seen REAL NOT NULL,"
                " checked_at REAL, available INTEGER)"
            )
            # Files created before the answer columns existed get them added in place.
            columns = {row[1] for row in db.execute("PRAGMA table_info(seen_domains)")}
            if "checked_at" not in columns:
                db.execute("ALTER TABLE seen_domains ADD COLUMN checked_at REAL")
            if "available" not in columns:
                db.execute("ALTER TABLE seen_domains ADD COLUMN available INTEGER")
            db.commit()
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def _select(self, query: str, domains: list[str]) -> list[tuple]:
        rows: list[tuple] = []
        with self._lock:
            db = self._connect()
            try:
                for start in range(0, len(domains), CHUNK):
                    chunk = domains[start:start + CHUNK]
                    marks = ",".join("?" * len(chunk))
                    rows.extend(db.execute(query.format(marks=marks), chunk).fetchall())
            finally:
                db.close()
        return rows

    def unseen(self, domains: list[str]) -> list[str]:
        """The domains that have never been generated or checked, in their original order."""
        seen = {row[0] for row in self._select("SELECT domain FROM seen_domains WHERE domain IN ({marks})", domains)}
        return [domain for domain in domains if domain not in seen]

    def checked(self, domains: list[str]) -> dict[str, bool]:
        """GoDaddy's stored answer (True = available) for the domains that were already checked."""
        rows = self._select(
            "SELECT domain, available FROM seen_domains WHERE checked_at IS NOT NULL AND domain IN ({marks})", domains
        )
        return {domain: bool(available) for domain, available in rows}

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

    def mark_checked(self, answers: dict[str, bool]) -> None:
        """Store GoDaddy's answer for each domain. Checked domains are never generated or checked again."""
        if not answers:
            return
        now = time.time()
        with self._lock:
            db = self._connect()
            try:
                db.executemany("INSERT OR IGNORE INTO seen_domains (domain, first_seen) VALUES (?, ?)", [(d, now) for d in answers])
                db.executemany(
                    "UPDATE seen_domains SET checked_at = ?, available = ? WHERE domain = ?",
                    [(now, int(bool(available)), domain) for domain, available in answers.items()],
                )
                db.commit()
            finally:
                db.close()

    def release(self, domains: list[str]) -> None:
        """Make generated-but-unanswered domains available again. Checked domains are never released."""
        if not domains:
            return
        with self._lock:
            db = self._connect()
            try:
                for start in range(0, len(domains), CHUNK):
                    chunk = domains[start:start + CHUNK]
                    marks = ",".join("?" * len(chunk))
                    db.execute(f"DELETE FROM seen_domains WHERE checked_at IS NULL AND domain IN ({marks})", chunk)
                db.commit()
            finally:
                db.close()
