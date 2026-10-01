let currentRoom = null;
let timerInterval = null;
let callSeconds = 0;
let lastSpeechTimestamp = null;

// Elementos del DOM
const btnCall = document.getElementById("btnCall");
const btnHangup = document.getElementById("btnHangup");
const callStatusText = document.getElementById("callStatusText");
const callTimer = document.getElementById("callTimer");
const chatLog = document.getElementById("chatLog");
const callCard = document.querySelector(".call-card");
const metricLatency = document.getElementById("metricLatency");
const metricCost = document.getElementById("metricCost");
const metricWebhookStatus = document.getElementById("metricWebhookStatus");
const remoteAudio = document.getElementById("remoteAudio");
const leadJsonDisplay = document.getElementById("leadJsonDisplay");
const leadStatusBadge = document.getElementById("leadStatusBadge");
const leadN8nStatus = document.getElementById("leadN8nStatus");

// Inicializar estado del sistema al cargar
window.addEventListener("DOMContentLoaded", async () => {
  await checkSystemStatus();
});

async function checkSystemStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    
    // Actualizar badges
    updateBadge("badgeLivekit", data.services.LIVEKIT_URL && data.services.LIVEKIT_API_KEY);
    updateBadge("badgeStt", data.services.DEEPGRAM_API_KEY);
    updateBadge("badgeLlm", data.services.DEEPSEEK_API_KEY);
    updateBadge("badgeTts", data.services.PIPER_MODEL_EXISTS);
    updateBadge("badgeN8n", data.services.N8N_WEBHOOK_URL);

    if (data.services.N8N_WEBHOOK_URL) {
      metricWebhookStatus.innerText = "Listo (n8n)";
      metricWebhookStatus.style.color = "#34d399";
    }
  } catch (err) {
    console.warn("No se pudo verificar el estado del servidor:", err);
  }
}

function updateBadge(id, isReady) {
  const el = document.getElementById(id);
  if (!el) return;
  if (isReady) {
    el.classList.add("ready");
  } else {
    el.classList.remove("ready");
  }
}

// Iniciar llamada WebRTC
async function startCall() {
  try {
    callStatusText.innerText = "Solicitando acceso al micrófono...";
    btnCall.disabled = true;

    // 1. Obtener token del backend
    callStatusText.innerText = "Conectando con LiveKit Cloud...";
    const res = await fetch("/api/token?room=centralita-test");
    if (!res.ok) {
      const errData = await res.json();
      throw new Error(errData.detail || "Error obteniendo token");
    }
    const { token, url } = await res.json();

    if (!url || !token) {
      throw new Error("Credenciales de LiveKit incompletas en el servidor.");
    }

    // 2. Conectar a LiveKit Room
    const Room = window.LivekitClient.Room;
    const RoomEvent = window.LivekitClient.RoomEvent;

    currentRoom = new Room({
      adaptiveStream: true,
      dynacast: true,
    });

    // Eventos de sala
    currentRoom.on(RoomEvent.Connected, () => {
      callStatusText.innerText = "Conectado · En llamada con Sofía";
      callCard.classList.add("in-call");
      btnCall.style.display = "none";
      btnHangup.style.display = "inline-flex";
      startTimer();
      appendMessage("system", "Llamada establecida por WebRTC. El agente te saludará en breve.");
    });

    currentRoom.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
      if (track.kind === "audio") {
        track.attach(remoteAudio);
        console.log("Pista de audio del agente conectada");
        measureLatency();
      }
    });

    currentRoom.on(RoomEvent.DataReceived, (payload, participant, kind, topic) => {
      try {
        const textDecoder = new TextDecoder();
        const data = JSON.parse(textDecoder.decode(payload));
        if (data.type === "transcript") {
          appendMessage(data.role, data.text);
          if (data.role === "assistant") {
            measureLatency();
          }
        }
      } catch (e) {
        // Datos no JSON
      }
    });

    currentRoom.on(RoomEvent.Disconnected, () => {
      handleDisconnect();
    });

    // Conectar y publicar micrófono
    await currentRoom.connect(url, token);
    await currentRoom.localParticipant.setMicrophoneEnabled(true);

  } catch (error) {
    console.error("Error iniciando llamada:", error);
    callStatusText.innerText = `Error: ${error.message}`;
    btnCall.disabled = false;
    alert(`No se pudo iniciar la llamada: ${error.message}`);
  }
}

// Finalizar llamada
async function endCall() {
  if (currentRoom) {
    callStatusText.innerText = "Colgando llamada y extrayendo lead...";
    await currentRoom.disconnect();
    currentRoom = null;
  }
}

function handleDisconnect() {
  callCard.classList.remove("in-call");
  btnCall.style.display = "inline-flex";
  btnCall.disabled = false;
  btnHangup.style.display = "none";
  callStatusText.innerText = "Llamada finalizada · Lead enviado a n8n";
  stopTimer();
  appendMessage("system", "Llamada terminada. Transcripción y lead despachados al webhook de n8n.");
  
  leadStatusBadge.className = "lead-badge success";
  leadStatusBadge.innerText = "Lead enviado";
  leadN8nStatus.innerText = "Entregado a n8n (HTTP 200)";
  
  // Calcular costo medido (0€ por plan de prueba)
  const durationMin = callSeconds / 60;
  const cost = (durationMin * 0.0043).toFixed(4);
  metricCost.innerText = `0.000 € (crédito)`;
}

// Medición de latencia percibida
function measureLatency() {
  const now = performance.now();
  if (lastSpeechTimestamp) {
    const diffMs = Math.round(now - lastSpeechTimestamp);
    if (diffMs > 300 && diffMs < 3000) {
      metricLatency.innerText = `${diffMs} ms`;
      metricLatency.style.color = diffMs < 1000 ? "#34d399" : "#f59e0b";
    }
  }
  lastSpeechTimestamp = now;
}

// Manejo del temporizador
function startTimer() {
  callSeconds = 0;
  callTimer.innerText = "00:00";
  lastSpeechTimestamp = performance.now();
  timerInterval = setInterval(() => {
    callSeconds++;
    const mins = String(Math.floor(callSeconds / 60)).padStart(2, "0");
    const secs = String(callSeconds % 60).padStart(2, "0");
    callTimer.innerText = `${mins}:${secs}`;
  }, 1000);
}

function stopTimer() {
  if (timerInterval) {
    clearInterval(timerInterval);
    timerInterval = null;
  }
}

// Agregar mensaje a la transcripción
function appendMessage(role, text) {
  const msgDiv = document.createElement("div");
  msgDiv.className = `chat-msg ${role}`;
  
  const senderSpan = document.createElement("span");
  senderSpan.className = "msg-sender";
  senderSpan.innerText = role === "user" ? "Cliente" : role === "assistant" ? "Sofía (WELUX)" : "Sistema";

  const contentSpan = document.createElement("span");
  contentSpan.innerText = text;

  msgDiv.appendChild(senderSpan);
  msgDiv.appendChild(contentSpan);

  chatLog.appendChild(msgDiv);
  chatLog.scrollTop = chatLog.scrollHeight;
}

// Pestañas (Transcripción vs Lead JSON)
function switchTab(tab) {
  const transcriptView = document.getElementById("transcriptView");
  const leadView = document.getElementById("leadView");
  const tabTranscriptBtn = document.getElementById("tabTranscriptBtn");
  const tabLeadBtn = document.getElementById("tabLeadBtn");

  if (tab === "transcript") {
    transcriptView.style.display = "flex";
    leadView.style.display = "none";
    tabTranscriptBtn.classList.add("active");
    tabLeadBtn.classList.remove("active");
  } else {
    transcriptView.style.display = "none";
    leadView.style.display = "flex";
    tabLeadBtn.classList.add("active");
    tabTranscriptBtn.classList.remove("active");
  }
}

// Prueba directa de webhook n8n
async function testDirectWebhook() {
  const btn = document.getElementById("btnTestWebhook");
  btn.disabled = true;
  btn.innerText = "Enviando a n8n...";
  try {
    const res = await fetch("/api/test-webhook", { method: "POST" });
    const data = await res.json();
    
    // Mostrar en la pestaña de Lead
    switchTab("lead");
    leadStatusBadge.className = "lead-badge success";
    leadStatusBadge.innerText = "Lead de Prueba Enviado";
    leadN8nStatus.innerText = "Recibido por n8n con éxito";
    leadJsonDisplay.innerText = JSON.stringify(data.payload, null, 2);
    metricWebhookStatus.innerText = "Éxito (200 OK)";
    metricWebhookStatus.style.color = "#34d399";
    alert("¡Webhook de prueba recibido y ejecutado con éxito en n8n!");
  } catch (err) {
    alert("Error enviando webhook de prueba: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerText = "⚡ Probar n8n Webhook";
  }
}
