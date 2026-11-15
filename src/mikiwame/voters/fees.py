"""Fee voter: the advertiser charges the tenant agency or management fees.

Informative only (VOTER_WEIGHT 0, like the street check): it never moves the level. Since the
Ley de Vivienda (Ley 12/2023, LAU art. 20.1) those fees are the landlord's, yet agencies still
charge them (OCU complaints, October 2026), so on a legitimate listing the line tells the user
something useful. Scammers use the same word: the man arrested in Granada on 08-10-2026 took
«fianza» plus «gastos de gestión» from students after showing them the flat.
"""

from __future__ import annotations

import re

from mikiwame.extract.redact import Redactor
from mikiwame.models import Case, Vote
from mikiwame.voters.base import score_from, signal
from mikiwame.voters.patterns import _negated, _quote, _rx, advertiser_text, fold_with_map

_FEES = _rx(
    r"gastos\s+de\s+(?:gestion|agencia|tramitacion|formalizacion)",
    r"honorarios",
    r"comision\s+(?:de\s+)?(?:la\s+)?(?:agencia|inmobiliaria)",
    r"\b(?:agency|admin(?:istration)?|management|handling)\s+fees?\b",
)
# "sin honorarios", "honorarios a cargo del propietario", "no fees": not charged to the tenant.
_NOT_TENANT_BEFORE = re.compile(r"\b(?:sin|no|zero)\s+(?:\w+\s+){0,2}$")
_NOT_TENANT_AFTER = re.compile(
    r"^[^.;:!?\n]{0,40}(?:(?:a\s+cargo|corren|los\s+paga|las\s+paga|lo\s+paga|asume)\w*\s+"
    r"(?:\w+\s+){0,2}(?:propietari|arrendador|casero|landlord)|paid\s+by\s+the\s+(?:owner|landlord)"
    r"|gratis|incluid)"
)


class FeeVoter:
    name = "fees"

    async def vote(self, case: Case) -> Vote:
        return self.vote_sync(case)

    def vote_sync(self, case: Case) -> Vote:
        text = advertiser_text(case)
        folded, index = fold_with_map(text)
        for m in _FEES.finditer(folded):
            before = folded[max(0, m.start() - 30) : m.start()]
            if _negated(folded, m) or _NOT_TENANT_BEFORE.search(before):
                continue
            if _NOT_TENANT_AFTER.search(folded[m.end() :]):
                continue
            quote = Redactor.for_case(case).redact(_quote(text, index, m))
            signals = [signal("fee_to_tenant", quote=quote)]
            return Vote(
                voter=self.name,
                score=score_from(signals),
                confidence=0.8,
                evidence=[s.message for s in signals],
                signals=signals,
            )
        return Vote.abstain(self.name)
