"""Keyword search: readable names that contain the keyword, TLD selection, availability and budget.

A search runs in a background thread. The caller gets a job id, then polls the job store for
progress and offers. Jobs are stored in SQLite, so they survive a restart. Availability always
comes from GoDaddy; RDAP is only used to skip names it reports as registered.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import godaddy_checker as gc
import settings

logger = logging.getLogger("search")
KEYWORD_RE = re.compile(r"^[A-Za-z][A-Za-z \-]*$")
MIN_KEYWORD_WORD = 3

CheckFn = Callable[[list[str], dict[str, Any], str, logging.Logger], tuple[list[gc.CheckResult], str | None]]


class SearchError(RuntimeError):
    """A user-facing failure, such as a missing token. The message is shown in the UI."""


@dataclass(frozen=True)
class SearchRequest:
    keyword: str
    tlds: tuple[str, ...]
    max_price: float | None = None

    def __post_init__(self) -> None:
        keyword = self.keyword.strip()
        if len(keyword) > settings.MAX_KEYWORD_LENGTH or not KEYWORD_RE.fullmatch(keyword):
            raise ValueError("Keyword may contain only letters, spaces and hyphens")
        if not tokenize_keywords(keyword):
            raise ValueError(f"Keyword needs at least one word of {MIN_KEYWORD_WORD} letters")
        if not self.tlds or any(tld not in settings.ALLOWED_TLDS for tld in self.tlds):
            raise ValueError(f"Choose one or more of: {', '.join(settings.ALLOWED_TLDS)}")
        if self.max_price is not None and self.max_price < 0:
            raise ValueError("max_price cannot be negative")


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
        if any(term in name for term in settings.BLOCKED_TERMS):
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
            "max_renewal_price": None,
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


def execute(request: SearchRequest, check_fn: CheckFn, progress: Callable[..., None]) -> dict[str, Any]:
    """Run one search end to end. `progress` receives keyword updates for the job record."""
    config = gc.default_config()
    industry, domains = plan_domains(request)
    progress(industry=industry, total=len(domains), checked=0)

    pat = gc.load_pat()
    if not pat:
        raise SearchError("GoDaddy token is not configured. Set GODADDY_PAT in the environment or .env, then restart.")

    remaining: list[str] = []
    skipped_registered = 0
    for domain in domains:
        if gc.rdap_is_registered(domain, config) is True:
            skipped_registered += 1
        else:
            remaining.append(domain)
    progress(total=len(remaining), skipped_registered=skipped_registered)

    budget = budget_config(request)
    results: list[gc.CheckResult] = []
    failed: list[str] = []
    checked = 0
    for batch in gc.batches(remaining, config["batch_size"]):
        batch_results, error = check_fn(batch, config, pat, logger)
        if error:
            logger.warning("Batch failed: %s", error)
            failed.extend(batch)
        else:
            results.extend(batch_results)
        checked += len(batch)
        progress(checked=checked)

    offers = [to_offer(result, budget) for result in results if result.available is True]
    offers.sort(key=lambda offer: (not offer["within_budget"], offer["price"] is None, offer["price"] or 0, offer["domain"]))
    return {"offers": offers, "failed": failed, "skipped_registered": skipped_registered}


class JobStore:
    """Job records in SQLite. Jobs that were running when the server stopped are marked failed on start."""

    def __init__(self, path: Path = settings.JOBS_DATABASE) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            for job_id, body in db.execute("SELECT id, body FROM jobs").fetchall():
                job = json.loads(body)
                if job["status"] in {"queued", "running"}:
                    job.update(status="failed", error="The server stopped during this search. Run it again.")
                    db.execute("UPDATE jobs SET body = ? WHERE id = ?", (json.dumps(job), job_id))
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
            db.execute("INSERT OR REPLACE INTO jobs (id, body) VALUES (?, ?)", (job["id"], json.dumps(job)))
            db.commit()
        finally:
            db.close()

    def create(self, request: SearchRequest) -> str:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "status": "queued",
            "keyword": request.keyword.strip(),
            "tlds": list(request.tlds),
            "industry": None,
            "total": 0,
            "checked": 0,
            "skipped_registered": 0,
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


def run_job(store: JobStore, job_id: str, request: SearchRequest, check_fn: CheckFn) -> None:
    """Run a search and record its outcome. Every failure lands on the job, never on the thread."""
    store.update(job_id, status="running")
    try:
        outcome = execute(request, check_fn, lambda **fields: store.update(job_id, **fields))
    except (SearchError, gc.AuthError) as exc:
        store.update(job_id, status="failed", error=str(exc))
    except Exception:
        logger.exception("search %s failed", job_id)
        store.update(job_id, status="failed", error="The search failed. See the server log for details.")
    else:
        store.update(job_id, status="done", offers=outcome["offers"], failed=outcome["failed"], skipped_registered=outcome["skipped_registered"])


def start_search(store: JobStore, request: SearchRequest, check_fn: CheckFn = gc.check_batch) -> str:
    job_id = store.create(request)
    threading.Thread(target=run_job, args=(store, job_id, request, check_fn), daemon=True).start()
    return job_id
