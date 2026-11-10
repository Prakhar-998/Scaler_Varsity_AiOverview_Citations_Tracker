import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def _load(name):
        return json.loads((FIXTURES / name).read_text())
    return _load


SECRET_VARS = ("SERP_PROVIDER", "SERPHOUSE_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "OPENROUTER_KEY", "OPENROUTER_API_KEY", "OPENROUTER_MODELS",
               "GEMINI_API_KEY", "GEMINI_MODEL", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID")


@pytest.fixture(autouse=True)
def offline_env(monkeypatch, tmp_path):
    """Tests never read the real .env or use real keys, so nothing can reach the network, and
    never write to the real runs/ (the cost log is redirected to a temp file)."""
    from tracker import cli

    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(cli, "COST_LOG", tmp_path / "cost_log.csv")
    for var in SECRET_VARS:
        monkeypatch.delenv(var, raising=False)
