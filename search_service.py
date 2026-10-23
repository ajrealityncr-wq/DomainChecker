"""Keyword search: readable names that contain the keyword, TLD selection, availability and budget.

A search runs in a background thread. The caller gets a job id, then polls the job store for
progress and offers. Jobs and every checked domain are stored in SQLite. Availability always
comes from GoDaddy; RDAP is only used to skip names it reports as registered.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import brand
import godaddy_checker as gc
import settings
import trademark
from history import DomainHistory

logger = logging.getLogger("search")
_plan_lock = threading.Lock()
KEYWORD_RE = re.compile(r"^[A-Za-z][A-Za-z \-]*$")
MIN_KEYWORD_WORD = 3

CheckFn = Callable[[list[str], dict[str, Any], str, logging.Logger], tuple[list[gc.CheckResult], str | None]]
Progress = Callable[..., None]


class SearchError(RuntimeError):
    """A user-facing failure, such as a missing token. The message is shown in the UI."""


class ExhaustedError(SearchError):
    """Every name for this keyword and these extensions has been used. Carries what to try next."""

    def __init__(self, message: str, suggestions: dict[str, list[str]]) -> None:
        super().__init__(message)
        self.suggestions = suggestions


class LimitError(RuntimeError):
    """The app is at its limit for concurrent or daily searches."""


class _StopRequested(Exception):
    """The user pressed Stop. Raised between GoDaddy batches."""


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
    raw += [term + word for term in terms for word in paired]
    raw += [word + term for term in terms for word in paired]
    raw += [word + suffix for word in vocabulary for suffix in suffixes]
    raw += [term + second for index, term in enumerate(terms) for second in terms[index + 1:]]
    # Prefix + keyword + suffix gives a much larger pool for short keywords. These rank last.
    raw += [prefix + joined + suffix for prefix in prefixes for suffix in suffixes]
    # Inflections (automations, automationer, ...) and two suffixes in a row (automationhubpro).
    raw += [joined + ending for ending in settings.INFLECTIONS]
    # Short endings (hq, io, ly, ...) only go last and never in pairs: "automationhqio" reads badly.
    raw += [joined + first + second for first in suffixes for second in suffixes
            if first != second and first not in settings.END_ONLY_SUFFIXES and second not in settings.END_ONLY_SUFFIXES]

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
    # Keep names that read like a brand. Names with one add-on come before names with two, so
    # stacked names (tagkitbay) are only used once the simpler ones run out. Then best score first,
    # then the simpler pattern and the shorter name.
    scored = {name: brand.score(name, terms)[0] for name in names}
    names = [name for name in names if scored[name] >= settings.MIN_BRAND_SCORE]
    names.sort(key=lambda name: (brand.addon_count(name, terms) >= 2, -scored[name], _rank_tier(name, joined), len(name), name))
    return names[:limit]


def _rank_tier(name: str, joined: str) -> int:
    """0: the exact keyword. 1: keyword with one suffix or inflection. 2: keyword with a prefix. 3: everything else."""
    if name == joined:
        return 0
    if name.startswith(joined) and (name[len(joined):] in settings.SUFFIXES or name[len(joined):] in settings.INFLECTIONS):
        return 1
    if name.endswith(joined):
        return 2
    return 3


def plan_domains(request: SearchRequest) -> tuple[str | None, list[str]]:
    industry = infer_industry(request.keyword)
    names = word_candidates(request.keyword, industry, settings.CANDIDATE_POOL_PER_KEYWORD)
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


def to_taken(domain: str, terms: list[str], reason: str) -> dict[str, Any]:
    """A generated name that is not available, shown in the results next to the available ones."""
    brand_score, brand_notes = brand.score(domain.rsplit(".", 1)[0], terms)
    return {"domain": domain, "available": False, "reason": reason, "brand_score": brand_score, "brand_notes": brand_notes}


def to_offer(result: gc.CheckResult, budget: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    record = {"price": result.price, "currency": result.currency, "renewal_price": result.renewal_price}
    brand_score, brand_notes = brand.score(result.domain.rsplit(".", 1)[0], terms)
    return {
        "domain": result.domain,
        "price": result.price,
        "renewal_price": result.renewal_price,
        "currency": result.currency,
        # None when no price limit is set, so the page can say "no limit set" instead of "within budget".
        "within_budget": gc.meets_budget(record, budget) if gc.has_limits(budget) else None,
        "brand_score": brand_score,
        "brand_notes": brand_notes,
        "buy_url": buy_url(result.domain),
    }




def preview_domains(request: SearchRequest, history: DomainHistory) -> tuple[str | None, list[str]]:
    """The names the next search would check, without reserving them (for the command-line generator)."""
    industry, domains = plan_domains(request)
    return industry, history.unseen(domains)[: settings.CANDIDATES_PER_SEARCH * len(request.tlds)]


def keyword_suggestions(request: SearchRequest, history: DomainHistory) -> dict[str, list[str]]:
    """What to try when a keyword has no new names: related keywords and other extensions that still have names."""
    keyword = request.keyword.strip().lower()
    terms = tokenize_keywords(keyword)
    industry = infer_industry(keyword)
    candidates = list(settings.RELATED_KEYWORDS.get(keyword, ()))
    candidates += terms if len(terms) > 1 else []
    candidates += list(settings.INDUSTRY_VOCABULARY.get(industry, ())) if industry else []
    keywords: list[str] = []
    for candidate in dict.fromkeys(candidates):
        if len(keywords) >= settings.MAX_KEYWORD_SUGGESTIONS:
            break
        if candidate == keyword:
            continue
        try:
            other = SearchRequest(keyword=candidate, tlds=request.tlds)
        except ValueError:
            continue
        if history.unseen(plan_domains(other)[1]):
            keywords.append(candidate)
    extensions = []
    for tld in settings.ALLOWED_TLDS:
        if tld not in request.tlds and history.unseen(plan_domains(SearchRequest(keyword=keyword, tlds=(tld,)))[1]):
            extensions.append(tld)
    return {"keywords": keywords, "extensions": extensions}


def next_domains(request: SearchRequest, history: DomainHistory,
                 plan: tuple[str | None, list[str]] | None = None) -> tuple[str | None, list[str]]:
    """The next names for this request that have never been generated, and record them as generated.

    Runs under one lock, so two searches at the same time cannot take the same name. A search that
    runs many rounds passes its `plan` (from plan_domains) so the names are not built again each round.
    """
    with _plan_lock:
        industry, domains = plan or plan_domains(request)
        unseen = history.unseen(domains)
        if not unseen:
            raise ExhaustedError("No new names are left for this keyword. Try a different keyword or extension.",
                                 keyword_suggestions(request, history))
        chosen = unseen[: settings.CANDIDATES_PER_SEARCH * len(request.tlds)]
        terms = tokenize_keywords(request.keyword)
        scores = {domain: brand.score(domain.rsplit(".", 1)[0], terms) for domain in chosen}
        history.mark_seen(chosen, keyword=request.keyword.strip(), brand=scores)
    return industry, chosen


def _sorted_offers(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Names that break a limit go last; then the strongest brand names, then the cheapest.
    return sorted(offers, key=lambda offer: (offer["within_budget"] is False, -offer["brand_score"], offer["price"] is None, offer["price"] or 0, offer["domain"]))


def execute(request: SearchRequest, check_fn: CheckFn, progress: Progress,
            screen: trademark.TrademarkScreen | None = None, history: DomainHistory | None = None,
            should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Run one search. `progress` receives keyword updates for the job record, including the offers so far.

    The search keeps checking rounds of new names until `should_stop()` returns True (the user pressed
    Stop), the keyword runs out of names, GoDaddy fails a batch, or MAX_ROUNDS_PER_SEARCH is reached.
    """
    config = gc.default_config()
    screen = screen or trademark.NotConfiguredScreen()
    history = history or DomainHistory()
    should_stop = should_stop or (lambda: False)

    pat = gc.load_pat()
    if not pat:
        raise SearchError("GoDaddy token is not configured. Set GODADDY_PAT in the environment or .env, then restart.")

    budget = budget_config(request)
    terms = tokenize_keywords(request.keyword)
    plan = plan_domains(request)  # built once; every round takes the next unused names from it
    offers: list[dict[str, Any]] = []  # each available result is scored once, as it arrives
    taken_names: list[dict[str, Any]] = []  # every generated name that is not available, and why
    failed: list[str] = []
    skipped_registered = 0
    total = checked_before = 0
    exhausted = stopped = False
    suggestions: dict[str, list[str]] = {"keywords": [], "extensions": []}
    industry: str | None = None
    for round_number in range(1, settings.MAX_ROUNDS_PER_SEARCH + 1):
        if should_stop():
            stopped = True
            break
        try:
            industry, domains = next_domains(request, history, plan)
        except ExhaustedError as exc:
            if round_number == 1:
                raise
            exhausted = True
            suggestions = exc.suggestions
            break
        total += len(domains)
        label = f" (round {round_number})" if round_number > 1 else ""
        answered: set[str] = set()  # names whose outcome is known: registered, or answered by GoDaddy
        try:
            progress(industry=industry, total=total, checked=checked_before, stage=f"Checking for registered names{label}")
            registered = gc.rdap_statuses(domains, config, on_progress=lambda done: progress(checked=checked_before + done))
            taken = [domain for domain in domains if registered.get(domain) is True]
            to_check = [domain for domain in domains if registered.get(domain) is not True]
            skipped_registered += len(taken)
            answered.update(taken)
            # Registered names are taken; store that so they are never generated or checked again.
            history.mark_checked({domain: False for domain in taken})
            taken_names.extend(to_taken(domain, terms, "registered") for domain in taken)
            base = checked_before + len(taken)
            progress(checked=base, skipped_registered=skipped_registered, taken=taken_names,
                     stage=f"Checking availability with GoDaddy{label}")

            def on_batch(batch: list[str], batch_results: list[gc.CheckResult], error: str | None, checked: int) -> None:
                answered.update(batch)
                # Store each answer as it arrives, so a stopped run never re-sends what GoDaddy already answered.
                history.mark_checked({result.domain: gc.answer_for(result) for result in batch_results})
                if not error:
                    offers.extend(to_offer(result, budget, terms) for result in batch_results if result.available is True)
                    taken_names.extend(to_taken(result.domain, terms, "taken") for result in batch_results if result.available is not True)
                # Show the names found so far, so the user can stop once they see the one they want.
                progress(checked=base + checked, offers=_sorted_offers(offers), taken=taken_names)
                if should_stop():
                    raise _StopRequested

            _, round_failed = gc.check_in_batches(to_check, config, pat, check_fn, logger, on_batch)
        except _StopRequested:
            # Names that were never sent can be used by a later search.
            history.release([domain for domain in domains if domain not in answered])
            checked_before += len(answered)
            stopped = True
            break
        except BaseException:
            # Names that were never answered can be tried again later.
            history.release([domain for domain in domains if domain not in answered])
            raise
        # Names GoDaddy did not answer for can be tried again later.
        history.release(round_failed)
        failed.extend(round_failed)
        checked_before += len(domains)
        if round_failed:
            break  # GoDaddy is having trouble; released names would only come back in the next round

    return {
        "offers": trademark.apply_screen(_sorted_offers(offers), screen),
        "taken": sorted(taken_names, key=lambda name: (-name["brand_score"], name["domain"])),
        "failed": failed,
        "skipped_registered": skipped_registered,
        "names_checked": checked_before,
        "names_exhausted": exhausted,
        "suggestions": suggestions,
        "stopped": stopped,
        "trademark_checked": not isinstance(screen, trademark.NotConfiguredScreen),
    }


class JobStore:
    """Job records in SQLite. Jobs running at shutdown are marked failed on start; old jobs are removed."""

    def __init__(self, path: Path | None = None) -> None:
        path = path or settings.APP_DATABASE
        self._path = path
        self._lock = threading.Lock()
        self._stop: set[str] = set()  # searches asked to stop; read after every batch, so kept in memory
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL,"
                " created_at REAL NOT NULL, body TEXT NOT NULL)"
            )
            # A search that was running when the server stopped keeps the names it had found.
            for job_id, body in db.execute("SELECT id, body FROM jobs WHERE status IN ('queued', 'running')").fetchall():
                job = json.loads(body)
                job.update(status="done", stage="Interrupted by a server restart", interrupted=True,
                           names_checked=job.get("checked", 0))
                db.execute("UPDATE jobs SET status = ?, body = ? WHERE id = ?", ("done", json.dumps(job), job_id))
            db.execute("DELETE FROM jobs WHERE created_at < ?", (time.time() - settings.JOB_RETENTION_DAYS * 86400,))
            db.commit()
        finally:
            db.close()
        # No search runs while the store starts, so any reserved-but-unanswered name was left by an
        # interrupted search. Releasing it lets a later search check it.
        DomainHistory(path).release_unanswered()

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
            "taken": [],
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

    def request_stop(self, job_id: str) -> bool:
        """Ask a running search to stop after its current batch. False when it is not running."""
        with self._lock:
            job = self._load(job_id)
            if job is None or job["status"] not in {"queued", "running"}:
                return False
            job["stop_requested"] = True
            self._save(job)
            self._stop.add(job_id)
        return True

    def delete(self, job_id: str) -> str:
        """Remove a finished search from History: "deleted", "running" or "missing".
        Its domains stay in the domain record, so they are still never checked twice."""
        with self._lock:
            job = self._load(job_id)
            if job is None:
                return "missing"
            if job["status"] in {"queued", "running"}:
                return "running"
            db = self._connect()
            try:
                db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                db.commit()
            finally:
                db.close()
        return "deleted"

    def stop_requested(self, job_id: str) -> bool:
        return job_id in self._stop

    def list_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        """Past searches, newest first, without their offers (summaries for the history page)."""
        db = self._connect()
        try:
            rows = db.execute("SELECT body FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        finally:
            db.close()
        summaries = []
        for (body,) in rows:
            job = json.loads(body)
            offers = job.get("offers", [])
            summaries.append({
                "id": job["id"],
                "status": job["status"],
                "keyword": job.get("keyword"),
                "tlds": job.get("tlds", []),
                "created_at": job.get("created_at"),
                "max_price": job.get("max_price"),
                "stage": job.get("stage"),
                "checked": job.get("checked", 0),
                "total": job.get("total", 0),
                "currency": next((offer.get("currency") for offer in offers if offer.get("currency")), settings.CURRENCY),
                "available": len(offers),
                "taken": len(job.get("taken", [])),
                "within_budget": sum(1 for offer in offers if offer.get("within_budget") is True),
                "cheapest": min((offer["price"] for offer in offers if isinstance(offer.get("price"), (int, float))), default=None),
                "error": job.get("error"),
            })
        return summaries

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


def _with_details(row: dict[str, Any]) -> dict[str, Any]:
    name, _, extension = row["domain"].rpartition(".")
    if row.get("brand_score") is None:
        # Domains recorded before brand scores were stored.
        row["brand_score"], row["brand_notes"] = brand.score(name, tokenize_keywords(row.get("keyword") or "") or [name])
    row["tld"] = "." + extension
    row["buy_url"] = buy_url(row["domain"])
    return row


def buy_url(domain: str) -> str:
    return settings.BUY_URL.format(domain=domain)


def domain_library(history: DomainHistory | None = None, offset: int = 0,
                   limit: int | None = None) -> dict[str, Any]:
    """A page of the domains the app has checked, newest first, with price, extension and brand score."""
    rows, total = (history or DomainHistory()).library(limit=limit or settings.DOMAINS_PAGE_SIZE, offset=offset)
    return {"domains": [_with_details(row) for row in rows], "total": total, "offset": offset}


def recheck(domains: list[str], check_fn: CheckFn = gc.check_batch, history: DomainHistory | None = None) -> list[dict[str, Any]]:
    """Ask GoDaddy again about domains the user picked, store the new answers and return the updated rows.

    This is the only way a domain is sent to GoDaddy a second time, and only on the user's request.
    """
    history = history or DomainHistory()
    if len(domains) > settings.MAX_RECHECK_DOMAINS:
        raise ValueError(f"Recheck at most {settings.MAX_RECHECK_DOMAINS} domains at a time")
    known = set(history.checked(domains))
    unknown = [domain for domain in domains if domain not in known]
    if unknown:
        raise ValueError(f"Only checked domains can be rechecked: {', '.join(unknown[:5])}")
    pat = gc.load_pat()
    if not pat:
        raise SearchError("GoDaddy token is not configured. Set GODADDY_PAT in the environment or .env, then restart.")
    results, failed = gc.check_in_batches(domains, gc.default_config(), pat, check_fn, logger)
    history.mark_checked({result.domain: gc.answer_for(result) for result in results})
    if failed:
        raise SearchError(f"GoDaddy did not answer for {len(failed)} of the domains. Try again.")
    rows, _ = history.library(domains=domains)
    return [_with_details(row) for row in rows]


class _ThrottledProgress:
    """Writes a running search's progress to the job store at most every PROGRESS_INTERVAL seconds.

    Each write saves the whole job, offers included, so writing after every batch would make a long
    search spend most of its time rewriting the same record. A new stage is written at once.
    """

    PROGRESS_INTERVAL = 0.5

    def __init__(self, store: JobStore, job_id: str) -> None:
        self._store = store
        self._job_id = job_id
        self._pending: dict[str, Any] = {}
        self._written = 0.0

    def __call__(self, **fields: Any) -> None:
        self._pending.update(fields)
        if "stage" in fields or time.monotonic() - self._written >= self.PROGRESS_INTERVAL:
            self.flush()

    def flush(self) -> None:
        if self._pending:
            self._store.update(self._job_id, **self._pending)
            self._pending = {}
        self._written = time.monotonic()


def run_job(store: JobStore, job_id: str, request: SearchRequest, check_fn: CheckFn,
            screen: trademark.TrademarkScreen | None = None) -> None:
    """Run a search and record its outcome. Every failure lands on the job, never on the thread."""
    store.update(job_id, status="running", stage="Starting")
    progress = _ThrottledProgress(store, job_id)
    try:
        outcome = execute(request, check_fn, progress, screen, should_stop=lambda: store.stop_requested(job_id))
    except ExhaustedError as exc:
        progress.flush()
        store.update(job_id, status="failed", stage="Stopped", error=str(exc), suggestions=exc.suggestions)
    except (SearchError, gc.AuthError) as exc:
        progress.flush()
        store.update(job_id, status="failed", stage="Stopped", error=str(exc))
    except Exception:
        logger.exception("search %s failed", job_id)
        progress.flush()
        store.update(job_id, status="failed", stage="Stopped", error="The search failed. See the server log for details.")
    else:
        progress.flush()
        store.update(
            job_id,
            status="done",
            stage="Stopped by you" if outcome["stopped"] else "Finished",
            offers=outcome["offers"],
            taken=outcome["taken"],
            failed=outcome["failed"],
            skipped_registered=outcome["skipped_registered"],
            names_checked=outcome["names_checked"],
            names_exhausted=outcome["names_exhausted"],
            suggestions=outcome["suggestions"],
            stopped=outcome["stopped"],
            trademark_checked=outcome["trademark_checked"],
        )


_start_lock = threading.Lock()


def start_search(store: JobStore, request: SearchRequest, check_fn: CheckFn = gc.check_batch,
                 screen: trademark.TrademarkScreen | None = None) -> str:
    """Check the limits, create the job and start it in a background thread."""
    with _start_lock:  # the limit check and the job creation must happen together
        if store.active_count() >= settings.MAX_CONCURRENT_SEARCHES:
            raise LimitError("Two searches are already running. Wait for one to finish, then try again.")
        if store.created_since(time.time() - 86400) >= settings.MAX_SEARCHES_PER_DAY:
            raise LimitError("The daily search limit has been reached. Try again tomorrow.")
        job_id = store.create(request)
    threading.Thread(target=run_job, args=(store, job_id, request, check_fn, screen), daemon=True).start()
    return job_id
