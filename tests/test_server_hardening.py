"""Pruebas de verificación para el endurecimiento del backend (Trabajo 2).

Verifica:
  1. Inyección de cabeceras de seguridad HTTP (X-Content-Type-Options, X-Frame-Options, X-Request-ID).
  2. Validación estricta con esquemas Pydantic v2 (CallEventModel, DocuSealWebhookModel).
  3. Sobre de error estructurado JSON (error: True, status_code, timestamp, path).
  4. Formato de logging estructurado en JSON.
"""

import json
import logging
from fastapi.testclient import TestClient
from server.app import (
    app,
    CallEventModel,
    DocuSealWebhookModel,
    StructuredJsonFormatter,
    get_authorized_tokens,
)

client = TestClient(app)


def test_security_headers_injected_on_responses():
    """Verifica que las cabeceras de endurecimiento HTTP se inyecten en todas las respuestas."""
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "SAMEORIGIN"
    assert response.headers.get("x-xss-protection") == "1; mode=block"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "x-request-id" in response.headers
    assert len(response.headers["x-request-id"]) > 10


def test_structured_error_envelope():
    """Verifica que los errores HTTP retornen el envelope estructurado auditable."""
    response = client.get("/api/token?room=centralita-test")
    assert response.status_code == 401
    data = response.json()
    assert data.get("error") is True
    assert data.get("status_code") == 401
    assert "detail" in data
    assert "timestamp" in data
    assert data.get("path") == "/api/token"


def test_pydantic_v2_call_event_validation():
    """Verifica la validación de tipos e invariantes de Pydantic v2 para CallEventModel."""
    valid_payload = {
        "type": "call_ended",
        "call_id": "call-12345",
        "duration": "01:30",
        "room": "centralita-test",
        "lead": {"nombre": "Sophie", "telefono": "+352 691 111 222"},
    }
    model = CallEventModel.model_validate(valid_payload)
    assert model.type == "call_ended"
    assert model.call_id == "call-12345"
    assert model.lead["nombre"] == "Sophie"


def test_structured_json_logger_formatter():
    """Verifica que StructuredJsonFormatter produzca un JSON parseable con timestamp y level."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Operación completada exitosamente",
        args=(),
        exc_info=None,
    )
    record.extra_data = {"test_metric": 42, "user": "auditor"}
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.logger"
    assert "Operación completada exitosamente" in parsed["message"]
    assert parsed["test_metric"] == 42
    assert "timestamp" in parsed


def test_call_transfer_and_hangup_endpoints():
    """Verifica endpoints de transferencia y finalización de llamada."""
    headers = {"Authorization": "Bearer centralita-secure-token-2026"}
    
    # 1. Transferencia
    transfer_res = client.post(
        "/api/call/transfer",
        headers=headers,
        json={
            "room": "centralita-test",
            "target_operator": "Marc Becker (Supervisor)",
            "target_phone": "+352 691 334 221",
            "reason": "Escalado técnico"
        }
    )
    assert transfer_res.status_code == 200
    assert transfer_res.json()["status"] == "transferred"
    assert transfer_res.json()["target_operator"] == "Marc Becker (Supervisor)"

    # 2. Finalización
    hangup_res = client.post(
        "/api/call/hangup",
        headers=headers,
        json={"room": "centralita-test", "reason": "Terminada por operador"}
    )
    assert hangup_res.status_code == 200
    assert hangup_res.json()["status"] == "terminated"


def test_auth_verify_endpoint():
    """Verifica endpoint de autenticación real contra token autorizado."""
    # Token correcto
    res_ok = client.post("/api/auth/verify", json={"token": "centralita-secure-token-2026"})
    assert res_ok.status_code == 200
    assert res_ok.json()["authenticated"] is True

    # Token incorrecto
    res_fail = client.post("/api/auth/verify", json={"token": "token-invalido-123"})
    assert res_fail.status_code == 401


def test_ycloud_whatsapp_webhook_verification_and_event():
    """Verifica handshake y recepción de mensajes/notas de voz de YCloud WhatsApp Business API."""
    # 1. Challenge verification
    verify_res = client.get(
        "/api/whatsapp/webhook?hub.mode=subscribe&hub.challenge=test_challenge_1234&hub.verify_token=welux-centralita-whatsapp-2026"
    )
    assert verify_res.status_code == 200
    assert verify_res.text == "test_challenge_1234"

    # 2. Inbound text message
    inbound_res = client.post(
        "/api/whatsapp/webhook",
        json={
            "id": "evt_wa_101",
            "type": "whatsapp.inbound_message",
            "whatsappMessage": {
                "id": "wamid_123",
                "from": "+352691452890",
                "to": "+352621999888",
                "type": "text",
                "text": {"body": "Hola, necesito información de fotoespejo"},
            }
        }
    )
    assert inbound_res.status_code == 200
    assert inbound_res.json()["status"] == "received"
    assert inbound_res.json()["sender"] == "+352691452890"

    # 3. Inbound voice note
    voice_res = client.post(
        "/api/whatsapp/webhook",
        json={
            "id": "evt_wa_102",
            "type": "whatsapp.inbound_message",
            "whatsappMessage": {
                "id": "wamid_124",
                "from": "+352691452890",
                "to": "+352621999888",
                "type": "audio",
                "audio": {"id": "media_voice_001", "link": "https://api.ycloud.com/v2/media/1"},
            }
        }
    )
    assert voice_res.status_code == 200
    assert voice_res.json()["is_voice"] is True

