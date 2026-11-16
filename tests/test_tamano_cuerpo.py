"""Pruebas del límite de tamaño del cuerpo HTTP (sin claves, sin red,
sin dependencias extra: solo librería estándar de Python).

Cubre el endurecimiento de server/app.py:

  - Un POST/PUT/PATCH a /api/* con Content-Length mayor de 64 KiB
    recibe 413 sin que la app interior se invoque siquiera (la
    validación del 14/11 medía el evento DESPUÉS de parsear el JSON;
    este middleware lo frena ANTES).
  - Sin Content-Length (chunked), el conteo real de bytes manda: un
    cuerpo que pasa del tope en mitad del stream también es 413.
  - Un cuerpo dentro del tope llega intacto a la app interior
    (replay del búfer), también cuando viene en varios trozos.
  - GET y rutas fuera de /api/* no se limitan; los scopes que no son
    HTTP (WebSocket) pasan sin tocarse.

El middleware es ASGI puro (sin Starlette), así que se prueba
directamente: una app falsa que emite los mensajes ASGI y un send
falso que los captura. Igual que tests/test_cabeceras_seguridad.py,
fastapi/livekit/aiohttp/openai se sustituyen por stubs mínimos antes
de importar server.app (en una máquina limpia no están instalados).

Cómo correr (desde la raíz del repo, sin instalar nada)::

    python3 -m unittest tests.test_tamano_cuerpo -v

Rama: feature/limite-cuerpo-peticion (orden de mejora continua, ciclo
16/11/2026; continúa las suites sin claves del 11/11, 12/11, 13/11,
14/11 y 15/11).
"""

import json
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Stubs mínimos de las dependencias del servidor (solo para poder importar
# server.app en una máquina limpia; no se ejecuta lógica real de ninguna).
# ---------------------------------------------------------------------------

def _install_stub_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _HTTPException(Exception):
    """Sustituto de fastapi.HTTPException: misma forma (status_code/detail)."""

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
_livekit = _install_stub_module("livekit", api=_livekit_api)
_install_stub_module(
    "aiohttp", ClientSession=_ClientSession, ClientTimeout=_ClientTimeout
)
_install_stub_module("openai", AsyncOpenAI=_AsyncOpenAI)

import server.app as app_modulo  # noqa: E402


# ---------------------------------------------------------------------------
# Utilidades ASGI falsas: una app que registra si la invocaron y qué
# cuerpo recibió, un receive que entrega trozos y un send que captura.
# ---------------------------------------------------------------------------

class _CapturaSend:
    """Send falso que guarda cada mensaje que recibe."""

    def __init__(self):
        self.mensajes = []

    async def __call__(self, message):
        self.mensajes.append(message)


class _AppFalsa:
    """App ASGI falsa: cuenta invocaciones y guarda el cuerpo que lee."""

    def __init__(self):
        self.invocaciones = 0
        self.cuerpo_recibido = b""

    async def __call__(self, scope, receive, send):
        self.invocaciones += 1
        # Lee el cuerpo como lo haría FastAPI: hasta more_body False.
        while True:
            mensaje = await receive()
            if mensaje.get("type") != "http.request":
                continue
            self.cuerpo_recibido += mensaje.get("body", b"")
            if not mensaje.get("more_body", False):
                break
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": b"{}"})


def _receive_trozos(trozos):
    """Receive falso que entrega los trozos en orden (chunked)."""
    indice = 0

    async def receive():
        nonlocal indice
        if indice < len(trozos):
            trozo = trozos[indice]
            indice += 1
            return {
                "type": "http.request",
                "body": trozo,
                "more_body": indice < len(trozos),
            }
        return {"type": "http.request", "body": b"", "more_body": False}

    return receive


def _scope_http(metodo, ruta, cabeceras=None):
    return {
        "type": "http",
        "method": metodo,
        "path": ruta,
        "headers": list(cabeceras or []),
    }


# ---------------------------------------------------------------------------
# Límite de tamaño del cuerpo HTTP
# ---------------------------------------------------------------------------

class TestLimiteCuerpoPeticion(unittest.IsolatedAsyncioTestCase):
    """Un cuerpo mayor de 64 KiB a /api/* es 413 antes de parsearse."""

    async def _atravesar(self, metodo, ruta, cabeceras=None, trozos=(b"",)):
        app_falsa = _AppFalsa()
        middleware = app_modulo.RequestBodySizeLimitMiddleware(app_falsa)
        captura = _CapturaSend()
        await middleware(
            _scope_http(metodo, ruta, cabeceras),
            _receive_trozos(trozos),
            captura,
        )
        return app_falsa, captura.mensajes

    async def test_content_length_mayor_es_413_sin_invocar_la_app(self):
        app_falsa, mensajes = await self._atravesar(
            "POST", "/api/call-event",
            [(b"content-length", b"65537")],
            (b"x" * 65537,),
        )
        # La app interior no se invocó: el cuerpo nunca llegó a parsearse.
        self.assertEqual(app_falsa.invocaciones, 0)
        self.assertEqual(mensajes[0]["type"], "http.response.start")
        self.assertEqual(mensajes[0]["status"], 413)
        cabeceras = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in mensajes[0].get("headers", [])
        }
        self.assertEqual(cabeceras["content-type"], "application/json")
        detalle = json.loads(mensajes[1]["body"].decode("utf-8"))["detail"]
        self.assertIn("65536", detalle)

    async def test_content_length_exactamente_en_el_tope_pasa(self):
        cuerpo = b"x" * 65536
        app_falsa, mensajes = await self._atravesar(
            "POST", "/api/call-event",
            [(b"content-length", b"65536")],
            (cuerpo,),
        )
        self.assertEqual(app_falsa.invocaciones, 1)
        self.assertEqual(mensajes[0]["status"], 200)
        self.assertEqual(app_falsa.cuerpo_recibido, cuerpo)

    async def test_chunked_por_encima_del_tope_es_413(self):
        # Sin Content-Length: el conteo real manda, trozo a trozo.
        app_falsa, mensajes = await self._atravesar(
            "POST", "/api/call-event",
            None,
            (b"a" * 40000, b"b" * 25537),
        )
        self.assertEqual(app_falsa.invocaciones, 0)
        self.assertEqual(mensajes[0]["status"], 413)
        self.assertIn("65536", mensajes[1]["body"].decode("utf-8"))

    async def test_chunked_dentro_del_tope_llega_intacto(self):
        app_falsa, mensajes = await self._atravesar(
            "POST", "/api/call-event",
            None,
            (b'{"type":', b'"call_ended"}'),
        )
        self.assertEqual(app_falsa.invocaciones, 1)
        self.assertEqual(mensajes[0]["status"], 200)
        # La app interior recibe el cuerpo reensamblado, íntegro.
        self.assertEqual(app_falsa.cuerpo_recibido, b'{"type":"call_ended"}')

    async def test_get_con_content_length_gigante_no_se_limita(self):
        app_falsa, mensajes = await self._atravesar(
            "GET", "/api/status",
            [(b"content-length", b"1000000")],
            (b"",),
        )
        self.assertEqual(app_falsa.invocaciones, 1)
        self.assertEqual(mensajes[0]["status"], 200)

    async def test_post_fuera_de_api_no_se_limita(self):
        app_falsa, mensajes = await self._atravesar(
            "POST", "/panel/index.html",
            [(b"content-length", b"1000000")],
            (b"",),
        )
        self.assertEqual(app_falsa.invocaciones, 1)
        self.assertEqual(mensajes[0]["status"], 200)

    async def test_content_length_no_numerico_no_rompe(self):
        # Cabecera rota: no fiarse de ella; manda el conteo real.
        app_falsa, mensajes = await self._atravesar(
            "POST", "/api/call-event",
            [(b"content-length", b"abc")],
            (b'{"type":"ping"}',),
        )
        self.assertEqual(app_falsa.invocaciones, 1)
        self.assertEqual(mensajes[0]["status"], 200)
        self.assertEqual(app_falsa.cuerpo_recibido, b'{"type":"ping"}')

    async def test_websocket_pasa_sin_tocarse(self):
        app_falsa = _AppFalsa()
        middleware = app_modulo.RequestBodySizeLimitMiddleware(app_falsa)
        captura = _CapturaSend()
        await middleware(
            {"type": "websocket", "path": "/ws/monitor"},
            _receive_trozos((b"",)),
            captura,
        )
        self.assertEqual(app_falsa.invocaciones, 1)
        self.assertEqual(captura.mensajes[0]["status"], 200)


class TestConstantesTamanoCuerpo(unittest.TestCase):
    """Guardas: el tope del cuerpo coincide con el tope del evento."""

    def test_tope_64_kib_igual_que_evento(self):
        self.assertEqual(app_modulo.MAX_REQUEST_BODY_BYTES, 65536)
        self.assertEqual(
            app_modulo.MAX_REQUEST_BODY_BYTES,
            app_modulo.MAX_EVENT_JSON_BYTES,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
