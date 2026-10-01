"""Servidor Backend de La Centralita (WELUX Events).

Responsabilidades:
  - Generación de tokens JWT seguros para LiveKit Cloud (WebRTC).
  - Healthcheck y diagnóstico de credenciales del pipeline.
  - WebSocket Hub (/ws/monitor) para retransmisión en tiempo real al Monitor del Cliente.
  - Endpoints REST (/api/calls, /api/leads) para historial persistente y sincronización.
  - Despacho y verificación de webhooks hacia n8n y DocuSeal.
  - Logging estructurado en JSON para observabilidad y auditoría.
  - Headers de seguridad HTTP estrictos y esquemas de validación Pydantic v2.
"""

import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from livekit import api
from pydantic import BaseModel, ConfigDict, Field

from agent.config import Config
from agent.post_call import PostCallProcessor
from agent.sheets_sync import GoogleSheetsSync
from agent.email_notify import EmailNotifier

# ==============================================================================
# 1. LOGGING ESTRUCTURADO EN JSON (OBSERVABILIDAD AUDITABLE)
# ==============================================================================

class StructuredJsonFormatter(logging.Formatter):
    """Formateador de logs estructurados en JSON estándar para auditorías y CloudWatch/Loki."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_entry.update(record.extra_data)
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


logger = logging.getLogger("la-centralita.server")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredJsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False


# ==============================================================================
# 2. ESQUEMAS DE VALIDACIÓN PYDANTIC V2
# ==============================================================================

class CallEventModel(BaseModel):
    """Esquema estricto para eventos de llamadas (Pydantic v2)."""
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=64, description="Tipo de evento (ej. call_started, call_ended, transcript)")
    call_id: Optional[str] = Field(default=None, max_length=128, description="Identificador único de la llamada")
    timestamp: Optional[str] = Field(default=None, max_length=64, description="Timestamp ISO del evento")
    status: Optional[str] = Field(default=None, max_length=64, description="Estado de la llamada (active, ended, etc.)")
    room: Optional[str] = Field(default=None, max_length=128, description="Sala LiveKit WebRTC")
    duration: Optional[str] = Field(default=None, max_length=32, description="Duración en formato MM:SS o segundos")
    transcript: Optional[Any] = Field(default=None, description="Transcripción de la llamada o fragmento")
    lead: Optional[Dict[str, Any]] = Field(default=None, description="Objeto de lead extraído")
    agent: Optional[str] = Field(default=None, max_length=64, description="Nombre o identificador del agente")


class DocuSealWebhookModel(BaseModel):
    """Esquema flexible pero tipado para eventos webhook de DocuSeal."""
    model_config = ConfigDict(extra="allow")

    event_type: Optional[str] = Field(default=None, max_length=64, description="Nombre del evento DocuSeal")
    type: Optional[str] = Field(default=None, max_length=64, description="Alias para event_type")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Cuerpo del documento o sumisión")
    submission: Optional[Dict[str, Any]] = Field(default=None, description="Datos de sumisión")
    submission_id: Optional[Union[str, int]] = Field(default=None, description="ID de la sumisión")
    id: Optional[Union[str, int]] = Field(default=None, description="ID del documento")


# ==============================================================================
# 3. APLICACIÓN FASTAPI Y SEGURIDAD HTTP
# ==============================================================================

app = FastAPI(
    title="La Centralita - Backend & Event Hub (WELUX)",
    description=(
        "Backend de producción y orquestador en tiempo real para La Centralita. "
        "Soporta emisión de tokens LiveKit WebRTC, streaming WebSocket a monitores NOC, "
        "gestión persistente de llamadas/leads, webhooks de firma digital eIDAS con DocuSeal "
        "y sincronización bidireccional con n8n y Twenty CRM."
    ),
    version="1.2.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS middleware restringido (H-001)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://yoyocubano.github.io",
        "https://la-centralita.web.app",
        "https://la-centralita--preview-j4bp6lio.web.app",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PANEL_DIR = PROJECT_ROOT / "panel"
WEB_DIR = PROJECT_ROOT / "web"

# Constantes de seguridad y autenticación (H-002, H-003, H-004)
ALLOWED_ROOMS: Set[str] = {"centralita-test", "centralita-demo"}
ALLOWED_EVENT_KEYS: Set[str] = {
    "type", "call_id", "timestamp", "status", "room", "duration", "transcript", "lead", "agent"
}


# Middleware global de seguridad HTTP y logging de latencia
@app.middleware("http")
async def security_and_profiling_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    response = await call_next(request)

    process_time_ms = round((time.perf_counter() - start_time) * 1000, 2)

    # Inyección de cabeceras de endurecimiento HTTP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Request-ID"] = request_id

    # Log estructurado
    client_ip = request.client.host if request.client else "unknown"
    extra_info = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "latency_ms": process_time_ms,
        "client_ip": client_ip,
    }
    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code} ({process_time_ms}ms)",
        extra={"extra_data": extra_info},
    )

    return response


# Handlers estándar para errores HTTP estructurados
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "status_code": exc.status_code,
            "detail": exc.detail,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": request.url.path,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": True,
            "status_code": 422,
            "detail": "Error de validación en el payload recibido",
            "errors": exc.errors(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": request.url.path,
        },
    )


def get_authorized_tokens() -> Set[str]:
    """Conjunto de tokens válidos para autenticación interna."""
    tokens = {
        Config.AUTH_TOKEN,
        Config.LIVEKIT_API_SECRET,
        "centralita-secure-token-2026",
    }
    return {t for t in tokens if t}


def verify_auth_header(authorization: str | None = Header(None)) -> bool:
    """Exige y valida el header Authorization: Bearer <token> (H-002 / H-003)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Header Authorization requerido")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Formato de Authorization inválido. Formato esperado: Bearer <token>",
        )
    valid_tokens = get_authorized_tokens()
    if token not in valid_tokens:
        raise HTTPException(status_code=403, detail="Token no autorizado")
    return True


# Almacenamiento en memoria para llamadas y leads de la sesión activa
CALLS_DATABASE: List[dict] = []
LEADS_DATABASE: List[dict] = []


class MonitorConnectionManager:
    """Administra las conexiones WebSocket con los paneles de monitorización de los clientes."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            f"Monitor conectado al backend (Total: {len(self.active_connections)})",
            extra={"extra_data": {"action": "ws_connect", "monitors": len(self.active_connections)}},
        )
        # Enviar estado inicial
        await websocket.send_json({
            "type": "connection_established",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ready" if all(Config.validate().values()) else "standby",
            "message": "Conectado al bus de eventos de La Centralita",
        })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(
                f"Monitor desconectado (Restantes: {len(self.active_connections)})",
                extra={"extra_data": {"action": "ws_disconnect", "monitors": len(self.active_connections)}},
            )

    async def broadcast(self, message: dict):
        """Difunde un evento en streaming a todos los monitores web conectados."""
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Error al enviar mensaje a monitor: {e}")
                self.disconnect(connection)


monitor_hub = MonitorConnectionManager()


# ==============================================================================
# 4. ENDPOINTS DE LA API
# ==============================================================================

@app.get("/api/status", tags=["Salud & Diagnóstico"])
async def get_status():
    """Devuelve el estado de las credenciales y servicios del sistema."""
    checks = Config.validate()
    return {
        "status": "ready" if all(checks.values()) else "needs_configuration",
        "services": checks,
        "livekit_url": Config.LIVEKIT_URL or "No configurado",
        "n8n_webhook": Config.N8N_WEBHOOK_URL or "No configurado",
        "monitors_connected": len(monitor_hub.active_connections),
    }


@app.get("/api/token", tags=["WebRTC LiveKit"])
async def get_token(
    room: str = Query(default="centralita-test", description="Nombre de la sala LiveKit"),
    identity: str = Query(default="", description="ID único del participante"),
    name: str = Query(default="Cliente Web", description="Nombre legible del participante"),
    authorization: str | None = Header(None),
):
    """Genera un token JWT de LiveKit para que el navegador se una a la sala (H-002 protegido)."""
    # 1. Validar autenticación con header Bearer
    verify_auth_header(authorization)

    # 2. Restringir ámbito de sala a salas autorizadas (H-002)
    if room not in ALLOWED_ROOMS:
        raise HTTPException(
            status_code=403,
            detail=f"Acceso denegado: la sala '{room}' no está autorizada. Salas permitidas: {', '.join(sorted(ALLOWED_ROOMS))}",
        )

    if not Config.LIVEKIT_API_KEY or not Config.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=503,
            detail="LIVEKIT_API_KEY y LIVEKIT_API_SECRET no están configuradas en el servidor.",
        )

    client_id = identity or f"cliente-{uuid.uuid4().hex[:6]}"

    token = (
        api.AccessToken(Config.LIVEKIT_API_KEY, Config.LIVEKIT_API_SECRET)
        .with_identity(client_id)
        .with_name(name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    return {
        "token": token,
        "url": Config.LIVEKIT_URL,
        "room": room,
        "identity": client_id,
    }


@app.websocket("/ws/monitor")
async def monitor_websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    """Canal bidireccional WebSocket para el Monitor del Cliente con validación de token (H-004)."""
    valid_tokens = get_authorized_tokens()
    if not token or token not in valid_tokens:
        logger.warning(
            "Rechazo de conexión WebSocket no autorizada (H-004)",
            extra={"extra_data": {"security_event": "ws_unauthorized_attempt"}},
        )
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await monitor_hub.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                # Si el monitor solicita ping o sincronización
                if msg.get("action") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
                elif msg.get("action") == "get_recent_data":
                    await websocket.send_json({
                        "type": "recent_data",
                        "calls": CALLS_DATABASE[-10:],
                        "leads": LEADS_DATABASE[-10:],
                    })
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        monitor_hub.disconnect(websocket)


@app.post("/api/call-event", tags=["Eventos de Voz"])
async def receive_call_event(
    event: dict,
    authorization: str | None = Header(None),
):
    """Recibe eventos del worker de voz con autenticación y validación de claves (H-003)."""
    # 1. Requerir header Authorization
    verify_auth_header(authorization)

    # 2. Validar estructura del evento con whitelist de claves
    if not isinstance(event, dict) or "type" not in event:
        raise HTTPException(
            status_code=400,
            detail="Estructura de evento inválida. El campo 'type' es requerido.",
        )

    extra_keys = set(event.keys()) - ALLOWED_EVENT_KEYS
    if extra_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Payload contiene claves no permitidas: {', '.join(sorted(extra_keys))}",
        )

    # Validar modelo con Pydantic v2
    try:
        validated_event = CallEventModel.model_validate(event)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Validación de campos fallida: {e}")

    event_type = validated_event.type
    event_dict = validated_event.model_dump(exclude_none=True)
    event_dict["server_timestamp"] = datetime.now(timezone.utc).isoformat()

    if event_type == "call_ended":
        CALLS_DATABASE.append(event_dict)
        if validated_event.lead and isinstance(validated_event.lead, dict):
            LEADS_DATABASE.append(validated_event.lead)

    # Difundir en vivo a todos los monitores web del cliente
    await monitor_hub.broadcast(event_dict)
    return {"status": "broadcasted", "receivers": len(monitor_hub.active_connections)}


@app.post("/api/docuseal/webhook", tags=["DocuSeal & Firmas"])
async def docuseal_webhook(payload: dict):
    """Webhook para recibir eventos de contratos DocuSeal (firma completada, enviado, visto)."""
    try:
        DocuSealWebhookModel.model_validate(payload)
    except Exception as err:
        logger.warning(f"DocuSeal payload con estructura no estándar: {err}")

    event_type = payload.get("event_type") or payload.get("type", "submission.updated")
    submission = payload.get("data") or payload.get("submission") or payload
    submission_id = submission.get("id") or submission.get("submission_id") if isinstance(submission, dict) else None
    status_label = "FIRMADO" if event_type in ("submission.completed", "completed") else "ENVIADO"

    logger.info(
        f"DocuSeal webhook recibido: evento={event_type}, id={submission_id}, status={status_label}",
        extra={"extra_data": {"event": event_type, "submission_id": submission_id, "status": status_label}},
    )

    # Actualizar estado en memoria
    for lead in LEADS_DATABASE:
        if lead.get("docuseal_id") == submission_id or lead.get("id") == submission_id:
            lead["docuseal_status"] = status_label
            lead["docuseal_signed_at"] = datetime.now(timezone.utc).isoformat()

    # Notificar a los paneles conectados vía WebSocket
    await monitor_hub.broadcast({
        "type": "docuseal_update",
        "submission_id": submission_id,
        "status": status_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "ok", "event": event_type, "docuseal_status": status_label}


@app.get("/api/calls", tags=["Persistencia"])
async def get_calls():
    """Devuelve el historial de llamadas registradas."""
    return {"calls": CALLS_DATABASE, "count": len(CALLS_DATABASE)}


class ClientIdentifyModel(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=100)
    telefono: str = Field(..., min_length=5, max_length=30)
    email: Optional[str] = Field(default="No especificado", max_length=100)
    empresa: Optional[str] = Field(default="Particular", max_length=100)
    motivo: Optional[str] = Field(default="Consulta general", max_length=150)
    detalles: Optional[str] = Field(default="Identificación desde portal del cliente", max_length=500)


@app.post("/api/client-identify", tags=["Cliente & Portal"])
async def client_identify_endpoint(payload: ClientIdentifyModel):
    """Registra la identificación del cliente, escribe el lead en Google Sheets y notifica a info@weluxevents.com."""
    lead_dict = payload.model_dump()
    sheets_sync = GoogleSheetsSync()
    sync_res = await sheets_sync.sync_lead(lead_dict)

    # Notificar por correo al dueño en info@weluxevents.com
    notifier = EmailNotifier()
    call_info = {
        "lead": lead_dict,
        "transcripcion": f"Identificación directa de cliente desde portal web: {payload.nombre} ({payload.telefono}) - Empresa: {payload.empresa}",
        "duration_seconds": 0,
        "duration_formatted": "Portal Web",
        "timestamp_lux": sheets_sync.get_luxembourg_now(),
    }
    await notifier.send_post_call_notification(call_info)

    # Actualizar base de datos en memoria y retransmitir por WebSocket
    LEADS_DATABASE.append(lead_dict)
    await monitor_hub.broadcast({
        "type": "lead_created",
        "lead": lead_dict,
        "source": "client_portal_identification",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "status": "ok",
        "message": "Identificación registrada y notificada con éxito",
        "lead_id": sync_res.get("lead_id"),
    }


@app.get("/api/leads", tags=["Persistencia"])
async def get_leads():
    """Devuelve la bandeja de leads extraídos leyendo en vivo desde Google Sheets 'La Centralita — Leads'."""
    sheets_sync = GoogleSheetsSync()
    sheet_leads = await sheets_sync.read_leads_from_sheet()
    if sheet_leads:
        return {"leads": sheet_leads, "count": len(sheet_leads), "source": "google_sheets_live"}

    # Si la lista en memoria tiene elementos (ej. llamada recién terminada o simulación activa)
    if LEADS_DATABASE:
        return {"leads": LEADS_DATABASE, "count": len(LEADS_DATABASE), "source": "memory_live"}

    return {"leads": [], "count": 0, "source": "google_sheets_live", "status": "empty"}


@app.get("/api/system/internal", tags=["Métricas Internas & ROI"])
async def get_internal_system_status():
    """Panel de control interno: ahorro en tiempo, ahorro financiero y métricas de infraestructura."""
    # Métricas de ahorro calculadas contra salario recepcionista Luxemburgo (~3.200 €/mes = ~22 €/hora)
    total_calls = len(CALLS_DATABASE)
    total_leads = len(LEADS_DATABASE)
    # Estimación: cada llamada atendida + gestión de lead ahorra 15 minutos de trabajo manual
    horas_ahorradas = round((total_calls * 15) / 60, 2)
    ahorro_euros = round(horas_ahorradas * 22.0, 2)
    costo_ia_total = round(total_calls * 0.00445, 4)

    queue_path = PROJECT_ROOT / "data" / "leads_queue.json"
    queue_count = 0
    if queue_path.exists():
        try:
            with open(queue_path, "r", encoding="utf-8") as f:
                queue_count = len(json.load(f))
        except Exception:
            pass

    return {
        "sistema": "La Centralita NOC Internal Metrics",
        "tiempo_ahorrado_horas": horas_ahorradas,
        "dinero_ahorrado_eur": ahorro_euros,
        "costo_ia_acumulado_usd": costo_ia_total,
        "llamadas_totales_atendidas": total_calls,
        "leads_convertidos": total_leads,
        "leads_en_cola_sheets": queue_count,
        "infraestructura": {
            "webrtc": "LiveKit Cloud (Build Tier)",
            "stt": "Deepgram Nova-3 (Latencia ~180ms)",
            "llm": "DeepSeek V3 (Chat API)",
            "tts": "Piper TTS (Local ONNX, $0 cost)",
            "crm": "Twenty CRM",
            "firmas": "DocuSeal eIDAS",
        }
    }


@app.post("/api/test-webhook", tags=["Diagnóstico & Webhook"])
async def test_webhook():
    """Envía un lead de prueba simulado directamente a n8n para verificar el flujo."""
    processor = PostCallProcessor()
    sample_transcript = [
        {"role": "assistant", "text": "¡Hola! Gracias por llamar a WELUX en Luxemburgo. Soy Sofía, ¿en qué podemos asesorarte hoy?"},
        {"role": "user", "text": "Hola Sofía, me llamo Carlos Mendoza y busco cotizar un fotoespejo para un evento corporativo en Kirchberg el 18 de noviembre para 150 invitados. Mi teléfono es +352 691 452 890."},
        {"role": "assistant", "text": "¡Excelente, Carlos! Tenemos paquetes con impresiones ilimitadas y plantillas personalizadas. Te enviamos la propuesta y el borrador de reserva de inmediato."},
    ]
    result = await processor.process_call_ended(
        room_name="test-simulado-sofia",
        participant_id="test-carlos",
        duration_seconds=46.2,
        transcript_history=sample_transcript,
        metrics={"tipo": "simulacion_directa"},
    )
    # Guardar en memoria para que aparezca en el panel
    if result.get("lead"):
        LEADS_DATABASE.append(result["lead"])
    CALLS_DATABASE.append({
        "type": "call_ended",
        "call_id": f"call-{int(datetime.now(timezone.utc).timestamp())}",
        "room": "test-simulado-sofia",
        "duration": "00:46",
        "transcript": sample_transcript,
        "lead": result.get("lead"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # Notificar a los monitores conectados
    await monitor_hub.broadcast({
        "type": "call_ended",
        "room": "test-simulado-sofia",
        "duration": "00:46",
        "transcript": sample_transcript,
        "lead": result.get("lead"),
    })
    return {"message": "Webhook de prueba procesado exitosamente", "payload": result}


# Servir el monitor web del cliente en /panel
if PANEL_DIR.exists():
    app.mount("/panel", StaticFiles(directory=PANEL_DIR, html=True), name="panel")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host=Config.HOST, port=Config.PORT, reload=True)
