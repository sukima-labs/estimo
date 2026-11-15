"""Market voter: what a local mirror of the portal says about the listing the user sent.

Only raises caution: a listing the portal certified as removed but
still offered to the user, or a price clearly below the published one. Everything else becomes
plain facts for the report and the text voter (case.market_facts).
"""

from __future__ import annotations

from mikiwame.extract.address import MADRID_DISTRICTS, MUNICIPALITY_ES
from mikiwame.extract.text import listing_ref
from mikiwame.models import Case, Vote
from mikiwame.store.market import MarketListing, MarketStore
from mikiwame.voters.base import score_from, signal

# Below the published price by more than this fraction counts as a mismatch.
PRICE_GAP = 0.15
# Below this fraction of the local median for similar flats counts as a bargain (proposal in
# validated with evaluation/ on 2026-10-01). Only raises caution.
BARGAIN = 0.6
# A removal of a listing that already reappeared after a false one counts this much.
REAPPEARED_FACTOR = 0.5


def _words(slug_part: str) -> str:
    return slug_part.replace("-", " ").strip()


def _district_of(zone: str) -> str:
    """District words of a Madrid zone slug. idealista's slugs are its own: "madrid/fuencarral",
    "madrid/moncloa", "madrid/san-blas" and "madrid/barrio-de-salamanca" (first sync, 04-10)."""
    return _words(zone.removeprefix("madrid/")).removeprefix("barrio de ")


def zone_for(zones: list[str], town: str | None, district: str | None) -> str | None:
    """The feed zone of our town (and district, in Madrid capital), among the zones the mirror
    holds. Zone slugs: "alcala-de-henares-madrid", "madrid/chamberi". Madrid without a district
    has none: the city's median mixes Salamanca and Villaverde and would call a fair flat in
    Villaverde a bargain."""
    if not town:
        return None
    if town == "madrid":
        if not district:
            return None
        for z in zones:
            name = _district_of(z)
            if z.startswith("madrid/") and (
                name == district or district.startswith(name + " ") or name.startswith(district)
            ):
                return z
        return None
    return next((z for z in zones if not z.startswith("madrid/")
                 and _words(z.removesuffix("-madrid")) == town), None)  # fmt: skip


def zone_es(zone: str) -> str:
    """How to name a zone for the user: "Alcalá de Henares", "Madrid (Chamberí)"."""
    if zone.startswith("madrid/"):
        name = _district_of(zone)
        district = next((v for k, v in MADRID_DISTRICTS.items()
                         if k == name or k.startswith(name + " ")), name.title())  # fmt: skip
        return f"Madrid ({district})"
    town = _words(zone.removesuffix("-madrid"))
    return MUNICIPALITY_ES.get(town, town.title())


def eur(amount: float) -> str:
    """Spanish thousands separator: 1.450 €."""
    return f"{amount:,.0f}".replace(",", ".") + " €"


def eur_en(amount: float) -> str:
    """€1,450."""
    return f"€{amount:,.0f}"


def _date(d) -> str:
    return d.strftime("%d-%m-%Y") if d else "?"


def facts(m: MarketListing, *, presence: str = "active", lang: str = "es") -> str:
    """presence: MarketStore.presence(m). "active" only when the zone's latest sweep saw it or
    was complete; otherwise all we know is when it was last seen."""
    en = lang == "en"
    money = eur_en if en else eur
    parts = [f"on idealista since {_date(m.first_seen)}" if en
             else f"en idealista desde el {_date(m.first_seen)}"]  # fmt: skip
    if m.price_eur:
        hist = [h.get("price_eur") for h in m.price_history if h.get("price_eur")]
        was = "previously" if en else "antes"
        before = f" ({was} {money(hist[0])})" if hist and hist[0] != m.price_eur else ""
        published = "published price" if en else "precio publicado"
        parts.append(f"{published} {money(m.price_eur)}{before}")
    if m.advertiser_type == "agencia":
        name = f" «{m.advertiser_business}»" if m.advertiser_business else ""
        parts.append(f"advertiser: agency{name}" if en else f"anunciante: agencia{name}")
    elif m.advertiser_type == "particular":
        parts.append("advertiser: private individual" if en else "anunciante: particular")
    if m.removed_at:
        # removed_at = not seen in a complete sweep; ~3-5 % are still live (idealista, 01-10).
        parts.append(f"not on the portal since {_date(m.removed_at)}" if en
                     else f"no aparece en el portal desde el {_date(m.removed_at)}")  # fmt: skip
    elif m.status == "active" and presence == "active":
        parts.append(f"active (last seen {_date(m.last_seen)})" if en
                     else f"activo (última vez visto el {_date(m.last_seen)})")  # fmt: skip
    elif m.last_seen:
        parts.append(f"last seen {_date(m.last_seen)}" if en
                     else f"visto por última vez el {_date(m.last_seen)}")  # fmt: skip
    if m.reappeared_count:
        parts.append(
            "it once dropped off the portal and then came back"
            if en
            else "alguna vez dejó de verse en el portal y luego volvió a aparecer"
        )
    return ", ".join(parts)


class MarketVoter:
    name = "market"

    def __init__(self, store: MarketStore | None) -> None:
        self.store = store

    def lookup(self, case: Case) -> MarketListing | None:
        if self.store is None or not case.listing_url:
            return None
        ref = listing_ref(case.listing_url)
        if not ref:
            return None
        source, _, listing_id = ref.partition(":")
        return self.store.get(source, listing_id)

    def presence(self, listing: MarketListing) -> str:
        return self.store.presence(listing) if self.store is not None else "unknown"

    def reference(
        self, case: Case, listing: MarketListing | None = None
    ) -> tuple[dict, str, str] | None:
        """Local price reference for flats like this one, and a sentence for the report in
        Spanish and in English.

        Only when we know the town: from the case, or from the listing in our mirror. Never a
        default town (01-10-2026, open to the whole Comunidad de Madrid: a flat in Getafe got
        Alcalá's median and a bargain_price signal)."""
        if self.store is None:
            return None
        room = case.property_type == "habitacion"
        category = "habitacion" if room else "viviendas"
        # The portal's own zone (and bedrooms, when the text gives none) beats the text:
        # "idealista.com/inmueble/… me lo dejan en 650 €" compared with every flat in
        # Arganzuela, studios to 6 bedrooms (Telegram test, 05-10-2026).
        rooms = case.rooms
        if listing is not None and listing.municipality and listing.category == category:
            slug = listing.municipality
            rooms = rooms or listing.rooms
        else:
            slug = zone_for(self.store.zones(category), case.municipality, case.district)
        if not slug:
            return None
        # Rooms: one median for the whole town. Their prices barely depend on the flat's
        # bedrooms (Alcalá, 04-10-2026: 423 rooms, p10 370 €, p90 550 €, none below 0.6 x the
        # 450 € median), and case.rooms of a room ad is ambiguous.
        ref = self.store.reference(slug, category, None if room else rooms)
        if not ref:
            return None
        if room:
            what, what_en = "una habitación", "a room"
        else:
            what = f"un piso de {ref['rooms']} habitaciones" if ref["rooms"] else "un piso"
            what_en = f"a {ref['rooms']}-bedroom flat" if ref["rooms"] else "a flat"
            if ref["rooms"] == 1:
                what = "un piso de 1 habitación"
        n = f"{ref['n']:,}".replace(",", ".")
        sentence = (
            f"en {zone_es(slug)}, {what} se anuncia por unos {eur(ref['median'])} al "
            f"mes (mediana de {n} anuncios en idealista)"
        )
        sentence_en = (
            f"in {zone_es(slug)}, {what_en} is advertised for about {eur_en(ref['median'])} a "
            f"month (median of {ref['n']:,} listings on idealista)"
        )
        return ref, sentence, sentence_en

    def vote_sync(
        self,
        case: Case,
        listing: MarketListing | None,
        reference: tuple[dict, str, str] | None = None,
    ) -> Vote:
        signals = []
        if reference and case.price_eur:
            ref, sentence, sentence_en = reference
            if case.price_eur < BARGAIN * ref["median"]:
                signals.append(
                    signal(
                        "bargain_price",
                        message=f"El precio ({eur(case.price_eur)} al mes) está muy por debajo "
                        f"de lo habitual: {sentence}.",
                        message_en=f"The price ({eur_en(case.price_eur)} a month) is far below "
                        f"the usual: {sentence_en}.",
                    )
                )
        if listing is None:
            if not signals:
                return Vote.abstain(self.name, "sin datos del portal que lo contradigan")
            return Vote(voter=self.name, score=score_from(signals), confidence=0.6,
                        evidence=[s.message for s in signals], signals=signals)  # fmt: skip
        if listing.removed_at:
            removed = signal(
                "listing_removed",
                message="Este anuncio no aparece en idealista desde el "
                f"{_date(listing.removed_at)}, pero te lo siguen ofreciendo.",
                message_en="This listing has not appeared on idealista since "
                f"{_date(listing.removed_at)}, but it is still being offered to you.",
            )
            if listing.reappeared_count:
                # It already came back after a false removal: this one may be real or not
                # (idealista, 03-10). Weaker, never dropped.
                removed.weight *= REAPPEARED_FACTOR
                removed.message = (
                    "Este anuncio no aparece en idealista desde el "
                    f"{_date(listing.removed_at)}, aunque otras veces dejó de verse y volvió a "
                    "aparecer: ábrelo tú para confirmarlo."
                )
                removed.message_en = (
                    "This listing has not appeared on idealista since "
                    f"{_date(listing.removed_at)}, although it has dropped off and come back "
                    "before: open it yourself to confirm."
                )
            signals.append(removed)
        if (
            listing.price_eur
            and case.price_eur
            and case.price_eur < listing.price_eur * (1 - PRICE_GAP)
        ):
            signals.append(
                signal(
                    "price_mismatch",
                    message=f"Te ofrecen {eur(case.price_eur)} al mes, bastante menos que los "
                    f"{eur(listing.price_eur)} publicados en idealista.",
                    message_en=f"You are offered {eur_en(case.price_eur)} a month, much less "
                    f"than the {eur_en(listing.price_eur)} published on idealista.",
                )
            )
        if not signals:
            return Vote.abstain(self.name, "sin discrepancias con el portal")
        return Vote(
            voter=self.name,
            score=score_from(signals),
            confidence=0.7,
            evidence=[s.message for s in signals],
            signals=signals,
        )
