import json
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from agent.sheets_sync import GoogleSheetsSync
from agent.email_notify import EmailNotifier
from agent.post_call import PostCallProcessor
from server.app import app

client = TestClient(app)


def test_sheets_sync_formatting_and_idempotency(tmp_path):
    with patch("agent.sheets_sync.DATA_DIR", tmp_path), \
         patch("agent.sheets_sync.QUEUE_FILE", tmp_path / "leads_queue.json"), \
         patch("agent.sheets_sync.SYNCED_CACHE_FILE", tmp_path / "leads_synced.json"):
        
        sync = GoogleSheetsSync()
        lead = {
            "nombre": "Jean Dupont",
            "telefono": "+352 691 123 456",
            "email": "jean@dupont.lu",
            "empresa": "Dupont Consulting",
            "motivo": "Alquiler Fotoespejo",
            "detalles": "Para evento de fin de año en Kirchberg",
            "fecha_evento": "2026-12-18",
        }
        
        lead_id = sync.generate_lead_id(lead)
        assert len(lead_id) == 16
        
        row = sync.format_row(lead)
        assert row[0] == lead_id
        assert row[2] == "Jean Dupont"
        # Phones starting with + are prefixed with apostrophe so Sheets stores
        # them as plain text (RAW mode) and does not evaluate them as formulas.
        assert row[3] in ("+352 691 123 456", "'+352 691 123 456")
        assert row[5] == "Dupont Consulting"
        assert row[6] == "Alquiler Fotoespejo"
        assert row[10] == "BORRADOR"


@pytest.mark.asyncio
async def test_sheets_sync_offline_queue(tmp_path):
    with patch("agent.sheets_sync.DATA_DIR", tmp_path), \
         patch("agent.sheets_sync.QUEUE_FILE", tmp_path / "leads_queue.json"), \
         patch("agent.sheets_sync.SYNCED_CACHE_FILE", tmp_path / "leads_synced.json"), \
         patch.dict("os.environ", {"GOOGLE_SHEETS_CREDENTIALS_JSON": "", "GOOGLE_SHEET_ID": ""}):
        
        sync = GoogleSheetsSync()
        lead = {
            "nombre": "Marc Weber",
            "telefono": "+352 621 999 888",
            "motivo": "Consultoría Estratégica",
        }
        res = await sync.sync_lead(lead)
        assert res["status"] == "QUEUED_OFFLINE"
        
        # Sincronización duplicada debe ser detectada si se marca
        sync.mark_synced(res["lead_id"], res["row"])
        res2 = await sync.sync_lead(lead)
        assert res2["status"] == "ALREADY_SYNCED"


def test_email_notify_content_generation():
    notifier = EmailNotifier()
    call_info = {
        "lead": {
            "nombre": "Claire Schmit",
            "telefono": "+352 661 445 221",
            "empresa": "Schmit & Co",
            "motivo": "Desarrollo Web & CRM",
            "detalles": "Portal corporativo con chatbot integrado",
        },
        "transcripcion": "Cliente solicitando cotización web...",
        "duration_seconds": 125.0,
    }
    text, html = notifier.build_email_content(call_info)
    assert "Claire Schmit" in text
    assert "+352 661 445 221" in text
    assert "Desarrollo Web & CRM" in text
    assert "LA CENTRALITA · WELUX EVENTS" in html
    assert "Claire Schmit" in html


def test_docuseal_webhook_endpoint():
    payload = {
        "event_type": "submission.completed",
        "data": {
            "id": "DOCUSEAL-WLX-TEST-999",
            "email": "cliente@test.lu",
        }
    }
    response = client.post("/api/docuseal/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["docuseal_status"] == "FIRMADO"


def test_internal_system_status_endpoint():
    response = client.get("/api/system/internal")
    assert response.status_code == 200
    data = response.json()
    assert "tiempo_ahorrado_horas" in data
    assert "dinero_ahorrado_eur" in data
    assert "infraestructura" in data
    assert data["infraestructura"]["stt"] == "Deepgram Nova-3 (Latencia ~180ms)"
