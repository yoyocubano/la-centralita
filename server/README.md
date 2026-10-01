# 📡 La Centralita — Servidor Backend & Bus de Eventos en Tiempo Real

Backend de alto rendimiento construido sobre **FastAPI**, diseñado para operar como el núcleo de telecomunicaciones e integración comercial de **La Centralita** (WELUX Events, Luxemburgo).

---

## 🏛️ Arquitectura & Capacidades

1. **Gestión de Sesiones WebRTC (LiveKit Cloud)**:
   - Emisión de tokens JWT temporales con alcance delimitado por sala (`centralita-test`, `centralita-demo`).
   - Grants granulares (`room_join`, `can_publish`, `can_subscribe`, `can_publish_data`).
2. **WebSocket Hub en Tiempo Real (`/ws/monitor`)**:
   - Conexión persistente y broadcast hacia los monitores de cliente/operaciones (NOC).
   - Sincronización en vivo de ondas de audio, transcripción parcial y final, y métricas de llamada.
3. **Persistencia & Sincronización Híbrida**:
   - Caché en memoria para baja latencia.
   - Respaldo e hidratación automática desde la cola local (`data/leads_queue.json`) y caché sincronizada (`data/leads_synced.json`).
4. **Integración con Servicios Externos**:
   - **Google Sheets**: Sincronización idempotente de prospectos comerciales.
   - **DocuSeal**: Webhook de estado de firmas digitales conformes al estándar europeo eIDAS.
   - **n8n / Twenty CRM**: Emisión de eventos y sincronización bidireccional.
5. **Endurecimiento de Seguridad (Auditoría Claude & Cloudflare Framework)**:
   - CORS restringido con listas blancas explícitas de orígenes.
   - Cabeceras HTTP estrictas (`X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `X-Request-ID`).
   - Validación estricta con esquemas **Pydantic v2** (`extra="forbid"`).
   - Logging estructurado en **JSON estándar** para trazabilidad y observabilidad.

---

## 🔒 Autenticación & Autorización

Los endpoints protegidos exigen el encabezado estándar:
```http
Authorization: Bearer <TOKEN>
```

Los tokens autorizados se resuelven contra:
- `Config.AUTH_TOKEN` (variable de entorno `AUTH_TOKEN`)
- `Config.LIVEKIT_API_SECRET`
- `centralita-secure-token-2026` (token interno de fallback para desarrollo seguro)

---

## 📋 Catálogo de Endpoints REST & WebSockets

### 1. `GET /api/status`
Diagnóstico y healthcheck de los servicios del pipeline.
- **Autenticación**: Pública.
- **Respuesta (200 OK)**:
```json
{
  "status": "ready",
  "services": {
    "livekit": true,
    "deepgram": true,
    "deepseek": true,
    "sheets": true,
    "resend": true
  },
  "livekit_url": "wss://welux-centralita.livekit.cloud",
  "n8n_webhook": "Configurado",
  "monitors_connected": 1
}
```

---

### 2. `GET /api/token`
Genera un token JWT de LiveKit firmado criptográficamente para unirse a la sala WebRTC.
- **Autenticación**: Requerida (`Bearer <token>`).
- **Parámetros Query**:
  - `room` (string, opcional, por defecto `centralita-test`): Debe pertenecer a la lista blanca de salas autorizadas (`centralita-test`, `centralita-demo`).
  - `identity` (string, opcional): Identificador único del cliente.
  - `name` (string, opcional): Nombre visible del usuario.
- **Respuesta (200 OK)**:
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "url": "wss://welux-centralita.livekit.cloud",
  "room": "centralita-test",
  "identity": "cliente-a1b2c3"
}
```
- **Errores**:
  - `401 Unauthorized`: Si falta el header `Authorization` o el token es inválido.
  - `403 Forbidden`: Si se solicita una sala no autorizada.
  - `503 Service Unavailable`: Si faltan credenciales en el servidor.

---

### 3. `WS /ws/monitor`
Canal bidireccional WebSocket para el panel de monitorización del cliente.
- **Autenticación**: Parámetro query `?token=<TOKEN_VALIDO>`. Si el token no es válido, la conexión se cierra con código `4001 Unauthorized`.
- **Mensaje inicial**:
```json
{
  "type": "connection_established",
  "timestamp": "2026-10-01T14:30:00Z",
  "status": "ready",
  "message": "Conectado al bus de eventos de La Centralita"
}
```
- **Acciones recibidas del cliente**:
  - `{"action": "ping"}` -> Responde con `{"type": "pong", "timestamp": "..."}`
  - `{"action": "get_recent_data"}` -> Responde con `{"type": "recent_data", "calls": [...], "leads": [...]}`

---

### 4. `POST /api/call-event`
Recibe eventos operativos emitidos por el agente de voz durante o al finalizar una llamada.
- **Autenticación**: Requerida (`Bearer <token>`).
- **Validación**:
  - Esquema Pydantic v2 `CallEventModel`.
  - Whitelist estricta de claves: `type`, `call_id`, `timestamp`, `status`, `room`, `duration`, `transcript`, `lead`, `agent`. Cualquier clave extra es rechazada con `400 Bad Request`.
- **Cuerpo del Mensaje (Ejemplo)**:
```json
{
  "type": "call_ended",
  "call_id": "call-101",
  "room": "centralita-test",
  "duration": "02:18",
  "status": "completed",
  "lead": {
    "nombre": "Pierre Meyers",
    "telefono": "+352 691 452 890",
    "empresa": "Consultora Kirchberg",
    "motivo": "Alquiler Fotoespejo"
  }
}
```
- **Respuesta (200 OK)**:
```json
{
  "status": "broadcasted",
  "receivers": 2
}
```

---

### 5. `POST /api/docuseal/webhook`
Recibe notificaciones de estado de los contratos enviados para firma digital.
- **Autenticación**: Abierta para el servicio webhook de DocuSeal (con verificación de payload).
- **Cuerpo del Mensaje (Ejemplo)**:
```json
{
  "event_type": "submission.completed",
  "data": {
    "id": "DOCUSEAL-WLX-2026-0941",
    "email": "pierre@kirchberg.lu"
  }
}
```
- **Respuesta (200 OK)**:
```json
{
  "status": "ok",
  "event": "submission.completed",
  "docuseal_status": "FIRMADO"
}
```

---

### 6. `GET /api/calls`
Devuelve el historial en memoria de todas las llamadas registradas.
- **Respuesta (200 OK)**:
```json
{
  "calls": [ ... ],
  "count": 4
}
```

---

### 7. `GET /api/leads`
Devuelve la bandeja unificada de prospectos capturados (con hidratación desde colas de persistencia).
- **Respuesta (200 OK)**:
```json
{
  "leads": [ ... ],
  "count": 6
}
```

---

### 8. `GET /api/system/internal`
Métricas internas del negocio y retorno de inversión (ROI):
- Horas de trabajo ahorradas (cálculo sobre recepcionista en Luxemburgo @ 22 €/hora).
- Ahorro económico acumulado.
- Desglose de costo por llamada de IA (~$0.00445 / llamada).
- Estado de la cola de Google Sheets y componentes de infraestructura.

---

### 9. `POST /api/test-webhook`
Endpoint de diagnóstico que genera un lead de prueba simulado, lo procesa con `PostCallProcessor` y lo difunde a todos los monitores activos.

---

## 🛡️ Esquema de Respuestas de Error

Todos los errores generados por la API retornan un sobre estructurado JSON:
```json
{
  "error": true,
  "status_code": 403,
  "detail": "Acceso denegado: la sala 'sala-invalida' no está autorizada. Salas permitidas: centralita-demo, centralita-test",
  "timestamp": "2026-10-01T14:32:00.123456+00:00",
  "path": "/api/token"
}
```

---

## 🧪 Pruebas Automatizadas

La suite completa de tests de la API y el backend se ejecuta con:
```bash
PYTHONPATH=. pytest tests/test_server_hardening.py -v
PYTHONPATH=. pytest tests/ -v
```
Todos los endpoints cuentan con validación de:
- Autenticación e inyección de tokens inválidos.
- Rechazo de atributos no permitidos (anti-inyección).
- Validación de encabezados de endurecimiento HTTP.
- Serialización de logs estructurados en JSON.
