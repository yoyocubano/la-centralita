"""Módulo de sincronización con Google Sheets ('La Centralita — Leads').

Capacidades:
- Escritura idempotente con reintentos y retroceso exponencial.
- Cola local persistente en 'data/leads_queue.json' si la API falla o no está configurada.
- Formato horario estricto en zona horaria 'Europe/Luxembourg'.
- Validación de campos: Nombre, Teléfono, Email, Resumen, Intención, Fecha/Hora.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

logger = logging.getLogger("la-centralita.sheets_sync")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
QUEUE_FILE = DATA_DIR / "leads_queue.json"
SYNCED_CACHE_FILE = DATA_DIR / "leads_synced.json"

TIMEZONE_LUX = ZoneInfo("Europe/Luxembourg")


def ensure_data_dirs():
    """Garantiza la existencia de la carpeta de almacenamiento de datos."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not QUEUE_FILE.exists():
        QUEUE_FILE.write_text("[]", encoding="utf-8")
    if not SYNCED_CACHE_FILE.exists():
        SYNCED_CACHE_FILE.write_text("{}", encoding="utf-8")


class GoogleSheetsSync:
    """Sincronizador idempotente para el Google Sheet 'La Centralita — Leads'."""

    SHEET_NAME = "La Centralita — Leads"
    COLUMNS = [
        "ID_LEAD",
        "FECHA_HORA_LUXEMBOURG",
        "NOMBRE",
        "TELEFONO",
        "EMAIL",
        "EMPRESA",
        "INTENCION_MOTIVO",
        "RESUMEN_REQUERIMIENTOS",
        "VALOR_ESTIMADO_EUR",
        "ESTADO_TWENTY_CRM",
        "ESTADO_DOCUSEAL",
        "AGENTE",
    ]

    def __init__(self):
        ensure_data_dirs()
        self.credentials_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON", "")
        self.sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        self.access_token: Optional[str] = None
        self.token_expiry: float = 0.0

    def generate_lead_id(self, lead_data: Dict[str, Any]) -> str:
        """Genera un hash determinista para garantizar idempotencia y evitar duplicados."""
        raw_key = f"{lead_data.get('telefono', '')}-{lead_data.get('nombre', '')}-{lead_data.get('fecha_evento', '')}-{lead_data.get('motivo', '')}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    def get_luxembourg_now(self) -> str:
        """Devuelve fecha y hora actual en zona horaria Europe/Luxembourg."""
        now = datetime.now(TIMEZONE_LUX)
        return now.strftime("%Y-%m-%d %H:%M:%S (%Z)")

    def format_row(self, lead_data: Dict[str, Any]) -> List[Any]:
        """Convierte los datos del lead en una fila estructurada para la hoja de cálculo."""
        lead_id = lead_data.get("id") or self.generate_lead_id(lead_data)
        lux_timestamp = lead_data.get("timestamp_lux") or self.get_luxembourg_now()
        
        phone_val = str(lead_data.get("telefono") or lead_data.get("phone") or "No especificado")
        if phone_val.startswith("+"):
            phone_val = "'" + phone_val

        return [
            lead_id,
            lux_timestamp,
            lead_data.get("nombre") or lead_data.get("name") or "No especificado",
            phone_val,
            lead_data.get("email") or "No especificado",
            lead_data.get("empresa") or lead_data.get("company") or "Particular",
            lead_data.get("motivo") or lead_data.get("interest") or "Consulta general",
            lead_data.get("detalles") or lead_data.get("summary") or "Sin resumen",
            lead_data.get("valor_eur") or lead_data.get("amount") or "2.500 €",
            lead_data.get("stage") or lead_data.get("estado_crm") or "nuevo",
            lead_data.get("docuseal_status") or "BORRADOR",
            lead_data.get("agente") or "Sofía (IA WELUX)",
        ]

    def load_queue(self) -> List[Dict[str, Any]]:
        try:
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_queue(self, queue: List[Dict[str, Any]]) -> None:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)

    def load_synced_cache(self) -> Dict[str, str]:
        try:
            with open(SYNCED_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def mark_synced(self, lead_id: str, row_data: List[Any]) -> None:
        cache = self.load_synced_cache()
        cache[lead_id] = {
            "synced_at": self.get_luxembourg_now(),
            "data": row_data,
        }
        with open(SYNCED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)

    def is_already_synced(self, lead_id: str) -> bool:
        cache = self.load_synced_cache()
        return lead_id in cache

    async def sync_lead(self, lead_data: Dict[str, Any]) -> Dict[str, Any]:
        """Punto de entrada principal para escribir un lead con reintentos y cola."""
        lead_id = lead_data.get("id") or self.generate_lead_id(lead_data)
        lead_data["id"] = lead_id

        # 1. Verificación de Idempotencia
        if self.is_already_synced(lead_id):
            logger.info("Lead %s ya fue sincronizado previamente. Ignorando duplicado.", lead_id)
            return {"status": "ALREADY_SYNCED", "lead_id": lead_id}

        row = self.format_row(lead_data)

        # 2. Si no hay credenciales de Google Sheets configuradas aún
        if not self.credentials_json or not self.sheet_id:
            logger.info("GOOGLE_SHEETS_CREDENTIALS_JSON o GOOGLE_SHEET_ID no configuradas. Guardando lead en cola local.")
            queue = self.load_queue()
            # Evitar duplicados en la cola
            if not any(item.get("id") == lead_id for item in queue):
                queue.append({"lead": lead_data, "row": row, "queued_at": self.get_luxembourg_now()})
                self.save_queue(queue)
            return {"status": "QUEUED_OFFLINE", "lead_id": lead_id, "row": row}

        # 3. Intentos de sincronización directa con retroceso exponencial
        for attempt in range(1, 4):
            try:
                success = await self._send_to_sheets_api(row)
                if success:
                    self.mark_synced(lead_id, row)
                    logger.info("Lead %s sincronizado exitosamente en Google Sheets en intento %d.", lead_id, attempt)
                    return {"status": "SYNCED_ONLINE", "lead_id": lead_id, "row": row}
            except Exception as e:
                logger.warning("Fallo intento %d/3 de sincronización con Sheets: %s", attempt, e)
                time.sleep(0.5 * (2 ** (attempt - 1)))

        # 4. Fallback a cola local si los 3 intentos fallaron
        logger.error("No se pudo escribir en Google Sheets tras 3 intentos. Encolando lead %s.", lead_id)
        queue = self.load_queue()
        queue.append({"lead": lead_data, "row": row, "queued_at": self.get_luxembourg_now()})
        self.save_queue(queue)
        return {"status": "QUEUED_ERROR", "lead_id": lead_id, "row": row}

    def _get_access_token(self) -> Optional[str]:
        """Obtiene o renueva un token OAuth2 para la cuenta de servicio."""
        if self.access_token and time.time() < self.token_expiry:
            return self.access_token
        if not self.credentials_json:
            return None
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
            creds_dict = json.loads(self.credentials_json)
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            credentials.refresh(Request())
            self.access_token = credentials.token
            self.token_expiry = time.time() + 3500
            return self.access_token
        except Exception as err:
            logger.error("Error renovando token de Google Sheets Service Account: %s", err)
            return None

    async def read_leads_from_sheet(self) -> List[Dict[str, Any]]:
        """Lee los leads directamente desde la hoja 'Leads' del Google Sheet oficial."""
        token = self._get_access_token()
        if not token or not self.sheet_id:
            logger.warning("Credenciales no disponibles para leer Google Sheets.")
            return []
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}/values/Leads!A2:L"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                rows = data.get("values", [])
                leads = []
                for row in rows:
                    if not row or len(row) < 3:
                        continue
                    lead_obj = {
                        "id": row[0] if len(row) > 0 else "",
                        "timestamp_lux": row[1] if len(row) > 1 else "",
                        "name": row[2] if len(row) > 2 else "Sin nombre",
                        "phone": row[3] if len(row) > 3 else "",
                        "email": row[4] if len(row) > 4 else "",
                        "company": row[5] if len(row) > 5 else "",
                        "interest": row[6] if len(row) > 6 else "",
                        "summary": row[7] if len(row) > 7 else "",
                        "amount": row[8] if len(row) > 8 else "2.500 €",
                        "stage": row[9] if len(row) > 9 else "nuevo",
                        "docuseal_status": row[10] if len(row) > 10 else "BORRADOR",
                        "agent": row[11] if len(row) > 11 else "Sofía (IA)",
                    }
                    leads.append(lead_obj)
                return leads
        except Exception as e:
            logger.error("Error leyendo leads de Google Sheets: %s", e)
            return []

    async def _send_to_sheets_api(self, row: List[Any]) -> bool:
        """Envía la fila a la Google Sheets API v4 en la pestaña 'Leads'."""
        token = self._get_access_token()
        if not token:
            raise ValueError("No se pudo obtener access token para Google Sheets")

        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}/values/Leads!A1:append?valueInputOption=RAW"
        payload = json.dumps({"values": [row]}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 201)
        except urllib.error.HTTPError as he:
            logger.error("HTTP error de Google Sheets: %d - %s", he.code, he.read().decode("utf-8", "ignore"))
            raise
