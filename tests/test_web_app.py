import time

import pytest
from fastapi.testclient import TestClient

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
    app = web_app.create_app(check_fn=fake_check, store=store)
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
    assert 'id="tab-history"' in response.text
    assert 'id="tab-domains"' in response.text
    assert "Free first year" in response.text
    assert "3-year cost" in response.text


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


def test_history_lists_every_search_including_repeats(client):
    first = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"], "max_price": 1000}).json()["id"]
    wait_for_job(client, first)
    second = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"], "max_price": 1000}).json()["id"]
    wait_for_job(client, second)

    searches = client.get("/api/searches").json()["searches"]
    assert [s["id"] for s in searches] == [second, first]
    summary = searches[1]
    assert summary["keyword"] == "real estate"
    assert summary["tlds"] == [".com"]
    assert summary["status"] == "done"
    assert summary["available"] > 0
    assert summary["within_budget"] == summary["available"]
    assert summary["cheapest"] == 800
    assert "offers" not in summary


def test_domains_lists_every_checked_domain_with_prices(client):
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    job = wait_for_job(client, job_id)

    body = client.get("/api/domains").json()
    assert body["currency"] == settings.CURRENCY
    domains = {row["domain"]: row for row in body["domains"]}
    assert all(offer["domain"] in domains for offer in job["offers"])
    offer = job["offers"][0]
    row = domains[offer["domain"]]
    assert row["available"] is True
    assert row["price"] == 800
    assert row["renewal_price"] == 700
    assert row["currency"] == "INR"
    assert row["keyword"] == "real estate"
    assert row["tld"] == ".com"
    assert row["brand_score"] == offer["brand_score"]
    taken = [row for row in body["domains"] if not row["available"]]
    assert all(row["price"] is None for row in taken)


def test_domains_are_paged(client, monkeypatch):
    monkeypatch.setattr(settings, "DOMAINS_PAGE_SIZE", 3)
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    wait_for_job(client, job_id)
    first = client.get("/api/domains").json()
    second = client.get("/api/domains?offset=3").json()
    assert first["total"] == second["total"] > 3
    assert len(first["domains"]) == 3
    assert {row["domain"] for row in first["domains"]}.isdisjoint(row["domain"] for row in second["domains"])
    assert client.get("/api/domains?offset=-1").status_code == 422


def test_shortlist_endpoints(client):
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    domain = wait_for_job(client, job_id)["offers"][0]["domain"]
    assert client.put(f"/api/shortlist/{domain}", json={"starred": True}).json() == {"domain": domain, "starred": True}
    assert client.get("/api/shortlist").json() == {"domains": [domain]}
    assert any(row["starred"] for row in client.get("/api/domains").json()["domains"] if row["domain"] == domain)
    assert client.put("/api/shortlist/unknown.com", json={"starred": True}).status_code == 404
    client.put(f"/api/shortlist/{domain}", json={"starred": False})
    assert client.get("/api/shortlist").json() == {"domains": []}


def test_recheck_endpoint(client):
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    domain = wait_for_job(client, job_id)["offers"][0]["domain"]
    body = client.post("/api/domains/recheck", json={"domains": [domain]}).json()
    assert [row["domain"] for row in body["domains"]] == [domain]
    assert body["domains"][0]["available"] is True
    assert client.post("/api/domains/recheck", json={"domains": ["unknown.com"]}).status_code == 422
    too_many = [f"n{i}.com" for i in range(settings.MAX_RECHECK_DOMAINS + 1)]
    assert client.post("/api/domains/recheck", json={"domains": too_many}).status_code == 422


def test_recheck_without_token_is_reported(client, monkeypatch):
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    domain = wait_for_job(client, job_id)["offers"][0]["domain"]
    monkeypatch.setattr(gc, "load_pat", lambda: None)
    response = client.post("/api/domains/recheck", json={"domains": [domain]})
    assert response.status_code == 503
    assert "token" in response.json()["detail"]


def test_history_summary_has_currency_and_progress(client):
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    wait_for_job(client, job_id)
    summary = client.get("/api/searches").json()["searches"][0]
    assert summary["currency"] == "INR"
    assert summary["stage"] == "Finished"
    assert summary["checked"] == summary["total"] > 0


def test_stop_endpoint(client):
    assert client.post("/api/searches/unknown/stop").status_code == 404
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    wait_for_job(client, job_id)
    response = client.post(f"/api/searches/{job_id}/stop")
    assert response.status_code == 409
    assert "not running" in response.json()["detail"]
