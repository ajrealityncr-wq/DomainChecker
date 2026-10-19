import json
import subprocess
import sys
from pathlib import Path

import pytest

import generate_domains as gd


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    with (ROOT / "domain_generation_config.json").open(encoding="utf-8") as handle:
        return json.load(handle)


# --- keyword intent -------------------------------------------------------

def test_keywords_select_matching_industry(config):
    intent = gd.infer_intent("finance, cash advance", config)
    assert intent["industry"] == "finance"
    assert intent["source"] == "keywords"
    assert "cash" in intent["matched_terms"]


def test_no_keywords_keeps_configured_industry(config):
    intent = gd.infer_intent(None, config)
    assert intent["industry"] == config["industry"]
    assert intent["source"].startswith("config")


def test_unmatched_keywords_are_reported_as_brand_seeds(config):
    intent = gd.infer_intent("zebra studio", config)
    assert intent["industry"] == config["industry"]
    assert "zebra" in intent["brand_seeds"]
    assert intent["unmatched_terms"] == ["zebra", "studio"]


def test_intent_applies_industry_and_brand_seeds(config):
    intent = gd.infer_intent("urban homes", config)
    applied = gd.apply_intent(config, intent)
    assert applied["industry"] == "real_estate"
    assert "urban" in applied["brand_keywords"]
    assert config["industry"] == "real_estate"  # original config is not mutated


def test_short_tokens_are_ignored():
    assert gd.tokenize_keywords("a ai go nest") == ["nest"]


def test_most_matched_profile_wins_and_ties_prefer_default(config):
    tied = gd.infer_intent("pea", {**config, "industry": "technology"})
    assert tied["industry"] == "technology"


# --- scoring and filters --------------------------------------------------

def test_scores_are_never_negative_for_generated_names(config):
    scored = gd.score_candidates(config)
    assert scored and min(score for score, _ in scored) >= config["premium_gate"]["min_score"]


def test_blocked_terms_are_filtered_as_substrings(config):
    names = [name for _, name in gd.score_candidates(config)]
    assert not any("sex" in name or "hate" in name for name in names)


def test_repeated_letters_are_rejected(config):
    names = [name for _, name in gd.score_candidates(config)]
    assert all(name[0] != name[2] and name[1] != name[3] for name in names)


def test_candidate_names_are_unique_and_four_letters(config):
    names = gd.generate_names(config)
    assert len(names) == len(set(names))
    assert all(len(name) == 4 and name.isalpha() for name in names)


def test_non_four_letter_length_is_rejected(config):
    with pytest.raises(ValueError):
        gd.generate_names({**config, "name_length": 5})


def test_build_domains_rotates_tlds(config):
    domains = gd.build_domains(["abcd", "efgh"], {"tlds": [".com", ".in"], "preferred_tlds": [".com", ".in"]})
    assert domains == ["abcd.com", "efgh.in"]


# --- CLI ------------------------------------------------------------------

def test_cli_writes_report_describing_inference(tmp_path):
    output = tmp_path / "candidates.json"
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "generate_domains.py"), "--keywords", "finance loans", "--output", str(output), "--report", str(report)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Industry inferred: finance" in result.stdout
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["intent"]["industry"] == "finance"
    assert data["keywords_input"] == "finance loans"
    assert len(json.loads(output.read_text(encoding="utf-8"))) == data["written_count"]
