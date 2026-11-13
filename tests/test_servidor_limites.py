"""Pruebas de los límites de memoria del servidor (sin claves, sin red,
sin dependencias extra: solo librería estándar de Python).

Cubre el endurecimiento de server/app.py:

  - CALLS_DATABASE y LEADS_DATABASE tienen tope: solo se conservan los
    N registros más recientes; un servidor de larga duración no crece
    sin límite con cada llamada y cada lead.
  - El registro de rate-limit del token público (/api/token/public) poda
    las IPs sin actividad en la última hora y expulsa la más antigua
    cuando llega a su tope: ni un escaneo de muchas IPs lo hace crecer
    sin límite.

El servidor depende de fastapi, livekit, aiohttp y openai, que NO están
en una máquina limpia: aquí se sustituyen por stubs mínimos antes de
importar server.app (los decoradores devuelven la función tal cual, así
los endpoints se pueden llamar directamente). Igual que
tests/test_sin_claves.py, python-dotenv se sustituye por un no-op si no
está instalado.

Cómo correr (desde la raíz del repo, sin instalar nada)::

    python3 -m unittest tests.test_servidor_limites -v

Rama: feature/limites-memoria-servidor (orden de mejora continua, ciclo
13/11/2026; continúa las suites sin claves del 11/11 y 12/11).
"""

import sys
import time
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


# ---------------------------------------------------------------------------
# Tope de los historiales en memoria
# ---------------------------------------------------------------------------

class TestHistorialesConTope(unittest.IsolatedAsyncioTestCase):
    """CALLS_DATABASE y LEADS_DATABASE no pasan de su tope."""

    TOKEN = "token-de-prueba-solo-test"

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

    async def test_las_listas_no_pasan_del_tope(self):
        total = app_modulo.MAX_STORED_CALLS + 5
        for numero in range(total):
            await app_modulo.receive_call_event(
                self._evento_llamada(numero),
                authorization=f"Bearer {self.TOKEN}",
            )
        self.assertEqual(len(app_modulo.CALLS_DATABASE), app_modulo.MAX_STORED_CALLS)
        self.assertEqual(len(app_modulo.LEADS_DATABASE), app_modulo.MAX_STORED_LEADS)

    async def test_solo_quedan_los_registros_mas_recientes(self):
        total = app_modulo.MAX_STORED_CALLS + 5
        for numero in range(total):
            await app_modulo.receive_call_event(
                self._evento_llamada(numero),
                authorization=f"Bearer {self.TOKEN}",
            )
        primero = app_modulo.CALLS_DATABASE[0]
        ultimo = app_modulo.CALLS_DATABASE[-1]
        # Los 5 primeros se expulsaron por la cola; queda del 5 al 504.
        self.assertEqual(primero["call_id"], "call-0005")
        self.assertEqual(ultimo["call_id"], f"call-{total - 1:04d}")
        self.assertEqual(app_modulo.LEADS_DATABASE[0]["nombre"], "Cliente 5")

    async def test_evento_sin_call_ended_no_crece_los_historiales(self):
        await app_modulo.receive_call_event(
            {"type": "call_started", "call_id": "call-0000", "room": "centralita-test"},
            authorization=f"Bearer {self.TOKEN}",
        )
        self.assertEqual(app_modulo.CALLS_DATABASE, [])
        self.assertEqual(app_modulo.LEADS_DATABASE, [])

    async def test_sin_token_no_guarda_nada(self):
        with self.assertRaises(_HTTPException) as ctx:
            await app_modulo.receive_call_event(
                self._evento_llamada(0), authorization=None
            )
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(app_modulo.CALLS_DATABASE, [])


# ---------------------------------------------------------------------------
# Rate-limit del token público: poda y tope del registro por IP
# ---------------------------------------------------------------------------

class TestRateLimitConTope(unittest.TestCase):
    def setUp(self):
        app_modulo._public_token_log.clear()
        self.addCleanup(app_modulo._public_token_log.clear)

    def test_treinta_peticiones_pasan_la_31_es_429(self):
        ip = "203.0.113.10"
        for _ in range(app_modulo.PUBLIC_TOKEN_MAX_PER_HOUR):
            app_modulo._check_public_token_rate_limit(ip)
        with self.assertRaises(_HTTPException) as ctx:
            app_modulo._check_public_token_rate_limit(ip)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_entradas_de_hace_mas_de_una_hora_no_cuentan(self):
        ip = "203.0.113.11"
        ahora = time.monotonic()
        app_modulo._public_token_log[ip] = [ahora - 4000] * 30
        # Las 30 viejas se podan: la siguiente pasa y el registro queda en 1.
        app_modulo._check_public_token_rate_limit(ip)
        self.assertEqual(len(app_modulo._public_token_log[ip]), 1)

    def test_ips_inactivas_se_podan_del_registro(self):
        ahora = time.monotonic()
        app_modulo._public_token_log["198.51.100.1"] = [ahora - 4000]
        app_modulo._public_token_log["198.51.100.2"] = [ahora]
        app_modulo._check_public_token_rate_limit("203.0.113.12")
        self.assertNotIn("198.51.100.1", app_modulo._public_token_log)
        self.assertIn("198.51.100.2", app_modulo._public_token_log)

    def test_el_registro_no_pasa_del_tope_de_ips(self):
        ahora = time.monotonic()
        for numero in range(app_modulo.PUBLIC_TOKEN_LOG_MAX_IPS):
            app_modulo._public_token_log[f"192.0.2.{numero % 250 + 1}.{numero}"] = [ahora]
        # Una IP nueva con el registro lleno: entra y expulsa a la más antigua.
        app_modulo._check_public_token_rate_limit("203.0.113.99")
        self.assertEqual(len(app_modulo._public_token_log), app_modulo.PUBLIC_TOKEN_LOG_MAX_IPS)
        self.assertIn("203.0.113.99", app_modulo._public_token_log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
