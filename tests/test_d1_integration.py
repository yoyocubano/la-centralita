"""Integración del backend con el gateway D1 (opción B), con un gateway falso en memoria."""

import pytest

from agent import d1_gateway
from agent.d1_gateway import D1Gateway, D1GatewayError
from server import app as server_app

SECRET = "s" * 40


class FakeStore:
    def __init__(self):
        self.leads, self.calls, self.turns, self.events, self.appointments = {}, {}, [], [], []
        self.stages, self.hits, self.fail = {}, {}, False

    def check(self):
        if self.fail:
            raise D1GatewayError("D1 gateway no disponible (URLError)")


@pytest.fixture
def fake_d1(monkeypatch):
    store = FakeStore()

    async def request(self, method, path, body=None, query=None):
        store.check()
        if path == "/v1/leads" and method == "POST":
            store.leads[body["id"]] = {**store.leads.get(body["id"], {}), **body}
            return {"id": body["id"]}
        if path == "/v1/leads":
            return {"leads": list(store.leads.values())}
        if path.endswith("/stage"):
            lead_id = path.split("/")[3]
            if lead_id not in store.leads:
                raise D1GatewayError("D1 gateway HTTP 404: lead no encontrado", 404)
            store.leads[lead_id]["stage"] = body["stage"]
            return {"ok": True}
        if path == "/v1/calls" and method == "POST":
            store.calls[body["id"]] = {**store.calls.get(body["id"], {}), **body}
            return {"id": body["id"]}
        if path == "/v1/calls":
            return {"calls": list(store.calls.values())}
        if path.endswith("/turns"):
            store.turns.append((path.split("/")[3], body["role"], body["text"]))
            return {"seq": len(store.turns)}
        if path == "/v1/events" and method == "POST":
            store.events.append(body["payload"])
            return {"id": len(store.events)}
        if path == "/v1/events":
            after = int(query["after"])
            return {"events": [{"id": i + 1, "payload": p} for i, p in enumerate(store.events) if i + 1 > after]}
        if path == "/v1/appointments" and method == "POST":
            store.appointments.append(body)
            return {"id": body["id"]}
        if path == "/v1/appointments":
            return {"appointments": store.appointments}
        if path == "/v1/ratelimit":
            store.hits[body["bucket"]] = store.hits.get(body["bucket"], 0) + 1
            return {"allowed": store.hits[body["bucket"]] <= body["max"]}
        raise AssertionError(f"ruta inesperada {method} {path}")

    monkeypatch.setenv("D1_GATEWAY_URL", "https://d1.example.test")
    monkeypatch.setenv("D1_GATEWAY_SECRET", SECRET)
    monkeypatch.setattr(D1Gateway, "_request", request)
    return store


def test_gateway_disabled_without_secret(monkeypatch):
    monkeypatch.setenv("D1_GATEWAY_URL", "https://d1.example.test")
    monkeypatch.setenv("D1_GATEWAY_SECRET", "corto")
    assert D1Gateway().enabled is False


def test_gateway_sends_bearer_and_tenant(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true, "db": true}'

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, auth=req.get_header("Authorization"), tenant=req.get_header("X-tenant-id"))
        return Resp()

    monkeypatch.setattr(d1_gateway.urllib.request, "urlopen", fake_urlopen)
    gw = D1Gateway(url="https://d1.example.test/", secret=SECRET, tenant="welux")
    assert gw._request_blocking("GET", "/v1/health", None, None)["db"] is True
    assert seen == {"url": "https://d1.example.test/v1/health", "auth": f"Bearer {SECRET}", "tenant": "welux"}


def test_call_lifecycle_persisted(client, auth_headers, fake_d1):
    base = {"call_id": "call-abc", "room": "sala-1"}
    for event in (
        {**base, "type": "call_started", "timestamp": "2026-10-02T10:00:00Z"},
        {**base, "type": "transcript_delta", "role": "user", "text": "Hola"},
        {**base, "type": "call_ended", "duration": "01:30",
         "lead": {"nombre": "Ana", "telefono": "+352 621 000 000", "motivo": "Boda"}},
    ):
        res = client.post("/api/call-event", json=event, headers=auth_headers)
        assert res.status_code == 200 and res.json()["persisted"] is True

    call = fake_d1.calls["call-abc"]
    assert call["status"] == "ended" and call["duration_seconds"] == 90
    assert call["lead_id"] in fake_d1.leads
    assert fake_d1.leads[call["lead_id"]]["source"] == "llamada"
    assert fake_d1.turns == [("call-abc", "user", "Hola")]
    assert len(fake_d1.events) == 3


def test_call_event_reports_d1_failure(client, auth_headers, fake_d1):
    fake_d1.fail = True
    res = client.post("/api/call-event", json={"type": "call_started", "call_id": "call-x"}, headers=auth_headers)
    assert res.status_code == 200 and res.json()["persisted"] is False


def test_leads_and_stage_from_d1(client, auth_headers, fake_d1):
    fake_d1.leads["lead1"] = {"id": "lead1", "nombre": "Ana", "stage": "nuevo", "created_at": "2026-10-02"}
    body = client.get("/api/leads", headers=auth_headers).json()
    assert body["source"] == "cloudflare_d1" and body["count"] == 1

    res = client.post("/api/leads/lead1/stage", json={"stage": "ganado"}, headers=auth_headers)
    assert res.status_code == 200 and fake_d1.leads["lead1"]["stage"] == "ganado"
    assert client.post("/api/leads/nope/stage", json={"stage": "ganado"}, headers=auth_headers).status_code == 404

    fake_d1.fail = True
    assert client.post("/api/leads/lead1/stage", json={"stage": "nuevo"}, headers=auth_headers).status_code == 502


def test_leads_fall_back_when_d1_down(client, auth_headers, fake_d1):
    fake_d1.fail = True
    body = client.get("/api/leads", headers=auth_headers).json()
    assert body["source"] == "memory_session" and "d1_error" in body


def test_events_polling_uses_durable_cursor(client, auth_headers, fake_d1):
    client.post("/api/call-event", json={"type": "call_started", "call_id": "call-1"}, headers=auth_headers)
    first = client.get("/api/events", headers=auth_headers).json()
    assert first["instance"] == "d1" and first["cursor"] == 1 and len(first["events"]) == 1
    again = client.get("/api/events", params={"since": first["cursor"], "instance": "d1"}, headers=auth_headers).json()
    assert again["events"] == [] and again["cursor"] == 1


def test_portal_lead_stored_in_d1(client, fake_d1):
    res = client.post("/api/client-identify", json={
        "nombre": "Ana Pérez", "telefono": "+352 621 000 000", "email": "ana@example.com",
        "consentimiento": True,
    })
    assert res.status_code == 200, res.text
    assert res.json()["d1_status"] == "stored"
    (lead,) = fake_d1.leads.values()
    assert lead["source"] == "portal" and lead["consent_at"]


def test_shared_rate_limit_via_d1(client, fake_d1, monkeypatch):
    monkeypatch.setattr(server_app.identify_limiter, "max_requests", 1)
    payload = {"nombre": "Ana", "telefono": "+352 621 000 000", "email": "a@example.com", "consentimiento": True}
    assert client.post("/api/client-identify", json=payload).status_code == 200
    assert client.post("/api/client-identify", json=payload).status_code == 429
    assert any(k.startswith("identify:") for k in fake_d1.hits)


def test_appointments_from_d1(client, auth_headers, fake_d1):
    fake_d1.appointments.append({"id": "a1", "client_name": "Ana", "status": "PENDIENTE_CONFIRMACION"})
    body = client.get("/api/appointments", headers=auth_headers).json()
    assert body["source"] == "cloudflare_d1" and body["count"] == 1
