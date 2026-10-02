"""Cliente del gateway D1 (workers/d1-gateway) — persistencia duradera de La Centralita.

Se activa solo si D1_GATEWAY_URL y D1_GATEWAY_SECRET están definidas; si no, el
backend sigue con su comportamiento anterior (memoria/ficheros + Google Sheets).

Todas las llamadas son best-effort desde el punto de vista del llamador: los métodos
lanzan D1GatewayError y cada punto de integración decide cómo degradar (y lo declara).
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("la-centralita.d1")

TIMEOUT_SECONDS = 5.0


class D1GatewayError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class D1Gateway:
    def __init__(self, url: Optional[str] = None, secret: Optional[str] = None, tenant: Optional[str] = None):
        self.url = (url if url is not None else os.getenv("D1_GATEWAY_URL", "")).rstrip("/")
        self.secret = secret if secret is not None else os.getenv("D1_GATEWAY_SECRET", "")
        self.tenant = tenant or os.getenv("CENTRALITA_TENANT_ID", "welux")

    @property
    def enabled(self) -> bool:
        return bool(self.url and len(self.secret) >= 32)

    # ------------------------------------------------------------------ transporte
    def _request_blocking(self, method: str, path: str, body: Optional[dict], query: Optional[dict]) -> Dict[str, Any]:
        url = f"{self.url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.secret}",
                "X-Tenant-Id": self.tenant,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as he:
            try:
                detail = json.loads(he.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            raise D1GatewayError(f"D1 gateway HTTP {he.code}: {detail}", he.code) from None
        except Exception as exc:
            raise D1GatewayError(f"D1 gateway no disponible ({type(exc).__name__})") from None

    async def _request(self, method: str, path: str, body: Optional[dict] = None, query: Optional[dict] = None) -> Dict[str, Any]:
        if not self.enabled:
            raise D1GatewayError("D1 gateway no configurado")
        return await asyncio.to_thread(self._request_blocking, method, path, body, query)

    # ------------------------------------------------------------------ API
    async def health(self) -> bool:
        return bool((await self._request("GET", "/v1/health")).get("db"))

    async def upsert_lead(self, lead: Dict[str, Any], source: str) -> str:
        fields = ("nombre", "telefono", "email", "empresa", "motivo", "detalles", "fecha_evento",
                  "tipo_evento", "valor_eur", "stage", "docuseal_status", "docuseal_url", "consent_at")
        body = {k: lead.get(k) for k in fields if lead.get(k) not in (None, "")}
        if lead.get("consentimiento_rgpd"):
            body["consent_at"] = lead["consentimiento_rgpd"]
        body.update({"id": lead["id"], "source": source})
        return (await self._request("POST", "/v1/leads", body))["id"]

    async def list_leads(self, limit: int = 200) -> List[Dict[str, Any]]:
        return (await self._request("GET", "/v1/leads", query={"limit": limit}))["leads"]

    async def set_stage(self, lead_id: str, stage: str) -> None:
        await self._request("POST", f"/v1/leads/{urllib.parse.quote(lead_id, safe='')}/stage", {"stage": stage})

    async def upsert_call(self, call: Dict[str, Any]) -> None:
        await self._request("POST", "/v1/calls", {k: v for k, v in call.items() if v is not None})

    async def add_turn(self, call_id: str, role: str, text: str) -> None:
        await self._request("POST", f"/v1/calls/{urllib.parse.quote(call_id, safe='')}/turns", {"role": role, "text": text})

    async def list_calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        return (await self._request("GET", "/v1/calls", query={"limit": limit}))["calls"]

    async def append_event(self, event_type: str, payload: Dict[str, Any]) -> int:
        return (await self._request("POST", "/v1/events", {"type": event_type, "payload": payload}))["id"]

    async def events_since(self, after: int, limit: int = 100) -> List[Dict[str, Any]]:
        return (await self._request("GET", "/v1/events", query={"after": after, "limit": limit}))["events"]

    async def add_appointment(self, appointment: Dict[str, Any]) -> None:
        await self._request("POST", "/v1/appointments", appointment)

    async def list_appointments(self, limit: int = 200) -> List[Dict[str, Any]]:
        return (await self._request("GET", "/v1/appointments", query={"limit": limit}))["appointments"]

    async def rate_limit_hit(self, bucket: str, window_seconds: int, max_requests: int) -> bool:
        res = await self._request("POST", "/v1/ratelimit", {"bucket": bucket, "window_seconds": window_seconds, "max": max_requests})
        return bool(res.get("allowed"))
