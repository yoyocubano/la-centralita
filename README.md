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
  `piper_tts.py`, `lead_extract.py`, `requirements.txt`, `.env.example`,
  `make_token.py`
- `panel/` — Panel del Cliente (monitor web en vivo, CRM Twenty, analítica Metabase, agenda Dograh y firma digital DocuSeal)
- `docs/` — referencias arquitectónicas (`OPEN_SOURCE_REFERENCES.md`) y auditoría de seguridad (`SECURITY_AUDIT.md`)
- `security/` — ledger de seguridad Cloudflare (`findings.json` y `coverage-ledger.json`)
- `server/` — backend FastAPI (tokens LiveKit, API del panel, webhooks)
- `api/index.py` + `vercel.json` — despliegue del backend en Vercel (serverless); ver [`docs/VERCEL.md`](docs/VERCEL.md)
- `web/test-page.html` — página de prueba WebRTC básica
- `n8n/` — documentación del webhook post-llamada

## Puesta en marcha (prueba)

1. `cd agent && cp .env.example .env` y rellenar las claves.
2. `pip install -r requirements.txt`
3. Levantar el worker: `python agent.py dev`
4. Generar token: `python make_token.py`
5. Abrir `web/test-page.html`, pegar URL + token, pulsar **Llamar**.

## Reglas

- No tocar el chatbot Rebeca AI. Proyecto separado.
- Prohibido Twilio.
- Cero secretos en el repo (el `.env` está en `.gitignore`).

