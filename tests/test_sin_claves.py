"""Pruebas automatizadas de todo lo testeable SIN claves, SIN red y SIN
dependencias extra (solo librería estándar de Python).

Cubre lo que la suite existente (pytest + livekit + fastapi + piper) no puede
probar en una máquina limpia:

  - agent/config.py: Config.validate() con claves ausentes y completas,
    y los valores por defecto en un entorno limpio (subproceso).
  - agent/lead_extract.py: extract_lead() con el HTTP simulado (éxito,
    fallo de red y respuesta rota: nunca rompe, devuelve dict de error),
    y transcripciones con llaves {} (regresión del .replace vs .format).
  - agent/prompts.py: sanidad de SYSTEM_PROMPT y EXTRACTION_PROMPT.
  - panel/ (modo demo): el monitor cae a "MODO DEMO ACTIVO" sin backend,
    la URL WebSocket sale del host actual (nunca hardcodeada) y la
    simulación de llamada existe.
  - Seguridad estática: ningún secreto real en los archivos que se sirven
    (nada de sk-... ni claves asignadas en el código) y .env ignorado.

Cómo correr (desde la raíz del repo, sin instalar nada)::

    python3 -m unittest tests.test_sin_claves -v
    # o también:
    python3 tests/test_sin_claves.py

Sin LIVEKIT_URL, sin DEEPGRAM_API_KEY, sin DEEPSEEK_API_KEY, sin red.
Rama: feature/tests-centralita (orden de mejora continua, ciclo 11/11/2026;
la orden del 08/11 a Antigravity nunca se ejecutó por cuota agotada).
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# python-dotenv es dependencia del agente, pero esta suite debe correr en
# cualquier máquina limpia: si no está instalado, se sustituye solo la
# carga del .env por un no-op. La lógica de Config (os.environ) se prueba
# tal cual, sin tocarla.
try:
    import dotenv  # noqa: F401
except ImportError:  # pragma: no cover - solo en máquinas sin dotenv
    import types

    _dotenv_stub = types.ModuleType("dotenv")
    _dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _dotenv_stub

from agent import prompts
from agent.config import Config
from agent.lead_extract import extract_lead


# ---------------------------------------------------------------------------
# Config.validate()
# ---------------------------------------------------------------------------

class TestConfigValidate(unittest.TestCase):
    """Validación de variables de entorno: sin claves, todo en falso."""

    _ATTRS = (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "DEEPGRAM_API_KEY",
        "DEEPSEEK_API_KEY",
        "N8N_WEBHOOK_URL",
        "PIPER_MODEL_PATH",
    )
    _EXPECTED_KEYS = {
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "DEEPGRAM_API_KEY",
        "DEEPSEEK_API_KEY",
        "N8N_WEBHOOK_URL",
        "PIPER_MODEL_EXISTS",
    }

    def setUp(self):
        self._originals = {a: getattr(Config, a) for a in self._ATTRS}
        for attr in self._ATTRS:
            setattr(Config, attr, "" if attr != "PIPER_MODEL_PATH" else "/ruta/que/no/existe/modelo.onnx")
        self.addCleanup(self._restore)

    def _restore(self):
        for attr, value in self._originals.items():
            setattr(Config, attr, value)

    def test_sin_claves_todo_en_falso(self):
        status = Config.validate()
        self.assertEqual(set(status), self._EXPECTED_KEYS)
        for key, value in status.items():
            self.assertIs(value, False, f"{key} debería ser False sin claves")

    def test_raise_on_missing_nombra_las_que_faltan(self):
        with self.assertRaises(ValueError) as ctx:
            Config.validate(raise_on_missing=True)
        mensaje = str(ctx.exception)
        self.assertIn("LIVEKIT_URL", mensaje)
        self.assertIn("DEEPSEEK_API_KEY", mensaje)
        self.assertIn("N8N_WEBHOOK_URL", mensaje)

    def test_con_todo_configurado_todo_en_verdadero(self):
        Config.LIVEKIT_URL = "https://mi-sala-abc123.livekit.cloud"
        Config.LIVEKIT_API_KEY = "clave-falsa-solo-test"
        Config.LIVEKIT_API_SECRET = "secreto-falso-solo-test"
        Config.DEEPGRAM_API_KEY = "deepgram-falso-solo-test"
        Config.DEEPSEEK_API_KEY = "deepseek-falso-solo-test"
        Config.N8N_WEBHOOK_URL = "https://ejemplo.test/webhook"
        Config.PIPER_MODEL_PATH = str(ROOT / "README.md")  # existe
        status = Config.validate()
        for key, value in status.items():
            self.assertIs(value, True, f"{key} debería ser True con todo configurado")
        # Con todo configurado, validate(raise_on_missing=True) no levanta.
        Config.validate(raise_on_missing=True)

    def test_livekit_url_debe_ser_de_livekit_cloud(self):
        Config.LIVEKIT_API_KEY = "x"
        Config.LIVEKIT_URL = "https://mi-servidor-propio.example.com"
        self.assertIs(Config.validate()["LIVEKIT_URL"], False)
        Config.LIVEKIT_URL = "https://mi-sala-abc123.livekit.cloud"
        self.assertIs(Config.validate()["LIVEKIT_URL"], True)

    def test_defaults_en_entorno_limpio(self):
        """Los valores por defecto (sin ninguna variable) son los documentados."""
        script = (
            "import sys, types, json\n"
            "d = types.ModuleType('dotenv'); "
            "d.load_dotenv = lambda *a, **k: None; "
            "sys.modules['dotenv'] = d\n"
            "from agent.config import Config\n"
            "print(json.dumps({"
            "'DEEPGRAM_MODEL': Config.DEEPGRAM_MODEL, "
            "'DEEPSEEK_MODEL': Config.DEEPSEEK_MODEL, "
            "'DEEPSEEK_BASE_URL': Config.DEEPSEEK_BASE_URL, "
            "'TTS_PROVIDER': Config.TTS_PROVIDER, "
            "'PORT': Config.PORT, 'HOST': Config.HOST}))\n"
        )
        env = dict(os.environ)
        for var in (
            "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
            "DEEPGRAM_API_KEY", "DEEPGRAM_MODEL", "DEEPGRAM_LANGUAGE",
            "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
            "TTS_PROVIDER", "PIPER_MODEL_PATH", "N8N_WEBHOOK_URL",
            "CENTRALITA_AUTH_TOKEN", "PORT", "HOST",
        ):
            env.pop(var, None)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ROOT), env=env,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
        valores = json.loads(proc.stdout.strip())
        self.assertEqual(valores["DEEPGRAM_MODEL"], "nova-3")
        self.assertEqual(valores["DEEPSEEK_MODEL"], "deepseek-chat")
        self.assertEqual(valores["DEEPSEEK_BASE_URL"], "https://api.deepseek.com/v1")
        self.assertEqual(valores["TTS_PROVIDER"], "piper")
        self.assertEqual(valores["PORT"], 8080)
        self.assertEqual(valores["HOST"], "0.0.0.0")


# ---------------------------------------------------------------------------
# extract_lead() con HTTP simulado
# ---------------------------------------------------------------------------

_LEAD = {
    "nombre": "Jean-Luc Weber",
    "telefono": "+352 691 452 890",
    "motivo": "Gala corporativa de fin de año",
    "linea_negocio": "eventos_produccion",
    "tipo_evento": "Gala corporativa",
    "fecha_evento": "2026-11-18",
    "numero_invitados": 150,
    "requerimientos_tecnicos": ["sonido", "iluminacion"],
    "detalles": "Salón en Kirchberg, 150 personas",
    "es_lead_valido": True,
    "requiere_seguimiento": True,
}
_SUCCESS_BODY = json.dumps(
    {"choices": [{"message": {"content": json.dumps(_LEAD)}}]}
).encode("utf-8")


class _FakeHTTPResponse:
    """Respuesta HTTP simulada (sin red): solo read() + contexto."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestExtractLead(unittest.IsolatedAsyncioTestCase):
    """La extracción nunca rompe el envío a n8n aunque falle DeepSeek."""

    def _fake_urlopen(self, body):
        def _call(req, timeout=30):
            return _FakeHTTPResponse(body)

        return _call

    async def test_exito_devuelve_el_lead_parseado(self):
        captured = {}

        def _call(req, timeout=30):
            captured["request"] = req
            return _FakeHTTPResponse(_SUCCESS_BODY)

        with patch("urllib.request.urlopen", side_effect=_call):
            result = await extract_lead("Cliente: hola, busco sonido para una gala.")
        self.assertEqual(result["nombre"], "Jean-Luc Weber")
        self.assertEqual(result["telefono"], "+352 691 452 890")
        self.assertIs(result["es_lead_valido"], True)

        req = captured["request"]
        self.assertEqual(req.get_method(), "POST")
        self.assertTrue(req.full_url.endswith("/chat/completions"))
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["temperature"], 0.1)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertIn("Cliente: hola, busco sonido para una gala.",
                      payload["messages"][0]["content"])
        self.assertTrue(req.get_header("Authorization").startswith("Bearer "))

    async def test_fallo_de_red_devuelve_dict_de_error(self):
        with patch("urllib.request.urlopen",
                   side_effect=URLError("sin red en el test")), \
                self.assertLogs("centralita", level="ERROR"):
            result = await extract_lead("Cliente: hola.")
        self.assertIn("error", result)
        self.assertEqual(result["motivo"], "extracción fallida, ver transcripción")

    async def test_respuesta_rota_devuelve_dict_de_error(self):
        casos_rotos = [
            json.dumps({"choices": []}).encode("utf-8"),          # sin choices
            json.dumps({"choices": [{"message": {"content": "no es json"}}]}).encode("utf-8"),
            b"esto no es JSON",
        ]
        for body in casos_rotos:
            with self.subTest(body=body[:40]), \
                    patch("urllib.request.urlopen", side_effect=self._fake_urlopen(body)), \
                    self.assertLogs("centralita", level="ERROR"):
                result = await extract_lead("Cliente: hola.")
            self.assertIn("error", result)
            self.assertEqual(result["motivo"], "extracción fallida, ver transcripción")

    async def test_transcripcion_con_llaves_no_rompe(self):
        """Regresión: el prompt usa .replace(), no .format() (hay llaves JSON)."""
        captured = {}

        def _call(req, timeout=30):
            captured["request"] = req
            return _FakeHTTPResponse(_SUCCESS_BODY)

        transcript = "Cliente: me llamo {nombre}, teléfono {telefono}; llaves sueltas { y }."
        with patch("urllib.request.urlopen", side_effect=_call):
            result = await extract_lead(transcript)
        self.assertEqual(result["nombre"], "Jean-Luc Weber")
        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertIn(transcript, payload["messages"][0]["content"])

    async def test_transcripcion_vacia_no_lanza_excepcion(self):
        with patch("urllib.request.urlopen",
                   side_effect=self._fake_urlopen(_SUCCESS_BODY)):
            result = await extract_lead("")
        self.assertEqual(result["nombre"], "Jean-Luc Weber")


# ---------------------------------------------------------------------------
# prompts.py
# ---------------------------------------------------------------------------

class TestPrompts(unittest.TestCase):
    def test_system_prompt_presenta_a_sofia_y_las_cuatro_lineas(self):
        self.assertTrue(prompts.SYSTEM_PROMPT.strip())
        self.assertIn("Sofía", prompts.SYSTEM_PROMPT)
        for linea in ("Asesoría", "digitales", "Alquileres", "B2B"):
            self.assertIn(linea, prompts.SYSTEM_PROMPT)

    def test_extraction_prompt_tiene_placeholder_y_claves(self):
        self.assertIn("{transcript}", prompts.EXTRACTION_PROMPT)
        self.assertIn("JSON", prompts.EXTRACTION_PROMPT)
        for clave in (
            "nombre", "telefono", "motivo", "linea_negocio", "tipo_evento",
            "fecha_evento", "numero_invitados", "requerimientos_tecnicos",
            "detalles", "es_lead_valido", "requiere_seguimiento",
        ):
            self.assertIn(f'"{clave}"', prompts.EXTRACTION_PROMPT)

    def test_extraction_prompt_es_alias_del_prompt_de_extraccion(self):
        self.assertIs(prompts.EXTRACTION_PROMPT, prompts.LEAD_EXTRACTION_PROMPT)

    def test_sustitucion_con_replace_deja_la_transcripcion_dentro(self):
        texto = prompts.EXTRACTION_PROMPT.replace("{transcript}", "TEXTO DE PRUEBA")
        self.assertIn("TEXTO DE PRUEBA", texto)
        self.assertNotIn("{transcript}", texto)


# ---------------------------------------------------------------------------
# Panel en modo demo (estático, sin navegador)
# ---------------------------------------------------------------------------

class TestPanelDemoMode(unittest.TestCase):
    PANEL_JS = ROOT / "panel" / "app.js"
    PANEL_HTML = ROOT / "panel" / "index.html"

    def _js(self):
        return self.PANEL_JS.read_text(encoding="utf-8")

    def test_sin_backend_cae_a_modo_demo(self):
        js = self._js()
        self.assertIn("MODO DEMO ACTIVO", js)
        self.assertIn("Simulación Local", js)
        self.assertIn("Ejecutando en Modo Demo", js)

    def test_existe_la_simulacion_de_llamada_demo(self):
        js = self._js()
        self.assertIn("function triggerIncomingCallDemo", js)
        self.assertIn("function playNextDemoTurn", js)

    def test_websocket_sale_del_host_actual_no_hardcodeado(self):
        js = self._js()
        self.assertIn("/ws/monitor?token=", js)
        self.assertIn("window.location.host", js)
        self.assertIn("localhost:8080", js)  # solo para desarrollo local
        self.assertNotIn("wss://yoyocubano.github.io", js)
        self.assertNotIn("wss://tu-servidor", js)

    def test_panel_html_marca_el_modo_demo(self):
        html = self.PANEL_HTML.read_text(encoding="utf-8")
        self.assertIn("Demo", html)
        self.assertIn("btnSimulateTrigger", html)


class TestSinSecretosEnArchivosServidos(unittest.TestCase):
    """Ningún secreto real en los archivos que se publican (regla HANDSHAKE)."""

    ARCHIVOS = [
        "index.html",
        "panel/app.js", "panel/index.html", "panel/style.css",
        "web/app.js", "web/index.html", "web/style.css",
        "agent/agent.py", "agent/config.py", "agent/lead_extract.py",
        "agent/make_token.py", "agent/piper_tts.py", "agent/post_call.py",
        "agent/prompts.py", "server/app.py",
    ]
    _PATRONES = [
        re.compile(r"sk-[A-Za-z0-9]{16,}"),  # claves tipo OpenAI/DeepSeek
        re.compile(
            r"(LIVEKIT_API_SECRET|DEEPGRAM_API_KEY|DEEPSEEK_API_KEY)"
            r"\s*=\s*['\"][^'\"]+['\"]"
        ),
    ]

    def test_ningun_secreto_en_archivos_servidos(self):
        for relativo in self.ARCHIVOS:
            ruta = ROOT / relativo
            self.assertTrue(ruta.exists(), f"falta {relativo}")
            contenido = ruta.read_text(encoding="utf-8")
            for patron in self._PATRONES:
                hallado = patron.search(contenido)
                self.assertIsNone(
                    hallado,
                    f"{relativo} parece contener un secreto: {hallado.group(0)[:40] if hallado else ''}",
                )
            self.assertNotIn("weluxdigitalservices.app.n8n.cloud", contenido,
                             f"{relativo} contiene la URL real del webhook de n8n")


class TestGitignoreSecretos(unittest.TestCase):
    def test_env_esta_ignorado(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        lineas = [l.strip() for l in gitignore.splitlines()]
        self.assertIn(".env", lineas)
        self.assertIn("*.env", lineas)

    def test_no_hay_archivo_env_en_el_repo(self):
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            self.assertNotIn(".env", filenames, f".env real en {dirpath}")


# ---------------------------------------------------------------------------
# Meta: esta suite es solo stdlib
# ---------------------------------------------------------------------------

class TestSuiteSoloStdlib(unittest.TestCase):
    def test_sin_dependencias_externas(self):
        propio = Path(__file__).read_text(encoding="utf-8")
        # Quitar el docstring del módulo: ahí se nombran pytest/livekit/
        # fastapi solo como texto explicativo, no como imports.
        sin_docstring = re.sub(r'\A""".*?"""', "", propio, flags=re.S)
        for prohibido in ("pytest", "livekit", "fastapi", "requests"):
            self.assertNotRegex(
                sin_docstring,
                rf"(?m)^\s*(import|from)\s+{prohibido}\b",
                f"esta suite no debe importar {prohibido}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
