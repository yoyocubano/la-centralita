-- La deduplicación de leads la hace el id determinista del backend (hash de teléfono,
-- nombre, fecha y motivo). El índice único (tenant, teléfono, email) de 0001 rechazaba
-- un segundo lead legítimo del mismo cliente con otro motivo: se sustituye por uno normal.
DROP INDEX IF EXISTS uq_leads_tenant_phone_email;
CREATE INDEX IF NOT EXISTS idx_leads_tenant_phone ON leads (tenant_id, telefono);
