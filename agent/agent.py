"""La Centralita — agente de voz WELUX (Fase 1).

Pipeline:
  - Deepgram Nova-3 (STT streaming; keyterms de dominio; opt-out del programa de
    mejora de modelos de Deepgram -> el audio no se usa para entrenar, RGPD)
  - DeepSeek Chat (LLM, temperatura 0.5, respuestas orales concisas)
  - TTS intercambiable por configuración (agent/tts_factory.py):
    CosyVoice 3 auto-hospedado por defecto, Piper como respaldo, ElevenLabs como upgrade
  - Silero VAD (turn-taking ágil e interrupciones naturales)

Durante la llamada emite eventos al backend (/api/call-event) para que el panel
muestre la transcripción en vivo. Al colgar ejecuta el pipeline post-llamada
(lead -> Sheets, email, DocuSeal, evento anonimizado a n8n).
"""

import asyncio
import json
import logging
import os
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, llm
from livekit.plugins import deepgram, openai, silero

try:
    from prompts import SYSTEM_PROMPT
    from post_call import PostCallProcessor, format_duration, mask_phone
    from tts_factory import build_tts
    from d1_gateway import D1Gateway, D1GatewayError
except ImportError:
    from .prompts import SYSTEM_PROMPT
    from .post_call import PostCallProcessor, format_duration, mask_phone
    from .tts_factory import build_tts
    from .d1_gateway import D1Gateway, D1GatewayError

load_dotenv()
logger = logging.getLogger("centralita")
logging.basicConfig(level=logging.INFO)

APPOINTMENT_REQUESTS_FILE = Path(__file__).resolve().parent.parent / "data" / "appointment_requests.jsonl"


# ==============================================================================
# Herramientas de agenda (tool-calling). Registran SOLICITUDES, no reservas firmes:
# no existe aún integración con un calendario real, así que el agente nunca
# promete una cita confirmada (ver prompts.py §6).
# ==============================================================================

@llm.function_tool(description="Consulta si se puede solicitar una reunión técnica en una fecha. No confirma disponibilidad real.")
async def check_calendar_availability(date: str) -> str:
    """Informa de las franjas en las que el equipo suele atender reuniones."""
    logger.info("[Agenda] Consulta de disponibilidad (fecha solicitada registrada)")
    return (
        f"Para el {date} el equipo atiende reuniones normalmente entre las 9:00 y las 18:00. "
        "Puedo registrar la franja que prefiera el cliente y el equipo la confirmará por teléfono o email."
    )


@llm.function_tool(description="Registra una solicitud de reunión técnica para que el equipo la confirme")
async def book_technical_meeting(
    client_name: str,
    phone: str,
    event_type: str,
    requested_date: str,
    requested_time: str = "",
) -> str:
    """Guarda la solicitud de cita en data/appointment_requests.jsonl (pendiente de confirmación)."""
    request = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "client_name": client_name,
        "phone": phone,
        "event_type": event_type,
        "requested_date": requested_date,
        "requested_time": requested_time,
        "status": "PENDIENTE_CONFIRMACION",
    }

    def _append() -> None:
        APPOINTMENT_REQUESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(APPOINTMENT_REQUESTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(request, ensure_ascii=False) + "\n")

    await asyncio.to_thread(_append)
    d1 = D1Gateway()
    if d1.enabled:
        try:
            await d1.add_appointment({k: v for k, v in request.items() if k not in ("created_at", "status")})
        except D1GatewayError as exc:
            # El fichero local ya la tiene: el equipo no la pierde aunque D1 falle.
            logger.warning("[Agenda] Solicitud %s no guardada en D1: %s", request["id"], exc)
    logger.info("[Agenda] Solicitud %s registrada (tel %s)", request["id"], mask_phone(phone))
    when = f"el {requested_date}" + (f" a las {requested_time}" if requested_time else "")
    return (
        f"Solicitud de reunión registrada para {client_name} {when}. "
        "Queda pendiente de confirmación: el equipo de WELUX contactará al cliente para confirmarla."
    )


class CentralitaAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=(
                "Saluda con calidez y elegancia como Sofía de WELUX en Luxemburgo. "
                "Informa con naturalidad de que la llamada se graba y transcribe para calidad del servicio "
                "y pregunta amablemente en qué puedes asesorarle hoy."
            )
        )


class MonitorEmitter:
    """Envía eventos de la llamada al backend (/api/call-event) para el panel en vivo.

    Best-effort: si el backend no está configurado o no responde, la llamada sigue
    sin interrupciones (solo se registra un aviso).
    """

    def __init__(self, call_id: str, room: str) -> None:
        self.base_url = os.getenv("CENTRALITA_API_URL", "").rstrip("/")
        self.token = os.getenv("CENTRALITA_AUTH_TOKEN", "")
        self.call_id = call_id
        self.room = room
        self.enabled = bool(self.base_url and self.token)
        if not self.enabled:
            logger.warning("CENTRALITA_API_URL / CENTRALITA_AUTH_TOKEN no configurados: panel en vivo desactivado.")

    def _post(self, payload: dict) -> None:
        req = urllib.request.Request(
            f"{self.base_url}/api/call-event",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()

    async def emit(self, event_type: str, **fields) -> None:
        if not self.enabled:
            return
        payload = {
            "type": event_type,
            "call_id": self.call_id,
            "room": self.room,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in fields.items() if v is not None},
        }
        try:
            await asyncio.to_thread(self._post, payload)
        except Exception as exc:
            logger.warning("No se pudo emitir '%s' al panel (%s)", event_type, type(exc).__name__)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    logger.info("Agente conectado a la sala '%s', esperando participante...", ctx.room.name)

    call_id = f"call-{uuid.uuid4().hex[:12]}"
    started_at = time.monotonic()
    transcript_history: list[dict] = []
    monitor = MonitorEmitter(call_id=call_id, room=ctx.room.name)
    background: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        background.add(task)
        task.add_done_callback(background.discard)

    vad_plugin = silero.VAD.load(
        min_speech_duration=0.08,
        min_silence_duration=0.45,
        prefix_padding_duration=0.25,
        activation_threshold=0.5,
    )

    stt_plugin = deepgram.STT(
        model=os.getenv("DEEPGRAM_MODEL", "nova-3"),
        language=os.getenv("DEEPGRAM_LANGUAGE", "es"),
        smart_format=True,
        punctuate=True,
        interim_results=True,
        # Nova-3 usa keyterm prompting (el parámetro `keywords` es solo para Nova-2).
        keyterms=["WELUX", "Luxemburgo", "Kirchberg", "Strassen", "Cloche d'Or"],
        endpointing_ms=250,
        mip_opt_out=True,
    )

    llm_plugin = openai.LLM(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        temperature=0.5,
        max_completion_tokens=150,
    )

    session = AgentSession(
        stt=stt_plugin,
        llm=llm_plugin,
        tts=build_tts(),
        vad=vad_plugin,
        tools=[check_calendar_availability, book_technical_meeting],
        allow_interruptions=True,
        min_interruption_duration=0.2,
        min_interruption_words=1,
        preemptive_generation=True,
        min_endpointing_delay=0.15,
        max_endpointing_delay=0.5,
    )

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if role not in ("user", "assistant") or not text or not text.strip():
            return
        transcript_history.append({"role": role, "text": text.strip()})
        spawn(monitor.emit(
            "transcript_delta",
            role=role,
            text=text.strip(),
            duration=format_duration(time.monotonic() - started_at),
        ))

    await session.start(room=ctx.room, agent=CentralitaAgent())
    spawn(monitor.emit("call_started", status="active", agent="Sofía (IA WELUX)"))

    async def on_shutdown() -> None:
        duration_seconds = time.monotonic() - started_at
        logger.info("Llamada %s terminada (%s): ejecutando pipeline post-llamada.", call_id, format_duration(duration_seconds))
        if background:
            await asyncio.gather(*list(background), return_exceptions=True)

        result = await PostCallProcessor().process_call_ended(
            room_name=ctx.room.name,
            participant_id=call_id,
            duration_seconds=duration_seconds,
            transcript_history=list(transcript_history),
        )
        await monitor.emit(
            "call_ended",
            status="ended",
            duration=format_duration(duration_seconds),
            transcript=result.get("full_transcript"),
            lead=result.get("lead"),
        )

    ctx.add_shutdown_callback(on_shutdown)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
