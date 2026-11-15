"""Pruebas de las cabeceras de seguridad del servidor (sin claves, sin
red, sin dependencias extra: solo librería estándar de Python).

Cubre el endurecimiento de server/app.py:

  - Cada respuesta HTTP lleva X-Content-Type-Options: nosniff,
    X-Frame-Options: SAMEORIGIN y Referrer-Policy: no-referrer.
  - Las respuestas de /api/* y /ws/* llevan además Cache-Control:
    no-store (datos vivos, sin caché intermedia); los archivos
    estáticos del panel (/panel/*) conservan su caché normal.
  - El middleware no pisa una cabecera que la respuesta ya traiga.
  - Las conexiones WebSocket pasan sin tocarse.

El middleware es ASGI puro (sin Starlette), así que se prueba
directamente: una app falsa que emite los mensajes ASGI y un send
falso que los captura. Igual que tests/test_validacion_entrada.py,
fastapi/livekit/aiohttp/openai se sustituyen por stubs mínimos antes
de importar server.app (en una máquina limpia no están instalados).

Cómo correr (desde la raíz del repo, sin instalar nada)::

    python3 -m unittest tests.test_cabeceras_seguridad -v

Rama: feature/cabeceras-seguridad-servidor (orden de mejora continua,
ciclo 15/11/2026; continúa las suites sin claves del 11/11, 12/11,
13/11 y 14/11).
"""

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
# Utilidades ASGI falsas: una app que emite una respuesta y un send que
# captura los mensajes que el middleware deja pasar.
# ---------------------------------------------------------------------------

async def _receive_vacio():
    return {"type": "http.request", "body": b"", "more_body": False}


def _app_respuesta_falsa(cabeceras_iniciales=None):
    """App ASGI falsa: emite http.response.start + un body, nada más."""

    async def app_falsa(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": list(cabeceras_iniciales or []),
        })
        await send({"type": "http.response.body", "body": b"{}"})

    return app_falsa


class _CapturaSend:
    """Send falso que guarda cada mensaje que recibe."""

    def __init__(self):
        self.mensajes = []

    async def __call__(self, message):
        self.mensajes.append(message)


def _cabeceras_dict(mensaje_inicio):
    return {
        k.decode("latin-1").lower(): v.decode("latin-1")
        for k, v in mensaje_inicio.get("headers", [])
    }


# ---------------------------------------------------------------------------
# Cabeceras de seguridad en respuestas HTTP
# ---------------------------------------------------------------------------

class TestCabecerasSeguridad(unittest.IsolatedAsyncioTestCase):
    """Toda respuesta HTTP sale con las cabeceras de endurecimiento."""

    async def _atravesar_middleware(self, ruta, cabeceras_iniciales=None,
                                    tipo_scope="http"):
        middleware = app_modulo.SecurityHeadersMiddleware(
            _app_respuesta_falsa(cabeceras_iniciales)
        )
        captura = _CapturaSend()
        await middleware(
            {"type": tipo_scope, "path": ruta}, _receive_vacio, captura
        )
        return captura.mensajes

    async def test_api_lleva_las_tres_cabeceras_mas_no_store(self):
        mensajes = await self._atravesar_middleware(
            "/api/status", [(b"content-type", b"application/json")]
        )
        inicio = mensajes[0]
        self.assertEqual(inicio["type"], "http.response.start")
        cabeceras = _cabeceras_dict(inicio)
        self.assertEqual(cabeceras["x-content-type-options"], "nosniff")
        self.assertEqual(cabeceras["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(cabeceras["referrer-policy"], "no-referrer")
        self.assertEqual(cabeceras["cache-control"], "no-store")
        # La cabecera original de la respuesta sigue intacta.
        self.assertEqual(cabeceras["content-type"], "application/json")
        # El body pasa sin tocarse.
        self.assertEqual(mensajes[1], {"type": "http.response.body", "body": b"{}"})

    async def test_ws_tambien_lleva_no_store(self):
        mensajes = await self._atravesar_middleware("/ws/monitor")
        cabeceras = _cabeceras_dict(mensajes[0])
        self.assertEqual(cabeceras["cache-control"], "no-store")
        self.assertEqual(cabeceras["x-content-type-options"], "nosniff")

    async def test_archivo_estatico_sin_no_store(self):
        mensajes = await self._atravesar_middleware(
            "/panel/index.html", [(b"content-type", b"text/html")]
        )
        cabeceras = _cabeceras_dict(mensajes[0])
        self.assertEqual(cabeceras["x-content-type-options"], "nosniff")
        self.assertEqual(cabeceras["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(cabeceras["referrer-policy"], "no-referrer")
        # El panel estático conserva su caché normal: sin no-store.
        self.assertNotIn("cache-control", cabeceras)

    async def test_no_pisa_una_cabecera_que_ya_existe(self):
        mensajes = await self._atravesar_middleware(
            "/api/status", [(b"x-frame-options", b"DENY")]
        )
        cabeceras = _cabeceras_dict(mensajes[0])
        # La respuesta ya traía su X-Frame-Options: la existente manda.
        self.assertEqual(cabeceras["x-frame-options"], "DENY")
        # Las demás cabeceras se añaden igual.
        self.assertEqual(cabeceras["x-content-type-options"], "nosniff")
        self.assertEqual(cabeceras["cache-control"], "no-store")

    async def test_respuesta_sin_cabeceras_no_rompe(self):
        mensajes = await self._atravesar_middleware("/api/status", [])
        cabeceras = _cabeceras_dict(mensajes[0])
        self.assertEqual(cabeceras["x-content-type-options"], "nosniff")
        self.assertEqual(cabeceras["cache-control"], "no-store")

    async def test_websocket_pasa_sin_tocarse(self):
        # Un scope que no es HTTP no pasa por el envoltorio de send:
        # los mensajes llegan exactamente como la app los emitió.
        middleware = app_modulo.SecurityHeadersMiddleware(
            _app_respuesta_falsa()
        )
        captura = _CapturaSend()
        await middleware(
            {"type": "websocket", "path": "/ws/monitor"}, _receive_vacio, captura
        )
        self.assertEqual(len(captura.mensajes), 2)
        self.assertEqual(
            captura.mensajes[0]["headers"], []
        )


class TestConstantesCabeceras(unittest.TestCase):
    """Guardas: las constantes que usa el middleware no se vacían por error."""

    def test_las_tres_cabeceras_definidas(self):
        self.assertEqual(
            set(app_modulo.SECURITY_HEADERS),
            {"x-content-type-options", "x-frame-options", "referrer-policy"},
        )
        self.assertEqual(app_modulo.SECURITY_HEADERS["x-frame-options"], "SAMEORIGIN")

    def test_no_store_solo_api_y_ws(self):
        self.assertIn("/api/", app_modulo.API_NO_STORE_PREFIXES)
        self.assertIn("/ws/", app_modulo.API_NO_STORE_PREFIXES)
        self.assertNotIn("/panel/", app_modulo.API_NO_STORE_PREFIXES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
