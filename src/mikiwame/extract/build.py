"""Assemble a normalised `Case` from what the user sent (texts, screenshots)."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from mikiwame.extract import text as tx
from mikiwame.extract.address import (
    district_in,
    elsewhere_in,
    find_addresses,
    municipality_in,
    town_of,
)
from mikiwame.extract.vision import VisionPage
from mikiwame.models import Case, Message, new_case_id
from mikiwame.photos import phash

VisionFn = Callable[..., Awaitable[list[VisionPage]]]

# Chat-header status lines, never a name (review 2026-10-01).
_STATUS_RE = re.compile(
    r"^(?:en l[ií]nea|online|escribiendo|grabando|[uú]lt\.? vez|[uú]ltima vez|visto por|"
    r"toca (?:aqu[ií]|para)|click here|last seen|typing|hace \d+|\d{1,2}:\d{2})",
    re.I,
)


@dataclass
class DraftItem:
    kind: Literal["text", "image"]
    text: str = ""
    image: bytes = b""


@dataclass
class Draft:
    """What a user has sent so far for one case. Images live only in memory."""

    user_id: int
    case_id: str = field(default_factory=new_case_id)
    items: list[DraftItem] = field(default_factory=list)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    # Prefilled from the previous analysed case (the user is sending the advertiser's answer).
    continues: bool = False

    def texts(self) -> list[str]:
        return [i.text for i in self.items if i.kind == "text" and i.text.strip()]

    def images(self) -> list[bytes]:
        return [i.image for i in self.items if i.kind == "image"]

    def summary(self) -> tuple[int, int]:
        return len(self.images()), len(self.texts())


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def enrich(case: Case) -> Case:
    """Fill identifiers, price and type from the case's text fields. Idempotent."""
    # Label-free, one blank line between messages, so an IBAN never swallows "ANUNCIANTE:".
    all_text = "\n\n".join(
        p for p in [case.listing_text, case.pasted_text, *(m.text for m in case.chat)] if p
    )
    ids = tx.identifiers(all_text)
    phones = [p for p in (tx.normalize_phone(x) for x in case.phones) if p]
    case.phones = _dedupe(phones + ids.phones)
    # Blank lines between parts: identifiers never run from one message into the next.
    advertiser_text = "\n\n".join(
        [case.listing_text, case.pasted_text] + [m.text for m in case.chat if m.sender != "user"]
    )
    adv_ids = tx.identifiers(advertiser_text)
    case.advertiser_ibans = _dedupe(case.advertiser_ibans + adv_ids.ibans)
    case.advertiser_emails = _dedupe(case.advertiser_emails + adv_ids.emails)
    header = [p for p in (tx.normalize_phone(x) for x in case.advertiser_phones) if p]
    case.advertiser_phones = _dedupe(header + [sp.value for sp in tx.phone_spans(advertiser_text)])
    case.ibans = _dedupe(case.ibans + ids.ibans)
    case.emails = _dedupe(case.emails + ids.emails)
    case.urls = _dedupe(case.urls + ids.urls)
    if case.listing_url is None:
        case.listing_url = next((u for u in case.urls if tx.listing_ref(u)), None)
    case.advertiser_names = _dedupe([n.strip().lstrip("~").strip() for n in case.advertiser_names])
    if case.price_eur is None:
        case.price_eur = tx.price_eur(case.listing_text) or tx.price_eur(case.pasted_text)
    if not case.addresses:
        town = municipality_in(all_text)
        found = []
        for part in [case.listing_text, case.pasted_text] + [
            m.text for m in case.chat if m.sender != "user"
        ]:
            found += find_addresses(part, default_municipality=town) if part else []
        case.addresses = _dedupe([a.building_key for a in found])
    if case.rooms is None:
        for part in [case.listing_text, case.pasted_text] + [
            m.text for m in case.chat if m.sender != "user"
        ]:
            if part and (n := tx.rooms_in(part)):
                case.rooms = n
                break
    if case.municipality is None:
        # The ad first: "estoy en Guadalajara, en casa de mi hija" in the chat is where the
        # landlord is, not where the flat is.
        for part in [case.listing_text, case.pasted_text] + [
            m.text for m in case.chat if m.sender != "user"
        ]:
            if part and (town := town_of(part)):
                case.municipality = town
                break
    # A Madrid district or neighbourhood with no other town named is Madrid capital ("piso en
    # Chamberí" never says Madrid: Telegram test of 05-10-2026 got no price reference). The
    # aliases leave out words that are also ordinary ones or other places (Centro, Salamanca).
    # A city outside the Comunidad anywhere in the case stops that guess (Pueblo Nuevo is also
    # Barcelona's Poblenou): better no price reference than Madrid's.
    landlord_parts = [case.listing_text, case.pasted_text] + [
        m.text for m in case.chat if m.sender != "user"
    ]
    madrid_ok = case.municipality == "madrid" or (
        case.municipality is None and not any(p and elsewhere_in(p) for p in landlord_parts)
    )
    if madrid_ok and case.district is None:
        for part in landlord_parts:
            if part and (district := district_in(part)):
                case.district = district
                case.municipality = "madrid"
                break
    if case.property_type == "unknown":
        body = case.listing_text or case.pasted_text
        if body:
            case.property_type = "habitacion" if tx.looks_like_room(body) else "piso"
    return case


def apply_pages(case: Case, pages: list[VisionPage]) -> Case:
    listing_parts, other_parts = [], []
    for page in pages:
        if page.kind == "id_document":
            if page.sent_by == "them":
                case.id_documents += 1
            elif page.sent_by == "unknown":
                case.id_documents_unattributed += 1
            continue
        for title in (page.contact_title, page.contact_subtitle):
            title = title.strip()
            if not title or _STATUS_RE.search(title):
                continue
            phone = tx.normalize_phone(title) if sum(c.isdigit() for c in title) >= 9 else None
            if phone:
                case.phones.append(phone)
                case.advertiser_phones.append(phone)
            else:
                case.advertiser_names.append(title)
        if case.channel == "unknown" and page.channel != "unknown":
            case.channel = page.channel
        for m in page.messages:
            sender = {"them": "advertiser", "me": "user"}.get(m.from_, "unknown")
            case.chat.append(Message(sender=sender, text=m.text.strip()))
        if page.text.strip():
            (listing_parts if page.kind == "listing" else other_parts).append(page.text.strip())
    if listing_parts:
        case.listing_text = "\n\n".join(filter(None, [case.listing_text, *listing_parts]))
    if other_parts:
        case.pasted_text = "\n\n".join(filter(None, [case.pasted_text, *other_parts]))
    return case


async def build_case(
    draft: Draft,
    *,
    vision: VisionFn | None,
    max_images: int,
    max_text_chars: int,
) -> Case:
    texts = draft.texts()
    images = draft.images()[:max_images]
    case = Case(case_id=draft.case_id, image_count=len(images))
    case.pasted_text = "\n\n".join(texts)[:max_text_chars]
    if images and vision is not None:
        pages = await vision(images, case_id=draft.case_id)
        apply_pages(case, pages)
        case.photo_hashes = photo_hashes(images, pages)
    return enrich(case)


def photo_hashes(images: list[bytes], pages: list[VisionPage]) -> list[str]:
    """pHash of the images the vision model classified as property photos. Pages come back one
    per image in order; if the counts disagree, trust the page index when it is in range."""
    out: list[str] = []
    for pos, page in enumerate(pages):
        if page.kind != "photo":
            continue
        i = pos if len(pages) == len(images) else page.index - 1
        if 0 <= i < len(images):
            h = phash(images[i])
            if h and h not in out:
                out.append(h)
    return out
