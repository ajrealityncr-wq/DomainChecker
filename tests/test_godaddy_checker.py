import logging
import os

import pytest
import requests

import godaddy_checker as gc
import settings


CONFIG = {**gc.default_config(), "allowed_tlds": [".com", ".in"]}


def fake_response(status=200, headers=None, body=None):
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    response._content = b"[]" if body is None else body
    return response


# --- domain normalisation -------------------------------------------------

def test_normalize_strips_protocol_and_lowercases():
    assert gc.normalize_domain("https://Example.com/path", CONFIG) == ("example.com", "https://Example.com/path")


def test_tld_match_is_exact_not_suffix():
    # "xcom" ends with "com" but is not a .com domain.
    assert gc.normalize_domain("abc.xcom", CONFIG) == (None, "abc.xcom")


def test_tld_list_accepts_missing_dots():
    config = {**CONFIG, "allowed_tlds": ["com"]}
    assert gc.normalize_domain("abc.com", config)[0] == "abc.com"


def test_prepare_removes_duplicates_and_reports_invalid():
    valid, invalid, _ = gc.prepare_domains(["a1.com", "A1.com", "bad domain", "a2.in"], CONFIG)
    assert valid == ["a1.com", "a2.in"]
    assert invalid == ["bad domain"]


# --- budget ---------------------------------------------------------------

def budget_config(**overrides):
    budget = {"max_yearly_price": 5000, "max_renewal_price": 6000, "currency": "INR", "require_renewal_price": False}
    budget.update(overrides)
    return {"budget": budget}


def test_renewal_cap_is_enforced_when_known():
    record = {"price": 1000, "currency": "INR", "renewal_price": 7000}
    assert gc.meets_budget(record, budget_config()) is False


def test_renewal_within_cap_passes():
    record = {"price": 1000, "currency": "INR", "renewal_price": 5500}
    assert gc.meets_budget(record, budget_config()) is True


def test_unknown_renewal_passes_unless_required():
    record = {"price": 1000, "currency": "INR", "renewal_price": None}
    assert gc.meets_budget(record, budget_config()) is True
    assert gc.meets_budget(record, budget_config(require_renewal_price=True)) is False


def test_price_over_cap_or_wrong_currency_fails():
    assert gc.meets_budget({"price": 5001, "currency": "INR"}, budget_config()) is False
    assert gc.meets_budget({"price": 100, "currency": "USD"}, budget_config()) is False
    assert gc.meets_budget({"price": None, "currency": "INR"}, budget_config()) is False


# --- responses and retries ------------------------------------------------

def test_parse_response_rejects_omitted_domains():
    items = [{"domain": "a1.com", "available": True}]
    results, error = gc.parse_response(items, ["a1.com", "b2.com"])
    assert results == [] and "omitted" in error


def test_parse_response_rejects_missing_availability():
    results, error = gc.parse_response([{"domain": "a1.com"}], ["a1.com"])
    assert results == [] and error


def test_retry_delay_honours_numeric_retry_after_and_caps_it():
    assert gc.retry_delay(fake_response(headers={"Retry-After": "1.5"}), CONFIG, 0) == 1.5
    assert gc.retry_delay(fake_response(headers={"Retry-After": "9999"}), CONFIG, 0) == settings.MAX_RETRY_DELAY_SECONDS


def test_retry_delay_falls_back_for_http_date():
    delay = gc.retry_delay(fake_response(headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), CONFIG, 2)
    assert delay == gc.backoff_delay(CONFIG, 2)


def test_auth_failure_raises_instead_of_returning_error(monkeypatch):
    monkeypatch.setattr(gc.requests, "post", lambda *a, **k: fake_response(status=401))
    logger = logging.getLogger("test")
    with pytest.raises(gc.AuthError):
        gc.check_batch(["a1.com"], {**CONFIG, "max_retries": 0}, "token", logger)


# --- progress bookkeeping -------------------------------------------------

def test_unchecked_excludes_processed_and_errored():
    valid = ["a1.com", "b2.com", "c3.com"]
    processed = {"a1.com": {}}
    errors = [{"domain": "b2.com"}]
    assert gc.unchecked_domains(valid, processed, errors) == ["c3.com"]


def test_pending_includes_errored_domains_so_resume_retries_them():
    assert gc.pending_domains(["a1.com", "b2.com"], {"a1.com": {}}) == ["b2.com"]


def test_record_errors_replaces_earlier_error_for_same_domain():
    first = gc.record_errors([], ["a1.com"], "timeout")
    second = gc.record_errors(first, ["a1.com"], "timeout again")
    assert len(second) == 1 and second[0]["error"] == "timeout again"


def test_resolved_errors_are_dropped_once_processed():
    errors = [{"domain": "a1.com"}, {"domain": "b2.com"}]
    assert gc.drop_resolved_errors(errors, {"a1.com": {}}) == [{"domain": "b2.com"}]


# --- report rows and Excel --------------------------------------------------

def test_status_for_rdap_registered_is_taken():
    result = gc.CheckResult(domain="a1.com", available=False, status=gc.RDAP_REGISTERED)
    assert gc.status_for(result, None) == ("TAKEN", gc.RDAP_REGISTERED)


def test_status_for_missing_result_and_error_is_error():
    assert gc.status_for(None, {"reason": "API_CHECK_FAILED"}) == ("ERROR", "API_CHECK_FAILED")


def test_excel_table_spans_every_header(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    rows = gc.build_report_rows(["a1.com"], [], [gc.CheckResult(domain="a1.com", available=True, price=100, currency="INR")], [])
    path = tmp_path / "out.xlsx"
    gc.write_excel(path, rows)
    sheet = openpyxl.load_workbook(path).active
    table = sheet.tables["DomainResultsTable"]
    assert table.ref == "A1:L2"  # 12 headers, one data row


def test_sort_by_price_handles_missing_price():
    records = [{"domain": "a.com", "price": None}, {"domain": "b.com", "price": 300}]
    gc.sort_available(records, "price")
    assert [r["domain"] for r in records] == ["b.com", "a.com"]


def test_save_outputs_writes_unchecked_and_summary(tmp_path):
    config = {**gc.default_config(), "output_directory": "out", "save_csv": False}
    summary = gc.save_outputs(tmp_path, config, ["a1.com"], [], [], [], ["a1.com"], "start", 0, [])
    assert summary["unchecked"] == 1
    assert (tmp_path / "out" / "BUY_NOW.txt").exists()
    assert (tmp_path / "out" / "domain_results.md").exists()


# --- RDAP prefilter -------------------------------------------------------

@pytest.mark.parametrize("status, expected", [(200, True), (404, False), (429, None), (500, None)])
def test_rdap_only_acts_on_a_clear_registered_answer(monkeypatch, status, expected):
    monkeypatch.setattr(gc.requests, "get", lambda *a, **k: fake_response(status=status))
    assert gc.rdap_is_registered("a1.com", CONFIG) is expected


def test_rdap_network_failure_is_inconclusive(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(gc.requests, "get", boom)
    assert gc.rdap_is_registered("a1.com", CONFIG) is None


# --- price units ----------------------------------------------------------

def test_micro_unit_prices_are_flagged():
    assert gc.price_units_warning({"price": 11990000}) is not None


def test_whole_currency_prices_are_not_flagged():
    assert gc.price_units_warning({"price": 1000}) is None
    assert gc.price_units_warning({"price": None}) is None


# --- token from .env (review item 1) ----------------------------------------

def test_load_pat_reads_the_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k != "GODADDY_PAT"})
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("GODADDY_PAT=token-from-dotenv\n", encoding="utf-8")
    assert gc.load_pat() == "token-from-dotenv"


def test_environment_variable_takes_precedence_over_dotenv(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", {**os.environ, "GODADDY_PAT": "token-from-env"})
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("GODADDY_PAT=token-from-dotenv\n", encoding="utf-8")
    assert gc.load_pat() == "token-from-env"


# --- budget rules (review item 4) -------------------------------------------

def test_renewal_only_cap_passes_a_known_renewal_under_the_cap():
    config = {"budget": {"max_yearly_price": None, "max_renewal_price": 800, "currency": "INR"}}
    assert gc.meets_budget({"price": None, "currency": None, "renewal_price": 700}, config) is True


def test_renewal_only_cap_fails_a_known_renewal_over_the_cap():
    config = {"budget": {"max_yearly_price": None, "max_renewal_price": 800, "currency": "INR"}}
    assert gc.meets_budget({"price": None, "currency": None, "renewal_price": 900}, config) is False


def test_price_cap_does_not_need_a_renewal_price_unless_required():
    config = {"budget": {"max_yearly_price": 1000, "max_renewal_price": 800, "currency": "INR", "require_renewal_price": False}}
    assert gc.meets_budget({"price": 500, "currency": "INR", "renewal_price": None}, config) is True


def test_has_limits_is_false_with_no_caps():
    assert gc.has_limits({"budget": {"max_yearly_price": None, "max_renewal_price": None}}) is False
    assert gc.has_limits({"budget": {"max_yearly_price": 500, "max_renewal_price": None}}) is True


# --- shared batch loop and parallel RDAP (review items 2 and 3) -----------------

def test_check_in_batches_collects_results_and_failed_batches():
    config = {**gc.default_config(), "batch_size": 2}
    seen = []

    def fake(batch, config, pat, logger):
        if batch[0] == "bad1.com":
            return [], "GoDaddy returned HTTP 500"
        return [gc.CheckResult(domain=d, available=True) for d in batch], None

    results, failed = gc.check_in_batches(["ok1.com", "ok2.com", "bad1.com", "bad2.com"], config, "t", fake, logging.getLogger("t"),
                                          on_batch=lambda batch, res, err, checked: seen.append(checked))
    assert [r.domain for r in results] == ["ok1.com", "ok2.com"]
    assert failed == ["bad1.com", "bad2.com"]
    assert seen == [2, 4]


def test_check_in_batches_stops_on_auth_error():
    config = {**gc.default_config(), "batch_size": 1}

    def rejected(batch, config, pat, logger):
        raise gc.AuthError("HTTP 401")

    with pytest.raises(gc.AuthError):
        gc.check_in_batches(["a1.com", "a2.com"], config, "t", rejected, logging.getLogger("t"))


def test_rdap_statuses_maps_each_domain_and_treats_errors_as_unknown(monkeypatch):
    def fake(domain, config):
        if domain == "boom.com":
            raise RuntimeError("network down")
        return domain.startswith("taken")

    monkeypatch.setattr(gc, "rdap_is_registered", fake)
    status = gc.rdap_statuses(["taken1.com", "free1.com", "boom.com"], gc.default_config())
    assert status == {"taken1.com": True, "free1.com": False, "boom.com": None}


def test_answer_for_keeps_prices_only_for_available_names():
    available = gc.CheckResult(domain="a.com", available=True, price=0, renewal_price=999, currency="INR", api_success=True)
    taken = gc.CheckResult(domain="b.com", available=False, price=1200, renewal_price=999, currency="INR", api_success=True)
    assert gc.answer_for(available) == {"available": True, "price": 0, "renewal_price": 999, "currency": "INR"}
    assert gc.answer_for(taken) == {"available": False}
