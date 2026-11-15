"""Address voter: does the street the advertiser gave exist in that municipality (geo.py)?

Weak and never a hard rule: a typo, a street OpenStreetMap lacks or a misread screenshot look
the same as an invented address. Abstains when the municipality is unknown (a "Calle Mayor"
exists everywhere) or when Nominatim cannot be reached.
"""

from __future__ import annotations

import asyncio
import logging
import re

from mikiwame.extract.address import MUNICIPALITY_ES, find_addresses, street_of
from mikiwame.geo import StreetChecker
from mikiwame.models import Case, Vote
from mikiwame.voters.base import score_from, signal

log = logging.getLogger(__name__)

# At most this many distinct streets per case (Nominatim: 1 request/s, 2 per street).
MAX_STREETS = 2
# The whole check; past it the voter abstains rather than delay the report.
TIMEOUT_S = 10.0


class AddressVoter:
    name = "address"

    def __init__(self, checker: StreetChecker | None) -> None:
        self.checker = checker

    async def vote(self, case: Case) -> Vote:
        if self.checker is None:
            return Vote.abstain(self.name)
        try:
            async with asyncio.timeout(TIMEOUT_S):
                return await self._vote(case, self.checker)
        except TimeoutError:
            log.warning("case %s: street check timed out", case.case_id)
            return Vote.abstain(self.name)

    async def _vote(self, case: Case, checker: StreetChecker) -> Vote:
        streets: list[tuple[str, str]] = []
        for key in case.addresses:
            street, _, town = street_of(key).partition("|")
            if town and town != "?" and (street, town) not in streets:
                streets.append((street, town))
        signals = []
        for street, town in streets[:MAX_STREETS]:
            try:
                found = await checker.exists(street, town)
            except Exception:  # noqa: BLE001 - an outside service never breaks an analysis
                log.exception("case %s: street check failed", case.case_id)
                found = None
            if found is False:
                shown = _as_written(case).get(f"{street}|{town}") or street.title()
                where = MUNICIPALITY_ES.get(town, town)
                signals.append(
                    signal(
                        "street_not_found",
                        message=f"No encontramos «{shown}» en {where} en el callejero de "
                        "OpenStreetMap.",
                        message_en=f"We could not find «{shown}» in {where} in the "
                        "OpenStreetMap street map.",
                    )
                )
                break
        if not signals:
            return Vote.abstain(self.name)
        return Vote(
            voter=self.name,
            score=score_from(signals),
            confidence=0.4,
            evidence=[s.message for s in signals],
            signals=signals,
        )


def _as_written(case: Case) -> dict[str, str]:
    """Street key → the street as the advertiser wrote it ("Calle del Marqués de Valdeluz"),
    without the number or floor. Our key drops connectors and accents."""
    out: dict[str, str] = {}
    parts = [case.listing_text, case.pasted_text] + [
        m.text for m in case.chat if m.sender != "user"
    ]
    for part in filter(None, parts):
        for a in find_addresses(part, default_municipality=case.municipality):
            name = re.split(r"\s*,?\s*(?:n[ºo°.]?\s*)?\d", a.raw, maxsplit=1)[0].strip(" ,.")
            out.setdefault(a.street_key, name)
    return out
