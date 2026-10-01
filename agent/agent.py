"""La Centralita — agente de voz WELUX (Fase 1, prueba $0).

Pipeline: Deepgram Nova-3 (STT) -> DeepSeek (LLM) -> Piper (TTS).
Al colgar: extrae el lead de la transcripción y lo envía a n8n.

Uso:
    cp .env.example .env   # rellenar claves (nunca subir el .env)
    pip install -r requirements.txt
    python agent.py dev
"""

import asyncio
import json
import logging
import os
import urllib.request

from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import deepgram, openai, silero

try:
    from prompts import SYSTEM_PROMPT
    from piper_tts import PiperTTS
    from lead_extract import extract_lead
except ImportError:
    from .prompts import SYSTEM_PROMPT
    from .piper_tts import PiperTTS
    from .lead_extract import extract_lead

load_dotenv()
logger = logging.getLogger("centralita")
logging.basicConfig(level=logging.INFO)


class CentralitaAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions=(
                "Saluda brevemente como la recepcionista de voz de WELUX, "
                "avisa de que la llamada será grabada y pregunta en qué puedes ayudar."
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

    status = await asyncio.to_thread(_do)
    logger.info("n8n respondió HTTP %s", status)


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
            lines.append(f"{role}: {text.strip()}")
    return "\n".join(lines)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    logger.info("Agente conectado a la sala, esperando participante...")

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="es"),
        llm=openai.LLM(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
        ),
        tts=PiperTTS(
            base_url=os.getenv("PIPER_HTTP_URL", "http://localhost:10200"),
            voice=os.getenv("PIPER_VOICE", "es_ES-davefx-medium"),
        ),
        vad=silero.VAD.load(),
    )

    agent = CentralitaAgent()
    await session.start(room=ctx.room, agent=agent)

    async def on_shutdown() -> None:
        logger.info("Llamada terminada: extrayendo lead y enviando a n8n...")
        transcript = transcript_to_text(session)
        lead = await extract_lead(transcript)
        await post_to_n8n(
            {
                "transcript": transcript,
                "lead": lead,
                "agent": os.getenv("AGENT_NAME", "Centralita WELUX"),
            }
        )
        logger.info("Lead enviado: %s", json.dumps(lead, ensure_ascii=False))

    ctx.add_shutdown_callback(on_shutdown)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
