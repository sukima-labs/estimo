"""Deterministic extraction of identifiers from free text: IBANs, phones, e-mails, URLs, prices.

Identifiers are never taken from an LLM's word: the vision model transcribes, and this module
finds and validates what is in the transcription.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import phonenumbers
from schwifty import IBAN
from schwifty.registry import get_iban_spec

# Country code + check digits + BBAN in uppercase letters/digits, optionally grouped by spaces,
# dashes or a line break (chat bubbles wrap long IBANs). Uppercase only, so trailing Spanish
# words ("a nombre de...") are not swallowed; over-matches are trimmed to the country length.
_IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}(?:(?:[ \-.\u00a0\u202f]|\r?\n)?[A-Z0-9]){10,32})\b")
_IBAN_SEP = " -.\u00a0\u202f\r\n"
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


def _ascii_upper(text: str) -> str:
    """Uppercase ASCII letters only: same length as the input, so spans still line up."""
    return "".join(c.upper() if "a" <= c <= "z" else c for c in text)


URL_RE = _URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.I)
_PRICE_RE = re.compile(
    r"(\d{1,3}(?:[.\s]\d{3})+|\d{2,5})(?:,\d{1,2})?\s?(?:€|euros?)\s?(?:/|al|por|cada)\s?mes",
    re.I,
)
# English listings (14-10-2026): "280 €/month", "€1,250 a month", "650 euros per month", "pcm".
_PRICE_EN_RE = re.compile(
    r"(?:€\s?(\d{1,3}(?:,\d{3})+|\d{2,5})|(\d{1,3}(?:[.,]\d{3})+|\d{2,5})\s?(?:€|euros?))"
    r"\s?(?:/\s?(?:month|mo)\b|(?:a|per)\s+month\b|pcm\b|monthly\b)",
    re.I,
)
_ROOM_RE = re.compile(
    r"^\W*habitaci[oó]n\b|\balquil\w*\s+(?:una\s+)?habitaci[oó]n\b|"
    r"\bhabitaci[oó]n\s+(?:individual|doble|exterior|interior|amueblada|en\s+piso|con\s+baño)\b|"
    r"\bpiso\s+compartido\b|"
    r"^\W*room\b|\broom\s+(?:in|for\s+rent|to\s+rent|available)\b|"
    r"\b(?:double|single|private|en-suite|ensuite)\s+room\b|\bshared\s+(?:flat|apartment)\b|"
    r"\bflat\s?share\b",
    re.I | re.M,
)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    raw: str
    value: str  # normalised


@dataclass(frozen=True)
class IbanInfo:
    compact: str
    country: str
    valid: bool
    bank_name: str | None = None
    bic: str | None = None


def _iban_length(country: str) -> int | None:
    try:
        return get_iban_spec(country).iban_length
    except Exception:
        return None


def iban_spans(text: str) -> list[Span]:
    out = []
    # Lowercase IBANs ("es91 2100…") exist; uppercase a same-length copy to match them.
    upper = _ascii_upper(text)
    for m in _IBAN_RE.finditer(upper):
        raw = m.group(1)
        compact = re.sub(r"[ \-.\u00a0\u202f\r\n]", "", raw)
        country = compact[:2]
        length = _iban_length(country)
        if length is None:
            continue
        if len(compact) > length:
            # Regex over-matched into a following token: trim to the country's length and map
            # the cut back onto the raw span.
            keep, n = 0, 0
            for i, ch in enumerate(raw):
                if ch not in _IBAN_SEP:
                    n += 1
                if n == length:
                    keep = i + 1
                    break
            raw = raw[:keep]
            compact = compact[:length]
        out.append(Span(m.start(1), m.start(1) + len(raw), raw, compact))
    return out


def iban_info(compact: str) -> IbanInfo:
    try:
        iban = IBAN(compact, allow_invalid=True)
    except Exception:  # hostile text: odd digits or letters must not abort the analysis
        return IbanInfo(compact, compact[:2], False)
    if not iban.is_valid:
        return IbanInfo(compact, compact[:2], False)
    bic = iban.bic
    return IbanInfo(
        compact,
        iban.country_code,
        True,
        bank_name=iban.bank_name,
        bic=str(bic) if bic else None,
    )


def normalize_phone(raw: str) -> str | None:
    try:
        n = phonenumbers.parse(raw, "ES")
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(n):
        return None
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)


def phone_region(e164: str) -> str | None:
    """ISO country of a phone number, by its prefix (works for unallocated ranges too)."""
    try:
        n = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return None
    region = phonenumbers.region_code_for_country_code(n.country_code)
    return None if region == "ZZ" else region


_WA_RE = re.compile(r"(?:wa\.me|api\.whatsapp\.com/send\?phone=)/?\+?(\d{8,15})", re.I)


def phone_spans(text: str) -> list[Span]:
    """Phones in free text. IBANs and URLs are blanked first: an IBAN's digit groups or a
    9-digit listing id in a portal URL are not phones (review 2026-10-01). WhatsApp links are
    the exception: wa.me/34600… IS a phone, always with country code."""
    masked = text
    for sp in iban_spans(text):
        masked = masked[: sp.start] + " " * (sp.end - sp.start) + masked[sp.end :]
    out = []
    for m in _URL_RE.finditer(text):
        wa = _WA_RE.search(m.group(0))
        if wa:
            e164 = normalize_phone("+" + wa.group(1))
            if e164:
                out.append(Span(m.start() + wa.start(1), m.start() + wa.end(1), wa.group(1), e164))
        masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end() :]
    for m in phonenumbers.PhoneNumberMatcher(masked, "ES", leniency=phonenumbers.Leniency.POSSIBLE):
        e164 = phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164)
        # Short Spanish numbers are prices, years or references, not phones.
        if len(e164) < 11:
            continue
        out.append(Span(m.start, m.end, m.raw_string, e164))
    return sorted(out, key=lambda sp: sp.start)


def email_spans(text: str) -> list[Span]:
    return [
        Span(m.start(), m.end(), m.group(0), m.group(0).lower()) for m in _EMAIL_RE.finditer(text)
    ]


_URL_TRAILING = ".,;:!?)]}>»”’\"'…*"
_HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def urls(text: str) -> list[str]:
    return _dedupe(m.group(0).rstrip(_URL_TRAILING) for m in _URL_RE.finditer(text))


def url_domain(url: str) -> str | None:
    """Lowercase host without "www.", or None. Never raises: hostile text can hold any URL."""
    u = url.rstrip(_URL_TRAILING)
    u = u if "://" in u else f"http://{u}"
    try:
        host = urlparse(u).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().removeprefix("www.")
    return host if _HOST_RE.match(host) else None


_LISTING_PATTERNS = [
    ("idealista", re.compile(r"idealista\.com/(?:[a-z]{2}/)?inmueble/(\d+)", re.I)),
    ("fotocasa", re.compile(r"fotocasa\.es/.*?/(\d{6,})/d", re.I)),
    ("milanuncios", re.compile(r"milanuncios\.com/.*?-(\d{6,})\.htm", re.I)),
    ("pisos.com", re.compile(r"pisos\.com/.*?-(\d{6,}(?:_\d+)?)", re.I)),
    ("habitaclia", re.compile(r"habitaclia\.com/.*?-i(\d{6,})", re.I)),
]


def listing_ref(url: str) -> str | None:
    """'portal:id' for a known portal listing URL."""
    for portal, rx in _LISTING_PATTERNS:
        m = rx.search(url)
        if m:
            return f"{portal}:{m.group(1)}"
    return None


def price_eur(text: str) -> int | None:
    m = _PRICE_RE.search(text)
    if m:
        return int(re.sub(r"[.\s]", "", m.group(1)))
    m = _PRICE_EN_RE.search(text)
    if m:
        return int(re.sub(r"[.,\s]", "", m.group(1) or m.group(2)))
    return None


_ROOMS_RE = re.compile(
    r"\b(\d{1,2})\s*(?:habitaciones|dormitorios|habs?\.?|dorm\.?|dormitorio|habitaci[oó]n|"
    r"-?\s?bed(?:room)?s?|br)\b",
    re.I,
)


def rooms_in(text: str) -> int | None:
    """Number of rooms of a flat ("3 dormitorios", "2 hab."); a studio counts as 1."""
    m = _ROOMS_RE.search(text)
    if m:
        n = int(m.group(1))
        return n if 0 < n < 15 else None
    return 1 if re.search(r"\b(?:estudio|studio)\b", text, re.I) else None


def looks_like_room(text: str) -> bool:
    return bool(_ROOM_RE.search(text))


def _dedupe(items) -> list:
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


@dataclass(frozen=True)
class Identifiers:
    ibans: list[str]
    phones: list[str]
    emails: list[str]
    urls: list[str]


def identifiers(text: str) -> Identifiers:
    return Identifiers(
        ibans=_dedupe(sp.value for sp in iban_spans(text)),
        phones=_dedupe(sp.value for sp in phone_spans(text)),
        emails=_dedupe(sp.value for sp in email_spans(text)),
        urls=urls(text),
    )
