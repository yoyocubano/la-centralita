"""Pruebas sin claves para la emisión de eventos del agente al panel (/api/call-event).

Verifica:
  - Forma de los payloads `call_started` y `call_ended` (compatibles con
    ALLOWED_EVENT_KEYS de server/app.py).
  - Anonimización RGPD con redact_pii + supresión de nombre, teléfono y email.
  - Lectura de CENTRALITA_SERVER_URL (default http://127.0.0.1:8000) y
    CENTRALITA_AUTH_TOKEN como Bearer sin exponer el secreto en logs.
  - Tolerancia a fallos de red/HTTP (loguea error, retorna False, no bloquea).

Funciona tanto con librería estándar (`python3 -m unittest tests.test_agente_eventos_panel -v`)
como con `pytest`.
"""

from datetime import datetime, timedelta, timezone
import io
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# Instalar stubs mínimos SOLO si las dependencias externas no están instaladas.
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
    import livekit.agents  # noqa: F401
    import livekit.plugins  # noqa: F401
except ImportError:
    class _StubBase:
        def __init__(self, *a, **kw):
            pass

    class _StubLLMMod:
        @staticmethod
        def function_tool(*a, **kw):
            def wrap(fn):
                return fn
            return wrap

    class _StubDeepgram:
        STT = _StubBase

    class _StubOpenAIPlugin:
        LLM = _StubBase

    class _StubSilero:
        class VAD:
            @staticmethod
            def load(*a, **kw):
                return object()

    class _StubLiveKitPlugin:
        @staticmethod
        def register_plugin(*a, **kw):
            pass

    class _StubAudioStream:
        def __init__(self, *a, **kw):
            pass

    class _StubCapabilities:
        def __init__(self, *a, **kw):
            pass

    class _StubTTS:
        def __init__(self, *a, **kw):
            pass

    class _StubChunkedStream:
        def __init__(self, *a, **kw):
            pass

    _tts_mod = _ensure_stub(
        "livekit.agents.tts",
        TTS=_StubTTS,
        ChunkedStream=_StubChunkedStream,
        TTSCapabilities=_StubCapabilities,
        AudioEmitter=object,
    )
    _ensure_stub("livekit.agents.utils", AudioBuffer=object)
    _agents_mod = _ensure_stub(
        "livekit.agents",
        Agent=_StubBase,
        AgentSession=_StubBase,
        APIConnectOptions=object,
        JobContext=_StubBase,
        Plugin=_StubLiveKitPlugin,
        WorkerOptions=_StubBase,
        cli=types.SimpleNamespace(run_app=lambda *a, **kw: None),
        llm=_StubLLMMod,
        tts=_tts_mod,
    )
    _plugins_mod = _ensure_stub(
        "livekit.plugins",
        deepgram=_StubDeepgram,
        openai=_StubOpenAIPlugin,
        silero=_StubSilero,
    )
    _ensure_stub("livekit.rtc", AudioStream=_StubAudioStream)
    _ensure_stub("livekit", agents=_agents_mod, plugins=_plugins_mod)

import agent.agent as agent_modulo  # noqa: E402
from agent.config import Config  # noqa: E402

ALLOWED_EVENT_KEYS = {
    "type",
    "call_id",
    "timestamp",
    "status",
    "room",
    "duration",
    "transcript",
    "lead",
    "agent",
}


class _FakeHTTPResponse:
    def __init__(self, status=200, body=b'{"status":"broadcasted","receivers":1}'):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class TestAgenteEventosPanel(unittest.IsolatedAsyncioTestCase):
    """Emisión de eventos reales del agente hacia POST /api/call-event."""

    def setUp(self):
        self._orig_token = Config.AUTH_TOKEN
        self._orig_server_url = Config.SERVER_URL
        self.addCleanup(self._restore)

    def _restore(self):
        Config.AUTH_TOKEN = self._orig_token
        Config.SERVER_URL = self._orig_server_url

    def test_payload_call_started_forma_y_claves_permitidas(self):
        inicio = datetime(2026, 11, 26, 9, 30, 0, tzinfo=timezone.utc)
        payload = agent_modulo.build_call_started_payload(
            call_id="call-abc123def456",
            room="centralita-test",
            started_at=inicio,
            agent_name="Sofía (WELUX Events)",
        )
        self.assertEqual(payload["type"], "call_started")
        self.assertEqual(payload["call_id"], "call-abc123def456")
        self.assertEqual(payload["timestamp"], inicio.isoformat())
        self.assertEqual(payload["status"], "iniciada")
        self.assertEqual(payload["room"], "centralita-test")
        self.assertEqual(payload["duration"], 0)
        self.assertTrue(set(payload.keys()).issubset(ALLOWED_EVENT_KEYS))

    def test_payload_call_ended_forma_duracion_y_anonimizacion_rgpd(self):
        inicio = datetime(2026, 11, 26, 9, 30, 0, tzinfo=timezone.utc)
        fin = inicio + timedelta(seconds=95, milliseconds=500)
        transcript = (
            "Cliente: Hola, soy Jean-Luc Weber, mi teléfono es +352 691 452 890 "
            "y mi correo jeanluc@kirchberg.lu. Buscamos fotoespejo para gala en Kirchberg."
        )
        lead = {
            "nombre": "Jean-Luc Weber",
            "telefono": "+352 691 452 890",
            "email": "jeanluc@kirchberg.lu",
            "motivo": "Cotización para Jean-Luc Weber al +352 691 452 890",
            "tipo_evento": "Gala corporativa",
            "fecha_evento": "2026-12-12",
            "detalles": "Contacto Jean-Luc Weber (jeanluc@kirchberg.lu) solicita fotoespejo.",
            "es_lead_valido": True,
        }
        payload = agent_modulo.build_call_ended_payload(
            call_id="call-abc123def456",
            room="centralita-test",
            started_at=inicio,
            ended_at=fin,
            transcript=transcript,
            lead=lead,
            lead_hash="hash-sha256-prueba",
            agent_name="Sofía (WELUX Events)",
        )
        self.assertTrue(set(payload.keys()).issubset(ALLOWED_EVENT_KEYS))
        self.assertEqual(payload["type"], "call_ended")
        self.assertEqual(payload["call_id"], "call-abc123def456")
        self.assertEqual(payload["status"], "terminada")
        self.assertEqual(payload["duration"], 95.5)
        self.assertEqual(payload["timestamp"], fin.isoformat())

        lead_panel = payload["lead"]
        self.assertEqual(lead_panel["inicio"], inicio.isoformat())
        self.assertEqual(lead_panel["fin"], fin.isoformat())
        self.assertEqual(lead_panel["lead_hash"], "hash-sha256-prueba")
        self.assertNotIn("nombre", lead_panel)
        self.assertNotIn("telefono", lead_panel)
        self.assertNotIn("email", lead_panel)

        serialized = json.dumps(payload, ensure_ascii=False)
        for pii in ("Jean-Luc", "Weber", "691 452 890", "jeanluc@kirchberg.lu"):
            self.assertNotIn(pii, serialized)
        self.assertIn("[EMAIL_REDACTED]", serialized)
        self.assertIn("[PHONE_REDACTED]", serialized)
        self.assertIn("[NAME_REDACTED]", serialized)

    async def test_post_call_event_envia_bearer_y_usa_server_url_de_entorno(self):
        captured = {}

        def _fake_urlopen(req, timeout=5):
            captured["req"] = req
            captured["timeout"] = timeout
            return _FakeHTTPResponse(status=200)

        payload = {"type": "call_started", "call_id": "call-1", "status": "iniciada"}
        env_patch = {
            "CENTRALITA_SERVER_URL": "https://centralita.welux.example/",
            "CENTRALITA_AUTH_TOKEN": "token-test-seguro-no-real",
        }
        with patch.dict(os.environ, env_patch, clear=False), patch(
            "urllib.request.urlopen", side_effect=_fake_urlopen
        ):
            ok = await agent_modulo.post_call_event(payload)

        self.assertTrue(ok)
        req = captured["req"]
        self.assertEqual(req.full_url, "https://centralita.welux.example/api/call-event")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "Bearer token-test-seguro-no-real")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(req.data.decode("utf-8")), payload)

    async def test_post_call_event_usa_url_por_defecto_localhost_8000(self):
        captured = {}

        def _fake_urlopen(req, timeout=5):
            captured["url"] = req.full_url
            return _FakeHTTPResponse(status=200)

        Config.SERVER_URL = "http://127.0.0.1:8000"
        with patch.dict(
            os.environ,
            {"CENTRALITA_AUTH_TOKEN": "token-test-localhost"},
            clear=False,
        ):
            os.environ.pop("CENTRALITA_SERVER_URL", None)
            with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                ok = await agent_modulo.post_call_event({"type": "call_started"})

        self.assertTrue(ok)
        self.assertEqual(captured["url"], "http://127.0.0.1:8000/api/call-event")

    async def test_post_call_event_fallo_de_red_o_http_no_rompe_ni_filtra_token(self):
        secreto = "secreto-ultrasecreto-que-no-debe-loguearse"
        errores = [
            URLError("conexión rechazada en test"),
            HTTPError("http://127.0.0.1:8000/api/call-event", 500, "Internal Error", {}, io.BytesIO(b"fail")),
            TimeoutError("timeout en test"),
        ]
        for err in errores:
            with patch.dict(
                os.environ,
                {"CENTRALITA_AUTH_TOKEN": secreto, "CENTRALITA_SERVER_URL": "http://127.0.0.1:8000"},
                clear=False,
            ), patch("urllib.request.urlopen", side_effect=err), self.assertLogs(
                "centralita", level="ERROR"
            ) as cm:
                ok = await agent_modulo.post_call_event({"type": "call_ended", "call_id": "call-fail"})
            self.assertFalse(ok)
            log_text = "\n".join(cm.output)
            self.assertIn("Error al enviar evento 'call_ended' al panel", log_text)
            self.assertNotIn(secreto, log_text)

    async def test_post_call_event_sin_token_salta_envio_sin_error(self):
        Config.AUTH_TOKEN = ""
        env_clean = {k: v for k, v in os.environ.items() if k != "CENTRALITA_AUTH_TOKEN"}
        with patch.dict(os.environ, env_clean, clear=True), patch(
            "urllib.request.urlopen"
        ) as mocked_open, self.assertLogs("centralita", level="WARNING"):
            ok = await agent_modulo.post_call_event({"type": "call_started", "call_id": "call-no-tok"})

        self.assertFalse(ok)
        mocked_open.assert_not_called()

    def test_compatibilidad_real_con_server_api_call_event(self):
        try:
            from fastapi.testclient import TestClient
            from server.app import app, CALLS_DATABASE, LEADS_DATABASE
        except Exception:
            self.skipTest("fastapi.testclient no disponible en modo stdlib puro")

        token_original = Config.AUTH_TOKEN
        Config.AUTH_TOKEN = "token-integracion-panel-local"
        CALLS_DATABASE.clear()
        LEADS_DATABASE.clear()
        try:
            client = TestClient(app)
            inicio = datetime(2026, 11, 26, 9, 40, 0, tzinfo=timezone.utc)
            fin = inicio + timedelta(seconds=30)

            started = agent_modulo.build_call_started_payload(
                "call-e2e-int", "centralita-test", inicio, "Sofía (WELUX Events)"
            )
            r1 = client.post(
                "/api/call-event",
                json=started,
                headers={"Authorization": "Bearer token-integracion-panel-local"},
            )
            self.assertEqual(r1.status_code, 200)

            ended = agent_modulo.build_call_ended_payload(
                "call-e2e-int",
                "centralita-test",
                inicio,
                fin,
                "Cliente: Me llamo Marie Claire, tel +352 621 111 222, email marie@lux.lu.",
                {
                    "nombre": "Marie Claire",
                    "telefono": "+352 621 111 222",
                    "email": "marie@lux.lu",
                    "motivo": "Información servicios B2B",
                    "tipo_evento": "B2B",
                    "es_lead_valido": True,
                },
                "hash-e2e",
                "Sofía (WELUX Events)",
            )
            r2 = client.post(
                "/api/call-event",
                json=ended,
                headers={"Authorization": "Bearer token-integracion-panel-local"},
            )
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(len(CALLS_DATABASE), 1)
            self.assertEqual(len(LEADS_DATABASE), 1)
            stored = json.dumps(list(CALLS_DATABASE) + list(LEADS_DATABASE), ensure_ascii=False)
            self.assertNotIn("Marie Claire", stored)
            self.assertNotIn("621 111 222", stored)
            self.assertNotIn("marie@lux.lu", stored)
        finally:
            Config.AUTH_TOKEN = token_original
            CALLS_DATABASE.clear()
            LEADS_DATABASE.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
