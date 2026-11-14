"""Pruebas de la validación de tamaño de entrada del servidor (sin claves,
sin red, sin dependencias extra: solo librería estándar de Python).

Cubre el endurecimiento de server/app.py:

  - /api/call-event rechaza eventos desproporcionados: más de 64 KiB
    serializados (413), campos de texto de más de 8 KiB (400) y listas
    de más de 200 elementos, como una transcripción con más de 200
    turnos (400). Sin este tope, un solo evento gigante agota la RAM
    aunque los historiales tengan tope de 500 registros (ciclo 13/11).
  - /api/token y /api/token/public rechazan nombres e identidades de
    más de 100 caracteres (400): un texto gigante infla el JWT.
  - En el token público, la validación del nombre va antes del
    rate-limit: una petición inválida no consume cuota de la IP.

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

    python3 -m unittest tests.test_validacion_entrada -v

Rama: feature/validacion-entrada-servidor (orden de mejora continua,
ciclo 14/11/2026; continúa las suites sin claves del 11/11, 12/11 y
13/11).
"""

import copy
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
from agent.config import Config  # noqa: E402

# La excepción que levanta el servidor es la del stub con el que se
# importó server.app (ver docstring): usar siempre esta referencia.
HTTPException = app_modulo.HTTPException


def _peticion_publica(ip):
    """Petición simulada para /api/token/public: solo importa client.host."""
    return types.SimpleNamespace(client=types.SimpleNamespace(host=ip))


# ---------------------------------------------------------------------------
# Validación del evento de llamada
# ---------------------------------------------------------------------------

class TestValidacionEvento(unittest.IsolatedAsyncioTestCase):
    """Un evento gigante no llega a guardarse ni a difundirse."""

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

    def _evento_base(self):
        return {
            "type": "call_ended",
            "call_id": "call-0001",
            "room": "centralita-test",
            "duration": "01:00",
        }

    async def test_evento_normal_se_guarda(self):
        evento = self._evento_base()
        evento["lead"] = {"nombre": "Cliente Demo", "telefono": "+352 600 000 000"}
        resultado = await app_modulo.receive_call_event(
            evento, authorization=f"Bearer {self.TOKEN}"
        )
        self.assertEqual(resultado["status"], "broadcasted")
        self.assertEqual(len(app_modulo.CALLS_DATABASE), 1)
        self.assertEqual(len(app_modulo.LEADS_DATABASE), 1)

    async def test_evento_mayor_de_64k_es_413_y_no_se_guarda(self):
        evento = self._evento_base()
        # 9 turnos de 8 KiB: ~74 KiB serializado, por encima del tope.
        evento["transcript"] = [
            {"role": "user", "text": "x" * 8192} for _ in range(9)
        ]
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.receive_call_event(
                evento, authorization=f"Bearer {self.TOKEN}"
            )
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(app_modulo.CALLS_DATABASE, [])
        self.assertEqual(app_modulo.LEADS_DATABASE, [])

    async def test_evento_justo_en_el_tope_pasa_y_un_byte_mas_no(self):
        # 8 turnos: los 7 primeros a 8 KiB y el último ajustado para que
        # el total serializado sea exactamente MAX_EVENT_JSON_BYTES.
        evento = self._evento_base()
        evento["transcript"] = [
            {"role": "user", "text": "x" * 8192} for _ in range(7)
        ] + [{"role": "user", "text": ""}]
        medido = len(json.dumps(evento, ensure_ascii=False).encode("utf-8"))
        resto = app_modulo.MAX_EVENT_JSON_BYTES - medido
        self.assertGreater(resto, 0)
        self.assertLessEqual(resto, app_modulo.MAX_STRING_FIELD_CHARS)
        evento["transcript"][-1]["text"] = "x" * resto
        self.assertEqual(
            len(json.dumps(evento, ensure_ascii=False).encode("utf-8")),
            app_modulo.MAX_EVENT_JSON_BYTES,
        )
        # Copia antes de enviar: el servidor añade server_timestamp al
        # evento que recibe, y esa clave no pasaría la whitelist en un
        # segundo envío del mismo dict.
        evento_un_byte_mas = copy.deepcopy(evento)
        evento_un_byte_mas["transcript"][-1]["text"] += "x"
        await app_modulo.receive_call_event(
            evento, authorization=f"Bearer {self.TOKEN}"
        )
        self.assertEqual(len(app_modulo.CALLS_DATABASE), 1)
        # Un byte más: mismo evento con un carácter extra en el último turno.
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.receive_call_event(
                evento_un_byte_mas, authorization=f"Bearer {self.TOKEN}"
            )
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(len(app_modulo.CALLS_DATABASE), 1)

    async def test_campo_de_texto_mayor_de_8k_es_400(self):
        evento = self._evento_base()
        evento["lead"] = {"nombre": "x" * 8193, "telefono": "+352 600 000 000"}
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.receive_call_event(
                evento, authorization=f"Bearer {self.TOKEN}"
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(app_modulo.CALLS_DATABASE, [])

    async def test_transcripcion_con_mas_de_200_turnos_es_400(self):
        evento = self._evento_base()
        evento["transcript"] = [
            {"role": "user", "text": f"turno {n}"} for n in range(201)
        ]
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.receive_call_event(
                evento, authorization=f"Bearer {self.TOKEN}"
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(app_modulo.CALLS_DATABASE, [])

    async def test_sin_auth_el_401_llega_antes_que_la_validacion(self):
        evento = self._evento_base()
        evento["transcript"] = [
            {"role": "user", "text": "x" * 8192} for _ in range(9)
        ]
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.receive_call_event(evento, authorization=None)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(app_modulo.CALLS_DATABASE, [])


# ---------------------------------------------------------------------------
# Validación del nombre / identidad en los tokens
# ---------------------------------------------------------------------------

class TestValidacionNombreToken(unittest.IsolatedAsyncioTestCase):
    """Un nombre gigante no infla el JWT ni consume cuota pública."""

    TOKEN = "token-de-prueba-solo-test"
    IP = "203.0.113.60"

    def setUp(self):
        self._originales = {
            "AUTH_TOKEN": Config.AUTH_TOKEN,
            "LIVEKIT_API_KEY": Config.LIVEKIT_API_KEY,
            "LIVEKIT_API_SECRET": Config.LIVEKIT_API_SECRET,
        }
        Config.AUTH_TOKEN = self.TOKEN
        Config.LIVEKIT_API_KEY = "clave-falsa-solo-test"
        Config.LIVEKIT_API_SECRET = "secreto-falso-solo-test"
        app_modulo._public_token_log.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        for attr, value in self._originales.items():
            setattr(Config, attr, value)
        app_modulo._public_token_log.clear()

    async def test_nombre_muy_largo_en_token_privado_es_400(self):
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.get_token(
                room="centralita-test",
                identity="cliente-demo",
                name="x" * 101,
                authorization=f"Bearer {self.TOKEN}",
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_identity_muy_larga_en_token_privado_es_400(self):
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.get_token(
                room="centralita-test",
                identity="x" * 101,
                name="Cliente Demo",
                authorization=f"Bearer {self.TOKEN}",
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_nombre_muy_largo_en_token_publico_es_400(self):
        with self.assertRaises(HTTPException) as ctx:
            await app_modulo.get_public_token(
                _peticion_publica(self.IP),
                room="centralita-test",
                name="x" * 101,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_nombre_de_100_caracteres_pasa_en_token_publico(self):
        resultado = await app_modulo.get_public_token(
            _peticion_publica(self.IP),
            room="centralita-test",
            name="x" * 100,
        )
        self.assertEqual(resultado["token"], "token-simulado-solo-test")
        self.assertEqual(resultado["room"], "centralita-test")

    async def test_nombre_largo_no_consume_cuota_publica(self):
        with self.assertRaises(HTTPException):
            await app_modulo.get_public_token(
                _peticion_publica(self.IP),
                room="centralita-test",
                name="x" * 101,
            )
        # La petición inválida se rechazó antes del rate-limit: la IP
        # no quedó registrada y su cuota sigue intacta.
        self.assertNotIn(self.IP, app_modulo._public_token_log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
