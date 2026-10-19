import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_batch_delay(monkeypatch):
    """Tests never wait between GoDaddy batches."""
    import settings
    monkeypatch.setattr(settings, "DELAY_BETWEEN_BATCHES_SECONDS", 0)
