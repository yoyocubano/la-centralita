/**
 * La Centralita — gateway interno sobre Cloudflare D1.
 *
 * Solo lo llama el backend FastAPI (servidor a servidor):
 *   Authorization: Bearer <GATEWAY_SECRET>   (comparación en tiempo constante)
 *   X-Tenant-Id: <tenant>                    (todas las consultas se filtran por tenant)
 *
 * Todas las consultas son parametrizadas; los campos de entrada pasan por lista blanca.
 * Sin secreto configurado el gateway responde 503 (fail-closed).
 */

const TENANT_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
const STAGES = new Set(["nuevo", "contactado", "agendado", "ganado"]);
const LEAD_SOURCES = new Set(["llamada", "portal", "whatsapp", "manual"]);
const CALL_STATUS = new Set(["active", "ended", "transfer_requested", "failed"]);
const LEAD_FIELDS = [
  "nombre", "telefono", "email", "empresa", "motivo", "detalles", "fecha_evento", "tipo_evento",
  "valor_eur", "stage", "docuseal_status", "docuseal_url", "source", "consent_at", "sheets_synced_at",
];
const MAX_TEXT = 4000;

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });

function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] || 0) ^ (y[i] || 0);
  return diff === 0;
}

function authorize(request, env) {
  const secret = env.GATEWAY_SECRET || "";
  if (secret.length < 32) throw new HttpError(503, "GATEWAY_SECRET no configurado (mínimo 32 caracteres)");
  const [scheme, token] = (request.headers.get("authorization") || "").split(" ");
  if (scheme !== "Bearer" || !token || !timingSafeEqual(token, secret)) throw new HttpError(401, "no autorizado");
  const tenant = request.headers.get("x-tenant-id") || "";
  if (!TENANT_RE.test(tenant)) throw new HttpError(400, "X-Tenant-Id inválido");
  return tenant;
}

async function body(request) {
  let data;
  try {
    data = await request.json();
  } catch {
    throw new HttpError(400, "JSON inválido");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new HttpError(400, "se esperaba un objeto JSON");
  return data;
}

const text = (v, max = MAX_TEXT) => (v === undefined || v === null ? null : String(v).slice(0, max));
const id = (v, what) => {
  if (!ID_RE.test(String(v || ""))) throw new HttpError(400, `${what} inválido`);
  return String(v);
};
const limitParam = (url, def = 100, max = 500) => Math.min(Math.max(parseInt(url.searchParams.get("limit") || def, 10) || def, 1), max);
const now = () => new Date().toISOString();

// ------------------------------------------------------------------ leads
async function upsertLead(env, tenant, data) {
  const leadId = id(data.id, "id");
  const source = data.source || "manual";
  if (!LEAD_SOURCES.has(source)) throw new HttpError(400, "source inválido");
  const stage = data.stage || "nuevo";
  if (!STAGES.has(stage)) throw new HttpError(400, "stage inválido");
  // Solo se insertan/actualizan los campos recibidos: los ausentes conservan su valor
  // (o el DEFAULT del esquema en altas nuevas). Nunca se pisa un valor con NULL.
  const given = LEAD_FIELDS.filter((f) => f === "source" || f === "stage" || (data[f] !== undefined && data[f] !== null));
  const values = given.map((f) => (f === "source" ? source : f === "stage" ? stage : text(data[f])));
  const updates = given.filter((f) => f !== "source" && !(f === "stage" && data.stage === undefined))
    .map((f) => `${f} = excluded.${f}`).concat(["updated_at = ?"]).join(", ");
  const res = await env.DB.prepare(
    `INSERT INTO leads (id, tenant_id, ${given.join(", ")}) VALUES (?, ?, ${given.map(() => "?").join(", ")})
     ON CONFLICT(id) DO UPDATE SET ${updates}
     WHERE leads.tenant_id = excluded.tenant_id`
  ).bind(leadId, tenant, ...values, now()).run();
  if (!res.meta.changes) throw new HttpError(409, "id de lead perteneciente a otro tenant");
  return { id: leadId };
}

async function listLeads(env, tenant, url) {
  const { results } = await env.DB.prepare(
    `SELECT * FROM leads WHERE tenant_id = ? AND erased_at IS NULL ORDER BY created_at DESC LIMIT ?`
  ).bind(tenant, limitParam(url)).all();
  return { leads: results };
}

async function setStage(env, tenant, leadId, data) {
  if (!STAGES.has(data.stage)) throw new HttpError(400, "stage inválido");
  const res = await env.DB.prepare(`UPDATE leads SET stage = ?, updated_at = ? WHERE id = ? AND tenant_id = ?`)
    .bind(data.stage, now(), id(leadId, "id"), tenant).run();
  if (!res.meta.changes) throw new HttpError(404, "lead no encontrado");
  return { id: leadId, stage: data.stage };
}

// ------------------------------------------------------------------ llamadas
async function upsertCall(env, tenant, data) {
  const callId = id(data.id, "id");
  const status = data.status || "active";
  if (!CALL_STATUS.has(status)) throw new HttpError(400, "status inválido");
  const res = await env.DB.prepare(
    `INSERT INTO calls (id, tenant_id, room, started_at, ended_at, duration_seconds, status, lead_id, tts_provider, cost_usd, n8n_delivered)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
     ON CONFLICT(id) DO UPDATE SET
       ended_at = COALESCE(excluded.ended_at, calls.ended_at),
       duration_seconds = COALESCE(excluded.duration_seconds, calls.duration_seconds),
       status = excluded.status,
       lead_id = COALESCE(excluded.lead_id, calls.lead_id),
       tts_provider = COALESCE(excluded.tts_provider, calls.tts_provider),
       cost_usd = COALESCE(excluded.cost_usd, calls.cost_usd),
       n8n_delivered = MAX(excluded.n8n_delivered, calls.n8n_delivered)
     WHERE calls.tenant_id = excluded.tenant_id`
  ).bind(
    callId, tenant, text(data.room, 128) || "desconocida", text(data.started_at, 64) || now(),
    text(data.ended_at, 64), Number.isFinite(data.duration_seconds) ? data.duration_seconds : null, status,
    data.lead_id ? id(data.lead_id, "lead_id") : null, text(data.tts_provider, 32),
    Number.isFinite(data.cost_usd) ? data.cost_usd : null, data.n8n_delivered ? 1 : 0,
  ).run();
  if (!res.meta.changes) throw new HttpError(409, "id de llamada perteneciente a otro tenant");
  return { id: callId };
}

async function addTurn(env, tenant, callId, data) {
  callId = id(callId, "id");
  if (!["user", "assistant"].includes(data.role)) throw new HttpError(400, "role inválido");
  const owner = await env.DB.prepare(`SELECT 1 FROM calls WHERE id = ? AND tenant_id = ?`).bind(callId, tenant).first();
  if (!owner) throw new HttpError(404, "llamada no encontrada");
  const row = await env.DB.prepare(
    `INSERT INTO call_turns (call_id, seq, role, text)
     VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM call_turns WHERE call_id = ?), ?, ?) RETURNING seq`
  ).bind(callId, callId, data.role, text(data.text) || "").first();
  return { call_id: callId, seq: row.seq };
}

async function listCalls(env, tenant, url) {
  const { results } = await env.DB.prepare(
    `SELECT c.*, l.nombre AS lead_nombre, l.telefono AS lead_telefono, l.motivo AS lead_motivo,
            (SELECT group_concat(CASE t.role WHEN 'user' THEN 'Cliente: ' ELSE 'Sofía: ' END || t.text, char(10))
               FROM (SELECT role, text FROM call_turns WHERE call_id = c.id ORDER BY seq) t) AS transcript
       FROM calls c LEFT JOIN leads l ON l.id = c.lead_id AND l.tenant_id = c.tenant_id
      WHERE c.tenant_id = ? ORDER BY c.started_at DESC LIMIT ?`
  ).bind(tenant, limitParam(url, 50, 200)).all();
  return { calls: results };
}

// ------------------------------------------------------------------ eventos del panel
async function appendEvent(env, tenant, data) {
  const type = text(data.type, 64);
  if (!type) throw new HttpError(400, "type requerido");
  const payload = JSON.stringify(data.payload ?? {});
  if (payload.length > 64000) throw new HttpError(413, "payload demasiado grande");
  const row = await env.DB.prepare(`INSERT INTO monitor_events (tenant_id, type, payload) VALUES (?, ?, ?) RETURNING id`)
    .bind(tenant, type, payload).first();
  return { id: row.id };
}

async function eventsSince(env, tenant, url) {
  const after = Math.max(parseInt(url.searchParams.get("after") || "0", 10) || 0, 0);
  const { results } = await env.DB.prepare(
    `SELECT id, type, payload, created_at FROM monitor_events WHERE tenant_id = ? AND id > ? ORDER BY id LIMIT ?`
  ).bind(tenant, after, limitParam(url, 100, 200)).all();
  return { events: results.map((r) => ({ id: r.id, type: r.type, created_at: r.created_at, payload: JSON.parse(r.payload) })) };
}

// ------------------------------------------------------------------ citas
async function addAppointment(env, tenant, data) {
  const apptId = id(data.id, "id");
  await env.DB.prepare(
    `INSERT INTO appointment_requests (id, tenant_id, call_id, client_name, phone, event_type, requested_date, requested_time)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING`
  ).bind(apptId, tenant, data.call_id ? id(data.call_id, "call_id") : null, text(data.client_name, 200), text(data.phone, 40),
    text(data.event_type, 200), text(data.requested_date, 32), text(data.requested_time, 16)).run();
  return { id: apptId };
}

async function listAppointments(env, tenant, url) {
  const { results } = await env.DB.prepare(
    `SELECT * FROM appointment_requests WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?`
  ).bind(tenant, limitParam(url, 200, 500)).all();
  return { appointments: results };
}

// ------------------------------------------------------------------ rate-limit compartido
async function rateLimitHit(env, tenant, data) {
  const bucket = text(data.bucket, 200);
  const windowSeconds = parseInt(data.window_seconds, 10);
  const max = parseInt(data.max, 10);
  if (!bucket || !(windowSeconds > 0) || !(max > 0)) throw new HttpError(400, "bucket/window_seconds/max inválidos");
  const windowStart = Math.floor(Date.now() / 1000 / windowSeconds) * windowSeconds;
  const row = await env.DB.prepare(
    `INSERT INTO rate_limits (bucket, window_start, hits) VALUES (?, ?, 1)
     ON CONFLICT(bucket, window_start) DO UPDATE SET hits = hits + 1 RETURNING hits`
  ).bind(`${tenant}:${bucket}`, windowStart).first();
  return { allowed: row.hits <= max, hits: row.hits };
}

// ------------------------------------------------------------------ router
const ROUTES = [
  ["GET", /^\/v1\/health$/, async (env) => ({ ok: true, db: !!(await env.DB.prepare("SELECT 1 AS ok").first()) })],
  ["POST", /^\/v1\/leads$/, (env, t, req) => body(req).then((d) => upsertLead(env, t, d))],
  ["GET", /^\/v1\/leads$/, (env, t, req, url) => listLeads(env, t, url)],
  ["POST", /^\/v1\/leads\/([^/]+)\/stage$/, (env, t, req, url, m) => body(req).then((d) => setStage(env, t, decodeURIComponent(m[1]), d))],
  ["POST", /^\/v1\/calls$/, (env, t, req) => body(req).then((d) => upsertCall(env, t, d))],
  ["GET", /^\/v1\/calls$/, (env, t, req, url) => listCalls(env, t, url)],
  ["POST", /^\/v1\/calls\/([^/]+)\/turns$/, (env, t, req, url, m) => body(req).then((d) => addTurn(env, t, decodeURIComponent(m[1]), d))],
  ["POST", /^\/v1\/events$/, (env, t, req) => body(req).then((d) => appendEvent(env, t, d))],
  ["GET", /^\/v1\/events$/, (env, t, req, url) => eventsSince(env, t, url)],
  ["POST", /^\/v1\/appointments$/, (env, t, req) => body(req).then((d) => addAppointment(env, t, d))],
  ["GET", /^\/v1\/appointments$/, (env, t, req, url) => listAppointments(env, t, url)],
  ["POST", /^\/v1\/ratelimit$/, (env, t, req) => body(req).then((d) => rateLimitHit(env, t, d))],
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      const tenant = authorize(request, env);
      for (const [method, pattern, handler] of ROUTES) {
        const match = url.pathname.match(pattern);
        if (match && request.method === method) return json(await handler(env, tenant, request, url, match));
      }
      throw new HttpError(404, "ruta no encontrada");
    } catch (err) {
      if (err instanceof HttpError) return json({ error: err.message }, err.status);
      const constraint = /constraint/i.test(String(err && err.message));
      console.error("d1-gateway", err && err.message);
      return json({ error: constraint ? "violación de restricción" : "error interno" }, constraint ? 409 : 500);
    }
  },
};
