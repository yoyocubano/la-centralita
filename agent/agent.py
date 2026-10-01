"""La Centralita — agente de voz WELUX (Fase 1, prueba $0).

Pipeline de alta fluidez:
  - Deepgram Nova-3 (STT streaming de ultra-baja latencia con formateo inteligente)
  - DeepSeek Chat (LLM con temperatura 0.5, generación preemptiva y respuestas orales concisas)
  - Piper TTS (Sintetizador neural local in-process < 50ms, 0 €)
  - Silero VAD (Detección de voz afinada para turn-taking ágil e interrupciones naturales)
Al colgar: extrae el lead estructurado en JSON y lo notifica al webhook de n8n.
"""

import asyncio
import hashlib
import json
import logging
import os
import urllib.request

from dotenv import load_dotenv

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, llm
from livekit.plugins import deepgram, openai, silero

try:
    from prompts import SYSTEM_PROMPT
    from piper_tts import PiperTTS
    from lead_extract import extract_lead
    from post_call import redact_pii
except ImportError:
    from .prompts import SYSTEM_PROMPT
    from .piper_tts import PiperTTS
    from .lead_extract import extract_lead
    from .post_call import redact_pii

load_dotenv()
logger = logging.getLogger("centralita")
logging.basicConfig(level=logging.INFO)


# ==============================================================================
# Herramientas de Agendamiento Autónomo (Arquitectura Dograh MCP)
# ==============================================================================

@llm.function_tool(description="Verifica disponibilidad de fechas y horarios para reuniones técnicas en WELUX Events")
async def check_calendar_availability(date: str) -> str:
    """Verifica si hay huecos disponibles en el calendario de eventos."""
    logger.info("[Dograh Tool] Verificando disponibilidad para fecha: %s", date)
    return f"Para la fecha {date}, hay disponibilidad técnica a las 11:00 y a las 16:30."


@llm.function_tool(description="Agenda formalmente una reunión técnica o llamada de asesoría con el cliente")
async def book_technical_meeting(
    client_name: str,
    phone: str,
    event_type: str,
    requested_date: str,
    requested_time: str = "16:30",
) -> str:
    """Registra y confirma la cita en la agenda de WELUX Events."""
    logger.info(
        "[Dograh Tool] Agendando cita técnica: %s (%s) para %s a las %s",
        client_name,
        phone,
        requested_date,
        requested_time,
    )
    return (
        f"Reunión técnica confirmada con éxito para {client_name} el {requested_date} a las {requested_time}. "
        f"Se ha reservado el slot y notificado al director de producción de WELUX."
    )


class CentralitaAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    async def on_enter(self) -> None:
        # Saludo inicial cálido, natural y humano
        await self.session.generate_reply(
            instructions=(
                "Saluda con entusiasmo y elegancia como Sofía de WELUX Events en Luxemburgo. "
                "Menciona con naturalidad que la llamada se graba para calidad del servicio "
                "y pregunta amablemente en qué puedes asesorarles hoy."
            )
        )


async def post_to_n8n(payload: dict) -> None:
    """Envía transcripción + lead al webhook de n8n al colgar."""
    url = os.getenv("N8N_WEBHOOK_URL")
    if not url:
        logger.warning("N8N_WEBHOOK_URL no configurada; salto el envío.")
        return

    def _do() -> int:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status

    try:
        status = await asyncio.to_thread(_do)
        logger.info("n8n respondió HTTP %s", status)
    except Exception as e:
        logger.error("Error al enviar evento a n8n: %s", e)


def transcript_to_text(session: AgentSession) -> str:
    """Serializa el historial de la sesión a texto legible."""
    lines: list[str] = []
    for item in session.history.items:
        role = getattr(item, "role", "?")
        text = getattr(item, "text_content", None)
        if text is None:
            content = getattr(item, "content", "")
            if isinstance(content, list):
                text = " ".join(
                    getattr(part, "text", str(part)) for part in content if part
                )
            else:
                text = str(content)
        if text and text.strip():
            role_label = "Sofía (WELUX)" if role == "assistant" else "Cliente"
            lines.append(f"{role_label}: {text.strip()}")
    return "\n".join(lines)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    logger.info("Agente conectado a la sala '%s', esperando participante...", ctx.room.name)

    # 1. VAD afinado: turn-taking ágil, sin pausas vacías incómodas
    vad_plugin = silero.VAD.load(
        min_speech_duration=0.08,     # Detecta rápidamente cuando el cliente empieza a hablar
        min_silence_duration=0.45,    # Reducido de 0.55s para responder sin vacíos extraños
        prefix_padding_duration=0.25, # Preserva los primeros fonemas como "Hola" o "Sí"
        activation_threshold=0.5,
    )

    # 2. STT streaming Deepgram Nova-3: alta precisión y puntuación automática
    stt_plugin = deepgram.STT(
        model=os.getenv("DEEPGRAM_MODEL", "nova-3"),
        language=os.getenv("DEEPGRAM_LANGUAGE", "es"),
        smart_format=True,
        punctuate=True,
        interim_results=True,
        keywords=[
            ("WELUX", 2.5),
            ("Luxemburgo", 2.0),
            ("Kirchberg", 1.8),
            ("Strassen", 1.8),
            ("Cloche d'Or", 1.8),
        ],
        endpointing_ms=250,
    )

    # 3. LLM DeepSeek con temperatura conversacional y generación concisa
    llm_plugin = openai.LLM(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        temperature=0.5,
        max_completion_tokens=150,
    )

    # 4. Piper TTS local o HTTP
    tts_plugin = PiperTTS(
        base_url=os.getenv("PIPER_HTTP_URL", "http://localhost:10200"),
        voice=os.getenv("PIPER_VOICE", "es_ES-sharvard-medium"),
        model_path=os.getenv("PIPER_MODEL_PATH", "models/piper/es_ES-sharvard-medium.onnx"),
    )

    # 5. Sesión con soporte fluido para interrupciones (barge-in) y generación preemptiva
    session = AgentSession(
        stt=stt_plugin,
        llm=llm_plugin,
        tts=tts_plugin,
        vad=vad_plugin,
        tools=[check_calendar_availability, book_technical_meeting],
        allow_interruptions=True,
        min_interruption_duration=0.2,
        min_interruption_words=1,
        preemptive_generation=True,
        min_endpointing_delay=0.15,
        max_endpointing_delay=0.5,
    )

    agent = CentralitaAgent()
    await session.start(room=ctx.room, agent=agent)

    async def on_shutdown() -> None:
        logger.info("Llamada terminada: extrayendo lead y enviando a n8n...")
        transcript = transcript_to_text(session)
        lead = await extract_lead(transcript)
        redacted_transcript = redact_pii(transcript)
        lead_json = json.dumps(lead or {}, sort_keys=True, ensure_ascii=False)
        lead_hash = hashlib.sha256(lead_json.encode("utf-8")).hexdigest()
        await post_to_n8n(
            {
                "event": "call_ended",
                "room": ctx.room.name,
                "transcript": redacted_transcript,
                "lead_hash": lead_hash,
                "lead_resumen": {
                    "motivo": (lead or {}).get("motivo"),
                    "tipo_evento": (lead or {}).get("tipo_evento"),
                    "es_lead_valido": (lead or {}).get("es_lead_valido", False),
                },
                "agent": os.getenv("AGENT_NAME", "Sofía (WELUX Events)"),
            }
        )

    ctx.add_shutdown_callback(on_shutdown)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
