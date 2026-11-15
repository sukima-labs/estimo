"""Verdict → short Telegram report (HTML parse mode), in plain Spanish or English.

Never states that someone is a scammer: it describes patterns. Quotes come from hostile input,
so everything interpolated is HTML-escaped.
"""

from __future__ import annotations

import html

from mikiwame.models import CHANNEL_EN, CHANNEL_ES, Level, Signal, Verdict
from mikiwame.rubric import ASKS, DEFAULT_ASK, POINTS
from mikiwame.rubric_en import ASKS_EN, DEFAULT_ASK_EN, POINTS_EN

MAX_RISK = 5
MAX_REASSURING = 2
MAX_ADVICE = 3

HEADLINE = {
    Level.RED: ("🔴", "Riesgo alto de estafa", "Lo que nos has enviado coincide con patrones "
                "típicos de estafa en alquileres."),
    Level.AMBER: ("🟠", "Precaución", "Hay señales que conviene comprobar antes de pagar nada."),
    Level.GREEN: ("🟢", "Sin señales claras de estafa", "En lo que nos has enviado no vemos las "
                  "señales típicas de estafa."),
}  # fmt: skip
NO_SIGNALS_YET = "No vemos señales de estafa, pero tampoco tenemos bastante para descartarla."

DEFAULT_ADVICE = {
    Level.RED: [
        "No pagues nada, ni reserva ni señal, antes de visitar la vivienda y firmar un contrato.",
        "Si ya has pagado, guarda el justificante y avisa a tu banco y a la Policía cuanto antes.",
    ],
    Level.AMBER: [
        "No pagues nada, ni reserva ni señal, antes de visitar la vivienda y firmar un contrato.",
        # Long-press → Lens only works in Chrome and the Google app, not in Safari or in the
        # portal apps where people actually see the photos (2026-10-01): screenshot + Lens does.
        "Comprueba si las fotos son robadas: haz una captura de una foto del anuncio y búscala "
        "con Google Lens (el icono de la cámara en la app de Google o en Chrome). Si aparece en "
        "otro anuncio, en Airbnb o en Booking, desconfía.",
    ],
    Level.GREEN: [
        "Aun así, no pagues antes de visitar la vivienda y firmar el contrato.",
        "La fianza se deposita en la Agencia de Vivienda Social de la Comunidad de Madrid: "
        "puedes pedir el justificante.",
    ],
}

REPORT_TO_PORTAL = (
    "📣 Si lo viste en un portal (idealista, Fotocasa…), denuncia el anuncio desde la propia "
    "ficha: lo revisan y pueden avisar a otras personas que lo hayan contactado."
)

DISCLAIMER = (
    "Este informe describe patrones que aparecen en estafas conocidas; no afirma que nadie sea "
    "un estafador."
)

# --- English (14-10-2026). Same structure, same rules: patterns, never «this is a scammer». ---
HEADLINE_EN = {
    Level.RED: ("🔴", "High risk of a scam", "What you sent matches typical rental scam "
                "patterns."),
    Level.AMBER: ("🟠", "Caution", "There are signs worth checking before you pay anything."),
    Level.GREEN: ("🟢", "No clear signs of a scam", "In what you sent we do not see the typical "
                  "signs of a scam."),
}  # fmt: skip
NO_SIGNALS_YET_EN = "We see no signs of a scam, but not enough to rule one out either."
DEFAULT_ADVICE_EN = {
    Level.RED: [
        "Do not pay anything, not even a booking fee or deposit, before visiting the property "
        "and signing a contract.",
        "If you have already paid, keep the receipt and tell your bank and the police as soon "
        "as you can.",
    ],
    Level.AMBER: [
        "Do not pay anything, not even a booking fee or deposit, before visiting the property "
        "and signing a contract.",
        "Check whether the photos are stolen: take a screenshot of a photo from the listing and "
        "search it with Google Lens (the camera icon in the Google app or in Chrome). If it "
        "shows up in another listing, on Airbnb or on Booking, be wary.",
    ],
    Level.GREEN: [
        "Even so, do not pay before visiting the property and signing the contract.",
        "In the Comunidad de Madrid the deposit («fianza») is lodged with the Agencia de "
        "Vivienda Social: you can ask for the receipt.",
    ],
}
REPORT_TO_PORTAL_EN = (
    "📣 If you saw it on a portal (idealista, Fotocasa…), report the listing from its own page: "
    "they review it and may warn other people who contacted it."
)
DISCLAIMER_EN = (
    "This report describes patterns found in known scams; it does not claim that anyone is a "
    "scammer."
)
# The rest of the report's own words, by language.
WORDS = {
    "es": {
        "insufficient": "Con lo que nos has enviado no podemos valorar bien el riesgo. Si tienes "
        "la conversación con el anunciante, mándanos capturas del chat.",
        "consider": "A tener en cuenta",
        "why": "Por qué",
        "for": "A favor",
        "softened": "Una segunda revisión encontró detalles que podrían tener una explicación "
        "legítima, así que lo dejamos en precaución.",
        "todo": "Qué hacer ahora",
        "listing": "El anuncio",
        "reference": "Referencia",
        "ask_red": "<b>Si quieres confirmarlo, mándale esto</b> (toca para copiar)",
        "ask": "<b>Mándale esto</b> (toca para copiar)",
        "tail_red": "Pase lo que pase, no pagues nada antes de ver la vivienda y firmar.",
        "tail": "Mándame la captura de lo que conteste (o dime si no contesta) y lo analizo junto "
        "con lo anterior.",
        "seen": "Conversación: {channel}. ",
        "confidence": "Confianza: {word}.",
        "high": "alta",
        "medium": "media",
        "low": "baja",
    },
    "en": {
        "insufficient": "With what you sent we cannot properly assess the risk. If you have the "
        "conversation with the advertiser, send us screenshots of the chat.",
        "consider": "Worth noting",
        "why": "Why",
        "for": "In its favour",
        "softened": "A second review found details that could have a legitimate explanation, so "
        "we leave it at caution.",
        "todo": "What to do now",
        "listing": "The listing",
        "reference": "Reference",
        "ask_red": "<b>If you want to confirm it, send them this</b> (tap to copy)",
        "ask": "<b>Send them this</b> (tap to copy)",
        "tail_red": "Whatever happens, do not pay anything before seeing the property and signing.",
        "tail": "Send me a screenshot of their answer (or tell me if they do not answer) and I "
        "will analyse it together with the rest.",
        "seen": "Conversation: {channel}. ",
        "confidence": "Confidence: {word}.",
        "high": "high",
        "medium": "medium",
        "low": "low",
    },
}


def _confidence_word(c: float, lang: str = "es") -> str:
    w = WORDS[lang]
    return w["high"] if c >= 0.75 else w["medium"] if c >= 0.5 else w["low"]


def message_of(s: Signal, lang: str = "es") -> str:
    """The signal's line in the report's language. English: the voter's own English when the
    message carries case data, else the code's generic line."""
    if lang != "en":
        return s.message
    if s.message_en:
        return s.message_en
    en = POINTS_EN.get(s.code)
    return en[0] if en else s.message


def advice_of(s: Signal, lang: str = "es") -> str | None:
    if lang != "en" or not s.advice:
        return s.advice
    en = POINTS_EN.get(s.code)
    # Only the rubric's advice exists; anything else would be a Spanish line left over.
    if en and POINTS.get(s.code) and s.advice == POINTS[s.code].advice:
        return en[1]
    return None


def _line(s: Signal, lang: str = "es", quoted: set[str] | None = None) -> str:
    out = f"• {html.escape(message_of(s, lang))}"
    q = s.quote if not s.quote or len(s.quote) <= 110 else s.quote[:107].rstrip() + "…"
    # Two signals often rest on the same sentence ("no puedo enseñarlo en persona" is both no
    # visit and a refused check): quote it under the first one only.
    if q and (quoted is None or q not in quoted):
        out += f"\n   <i>«{html.escape(q)}»</i>"
        if quoted is not None:
            quoted.add(q)
    return out


def suggested_asks(
    verdict: Verdict, shown: list[Signal], lang: str = "es"
) -> list[tuple[str, str]]:
    """(phrase to send, what gives the scam away), most important signal first."""
    asks_by_code, default = (ASKS_EN, DEFAULT_ASK_EN) if lang == "en" else (ASKS, DEFAULT_ASK)
    if verdict.level == Level.GREEN:
        # Nothing to corner: the one check every genuine landlord passes (seen on a real case,
        # 2026-10-01: a green suggested "let's keep chatting on the portal" to an agency that
        # simply wanted to phone).
        return [default]
    asks: list[tuple[str, str]] = []
    for s in shown:
        ask = asks_by_code.get(s.code)
        if ask and ask not in asks:
            asks.append(ask)
    if not asks:
        asks.append(default)
    return asks[: 1 if verdict.level == Level.RED else 2]


def _asks_block(verdict: Verdict, shown: list[Signal], lang: str = "es") -> str:
    # <code> makes the phrase tap-to-copy in Telegram; the tell under it is only for the user.
    w = WORDS[lang]
    lines = "\n".join(
        f"<code>{html.escape(phrase)}</code>\n   <i>{html.escape(tell)}</i>"
        for phrase, tell in suggested_asks(verdict, shown, lang)
    )
    if verdict.level == Level.RED:
        head, tail = w["ask_red"], w["tail_red"]
    else:
        head, tail = w["ask"], w["tail"]
    return f"{head}\n{lines}\n{tail}"


def render_report(
    verdict: Verdict,
    channel: str = "unknown",
    market_facts: str = "",
    market_reference: str = "",
    lang: str = "es",
) -> str:
    """lang "en": English, with market_facts and market_reference already in English."""
    en = lang == "en"
    w = WORDS["en" if en else "es"]
    lang = "en" if en else "es"
    icon, title, lead = (HEADLINE_EN if en else HEADLINE)[verdict.level]
    risk = [s for s in verdict.signals if s.weight > 0][:MAX_RISK]
    n_reassuring = 3 if verdict.level == Level.GREEN else MAX_REASSURING
    reassuring = [s for s in verdict.signals if s.weight < 0][:n_reassuring]
    if verdict.level == Level.AMBER and verdict.insufficient_info and not risk:
        # Amber only for lack of material: "hay señales" would be false (05-10-2026).
        lead = NO_SIGNALS_YET_EN if en else NO_SIGNALS_YET
    parts = [f"{icon} <b>{title}</b>", html.escape(lead)]

    if verdict.insufficient_info:
        parts.append(w["insufficient"])
    quoted: set[str] = set()
    if risk:
        # A green with minor signals must not read as "here is why it is a scam".
        heading = w["consider"] if verdict.level == Level.GREEN else w["why"]
        parts.append(f"<b>{heading}</b>\n" + "\n".join(_line(s, lang, quoted) for s in risk))
    if reassuring:
        lines = "\n".join(_line(s, lang, quoted) for s in reassuring)
        parts.append(f"<b>{w['for']}</b>\n" + lines)
    if verdict.softened_by_adversary:
        parts.append(w["softened"])
    defaults = DEFAULT_ADVICE_EN if en else DEFAULT_ADVICE

    # Level defaults first (on red they include "if you already paid, call your bank"), then
    # signal-specific advice, capped as a whole (review 2026-10-01: the red advice got dropped).
    advice: list[str] = list(defaults[verdict.level][:1])
    if verdict.level == Level.RED:
        advice += defaults[Level.RED][1:]
    for s in risk + reassuring:
        if len(advice) >= MAX_ADVICE + (1 if verdict.level == Level.RED else 0):
            break
        a = advice_of(s, lang)
        if a and a not in advice:
            advice.append(a)
    for a in defaults[verdict.level]:
        if len(advice) >= MAX_ADVICE:
            break
        if a not in advice:
            advice.append(a)
    parts.append(f"<b>{w['todo']}</b>\n" + "\n".join(f"• {html.escape(a)}" for a in advice))
    if verdict.level == Level.RED:
        # Portals catch fraud largely through reports, and warn whoever contacted the ad once
        # they act (research 01-10-2026). Red only: an amber may be a
        # real landlord.
        parts.append(html.escape(REPORT_TO_PORTAL_EN if en else REPORT_TO_PORTAL))
    if market_facts:
        parts.append(f"📌 <b>{w['listing']}</b>: {html.escape(market_facts)}.")
    # A bargain_price line already quotes the reference: say it once.
    if market_reference and not any(
        market_reference in message_of(s, lang) for s in verdict.signals
    ):
        ref = market_reference[0].upper() + market_reference[1:]
        parts.append(f"💶 <b>{w['reference']}</b>: {html.escape(ref)}.")
    parts.append(_asks_block(verdict, risk + reassuring, lang))
    channels = CHANNEL_EN if en else CHANNEL_ES
    seen = w["seen"].format(channel=channels[channel]) if channel in channels else ""
    confidence = w["confidence"].format(word=_confidence_word(verdict.confidence, lang))
    disclaimer = DISCLAIMER_EN if en else DISCLAIMER
    parts.append(f"<i>{seen}{confidence} {html.escape(disclaimer)}</i>")
    return "\n\n".join(parts)
