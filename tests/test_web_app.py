import time

import pytest
from fastapi.testclient import TestClient

import godaddy_checker as gc
import search_service as ss
import settings
import web_app

LOCAL = "http://127.0.0.1:8000"
REAL_LOAD_PAT = gc.load_pat  # the client fixture replaces load_pat; the token tests need the real one


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


def test_delete_search_endpoint(client):
    job_id = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).json()["id"]
    wait_for_job(client, job_id)
    assert client.delete(f"/api/searches/{job_id}").json() == {"id": job_id, "deleted": True}
    assert client.get(f"/api/searches/{job_id}").status_code == 404
    assert client.delete(f"/api/searches/{job_id}").status_code == 404
    assert client.get("/api/domains").json()["total"] > 0


def test_new_extensions_are_offered(client):
    assert {".io", ".app", ".dev"} <= set(client.get("/api/config").json()["tlds"])


def rejected_check(batch, config, pat, logger):
    raise gc.AuthError("HTTP 401")


@pytest.fixture
def client_with_bad_token(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "load_pat", lambda: "bad-token")
    monkeypatch.setattr(gc, "rdap_is_registered", lambda domain, config: None)
    app = web_app.create_app(check_fn=rejected_check, store=ss.JobStore(tmp_path / "app.sqlite3"))
    return TestClient(app, base_url=LOCAL)


def test_token_status_is_reported(client):
    assert client.get("/api/token").json()["status"] == "valid"


def test_search_is_refused_until_the_token_is_valid(client_with_bad_token):
    status = client_with_bad_token.get("/api/token").json()
    assert status["status"] == "invalid"
    response = client_with_bad_token.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]})
    assert response.status_code == 409
    assert "token box" in response.json()["detail"]
    assert client_with_bad_token.get("/api/searches").json()["searches"] == []


def test_missing_token_is_reported(client, monkeypatch):
    monkeypatch.setattr(gc, "load_pat", lambda: None)
    status = client.get("/api/token?refresh=true").json()
    assert status["status"] == "missing"
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).status_code == 409


def test_check_again_picks_up_a_fixed_token(client, monkeypatch):
    monkeypatch.setattr(gc, "load_pat", lambda: None)
    assert client.get("/api/token?refresh=true").json()["status"] == "missing"
    monkeypatch.setattr(gc, "load_pat", lambda: "token")
    assert client.get("/api/token").json()["status"] == "valid"  # a failed check is never reused
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).status_code == 202


def test_a_valid_token_check_is_reused(client, monkeypatch):
    assert client.get("/api/token").json()["status"] == "valid"
    calls = []
    monkeypatch.setattr(gc, "verify_token", lambda check_fn=None: calls.append(1) or {"status": "valid", "message": ""})
    client.get("/api/token")
    client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]})
    assert calls == []
    client.get("/api/token?refresh=true")
    assert calls == [1]


def test_token_saved_from_the_page_is_used_for_searches(client, monkeypatch, tmp_path):
    import os
    monkeypatch.setattr(gc, "load_pat", REAL_LOAD_PAT)  # the saved value must be found by the real loader
    assert client.get("/api/token").json()["status"] == "missing"
    saved = client.post("/api/token", json={"pat": "good-token-123456"})
    assert saved.status_code == 200
    body = saved.json()
    assert body["status"] == "valid"
    assert body["masked"] == "…3456"
    assert "good-token-123456" not in saved.text  # the full token is never sent back
    assert "GODADDY_PAT=good-token-123456" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert os.environ["GODADDY_PAT"] == "good-token-123456"
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).status_code == 202


def test_token_that_fails_the_check_is_not_saved(client, monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "load_pat", REAL_LOAD_PAT)
    def rejected(batch, config, pat, logger):
        raise gc.AuthError("HTTP 401")
    app = web_app.create_app(check_fn=rejected, store=ss.JobStore(tmp_path / "app.sqlite3"))
    bad = TestClient(app, base_url=LOCAL)
    response = bad.post("/api/token", json={"pat": "wrong-token-000000"})
    assert response.status_code == 422
    assert "not saved" in response.json()["detail"]
    assert "wrong-token-000000" not in response.text
    assert not (tmp_path / ".env").exists()


def test_token_is_not_saved_when_goddaddy_cannot_be_reached(client, monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "load_pat", REAL_LOAD_PAT)
    def down(batch, config, pat, logger):
        return [], "network error: timed out"
    app = web_app.create_app(check_fn=down, store=ss.JobStore(tmp_path / "app.sqlite3"))
    response = TestClient(app, base_url=LOCAL).post("/api/token", json={"pat": "some-token-000000"})
    assert response.status_code == 503
    assert not (tmp_path / ".env").exists()


def test_removing_the_token_refuses_searches_again(client, monkeypatch):
    monkeypatch.setattr(gc, "load_pat", REAL_LOAD_PAT)
    client.post("/api/token", json={"pat": "good-token-123456"})
    assert client.delete("/api/token").json()["status"] == "missing"
    assert client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com"]}).status_code == 409


def test_a_short_token_is_refused_before_checking(client, monkeypatch):
    monkeypatch.setattr(gc, "load_pat", REAL_LOAD_PAT)
    assert client.post("/api/token", json={"pat": "short"}).status_code == 422


def test_full_token_never_appears_in_status(client, monkeypatch):
    monkeypatch.setattr(gc, "load_pat", REAL_LOAD_PAT)
    client.post("/api/token", json={"pat": "good-token-123456"})
    status = client.get("/api/token").json()
    assert status["masked"] == "…3456"
    assert "good-token" not in str(status)
