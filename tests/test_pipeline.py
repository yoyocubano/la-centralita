import asyncio
import os
import pytest
from pathlib import Path
from dotenv import load_dotenv

from agent.piper_tts import PiperTTS
from agent.lead_extract import extract_lead
from agent.agent import post_to_n8n
from livekit.api import AccessToken, VideoGrants

load_dotenv()


def test_piper_model_file():
    """Verifica que el modelo ONNX de voz en español exista localmente."""
    model_path = Path("models/piper/es_ES-sharvard-medium.onnx")
    assert model_path.exists(), "El modelo ONNX de Piper debe estar descargado."


def test_piper_tts_synthesis():
    """Verifica que el motor PiperTTS sintetice audio PCM válido."""
    tts = PiperTTS(model_path="models/piper/es_ES-sharvard-medium.onnx")
    assert tts.sample_rate == 22050
    assert tts.num_channels == 1

    chunks = list(tts._local_voice.synthesize("Hola, gracias por llamar a WELUX Events."))
    assert len(chunks) > 0
    assert len(chunks[0].audio_int16_bytes) > 0


def test_livekit_token_generation():
    """Verifica que se generen tokens JWT válidos para la sala centralita-test."""
    token = (
        AccessToken("devkey_test_12345678901234567890", "devsecret_test_12345678901234567890")
        .with_identity("probador-test")
        .with_grants(VideoGrants(room_join=True, room="centralita-test"))
        .to_jwt()
    )
    assert isinstance(token, str)
    assert len(token) > 50


@pytest.mark.asyncio
async def test_n8n_webhook_delivery():
    """Verifica que el webhook de n8n reciba el payload y responda exitosamente."""
    test_payload = {
        "transcript": "Cliente: Hola, busco cotizar sonido para una boda.\nSofía: Encantada, tomamos nota.",
        "lead": {
            "nombre": "Test Automatizado",
            "telefono": "+352 691 000 000",
            "motivo": "Cotización boda",
            "fecha_interes": "2026-11-20",
            "detalles": "Prueba de integración",
            "requiere_seguimiento": True,
        },
        "agent": "Centralita WELUX (Test Unitario)",
    }
    # No debe levantar excepciones al ejecutarse
    await post_to_n8n(test_payload)
