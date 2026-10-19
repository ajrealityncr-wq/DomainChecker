import json
import sys

import generate_domains as gd
import settings


def test_generator_writes_candidates_and_report_under_output(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_DIRECTORY", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_domains.py", "--keywords", "automation"])
    assert gd.main() == 0
    domains = json.loads((tmp_path / "generated_review_domains.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "generation_report.json").read_text(encoding="utf-8"))
    assert "automation.com" in domains
    assert report["industry"] is None
    assert report["written_count"] == len(domains)


def test_generator_reports_matched_industry(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_DIRECTORY", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_domains.py", "--keywords", "real estate"])
    assert gd.main() == 0
    report = json.loads((tmp_path / "generation_report.json").read_text(encoding="utf-8"))
    assert report["industry"] == "real_estate"


def test_generator_rejects_bad_keyword(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(settings, "OUTPUT_DIRECTORY", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_domains.py", "--keywords", "x9"])
    assert gd.main() == 2
    assert "Input error" in capsys.readouterr().out


def test_generator_never_repeats_a_name_across_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OUTPUT_DIRECTORY", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_domains.py", "--keywords", "automation"])
    assert gd.main() == 0
    first = json.loads((tmp_path / "generated_review_domains.json").read_text(encoding="utf-8"))
    assert gd.main() == 0
    second = json.loads((tmp_path / "generated_review_domains.json").read_text(encoding="utf-8"))
    assert first and second
    assert set(first).isdisjoint(second)
