"""Adapter TTS para CosyVoice 3 auto-hospedado (open source, $0 por llamada).

Habla con el servidor FastAPI oficial de CosyVoice
(`runtime/python/fastapi/server.py` del repo FunAudioLLM/CosyVoice), que expone:

  POST /inference_sft          tts_text, spk_id                       (modelos con speakers SFT)
  POST /inference_zero_shot    tts_text, prompt_text, prompt_wav      (clonación de voz)
  POST /inference_cross_lingual tts_text, prompt_wav
  POST /inference_instruct2    tts_text, instruct_text, prompt_wav    (estilo por instrucción)

y responde con PCM int16 mono en streaming (24 kHz en CosyVoice 2/3).
El audio se empuja al AudioEmitter según llega (baja latencia al primer byte).

Variables de entorno:
  COSYVOICE_URL            http://localhost:50000
  COSYVOICE_MODE           zero_shot | sft | cross_lingual | instruct2   (defecto: zero_shot)
  COSYVOICE_SAMPLE_RATE    24000
  COSYVOICE_SPK_ID         (solo modo sft)
  COSYVOICE_PROMPT_WAV     ruta a la muestra de voz de referencia (modos con prompt)
  COSYVOICE_PROMPT_TEXT    transcripción exacta de la muestra (modo zero_shot)
  COSYVOICE_INSTRUCT_TEXT  instrucción de estilo (modo instruct2)
"""

import logging
import os
from pathlib import Path

import aiohttp

from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger("centralita.cosyvoice")

NUM_CHANNELS = 1
VALID_MODES = ("zero_shot", "sft", "cross_lingual", "instruct2")


class CosyVoiceTTS(tts.TTS):
    """TTS contra un servidor CosyVoice 3 propio, compatible con LiveKit Agents."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        mode: str | None = None,
        sample_rate: int | None = None,
        spk_id: str | None = None,
        prompt_wav: str | None = None,
        prompt_text: str | None = None,
        instruct_text: str | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        sample_rate = int(sample_rate or os.getenv("COSYVOICE_SAMPLE_RATE", "24000"))
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self.base_url = (base_url or os.getenv("COSYVOICE_URL", "http://localhost:50000")).rstrip("/")
        self.mode = (mode or os.getenv("COSYVOICE_MODE", "zero_shot")).strip().lower()
        if self.mode not in VALID_MODES:
            raise ValueError(f"COSYVOICE_MODE inválido '{self.mode}'. Opciones: {', '.join(VALID_MODES)}")
        self.spk_id = spk_id or os.getenv("COSYVOICE_SPK_ID", "")
        self.prompt_text = prompt_text or os.getenv("COSYVOICE_PROMPT_TEXT", "")
        self.instruct_text = instruct_text or os.getenv("COSYVOICE_INSTRUCT_TEXT", "")
        self.timeout_s = timeout_s

        wav_path = prompt_wav or os.getenv("COSYVOICE_PROMPT_WAV", "")
        self._prompt_wav_bytes: bytes | None = None
        if self.mode in ("zero_shot", "cross_lingual", "instruct2"):
            if not wav_path or not Path(wav_path).is_file():
                raise ValueError(f"COSYVOICE_PROMPT_WAV es obligatorio en modo '{self.mode}' (archivo no encontrado).")
            self._prompt_wav_bytes = Path(wav_path).read_bytes()
        if self.mode == "sft" and not self.spk_id:
            raise ValueError("COSYVOICE_SPK_ID es obligatorio en modo 'sft'.")
        if self.mode == "zero_shot" and not self.prompt_text:
            raise ValueError("COSYVOICE_PROMPT_TEXT es obligatorio en modo 'zero_shot'.")

        self._session: aiohttp.ClientSession | None = None

    @property
    def model(self) -> str:
        return f"cosyvoice-{self.mode}"

    @property
    def provider(self) -> str:
        return "cosyvoice-selfhosted"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def build_form(self, text: str) -> tuple[str, aiohttp.FormData]:
        """Construye endpoint y formulario multipart según el modo configurado."""
        form = aiohttp.FormData()
        form.add_field("tts_text", text)
        if self.mode == "sft":
            form.add_field("spk_id", self.spk_id)
        if self.mode == "zero_shot":
            form.add_field("prompt_text", self.prompt_text)
        if self.mode == "instruct2":
            form.add_field("instruct_text", self.instruct_text)
        if self._prompt_wav_bytes is not None:
            form.add_field("prompt_wav", self._prompt_wav_bytes, filename="prompt.wav", content_type="audio/wav")
        return f"{self.base_url}/inference_{self.mode}", form

    def synthesize(self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        return CosyVoiceChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


class CosyVoiceChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: CosyVoiceTTS, input_text: str, conn_options) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: CosyVoiceTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        text = self.input_text.strip()
        if not text:
            return
        url, form = self._tts.build_form(text)
        try:
            async with self._tts._ensure_session().post(
                url, data=form, timeout=aiohttp.ClientTimeout(total=self._tts.timeout_s)
            ) as resp:
                if resp.status != 200:
                    raise APIStatusError(f"CosyVoice HTTP {resp.status}", status_code=resp.status, body=None)
                output_emitter.initialize(
                    request_id=utils.shortuuid(),
                    sample_rate=self._tts.sample_rate,
                    num_channels=NUM_CHANNELS,
                    mime_type="audio/pcm",
                )
                remainder = b""
                async for chunk in resp.content.iter_chunked(4096):
                    data = remainder + chunk
                    cut = len(data) - (len(data) % 2)  # alinear a muestras int16
                    if cut:
                        output_emitter.push(data[:cut])
                    remainder = data[cut:]
                output_emitter.flush()
        except (APIStatusError, APIConnectionError):
            raise
        except TimeoutError as e:
            raise APITimeoutError() from e
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"CosyVoice no disponible en {self._tts.base_url}") from e
