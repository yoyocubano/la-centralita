import logging
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from livekit import api

from agent.config import Config
from agent.post_call import PostCallProcessor

logger = logging.getLogger("la-centralita.server")

app = FastAPI(title="La Centralita - WELUX Voice Assistant")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/api/status")
async def get_status():
    """Devuelve el estado de las credenciales y servicios del sistema."""
    checks = Config.validate()
    return {
        "status": "ready" if all(checks.values()) else "needs_configuration",
        "services": checks,
        "livekit_url": Config.LIVEKIT_URL or "No configurado",
        "n8n_webhook": Config.N8N_WEBHOOK_URL or "No configurado",
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


@app.post("/api/test-webhook")
async def test_webhook():
    """Envía un lead de prueba simulado directamente a n8n para verificar el flujo."""
    processor = PostCallProcessor()
    sample_transcript = [
        {"role": "assistant", "text": "Hola, gracias por llamar a WELUX Events. Soy Sofía, ¿en qué puedo ayudarte?"},
        {"role": "user", "text": "Hola, me llamo Carlos Mendoza y busco cotizar iluminación y DJ para una boda en Luxemburgo el 15 de agosto para 120 invitados. Mi teléfono es +352 691 123 456."},
        {"role": "assistant", "text": "Perfecto Carlos, un placer. Tenemos sistemas de iluminación y sonido ideales para bodas de ese tamaño. Nuestro equipo te llamará enseguida con la propuesta."},
    ]
    result = await processor.process_call_ended(
        room_name="test-simulado",
        participant_id="test-carlos",
        duration_seconds=42.5,
        transcript_history=sample_transcript,
        metrics={"tipo": "simulacion_directa"},
    )
    return {"message": "Webhook de prueba enviado a n8n", "payload": result}


# Montar archivos estáticos del frontend
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.app:app", host=Config.HOST, port=Config.PORT, reload=True)
