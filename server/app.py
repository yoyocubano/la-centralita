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

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from livekit import api

from agent.config import Config
from agent.post_call import PostCallProcessor

logger = logging.getLogger("la-centralita.server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="La Centralita - Backend & Event Hub (WELUX)")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PANEL_DIR = PROJECT_ROOT / "panel"
WEB_DIR = PROJECT_ROOT / "web"

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
):
    """Genera un token JWT de LiveKit para que el navegador se una a la sala."""
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
async def monitor_websocket_endpoint(websocket: WebSocket):
    """Canal bidireccional WebSocket para el Monitor del Cliente."""
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
async def receive_call_event(event: dict):
    """Recibe eventos del worker de voz (inicio, delta de transcripción, fin) y los difunde a los monitores."""
    event_type = event.get("type", "unknown")
    event["server_timestamp"] = datetime.now(timezone.utc).isoformat()

    if event_type == "call_ended":
        CALLS_DATABASE.append(event)
        if event.get("lead"):
            LEADS_DATABASE.append(event["lead"])

    # Difundir en vivo a todos los monitores web del cliente
    await monitor_hub.broadcast(event)
    return {"status": "broadcasted", "receivers": len(monitor_hub.active_connections)}


@app.get("/api/calls")
async def get_calls():
    """Devuelve el historial de llamadas registradas."""
    return {"calls": CALLS_DATABASE, "count": len(CALLS_DATABASE)}


@app.get("/api/leads")
async def get_leads():
    """Devuelve la bandeja de leads extraídos."""
    return {"leads": LEADS_DATABASE, "count": len(LEADS_DATABASE)}


@app.post("/api/test-webhook")
async def test_webhook():
    """Envía un lead de prueba simulado directamente a n8n para verificar el flujo."""
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
