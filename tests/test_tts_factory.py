"""TTS intercambiable: selección por configuración y adapter CosyVoice contra un servidor HTTP real."""

import asyncio
import socket
import struct

import pytest
from aiohttp import web

from agent.cosyvoice_tts import CosyVoiceTTS
from agent.piper_tts import PiperTTS
from agent.tts_factory import SUPPORTED_PROVIDERS, build_single_tts, build_tts
from livekit.agents import tts as lk_tts


@pytest.fixture
def prompt_wav(tmp_path):
    path = tmp_path / "voz.wav"
    path.write_bytes(b"RIFF....WAVEfmt ")
    return path


def test_supported_providers():
    assert SUPPORTED_PROVIDERS == ("cosyvoice", "piper", "elevenlabs")


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError):
        build_single_tts("google")


def test_default_provider_is_cosyvoice(monkeypatch, prompt_wav):
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.setenv("COSYVOICE_PROMPT_WAV", str(prompt_wav))
    monkeypatch.setenv("COSYVOICE_PROMPT_TEXT", "Hola, soy Sofía.")
    engine = build_tts(fallback="none")
    assert isinstance(engine, CosyVoiceTTS)
    assert engine.sample_rate == 24000


def test_cosyvoice_with_piper_fallback(monkeypatch, prompt_wav):
    monkeypatch.setenv("COSYVOICE_PROMPT_WAV", str(prompt_wav))
    monkeypatch.setenv("COSYVOICE_PROMPT_TEXT", "Hola")
    engine = build_tts(provider="cosyvoice", fallback="piper")
    assert isinstance(engine, lk_tts.FallbackAdapter)


def test_misconfigured_primary_falls_back_to_piper(monkeypatch):
    monkeypatch.delenv("COSYVOICE_PROMPT_WAV", raising=False)
    engine = build_tts(provider="cosyvoice", fallback="piper")
    assert isinstance(engine, PiperTTS)


def test_misconfigured_primary_without_fallback_fails_loudly(monkeypatch):
    monkeypatch.delenv("COSYVOICE_PROMPT_WAV", raising=False)
    with pytest.raises(ValueError):
        build_tts(provider="cosyvoice", fallback="none")


def test_elevenlabs_requires_credentials(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        build_single_tts("elevenlabs")


def test_cosyvoice_mode_validation(prompt_wav):
    with pytest.raises(ValueError):
        CosyVoiceTTS(mode="magic", prompt_wav=str(prompt_wav))
    with pytest.raises(ValueError):
        CosyVoiceTTS(mode="sft", spk_id="")
    engine = CosyVoiceTTS(mode="sft", spk_id="es_female")
    url, _form = engine.build_form("hola")
    assert url.endswith("/inference_sft")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_cosyvoice_streams_pcm_from_server(prompt_wav):
    """Servidor falso con la misma interfaz que el FastAPI oficial de CosyVoice:
    recibe multipart y devuelve PCM int16 en trozos de tamaño impar."""
    samples = struct.pack("<" + "h" * 2400, *([1000] * 2400))  # 0,1 s a 24 kHz
    received = {}

    async def handler(request: web.Request) -> web.StreamResponse:
        form = await request.post()
        received["text"] = form["tts_text"]
        received["prompt_text"] = form["prompt_text"]
        received["has_wav"] = "prompt_wav" in form
        resp = web.StreamResponse()
        await resp.prepare(request)
        for i in range(0, len(samples), 333):  # trozos impares: fuerza el realineado int16
            await resp.write(samples[i:i + 333])
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_post("/inference_zero_shot", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        engine = CosyVoiceTTS(base_url=f"http://127.0.0.1:{port}", mode="zero_shot",
                              prompt_wav=str(prompt_wav), prompt_text="Hola, soy Sofía.")
        voiced = 0
        async with engine.synthesize("Buenos días, gracias por llamar.") as stream:
            async for ev in stream:
                voiced += sum(1 for s in ev.frame.data if s == 1000)
        await engine.aclose()
    finally:
        await runner.cleanup()

    assert received == {"text": "Buenos días, gracias por llamar.", "prompt_text": "Hola, soy Sofía.", "has_wav": True}
    # Todas las muestras emitidas por el servidor llegan intactas (LiveKit puede
    # añadir un relleno de silencio al final del segmento).
    assert voiced == 2400


async def test_cosyvoice_server_down_raises_api_error(prompt_wav):
    from livekit.agents import APIConnectionError
    from livekit.agents.types import APIConnectOptions

    engine = CosyVoiceTTS(base_url=f"http://127.0.0.1:{_free_port()}", mode="zero_shot",
                          prompt_wav=str(prompt_wav), prompt_text="Hola")
    with pytest.raises(APIConnectionError):
        async with engine.synthesize("hola", conn_options=APIConnectOptions(max_retry=0, timeout=2)) as stream:
            async for _ in stream:
                pass
    await engine.aclose()
