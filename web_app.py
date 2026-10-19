"""Web front end for keyword domain search.

Start it with run_server.py, which listens on 127.0.0.1 only, then open http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import cache as cache_module
import godaddy_checker as gc
import search_service as ss
import settings
import trademark

STATIC_DIRECTORY = settings.ROOT / "static"


class SearchIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=settings.MAX_KEYWORD_LENGTH)
    tlds: list[str] = Field(min_length=1, max_length=len(settings.ALLOWED_TLDS))
    max_price: float | None = Field(default=None, ge=0, le=settings.MAX_PRICE_LIMIT)
    max_renewal_price: float | None = Field(default=None, ge=0, le=settings.MAX_PRICE_LIMIT)


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
               cache: cache_module.DomainCache | None = None, screen: trademark.TrademarkScreen | None = None,
               static_dir: Path = STATIC_DIRECTORY) -> FastAPI:
    app = FastAPI(title="Domain Search")
    holder: dict[str, object] = {"store": store, "cache": cache}

    def job_store() -> ss.JobStore:
        # Created on first use, so importing this module does not touch the database.
        if holder["store"] is None:
            holder["store"] = ss.JobStore()
        return holder["store"]  # type: ignore[return-value]

    def domain_cache() -> cache_module.DomainCache:
        if holder["cache"] is None:
            holder["cache"] = cache_module.DomainCache()
        return holder["cache"]  # type: ignore[return-value]

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
        }

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
        try:
            job_id = ss.start_search(job_store(), request, check_fn, domain_cache(), screen)
        except ss.LimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {"id": job_id}

    @app.get("/api/searches/{job_id}")
    def get_search(job_id: str) -> dict[str, object]:
        job = job_store().get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown search")
        return job

    return app


app = create_app()
