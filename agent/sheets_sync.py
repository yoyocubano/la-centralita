"""Módulo de sincronización con Google Sheets ('La Centralita — Leads').

Capacidades:
- Escritura idempotente con reintentos y retroceso exponencial (sin bloquear el event loop).
- Cola local persistente en 'data/leads_queue.json' si la API falla o no está configurada.
- Formato horario estricto en zona horaria 'Europe/Luxembourg'.
- Escritura en modo RAW: los teléfonos "+352 ..." se guardan como texto literal
  (nunca se interpretan como fórmula -> sin '#ERROR!').
- Lectura en vivo con estado explícito (ok / not_configured / error) y caché corta,
  para que el panel nunca confunda "Sheet caído" con "no hay leads".
"""

import asyncio
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("la-centralita.sheets_sync")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
QUEUE_FILE = DATA_DIR / "leads_queue.json"
SYNCED_CACHE_FILE = DATA_DIR / "leads_synced.json"

TIMEZONE_LUX = ZoneInfo("Europe/Luxembourg")
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

# Valores de error que Google Sheets devuelve en FORMATTED_VALUE.
SHEET_ERROR_VALUES = {"#ERROR!", "#NAME?", "#VALUE!", "#REF!", "#N/A", "#DIV/0!", "#NUM!", "#NULL!"}
PHONE_NEEDS_REPAIR = "⚠ Revisar en Sheet (#ERROR!)"

READ_CACHE_TTL_SECONDS = 10.0


def ensure_data_dirs():
    """Garantiza la existencia de la carpeta de almacenamiento de datos."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not QUEUE_FILE.exists():
        QUEUE_FILE.write_text("[]", encoding="utf-8")
    if not SYNCED_CACHE_FILE.exists():
        SYNCED_CACHE_FILE.write_text("{}", encoding="utf-8")


def clean_cell_text(value: Any) -> str:
    """Normaliza un valor leído del Sheet: quita el apóstrofe literal heredado
    (filas escritas con el bug apóstrofe+RAW) y detecta celdas en error."""
    text = "" if value is None else str(value).strip()
    if text.startswith("'"):
        text = text[1:]
    return text


def clean_phone_cell(value: Any) -> str:
    text = clean_cell_text(value)
    if text.upper() in SHEET_ERROR_VALUES:
        return PHONE_NEEDS_REPAIR
    return text


@dataclass
class SheetReadResult:
    """Resultado de lectura del Sheet con estado explícito."""
    status: str  # "ok" | "not_configured" | "error"
    leads: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    rows_needing_repair: int = 0


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

    # Caché de lectura compartida entre instancias (el panel hace polling cada 15 s).
    _read_cache: Dict[str, Any] = {"at": 0.0, "result": None}

    def __init__(self):
        ensure_data_dirs()
        self.credentials_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON", "")
        self.sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        self.tab_name = os.getenv("GOOGLE_SHEET_TAB", "Leads")
        self.access_token: Optional[str] = None
        self.token_expiry: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.credentials_json and self.sheet_id)

    def _range(self, a1: str) -> str:
        # El nombre de pestaña va entre comillas simples (admite espacios y guiones largos).
        return urllib.parse.quote(f"'{self.tab_name}'!{a1}", safe="")

    def generate_lead_id(self, lead_data: Dict[str, Any]) -> str:
        """Genera un hash determinista para garantizar idempotencia y evitar duplicados."""
        raw_key = f"{lead_data.get('telefono', '')}-{lead_data.get('nombre', '')}-{lead_data.get('fecha_evento', '')}-{lead_data.get('motivo', '')}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    def get_luxembourg_now(self) -> str:
        """Devuelve fecha y hora actual en zona horaria Europe/Luxembourg."""
        now = datetime.now(TIMEZONE_LUX)
        return now.strftime("%Y-%m-%d %H:%M:%S (%Z)")

    def format_row(self, lead_data: Dict[str, Any]) -> List[Any]:
        """Convierte los datos del lead en una fila estructurada para la hoja de cálculo.

        La escritura usa valueInputOption=RAW, por lo que cada valor se guarda como
        texto literal: NO se antepone apóstrofe (con RAW quedaría visible en la celda).
        """
        lead_id = lead_data.get("id") or self.generate_lead_id(lead_data)
        lux_timestamp = lead_data.get("timestamp_lux") or self.get_luxembourg_now()
        phone_val = str(lead_data.get("telefono") or lead_data.get("phone") or "No especificado").strip()

        return [
            lead_id,
            lux_timestamp,
            lead_data.get("nombre") or lead_data.get("name") or "No especificado",
            phone_val,
            lead_data.get("email") or "No especificado",
            lead_data.get("empresa") or lead_data.get("company") or "Particular",
            lead_data.get("motivo") or lead_data.get("interest") or "Consulta general",
            lead_data.get("detalles") or lead_data.get("summary") or "Sin resumen",
            lead_data.get("valor_eur") or lead_data.get("amount") or "Por cotizar",
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

    def load_synced_cache(self) -> Dict[str, Any]:
        try:
            with open(SYNCED_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def mark_synced(self, lead_id: str, row_data: List[Any]) -> None:
        # Solo se guarda el sello de sincronización: la fila con PII vive en el Sheet,
        # no se duplica en disco (minimización RGPD).
        cache = self.load_synced_cache()
        cache[lead_id] = {"synced_at": self.get_luxembourg_now()}
        with open(SYNCED_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)

    def is_already_synced(self, lead_id: str) -> bool:
        cache = self.load_synced_cache()
        return lead_id in cache

    def _enqueue(self, lead_id: str, lead_data: Dict[str, Any], row: List[Any]) -> None:
        queue = self.load_queue()
        if not any((item.get("lead") or {}).get("id") == lead_id for item in queue):
            queue.append({"lead": lead_data, "row": row, "queued_at": self.get_luxembourg_now()})
            self.save_queue(queue)

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
        if not self.is_configured:
            logger.warning("Google Sheets no configurado: lead %s guardado en cola local.", lead_id)
            self._enqueue(lead_id, lead_data, row)
            return {"status": "QUEUED_OFFLINE", "lead_id": lead_id, "row": row}

        # 3. Intentos de sincronización directa con retroceso exponencial (no bloqueante)
        for attempt in range(1, 4):
            try:
                success = await asyncio.to_thread(self._send_to_sheets_api, row)
                if success:
                    self.mark_synced(lead_id, row)
                    self.invalidate_read_cache()
                    logger.info("Lead %s sincronizado en Google Sheets (intento %d).", lead_id, attempt)
                    return {"status": "SYNCED_ONLINE", "lead_id": lead_id, "row": row}
            except Exception as e:
                logger.warning("Fallo intento %d/3 de sincronización con Sheets: %s", attempt, e)
            if attempt < 3:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

        # 4. Fallback a cola local si los 3 intentos fallaron
        logger.error("No se pudo escribir en Google Sheets tras 3 intentos. Encolando lead %s.", lead_id)
        self._enqueue(lead_id, lead_data, row)
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
            self.token_expiry = time.time() + 3300
            return self.access_token
        except Exception as err:
            logger.error("Error renovando token de Google Sheets Service Account: %s", err)
            return None

    @classmethod
    def invalidate_read_cache(cls) -> None:
        cls._read_cache = {"at": 0.0, "result": None}

    def _row_to_lead(self, row: List[Any]) -> Dict[str, Any]:
        def col(i: int, default: str = "") -> str:
            return clean_cell_text(row[i]) if len(row) > i and row[i] not in (None, "") else default

        return {
            "id": col(0),
            "timestamp_lux": col(1),
            "name": col(2, "Sin nombre"),
            "phone": clean_phone_cell(row[3]) if len(row) > 3 else "",
            "email": col(4),
            "company": col(5),
            "interest": col(6),
            "summary": col(7),
            "amount": col(8, "Por cotizar"),
            "stage": col(9, "nuevo").lower(),
            "docuseal_status": col(10, "BORRADOR"),
            "agent": col(11, "Sofía (IA)"),
        }

    def _fetch_rows(self) -> List[List[Any]]:
        token = self._get_access_token()
        if not token:
            raise RuntimeError("No se pudo obtener access token de la cuenta de servicio")
        url = f"{SHEETS_API}/{self.sheet_id}/values/{self._range('A2:L')}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("values", [])

    async def read_leads(self, use_cache: bool = True) -> SheetReadResult:
        """Lee los leads en vivo desde la pestaña configurada del Google Sheet.

        Nunca devuelve datos simulados. Si el Sheet no responde, el estado es "error"
        con el motivo, y el llamador decide cómo degradar (y lo declara).
        """
        if not self.is_configured:
            return SheetReadResult(status="not_configured", error="GOOGLE_SHEETS_CREDENTIALS_JSON / GOOGLE_SHEET_ID ausentes")

        cache = GoogleSheetsSync._read_cache
        if use_cache and cache["result"] is not None and time.time() - cache["at"] < READ_CACHE_TTL_SECONDS:
            return cache["result"]

        try:
            rows = await asyncio.to_thread(self._fetch_rows)
        except urllib.error.HTTPError as he:
            logger.error("HTTP %s leyendo Google Sheets", he.code)
            return SheetReadResult(status="error", error=f"Google Sheets respondió HTTP {he.code}")
        except Exception as e:
            logger.error("Error leyendo leads de Google Sheets: %s", e)
            return SheetReadResult(status="error", error=f"Google Sheets no disponible: {type(e).__name__}")

        leads = [self._row_to_lead(row) for row in rows if row and len(row) >= 3]
        repair = sum(1 for lead in leads if lead["phone"] == PHONE_NEEDS_REPAIR)
        result = SheetReadResult(status="ok", leads=leads, rows_needing_repair=repair)
        GoogleSheetsSync._read_cache = {"at": time.time(), "result": result}
        return result

    async def read_leads_from_sheet(self) -> List[Dict[str, Any]]:
        """Compatibilidad: devuelve solo la lista (vacía si no hay acceso)."""
        return (await self.read_leads()).leads

    VALID_STAGES = ("nuevo", "contactado", "agendado", "ganado")

    def _update_stage_blocking(self, lead_id: str, stage: str) -> bool:
        token = self._get_access_token()
        if not token:
            raise RuntimeError("No se pudo obtener access token de la cuenta de servicio")
        ids_url = f"{SHEETS_API}/{self.sheet_id}/values/{self._range('A2:A')}"
        req = urllib.request.Request(ids_url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            ids = [r[0] if r else "" for r in json.loads(resp.read().decode("utf-8")).get("values", [])]
        try:
            row_number = ids.index(lead_id) + 2
        except ValueError:
            return False
        url = f"{SHEETS_API}/{self.sheet_id}/values/{self._range(f'J{row_number}')}?valueInputOption=RAW"
        body = json.dumps({"values": [[stage]]}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="PUT",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200

    async def update_stage(self, lead_id: str, stage: str) -> str:
        """Actualiza la columna ESTADO_TWENTY_CRM de un lead. Devuelve updated | not_found | not_configured."""
        if stage not in self.VALID_STAGES:
            raise ValueError(f"etapa no válida: {stage}")
        if not self.is_configured:
            return "not_configured"
        updated = await asyncio.to_thread(self._update_stage_blocking, lead_id, stage)
        if updated:
            self.invalidate_read_cache()
            return "updated"
        return "not_found"

    def _send_to_sheets_api(self, row: List[Any]) -> bool:
        """Envía la fila a la Google Sheets API v4 (bloqueante; se ejecuta en un hilo)."""
        token = self._get_access_token()
        if not token:
            raise ValueError("No se pudo obtener access token para Google Sheets")

        url = (
            f"{SHEETS_API}/{self.sheet_id}/values/{self._range('A1')}:append"
            "?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
        )
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
            logger.error("HTTP error de Google Sheets: %d", he.code)
            raise
