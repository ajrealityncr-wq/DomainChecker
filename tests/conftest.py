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
