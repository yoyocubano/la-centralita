# n8n — eventos post-llamada (anonimizados)

Webhook (POST): `N8N_WEBHOOK_URL` (privado, definido solo en `.env`).
Autenticación: cabecera `X-Centralita-Secret: <N8N_WEBHOOK_SECRET>` (credencial *Header Auth* en n8n).

## Reparto de responsabilidades
- **Backend** (`agent/post_call.py`): escribe los leads con PII en la pestaña `Leads` del Sheet (modo RAW) y envía el email al dueño.
- **n8n** (`workflows/la-centralita-post-call.json`): recibe un evento SIN PII y lo registra en la pestaña `Llamadas`. No escribe leads (evita duplicados y el bug `#ERROR!` de USER_ENTERED).

## Payload que envía el backend
```json
{
  "event": "call_ended",
  "simulated": false,
  "timestamp": "2026-10-01T12:00:00+00:00",
  "timestamp_lux": "2026-10-01 14:00:00 (CEST)",
  "room_name": "centralita-test",
  "duration_seconds": 125.4,
  "lead_id": "a1b2c3d4e5f60718",
  "lead_hash": "<sha256>",
  "transcripcion": "Cliente: Soy [NAME_REDACTED], mi número es [PHONE_REDACTED]...",
  "lead_resumen": {"motivo": "...", "tipo_evento": "...", "fecha_evento": "2026-11-18", "es_lead_valido": true},
  "docuseal_status": "BORRADOR",
  "metricas": {"duracion_minutos": 2.09, "total_turnos": 12, "costo_estimado_llamada_usd": 0.009}
}
```
