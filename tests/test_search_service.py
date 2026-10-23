import time

import pytest

from history import DomainHistory
import godaddy_checker as gc
import search_service as ss
import settings
import trademark


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
    return ss.JobStore(tmp_path / "app.sqlite3")


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


def test_rejects_negative_or_huge_prices():
    with pytest.raises(ValueError):
        ss.SearchRequest(keyword="homes", tlds=(".com",), max_price=-1)
    with pytest.raises(ValueError):
        ss.SearchRequest(keyword="homes", tlds=(".com",), max_price=settings.MAX_PRICE_LIMIT + 1)
    with pytest.raises(ValueError):
        ss.SearchRequest(keyword="homes", tlds=(".com",), max_renewal_price=float("inf"))


def test_rejects_blocked_keyword():
    with pytest.raises(ValueError, match="not allowed"):
        ss.SearchRequest(keyword="google", tlds=(".com",))


# --- keyword anchoring and name rules ---------------------------------------

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


def test_general_keywords_get_a_useful_set_of_names():
    assert len(ss.word_candidates("tag", None, 200)) >= 50


def test_blocklist_is_exact_for_short_generic_words():
    assert not ss._is_blocked("essex")
    assert not ss._is_blocked("barcode")
    assert ss._is_blocked("test")
    assert ss._is_blocked("googlehub")


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


def test_renewal_cap_marks_offers_over_budget(online):
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",), max_price=5000, max_renewal_price=100)
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


def test_rdap_lookups_run_in_parallel_and_report_progress(monkeypatch):
    monkeypatch.setattr(gc, "load_pat", lambda: "token")
    seen_threads = set()

    def slow_rdap(domain, config):
        import threading
        seen_threads.add(threading.get_ident())
        time.sleep(0.01)
        return None

    monkeypatch.setattr(gc, "rdap_is_registered", slow_rdap)
    stages = []
    ss.execute(
        ss.SearchRequest(keyword="real estate", tlds=(".com",)),
        fake_check("real"),
        lambda **fields: stages.append(fields.get("stage")),
    )
    assert len(seen_threads) > 1
    assert "Checking for registered names" in stages
    assert "Checking availability with GoDaddy" in stages


def test_repeat_search_never_repeats_a_domain(online):
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    first = ss.execute(request, fake_check("real"), lambda **fields: None)
    second = ss.execute(request, fake_check("real"), lambda **fields: None)
    first_names = {offer["domain"] for offer in first["offers"]}
    second_names = {offer["domain"] for offer in second["offers"]}
    assert first_names
    assert first_names.isdisjoint(second_names)


def test_failed_batch_is_reported_not_marked_available(online):
    def flaky(batch, config, pat, logger):
        return [], "GoDaddy returned HTTP 500"

    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    outcome = ss.execute(request, flaky, lambda **fields: None)
    assert outcome["offers"] == []
    assert len(outcome["failed"]) > 0


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


def test_job_keeps_inputs_so_it_can_be_run_again(online, store):
    request = ss.SearchRequest(keyword="real estate", tlds=(".com", ".in"), max_price=900, max_renewal_price=800)
    job_id = store.create(request)
    ss.run_job(store, job_id, request, fake_check())
    job = store.get(job_id)
    assert job["keyword"] == "real estate"
    assert job["tlds"] == [".com", ".in"]
    assert job["max_price"] == 900
    assert job["max_renewal_price"] == 800


# --- trademark hook ---------------------------------------------------------

def test_trademark_screen_not_configured_marks_names_not_checked(online):
    outcome = ss.execute(ss.SearchRequest(keyword="real estate", tlds=(".com",)), fake_check("real"), lambda **fields: None)
    assert outcome["trademark_checked"] is False
    assert all(offer["trademark"] == trademark.NOT_CHECKED for offer in outcome["offers"])


class _MatchScreen:
    def screen(self, names):
        return {name: (trademark.MATCH if name.startswith("realestate") else trademark.CLEAR) for name in names}


def test_trademark_match_removes_the_name(online):
    outcome = ss.execute(ss.SearchRequest(keyword="real estate", tlds=(".com",)), fake_check("real"), lambda **fields: None, screen=_MatchScreen())
    assert outcome["trademark_checked"] is True
    assert not any(offer["domain"].startswith("realestate") for offer in outcome["offers"])
    assert all(offer["trademark"] == trademark.CLEAR for offer in outcome["offers"])



# --- job store and limits ----------------------------------------------------

def test_jobs_survive_a_restart(online, tmp_path):
    path = tmp_path / "app.sqlite3"
    first = ss.JobStore(path)
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    job_id = first.create(request)
    ss.run_job(first, job_id, request, fake_check())
    assert ss.JobStore(path).get(job_id)["status"] == "done"


def test_a_job_running_at_shutdown_is_marked_failed_on_restart(tmp_path):
    path = tmp_path / "app.sqlite3"
    first = ss.JobStore(path)
    job_id = first.create(ss.SearchRequest(keyword="real estate", tlds=(".com",)))
    first.update(job_id, status="running")
    recovered = ss.JobStore(path).get(job_id)
    assert recovered["status"] == "failed"
    assert "stopped" in recovered["error"]


def test_old_jobs_are_removed(tmp_path, monkeypatch):
    path = tmp_path / "app.sqlite3"
    first = ss.JobStore(path)
    job_id = first.create(ss.SearchRequest(keyword="real estate", tlds=(".com",)))
    first.update(job_id, created_at=time.time() - (settings.JOB_RETENTION_DAYS + 1) * 86400)
    assert ss.JobStore(path).get(job_id) is None


def test_concurrent_search_limit(store, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SEARCHES", 1)
    store.create(ss.SearchRequest(keyword="real estate", tlds=(".com",)))
    with pytest.raises(ss.LimitError, match="already running"):
        ss.start_search(store, ss.SearchRequest(keyword="automation", tlds=(".com",)), fake_check())


def test_daily_search_limit(store, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SEARCHES_PER_DAY", 1)
    store.create(ss.SearchRequest(keyword="real estate", tlds=(".com",)))
    with pytest.raises(ss.LimitError, match="daily"):
        ss.start_search(store, ss.SearchRequest(keyword="automation", tlds=(".com",)), fake_check())


def test_start_search_runs_in_background(online, store):
    job_id = ss.start_search(store, ss.SearchRequest(keyword="real estate", tlds=(".com",)), fake_check())
    deadline = time.time() + 10
    while time.time() < deadline:
        if store.get(job_id)["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert store.get(job_id)["status"] == "done"


# --- no limit set (review item 5) ---------------------------------------------

def test_offers_have_no_budget_flag_when_no_limit_is_set(online):
    outcome = ss.execute(ss.SearchRequest(keyword="real estate", tlds=(".com",)), fake_check("real"), lambda **fields: None)
    assert outcome["offers"]
    assert all(offer["within_budget"] is None for offer in outcome["offers"])


def test_renewal_cap_alone_still_flags_budget(online):
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",), max_renewal_price=950)
    outcome = ss.execute(request, fake_check("real", price=1000), lambda **fields: None)
    assert outcome["offers"]
    assert all(offer["within_budget"] is True for offer in outcome["offers"])  # fake renewal is 900, under 950


# --- every generated name is unique (uniqueness rule) -----------------------

def test_concurrent_searches_never_take_the_same_name(tmp_path):
    import threading
    history = DomainHistory(tmp_path / "h.sqlite3")
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))
    taken: list[list[str]] = []
    lock = threading.Lock()

    def take():
        _, domains = ss.next_domains(request, history)
        with lock:
            taken.append(domains)

    threads = [threading.Thread(target=take) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    everything = [domain for batch in taken for domain in batch]
    assert len(everything) == len(set(everything))


def test_exhausted_keyword_says_no_new_names_are_left(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CANDIDATE_POOL_PER_KEYWORD", 2)
    monkeypatch.setattr(settings, "CANDIDATES_PER_SEARCH", 2)
    history = DomainHistory(tmp_path / "h.sqlite3")
    request = ss.SearchRequest(keyword="automation", tlds=(".com",))
    ss.next_domains(request, history)
    with pytest.raises(ss.SearchError, match="No new names"):
        ss.next_domains(request, history)


def test_names_are_released_when_the_check_fails_so_they_can_be_retried(online, tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))

    def flaky(batch, config, pat, logger):
        return [], "GoDaddy returned HTTP 500"

    ss.execute(request, flaky, lambda **fields: None, history=history)
    retried = ss.execute(request, fake_check("real"), lambda **fields: None, history=history)
    assert retried["offers"]


def test_names_are_released_when_the_run_stops_with_an_error(online, tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    request = ss.SearchRequest(keyword="real estate", tlds=(".com",))

    def rejected(batch, config, pat, logger):
        raise gc.AuthError("HTTP 401")

    with pytest.raises(gc.AuthError):
        ss.execute(request, rejected, lambda **fields: None, history=history)
    _, domains = ss.plan_domains(request)
    assert history.unseen(domains) == domains  # nothing from the stopped run stays marked as used


def test_history_marks_and_releases(tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    history.mark_seen(["a1.com", "b2.com"])
    assert history.unseen(["a1.com", "b2.com", "c3.com"]) == ["c3.com"]
    history.release(["a1.com"])
    assert history.unseen(["a1.com", "b2.com"]) == ["a1.com"]


def test_short_keywords_get_a_large_pool_of_names():
    names = ss.word_candidates("tag", None, 100000)
    assert len(names) >= 1000
    assert "taghub" in names
    assert "tags" in names


def test_long_keywords_still_get_enough_names():
    assert len(ss.word_candidates("automation", None, 100000)) >= 50


def test_stronger_brand_names_rank_first():
    names = ss.word_candidates("tag", None, 100000)
    assert names.index("taghub") < names.index("tagnowworld")


# --- rounds, ranking, recheck and shortlist ----------------------------------

def counting_check(available_every):
    """Fake GoDaddy: every n-th name sent is available. Records how many names were sent."""
    sent = []

    def check(batch, config, pat, logger):
        results = []
        for domain in batch:
            sent.append(domain)
            free = len(sent) % available_every == 0
            results.append(gc.CheckResult(domain=domain, available=free, price=500 if free else None,
                                          renewal_price=900 if free else None, currency="INR", api_success=True))
        return results, None
    check.sent = sent
    return check


def test_search_keeps_going_until_the_names_run_out(online, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ROUNDS_PER_SEARCH", 100)
    monkeypatch.setattr(settings, "CANDIDATE_POOL_PER_KEYWORD", 120)
    check = counting_check(available_every=1)  # plenty of available names: the search still continues
    outcome = ss.execute(ss.SearchRequest(keyword="tag", tlds=(".com",)), check, lambda **fields: None)
    assert len(check.sent) == 120
    assert len(set(check.sent)) == 120
    assert outcome["names_exhausted"] is True
    assert outcome["stopped"] is False
    assert len(outcome["offers"]) == 120


def test_search_stops_after_the_round_limit(online, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ROUNDS_PER_SEARCH", 3)
    check = counting_check(available_every=1000)
    outcome = ss.execute(ss.SearchRequest(keyword="tag", tlds=(".com",)), check, lambda **fields: None)
    assert len(check.sent) == 150
    assert outcome["names_exhausted"] is False


def test_stop_ends_the_search_after_the_current_batch_and_keeps_its_names(online, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "MAX_ROUNDS_PER_SEARCH", 100)
    history = DomainHistory(tmp_path / "h.sqlite3")
    check = counting_check(available_every=2)
    stop_after = 3 * settings.BATCH_SIZE
    outcome = ss.execute(ss.SearchRequest(keyword="tag", tlds=(".com",)), check, lambda **fields: None,
                         history=history, should_stop=lambda: len(check.sent) >= stop_after)
    assert len(check.sent) == stop_after
    assert outcome["stopped"] is True
    assert outcome["names_checked"] == stop_after
    assert len(outcome["offers"]) == stop_after // 2
    # Names that were never sent go back to the pool; the sent ones are never sent again.
    _, chosen = ss.next_domains(ss.SearchRequest(keyword="tag", tlds=(".com",)), history)
    assert set(chosen).isdisjoint(check.sent)
    assert history.checked(check.sent).keys() == set(check.sent)


def test_offers_are_reported_while_the_search_runs(online, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ROUNDS_PER_SEARCH", 2)
    updates = []
    ss.execute(ss.SearchRequest(keyword="tag", tlds=(".com",)), counting_check(available_every=5),
               lambda **fields: "offers" in fields and updates.append(len(fields["offers"])))
    assert updates == sorted(updates)
    assert updates[0] < updates[-1] == 20


def test_names_with_one_add_on_come_before_stacked_names():
    import brand
    names = ss.word_candidates("tag", None, 100000)
    stacked = [brand.addon_count(name, ["tag"]) >= 2 for name in names]
    assert stacked.index(True) > 50
    assert all(stacked[stacked.index(True):])


def test_brand_score_is_stored_when_a_name_is_generated(tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    _, chosen = ss.next_domains(ss.SearchRequest(keyword="tag", tlds=(".com",)), history)
    history.mark_checked({chosen[0]: False})
    rows, total = history.library()
    assert total == 1
    assert rows[0]["brand_score"] is not None
    assert isinstance(rows[0]["brand_notes"], list)


def test_offers_link_to_buy_the_domain(online):
    outcome = ss.execute(ss.SearchRequest(keyword="real estate", tlds=(".com",)), fake_check("real"), lambda **fields: None)
    offer = outcome["offers"][0]
    assert offer["buy_url"] == settings.BUY_URL.format(domain=offer["domain"])


def test_library_pages_newest_first(tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    for index in range(5):
        history.mark_checked({f"name{index}.com": False})
        time.sleep(0.001)
    page = ss.domain_library(history, offset=1, limit=2)
    assert page["total"] == 5
    assert [row["domain"] for row in page["domains"]] == ["name3.com", "name2.com"]


def test_recheck_updates_the_stored_answer(online, tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    history.mark_checked({"realhome.com": False})
    rows = ss.recheck(["realhome.com"], fake_check("real", price=750), history)
    assert rows[0]["available"] is True
    assert rows[0]["price"] == 750
    assert history.checked(["realhome.com"]) == {"realhome.com": True}


def test_recheck_only_accepts_checked_domains_and_a_limited_number(online, tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    with pytest.raises(ValueError, match="Only checked domains"):
        ss.recheck(["never.com"], fake_check(), history)
    with pytest.raises(ValueError, match="at most"):
        ss.recheck([f"n{i}.com" for i in range(settings.MAX_RECHECK_DOMAINS + 1)], fake_check(), history)


def test_recheck_needs_the_token(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "load_pat", lambda: None)
    history = DomainHistory(tmp_path / "h.sqlite3")
    history.mark_checked({"realhome.com": False})
    with pytest.raises(ss.SearchError, match="token"):
        ss.recheck(["realhome.com"], fake_check(), history)


def test_shortlist_stars_known_domains_only(tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    history.mark_checked({"realhome.com": True})
    assert history.set_starred("realhome.com", True) is True
    assert history.set_starred("unknown.com", True) is False
    assert history.starred() == ["realhome.com"]
    history.set_starred("realhome.com", False)
    assert history.starred() == []


def test_job_stopped_by_the_user_is_done_with_its_names(online, store, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ROUNDS_PER_SEARCH", 100)
    request = ss.SearchRequest(keyword="tag", tlds=(".com",))
    job_id = store.create(request)
    check = counting_check(available_every=1)

    def check_then_stop(batch, config, pat, logger):
        store.request_stop(job_id)  # the user presses Stop while the first batch is checked
        return check(batch, config, pat, logger)

    ss.run_job(store, job_id, request, check_then_stop)
    job = store.get(job_id)
    assert job["status"] == "done"
    assert job["stage"] == "Stopped by you"
    assert job["stopped"] is True
    assert len(job["offers"]) == settings.BATCH_SIZE
    assert store.request_stop(job_id) is False  # a finished search cannot be stopped
