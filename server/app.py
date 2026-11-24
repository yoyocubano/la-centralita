"""Servidor Backend de La Centralita (WELUX Events).

Responsabilidades:
  - Generación de tokens JWT seguros para LiveKit Cloud (WebRTC).
  - Healthcheck y diagnóstico de credenciales del pipeline.
  - WebSocket Hub (/ws/monitor) para retransmisión en tiempo real al Monitor del Cliente.
  - Endpoints REST (/api/calls, /api/leads) para historial persistente y sincronización.
  - Despacho y verificación de webhooks hacia n8n.
"""

import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from livekit import api

from agent.config import Config
from agent.post_call import PostCallProcessor

logger = logging.getLogger("la-centralita.server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="La Centralita - Backend & Event Hub (WELUX)")

# CORS middleware restringido (H-001)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://yoyocubano.github.io",
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

# Cabeceras de seguridad (endurecimiento): el servidor no enviaba
# ninguna; un navegador que cargara el panel o consumiera la API no
# tenía protección contra MIME-sniffing ni clickjacking. Middleware
# ASGI puro (sin Starlette) para que funcione con FastAPI y siga siendo
# importable en los tests sin claves, donde fastapi es un stub y
# add_middleware es un no-op.
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "no-referrer",
}
API_NO_STORE_PREFIXES = ("/api/", "/ws/")


class SecurityHeadersMiddleware:
    """Añade cabeceras de seguridad a cada respuesta HTTP.

    - X-Content-Type-Options: nosniff — el navegador no interpreta un
      JSON como HTML/JS aunque el Content-Type venga mal.
    - X-Frame-Options: SAMEORIGIN — el panel no se puede incrustar en
      un iframe de otro origen (clickjacking); en el mismo origen sí.
    - Referrer-Policy: no-referrer — la URL del panel no se filtra en
      las peticiones que salen de él.
    - Cache-Control: no-store solo en /api/* y /ws/*: las respuestas
      de la API y del monitor llevan datos vivos y no deben quedar en
      cachés intermedias; los archivos estáticos del panel conservan
      su caché normal.

    No pisa una cabecera que la respuesta ya traiga (p. ej. si un día
    StaticFiles o FastAPI fijan una): la existente manda. Las
    conexiones WebSocket pasan sin tocarse.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        needs_no_store = path.startswith(API_NO_STORE_PREFIXES)

        async def send_with_headers(message):
            if message.get("type") == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])]
                present = {k.lower() for k, _ in headers}
                for name, value in SECURITY_HEADERS.items():
                    if name.encode("latin-1") not in present:
                        headers.append(
                            (name.encode("latin-1"), value.encode("latin-1"))
                        )
                if needs_no_store and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestBodySizeLimitMiddleware:
    """Rechaza (413) cuerpos HTTP desproporcionados antes de parsearlos.

    - Solo /api/* con método POST/PUT/PATCH: el panel estático no
      recibe cuerpos y los GET no llevan.
    - Dos defensas: si Content-Length ya declara más del tope, 413
      inmediato sin leer nada; si no, cuenta los bytes mientras lee
      (cubre chunked) y 413 en cuanto pasa del tope.
    - La app interior nunca se invoca con un cuerpo rechazado y
      recibe el cuerpo intacto cuando pasa (replay del búfer).

    ASGI puro (sin Starlette), como SecurityHeadersMiddleware, para
    seguir siendo importable en los tests sin claves.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if method not in ("POST", "PUT", "PATCH") or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > MAX_REQUEST_BODY_BYTES:
                    await self._send_413(send)
                    return
            except ValueError:
                # Content-Length no numérico: no fiarse de él; el
                # conteo real de abajo manda (h11/uvicorn además lo
                # rechaza antes de llegar aquí en despliegue real).
                pass
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BODY_BYTES:
                await self._send_413(send)
                return
            if not message.get("more_body", False):
                break
        sent = False

        async def receive_replay():
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": bytes(body),
                    "more_body": False,
                }
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, receive_replay, send)

    async def _send_413(self, send):
        payload = json.dumps(
            {
                "detail": (
                    "Cuerpo de la petición demasiado grande "
                    f"(máximo {MAX_REQUEST_BODY_BYTES} bytes)."
                )
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


# Se registra ANTES que el de cabeceras: en Starlette el último
# middleware registrado es el más externo, así el 413 de este
# middleware también sale con las cabeceras de seguridad.
app.add_middleware(RequestBodySizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Constantes de seguridad y autenticación (H-002, H-003, H-004)
ALLOWED_ROOMS = {"centralita-test", "centralita-demo"}
ALLOWED_EVENT_KEYS = {"type", "call_id", "timestamp", "status", "room", "duration", "transcript", "lead", "agent"}

# Límites de memoria del servidor (endurecimiento): los historiales en
# memoria y el registro de rate-limit no pueden crecer sin límite; un
# cliente que emita muchos eventos, o muchas IPs distintas pidiendo el
# token público, no debe agotar la RAM del proceso.
MAX_STORED_CALLS = 500
MAX_STORED_LEADS = 500
PUBLIC_TOKEN_LOG_MAX_IPS = 1000

# Límites de tamaño de entrada (endurecimiento): un solo evento gigante
# puede agotar la RAM aunque los historiales tengan tope (500 registros
# enormes siguen siendo enormes), y un nombre gigante infla el JWT del
# token. Sin estos topes, un cliente autenticado (o cualquiera en el
# endpoint público) puede tumbar el proceso con un solo POST.
MAX_EVENT_JSON_BYTES = 65536  # 64 KiB por evento de llamada
MAX_STRING_FIELD_CHARS = 8192  # 8 KiB por campo de texto del evento
MAX_LIST_ITEMS = 200  # p. ej. turnos máximos de una transcripción
MAX_NAME_CHARS = 100  # nombre/identidad máximos en los tokens

# Límite de tamaño del cuerpo HTTP (endurecimiento): la validación de
# arriba mide el evento DESPUÉS de parsear el JSON, así que un cuerpo
# gigante ya estaba en memoria antes de rechazarlo. El middleware
# RequestBodySizeLimitMiddleware mide el cuerpo ANTES de que FastAPI lo
# parsee: si pasa de 64 KiB responde 413 sin invocar la app. Lee el
# cuerpo en un búfer acotado (como mucho el tope + 1 byte) y lo
# reinyecta, así el límite vale igual con Content-Length y con chunked.
MAX_REQUEST_BODY_BYTES = 65536  # 64 KiB, igual que MAX_EVENT_JSON_BYTES

# Paginación del historial (endurecimiento/rendimiento): /api/calls y
# /api/leads devolvían el historial COMPLETO en cada GET: con el tope
# de 500 registros y eventos de hasta 64 KiB, una sola respuesta podía
# rozar los 32 MB. Ahora devuelven una página (por defecto las 50 más
# recientes) con el total, y limit/offset validados.
DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = 100

# Rate-limit de /api/call-event (endurecimiento): el endpoint es el
# único de escritura autenticado sin tope por IP. Un worker en bucle,
# un reintento desbocado o un token filtrado puede inundar el hub: cada
# evento se difunde a todos los monitores y se guarda en el historial.
# 60 eventos por minuto y por IP es generoso (una llamada normal emite
# un puñado: inicio, turnos, fin); al pasarlo, 429. La ventana es de
# 60 segundos y el registro tiene el mismo tope de IPs que el del
# token público (1000): ni el registro puede crecer sin límite.
CALL_EVENT_MAX_PER_MINUTE = 60
CALL_EVENT_WINDOW_SECONDS = 60
CALL_EVENT_LOG_MAX_IPS = 1000

# Límite de conexiones WebSocket simultáneas en el monitor (endurecimiento):
# cada monitor queda abierto de forma indefinida y recibe cada broadcast;
# un bucle de reconexión o un token filtrado no debe agotar descriptores ni RAM.
MAX_MONITOR_CONNECTIONS = 50


def get_authorized_tokens() -> set[str]:
    """Conjunto de tokens válidos para autenticación interna.

    LC-0: las credenciales salen SOLO del entorno del servidor
    (CENTRALITA_AUTH_TOKEN y, como credencial interna de máquina,
    LIVEKIT_API_SECRET). Ningún token válido vive en el código: el
    literal que antes se aceptaba aquí quedó revocado al retirarlo.
    """
    tokens = {
        Config.AUTH_TOKEN,
        Config.LIVEKIT_API_SECRET,
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
    if not _is_authorized(token):
        raise HTTPException(status_code=403, detail="Token no autorizado")
    return True


def _is_authorized(token: str) -> bool:
    """Comparación en tiempo constante contra el conjunto de tokens válidos."""
    return any(secrets.compare_digest(token, valid) for valid in get_authorized_tokens())

# Almacenamiento en memoria para llamadas y leads de la sesión activa.
# Con tope: solo se conservan los N registros más recientes (ver
# _append_capped); sin tope, un servidor de larga duración crece sin
# límite con cada llamada y cada lead.
CALLS_DATABASE: List[dict] = []
LEADS_DATABASE: List[dict] = []


def _append_capped(database: List[dict], item: dict, max_items: int) -> None:
    """Añade un registro y recorta por la cola: solo quedan los N más recientes."""
    database.append(item)
    if len(database) > max_items:
        del database[:-max_items]


def _check_value_size(value, depth: int = 0) -> None:
    """Rechaza (400) campos de texto o listas desproporcionados, recursivo.

    Cubre la transcripción (lista de turnos con texto) y el lead (dict
    con nombre, teléfono, detalles...): ningún campo puede pasar de
    MAX_STRING_FIELD_CHARS ni ninguna lista de MAX_LIST_ITEMS, y la
    anidación no pasa de 4 niveles.
    """
    if depth > 4:
        raise HTTPException(status_code=400, detail="Payload demasiado anidado.")
    if isinstance(value, str):
        if len(value) > MAX_STRING_FIELD_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"Campo de texto demasiado largo (máximo {MAX_STRING_FIELD_CHARS} caracteres).",
            )
    elif isinstance(value, list):
        if len(value) > MAX_LIST_ITEMS:
            raise HTTPException(
                status_code=400,
                detail=f"Lista demasiado larga (máximo {MAX_LIST_ITEMS} elementos).",
            )
        for item in value:
            _check_value_size(item, depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            _check_value_size(item, depth + 1)


def _validate_event_payload(event: dict) -> None:
    """Rechaza eventos de llamada desproporcionados antes de guardarlos.

    Primero el tamaño total serializado (413 si pasa de 64 KiB) y luego
    campo por campo (400). Se llama después de la whitelist de claves:
    un evento con claves no permitidas sigue siendo 400 por ese motivo.
    """
    try:
        size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Evento no serializable como JSON.")
    if size > MAX_EVENT_JSON_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Evento demasiado grande (máximo {MAX_EVENT_JSON_BYTES} bytes).",
        )
    _check_value_size(event)


def _validate_token_name(name: str, field: str = "name") -> None:
    """Rechaza (400) nombres/identidades que inflarían el JWT del token."""
    if len(name) > MAX_NAME_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"El campo '{field}' es demasiado largo (máximo {MAX_NAME_CHARS} caracteres).",
        )


def _validate_pagination(limit, offset) -> tuple[int, int]:
    """Valida limit/offset de /api/calls y /api/leads (400 si no valen).

    - limit: entero entre 1 y MAX_HISTORY_LIMIT (100). Sin tope, un
      cliente podría pedir los 500 registros de una tacada y llevarse
      una respuesta de decenas de MB.
    - offset: entero mayor o igual que 0.
    """
    try:
        limit = int(limit)
        offset = int(offset)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Los parámetros 'limit' y 'offset' deben ser números enteros.",
        )
    if limit < 1 or limit > MAX_HISTORY_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"El parámetro 'limit' debe estar entre 1 y {MAX_HISTORY_LIMIT}.",
        )
    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail="El parámetro 'offset' no puede ser negativo.",
        )
    return limit, offset


def _paginate_history(items, limit: int, offset: int):
    """Página del historial, de la más reciente a la más antigua.

    El monitor quiere lo último, no lo primero: la página por defecto
    (limit 50, offset 0) son los 50 registros más recientes. No muta
    la lista original (reversed sobre una copia).
    """
    recientes = list(reversed(items))
    return recientes[offset:offset + limit]


class MonitorConnectionManager:
    """Administra las conexiones WebSocket con los paneles de monitorización de los clientes."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> bool:
        if len(self.active_connections) >= MAX_MONITOR_CONNECTIONS:
            logger.warning(
                f"Límite de monitores conectados alcanzado ({MAX_MONITOR_CONNECTIONS}); rechazando conexión WebSocket"
            )
            await websocket.close(
                code=1013,
                reason=f"Demasiados monitores conectados (máximo {MAX_MONITOR_CONNECTIONS}).",
            )
            return False

        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Monitor conectado al backend (Total: {len(self.active_connections)})")
        # Enviar estado inicial
        await websocket.send_json({
            "type": "connection_established",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "ready" if all(Config.validate().values()) else "standby",
            "message": "Conectado al bus de eventos de La Centralita",
        })
        return True

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Monitor desconectado (Restantes: {len(self.active_connections)})")

    async def broadcast(self, message: dict):
        """Difunde un evento en streaming a todos los monitores web conectados."""
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Error al enviar mensaje a monitor: {e}")
                self.disconnect(connection)


monitor_hub = MonitorConnectionManager()


@app.get("/api/status")
async def get_status():
    """Devuelve el estado de las credenciales y servicios del sistema."""
    checks = Config.validate()
    return {
        "status": "ready" if all(checks.values()) else "needs_configuration",
        "services": checks,
        # LC-2: el estado público no revela URLs (la del webhook de n8n es
        # una URL-capacidad: quien la conoce puede inyectar leads falsos).
        "livekit_configured": bool(Config.LIVEKIT_URL),
        "n8n_configured": bool(Config.N8N_WEBHOOK_URL),
        "monitors_connected": len(monitor_hub.active_connections),
    }


@app.get("/api/token")
async def get_token(
    room: str = Query(default="centralita-test"),
    identity: str = Query(default=""),
    name: str = Query(default="Cliente Web"),
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

    # 3. Acotar nombre e identidad: un texto gigante infla el JWT.
    _validate_token_name(identity, "identity")
    _validate_token_name(name, "name")

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


# ---------------------------------------------------------------------------
# Emisión pública de tokens para la página web de llamadas (LC-0).
# La página pública no puede guardar la credencial maestra: este endpoint
# emite tokens de LiveKit de corta duración (10 min), solo para las salas
# permitidas y con límite de emisión por IP. La credencial maestra sigue
# siendo necesaria para /api/token, el monitor y los eventos.
# ---------------------------------------------------------------------------
PUBLIC_TOKEN_TTL_MINUTES = 10
PUBLIC_TOKEN_MAX_PER_HOUR = 30
_public_token_log: dict[str, list[float]] = {}
_call_event_log: dict[str, list[float]] = {}


def _check_rate_limit(log, client_ip, max_per_window, window_seconds, max_ips, detail):
    """Tope por IP y por ventana deslizante, con poda y tope de IPs.

    - Las marcas fuera de la ventana no cuentan; las IPs sin actividad
      en la ventana se podan del registro (no crece con IPs que no vuelven).
    - Si la IP ya llegó al tope dentro de la ventana: 429.
    - Con el registro lleno de IPs, entra la nueva y sale la de
      actividad más antigua: el registro nunca pasa de max_ips.
    """
    now = time.monotonic()
    for ip in [ip for ip, stamps in log.items()
               if not stamps or now - stamps[-1] >= window_seconds]:
        del log[ip]
    recent = [t for t in log.get(client_ip, []) if now - t < window_seconds]
    if len(recent) >= max_per_window:
        raise HTTPException(status_code=429, detail=detail)
    recent.append(now)
    if client_ip not in log and len(log) >= max_ips:
        # Registro lleno: expulsar la IP con la actividad más antigua.
        oldest_ip = min(
            log,
            key=lambda ip: log[ip][-1] if log[ip] else 0,
        )
        del log[oldest_ip]
    log[client_ip] = recent


def _check_public_token_rate_limit(client_ip: str) -> None:
    _check_rate_limit(
        _public_token_log, client_ip,
        PUBLIC_TOKEN_MAX_PER_HOUR, 3600, PUBLIC_TOKEN_LOG_MAX_IPS,
        "Demasiadas solicitudes de token desde esta conexión. Inténtalo de nuevo más tarde.",
    )


def _check_call_event_rate_limit(client_ip: str) -> None:
    _check_rate_limit(
        _call_event_log, client_ip,
        CALL_EVENT_MAX_PER_MINUTE, CALL_EVENT_WINDOW_SECONDS, CALL_EVENT_LOG_MAX_IPS,
        "Demasiados eventos de llamada desde esta conexión. Inténtalo de nuevo más tarde.",
    )


@app.get("/api/token/public")
async def get_public_token(
    request: Request,
    room: str = Query(default="centralita-test"),
    name: str = Query(default="Cliente Web"),
):
    """Emite un token de LiveKit efímero para la página pública de llamadas."""
    if room not in ALLOWED_ROOMS:
        raise HTTPException(
            status_code=403,
            detail=f"Acceso denegado: la sala '{room}' no está autorizada. Salas permitidas: {', '.join(sorted(ALLOWED_ROOMS))}",
        )
    # El nombre se valida antes del rate-limit: una petición inválida
    # no consume cuota de la IP.
    _validate_token_name(name, "name")
    _check_public_token_rate_limit(request.client.host if request.client else "desconocida")
    if not Config.LIVEKIT_API_KEY or not Config.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=503,
            detail="LIVEKIT_API_KEY y LIVEKIT_API_SECRET no están configuradas en el servidor.",
        )
    client_id = f"cliente-{uuid.uuid4().hex[:6]}"
    token = (
        api.AccessToken(Config.LIVEKIT_API_KEY, Config.LIVEKIT_API_SECRET)
        .with_identity(client_id)
        .with_name(name)
        .with_ttl(timedelta(minutes=PUBLIC_TOKEN_TTL_MINUTES))
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
    return {"token": token, "url": Config.LIVEKIT_URL, "room": room, "identity": client_id}


@app.websocket("/ws/monitor")
async def monitor_websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    """Canal bidireccional WebSocket para el Monitor del Cliente con validación de token (H-004)."""
    if not token or not _is_authorized(token):
        logger.warning(f"Rechazo de conexión WebSocket no autorizada (H-004)")
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if not await monitor_hub.connect(websocket):
        return
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


@app.post("/api/call-event")
async def receive_call_event(
    event: dict,
    request: Request = None,
    authorization: str | None = Header(None),
):
    """Recibe eventos del worker de voz con autenticación, validación de claves y rate-limit por IP (H-003)."""
    # 1. Requerir header Authorization
    verify_auth_header(authorization)

    # 2. Validar estructura del evento con whitelist de claves
    if not isinstance(event, dict) or "type" not in event:
        raise HTTPException(status_code=400, detail="Estructura de evento inválida. El campo 'type' es requerido.")

    extra_keys = set(event.keys()) - ALLOWED_EVENT_KEYS
    if extra_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Payload contiene claves no permitidas: {', '.join(sorted(extra_keys))}",
        )

    # 3. Validar tamaño de entrada: un solo evento gigante no debe
    # poder agotar la RAM aunque los historiales tengan tope.
    _validate_event_payload(event)

    # 4. Rate-limit por IP: un emisor autenticado en bucle (worker con
    # bug, reintento desbocado, token filtrado) no debe poder inundar
    # el hub: cada evento se difunde a todos los monitores y se guarda.
    # Va DESPUÉS de validar: una petición inválida (400) no consume
    # cuota, igual que en el token público. Solo se aplica cuando hay
    # una IP real de cliente: sin request (llamada directa, como en
    # las suites viejas) no hay IP a la que atribuir el consumo y
    # todas las llamadas compartirían un mismo cubo — en HTTP real
    # FastAPI siempre inyecta el request, así que el tope cubre todo
    # el tráfico de verdad.
    client_ip = request.client.host if request is not None and request.client else None
    if client_ip:
        _check_call_event_rate_limit(client_ip)

    event_type = event["type"]
    event["server_timestamp"] = datetime.now(timezone.utc).isoformat()

    if event_type == "call_ended":
        _append_capped(CALLS_DATABASE, event, MAX_STORED_CALLS)
        if event.get("lead") and isinstance(event.get("lead"), dict):
            _append_capped(LEADS_DATABASE, event["lead"], MAX_STORED_LEADS)

    # Difundir en vivo a todos los monitores web del cliente
    await monitor_hub.broadcast(event)
    return {"status": "broadcasted", "receivers": len(monitor_hub.active_connections)}


@app.get("/api/calls")
async def get_calls(
    authorization: str | None = Header(None),
    limit: int = Query(default=DEFAULT_HISTORY_LIMIT),
    offset: int = Query(default=0),
):
    """Devuelve el historial de llamadas registradas (LC-1: exige autenticación).

    Paginado: por defecto las 50 llamadas más recientes; 'limit' entre
    1 y 100, 'offset' desde 0. La respuesta trae la página en 'calls',
    'count' (registros en esta página), 'total' (registros en el
    historial), 'limit' y 'offset'. Antes devolvía el historial
    completo: hasta 500 registros de 64 KiB en una sola respuesta.
    """
    verify_auth_header(authorization)
    limit, offset = _validate_pagination(limit, offset)
    pagina = _paginate_history(CALLS_DATABASE, limit, offset)
    return {
        "calls": pagina,
        "count": len(pagina),
        "total": len(CALLS_DATABASE),
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/leads")
async def get_leads(
    authorization: str | None = Header(None),
    limit: int = Query(default=DEFAULT_HISTORY_LIMIT),
    offset: int = Query(default=0),
):
    """Devuelve la bandeja de leads extraídos (LC-1: exige autenticación).

    Paginado igual que /api/calls: por defecto los 50 leads más
    recientes; 'limit' entre 1 y 100, 'offset' desde 0. La respuesta
    trae la página en 'leads', 'count', 'total', 'limit' y 'offset'.
    """
    verify_auth_header(authorization)
    limit, offset = _validate_pagination(limit, offset)
    pagina = _paginate_history(LEADS_DATABASE, limit, offset)
    return {
        "leads": pagina,
        "count": len(pagina),
        "total": len(LEADS_DATABASE),
        "limit": limit,
        "offset": offset,
    }


@app.post("/api/test-webhook")
async def test_webhook(authorization: str | None = Header(None)):
    """Envía un lead de prueba simulado directamente a n8n para verificar el flujo (LC-3: exige autenticación)."""
    verify_auth_header(authorization)
    processor = PostCallProcessor()
    sample_transcript = [
        {"role": "assistant", "text": "¡Hola! Gracias por llamar a WELUX Events en Luxemburgo. Soy Sofía, ¿en qué podemos asesorarte hoy?"},
        {"role": "user", "text": "Hola Sofía, me llamo Carlos Mendoza y busco cotizar iluminación y DJ para una gala corporativa en Kirchberg el 18 de noviembre para 150 invitados. Mi teléfono es +352 691 452 890."},
        {"role": "assistant", "text": "¡Qué maravilla de evento, Carlos! Tenemos equipos de audio line-array y diseño de iluminación ideales para salones en Kirchberg. Nuestro equipo técnico te contactará hoy mismo con el dossier detallado."},
    ]
    result = await processor.process_call_ended(
        room_name="test-simulado-sofia",
        participant_id="test-carlos",
        duration_seconds=46.2,
        transcript_history=sample_transcript,
        metrics={"tipo": "simulacion_directa"},
    )
    # Notificar a los monitores conectados
    await monitor_hub.broadcast({
        "type": "call_ended",
        "room": "test-simulado-sofia",
        "duration": "00:46",
        "transcript": sample_transcript,
        "lead": result.get("lead"),
    })
    return {"message": "Webhook de prueba enviado a n8n", "payload": result}


# Servir el monitor web del cliente en /panel
if PANEL_DIR.exists():
    app.mount("/panel", StaticFiles(directory=PANEL_DIR, html=True), name="panel")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host=Config.HOST, port=Config.PORT, reload=True)
