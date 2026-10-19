import time

import pytest

import godaddy_checker as gc
import search_service as ss
import settings


def fake_check(prefix="real", price=1000):
    """Fake GoDaddy: names starting with `prefix` are available."""
    def check(batch, config, pat, logger):
        results = []
        for domain in batch:
            free = domain.startswith(prefix)
            results.append(gc.CheckResult(
                domain=domain,
                available=free,
                price=price if free else None,
                renewal_price=900 if free else None,
                currency="INR" if free else None,
                api_success=True,
            ))
        return results, None
    return check


@pytest.fixture
def online(monkeypatch):
    """Fake the token and the RDAP pre-check so the pipeline runs offline."""
    monkeypatch.setattr(gc, "load_pat", lambda: "token")
    monkeypatch.setattr(gc, "rdap_is_registered", lambda domain, config: None)


@pytest.fixture
def store(tmp_path):
    return ss.JobStore(tmp_path / "jobs.sqlite3")


# --- request validation ---------------------------------------------------

def test_rejects_unknown_extension():
    with pytest.raises(ValueError, match="Choose one or more"):
        ss.SearchRequest(keyword="real estate", tlds=(".xyz",))


def test_rejects_keyword_with_digits_or_symbols():
    with pytest.raises(ValueError, match="letters"):
        ss.SearchRequest(keyword="real estate 2", tlds=(".com",))


def test_rejects_keyword_without_three_letter_word():
    with pytest.raises(ValueError, match="letters"):
        ss.SearchRequest(keyword="ai", tlds=(".com",))


def test_rejects_negative_price():
    with pytest.raises(ValueError):
        ss.SearchRequest(keyword="homes", tlds=(".com",), max_price=-1)


# --- keyword anchoring ----------------------------------------------------

@pytest.mark.parametrize("keyword", ["automation", "tag", "real estate"])
def test_every_name_contains_the_keyword(keyword):
    names = ss.word_candidates(keyword, ss.infer_industry(keyword), 200)
    terms = ss.tokenize_keywords(keyword)
    assert names, f"no candidates for {keyword!r}"
    assert all(any(term in name for term in terms) for name in names)


def test_unmatched_keyword_gets_no_industry_words():
    assert ss.infer_industry("automation") is None
    industry, domains = ss.plan_domains(ss.SearchRequest(keyword="automation", tlds=(".com",)))
    assert industry is None
    assert "automation.com" in domains
    assert not any(word in domain for domain in domains for word in ("realty", "homes", "property", "estate"))


def test_real_estate_matches_its_industry_and_combines_vocabulary():
    assert ss.infer_industry("real estate") == "real_estate"
    names = ss.word_candidates("real estate", "real_estate", 200)
    assert "realestate" in names
    assert any("realty" in name for name in names)
    assert any(name in names for name in ("realhomes", "realrent"))


def test_exact_keyword_first_and_reversed_pairs_dropped():
    names = ss.word_candidates("real estate", "real_estate", 200)
    assert names[0] == "realestate"
    assert "estatereal" not in names


def test_suffix_variants_rank_before_prefix_variants():
    names = ss.word_candidates("automation", None, 200)
    first_suffix = next(i for i, n in enumerate(names) if n.startswith("automation") and n != "automation")
    first_prefix = next(i for i, n in enumerate(names) if n.endswith("automation") and n != "automation")
    assert first_suffix < first_prefix


def test_long_keywords_keep_room_for_affixes():
    names = ss.word_candidates("automation", None, 200)
    assert len(names) >= 30
    assert all(len(name) <= settings.MAX_NAME_LENGTH for name in names)


def test_general_keywords_get_a_wide_set_of_names():
    assert len(ss.word_candidates("tag", None, 200)) >= 60


def test_legal_and_brand_terms_are_blocked():
    names = ss.word_candidates("real estate test", "real_estate", 500)
    assert not any("test" in name for name in names)


# --- execution -------------------------------------------------------------

def test_execute_returns_available_offers_sorted_with_budget_tag(online):
    request = ss.SearchRequest(keyword="real estate", tlds=(".com", ".in"), max_price=1500)
    outcome = ss.execute(request, fake_check("real", price=1000), lambda **fields: None)
    assert outcome["offers"]
    assert all(offer["domain"].startswith("real") for offer in outcome["offers"])
    assert all(offer["within_budget"] for offer in outcome["offers"])
    assert outcome["failed"] == []


def test_over_budget_offers_are_kept_tagged_and_sorted_last(online):
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",), max_price=500)
    outcome = ss.execute(request, fake_check("real", price=1000), lambda **fields: None)
    assert outcome["offers"]
    assert all(offer["within_budget"] is False for offer in outcome["offers"])


def test_registered_names_are_skipped_before_the_api(monkeypatch):
    monkeypatch.setattr(gc, "load_pat", lambda: "token")
    monkeypatch.setattr(gc, "rdap_is_registered", lambda domain, config: True)
    calls = []
    outcome = ss.execute(
        ss.SearchRequest(keyword="real estate", tlds=(".com",)),
        lambda batch, *rest: (calls.append(batch) or ([], None)),
        lambda **fields: None,
    )
    assert calls == []
    assert outcome["skipped_registered"] > 0


def test_missing_token_fails_the_job(monkeypatch, store):
    monkeypatch.setattr(gc, "load_pat", lambda: None)
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    job_id = store.create(request)
    ss.run_job(store, job_id, request, fake_check())
    job = store.get(job_id)
    assert job["status"] == "failed"
    assert "GODADDY_PAT" in job["error"]


def test_auth_failure_fails_the_job(online, store):
    def rejected(batch, config, pat, logger):
        raise gc.AuthError("authentication/authorization failed (HTTP 401)")

    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    job_id = store.create(request)
    ss.run_job(store, job_id, request, rejected)
    assert store.get(job_id)["status"] == "failed"


def test_failed_batch_is_reported_not_marked_available(online):
    def flaky(batch, config, pat, logger):
        return [], "GoDaddy returned HTTP 500"

    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    outcome = ss.execute(request, flaky, lambda **fields: None)
    assert outcome["offers"] == []
    assert len(outcome["failed"]) > 0


# --- job store -------------------------------------------------------------

def test_jobs_survive_a_restart(online, tmp_path):
    path = tmp_path / "jobs.sqlite3"
    first = ss.JobStore(path)
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    job_id = first.create(request)
    ss.run_job(first, job_id, request, fake_check())
    second = ss.JobStore(path)
    job = second.get(job_id)
    assert job["status"] == "done"
    assert job["offers"]


def test_a_job_running_at_shutdown_is_marked_failed_on_restart(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    first = ss.JobStore(path)
    job_id = first.create(ss.SearchRequest(keyword="real estate", tlds=(".com",)))
    first.update(job_id, status="running")
    recovered = ss.JobStore(path).get(job_id)
    assert recovered["status"] == "failed"
    assert "stopped" in recovered["error"]


def test_start_search_runs_in_background(online, store):
    job_id = ss.start_search(store, ss.SearchRequest(keyword="real estate", tlds=(".com",)), fake_check())
    deadline = time.time() + 10
    while time.time() < deadline:
        job = store.get(job_id)
        if job["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert store.get(job_id)["status"] == "done"
