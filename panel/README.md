# Panel del cliente (La Centralita)

Archivos: `index.html` (estructura, sin scripts ni estilos inline), `app.js` (lógica), `style.css`, `config.js` (despliegue, sin secretos).

- **Modo en vivo**: backend alcanzable + token de operador (`CENTRALITA_AUTH_TOKEN`), introducido en el panel o pasado una vez como `#token=...`; vive solo en `sessionStorage`.
- **Modo demo**: sin backend o sin token; datos de ejemplo con banner visible. Nunca se mezclan con datos reales.
- **Transporte**: WebSocket `/ws/monitor` (auth por mensaje) o polling `GET /api/events` en backends serverless (`config.js` → `transport`).
- **Despliegue estático**: `firebase deploy --only hosting:la-centralita` (fijar antes `apiBase` en `config.js` y el dominio en `CORS_ALLOWED_ORIGINS` del backend).
- **Integridad**: `tests/test_panel_integrity.py` exige estilo para cada clase usada, ids existentes, acciones registradas y cero handlers/estilos inline.
