"""Configuración central de La Centralita (todo por variables de entorno).

Ningún secreto tiene valor por defecto: si falta, la funcionalidad que lo
necesita queda deshabilitada de forma explícita (fail-closed).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# Longitud mínima aceptada para cualquier token de autenticación interno.
MIN_AUTH_TOKEN_LENGTH = 24

DEFAULT_CORS_ORIGINS = (
    "https://la-centralita.web.app",
    "https://la-centralita.firebaseapp.com",
    "https://yoyocubano.github.io",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
)


def _env_list(name: str, default: tuple[str, ...]) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return list(default)
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_serverless() -> bool:
    """True en Vercel (define VERCEL=1) o si se fuerza con CENTRALITA_SERVERLESS=1."""
    return bool(os.getenv("VERCEL")) or _env_bool("CENTRALITA_SERVERLESS", False)


def resolve_data_dir() -> Path:
    """Carpeta de datos locales (cola de Sheets, bandeja de email, solicitudes de cita).

    CENTRALITA_DATA_DIR tiene prioridad. En serverless el código es de solo lectura y
    solo /tmp es escribible (y efímero, por instancia): ahí se degrada por defecto.
    """
    explicit = os.getenv("CENTRALITA_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit)
    if is_serverless():
        return Path("/tmp") / "la-centralita-data"
    return ROOT_DIR / "data"


DATA_DIR = resolve_data_dir()


class Config:
    # LiveKit
    LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
    LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
    LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

    # Deepgram STT
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
    DEEPGRAM_MODEL = os.getenv("DEEPGRAM_MODEL", "nova-3")
    DEEPGRAM_LANGUAGE = os.getenv("DEEPGRAM_LANGUAGE", "es")

    # DeepSeek LLM
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # TTS intercambiable (ver agent/tts_factory.py): cosyvoice | piper | elevenlabs
    TTS_PROVIDER = os.getenv("TTS_PROVIDER", "cosyvoice").strip().lower()
    TTS_FALLBACK_PROVIDER = os.getenv("TTS_FALLBACK_PROVIDER", "piper").strip().lower()
    PIPER_MODEL_PATH = os.getenv(
        "PIPER_MODEL_PATH",
        str(ROOT_DIR / "models" / "piper" / "es_ES-sharvard-medium.onnx")
    )
    COSYVOICE_URL = os.getenv("COSYVOICE_URL", "http://localhost:50000")

    # Post-call Webhook (n8n). El secreto viaja en cabecera, nunca en la URL pública.
    N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
    N8N_WEBHOOK_SECRET = os.getenv("N8N_WEBHOOK_SECRET", "")

    # DocuSeal: secreto compartido configurado como cabecera del webhook en DocuSeal.
    DOCUSEAL_WEBHOOK_SECRET = os.getenv("DOCUSEAL_WEBHOOK_SECRET", "")
    DOCUSEAL_WEBHOOK_HEADER = os.getenv("DOCUSEAL_WEBHOOK_HEADER", "X-Docuseal-Secret")

    # Servidor y autenticación interna (único token aceptado; sin valores de fallback)
    AUTH_TOKEN = os.getenv("CENTRALITA_AUTH_TOKEN", "")
    PORT = int(os.getenv("PORT", "8080"))
    HOST = os.getenv("HOST", "0.0.0.0")
    CORS_ALLOWED_ORIGINS = _env_list("CORS_ALLOWED_ORIGINS", DEFAULT_CORS_ORIGINS)
    ENABLE_API_DOCS = _env_bool("ENABLE_API_DOCS", False)

    # Demo pública por WebRTC (landing). Desactivada por defecto.
    PUBLIC_DEMO_ENABLED = _env_bool("PUBLIC_DEMO_ENABLED", False)
    PUBLIC_DEMO_ROOM = os.getenv("PUBLIC_DEMO_ROOM", "centralita-demo")

    # WhatsApp (YCloud / Meta): token de verificación y secreto de firma del webhook.
    WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    WHATSAPP_WEBHOOK_SECRET = os.getenv("WHATSAPP_WEBHOOK_SECRET", "")

    # URL del backend que usa el worker de voz para emitir eventos al panel en vivo.
    CENTRALITA_API_URL = os.getenv("CENTRALITA_API_URL", "")

    # Despliegue serverless (Vercel): sin WebSocket persistente ni disco duradero.
    SERVERLESS = is_serverless()

    @classmethod
    def auth_token_is_strong(cls) -> bool:
        return len(cls.AUTH_TOKEN or "") >= MIN_AUTH_TOKEN_LENGTH

    # WhatsApp Business API (YCloud BSP)
    YCLOUD_API_KEY = os.getenv("YCLOUD_API_KEY", "")

    @classmethod
    def validate(cls, raise_on_missing=False) -> dict[str, bool]:
        """Check status of required environment variables."""
        status = {
            "LIVEKIT_URL": bool(cls.LIVEKIT_URL and cls.LIVEKIT_URL.startswith("wss://")),
            "LIVEKIT_API_KEY": bool(cls.LIVEKIT_API_KEY),
            "LIVEKIT_API_SECRET": bool(cls.LIVEKIT_API_SECRET),
            "DEEPGRAM_API_KEY": bool(cls.DEEPGRAM_API_KEY),
            "DEEPSEEK_API_KEY": bool(cls.DEEPSEEK_API_KEY),
            "N8N_WEBHOOK_URL": bool(cls.N8N_WEBHOOK_URL),
            "CENTRALITA_AUTH_TOKEN": cls.auth_token_is_strong(),
        }
        if raise_on_missing:
            missing = [k for k, v in status.items() if not v]
            if missing:
                raise ValueError(f"Faltan variables críticas de entorno: {', '.join(missing)}")
        return status
