"""Servidor Backend de La Centralita (WELUX Events).

Responsabilidades:
  - Emisión de tokens JWT de LiveKit (WebRTC) para operadores autenticados y,
    opcionalmente, para la demo pública (sala única, TTL corto, rate-limit).
  - Healthcheck mínimo público y diagnóstico detallado autenticado.
  - WebSocket Hub (/ws/monitor) para el panel en vivo (autenticación por mensaje) y,
    para despliegues serverless (Vercel) sin conexiones persistentes, el mismo flujo de
    eventos por polling autenticado en /api/events (ver docs/VERCEL.md).
  - Endpoints REST con PII (/api/calls, /api/leads, ...) SIEMPRE autenticados.
  - Webhook DocuSeal verificado con secreto compartido.
  - Logging estructurado en JSON sin PII y cabeceras de seguridad estrictas.

Modelo de autenticación: un único secreto CENTRALITA_AUTH_TOKEN (>= 24 caracteres)
definido en el entorno. No existe ningún token de fallback en el código: si no está
configurado, todos los endpoints protegidos responden 503 (fail-closed).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import sys
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from livekit import api
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.config import DATA_DIR, Config
from agent.email_notify import EmailNotifier
from agent.post_call import PostCallProcessor, mask_phone
from agent.sheets_sync import GoogleSheetsSync

# ==============================================================================
# 1. LOGGING ESTRUCTURADO EN JSON (OBSERVABILIDAD AUDITABLE, SIN PII)
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

ALLOWED_EVENT_TYPES: Set[str] = {"call_started", "transcript_delta", "call_ended", "call_status"}


class CallEventModel(BaseModel):
    """Esquema estricto para eventos emitidos por el worker de voz."""
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1, max_length=64, description="call_started | transcript_delta | call_ended | call_status")
    call_id: Optional[str] = Field(default=None, max_length=128)
    timestamp: Optional[str] = Field(default=None, max_length=64)
    status: Optional[str] = Field(default=None, max_length=64)
    room: Optional[str] = Field(default=None, max_length=128)
    duration: Optional[str] = Field(default=None, max_length=32)
    role: Optional[str] = Field(default=None, max_length=16, description="user | assistant (transcript_delta)")
    text: Optional[str] = Field(default=None, max_length=4000, description="Texto de un turno (transcript_delta)")
    transcript: Optional[Any] = Field(default=None, description="Transcripción completa (call_ended)")
    lead: Optional[Dict[str, Any]] = Field(default=None, description="Lead extraído (call_ended)")
    agent: Optional[str] = Field(default=None, max_length=64)

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"tipo de evento no soportado: {value}")
        return value

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ("user", "assistant"):
            raise ValueError("role debe ser 'user' o 'assistant'")
        return value


class DocuSealWebhookModel(BaseModel):
    """Esquema flexible pero tipado para eventos webhook de DocuSeal."""
    model_config = ConfigDict(extra="allow")

    event_type: Optional[str] = Field(default=None, max_length=64)
    type: Optional[str] = Field(default=None, max_length=64)
    data: Optional[Dict[str, Any]] = Field(default=None)
    submission: Optional[Dict[str, Any]] = Field(default=None)
    submission_id: Optional[Union[str, int]] = Field(default=None)
    id: Optional[Union[str, int]] = Field(default=None)


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ().-]{4,22}[0-9]$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return " ".join(_CONTROL_CHARS.sub(" ", value).split())


class ClientIdentifyModel(BaseModel):
    """Formulario público de identificación del cliente (portal)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    nombre: str = Field(..., min_length=2, max_length=100)
    telefono: str = Field(..., min_length=6, max_length=24)
    email: Optional[str] = Field(default=None, max_length=120)
    empresa: Optional[str] = Field(default=None, max_length=100)
    motivo: Optional[str] = Field(default="Identificación en portal web", max_length=150)
    detalles: Optional[str] = Field(default=None, max_length=500)
    consentimiento: bool = Field(..., description="Consentimiento RGPD explícito para tratar los datos")

    @field_validator("nombre", "empresa", "motivo", "detalles")
    @classmethod
    def _strip_control(cls, value: Optional[str]) -> Optional[str]:
        return _clean_text(value)

    @field_validator("telefono")
    @classmethod
    def _valid_phone(cls, value: str) -> str:
        value = _clean_text(value) or ""
        digits = re.sub(r"\D", "", value)
        if not _PHONE_RE.match(value) or not 6 <= len(digits) <= 15:
            raise ValueError("teléfono no válido (use formato internacional, p. ej. +352 691 123 456)")
        return value

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        if not _EMAIL_RE.match(value):
            raise ValueError("email no válido")
        return value.lower()

    @field_validator("consentimiento")
    @classmethod
    def _consent_required(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("se requiere consentimiento explícito para registrar los datos")
        return value


# ==============================================================================
# 3. APLICACIÓN FASTAPI Y SEGURIDAD HTTP
# ==============================================================================

app = FastAPI(
    title="La Centralita - Backend & Event Hub (WELUX)",
    description="Backend y bus de eventos en tiempo real de La Centralita.",
    version="1.3.0",
    docs_url="/api/docs" if Config.ENABLE_API_DOCS else None,
    redoc_url="/api/redoc" if Config.ENABLE_API_DOCS else None,
    openapi_url="/api/openapi.json" if Config.ENABLE_API_DOCS else None,
)

# CORS: lista explícita (sin comodines ni regex). Se amplía con CORS_ALLOWED_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.CORS_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=600,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PANEL_DIR = PROJECT_ROOT / "panel"
WEB_DIR = PROJECT_ROOT / "web"

# Salas a las que un operador autenticado puede pedir token.
ALLOWED_ROOMS: Set[str] = {"centralita-test", "centralita-demo"}
ALLOWED_EVENT_KEYS: Set[str] = set(CallEventModel.model_fields.keys())

MAX_MONITOR_CONNECTIONS = 50
WS_AUTH_TIMEOUT_SECONDS = 5.0
WS_IDLE_TIMEOUT_SECONDS = 90.0


@app.middleware("http")
async def security_and_profiling_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    response = await call_next(request)

    process_time_ms = round((time.perf_counter() - start_time) * 1000, 2)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # X-XSS-Protection está obsoleta y puede introducir fugas: OWASP recomienda "0" + CSP.
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(self)"
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

    # Log estructurado (solo ruta, nunca query string: podría contener datos sensibles)
    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code} ({process_time_ms}ms)",
        extra={"extra_data": {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": process_time_ms,
        }},
    )
    return response


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
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # No se devuelve el valor recibido ('input'): podría contener PII.
    errors = [
        {"loc": list(err.get("loc", [])), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": True,
            "status_code": 422,
            "detail": "Error de validación en el payload recibido",
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": request.url.path,
        },
    )


# ------------------------------------------------------------------------------
# Autenticación
# ------------------------------------------------------------------------------

def get_authorized_tokens() -> Set[str]:
    """Tokens válidos: exclusivamente CENTRALITA_AUTH_TOKEN si es suficientemente fuerte."""
    return {Config.AUTH_TOKEN} if Config.auth_token_is_strong() else set()


def is_valid_token(candidate: Optional[str]) -> bool:
    if not candidate:
        return False
    # compare_digest: comparación en tiempo constante (sin oráculo de timing).
    return any(hmac.compare_digest(candidate.encode(), t.encode()) for t in get_authorized_tokens())


def verify_auth_header(authorization: Optional[str] = Header(None)) -> bool:
    """Exige `Authorization: Bearer <CENTRALITA_AUTH_TOKEN>` (401 / 403 / 503)."""
    if not get_authorized_tokens():
        raise HTTPException(status_code=503, detail="Autenticación no configurada en el servidor (CENTRALITA_AUTH_TOKEN).")
    if not authorization:
        raise HTTPException(status_code=401, detail="Header Authorization requerido", headers={"WWW-Authenticate": "Bearer"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Formato de Authorization inválido. Formato esperado: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not is_valid_token(token.strip()):
        raise HTTPException(status_code=403, detail="Token no autorizado")
    return True


require_auth = Depends(verify_auth_header)


# ------------------------------------------------------------------------------
# Rate limiting en memoria (por IP y ventana deslizante)
# ------------------------------------------------------------------------------

class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


identify_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=600)
auth_verify_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=600)
demo_token_limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=600)


def client_key(request: Request) -> str:
    if Config.SERVERLESS:
        # Vercel fija x-real-ip; en x-forwarded-for la entrada fiable es la ÚLTIMA
        # (la añade el proxy). La primera la controla el cliente: no sirve para rate-limit.
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if forwarded:
            return forwarded[-1]
    return request.client.host if request.client else "unknown"


CALLS_FILE = DATA_DIR / "calls_history.json"


def load_calls_history() -> List[dict]:
    """Carga el historial de llamadas reales persistido en disco si existe."""
    if CALLS_FILE.exists():
        try:
            with open(CALLS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_calls_history(calls: Union[List[dict], Deque[dict]]) -> None:
    """Guarda las llamadas reales en disco para persistencia sin datos simulados."""
    try:
        CALLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CALLS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(calls), f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Error guardando calls_history: {e}")


# ------------------------------------------------------------------------------
# Estado en memoria de la sesión (acotado para evitar crecimiento ilimitado)
# ------------------------------------------------------------------------------

CALLS_DATABASE: Deque[dict] = deque(load_calls_history(), maxlen=500)
LEADS_DATABASE: Deque[dict] = deque(maxlen=500)


class MonitorConnectionManager:
    """Administra las conexiones WebSocket autenticadas de los paneles."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def register(self, websocket: WebSocket):
        self.active_connections.append(websocket)
        logger.info(
            "Monitor autenticado",
            extra={"extra_data": {"action": "ws_connect", "monitors": len(self.active_connections)}},
        )
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
                "Monitor desconectado",
                extra={"extra_data": {"action": "ws_disconnect", "monitors": len(self.active_connections)}},
            )

    async def broadcast(self, message: dict):
        """Difunde un evento a todos los monitores autenticados."""
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Error al enviar mensaje a monitor: {type(e).__name__}")
                self.disconnect(connection)


monitor_hub = MonitorConnectionManager()


class EventLog:
    """Buffer acotado de eventos con cursor, para el polling de /api/events.

    En serverless no hay WebSocket persistente: el panel pide "eventos desde el cursor N".
    El buffer vive en la memoria de ESTA instancia; `instance` cambia en cada arranque
    en frío y el panel lo usa para saber que debe reiniciar el cursor.
    """

    def __init__(self, maxlen: int = 200) -> None:
        self.instance = uuid.uuid4().hex[:12]
        self._seq = 0
        self._events: Deque[dict] = deque(maxlen=maxlen)

    def append(self, event: dict) -> int:
        self._seq += 1
        self._events.append({"seq": self._seq, "event": event})
        return self._seq

    def since(self, cursor: int, limit: int = 100) -> List[dict]:
        return [item for item in self._events if item["seq"] > cursor][:limit]

    def clear(self) -> None:
        self._events.clear()


EVENT_LOG = EventLog()


async def publish_event(event: dict) -> None:
    """Registra el evento para polling y lo difunde a los WebSocket conectados."""
    EVENT_LOG.append(event)
    await monitor_hub.broadcast(event)


# ==============================================================================
# 4. ENDPOINTS DE LA API
# ==============================================================================

@app.get("/api/status", tags=["Salud & Diagnóstico"])
async def get_status():
    """Healthcheck público mínimo: no expone URLs, claves ni detalle de configuración."""
    checks = Config.validate()
    return {
        "status": "ready" if all(checks.values()) else "needs_configuration",
        "auth_configured": checks["CENTRALITA_AUTH_TOKEN"],
        "public_demo_enabled": Config.PUBLIC_DEMO_ENABLED,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/status/details", tags=["Salud & Diagnóstico"], dependencies=[require_auth])
async def get_status_details():
    """Diagnóstico detallado del pipeline (solo operadores autenticados)."""
    sheets = GoogleSheetsSync()
    return {
        "services": Config.validate(),
        "google_sheets_configured": sheets.is_configured,
        "smtp_configured": EmailNotifier().is_configured,
        "tts_provider": Config.TTS_PROVIDER,
        "tts_fallback": Config.TTS_FALLBACK_PROVIDER,
        "n8n_configured": bool(Config.N8N_WEBHOOK_URL),
        "monitors_connected": len(monitor_hub.active_connections),
        "serverless": Config.SERVERLESS,
        "realtime_transport": "polling" if Config.SERVERLESS else "websocket",
    }


def _livekit_token(room: str, identity: str, name: str, ttl: timedelta, can_publish_data: bool) -> str:
    return (
        api.AccessToken(Config.LIVEKIT_API_KEY, Config.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name)
        .with_ttl(ttl)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=can_publish_data,
            )
        )
        .to_jwt()
    )


@app.get("/api/token", tags=["WebRTC LiveKit"], dependencies=[require_auth])
async def get_token(
    room: str = Query(default="centralita-test", max_length=64),
    identity: str = Query(default="", max_length=64, pattern=r"^[A-Za-z0-9_.-]*$"),
    name: str = Query(default="Cliente Web", max_length=64),
):
    """Token JWT de LiveKit (TTL 1 h) para operadores autenticados y salas autorizadas."""
    if room not in ALLOWED_ROOMS:
        raise HTTPException(
            status_code=403,
            detail=f"Acceso denegado: la sala '{room}' no está autorizada. Salas permitidas: {', '.join(sorted(ALLOWED_ROOMS))}",
        )
    if not Config.LIVEKIT_API_KEY or not Config.LIVEKIT_API_SECRET:
        raise HTTPException(status_code=503, detail="LiveKit no configurado en el servidor.")

    client_id = identity or f"cliente-{uuid.uuid4().hex[:8]}"
    token = _livekit_token(room, client_id, _clean_text(name) or "Cliente Web", timedelta(hours=1), True)
    return {"token": token, "url": Config.LIVEKIT_URL, "room": room, "identity": client_id}


@app.post("/api/public/demo-token", tags=["WebRTC LiveKit"])
async def get_public_demo_token(request: Request):
    """Token efímero para la demo pública de la landing (desactivado por defecto).

    Sala fija, TTL 10 min, sin canal de datos y máximo 3 tokens / 10 min por IP.
    Nunca requiere ni expone el secreto interno en el navegador.
    """
    if not Config.PUBLIC_DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Demo pública desactivada.")
    if not Config.LIVEKIT_API_KEY or not Config.LIVEKIT_API_SECRET:
        raise HTTPException(status_code=503, detail="LiveKit no configurado en el servidor.")
    if not demo_token_limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes. Inténtelo más tarde.")

    identity = f"demo-{uuid.uuid4().hex[:10]}"
    token = _livekit_token(Config.PUBLIC_DEMO_ROOM, identity, "Visitante Demo", timedelta(minutes=10), False)
    return {"token": token, "url": Config.LIVEKIT_URL, "room": Config.PUBLIC_DEMO_ROOM, "identity": identity}


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    # Clientes no-navegador (worker, tests) no envían Origin; el token sigue siendo obligatorio.
    return origin is None or origin.rstrip("/") in Config.CORS_ALLOWED_ORIGINS


@app.websocket("/ws/monitor")
async def monitor_websocket_endpoint(websocket: WebSocket):
    """Canal WebSocket del panel.

    Protocolo: el cliente conecta SIN token en la URL (evita que quede en logs de
    proxies) y envía como primer mensaje {"action": "auth", "token": "..."} en
    menos de 5 s. Token inválido -> cierre 4001. Origen no permitido -> 4003.
    """
    if not _origin_allowed(websocket):
        await websocket.close(code=4003, reason="Origin not allowed")
        return
    if len(monitor_hub.active_connections) >= MAX_MONITOR_CONNECTIONS:
        await websocket.close(code=4029, reason="Too many monitors")
        return

    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=WS_AUTH_TIMEOUT_SECONDS)
        msg = json.loads(first)
        token = msg.get("token") if isinstance(msg, dict) and msg.get("action") == "auth" else None
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        token = None

    if not is_valid_token(token):
        logger.warning(
            "Rechazo de conexión WebSocket no autorizada",
            extra={"extra_data": {"security_event": "ws_unauthorized_attempt"}},
        )
        try:
            await websocket.close(code=4001, reason="Unauthorized")
        except RuntimeError:
            pass
        return

    await monitor_hub.register(websocket)
    try:
        while True:
            data = await asyncio.wait_for(websocket.receive_text(), timeout=WS_IDLE_TIMEOUT_SECONDS)
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("action") == "ping":
                await websocket.send_json({"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()})
            elif msg.get("action") == "get_recent_data":
                await websocket.send_json({
                    "type": "recent_data",
                    "calls": list(CALLS_DATABASE)[-10:],
                    "leads": list(LEADS_DATABASE)[-10:],
                })
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        monitor_hub.disconnect(websocket)
        try:
            await websocket.close()
        except RuntimeError:
            pass


@app.get("/api/events", tags=["Eventos de Voz"], dependencies=[require_auth])
async def poll_events(
    since: int = Query(default=0, ge=0),
    instance: str = Query(default="", max_length=32, pattern=r"^[a-f0-9]*$"),
):
    """Alternativa por polling al WebSocket /ws/monitor (serverless, proxies sin WS).

    Devuelve los eventos con `seq > since`. Si `instance` no coincide con la instancia
    actual (arranque en frío o petición servida por otra instancia), el cursor del
    cliente no es válido aquí: se devuelve el buffer completo con `reset=true`.
    """
    reset = bool(instance) and instance != EVENT_LOG.instance
    items = EVENT_LOG.since(0 if reset else since)
    return {
        "instance": EVENT_LOG.instance,
        "cursor": items[-1]["seq"] if items else (0 if reset else max(since, 0)),
        "reset": reset,
        "events": [item["event"] for item in items],
        "transport": "polling",
    }


@app.post("/api/call-event", tags=["Eventos de Voz"], dependencies=[require_auth])
async def receive_call_event(event: dict):
    """Recibe eventos del worker de voz (whitelist de claves + esquema estricto)."""
    if not isinstance(event, dict) or "type" not in event:
        raise HTTPException(status_code=400, detail="Estructura de evento inválida. El campo 'type' es requerido.")

    extra_keys = set(event.keys()) - ALLOWED_EVENT_KEYS
    if extra_keys:
        raise HTTPException(status_code=400, detail=f"Payload contiene claves no permitidas: {', '.join(sorted(extra_keys))}")

    try:
        validated_event = CallEventModel.model_validate(event)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Validación de campos fallida: {e}")

    event_dict = validated_event.model_dump(exclude_none=True)
    event_dict["server_timestamp"] = datetime.now(timezone.utc).isoformat()

    if validated_event.type == "call_ended":
        CALLS_DATABASE.append(event_dict)
        if validated_event.lead:
            LEADS_DATABASE.append(validated_event.lead)

    await publish_event(event_dict)
    return {"status": "broadcasted", "receivers": len(monitor_hub.active_connections)}


@app.post("/api/docuseal/webhook", tags=["DocuSeal & Firmas"])
async def docuseal_webhook(request: Request, payload: dict):
    """Webhook de DocuSeal. Exige la cabecera secreta configurada en DocuSeal
    (DOCUSEAL_WEBHOOK_HEADER, por defecto X-Docuseal-Secret)."""
    if not Config.DOCUSEAL_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook DocuSeal no configurado (DOCUSEAL_WEBHOOK_SECRET).")
    provided = request.headers.get(Config.DOCUSEAL_WEBHOOK_HEADER, "")
    if not hmac.compare_digest(provided.encode(), Config.DOCUSEAL_WEBHOOK_SECRET.encode()):
        logger.warning("Webhook DocuSeal rechazado", extra={"extra_data": {"security_event": "docuseal_bad_secret"}})
        raise HTTPException(status_code=401, detail="Firma del webhook inválida")

    try:
        DocuSealWebhookModel.model_validate(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Payload DocuSeal inválido")

    event_type = payload.get("event_type") or payload.get("type") or "submission.updated"
    submission = payload.get("data") or payload.get("submission") or payload
    submission_id = None
    if isinstance(submission, dict):
        submission_id = submission.get("submission_id") or submission.get("id")
    status_map = {
        "submission.completed": "FIRMADO", "form.completed": "FIRMADO", "completed": "FIRMADO",
        "submission.created": "ENVIADO", "form.viewed": "VISTO", "form.started": "VISTO",
        "submission.expired": "EXPIRADO", "form.declined": "RECHAZADO", "submission.archived": "ARCHIVADO",
    }
    status_label = status_map.get(str(event_type), "ENVIADO")

    logger.info(
        "DocuSeal webhook recibido",
        extra={"extra_data": {"event": event_type, "submission_id": submission_id, "status": status_label}},
    )

    for lead in LEADS_DATABASE:
        if submission_id is not None and str(lead.get("docuseal_id")) == str(submission_id):
            lead["docuseal_status"] = status_label
            lead["docuseal_updated_at"] = datetime.now(timezone.utc).isoformat()

    await publish_event({
        "type": "docuseal_update",
        "submission_id": submission_id,
        "status": status_label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "ok", "event": event_type, "docuseal_status": status_label}


@app.get("/api/calls", tags=["Persistencia"], dependencies=[require_auth])
async def get_calls():
    """Historial de llamadas de la sesión del servidor (memoria acotada)."""
    calls = list(CALLS_DATABASE)
    return {"calls": calls, "count": len(calls), "source": "memory_session"}


@app.post("/api/client-identify", tags=["Cliente & Portal"])
async def client_identify_endpoint(payload: ClientIdentifyModel, request: Request):
    """Registra la identificación del cliente: Google Sheets + notificación al dueño + panel.

    Público (formulario del portal) pero con consentimiento RGPD obligatorio,
    validación estricta y rate-limit por IP (5 / 10 min). La respuesta declara el
    estado REAL de cada destino (nunca "notificado" si el email quedó en cola).
    """
    if not identify_limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes. Inténtelo más tarde.")

    consent_at = datetime.now(timezone.utc).isoformat()
    sheets_sync = GoogleSheetsSync()
    lead_dict = {
        "nombre": payload.nombre,
        "telefono": payload.telefono,
        "email": payload.email,
        "empresa": payload.empresa or "Particular",
        "motivo": payload.motivo or "Identificación en portal web",
        "detalles": payload.detalles or "Identificación desde portal del cliente",
        "timestamp_lux": sheets_sync.get_luxembourg_now(),
        "agente": "Portal web",
        "consentimiento_rgpd": consent_at,
    }

    sync_res = await sheets_sync.sync_lead(lead_dict)
    email_res = await EmailNotifier().send_post_call_notification({
        "lead": lead_dict,
        "transcripcion": "Identificación directa de cliente desde el portal web.",
        "duration_seconds": 0,
        "duration_formatted": "Portal Web",
        "timestamp_lux": lead_dict["timestamp_lux"],
    })

    logger.info(
        "Cliente identificado en portal",
        extra={"extra_data": {
            "lead_id": sync_res.get("lead_id"),
            "phone": mask_phone(payload.telefono),
            "sheets_status": sync_res.get("status"),
            "email_status": email_res.get("status"),
        }},
    )

    LEADS_DATABASE.append(lead_dict)
    await publish_event({
        "type": "lead_created",
        "lead": lead_dict,
        "source": "client_portal_identification",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    sheets_ok = sync_res.get("status") in ("SYNCED_ONLINE", "ALREADY_SYNCED")
    email_ok = email_res.get("status") == "SENT"
    return {
        "status": "ok" if sheets_ok and email_ok else "partial",
        "message": "Identificación registrada" if sheets_ok else "Identificación recibida; pendiente de sincronizar",
        "lead_id": sync_res.get("lead_id"),
        "sheets_status": sync_res.get("status"),
        "email_status": email_res.get("status"),
    }


@app.get("/api/leads", tags=["Persistencia"], dependencies=[require_auth])
async def get_leads():
    """Leads leídos EN VIVO del Google Sheet. Sin datos simulados.

    - Sheet OK                -> source="google_sheets_live" (aunque esté vacío).
    - Sheet caído / sin config -> source="memory_session", degraded=true y el motivo:
      solo los leads de ESTA sesión del servidor; el panel lo muestra como aviso.
    """
    result = await GoogleSheetsSync().read_leads()
    if result.status == "ok":
        return {
            "leads": result.leads,
            "count": len(result.leads),
            "source": "google_sheets_live",
            "degraded": False,
            "rows_needing_repair": result.rows_needing_repair,
        }

    session_leads = list(LEADS_DATABASE)
    return {
        "leads": session_leads,
        "count": len(session_leads),
        "source": "memory_session",
        "degraded": True,
        "sheet_status": result.status,
        "sheet_error": result.error,
    }


class LeadStageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str = Field(..., pattern=r"^(nuevo|contactado|agendado|ganado)$")


@app.post("/api/leads/{lead_id}/stage", tags=["Persistencia"], dependencies=[require_auth])
async def update_lead_stage(lead_id: str, payload: LeadStageModel):
    """Cambia la etapa comercial de un lead directamente en el Google Sheet."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", lead_id):
        raise HTTPException(status_code=400, detail="lead_id inválido")
    try:
        result = await GoogleSheetsSync().update_stage(lead_id, payload.stage)
    except Exception as exc:
        logger.error("Error actualizando etapa en Sheets", extra={"extra_data": {"error": type(exc).__name__}})
        raise HTTPException(status_code=502, detail="Google Sheets no disponible; la etapa no se ha guardado.")
    if result == "not_configured":
        raise HTTPException(status_code=503, detail="Google Sheets no configurado; la etapa no se puede persistir.")
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Lead no encontrado en el Sheet.")
    return {"status": "updated", "lead_id": lead_id, "stage": payload.stage}


# En serverless el worker de voz escribe en SU disco, no en el de la función: este
# listado solo verá solicitudes si ambos comparten DATA_DIR (despliegue no serverless).
APPOINTMENT_REQUESTS_FILE = DATA_DIR / "appointment_requests.jsonl"


@app.get("/api/appointments", tags=["Agenda"], dependencies=[require_auth])
async def get_appointment_requests():
    """Solicitudes de cita registradas por el agente de voz (pendientes de confirmación)."""
    items: List[dict] = []
    try:
        lines = APPOINTMENT_REQUESTS_FILE.read_text(encoding="utf-8").splitlines()[-200:]
    except OSError:
        lines = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    items.reverse()
    return {"appointments": items, "count": len(items), "source": "agent_requests"}


@app.get("/api/system/internal", tags=["Métricas Internas & ROI"], dependencies=[require_auth])
async def get_internal_system_status():
    """Métricas internas de la sesión del servidor (estimaciones declaradas como tales)."""
    total_calls = len(CALLS_DATABASE)
    total_leads = len(LEADS_DATABASE)
    # Supuesto: cada llamada atendida ahorra 15 min de trabajo manual a ~22 €/h (recepcionista LU).
    horas_ahorradas = round((total_calls * 15) / 60, 2)

    queue_count = 0
    try:
        queue_count = len(json.loads((DATA_DIR / "leads_queue.json").read_text(encoding="utf-8")))
    except Exception:
        pass

    return {
        "sistema": "La Centralita NOC Internal Metrics",
        "ambito": "sesion_actual_del_servidor",
        "tiempo_ahorrado_horas": horas_ahorradas,
        "dinero_ahorrado_eur": round(horas_ahorradas * 22.0, 2),
        "costo_ia_acumulado_usd": round(total_calls * 0.00445, 4),
        "llamadas_totales_atendidas": total_calls,
        "leads_convertidos": total_leads,
        "leads_en_cola_sheets": queue_count,
        "supuestos": "15 min ahorrados por llamada a 22 €/h; coste IA estimado 0,00445 USD/llamada",
        "infraestructura": {
            "webrtc": "LiveKit Cloud",
            "stt": "Deepgram Nova-3",
            "llm": "DeepSeek V3 (Chat API)",
            "tts": f"{Config.TTS_PROVIDER} (respaldo: {Config.TTS_FALLBACK_PROVIDER})",
            "crm": "Google Sheets (Twenty CRM: pendiente)",
            "firmas": "DocuSeal",
        },
    }


@app.post("/api/test-webhook", tags=["Diagnóstico & Webhook"], dependencies=[require_auth])
async def test_webhook():
    """Ejecuta el pipeline post-llamada con una transcripción de PRUEBA.

    Marcado `simulated=true`: no escribe en el Sheet de producción, no envía
    email ni crea contratos; solo extrae el lead y envía el evento anonimizado a n8n.
    """
    sample_transcript = [
        {"role": "assistant", "text": "¡Hola! Gracias por llamar a WELUX en Luxemburgo. Soy Sofía, ¿en qué podemos asesorarte hoy?"},
        {"role": "user", "text": "Hola Sofía, me llamo Carlos Mendoza y busco cotizar un fotoespejo para un evento corporativo en Kirchberg el 18 de noviembre para 150 invitados. Mi teléfono es +352 691 000 000."},
        {"role": "assistant", "text": "¡Excelente, Carlos! Tomo nota y el equipo te enviará la propuesta."},
    ]
    result = await PostCallProcessor().process_call_ended(
        room_name="test-simulado-sofia",
        participant_id="test-carlos",
        duration_seconds=46.2,
        transcript_history=sample_transcript,
        metrics={"tipo": "simulacion_directa"},
        simulated=True,
    )
    event = {
        "type": "call_ended",
        "call_id": f"sim-{uuid.uuid4().hex[:8]}",
        "room": "test-simulado-sofia",
        "duration": "00:46",
        "transcript": result.get("full_transcript"),
    }
    save_calls_history(CALLS_DATABASE)
    await publish_event(event)
    return {
        "message": "Pipeline de prueba ejecutado (simulado: sin escritura en Sheets ni email)",
        "simulated": True,
        "n8n_delivered": result.get("n8n_delivered"),
        "lead": result.get("lead"),
    }


# ==============================================================================
# 5. CONTROL EN VIVO, PANTALLA DUAL & INTEGRACIÓN YCLOUD WHATSAPP
# ==============================================================================

class CallTransferRequest(BaseModel):
    call_id: Optional[str] = Field(default="", max_length=50)
    room: str = Field(default="centralita-test", max_length=50)
    target_operator: str = Field(..., min_length=2, max_length=100)
    target_phone: Optional[str] = Field(default=None, max_length=30)
    reason: Optional[str] = Field(default="Escalado a operador humano", max_length=200)


@app.post("/api/call/transfer", tags=["Control en Vivo"])
async def transfer_call_endpoint(payload: CallTransferRequest, authorization: str | None = Header(None)):
    """Transfiere una llamada en vivo a un operador o departamento humano real (H-002 protegido)."""
    verify_auth_header(authorization)
    # Sin integración SIP aún: se registra y difunde la SOLICITUD; no se finge una transferencia.
    logger.info(
        "Solicitud de transferencia de llamada",
        extra={"extra_data": {"action": "call_transfer_requested", "room": payload.room, "phone": mask_phone(payload.target_phone)}},
    )
    await monitor_hub.broadcast({
        "type": "call_transfer_requested",
        "room": payload.room,
        "call_id": payload.call_id,
        "target_operator": payload.target_operator,
        "target_phone": payload.target_phone,
        "reason": payload.reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "status": "transfer_requested",
        "note": "Transferencia SIP no implementada: un operador debe devolver la llamada manualmente.",
        "room": payload.room,
        "target_operator": payload.target_operator,
        "target_phone": payload.target_phone,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class CallHangupRequest(BaseModel):
    room: str = Field(default="centralita-test", max_length=50)
    reason: Optional[str] = Field(default="Finalizada por operador", max_length=100)


@app.post("/api/call/hangup", tags=["Control en Vivo"])
async def hangup_call_endpoint(payload: CallHangupRequest, authorization: str | None = Header(None)):
    """Finaliza y cuelga de forma segura una llamada activa desde el panel."""
    verify_auth_header(authorization)
    if payload.room not in ALLOWED_ROOMS:
        raise HTTPException(status_code=403, detail="Sala no autorizada")
    terminated = False
    if Config.LIVEKIT_API_KEY and Config.LIVEKIT_API_SECRET and Config.LIVEKIT_URL:
        lk = api.LiveKitAPI(Config.LIVEKIT_URL.replace("wss://", "https://"), Config.LIVEKIT_API_KEY, Config.LIVEKIT_API_SECRET)
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=payload.room))
            terminated = True
        except Exception as exc:
            logger.warning("No se pudo cerrar la sala LiveKit", extra={"extra_data": {"error": type(exc).__name__}})
        finally:
            await lk.aclose()
    if not terminated:
        raise HTTPException(status_code=503, detail="No se pudo colgar: LiveKit no configurado o no responde.")
    logger.info("Llamada colgada desde el panel", extra={"extra_data": {"room": payload.room}})
    await monitor_hub.broadcast({
        "type": "call_ended",
        "room": payload.room,
        "status": "terminated",
        "reason": payload.reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "terminated", "room": payload.room}


class CallUpdateLeadRequest(BaseModel):
    lead_id: Optional[str] = Field(default="", max_length=50)
    nombre: Optional[str] = Field(default=None, max_length=100)
    telefono: Optional[str] = Field(default=None, max_length=30)
    empresa: Optional[str] = Field(default=None, max_length=100)
    motivo: Optional[str] = Field(default=None, max_length=150)
    detalles: Optional[str] = Field(default=None, max_length=1000)
    notas_operador: Optional[str] = Field(default=None, max_length=1000)
    valor_eur: Optional[str] = Field(default=None, max_length=50)
    stage: Optional[str] = Field(default=None, pattern=r"^(nuevo|contactado|agendado|ganado)$")


@app.post("/api/call/update-lead", tags=["Control en Vivo"])
async def update_lead_live_endpoint(payload: CallUpdateLeadRequest, authorization: str | None = Header(None)):
    """Actualiza los datos del lead en tiempo real durante la llamada desde el lado interno del operador."""
    verify_auth_header(authorization)
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    
    # Lead existente: solo se actualiza la etapa (no se añaden filas por cada edición).
    # Lead nuevo: se escribe una vez con ID estable (idempotente).
    sheets_status = "skipped"
    sheets_sync = GoogleSheetsSync()
    if payload.lead_id and payload.stage:
        sheets_status = await sheets_sync.update_stage(payload.lead_id, payload.stage)
    elif not payload.lead_id and (payload.nombre or payload.telefono):
        sheets_status = (await sheets_sync.sync_lead(data)).get("status", "error")

    await monitor_hub.broadcast({
        "type": "lead_updated_live",
        "lead": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "ok", "sheets_status": sheets_status, "lead": data}


class AuthVerifyRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=200)


@app.post("/api/auth/verify", tags=["Seguridad"])
async def auth_verify_endpoint(payload: AuthVerifyRequest, request: Request):
    """Verifica el token del operador (tiempo constante, máx. 10 intentos / 10 min por IP)."""
    if not auth_verify_limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Inténtelo más tarde.")
    if is_valid_token(payload.token):
        return {"authenticated": True, "role": "operator", "timestamp": datetime.now(timezone.utc).isoformat()}
    raise HTTPException(status_code=401, detail="Token de acceso no válido")


def verify_whatsapp_signature(raw: bytes, headers) -> bool:
    """Firma HMAC-SHA256 del webhook: Meta (X-Hub-Signature-256: sha256=<hex>) o
    YCloud (YCloud-Signature: t=<ts>,s=<hex> sobre "<ts>.<body>"). Fail-closed sin secreto."""
    secret = Config.WHATSAPP_WEBHOOK_SECRET
    if not secret:
        return False
    key = secret.encode()
    meta = headers.get("x-hub-signature-256", "")
    if meta.startswith("sha256="):
        expected = hmac.new(key, raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(meta[7:], expected)
    ycloud = headers.get("ycloud-signature", "")
    if ycloud:
        parts = dict(p.split("=", 1) for p in ycloud.split(",") if "=" in p)
        ts, sig = parts.get("t", ""), parts.get("s", "")
        if not ts.isdigit() or abs(time.time() - int(ts)) > 300:
            return False
        expected = hmac.new(key, f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    return False


@app.get("/api/whatsapp/webhook", tags=["WhatsApp & YCloud"])
async def whatsapp_webhook_verification(
    request: Request,
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
):
    """Verificación de suscripción para webhook de YCloud / Meta WhatsApp Business API."""
    expected_token = Config.WHATSAPP_VERIFY_TOKEN
    if not expected_token:
        raise HTTPException(status_code=503, detail="Webhook WhatsApp no configurado (WHATSAPP_VERIFY_TOKEN).")
    if hub_mode == "subscribe" and hub_verify_token and hmac.compare_digest(hub_verify_token.encode(), expected_token.encode()):
        logger.info("Webhook de WhatsApp verificado con éxito por challenge")
        return Response(content=hub_challenge or "OK", media_type="text/plain")
    return {
        "status": "active",
        "service": "YCloud WhatsApp Business API Webhook",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/whatsapp/webhook", tags=["WhatsApp & YCloud"])
async def whatsapp_webhook_handler(request: Request):
    """Manejo de eventos entrantes de WhatsApp vía YCloud: mensajes de texto, notas de voz y estado de entrega."""
    raw = await request.body()
    if not verify_whatsapp_signature(raw, request.headers):
        logger.warning("Webhook WhatsApp rechazado", extra={"extra_data": {"security_event": "whatsapp_bad_signature"}})
        raise HTTPException(status_code=401, detail="Firma del webhook inválida")
    try:
        body = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Formato JSON no válido")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Formato JSON no válido")

    logger.info("Evento WhatsApp recibido", extra={"extra_data": {"event_type": body.get("type", "meta")}})

    # Soporte para formato nativo YCloud y formato estándar Meta
    event_type = body.get("type") or "whatsapp.event"
    wa_msg = body.get("whatsappMessage") or {}

    if "entry" in body and isinstance(body["entry"], list):
        for entry in body["entry"]:
            for change in entry.get("changes", []):
                val = change.get("value", {})
                messages = val.get("messages", [])
                for m in messages:
                    wa_msg = {
                        "id": m.get("id"),
                        "from": m.get("from"),
                        "type": m.get("type"),
                        "text": m.get("text", {}),
                        "audio": m.get("audio", {}),
                        "timestamp": m.get("timestamp"),
                    }
                    event_type = "whatsapp.inbound_message"

    sender = wa_msg.get("from") or "Remitente"
    msg_type = wa_msg.get("type") or "text"
    text_content = ""
    is_voice = False
    media_url = None

    if msg_type == "text":
        text_content = (wa_msg.get("text") or {}).get("body", "")
    elif msg_type in ("audio", "voice"):
        is_voice = True
        audio_info = wa_msg.get("audio") or {}
        media_url = audio_info.get("link") or audio_info.get("id")
        text_content = "[Nota de voz recibida - lista para transcripción Deepgram Nova-3]"

    # Retransmitir al panel y monitor
    await monitor_hub.broadcast({
        "type": "whatsapp_incoming",
        "sender": sender,
        "message_type": msg_type,
        "is_voice": is_voice,
        "text": text_content,
        "media_url": media_url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "status": "received",
        "event_type": event_type,
        "sender": sender,
        "type": msg_type,
        "is_voice": is_voice,
    }


# Servir el monitor web del cliente en /panel y página de prueba en /web
if PANEL_DIR.exists():
    app.mount("/panel", StaticFiles(directory=PANEL_DIR, html=True), name="panel")

if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host=Config.HOST, port=Config.PORT, reload=False)
