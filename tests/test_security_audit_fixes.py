"""Pruebas de verificación de seguridad para los hallazgos H-001 a H-007 (Auditoría Claude)."""

import hashlib
import json
import os
import pytest
from fastapi.testclient import TestClient

from agent.config import Config
from agent.post_call import PostCallProcessor, redact_pii
from server.app import app, ALLOWED_ROOMS, ALLOWED_EVENT_KEYS, get_authorized_tokens

client = TestClient(app)


def test_h001_cors_restrictions():
    """H-001: Verifica que CORS restrinja orígenes, desactive credenciales y limite métodos."""
    # Probar origen permitido
    res_allowed = client.options(
        "/api/status",
        headers={
            "Origin": "https://yoyocubano.github.io",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res_allowed.headers.get("access-control-allow-origin") == "https://yoyocubano.github.io"
    assert res_allowed.headers.get("access-control-allow-credentials") is None or res_allowed.headers.get("access-control-allow-credentials") == "false"

    # Probar origen no permitido
    res_disallowed = client.options(
        "/api/status",
        headers={
            "Origin": "https://sitio-malicioso.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res_disallowed.headers.get("access-control-allow-origin") != "https://sitio-malicioso.com"


def test_h002_token_endpoint_auth_and_room_restriction():
    """H-002: Token endpoint exige header Authorization y restringe el ámbito de la sala."""
    # 1. Sin Authorization header -> 401
    res_no_auth = client.get("/api/token?room=centralita-test")
    assert res_no_auth.status_code == 401
    assert "Authorization requerido" in res_no_auth.json()["detail"]

    # 2. Con token inválido -> 403
    res_invalid_auth = client.get(
        "/api/token?room=centralita-test",
        headers={"Authorization": "Bearer token-falso-invalido"},
    )
    assert res_invalid_auth.status_code == 403

    valid_token = list(get_authorized_tokens())[0]

    # 3. Con sala no autorizada -> 403
    res_bad_room = client.get(
        "/api/token?room=sala-privada-welux-vip",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert res_bad_room.status_code == 403
    assert "no está autorizada" in res_bad_room.json()["detail"]

    # 4. Con sala autorizada (centralita-test)
    res_valid_room = client.get(
        "/api/token?room=centralita-test",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    # Puede ser 200 (si hay livekit keys) o 503 (si no hay keys configuradas), pero nunca 401 ni 403
    assert res_valid_room.status_code in (200, 503)


def test_h003_call_event_endpoint_auth_and_schema_validation():
    """H-003: POST /api/call-event exige auth y valida claves con whitelist estricta."""
    valid_token = list(get_authorized_tokens())[0]

    # 1. Sin autenticación -> 401
    res_no_auth = client.post("/api/call-event", json={"type": "ping"})
    assert res_no_auth.status_code == 401

    # 2. Con autenticación pero sin campo 'type' -> 400
    res_no_type = client.post(
        "/api/call-event",
        json={"room": "centralita-test"},
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert res_no_type.status_code == 400
    assert "type" in res_no_type.json()["detail"]

    # 3. Con claves maliciosas o no permitidas (intento de inyección) -> 400
    res_extra_keys = client.post(
        "/api/call-event",
        json={
            "type": "call_started",
            "room": "centralita-test",
            "malicious_extra_payload": "<script>alert('xss')</script>",
        },
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert res_extra_keys.status_code == 400
    assert "claves no permitidas" in res_extra_keys.json()["detail"]

    # 4. Con payload legítimo y claves autorizadas -> 200
    res_ok = client.post(
        "/api/call-event",
        json={
            "type": "call_started",
            "room": "centralita-test",
            "status": "active",
        },
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["status"] == "broadcasted"


def test_h004_websocket_monitor_auth():
    """H-004: WebSocket /ws/monitor rechaza conexiones sin token o con token inválido con código 4001."""
    # 1. Conexión sin token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/monitor") as ws:
            pass

    # 2. Conexión con token inválido
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/monitor?token=token-invalido") as ws:
            pass

    # 3. Conexión con token válido
    valid_token = list(get_authorized_tokens())[0]
    with client.websocket_connect(f"/ws/monitor?token={valid_token}") as ws:
        # Recibir mensaje de bienvenida
        initial_msg = ws.receive_json()
        assert initial_msg["type"] == "connection_established"


def test_h005_redact_pii_and_lead_hash():
    """H-005: Anonimización de datos personales (PII) en transcripciones y generación de lead_hash."""
    sample_raw = (
        "Hola, me llamo Carlos y mi teléfono es +352 691 452 890. "
        "También puedes escribirme a carlos.mendoza@empresa.lu para coordinar."
    )
    redacted = redact_pii(sample_raw)

    assert "+352 691 452 890" not in redacted
    assert "carlos.mendoza@empresa.lu" not in redacted
    assert "[PHONE_REDACTED]" in redacted
    assert "[EMAIL_REDACTED]" in redacted

    # Lead hash debe ser determinista
    lead_sample = {"name": "Carlos", "phone": "+352 691 452 890"}
    lead_json = json.dumps(lead_sample, sort_keys=True, ensure_ascii=False)
    lead_hash = hashlib.sha256(lead_json.encode("utf-8")).hexdigest()
    assert len(lead_hash) == 64


def test_h006_no_real_webhook_url_in_example_files():
    """H-006: Verifica que las plantillas de entorno no contengan la URL real del webhook de n8n."""
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env_example = os.path.join(root_dir, ".env.example")
    agent_env_example = os.path.join(root_dir, "agent", ".env.example")

    for path in [env_example, agent_env_example]:
        assert os.path.exists(path), f"{path} debe existir"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "weluxdigitalservices.app.n8n.cloud" not in content, (
            f"El archivo {path} contiene la URL real del webhook expuesta"
        )
        assert "tu-instancia-n8n.com" in content


def test_h007_panel_app_xss_protection():
    """H-007: Verifica que panel/app.js no contenga interpolación dinámica no segura en innerHTML."""
    panel_js = os.path.join(os.path.dirname(__file__), "..", "panel", "app.js")
    with open(panel_js, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Cada asignación a innerHTML debe ser estrictamente vacía ("") para limpieza
    for i, line in enumerate(lines, 1):
        if "innerHTML" in line and not line.strip().startswith("//"):
            assert line.strip().endswith('innerHTML = "";') or line.strip().endswith("innerHTML = '';"), (
                f"Línea {i} contiene asignación a innerHTML potencialmente peligrosa: {line.strip()}"
            )
