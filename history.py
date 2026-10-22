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
from typing import Any

import settings

CHUNK = 500
EXTRA_COLUMNS = (
    ("checked_at", "REAL"),
    ("available", "INTEGER"),
    ("keyword", "TEXT"),
    ("price", "REAL"),
    ("renewal_price", "REAL"),
    ("currency", "TEXT"),
)


class DomainHistory:
    def __init__(self, path: Path | None = None) -> None:
        path = path or settings.APP_DATABASE
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS seen_domains (domain TEXT PRIMARY KEY, first_seen REAL NOT NULL)")
            # Columns added over time; older files get them added in place.
            columns = {row[1] for row in db.execute("PRAGMA table_info(seen_domains)")}
            for name, kind in EXTRA_COLUMNS:
                if name not in columns:
                    db.execute(f"ALTER TABLE seen_domains ADD COLUMN {name} {kind}")
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

    def mark_seen(self, domains: list[str], keyword: str | None = None) -> None:
        if not domains:
            return
        now = time.time()
        with self._lock:
            db = self._connect()
            try:
                db.executemany("INSERT OR IGNORE INTO seen_domains (domain, first_seen, keyword) VALUES (?, ?, ?)",
                               [(d, now, keyword) for d in domains])
                db.commit()
            finally:
                db.close()

    def mark_checked(self, answers: dict[str, bool | dict[str, Any]]) -> None:
        """Store the answer for each domain. Checked domains are never generated or checked again.

        An answer is either True/False (available or not) or a dict with "available" and,
        when GoDaddy gave them, "price", "renewal_price" and "currency".
        """
        if not answers:
            return
        now = time.time()
        rows = []
        for domain, answer in answers.items():
            details = answer if isinstance(answer, dict) else {"available": answer}
            rows.append((now, int(details.get("available") is True), _number(details.get("price")),
                         _number(details.get("renewal_price")), details.get("currency"), domain))
        with self._lock:
            db = self._connect()
            try:
                db.executemany("INSERT OR IGNORE INTO seen_domains (domain, first_seen) VALUES (?, ?)", [(d, now) for d in answers])
                db.executemany(
                    "UPDATE seen_domains SET checked_at = ?, available = ?, price = ?, renewal_price = ?, currency = ?"
                    " WHERE domain = ?",
                    rows,
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

    def library(self) -> list[dict[str, Any]]:
        """Every domain with a known answer, newest first."""
        with self._lock:
            db = self._connect()
            try:
                rows = db.execute(
                    "SELECT domain, keyword, available, price, renewal_price, currency, checked_at FROM seen_domains"
                    " WHERE checked_at IS NOT NULL ORDER BY checked_at DESC, domain"
                ).fetchall()
            finally:
                db.close()
        return [
            {"domain": domain, "keyword": keyword, "available": bool(available), "price": price,
             "renewal_price": renewal_price, "currency": currency, "checked_at": checked_at}
            for domain, keyword, available, price, renewal_price, currency, checked_at in rows
        ]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
