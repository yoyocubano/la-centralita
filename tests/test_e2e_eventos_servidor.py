"""Pruebas E2E sin claves de la cadena servidor del panel (/api/call-event).

Verifica la cadena completa del lado servidor, sin red real ni claves:
  - POST /api/call-event con call_started: se difunde al monitor WebSocket
    y NO se guarda en el historial.
  - POST /api/call-event con call_ended: se guarda en CALLS_DATABASE, su
    lead en LEADS_DATABASE y se difunde a los monitores.
  - GET /api/calls y GET /api/leads con token devuelven lo guardado.
  - Evento invalido (400) no se guarda ni se difunde; sin token 401 y con
    token invalido 403.
  - /ws/monitor sin token rechaza con 4001; con token da bienvenida,
    responde ping con pong y sirve los datos recientes.
  - La difusion llega a todos los monitores conectados.

Funciona con libreria estandar (`python3 -m unittest
tests.test_e2e_eventos_servidor -v`) y con `pytest`. Las dependencias
externas (fastapi, livekit, aiohttp, openai, dotenv) se sustituyen por
stubs minimos solo si no estan instaladas, con el mismo patron de
tests/test_agente_eventos_panel.py.
"""

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Instalar stubs minimos SOLO si las dependencias externas no estan instaladas.
def _ensure_stub(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


try:
    import dotenv  # noqa: F401
except ImportError:
    _ensure_stub("dotenv", load_dotenv=lambda *a, **kw: None)

try:
    import aiohttp  # noqa: F401
except ImportError:
    class _DummySession:
        pass

    class _DummyTimeout:
        def __init__(self, *a, **kw):
            pass

    _ensure_stub("aiohttp", ClientSession=_DummySession, ClientTimeout=_DummyTimeout)

try:
    import openai  # noqa: F401
except ImportError:
    class _DummyOpenAI:
        def __init__(self, *a, **kw):
            pass

    _ensure_stub("openai", AsyncOpenAI=_DummyOpenAI)

try:
    import livekit.api  # noqa: F401
except ImportError:
    class _StubAccessToken:
        def __init__(self, *a, **kw):
            pass

        def with_identity(self, *a, **kw):
            return self

        def with_name(self, *a, **kw):
            return self

        def with_grants(self, *a, **kw):
            return self

        def with_ttl(self, *a, **kw):
            return self

        def to_jwt(self):
            return "jwt-stub-sin-claves"

    class _StubVideoGrants:
        def __init__(self, *a, **kw):
            pass

    _ensure_stub(
        "livekit.api",
        AccessToken=_StubAccessToken,
        VideoGrants=_StubVideoGrants,
    )
    _ensure_stub("livekit", api=sys.modules["livekit.api"])

try:
    import fastapi  # noqa: F401
    from fastapi import HTTPException, WebSocketDisconnect  # noqa: F401
except ImportError:
    class _StubHTTPException(Exception):
        def __init__(self, status_code, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _StubWebSocketDisconnect(Exception):
        pass

    class _StubWebSocket:
        pass

    class _StubRequest:
        def __init__(self, *a, **kw):
            self.client = None

    def _passthrough_default(default=None):
        return default

    class _StubFastAPI:
        def __init__(self, *a, **kw):
            pass

        def add_middleware(self, *a, **kw):
            pass

        def mount(self, *a, **kw):
            pass

        def _route(self, *a, **kw):
            def decorator(fn):
                return fn

            return decorator

        get = _route
        post = _route
        websocket = _route

    class _StubStaticFiles:
        def __init__(self, *a, **kw):
            pass

    class _StubCORSMiddleware:
        def __init__(self, *a, **kw):
            pass

    _ensure_stub(
        "fastapi",
        FastAPI=_StubFastAPI,
        Header=_passthrough_default,
        HTTPException=_StubHTTPException,
        Query=_passthrough_default,
        Request=_StubRequest,
        WebSocket=_StubWebSocket,
        WebSocketDisconnect=_StubWebSocketDisconnect,
    )
    _ensure_stub("fastapi.middleware.cors", CORSMiddleware=_StubCORSMiddleware)
    _ensure_stub("fastapi.middleware", cors=sys.modules["fastapi.middleware.cors"])
    _ensure_stub("fastapi.staticfiles", StaticFiles=_StubStaticFiles)

    from fastapi import HTTPException, WebSocketDisconnect  # noqa: E402,F401

from server.app import (  # noqa: E402
    ALLOWED_EVENT_KEYS,
    CALLS_DATABASE,
    LEADS_DATABASE,
    get_calls,
    get_leads,
    monitor_hub,
    monitor_websocket_endpoint,
    receive_call_event,
)
from agent.config import Config  # noqa: E402

TOKEN_PRUEBA = "token-e2e-prueba-sin-claves"


class _FakeMonitorWebSocket:
    """Monitor WebSocket falso: registra lo enviado y alimenta receive_text."""

    def __init__(self):
        self.sent = []
        self.closed_code = None
        self.accepted = False
        self._queue = asyncio.Queue()

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self, code=1000, reason=""):
        self.closed_code = code

    async def receive_text(self):
        item = await self._queue.get()
        if item is None:
            raise WebSocketDisconnect()
        return item

    def push_text(self, text):
        self._queue.put_nowait(text)


def _started(call_id="call-e2e-1"):
    return {
        "type": "call_started",
        "call_id": call_id,
        "timestamp": "2026-11-28T09:30:00+00:00",
        "status": "iniciada",
        "room": "centralita-test",
        "duration": 0,
        "agent": "Sofia (WELUX Events)",
    }


def _ended(call_id="call-e2e-1", con_lead=True):
    evento = {
        "type": "call_ended",
        "call_id": call_id,
        "timestamp": "2026-11-28T09:31:35+00:00",
        "status": "terminada",
        "room": "centralita-test",
        "duration": 95.5,
        "transcript": "Cliente: busco fotoespejo para gala en Kirchberg.",
        "agent": "Sofia (WELUX Events)",
    }
    if con_lead:
        evento["lead"] = {
            "inicio": "2026-11-28T09:30:00+00:00",
            "fin": "2026-11-28T09:31:35+00:00",
            "lead_hash": "hash-e2e-prueba",
            "motivo": "Cotizacion fotoespejo gala Kirchberg",
            "tipo_evento": "Gala corporativa",
            "fecha_evento": "2026-12-12",
            "es_lead_valido": True,
        }
    return evento


class TestE2EEventosServidor(unittest.IsolatedAsyncioTestCase):
    """Cadena servidor: call-event -> guardado -> historial -> monitor."""

    def setUp(self):
        self._orig_token = Config.AUTH_TOKEN
        self._orig_secret = Config.LIVEKIT_API_SECRET
        Config.AUTH_TOKEN = TOKEN_PRUEBA
        Config.LIVEKIT_API_SECRET = ""
        CALLS_DATABASE.clear()
        LEADS_DATABASE.clear()
        monitor_hub.active_connections.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        Config.AUTH_TOKEN = self._orig_token
        Config.LIVEKIT_API_SECRET = self._orig_secret
        CALLS_DATABASE.clear()
        LEADS_DATABASE.clear()
        monitor_hub.active_connections.clear()

    def _auth(self):
        return f"Bearer {TOKEN_PRUEBA}"

    async def _conectar_monitor(self):
        ws = _FakeMonitorWebSocket()
        await monitor_hub.connect(ws)
        return ws

    async def test_call_started_se_difunde_y_no_se_guarda(self):
        ws = await self._conectar_monitor()
        resultado = await receive_call_event(_started(), self._auth())
        self.assertEqual(resultado["status"], "broadcasted")
        self.assertEqual(resultado["receivers"], 1)
        self.assertEqual(len(CALLS_DATABASE), 0)
        self.assertEqual(len(LEADS_DATABASE), 0)
        # Bienvenida + evento difundido, y nada mas.
        self.assertEqual(ws.sent[0]["type"], "connection_established")
        self.assertEqual(ws.sent[1]["type"], "call_started")
        self.assertEqual(ws.sent[1]["call_id"], "call-e2e-1")
        self.assertIn("server_timestamp", ws.sent[1])
        historial = await get_calls(self._auth())
        self.assertEqual(historial["count"], 0)
        self.assertEqual(historial["calls"], [])

    async def test_call_ended_se_guarda_se_difunde_y_aparece_en_historial(self):
        ws = await self._conectar_monitor()
        resultado = await receive_call_event(_ended(), self._auth())
        self.assertEqual(resultado["status"], "broadcasted")
        self.assertEqual(resultado["receivers"], 1)
        self.assertEqual(len(CALLS_DATABASE), 1)
        self.assertEqual(len(LEADS_DATABASE), 1)
        self.assertEqual(CALLS_DATABASE[0]["call_id"], "call-e2e-1")
        self.assertEqual(CALLS_DATABASE[0]["duration"], 95.5)
        self.assertEqual(LEADS_DATABASE[0]["lead_hash"], "hash-e2e-prueba")
        self.assertEqual(ws.sent[1]["type"], "call_ended")
        llamadas = await get_calls(self._auth())
        self.assertEqual(llamadas["count"], 1)
        self.assertEqual(llamadas["calls"][0]["call_id"], "call-e2e-1")
        leads = await get_leads(self._auth())
        self.assertEqual(leads["count"], 1)
        self.assertEqual(leads["leads"][0]["motivo"], "Cotizacion fotoespejo gala Kirchberg")

    async def test_historial_acumula_varias_llamadas_y_solo_ended_guarda(self):
        await receive_call_event(_started("call-e2e-1"), self._auth())
        await receive_call_event(_ended("call-e2e-1", con_lead=True), self._auth())
        await receive_call_event(_started("call-e2e-2"), self._auth())
        await receive_call_event(_ended("call-e2e-2", con_lead=False), self._auth())
        llamadas = await get_calls(self._auth())
        self.assertEqual(llamadas["count"], 2)
        self.assertEqual(
            [c["call_id"] for c in llamadas["calls"]],
            ["call-e2e-1", "call-e2e-2"],
        )
        leads = await get_leads(self._auth())
        self.assertEqual(leads["count"], 1)

    async def test_evento_invalido_400_no_guarda_ni_difunde(self):
        ws = await self._conectar_monitor()
        invalidos = [
            {"call_id": "sin-type"},  # sin "type"
            {**_started(), "clave_no_permitida": "x"},  # clave fuera de la whitelist
            {"type": "call_started", "secreto": "no-debe-colarse"},
        ]
        for evento in invalidos:
            with self.subTest(evento=evento):
                with self.assertRaises(HTTPException) as cm:
                    await receive_call_event(dict(evento), self._auth())
                self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(len(CALLS_DATABASE), 0)
        self.assertEqual(len(LEADS_DATABASE), 0)
        # Solo la bienvenida: ningun evento invalido llego al monitor.
        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(ws.sent[0]["type"], "connection_established")

    async def test_sin_token_401_y_token_invalido_403(self):
        casos = [
            (None, 401),  # sin header Authorization
            ("Bearer token-no-valido", 403),  # Bearer con token desconocido
            ("Token sin-bearer", 401),  # esquema distinto de Bearer
        ]
        for auth, esperado in casos:
            with self.subTest(auth=auth):
                with self.assertRaises(HTTPException) as cm:
                    await receive_call_event(_ended(), auth)
                self.assertEqual(cm.exception.status_code, esperado)
                with self.assertRaises(HTTPException) as cm:
                    await get_calls(auth)
                self.assertEqual(cm.exception.status_code, esperado)
                with self.assertRaises(HTTPException) as cm:
                    await get_leads(auth)
                self.assertEqual(cm.exception.status_code, esperado)
        self.assertEqual(len(CALLS_DATABASE), 0)
        self.assertEqual(len(LEADS_DATABASE), 0)

    async def test_websocket_sin_token_rechaza_4001(self):
        ws = _FakeMonitorWebSocket()
        await monitor_websocket_endpoint(ws, token="")
        self.assertEqual(ws.closed_code, 4001)
        self.assertFalse(ws.accepted)
        self.assertNotIn(ws, monitor_hub.active_connections)
        self.assertEqual(ws.sent, [])

    async def test_websocket_con_token_bienvenida_ping_y_datos_recientes(self):
        await receive_call_event(_ended("call-e2e-1", con_lead=True), self._auth())
        ws = _FakeMonitorWebSocket()
        ws.push_text(json.dumps({"action": "ping"}))
        ws.push_text(json.dumps({"action": "get_recent_data"}))
        ws.push_text(None)  # centinela: cierra el bucle con WebSocketDisconnect
        await monitor_websocket_endpoint(ws, token=TOKEN_PRUEBA)
        self.assertTrue(ws.accepted)
        tipos = [m.get("type") for m in ws.sent]
        self.assertEqual(tipos[0], "connection_established")
        self.assertIn("pong", tipos)
        self.assertIn("recent_data", tipos)
        recientes = next(m for m in ws.sent if m.get("type") == "recent_data")
        self.assertEqual(len(recientes["calls"]), 1)
        self.assertEqual(recientes["calls"][0]["call_id"], "call-e2e-1")
        self.assertEqual(len(recientes["leads"]), 1)
        # Al cerrarse, el monitor sale del hub.
        self.assertNotIn(ws, monitor_hub.active_connections)

    async def test_dos_monitores_reciben_la_difusion(self):
        ws1 = await self._conectar_monitor()
        ws2 = await self._conectar_monitor()
        resultado = await receive_call_event(_started(), self._auth())
        self.assertEqual(resultado["receivers"], 2)
        for ws in (ws1, ws2):
            self.assertEqual(ws.sent[1]["type"], "call_started")
            self.assertEqual(ws.sent[1]["call_id"], "call-e2e-1")

    def test_constantes_y_server_timestamp(self):
        self.assertIn("type", ALLOWED_EVENT_KEYS)
        self.assertIn("call_id", ALLOWED_EVENT_KEYS)
        self.assertIn("lead", ALLOWED_EVENT_KEYS)
        self.assertIn("transcript", ALLOWED_EVENT_KEYS)
        self.assertNotIn("nombre", ALLOWED_EVENT_KEYS)
        self.assertNotIn("telefono", ALLOWED_EVENT_KEYS)
        self.assertNotIn("email", ALLOWED_EVENT_KEYS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
