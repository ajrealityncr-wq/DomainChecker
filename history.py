"""Record of every domain name the app has generated or checked, kept in the app's SQLite file.

Every name is stored once. A search or a generator run only uses names that are not in the
record yet, and then adds them. Once GoDaddy has answered for a name (available or taken),
the answer is stored with it and the name is never generated or sent to GoDaddy again.
A name is released only when it was generated but never answered, so it can be tried later.
"""

from __future__ import annotations

import json
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
    ("brand_score", "INTEGER"),
    ("brand_notes", "TEXT"),
    ("starred", "INTEGER"),
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

    def mark_seen(self, domains: list[str], keyword: str | None = None,
                  brand: dict[str, tuple[int, list[str]]] | None = None) -> None:
        """Record generated names, with the keyword and brand score (score, notes) they were made for."""
        if not domains:
            return
        now = time.time()
        brand = brand or {}
        rows = []
        for d in domains:
            score, notes = brand.get(d, (None, None))
            rows.append((d, now, keyword, score, None if notes is None else json.dumps(notes)))
        with self._lock:
            db = self._connect()
            try:
                db.executemany("INSERT OR IGNORE INTO seen_domains (domain, first_seen, keyword, brand_score, brand_notes)"
                               " VALUES (?, ?, ?, ?, ?)", rows)
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

    def release_unanswered(self) -> int:
        """Make every generated-but-unanswered domain available again. Used at startup, when no search
        is running, so names reserved by a search that was interrupted are not lost."""
        with self._lock:
            db = self._connect()
            try:
                count = db.execute("DELETE FROM seen_domains WHERE checked_at IS NULL").rowcount
                db.commit()
            finally:
                db.close()
        return count

    def library(self, limit: int | None = None, offset: int = 0,
                domains: list[str] | None = None) -> tuple[list[dict[str, Any]], int]:
        """Domains with a known answer, newest first, and how many there are in total.

        With `domains`, only those domains (at most CHUNK of them) are returned.
        """
        where = "checked_at IS NOT NULL"
        params: list[Any] = []
        if domains is not None:
            params = list(domains[:CHUNK])
            where += f" AND domain IN ({','.join('?' * len(params))})"
        with self._lock:
            db = self._connect()
            try:
                total = db.execute(f"SELECT COUNT(*) FROM seen_domains WHERE {where}", params).fetchone()[0]
                rows = db.execute(
                    "SELECT domain, keyword, available, price, renewal_price, currency, checked_at, brand_score, brand_notes,"
                    f" starred FROM seen_domains WHERE {where} ORDER BY checked_at DESC, domain LIMIT ? OFFSET ?",
                    [*params, -1 if limit is None else limit, offset],
                ).fetchall()
            finally:
                db.close()
        return [
            {"domain": domain, "keyword": keyword, "available": bool(available), "price": price,
             "renewal_price": renewal_price, "currency": currency, "checked_at": checked_at,
             "brand_score": brand_score, "brand_notes": None if brand_notes is None else json.loads(brand_notes),
             "starred": bool(starred)}
            for domain, keyword, available, price, renewal_price, currency, checked_at, brand_score, brand_notes, starred in rows
        ], total

    def set_starred(self, domain: str, starred: bool) -> bool:
        """Add a domain to the shortlist or remove it. False when the domain is not in the record."""
        with self._lock:
            db = self._connect()
            try:
                changed = db.execute("UPDATE seen_domains SET starred = ? WHERE domain = ?", (int(starred), domain)).rowcount
                db.commit()
            finally:
                db.close()
        return changed > 0

    def starred(self) -> list[str]:
        with self._lock:
            db = self._connect()
            try:
                rows = db.execute("SELECT domain FROM seen_domains WHERE starred = 1 ORDER BY domain").fetchall()
            finally:
                db.close()
        return [row[0] for row in rows]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
