"""Pruebas del límite de conexiones WebSocket en el monitor (/ws/monitor)
(sin claves, sin red, sin dependencias extra: solo librería estándar).

Cubre el endurecimiento de server/app.py:

  - MonitorConnectionManager.connect() acepta como máximo
    MAX_MONITOR_CONNECTIONS (50) monitores simultáneos.
  - La conexión 51 (aunque lleve token válido) es rechazada antes de
    accept() con el código WebSocket 1013 (Try Again Later) y no entra en
    active_connections.
  - Al desconectarse un monitor (o al expulsar uno caído en broadcast()),
    se libera la plaza y puede entrar uno nuevo.
  - La autenticación (código 4001) sigue ejecutándose ANTES del chequeo
    de aforo incluso cuando el hub está lleno.
  - /api/status refleja monitors_connected sin superar nunca el tope.

Solo librería estándar de Python: sustituye fastapi, livekit, aiohttp,
openai y python-dotenv por stubs mínimos antes de importar server.app.

Cómo correr (desde la raíz del repo, sin instalar nada)::

    python3 -m unittest tests.test_limite_conexiones_monitor -v
"""

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Stubs mínimos de las dependencias del servidor (solo stdlib).
# ---------------------------------------------------------------------------

def _install_stub_module(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _HTTPException(Exception):
    """Sustituto de fastapi.HTTPException."""

    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _identity_decorator(*args, **kwargs):
    def wrap(fn):
        return fn
    return wrap


class _FastAPI:
    def __init__(self, *args, **kwargs):
        pass

    def add_middleware(self, *args, **kwargs):
        pass

    def mount(self, *args, **kwargs):
        pass

    get = staticmethod(_identity_decorator)
    post = staticmethod(_identity_decorator)
    websocket = staticmethod(_identity_decorator)


class _Request:
    pass


class _WebSocket:
    pass


class _WebSocketDisconnect(Exception):
    pass


def _Header(default=None):
    return default


def _Query(default=None):
    return default


class _CORSMiddleware:
    def __init__(self, *args, **kwargs):
        pass


class _StaticFiles:
    def __init__(self, *args, **kwargs):
        pass


class _AccessToken:
    def __init__(self, *args, **kwargs):
        pass

    def with_identity(self, *args, **kwargs):
        return self

    def with_name(self, *args, **kwargs):
        return self

    def with_grants(self, *args, **kwargs):
        return self

    def with_ttl(self, *args, **kwargs):
        return self

    def to_jwt(self):
        return "token-simulado-solo-test"


class _VideoGrants:
    def __init__(self, *args, **kwargs):
        pass


class _ClientSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _ClientTimeout:
    def __init__(self, *args, **kwargs):
        pass


class _AsyncOpenAI:
    def __init__(self, *args, **kwargs):
        pass


try:
    import dotenv  # noqa: F401
except ImportError:  # pragma: no cover - solo en máquinas sin dotenv
    _dotenv_stub = types.ModuleType("dotenv")
    _dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _dotenv_stub

_install_stub_module(
    "fastapi",
    FastAPI=_FastAPI,
    Header=_Header,
    HTTPException=_HTTPException,
    Query=_Query,
    Request=_Request,
    WebSocket=_WebSocket,
    WebSocketDisconnect=_WebSocketDisconnect,
)
_install_stub_module("fastapi.middleware")
_install_stub_module("fastapi.middleware.cors", CORSMiddleware=_CORSMiddleware)
_install_stub_module("fastapi.staticfiles", StaticFiles=_StaticFiles)
_livekit_api = _install_stub_module(
    "livekit.api", AccessToken=_AccessToken, VideoGrants=_VideoGrants
)
_install_stub_module("livekit", api=_livekit_api)
_install_stub_module(
    "aiohttp", ClientSession=_ClientSession, ClientTimeout=_ClientTimeout
)
_install_stub_module("openai", AsyncOpenAI=_AsyncOpenAI)

import server.app as app_modulo  # noqa: E402
from agent.config import Config  # noqa: E402


class _FakeWebSocket:
    """WebSocket mínimo en memoria para probar el hub sin red."""

    def __init__(self, fail_on_send=False):
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.close_reason = None
        self.sent_json = []
        self.fail_on_send = fail_on_send

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def send_json(self, payload):
        # El saludo inicial ocurre dentro de connect(); si fail_on_send está
        # activo tras conectar, solo falla en broadcasts posteriores.
        if self.fail_on_send and self.sent_json:
            raise RuntimeError("Conexión WebSocket rota en test")
        self.sent_json.append(payload)

    async def receive_text(self):
        raise app_modulo.WebSocketDisconnect()


class TestLimiteConexionesMonitor(unittest.IsolatedAsyncioTestCase):
    """Tope de 50 conexiones WebSocket simultáneas en /ws/monitor."""

    TOKEN = "token-de-prueba-monitor-ws"

    def setUp(self):
        self._token_original = Config.AUTH_TOKEN
        Config.AUTH_TOKEN = self.TOKEN
        app_modulo.monitor_hub.active_connections.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        Config.AUTH_TOKEN = self._token_original
        app_modulo.monitor_hub.active_connections.clear()

    async def _llenar_monitores(self, cantidad=None):
        limite = app_modulo.MAX_MONITOR_CONNECTIONS if cantidad is None else cantidad
        conectados = []
        for _ in range(limite):
            ws = _FakeWebSocket()
            ok = await app_modulo.monitor_hub.connect(ws)
            self.assertTrue(ok)
            conectados.append(ws)
        return conectados

    def test_constante_tope_conexiones_monitor_es_50(self):
        self.assertEqual(app_modulo.MAX_MONITOR_CONNECTIONS, 50)

    async def test_acepta_hasta_el_tope_de_50_conexiones(self):
        conectados = await self._llenar_monitores()
        self.assertEqual(len(conectados), 50)
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 50)
        for ws in conectados:
            self.assertTrue(ws.accepted)
            self.assertFalse(ws.closed)
            self.assertEqual(len(ws.sent_json), 1)
            self.assertEqual(ws.sent_json[0]["type"], "connection_established")

    async def test_rechaza_conexion_51_con_1013_sin_aceptar(self):
        await self._llenar_monitores()
        ws51 = _FakeWebSocket()
        await app_modulo.monitor_websocket_endpoint(ws51, token=self.TOKEN)
        self.assertFalse(ws51.accepted)
        self.assertTrue(ws51.closed)
        self.assertEqual(ws51.close_code, 1013)
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 50)

    async def test_desconectar_libera_plaza_para_nueva_conexion(self):
        conectados = await self._llenar_monitores()
        app_modulo.monitor_hub.disconnect(conectados[0])
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 49)

        ws_nuevo = _FakeWebSocket()
        ok = await app_modulo.monitor_hub.connect(ws_nuevo)
        self.assertTrue(ok)
        self.assertTrue(ws_nuevo.accepted)
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 50)

    async def test_sin_token_o_invalido_sigue_siendo_4001_aunque_este_lleno(self):
        await self._llenar_monitores()
        for token in ("", "token-invalido"):
            ws = _FakeWebSocket()
            await app_modulo.monitor_websocket_endpoint(ws, token=token)
            self.assertFalse(ws.accepted)
            self.assertTrue(ws.closed)
            self.assertEqual(ws.close_code, 4001)
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 50)

    async def test_broadcast_expulsa_monitor_caido_y_libera_plaza(self):
        await self._llenar_monitores(49)
        ws_caido = _FakeWebSocket(fail_on_send=True)
        self.assertTrue(await app_modulo.monitor_hub.connect(ws_caido))
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 50)

        await app_modulo.monitor_hub.broadcast({"type": "ping"})
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 49)
        self.assertNotIn(ws_caido, app_modulo.monitor_hub.active_connections)

        ws_reemplazo = _FakeWebSocket()
        self.assertTrue(await app_modulo.monitor_hub.connect(ws_reemplazo))
        self.assertEqual(len(app_modulo.monitor_hub.active_connections), 50)

    async def test_status_refleja_monitores_conectados_sin_superar_tope(self):
        estado_inicial = await app_modulo.get_status()
        self.assertEqual(estado_inicial["monitors_connected"], 0)

        conectados = await self._llenar_monitores()
        estado_lleno = await app_modulo.get_status()
        self.assertEqual(estado_lleno["monitors_connected"], 50)

        ws51 = _FakeWebSocket()
        await app_modulo.monitor_websocket_endpoint(ws51, token=self.TOKEN)
        estado_tras_rechazo = await app_modulo.get_status()
        self.assertEqual(estado_tras_rechazo["monitors_connected"], 50)

        app_modulo.monitor_hub.disconnect(conectados[0])
        estado_tras_baja = await app_modulo.get_status()
        self.assertEqual(estado_tras_baja["monitors_connected"], 49)


if __name__ == "__main__":
    unittest.main(verbosity=2)
