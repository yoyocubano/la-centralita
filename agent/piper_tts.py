"""Adapter TTS para Piper (open source, local, $0).

Soporta dos modos:
1. In-process (por defecto si el modelo ONNX está presente): Carga el modelo
   ONNX localmente con piper-tts (cero servicios externos, cero puertos, latencia ultra baja <50ms).
2. Servidor HTTP: Si se define PIPER_HTTP_URL o se ejecuta un servidor externo.
"""

import asyncio
import io
import logging
import os
from pathlib import Path
import urllib.parse
import urllib.request

from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger("centralita.piper")

SAMPLE_RATE = 22050
NUM_CHANNELS = 1


class PiperTTS(tts.TTS):
    """TTS basado en Piper local u HTTP, compatible con LiveKit Agents SDK."""

    def __init__(
        self,
        base_url: str | None = None,
        voice: str | None = None,
        model_path: str | None = None,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self.base_url = (base_url or os.getenv("PIPER_HTTP_URL", "")).rstrip("/")
        self.voice_name = voice or os.getenv("PIPER_VOICE", "es_ES-sharvard-medium")

        # Ruta del modelo ONNX local
        self.model_path = model_path or os.getenv(
            "PIPER_MODEL_PATH",
            str(Path(__file__).resolve().parent.parent / "models" / "piper" / f"{self.voice_name}.onnx"),
        )

        self._local_voice = None
        # Intentar cargar modelo local in-process si el archivo ONNX existe
        if Path(self.model_path).exists():
            try:
                from piper.voice import PiperVoice

                logger.info("Cargando modelo Piper local ONNX desde %s...", self.model_path)
                self._local_voice = PiperVoice.load(str(self.model_path))
                logger.info("Modelo Piper cargado exitosamente.")
            except Exception as e:
                logger.warning("No se pudo cargar Piper local: %s. Se usará modo HTTP si está disponible.", e)

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        return PiperChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class PiperChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: PiperTTS, input_text: str, conn_options):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: PiperTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        text = self.input_text.strip()
        if not text:
            return

        try:
            if self._tts._local_voice is not None:
                # Síntesis local in-process ultra rápida
                def _synth():
                    chunks = list(self._tts._local_voice.synthesize(text))
                    return b"".join(c.audio_int16_bytes for c in chunks)

                audio_bytes = await asyncio.to_thread(_synth)
            else:
                # Síntesis vía HTTP
                base_url = self._tts.base_url or "http://localhost:10200"
                encoded = urllib.parse.quote(text)
                voice_param = urllib.parse.quote(self._tts.voice_name)
                url = f"{base_url}/?text={encoded}&voice={voice_param}"

                def _fetch():
                    with urllib.request.urlopen(url, timeout=10) as resp:
                        return resp.read()

                wav_bytes = await asyncio.to_thread(_fetch)
                import soundfile as sf

                audio_int16, _ = sf.read(io.BytesIO(wav_bytes), dtype="int16")
                audio_bytes = audio_int16.tobytes()

            output_emitter.initialize(
                request_id=utils.shortuuid(),
                sample_rate=SAMPLE_RATE,
                num_channels=NUM_CHANNELS,
                mime_type="audio/pcm",
            )
            output_emitter.push(audio_bytes)
            output_emitter.flush()
        except Exception as e:
            logger.error("Error en síntesis Piper: %s", e, exc_info=True)
