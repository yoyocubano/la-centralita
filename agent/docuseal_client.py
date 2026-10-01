"""Cliente de integración con DocuSeal para firma electrónica de contratos.

Genera envíos de contrato vía la API de DocuSeal (eIDAS) cuando hay
DOCUSEAL_API_KEY y DOCUSEAL_TEMPLATE_ID configurados.

Sin configuración NO se simula nada: el contrato queda en estado "BORRADOR"
(pendiente de emisión manual) y sin enlace de firma. Nunca se devuelve un
estado "ENVIADO" ni una URL de firma que no exista.
"""

import asyncio
import json
import logging
import os
import urllib.request
from typing import Any, Dict

logger = logging.getLogger("la-centralita.docuseal")


class DocuSealClient:
    """Cliente para la creación y gestión de contratos en DocuSeal."""

    def __init__(self):
        self.api_key = os.getenv("DOCUSEAL_API_KEY", "")
        self.api_url = os.getenv("DOCUSEAL_API_URL", "https://api.docuseal.com").rstrip("/")
        self.default_template_id = os.getenv("DOCUSEAL_TEMPLATE_ID", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.default_template_id)

    def _post_submission(self, payload: Dict[str, Any]) -> Any:
        req = urllib.request.Request(
            f"{self.api_url}/submissions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Auth-Token": self.api_key},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def create_contract_submission(
        self,
        client_name: str,
        client_email: str,
        client_phone: str,
        event_interest: str,
        amount_eur: float | None = None,
        event_date: str = "A definir",
    ) -> Dict[str, Any]:
        """Genera un envío de contrato para firma electrónica con DocuSeal."""
        if not self.is_configured:
            logger.info("DocuSeal no configurado: contrato queda en BORRADOR (emisión manual).")
            return {"status": "BORRADOR", "submission_id": None, "sign_url": None, "reason": "docuseal_not_configured"}

        if not client_email and not client_phone:
            return {"status": "BORRADOR", "submission_id": None, "sign_url": None, "reason": "missing_contact"}

        submitter: Dict[str, Any] = {
            "role": os.getenv("DOCUSEAL_SUBMITTER_ROLE", "Client"),
            "name": client_name,
            "fields": [
                {"name": "ClientName", "default_value": client_name},
                {"name": "EventInterest", "default_value": event_interest},
                {"name": "EventDate", "default_value": event_date},
            ],
        }
        if client_email:
            submitter["email"] = client_email
        if client_phone:
            submitter["phone"] = client_phone
        if amount_eur:
            submitter["fields"].append({"name": "TotalAmount", "default_value": f"{amount_eur:,.2f} €"})

        payload = {"template_id": self.default_template_id, "send_email": bool(client_email), "submitters": [submitter]}
        try:
            data = await asyncio.to_thread(self._post_submission, payload)
        except Exception as e:
            logger.warning("Fallo al crear envío en DocuSeal (%s). Contrato queda en BORRADOR.", type(e).__name__)
            return {"status": "BORRADOR", "submission_id": None, "sign_url": None, "reason": "docuseal_error"}

        # La API devuelve una lista de submitters (o un objeto con 'submitters').
        submitters = data if isinstance(data, list) else (data or {}).get("submitters", [])
        first = submitters[0] if submitters else {}
        return {
            "status": "ENVIADO",
            "submission_id": first.get("submission_id") or (data.get("id") if isinstance(data, dict) else None),
            "sign_url": first.get("embed_src"),
            "amount_eur": amount_eur,
        }
