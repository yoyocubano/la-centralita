# Reutilización de Arquitectura Open Source en La Centralita

> **Documentación de referencias open source verificadas.**
> Este documento detalla qué componentes, modelos de datos y patrones de diseño han sido adaptados y reutilizados de las notas de arquitectura del proyecto:
> - **Nota 24:** Twenty (CRM), Metabase (BI/Dashboards), Dograh (Telefonía IA & MCP), DocuSeal (Firma Digital Electrónica).
> - **Nota 25 & Nota extra:** Cloudflare Security Audit Skill (`cloudflare/security-audit-skill`).

---

## 1. Twenty CRM (`twentyhq/twenty` · ~57k ⭐ en GitHub) [Nota 24]

### Rol en el proyecto
Modelo de referencia y estándar de datos para el módulo **Bandeja de Leads & Oportunidades** del Panel del Cliente de La Centralita.

### Qué se reutilizó y adaptó
1. **Modelo de Entidades Normalizado**:
   - **`Person` (Contacto)**: `firstName`, `lastName`, `phone` (formato E.164 con prefijo luxemburgués `+352`), `company`, `email`.
   - **`Opportunity` (Oportunidad Comercial)**: `name`, `amount` estimado, `stage` (etapa del pipeline), `closeDate`, `technicalRequirements` (chips de sonido, iluminación, DJ, etc.).
   - **`Activity.Call` (Registro de Interacción)**: transcripción estructurada, duración, identificador de sala LiveKit, archivo de audio y análisis de sentimiento.
2. **Pipeline de Estados Comercial (Kanban & Tabla)**:
   - `NEW` (Lead recién extraído de la llamada).
   - `CONTACTED` (Primer contacto de seguimiento realizado vía llamada o WhatsApp).
   - `SCHEDULED` (Reunión técnica o visita agendada en calendario).
   - `WON` (Presupuesto aceptado / Contratado).
   - `LOST` (Llamada no calificada o descartada).
3. **Interfaz de Usuario**:
   - Selector dual de vista en el panel: **Vista de Tabla Detallada** y **Vista de Tablero Kanban** con conteo por columna y badges de valor.

### Dictamen de Integración Directa con Twenty
- **Factibilidad**: **ALTA (100% compatible)**.
- **Vía de Integración Recomendada**:
  - En la Fase 1 actual, el despacho post-llamada va a **n8n**. n8n dispone de nodos nativos HTTP y conector para Twenty.
  - El webhook de n8n recibe el payload de `agent/agent.py` y genera directamente:
    1. `POST /rest/people` (crea o actualiza el contacto con su teléfono `+352`).
    2. `POST /rest/opportunities` (crea la oportunidad vinculada con los requerimientos técnicos y fecha).
    3. `POST /rest/activities` (adjunta la transcripción completa de la llamada como nota auditada).

---

## 2. Metabase (`metabase/metabase` · ~49k ⭐ en GitHub) [Nota 24]

### Rol en el proyecto
Estándar visual y de diseño para el **Dashboard Ejecutivo de Métricas, SLAs y Analítica** en el Panel del Cliente.

### Qué se reutilizó y adaptó
1. **Sistema de Tarjetas KPI Ejecutivas**:
   - Números de gran tamaño con tipografía clara y limpia.
   - Indicadores de delta porcentual comparativo (`+18.4% vs semana previa`, `94% satisfacción`).
   - Micro-etiquetas semánticas con código de color (verde esmeralda, dorado ámbar y rojo alerta).
2. **Embudo de Conversión Comercial (Funnel)**:
   - Visualización por fases:
     *Llamadas Totales (100%) → Consultas Calificadas (78%) → Leads con Contacto (61%) → Citas Agendadas (33%)*.
3. **Distribución Temporal de Llamadas (Peak Hours)**:
   - Gráfico de barras de carga horaria (identificación de franjas pico entre 10:00 y 16:00 en Luxemburgo para optimizar turnos de operadores y capacidad del agente).
4. **Métricas de Rendimiento y SLAs Técnicos**:
   - Desglose de latencia por etapas: STT Streaming (<240ms) + Inferencia LLM (<420ms) + Síntesis TTS (<50ms).
   - Monitor de ahorro de costes operativos (€) frente a un operador telefónico físico o centralitas tradicionales.

---

## 3. Dograh (`dograh-hq/dograh` · ~5.8k ⭐ en GitHub) [Nota 24]

### Rol en el proyecto
Arquitectura de referencia para el **Pipeline de Voz con Tool-Calling (MCP)** y el módulo de **Agenda & Reserva Autónoma de Citas**.

### Qué se reutilizó y adaptó
1. **Turn-Taking y Manejo de Interrupciones (Barge-in)**:
   - Dograh implementa streaming bidireccional continuo con cancelación de síntesis acústica en cuanto el VAD detecta voz del usuario. Se adoptó este mismo principio en `agent/agent.py` mediante LiveKit Agents 1.8 (`allow_interruptions=True`, `min_interruption_duration=0.2s`).
2. **Mecanismo de Reserva de Citas por Function Calling**:
   - En lugar de limitarse a anotar la fecha como texto libre, el agente dispone de la capacidad de verificar disponibilidad y agendar la cita directamente en la llamada:
     - `check_calendar_slot(date, time)`
     - `book_calendar_appointment(client_name, phone, event_type, date, time)`
   - El guion de Sofía (`agent/prompts.py`) adopta las pautas de confirmación oral de Dograh: proponer dos opciones horarias concretas (*"¿Te vendría mejor el martes por la tarde a las 16:30 o prefieres el jueves por la mañana?"*) para acelerar el cierre.
3. **Sincronización Bidireccional de Citas**:
   - Las citas agendadas por Sofía se reflejan instantáneamente en la pestaña **Agenda** del panel, generando enlaces directos con un clic a Google Calendar / Outlook y disparando recordatorios a n8n.

---

## 4. DocuSeal (`docusealco/docuseal` · ~18.6k ⭐ en GitHub) [Nota 24]

### Rol en el proyecto
Motor de **Firma Digital Electrónica de Contratos y Presupuestos** integrado con el flujo comercial post-llamada.

### Qué se reutilizó y adaptó
1. **Flujo de Cierre y Firma eIDAS**:
   - Cuando un lead calificado pasa a estado `agendado` o solicita presupuesto formal, el operador o el workflow automático de n8n puede generar un contrato de servicios técnicos de WELUX Events S.à r.l.
2. **Componente Embebible & Notificaciones**:
   - Modelo de formulario de firma digital (`docuseal-form`) con campos pre-poblados: nombre del cliente, empresa, teléfono luxemburgués (`+352`), fecha del evento, paquete técnico contratado e importe en euros.
   - Sello criptográfico auditable de firma electrónica con fecha, hora e IP.
   - Disparo de evento webhook a n8n (`document.completed`) que actualiza la oportunidad en el CRM a `GANADO / CONTRATADO`.

---

## 5. Cloudflare Security Audit Skill (`cloudflare/security-audit-skill`) [Nota 25 & Nota extra]

### Rol en el proyecto
Framework automatizado de **Auditoría de Seguridad y Endurecimiento Defensivo** en 6 fases para agentes y backends.

### Qué se reutilizó y adaptó
1. **Metodología en 6 Fases**:
   - *Phase 1 (Reconnaissance)*: Mapeo de superficie de ataque (WebRTC, SIP, Webhooks n8n, TTS en memoria, Panel web).
   - *Phase 2 (Coverage Tracking)*: Generación y mantenimiento del ledger estructurado [`security/coverage-ledger.json`](../security/coverage-ledger.json).
   - *Phase 3 (Hunting & Vector Analysis)*: Búsqueda exhaustiva de vectores CWE (credenciales expuestas, XSS, inyección de prompts, secuestro de tokens).
   - *Phase 4 (Candidate Validation)*: Mecanismo de sub-agente validador para refutar falsos positivos antes del reporte final.
   - *Phase 5 (Findings Classification)*: Emisión del archivo canónico [`security/findings.json`](../security/findings.json) con severidades objetivas.
   - *Phase 6 (Reporting)*: Generación del informe ejecutivo [`docs/SECURITY_AUDIT.md`](./SECURITY_AUDIT.md).
2. **Monitor de Cumplimiento en el Panel**:
   - Tarjeta en vivo en la sección *Estado del Sistema* que muestra la postura de seguridad (0 credenciales en git, cifrado DTLS-SRTP, tokens JWT efímeros de 1h, cumplimiento RGPD Luxemburgo).

---

## 6. Matriz de Síntesis Arquitectónica

| Módulo | Fuente / Referencia | Origen | Aporte Concreto Integrado |
| :--- | :--- | :--- | :--- |
| **Bandeja de Leads** | `twentyhq/twenty` | Nota 24 | Modelo Persona/Oportunidad, etapas Kanban (`Nuevo` → `Contactado` → `Agendado` → `Ganado`), conector API documentado. |
| **Dashboard y Estado** | `metabase/metabase` | Nota 24 | Tarjetas KPI con deltas, embudo de conversión, distribución horaria de tráfico y monitor de SLAs de latencia. |
| **Pipeline y Agenda** | `dograh-hq/dograh` | Nota 24 | Turn-taking oral ágil, barge-in sin pausas, tool-calling para slots de calendario y confirmación oral natural. |
| **Firma Electrónica** | `docusealco/docuseal` | Nota 24 | Generación y firma electrónica de contratos/presupuestos de eventos con sellado digital eIDAS y webhook n8n. |
| **Auditoría y Seguridad** | `cloudflare/security-audit-skill` | Nota 25 / Extra | Protocolo en 6 fases, ledgers `findings.json` y `coverage-ledger.json`, verificación de secretos y blindaje de prompts. |
| **Transporte WebRTC** | `LiveKit Cloud` | Arquitectura Core | Transporte ultra-eficiente SIP/WebRTC, audio Opus a 48kHz, sin dependencias de Twilio. |
| **Post-Llamada** | `n8n` | Arquitectura Core | Webhook centralizado (`centralita-test`) para distribución hacia CRM, correo y mensajería. |
