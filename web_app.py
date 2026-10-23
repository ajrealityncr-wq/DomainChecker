"""Web front end for keyword domain search.

Start it with run_server.py, which listens on 127.0.0.1 only, then open http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import godaddy_checker as gc
import search_service as ss
import settings
import trademark
from history import DomainHistory

STATIC_DIRECTORY = settings.ROOT / "static"


class SearchIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=settings.MAX_KEYWORD_LENGTH)
    tlds: list[str] = Field(min_length=1, max_length=len(settings.ALLOWED_TLDS))
    max_price: float | None = Field(default=None, ge=0, le=settings.MAX_PRICE_LIMIT)
    max_renewal_price: float | None = Field(default=None, ge=0, le=settings.MAX_PRICE_LIMIT)


class RecheckIn(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=settings.MAX_RECHECK_DOMAINS)


class StarIn(BaseModel):
    starred: bool


def _configure_search_log() -> None:
    """Write search warnings and errors to output/search.log as well as the console."""
    if any(isinstance(handler, logging.FileHandler) for handler in logging.getLogger("search").handlers):
        return
    settings.SEARCH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(settings.SEARCH_LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("search").addHandler(handler)
    logging.getLogger("search").setLevel(logging.INFO)


def create_app(check_fn: ss.CheckFn = gc.check_batch, store: ss.JobStore | None = None,
               screen: trademark.TrademarkScreen | None = None, static_dir: Path = STATIC_DIRECTORY) -> FastAPI:
    app = FastAPI(title="Domain Search")
    holder: dict[str, object] = {"store": store}
    token_lock = threading.Lock()
    token_state: dict[str, object] = {}

    def token_status(refresh: bool = False) -> dict[str, object]:
        """The GoDaddy token check. A valid result is reused for TOKEN_CHECK_TTL_SECONDS; any other
        result is checked again next time, so a fixed token or a passing network blip is noticed at once."""
        with token_lock:
            fresh = (token_state.get("status") == "valid"
                     and time.time() - float(token_state.get("checked_at", 0)) < settings.TOKEN_CHECK_TTL_SECONDS)
            if refresh or not fresh:
                token_state.clear()
                token_state.update(gc.verify_token(check_fn), checked_at=time.time())
            return dict(token_state)

    def require_valid_token() -> None:
        status = token_status()
        if status["status"] != "valid":
            raise HTTPException(status_code=409, detail=status["message"])

    def job_store() -> ss.JobStore:
        # Created on first use, so importing this module does not touch the database.
        if holder["store"] is None:
            holder["store"] = ss.JobStore()
        return holder["store"]  # type: ignore[return-value]

    @app.middleware("http")
    async def local_requests_only(request: Request, call_next):
        # Blocks DNS rebinding (wrong Host) and pages on other sites calling the API (wrong Origin).
        if request.headers.get("host", "") not in settings.ALLOWED_HOSTS:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD"} and origin is not None and origin not in settings.ALLOWED_ORIGINS:
            return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
        return await call_next(request)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/config")
    def public_config() -> dict[str, object]:
        return {
            "tlds": list(settings.ALLOWED_TLDS),
            "default_tlds": list(settings.DEFAULT_TLDS),
            "default_max_price": settings.DEFAULT_MAX_PRICE,
            "currency": settings.CURRENCY,
            "trademark_checked": False,
            "max_recheck": settings.MAX_RECHECK_DOMAINS,
            "job_retention_days": settings.JOB_RETENTION_DAYS,
        }

    @app.get("/api/token")
    def check_token(refresh: bool = False) -> dict[str, object]:
        """Whether the GoDaddy token works. The page checks this first and asks for a fix if it does not."""
        return token_status(refresh)

    @app.post("/api/searches", status_code=202)
    def create_search(body: SearchIn) -> dict[str, str]:
        _configure_search_log()
        try:
            request = ss.SearchRequest(
                keyword=body.keyword,
                tlds=tuple(body.tlds),
                max_price=body.max_price,
                max_renewal_price=body.max_renewal_price,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        require_valid_token()
        try:
            job_id = ss.start_search(job_store(), request, check_fn, screen)
        except ss.LimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {"id": job_id}

    @app.get("/api/searches")
    def list_searches() -> dict[str, object]:
        return {"searches": job_store().list_jobs()}

    @app.get("/api/domains")
    def list_domains(offset: int = Query(default=0, ge=0)) -> dict[str, object]:
        return {**ss.domain_library(offset=offset), "page_size": settings.DOMAINS_PAGE_SIZE, "currency": settings.CURRENCY}

    @app.post("/api/domains/recheck")
    def recheck_domains(body: RecheckIn) -> dict[str, object]:
        """Send the chosen domains to GoDaddy again. Only runs when the user asks for it."""
        domains = [domain.strip().lower() for domain in body.domains]
        require_valid_token()
        try:
            return {"domains": ss.recheck(domains, check_fn)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (ss.SearchError, gc.AuthError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/shortlist")
    def shortlist() -> dict[str, object]:
        return {"domains": DomainHistory().starred()}

    @app.put("/api/shortlist/{domain}")
    def set_shortlist(domain: str, body: StarIn) -> dict[str, object]:
        if not DomainHistory().set_starred(domain.lower(), body.starred):
            raise HTTPException(status_code=404, detail="Unknown domain")
        return {"domain": domain.lower(), "starred": body.starred}

    @app.delete("/api/searches/{job_id}")
    def delete_search(job_id: str) -> dict[str, object]:
        """Remove a search from History. Its domains stay in All domains."""
        outcome = job_store().delete(job_id)
        if outcome == "missing":
            raise HTTPException(status_code=404, detail="Unknown search")
        if outcome == "running":
            raise HTTPException(status_code=409, detail="Stop the search before deleting it")
        return {"id": job_id, "deleted": True}

    @app.post("/api/searches/{job_id}/stop", status_code=202)
    def stop_search(job_id: str) -> dict[str, object]:
        """Stop a running search after its current batch. The names found so far are kept."""
        if job_store().get(job_id) is None:
            raise HTTPException(status_code=404, detail="Unknown search")
        if not job_store().request_stop(job_id):
            raise HTTPException(status_code=409, detail="This search is not running")
        return {"id": job_id, "stop_requested": True}

    @app.get("/api/searches/{job_id}")
    def get_search(job_id: str) -> dict[str, object]:
        job = job_store().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown search")
        return job

    return app


app = create_app()
