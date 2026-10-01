"""Herramientas de agenda del agente: registran solicitudes, nunca confirman citas falsas."""

import json

import pytest

import agent.agent as voice_agent


@pytest.mark.asyncio
async def test_availability_does_not_invent_slots():
    res = await voice_agent.check_calendar_availability("2026-10-22")
    assert "2026-10-22" in res
    assert "confirmará" in res


@pytest.mark.asyncio
async def test_booking_registers_pending_request(tmp_path, monkeypatch):
    target = tmp_path / "appointment_requests.jsonl"
    monkeypatch.setattr(voice_agent, "APPOINTMENT_REQUESTS_FILE", target)
    res = await voice_agent.book_technical_meeting(
        client_name="Marc Becker",
        phone="+352 691 334 221",
        event_type="Lanzamiento",
        requested_date="2026-10-22",
        requested_time="11:00",
    )
    assert "pendiente de confirmación" in res.lower()
    assert "confirmada con éxito" not in res.lower()
    stored = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert stored[0]["status"] == "PENDIENTE_CONFIRMACION"
    assert stored[0]["requested_time"] == "11:00"
