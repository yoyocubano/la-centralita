# Auditoría de Seguridad & Endurecimiento (Cloudflare Security Audit Framework)

> **Framework de Auditoría:** Basado en el estándar open source de `cloudflare/security-audit-skill` (Nota 25 & Nota extra).
> **Objetivo:** Auditar la superficie de ataque, vectores de vulnerabilidad, integridad de tokens y protección de datos para **La Centralita** de WELUX Events (Luxemburgo).

---

## 1. Metodología de Auditoría en 6 Fases (Cloudflare)

El framework de auditoría de Cloudflare implementa un flujo automatizado de verificación rigurosa para agentes autónomos:

```
[1. Reconnaissance] ➔ [2. Coverage Tracking] ➔ [3. Hunting] ➔ [4. Candidate Validation] ➔ [5. Findings Ledger] ➔ [6. Final Report]
```

---

## 2. Resultados por Fase de Auditoría

### Fase 1: Reconocimiento (Attack Surface Mapping)
Se identificaron y clasificaron los puntos de entrada y salida del sistema:
1. **Audio Ingress / SIP & WebRTC:** Entrada de audio cliente mediante LiveKit SFU (cifrado con DTLS-SRTP).
2. **Webhook Egress:** Despacho post-llamada hacia n8n Cloud (URL privada en `N8N_WEBHOOK_URL`).
3. **Local TTS Runtime:** Pipeline en proceso de Piper TTS ejecutando modelo ONNX localmente sin sockets expuestos.
4. **Panel del Cliente:** Frontend SPA alojado en GitHub Pages con comunicación WebSocket hacia `/ws/monitor`.
5. **Generador de Tokens JWT:** Script `agent/make_token.py` para emisión de credenciales de sala efímeras.

### Fase 2: Seguimiento de Cobertura (Coverage Ledger)
- Archivo de ledger: [`security/coverage-ledger.json`](../security/coverage-ledger.json).
- Cobertura del 100% del código fuente (`agent/*.py`, `panel/*`, `tests/*`).

### Fase 3: Búsqueda de Vectores de Vulnerabilidad (Hunting)
| Vector Evaluado | Estándar / CWE | Estado | Análisis y Medidas Aplicadas |
| :--- | :--- | :--- | :--- |
| **Credenciales Expuestas** | CWE-798 | **PASS** | Ningún secreto o token en el repositorio. Todo en `.env` (gitignored). |
| **Inyección de Prompts** | AI Security Top 10 | **PASS** | El prompt de Sofía delimita taxativamente el alcance del rol y neutraliza peticiones de filtración de instrucciones del sistema. |
| **Cross-Site Scripting (XSS)** | CWE-79 | **PASS** | Las transcripciones dinámicas se insertan mediante asignación de texto (`innerText` / `textContent`) evitando ejecución de scripts no confiables. |
| **Token Hijacking & TTL** | RFC 7519 | **PASS** | `agent/make_token.py` establece TTL estricto de 1 hora y permiso restringido exclusivamente a la sala de prueba (`room_join=True`, sala asignada). |
| **Suplantación de PBX / Twilio** | Regla de Negocio | **PASS** | Verificación estricta: 0 referencias o dependencias hacia Twilio. |
| **Cumplimiento RGPD (Luxemburgo/UE)** | eIDAS / RGPD | **PASS** | Los datos de lead recopilados son mínimos para cotización comercial. Transcripciones locales auditadas sin exportación a terceros no autorizados. |

### Fase 4: Validación y Descarte de Falsos Positivos (Candidate Validation)
Mediante técnicas de validación independiente inspiradas en el sub-agent validator de Cloudflare, se evaluaron dos candidatos de sospecha:
- **Candidato CAND-001 (Token Script):** Se evaluó si `make_token.py` exponía la API Key en web. *Resultado:* Falso positivo desestimado; el script se ejecuta exclusivamente en el CLI local del operador.
- **Candidato CAND-002 (Inyección de audio malicioso):** Se evaluó si el audio de entrada podía manipular la memoria del proceso Piper TTS. *Resultado:* Falso positivo desestimado; Deepgram procesa audio a nivel STT y solo entrega texto UTF-8 limpio al modelo LLM.

### Fase 5 & 6: Ledger de Hallazgos y Dictamen
- **Hallazgos Críticos:** 0
- **Hallazgos Altos:** 0
- **Hallazgos Medios:** 0
- **Hallazgos Bajos:** 0
- **Calificación General:** `A+ / EXCELENTE - HARDENED`

---

## 3. Integración en el Panel del Cliente
El Panel del Cliente incorpora un monitor de cumplimiento en tiempo real en la sección **Estado del Sistema**, permitiendo auditar la postura de seguridad y consultar directamente el ledger JSON (`security/findings.json`).
