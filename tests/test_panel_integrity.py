"""Integridad del panel: cada clase usada tiene estilo, cada data-action existe, sin handlers inline."""

import re
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent / "panel"
HTML = (PANEL / "index.html").read_text(encoding="utf-8")
JS = (PANEL / "app.js").read_text(encoding="utf-8")
CSS = re.sub(r"/\*.*?\*/", "", (PANEL / "style.css").read_text(encoding="utf-8"), flags=re.S)


def used_classes() -> set[str]:
    used = set()
    for m in re.finditer(r'class="([^"]+)"', HTML):
        used |= set(m.group(1).split())
    for m in re.finditer(r'className\s*=\s*([`"\'])(.*?)\1', JS):
        val = m.group(2)
        used |= set(re.findall(r"['\"]([a-z][\w-]*)['\"]", val))
        used |= {t for t in re.sub(r"\$\{[^}]*\}", " ", val).split() if re.fullmatch(r"[a-z][\w-]*", t)}
    used |= set(re.findall(r'classList\.(?:add|remove|toggle)\(\s*["\']([\w-]+)', JS))
    return used


def defined_classes() -> set[str]:
    selectors = CSS
    while re.search(r"\{[^{}]*\}", selectors):
        selectors = re.sub(r"\{[^{}]*\}", " ", selectors)
    return set(re.findall(r"\.(-?[_a-zA-Z][\w-]*)", selectors))


def test_every_used_class_is_styled():
    missing = sorted(used_classes() - defined_classes())
    assert missing == [], f"Clases sin estilo: {missing}"


def test_hidden_attribute_wins_over_display_rules():
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", CSS)


def test_no_inline_event_handlers_or_styles():
    assert not re.search(r"\son[a-z]+=\"", HTML)
    assert 'style="' not in HTML


def test_all_data_actions_are_registered():
    registry = re.search(r"const ACTIONS = \{(.*?)\};", JS, re.S).group(1)
    registered = {x.strip() for x in registry.split(",") if x.strip()}
    actions = set(re.findall(r'data-action="(\w+)"', HTML))
    assert actions <= registered, actions - registered


def test_every_referenced_id_exists():
    ids = set(re.findall(r'\$\("(\w+)"\)', JS)) | set(re.findall(r'setText\("(\w+)"', JS))
    missing = sorted(i for i in ids if f'id="{i}"' not in HTML)
    assert missing == []


def test_no_third_party_fonts():
    assert "fonts.googleapis.com" not in HTML
