"""Pipeline post-llamada: extracción del lead y sincronización (Sheets, email, DocuSeal, n8n).

Reparto de responsabilidades (sin duplicados):
- Google Sheets ("Leads")   -> escrito SOLO por este backend (registro maestro, con PII).
- Email al dueño            -> enviado SOLO por este backend.
- n8n                       -> recibe un evento ANONIMIZADO (sin nombre, teléfono, email
                               ni transcripción en claro) para automatizaciones/log.
"""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

import aiohttp
from openai import AsyncOpenAI

try:
    from .config import Config
    from .prompts import LEAD_EXTRACTION_PROMPT
    from .sheets_sync import GoogleSheetsSync
    from .email_notify import EmailNotifier
    from .docuseal_client import DocuSealClient
except ImportError:
    from config import Config
    from prompts import LEAD_EXTRACTION_PROMPT
    from sheets_sync import GoogleSheetsSync
    from email_notify import EmailNotifier
    from docuseal_client import DocuSealClient

logger = logging.getLogger("la-centralita.post_call")

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Candidato a teléfono: empieza por +, 00 o dígito; dígitos con separadores habituales.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w+])(?:\+|00)?\d[\d\s().-]{4,}\d(?!\w)")
# Fechas que NO deben confundirse con teléfonos (2026-10-22, 18.11.2026, 18/11/26).
_DATE_RE = re.compile(r"^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})$")


def _redact_phone(match: re.Match) -> str:
    candidate = match.group(0)
    digits = re.sub(r"\D", "", candidate)
    if _DATE_RE.match(candidate.strip()):
        return candidate
    if 6 <= len(digits) <= 15:
        return "[PHONE_REDACTED]"
    return candidate


def redact_pii(text: str, known_names: list[str] | None = None) -> str:
    """Anonimiza teléfonos, emails y nombres conocidos en texto libre (RGPD / Luxemburgo).

    `known_names`: nombres ya identificados (p. ej. el del lead extraído); se
    enmascara el nombre completo y cada palabra de 3+ letras que lo compone.
    """
    if not text:
        return ""
    redacted = _EMAIL_RE.sub("[EMAIL_REDACTED]", str(text))
    redacted = _PHONE_CANDIDATE_RE.sub(_redact_phone, redacted)
    terms: set[str] = set()
    for name in known_names or []:
        name = (name or "").strip()
        if len(name) >= 3:
            terms.add(name)
            terms.update(part for part in re.split(r"[\s,.'-]+", name) if len(part) >= 3)
    for term in sorted(terms, key=len, reverse=True):
        redacted = re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", "[NAME_REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted


def mask_phone(phone: str | None) -> str:
    """Enmascara un teléfono para logs: deja solo los 2 últimos dígitos."""
    digits = re.sub(r"\D", "", phone or "")
    return f"***{digits[-2:]}" if len(digits) >= 4 else "***"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


EMPTY_LEAD = {
    "nombre": None,
    "telefono": None,
    "email": None,
    "motivo": None,
    "tipo_evento": None,
    "fecha_evento": None,
    "numero_invitados": None,
    "detalles": None,
    "es_lead_valido": False,
}


class PostCallProcessor:
    """Procesador post-llamada para estructurar el lead y sincronizar con n8n, Sheets, Email y DocuSeal."""

    def __init__(self):
        self.webhook_url = Config.N8N_WEBHOOK_URL
        self.webhook_secret = Config.N8N_WEBHOOK_SECRET
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
        """Extrae el lead estructurado usando DeepSeek; sin LLM marca revisión manual."""
        if not transcript_text.strip():
            return {**EMPTY_LEAD, "motivo": "Llamada vacía o sin audio", "detalles": "No se registró conversación."}

        if self.openai_client:
            try:
                response = await self.openai_client.chat.completions.create(
                    model=Config.DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": LEAD_EXTRACTION_PROMPT.replace("{transcript}", "")},
                        {"role": "user", "content": transcript_text},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=500,
                )
                lead_data = json.loads(response.choices[0].message.content.strip())
                if not isinstance(lead_data, dict):
                    raise ValueError("La respuesta del LLM no es un objeto JSON")
                logger.info("Lead extraído con DeepSeek (válido=%s)", lead_data.get("es_lead_valido"))
                return {**EMPTY_LEAD, **lead_data}
            except Exception as e:
                logger.error("Error extrayendo lead con DeepSeek (%s). Requiere revisión manual.", type(e).__name__)

        return {
            **EMPTY_LEAD,
            "motivo": "Consulta recibida (extracción manual pendiente)",
            "detalles": "Extracción automática no disponible; revisar la transcripción completa.",
            "es_lead_valido": len(transcript_text) > 50,
            "requiere_revision_manual": True,
        }

    async def send_to_n8n(self, payload: dict) -> bool:
        """Envía el evento anonimizado a n8n (con secreto compartido en cabecera)."""
        if not self.webhook_url:
            logger.warning("N8N_WEBHOOK_URL no está configurada. Omitiendo despacho.")
            return False

        headers = {"Content-Type": "application/json"}
        if self.webhook_secret:
            headers["X-Centralita-Secret"] = self.webhook_secret
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if 200 <= resp.status < 300:
                        logger.info("Evento enviado a n8n (HTTP %s)", resp.status)
                        return True
                    logger.warning("n8n respondió HTTP %s", resp.status)
                    return False
        except Exception as e:
            logger.error("Fallo al conectar con webhook de n8n (%s)", type(e).__name__)
            return False

    @staticmethod
    def format_transcript(transcript_history: list[dict]) -> str:
        lines = []
        for item in transcript_history:
            role = "Cliente" if item.get("role") in ("user", "participant", "customer") else "Sofía (WELUX)"
            content = str(item.get("text", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def process_call_ended(
        self,
        room_name: str,
        participant_id: str,
        duration_seconds: float,
        transcript_history: list[dict],
        metrics: dict | None = None,
        simulated: bool = False,
    ) -> dict:
        """
        Punto de entrada principal al colgar la llamada:
        1. Formatea la transcripción y extrae el lead.
        2. (Solo llamadas reales) DocuSeal, Google Sheets y email al dueño.
        3. Envía el evento anonimizado a n8n.

        `simulated=True` (diagnóstico) NO escribe en el Sheet de producción ni envía
        email ni crea contratos: así ningún dato de prueba contamina los leads reales.
        """
        full_transcript = self.format_transcript(transcript_history)
        lead = await self.extract_lead_from_transcript(full_transcript)

        # Coste aproximado: Deepgram Nova-3 ~$0.0043/min + LLM ~$0.00015 por llamada.
        duration_min = duration_seconds / 60.0
        total_cost_usd = round(duration_min * 0.0043 + 0.00015, 6)
        lux_timestamp = self.sheets_sync.get_luxembourg_now()

        lead_json = json.dumps(lead, sort_keys=True, ensure_ascii=False)
        lead_hash = hashlib.sha256(lead_json.encode("utf-8")).hexdigest()
        known_names = [lead.get("nombre") or ""]
        redacted_transcript = redact_pii(full_transcript, known_names)

        lead_data = {
            "nombre": lead.get("nombre"),
            "telefono": lead.get("telefono"),
            "email": lead.get("email"),
            "empresa": lead.get("empresa") or "Empresa / Particular",
            "motivo": lead.get("motivo") or "Consulta general",
            "detalles": lead.get("detalles") or full_transcript[:300],
            "fecha_evento": lead.get("fecha_evento") or "A convenir",
            "tipo_evento": lead.get("tipo_evento"),
            "valor_eur": "Por cotizar",
            "timestamp_lux": lux_timestamp,
            "docuseal_status": "BORRADOR",
            "simulated": simulated,
        }

        docuseal_res = None
        sheets_res = {"status": "SKIPPED_SIMULATION"} if simulated else None
        email_res = {"status": "SKIPPED_SIMULATION"} if simulated else None

        if not simulated:
            if lead.get("es_lead_valido") and lead.get("nombre"):
                docuseal_res = await self.docuseal_client.create_contract_submission(
                    client_name=lead.get("nombre") or "Cliente",
                    client_email=lead.get("email") or "",
                    client_phone=lead.get("telefono") or "",
                    event_interest=lead.get("motivo") or "Servicios WELUX",
                    event_date=lead.get("fecha_evento") or "A definir",
                )
                lead_data["docuseal_status"] = docuseal_res.get("status", "BORRADOR")
                lead_data["docuseal_url"] = docuseal_res.get("sign_url")

            try:
                sheets_res = await self.sheets_sync.sync_lead(lead_data)
            except Exception as e:
                logger.error("Error al sincronizar con Google Sheets (%s)", type(e).__name__)
                sheets_res = {"status": "ERROR"}

            try:
                email_res = await self.email_notifier.send_post_call_notification({
                    "lead": lead_data,
                    "transcripcion": full_transcript,
                    "duration_seconds": duration_seconds,
                    "duration_formatted": format_duration(duration_seconds),
                    "timestamp_lux": lux_timestamp,
                })
            except Exception as e:
                logger.error("Error al enviar notificación por email (%s)", type(e).__name__)
                email_res = {"status": "ERROR"}

        # Payload anonimizado para n8n: sin nombre/teléfono/email; texto libre redactado.
        n8n_payload = {
            "event": "call_ended",
            "simulated": simulated,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timestamp_lux": lux_timestamp,
            "room_name": room_name,
            "duration_seconds": round(duration_seconds, 2),
            "lead_id": (sheets_res or {}).get("lead_id") or lead_data.get("id"),
            "lead_hash": lead_hash,
            "transcripcion": redacted_transcript,
            "lead_resumen": {
                "motivo": redact_pii(lead.get("motivo") or "", known_names),
                "tipo_evento": redact_pii(lead.get("tipo_evento") or "", known_names),
                "fecha_evento": lead.get("fecha_evento"),
                "es_lead_valido": bool(lead.get("es_lead_valido", False)),
            },
            "docuseal_status": lead_data["docuseal_status"],
            "metricas": {
                "duracion_minutos": round(duration_min, 2),
                "total_turnos": len(transcript_history),
                "costo_estimado_llamada_usd": total_cost_usd,
                **(metrics or {}),
            },
        }

        n8n_ok = await self.send_to_n8n(n8n_payload)

        internal_result = dict(n8n_payload)
        internal_result["participant_id"] = participant_id
        internal_result["lead"] = lead_data
        internal_result["sheets_sync"] = {k: v for k, v in (sheets_res or {}).items() if k != "row"}
        internal_result["email_notify"] = email_res
        internal_result["n8n_delivered"] = n8n_ok
        internal_result["full_transcript"] = full_transcript
        return internal_result
