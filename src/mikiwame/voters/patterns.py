"""Deterministic pattern voter: blatant phrases no model is allowed to talk us out of.

It is the backstop against prompt injection: if a message persuades the text LLM that
everything is fine, these regexes still see "ignora las instrucciones" or "Western Union".
Kept narrow on purpose; every pattern here must be one a legitimate landlord would not write.
"""

from __future__ import annotations

import re
import unicodedata

from mikiwame.extract.redact import Redactor
from mikiwame.extract.text import url_domain
from mikiwame.models import Case, Signal, Vote
from mikiwame.voters.base import score_from, signal


def fold(text: str) -> str:
    """Lowercase and strip accents so patterns stay simple."""
    return fold_with_map(text)[0]


def fold_with_map(text: str) -> tuple[str, list[int]]:
    """Folded text plus, for each folded char, the index of its source char in `text`."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        for d in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(d):
                continue
            for f in d.casefold():
                out.append(f)
                index.append(i)
    return "".join(out), index


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.M)


# Instructions aimed at an AI. Kept narrow on purpose (review 2026-10-01): what a USER naturally
# types at the bot ("evalúa si es seguro", "responde si es legítimo") must never match, or we
# end up accusing the landlord of manipulating us.
_AI_INSTRUCTIONS = _rx(
    r"ignor\w*\s+(?:\w+\s+){0,3}(?:instrucciones|indicaciones|ordenes)",
    r"ignore\s+(?:\w+\s+){0,3}(?:instructions|prompts?)",
    r"(?:nota|mensaje|aviso|instruccion(?:es)?)\s+(?:\w+\s+){0,2}para\s+(?:el\s+|la\s+|los\s+|las\s+)?"
    r"(?:asistente|ia\b|inteligencia artificial|sistemas?\s+(?:de|autom)\w*|bot\b|chatbot|"
    r"verificador\w*|modelo de lenguaje|revisor\w*\s+autom\w*)",
    r"(?:clasifica|marca|califica|etiqueta)\w*\s+(?:\w+\s+){0,4}como\s+"
    r"(?:seguro|verde|legitimo|fiable|no fraudulento|riesgo bajo|bajo riesgo)",
    r"^\s*(?:system|assistant)\s*:",
)
_UNTRACEABLE = _rx(
    r"western\s*union", r"money\s*gram", r"\bria\s+money", r"\bmtcn\b", r"paysafecard",
    r"tarjetas?\s+regalo", r"gift\s*cards?", r"\bbitcoin\b", r"\bcripto(?:moneda)?s?\b",
    r"\busdt\b",
)  # fmt: skip
_KEYS_BY_POST = _rx(
    r"(?:envi|mand)\w*\s+(?:\w+\s+){0,3}llaves?\s+(?:\w+\s+){0,3}"
    r"(?:correo|mensajer|dhl|seur|mrw|ups|fedex|gls|nacex|paquete)",
    r"llaves?\s+(?:\w+\s+){0,3}(?:por|con)\s+(?:correo|mensajer|dhl|seur|mrw|ups|fedex|gls|nacex)",
    r"(?:airbnb|booking)\s+(?:\w+\s+){0,6}(?:llaves?)",
)
_PLATFORM_PAYMENT = _rx(
    r"(?:pag\w*|fianza|transferencia|dinero)\s+(?:\w+\s+){0,6}(?:a|de|con|mediante|por)\s+"
    r"(?:airbnb|booking)",
    r"(?:airbnb|booking|idealista|fotocasa)\s+(?:\w+\s+){0,6}(?:custodia|garantiza|protege|"
    r"devuelve el dinero|te devuelve)",
    r"pago\s+seguro\s+(?:de\s+)?(?:idealista|fotocasa|airbnb|booking)",
)
_VIDEO_ONLY = _rx(
    r"(?:solo|unicamente|exclusivamente)\s+(?:\w+\s+){0,4}videollamada",
    r"visitas?\s+(?:\w+\s+){0,3}(?:son|es|por)\s+(?:\w+\s+){0,2}videollamada",
)
_TOURIST_LICENSE = _rx(r"\bvt-?\d{3,}", r"\bvut\b", r"licencia\s+turistica", r"uso\s+turistico")
_LONG_TERM = _rx(r"larga\s+duracion", r"larga\s+estancia")
# No legitimate landlord needs a selfie holding your ID: that is identity-theft material.
_ID_SELFIE = _rx(
    r"selfie\s+(?:\w+\s+){0,4}(?:dni|nie|pasaporte|documento)\b",
    r"(?:foto|imagen)\s+(?:\w+\s+){0,3}sujetando\s+(?:\w+\s+){0,2}(?:dni|nie|pasaporte)\b",
)
_ID_SENT = _rx(
    r"(?:te\s+)?(?:mando|envio|adjunto|paso)\s+(?:\w+\s+){0,3}(?:mi\s+)(?:dni|pasaporte|nie)\b",
    r"\[imagen:\s*dni\b",
)

# A match right after an explicit denial or warning is not a signal: "nunca te pediremos pagar
# por Western Union", "cuidado con quien te mande las llaves por mensajería" (review 2026-10-01).
# Deliberately narrow: scammers reassure with the same vocabulary ("no te preocupes, pagas por
# Western Union", "paga por Airbnb para evitar estafas"), so a bare "no" or a later "estafa"
# must not switch a signal off. Only a denial of the action itself, or a warning, before the
# match and in the same clause.
_DENIAL = re.compile(
    r"\b(?:nunca|jamas|"
    r"no\s+(?:te\s+|le\s+|os\s+)?(?:pedire\w*|pedimos|pediremos|aceptamos|aceptare\w*|usamos|"
    r"usare\w*|hacemos|haremos|enviamos|enviare\w*|mandamos|mandare\w*|cobramos|cobrare\w*|"
    r"trabajamos\s+con|admitimos|necesitas|hace\s+falta|se\s+hace)|"
    r"cuidado\s+con|desconfi\w*\s+de|evita\w*|ojo\s+con|ni\s+se\s+te\s+ocurra)\b"
)
_CLAUSE_END = ".;:!?\n,"


def _negated(folded: str, m: re.Match[str]) -> bool:
    start = max(folded.rfind(ch, 0, m.start()) for ch in _CLAUSE_END) + 1
    before = folded[max(start, m.start() - 60) : m.start()]
    return bool(_DENIAL.search(before))


# Brands with their registrable domain label. A host whose registrable domain is exactly the
# brand (idealista.com, airbnb.co.uk) is official; a host that carries the brand as a separate
# token anywhere else (idealista-pagoseguro.com, pagos.idealista-seguro.net) imitates it. A
# plain substring does not count: "pisosalcala.es" is an agency, not a fake pisos.com, so
# generic words are not brands at all (review 2026-10-01).
_BRANDS = ("idealista", "fotocasa", "airbnb", "booking", "milanuncios", "habitaclia", "wallapop")
_SECOND_LEVEL = {"co", "com", "org", "net", "gob"}


def _registrable(host: str) -> str:
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def lookalike_domains(urls: list[str]) -> list[str]:
    out = []
    for u in urls:
        host = url_domain(u)
        if not host:
            continue
        registrable = _registrable(host)
        if registrable.split(".")[0] in _BRANDS:
            continue  # the brand's own domain, any TLD or subdomain
        tokens = set(re.split(r"[.\-_]", host))
        if tokens & set(_BRANDS):
            out.append(host)
    return out


# A full stop ends a sentence only before whitespace or the end: "1.100 €" or "idealista.com"
# must not cut a quote in half (Telegram test 2026-10-08 quoted "100 € (fianza…").
_SENTENCE_END = re.compile(r"[!?\n]|\.(?=\s|$)")


def _quote(text: str, index: list[int], m: re.Match[str]) -> str:
    """The sentence of the original text around a match found on the folded text."""
    start, end = index[m.start()], index[m.end() - 1] + 1
    # Search the whole text: with endpos the lookahead's "$" would match at the cut.
    left = max((e.start() for e in _SENTENCE_END.finditer(text) if e.start() < start), default=-1)
    after = _SENTENCE_END.search(text, end)
    right = after.start() + 1 if after else len(text)
    sentence = text[left + 1 : right].strip()
    return sentence if len(sentence) <= 200 else text[start:end]


def advertiser_text(case: Case) -> str:
    """What the advertiser (or the ad) wrote. The user's own chat messages are never evidence:
    "¿Y si voy en persona?" or "no uso Western Union" are the user's words (review 2026-10-01).
    Messages are separated by blank lines so no pattern spans two of them."""
    parts = [case.listing_text, case.pasted_text] + [
        m.text for m in case.chat if m.sender != "user"
    ]
    return "\n\n".join(p for p in parts if p and p.strip())


class PatternVoter:
    name = "patterns"

    async def vote(self, case: Case) -> Vote:
        return self.vote_sync(case)

    def vote_sync(self, case: Case) -> Vote:
        text = advertiser_text(case)
        folded, index = fold_with_map(text)
        signals: list[Signal] = []
        # Quotes end up in the report and in storage: same redaction as the text voter's.
        redactor = Redactor.for_case(case)

        def check(rx: re.Pattern[str], code: str, *, negatable: bool = True) -> None:
            for m in rx.finditer(folded):
                if negatable and _negated(folded, m):
                    continue
                signals.append(signal(code, quote=redactor.redact(_quote(text, index, m))))
                return

        check(_AI_INSTRUCTIONS, "ai_instructions", negatable=False)
        check(_UNTRACEABLE, "untraceable_payment")
        check(_KEYS_BY_POST, "keys_by_post")
        check(_PLATFORM_PAYMENT, "platform_payment")
        check(_VIDEO_ONLY, "no_visit")
        if _TOURIST_LICENSE.search(folded) and _LONG_TERM.search(folded):
            check(_TOURIST_LICENSE, "tourist_as_long_term", negatable=False)
        check(_ID_SELFIE, "docs_before_visit")
        if case.id_documents or _ID_SENT.search(folded):
            signals.append(signal("id_unprompted"))
        fakes = lookalike_domains(case.urls)
        if fakes:
            signals.append(
                signal(
                    "lookalike_domain",
                    quote=fakes[0],
                    message=f"Aparece un enlace a «{fakes[0]}», que imita a un portal o plataforma "
                    "conocida pero no es su web oficial.",
                    message_en=f"There is a link to «{fakes[0]}», which imitates a well-known "
                    "portal or platform but is not its official website.",
                )
            )
        if not signals:
            return Vote.abstain(self.name)
        return Vote(
            voter=self.name,
            score=score_from(signals),
            confidence=0.8,
            evidence=[s.message for s in signals],
            hard_flag=any(s.code in ("ai_instructions", "lookalike_domain") for s in signals),
            signals=signals,
        )
