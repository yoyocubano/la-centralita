# 📞 La Centralita — centralita de voz con agente IA (WELUX)

El cliente llama → un agente de voz natural conversa → la llamada se transcribe →
al colgar se extrae el lead y se envía al dueño + n8n.

**Fase 1: prueba a costo cero.** Sin número telefónico: las llamadas de prueba se
hacen desde el navegador (WebRTC).

## Coordinación

👉 **Leer primero [`HANDSHAKE.md`](HANDSHAKE.md).** Es el documento único de
coordinación: todos los agentes lo leen y lo actualizan ahí mismo. No crear
otros documentos de seguimiento.

## Estructura

- `agent/` — worker de LiveKit Agents (Python): `agent.py`, `prompts.py`,
  `piper_tts.py`, `lead_extract.py`, `requirements.txt`, `.env.example`,
  `make_token.py`
- `web/test-page.html` — página de prueba WebRTC
- `n8n/` — documentación del webhook post-llamada

## Puesta en marcha (prueba)

1. `cd agent && cp .env.example .env` y rellenar las claves.
2. `pip install -r requirements.txt`
3. Levantar Piper local y el worker: `python agent.py dev`
4. Generar token: `python make_token.py`
5. Abrir `web/test-page.html`, pegar URL + token, pulsar **Llamar**.

## Reglas

- No tocar el chatbot Rebeca AI. Proyecto separado.
- Prohibido Twilio.
- Cero secretos en el repo (el `.env` está en `.gitignore`).
