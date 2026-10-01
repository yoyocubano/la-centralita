/**
 * LA CENTRALITA — PANEL DEL CLIENTE (MONITOR EN VIVO)
 *
 * Dos modos, siempre declarados en pantalla:
 *  - EN VIVO: backend alcanzable + token de operador válido. Todos los datos salen del
 *    backend (Google Sheets, eventos del agente de voz). Nunca se mezclan datos de ejemplo.
 *  - DEMO: sin backend o sin token. Datos de ejemplo locales, con banner visible.
 *
 * Seguridad:
 *  - Sin credenciales en el código. El token se introduce en el panel (o llega una vez
 *    como #token=... en la URL, que se elimina al instante) y vive solo en sessionStorage.
 *  - WebSocket autenticado por mensaje (el token no viaja en la URL).
 *  - Cero inyección de HTML: todo el contenido dinámico usa textContent / nodos DOM.
 *  - Sin handlers inline: eventos delegados vía data-action (CSP sin 'unsafe-inline').
 */
"use strict";

// ==============================================================================
// 0. CONFIGURACIÓN, TOKEN Y UTILIDADES
// ==============================================================================

const CONFIG = Object.assign({ apiBase: "" }, window.CENTRALITA_CONFIG || {});
const TOKEN_KEY = "centralita_operator_token";
const CLIENT_KEY = "welux_current_client";
const STAGES = ["nuevo", "contactado", "agendado", "ganado"];

/** Base de la API: config explícita, mismo origen si el backend sirve el panel, o null (sin backend). */
function resolveApiBase() {
  if (CONFIG.apiBase) return String(CONFIG.apiBase).replace(/\/$/, "");
  const servedByBackend = window.location.pathname.startsWith("/panel");
  return servedByBackend ? window.location.origin : null;
}

const API_BASE = resolveApiBase();

function storageGet(store, key) {
  try { return store.getItem(key); } catch { return null; }
}

function storageSet(store, key, value) {
  try { store.setItem(key, value); } catch { /* almacenamiento bloqueado: se ignora */ }
}

function storageRemove(store, key) {
  try { store.removeItem(key); } catch { /* ignorado */ }
}

/** Captura un token pasado una vez por URL (#token= o ?token=) y lo borra de la barra de direcciones. */
function captureTokenFromUrl() {
  const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const queryParams = new URLSearchParams(window.location.search);
  const token = hashParams.get("token") || queryParams.get("token");
  if (!token) return;
  storageSet(sessionStorage, TOKEN_KEY, token.trim());
  queryParams.delete("token");
  const query = queryParams.toString();
  history.replaceState(null, "", window.location.pathname + (query ? `?${query}` : ""));
}

function getToken() {
  return storageGet(sessionStorage, TOKEN_KEY) || "";
}

function jsonParseSafe(raw, fallback = null) {
  try { return JSON.parse(raw); } catch { return fallback; }
}

function $(id) {
  return document.getElementById(id);
}

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function nowTimeLabel() {
  const now = new Date();
  return `${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;
}

function downloadBlob(content, mime, filename) {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

/** Celda CSV segura: escapa comillas y neutraliza fórmulas (CWE-1236). */
function csvCell(value) {
  let text = value === null || value === undefined ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function digitsOnly(phone) {
  return String(phone || "").replace(/[^0-9]/g, "");
}

class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
  }
}

/** fetch al backend con token y timeout. */
async function apiFetch(path, { method = "GET", body = null, auth = true, timeoutMs = 10000 } = {}) {
  if (API_BASE === null) throw new ApiError(0, "Sin backend configurado");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const headers = { "Accept": "application/json" };
  if (body !== null) headers["Content-Type"] = "application/json";
  if (auth && getToken()) headers["Authorization"] = `Bearer ${getToken()}`;
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== null ? JSON.stringify(body) : null,
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, data.detail || `HTTP ${res.status}`);
    return data;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    throw new ApiError(0, err.name === "AbortError" ? "Tiempo de espera agotado" : "Backend no alcanzable");
  } finally {
    clearTimeout(timer);
  }
}

// ==============================================================================
// 1. ESTADO GLOBAL Y MODO (EN VIVO / DEMO)
// ==============================================================================

const state = {
  mode: "connecting",          // connecting | live | demo
  calls: [],
  leads: [],
  leadsMeta: null,             // { source, degraded, sheet_error, rows_needing_repair }
  appointments: [],
  callFilter: "all",
  leadFilter: "all",
  leadsView: "cards",
};

function isLive() {
  return state.mode === "live";
}

function setMode(mode, message, tone = "info") {
  state.mode = mode;
  document.body.dataset.mode = mode;
  const banner = $("modeBanner");
  if (banner) {
    banner.hidden = !message;
    banner.dataset.tone = tone;
  }
  setText("modeBannerText", message || "");
  const btnConnect = $("btnConnectBackend");
  if (btnConnect) btnConnect.hidden = mode === "live" || API_BASE === null;
  setText("modeTag", mode === "live" ? "Fase 1 · Operación en vivo" : "Fase 1 · Modo demo (datos de ejemplo)");
}

function updateConnectionPill(text, status, rtt) {
  setText("connText", text);
  if (rtt !== undefined) setText("liveLatencyBadge", rtt);
  const dot = $("connDot");
  if (dot) dot.dataset.status = status;
  const navDot = $("navStatusDot");
  if (navDot) navDot.dataset.status = status;
}

// ==============================================================================
// 2. PROVEEDORES DE DATOS
// ==============================================================================

/** Datos de EJEMPLO para el modo demo (nunca se usan en modo en vivo). */
const DEMO_CALLS = [
  {
    id: "demo-call-101", date: "Hoy, 13:42", client: "Pierre Meyers", phone: "+352 691 000 101",
    duration: "02:18", operator: "Sofía (IA)", reason: "Alquiler Fotoespejo (Photobooth)", hasLead: true,
    transcript: "Sofía: ¡Hola! Gracias por llamar. Soy Sofía, ¿en qué podemos asesorarte hoy?\nPierre Meyers: Queremos alquilar un fotoespejo para nuestra jornada de empresa el 14 de noviembre.\nSofía: ¡Excelente iniciativa! ¿Cuántos asistentes calculan?\nPierre Meyers: Unas 120 personas en Kirchberg.\nSofía: Tomo nota, el equipo te enviará la propuesta hoy mismo.",
  },
  {
    id: "demo-call-102", date: "Hoy, 12:15", client: "Camille Wagner", phone: "+352 621 000 102",
    duration: "01:45", operator: "Sofía (IA)", reason: "Asesoría de Negocios y Procesos", hasLead: true,
    transcript: "Sofía: Te atiende Sofía, ¿cómo podemos ayudarte?\nCamille Wagner: Buscamos asesoría para automatizar la atención comercial telefónica.\nSofía: ¿Te vendría bien una sesión de diagnóstico de 30 minutos?\nCamille Wagner: Sí, estupendo.",
  },
  {
    id: "demo-call-103", date: "Ayer, 11:05", client: "Alexandre Dupont", phone: "+352 661 000 103",
    duration: "03:10", operator: "Sofía (IA)", reason: "Web Corporativa, Chatbot & CRM", hasLead: false,
    transcript: "Sofía: ¡Hola! Soy Sofía, ¿en qué te puedo apoyar?\nAlexandre Dupont: Solo quería información general, gracias.",
  },
];

const DEMO_LEADS = [
  { id: "demo-lead-1", name: "Pierre Meyers", phone: "+352 691 000 101", company: "Consultora Kirchberg", interest: "Alquiler Fotoespejo (Photobooth)", eventDate: "14 Nov 2026", stage: "nuevo", summary: "Fotoespejo para jornada de empresa (120 personas) en Kirchberg.", timestamp: "Hoy, 13:42", amount: "Por cotizar", docusealStatus: "BORRADOR" },
  { id: "demo-lead-2", name: "Camille Wagner", phone: "+352 621 000 102", company: "Wagner Logistics SARL", interest: "Asesoría de Negocios y Procesos", eventDate: "28 Oct 2026", stage: "contactado", summary: "Optimización de flujos comerciales y atención telefónica 24/7.", timestamp: "Hoy, 12:15", amount: "Por cotizar", docusealStatus: "BORRADOR" },
  { id: "demo-lead-3", name: "Marc Becker", phone: "+352 691 000 104", company: "Becker Auto Bertrange", interest: "Alquiler Inflables (Billar & Minigolf)", eventDate: "22 Oct 2026", stage: "agendado", summary: "Inflables de minigolf y billar para jornada de puertas abiertas.", timestamp: "Ayer, 10:20", amount: "Por cotizar", docusealStatus: "ENVIADO" },
  { id: "demo-lead-4", name: "Sophie Laurent", phone: "+352 661 000 105", company: "Particular", interest: "Producción técnica de gala", eventDate: "15 May 2027", stage: "ganado", summary: "Producción audiovisual e iluminación para gala privada.", timestamp: "Ayer, 16:30", amount: "Por cotizar", docusealStatus: "FIRMADO" },
];

const DEMO_APPOINTMENTS = [
  { id: "demo-appt-1", client_name: "Sophie Laurent", event_type: "Reunión técnica", requested_date: isoDateOffset(3), requested_time: "16:30", status: "PENDIENTE_CONFIRMACION" },
  { id: "demo-appt-2", client_name: "Marc Becker", event_type: "Reunión comercial", requested_date: isoDateOffset(7), requested_time: "11:00", status: "PENDIENTE_CONFIRMACION" },
];

function isoDateOffset(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

class DemoProvider {
  constructor() {
    this.callsKey = "welux_centralita_demo_calls_v5";
    this.leadsKey = "welux_centralita_demo_leads_v5";
    if (!storageGet(localStorage, this.callsKey)) storageSet(localStorage, this.callsKey, JSON.stringify(DEMO_CALLS));
    if (!storageGet(localStorage, this.leadsKey)) storageSet(localStorage, this.leadsKey, JSON.stringify(DEMO_LEADS));
  }

  async getCalls() {
    return jsonParseSafe(storageGet(localStorage, this.callsKey), DEMO_CALLS) || DEMO_CALLS;
  }

  async getLeads() {
    const leads = jsonParseSafe(storageGet(localStorage, this.leadsKey), DEMO_LEADS) || DEMO_LEADS;
    return { leads, meta: { source: "demo", degraded: false } };
  }

  async getAppointments() {
    return DEMO_APPOINTMENTS;
  }

  async saveCall(call) {
    const calls = await this.getCalls();
    calls.unshift(call);
    storageSet(localStorage, this.callsKey, JSON.stringify(calls.slice(0, 50)));
  }

  async updateLeadStage(leadId, stage) {
    const { leads } = await this.getLeads();
    const target = leads.find(l => l.id === leadId);
    if (target) target.stage = stage;
    storageSet(localStorage, this.leadsKey, JSON.stringify(leads));
  }
}

class LiveProvider {
  async getCalls() {
    const data = await apiFetch("/api/calls");
    return (data.calls || []).map((c, idx) => ({
      id: c.call_id || `call-${idx}`,
      date: c.timestamp ? new Date(c.timestamp).toLocaleString("es-ES", { timeZone: "Europe/Luxembourg" }) : "—",
      client: (c.lead && c.lead.nombre) || "Cliente sin identificar",
      phone: (c.lead && c.lead.telefono) || "—",
      duration: c.duration || "—",
      operator: c.agent || "Sofía (IA)",
      reason: (c.lead && c.lead.motivo) || "—",
      hasLead: Boolean(c.lead && (c.lead.nombre || c.lead.telefono)),
      transcript: typeof c.transcript === "string" ? c.transcript : JSON.stringify(c.transcript || "", null, 2),
      isToday: c.timestamp ? new Date(c.timestamp).toDateString() === new Date().toDateString() : false,
    }));
  }

  async getLeads() {
    const data = await apiFetch("/api/leads");
    const leads = (data.leads || []).map((l, idx) => ({
      id: l.id || `lead-${idx}`,
      name: l.nombre || l.name || "Sin nombre",
      phone: l.telefono || l.phone || "—",
      company: l.empresa || l.company || "—",
      interest: l.motivo || l.interest || "—",
      eventDate: l.fecha_evento || l.eventDate || "A convenir",
      stage: STAGES.includes(String(l.stage || "").toLowerCase()) ? String(l.stage).toLowerCase() : "nuevo",
      summary: l.detalles || l.summary || "",
      timestamp: l.timestamp_lux || l.timestamp || "—",
      amount: l.amount || l.valor_eur || "Por cotizar",
      docusealStatus: l.docuseal_status || "BORRADOR",
      docusealUrl: l.docuseal_url || null,
    }));
    return { leads, meta: data };
  }

  async getAppointments() {
    const data = await apiFetch("/api/appointments");
    return data.appointments || [];
  }

  async saveCall() {
    /* En vivo las llamadas las registra el backend. */
  }

  async updateLeadStage(leadId, stage) {
    await apiFetch(`/api/leads/${encodeURIComponent(leadId)}/stage`, { method: "POST", body: { stage } });
  }
}

let provider = new DemoProvider();

// ==============================================================================
// 3. ARRANQUE: DETECCIÓN DE MODO
// ==============================================================================

async function bootstrapMode() {
  captureTokenFromUrl();

  if (API_BASE === null) {
    enterDemoMode("MODO DEMO — este despliegue no tiene backend configurado: todos los datos son de ejemplo.");
    return;
  }
  if (!getToken()) {
    enterDemoMode("MODO DEMO — introduce el token de operador para ver datos reales.");
    return;
  }

  try {
    const details = await apiFetch("/api/status/details");
    enterLiveMode(details);
  } catch (err) {
    if (err.status === 401 || err.status === 403) {
      storageRemove(sessionStorage, TOKEN_KEY);
      enterDemoMode("Token de operador no válido. Mostrando datos de ejemplo.", "error");
    } else if (err.status === 503) {
      enterDemoMode("El backend no tiene la autenticación configurada (CENTRALITA_AUTH_TOKEN).", "error");
    } else {
      enterDemoMode(`MODO DEMO — backend no disponible (${err.message}). Datos de ejemplo.`, "error");
    }
  }
}

function enterDemoMode(message, tone = "warning") {
  provider = new DemoProvider();
  setMode("demo", message, tone);
  updateConnectionPill("MODO DEMO", "standby", "— ms");
  loadDemoLiveCall();
  refreshAll();
}

function enterLiveMode(details) {
  provider = new LiveProvider();
  setMode("live", "", "info");
  updateConnectionPill("BACKEND CONECTADO", "ok");
  resetLiveCallView();
  renderServiceStatus(details);
  refreshAll();
  monitorSocket.connect();
  startPolling();
}

function promptOperatorToken() {
  const token = (window.prompt("Token de operador (CENTRALITA_AUTH_TOKEN):") || "").trim();
  if (!token) return;
  storageSet(sessionStorage, TOKEN_KEY, token);
  bootstrapMode();
}

async function refreshAll() {
  await Promise.allSettled([renderCallsTable(), refreshLeads(), refreshAppointments()]);
  await refreshSystemMetrics();
}

let pollTimer = null;
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  // El Sheet es la fuente de verdad: polling ligero (el backend cachea 10 s).
  pollTimer = setInterval(() => {
    if (!isLive() || document.hidden) return;
    refreshLeads();
    refreshSystemMetrics();
  }, 15000);
}

// ==============================================================================
// 4. WEBSOCKET DEL MONITOR (AUTH POR MENSAJE, BACKOFF, HEARTBEAT, RTT REAL)
// ==============================================================================

class MonitorSocket {
  constructor() {
    this.ws = null;
    this.attempt = 0;
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
    this.pingSentAt = null;
    this.stopped = false;
  }

  url() {
    const base = new URL(API_BASE);
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
    base.pathname = "/ws/monitor";
    base.search = "";
    return base.toString();
  }

  connect() {
    if (API_BASE === null || !getToken()) return;
    this.stopped = false;
    clearTimeout(this.reconnectTimer);
    try {
      this.ws = new WebSocket(this.url());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.ws.send(JSON.stringify({ action: "auth", token: getToken() }));
    };
    this.ws.onmessage = (event) => {
      const msg = jsonParseSafe(event.data);
      if (msg) this.handle(msg);
    };
    this.ws.onclose = (event) => {
      this.stopHeartbeat();
      if (event.code === 4001) {
        updateConnectionPill("NO AUTORIZADO", "error", "—");
        storageRemove(sessionStorage, TOKEN_KEY);
        enterDemoMode("El backend rechazó el token del monitor (4001). Introduce un token válido.", "error");
        this.stopped = true;
        return;
      }
      if (event.code === 4003) {
        updateConnectionPill("ORIGEN NO PERMITIDO", "error", "—");
        setMode("live", "Este dominio no está en CORS_ALLOWED_ORIGINS del backend: sin eventos en vivo.", "error");
        this.stopped = true;
        return;
      }
      if (!this.stopped) {
        updateConnectionPill("RECONECTANDO…", "standby");
        this.scheduleReconnect();
      }
    };
    this.ws.onerror = () => { /* onclose gestiona el reintento */ };
  }

  handle(msg) {
    if (msg.type === "connection_established") {
      this.attempt = 0;
      updateConnectionPill("EN VIVO", "ok");
      if (state.mode === "live") setMode("live", "", "info");
      this.startHeartbeat();
      return;
    }
    if (msg.type === "pong") {
      if (this.pingSentAt !== null) {
        const rtt = Math.round(performance.now() - this.pingSentAt);
        setText("liveLatencyBadge", `${rtt} ms RTT`);
        setText("kpiRtt", `${rtt} ms`);
        this.pingSentAt = null;
      }
      return;
    }
    handleIncomingBackendEvent(msg);
  }

  startHeartbeat() {
    this.stopHeartbeat();
    const ping = () => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.pingSentAt = performance.now();
        this.ws.send(JSON.stringify({ action: "ping" }));
      }
    };
    ping();
    this.heartbeatTimer = setInterval(ping, 25000);
  }

  stopHeartbeat() {
    clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  scheduleReconnect() {
    // Backoff exponencial con jitter: 1 s, 2 s, 4 s … máx. 30 s.
    const delay = Math.min(30000, 1000 * 2 ** this.attempt) * (0.5 + Math.random() / 2);
    this.attempt = Math.min(this.attempt + 1, 6);
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  reconnectNow() {
    if (this.stopped || !isLive()) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;
    this.attempt = 0;
    this.connect();
  }
}

const monitorSocket = new MonitorSocket();
window.addEventListener("online", () => monitorSocket.reconnectNow());
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) monitorSocket.reconnectNow();
});

function handleIncomingBackendEvent(event) {
  switch (event.type) {
    case "call_started":
      startRealCallView(event);
      break;
    case "transcript_delta":
      appendStreamTurn({
        role: event.role === "assistant" ? "agent" : "customer",
        author: event.role === "assistant" ? "Sofía (IA WELUX)" : "Cliente",
        time: event.duration || nowTimeLabel(),
        text: event.text,
      });
      break;
    case "call_ended":
      endRealCallView(event);
      renderCallsTable();
      refreshLeads();
      refreshSystemMetrics();
      break;
    case "lead_created":
      refreshLeads();
      break;
    case "docuseal_update":
      refreshLeads();
      break;
    default:
      break;
  }
}

// ==============================================================================
// 5. VISTA EN VIVO
// ==============================================================================

let callDurationTimer = null;
let currentDurationSecs = 0;
let isCallActive = false;
let isSofiaSpeaking = false;
let isAudioMuted = false;

function startDurationTimer() {
  stopDurationTimer();
  currentDurationSecs = 0;
  setText("liveDurationTimer", "00:00");
  callDurationTimer = setInterval(() => {
    currentDurationSecs++;
    const m = String(Math.floor(currentDurationSecs / 60)).padStart(2, "0");
    const s = String(currentDurationSecs % 60).padStart(2, "0");
    setText("liveDurationTimer", `${m}:${s}`);
  }, 1000);
}

function stopDurationTimer() {
  if (callDurationTimer) clearInterval(callDurationTimer);
  callDurationTimer = null;
}

function setActiveCallBadge(active) {
  const badge = $("liveCallBadge");
  if (badge) {
    badge.textContent = active ? "1 ACTIVA" : "0 ACTIVAS";
    badge.classList.toggle("live", active);
  }
  const status = $("liveCallStatusBadge");
  if (status) {
    status.textContent = active ? "EN LLAMADA" : "SIN LLAMADA";
    status.classList.toggle("idle", !active);
  }
}

function clearStream(placeholder) {
  const container = $("liveChatStream");
  container.textContent = "";
  if (placeholder) {
    container.appendChild(createEmptyStateElement("🎧", placeholder.title, placeholder.desc));
  }
}

function resetLeadCard() {
  ["extLeadName", "extLeadPhone", "extLeadDate", "extLeadInterest", "extLeadStatus"].forEach(id => setText(id, "—"));
  setText("extLeadSummary", "El lead se extrae automáticamente al terminar la llamada.");
  $("extLeadTags").textContent = "";
}

function resetLiveCallView() {
  isCallActive = false;
  stopDurationTimer();
  setActiveCallBadge(false);
  setText("liveCallerName", "Sin llamada activa");
  setText("liveCallerNumber", "");
  setText("liveCallerAvatar", "—");
  setText("liveRoomName", "—");
  setText("liveDurationTimer", "00:00");
  setText("typingText", "Esperando llamada entrante…");
  setText("n8nSyncTag", "En espera");
  clearStream({ title: "Esperando llamada", desc: "La transcripción aparecerá aquí en tiempo real cuando Sofía atienda una llamada." });
  resetLeadCard();
}

function startRealCallView(event) {
  switchTab("envivo");
  isCallActive = true;
  setActiveCallBadge(true);
  setText("liveCallerName", "Llamada entrante");
  setText("liveCallerAvatar", "☎");
  setText("liveRoomName", event.room || "—");
  setText("typingText", "Sofía atendiendo la llamada…");
  setText("n8nSyncTag", "Llamada en curso");
  clearStream(null);
  resetLeadCard();
  startDurationTimer();
}

function endRealCallView(event) {
  isCallActive = false;
  stopDurationTimer();
  setActiveCallBadge(false);
  if (event.duration) setText("liveDurationTimer", event.duration);
  setText("typingText", "Llamada finalizada · lead procesado");
  if (event.lead) updateExtractedLeadCard(event.lead);
  setText("n8nSyncTag", event.lead ? "Lead registrado" : "Sin lead");
}

function appendStreamTurn(turn) {
  const container = $("liveChatStream");
  const empty = container.querySelector(".panel-empty-state");
  if (empty) empty.remove();

  const bubble = document.createElement("div");
  bubble.className = `stream-bubble ${turn.role === "agent" ? "agent" : "customer"}`;

  const metaDiv = document.createElement("div");
  metaDiv.className = "bubble-meta";
  const authorSpan = document.createElement("span");
  authorSpan.textContent = turn.author || "";
  const timeSpan = document.createElement("span");
  timeSpan.className = "bubble-time";
  timeSpan.textContent = turn.time || "";
  metaDiv.appendChild(authorSpan);
  metaDiv.appendChild(timeSpan);

  const textDiv = document.createElement("div");
  textDiv.className = "bubble-text";
  textDiv.textContent = turn.text || "";

  bubble.appendChild(metaDiv);
  bubble.appendChild(textDiv);
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

function updateExtractedLeadCard(lead) {
  setText("extLeadName", lead.nombre || "No indicado");
  setText("extLeadPhone", lead.telefono || "No indicado");
  setText("extLeadInterest", lead.motivo || "—");
  setText("extLeadDate", lead.fecha_evento || "A convenir");
  setText("extLeadSummary", lead.detalles || "");
  setText("extLeadStatus", lead.docuseal_status ? `Registrado · contrato ${lead.docuseal_status}` : "Registrado");
  const tags = $("extLeadTags");
  tags.textContent = "";
  (Array.isArray(lead.requerimientos_tecnicos) ? lead.requerimientos_tecnicos : []).forEach(r => {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = r;
    tags.appendChild(tag);
  });
  if (lead.nombre) {
    setText("liveCallerName", lead.nombre);
    setText("liveCallerAvatar", lead.nombre.split(/\s+/).map(p => p[0]).join("").slice(0, 2).toUpperCase());
  }
  if (lead.telefono) setText("liveCallerNumber", lead.telefono);
}

function initLiveVisualizer() {
  const canvas = $("waveformCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let step = 0;

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < 22; i++) {
      let height = 3;
      if (isSofiaSpeaking) height = Math.abs(Math.sin(step + i * 0.45)) * 24 + 4;
      else if (isCallActive) height = Math.abs(Math.sin(step * 0.5 + i * 0.3)) * 6 + 3;
      ctx.fillStyle = i % 2 === 0 ? "#0071e3" : "#34c759";
      ctx.fillRect(i * 7 + 4, (canvas.height - height) / 2, 4, height);
    }
    step += isSofiaSpeaking ? 0.18 : 0.06;
    if (!reduceMotion) requestAnimationFrame(draw);
  }
  draw();
}

// ---------------- Simulación local (solo modo demo) ----------------

const LIVE_CALL_SCRIPT = [
  { role: "agent", author: "Sofía (IA WELUX)", time: "00:03", text: "¡Hola! Gracias por llamar a WELUX en Luxemburgo. Soy Sofía, ¿en qué podemos asesorarte hoy?" },
  { role: "customer", author: "Jean-Luc Weber", time: "00:15", text: "Hola Sofía. Me llamo Jean-Luc Weber, de una consultora en Kirchberg. Queremos organizar nuestra gala de fin de año el 18 de noviembre para unas 150 personas." },
  { role: "agent", author: "Sofía (IA WELUX)", time: "00:30", text: "¡Qué buen evento, Jean-Luc! Contamos con iluminación, audio profesional y DJ para galas corporativas. ¿Ya tienen el salón reservado?" },
  { role: "customer", author: "Jean-Luc Weber", time: "00:48", text: "Sí, en Kirchberg. Necesitaremos también micrófonos inalámbricos para los discursos." },
  { role: "agent", author: "Sofía (IA WELUX)", time: "01:05", text: "Tomo nota de todo. El equipo preparará la propuesta personalizada y te la enviará hoy mismo. ¿Algún otro detalle?" },
  { role: "customer", author: "Jean-Luc Weber", time: "01:22", text: "No, con eso es suficiente. ¡Muchas gracias!" },
  { role: "agent", author: "Sofía (IA WELUX)", time: "01:30", text: "Un placer, Jean-Luc. ¡Que tengas un excelente día!" },
];

const DEMO_LEAD = {
  nombre: "Jean-Luc Weber",
  telefono: "+352 691 000 100",
  motivo: "Gala corporativa de fin de año (150 personas)",
  fecha_evento: "18 Nov 2026",
  detalles: "Producción técnica completa para salón en Kirchberg; requiere micrófonos para discursos.",
  requerimientos_tecnicos: ["Iluminación", "Sonido", "Micrófonos inalámbricos", "DJ"],
};

let streamIndex = 0;

function loadDemoLiveCall() {
  stopDurationTimer();
  clearStream(null);
  LIVE_CALL_SCRIPT.slice(0, 4).forEach(appendStreamTurn);
  streamIndex = 4;
  isCallActive = true;
  setActiveCallBadge(true);
  setText("liveCallerName", "Jean-Luc Weber (ejemplo)");
  setText("liveCallerNumber", "+352 691 000 100");
  setText("liveCallerAvatar", "JW");
  setText("liveRoomName", "demo-local");
  setText("liveDurationTimer", "00:48");
  setText("typingText", "Simulación local · no hay llamada real");
  setText("n8nSyncTag", "Demo");
  updateExtractedLeadCard(DEMO_LEAD);
  setText("extLeadStatus", "Ejemplo (modo demo)");
}

function triggerIncomingCallDemo() {
  if (isLive()) return;
  switchTab("envivo");
  clearStream(null);
  streamIndex = 0;
  isCallActive = true;
  setActiveCallBadge(true);
  resetLeadCard();
  startDurationTimer();
  playNextDemoTurn();
}

function playNextDemoTurn() {
  if (streamIndex >= LIVE_CALL_SCRIPT.length) {
    updateExtractedLeadCard(DEMO_LEAD);
    setText("extLeadStatus", "Ejemplo (modo demo)");
    setText("typingText", "Simulación finalizada · puedes colgar.");
    return;
  }
  const turn = LIVE_CALL_SCRIPT[streamIndex++];
  appendStreamTurn(turn);
  if (turn.role === "agent") speakSofia(turn.text, () => setTimeout(playNextDemoTurn, 800));
  else setTimeout(playNextDemoTurn, 1200);
}

function getBestSpanishVoice() {
  if (!("speechSynthesis" in window)) return null;
  const voices = window.speechSynthesis.getVoices();
  return voices.find(v => v.lang.startsWith("es") && /Google|Monica|Paulina|Helena|Natural|Jorge/.test(v.name))
    || voices.find(v => v.lang.startsWith("es")) || null;
}

function speakSofia(text, onEnd) {
  if (isAudioMuted || !("speechSynthesis" in window)) {
    if (onEnd) setTimeout(onEnd, 1200);
    return;
  }
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = "es-ES";
  utter.rate = 1.05;
  const voice = getBestSpanishVoice();
  if (voice) utter.voice = voice;
  isSofiaSpeaking = true;
  setText("typingText", "Sofía hablando (voz del navegador, simulación)…");
  const done = () => {
    isSofiaSpeaking = false;
    setText("typingText", "Simulación local · escuchando…");
    if (onEnd) setTimeout(onEnd, 300);
  };
  utter.onend = done;
  utter.onerror = done;
  window.speechSynthesis.speak(utter);
}

function toggleAudioMute() {
  isAudioMuted = !isAudioMuted;
  if (isAudioMuted && "speechSynthesis" in window) window.speechSynthesis.cancel();
  setText("muteLabel", isAudioMuted ? "Activar Voz" : "Silenciar Voz");
}

function hangupActiveCall() {
  if (isLive()) return;
  isCallActive = false;
  isSofiaSpeaking = false;
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  stopDurationTimer();
  setActiveCallBadge(false);
  setText("typingText", "Simulación finalizada (no se ha enviado nada a ningún sistema).");
  provider.saveCall({
    id: `demo-call-${Date.now()}`,
    date: "Hoy, " + new Date().toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" }),
    client: $("extLeadName").textContent || "Cliente de ejemplo",
    phone: $("extLeadPhone").textContent || "—",
    duration: $("liveDurationTimer").textContent,
    operator: "Sofía (IA)",
    reason: $("extLeadInterest").textContent,
    hasLead: true,
    isToday: true,
    transcript: Array.from(document.querySelectorAll(".stream-bubble")).map(b => b.innerText).join("\n"),
  }).then(() => renderCallsTable());
}

function copyLiveTranscript() {
  const text = Array.from(document.querySelectorAll(".stream-bubble")).map(b => b.innerText).join("\n\n");
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => setText("typingText", "Transcripción copiada al portapapeles."));
}

function openWhatsAppForPhone(phone, message) {
  const clean = digitsOnly(phone);
  if (clean.length < 6) {
    window.alert("Este lead no tiene un teléfono válido.");
    return;
  }
  const text = encodeURIComponent(message || "Hola, te contactamos de WELUX sobre tu solicitud.");
  window.open(`https://wa.me/${clean}?text=${text}`, "_blank", "noopener,noreferrer");
}

function contactViaWhatsApp() {
  const name = $("extLeadName").textContent;
  openWhatsAppForPhone($("extLeadPhone").textContent, `Hola ${name}, te contacto de WELUX sobre tu solicitud.`);
}

function scheduleDirectMeeting() {
  switchTab("agenda");
}

// Entrada de texto / micrófono: prueba local del guion (solo demo, sin IA real)
let speechRecognizer = null;
let isRecording = false;

function toggleVoiceInputTest() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) {
    window.alert("Tu navegador no soporta reconocimiento de voz. Escribe la frase en el campo de texto.");
    return;
  }
  if (isRecording) {
    speechRecognizer.stop();
    return;
  }
  speechRecognizer = new SpeechRec();
  speechRecognizer.lang = "es-ES";
  speechRecognizer.interimResults = false;
  speechRecognizer.onstart = () => {
    isRecording = true;
    $("btnLiveMic").classList.add("recording");
    setText("micBtnLabel", "Escuchando…");
  };
  speechRecognizer.onresult = (event) => {
    $("liveUserTextInput").value = event.results[0][0].transcript;
    sendLiveUserText();
  };
  speechRecognizer.onend = speechRecognizer.onerror = () => {
    isRecording = false;
    $("btnLiveMic").classList.remove("recording");
    setText("micBtnLabel", "Hablar");
  };
  speechRecognizer.start();
}

const DEMO_INTENTS = [
  { keys: ["fotoespejo", "photobooth", "espejo"], reply: "¡El fotoespejo es ideal para activaciones de marca! ¿Para qué fecha lo necesitas y cuántos asistentes calculan?", interest: "Alquiler Fotoespejo (Photobooth)", reqs: ["Fotoespejo", "Impresión instantánea"] },
  { keys: ["asesor", "consultor", "proceso", "negocio"], reply: "Ayudamos a empresas a optimizar procesos comerciales. ¿Te vendría bien una sesión de diagnóstico de 30 minutos?", interest: "Asesoría de Negocios y Procesos", reqs: ["Diagnóstico", "Automatización comercial"] },
  { keys: ["web", "crm", "chatbot", "newsletter", "mailing"], reply: "Diseñamos webs profesionales, chatbots con IA y despliegue de CRM. ¿Para cuándo lo necesitarían operativo?", interest: "Servicios B2B (Web, Chatbot & CRM)", reqs: ["Desarrollo web", "Chatbot IA", "CRM"] },
  { keys: ["minigolf", "billar", "inflable"], reply: "Los inflables de minigolf y billar gigante son un éxito en empresas. ¿Sería con entrega y montaje en vuestra sede?", interest: "Alquiler Inflables (Minigolf & Billar)", reqs: ["Inflables", "Montaje"] },
  { keys: ["gala", "empresa", "corporativ", "evento"], reply: "Para galas corporativas disponemos de sonido, iluminación y micrófonos. ¿Para qué fecha y qué salón?", interest: "Evento corporativo", reqs: ["Sonido", "Iluminación", "Micrófonos"] },
  { keys: ["precio", "cuanto", "cuánto", "tarifa", "costo", "cotiz"], reply: "Cada solución se adapta a tu empresa: preparamos una propuesta detallada. ¿Me indicas un teléfono de contacto?", interest: "Cotización formal requerida", reqs: [] },
];

function sendLiveUserText() {
  if (isLive()) return;
  const input = $("liveUserTextInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  appendStreamTurn({ role: "customer", author: "Tú (prueba)", time: nowTimeLabel(), text });

  const lower = text.toLowerCase();
  const intent = DEMO_INTENTS.find(i => i.keys.some(k => lower.includes(k)));
  const reply = intent ? intent.reply : "Tomo nota para que el asesor asignado te contacte hoy mismo. ¿Algún detalle adicional?";
  if (intent) {
    setText("extLeadInterest", intent.interest);
    const tags = $("extLeadTags");
    tags.textContent = "";
    intent.reqs.forEach(r => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = r;
      tags.appendChild(tag);
    });
  }
  const phoneMatch = text.match(/(?:\+|00)?\d[\d\s]{7,16}\d/);
  if (phoneMatch) setText("extLeadPhone", phoneMatch[0].trim());
  const nameMatch = text.match(/(?:me llamo|mi nombre es)\s+([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+)?)/i);
  if (nameMatch) setText("extLeadName", nameMatch[1].trim());

  setTimeout(() => {
    appendStreamTurn({ role: "agent", author: "Sofía (respuesta de ejemplo)", time: nowTimeLabel(), text: reply });
    speakSofia(reply);
  }, 400);
}

function quickSendChip(phrase) {
  $("liveUserTextInput").value = phrase;
  sendLiveUserText();
}

// ==============================================================================
// 6. NAVEGACIÓN
// ==============================================================================

const VIEW_TITLES = {
  envivo: { title: "Monitor de Llamada en Vivo", subtitle: "Transcripción en tiempo real y lead extraído al colgar." },
  llamadas: { title: "Historial de Llamadas", subtitle: "Llamadas atendidas con transcripción descargable." },
  leads: { title: "Bandeja de Leads", subtitle: "Contactos comerciales registrados en Google Sheets." },
  agenda: { title: "Agenda & Solicitudes de Cita", subtitle: "Solicitudes registradas por Sofía, pendientes de confirmación por el equipo." },
  estado: { title: "Estado del Sistema", subtitle: "Configuración real del pipeline y métricas de la sesión del servidor." },
};

function switchTab(tabId) {
  if (!VIEW_TITLES[tabId]) return;
  document.querySelectorAll(".nav-item").forEach(el => {
    const active = el.getAttribute("data-tab") === tabId;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  document.querySelectorAll(".view-panel").forEach(panel => {
    panel.classList.toggle("active", panel.id === `view-${tabId}`);
  });
  setText("currentViewTitle", VIEW_TITLES[tabId].title);
  setText("currentViewSubtitle", VIEW_TITLES[tabId].subtitle);
  if (window.location.hash !== `#${tabId}`) history.replaceState(null, "", `#${tabId}`);
}

function initNavigation() {
  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      switchTab(item.getAttribute("data-tab"));
    });
  });
  const hash = window.location.hash.replace("#", "");
  if (VIEW_TITLES[hash]) switchTab(hash);
}

function createEmptyStateElement(icon, title, desc) {
  const container = document.createElement("div");
  container.className = "panel-empty-state";
  const iconDiv = document.createElement("div");
  iconDiv.className = "empty-icon";
  iconDiv.textContent = icon;
  const titleDiv = document.createElement("div");
  titleDiv.className = "empty-title";
  titleDiv.textContent = title;
  const descDiv = document.createElement("div");
  descDiv.className = "empty-desc";
  descDiv.textContent = desc;
  container.append(iconDiv, titleDiv, descDiv);
  return container;
}

// ==============================================================================
// 7. IDENTIFICACIÓN DEL CLIENTE (PORTAL)
// ==============================================================================

function checkSavedClientIdentity() {
  const data = jsonParseSafe(storageGet(sessionStorage, CLIENT_KEY));
  if (data && data.nombre) updateClientIdentifiedUI(data);
}

async function handleClientIdentification(e) {
  e.preventDefault();
  const name = $("clientIdName").value.trim();
  const phone = $("clientIdPhone").value.trim();
  const email = $("clientIdEmail").value.trim();
  const company = $("clientIdCompany").value.trim();
  const consent = $("clientIdConsent").checked;

  if (name.length < 2 || digitsOnly(phone).length < 6) {
    window.alert("Indica tu nombre y un teléfono válido (formato internacional, p. ej. +352 691 123 456).");
    return;
  }
  if (!consent) {
    window.alert("Para registrar tus datos necesitamos tu consentimiento explícito (RGPD).");
    return;
  }
  if (API_BASE === null) {
    window.alert("Modo demo: este despliegue no tiene backend, tus datos NO se han registrado.");
    return;
  }

  const payload = { nombre: name, telefono: phone, consentimiento: true };
  if (email) payload.email = email;
  if (company) payload.empresa = company;

  const btn = $("btnSaveClient");
  btn.disabled = true;
  try {
    const res = await apiFetch("/api/client-identify", { method: "POST", body: payload, auth: false });
    const identity = { nombre: name, telefono: phone };
    storageSet(sessionStorage, CLIENT_KEY, JSON.stringify(identity));
    updateClientIdentifiedUI(identity);
    window.alert(res.status === "ok"
      ? `Gracias ${name}. Tus datos han quedado registrados.`
      : `Gracias ${name}. Hemos recibido tus datos; se sincronizarán en breve.`);
    if (isLive()) refreshLeads();
  } catch (err) {
    window.alert(err.status === 429
      ? "Demasiados intentos. Inténtalo de nuevo en unos minutos."
      : `No se pudieron registrar tus datos (${err.message}). Inténtalo de nuevo.`);
  } finally {
    btn.disabled = false;
  }
}

function updateClientIdentifiedUI(data) {
  $("clientIdentificationForm").hidden = true;
  $("clientIdentifiedBadge").hidden = false;
  setText("clientIdentifiedText", `${data.nombre} (${data.telefono})`);
}

// ==============================================================================
// 8. HISTORIAL DE LLAMADAS
// ==============================================================================

let selectedCall = null;

async function renderCallsTable() {
  try {
    state.calls = await provider.getCalls();
  } catch (err) {
    state.calls = [];
  }
  const tbody = $("callsTableBody");
  tbody.textContent = "";

  const filtered = state.calls.filter(c => {
    if (state.callFilter === "today") return c.isToday || String(c.date).startsWith("Hoy");
    if (state.callFilter === "leads") return c.hasLead;
    return true;
  });

  setText("totalCallsCount", String(state.calls.length));
  setText("callCountBadge", String(state.calls.length));

  if (filtered.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 7;
    td.className = "text-center";
    td.appendChild(createEmptyStateElement("📡", "Sin llamadas", isLive()
      ? "Aún no hay llamadas en esta sesión del servidor."
      : "No hay llamadas que coincidan con el filtro."));
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  filtered.forEach(call => {
    const tr = document.createElement("tr");
    const cells = [
      () => { const s = document.createElement("strong"); s.textContent = call.date || ""; return s; },
      () => {
        const wrap = document.createElement("div");
        const strong = document.createElement("strong");
        strong.textContent = call.client || "";
        const phone = document.createElement("div");
        phone.className = "cell-mono-muted";
        phone.textContent = call.phone || "";
        wrap.append(strong, phone);
        return wrap;
      },
      () => { const s = document.createElement("span"); s.className = "mono"; s.textContent = call.duration || ""; return s; },
      () => document.createTextNode(call.operator || ""),
      () => document.createTextNode(call.reason || ""),
      () => {
        const badge = document.createElement("span");
        badge.className = `status-badge ${call.hasLead ? "lead-yes" : "lead-no"}`;
        badge.textContent = call.hasLead ? "✓ Lead" : "Sin lead";
        return badge;
      },
      () => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-sm btn-outline";
        btn.textContent = "Ver transcripción";
        btn.addEventListener("click", () => openTranscriptModal(call.id));
        return btn;
      },
    ];
    cells.forEach(build => {
      const td = document.createElement("td");
      td.appendChild(build());
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  searchCallsTable();
}

function filterCalls(mode, el) {
  document.querySelectorAll("#view-llamadas .filter-btn").forEach(btn => btn.classList.toggle("active", btn === el));
  state.callFilter = mode;
  renderCallsTable();
}

function searchCallsTable() {
  const query = ($("callSearchInput").value || "").toLowerCase();
  document.querySelectorAll("#callsTableBody tr").forEach(r => {
    r.hidden = Boolean(query) && !r.innerText.toLowerCase().includes(query);
  });
}

function exportCallsReport() {
  const header = ["ID", "Fecha", "Cliente", "Telefono", "Duracion", "Operadora", "Motivo", "Lead"].map(csvCell).join(",");
  const rows = state.calls.map(c => [c.id, c.date, c.client, c.phone, c.duration, c.operator, c.reason, c.hasLead].map(csvCell).join(","));
  downloadBlob([header, ...rows].join("\n") + "\n", "text/csv", `la_centralita_llamadas_${Date.now()}.csv`);
}

function openModal(id) {
  const modal = $(id);
  modal.hidden = false;
  requestAnimationFrame(() => modal.classList.add("open"));
  const focusable = modal.querySelector(".btn-close-modal");
  if (focusable) focusable.focus();
}

function closeModal(id) {
  const modal = $(id);
  modal.classList.remove("open");
  modal.hidden = true;
}

function openTranscriptModal(callId) {
  selectedCall = state.calls.find(c => c.id === callId);
  if (!selectedCall) return;
  setText("modalCallTitle", `Llamada con ${selectedCall.client}`);
  const meta = $("modalCallMeta");
  meta.textContent = "";
  [["Fecha:", selectedCall.date], ["Teléfono:", selectedCall.phone], ["Duración:", selectedCall.duration], ["Operadora:", selectedCall.operator]]
    .forEach(([label, val]) => {
      const span = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = `${label} `;
      span.append(strong, document.createTextNode(val || ""));
      meta.appendChild(span);
    });
  $("modalTranscriptContent").textContent = selectedCall.transcript || "";
  openModal("transcriptModal");
}

function closeTranscriptModal() {
  closeModal("transcriptModal");
}

function downloadTranscriptFile(ext) {
  if (!selectedCall) return;
  if (ext === "json") {
    downloadBlob(JSON.stringify(selectedCall, null, 2), "application/json", `transcripcion_${selectedCall.id}.json`);
    return;
  }
  const content = `DETALLES DE LA LLAMADA\n====================\nCliente: ${selectedCall.client}\nTeléfono: ${selectedCall.phone}\nFecha: ${selectedCall.date}\nDuración: ${selectedCall.duration}\n\nTRANSCRIPCIÓN COMPLETA:\n--------------------\n${selectedCall.transcript}`;
  downloadBlob(content, "text/plain", `transcripcion_${selectedCall.id}.txt`);
}

// ==============================================================================
// 9. BANDEJA DE LEADS (LISTA + KANBAN)
// ==============================================================================

async function refreshLeads() {
  try {
    const { leads, meta } = await provider.getLeads();
    state.leads = leads;
    state.leadsMeta = meta;
  } catch (err) {
    state.leadsMeta = { source: "error", degraded: true, sheet_error: err.message };
  }
  renderLeadsSourceNotice();
  renderLeadsGrid();
  renderTwentyKanban();
  refreshSystemMetrics(false);
}

function renderLeadsSourceNotice() {
  const meta = state.leadsMeta || {};
  if (!isLive()) return;
  if (meta.degraded) {
    setMode("live", `Google Sheets no disponible (${meta.sheet_error || meta.sheet_status || "error"}). Se muestran solo los leads de esta sesión del servidor.`, "error");
  } else if (meta.rows_needing_repair) {
    setMode("live", `${meta.rows_needing_repair} fila(s) del Sheet tienen el teléfono en #ERROR!: ejecutar scripts/repair_sheet_phones.py --apply.`, "warning");
  } else {
    setMode("live", "", "info");
  }
}

function docusealBadge(lead) {
  const status = String(lead.docusealStatus || "BORRADOR").toUpperCase();
  const map = {
    FIRMADO: ["✅ Firmado", ""],
    ENVIADO: ["📨 Enviado", "pending"],
    VISTO: ["👁 Visto", "pending"],
  };
  const [text, cls] = map[status] || ["📄 Borrador", "draft"];
  const span = document.createElement("span");
  span.className = `docuseal-badge ${cls}`.trim();
  span.textContent = `DocuSeal: ${text}`;
  return span;
}

function buildStageSelect(lead, onChange) {
  const select = document.createElement("select");
  select.className = "lead-stage-select";
  select.setAttribute("aria-label", `Etapa de ${lead.name}`);
  STAGES.forEach(stg => {
    const opt = document.createElement("option");
    opt.value = stg;
    opt.textContent = stg.charAt(0).toUpperCase() + stg.slice(1);
    opt.selected = lead.stage === stg;
    select.appendChild(opt);
  });
  select.addEventListener("change", (e) => onChange(e.target.value, select));
  return select;
}

async function changeLeadStage(lead, newStage, select) {
  const previous = lead.stage;
  select.disabled = true;
  try {
    await provider.updateLeadStage(lead.id, newStage);
    lead.stage = newStage;
    renderLeadsGrid();
    renderTwentyKanban();
  } catch (err) {
    select.value = previous;
    window.alert(`No se pudo guardar la etapa: ${err.message}`);
  } finally {
    select.disabled = false;
  }
}

function renderLeadsGrid() {
  const container = $("leadsCardsGrid");
  container.textContent = "";
  setText("totalLeadsCount", String(state.leads.length));
  setText("leadsCountBadge", String(state.leads.length));

  const filtered = state.leads.filter(l => state.leadFilter === "all" || l.stage === state.leadFilter);
  if (filtered.length === 0) {
    const empty = createEmptyStateElement("💼", "Sin leads", state.leads.length
      ? "No hay leads en esta etapa."
      : (isLive() ? "El Google Sheet no tiene leads todavía." : "No hay datos de ejemplo."));
    empty.classList.add("span-all");
    container.appendChild(empty);
    return;
  }

  filtered.forEach(lead => {
    const card = document.createElement("article");
    card.className = "lead-box-card";

    const header = document.createElement("div");
    header.className = "lead-box-header";
    const info = document.createElement("div");
    const name = document.createElement("div");
    name.className = "lead-box-name";
    name.textContent = lead.name;
    const phone = document.createElement("div");
    phone.className = "lead-box-phone";
    phone.textContent = `${lead.phone} • ${lead.company}`;
    info.append(name, phone);
    header.append(info, buildStageSelect(lead, (v, sel) => changeLeadStage(lead, v, sel)));

    const metaBar = document.createElement("div");
    metaBar.className = "lead-box-meta";
    const amount = document.createElement("span");
    amount.className = "lead-box-amount";
    amount.textContent = lead.amount || "Por cotizar";
    metaBar.append(docusealBadge(lead), amount);

    const interest = document.createElement("div");
    interest.className = "lead-box-interest";
    interest.textContent = `${lead.interest} · ${lead.eventDate}`;

    const summary = document.createElement("div");
    summary.className = "lead-box-summary";
    summary.textContent = lead.summary;

    const footer = document.createElement("div");
    footer.className = "lead-box-footer";
    const captured = document.createElement("span");
    captured.textContent = `Capturado: ${lead.timestamp}`;
    const actions = document.createElement("div");
    actions.className = "lead-box-actions";
    const btnDoc = document.createElement("button");
    btnDoc.type = "button";
    btnDoc.className = "btn-docuseal";
    btnDoc.textContent = "📝 Contrato";
    btnDoc.addEventListener("click", () => openDocuSealModal(lead.id));
    const btnWa = document.createElement("button");
    btnWa.type = "button";
    btnWa.className = "btn-sm btn-outline";
    btnWa.textContent = "WhatsApp";
    btnWa.addEventListener("click", () => openWhatsAppForPhone(lead.phone, `Hola ${lead.name}, te contactamos de WELUX sobre tu solicitud.`));
    actions.append(btnDoc, btnWa);
    footer.append(captured, actions);

    card.append(header, metaBar, interest, summary, footer);
    container.appendChild(card);
  });
}

function filterLeads(stage, el) {
  document.querySelectorAll("#view-leads .filter-btn").forEach(btn => btn.classList.toggle("active", btn === el));
  state.leadFilter = stage;
  renderLeadsGrid();
}

function switchLeadsViewMode(mode) {
  state.leadsView = mode;
  $("leadsCardsGrid").hidden = mode === "kanban";
  $("twentyKanbanBoard").hidden = mode !== "kanban";
  $("btnViewCards").classList.toggle("active", mode !== "kanban");
  $("btnViewKanban").classList.toggle("active", mode === "kanban");
  if (mode === "kanban") renderTwentyKanban();
  else renderLeadsGrid();
}

function renderTwentyKanban() {
  STAGES.forEach(stage => {
    const key = stage === "ganado" ? "Ganado" : stage.charAt(0).toUpperCase() + stage.slice(1);
    const col = $(`kanbanCol${key}`);
    if (!col) return;
    col.textContent = "";
    const stageLeads = state.leads.filter(l => l.stage === stage);
    setText(`kanbanCount${key}`, String(stageLeads.length));

    stageLeads.forEach(lead => {
      const card = document.createElement("div");
      card.className = "twenty-card";

      const header = document.createElement("div");
      header.className = "twenty-card-header";
      const name = document.createElement("span");
      name.className = "twenty-card-name";
      name.textContent = lead.name;
      const amount = document.createElement("span");
      amount.className = "twenty-card-amount";
      amount.textContent = lead.amount || "Por cotizar";
      header.append(name, amount);

      const details = document.createElement("div");
      details.className = "twenty-card-details";
      const summary = lead.summary || lead.interest || "";
      details.textContent = summary.length > 90 ? `${summary.slice(0, 90)}…` : summary;

      const footer = document.createElement("div");
      footer.className = "twenty-card-footer";
      const btnDoc = document.createElement("button");
      btnDoc.type = "button";
      btnDoc.className = "btn-docuseal";
      btnDoc.textContent = "📝 Contrato";
      btnDoc.addEventListener("click", () => openDocuSealModal(lead.id));
      footer.append(btnDoc, buildStageSelect(lead, (v, sel) => changeLeadStage(lead, v, sel)));

      card.append(header, docusealBadge(lead), details, footer);
      col.appendChild(card);
    });
  });
}

function exportLeadsToCRM() {
  const payload = { evento: "leads_export", modo: state.mode, timestamp: new Date().toISOString(), total_leads: state.leads.length, leads: state.leads };
  downloadBlob(JSON.stringify(payload, null, 2), "application/json", `leads_${Date.now()}.json`);
}

// ==============================================================================
// 10. AGENDA (SOLICITUDES DE CITA) Y CALENDARIO
// ==============================================================================

const calendarCursor = new Date();
calendarCursor.setDate(1);

async function refreshAppointments() {
  try {
    state.appointments = await provider.getAppointments();
  } catch {
    state.appointments = [];
  }
  setText("agendaCountBadge", String(state.appointments.length));
  setText("appointmentsBadge", `${state.appointments.length} solicitud${state.appointments.length === 1 ? "" : "es"}`);
  renderCalendar();
  renderAppointments();
}

function shiftCalendar(delta) {
  const n = Number(delta);
  if (n === 0) {
    const today = new Date();
    calendarCursor.setFullYear(today.getFullYear(), today.getMonth(), 1);
  } else {
    calendarCursor.setMonth(calendarCursor.getMonth() + n);
  }
  renderCalendar();
}

function renderCalendar() {
  const grid = $("calendarDaysGrid");
  grid.textContent = "";
  const year = calendarCursor.getFullYear();
  const month = calendarCursor.getMonth();
  setText("calendarMonthTitle", calendarCursor.toLocaleDateString("es-ES", { month: "long", year: "numeric" }));

  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const offset = (new Date(year, month, 1).getDay() + 6) % 7; // lunes = 0
  const today = new Date();
  const eventDays = new Set(state.appointments
    .map(a => new Date(`${a.requested_date}T12:00:00`))
    .filter(d => !Number.isNaN(d.getTime()) && d.getFullYear() === year && d.getMonth() === month)
    .map(d => d.getDate()));

  for (let i = 0; i < offset; i++) {
    const empty = document.createElement("div");
    empty.className = "cal-day empty";
    grid.appendChild(empty);
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const cell = document.createElement("div");
    cell.className = "cal-day";
    if (day === today.getDate() && month === today.getMonth() && year === today.getFullYear()) cell.classList.add("today");
    const num = document.createElement("span");
    num.className = "cal-day-num";
    num.textContent = String(day);
    cell.appendChild(num);
    if (eventDays.has(day)) {
      cell.classList.add("has-event");
      const dot = document.createElement("span");
      dot.className = "cal-event-dot";
      cell.appendChild(dot);
    }
    grid.appendChild(cell);
  }
}

function renderAppointments() {
  const container = $("appointmentsList");
  container.textContent = "";
  if (state.appointments.length === 0) {
    container.appendChild(createEmptyStateElement("📅", "Sin solicitudes", "Cuando Sofía registre una solicitud de cita aparecerá aquí."));
    return;
  }
  state.appointments.forEach(app => {
    const date = new Date(`${app.requested_date}T12:00:00`);
    const valid = !Number.isNaN(date.getTime());

    const item = document.createElement("div");
    item.className = "appointment-item";
    const dateBox = document.createElement("div");
    dateBox.className = "app-date-box";
    const day = document.createElement("span");
    day.className = "app-day";
    day.textContent = valid ? String(date.getDate()) : "?";
    const month = document.createElement("span");
    month.className = "app-month";
    month.textContent = valid ? date.toLocaleDateString("es-ES", { month: "short" }) : String(app.requested_date || "");
    dateBox.append(day, month);

    const info = document.createElement("div");
    info.className = "app-info";
    const title = document.createElement("div");
    title.className = "app-title";
    title.textContent = `${app.event_type || "Reunión"} · ${app.client_name || "Cliente"}`;
    const meta = document.createElement("div");
    meta.className = "app-meta";
    meta.textContent = `${app.requested_time || "Hora a convenir"} • ${app.status === "PENDIENTE_CONFIRMACION" ? "Pendiente de confirmación" : app.status || ""}`;
    info.append(title, meta);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-sm btn-outline";
    btn.textContent = "📅 .ics";
    btn.title = "Descargar para añadir a tu calendario";
    btn.disabled = !valid;
    btn.addEventListener("click", () => downloadAppointmentIcs(app));

    item.append(dateBox, info, btn);
    container.appendChild(item);
  });
}

function icsEscape(text) {
  return String(text || "").replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
}

function downloadAppointmentIcs(app) {
  const [h, m] = /^\d{1,2}:\d{2}$/.test(app.requested_time || "") ? app.requested_time.split(":") : ["09", "00"];
  const start = `${app.requested_date.replace(/-/g, "")}T${h.padStart(2, "0")}${m}00`;
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
  const ics = [
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//WELUX//La Centralita//ES", "BEGIN:VEVENT",
    `UID:${icsEscape(app.id || stamp)}@la-centralita`, `DTSTAMP:${stamp}`,
    `DTSTART;TZID=Europe/Luxembourg:${start}`, "DURATION:PT45M",
    `SUMMARY:${icsEscape(`${app.event_type || "Reunión"} · ${app.client_name || ""}`)}`,
    `DESCRIPTION:${icsEscape(`Solicitud registrada por Sofía. Tel: ${app.phone || "—"}. Pendiente de confirmar con el cliente.`)}`,
    "STATUS:TENTATIVE", "END:VEVENT", "END:VCALENDAR",
  ].join("\r\n");
  downloadBlob(ics, "text/calendar", `cita_${app.requested_date}.ics`);
}

function openDirectBookingModal() {
  if (isLive()) {
    window.alert("Las solicitudes de cita las registra Sofía durante las llamadas. Para citas manuales usa tu calendario.");
    return;
  }
  const clientName = (window.prompt("Nombre del cliente (ejemplo local):") || "").trim();
  if (!clientName) return;
  const date = (window.prompt("Fecha (AAAA-MM-DD):", isoDateOffset(2)) || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return;
  const time = (window.prompt("Hora (HH:MM):", "16:30") || "").trim();
  DEMO_APPOINTMENTS.unshift({ id: `demo-${Date.now()}`, client_name: clientName, event_type: "Reunión técnica", requested_date: date, requested_time: time, status: "PENDIENTE_CONFIRMACION" });
  refreshAppointments();
}

// ==============================================================================
// 11. DOCUSEAL
// ==============================================================================

let currentDocuSealLead = null;

function openDocuSealModal(leadId) {
  currentDocuSealLead = state.leads.find(l => l.id === leadId) || state.leads[0];
  if (!currentDocuSealLead) {
    window.alert("No hay leads para generar contrato.");
    return;
  }
  const lead = currentDocuSealLead;
  const interest = String(lead.interest || "").toLowerCase();
  let title = "WELUX Events S.à r.l. — Contrato de Producción Técnica para Eventos";
  if (interest.includes("fotoespejo") || interest.includes("inflable")) title = "WELUX — Contrato de Alquiler de Fotoespejo e Inflables";
  else if (interest.includes("asesor") || interest.includes("proceso")) title = "WELUX — Contrato de Asesoría de Negocios";
  else if (interest.includes("web") || interest.includes("crm")) title = "WELUX — Contrato de Desarrollo Web, Chatbots & CRM";

  setText("docusealContractTitle", title);
  setText("docusealContractId", lead.id);
  setText("docusealClientName", lead.name);
  setText("docusealClientCompany", lead.company || "—");
  setText("docusealClientPhone", lead.phone);
  setText("docusealEventInterest", lead.interest);
  setText("docusealEventDate", lead.eventDate || "A convenir");
  setText("docusealEventSummary", lead.summary || "—");
  setText("docusealContractAmount", lead.amount || "Por cotizar");
  setText("docusealSignatureVisual", lead.name);

  const status = String(lead.docusealStatus || "BORRADOR").toUpperCase();
  const signed = status === "FIRMADO";
  const badge = $("docusealContractBadge");
  badge.className = signed ? "docuseal-badge" : "docuseal-badge pending";
  badge.textContent = signed ? "FIRMADO" : status === "ENVIADO" ? "ENVIADO · PENDIENTE DE FIRMA" : "BORRADOR";
  setText("docusealCertInfo", signed
    ? "Firmado en DocuSeal (el certificado está en la plataforma DocuSeal)."
    : lead.docusealUrl ? "Enlace de firma generado en DocuSeal." : "Sin enlace de firma: emitir el contrato desde DocuSeal.");

  const link = $("docusealSignLink");
  const safeUrl = lead.docusealUrl && /^https:\/\//.test(lead.docusealUrl) ? lead.docusealUrl : null;
  link.hidden = !safeUrl;
  if (safeUrl) link.href = safeUrl;

  const btnSign = $("btnSignDocuSeal");
  btnSign.hidden = isLive();
  btnSign.disabled = signed;
  btnSign.textContent = signed ? "✓ Firmado (ejemplo)" : "✍️ Simular firma (modo demo)";
  openModal("docusealModal");
}

function closeDocuSealModal() {
  closeModal("docusealModal");
}

async function executeDocuSealSignature() {
  if (isLive() || !currentDocuSealLead) return;
  currentDocuSealLead.docusealStatus = "FIRMADO";
  await provider.updateLeadStage(currentDocuSealLead.id, "ganado");
  currentDocuSealLead.stage = "ganado";
  renderLeadsGrid();
  renderTwentyKanban();
  setText("docusealCertInfo", `Firma SIMULADA en modo demo · ${new Date().toLocaleTimeString("es-ES")} · sin validez legal`);
  const badge = $("docusealContractBadge");
  badge.className = "docuseal-badge";
  badge.textContent = "FIRMADO (SIMULACIÓN)";
  $("btnSignDocuSeal").disabled = true;
}

function sendDocuSealWhatsApp() {
  const lead = currentDocuSealLead;
  if (!lead) return;
  if (!lead.docusealUrl) {
    window.alert("Este contrato aún no tiene enlace de firma en DocuSeal.");
    return;
  }
  openWhatsAppForPhone(lead.phone, `Hola ${lead.name}, este es el enlace para firmar tu contrato con WELUX: ${lead.docusealUrl}`);
}

function downloadSignedContract() {
  const lead = currentDocuSealLead;
  if (!lead) return;
  const status = String(lead.docusealStatus || "BORRADOR").toUpperCase();
  const content = `CONTRATO DE PRESTACIÓN DE SERVICIOS — ${status === "FIRMADO" ? "FIRMADO EN DOCUSEAL" : "BORRADOR (SIN FIRMA)"}
======================================================
REF: ${lead.id}
EMPRESA: WELUX Events S.à r.l. (Luxemburgo)
CLIENTE: ${lead.name} (${lead.company})
TELÉFONO: ${lead.phone}
SERVICIO: ${lead.interest}
FECHA: ${lead.eventDate}
ESPECIFICACIONES: ${lead.summary}
IMPORTE: ${lead.amount || "Por cotizar"}

Estado DocuSeal: ${status}
${status === "FIRMADO" ? "El documento firmado y su certificado de auditoría se descargan desde DocuSeal." : "Documento informativo sin validez contractual hasta su firma en DocuSeal."}
Generado: ${new Date().toISOString()}${isLive() ? "" : "\n*** DATOS DE EJEMPLO (MODO DEMO) ***"}`;
  downloadBlob(content, "text/plain", `contrato_${lead.id}.txt`);
}

// ==============================================================================
// 12. ESTADO DEL SISTEMA (DATOS REALES)
// ==============================================================================

function renderServiceStatus(details) {
  const services = (details && details.services) || {};
  const flags = {
    LIVEKIT_API_KEY: services.LIVEKIT_API_KEY && services.LIVEKIT_API_SECRET && services.LIVEKIT_URL,
    DEEPGRAM_API_KEY: services.DEEPGRAM_API_KEY,
    DEEPSEEK_API_KEY: services.DEEPSEEK_API_KEY,
    tts: Boolean(details && details.tts_provider),
    google_sheets_configured: details && details.google_sheets_configured,
    smtp_configured: details && details.smtp_configured,
    n8n_configured: details && details.n8n_configured,
  };
  document.querySelectorAll("#servicesList .service-row").forEach(row => {
    const key = row.dataset.service;
    const ok = Boolean(flags[key]);
    const dot = row.querySelector(".status-dot-lg");
    const badge = row.querySelector(".srv-badge");
    dot.classList.toggle("ok", ok);
    dot.classList.toggle("warn", !ok);
    badge.classList.toggle("ok", ok);
    badge.classList.toggle("warn", !ok);
    badge.textContent = ok ? "Configurado" : "No configurado";
  });
  if (details && details.tts_provider) {
    setText("ttsProviderText", `Proveedor: ${details.tts_provider} · respaldo: ${details.tts_fallback || "ninguno"}`);
  }
  if (details && typeof details.monitors_connected === "number") setText("kpiMonitors", String(details.monitors_connected));
  const allOk = Object.values(flags).every(Boolean);
  const navDot = $("navStatusDot");
  if (navDot) navDot.dataset.status = allOk ? "ok" : "standby";
}

async function refreshSystemMetrics(fetchDetails = true) {
  if (!isLive()) {
    setText("kpiCalls", String(state.calls.length));
    setText("kpiLeads", String(state.leads.length));
    setText("kpiConversion", state.calls.length ? `${Math.round((state.calls.filter(c => c.hasLead).length / state.calls.length) * 100)}%` : "—");
    setText("kpiSavings", "—");
    setText("kpiScope", "Datos de ejemplo (modo demo)");
    setText("kpiLeadsSource", "Ejemplo local");
    return;
  }
  try {
    const metrics = await apiFetch("/api/system/internal");
    setText("kpiCalls", String(metrics.llamadas_totales_atendidas));
    setText("kpiSavings", `${metrics.dinero_ahorrado_eur.toLocaleString("es-ES")} €`);
    setText("kpiQueue", String(metrics.leads_en_cola_sheets));
    setText("kpiIaCost", `$${metrics.costo_ia_acumulado_usd}`);
    setText("kpiScope", "Sesión actual del servidor");
    const calls = metrics.llamadas_totales_atendidas;
    setText("kpiConversion", calls ? `${Math.round((metrics.leads_convertidos / calls) * 100)}%` : "—");
  } catch { /* se mantiene el último valor */ }
  setText("kpiLeads", String(state.leads.length));
  const meta = state.leadsMeta || {};
  setText("kpiLeadsSource", meta.source === "google_sheets_live" ? "Google Sheets (en vivo)" : "Solo sesión (Sheet no disponible)");
  if (fetchDetails) {
    try {
      renderServiceStatus(await apiFetch("/api/status/details"));
    } catch { /* sin cambios */ }
  }
}

const SECURITY_SUMMARY = {
  fuente: "docs/SECURITY_AUDIT.md (auditoría del repositorio, rama feature/centralita-voz)",
  autenticacion: "Token único CENTRALITA_AUTH_TOKEN en el servidor; sin credenciales en el frontend",
  datos_personales: "Endpoints con PII autenticados; n8n recibe eventos anonimizados; consentimiento RGPD en el portal",
  navegador: "Sin inyección de HTML (textContent), sin handlers inline, CSP estricta en Firebase Hosting",
  integraciones: "Webhook DocuSeal con secreto compartido; WebSocket con autenticación por mensaje y control de origen",
  pendientes_del_dueno: [
    "Rotar la ruta del webhook n8n (expuesta en el historial git)",
    "Configurar SMTP para que los avisos no queden en la bandeja de salida local",
    "Ejecutar scripts/repair_sheet_phones.py --apply para las filas antiguas con #ERROR!",
  ],
};

function openSecurityAuditModal() {
  $("securityFindingsJsonContent").textContent = JSON.stringify(SECURITY_SUMMARY, null, 2);
  openModal("securityAuditModal");
}

function closeSecurityAuditModal() {
  closeModal("securityAuditModal");
}

function downloadSecurityLedger() {
  downloadBlob(JSON.stringify(SECURITY_SUMMARY, null, 2), "application/json", "security-summary.json");
}

// ==============================================================================
// 13. DELEGACIÓN DE EVENTOS E INICIALIZACIÓN
// ==============================================================================

const ACTIONS = {
  triggerIncomingCallDemo, copyLiveTranscript, toggleVoiceInputTest, sendLiveUserText, quickSendChip,
  toggleAudioMute, hangupActiveCall, contactViaWhatsApp, scheduleDirectMeeting, filterCalls, exportCallsReport,
  filterLeads, switchLeadsViewMode, openDocuSealModal, exportLeadsToCRM, openDirectBookingModal, shiftCalendar,
  openSecurityAuditModal, closeTranscriptModal, downloadTranscriptFile, closeDocuSealModal, executeDocuSealSignature,
  sendDocuSealWhatsApp, downloadSignedContract, closeSecurityAuditModal, downloadSecurityLedger, promptOperatorToken,
};

function initEventDelegation() {
  document.addEventListener("click", (e) => {
    const backdrop = e.target.classList && e.target.classList.contains("modal-backdrop") ? e.target : null;
    if (backdrop) {
      closeModal(backdrop.id);
      return;
    }
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const fn = ACTIONS[el.dataset.action];
    if (!fn) return;
    e.preventDefault();
    fn(el.dataset.arg, el);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-backdrop:not([hidden])").forEach(m => closeModal(m.id));
    }
  });

  $("liveUserTextInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendLiveUserText();
  });
  $("callSearchInput").addEventListener("input", searchCallsTable);
  $("clientIdentificationForm").addEventListener("submit", handleClientIdentification);
}

function applyDataDimensions() {
  document.querySelectorAll("[data-width]").forEach(el => { el.style.width = `${el.dataset.width}%`; });
  document.querySelectorAll("[data-height]").forEach(el => { el.style.height = `${el.dataset.height}%`; });
}

document.addEventListener("DOMContentLoaded", () => {
  initEventDelegation();
  initNavigation();
  applyDataDimensions();
  initLiveVisualizer();
  checkSavedClientIdentity();
  setText("currentDateDisplay", new Date().toLocaleDateString("es-ES", { day: "numeric", month: "short", year: "numeric" }));
  bootstrapMode();
});
