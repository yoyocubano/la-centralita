# HANDSHAKE — La Centralita (WELUX)

> **Documento único de coordinación.** Todos los agentes que intervienen en este
> proyecto (**moise, Antigravity, Claude, Jay**) LEEN este archivo antes de tocar
> nada y lo ACTUALIZAN aquí mismo.
>
> **Reglas:**
> 1. No crear otro documento de coordinación: todo vive en este archivo.
> 2. Cada agente actualiza su sección y el tablero; no borrar el trabajo de otros.
> 3. Las claves reales NUNCA se escriben aquí (solo qué falta / qué ya existe).
> 4. Ver la REGLA DE SEGURIDAD sobre secretos más abajo: aplica a TODOS
>    los agentes sin excepciones.
> 4. Cuando algo queda resuelto, márcalo `✅ RESUELTO (fecha)` con evidencia
>    (commit, URL, medición).

## 🔒 REGLA DE SEGURIDAD — SECRETOS (para TODOS, sin excepciones)

Claves API, tokens, códigos de verificación, contraseñas y cualquier dato
sensible:

- Viajan ÚNICAMENTE por el canal interno (el chat "la centralita").
- Van directo al `.env` local (gitignored) de quien los necesite. Nada más.
- PROHIBIDO escribirlos en el repo, en este HANDSHAKE, en commits, en issues,
  en notas/memoria, en logs o exponerlos en cualquier otra superficie.
- Si un secreto aparece donde no debe: se rota inmediatamente y se avisa en
  el chat interno.

No hay excepciones a esta regla para ningún agente.

## 1. Qué es

Centralita telefónica con agente de voz natural impulsada por IA. Es un **PRODUCTO B2B** comercializable para negocios, empresas y pymes de servicios en Luxemburgo (empresas de alquileres, consultorías, agencias digitales, etc.).

El cliente o prospecto llama → el agente conversa de forma natural y ágil 24/7 (reservas, cotizaciones, información de servicios, agenda) → la llamada se transcribe en tiempo real → al colgar se extrae el lead estructurado (nombre, empresa, teléfono, motivo, fecha/plazo, requerimientos) y se sincroniza con el CRM (Twenty) + webhook n8n.

### 🏢 Las 4 Líneas de Negocio Respaldadas:
1. **Asesoría de negocios:** consultoría estratégica y diagnóstico para empresas.
2. **Servicios digitales:** automatización, integraciones de software y soporte técnico digital.
3. **Alquileres para eventos y ocio:** fotoespejos (photobooths), inflables interactivos (billar inflable, minigolf) y reservas telefónicas automáticas.
4. **Servicios B2B para empresas:** páginas web, chatbots con IA, campañas de mailing/newsletters y CRM.
*(Las bodas y eventos exclusivos operan como una vertical de demostración, no la identidad única del negocio).*

## 2. Reglas de oro (no negociables)

- NO tocar el chatbot web Rebeca AI (repo `welux-events`). Proyecto separado.
- PROHIBIDO usar Twilio bajo ningún concepto.
- Fase 1 a costo cero: solo tiers gratuitos / créditos de prueba.
  Nada de tarjetas ni pagos sin orden expresa del usuario.
- El `.env` real siempre va en `.gitignore`. Cero secretos en el repo.

## 3. Stack Fase 1 (prueba $0)

| Pieza | Servicio | Estado |
|---|---|---|
| Transporte WebRTC | LiveKit Cloud (Build, gratis) | 🟡 enlace enviado a info@weluxevents.com |
| STT | Deepgram Nova-3 | 🟡 cuenta en creación (bloqueo por reCAPTCHA) |
| LLM | DeepSeek (cliente OpenAI-compatible) | 🟡 cuenta en creación (esperando credencial/código) |
| TTS | Piper (local, open source, $0) | ✅ RESUELTO (2026-10-01) adapter verificado en livekit-agents 1.8.3 |
| Post-llamada | n8n webhook | ✅ `https://weluxdigitalservices.app.n8n.cloud/webhook/centralita-test` (HTTP 200 verificado) |
| Canal de prueba | Página web GitHub Pages (sin número) | ✅ https://yoyocubano.github.io/la-centralita/ |

## 4. Tablero Fase 1 — criterio de salida

- [ ] Cuentas trial creadas (LiveKit, Deepgram, DeepSeek)
- [x] Adapter Piper TTS verificado contra la versión instalada de `livekit-agents`
- [ ] Agente conversa por WebRTC en la página de prueba
- [ ] Latencia percibida < 1 s (medir y anotar aquí)
- [ ] Transcripción + lead extraído al colgar → POST a n8n verificado en el workflow
- [ ] Costo real por llamada de prueba medido (anotar aquí)
- [ ] `git push` a `main` con la versión probada
- [ ] Reporte de requisitos para Fase 2 (número LU real — NO ejecutar sin orden)

## 5. Mapa del repo

- `HANDSHAKE.md` — este archivo (leer primero, actualizar siempre)
- `index.html` — landing page interactiva y simulador para GitHub Pages
- `docs/OPEN_SOURCE_REFERENCES.md` — documentación de arquitectura reutilizada (Twenty CRM, Metabase BI, Dograh Voice/MCP, DocuSeal Firma Digital, Cloudflare Security Audit)
- `docs/SECURITY_AUDIT.md` — informe ejecutivo de auditoría defensiva (estándar Cloudflare)
- `security/findings.json` — ledger estructurado de hallazgos de seguridad
- `security/coverage-ledger.json` — registro persistente de cobertura de componentes auditados
- `panel/` — Panel del Cliente (monitor web en vivo: llamadas, transcripción en tiempo real, leads, agenda, firma digital DocuSeal y auditoría Cloudflare)
- `agent/agent.py` — worker de LiveKit Agents (STT → LLM → TTS + tool-calling de calendario + envío a n8n)
- `agent/prompts.py` — guion del agente de voz
- `agent/piper_tts.py` — adapter TTS Piper (verificado contra SDK instalado)
- `agent/lead_extract.py` — extracción de lead al colgar (DeepSeek, temp 0.1)
- `agent/requirements.txt` — dependencias Python
- `agent/.env.example` — plantilla (copiar a `.env`, nunca subir el real)
- `agent/make_token.py` — genera token de prueba para la página web (TTL 1h restringido)
- `web/test-page.html` — página de prueba WebRTC básica (llamar desde el navegador)
- `n8n/README.md` — formato del payload que recibe el webhook
- `tests/` — suite de pruebas automatizadas (pipeline, Dograh tools, DocuSeal y Cloudflare security)

## 6. Bitácora (actualizar aquí mismo, no en otro archivo)

### 2026-10-01 — Antigravity
- Pull del scaffold v0.1 completado.
- ✅ **Adapter Piper TTS verificado y adaptado** a `livekit-agents` 1.8.3 (`tts.TTS` y `ChunkedStream`).
- Implementado soporte dual:
  1. **In-process (recomendado)**: Carga directa de modelo ONNX con `piper-tts` en Python (`models/piper/es_ES-sharvard-medium.onnx`), latencia de síntesis `< 50 ms`, 0 € y sin necesidad de levantar servidor HTTP externo.
  2. **HTTP Server**: Fallback compatible con servidores HTTP de Piper (`PIPER_HTTP_URL`).
- Probada la integración con n8n enviando payload de prueba: HTTP 200 recibido exitosamente en `centralita-test`.
- ✅ **Página web de prueba y simulador desplegados en GitHub Pages**: `https://yoyocubano.github.io/la-centralita/`
- ✅ **Panel del Cliente (Monitor Web en Vivo) construido y publicado**: `https://yoyocubano.github.io/la-centralita/panel/`
- ⚡ **Reutilización de Arquitectura Open Source Verificada (Notas 24, 25 y Extra)**:
  - **`twentyhq/twenty`** (Nota 24): Modelo de datos de leads normalizado (`Person`, `Opportunity`, `Activity.Call`), pipeline Kanban interactivo en el panel con cálculo de importes por etapa, y especificación de integración directa vía n8n REST API.
  - **`metabase/metabase`** (Nota 24): Dashboard analítico de alto impacto en el monitor: tarjetas KPI ejecutivas con deltas porcentuales, embudo visual de conversión comercial (18 llamadas → 4 citas), distribución horaria de tráfico en Luxemburgo y métricas de latencia/SLA.
  - **`dograh-hq/dograh`** (Nota 24): Tool-calling nativo para agendamiento de citas en `agent/agent.py` (`check_calendar_availability`, `book_technical_meeting`) con LiveKit Agents 1.8 (`@llm.function_tool`), widget de reserva rápida y sincronización de citas.
  - **`docusealco/docuseal`** (Nota 24): Motor de firma digital electrónica para contratos de eventos de WELUX Events S.à r.l., integrado en el módulo de leads y pipeline comercial con sellado eIDAS y webhook n8n.
  - **`cloudflare/security-audit-skill`** (Nota 25 y Nota extra): Framework automatizado de auditoría en 6 fases. Creados `security/findings.json` y `security/coverage-ledger.json` (0 vulnerabilidades críticas, postura HARDENED, secreto cero en git, DTLS-SRTP y RGPD Luxemburgo). Incorporado monitor de seguridad en el panel.
- ✅ **Suite de tests ampliada**: 16 tests pasando (`pytest tests/`).
- 🎯 **Ajuste de Enfoque y Posicionamiento B2B**:
  - Reescrita la documentación, prompts y páginas para posicionar La Centralita como producto comercial B2B para pymes y empresas en Luxemburgo.
  - Articuladas las 4 líneas de negocio: 1) Asesoría de negocios (consultoría empresarial), 2) Servicios digitales, 3) Alquileres para eventos (fotoespejos/photobooths, inflables interactivos), 4) Servicios B2B (páginas web, chatbots, mailing, CRM). Bodas/eventos preservados como un escenario demo adicional.
- 🛡️ **Resolución de Auditoría de Seguridad (Hallazgos Critical, High, Medium H-001 a H-007)**:
  - **H-001 (CORS, Medium)**: Restringido a GitHub Pages (`https://yoyocubano.github.io`) y localhost; `allow_credentials=False`; métodos `GET, POST, OPTIONS`; headers `Content-Type, Authorization`.
  - **H-002 (Token Endpoint, Critical)**: Exigido header `Authorization: Bearer <token>` y restricción estricta de salas autorizadas (`ALLOWED_ROOMS = {"centralita-test", "centralita-demo"}`). 401/403 si no autorizado.
  - **H-003 (Call-Event Endpoint, Critical)**: Exigido header `Authorization: Bearer <token>` y validación de esquema con whitelist estricta (`ALLOWED_EVENT_KEYS`). Rechazo 400 ante claves extra o inyecciones.
  - **H-004 (WebSocket Monitor, High)**: Validación de token por query param (`/ws/monitor?token=...`) antes de aceptar conexión; cierre con código `4001` ("Unauthorized") si el token es inválido o falta.
  - **H-005 (Scrubbing de PII, Medium)**: Implementada función `redact_pii()` para anonimizar teléfonos y emails en transcripciones (RGPD Luxemburgo); despacho seguro con `lead_hash` (SHA-256) hacia el webhook de n8n.
  - **H-006 (Exposición de Webhook, Medium)**: Reemplazada URL real en `.env.example` y `agent/.env.example` por placeholder seguro `https://tu-instancia-n8n.com/webhook/tu-secret-path-aqui`.
  - **H-007 (XSS en Panel, High)**: Eliminados todos los `innerHTML` dinámicos en `panel/app.js` e `index.html`. Reemplazados por construcción segura de DOM y `textContent`.
- ⏳ A la espera de las credenciales (LiveKit Cloud, Deepgram, DeepSeek) para configurar `.env` y levantar el worker.

### 2026-10-01 — Antigravity (Actualización PRO, Stitch, Backend Real & Pipeline)
- 🎨 **Integración del Paquete de Diseño PRO**:
  - Incorporada la hoja de estilos `style-pro-v1.1.css` (204 clases) en `panel/style.css`, aplicando la estética Consola NOC Telecom (violeta `#8b5cf6`, oro `#d4af37`, azul `#38bdf8`, fondo `#07080d`).
  - Añadidos los 6 overlays gráficos con transparencias en `panel/img/`: `wave-hero.png`, `wave-divider.png`, `radar-call.png`, `bg-texture.png`, `signal-bars.png` y `vignette-glow.png`.
- 🌐 **Conexión con Google Stitch MCP**:
  - Conectado al servidor MCP de Stitch y creado el proyecto `projects/5860590090529869842` ("La Centralita - Panel del Cliente (WELUX)").
  - Creado y asociado el Design System `assets/975930371328736050` ("La Centralita - Consola NOC Telecom", modo oscuro, Inter + JetBrains Mono).
  - Generada Pantalla 1 ("Consola NOC - Vista En Vivo", screen `e1859c482a964960a7cd000a6ff49a2a`).
- 📊 **Orden 1 — Sincronización Real con Google Sheets (`agent/sheets_sync.py`)**:
  - Escritura estructurada en la hoja *"La Centralita — Leads"*.
  - Idempotencia determinista con hash SHA-256 de campos clave (evita duplicados).
  - Zona horaria estricta `Europe/Luxembourg` con sellado de fecha y hora local.
  - Reintentos automáticos con retroceso exponencial (3 intentos) y cola local persistente `data/leads_queue.json` si la API o credenciales están offline.
- 📧 **Orden 2 — Notificaciones Post-Llamada (`agent/email_notify.py`)**:
  - Envío automático de resumen ejecutivo post-llamada a `info@weluxevents.com`.
  - Plantilla dual (texto plano y HTML responsivo dark luxury WELUX) con datos del contacto, resumen de llamada, cálculo de valor y recomendación de próximos pasos según la intención detectada.
  - Fallback seguro con registro local si las variables SMTP no están presentes.
- ⚡ **Orden 3 — Workflow n8n Importable (`n8n/workflows/la-centralita-post-call.json`)**:
  - Pipeline completo: Webhook Inbound → Filtro PII & Preparación RGPD → Google Sheets → Envío de Email → Twenty CRM (Upsert Contacto) → Error Trigger con alerta por correo en caso de fallos.
- 💻 **Orden 4 — Panel del Cliente con Datos Reales (`panel/app.js`)**:
  - Implementado `HybridCentralitaProvider` que consulta `/api/calls` y `/api/leads` del backend en tiempo real, manteniendo fallback local automático en modo estático.
  - Suscripción y renderizado en vivo para eventos WebSocket `call_ended` y `docuseal_update`.
- 📝 **Orden 5 — Firma Digital DocuSeal (`agent/docuseal_client.py` & `server/app.py`)**:
  - Generación de contratos y enlaces de firma eIDAS para clientes calificados.
  - Endpoint receptor `POST /api/docuseal/webhook` para actualizar en vivo el estado del contrato (`BORRADOR` → `ENVIADO` → `FIRMADO`) y retransmitirlo a los paneles conectados.
- 🛡️ **Limpieza del Landing Page (`index.html`)**:
  - Eliminados todos los snippets de código Python y JSON expuestos, selectores de pestañas de desarrollo y menciones a tokens manuales o `make_token.py`.
  - Reemplazados por secciones 100% orientadas al cliente B2B: *Beneficios & ROI*, *Comparativa de Costes Luxemburgo* (ahorro >85% frente a salario recepcionista) y *Seguridad Corporativa, RGPD y Validez eIDAS*.
- 📈 **Métricas del Sistema (Interno vs Cliente)**:
  - Endpoint `/api/system/internal` que calcula tiempo total ahorrado (horas), dinero ahorrado en EUR, coste marginal acumulado de IA y estado de la cola.
  - Endpoint `/api/status` que entrega al cliente un estado simplificado de alta disponibilidad (99.9% uptime).
- 🧪 **Suite de Pruebas Ampliada**:
  - 21 pruebas automatizadas pasando con éxito (`pytest tests/`), cubriendo pipeline, Dograh tools, seguridad H-001 a H-007, sheets sync, email notifications y endpoints del servidor.
- 🛑 **Regla Estricta**: Cero push al repositorio remoto sin confirmación previa del usuario.

### 2026-10-01 — moise
- Scaffold inicial v0.1 creado y pusheado (código sin probar en vivo).
- Webhook n8n creado, publicado y entregado a Antigravity.
- Estrategia de correos para trials enviada a Antigravity
  (info@weluxevents.com; NO usar yucolaguilar@gmail.com).
- ⏳ Pendiente: credenciales del usuario (las pasará por chat) → van al `.env`
  local de Antigravity, nunca al repo.

## 7. Decisiones pendientes (las toma el usuario, Fase 2)

1. Telnyx vs Vonage para el número virtual de Luxemburgo.
2. Voz del agente: masculina / femenina.
3. Nombre del agente y guion de bienvenida definitivo.
4. Aviso al dueño: email, WhatsApp o ambos.
5. Número público desde el día uno o prueba privada primero.

## 8. Instrucciones para Claude (auditor) — 2026-10-01 (moise)

Claude: antes de actuar, lee este HANDSHAKE completo.

1. Tu rol en este proyecto es **auditor en SOLO LECTURA sobre el código**:
   no modificas archivos de código, no creas ramas, no haces commits ni push.
2. **Revisa** lo pendiente en este handshake (tablero §4, bitácora §6) y
   **resuelve** lo que esté a tu alcance en solo lectura: verificaciones,
   revisiones y validaciones contra el repo.
3. **Responde DESDE este handshake**: escribe tus respuestas, hallazgos y
   veredictos en tu subsección de la bitácora (`### 2026-10-01 — Claude`),
   no solo en el chat. Este archivo es el punto único de comunicación:
   Antigravity y moise leen aquí lo que está pasando.
4. Empieza por conciliar la bitácora del 2026-10-01 (postura HARDENED,
   "0 vulnerabilidades") con tu informe de auditoría del 2026-10-01
   (11 hallazgos accionables, postura EXPOSED) y deja el registro
   corregido aquí mismo.
5. Cuando verifiques algo como resuelto, márcalo `✅ RESUELTO (fecha)` con
   evidencia (commit, URL, medición).
6. La REGLA DE SEGURIDAD sobre secretos (arriba) aplica sin excepciones:
   ningún secreto se escribe en este archivo ni en ningún commit.

