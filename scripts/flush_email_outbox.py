#!/usr/bin/env python3
"""Reenvía las notificaciones pendientes de data/email_outbox.jsonl.

Las notificaciones se acumulan ahí cuando SMTP no estaba configurado
(estado QUEUED_NO_SMTP) o falló (QUEUED_SMTP_ERROR). Tras configurar
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD en el .env:

    python scripts/flush_email_outbox.py            # muestra cuántas hay
    python scripts/flush_email_outbox.py --send     # las envía; conserva las que fallen
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.email_notify import OUTBOX_FILE, EmailNotifier  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="Enviar realmente los correos")
    args = parser.parse_args()

    if not OUTBOX_FILE.exists():
        print("Bandeja de salida vacía.")
        return 0
    entries = [json.loads(line) for line in OUTBOX_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"{len(entries)} notificación(es) pendiente(s).")
    if not args.send or not entries:
        return 0

    notifier = EmailNotifier()
    if not notifier.is_configured:
        print("ERROR: SMTP no configurado (SMTP_HOST/SMTP_USER/SMTP_PASSWORD).", file=sys.stderr)
        return 2

    pending = []
    for entry in entries:
        try:
            notifier._send_smtp(entry["subject"], entry["text"], entry["html"])
            print(f"Enviada: {entry['queued_at']}")
        except Exception as exc:  # se conserva para el siguiente intento
            print(f"Fallo ({type(exc).__name__}): {entry['queued_at']}", file=sys.stderr)
            pending.append(entry)

    OUTBOX_FILE.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in pending), encoding="utf-8")
    print(f"Pendientes tras el envío: {len(pending)}")
    return 0 if not pending else 1


if __name__ == "__main__":
    raise SystemExit(main())
