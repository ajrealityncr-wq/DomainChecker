import time

import pytest
from fastapi.testclient import TestClient

import cache as cache_module
import godaddy_checker as gc
import search_service as ss
import settings
import web_app

LOCAL = "http://127.0.0.1:8000"


def fake_check(batch, config, pat, logger):
    return [
        gc.CheckResult(domain=d, available=d.startswith("real"), price=800 if d.startswith("real") else None,
                       renewal_price=700 if d.startswith("real") else None, currency="INR", api_success=True)
        for d in batch
    ], None


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "load_pat", lambda: "token")
    monkeypatch.setattr(gc, "rdap_is_registered", lambda domain, config: None)
    store = ss.JobStore(tmp_path / "app.sqlite3")
    cache = cache_module.DomainCache(tmp_path / "app.sqlite3")
    app = web_app.create_app(check_fn=fake_check, store=store, cache=cache)
    return TestClient(app, base_url=LOCAL)


def wait_for_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/searches/{job_id}").json()
        if job["status"] in {"done", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError("search did not finish")


def test_index_serves_the_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="keyword"' in response.text
    assert 'id="max_renewal_price"' in response.text
    assert "Run again" in response.text
    assert "<th>Brand</th>" in response.text


def test_config_lists_extensions_and_defaults(client):
    body = client.get("/api/config").json()
    assert body["tlds"] == list(settings.ALLOWED_TLDS)
    assert body["default_tlds"] == list(settings.DEFAULT_TLDS)
    assert body["trademark_checked"] is False


def test_rejects_other_host(client):
    response = client.get("/api/config", headers={"host": "evil.example:8000"})
    assert response.status_code == 403


def test_rejects_post_from_other_origin(client):
    response = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}, headers={"origin": "https://evil.example"})
    assert response.status_code == 403


def test_accepts_post_from_local_origin(client):
    response = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}, headers={"origin": LOCAL})
    assert response.status_code == 202


def test_rejects_unknown_extension(client):
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": [".xyz"]}).status_code == 422


def test_rejects_missing_extensions(client):
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": []}).status_code == 422


def test_rejects_negative_price(client):
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"], "max_price": -5}).status_code == 422


def test_rejects_price_above_limit(client):
    too_high = settings.MAX_PRICE_LIMIT + 1
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"], "max_price": too_high}).status_code == 422


def test_full_search_round_trip(client):
    response = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com", ".in"], "max_price": 1000, "max_renewal_price": 800})
    assert response.status_code == 202
    job = wait_for_job(client, response.json()["id"])
    assert job["status"] == "done"
    assert job["offers"]
    assert all(offer["within_budget"] for offer in job["offers"])
    assert job["max_renewal_price"] == 800
    assert all("brand_score" in offer and "brand_notes" in offer for offer in job["offers"])
    scores = [offer["brand_score"] for offer in job["offers"]]
    assert scores == sorted(scores, reverse=True)  # all within budget, so strongest brand first
    assert {offer["domain"].rsplit(".", 1)[1] for offer in job["offers"]} <= {"com", "in"}


def test_unknown_search_is_404(client):
    assert client.get("/api/searches/doesnotexist").status_code == 404


def test_daily_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SEARCHES_PER_DAY", 0)
    response = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]})
    assert response.status_code == 429
