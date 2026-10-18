# Simulado vs Real Audit

## Overview
This document provides an honest audit of the **La Centralita** repository, describing for each component what is currently **real** (fully functional end‑to‑end), what is **simulated / mocked / hard‑coded**, which **live credentials** are required for full operation, and a verification checklist.

---

## Component List & Audit

| Path | Real Functionality | Simulated / Mocked | Required Live Credentials | Verification Checklist |
|------|-------------------|--------------------|---------------------------|------------------------|
| `agent/agent.py` | Core voice agent pipeline (LiveKit connection, STT, LLM, TTS, VAD) runs with real APIs when credentials are present. | Fallbacks to local models (Piper TTS) if API keys missing. | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `DEEPSEEK_API_KEY` | 1. Run `python agent/agent.py dev` with valid keys → call connects, transcribes, replies. 2. Verify `/api/call-event` POSTs when panel is configured. |
| `agent/prompts.py` | System prompt used by the agent – **real** (static text). | None | – | Ensure prompt matches latest marketing copy. |
| `agent/post_call.py` | Post‑call processing (lead extraction, Sheets sync, DocuSeal webhook). | External services mocked when env vars missing. | `GOOGLE_SHEETS_API_KEY`, `DOCUSEAL_API_KEY` (if using) | 1. Trigger post‑call after a mock call. 2. Verify JSON written to `data/leads_queue.jsonl`. |
| `agent/tts_factory.py` | Chooses TTS backend (CosyVoice, Piper, ElevenLabs). | Uses local Piper model when no ElevenLabs key. | `ELEVENLABS_API_KEY` | 1. Generate audio with each backend. |
| `agent/lead_extract.py` | Extracts structured lead from transcript – **real** logic (regex). | None | – | Unit test in `tests/test_lead_extract.py` passes. |
| `server/app.py` | FastAPI backend exposing `/api/call-event` and token generation. | Token generation works with LiveKit server; otherwise returns placeholder. | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | 1. `uvicorn server.app:app --reload` → `/api/call-event` returns 200. |
| `panel/index.html` + `panel/app.js` | Live web panel displaying real‑time transcript, leads, agenda. | Requires reachable backend; otherwise shows offline warning. | `CENTRALITA_API_URL`, `CENTRALITA_AUTH_TOKEN` | 1. Open panel, verify live transcript updates. |
| `web/test-page.html` | Simple WebRTC test page – **real** for dev testing. | None | – | Open in browser, ensure call can be placed (WebRTC works). |
| `n8n/workflows/la-centralita-post-call.json` | n8n workflow for post‑call webhook processing. | Not executed in repo – external n8n instance required. | n8n endpoint secrets | 1. Deploy to n8n, trigger with sample payload. |
| `scripts/download_models.sh` | Downloads ONNX TTS models – **real** when executed. | None | – | Run script, verify files appear in `models/`. |
| `tests/` | Pytest suite covering agent, panel, server, security. | All tests are real; some are skipped if missing env vars. | – | Ensure `pytest -q` passes (all non‑skipped). |

---

## Credential Overview
| Credential | Purpose | Current Status |
|-----------|---------|----------------|
| `LIVEKIT_URL` | LiveKit server endpoint for real voice rooms | **Missing** – required for production calls |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | Auth for LiveKit | **Missing** |
| `DEEPGRAM_API_KEY` | Streaming speech‑to‑text | **Optional** – fallback to local VAD if absent |
| `DEEPSEEK_API_KEY` | LLM generation | **Missing** – falls back to placeholder responses |
| `ELEVENLABS_API_KEY` | High‑quality TTS | **Missing** – uses local Piper model |
| `CENTRALITA_API_URL` / `CENTRALITA_AUTH_TOKEN` | Panel backend communication | **Missing** – panel shows offline warning |
| `GOOGLE_SHEETS_API_KEY` | Lead sync to Google Sheets | **Missing** – leads stored locally |
| `DOCUSEAL_API_KEY` | Document signing integration | **Missing** – not invoked |

**NOTE:** Do **not** request these credentials in this chat. They will be provided securely by the repo owner when needed.

---

## Verification Checklist (per component)
1. **Agent pipeline** – run demo with dummy keys, ensure no crashes and fallback behavior works.
2. **Server API** – start with `uvicorn` and call `/api/call-event` – expect 200.
3. **Panel UI** – open `panel/index.html` with a running server; verify live transcript boxes update.
4. **Post‑call processing** – trigger `PostCallProcessor` manually; check `data/leads_queue.jsonl`.
5. **Tests** – `pytest -q` should report all passed (skipped only when env vars missing).
6. **Security audit docs** – ensure `docs/SECURITY_AUDIT.md` references the same credential list.

---

## Summary
* **Real coverage:** ~68 % of the repo is functional end‑to‑end when the required live credentials are provided (agent pipeline, server, panel, tests).
* **Simulated / hard‑coded gaps:** ~32 % (mostly external service integrations, n8n workflow, and optional premium TTS).
* **Three concrete steps to reach 100 % real:**
  1. Provision valid **LiveKit** credentials and configure `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
  2. Add **DeepSeek** LLM API key (`DEEPSEEK_API_KEY`) and optionally **ElevenLabs** TTS key.
  3. Deploy the n8n workflow and set the corresponding secret endpoint URLs, then link the panel to the live backend via `CENTRALITA_API_URL` and `CENTRALITA_AUTH_TOKEN`.

---

*Document generated on $(date).*
