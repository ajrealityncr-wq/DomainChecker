"""A domain GoDaddy has answered (available or taken) is never generated or sent to GoDaddy again."""

import json
import sqlite3
import sys

import pytest

import godaddy_checker as gc
import search_service as ss
import settings
from history import DomainHistory


def counting_check(sent):
    def check(batch, config, pat, logger):
        sent.extend(batch)
        return [gc.CheckResult(domain=d, available=d.startswith("a"), price=500, currency="INR", api_success=True) for d in batch], None
    return check


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(gc, "load_pat", lambda: "token")
    monkeypatch.setattr(gc, "rdap_is_registered", lambda domain, config: None)


# --- the record ---------------------------------------------------------------

def test_answers_are_stored_and_never_released(tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    history.mark_checked({"a1.com": True, "b2.com": False})
    assert history.checked(["a1.com", "b2.com", "c3.com"]) == {"a1.com": True, "b2.com": False}
    history.release(["a1.com", "b2.com"])
    assert history.checked(["a1.com", "b2.com"]) == {"a1.com": True, "b2.com": False}
    assert history.unseen(["a1.com", "b2.com", "c3.com"]) == ["c3.com"]


def test_generated_but_unanswered_names_can_still_be_released(tmp_path):
    history = DomainHistory(tmp_path / "h.sqlite3")
    history.mark_seen(["a1.com"])
    assert history.checked(["a1.com"]) == {}
    history.release(["a1.com"])
    assert history.unseen(["a1.com"]) == ["a1.com"]


def test_older_database_files_get_the_answer_columns(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE seen_domains (domain TEXT PRIMARY KEY, first_seen REAL NOT NULL)")
    db.execute("INSERT INTO seen_domains VALUES ('old1.com', 1.0)")
    db.commit()
    db.close()
    history = DomainHistory(path)
    history.mark_checked({"old1.com": False})
    assert history.checked(["old1.com"]) == {"old1.com": False}


# --- web search ---------------------------------------------------------------------

def test_web_searches_never_send_the_same_domain_twice(offline, tmp_path):
    sent: list[str] = []
    history = DomainHistory()
    request = ss.SearchRequest(keyword="tag", tlds=(".com",))  # a short keyword has enough names for 3 searches
    for _ in range(3):
        ss.execute(request, counting_check(sent), lambda **fields: None, history=history)
    assert sent and len(sent) == len(set(sent))
    assert set(history.checked(sent)) == set(sent)  # every answer was stored


def test_registered_names_are_stored_as_taken(monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "load_pat", lambda: "token")
    monkeypatch.setattr(gc, "rdap_is_registered", lambda domain, config: True)
    history = DomainHistory()
    ss.execute(ss.SearchRequest(keyword="automation", tlds=(".com",)), counting_check([]), lambda **fields: None, history=history)
    _, planned = ss.plan_domains(ss.SearchRequest(keyword="automation", tlds=(".com",)))
    stored = history.checked(planned)
    assert stored and all(available is False for available in stored.values())


# --- command-line checker -------------------------------------------------------------

@pytest.fixture
def cli(monkeypatch, tmp_path, offline):
    monkeypatch.setattr(settings, "OUTPUT_DIRECTORY", tmp_path / "out")
    monkeypatch.setattr(settings, "CHECKPOINT_FILE", tmp_path / "out" / "checkpoint.json")
    domains_file = tmp_path / "domains.json"
    domains_file.write_text(json.dumps(["alpha1.com", "beta2.com", "alpha3.in"]), encoding="utf-8")
    sent: list[str] = []
    monkeypatch.setattr(gc, "check_batch", counting_check(sent))
    monkeypatch.setattr(sys, "argv", ["godaddy_checker.py", "--input", str(domains_file)])
    return sent, tmp_path


def test_cli_does_not_resend_answered_domains_even_without_a_checkpoint(cli):
    sent, tmp_path = cli
    assert gc.main() == 0
    assert sorted(sent) == ["alpha1.com", "alpha3.in", "beta2.com"]
    (tmp_path / "out" / "checkpoint.json").unlink()
    sent.clear()
    assert gc.main() == 0
    assert sent == []  # nothing went to GoDaddy the second time
    rows = (tmp_path / "out" / "domain_results.md").read_text(encoding="utf-8")
    assert "CHECKED_EARLIER" in rows
    available = json.loads((tmp_path / "out" / "available_domains.json").read_text(encoding="utf-8"))
    assert {item["domain"] for item in available} == {"alpha1.com", "alpha3.in"}  # earlier answers are still reported


def test_cli_force_recheck_still_does_not_resend_answered_domains(cli, monkeypatch):
    sent, tmp_path = cli
    assert gc.main() == 0
    sent.clear()
    monkeypatch.setattr(sys, "argv", sys.argv + ["--force-recheck"])
    assert gc.main() == 0
    assert sent == []


def test_cli_skips_domains_answered_in_the_web_app(cli):
    sent, _ = cli
    DomainHistory().mark_checked({"alpha1.com": True})
    assert gc.main() == 0
    assert "alpha1.com" not in sent
    assert sorted(sent) == ["alpha3.in", "beta2.com"]
