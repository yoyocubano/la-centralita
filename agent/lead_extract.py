"""Extracción del lead al colgar (DeepSeek, temperatura 0.1)."""

import asyncio
import json
import logging
import os
import urllib.request

from prompts import EXTRACTION_PROMPT

logger = logging.getLogger("centralita")


async def extract_lead(transcript: str) -> dict:
    """Llama a DeepSeek para extraer el lead en JSON desde la transcripción."""
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": EXTRACTION_PROMPT.format(transcript=transcript),
            }
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    def _do() -> dict:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)

    try:
        return await asyncio.to_thread(_do)
    except Exception as exc:  # no romper el envío a n8n si falla la extracción
        logger.exception("Falló la extracción del lead: %s", exc)
        return {"error": str(exc), "motivo": "extracción fallida, ver transcripción"}
