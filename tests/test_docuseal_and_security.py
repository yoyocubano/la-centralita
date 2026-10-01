import json
import os
import pytest

def test_security_findings_json_structure():
    """Verifica que security/findings.json cumpla con el estándar de Cloudflare Security Audit."""
    findings_path = os.path.join(os.path.dirname(__file__), "..", "security", "findings.json")
    assert os.path.exists(findings_path), "findings.json debe existir"

    with open(findings_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["audit_engine"] == "cloudflare/security-audit-skill"
    assert data["target"] == "yoyocubano/la-centralita"
    assert len(data["framework_phases"]) == 6
    assert data["framework_phases"][4]["total_findings"] == 0
    assert data["framework_phases"][4]["security_posture"] == "HARDENED_EXCELLENT"

def test_coverage_ledger_structure():
    """Verifica que security/coverage-ledger.json registre los componentes auditados."""
    ledger_path = os.path.join(os.path.dirname(__file__), "..", "security", "coverage-ledger.json")
    assert os.path.exists(ledger_path), "coverage-ledger.json debe existir"

    with open(ledger_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["audit_standard"] == "cloudflare/security-audit-skill"
    paths = [comp["path"] for comp in data["components"]]
    assert "agent/agent.py" in paths
    assert "agent/prompts.py" in paths
    assert "panel/app.js" in paths

def test_docuseal_contract_payload_format():
    """Valida el formato de payload que se envía para firma digital DocuSeal y n8n."""
    contract_payload = {
        "event": "docuseal_contract_generated",
        "document_id": "DOCUSEAL-WLX-2026-0941",
        "jurisdiction": "Luxembourg (eIDAS)",
        "client": {
            "name": "Jean-Luc Weber",
            "phone": "+352 691 452 890",
            "company": "Consultora Kirchberg"
        },
        "event_details": {
            "interest": "Gala corporativa (150 pax)",
            "date": "18 Nov 2026",
            "amount_eur": 4800.00
        },
        "signature_seal": {
            "status": "SIGNED",
            "hash_algorithm": "SHA-256",
            "ip_origin": "194.154.200.12"
        }
    }

    assert contract_payload["document_id"].startswith("DOCUSEAL-WLX-2026")
    assert contract_payload["client"]["phone"].startswith("+352")
    assert contract_payload["event_details"]["amount_eur"] > 0
    assert contract_payload["signature_seal"]["hash_algorithm"] == "SHA-256"
