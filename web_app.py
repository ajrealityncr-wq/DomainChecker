"""Web front end for keyword domain search.

Start it with run_server.py, which listens on 127.0.0.1 only, then open http://127.0.0.1:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import godaddy_checker as gc
import search_service as ss
import settings

STATIC_DIRECTORY = settings.ROOT / "static"


class SearchIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=settings.MAX_KEYWORD_LENGTH)
    tlds: list[str] = Field(min_length=1, max_length=len(settings.ALLOWED_TLDS))
    max_price: float | None = Field(default=None, ge=0)


def create_app(check_fn: ss.CheckFn = gc.check_batch, store: ss.JobStore | None = None, static_dir: Path = STATIC_DIRECTORY) -> FastAPI:
    app = FastAPI(title="Domain Search")
    job_store = store or ss.JobStore()

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
        }

    @app.post("/api/searches", status_code=202)
    def create_search(body: SearchIn) -> dict[str, str]:
        try:
            request = ss.SearchRequest(keyword=body.keyword, tlds=tuple(body.tlds), max_price=body.max_price)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": ss.start_search(job_store, request, check_fn)}

    @app.get("/api/searches/{job_id}")
    def get_search(job_id: str) -> dict[str, object]:
        job = job_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown search")
        return job

    return app


app = create_app()
