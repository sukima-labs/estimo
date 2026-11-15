"""Indicator base: normalised identifiers stored as HMAC-SHA256 with a secret key.

A keyed HMAC (not hash + per-row salt) so that the same phone seen in two cases produces the
same digest and recidivism can match, while the digests are useless without the key.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata

from mikiwame.extract import text as tx
from mikiwame.extract.address import street_of
from mikiwame.extract.redact import is_business_name
from mikiwame.models import Case


def normalize_name(name: str) -> str | None:
    s = unicodedata.normalize("NFKD", name.strip().lstrip("~"))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) >= 3 else None


def digest(key: bytes, kind: str, value: str) -> str:
    return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def indicator_value(kind: str, raw: str) -> str | None:
    """Normalise a value typed by hand (a landlord's complaint) exactly as case_indicators does,
    so its digest matches the registry."""
    if kind == "phone":
        return tx.normalize_phone(raw)
    if kind == "iban":
        found = tx.identifiers(raw).ibans
        return found[0] if found else None
    if kind == "email":
        found = tx.identifiers(raw).emails
        return found[0] if found else None
    if kind in ("name", "business"):
        return normalize_name(raw)
    if kind == "listing":
        return tx.listing_ref(raw)
    return None


def case_indicators(case: Case, key: bytes) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    # Advertiser side only: the user's own phone or IBAN must never be filed as the advertiser's.
    values += [("iban", i) for i in case.advertiser_ibans]
    values += [("phone", p) for p in case.advertiser_phones]
    values += [("email", e) for e in case.advertiser_emails]
    context = case.all_text()
    for name in case.advertiser_names:
        n = normalize_name(name)
        if n:
            values.append(("business" if is_business_name(name, context) else "name", n))
    if case.listing_url:
        ref = tx.listing_ref(case.listing_url)
        if ref:
            values.append(("listing", ref))
    for building in case.addresses:  # building keys, see extract/address.py
        values.append(("address", building))
        values.append(("street", street_of(building)))
    seen: set[tuple[str, str]] = set()
    out = []
    for kind, value in values:
        if (kind, value) not in seen:
            seen.add((kind, value))
            out.append((kind, digest(key, kind, value)))
    return out
