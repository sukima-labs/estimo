"""Recidivism voter: what our own registry already knows about this advertiser or property.

Facts about our records, worded as such ("ya apareció en análisis de otras personas"), never a
claim that someone is a scammer. Two signals:
- seen_before_red: an advertiser identifier (phone, IBAN, e-mail, name) is in other people's
  analyses that ended red, from at least two independent users: different branches of the
  invite tree (one person and the friends they invited count once; revoked members and disputed
  identifiers never count), so one user cannot poison the registry against a real landlord;
- reused_elsewhere: the same property (address, listing or photo) appears in another user's
  analysis under a different advertiser: the classic copied listing.
The user's own previous cases (follow-ups of the same case) never count.
"""

from __future__ import annotations

from mikiwame.models import Case, Vote
from mikiwame.photos import SAME_PHOTO
from mikiwame.store.sql import Store
from mikiwame.voters.base import score_from, signal

ADVERTISER_KINDS = {"phone", "iban", "email", "name", "business"}
PROPERTY_KINDS = {"address", "listing"}
MIN_USERS_FOR_RED = 2

_KIND_EN = {
    "phone": "phone",
    "iban": "IBAN",
    "email": "email",
    "name": "name",
    "business": "company name",
}
_KIND_ES = {
    "phone": "teléfono",
    "iban": "IBAN",
    "email": "correo",
    "name": "nombre",
    "business": "nombre de empresa",
}


class RecidivismVoter:
    name = "recidivism"

    def __init__(self, store: Store, roots: frozenset[int] = frozenset()) -> None:
        self.store = store
        self.roots = roots

    def vote_sync(self, case: Case, indicators: list[tuple[str, str]], tg_user_id: int) -> Vote:
        mine = set(indicators)
        my_adv = {p for p in mine if p[0] in ADVERTISER_KINDS}
        related = self.store.related_cases(mine, exclude_user=tg_user_id)
        photos = self.store.similar_photos(
            case.photo_hashes, exclude_user=tg_user_id, max_distance=SAME_PHOTO
        )
        signals = []

        red = {
            cid: info
            for cid, info in related.items()
            if info["level"] == "red" and info["kinds"] & ADVERTISER_KINDS
        }
        branches = self.store.branches({info["user"] for info in red.values()}, self.roots)
        if len(set(branches.values())) >= MIN_USERS_FOR_RED:
            kinds = sorted({k for info in red.values() for k in info["kinds"] & ADVERTISER_KINDS})
            what = ", ".join(_KIND_ES[k] for k in kinds)
            what_en = ", ".join(_KIND_EN[k] for k in kinds)
            signals.append(
                signal(
                    "seen_before_red",
                    message=f"Estos datos ({what}) ya aparecieron en {len(red)} análisis de otras "
                    "personas que salieron en rojo.",
                    message_en=f"These details ({what_en}) already appeared in {len(red)} "
                    "analyses by other people that came out red.",
                )
            )

        prop_cases = {cid for cid, info in related.items() if info["kinds"] & PROPERTY_KINDS}
        prop_cases |= set(photos)
        if prop_cases and my_adv:
            theirs = self.store.case_indicator_pairs(prop_cases, ADVERTISER_KINDS)
            reused = [cid for cid in prop_cases if theirs.get(cid) and not (theirs[cid] & my_adv)]
            if reused:
                by_photo = any(cid in photos for cid in reused)
                by_address = any(
                    "address" in related.get(cid, {}).get("kinds", set()) for cid in reused
                )
                what = (
                    "las mismas fotos" if by_photo and not by_address
                    else "la misma dirección" if by_address and not by_photo
                    else "la misma vivienda"
                )  # fmt: skip
                what_en = {
                    "las mismas fotos": "The same photos",
                    "la misma dirección": "The same address",
                }.get(what, "The same property")
                signals.append(
                    signal(
                        "reused_elsewhere",
                        message=f"Aparece {what} en otro análisis, pero con otro anunciante "
                        "(otro teléfono, cuenta o nombre).",
                        message_en=f"{what_en} appears in another analysis, but with a "
                        "different advertiser (another phone, account or name).",
                    )
                )

        if not signals:
            return Vote.abstain(self.name)
        return Vote(
            voter=self.name,
            score=score_from(signals),
            confidence=0.7,
            evidence=[s.message for s in signals],
            signals=signals,
        )
