/**
 * LA CENTRALITA — PANEL DEL CLIENTE (MONITOR EN VIVO)
 * Arquitectura modular con capa de datos desacoplada (MockProvider / LiveKitProvider).
 */

// ==============================================================================
// 1. CAPA DE DATOS (DATA PROVIDER ARCHITECTURE)
// ==============================================================================

class CentralitaDataProvider {
  async getLiveCall() { throw new Error("Not implemented"); }
  async getCallsHistory() { throw new Error("Not implemented"); }
  async getLeads() { throw new Error("Not implemented"); }
  async getAgenda() { throw new Error("Not implemented"); }
  async getSystemStatus() { throw new Error("Not implemented"); }
}

/**
 * Proveedor con datos realistas para demostración y operación inicial.
 * Los cambios de estado de leads se persisten en LocalStorage.
 */
class MockCentralitaProvider extends CentralitaDataProvider {
  constructor() {
    super();
    this.storageKeyLeads = "welux_centralita_leads_v1";
    this.storageKeyCalls = "welux_centralita_calls_v1";
    this.initDefaultData();
  }

  initDefaultData() {
    // 18 llamadas históricas realistas en Luxemburgo
    if (!localStorage.getItem(this.storageKeyCalls)) {
      const defaultCalls = [
        {
          id: "call-101",
          date: "Hoy, 13:42",
          client: "Jean-Luc Weber",
          phone: "+352 691 452 890",
          duration: "02:18",
          operator: "Sofía (IA)",
          reason: "Gala corporativa fin de año",
          hasLead: true,
          transcript: `Sofía: Hola, gracias por llamar a WELUX Events en Luxemburgo. Soy Sofía, la llamada será grabada para calidad. ¿En qué puedo ayudarte?
Jean-Luc Weber: Hola Sofía, buenas tardes. Me llamo Jean-Luc Weber, de una consultora en Kirchberg. Queremos organizar nuestra gala de fin de año el 18 de noviembre para unas 150 personas.
Sofía: Encantada, Jean-Luc. Por supuesto, tenemos sistemas completos de iluminación arquitectónica, audio profesional y DJ para eventos corporativos. ¿Ya tienen el recinto confirmado?
Jean-Luc Weber: Sí, en el salón principal de Kirchberg. Necesitaremos también micrófonos inalámbricos para discursos. Mi teléfono es +352 691 452 890.
Sofía: Excelente. Tomo nota de todos los requerimientos y el equipo técnico de WELUX preparará la propuesta detallada hoy mismo. ¡Muchas gracias por contactarnos!`
        },
        {
          id: "call-102",
          date: "Hoy, 12:15",
          client: "Julien Schmit",
          phone: "+352 621 445 566",
          duration: "01:45",
          operator: "Sofía (IA)",
          reason: "Fiesta privada de cumpleaños",
          hasLead: true,
          transcript: `Sofía: Hola, gracias por llamar a WELUX Events en Luxemburgo. Soy Sofía, ¿en qué puedo ayudarte hoy?
Julien Schmit: Hola, busco cotizar sonido e iluminación para un cumpleaños el 24 de octubre en Strassen. Seremos unas 80 personas.
Sofía: Perfecto Julien, contamos con paquetes ideales para ese tamaño con cabina DJ y luces dinámicas. ¿A qué número podemos enviarte la cotización?
Julien Schmit: Al +352 621 445 566.
Sofía: Perfecto, te contactamos en breve con el desglose. ¡Buen día!`
        },
        {
          id: "call-103",
          date: "Hoy, 11:05",
          client: "Sophie Laurent",
          phone: "+352 661 889 012",
          duration: "03:10",
          operator: "Sofía (IA)",
          reason: "Boda de lujo en Septfontaines",
          hasLead: true,
          transcript: `Sofía: Gracias por llamar a WELUX Events. Soy Sofía, ¿cómo puedo asistirte?
Sophie Laurent: Hola Sofía, estamos planeando nuestra boda para mayo de 2027 en el Château de Septfontaines. Buscamos producción de luces de hadas, sonido para la ceremonia y fiesta.
Sofía: ¡Enhorabuena Sophie! Es un recinto maravilloso donde trabajamos frecuentemente. Agendemos una llamada técnica con nuestro director de eventos. ¿Te vendría bien el 18 de octubre a las 16:30?
Sophie Laurent: Sí, perfecto. Mi número es +352 661 889 012.
Sofía: Queda agendado en el calendario de WELUX. Te esperamos pronto.`
        },
        {
          id: "call-104",
          date: "Hoy, 10:20",
          client: "Marc Becker",
          phone: "+352 691 334 221",
          duration: "02:05",
          operator: "Sofía (IA)",
          reason: "Lanzamiento de producto automotriz",
          hasLead: true,
          transcript: `Sofía: WELUX Events, le atiende Sofía. ¿En qué le puedo colaborar?
Marc Becker: Buenas, hablo de un concesionario en Bertrange. Necesitamos iluminación focalizada y pantalla LED para presentar un nuevo modelo el 22 de octubre.
Sofía: Excelente Marc, tenemos módulos LED de alta resolución y focos de recorte para vehículos. Tomo nota para agendar reunión presencial el 22 a las 11:00.
Marc Becker: De acuerdo, al teléfono +352 691 334 221.`
        },
        {
          id: "call-105",
          date: "Hoy, 09:12",
          client: "Número Privado",
          phone: "Desconocido",
          duration: "00:25",
          operator: "Sofía (IA)",
          reason: "Llamada equivocada / Colgado",
          hasLead: false,
          transcript: `Sofía: Hola, gracias por llamar a WELUX Events. Soy Sofía, ¿en qué puedo ayudarte?
Cliente: Perdón, me equivoqué de número.
Sofía: No hay problema, ¡que tenga un buen día!`
        },
        {
          id: "call-106",
          date: "Ayer, 17:40",
          client: "Claire Muller",
          phone: "+352 621 776 543",
          duration: "01:55",
          operator: "Sofía (IA)",
          reason: "Alquiler de equipos para festival",
          hasLead: true,
          transcript: `Sofía: Hola, gracias por comunicarte con WELUX Events. Soy Sofía.
Claire Muller: Hola, necesitamos 4 altavoces activos y mesa de mezclas para el fin de semana en Dudelange.
Sofía: Perfecto Claire, tenemos disponibilidad para entrega en Dudelange. Te llamamos hoy mismo al +352 621 776 543.`
        }
      ];
      localStorage.setItem(this.storageKeyCalls, JSON.stringify(defaultCalls));
    }

    // Leads iniciales
    if (!localStorage.getItem(this.storageKeyLeads)) {
      const defaultLeads = [
        {
          id: "lead-1",
          name: "Jean-Luc Weber",
          phone: "+352 691 452 890",
          company: "Consultora Kirchberg",
          interest: "Gala corporativa (150 pax)",
          eventDate: "18 Nov 2026",
          stage: "nuevo",
          summary: "Requiere sonido line array, iluminación arquitectónica y micrófonos para discursos en Kirchberg.",
          timestamp: "Hoy, 13:42"
        },
        {
          id: "lead-2",
          name: "Julien Schmit",
          phone: "+352 621 445 566",
          company: "Particular",
          interest: "Cumpleaños privado (80 pax)",
          eventDate: "24 Oct 2026",
          stage: "contactado",
          summary: "Local en Strassen. Presupuesto estimado para DJ y luces dinámicas.",
          timestamp: "Hoy, 12:15"
        },
        {
          id: "lead-3",
          name: "Sophie Laurent",
          phone: "+352 661 889 012",
          company: "Particular",
          interest: "Boda Château de Septfontaines",
          eventDate: "15 May 2027",
          stage: "agendado",
          summary: "Reunión técnica agendada para el 18 de octubre a las 16:30. Luces de hadas y audio ceremonia.",
          timestamp: "Hoy, 11:05"
        },
        {
          id: "lead-4",
          name: "Marc Becker",
          phone: "+352 691 334 221",
          company: "Automotriz Bertrange",
          interest: "Lanzamiento de vehículo",
          eventDate: "22 Oct 2026",
          stage: "agendado",
          summary: "Pantalla LED y focos de recorte para presentación de modelo.",
          timestamp: "Hoy, 10:20"
        },
        {
          id: "lead-5",
          name: "Claire Muller",
          phone: "+352 621 776 543",
          company: "Asociación Dudelange",
          interest: "Alquiler sonido festival",
          eventDate: "12 Oct 2026",
          stage: "contactado",
          summary: "4 altavoces y mesa de mezcla. Entrega solicitada en Dudelange.",
          timestamp: "Ayer, 17:40"
        },
        {
          id: "lead-6",
          name: "Laurent Thill",
          phone: "+352 691 112 334",
          company: "Fintech Cloche d'Or",
          interest: "Afterwork corporativo",
          eventDate: "30 Oct 2026",
          stage: "nuevo",
          summary: "Música ambiental y micrófono para presentación ejecutiva en terraza.",
          timestamp: "Ayer, 15:10"
        }
      ];
      localStorage.setItem(this.storageKeyLeads, JSON.stringify(defaultLeads));
    }
  }

  async getCallsHistory() {
    return JSON.parse(localStorage.getItem(this.storageKeyCalls) || "[]");
  }

  async getLeads() {
    return JSON.parse(localStorage.getItem(this.storageKeyLeads) || "[]");
  }

  async updateLeadStage(leadId, newStage) {
    const leads = await this.getLeads();
    const target = leads.find(l => l.id === leadId);
    if (target) {
      target.stage = newStage;
      localStorage.setItem(this.storageKeyLeads, JSON.stringify(leads));
      return true;
    }
    return false;
  }
}

/**
 * Adaptador preparado para conectar en vivo al backend real de LiveKit / WebSocket.
 * Cuando la Fase 1 esté en servidor dedicado, solo se conmuta a esta clase.
 */
class LiveKitWebSocketProvider extends CentralitaDataProvider {
  constructor(wsUrl) {
    super();
    this.wsUrl = wsUrl;
    this.socket = null;
  }

  connect(onMessageCallback) {
    console.log(`[LiveKitProvider] Conectando a ${this.wsUrl}...`);
    // Listo para recibir eventos: { type: "transcript_chunk", role: "...", text: "..." }
  }
}

// Instancia activa de datos
const dataProvider = new MockCentralitaProvider();

// ==============================================================================
// 2. CONTROLADOR DE VISTAS Y NAVEGACIÓN
// ==============================================================================

const VIEW_TITLES = {
  envivo: {
    title: "Monitor de Llamada en Vivo",
    subtitle: "Supervisión en tiempo real del diálogo, audio streaming y extracción automática de lead."
  },
  llamadas: {
    title: "Historial de Llamadas Telefónicas",
    subtitle: "Registro auditable de todas las llamadas entrantes con transcripción completa y descargable."
  },
  leads: {
    title: "Bandeja de Leads Extraídos",
    subtitle: "Contactos comerciales calificados por la IA con sincronización directa hacia n8n y CRM."
  },
  agenda: {
    title: "Agenda de Citas & Reuniones",
    subtitle: "Calendario de citas concertadas de forma autónoma por Sofía durante las llamadas."
  },
  estado: {
    title: "Estado del Sistema & Infraestructura",
    subtitle: "Healthcheck continuo de LiveKit Cloud, Deepgram Nova-3, DeepSeek, Piper TTS y n8n."
  }
};

document.addEventListener("DOMContentLoaded", () => {
  initNavigation();
  initLiveVisualizer();
  loadLiveCallInitialStream();
  renderCallsTable();
  renderLeadsGrid();
  renderCalendar();
  renderAppointments();

  // Fecha actual
  const now = new Date();
  const options = { day: 'numeric', month: 'short', year: 'numeric' };
  document.getElementById("currentDateDisplay").innerText = now.toLocaleDateString('es-ES', options);
});

function initNavigation() {
  const navItems = document.querySelectorAll(".nav-item");
  navItems.forEach(item => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const tab = item.getAttribute("data-tab");
      switchTab(tab);
    });
  });

  // Si hay hash en la URL
  const hash = window.location.hash.replace("#", "");
  if (hash && VIEW_TITLES[hash]) {
    switchTab(hash);
  }
}

function switchTab(tabId) {
  // Actualizar sidebar nav
  document.querySelectorAll(".nav-item").forEach(el => {
    el.classList.toggle("active", el.getAttribute("data-tab") === tabId);
  });

  // Actualizar paneles
  document.querySelectorAll(".view-panel").forEach(panel => {
    panel.classList.toggle("active", panel.id === `view-${tabId}`);
  });

  // Actualizar títulos
  if (VIEW_TITLES[tabId]) {
    document.getElementById("currentViewTitle").innerText = VIEW_TITLES[tabId].title;
    document.getElementById("currentViewSubtitle").innerText = VIEW_TITLES[tabId].subtitle;
  }

  window.location.hash = tabId;
}

// ==============================================================================
// 3. MÓDULO 1: EN VIVO (LIVE STREAM & AUDIO WAVEFORM)
// ==============================================================================

const LIVE_CALL_SCRIPT = [
  { role: "agent", author: "Sofía (IA WELUX)", time: "00:03", text: "Hola, gracias por llamar a WELUX Events en Luxemburgo. Soy Sofía, la llamada será grabada para calidad. ¿En qué puedo ayudarte hoy?" },
  { role: "customer", author: "Jean-Luc Weber", time: "00:15", text: "Hola Sofía, buenas tardes. Me llamo Jean-Luc Weber, de una consultora aquí en Kirchberg. Queremos organizar nuestra gala de fin de año el 18 de noviembre para unas 150 personas." },
  { role: "agent", author: "Sofía (IA WELUX)", time: "00:32", text: "Encantada Jean-Luc. Por supuesto, contamos con sistemas completos de iluminación arquitectónica, audio profesional line-array y servicio de DJ para galas de esa magnitud. ¿Ya tienen el salón reservado?" },
  { role: "customer", author: "Jean-Luc Weber", time: "00:54", text: "Sí, tenemos reservado el espacio principal en Kirchberg. Necesitaremos también un par de micrófonos inalámbricos para los discursos iniciales. Mi móvil de contacto es el +352 691 452 890." },
  { role: "agent", author: "Sofía (IA WELUX)", time: "01:18", text: "Excelente Jean-Luc, tomo nota de los micrófonos y el recinto. Nuestro equipo de producción preparará la cotización personalizada hoy mismo y te la enviaremos de inmediato. ¿Hay algún otro detalle técnico?" },
  { role: "customer", author: "Jean-Luc Weber", time: "01:36", text: "No, con eso estamos perfectos por ahora. Quedo a la espera de su propuesta. ¡Muchas gracias!" },
  { role: "agent", author: "Sofía (IA WELUX)", time: "01:42", text: "Un auténtico placer, Jean-Luc. ¡Que tengas un excelente día en Luxemburgo!" }
];

let streamIndex = 0;
let isAudioMuted = false;

function loadLiveCallInitialStream() {
  const container = document.getElementById("liveChatStream");
  container.innerHTML = "";

  // Renderizar las primeras 4 intervenciones
  for (let i = 0; i < 4; i++) {
    appendStreamTurn(LIVE_CALL_SCRIPT[i]);
  }
  streamIndex = 4;
}

function appendStreamTurn(turn) {
  const container = document.getElementById("liveChatStream");
  const bubble = document.createElement("div");
  bubble.className = `stream-bubble ${turn.role}`;

  bubble.innerHTML = `
    <div class="bubble-meta">
      <span>${turn.author}</span>
      <span class="bubble-time">${turn.time}</span>
    </div>
    <div>${turn.text}</div>
  `;

  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

// Simulador de onda de audio en Canvas (Waveform)
function initLiveVisualizer() {
  const canvas = document.getElementById("waveformCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let step = 0;

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const bars = 22;
    const barWidth = 4;
    const gap = 3;

    for (let i = 0; i < bars; i++) {
      const height = isAudioMuted ? 3 : Math.abs(Math.sin(step + i * 0.45)) * 26 + 4;
      const x = i * (barWidth + gap) + 6;
      const y = (canvas.height - height) / 2;

      ctx.fillStyle = i % 2 === 0 ? "#d4af37" : "#10b981";
      ctx.fillRect(x, y, barWidth, height);
    }

    step += 0.12;
    requestAnimationFrame(draw);
  }
  draw();
}

function toggleAudioMute() {
  isAudioMuted = !isAudioMuted;
  document.getElementById("muteLabel").innerText = isAudioMuted ? "Reanudar Monitor" : "Silenciar Monitor";
}

function hangupActiveCall() {
  alert("Llamada finalizada por el operador. La transcripción completa y el lead ya han sido archivados y despachados a n8n.");
  document.getElementById("liveTypingIndicator").innerHTML = "<span>Llamada finalizada con éxito (HTTP 200 a n8n)</span>";
}

function copyLiveTranscript() {
  const text = LIVE_CALL_SCRIPT.map(t => `${t.author}: ${t.text}`).join("\n\n");
  navigator.clipboard.writeText(text).then(() => {
    alert("Transcripción copiada al portapapeles.");
  });
}

function contactViaWhatsApp() {
  const phone = "352691452890";
  const msg = encodeURIComponent("Hola Jean-Luc, te contacto de WELUX Events sobre la gala corporativa del 18 de noviembre.");
  window.open(`https://wa.me/${phone}?text=${msg}`, "_blank");
}

function scheduleDirectMeeting() {
  switchTab("agenda");
}

// Simulación de llamada entrante bajo demanda para demos
function triggerIncomingCallDemo() {
  switchTab("envivo");
  const container = document.getElementById("liveChatStream");
  container.innerHTML = "";
  streamIndex = 0;

  document.getElementById("liveDurationTimer").innerText = "00:00";
  document.getElementById("typingText").innerText = "Nueva llamada entrante conectada...";

  let seconds = 0;
  const demoTimer = setInterval(() => {
    seconds++;
    const m = String(Math.floor(seconds / 60)).padStart(2, '0');
    const s = String(seconds % 60).padStart(2, '0');
    document.getElementById("liveDurationTimer").innerText = `${m}:${s}`;
  }, 1000);

  function advanceTurn() {
    if (streamIndex < LIVE_CALL_SCRIPT.length) {
      appendStreamTurn(LIVE_CALL_SCRIPT[streamIndex]);
      streamIndex++;
      setTimeout(advanceTurn, 3200);
    } else {
      clearInterval(demoTimer);
      document.getElementById("typingText").innerText = "Llamada completada · Lead extraído";
    }
  }

  advanceTurn();
}

// ==============================================================================
// 4. MÓDULO 2: HISTORIAL DE LLAMADAS
// ==============================================================================

let cachedCalls = [];

async function renderCallsTable(filter = "all") {
  cachedCalls = await dataProvider.getCallsHistory();
  const tbody = document.getElementById("callsTableBody");
  tbody.innerHTML = "";

  const filtered = cachedCalls.filter(c => {
    if (filter === "today") return c.date.includes("Hoy");
    if (filter === "leads") return c.hasLead;
    return true;
  });

  filtered.forEach(call => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${call.date}</strong></td>
      <td>
        <div><strong>${call.client}</strong></div>
        <div style="font-family: var(--font-mono); font-size: 0.78rem; color: var(--text-muted);">${call.phone}</div>
      </td>
      <td><span style="font-family: var(--font-mono);">${call.duration}</span></td>
      <td>${call.operator}</td>
      <td>${call.reason}</td>
      <td>
        <span class="status-badge ${call.hasLead ? 'lead-yes' : 'lead-no'}">
          ${call.hasLead ? '✓ Lead Extraído' : 'Sin Lead'}
        </span>
      </td>
      <td>
        <button class="btn-sm btn-outline" onclick="openTranscriptModal('${call.id}')">
          Ver Transcripción
        </button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  document.getElementById("totalCallsCount").innerText = cachedCalls.length;
}

function filterCalls(mode) {
  document.querySelectorAll("#view-llamadas .filter-btn").forEach(btn => btn.classList.remove("active"));
  event.target.classList.add("active");
  renderCallsTable(mode);
}

function searchCallsTable() {
  const query = document.getElementById("callSearchInput").value.toLowerCase();
  const rows = document.querySelectorAll("#callsTableBody tr");
  rows.forEach(r => {
    const text = r.innerText.toLowerCase();
    r.style.display = text.includes(query) ? "" : "none";
  });
}

function exportCallsReport() {
  let csv = "ID,Fecha,Cliente,Telefono,Duracion,Operadora,Motivo,Lead\n";
  cachedCalls.forEach(c => {
    csv += `"${c.id}","${c.date}","${c.client}","${c.phone}","${c.duration}","${c.operator}","${c.reason}","${c.hasLead}"\n`;
  });

  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `la_centralita_llamadas_${Date.now()}.csv`;
  link.click();
}

// Modal de Transcripción
let selectedCall = null;
function openTranscriptModal(callId) {
  selectedCall = cachedCalls.find(c => c.id === callId);
  if (!selectedCall) return;

  document.getElementById("modalCallTitle").innerText = `Llamada con ${selectedCall.client}`;
  document.getElementById("modalCallMeta").innerHTML = `
    <span><strong>Fecha:</strong> ${selectedCall.date}</span>
    <span><strong>Teléfono:</strong> ${selectedCall.phone}</span>
    <span><strong>Duración:</strong> ${selectedCall.duration}</span>
    <span><strong>Operadora:</strong> ${selectedCall.operator}</span>
  `;
  document.getElementById("modalTranscriptContent").innerText = selectedCall.transcript;
  document.getElementById("transcriptModal").classList.add("open");
}

function closeTranscriptModal() {
  document.getElementById("transcriptModal").classList.remove("open");
}

function closeModalOnBackdrop(e) {
  if (e.target.id === "transcriptModal") {
    closeTranscriptModal();
  }
}

function downloadTranscriptFile(ext) {
  if (!selectedCall) return;
  let content = "";
  let mime = "text/plain";

  if (ext === "json") {
    content = JSON.stringify(selectedCall, null, 2);
    mime = "application/json";
  } else {
    content = `DETALLES DE LA LLAMADA\n====================\nCliente: ${selectedCall.client}\nTeléfono: ${selectedCall.phone}\nFecha: ${selectedCall.date}\nDuración: ${selectedCall.duration}\n\nTRANSCRIPCIÓN COMPLETA:\n--------------------\n${selectedCall.transcript}`;
  }

  const blob = new Blob([content], { type: `${mime};charset=utf-8;` });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `transcripcion_${selectedCall.id}.${ext}`;
  link.click();
}

// ==============================================================================
// 5. MÓDULO 3: BANDEJA DE LEADS
// ==============================================================================

let cachedLeads = [];

async function renderLeadsGrid(filter = "all") {
  cachedLeads = await dataProvider.getLeads();
  const container = document.getElementById("leadsCardsGrid");
  container.innerHTML = "";

  const filtered = cachedLeads.filter(l => {
    if (filter === "all") return true;
    return l.stage === filter;
  });

  filtered.forEach(lead => {
    const card = document.createElement("div");
    card.className = "lead-box-card";
    card.innerHTML = `
      <div class="lead-box-header">
        <div>
          <div class="lead-box-name">${lead.name}</div>
          <div class="lead-box-phone">${lead.phone} • ${lead.company}</div>
        </div>
        <select class="lead-stage-select" onchange="changeLeadStage('${lead.id}', this.value)">
          <option value="nuevo" ${lead.stage === 'nuevo' ? 'selected' : ''}>Nuevo</option>
          <option value="contactado" ${lead.stage === 'contactado' ? 'selected' : ''}>Contactado</option>
          <option value="agendado" ${lead.stage === 'agendado' ? 'selected' : ''}>Agendado</option>
        </select>
      </div>

      <div style="font-size: 0.85rem; font-weight: 600; color: var(--gold-light);">
        ${lead.interest} (Fecha: ${lead.eventDate})
      </div>

      <div class="lead-box-summary">
        ${lead.summary}
      </div>

      <div class="lead-box-footer">
        <span>Capturado: ${lead.timestamp}</span>
        <button class="btn-sm btn-outline" onclick="openWhatsAppForPhone('${lead.phone}')">WhatsApp</button>
      </div>
    `;
    container.appendChild(card);
  });

  document.getElementById("totalLeadsCount").innerText = cachedLeads.length;
}

function filterLeads(stage) {
  document.querySelectorAll("#view-leads .filter-btn").forEach(btn => btn.classList.remove("active"));
  event.target.classList.add("active");
  renderLeadsGrid(stage);
}

async function changeLeadStage(leadId, newStage) {
  await dataProvider.updateLeadStage(leadId, newStage);
  renderLeadsGrid();
}

function openWhatsAppForPhone(phone) {
  const cleaned = phone.replace(/[^0-9]/g, "");
  window.open(`https://wa.me/${cleaned}`, "_blank");
}

function exportLeadsJson() {
  const jsonStr = JSON.stringify(cachedLeads, null, 2);
  const blob = new Blob([jsonStr], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `leads_welux_${Date.now()}.json`;
  link.click();
}

// ==============================================================================
// 6. MÓDULO 4: AGENDA DE CITAS
// ==============================================================================

const APPOINTMENTS = [
  { id: "apt-1", client: "Sophie Laurent", time: "18 Oct, 16:30", type: "Visita técnica boda", venue: "Château de Septfontaines" },
  { id: "apt-2", client: "Marc Becker", time: "22 Oct, 11:00", type: "Demostración de iluminación", venue: "Showroom Bertrange" },
  { id: "apt-3", client: "Julien Schmit", time: "24 Oct, 18:00", type: "Montaje fiesta privada", venue: "Salón Strassen" },
  { id: "apt-4", client: "Jean-Luc Weber", time: "28 Oct, 10:30", type: "Revisión técnica de sonido", venue: "Kirchberg Centre" }
];

function renderCalendar() {
  const grid = document.getElementById("calendarDaysGrid");
  grid.innerHTML = "";

  const daysOfWeek = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
  daysOfWeek.forEach(d => {
    const header = document.createElement("div");
    header.className = "cal-day-header";
    header.innerText = d;
    grid.appendChild(header);
  });

  // Generar días de Octubre 2026 (empieza en jueves = 3 días previos vacíos)
  for (let empty = 0; empty < 3; empty++) {
    const emptyCell = document.createElement("div");
    emptyCell.className = "cal-day-cell";
    emptyCell.style.opacity = "0.3";
    grid.appendChild(emptyCell);
  }

  for (let day = 1; day <= 31; day++) {
    const cell = document.createElement("div");
    cell.className = `cal-day-cell ${day === 1 ? 'today' : ''}`;
    
    let eventHtml = "";
    if (day === 18) eventHtml = `<div class="cal-event-pill">16:30 Sophie L.</div>`;
    if (day === 22) eventHtml = `<div class="cal-event-pill">11:00 Marc B.</div>`;
    if (day === 24) eventHtml = `<div class="cal-event-pill">18:00 Julien S.</div>`;
    if (day === 28) eventHtml = `<div class="cal-event-pill">10:30 Jean-Luc</div>`;

    cell.innerHTML = `
      <div class="cal-day-number">${day}</div>
      ${eventHtml}
    `;
    grid.appendChild(cell);
  }
}

function renderAppointments() {
  const list = document.getElementById("appointmentsList");
  list.innerHTML = "";

  APPOINTMENTS.forEach(apt => {
    const item = document.createElement("div");
    item.className = "appt-item";
    item.innerHTML = `
      <div class="appt-time-row">
        <span class="appt-time">${apt.time}</span>
        <span class="appt-badge">Confirmado</span>
      </div>
      <div class="appt-client">${apt.client}</div>
      <div class="appt-details">${apt.type} • 📍 ${apt.venue}</div>
    `;
    list.appendChild(item);
  });
}
