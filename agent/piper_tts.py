"""Adapter TTS para Piper (open source, local, $0).

Piper expone un servidor HTTP sencillo; este adapter envía el texto y
recibe el audio. Pensado para la prueba a costo cero de la Fase 1.

⚠️  PENDIENTE DE VERIFICACIÓN (Antigravity):
    Adaptar esta clase a la interfaz TTS exacta de la versión instalada
    de `livekit-agents` (ver `livekit.agents.tts.TTS` y `ChunkedStream`
    en el entorno). Si Piper complica el streaming en tiempo real,
    documentar la alternativa elegida en HANDSHAKE.md.
"""

import os


class PiperTTS:
    """TTS basado en Piper local vía HTTP."""

    def __init__(self, base_url: str | None = None, voice: str | None = None):
        self.base_url = (base_url or os.getenv("PIPER_HTTP_URL",
                                               "http://localhost:10200")).rstrip("/")
        self.voice = voice or os.getenv("PIPER_VOICE", "es_ES-davefx-medium")

    def synthesize_url(self, text: str) -> str:
        """URL del servidor Piper para sintetizar `text` (verificar formato)."""
        from urllib.parse import quote

        # Formato típico del servidor HTTP de Piper; confirmar en el entorno.
        return f"{self.base_url}/?text={quote(text)}&voice={quote(self.voice)}"

    # --- Integración con LiveKit Agents -----------------------------------
    # TODO(Antigravity): implementar `synthesize()` devolviendo el
    # `ChunkedStream` que espera `livekit.agents.tts.TTS` en la versión
    # instalada, haciendo streaming del WAV que devuelve synthesize_url().
    # Hasta entonces, este adapter NO está funcional: el agente no hablará.
