"""The invented example case («Ver un ejemplo» in the bot and on the website).

Someone who arrives without a case of their own sees what they would get, at no cost: no
analysis is charged, no model is called and nothing is stored. The report is the pipeline's
real output for this case, saved by `python -m evaluation.example`.

The data are made up: the IBAN is the standard German example from the IBAN documentation, and
there is no name or phone, so the example can never point at a real person.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

DATA_FILE = Path(__file__).with_name("example.json")

CASE = {
    "es": (
        "Anuncio: Piso de 2 habitaciones en Chamberí, Madrid. Exterior, reformado y amueblado. "
        "750 €/mes.\n\n"
        "Yo: Hola, me interesa el piso de Chamberí. ¿Cuándo puedo ir a verlo?\n"
        "Anunciante: Hola. Soy médico y me han trasladado a Londres, así que no puedo enseñarlo "
        "en persona. Busco a alguien de confianza que lo cuide.\n"
        "Anunciante: Para reservarlo me envías la fianza y el primer mes (1.500 €) por "
        "transferencia a mi cuenta DE89 3704 0044 0532 0130 00 y te mando las llaves y el "
        "contrato firmado por mensajería. Llegan en 48 horas.\n"
        "Anunciante: Tengo más interesados, así que tendría que ser hoy."
    ),
    "en": (
        "Listing: Bright 2-bedroom flat in Chamberí, Madrid. Refurbished and furnished. "
        "750 €/month.\n\n"
        "Me: Hi, I'm an Erasmus student and I'm interested in your flat in Chamberí. Can I come "
        "and see it this week?\n"
        "Advertiser: Hello! I'm a doctor and I've been transferred to London, so I can't show it "
        "in person. I'm looking for someone trustworthy to look after it.\n"
        "Advertiser: To book it, send me the deposit and the first month (1,500 €) by bank "
        "transfer to my account DE89 3704 0044 0532 0130 00 and I'll send you the keys and the "
        "signed contract by courier. They arrive in 48 hours.\n"
        "Advertiser: Other people are interested, so it would have to be today."
    ),
}


@cache
def _data() -> dict:
    return json.loads(DATA_FILE.read_text())


def example(lang: str = "es") -> tuple[str, str, str]:
    """(invented case, its level, its report as Telegram HTML) in Spanish or English."""
    lang = "en" if lang == "en" else "es"
    data = _data()
    return CASE[lang], data["level"][lang], data["report"][lang]
