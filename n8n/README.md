# n8n — workflow post-llamada

Workflow de prueba: **Centralita - Test Post-Llamada**
Webhook (POST): `https://weluxdigitalservices.app.n8n.cloud/webhook/centralita-test`

## Payload que envía el agente al colgar

```json
{
  "transcript": "user: hola...\nassistant: ¡Hola! Soy la recepcionista de WELUX...",
  "lead": {
    "nombre": "María López",
    "telefono": "+352 621 000 000",
    "motivo": "Quiere cotización para una web",
    "fecha_interes": null,
    "detalles": "Prefiere que la llamen por la tarde",
    "requiere_seguimiento": true
  },
  "agent": "Centralita WELUX"
}
```

## Evolución prevista (Fase 2+)

- Guardar transcripción en Google Drive / Storage.
- Crear/actualizar lead en el CRM (Google Sheet + Supabase, mismos destinos
  que los leads del chatbot, sin tocar su código).
- Avisar al dueño por el canal elegido (email / WhatsApp) con transcripción
  o resumen.
