# 📞 La Centralita — Centralita de Voz con Agente IA B2B (WELUX)

**La Centralita** es una solución de voz con IA en tiempo real orientada a pymes y negocios de servicios en Luxemburgo:
El cliente llama → el agente de voz natural conversa y califica → la llamada se transcribe en tiempo real → al colgar se extrae el lead estructurado y se sincroniza con el CRM (Twenty) y workflows (n8n).

### 🏢 Las 4 Líneas de Negocio Respaldadas:
1. **Asesoría de negocios:** consultoría para empresas, optimización operativa y atención comercial 24/7.
2. **Servicios digitales:** automatizaciones, integraciones y modernización de procesos.
3. **Alquileres para eventos y ocio:** fotoespejos interactivos (photobooths), inflables temáticos (billar inflable, minigolf) y reservas telefónicas ágiles.
4. **Servicios B2B para empresas:** diseño web, chatbots inteligentes, gestión de mailing/newsletters y despliegue de CRM.
*(Nota: la producción de bodas y eventos exclusivos opera como una vertical especializada de demostración, no la identidad única del negocio).*

**Fase 1: prueba a costo cero.** Sin número telefónico de pago: las llamadas de prueba se
hacen desde el navegador (WebRTC).

## Coordinación

👉 **Leer primero [`HANDSHAKE.md`](HANDSHAKE.md).** Es el documento único de
coordinación: todos los agentes lo leen y lo actualizan ahí mismo. No crear
otros documentos de seguimiento.

## Estructura

- `agent/` — worker de LiveKit Agents (Python): `agent.py`, `prompts.py`,
  `piper_tts.py`, `lead_extract.py`, `post_call.py`, `config.py`,
  `requirements.txt`, `.env.example`, `make_token.py`
- `server/` — backend FastAPI (`app.py`): tokens LiveKit, WebSocket del
  monitor (`/ws/monitor`), endpoints REST y despacho a n8n
- `index.html` — landing pública y simulador para GitHub Pages
- `panel/` — Panel del Cliente (monitor web en vivo, CRM Twenty, analítica Metabase, agenda Dograh y firma digital DocuSeal)
- `web/` — página de llamada WebRTC del cliente (`index.html`, `app.js`,
  `style.css`) y `test-page.html` (prueba básica con token manual)
- `docs/` — referencias arquitectónicas (`OPEN_SOURCE_REFERENCES.md`) y auditoría de seguridad (`SECURITY_AUDIT.md`)
- `security/` — ledger de seguridad Cloudflare (`findings.json` y `coverage-ledger.json`)
- `scripts/` — `download_models.sh` (descarga del modelo Piper ONNX)
- `tests/` — suite pytest (pipeline, herramientas Dograh, DocuSeal y fixes de seguridad)
- `n8n/` — documentación del webhook post-llamada

## Puesta en marcha (prueba)

1. `cd agent && cp .env.example .env` y rellenar las claves.
2. `pip install -r requirements.txt`
3. Levantar el worker: `python agent.py dev`
4. Generar token: `python make_token.py`
5. Abrir `web/test-page.html`, pegar URL + token, pulsar **Llamar**.

## Estado real vs. simulado (Agente y Panel)

- **Real a partir de ahora:**
  - El agente real de voz (`agent/agent.py`) emite eventos reales de inicio (`call_started`) y fin (`call_ended`) de llamada hacia `POST /api/call-event`, usando `CENTRALITA_SERVER_URL` (por defecto `http://127.0.0.1:8000`) y `Authorization: Bearer <CENTRALITA_AUTH_TOKEN>`.
  - Los payloads al panel van **anonimizados según RGPD** (`redact_pii` de `agent/post_call.py` + supresión del nombre del contacto), sin incluir nombre, teléfono ni email, e incluyen `call_id`, `inicio`/`fin`, `duration`, `status` (`iniciada` / `terminada`) y resumen anonimizado. Si el servidor falla o no responde, la llamada continúa sin bloquear el flujo de voz.
  - El backend (`server/app.py`) guarda el historial en `/api/calls` y `/api/leads` y lo difunde en tiempo real al panel vía `/ws/monitor`.
  - Al colgar también se mantiene el envío post-llamada al webhook de n8n (`N8N_WEBHOOK_URL`).
- **Sigue simulado:**
  - El botón **Simular Llamada Entrante** del panel (`panel/app.js`) en Modo Demo local.
  - La transcripción incremental turno a turno (`transcript_delta` aún no es emitida en streaming por el worker hacia `/api/call-event`).
  - Las herramientas Dograh (`check_calendar_availability` / `book_technical_meeting` en `agent/agent.py`) devuelven respuestas mock sin agenda real conectada.
  - Las vistas del panel sin integración viva (CRM Twenty, analítica Metabase y firma DocuSeal).

## Reglas

- No tocar el chatbot Rebeca AI. Proyecto separado.
- Prohibido Twilio.
- Cero secretos en el repo (el `.env` está en `.gitignore`).

