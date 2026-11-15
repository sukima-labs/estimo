"""Replace third-party identifiers with placeholders before text goes to a text LLM.

The rubric does not need the digits of a phone or an IBAN, only their country; minimising what
leaves the box is cheaper than trusting every provider's retention policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from mikiwame.extract import text as tx

_BUSINESS = re.compile(
    r"\b(inmobiliaria|agencia|gesti[oó]n|gestor[ií]a|alquileres|s\.?l\.?u?|s\.?a\.?|"
    r"fincas|properties|homes|real estate|estancias)\b",
    re.I,
)


# Words that can appear as (hostile or silly) display names but are the vocabulary of the case.
_COMMON = {
    "piso", "pisos", "casa", "alquiler", "alquileres", "habitacion", "habitación", "fianza",
    "reserva", "señal", "senal", "pago", "pagar", "pagos", "transferencia", "bizum", "llaves",
    "llave", "visita", "contrato", "dinero", "cuenta", "banco", "propietario", "propietaria",
    "dueño", "dueña", "inquilino", "anuncio", "idealista", "fotocasa", "whatsapp", "airbnb",
    "booking", "urgente", "hola", "gracias", "madrid", "alcalá", "alcala", "henares",
}  # fmt: skip


def _looks_like_name(name: str) -> bool:
    words = re.findall(r"[^\W\d_]+", name)
    if not words or all(w.casefold() in _COMMON for w in words):
        return False
    # Real names are capitalised somewhere; an all-lowercase "name" is more likely a word.
    return any(w[0].isupper() for w in words)


def is_business_name(name: str, context: str = "") -> bool:
    """A company, not a person: business words in the name, or, given the case text, the name is
    a label of a web/e-mail domain there or follows "equipo de" / "empresa" / "plataforma".

    Measured 2026-10-01 on a real case: "Spacest" (a rental platform replying automatically from
    idealista) was redacted as a person, inside its own URL too, and the model lost the one hint
    that it was a company."""
    if _BUSINESS.search(name):
        return True
    key = name.strip().lstrip("~").strip().casefold()
    if len(key) < 3 or not context:
        return False
    compact = re.sub(r"[^a-z0-9]", "", key)
    domains = [tx.url_domain(u) or "" for u in tx.urls(context)]
    domains += [e.split("@", 1)[1] for e in (sp.value for sp in tx.email_spans(context))]
    labels = {lab for d in domains for lab in re.split(r"[.\-]", d) if lab}
    # The name is a domain label, or starts one when long enough ("casalto" in casaltohomes.es);
    # a short name inside an unrelated label ("ana" in "banana") does not count (review).
    if compact and any(
        lab == compact or (len(compact) >= 5 and lab.startswith(compact)) for lab in labels
    ):
        return True
    rx = r"\b(?:equipo|empresa|plataforma|grupo|servicio)\s+(?:de\s+)?" + re.escape(key) + r"\b"
    return bool(re.search(rx, context.casefold()))


@dataclass
class Redactor:
    """Stateful so the same identifier gets the same placeholder across all texts of a case."""

    advertiser_names: list[str] = field(default_factory=list)
    # All text of the case, to tell companies from people (see is_business_name).
    context: str = ""
    _ids: dict[tuple[str, str], str] = field(default_factory=dict)
    _count: dict[str, int] = field(default_factory=dict)

    @classmethod
    def for_case(cls, case) -> Redactor:
        return cls(advertiser_names=list(case.advertiser_names), context=case.all_text())

    def person_names(self) -> list[str]:
        """Person names to redact: the full name and each part of 4+ letters (a surname shows up
        alone). A display name made of ordinary words ("transferencia", "Piso") is never
        redacted: a hostile profile name must not blind the model to the evidence (review)."""
        out: list[str] = []
        for name in self.advertiser_names:
            name = name.strip().lstrip("~").strip()
            if len(name) < 3 or is_business_name(name, self.context) or not _looks_like_name(name):
                continue
            out.append(name)
            out += [p for p in re.findall(r"[^\W\d_]{4,}", name) if p.casefold() not in _COMMON]
        return sorted(set(out), key=len, reverse=True)

    def _placeholder(self, kind: str, value: str, extra: str = "") -> str:
        key = (kind, value)
        if key not in self._ids:
            n = self._count.get(kind, 0) + 1
            self._count[kind] = n
            self._ids[key] = f"[{kind}_{n}{extra}]"
        return self._ids[key]

    def redact(self, text: str) -> str:
        if not text:
            return text
        repl: list[tuple[int, int, str]] = []
        for sp in tx.iban_spans(text):
            repl.append(
                (sp.start, sp.end, self._placeholder("IBAN", sp.value, f" país={sp.value[:2]}"))
            )
        for sp in tx.phone_spans(text):
            region = tx.phone_region(sp.value) or "?"
            repl.append((sp.start, sp.end, self._placeholder("TEL", sp.value, f" país={region}")))
        for sp in tx.email_spans(text):
            domain = sp.value.split("@", 1)[1]
            repl.append(
                (sp.start, sp.end, self._placeholder("EMAIL", sp.value, f" dominio={domain}"))
            )
        out = _apply(text, repl)
        names = self.person_names()
        if not names:
            return out
        # Never inside a URL: domains are not personal data and are often the best evidence.
        pieces, last = [], 0
        for m in tx.URL_RE.finditer(out):
            pieces.append(self._redact_names(out[last : m.start()], names))
            pieces.append(m.group(0))
            last = m.end()
        pieces.append(self._redact_names(out[last:], names))
        return "".join(pieces)

    @staticmethod
    def _redact_names(text: str, names: list[str]) -> str:
        for name in names:
            # Whole words only: "Ana" must not eat "ventana" (review 2026-10-01).
            rx = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
            text = re.sub(rx, "[NOMBRE_ANUNCIANTE]", text, flags=re.I)
        return text


def _apply(text: str, repl: list[tuple[int, int, str]]) -> str:
    # Drop overlaps (keep the earliest, longest), then replace from the end.
    repl.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    kept: list[tuple[int, int, str]] = []
    last_end = -1
    for r in repl:
        if r[0] >= last_end:
            kept.append(r)
            last_end = r[1]
    for start, end, ph in reversed(kept):
        text = text[:start] + ph + text[end:]
    return text


def redacted_case(case):
    """A copy of the case safe to persist: free text redacted, identifiers reduced to country.

    Identifiers themselves are only kept as keyed HMACs (indicators.py)."""
    from mikiwame.extract import text as tx

    r = Redactor.for_case(case)
    c = case.model_copy(deep=True)
    c.listing_text = r.redact(c.listing_text)
    c.pasted_text = r.redact(c.pasted_text)
    for m in c.chat:
        m.text = r.redact(m.text)
    c.phones = [f"TEL país={tx.phone_region(p) or '?'}" for p in c.phones]
    c.advertiser_phones = [f"TEL país={tx.phone_region(p) or '?'}" for p in c.advertiser_phones]
    c.ibans = [f"IBAN país={i[:2]}" for i in c.ibans]
    c.advertiser_ibans = [f"IBAN país={i[:2]}" for i in c.advertiser_ibans]
    c.advertiser_emails = [f"EMAIL dominio={e.split('@', 1)[-1]}" for e in c.advertiser_emails]
    # Only domains: a wa.me/34600… link or a query string can carry a phone or a name.
    c.urls = [tx.url_domain(u) or "?" for u in c.urls]
    if c.listing_url:
        c.listing_url = tx.listing_ref(c.listing_url) or tx.url_domain(c.listing_url)
    c.emails = [f"EMAIL dominio={e.split('@', 1)[-1]}" for e in c.emails]
    people = set(r.person_names())
    c.advertiser_names = [
        "[NOMBRE_ANUNCIANTE]" if n.strip().lstrip("~").strip() in people else n
        for n in c.advertiser_names
    ]
    return c
