"""Phone voter: the advertiser's contact number is from another country. Deterministic.

Only phones on the advertiser's side count (chat header, their messages, the ad): a user who
writes their own foreign number must not make the advertiser look suspicious. Low weight: a
foreign company or an owner abroad can be legitimate; it adds up with other signals.
"""

from __future__ import annotations

from mikiwame.extract.text import phone_region
from mikiwame.models import Case, Vote
from mikiwame.voters.base import score_from, signal
from mikiwame.voters.iban import country_en, country_es


class PhoneVoter:
    name = "phone"

    async def vote(self, case: Case) -> Vote:
        return self.vote_sync(case)

    def vote_sync(self, case: Case) -> Vote:
        regions = [r for r in (phone_region(p) for p in case.advertiser_phones) if r]
        foreign = [r for r in regions if r != "ES"]
        if not foreign:
            return Vote.abstain(self.name, "sin teléfono extranjero")
        country = country_es(foreign[0])
        signals = [
            signal(
                "phone_foreign",
                message=f"El teléfono de contacto del anunciante es de otro país ({country}).",
                message_en="The advertiser's contact phone is from another country "
                f"({country_en(foreign[0])}).",
            )
        ]
        return Vote(
            voter=self.name,
            score=score_from(signals),
            confidence=0.6,
            evidence=[s.message for s in signals],
            signals=signals,
        )
