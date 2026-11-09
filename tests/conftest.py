import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name):
        return json.loads((FIXTURES / name).read_text())
    return _load


SECRET_VARS = ("SERP_PROVIDER", "SERPHOUSE_API_KEY", "OPENROUTER_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODELS",
               "GEMINI_API_KEY", "GEMINI_MODEL", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID")


@pytest.fixture(autouse=True)
def offline_env(monkeypatch):
    """Tests never read the real .env or use real keys, so nothing can reach the network."""
    from tracker import cli

    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    for var in SECRET_VARS:
        monkeypatch.delenv(var, raising=False)
