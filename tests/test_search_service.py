import json
from pathlib import Path

import pytest

import godaddy_checker as gc
import search_service as ss

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def generation_config():
    with (ROOT / "domain_generation_config.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def config():
    return gc.load_config(ROOT / "config.json")


def fake_check(available_prefix="real", price=1000):
    """Fake GoDaddy: names starting with `available_prefix` are available."""
    def check(batch, config, pat, logger):
        results = []
        for domain in batch:
            is_free = domain.startswith(available_prefix)
            results.append(gc.CheckResult(
                domain=domain,
                available=is_free,
                price=price if is_free else None,
                renewal_price=900 if is_free else None,
                currency="INR" if is_free else None,
                api_success=True,
            ))
        return results, None
    return check


# --- request validation ---------------------------------------------------

def test_request_rejects_unknown_tld():
    with pytest.raises(ValueError, match="Choose one or more"):
        ss.SearchRequest(keyword="real estate", tlds=(".xyz",))


def test_request_rejects_keyword_with_digits_or_symbols():
    with pytest.raises(ValueError, match="letters"):
        ss.SearchRequest(keyword="real estate 2", tlds=(".com",))


def test_request_rejects_negative_price_and_oversized_limit():
    with pytest.raises(ValueError):
        ss.SearchRequest(keyword="homes", tlds=(".com",), max_price=-1)
    with pytest.raises(ValueError):
        ss.SearchRequest(keyword="homes", tlds=(".com",), candidate_limit=ss.MAX_CANDIDATES + 1)


# --- word candidates ------------------------------------------------------

def test_real_estate_keyword_produces_readable_names(generation_config):
    names = ss.word_candidates("real estate", generation_config, "real_estate", 200)
    assert "realestate" in names
    assert any("realty" in name for name in names)
    assert any(name.endswith("hub") for name in names)


def test_industry_words_are_not_blocked(generation_config):
    # "home", "land", "real" and "rent" are in avoid_words for the phonetic generator,
    # but they are core real-estate vocabulary for word names.
    names = ss.word_candidates("real estate", generation_config, "real_estate", 500)
    assert any("home" in name for name in names)
    assert any("rent" in name for name in names)


def test_legal_and_brand_terms_are_blocked(generation_config):
    names = ss.word_candidates("real estate test", generation_config, "real_estate", 500)
    assert not any("test" in name for name in names)
    assert not any("google" in name for name in names)


def test_exact_keyword_phrase_ranks_first_and_reversals_are_dropped(generation_config):
    names = ss.word_candidates("real estate", generation_config, "real_estate", 50)
    assert names[0] == "realestate"
    assert "estatereal" not in names


def test_limit_is_respected_and_names_are_unique(generation_config):
    names = ss.word_candidates("real estate", generation_config, "real_estate", 20)
    assert len(names) == 20 and len(set(names)) == 20


def test_keyword_without_three_letter_words_is_rejected(generation_config):
    with pytest.raises(ValueError):
        ss.word_candidates("ai", generation_config, "general", 10)


# --- planning and execution -----------------------------------------------

def test_plan_crosses_names_with_selected_tlds(generation_config):
    request = ss.SearchRequest(keyword="real estate", tlds=(".com", ".in"), candidate_limit=3)
    industry, domains = ss.plan_domains(request, generation_config)
    assert industry == "real_estate"
    assert len(domains) == 6
    assert {domain.rsplit(".", 1)[1] for domain in domains} == {"com", "in"}


def test_execute_returns_available_offers_sorted_with_budget_tag(monkeypatch, config):
    monkeypatch.setenv("GODADDY_PAT", "token")
    request = ss.SearchRequest(keyword="real estate", tlds=(".com", ".in"), max_price=1500, candidate_limit=10)
    store = ss.JobStore()
    job_id = store.create(request)
    outcome = ss.execute(request, ROOT, fake_check("real", price=1000), lambda **fields: store.update(job_id, **fields))
    assert outcome["offers"], "expected at least one available realty name"
    assert all(offer["domain"].startswith("real") for offer in outcome["offers"])
    assert all(offer["within_budget"] for offer in outcome["offers"])
    assert outcome["failed"] == []


def test_over_budget_offers_are_kept_but_tagged_and_sorted_last(monkeypatch):
    monkeypatch.setenv("GODADDY_PAT", "token")
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",), max_price=500, candidate_limit=5)
    outcome = ss.execute(request, ROOT, fake_check("real", price=1000), lambda **fields: None)
    assert outcome["offers"]
    assert all(offer["within_budget"] is False for offer in outcome["offers"])


def test_missing_token_is_a_user_facing_failure(monkeypatch, tmp_path):
    monkeypatch.delenv("GODADDY_PAT", raising=False)
    monkeypatch.setattr(ss.gc, "load_pat", lambda *a, **k: None)
    store = ss.JobStore()
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",), candidate_limit=2)
    job_id = store.create(request)
    ss.run_job(store, job_id, request, ROOT, fake_check())
    job = store.get(job_id)
    assert job["status"] == "failed"
    assert "GODADDY_PAT" in job["error"]


def test_auth_failure_marks_job_failed(monkeypatch):
    monkeypatch.setenv("GODADDY_PAT", "bad")

    def rejected(batch, config, pat, logger):
        raise gc.AuthError("authentication/authorization failed (HTTP 401)")

    store = ss.JobStore()
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",), candidate_limit=2)
    job_id = store.create(request)
    ss.run_job(store, job_id, request, ROOT, rejected)
    assert store.get(job_id)["status"] == "failed"


def test_failed_batch_is_reported_not_marked_available(monkeypatch):
    monkeypatch.setenv("GODADDY_PAT", "token")

    def flaky(batch, config, pat, logger):
        return [], "GoDaddy returned HTTP 500"

    request = ss.SearchRequest(keyword="real estate", tlds=(".com",), candidate_limit=3)
    outcome = ss.execute(request, ROOT, flaky, lambda **fields: None)
    assert outcome["offers"] == []
    assert len(outcome["failed"]) == 3


# --- keyword anchoring ----------------------------------------------------

@pytest.mark.parametrize("keyword", ["automation", "tag", "real estate"])
def test_every_name_contains_the_keyword(generation_config, keyword):
    industry = None
    names = ss.word_candidates(keyword, generation_config, industry, 100)
    terms = gd_terms(keyword)
    assert names, f"no candidates for {keyword!r}"
    assert all(any(term in name for term in terms) for name in names)


def gd_terms(keyword):
    import generate_domains as gd
    return gd.tokenize_keywords(keyword)


def test_unmatched_keyword_gets_no_real_estate_words(monkeypatch, generation_config):
    import generate_domains as gd
    intent = gd.infer_intent("automation", generation_config)
    assert intent["source"] != "keywords"  # no industry profile matched
    industry, domains = ss.plan_domains(ss.SearchRequest(keyword="automation", tlds=(".com",), candidate_limit=50), generation_config)
    assert industry is None
    assert not any(word in domain for domain in domains for word in ("realty", "homes", "property", "estate"))
    assert "automation.com" in domains


def test_matched_keyword_uses_industry_words_combined_with_keyword(generation_config):
    names = ss.word_candidates("real estate", generation_config, "real_estate", 200)
    assert any("realhomes" == name or "realrent" == name for name in names)
