"""Google Sheets (escritura RAW, lectura en vivo, degradación honesta), email y client-identify."""

import json
import urllib.error

import pytest

from agent import sheets_sync
from agent.email_notify import EmailNotifier
from agent.sheets_sync import PHONE_NEEDS_REPAIR, GoogleSheetsSync, SheetReadResult
from scripts.repair_sheet_phones import recover_phone

LEAD = {
    "nombre": "Jean Dupont",
    "telefono": "+352 691 123 456",
    "email": "jean@dupont.lu",
    "empresa": "Dupont Consulting",
    "motivo": "Alquiler Fotoespejo",
    "detalles": "Para evento de fin de año en Kirchberg",
    "fecha_evento": "2026-12-18",
}


def configured_sync(monkeypatch) -> GoogleSheetsSync:
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_JSON", "{}")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet-test")
    sync = GoogleSheetsSync()
    monkeypatch.setattr(sync, "_get_access_token", lambda: "tok")
    return sync


# ------------------------------------------------------------ teléfono / RAW
def test_phone_is_written_verbatim_without_apostrophe():
    """Con valueInputOption=RAW el valor se guarda literal: el apóstrofe quedaría visible.
    El teléfono debe salir EXACTAMENTE como se recibió."""
    row = GoogleSheetsSync().format_row(dict(LEAD))
    assert row[3] == "+352 691 123 456"
    assert not row[3].startswith("'")


def test_append_request_uses_raw_input_option(monkeypatch):
    sync = configured_sync(monkeypatch)
    captured = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return Resp()

    monkeypatch.setattr(sheets_sync.urllib.request, "urlopen", fake_urlopen)
    assert sync._send_to_sheets_api(sync.format_row(dict(LEAD))) is True
    assert "valueInputOption=RAW" in captured["url"]
    assert "USER_ENTERED" not in captured["url"]
    assert captured["body"]["values"][0][3] == "+352 691 123 456"


def test_row_layout_and_lead_id():
    sync = GoogleSheetsSync()
    row = sync.format_row(dict(LEAD))
    assert len(row) == len(GoogleSheetsSync.COLUMNS) == 12
    assert row[0] == sync.generate_lead_id(LEAD) and len(row[0]) == 16
    assert row[2] == "Jean Dupont" and row[5] == "Dupont Consulting"
    assert row[8] == "Por cotizar"  # sin importes inventados
    assert row[10] == "BORRADOR"


# --------------------------------------------------------- lectura en vivo
def test_read_parses_rows_and_flags_broken_phones(monkeypatch):
    sync = configured_sync(monkeypatch)
    rows = [
        ["id1", "2026-10-01", "Ana", "+352 691 111 222", "a@b.lu", "ACME", "Web", "Resumen", "Por cotizar", "Contactado", "BORRADOR", "Sofía"],
        ["id2", "2026-10-01", "Luc", "#ERROR!", "", "", "Alquiler"],
        ["id3", "2026-10-01", "Eva", "'+352 621 000 111"],
        ["solo-id"],
    ]
    monkeypatch.setattr(sync, "_fetch_rows", lambda: rows)
    result = __import__("asyncio").run(sync.read_leads(use_cache=False))
    assert result.status == "ok"
    assert [lead["id"] for lead in result.leads] == ["id1", "id2", "id3"]
    assert result.leads[0]["phone"] == "+352 691 111 222"
    assert result.leads[0]["stage"] == "contactado"
    assert result.leads[1]["phone"] == PHONE_NEEDS_REPAIR
    assert result.leads[2]["phone"] == "+352 621 000 111"  # apóstrofe heredado eliminado
    assert result.rows_needing_repair == 1


@pytest.mark.asyncio
async def test_read_reports_error_when_sheet_is_down(monkeypatch):
    sync = configured_sync(monkeypatch)

    def boom():
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(sync, "_fetch_rows", boom)
    result = await sync.read_leads(use_cache=False)
    assert result.status == "error" and result.leads == [] and "no disponible" in result.error


@pytest.mark.asyncio
async def test_read_not_configured():
    result = await GoogleSheetsSync().read_leads()
    assert result.status == "not_configured"


# ----------------------------------------------------------- /api/leads
def test_api_leads_live_source(client, auth_headers, monkeypatch):
    async def fake_read(self, use_cache=True):
        return SheetReadResult(status="ok", leads=[{"id": "x", "name": "Ana"}])
    monkeypatch.setattr(GoogleSheetsSync, "read_leads", fake_read)
    body = client.get("/api/leads", headers=auth_headers).json()
    assert body["source"] == "google_sheets_live" and body["degraded"] is False and body["count"] == 1


def test_api_leads_sheet_down_is_declared_not_hidden(client, auth_headers, monkeypatch):
    async def fake_read(self, use_cache=True):
        return SheetReadResult(status="error", error="Google Sheets respondió HTTP 503")
    monkeypatch.setattr(GoogleSheetsSync, "read_leads", fake_read)
    body = client.get("/api/leads", headers=auth_headers).json()
    assert body["degraded"] is True
    assert body["source"] == "memory_session"
    assert body["sheet_status"] == "error" and "503" in body["sheet_error"]
    assert body["leads"] == []  # nunca datos simulados


# --------------------------------------------------------------- cola local
@pytest.mark.asyncio
async def test_offline_queue_and_idempotency():
    sync = GoogleSheetsSync()
    lead = {"nombre": "Marc Weber", "telefono": "+352 621 999 888", "motivo": "Consultoría"}
    res = await sync.sync_lead(dict(lead))
    assert res["status"] == "QUEUED_OFFLINE"
    assert len(sync.load_queue()) == 1
    await sync.sync_lead(dict(lead))
    assert len(sync.load_queue()) == 1  # sin duplicados en cola

    sync.mark_synced(res["lead_id"], res["row"])
    assert (await sync.sync_lead(dict(lead)))["status"] == "ALREADY_SYNCED"
    # El caché de sincronizados no guarda PII
    assert "Marc Weber" not in json.dumps(sync.load_synced_cache())


@pytest.mark.asyncio
async def test_retries_then_queues_on_api_failure(monkeypatch):
    sync = configured_sync(monkeypatch)
    calls = {"n": 0}

    def failing(_row):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(sync, "_send_to_sheets_api", failing)
    monkeypatch.setattr(sheets_sync.asyncio, "sleep", no_sleep)
    res = await sync.sync_lead(dict(LEAD))
    assert calls["n"] == 3 and res["status"] == "QUEUED_ERROR"


# ------------------------------------------------------- reparación #ERROR!
@pytest.mark.parametrize("formatted,formula,expected", [
    ("#ERROR!", "=+352 691 452 890", "+352 691 452 890"),
    ("#ERROR!", "+352 691 452 890", "+352 691 452 890"),
    ("'+352 621 000 111", "'+352 621 000 111", "+352 621 000 111"),
    ("+352 621 000 111", "+352 621 000 111", None),
    ("#ERROR!", "=SUM(A1:A3)", None),
])
def test_repair_script_recovers_original_phone(formatted, formula, expected):
    assert recover_phone(formatted, formula) == expected


# -------------------------------------------------------------------- email
def test_email_content_escapes_html():
    _, html_body = EmailNotifier().build_email_content({
        "lead": {"nombre": "<script>alert(1)</script>", "motivo": "Web & CRM", "telefono": "+352 1"},
        "duration_seconds": 10,
    })
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body and "Web &amp; CRM" in html_body


def test_email_subject_has_no_header_injection():
    subject = EmailNotifier().build_subject({"lead": {"nombre": "Ana\r\nBcc: victim@x.com", "motivo": "x"}})
    assert "\n" not in subject and "\r" not in subject


@pytest.mark.asyncio
async def test_email_without_smtp_goes_to_outbox():
    from agent import email_notify
    res = await EmailNotifier().send_post_call_notification({"lead": {"nombre": "Ana"}, "duration_seconds": 1})
    assert res["status"] == "QUEUED_NO_SMTP"
    lines = email_notify.OUTBOX_FILE.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["subject"].startswith("[La Centralita]")


# ---------------------------------------------------------- client-identify
VALID_ID = {"nombre": "Ana Weber", "telefono": "+352 691 123 456", "empresa": "ACME", "consentimiento": True}


def test_client_identify_requires_consent(client):
    res = client.post("/api/client-identify", json={**VALID_ID, "consentimiento": False})
    assert res.status_code == 422


@pytest.mark.parametrize("phone", ["abc", "123", "+352 691 123 456 789 012 345", "=HYPERLINK(1)"])
def test_client_identify_rejects_bad_phone(client, phone):
    assert client.post("/api/client-identify", json={**VALID_ID, "telefono": phone}).status_code == 422


def test_client_identify_validation_error_does_not_echo_input(client):
    res = client.post("/api/client-identify", json={**VALID_ID, "telefono": "=HYPERLINK(1)"})
    assert "HYPERLINK" not in res.text


def test_client_identify_reports_real_delivery_status(client):
    res = client.post("/api/client-identify", json=VALID_ID)
    assert res.status_code == 200
    body = res.json()
    # Sin Sheets ni SMTP configurados: debe declararse parcial, no "éxito".
    assert body["status"] == "partial"
    assert body["sheets_status"] == "QUEUED_OFFLINE"
    assert body["email_status"] == "QUEUED_NO_SMTP"
    assert len(body["lead_id"]) == 16


def test_client_identify_rate_limited(client):
    codes = [client.post("/api/client-identify", json={**VALID_ID, "nombre": f"Ana {i}x"}).status_code for i in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_client_identify_broadcasts_to_authenticated_monitor(client):
    from tests.conftest import TEST_TOKEN
    with client.websocket_connect("/ws/monitor") as ws:
        ws.send_text(json.dumps({"action": "auth", "token": TEST_TOKEN}))
        ws.receive_json()
        client.post("/api/client-identify", json=VALID_ID)
        msg = ws.receive_json()
        assert msg["type"] == "lead_created" and msg["lead"]["nombre"] == "Ana Weber"
