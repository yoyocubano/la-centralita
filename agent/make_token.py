"""Genera un token de acceso para probar el agente desde la página web.

Uso:
    python make_token.py [nombre-participante]
Imprime un token válido por 1 hora para la sala "centralita-test".
"""

import os
import sys
from datetime import timedelta

from dotenv import load_dotenv
from livekit.api import AccessToken, VideoGrants

load_dotenv()

token = (
    AccessToken(
        os.environ["LIVEKIT_API_KEY"],
        os.environ["LIVEKIT_API_SECRET"],
    )
    .with_identity(sys.argv[1] if len(sys.argv) > 1 else "probador")
    .with_grants(VideoGrants(room_join=True, room="centralita-test"))
    .with_ttl(timedelta(hours=1))
    .to_jwt()
)

print(token)
