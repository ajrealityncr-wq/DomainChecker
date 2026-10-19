"""Keyword search: word-based candidates, TLD selection, availability and budget tagging.

A search runs in a background thread. The caller gets a job id, then polls the job store
for progress and offers. Availability always comes from GoDaddy; RDAP is only used to skip
names it reports as registered.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import generate_domains as gd
import godaddy_checker as gc

ALLOWED_TLDS = (".com", ".in", ".ai", ".co")
KEYWORD_RE = re.compile(r"^[A-Za-z][A-Za-z \-]{1,59}$")
MAX_CANDIDATES = 200
MIN_NAME_LENGTH = 4
logger = logging.getLogger("search")

CheckFn = Callable[[list[str], dict[str, Any], str, logging.Logger], tuple[list[gc.CheckResult], str | None]]


class SearchError(RuntimeError):
    """A user-facing failure, such as a missing token. The message is shown in the UI."""


@dataclass(frozen=True)
class SearchRequest:
    keyword: str
    tlds: tuple[str, ...]
    max_price: float | None = None
    max_renewal_price: float | None = None
    currency: str = "INR"
    candidate_limit: int = 50

    def __post_init__(self) -> None:
        if not KEYWORD_RE.fullmatch(self.keyword.strip()):
            raise ValueError("Keyword may contain only letters, spaces and hyphens")
        if not self.tlds or any(tld not in ALLOWED_TLDS for tld in self.tlds):
            raise ValueError(f"Choose one or more of: {', '.join(ALLOWED_TLDS)}")
        if not 1 <= self.candidate_limit <= MAX_CANDIDATES:
            raise ValueError(f"candidate_limit must be between 1 and {MAX_CANDIDATES}")
        for label, value in (("max_price", self.max_price), ("max_renewal_price", self.max_renewal_price)):
            if value is not None and value < 0:
                raise ValueError(f"{label} cannot be negative")


def word_blocklist(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Legal terms, brands and suffixes only. Industry words such as "home" or "real" are not blocked here."""
    legal = config.get("legal", {})
    terms = gd.normalize_list(legal.get("blocked_terms", [])) + gd.normalize_list(config.get("blocked_brands", []))
    suffixes = gd.normalize_list(legal.get("blocked_suffixes", [])) + gd.normalize_list(config.get("blocked_suffixes", []))
    return list(dict.fromkeys(terms)), list(dict.fromkeys(suffixes))


def word_candidates(keyword: str, config: dict[str, Any], industry: str | None, limit: int) -> list[str]:
    """Names that contain the keyword, built with affixes and (when the keyword fits an industry) its vocabulary.

    Every returned name contains at least one keyword word, so "automation" returns automation names
    and "tag" returns tag names. The industry vocabulary is only used when the keyword matched a profile.
    """
    word_cfg = config.get("word_generation", {})
    terms = gd.tokenize_keywords(keyword)
    if not terms:
        raise ValueError("Keyword needs at least one word of three or more letters")
    vocabulary = gd.normalize_list(word_cfg.get("vocabulary_by_industry", {}).get(industry, [])) if industry else []
    # A vocabulary word that already contains the keyword ("realty" for "real") is not paired with it again.
    paired = [word for word in vocabulary if not any(term in word for term in terms)]
    prefixes = gd.normalize_list(word_cfg.get("prefixes", []))
    suffixes = gd.normalize_list(word_cfg.get("suffixes", []))
    max_length = int(word_cfg.get("max_length", 14))
    blocked_terms, blocked_suffixes = word_blocklist(config)

    joined = "".join(terms)
    raw = [joined]
    raw += [joined + suffix for suffix in suffixes]
    raw += [prefix + joined for prefix in prefixes]
    raw += [term + suffix for term in terms for suffix in suffixes]
    raw += [prefix + term for term in terms for prefix in prefixes]
    raw += [term + word for term in terms for word in paired]
    raw += [word + term for term in terms for word in paired]
    raw += [word + suffix for word in vocabulary for suffix in suffixes]
    raw += [term + second for index, term in enumerate(terms) for second in terms[index + 1:]]

    names: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(rf"[a-z]{{{MIN_NAME_LENGTH},{max_length}}}")
    for name in raw:
        if name in seen or not pattern.fullmatch(name):
            continue
        if not any(term in name for term in terms):
            continue
        if any(term in name for term in blocked_terms):
            continue
        if any(name.endswith(suffix) for suffix in blocked_suffixes):
            continue
        seen.add(name)
        names.append(name)
    names.sort(key=lambda name: (_rank_tier(name, joined), len(name), name))
    return names[:limit]


def _rank_tier(name: str, joined: str) -> int:
    """0: the exact keyword. 1: the keyword phrase with an affix. 2: everything else that contains a keyword word."""
    if name == joined:
        return 0
    if joined in name:
        return 1
    return 2


def plan_domains(request: SearchRequest, generation_config: dict[str, Any]) -> tuple[str | None, list[str]]:
    intent = gd.infer_intent(request.keyword, generation_config)
    # Only a keyword that matched an industry profile brings in that industry's vocabulary.
    industry = intent["industry"] if intent["source"] == "keywords" else None
    names = word_candidates(request.keyword, generation_config, industry, request.candidate_limit)
    return industry, [name + tld for name in names for tld in request.tlds]


def budget_config(request: SearchRequest) -> dict[str, Any]:
    return {
        "budget": {
            "max_yearly_price": request.max_price,
            "max_renewal_price": request.max_renewal_price,
            "currency": request.currency,
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


def execute(request: SearchRequest, root: Path, check_fn: CheckFn, progress: Callable[..., None]) -> dict[str, Any]:
    """Run one search end to end. `progress` receives keyword updates for the job record."""
    config = gc.load_config(root / "config.json")
    generation_config = gd.load_generation_config(root / "domain_generation_config.json")
    industry, domains = plan_domains(request, generation_config)
    progress(industry=industry, total=len(domains), checked=0)

    pat = gc.load_pat(None, config, root)
    if not pat:
        raise SearchError("GoDaddy token is not configured. Add GODADDY_PAT to .env and restart.")

    skipped_registered = 0
    if config["prefilter"].get("enabled"):
        remaining = [domain for domain in domains if gc.rdap_is_registered(domain, config) is not True]
        skipped_registered = len(domains) - len(remaining)
        domains = remaining
        progress(total=len(domains), skipped_registered=skipped_registered)

    budget = budget_config(request)
    results: list[gc.CheckResult] = []
    failed: list[str] = []
    checked = 0
    for batch in gc.batches(domains, int(config["batch_size"])):
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
    """In-memory job records. Jobs are lost when the server restarts."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, request: SearchRequest) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
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
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(fields)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {**job, "offers": list(job["offers"]), "failed": list(job["failed"])}


def run_job(store: JobStore, job_id: str, request: SearchRequest, root: Path, check_fn: CheckFn) -> None:
    """Run a search and record its outcome. Every failure lands on the job, never on the thread."""
    store.update(job_id, status="running")
    try:
        outcome = execute(request, root, check_fn, lambda **fields: store.update(job_id, **fields))
    except (SearchError, gc.AuthError) as exc:
        store.update(job_id, status="failed", error=str(exc))
    except Exception:
        logger.exception("search %s failed", job_id)
        store.update(job_id, status="failed", error="The search failed. See the server log for details.")
    else:
        store.update(job_id, status="done", offers=outcome["offers"], failed=outcome["failed"], skipped_registered=outcome["skipped_registered"])


def start_search(store: JobStore, request: SearchRequest, root: Path, check_fn: CheckFn = gc.check_batch) -> str:
    job_id = store.create(request)
    threading.Thread(target=run_job, args=(store, job_id, request, root, check_fn), daemon=True).start()
    return job_id
