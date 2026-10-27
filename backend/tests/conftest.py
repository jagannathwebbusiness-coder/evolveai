import pytest


@pytest.fixture(autouse=True)
def isolated_private_storage(tmp_path, monkeypatch):
    """No test, including live API checks, may read/write the user's database or .env."""
    monkeypatch.setenv("EVOLVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EVOLVE_WORKSPACE_DIR", str(tmp_path / "workspace"))
    # Deterministic fixture identity; the real key/provider are never read by offline tests.
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MAX_RETRIES", "0")
