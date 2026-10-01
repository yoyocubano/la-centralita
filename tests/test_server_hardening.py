"""Endurecimiento HTTP, esquemas, logging y pipeline post-llamada."""

import json
import logging

import pytest

from agent.post_call import PostCallProcessor, format_duration
from server.app import CallEventModel, StructuredJsonFormatter


def test_security_headers_injected_on_responses(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-xss-protection"] == "0"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert len(response.headers["x-request-id"]) > 10


def test_api_docs_disabled_by_default(client):
    assert client.get("/api/docs").status_code == 404
    assert client.get("/api/openapi.json").status_code == 404


def test_structured_error_envelope(client):
    response = client.get("/api/token?room=centralita-test")
    data = response.json()
    assert response.status_code == 401
    assert data["error"] is True and data["status_code"] == 401 and data["path"] == "/api/token"
    assert response.headers.get("www-authenticate") == "Bearer"


def test_call_event_model_validation():
    model = CallEventModel.model_validate({"type": "call_ended", "call_id": "c-1", "lead": {"nombre": "Sophie"}})
    assert model.lead["nombre"] == "Sophie"
    with pytest.raises(Exception):
        CallEventModel.model_validate({"type": "transcript_delta", "role": "admin", "text": "x"})
    with pytest.raises(Exception):
        CallEventModel.model_validate({"type": "call_ended", "unexpected": 1})


def test_structured_json_logger_formatter():
    record = logging.LogRecord("test.logger", logging.INFO, "t.py", 10, "Operación completada", (), None)
    record.extra_data = {"test_metric": 42}
    parsed = json.loads(StructuredJsonFormatter().format(record))
    assert parsed["level"] == "INFO" and parsed["test_metric"] == 42 and "timestamp" in parsed


def test_internal_metrics_declare_scope_and_assumptions(client, auth_headers):
    data = client.get("/api/system/internal", headers=auth_headers).json()
    assert data["ambito"] == "sesion_actual_del_servidor"
    assert "supuestos" in data and data["llamadas_totales_atendidas"] == 0


def test_call_ended_event_is_persisted_in_session(client, auth_headers):
    client.post("/api/call-event", json={"type": "call_ended", "call_id": "c1", "lead": {"nombre": "Ana"}}, headers=auth_headers)
    calls = client.get("/api/calls", headers=auth_headers).json()
    assert calls["count"] == 1 and calls["calls"][0]["call_id"] == "c1"


def test_format_duration():
    assert format_duration(0) == "00:00"
    assert format_duration(46.2) == "00:46"
    assert format_duration(125) == "02:05"


@pytest.mark.asyncio
async def test_simulated_pipeline_never_touches_sheet_or_email(monkeypatch):
    processor = PostCallProcessor()

    async def forbidden(*_a, **_k):
        raise AssertionError("simulación no debe escribir en Sheets ni enviar email")

    monkeypatch.setattr(processor.sheets_sync, "sync_lead", forbidden)
    monkeypatch.setattr(processor.email_notifier, "send_post_call_notification", forbidden)
    result = await processor.process_call_ended("room", "p", 10, [{"role": "user", "text": "hola " * 20}], simulated=True)
    assert result["simulated"] is True
    assert result["sheets_sync"]["status"] == "SKIPPED_SIMULATION"


@pytest.mark.asyncio
async def test_real_pipeline_writes_sheet_and_email_and_keeps_roles(monkeypatch):
    processor = PostCallProcessor()
    seen = {}

    async def fake_sync(lead):
        seen["sheet"] = lead
        return {"status": "SYNCED_ONLINE", "lead_id": "abc"}

    async def fake_email(info):
        seen["email"] = info
        return {"status": "SENT"}

    monkeypatch.setattr(processor.sheets_sync, "sync_lead", fake_sync)
    monkeypatch.setattr(processor.email_notifier, "send_post_call_notification", fake_email)
    result = await processor.process_call_ended(
        "room", "p", 125,
        [{"role": "assistant", "text": "Hola, soy Sofía"}, {"role": "user", "text": "Quiero una web"}],
    )
    assert result["full_transcript"] == "Sofía (WELUX): Hola, soy Sofía\nCliente: Quiero una web"
    assert seen["email"]["duration_formatted"] == "02:05"
    assert seen["sheet"]["valor_eur"] == "Por cotizar"
    assert result["lead_id"] == "abc"


def test_lead_stage_update_validation(client, auth_headers):
    assert client.post("/api/leads/abc/stage", json={"stage": "ganado"}).status_code == 401
    assert client.post("/api/leads/abc/stage", json={"stage": "borrado"}, headers=auth_headers).status_code == 422
    assert client.post("/api/leads/a%20b/stage", json={"stage": "ganado"}, headers=auth_headers).status_code == 400
    # Sin Sheets configurado no se finge el guardado
    assert client.post("/api/leads/abc/stage", json={"stage": "ganado"}, headers=auth_headers).status_code == 503


def test_lead_stage_update_writes_sheet(client, auth_headers, monkeypatch):
    from agent.sheets_sync import GoogleSheetsSync
    calls = {}

    async def fake_update(self, lead_id, stage):
        calls["args"] = (lead_id, stage)
        return "updated"

    monkeypatch.setattr(GoogleSheetsSync, "update_stage", fake_update)
    res = client.post("/api/leads/abc123/stage", json={"stage": "contactado"}, headers=auth_headers)
    assert res.status_code == 200 and calls["args"] == ("abc123", "contactado")


def test_appointments_endpoint_reads_agent_requests(client, auth_headers, tmp_path, monkeypatch):
    import server.app as server_app
    f = tmp_path / "appointment_requests.jsonl"
    f.write_text('{"id": "1", "status": "PENDIENTE_CONFIRMACION"}\n{"id": "2", "status": "PENDIENTE_CONFIRMACION"}\n', encoding="utf-8")
    monkeypatch.setattr(server_app, "APPOINTMENT_REQUESTS_FILE", f)
    assert client.get("/api/appointments").status_code == 401
    body = client.get("/api/appointments", headers=auth_headers).json()
    assert [a["id"] for a in body["appointments"]] == ["2", "1"]
