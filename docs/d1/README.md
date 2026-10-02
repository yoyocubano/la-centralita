# Cloudflare D1 para La Centralita

**Base:** `la-centralita-db` · id `8d49e194-6de2-46e2-8d73-144c35ef2ecd` · región **WEUR** (servida desde LHR).
Migraciones `0001` y `0002` aplicadas en remoto (7 tablas, vacías). `welux-events-db` intacta (pertenece a welux-events / Rebeca AI, no se reutiliza).

## Arquitectura (opción B, implementada)

```
FastAPI (Vercel) / worker de voz ──HTTPS, Bearer GATEWAY_SECRET + X-Tenant-Id──▶ Worker d1-gateway ──binding──▶ D1
```

- Worker: [`workers/d1-gateway`](../../workers/d1-gateway) — API interna `/v1/*` (leads, calls, turns, events, appointments, ratelimit). SQL parametrizado, aislamiento por tenant (409/404 entre tenants), secreto comparado en tiempo constante. El token de Cloudflare no sale de Cloudflare.
- Esquema: [`workers/d1-gateway/migrations/`](../../workers/d1-gateway/migrations) (`0001` idempotente; `0002` quita la unicidad teléfono+email, que rechazaba leads legítimos).
- Cliente Python: `agent/d1_gateway.py`. Se activa solo con `D1_GATEWAY_URL` + `D1_GATEWAY_SECRET` (≥ 32 caracteres). Sin ellas el backend sigue igual (memoria/ficheros + Google Sheets).

**Qué usa D1 cuando está activo**
| Función | Comportamiento | Si D1 falla |
|---|---|---|
| `/api/call-event` | guarda llamada, turnos y lead (`persisted`) | `persisted:false`, el evento se emite igual |
| `/api/events` (polling) | cursor duradero entre instancias (`instance:"d1"`) | buffer de la instancia |
| `/api/leads`, `/api/calls`, `/api/appointments` | lectura de D1 | Sheets / memoria / fichero (con `d1_error`) |
| `/api/leads/{id}/stage` | D1 primero; Sheets best-effort | 502 (nunca "updated" falso) |
| `/api/client-identify` | lead `portal` con `consent_at` (`d1_status`) | Sheets + email como antes |
| Rate-limits (identify, auth, demo) | compartidos entre instancias | limitador en memoria |
| Agente `book_technical_meeting` | además del `.jsonl` local | queda en el `.jsonl` |

## Despliegue

**Desplegado 2026-10-02:** `https://la-centralita-d1-gateway.yucolaguilar.workers.dev` (sin auth → 401; `/v1/health` → `db:true`). Preview URLs desactivadas. Migraciones registradas en `d1_migrations`. Pendiente: `D1_GATEWAY_URL` / `D1_GATEWAY_SECRET` en Vercel y en el worker de voz.

Para redesplegar:

Requiere un token de Cloudflare con **Workers Scripts:Edit + D1:Edit** (el token actual es solo D1 y no puede desplegar Workers).

```bash
cd workers/d1-gateway
npm ci
export CLOUDFLARE_API_TOKEN=...            # solo en la terminal, nunca en ficheros
npx wrangler secret put GATEWAY_SECRET     # pegar un valor aleatorio >= 32 chars (p. ej. openssl rand -hex 32)
npm run migrate:remote                     # idempotente: 0001/0002 ya están aplicadas
npm run deploy                             # imprime la URL *.workers.dev
```

Después, en Vercel (y en el entorno del worker de voz): `D1_GATEWAY_URL=<url del worker>`, `D1_GATEWAY_SECRET=<mismo valor>`, `CENTRALITA_TENANT_ID=welux`. Comprobar con `GET /api/status/details` → `d1_configured: true`.

## Desarrollo local

```bash
cd workers/d1-gateway
echo "GATEWAY_SECRET=$(openssl rand -hex 24)" > .dev.vars   # gitignored
npm run migrate:local && npm run dev                         # http://127.0.0.1:8787
```

## Historial de acceso
- Token 1: sin D1 (revocado). Token 2: D1 solo lectura. Token 3: sin D1. → **revocar 2 y 3**.
- Token 5: Workers Scripts:Edit + D1:Edit (desplegó el Worker) → revocar o rotar tras el despliegue si no se usará en CI.
- Token 4: restringido a D1 (lectura/escritura). Con él / el conector se creó la base y se aplicaron las migraciones.
