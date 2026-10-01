"""Punto de entrada de Vercel (Python runtime) para el backend de La Centralita.

Vercel carga de este archivo la variable de nivel superior `app` (ASGI): es la que
sirve las peticiones en producción. Un `handler` de nivel superior está RESERVADO
por Vercel para subclases de `http.server.BaseHTTPRequestHandler`; por eso el
adaptador Mangum se expone como `lambda_handler` y no como `handler` (con ese
nombre Vercel intentaría tratarlo como clase y el despliegue fallaría).

`lambda_handler` (Mangum) sirve para ejecutar la misma app en AWS Lambda o en
cualquier plataforma que entregue eventos estilo API Gateway, sin tocar el código.
"""

import sys
from pathlib import Path

# Vercel ejecuta desde la raíz del proyecto, pero se garantiza que `server` y
# `agent` sean importables también al cargar este módulo desde otro directorio.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mangum import Mangum  # noqa: E402

from server.app import app  # noqa: E402

# lifespan="off": la app no usa lifespan ni lanza tareas en segundo plano.
lambda_handler = Mangum(app, lifespan="off")

__all__ = ["app", "lambda_handler"]
