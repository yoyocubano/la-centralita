"""Despliegue serverless en Vercel: entrypoint, Mangum, disco efímero y polling de eventos."""

import json
from pathlib import Path

import pytest

from agent import config as config_module
from agent import email_notify, sheets_sync

ROOT = Path(__file__).resolve().parent.parent
VALID_ID = {"nombre": "Ana Weber", "telefono": "+352 691 123 456", "empresa": "ACME", "consentimiento": True}


def _http_api_event(path: str, method: str = "GET", headers: dict | None = None) -> dict:
    """Evento API Gateway HTTP API v2 mínimo, como el que Mangum espera en Lambda."""
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "centralita.example", **(headers or {})},
        "requestContext": {
            "http": {"method": method, "path": path, "sourceIp": "203.0.113.7", "protocol": "HTTP/1.1", "userAgent": "pytest"},
            "stage": "$default", "requestId": "req-1", "accountId": "000000000000", "apiId": "api",
            "domainName": "centralita.example", "domainPrefix": "centralita", "time": "01/Oct/2026:00:00:00 +0000",
            "timeEpoch": 0,
        },
        "isBase64Encoded": False,
        "body": None,
    }


# ------------------------------------------------------------------ entrypoint
def test_entrypoint_exports_the_fastapi_app():
    import api.index as entry
    from server.app import app

    assert entry.app is app


def test_entrypoint_does_not_define_reserved_handler_name():
    # Vercel reserva `handler` para subclases de BaseHTTPRequestHandler: una instancia
    # de Mangum con ese nombre haría fallar el despliegue.
    import api.index as entry

    assert not hasattr(entry, "handler")


def test_mangum_handler_serves_public_status():
    from api.index import lambda_handler

    res = lambda_handler(_http_api_event("/api/status"), None)
    assert res["statusCode"] == 200
    assert json.loads(res["body"])["auth_configured"] is True


def test_mangum_handler_keeps_auth_fail_closed(auth_headers):
    from api.index import lambda_handler

    assert lambda_handler(_http_api_event("/api/calls"), None)["statusCode"] == 401
    ok = lambda_handler(_http_api_event("/api/calls", headers={"authorization": auth_headers["Authorization"]}), None)
    assert ok["statusCode"] == 200


# ------------------------------------------------------------------ configuración de despliegue
def test_vercel_json_rewrites_everything_to_the_function():
    cfg = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg["framework"] is None
    assert "api/index.py" in cfg["functions"]
    assert {"source": "/(.*)", "destination": "/api/index.py"} in cfg["rewrites"]


def test_backend_requirements_exclude_voice_worker_stack():
    reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    names = {line.split(">=")[0].split("==")[0].strip() for line in reqs.splitlines() if line and not line.startswith("#")}
    assert "mangum" in names
    assert {"fastapi", "livekit-api"} <= names
    # El worker (livekit-agents, plugins, Piper) supera el límite de bundle y no corre en serverless.
    assert not names & {"livekit-agents", "piper-tts", "soundfile", "livekit-plugins-silero"}


# ------------------------------------------------------------------ disco efímero
def test_data_dir_defaults_to_repo_outside_serverless(monkeypatch):
    for var in ("VERCEL", "CENTRALITA_SERVERLESS", "CENTRALITA_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert config_module.resolve_data_dir() == config_module.ROOT_DIR / "data"
    assert config_module.is_serverless() is False


def test_data_dir_uses_tmp_on_vercel(monkeypatch):
    monkeypatch.delenv("CENTRALITA_DATA_DIR", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert config_module.is_serverless() is True
    assert config_module.resolve_data_dir() == Path("/tmp/la-centralita-data")


def test_explicit_data_dir_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("CENTRALITA_DATA_DIR", str(tmp_path / "custom"))
    assert config_module.resolve_data_dir() == tmp_path / "custom"


@pytest.fixture
def read_only_disk(tmp_path, monkeypatch):
    """Simula un disco no escribible: la 'carpeta' de datos es en realidad un archivo."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(sheets_sync, "DATA_DIR", blocker / "data")
    monkeypatch.setattr(sheets_sync, "QUEUE_FILE", blocker / "data" / "leads_queue.json")
    monkeypatch.setattr(sheets_sync, "SYNCED_CACHE_FILE", blocker / "data" / "leads_synced.json")
    monkeypatch.setattr(email_notify, "OUTBOX_FILE", blocker / "data" / "email_outbox.jsonl")
    return blocker


@pytest.mark.asyncio
async def test_email_outbox_degrades_without_raising(read_only_disk):
    res = await email_notify.EmailNotifier().send_post_call_notification({
        "lead": {"nombre": "Ana", "telefono": "+352 691 123 456"},
        "transcripcion": "x", "duration_seconds": 0, "duration_formatted": "0:00", "timestamp_lux": "2026-10-01",
    })
    assert res["status"] == "NOT_PERSISTED_NO_SMTP"


@pytest.mark.asyncio
async def test_sheets_queue_degrades_without_raising(read_only_disk):
    res = await sheets_sync.GoogleSheetsSync().sync_lead({"nombre": "Ana", "telefono": "+352 691 123 456"})
    assert res["status"] == "NOT_PERSISTED_OFFLINE"


def test_client_identify_survives_read_only_disk(client, read_only_disk):
    res = client.post("/api/client-identify", json=VALID_ID)
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "partial"
    assert body["sheets_status"] == "NOT_PERSISTED_OFFLINE"
    assert body["email_status"] == "NOT_PERSISTED_NO_SMTP"


def test_appointments_and_metrics_survive_missing_data(client, auth_headers, monkeypatch, tmp_path):
    from server import app as server_app

    monkeypatch.setattr(server_app, "APPOINTMENT_REQUESTS_FILE", tmp_path / "nope" / "appointments.jsonl")
    assert client.get("/api/appointments", headers=auth_headers).json()["count"] == 0
    assert client.get("/api/system/internal", headers=auth_headers).status_code == 200


# ------------------------------------------------------------------ polling (alternativa al WebSocket)
def test_events_polling_requires_auth(client):
    assert client.get("/api/events").status_code == 401


def test_events_polling_returns_events_after_cursor(client, auth_headers):
    first = client.get("/api/events", headers=auth_headers).json()
    assert first["events"] == [] and first["transport"] == "polling"

    ev = {"type": "call_started", "call_id": "c-1", "room": "centralita-test"}
    assert client.post("/api/call-event", json=ev, headers=auth_headers).status_code == 200

    polled = client.get(f"/api/events?since={first['cursor']}&instance={first['instance']}", headers=auth_headers).json()
    assert polled["reset"] is False
    assert [e["call_id"] for e in polled["events"]] == ["c-1"]

    again = client.get(f"/api/events?since={polled['cursor']}&instance={polled['instance']}", headers=auth_headers).json()
    assert again["events"] == [] and again["cursor"] == polled["cursor"]


def test_events_polling_resets_cursor_on_other_instance(client, auth_headers):
    client.post("/api/call-event", json={"type": "call_status", "status": "ringing"}, headers=auth_headers)
    res = client.get("/api/events?since=999&instance=0123456789ab", headers=auth_headers).json()
    assert res["reset"] is True
    assert [e["type"] for e in res["events"]] == ["call_status"]


def test_events_polling_rejects_bad_parameters(client, auth_headers):
    assert client.get("/api/events?since=-1", headers=auth_headers).status_code == 422
    assert client.get("/api/events?instance=<script>", headers=auth_headers).status_code == 422


def test_status_details_reports_transport(client, auth_headers, monkeypatch):
    from agent.config import Config

    monkeypatch.setattr(Config, "SERVERLESS", True)
    body = client.get("/api/status/details", headers=auth_headers).json()
    assert body["serverless"] is True and body["realtime_transport"] == "polling"


# ------------------------------------------------------------------ rate-limit detrás del proxy de Vercel
def test_rate_limit_ignores_forwarded_for_outside_serverless(client):
    for i in range(5):
        assert client.post("/api/client-identify", json=VALID_ID, headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code == 200
    # Cabecera falsificada distinta y aun así limitado: la IP real es la misma.
    assert client.post("/api/client-identify", json=VALID_ID, headers={"X-Forwarded-For": "198.51.100.99"}).status_code == 429


def test_rate_limit_uses_vercel_forwarded_for_in_serverless(client, monkeypatch):
    from agent.config import Config

    monkeypatch.setattr(Config, "SERVERLESS", True)
    for _ in range(5):
        assert client.post("/api/client-identify", json=VALID_ID, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 200
    assert client.post("/api/client-identify", json=VALID_ID, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429
    # Otro cliente real (Vercel reescribe la cabecera) no hereda el límite del primero.
    assert client.post("/api/client-identify", json=VALID_ID, headers={"X-Forwarded-For": "198.51.100.2, 10.0.0.1"}).status_code == 200


def test_cors_allows_both_firebase_panel_domains(client):
    for origin in ("https://la-centralita.web.app", "https://la-centralita.firebaseapp.com"):
        res = client.options("/api/events", headers={"Origin": origin, "Access-Control-Request-Method": "GET"})
        assert res.headers.get("access-control-allow-origin") == origin
