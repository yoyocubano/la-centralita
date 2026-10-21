# n8n — workflow post-llamada

Workflow de prueba: **Centralita - Test Post-Llamada**
Webhook (POST): `https://tu-instancia-n8n.com/webhook/tu-secret-path-aqui`
(la URL real es una URL-capacidad: vive solo en el `.env` local, nunca en el repo)

## Payloads reales que llegan al webhook

Hay dos emisores y ninguno envía el lead completo en claro: la transcripción
va anonimizada con `redact_pii()` y el lead viaja como `lead_hash` (SHA-256)
más un resumen sin datos de contacto (mitigación H-005 / RGPD).

### 1. Servidor — `PostCallProcessor.process_call_ended` (`agent/post_call.py`)

Es el payload de `/api/test-webhook` y del procesado post-llamada del servidor:

```json
{
  "event": "call_ended",
  "timestamp": "2026-10-01T12:00:00+00:00",
  "room_name": "centralita-test",
  "participant_id": "cliente-abc123",
  "duration_seconds": 46.2,
  "lead_hash": "<sha256 del lead en JSON ordenado>",
  "transcripcion": "Cliente: Hola...\nSofía (WELUX): Encantada...",
  "lead_resumen": {
    "motivo": "Quiere cotización para una web",
    "tipo_evento": "Servicios B2B (Web, Chatbot & CRM)",
    "fecha_evento": null,
    "es_lead_valido": true
  },
  "metricas": {
    "duracion_minutos": 0.77,
    "total_turnos": 6,
    "costo_estimado_llamada_usd": 0.003464
  }
}
```

### 2. Worker de voz — `post_to_n8n` al terminar la sesión (`agent/agent.py`)

```json
{
  "event": "call_ended",
  "room": "centralita-test",
  "transcript": "Cliente: Hola...\nSofía (WELUX): Encantada...",
  "lead_hash": "<sha256 del lead en JSON ordenado>",
  "lead_resumen": {
    "motivo": "Quiere cotización para una web",
    "tipo_evento": "Servicios B2B (Web, Chatbot & CRM)",
    "es_lead_valido": true
  },
  "agent": "Sofía (WELUX Events)"
}
```

## Evolución prevista (Fase 2+)

- Guardar transcripción en Google Drive / Storage.
- Crear/actualizar lead en el CRM (Google Sheet + Supabase, mismos destinos
  que los leads del chatbot, sin tocar su código).
- Avisar al dueño por el canal elegido (email / WhatsApp) con transcripción
  o resumen.
