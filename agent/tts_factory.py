"""Selección de proveedor TTS por configuración (sin proveedor hardcodeado).

    TTS_PROVIDER=cosyvoice    # defecto: CosyVoice 3 auto-hospedado, $0 por llamada
    TTS_PROVIDER=piper        # Piper ONNX local, $0, menor calidad, sin GPU
    TTS_PROVIDER=elevenlabs   # upgrade de pago (requiere ELEVENLABS_API_KEY y
                              #   `pip install livekit-plugins-elevenlabs`)

    TTS_FALLBACK_PROVIDER=piper   # opcional; "none" para desactivar

Con fallback configurado se usa `tts.FallbackAdapter`: si el proveedor principal
falla (servidor caído, timeout), la llamada sigue con el de respaldo en vez de
quedarse en silencio.
"""

import logging
import os
from typing import Callable, Dict

from livekit.agents import tts

logger = logging.getLogger("centralita.tts")

SUPPORTED_PROVIDERS = ("cosyvoice", "piper", "elevenlabs")


def _build_cosyvoice() -> tts.TTS:
    try:
        from .cosyvoice_tts import CosyVoiceTTS
    except ImportError:
        from cosyvoice_tts import CosyVoiceTTS
    return CosyVoiceTTS()


def _build_piper() -> tts.TTS:
    try:
        from .piper_tts import PiperTTS
    except ImportError:
        from piper_tts import PiperTTS
    return PiperTTS(
        base_url=os.getenv("PIPER_HTTP_URL", ""),
        voice=os.getenv("PIPER_VOICE", "es_ES-sharvard-medium"),
        model_path=os.getenv("PIPER_MODEL_PATH") or None,
    )


def _build_elevenlabs() -> tts.TTS:
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
    if not api_key or not voice_id:
        raise ValueError("ElevenLabs requiere ELEVENLABS_API_KEY y ELEVENLABS_VOICE_ID.")
    try:
        from livekit.plugins import elevenlabs
    except ImportError as exc:
        raise ValueError("Instala `livekit-plugins-elevenlabs` para usar TTS_PROVIDER=elevenlabs.") from exc
    return elevenlabs.TTS(
        api_key=api_key,
        voice_id=voice_id,
        model=os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
        language=os.getenv("ELEVENLABS_LANGUAGE", "es"),
    )


BUILDERS: Dict[str, Callable[[], tts.TTS]] = {
    "cosyvoice": _build_cosyvoice,
    "piper": _build_piper,
    "elevenlabs": _build_elevenlabs,
}


def build_single_tts(provider: str) -> tts.TTS:
    provider = (provider or "").strip().lower()
    if provider not in BUILDERS:
        raise ValueError(f"TTS_PROVIDER desconocido '{provider}'. Opciones: {', '.join(SUPPORTED_PROVIDERS)}")
    return BUILDERS[provider]()


def build_tts(provider: str | None = None, fallback: str | None = None) -> tts.TTS:
    """Construye el TTS configurado (con respaldo opcional)."""
    provider = (provider or os.getenv("TTS_PROVIDER", "cosyvoice")).strip().lower()
    fallback = (fallback if fallback is not None else os.getenv("TTS_FALLBACK_PROVIDER", "piper")).strip().lower()

    try:
        primary = build_single_tts(provider)
    except ValueError as exc:
        if fallback in ("", "none") or fallback == provider:
            raise
        logger.error("TTS principal '%s' no disponible (%s); usando '%s'.", provider, exc, fallback)
        return build_single_tts(fallback)

    if fallback in ("", "none") or fallback == provider:
        logger.info("TTS activo: %s (sin respaldo)", provider)
        return primary

    try:
        backup = build_single_tts(fallback)
    except ValueError as exc:
        logger.warning("TTS de respaldo '%s' no disponible (%s); se usa solo '%s'.", fallback, exc, provider)
        return primary

    logger.info("TTS activo: %s (respaldo: %s)", provider, fallback)
    # FallbackAdapter remuestrea al sample rate del primero si difieren.
    return tts.FallbackAdapter([primary, backup])
