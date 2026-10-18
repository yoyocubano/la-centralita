# Auditoría integral — La Centralita (rama `feature/centralita-voz`)

**Fecha:** 2026-10-01 · **Auditora:** Claude (orden directa del dueño, con permiso de escritura) · **Base auditada:** `32fd8b4`
**Alcance:** todo el repositorio, línea por línea. **Corrección:** commits `469ba4b`, `80bc25a`, `ad2b114`, `47daa2b` y siguientes.

> Este documento sustituye al informe anterior ("0 hallazgos · HARDENED_EXCELLENT"), que no se correspondía con el código.
> Postura en `32fd8b4`: **EXPOSED**. Postura tras las correcciones: **HARDENED con pendientes del dueño** (ver §5).

## 1. Hallazgos H-001…H-007 (commit `92729b7`): ¿cerrados de verdad?

| ID | Veredicto en `32fd8b4` | Evidencia | Estado tras corrección |
|---|---|---|---|
| H-001 CORS | **Parcial** | Lista explícita correcta (sin comodín ni credenciales), pero fija en código e incluía un canal preview efímero. | Configurable por `CORS_ALLOWED_ORIGINS`; por defecto solo `la-centralita.web.app`, `.firebaseapp.com`, GitHub Pages y localhost. Test con orígenes engañosos (`…web.app.evil.com`, `evil-la-centralita.web.app`). |
| H-002 Token LiveKit | **REFUTADO (no cerrado) — CRÍTICO** | `server/app.py:211` aceptaba `centralita-secure-token-2026`, publicado en `panel/app.js:293` e `index.html:1797`. Cualquiera obtenía tokens LiveKit. | Token único `CENTRALITA_AUTH_TOKEN`, `compare_digest`, fail-closed 503. TTL 1 h explícito. Landing usa `/api/public/demo-token` (opt-in, sala única, TTL 10 min, 3/10 min/IP). |
| H-003 call-event | **REFUTADO — CRÍTICO** | Misma credencial pública ⇒ inyección de eventos en todos los paneles. | Igual que H-002 + validación de `type`/`role`. |
| H-004 WebSocket | **REFUTADO — ALTO** | Token pública por defecto y en la query string (queda en logs de proxy). | Auth por primer mensaje, control de `Origin` (4003), límite de conexiones, timeout. |
| H-005 PII a n8n | **Parcial — MEDIO** | Teléfono/email redactados, pero el nombre del cliente seguía en la transcripción y `docuseal.client_name` iba en claro. | Redacción de nombres conocidos; payload sin `docuseal`; test que verifica ausencia de PII. |
| H-006 URL webhook | **Parcial — MEDIO** | Retirada de `.env.example`, pero presente en `panel/index.html:663` (desplegado), `index.html`, `HANDSHAKE.md`, `n8n/README.md`, este documento. | Retirada de todo el árbol + test sobre todo el repo. **Sigue en el historial git: ROTAR la ruta.** |
| H-007 XSS | **Confirmado cerrado** (salvo vectores nuevos) | Todos los `innerHTML` eran `= ""`. Nuevos vectores encontrados: HTML del email sin escapar, CSV con fórmulas. | Email con `html.escape`; CSV anti-fórmulas; sin handlers inline ⇒ CSP `script-src 'self'`. |

Los hallazgos H-008…H-011 del informe de 11 puntos no están en el repositorio; **no verificables** como tales. Quedan cubiertos por los hallazgos nuevos de §2.

## 2. Hallazgos nuevos

| ID | Sev. | Archivo:línea (`32fd8b4`) | Hallazgo | Corrección |
|---|---|---|---|---|
| N-01 | CRÍTICO | `server/app.py:507,459,522,561` | `/api/leads`, `/api/calls`, `/api/system/internal` sin auth (PII de todos los leads); `/api/test-webhook` sin auth escribía en el Sheet real y enviaba emails. | Auth obligatoria; test-webhook simulado sin escrituras. |
| N-02 | ALTO | `server/app.py:210` | `LIVEKIT_API_SECRET` aceptado como token de API. | Eliminado. |
| N-03 | ALTO | `server/app.py:425` | Webhook DocuSeal sin autenticación (cualquiera marcaba contratos como FIRMADO). | Secreto en cabecera, fail-closed. |
| N-04 | ALTO | `server/app.py:474` | `client-identify` público sin consentimiento, sin rate-limit, sin validar teléfono (spam al Sheet y al email). | Consentimiento RGPD, regex, 5/10 min/IP, errores sin eco de datos. |
| N-05 | ALTO | `agent/agent.py:66` | `book_technical_meeting` decía al cliente "Reunión confirmada… notificado al director" sin guardar nada. | Registra solicitud en `data/appointment_requests.jsonl` y comunica "pendiente de confirmación". |
| N-06 | MEDIO | `agent/sheets_sync.py:340,422,453` | Apóstrofe + RAW ⇒ apóstrofe literal visible; `time.sleep` y `urllib` síncronos dentro de `async` (bloqueaban el servidor). | RAW sin apóstrofe; `asyncio.to_thread`/`asyncio.sleep`. |
| N-07 | MEDIO | `agent/email_notify.py:132` | HTML del email con datos del lead sin escapar (inyección de marcado). | `html.escape`, asunto sin CR/LF. |
| N-08 | MEDIO | `agent/docuseal_client.py:304` | Sin API key devolvía `ENVIADO` y una URL de firma inexistente. | `BORRADOR` sin URL. |
| N-09 | MEDIO | `n8n/workflows/…:34-40` | `USER_ENTERED` (causa del `#ERROR!`), Sheet ID de fallback hardcodeado, pestaña distinta, filas duplicadas. | Workflow sin PII, Header Auth, RAW, pestaña `Llamadas`. |
| N-10 | MEDIO | `agent/agent.py:198-205` | Duración fija 45 s; toda la transcripción como rol "user"; el agente nunca emitía eventos ⇒ el panel en vivo era imposible. | Duración real, roles, eventos `call_started/transcript_delta/call_ended`. |
| N-11 | MEDIO | `agent/agent.py:150` | Nova-3 con `keywords` (solo Nova-2) y sin `mip_opt_out` (audio usable para entrenar: RGPD). | `keyterms`, `mip_opt_out=True`. |
| N-12 | MEDIO | `agent/piper_tts.py:422` | Errores de síntesis silenciados ⇒ silencio en llamada, sin fallback. | `APIConnectionError`. |
| N-13 | BAJO | `panel/index.html:10` | Google Fonts (transferencia de IP a tercero, RGPD). | Eliminado; fuentes del sistema. |
| N-14 | BAJO | `server/app.py:108` | OpenAPI público, `X-XSS-Protection: 1` obsoleto, errores 422 con eco del input. | Docs off por defecto, `X-XSS-Protection: 0`, CSP en API, errores sin `input`. |
| N-15 | BAJO | `panel/app.js:207` | Exportación CSV vulnerable a inyección de fórmulas. | Celdas neutralizadas. |
| N-16 | BAJO | `panel/app.js:322,399,1019` | Funciones inexistentes (`renderLeadsTable`, `renderKanbanBoard`, `jsonParseSafe`): los eventos `call_ended` rompían el panel. | Corregido; test de integridad de ids/acciones. |
| N-17 | INFO | `.firebase/hosting.*.cache` | Caché de Firebase CLI versionada. | Fuera del repo y en `.gitignore`. |

## 3. Puntos pedidos expresamente

- **`GET /api/leads`**: leía del Sheet en vivo, pero si fallaba devolvía la memoria del proceso como `memory_live` sin avisar, y el panel **mezclaba datos simulados** con los reales (`app.js:252`). Ahora: `source=google_sheets_live` o `memory_session` + `degraded=true` + motivo; el panel muestra el aviso y nunca mezcla.
- **`POST /api/client-identify`**: flujo Sheet → email → panel funcional, pero respondía "notificada con éxito" aunque el email quedara `QUEUED_NO_SMTP`. Ahora declara `sheets_status`/`email_status` reales (`partial` cuando procede).
- **WebSocket**: reconexión fija de 15 s, sin heartbeat, RTT inventado ("38 ms"). Ahora backoff exponencial con jitter (1→30 s), ping cada 25 s, RTT medido, reconexión en `online`/`visibilitychange`, sin reintento en 4001/4003.
- **Tests (25/25)**: comprobado 23/25 en este entorno (2 dependen del modelo Piper local). **Tautológicos**: `test_docuseal_contract_payload_format` (construye y comprueba su propio dict), `test_security_findings_json_structure` (verifica que el JSON diga "HARDENED"), `test_n8n_webhook_delivery` (llamada real a red, sin aserción), y **el test del teléfono** (`row[3] in ("+352…", "'+352…")`: aceptaba ambos resultados, por eso no detectó que el apóstrofe con RAW queda visible). Sustituidos por 97 tests de comportamiento.
- **Firebase**: `.firebaserc` mapea el target `la-centralita` al sitio `la-centralita` del proyecto `weddings-events-96440339-a53e7`; `firebase.json` define un único target ⇒ `firebase deploy --only hosting:la-centralita` no toca los otros 6 sitios. **Confirmado**: `https://la-centralita.web.app` responde 404 "Site Not Found" (nunca se desplegó al canal live); el preview `…--preview-j4bp6lio.web.app` sirve exactamente los archivos de `32fd8b4` y `/api/*` devuelve 404 ⇒ el panel desplegado está en modo demo **por construcción** (no hay backend en Firebase Hosting).
- **CSS**: confirmado. 189 clases en HTML + JS dinámico, 161 sin definir (el CSS Apple-minimal estaba escrito para otra estructura: `apple-table` vs `data-table`, `modal-overlay` vs `modal-backdrop`…). Al no existir `.modal-backdrop`, los tres modales se renderizaban abiertos dentro de la página. Reescrito: 0 clases sin estilo (test automático).

## 4. Voz (TTS)

El TTS estaba hardcodeado a Piper (`agent/agent.py:170`), gratuito, por lo que se refactorizó directamente: `TTS_PROVIDER=cosyvoice|piper|elevenlabs` (por defecto CosyVoice 3 auto-hospedado), respaldo `TTS_FALLBACK_PROVIDER` (Piper) con `FallbackAdapter`. ElevenLabs queda como upgrade sin tocar código (`pip install livekit-plugins-elevenlabs` + 2 variables). El adapter CosyVoice está probado contra un servidor HTTP con la interfaz oficial; **no probado contra un modelo CosyVoice real** (requiere GPU).

## 5. Pendiente del dueño (no resoluble desde el código)

1. **Rotar la ruta del webhook n8n** y el token público `centralita-secure-token-2026` ya no sirve, pero ambos siguen en el historial git.
2. Definir `CENTRALITA_AUTH_TOKEN` (`openssl rand -hex 32`), `N8N_WEBHOOK_SECRET`, `DOCUSEAL_WEBHOOK_SECRET` en el `.env` del servidor.
3. **SMTP**: configurar `SMTP_*`; luego `python scripts/flush_email_outbox.py --send` (los avisos sin SMTP ya no se pierden).
4. **Filas `#ERROR!`**: `python scripts/repair_sheet_phones.py` (dry-run) y `--apply`.
5. **Backend público**: alojar FastAPI (p. ej. Cloud Run) y poner su URL en `panel/config.js` (`apiBase`) y en `CORS_ALLOWED_ORIGINS`; después `firebase deploy --only hosting:la-centralita`.
6. Servidor CosyVoice 3 + muestra de voz (`COSYVOICE_PROMPT_WAV/TEXT`).
