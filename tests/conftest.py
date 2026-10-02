"""Fixtures comunes: entorno aislado, sin red y sin tocar data/ real."""

import pytest

TEST_TOKEN = "test-token-0123456789abcdef-XYZ"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from agent import email_notify, sheets_sync
    from agent.config import Config

    # Sin credenciales externas: ningún test puede escribir en Sheets/SMTP/n8n reales.
    for var in (
        "GOOGLE_SHEETS_CREDENTIALS_JSON", "GOOGLE_SHEET_ID", "SMTP_HOST", "SMTP_USER",
        "SMTP_PASSWORD", "DOCUSEAL_API_KEY", "DOCUSEAL_TEMPLATE_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Config, "N8N_WEBHOOK_URL", "")
    monkeypatch.setattr(Config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(Config, "AUTH_TOKEN", TEST_TOKEN)
    monkeypatch.setattr(Config, "DOCUSEAL_WEBHOOK_SECRET", "docuseal-test-secret")
    monkeypatch.setattr(Config, "PUBLIC_DEMO_ENABLED", False)

    monkeypatch.setattr(sheets_sync, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sheets_sync, "QUEUE_FILE", tmp_path / "leads_queue.json")
    monkeypatch.setattr(sheets_sync, "SYNCED_CACHE_FILE", tmp_path / "leads_synced.json")
    monkeypatch.setattr(email_notify, "OUTBOX_FILE", tmp_path / "email_outbox.jsonl")
    sheets_sync.GoogleSheetsSync.invalidate_read_cache()

    from server import app as server_app
    server_app.identify_limiter.reset()
    server_app.auth_verify_limiter.reset()
    server_app.demo_token_limiter.reset()
    server_app.CALLS_DATABASE.clear()
    server_app.LEADS_DATABASE.clear()
    server_app.EVENT_LOG.clear()
    yield


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from server.app import app
    return TestClient(app)
