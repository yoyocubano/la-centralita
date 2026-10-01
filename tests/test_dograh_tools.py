import pytest
from agent.agent import check_calendar_availability, book_technical_meeting


@pytest.mark.asyncio
async def test_dograh_calendar_availability_tool():
    """Verifica que la herramienta Dograh de disponibilidad devuelva slots válidos."""
    res = await check_calendar_availability("2026-10-22")
    assert isinstance(res, str)
    assert "2026-10-22" in res
    assert "16:30" in res


@pytest.mark.asyncio
async def test_dograh_book_technical_meeting_tool():
    """Verifica que la herramienta Dograh de reserva confirme la reunión con el cliente."""
    res = await book_technical_meeting(
        client_name="Marc Becker",
        phone="+352 691 334 221",
        event_type="Lanzamiento automotriz",
        requested_date="2026-10-22",
        requested_time="11:00",
    )
    assert isinstance(res, str)
    assert "Marc Becker" in res
    assert "11:00" in res
    assert "confirmada" in res.lower()
