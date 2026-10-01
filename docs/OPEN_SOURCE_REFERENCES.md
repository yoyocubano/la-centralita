# Reutilización de Arquitectura Open Source en La Centralita

> **Documentación de referencias open source verificadas.**
> Este documento detalla qué componentes, modelos de datos y patrones de diseño han sido adaptados y reutilizados de los proyectos de referencia: **Twenty** (CRM), **Metabase** (BI/Dashboards) y **Dograh** (Telefonía IA & MCP).

---

## 1. Twenty CRM (`twentyhq/twenty` · ~57k ⭐ en GitHub)

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
- **Conclusión**: No es necesario escribir un CRM propio desde cero. El monitor actual de La Centralita funciona como visualizador operativo en vivo, y se conecta bidireccionalmente con la instancia de Twenty del cliente final mediante n8n.

---

## 2. Metabase (`metabase/metabase` · ~49k ⭐ en GitHub)

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

## 3. Dograh (`dograh-hq/dograh` · ~5.8k ⭐ en GitHub)

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

## 4. Matriz de Síntesis Arquitectónica

| Módulo | Proyecto de Referencia | Aporte Concreto Integrado |
| :--- | :--- | :--- |
| **Bandeja de Leads** | `twentyhq/twenty` | Modelo Persona/Oportunidad, etapas Kanban (`Nuevo` → `Contactado` → `Agendado` → `Ganado`), conector API documentado. |
| **Dashboard y Estado** | `metabase/metabase` | Tarjetas KPI con deltas, embudo de conversión, distribución horaria de tráfico y monitor de SLAs de latencia. |
| **Pipeline y Agenda** | `dograh-hq/dograh` | Turn-taking oral ágil, barge-in sin pausas, tool-calling para slots de calendario y confirmación oral natural. |
| **Transporte WebRTC** | `LiveKit Cloud` | Transporte ultra-eficiente SIP/WebRTC, audio Opus a 48kHz, sin dependencias de Twilio. |
| **Post-Llamada** | `n8n` | Webhook centralizado (`centralita-test`) para distribución hacia CRM, correo y mensajería. |
