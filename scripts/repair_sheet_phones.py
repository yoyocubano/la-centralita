#!/usr/bin/env python3
"""Repara la columna TELEFONO del Google Sheet 'La Centralita — Leads'.

Problema histórico:
  - Filas escritas con valueInputOption=USER_ENTERED (workflow n8n antiguo):
    "+352 691 452 890" se interpretó como fórmula -> la celda muestra #ERROR!.
    El texto original sigue guardado como fórmula ("=+352 691 452 890" o similar).
  - Filas escritas con apóstrofe + RAW (fix intermedio): el apóstrofe quedó
    como carácter literal ("'+352 ...").

Este script lee la columna D en modo FORMULA y FORMATTED_VALUE, reconstruye el
número original y lo reescribe como texto literal (RAW).

Uso (por defecto NO escribe nada):
    python scripts/repair_sheet_phones.py            # dry-run: lista lo que cambiaría
    python scripts/repair_sheet_phones.py --apply    # aplica los cambios

Requiere GOOGLE_SHEETS_CREDENTIALS_JSON y GOOGLE_SHEET_ID en el entorno (.env).
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.sheets_sync import SHEETS_API, SHEET_ERROR_VALUES, GoogleSheetsSync  # noqa: E402

PHONE_CHARS = re.compile(r"^[+0-9 ().-]{5,30}$")


def recover_phone(formatted: str, formula: str) -> str | None:
    """Devuelve el teléfono reparado, o None si la celda no necesita cambios."""
    formatted = (formatted or "").strip()
    formula = (formula or "").strip()

    if formatted.upper() in SHEET_ERROR_VALUES:
        candidate = formula.lstrip("=").strip()
        if candidate.startswith("'"):
            candidate = candidate[1:]
        return candidate if PHONE_CHARS.match(candidate) else None

    if formatted.startswith("'") and PHONE_CHARS.match(formatted[1:]):
        return formatted[1:]
    return None


def fetch_column(sync: GoogleSheetsSync, token: str, render: str) -> list:
    url = f"{SHEETS_API}/{sync.sheet_id}/values/{sync._range('D2:D')}?valueRenderOption={render}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8")).get("values", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Escribe los cambios en el Sheet")
    args = parser.parse_args()

    sync = GoogleSheetsSync()
    if not sync.is_configured:
        print("ERROR: faltan GOOGLE_SHEETS_CREDENTIALS_JSON / GOOGLE_SHEET_ID.", file=sys.stderr)
        return 2
    token = sync._get_access_token()
    if not token:
        print("ERROR: no se pudo obtener token de la cuenta de servicio.", file=sys.stderr)
        return 2

    formatted = fetch_column(sync, token, "FORMATTED_VALUE")
    formulas = fetch_column(sync, token, "FORMULA")

    updates = []
    for idx in range(max(len(formatted), len(formulas))):
        f_val = formatted[idx][0] if idx < len(formatted) and formatted[idx] else ""
        r_val = formulas[idx][0] if idx < len(formulas) and formulas[idx] else ""
        fixed = recover_phone(str(f_val), str(r_val))
        if fixed is not None:
            row_number = idx + 2
            updates.append({"range": f"'{sync.tab_name}'!D{row_number}", "values": [[fixed]]})
            # Se muestra solo el número de fila, no el teléfono (PII fuera de la consola).
            print(f"Fila {row_number}: reparable ({'#ERROR!' if str(f_val).startswith('#') else 'apóstrofe literal'})")

    if not updates:
        print("Nada que reparar.")
        return 0
    if not args.apply:
        print(f"{len(updates)} celda(s) reparables. Ejecuta con --apply para escribir.")
        return 0

    body = json.dumps({"valueInputOption": "RAW", "data": updates}).encode("utf-8")
    req = urllib.request.Request(
        f"{SHEETS_API}/{sync.sheet_id}/values:batchUpdate",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"Reparadas {len(updates)} celda(s). HTTP {resp.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
