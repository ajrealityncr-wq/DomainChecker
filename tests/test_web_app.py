import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import godaddy_checker as gc
import web_app

ROOT = Path(__file__).resolve().parents[1]


def fake_check(batch, config, pat, logger):
    return [
        gc.CheckResult(domain=d, available=d.startswith("real"), price=800 if d.startswith("real") else None,
                       renewal_price=700 if d.startswith("real") else None, currency="INR", api_success=True)
        for d in batch
    ], None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GODADDY_PAT", "token")
    return TestClient(web_app.create_app(root=ROOT, check_fn=fake_check))


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
    assert "Domain Search" in response.text


def test_config_lists_extensions(client):
    body = client.get("/api/config").json()
    assert body["tlds"] == [".com", ".in", ".ai", ".co"]


def test_rejects_unknown_extension(client):
    response = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".xyz"]})
    assert response.status_code == 422


def test_rejects_missing_extensions(client):
    response = client.post("/api/searches", json={"keyword": "real estate", "tlds": []})
    assert response.status_code == 422


def test_full_search_round_trip(client):
    response = client.post("/api/searches", json={"keyword": "real estate", "tlds": [".com", ".in"], "max_price": 1000, "candidate_limit": 5})
    assert response.status_code == 202
    job = wait_for_job(client, response.json()["id"])
    assert job["status"] == "done"
    assert job["offers"]
    assert all(offer["within_budget"] for offer in job["offers"])
    assert {offer["domain"].rsplit(".", 1)[1] for offer in job["offers"]} <= {"com", "in"}


def test_unknown_search_is_404(client):
    assert client.get("/api/searches/doesnotexist").status_code == 404
