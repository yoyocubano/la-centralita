"""Módulo de notificaciones por email post-llamada para WELUX Events.

Envía un informe ejecutivo tras cada llamada completada a NOTIFICATION_EMAIL
con datos del contacto, transcripción resumida, intención detectada y próximos pasos.

Garantías:
- Todos los datos del lead se escapan en el HTML (sin inyección de marcado).
- El asunto se limpia de saltos de línea (sin inyección de cabeceras).
- El envío SMTP corre en un hilo (no bloquea el event loop del servidor).
- Si SMTP no está configurado o falla, el correo se guarda en una bandeja de salida
  local (<DATA_DIR>/email_outbox.jsonl) para reenviarlo con `scripts/flush_email_outbox.py`;
  el estado devuelto lo declara (QUEUED_NO_SMTP / QUEUED_SMTP_ERROR), nunca "SENT".
- Si además el disco no es escribible (serverless), no se lanza excepción: el estado
  pasa a NOT_PERSISTED_NO_SMTP / NOT_PERSISTED_SMTP_ERROR para que nadie lo dé por enviado.
"""

import asyncio
import html
import json
import logging
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict

from agent.config import DATA_DIR

logger = logging.getLogger("la-centralita.email_notify")

OUTBOX_FILE = DATA_DIR / "email_outbox.jsonl"


def _one_line(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


class EmailNotifier:
    """Envío de notificaciones por correo electrónico post-llamada."""

    def __init__(self):
        self.recipient = os.getenv("NOTIFICATION_EMAIL", "info@weluxevents.com")
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", "centralita@weluxevents.com")

    @property
    def is_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    def build_subject(self, call_data: Dict[str, Any]) -> str:
        lead = call_data.get("lead") or {}
        nombre = _one_line(lead.get("nombre") or lead.get("name") or "Cliente", 80)
        motivo = _one_line(lead.get("motivo") or lead.get("interest") or "Consulta", 80)
        return f"[La Centralita] Nueva llamada de {nombre} · {motivo}"

    def build_email_content(self, call_data: Dict[str, Any]) -> tuple[str, str]:
        """Genera el contenido en texto plano y HTML profesional."""
        lead = call_data.get("lead") or {}
        nombre = lead.get("nombre") or lead.get("name") or "No especificado"
        telefono = lead.get("telefono") or lead.get("phone") or "No especificado"
        empresa = lead.get("empresa") or lead.get("company") or "Particular / No indicada"
        motivo = lead.get("motivo") or lead.get("interest") or "Consulta comercial"
        resumen = lead.get("detalles") or lead.get("summary") or str(call_data.get("transcripcion", ""))[:250]
        duracion = call_data.get("duration_formatted") or f"{float(call_data.get('duration_seconds') or 0):.0f} seg"
        fecha_lux = call_data.get("timestamp_lux") or "Europe/Luxembourg"
        valor = lead.get("valor_eur") or "Por cotizar"

        # Próximo paso sugerido según intención
        motivo_l = str(motivo).lower()
        proximo_paso = "Contactar al cliente en menos de 2 horas con dossier de servicios y disponibilidad."
        if "alquiler" in motivo_l or "fotoespejo" in motivo_l or "inflable" in motivo_l:
            proximo_paso = "Verificar disponibilidad de inventario para la fecha solicitada y emitir propuesta formal."
        elif "asesoría" in motivo_l or "consultor" in motivo_l:
            proximo_paso = "Agendar sesión de diagnóstico estratégico de 30 minutos con consultor senior."
        elif "web" in motivo_l or "chatbot" in motivo_l:
            proximo_paso = "Preparar especificación técnica de portal web y demostración de chatbot."

        # Versión Texto Plano
        text_body = f"""==================================================
NUEVA LLAMADA REGISTRADA — LA CENTRALITA (WELUX)
==================================================

DATOS DEL CLIENTE:
- Nombre: {nombre}
- Teléfono: {telefono}
- Empresa: {empresa}
- Motivo / Interés: {motivo}
- Valor Estimado: {valor}

DETALLES DE LA LLAMADA:
- Duración: {duracion}
- Fecha/Hora (Luxemburgo): {fecha_lux}
- Resumen Conversación:
  {resumen}

PRÓXIMO PASO SUGERIDO:
-> {proximo_paso}

Accede al Panel del Cliente para gestionar este lead o emitir contrato DocuSeal.
==================================================
WELUX Events S.à r.l. · Luxemburgo
"""

        e = {k: html.escape(str(v), quote=True) for k, v in {
            "nombre": nombre, "telefono": telefono, "empresa": empresa, "motivo": motivo,
            "valor": valor, "duracion": duracion, "fecha_lux": fecha_lux,
            "resumen": resumen, "proximo_paso": proximo_paso,
        }.items()}

        # Versión HTML (todos los datos escapados)
        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f5f5f7; color: #1d1d1f; margin: 0; padding: 24px; }}
    .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid rgba(0,0,0,0.08); border-radius: 14px; overflow: hidden; }}
    .header {{ padding: 22px 24px; border-bottom: 1px solid rgba(0,0,0,0.06); }}
    .header h1 {{ margin: 0; font-size: 17px; letter-spacing: 0.02em; }}
    .header p {{ margin: 4px 0 0; font-size: 13px; color: #86868b; }}
    .content {{ padding: 24px; }}
    .lead-box {{ background: #f5f5f7; border-radius: 10px; padding: 18px; margin-bottom: 20px; }}
    .lead-title {{ font-size: 18px; font-weight: 600; margin-bottom: 12px; }}
    .field {{ margin-bottom: 8px; font-size: 14px; }}
    .label {{ color: #86868b; display: inline-block; width: 140px; }}
    .val {{ font-weight: 600; }}
    .action-box {{ background: rgba(0,113,227,0.08); border-left: 3px solid #0071e3; padding: 14px; border-radius: 6px; margin-top: 16px; font-size: 14px; }}
    .footer {{ padding: 16px 24px; text-align: center; font-size: 12px; color: #86868b; border-top: 1px solid rgba(0,0,0,0.06); }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>LA CENTRALITA · WELUX EVENTS</h1>
      <p>Notificación ejecutiva post-llamada atendida por Sofía (IA)</p>
    </div>
    <div class="content">
      <div class="lead-box">
        <div class="lead-title">{e['nombre']}</div>
        <div class="field"><span class="label">Teléfono:</span> <span class="val">{e['telefono']}</span></div>
        <div class="field"><span class="label">Empresa:</span> <span class="val">{e['empresa']}</span></div>
        <div class="field"><span class="label">Interés:</span> <span class="val">{e['motivo']}</span></div>
        <div class="field"><span class="label">Valor Estimado:</span> <span class="val">{e['valor']}</span></div>
        <div class="field"><span class="label">Duración:</span> <span class="val">{e['duracion']}</span></div>
        <div class="field"><span class="label">Registro (LUX):</span> <span class="val">{e['fecha_lux']}</span></div>
      </div>

      <div style="font-size: 14px; line-height: 1.5; color: #48484a; margin-bottom: 16px;">
        <strong>Resumen de la Conversación:</strong><br>
        {e['resumen']}
      </div>

      <div class="action-box">
        <strong>Próximo Paso Sugerido:</strong><br>
        {e['proximo_paso']}
      </div>
    </div>
    <div class="footer">
      WELUX Events S.à r.l. · Luxembourg · Centralita Telefónica con Inteligencia Artificial
    </div>
  </div>
</body>
</html>"""

        return text_body, html_body

    def _write_outbox(self, entry: Dict[str, Any]) -> bool:
        try:
            OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTBOX_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True
        except OSError as exc:
            logger.error("Bandeja de salida local no disponible (%s): notificación no persistida.", type(exc).__name__)
            return False

    def _send_smtp(self, subject: str, text_content: str, html_content: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.smtp_from
        msg["To"] = self.recipient
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        context = ssl.create_default_context()
        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15, context=context) as server:
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_from, [self.recipient], msg.as_string())
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.starttls(context=context)
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_from, [self.recipient], msg.as_string())

    async def send_post_call_notification(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        """Envía el email post-llamada; si no es posible, lo deja en la bandeja de salida."""
        subject = self.build_subject(call_data)
        text_content, html_content = self.build_email_content(call_data)
        outbox_entry = {
            "queued_at": datetime.now(timezone.utc).isoformat(),
            "recipient": self.recipient,
            "subject": subject,
            "text": text_content,
            "html": html_content,
        }

        if not self.is_configured:
            if not self._write_outbox(outbox_entry):
                return {"status": "NOT_PERSISTED_NO_SMTP", "recipient": self.recipient, "subject": subject}
            logger.warning("SMTP no configurado: notificación guardada en bandeja de salida local.")
            return {"status": "QUEUED_NO_SMTP", "recipient": self.recipient, "subject": subject}

        try:
            await asyncio.to_thread(self._send_smtp, subject, text_content, html_content)
            logger.info("Notificación post-llamada enviada a %s", self.recipient)
            return {"status": "SENT", "recipient": self.recipient, "subject": subject}
        except Exception as e:
            status = "QUEUED_SMTP_ERROR" if self._write_outbox(outbox_entry) else "NOT_PERSISTED_SMTP_ERROR"
            logger.error("Error SMTP (%s); estado de la notificación: %s.", type(e).__name__, status)
            return {"status": status, "error": type(e).__name__, "recipient": self.recipient}
