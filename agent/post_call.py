import json
import logging
import time
from datetime import datetime, timezone
import aiohttp
from openai import AsyncOpenAI

from .config import Config
from .prompts import LEAD_EXTRACTION_PROMPT

logger = logging.getLogger("la-centralita.post_call")


class PostCallProcessor:
    """Procesador post-llamada para estructurar el lead y notificar a n8n."""

    def __init__(self):
        self.webhook_url = Config.N8N_WEBHOOK_URL
        self.openai_client = None
        if Config.DEEPSEEK_API_KEY:
            self.openai_client = AsyncOpenAI(
                api_key=Config.DEEPSEEK_API_KEY,
                base_url=Config.DEEPSEEK_BASE_URL,
            )

    async def extract_lead_from_transcript(self, transcript_text: str) -> dict:
        """Extrae el lead estructurado usando DeepSeek o fallback de emergencia."""
        if not transcript_text.strip():
            return {
                "nombre": None,
                "telefono": None,
                "email": None,
                "motivo": "Llamada vacía o sin audio",
                "tipo_evento": None,
                "fecha_evento": None,
                "numero_invitados": None,
                "detalles": "No se registró conversación.",
                "es_lead_valido": False,
            }

        if self.openai_client:
            try:
                response = await self.openai_client.chat.completions.create(
                    model=Config.DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": LEAD_EXTRACTION_PROMPT},
                        {"role": "user", "content": transcript_text},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=500,
                )
                raw_json = response.choices[0].message.content.strip()
                lead_data = json.loads(raw_json)
                logger.info(f"Lead extraído exitosamente con DeepSeek: {lead_data.get('nombre')}")
                return lead_data
            except Exception as e:
                logger.error(f"Error extrayendo lead con DeepSeek: {e}. Usando fallback.")

        # Fallback básico si DeepSeek API no estuviera disponible
        return {
            "nombre": None,
            "telefono": None,
            "email": None,
            "motivo": "Consulta recibida (extracción manual pendiente)",
            "tipo_evento": None,
            "fecha_evento": None,
            "numero_invitados": None,
            "detalles": f"Transcripción disponible para revisión manual: {transcript_text[:150]}...",
            "es_lead_valido": len(transcript_text) > 50,
        }

    async def send_to_n8n(self, payload: dict) -> bool:
        """Envía el webhook a n8n."""
        if not self.webhook_url:
            logger.warning("N8N_WEBHOOK_URL no está configurada. Omitiendo despacho.")
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    status = resp.status
                    body = await resp.text()
                    if status in (200, 201):
                        logger.info(f"Webhook enviado exitosamente a n8n (Status {status}): {body[:100]}")
                        return True
                    else:
                        logger.warning(f"n8n respondió con error status {status}: {body[:200]}")
                        return False
        except Exception as e:
            logger.error(f"Fallo al conectar con webhook de n8n: {e}")
            return False

    async def process_call_ended(
        self,
        room_name: str,
        participant_id: str,
        duration_seconds: float,
        transcript_history: list[dict],
        metrics: dict | None = None,
    ) -> dict:
        """
        Punto de entrada principal al colgar la llamada:
        1. Formatea la transcripción.
        2. Extrae el lead.
        3. Envía el evento completo a n8n.
        """
        # Formatear la transcripción legible
        formatted_lines = []
        for item in transcript_history:
            role = "Cliente" if item.get("role") in ("user", "participant") else "Sofía (WELUX)"
            content = item.get("text", "").strip()
            if content:
                formatted_lines.append(f"{role}: {content}")
        full_transcript = "\n".join(formatted_lines)

        # Extraer lead
        lead = await self.extract_lead_from_transcript(full_transcript)

        # Métricas y costos aproximados de la llamada (Fase 1: $0.00)
        # Deepgram Nova-3: $0.0043/min (cubierto por créditos de $200)
        # DeepSeek V3: ~$0.14 por millón de tokens (prácticamente $0.0001)
        # Piper TTS: $0.00 (local)
        # LiveKit Cloud: $0.00 (Build plan 1000 min gratis)
        duration_min = duration_seconds / 60.0
        cost_deepgram = duration_min * 0.0043
        cost_deepseek = 0.00015
        total_cost_usd = round(cost_deepgram + cost_deepseek, 6)

        payload = {
            "event": "call_ended",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "room_name": room_name,
            "participant_id": participant_id,
            "duration_seconds": round(duration_seconds, 2),
            "lead": lead,
            "transcripcion": full_transcript,
            "metricas": {
                "duracion_minutos": round(duration_min, 2),
                "total_turnos": len(transcript_history),
                "costo_estimado_llamada_usd": total_cost_usd,
                **(metrics or {}),
            },
        }

        # Despachar a n8n
        await self.send_to_n8n(payload)
        return payload
