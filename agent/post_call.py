import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
import aiohttp
from openai import AsyncOpenAI

from .config import Config
from .prompts import LEAD_EXTRACTION_PROMPT

try:
    from .sheets_sync import GoogleSheetsSync
    from .email_notify import EmailNotifier
    from .docuseal_client import DocuSealClient
except ImportError:
    from sheets_sync import GoogleSheetsSync
    from email_notify import EmailNotifier
    from docuseal_client import DocuSealClient

logger = logging.getLogger("la-centralita.post_call")


def redact_pii(text: str) -> str:
    """Anonimiza números de teléfono y direcciones de correo electrónico en transcripciones (RGPD / Luxemburgo)."""
    if not text:
        return ""
    # Redactar emails
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b"
    redacted = re.sub(email_pattern, "[EMAIL_REDACTED]", text)

    # Redactar teléfonos (formato Luxemburgo +352, internacional o números locales de 6-12 dígitos)
    phone_pattern = r"(?:\+352[\s.-]?)?(?:6\d{2}[\s.-]?\d{3}[\s.-]?\d{3}|\b\d{3}[\s.-]?\d{3}[\s.-]?\d{3,4}\b|\b(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b)"
    redacted = re.sub(phone_pattern, "[PHONE_REDACTED]", redacted)
    return redacted


class PostCallProcessor:
    """Procesador post-llamada para estructurar el lead y sincronizar con n8n, Sheets, Email y DocuSeal."""

    def __init__(self):
        self.webhook_url = Config.N8N_WEBHOOK_URL
        self.openai_client = None
        self.sheets_sync = GoogleSheetsSync()
        self.email_notifier = EmailNotifier()
        self.docuseal_client = DocuSealClient()
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
        lux_timestamp = self.sheets_sync.get_luxembourg_now()

        # Mitigación H-005 (RGPD Luxemburgo): calcular hash del lead y anonimizar transcripción
        lead_json = json.dumps(lead or {}, sort_keys=True, ensure_ascii=False)
        lead_hash = hashlib.sha256(lead_json.encode("utf-8")).hexdigest()
        redacted_transcript = redact_pii(full_transcript)

        # Preparar datos unificados del lead para Sheets y CRM
        lead_data = {
            "nombre": (lead or {}).get("nombre"),
            "telefono": (lead or {}).get("telefono"),
            "email": (lead or {}).get("email"),
            "empresa": (lead or {}).get("empresa") or "Empresa / Particular",
            "motivo": (lead or {}).get("motivo") or "Consulta general",
            "detalles": (lead or {}).get("detalles") or full_transcript[:300],
            "fecha_evento": (lead or {}).get("fecha_evento") or "A convenir",
            "tipo_evento": (lead or {}).get("tipo_evento"),
            "valor_eur": "2.500 €",
            "timestamp_lux": lux_timestamp,
            "docuseal_status": "BORRADOR",
        }

        # Generar contrato DocuSeal si hay un lead con datos mínimos
        docuseal_res = None
        if (lead or {}).get("es_lead_valido") and (lead or {}).get("nombre"):
            try:
                docuseal_res = await self.docuseal_client.create_contract_submission(
                    client_name=lead.get("nombre", "Cliente"),
                    client_email=lead.get("email") or "",
                    client_phone=lead.get("telefono") or "",
                    event_interest=lead.get("motivo") or "Servicios WELUX",
                    amount_eur=2500.0,
                    event_date=lead.get("fecha_evento") or "A definir",
                )
                lead_data["docuseal_status"] = docuseal_res.get("status", "ENVIADO")
                lead_data["docuseal_url"] = docuseal_res.get("sign_url")
            except Exception as e:
                logger.warning(f"Error generando contrato DocuSeal: {e}")

        # 1. Sincronización idempotente con Google Sheets ("La Centralita — Leads")
        sheets_res = None
        try:
            sheets_res = await self.sheets_sync.sync_lead(lead_data)
        except Exception as e:
            logger.error(f"Error al sincronizar con Google Sheets: {e}")

        # 2. Notificación post-llamada inmediata a info@weluxevents.com
        email_res = None
        try:
            email_info = {
                "lead": lead_data,
                "transcripcion": full_transcript,
                "duration_seconds": duration_seconds,
                "timestamp_lux": lux_timestamp,
                "docuseal": docuseal_res,
            }
            email_res = await self.email_notifier.send_post_call_notification(email_info)
        except Exception as e:
            logger.error(f"Error al enviar notificación por email: {e}")

        # Payload seguro sin PII en texto plano para el webhook externo n8n
        n8n_payload = {
            "event": "call_ended",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timestamp_lux": lux_timestamp,
            "room_name": room_name,
            "participant_id": participant_id,
            "duration_seconds": round(duration_seconds, 2),
            "lead_hash": lead_hash,
            "transcripcion": redacted_transcript,
            "lead_resumen": {
                "motivo": (lead or {}).get("motivo"),
                "tipo_evento": (lead or {}).get("tipo_evento"),
                "fecha_evento": (lead or {}).get("fecha_evento"),
                "es_lead_valido": (lead or {}).get("es_lead_valido", False),
            },
            "docuseal": docuseal_res,
            "metricas": {
                "duracion_minutos": round(duration_min, 2),
                "total_turnos": len(transcript_history),
                "costo_estimado_llamada_usd": total_cost_usd,
                **(metrics or {}),
            },
        }

        # 3. Despachar a n8n
        await self.send_to_n8n(n8n_payload)

        # Devolver payload enriquecido para uso interno del servidor
        internal_result = dict(n8n_payload)
        internal_result["lead"] = lead_data
        internal_result["lead_raw"] = lead
        internal_result["sheets_sync"] = sheets_res
        internal_result["email_notify"] = email_res
        return internal_result

