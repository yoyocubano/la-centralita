"""Pruebas de la paginación de /api/calls y /api/leads (sin claves, sin
red, sin dependencias extra: solo librería estándar de Python).

Cubre el endurecimiento de server/app.py:

  - /api/calls y /api/leads ya no devuelven el historial COMPLETO en
    cada GET: con el tope de 500 registros y eventos de hasta 64 KiB,
    una sola respuesta podía rozar los 32 MB. Ahora devuelven una
    página (por defecto las 50 más recientes) con el total, y
    limit/offset validados (400 si no valen).
  - La página va de la más reciente a la más antigua: el monitor quiere
    lo último, no lo primero. 'count' es el tamaño de la página y
    'total' el tamaño del historial.
  - La paginación no muta el historial y no cambia la autenticación
    (sin token sigue siendo 401).

El servidor depende de fastapi, livekit, aiohttp y openai, que NO están
en una máquina limpia: aquí se sustituyen por stubs mínimos antes de
importar server.app (los decoradores devuelven la función tal cual, así
los endpoints se pueden llamar directamente). Igual que
tests/test_servidor_limites.py, python-dotenv se sustituye por un no-op
si no está instalado.

OJO al correr varias suites en un mismo proceso: server.app queda
importado con los stubs del primer módulo que lo importó, así que la
clase HTTPException que levanta el servidor es app_modulo.HTTPException,
no la definida en este archivo: los assertRaises usan esa.

Cómo correr (desde la raíz del repo, sin instalar nada)::

    python3 -m unittest tests.test_paginacion_historial -v

Rama: feature/paginacion-historial (orden de mejora continua, ciclo
17/11/2026; continúa las suites sin claves del 11/11 al 16/11).
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
from agent.config import Config  # noqa: E402

# La excepción que levanta el servidor es la del stub con el que se
# importó server.app (ver docstring): usar siempre esta referencia.
HTTPException = app_modulo.HTTPException


# ---------------------------------------------------------------------------
# Paginación del historial
# ---------------------------------------------------------------------------

class TestPaginacionHistorial(unittest.IsolatedAsyncioTestCase):
    """Las páginas salen de la más reciente a la más antigua, con tope."""

    TOKEN = "token-de-prueba-solo-test"
    TOTAL_SEMBRADO = 120

    def setUp(self):
        self._token_original = Config.AUTH_TOKEN
        Config.AUTH_TOKEN = self.TOKEN
        app_modulo.CALLS_DATABASE.clear()
        app_modulo.LEADS_DATABASE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        Config.AUTH_TOKEN = self._token_original
        app_modulo.CALLS_DATABASE.clear()
        app_modulo.LEADS_DATABASE.clear()

    def _evento_llamada(self, numero):
        return {
            "type": "call_ended",
            "call_id": f"call-{numero:04d}",
            "room": "centralita-test",
            "duration": "01:00",
            "lead": {"nombre": f"Cliente {numero}", "telefono": "+352 600 000 000"},
        }

    async def _sembrar(self, total=TOTAL_SEMBRADO):
        for numero in range(total):
            await app_modulo.receive_call_event(
                self._evento_llamada(numero),
                authorization=f"Bearer {self.TOKEN}",
            )

    async def test_pagina_por_defecto_50_mas_recientes(self):
        await self._sembrar()
        respuesta = await app_modulo.get_calls(authorization=f"Bearer {self.TOKEN}")
        self.assertEqual(respuesta["count"], 50)
        self.assertEqual(respuesta["total"], self.TOTAL_SEMBRADO)
        self.assertEqual(respuesta["limit"], 50)
        self.assertEqual(respuesta["offset"], 0)
        # La primera es la más reciente (call-0119) y la última de la
        # página es la call-0070: 50 registros hacia atrás.
        self.assertEqual(respuesta["calls"][0]["call_id"], "call-0119")
        self.assertEqual(respuesta["calls"][-1]["call_id"], "call-0070")

    async def test_limit_y_offset_paginan_desde_la_mas_reciente(self):
        await self._sembrar()
        respuesta = await app_modulo.get_calls(
            authorization=f"Bearer {self.TOKEN}", limit=10, offset=10
        )
        self.assertEqual(respuesta["count"], 10)
        self.assertEqual(respuesta["total"], self.TOTAL_SEMBRADO)
        self.assertEqual(respuesta["limit"], 10)
        self.assertEqual(respuesta["offset"], 10)
        # Segunda página de 10: de la call-0109 a la call-0100.
        self.assertEqual(respuesta["calls"][0]["call_id"], "call-0109")
        self.assertEqual(respuesta["calls"][-1]["call_id"], "call-0100")

    async def test_limit_maximo_100_permitido(self):
        await self._sembrar()
        respuesta = await app_modulo.get_calls(
            authorization=f"Bearer {self.TOKEN}", limit=100
        )
        self.assertEqual(respuesta["count"], 100)
        self.assertEqual(respuesta["calls"][0]["call_id"], "call-0119")
        self.assertEqual(respuesta["calls"][-1]["call_id"], "call-0020")

    async def test_limit_fuera_de_rango_es_400(self):
        await self._sembrar()
        for limite in (0, -1, app_modulo.MAX_HISTORY_LIMIT + 1, 500):
            with self.subTest(limit=limite):
                with self.assertRaises(HTTPException) as ctx:
                    await app_modulo.get_calls(
                        authorization=f"Bearer {self.TOKEN}", limit=limite
                    )
                self.assertEqual(ctx.exception.status_code, 400)

    async def test_offset_negativo_es_400(self):
        await self._sembrar()
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.get_calls(
                authorization=f"Bearer {self.TOKEN}", offset=-1
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_limit_o_offset_no_numericos_son_400(self):
        await self._sembrar()
        for limite, desplazamiento in (("diez", 0), (10, "cinco"), (None, 0), (10, None)):
            with self.subTest(limit=limite, offset=desplazamiento):
                with self.assertRaises(HTTPException) as ctx:
                    await app_modulo.get_calls(
                        authorization=f"Bearer {self.TOKEN}",
                        limit=limite, offset=desplazamiento,
                    )
                self.assertEqual(ctx.exception.status_code, 400)

    async def test_offset_mas_alla_del_total_pagina_vacia(self):
        await self._sembrar()
        respuesta = await app_modulo.get_calls(
            authorization=f"Bearer {self.TOKEN}", limit=10, offset=200
        )
        self.assertEqual(respuesta["calls"], [])
        self.assertEqual(respuesta["count"], 0)
        self.assertEqual(respuesta["total"], self.TOTAL_SEMBRADO)

    async def test_leads_pagina_igual_que_calls(self):
        await self._sembrar()
        respuesta = await app_modulo.get_leads(authorization=f"Bearer {self.TOKEN}")
        self.assertEqual(respuesta["count"], 50)
        self.assertEqual(respuesta["total"], self.TOTAL_SEMBRADO)
        self.assertEqual(respuesta["leads"][0]["nombre"], "Cliente 119")
        self.assertEqual(respuesta["leads"][-1]["nombre"], "Cliente 70")
        segunda = await app_modulo.get_leads(
            authorization=f"Bearer {self.TOKEN}", limit=5, offset=115
        )
        self.assertEqual(segunda["count"], 5)
        self.assertEqual(segunda["leads"][0]["nombre"], "Cliente 4")
        self.assertEqual(segunda["leads"][-1]["nombre"], "Cliente 0")

    async def test_historial_vacio_pagina_vacia(self):
        respuesta = await app_modulo.get_calls(authorization=f"Bearer {self.TOKEN}")
        self.assertEqual(respuesta["calls"], [])
        self.assertEqual(respuesta["count"], 0)
        self.assertEqual(respuesta["total"], 0)
        respuesta_leads = await app_modulo.get_leads(authorization=f"Bearer {self.TOKEN}")
        self.assertEqual(respuesta_leads["leads"], [])
        self.assertEqual(respuesta_leads["total"], 0)

    async def test_sin_token_sigue_siendo_401(self):
        await self._sembrar()
        for endpoint in (app_modulo.get_calls, app_modulo.get_leads):
            with self.subTest(endpoint=endpoint.__name__):
                with self.assertRaises(HTTPException) as ctx:
                    await endpoint(authorization=None)
                self.assertEqual(ctx.exception.status_code, 401)

    async def test_la_paginacion_no_muta_el_historial(self):
        await self._sembrar()
        await app_modulo.get_calls(authorization=f"Bearer {self.TOKEN}", limit=10, offset=5)
        await app_modulo.get_leads(authorization=f"Bearer {self.TOKEN}", limit=100)
        self.assertEqual(len(app_modulo.CALLS_DATABASE), self.TOTAL_SEMBRADO)
        self.assertEqual(app_modulo.CALLS_DATABASE[0]["call_id"], "call-0000")
        self.assertEqual(app_modulo.CALLS_DATABASE[-1]["call_id"], "call-0119")
        self.assertEqual(len(app_modulo.LEADS_DATABASE), self.TOTAL_SEMBRADO)

    def test_constantes_de_paginacion(self):
        self.assertEqual(app_modulo.DEFAULT_HISTORY_LIMIT, 50)
        self.assertEqual(app_modulo.MAX_HISTORY_LIMIT, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
