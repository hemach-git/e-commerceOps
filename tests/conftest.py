import os

os.environ["LLM_PROVIDER"] = "mock"  # tests are offline & deterministic

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOG_DB_PATH", str(tmp_path / "test.db"))
    from app.data.seed_synthetic import seed
    from app.services import workflow
    workflow._client = None
    seed(reset=True)
    yield
    workflow._client = None
