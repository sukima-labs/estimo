"""English reports (14-10-2026): no Spanish may leak into them."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from mikiwame.models import Level, Signal, Verdict
from mikiwame.report import render_report
from mikiwame.rubric import ASKS, POINTS
from mikiwame.rubric_en import ASKS_EN, POINTS_EN
from mikiwame.voters.base import signal

SRC = Path(__file__).resolve().parents[1] / "src" / "mikiwame"

# Spanish names an English speaker meets in Spain, kept on purpose.
NAMES = (
    "Comunidad de Madrid", "Agencia de Vivienda Social", "Alcalá de Henares", "nota simple",
    "vivienda habitual", "Anuncio sospechoso", "Catastro", "fianza",
)  # fmt: skip
SPANISH = re.compile(
    r"\b(que|de|la|el|los|las|una|por|para|con|pagues|vivienda|anuncio|piso|señal|antes)\b|[ñ¿¡]",
    re.I,
)


def spanish_left(text: str) -> list[str]:
    text = re.sub(r"«[^»]*»", "", text)  # quotes are the advertiser's own words
    text = re.sub(r"<[^>]+>", " ", text)
    for name in NAMES:
        text = text.replace(name, "")
    return SPANISH.findall(text)


def test_every_signal_has_its_english():
    assert set(POINTS_EN) == set(POINTS)
    for code, p in POINTS.items():
        assert (POINTS_EN[code][1] is None) == (p.advice is None), code
    assert set(ASKS_EN) == set(ASKS)


def test_every_message_built_with_case_data_has_its_english():
    """A voter that writes its own message must write its English too."""
    missing = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "signal":
                kws = {k.arg for k in node.keywords}
                if "message" in kws and "message_en" not in kws:
                    missing.append(f"{path.name}:{node.lineno}")
    assert not missing


def _verdict(level: Level, codes: list[str]) -> Verdict:
    return Verdict(
        level=level,
        score=0.5,
        confidence=0.8,
        signals=[signal(c, quote="te pido una señal de 700 €") for c in codes],
    )


def test_english_reports_have_no_spanish_left():
    risk = [c for c, p in POINTS.items() if p.weight > 0]
    good = [c for c, p in POINTS.items() if p.weight < 0]
    for level in Level:
        for i in range(0, len(risk), 4):
            report = render_report(
                _verdict(level, risk[i : i + 4] + good[:2]), channel="whatsapp", lang="en"
            )
            assert not spanish_left(report), (level, risk[i : i + 4], spanish_left(report))
    insufficient = _verdict(Level.AMBER, [])
    insufficient.insufficient_info = True
    assert not spanish_left(render_report(insufficient, lang="en"))


def test_english_report_uses_the_voters_english_and_keeps_quotes():
    s = Signal(
        code="iban_foreign",
        weight=0.45,
        message="La cuenta para pagar es de otro país (Italia).",
        message_en="The account to pay into is in another country (Italy).",
        quote="Mi cuenta es IT60…",
    )
    v = Verdict(level=Level.RED, score=0.6, confidence=0.8, signals=[s])
    report = render_report(v, market_reference="in Alcalá de Henares, a flat …", lang="en")
    assert "(Italy)" in report and "«Mi cuenta es IT60…»" in report
    assert "High risk of a scam" in report and "Reference" in report
    # Spanish stays the default.
    assert "Riesgo alto de estafa" in render_report(v)


def test_a_message_without_english_falls_back_to_the_generic_line():
    s = Signal(code="bargain_price", weight=0.15, message="El precio (500 €) está muy bajo…")
    v = Verdict(level=Level.AMBER, score=0.2, confidence=0.6, signals=[s])
    report = render_report(v, lang="en")
    assert "The price is very low for what is offered." in report
    assert "500 €" not in report
