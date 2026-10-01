"""Módulo de notificaciones por email post-llamada para WELUX Events.

Envía un informe ejecutivo tras cada llamada completada a info@weluxevents.com
con datos del contacto, transcripción resumida, intención detectada y próximos pasos.
"""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import os
import smtplib
from typing import Any, Dict

logger = logging.getLogger("la-centralita.email_notify")


class EmailNotifier:
    """Envío de notificaciones por correo electrónico post-llamada."""

    def __init__(self):
        self.recipient = os.getenv("NOTIFICATION_EMAIL", "info@weluxevents.com")
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from = os.getenv("SMTP_FROM", "centralita@weluxevents.com")

    def build_email_content(self, call_data: Dict[str, Any]) -> tuple[str, str]:
        """Genera el contenido en texto plano y HTML profesional."""
        lead = call_data.get("lead") or {}
        nombre = lead.get("nombre") or lead.get("name") or "No especificado"
        telefono = lead.get("telefono") or lead.get("phone") or "No especificado"
        empresa = lead.get("empresa") or lead.get("company") or "Particular / No indicada"
        motivo = lead.get("motivo") or lead.get("interest") or "Consulta comercial"
        resumen = lead.get("detalles") or lead.get("summary") or call_data.get("transcripcion", "")[:250]
        duracion = call_data.get("duration_formatted") or f"{call_data.get('duration_seconds', 0):.0f} seg"
        fecha_lux = call_data.get("timestamp_lux") or "Europe/Luxembourg"
        valor = lead.get("valor_eur") or "2.500 € (estimado)"

        # Próximo paso sugerido según intención
        proximo_paso = "Contactar al cliente en menos de 2 horas con dossier de servicios y disponibilidad."
        if "alquiler" in motivo.lower() or "fotoespejo" in motivo.lower() or "inflable" in motivo.lower():
            proximo_paso = "Verificar disponibilidad de inventario para la fecha solicitada y emitir propuesta formal."
        elif "asesoría" in motivo.lower() or "consultor" in motivo.lower():
            proximo_paso = "Agendar sesión de diagnóstico estratégico de 30 minutos con consultor senior."
        elif "web" in motivo.lower() or "chatbot" in motivo.lower():
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

        # Versión HTML Profesional con estética WELUX
        html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #0b0e14; color: #f4f6fb; margin: 0; padding: 24px; }}
    .card {{ max-width: 600px; margin: 0 auto; background: #131726; border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.4); }}
    .header {{ background: linear-gradient(135deg, #2a2010 0%, #151824 100%); padding: 24px; border-bottom: 2px solid #d4af37; }}
    .header h1 {{ margin: 0; font-size: 20px; color: #f0d97a; letter-spacing: 1px; }}
    .header p {{ margin: 4px 0 0; font-size: 13px; color: #8b93a9; }}
    .content {{ padding: 24px; }}
    .lead-box {{ background: rgba(0,0,0,0.3); border: 1px solid rgba(212,175,55,0.25); border-radius: 8px; padding: 18px; margin-bottom: 20px; }}
    .lead-title {{ font-size: 18px; font-weight: bold; color: #ffffff; margin-bottom: 12px; }}
    .field {{ margin-bottom: 8px; font-size: 14px; }}
    .label {{ color: #8b93a9; font-weight: 500; display: inline-block; width: 140px; }}
    .val {{ color: #ffffff; font-weight: 600; }}
    .val-gold {{ color: #f0d97a; font-weight: 700; }}
    .action-box {{ background: rgba(139,92,246,0.12); border-left: 4px solid #8b5cf6; padding: 14px; border-radius: 4px; margin-top: 16px; font-size: 14px; }}
    .footer {{ background: #0a0c13; padding: 16px 24px; text-align: center; font-size: 12px; color: #5b6378; border-top: 1px solid rgba(255,255,255,0.06); }}
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
        <div class="lead-title">{nombre}</div>
        <div class="field"><span class="label">Teléfono:</span> <span class="val">{telefono}</span></div>
        <div class="field"><span class="label">Empresa:</span> <span class="val">{empresa}</span></div>
        <div class="field"><span class="label">Interés:</span> <span class="val-gold">{motivo}</span></div>
        <div class="field"><span class="label">Valor Estimado:</span> <span class="val-gold">{valor}</span></div>
        <div class="field"><span class="label">Duración:</span> <span class="val">{duracion}</span></div>
        <div class="field"><span class="label">Registro (LUX):</span> <span class="val">{fecha_lux}</span></div>
      </div>

      <div style="font-size: 14px; line-height: 1.5; color: #c3cadb; margin-bottom: 16px;">
        <strong>Resumen de la Conversación:</strong><br>
        {resumen}
      </div>

      <div class="action-box">
        <strong style="color: #a78bfa;">Próximo Paso Sugerido:</strong><br>
        {proximo_paso}
      </div>
    </div>
    <div class="footer">
      WELUX Events S.à r.l. · 128 Route d'Arlon, L-1150 Luxembourg · Centralita Telefónica con Inteligencia Artificial
    </div>
  </div>
</body>
</html>"""

        return text_body, html_body

    async def send_post_call_notification(self, call_data: Dict[str, Any]) -> Dict[str, Any]:
        """Envía el email post-llamada con fallback seguro si SMTP no está configurado."""
        subject = f"[La Centralita] Nueva llamada de {call_data.get('lead', {}).get('nombre') or 'Cliente'} · {call_data.get('lead', {}).get('motivo') or 'Consulta'}"
        text_content, html_content = self.build_email_content(call_data)

        if not self.smtp_host or not self.smtp_user:
            logger.info("Credenciales SMTP no configuradas. Notificación post-llamada preparada y registrada en logs.")
            return {
                "status": "QUEUED_NO_SMTP",
                "recipient": self.recipient,
                "subject": subject,
                "summary": text_content[:150],
            }

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.smtp_from
            msg["To"] = self.recipient

            part1 = MIMEText(text_content, "plain", "utf-8")
            part2 = MIMEText(html_content, "html", "utf-8")
            msg.attach(part1)
            msg.attach(part2)

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.smtp_from, [self.recipient], msg.as_string())

            logger.info("Notificación post-llamada enviada exitosamente a %s", self.recipient)
            return {"status": "SENT", "recipient": self.recipient, "subject": subject}
        except Exception as e:
            logger.error("Error al enviar email post-llamada: %s", e)
            return {"status": "ERROR", "error": str(e), "recipient": self.recipient}
