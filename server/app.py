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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

# Constantes de seguridad y autenticación (H-002, H-003, H-004)
ALLOWED_ROOMS = {"centralita-test", "centralita-demo"}
ALLOWED_EVENT_KEYS = {"type", "call_id", "timestamp", "status", "room", "duration", "transcript", "lead", "agent"}


def get_authorized_tokens() -> set[str]:
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
        logger.info(f"Monitor conectado al backend (Total: {len(self.active_connections)})")
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
        "livekit_url": Config.LIVEKIT_URL or "No configurado",
        "n8n_webhook": Config.N8N_WEBHOOK_URL or "No configurado",
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
        logger.warning(f"Rechazo de conexión WebSocket no autorizada (H-004)")
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


@app.post("/api/call-event")
async def receive_call_event(
    event: dict,
    authorization: str | None = Header(None),
):
    """Recibe eventos del worker de voz con autenticación y validación de claves (H-003)."""
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

    event_type = event["type"]
    event["server_timestamp"] = datetime.now(timezone.utc).isoformat()

    if event_type == "call_ended":
        CALLS_DATABASE.append(event)
        if event.get("lead") and isinstance(event.get("lead"), dict):
            LEADS_DATABASE.append(event["lead"])

    # Difundir en vivo a todos los monitores web del cliente
    await monitor_hub.broadcast(event)
    return {"status": "broadcasted", "receivers": len(monitor_hub.active_connections)}


@app.post("/api/docuseal/webhook")
async def docuseal_webhook(payload: dict):
    """Webhook para recibir eventos de contratos DocuSeal (firma completada, enviado, visto)."""
    event_type = payload.get("event_type") or payload.get("type", "submission.updated")
    submission = payload.get("data") or payload.get("submission") or payload
    submission_id = submission.get("id") or submission.get("submission_id")
    status = "FIRMADO" if event_type in ("submission.completed", "completed") else "ENVIADO"

    logger.info(f"DocuSeal webhook recibido: evento={event_type}, id={submission_id}, status={status}")

    # Actualizar estado en memoria
    for lead in LEADS_DATABASE:
        if lead.get("docuseal_id") == submission_id or lead.get("id") == submission_id:
            lead["docuseal_status"] = status
            lead["docuseal_signed_at"] = datetime.now(timezone.utc).isoformat()

    # Notificar a los paneles conectados vía WebSocket
    await monitor_hub.broadcast({
        "type": "docuseal_update",
        "submission_id": submission_id,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "ok", "event": event_type, "docuseal_status": status}


@app.get("/api/calls")
async def get_calls():
    """Devuelve el historial de llamadas registradas."""
    return {"calls": CALLS_DATABASE, "count": len(CALLS_DATABASE)}


@app.get("/api/leads")
async def get_leads():
    """Devuelve la bandeja de leads extraídos con sincronización desde persistencia."""
    leads = list(LEADS_DATABASE)
    # Si la lista en memoria está vacía, intentar hidratar desde la cola y caché local
    if not leads:
        queue_path = PROJECT_ROOT / "data" / "leads_queue.json"
        synced_path = PROJECT_ROOT / "data" / "leads_synced.json"
        if queue_path.exists():
            try:
                with open(queue_path, "r", encoding="utf-8") as f:
                    q_data = json.load(f)
                    for item in q_data:
                        lead = item.get("lead")
                        if lead and lead not in leads:
                            leads.append(lead)
            except Exception:
                pass
        if synced_path.exists():
            try:
                with open(synced_path, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                    for _, val in s_data.items():
                        lead_info = val.get("data")
                        if lead_info and isinstance(lead_info, list) and len(lead_info) >= 8:
                            lead_obj = {
                                "id": lead_info[0],
                                "timestamp_lux": lead_info[1],
                                "nombre": lead_info[2],
                                "telefono": lead_info[3],
                                "email": lead_info[4],
                                "empresa": lead_info[5],
                                "motivo": lead_info[6],
                                "detalles": lead_info[7],
                                "valor_eur": lead_info[8] if len(lead_info) > 8 else "2.500 €",
                                "docuseal_status": lead_info[10] if len(lead_info) > 10 else "BORRADOR",
                            }
                            if not any(l.get("id") == lead_obj["id"] for l in leads):
                                leads.append(lead_obj)
            except Exception:
                pass
    return {"leads": leads, "count": len(leads)}


@app.get("/api/system/internal")
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


@app.post("/api/test-webhook")
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
