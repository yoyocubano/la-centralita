import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

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

    # TTS
    TTS_PROVIDER = os.getenv("TTS_PROVIDER", "piper").lower()
    PIPER_MODEL_PATH = os.getenv(
        "PIPER_MODEL_PATH",
        str(ROOT_DIR / "models" / "piper" / "es_ES-sharvard-medium.onnx")
    )

    # Post-call Webhook (n8n)
    N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")

    # Servidor y autenticación interna
    AUTH_TOKEN = os.getenv("CENTRALITA_AUTH_TOKEN", "")
    PORT = int(os.getenv("PORT", "8080"))
    HOST = os.getenv("HOST", "0.0.0.0")

    @classmethod
    def validate(cls, raise_on_missing=False) -> dict[str, bool]:
        """Check status of required environment variables."""
        status = {
            "LIVEKIT_URL": bool(cls.LIVEKIT_URL and "livekit.cloud" in cls.LIVEKIT_URL),
            "LIVEKIT_API_KEY": bool(cls.LIVEKIT_API_KEY),
            "LIVEKIT_API_SECRET": bool(cls.LIVEKIT_API_SECRET),
            "DEEPGRAM_API_KEY": bool(cls.DEEPGRAM_API_KEY),
            "DEEPSEEK_API_KEY": bool(cls.DEEPSEEK_API_KEY),
            "N8N_WEBHOOK_URL": bool(cls.N8N_WEBHOOK_URL),
            "PIPER_MODEL_EXISTS": Path(cls.PIPER_MODEL_PATH).exists(),
        }
        if raise_on_missing:
            missing = [k for k, v in status.items() if not v]
            if missing:
                raise ValueError(f"Faltan variables críticas de entorno: {', '.join(missing)}")
        return status
