"""Verificación de los hallazgos de seguridad H-001…H-007 y de los nuevos (H-008+).

Cada test reproduce el ataque concreto y comprueba que ahora falla.
"""

import json
import re
from pathlib import Path

import pytest

from agent.config import Config
from agent.post_call import PostCallProcessor, mask_phone, redact_pii
from tests.conftest import TEST_TOKEN

ROOT = Path(__file__).resolve().parent.parent
LEAKED_TOKEN = "centralita-secure-token-2026"


# ---------------------------------------------------------------- H-001 CORS
def test_h001_cors_allows_only_listed_origins(client):
    ok = client.options("/api/status", headers={"Origin": "https://la-centralita.web.app", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "https://la-centralita.web.app"
    assert ok.headers.get("access-control-allow-credentials") in (None, "false")

    for evil in ("https://sitio-malicioso.com", "https://la-centralita.web.app.evil.com", "https://evil-la-centralita.web.app", "null"):
        res = client.options("/api/status", headers={"Origin": evil, "Access-Control-Request-Method": "GET"})
        assert res.headers.get("access-control-allow-origin") is None, evil


def test_h001_cors_has_no_wildcard():
    assert "*" not in Config.CORS_ALLOWED_ORIGINS
    assert all(o.startswith(("https://", "http://localhost", "http://127.0.0.1")) for o in Config.CORS_ALLOWED_ORIGINS)


# ------------------------------------------------- H-002 / H-003 / H-004: auth
def test_leaked_public_token_is_rejected_everywhere(client):
    """El token publicado en el frontend ya no da acceso a nada."""
    headers = {"Authorization": f"Bearer {LEAKED_TOKEN}"}
    assert client.get("/api/token?room=centralita-test", headers=headers).status_code == 403
    assert client.get("/api/leads", headers=headers).status_code == 403
    assert client.post("/api/call-event", json={"type": "call_started"}, headers=headers).status_code == 403


def test_no_hardcoded_tokens_in_source():
    for path in ["server/app.py", "agent/config.py", "panel/app.js", "panel/index.html", "index.html", "web/app.js", "web/test-page.html"]:
        src = (ROOT / path).read_text(encoding="utf-8")
        assert LEAKED_TOKEN not in src and "welux-centralita-whatsapp-2026" not in src, path


def test_livekit_secret_is_not_an_api_token(client, monkeypatch):
    monkeypatch.setattr(Config, "LIVEKIT_API_SECRET", "livekit-secret-value-0123456789")
    res = client.get("/api/leads", headers={"Authorization": "Bearer livekit-secret-value-0123456789"})
    assert res.status_code == 403


def test_fail_closed_without_configured_token(client, monkeypatch):
    monkeypatch.setattr(Config, "AUTH_TOKEN", "")
    assert client.get("/api/leads", headers={"Authorization": "Bearer x"}).status_code == 503
    monkeypatch.setattr(Config, "AUTH_TOKEN", "short")
    assert client.get("/api/leads", headers={"Authorization": "Bearer short"}).status_code == 503


def test_h002_token_endpoint_auth_and_room_restriction(client, auth_headers):
    assert client.get("/api/token?room=centralita-test").status_code == 401
    assert client.get("/api/token?room=centralita-test", headers={"Authorization": "Bearer token-falso"}).status_code == 403
    bad_room = client.get("/api/token?room=sala-privada", headers=auth_headers)
    assert bad_room.status_code == 403 and "no está autorizada" in bad_room.json()["detail"]
    assert client.get("/api/token?room=centralita-test", headers=auth_headers).status_code in (200, 503)


def test_h002_token_has_short_ttl(client, auth_headers, monkeypatch):
    import base64
    monkeypatch.setattr(Config, "LIVEKIT_API_KEY", "devkey_test_123456")
    monkeypatch.setattr(Config, "LIVEKIT_API_SECRET", "devsecret_test_12345678901234567890")
    res = client.get("/api/token?room=centralita-test", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()["token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert claims["exp"] - claims["nbf"] <= 3600
    assert claims["video"]["room"] == "centralita-test"


@pytest.mark.parametrize("path", ["/api/leads", "/api/calls", "/api/system/internal", "/api/status/details"])
def test_pii_endpoints_require_auth(client, path):
    assert client.get(path).status_code == 401


def test_test_webhook_requires_auth(client):
    assert client.post("/api/test-webhook").status_code == 401


def test_h003_call_event_auth_and_schema(client, auth_headers):
    assert client.post("/api/call-event", json={"type": "call_started"}).status_code == 401
    assert client.post("/api/call-event", json={"room": "x"}, headers=auth_headers).status_code == 400
    extra = client.post("/api/call-event", json={"type": "call_started", "evil": "<script>"}, headers=auth_headers)
    assert extra.status_code == 400 and "claves no permitidas" in extra.json()["detail"]
    unknown = client.post("/api/call-event", json={"type": "drop_tables"}, headers=auth_headers)
    assert unknown.status_code == 400
    ok = client.post("/api/call-event", json={"type": "transcript_delta", "role": "user", "text": "Hola"}, headers=auth_headers)
    assert ok.status_code == 200 and ok.json()["status"] == "broadcasted"


def test_h004_websocket_requires_auth_message(client):
    from starlette.websockets import WebSocketDisconnect

    # Sin mensaje de auth / token en la URL (ya no se acepta) -> 4001
    with client.websocket_connect(f"/ws/monitor?token={TEST_TOKEN}") as ws:
        ws.send_text(json.dumps({"action": "ping"}))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4001

    with client.websocket_connect("/ws/monitor") as ws:
        ws.send_text(json.dumps({"action": "auth", "token": LEAKED_TOKEN}))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4001

    with client.websocket_connect("/ws/monitor") as ws:
        ws.send_text(json.dumps({"action": "auth", "token": TEST_TOKEN}))
        assert ws.receive_json()["type"] == "connection_established"
        ws.send_text(json.dumps({"action": "ping"}))
        assert ws.receive_json()["type"] == "pong"


def test_h004_websocket_rejects_foreign_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/monitor", headers={"Origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert exc.value.code == 4003


def test_h004_websocket_receives_broadcast(client, auth_headers):
    with client.websocket_connect("/ws/monitor") as ws:
        ws.send_text(json.dumps({"action": "auth", "token": TEST_TOKEN}))
        ws.receive_json()
        client.post("/api/call-event", json={"type": "transcript_delta", "role": "assistant", "text": "Buenos días"}, headers=auth_headers)
        msg = ws.receive_json()
        assert msg["type"] == "transcript_delta" and msg["text"] == "Buenos días"


# ----------------------------------------------------------------- H-005 PII
@pytest.mark.parametrize("raw", [
    "+352 691 452 890", "00352 691452890", "691 452 890", "+33 6 12 34 56 78", "+352 26 12 34 56", "(+352) 691-452-890",
])
def test_h005_redact_pii_phone_formats(raw):
    out = redact_pii(f"Mi número es {raw}, gracias")
    assert re.sub(r"\D", "", raw) not in re.sub(r"\D", "", out), out
    assert "[PHONE_REDACTED]" in out


def test_h005_redact_pii_keeps_dates_and_amounts():
    text = "Evento el 2026-11-18 o el 18.11.2026, 150 invitados, presupuesto 2.500 €"
    assert redact_pii(text) == text


def test_h005_redact_pii_emails():
    out = redact_pii("Escríbeme a carlos.mendoza@empresa.lu por favor")
    assert "carlos.mendoza@empresa.lu" not in out and "[EMAIL_REDACTED]" in out


def test_mask_phone_for_logs():
    assert mask_phone("+352 691 452 890") == "***90"
    assert "691" not in mask_phone("+352 691 452 890")


@pytest.mark.asyncio
async def test_h005_n8n_payload_contains_no_pii(monkeypatch):
    processor = PostCallProcessor()

    async def fake_extract(_text):
        return {"nombre": "Carlos Mendoza", "telefono": "+352 691 000 000", "email": "c@x.lu",
                "motivo": "Fotoespejo, llamar al +352 691 000 000", "es_lead_valido": True}

    sent = {}

    async def fake_send(payload):
        sent.update(payload)
        return True

    monkeypatch.setattr(processor, "extract_lead_from_transcript", fake_extract)
    monkeypatch.setattr(processor, "send_to_n8n", fake_send)
    await processor.process_call_ended(
        room_name="r", participant_id="p", duration_seconds=30,
        transcript_history=[{"role": "user", "text": "Soy Carlos Mendoza, +352 691 000 000, c@x.lu"}],
        simulated=True,
    )
    blob = json.dumps(sent, ensure_ascii=False)
    for pii in ("Carlos Mendoza", "691 000 000", "c@x.lu"):
        assert pii not in blob, pii


# ----------------------------------------------------- H-006 secretos/URLs
def test_h006_real_webhook_url_not_in_repo():
    offenders = []
    for path in ROOT.rglob("*"):
        if ".git" in path.parts or path.suffix not in {".py", ".js", ".html", ".md", ".json", ".example", ".css"}:
            continue
        if path.name == "test_security_audit_fixes.py":
            continue
        if "weluxdigitalservices.app.n8n.cloud" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_h006_env_examples_use_placeholders():
    for rel in (".env.example", "agent/.env.example"):
        content = (ROOT / rel).read_text(encoding="utf-8")
        assert "tu-instancia-n8n.com" in content


# ------------------------------------------------------------------ H-007 XSS
def _strip_js_comments(src: str) -> str:
    return re.sub(r"//[^\n]*", "", src)


@pytest.mark.parametrize("rel", ["panel/app.js", "web/app.js"])
def test_h007_no_dynamic_html_sinks(rel):
    src = _strip_js_comments((ROOT / rel).read_text(encoding="utf-8"))
    for m in re.finditer(r"\.(innerHTML|outerHTML)\s*[+]?=\s*([^;\n]+)", src):
        assert m.group(2).strip() in ('""', "''"), f"{rel}: {m.group(0)}"
    assert "insertAdjacentHTML" not in src and "document.write" not in src
    assert not re.search(r"\beval\s*\(|new Function\s*\(", src)


def test_h007_index_inline_handlers_have_no_data_interpolation():
    html = (ROOT / "panel/index.html").read_text(encoding="utf-8")
    assert "${" not in html


# ------------------------------------------------------------- DocuSeal (H-010)
def test_docuseal_webhook_rejects_unsigned(client):
    payload = {"event_type": "submission.completed", "data": {"id": 99}}
    assert client.post("/api/docuseal/webhook", json=payload).status_code == 401
    assert client.post("/api/docuseal/webhook", json=payload, headers={"X-Docuseal-Secret": "wrong"}).status_code == 401


def test_docuseal_webhook_accepts_signed(client):
    res = client.post(
        "/api/docuseal/webhook",
        json={"event_type": "submission.completed", "data": {"id": 99}},
        headers={"X-Docuseal-Secret": "docuseal-test-secret"},
    )
    assert res.status_code == 200 and res.json()["docuseal_status"] == "FIRMADO"


def test_docuseal_webhook_fail_closed_without_secret(client, monkeypatch):
    monkeypatch.setattr(Config, "DOCUSEAL_WEBHOOK_SECRET", "")
    res = client.post("/api/docuseal/webhook", json={"event_type": "submission.completed"}, headers={"X-Docuseal-Secret": ""})
    assert res.status_code == 503


# ----------------------------------------------------------- demo pública
def test_public_demo_token_disabled_by_default(client):
    assert client.post("/api/public/demo-token").status_code == 404


def test_public_demo_token_rate_limited(client, monkeypatch):
    monkeypatch.setattr(Config, "PUBLIC_DEMO_ENABLED", True)
    monkeypatch.setattr(Config, "LIVEKIT_API_KEY", "devkey_test_123456")
    monkeypatch.setattr(Config, "LIVEKIT_API_SECRET", "devsecret_test_12345678901234567890")
    codes = [client.post("/api/public/demo-token").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert client.post("/api/public/demo-token").status_code == 429


def test_status_does_not_leak_configuration(client, monkeypatch):
    monkeypatch.setattr(Config, "N8N_WEBHOOK_URL", "https://n8n.example/webhook/secret-path")
    body = client.get("/api/status").text
    assert "secret-path" not in body and "livekit" not in body.lower()


def test_h005_known_names_are_redacted():
    out = redact_pii("Cliente: Soy Carlos Mendoza. Sofía: Gracias, carlos.", ["Carlos Mendoza"])
    assert "Carlos" not in out and "carlos" not in out and "Mendoza" not in out
    assert "Sofía" in out
