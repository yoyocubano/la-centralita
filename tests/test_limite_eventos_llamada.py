"""Pruebas del rate-limit de /api/call-event (sin claves, sin red, sin
dependencias extra: solo librería estándar de Python).

Cubre el endurecimiento de server/app.py:

  - /api/call-event era el único endpoint de escritura autenticado sin
    tope por IP: un worker en bucle, un reintento desbocado o un token
    filtrado podía inundar el hub (cada evento se difunde a todos los
    monitores y se guarda en el historial). Ahora admite 60 eventos
    por minuto y por IP; al pasarlo, 429.
  - El tope va DESPUÉS de la autenticación y de la validación: sin
    token sigue siendo 401 y una petición inválida (400) no consume
    cuota, igual que en el token público (principio del 14/11).
  - El chequeo del token público se refactorizó sobre el mismo
    ayudante genérico (_check_rate_limit) sin cambiar su
    comportamiento: las suites viejas lo siguen cubriendo y aquí hay
    una regresión ligera. El registro de eventos tiene su propio tope
    de IPs (1000) con poda de IPs inactivas.
  - La IP sale de request.client.host; sin request (llamada directa,
    como hacen las suites viejas) no se aplica el tope: sin IP no hay
    a quién atribuir el consumo y todas las llamadas compartirían un
    mismo cubo. En HTTP real FastAPI siempre inyecta el request, así
    que el tope cubre todo el tráfico de verdad.

El servidor depende de fastapi, livekit, aiohttp y openai, que NO están
en una máquina limpia: aquí se sustituyen por stubs mínimos antes de
importar server.app (los decoradores devuelven la función tal cual, así
los endpoints se pueden llamar directamente). Igual que
tests/test_paginacion_historial.py, python-dotenv se sustituye por un
no-op si no está instalado.

OJO al correr varias suites en un mismo proceso: server.app queda
importado con los stubs del primer módulo que lo importó, así que la
clase HTTPException que levanta el servidor es app_modulo.HTTPException,
no la definida en este archivo: los assertRaises usan esa.

Cómo correr (desde la raíz del repo, sin instalar nada)::

    python3 -m unittest tests.test_limite_eventos_llamada -v

Rama: feature/limite-eventos-llamada (orden de mejora continua, ciclo
18/11/2026; continúa las suites sin claves del 11/11 al 17/11).
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

# La excepción que levanta el servidor es la del stub con el que se
# importó server.app (ver docstring): usar siempre esta referencia.
HTTPException = app_modulo.HTTPException


class _FakeClient:
    """Sustituto mínimo de request.client: solo lleva el host."""

    def __init__(self, host):
        self.host = host


class _FakeRequest:
    """Sustituto mínimo de fastapi.Request: solo lleva client.host."""

    def __init__(self, host):
        self.client = _FakeClient(host)


def _request(ip):
    return _FakeRequest(ip)


# ---------------------------------------------------------------------------
# Rate-limit de /api/call-event
# ---------------------------------------------------------------------------

class TestLimiteEventosLlamada(unittest.IsolatedAsyncioTestCase):
    """60 eventos por minuto y por IP; al 61, 429."""

    TOKEN = "token-de-prueba-solo-test"
    IP = "203.0.113.7"

    def setUp(self):
        self._token_original = Config.AUTH_TOKEN
        Config.AUTH_TOKEN = self.TOKEN
        app_modulo.CALLS_DATABASE.clear()
        app_modulo.LEADS_DATABASE.clear()
        app_modulo._call_event_log.clear()
        app_modulo._public_token_log.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        Config.AUTH_TOKEN = self._token_original
        app_modulo.CALLS_DATABASE.clear()
        app_modulo.LEADS_DATABASE.clear()
        app_modulo._call_event_log.clear()
        app_modulo._public_token_log.clear()

    def _evento(self, numero, tipo="call_ended"):
        return {
            "type": tipo,
            "call_id": f"call-{numero:04d}",
            "room": "centralita-test",
            "duration": "01:00",
            "lead": {"nombre": f"Cliente {numero}", "telefono": "+352 600 000 000"},
        }

    async def _enviar(self, numero, ip=IP, tipo="call_ended"):
        return await app_modulo.receive_call_event(
            self._evento(numero, tipo),
            request=_request(ip),
            authorization=f"Bearer {self.TOKEN}",
        )

    async def _llenar_tope(self, ip=IP):
        for numero in range(app_modulo.CALL_EVENT_MAX_PER_MINUTE):
            await self._enviar(numero, ip=ip)

    async def test_sesenta_eventos_pasan_el_61_es_429(self):
        await self._llenar_tope()
        self.assertEqual(len(app_modulo.CALLS_DATABASE), 60)
        with self.assertRaises(HTTPException) as ctx:
            await self._enviar(60)
        self.assertEqual(ctx.exception.status_code, 429)

    async def test_otra_ip_no_afectada_tras_agotar_una(self):
        await self._llenar_tope()
        # La IP agotada sigue en 429...
        with self.assertRaises(HTTPException) as ctx:
            await self._enviar(60)
        self.assertEqual(ctx.exception.status_code, 429)
        # ...pero otra IP emite sin problema.
        respuesta = await self._enviar(100, ip="203.0.113.8")
        self.assertEqual(respuesta["status"], "broadcasted")

    async def test_marcas_viejas_no_cuentan(self):
        ahora = time.monotonic()
        app_modulo._call_event_log[self.IP] = [ahora - 61] * 60
        # Las 60 viejas (fuera de la ventana de 60 s) no cuentan: la
        # siguiente pasa y el registro queda en 1.
        respuesta = await self._enviar(0)
        self.assertEqual(respuesta["status"], "broadcasted")
        self.assertEqual(len(app_modulo._call_event_log[self.IP]), 1)

    async def test_evento_invalido_no_consume_cuota(self):
        # 35 con clave no permitida + 35 con texto demasiado largo:
        # todas 400, ninguna consume cuota.
        for numero in range(35):
            with self.subTest(caso="clave-extra", numero=numero):
                with self.assertRaises(HTTPException) as ctx:
                    await app_modulo.receive_call_event(
                        {"type": "call_ended", "call_id": f"bad-{numero}", "clave_no_permitida": 1},
                        request=_request(self.IP),
                        authorization=f"Bearer {self.TOKEN}",
                    )
                self.assertEqual(ctx.exception.status_code, 400)
            with self.subTest(caso="texto-largo", numero=numero):
                evento = self._evento(numero)
                evento["lead"] = {"nombre": "x" * 9000}
                with self.assertRaises(HTTPException) as ctx:
                    await app_modulo.receive_call_event(
                        evento,
                        request=_request(self.IP),
                        authorization=f"Bearer {self.TOKEN}",
                    )
                self.assertEqual(ctx.exception.status_code, 400)
        # Tras 70 inválidas, una válida sigue pasando: el registro
        # de la IP está vacío.
        self.assertNotIn(self.IP, app_modulo._call_event_log)
        respuesta = await self._enviar(0)
        self.assertEqual(respuesta["status"], "broadcasted")

    async def test_sin_token_es_401_y_no_consume_cuota(self):
        for numero in range(70):
            with self.assertRaises(HTTPException) as ctx:
                await app_modulo.receive_call_event(
                    self._evento(numero),
                    request=_request(self.IP),
                    authorization=None,
                )
            self.assertEqual(ctx.exception.status_code, 401)
        # 70 intentos sin token no dejaron marca en el registro.
        self.assertNotIn(self.IP, app_modulo._call_event_log)
        respuesta = await self._enviar(0)
        self.assertEqual(respuesta["status"], "broadcasted")

    async def test_evento_rechazado_por_429_no_se_guarda(self):
        await self._llenar_tope()
        with self.assertRaises(HTTPException) as ctx:
            await self._enviar(60)
        self.assertEqual(ctx.exception.status_code, 429)
        # El 61 no llegó al historial: siguen 60.
        self.assertEqual(len(app_modulo.CALLS_DATABASE), 60)
        self.assertEqual(app_modulo.CALLS_DATABASE[-1]["call_id"], "call-0059")

    async def test_eventos_que_no_son_fin_de_llamada_tambien_cuentan(self):
        # Los turnos e inicios de llamada también pasan por el tope:
        # 60 "call_started" (que no se guardan) y el 61 es 429.
        for numero in range(app_modulo.CALL_EVENT_MAX_PER_MINUTE):
            await self._enviar(numero, tipo="call_started")
        self.assertEqual(app_modulo.CALLS_DATABASE, [])
        with self.assertRaises(HTTPException) as ctx:
            await self._enviar(60, tipo="call_started")
        self.assertEqual(ctx.exception.status_code, 429)

    def test_ips_inactivas_se_podan_del_registro(self):
        ahora = time.monotonic()
        app_modulo._call_event_log["198.51.100.1"] = [ahora - 61]
        app_modulo._call_event_log["198.51.100.2"] = [ahora]
        app_modulo._check_call_event_rate_limit("203.0.113.12")
        self.assertNotIn("198.51.100.1", app_modulo._call_event_log)
        self.assertIn("198.51.100.2", app_modulo._call_event_log)

    def test_el_registro_no_pasa_del_tope_de_ips(self):
        ahora = time.monotonic()
        for numero in range(app_modulo.CALL_EVENT_LOG_MAX_IPS):
            app_modulo._call_event_log[f"192.0.2.{numero % 250 + 1}.{numero}"] = [ahora]
        # Una IP nueva con el registro lleno: entra y expulsa a la más antigua.
        app_modulo._check_call_event_rate_limit("203.0.113.99")
        self.assertEqual(len(app_modulo._call_event_log), app_modulo.CALL_EVENT_LOG_MAX_IPS)
        self.assertIn("203.0.113.99", app_modulo._call_event_log)

    def test_token_publico_sigue_con_su_tope(self):
        # Regresión del refactor a _check_rate_limit: el token público
        # mantiene su tope de 30/hora y la poda de marcas viejas.
        ip = "203.0.113.10"
        for _ in range(app_modulo.PUBLIC_TOKEN_MAX_PER_HOUR):
            app_modulo._check_public_token_rate_limit(ip)
        with self.assertRaises(HTTPException) as ctx:
            app_modulo._check_public_token_rate_limit(ip)
        self.assertEqual(ctx.exception.status_code, 429)
        ahora = time.monotonic()
        app_modulo._public_token_log["203.0.113.11"] = [ahora - 4000] * 30
        app_modulo._check_public_token_rate_limit("203.0.113.11")
        self.assertEqual(len(app_modulo._public_token_log["203.0.113.11"]), 1)

    async def test_sin_request_no_hay_tope(self):
        # Compatibilidad con las suites viejas, que llaman al endpoint
        # sin request: sin IP no se aplica el tope y 70 eventos seguidos
        # pasan todos (en HTTP real FastAPI siempre inyecta el request).
        for numero in range(70):
            respuesta = await app_modulo.receive_call_event(
                self._evento(numero),
                authorization=f"Bearer {self.TOKEN}",
            )
            self.assertEqual(respuesta["status"], "broadcasted")
        self.assertEqual(app_modulo._call_event_log, {})
        self.assertEqual(len(app_modulo.CALLS_DATABASE), 70)

    def test_constantes_del_limite(self):
        self.assertEqual(app_modulo.CALL_EVENT_MAX_PER_MINUTE, 60)
        self.assertEqual(app_modulo.CALL_EVENT_WINDOW_SECONDS, 60)
        self.assertEqual(app_modulo.CALL_EVENT_LOG_MAX_IPS, 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
