"""Keyword search: readable names that contain the keyword, TLD selection, availability and budget.

A search runs in a background thread. The caller gets a job id, then polls the job store for
progress and offers. Jobs and cached answers are stored in SQLite. Availability always comes
from GoDaddy; RDAP is only used to skip names it reports as registered.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import godaddy_checker as gc
import settings
import trademark
from cache import DomainCache

logger = logging.getLogger("search")
KEYWORD_RE = re.compile(r"^[A-Za-z][A-Za-z \-]*$")
MIN_KEYWORD_WORD = 3

CheckFn = Callable[[list[str], dict[str, Any], str, logging.Logger], tuple[list[gc.CheckResult], str | None]]
Progress = Callable[..., None]


class SearchError(RuntimeError):
    """A user-facing failure, such as a missing token. The message is shown in the UI."""


class LimitError(RuntimeError):
    """The app is at its limit for concurrent or daily searches."""


def _check_price(label: str, value: float | None) -> None:
    if value is None:
        return
    if not math.isfinite(value) or value < 0 or value > settings.MAX_PRICE_LIMIT:
        raise ValueError(f"{label} must be between 0 and {settings.MAX_PRICE_LIMIT:,}")


@dataclass(frozen=True)
class SearchRequest:
    keyword: str
    tlds: tuple[str, ...]
    max_price: float | None = None
    max_renewal_price: float | None = None

    def __post_init__(self) -> None:
        keyword = self.keyword.strip()
        if len(keyword) > settings.MAX_KEYWORD_LENGTH or not KEYWORD_RE.fullmatch(keyword):
            raise ValueError("Keyword may contain only letters, spaces and hyphens")
        if not tokenize_keywords(keyword):
            raise ValueError(f"Keyword needs at least one word of {MIN_KEYWORD_WORD} letters")
        if _is_blocked(re.sub(r"[^a-z]", "", keyword.lower())):
            raise ValueError("That keyword is not allowed")
        if not self.tlds or any(tld not in settings.ALLOWED_TLDS for tld in self.tlds):
            raise ValueError(f"Choose one or more of: {', '.join(settings.ALLOWED_TLDS)}")
        _check_price("max_price", self.max_price)
        _check_price("max_renewal_price", self.max_renewal_price)


def tokenize_keywords(text: str) -> list[str]:
    """Lowercase keyword words of three or more letters, first occurrence kept."""
    words = [word for word in re.split(r"[^a-z]+", text.lower()) if len(word) >= MIN_KEYWORD_WORD]
    return list(dict.fromkeys(words))


def _stem_matches(term: str, stem: str) -> bool:
    return len(stem) >= MIN_KEYWORD_WORD and (stem in term or term in stem)


def infer_industry(keyword: str) -> str | None:
    """The industry profile the keyword words point to, or None. The profile matching most words wins."""
    terms = tokenize_keywords(keyword)
    best, best_count = None, 0
    for industry, stems in settings.INDUSTRY_PROFILES.items():
        count = sum(1 for term in terms if any(_stem_matches(term, stem) for stem in stems))
        if count > best_count:
            best, best_count = industry, count
    return best


def _is_blocked(name: str) -> bool:
    if name in settings.BLOCKED_EXACT:
        return True
    return any(term in name for term in settings.BLOCKED_SUBSTRINGS)


def word_candidates(keyword: str, industry: str | None, limit: int) -> list[str]:
    """Names that contain the keyword, built with affixes and (for a matched industry) its vocabulary.

    Every returned name contains at least one keyword word, so "automation" returns automation
    names and "tag" returns tag names. Affixes are the same for every keyword.
    """
    terms = tokenize_keywords(keyword)
    joined = "".join(terms)
    vocabulary = list(settings.INDUSTRY_VOCABULARY.get(industry, ())) if industry else []
    # A vocabulary word that already contains the keyword ("realty" for "real") is not paired with it again.
    paired = [word for word in vocabulary if not any(term in word for term in terms)]
    prefixes = settings.PREFIXES
    suffixes = settings.SUFFIXES

    raw = [joined]
    raw += [joined + suffix for suffix in suffixes]
    raw += [prefix + joined for prefix in prefixes]
    raw += [term + suffix for term in terms for suffix in suffixes]
    raw += [prefix + term for term in terms for prefix in prefixes]
    raw += [term + word for term in terms for word in paired]
    raw += [word + term for term in terms for word in paired]
    raw += [word + suffix for word in vocabulary for suffix in suffixes]
    raw += [term + second for index, term in enumerate(terms) for second in terms[index + 1:]]

    pattern = re.compile(rf"[a-z]{{{settings.MIN_NAME_LENGTH},{settings.MAX_NAME_LENGTH}}}")
    names: list[str] = []
    seen: set[str] = set()
    for name in raw:
        if name in seen or not pattern.fullmatch(name):
            continue
        if not any(term in name for term in terms):
            continue
        if _is_blocked(name):
            continue
        if any(name.endswith(suffix) for suffix in settings.BLOCKED_SUFFIXES):
            continue
        seen.add(name)
        names.append(name)
    names.sort(key=lambda name: (_rank_tier(name, joined), len(name), name))
    return names[:limit]


def _rank_tier(name: str, joined: str) -> int:
    """0: the exact keyword. 1: keyword with a suffix. 2: keyword with a prefix. 3: everything else."""
    if name == joined:
        return 0
    if name.startswith(joined):
        return 1
    if name.endswith(joined):
        return 2
    return 3


def plan_domains(request: SearchRequest) -> tuple[str | None, list[str]]:
    industry = infer_industry(request.keyword)
    names = word_candidates(request.keyword, industry, settings.CANDIDATES_PER_SEARCH)
    return industry, [name + tld for name in names for tld in request.tlds]


def budget_config(request: SearchRequest) -> dict[str, Any]:
    return {
        "budget": {
            "max_yearly_price": request.max_price,
            "max_renewal_price": request.max_renewal_price,
            "currency": settings.CURRENCY,
            "require_renewal_price": False,
        }
    }


def to_offer(result: gc.CheckResult, budget: dict[str, Any]) -> dict[str, Any]:
    record = {"price": result.price, "currency": result.currency, "renewal_price": result.renewal_price}
    return {
        "domain": result.domain,
        "price": result.price,
        "renewal_price": result.renewal_price,
        "currency": result.currency,
        "within_budget": gc.meets_budget(record, budget),
    }


def _registered_status(domains: list[str], config: dict[str, Any], cache: DomainCache, progress: Progress) -> dict[str, bool | None]:
    """RDAP answers for every domain, in parallel. Cached answers are reused."""
    status: dict[str, bool | None] = dict(cache.get_many("rdap", domains))
    todo = [domain for domain in domains if domain not in status]
    progress(total=len(domains), checked=len(status), stage="Checking for registered names")
    done = 0
    with ThreadPoolExecutor(max_workers=settings.RDAP_WORKERS) as pool:
        futures = {pool.submit(gc.rdap_is_registered, domain, config): domain for domain in todo}
        for future in as_completed(futures):
            domain = futures[future]
            try:
                status[domain] = future.result()
            except Exception:
                logger.exception("RDAP lookup failed for %s", domain)
                status[domain] = None
            done += 1
            if done % 10 == 0 or done == len(todo):
                progress(checked=len(status))
    cache.put_many("rdap", {domain: status[domain] for domain in todo if status[domain] is not None})
    return status


def execute(request: SearchRequest, check_fn: CheckFn, progress: Progress,
            cache: DomainCache | None = None, screen: trademark.TrademarkScreen | None = None) -> dict[str, Any]:
    """Run one search end to end. `progress` receives keyword updates for the job record."""
    config = gc.default_config()
    cache = cache or DomainCache()
    screen = screen or trademark.NotConfiguredScreen()
    industry, domains = plan_domains(request)
    progress(industry=industry, total=len(domains), checked=0)

    pat = gc.load_pat()
    if not pat:
        raise SearchError("GoDaddy token is not configured. Set GODADDY_PAT in the environment or .env, then restart.")

    registered = _registered_status(domains, config, cache, progress)
    remaining = [domain for domain in domains if registered.get(domain) is not True]
    skipped_registered = len(domains) - len(remaining)

    budget = budget_config(request)
    known = cache.get_many("godaddy", remaining)
    to_check = [domain for domain in remaining if domain not in known]
    results = [gc.result_from_record(record) for record in known.values() if record.get("available") in {True, False}]
    checked = len(known)
    progress(total=len(remaining), checked=checked, skipped_registered=skipped_registered, stage="Checking availability with GoDaddy")

    failed: list[str] = []
    new_records: dict[str, Any] = {}
    for batch in gc.batches(to_check, config["batch_size"]):
        batch_results, error = check_fn(batch, config, pat, logger)
        if error:
            logger.warning("Batch failed: %s", error)
            failed.extend(batch)
        else:
            results.extend(batch_results)
            new_records.update({result.domain: gc.record_for_output(result) for result in batch_results})
        checked += len(batch)
        progress(checked=checked)
    cache.put_many("godaddy", new_records)

    offers = [to_offer(result, budget) for result in results if result.available is True]
    offers = trademark.apply_screen(offers, screen)
    offers.sort(key=lambda offer: (not offer["within_budget"], offer["price"] is None, offer["price"] or 0, offer["domain"]))
    return {
        "offers": offers,
        "failed": failed,
        "skipped_registered": skipped_registered,
        "trademark_checked": not isinstance(screen, trademark.NotConfiguredScreen),
    }


class JobStore:
    """Job records in SQLite. Jobs running at shutdown are marked failed on start; old jobs are removed."""

    def __init__(self, path: Path = settings.APP_DATABASE) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL,"
                " created_at REAL NOT NULL, body TEXT NOT NULL)"
            )
            for job_id, body in db.execute("SELECT id, body FROM jobs WHERE status IN ('queued', 'running')").fetchall():
                job = json.loads(body)
                job.update(status="failed", error="The server stopped during this search. Run it again.")
                db.execute("UPDATE jobs SET status = ?, body = ? WHERE id = ?", ("failed", json.dumps(job), job_id))
            db.execute("DELETE FROM jobs WHERE created_at < ?", (time.time() - settings.JOB_RETENTION_DAYS * 86400,))
            db.commit()
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=10)

    def _load(self, job_id: str) -> dict[str, Any] | None:
        db = self._connect()
        try:
            row = db.execute("SELECT body FROM jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            db.close()
        return json.loads(row[0]) if row else None

    def _save(self, job: dict[str, Any]) -> None:
        db = self._connect()
        try:
            db.execute(
                "INSERT OR REPLACE INTO jobs (id, status, created_at, body) VALUES (?, ?, ?, ?)",
                (job["id"], job["status"], job["created_at"], json.dumps(job)),
            )
            db.commit()
        finally:
            db.close()

    def create(self, request: SearchRequest) -> str:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "status": "queued",
            "stage": "Queued",
            "created_at": time.time(),
            "keyword": request.keyword.strip(),
            "tlds": list(request.tlds),
            "max_price": request.max_price,
            "max_renewal_price": request.max_renewal_price,
            "industry": None,
            "total": 0,
            "checked": 0,
            "skipped_registered": 0,
            "trademark_checked": False,
            "offers": [],
            "failed": [],
            "error": None,
        }
        with self._lock:
            self._save(job)
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._load(job_id)
            if job is None:
                raise KeyError(job_id)
            job.update(fields)
            self._save(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load(job_id)

    def active_count(self) -> int:
        db = self._connect()
        try:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')").fetchone()[0]
        finally:
            db.close()

    def created_since(self, since: float) -> int:
        db = self._connect()
        try:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE created_at >= ?", (since,)).fetchone()[0]
        finally:
            db.close()


def run_job(store: JobStore, job_id: str, request: SearchRequest, check_fn: CheckFn,
            cache: DomainCache | None = None, screen: trademark.TrademarkScreen | None = None) -> None:
    """Run a search and record its outcome. Every failure lands on the job, never on the thread."""
    store.update(job_id, status="running", stage="Starting")
    try:
        outcome = execute(request, check_fn, lambda **fields: store.update(job_id, **fields), cache, screen)
    except (SearchError, gc.AuthError) as exc:
        store.update(job_id, status="failed", stage="Stopped", error=str(exc))
    except Exception:
        logger.exception("search %s failed", job_id)
        store.update(job_id, status="failed", stage="Stopped", error="The search failed. See the server log for details.")
    else:
        store.update(
            job_id,
            status="done",
            stage="Finished",
            offers=outcome["offers"],
            failed=outcome["failed"],
            skipped_registered=outcome["skipped_registered"],
            trademark_checked=outcome["trademark_checked"],
        )


_start_lock = threading.Lock()


def start_search(store: JobStore, request: SearchRequest, check_fn: CheckFn = gc.check_batch,
                 cache: DomainCache | None = None, screen: trademark.TrademarkScreen | None = None) -> str:
    """Check the limits, create the job and start it in a background thread."""
    with _start_lock:  # the limit check and the job creation must happen together
        if store.active_count() >= settings.MAX_CONCURRENT_SEARCHES:
            raise LimitError("Two searches are already running. Wait for one to finish, then try again.")
        if store.created_since(time.time() - 86400) >= settings.MAX_SEARCHES_PER_DAY:
            raise LimitError("The daily search limit has been reached. Try again tomorrow.")
        job_id = store.create(request)
    threading.Thread(target=run_job, args=(store, job_id, request, check_fn, cache, screen), daemon=True).start()
    return job_id
