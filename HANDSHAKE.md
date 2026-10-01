# HANDSHAKE — La Centralita (WELUX)

> **Documento único de coordinación.** Todos los agentes que intervienen en este
> proyecto (**moise, Antigravity, Claude, Jay**) LEEN este archivo antes de tocar
> nada y lo ACTUALIZAN aquí mismo.
>
> **Reglas:**
> 1. No crear otro documento de coordinación: todo vive en este archivo.
> 2. Cada agente actualiza su sección y el tablero; no borrar el trabajo de otros.
> 3. Las claves reales NUNCA se escriben aquí (solo qué falta / qué ya existe).
> 4. Ver la REGLA DE SEGURIDAD sobre secretos más abajo: aplica a TODOS
>    los agentes sin excepciones.
> 4. Cuando algo queda resuelto, márcalo `✅ RESUELTO (fecha)` con evidencia
>    (commit, URL, medición).

## 🔒 REGLA DE SEGURIDAD — SECRETOS (para TODOS, sin excepciones)

Claves API, tokens, códigos de verificación, contraseñas y cualquier dato
sensible:

- Viajan ÚNICAMENTE por el canal interno (el chat "la centralita").
- Van directo al `.env` local (gitignored) de quien los necesite. Nada más.
- PROHIBIDO escribirlos en el repo, en este HANDSHAKE, en commits, en issues,
  en notas/memoria, en logs o exponerlos en cualquier otra superficie.
- Si un secreto aparece donde no debe: se rota inmediatamente y se avisa en
  el chat interno.

No hay excepciones a esta regla para ningún agente.

## 1. Qué es

Centralita telefónica con agente de voz natural (WELUX). El cliente llama →
el agente conversa como una recepcionista (reservas, cotizaciones, información) →
la llamada se transcribe en tiempo real → al colgar se extrae el lead
(nombre, teléfono, motivo, fecha, detalles) y se envía al dueño + n8n.

## 2. Reglas de oro (no negociables)

- NO tocar el chatbot web Rebeca AI (repo `welux-events`). Proyecto separado.
- PROHIBIDO usar Twilio bajo ningún concepto.
- Fase 1 a costo cero: solo tiers gratuitos / créditos de prueba.
  Nada de tarjetas ni pagos sin orden expresa del usuario.
- El `.env` real siempre va en `.gitignore`. Cero secretos en el repo.

## 3. Stack Fase 1 (prueba $0)

| Pieza | Servicio | Estado |
|---|---|---|
| Transporte WebRTC | LiveKit Cloud (Build, gratis) | 🟡 cuenta en creación |
| STT | Deepgram Nova-3 | 🟡 cuenta en creación |
| LLM | DeepSeek (cliente OpenAI-compatible) | 🟡 cuenta en creación |
| TTS | Piper (local, open source, $0) | 🟡 adapter por verificar |
| Post-llamada | n8n webhook | ✅ `https://weluxdigitalservices.app.n8n.cloud/webhook/centralita-test` |
| Canal de prueba | Página web WebRTC (sin número) | 🟡 scaffold listo |

## 4. Tablero Fase 1 — criterio de salida

- [ ] Cuentas trial creadas (LiveKit, Deepgram, DeepSeek)
- [ ] Adapter Piper TTS verificado contra la versión instalada de `livekit-agents`
- [ ] Agente conversa por WebRTC en la página de prueba
- [ ] Latencia percibida < 1 s (medir y anotar aquí)
- [ ] Transcripción + lead extraído al colgar → POST a n8n verificado en el workflow
- [ ] Costo real por llamada de prueba medido (anotar aquí)
- [ ] `git push` a `main` con la versión probada
- [ ] Reporte de requisitos para Fase 2 (número LU real — NO ejecutar sin orden)

## 5. Mapa del repo

- `HANDSHAKE.md` — este archivo (leer primero, actualizar siempre)
- `agent/agent.py` — worker de LiveKit Agents (STT → LLM → TTS + envío a n8n)
- `agent/prompts.py` — guion del agente de voz
- `agent/piper_tts.py` — adapter TTS Piper (verificar contra SDK instalado)
- `agent/lead_extract.py` — extracción de lead al colgar (DeepSeek, temp 0.1)
- `agent/requirements.txt` — dependencias Python
- `agent/.env.example` — plantilla (copiar a `.env`, nunca subir el real)
- `agent/make_token.py` — genera token de prueba para la página web
- `web/test-page.html` — página de prueba WebRTC (llamar desde el navegador)
- `n8n/README.md` — formato del payload que recibe el webhook

## 6. Bitácora (actualizar aquí mismo, no en otro archivo)

### 2026-10-01 — moise
- Scaffold inicial v0.1 creado y pusheado (código sin probar en vivo).
- Webhook n8n creado, publicado y entregado a Antigravity.
- Estrategia de correos para trials enviada a Antigravity
  (info@weluxevents.com; NO usar yucolaguilar@gmail.com).
- ⏳ Pendiente: credenciales del usuario (las pasará por chat) → van al `.env`
  local de Antigravity, nunca al repo.

## 7. Decisiones pendientes (las toma el usuario, Fase 2)

1. Telnyx vs Vonage para el número virtual de Luxemburgo.
2. Voz del agente: masculina / femenina.
3. Nombre del agente y guion de bienvenida definitivo.
4. Aviso al dueño: email, WhatsApp o ambos.
5. Número público desde el día uno o prueba privada primero.
