# Backend de La Centralita en Vercel (serverless)

El backend FastAPI (`server/app.py`) se despliega en Vercel como **una sola Vercel
Function de Python** (`api/index.py`), a la que `vercel.json` reescribe todas las
rutas. El panel sigue en Firebase Hosting y el worker de voz LiveKit corre aparte,
**fuera** de Vercel.

> **Aviso sobre el plan Hobby.** Según la documentación de Vercel, el plan Hobby
> está restringido a *uso personal y no comercial* ([Hobby plan](https://vercel.com/docs/plans/hobby),
> [fair use guidelines](https://vercel.com/docs/limits/fair-use-guidelines#commercial-usage)).
> Para pruebas y demo es suficiente. Antes de atender a clientes reales de WELUX
> conviene confirmar si este uso encaja en Hobby o si hace falta otro plan u otro
> proveedor. No es un límite técnico: lo decide el dueño.

## Arquitectura

```
                    ┌──────────────────────────────┐
  Navegador ───────►│ Firebase Hosting             │  panel/ (estático)
  (operador)        │ la-centralita.web.app        │  config.js → apiBase = URL de Vercel
                    └──────────────┬───────────────┘
                                   │ HTTPS (CORS) + Bearer token
                                   ▼
                    ┌──────────────────────────────┐
  Formulario  ─────►│ Vercel Function (Python)     │  api/index.py → server.app:app
  portal / DocuSeal │ /api/*  (todas las rutas)    │  sin disco duradero, sin WebSocket
  webhook           └───┬──────────────┬───────────┘
                        │              │
                        ▼              ▼
              Google Sheets       SMTP (email)        LiveKit Cloud (solo emite JWT)
              (fuente de verdad)

  Worker de voz (agent/agent.py) ── servidor propio, proceso largo ──►
      LiveKit Cloud (audio)  +  POST /api/call-event a Vercel (eventos del panel)
```

Flujo de eventos en vivo sin WebSocket: el worker publica `POST /api/call-event`;
el backend los guarda en un buffer con cursor; el panel consulta
`GET /api/events?since=<cursor>&instance=<id>` cada 5 s (`pollIntervalMs` en
`panel/config.js`; 15 s con la pestaña oculta).

## Archivos del despliegue

| Archivo | Para qué |
| --- | --- |
| `api/index.py` | Entrypoint. Exporta `app` (ASGI, lo que Vercel ejecuta) y `lambda_handler = Mangum(app, lifespan="off")`. |
| `vercel.json` | `framework: null`, función `api/index.py` (`maxDuration` 60 s, excluye tests/docs/modelos) y rewrite `/(.*)` → `/api/index.py`. |
| `requirements.txt` | Solo dependencias del backend (+ `mangum`). Lo instala Vercel. |
| `agent/requirements.txt` | Dependencias del worker de voz (LiveKit Agents, Piper…). **No** las instala Vercel. |
| `tests/test_vercel_serverless.py` | Entrypoint, Mangum, disco no escribible, polling, CORS y rate-limit tras el proxy. |

### Por qué Mangum no se llama `handler`

Vercel reserva el nombre de nivel superior `handler` para **clases** que heredan de
`http.server.BaseHTTPRequestHandler` ([Python en /api](https://vercel.com/docs/functions/runtimes/python/api-directory#supported-handlers)).
`handler = Mangum(app)` es una *instancia*: Vercel la tomaría como handler y el
despliegue fallaría. Por eso Vercel usa `app` (ASGI nativo) y el adaptador Mangum
queda como `lambda_handler`, útil si algún día se lleva la misma app a AWS Lambda.
Un test impide que vuelva a aparecer `handler` en `api/index.py`.

### Por qué se separaron las dependencias

Con las dependencias del worker (LiveKit Agents, plugins, Piper/ONNX), el
`site-packages` medido ocupa **556 MB**, por encima del límite de 500 MB del bundle de
Python en Vercel ([límites](https://vercel.com/docs/functions/runtimes/python#controlling-what-gets-bundled)).
Solo el backend: **135 MB**. El backend no importa nada del worker.

## Variables de entorno

Se configuran en Vercel → *Project* → *Settings* → *Environment Variables*
(entorno *Production*, y *Preview* si se usan previews). Ningún valor va en el repo.

| Variable | Obligatoria | Uso |
| --- | --- | --- |
| `CENTRALITA_AUTH_TOKEN` | Sí | Token de operador (mínimo 24 caracteres). Sin él, todo endpoint protegido responde 503. El worker usa el mismo valor para `POST /api/call-event`. |
| `CORS_ALLOWED_ORIGINS` | Recomendada | Lista separada por comas. Si se deja vacía se usan los valores por defecto del código, que ya incluyen `https://la-centralita.web.app` y `https://la-centralita.firebaseapp.com` (más GitHub Pages y localhost). En producción conviene fijarla solo a los dominios reales del panel. |
| `DOCUSEAL_WEBHOOK_SECRET` | Para DocuSeal | Secreto compartido que DocuSeal envía en la cabecera del webhook. Sin él, `/api/docuseal/webhook` responde 503. |
| `DOCUSEAL_WEBHOOK_HEADER` | No | Nombre de la cabecera (por defecto `X-Docuseal-Secret`). |
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | Para Sheets | JSON completo de la cuenta de servicio de Google (contenido, no ruta). |
| `GOOGLE_SHEET_ID` | Para Sheets | ID del spreadsheet de leads. |
| `GOOGLE_SHEET_TAB` | No | Pestaña (por defecto `Leads`). |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Recomendada | Envío de notificaciones. **En Vercel sin SMTP los emails no llegan**: la bandeja local vive en `/tmp` y se pierde (ver límites). |
| `NOTIFICATION_EMAIL` | No | Destinatario de las notificaciones. |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Para tokens WebRTC | `/api/token` y la demo pública. Sin ellas responden 503. |
| `PUBLIC_DEMO_ENABLED`, `PUBLIC_DEMO_ROOM` | No | Demo pública de la landing (desactivada por defecto). |
| `DEEPSEEK_API_KEY` (+ `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`) | No | Extracción de lead en `/api/test-webhook`. |
| `N8N_WEBHOOK_SECRET`, `N8N_WEBHOOK_URL` | No | n8n se elimina: dejarlas vacías. El backend sigue funcionando sin ellas. |
| `ENABLE_API_DOCS` | No | `1` para exponer `/api/docs`. Por defecto desactivado. |
| `CENTRALITA_DATA_DIR` | No | Carpeta de datos local. En Vercel, por defecto `/tmp/la-centralita-data`. |
| `CENTRALITA_SERVERLESS` | No | Fuerza el modo serverless fuera de Vercel (pruebas). En Vercel no hace falta: se detecta por la variable `VERCEL` que pone la plataforma. |

`PORT` y `HOST` no se usan en Vercel (solo con `uvicorn` local).

## Pasos de despliegue

1. **Crear el proyecto** en [vercel.com/new](https://vercel.com/new) importando
   `yoyocubano/la-centralita` (cuenta Hobby, sin tarjeta).
   - *Root Directory*: la raíz del repo.
   - *Framework Preset*: **Other** (coincide con `"framework": null` de `vercel.json`).
   - Rama de producción: la que el dueño decida. Esta rama es `feature/centralita-voz`;
     Vercel puede desplegar previews de ella sin tocar `main`.
2. **Variables de entorno**: cargar las de la tabla anterior antes del primer deploy.
3. **Deploy** desde el dashboard, o con la CLI:
   ```bash
   npm i -g vercel
   vercel link          # vincula la carpeta con el proyecto
   vercel deploy        # preview
   vercel deploy --prod # producción
   ```
4. **Comprobar**:
   ```bash
   curl https://<proyecto>.vercel.app/api/status
   # → {"status": ..., "auth_configured": true, ...}
   curl -H "Authorization: Bearer $CENTRALITA_AUTH_TOKEN" \
        https://<proyecto>.vercel.app/api/status/details
   # → ... "serverless": true, "realtime_transport": "polling"
   ```
5. **Panel (Firebase)**: en `panel/config.js` poner `apiBase: "https://<proyecto>.vercel.app"`
   y desplegar con `firebase deploy --only hosting:la-centralita`. El panel lee
   `realtime_transport` de `/api/status/details` y usa polling sin intentar WebSocket.
6. **DocuSeal**: apuntar el webhook a `https://<proyecto>.vercel.app/api/docuseal/webhook`
   con la cabecera secreta configurada.
7. **Worker de voz** (aparte, ver abajo): `CENTRALITA_API_URL=https://<proyecto>.vercel.app`.

## Worker de voz LiveKit (fuera de Vercel)

`agent/agent.py` es un proceso largo: mantiene una conexión con LiveKit Cloud,
recibe audio en tiempo real, llama a Deepgram/DeepSeek/TTS y vive mientras dure
cada llamada. Eso no cabe en una función serverless (duración máxima, sin
conexiones persistentes salientes de larga vida, bundle demasiado grande). Se
arranca por separado en cualquier máquina con Python 3.11+ y salida a Internet:

```bash
cd agent
cp .env.example .env    # rellenar claves (LiveKit, Deepgram, DeepSeek, TTS…)
pip install -r requirements.txt
# Para que el panel reciba los eventos de la llamada:
#   CENTRALITA_API_URL=https://<proyecto>.vercel.app
#   CENTRALITA_AUTH_TOKEN=<el mismo valor que en Vercel>
python agent.py start   # producción (python agent.py dev para desarrollo)
```

El TTS (CosyVoice, requiere GPU) sigue aparcado; este cambio no lo toca.

## Límites conocidos

| Límite | Efecto | Qué hacer |
| --- | --- | --- |
| Estado en memoria por instancia | `CALLS_DATABASE`, leads de sesión, buffer de eventos y rate-limiters viven en la memoria de cada instancia y se pierden en arranque en frío. | Los leads reales se leen siempre de Google Sheets (fuente de verdad). El historial de llamadas de `/api/calls` y las métricas de `/api/system/internal` son solo de la instancia actual. |
| Eventos en vivo entre instancias | Si el `POST /api/call-event` del worker cae en una instancia y el polling del panel en otra, el panel no ve ese evento. El parámetro `instance` evita cursores inválidos, pero no comparte eventos. Fluid compute suele reutilizar la misma instancia con poco tráfico, sin garantía. | El panel refresca leads y llamadas por su cuenta cada 15 s. Para tiempo real fiable: un almacén compartido (p. ej. Redis/KV del Marketplace de Vercel) detrás de `EventLog`. No implementado. |
| WebSocket `/ws/monitor` | No se usa en Vercel. El endpoint sigue en el código para despliegues con servidor propio (`uvicorn`). | El panel usa polling si el backend anuncia `realtime_transport: "polling"`, si `config.js` fija `transport: "polling"`, o tras 2 aperturas de WebSocket fallidas. |
| Disco efímero (`/tmp`) | Cola local de Sheets (`leads_queue.json`), marcas de idempotencia y bandeja de email (`email_outbox.jsonl`) se escriben en `/tmp/la-centralita-data` y desaparecen con la instancia. Si el disco no fuera escribible, la petición no falla: el estado devuelto es `NOT_PERSISTED_*`. | Configurar Google Sheets y SMTP en Vercel para que el camino normal no dependa del disco. |
| Solicitudes de cita | El worker las escribe en SU disco (`data/appointment_requests.jsonl`); `/api/appointments` en Vercel devolverá una lista vacía. | Pendiente: que el worker las envíe al backend o a Sheets. |
| Rate-limit por IP | En Vercel la IP se toma de `X-Forwarded-For` (Vercel la sobrescribe, no es falsificable allí). Los contadores son por instancia, así que el límite es aproximado. Fuera de serverless la cabecera se ignora. | Si hay abuso: reglas del Firewall de Vercel. |
| Peticiones del polling | Cada panel abierto hace una petición cada 5 s: unas 518 000 al mes si está abierto 24/7 (a 3 s serían unas 864 000). Cuentan para los límites de invocaciones del plan. | Revisar *Usage* en el dashboard de Vercel; subir `pollIntervalMs` si hace falta. |
| Duración | `maxDuration` 60 s. El peor caso de `/api/client-identify` (3 reintentos a Sheets + SMTP con timeout de 15 s) cabe. | — |
| Arranque en frío | La primera petición tras inactividad tarda más (importar FastAPI, livekit-api, openai). | Aceptable para panel y formularios. |
| Sin procesos en segundo plano | La app no define `lifespan` ni lanza tareas al arrancar, y Mangum va con `lifespan="off"`. | No añadir tareas de fondo: en serverless se congelan entre peticiones. |

## Desarrollo local

```bash
pip install -r requirements.txt                  # backend
pip install -r agent/requirements.txt            # + worker (para toda la suite de tests)
pip install pytest pytest-asyncio httpx
pytest -q

# Simular el modo Vercel en local:
CENTRALITA_SERVERLESS=1 CENTRALITA_AUTH_TOKEN=... uvicorn api.index:app --port 8080
# o con la CLI de Vercel: vercel dev
```
