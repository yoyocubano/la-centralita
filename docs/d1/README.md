# Cloudflare D1 para La Centralita

**Creada 2026-10-02:** `la-centralita-db` · id `8d49e194-6de2-46e2-8d73-144c35ef2ecd` · región **WEUR** (servida desde LHR).
Esquema `0001_la_centralita.sql` aplicado: 7 tablas + 7 índices, vacías. `welux-events-db` intacta.
El backend todavía NO la usa (sigue con memoria/ficheros + Google Sheets): falta elegir opción 1 o 2 de abajo.


**Estado verificado**
- Token de API original: válido para zonas, Workers y KV, **sin permiso D1** (`/accounts/{id}/d1/database` → `10000 Authentication error`).
- Segundo token (2026-10-02): D1 **solo lectura** (lista bases, no puede escribir); además lee Workers, KV, zonas y Pages.
- Conector Cloudflare de Claude (OAuth): D1 lectura y escritura en la misma cuenta (con él se creó la base).
- D1 existente: `welux-events-db` (US-East, 8 tablas: `chat_logs`, `whatsapp_conversations`, `client_inquiries`…). Pertenece a welux-events / Rebeca AI → **no se reutiliza** (regla del HANDSHAKE).

**Propuesta**
- Base nueva `la-centralita-db` en `weur` (datos personales de clientes UE → en la UE).
- Esquema: [`0001_la_centralita.sql`](0001_la_centralita.sql). Cubre todo lo que hoy es efímero en el backend: leads, llamadas y turnos de transcripción, bus de eventos del panel, solicitudes de cita, bandeja de email y rate-limit compartido.
- Google Sheets pasa a ser exportación (columna `sheets_synced_at`), no el registro maestro.
- `tenant_id` en todas las tablas raíz: la centralita es producto B2B.

**Acceso desde el backend (FastAPI en Vercel)**
D1 no tiene driver nativo para Python fuera de Workers. Dos opciones:
1. API HTTP de D1 (`POST /accounts/{id}/d1/database/{db}/query`) con un token **solo D1:Edit** de esta base. Simple; +latencia por consulta.
2. Un Worker propio que exponga un API interna sobre el binding D1 (recomendado para producción: menor latencia y el token no sale de Cloudflare).

**Pendiente de decisión del dueño**
1. ~~Crear `la-centralita-db` (weur) y aplicar `0001_la_centralita.sql`.~~ Hecho.
2. Crear un token **restringido** (D1 Edit sobre esta cuenta), en vez de usar el token de acceso total.
3. Elegir opción 1 o 2.
