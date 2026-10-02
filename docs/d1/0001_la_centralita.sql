-- La Centralita — esquema D1 (SQLite) propuesto · 2026-10-02
-- Base dedicada: la-centralita-db, ubicación EU (weur) por RGPD.
-- NO reutilizar welux-events-db (pertenece al chatbot Rebeca AI; regla del HANDSHAKE).
-- Sustituye el estado efímero actual: deques en memoria del servidor (calls, leads,
-- eventos) y ficheros en DATA_DIR (cola de Sheets, bandeja de email, citas), que en
-- Vercel/serverless se pierden entre instancias.
-- tenant_id: la centralita es un producto B2B; cada cliente (empresa) es un tenant.

PRAGMA foreign_keys = ON;

-- Leads: registro maestro (hoy Google Sheets "Leads", que pasa a ser una vista exportada).
CREATE TABLE leads (
  id                TEXT PRIMARY KEY,               -- hash determinista (GoogleSheetsSync.generate_lead_id)
  tenant_id         TEXT NOT NULL,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  nombre            TEXT,
  telefono          TEXT,                           -- texto literal (+352 ...), nunca número
  email             TEXT,
  empresa           TEXT,
  motivo            TEXT,
  detalles          TEXT,
  fecha_evento      TEXT,
  tipo_evento       TEXT,
  valor_eur         TEXT NOT NULL DEFAULT 'Por cotizar',
  stage             TEXT NOT NULL DEFAULT 'nuevo' CHECK (stage IN ('nuevo','contactado','agendado','ganado')),
  docuseal_status   TEXT NOT NULL DEFAULT 'BORRADOR',
  docuseal_url      TEXT,
  source            TEXT NOT NULL CHECK (source IN ('llamada','portal','whatsapp','manual')),
  consent_at        TEXT,                           -- consentimiento RGPD (portal)
  sheets_synced_at  TEXT,                           -- exportación a Google Sheets (si se mantiene)
  erased_at         TEXT                            -- supresión RGPD: PII anulada, fila conservada para métricas
);
CREATE INDEX idx_leads_tenant_created ON leads (tenant_id, created_at DESC);
CREATE INDEX idx_leads_tenant_stage   ON leads (tenant_id, stage);
CREATE UNIQUE INDEX uq_leads_tenant_phone_email ON leads (tenant_id, telefono, email) WHERE erased_at IS NULL;

-- Llamadas atendidas por Sofía.
CREATE TABLE calls (
  id                TEXT PRIMARY KEY,               -- call-<uuid> (MonitorEmitter.call_id)
  tenant_id         TEXT NOT NULL,
  room              TEXT NOT NULL,
  started_at        TEXT NOT NULL,
  ended_at          TEXT,
  duration_seconds  REAL,
  status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','ended','transfer_requested','failed')),
  lead_id           TEXT REFERENCES leads(id) ON DELETE SET NULL,
  tts_provider      TEXT,                           -- cosyvoice | piper | elevenlabs
  cost_usd          REAL,
  n8n_delivered     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_calls_tenant_started ON calls (tenant_id, started_at DESC);

-- Turnos de la transcripción (permite reconstruir el panel en vivo vía polling).
CREATE TABLE call_turns (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id   TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  seq       INTEGER NOT NULL,
  role      TEXT NOT NULL CHECK (role IN ('user','assistant')),
  text      TEXT NOT NULL,
  at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (call_id, seq)
);

-- Bus de eventos para el panel (sustituye EVENT_LOG en memoria; GET /api/events?after=<id>).
CREATE TABLE monitor_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id  TEXT NOT NULL,
  type       TEXT NOT NULL,
  payload    TEXT NOT NULL,                         -- JSON
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_events_tenant_id ON monitor_events (tenant_id, id);

-- Solicitudes de cita registradas por la herramienta book_technical_meeting.
CREATE TABLE appointment_requests (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  call_id         TEXT REFERENCES calls(id) ON DELETE SET NULL,
  client_name     TEXT,
  phone           TEXT,
  event_type      TEXT,
  requested_date  TEXT,
  requested_time  TEXT,
  status          TEXT NOT NULL DEFAULT 'PENDIENTE_CONFIRMACION'
                  CHECK (status IN ('PENDIENTE_CONFIRMACION','CONFIRMADA','RECHAZADA')),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX idx_appt_tenant_date ON appointment_requests (tenant_id, requested_date);

-- Bandeja de salida de emails (sustituye data/email_outbox.jsonl).
CREATE TABLE email_outbox (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id   TEXT NOT NULL,
  recipient   TEXT NOT NULL,
  subject     TEXT NOT NULL,
  body_text   TEXT NOT NULL,
  body_html   TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','FAILED')),
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  sent_at     TEXT
);
CREATE INDEX idx_outbox_pending ON email_outbox (status, created_at);

-- Rate-limit compartido entre instancias serverless (hoy en memoria por instancia).
CREATE TABLE rate_limits (
  bucket       TEXT NOT NULL,                       -- p. ej. 'identify:<ip>'
  window_start INTEGER NOT NULL,                    -- epoch s, ventana fija
  hits         INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, window_start)
);
