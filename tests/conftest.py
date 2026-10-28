import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Every test gets its own database, so names used in one test are not used in another."""
    import settings
    monkeypatch.setattr(settings, "APP_DATABASE", tmp_path / "app.sqlite3")


@pytest.fixture(autouse=True)
def no_batch_delay(monkeypatch):
    """Tests never wait between GoDaddy batches."""
    import settings
    monkeypatch.setattr(settings, "DELAY_BETWEEN_BATCHES_SECONDS", 0)


@pytest.fixture(autouse=True)
def one_round_per_search(monkeypatch):
    """A real search runs until stopped; tests run one round unless they test rounds."""
    import settings
    monkeypatch.setattr(settings, "MAX_ROUNDS_PER_SEARCH", 1)


@pytest.fixture(autouse=True)
def no_request_pacing(monkeypatch):
    """Tests never wait for the shared GoDaddy request pace."""
    import godaddy_checker
    monkeypatch.setattr(godaddy_checker.pacer, "wait", lambda: None)


@pytest.fixture(autouse=True)
def isolated_token_store(tmp_path, monkeypatch):
    """Saving a token writes .env and the environment; tests use their own copies, never the real ones."""
    import os
    import settings
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k != "GODADDY_PAT"})
