"""Web front end for keyword domain search.

Run locally from the project folder:

    python -m uvicorn web_app:app --reload

then open http://127.0.0.1:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import godaddy_checker as gc
import search_service as ss

ROOT = Path(__file__).resolve().parent


class SearchIn(BaseModel):
    keyword: str = Field(min_length=2, max_length=60)
    tlds: list[str] = Field(min_length=1, max_length=len(ss.ALLOWED_TLDS))
    max_price: float | None = Field(default=None, ge=0)
    max_renewal_price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    candidate_limit: int = Field(default=50, ge=1, le=ss.MAX_CANDIDATES)


def create_app(root: Path = ROOT, check_fn: ss.CheckFn = gc.check_batch) -> FastAPI:
    app = FastAPI(title="Domain Search")
    store = ss.JobStore()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(root / "static" / "index.html")

    @app.get("/api/config")
    def public_config() -> dict[str, object]:
        config = gc.load_config(root / "config.json")
        return {"tlds": list(ss.ALLOWED_TLDS), "default_max_price": config["budget"]["max_yearly_price"], "default_currency": config["budget"]["currency"]}

    @app.post("/api/searches", status_code=202)
    def create_search(body: SearchIn) -> dict[str, str]:
        try:
            request = ss.SearchRequest(
                keyword=body.keyword,
                tlds=tuple(body.tlds),
                max_price=body.max_price,
                max_renewal_price=body.max_renewal_price,
                currency=body.currency.upper(),
                candidate_limit=body.candidate_limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"id": ss.start_search(store, request, root, check_fn)}

    @app.get("/api/searches/{job_id}")
    def get_search(job_id: str) -> dict[str, object]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown search")
        return job

    return app


app = create_app()
