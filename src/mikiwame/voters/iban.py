"""IBAN voter: format, check digits and country. Deterministic."""

from __future__ import annotations

from mikiwame.extract.text import iban_info
from mikiwame.models import Case, Vote
from mikiwame.voters.base import score_from, signal

COUNTRY_ES = {
    "AT": "Austria", "BE": "Bélgica", "BG": "Bulgaria", "CH": "Suiza", "CY": "Chipre",
    "CZ": "Chequia", "DE": "Alemania", "DK": "Dinamarca", "EE": "Estonia", "FI": "Finlandia",
    "FR": "Francia", "GB": "Reino Unido", "GR": "Grecia", "HR": "Croacia", "HU": "Hungría",
    "IE": "Irlanda", "IT": "Italia", "LT": "Lituania", "LU": "Luxemburgo", "LV": "Letonia",
    "MT": "Malta", "NL": "Países Bajos", "PL": "Polonia", "PT": "Portugal", "RO": "Rumanía",
    "SE": "Suecia", "SI": "Eslovenia", "SK": "Eslovaquia", "AD": "Andorra", "GI": "Gibraltar",
    "US": "Estados Unidos", "MA": "Marruecos", "NG": "Nigeria", "SN": "Senegal",
    "CI": "Costa de Marfil", "CM": "Camerún", "GH": "Ghana", "RU": "Rusia", "UA": "Ucrania",
    "CN": "China", "AR": "Argentina", "MX": "México", "CO": "Colombia", "VE": "Venezuela",
    "BR": "Brasil", "EC": "Ecuador", "PE": "Perú", "IN": "India", "TR": "Turquía",
    "AE": "Emiratos Árabes Unidos", "NO": "Noruega",
}  # fmt: skip


COUNTRY_EN = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CH": "Switzerland", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EE": "Estonia", "FI": "Finland",
    "FR": "France", "GB": "United Kingdom", "GR": "Greece", "HR": "Croatia", "HU": "Hungary",
    "IE": "Ireland", "IT": "Italy", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia",
    "MT": "Malta", "NL": "the Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "AD": "Andorra", "GI": "Gibraltar",
    "US": "the United States", "MA": "Morocco", "NG": "Nigeria", "SN": "Senegal",
    "CI": "Côte d'Ivoire", "CM": "Cameroon", "GH": "Ghana", "RU": "Russia", "UA": "Ukraine",
    "CN": "China", "AR": "Argentina", "MX": "Mexico", "CO": "Colombia", "VE": "Venezuela",
    "BR": "Brazil", "EC": "Ecuador", "PE": "Peru", "IN": "India", "TR": "Turkey",
    "AE": "the United Arab Emirates", "NO": "Norway",
}  # fmt: skip


def country_es(code: str) -> str:
    return COUNTRY_ES.get(code, code)


def country_en(code: str) -> str:
    return COUNTRY_EN.get(code, code)


class IbanVoter:
    name = "iban"

    async def vote(self, case: Case) -> Vote:
        return self.vote_sync(case)

    def vote_sync(self, case: Case) -> Vote:
        # Only accounts on the advertiser's side: the user's own IBAN is not the payment
        # account (review 2026-10-01).
        if not case.advertiser_ibans:
            return Vote.abstain(self.name, "sin IBAN")
        signals = []
        infos = [iban_info(i) for i in case.advertiser_ibans]
        foreign = [i for i in infos if i.valid and i.country != "ES"]
        for info in foreign:
            country = COUNTRY_ES.get(info.country, info.country)
            signals.append(
                signal(
                    "iban_foreign",
                    message=f"La cuenta para pagar es de otro país ({country}).",
                    message_en="The account to pay into is in another country "
                    f"({country_en(info.country)}).",
                )
            )
            break
        if any(not i.valid for i in infos):
            signals.append(signal("iban_invalid"))
        if len({i.compact for i in infos if i.valid}) > 1:
            signals.append(signal("iban_multiple"))
        es = [i for i in infos if i.valid and i.country == "ES"]
        if es and not foreign:
            bank = f" ({es[0].bank_name})" if es[0].bank_name else ""
            signals.append(
                signal(
                    "iban_es",
                    message=f"La cuenta es de un banco español{bank}. "
                    "Por sí sola no garantiza nada.",
                    message_en=f"The account is at a Spanish bank{bank}. "
                    "On its own, that guarantees nothing.",
                )
            )
        confidence = 0.7 if foreign else 0.4
        return Vote(
            voter=self.name,
            score=score_from(signals),
            confidence=confidence,
            evidence=[s.message for s in signals],
            signals=signals,
        )
