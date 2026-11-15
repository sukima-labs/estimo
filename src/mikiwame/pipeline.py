"""Draft → case → votes → verdict → adversarial review → report, and persistence."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from mikiwame.adversarial import Adversary
from mikiwame.aggregate import aggregate
from mikiwame.config import Settings
from mikiwame.extract import text as tx
from mikiwame.extract.build import Draft, build_case, enrich
from mikiwame.extract.redact import redacted_case
from mikiwame.extract.vision import VisionExtractor
from mikiwame.geo import StreetChecker
from mikiwame.indicators import case_indicators
from mikiwame.llm.client import LLMError
from mikiwame.llm.router import LLMRouter
from mikiwame.models import Case, Verdict, Vote
from mikiwame.patterns import features
from mikiwame.report import render_report
from mikiwame.store.market import MarketStore
from mikiwame.store.sql import Store, is_web_user
from mikiwame.voters.address import AddressVoter
from mikiwame.voters.fees import FeeVoter
from mikiwame.voters.iban import IbanVoter
from mikiwame.voters.market import MarketVoter, facts
from mikiwame.voters.patterns import PatternVoter
from mikiwame.voters.phone import PhoneVoter
from mikiwame.voters.recidivism import RecidivismVoter
from mikiwame.voters.text import TextVoter

log = logging.getLogger(__name__)


def safe_error(stage: str, e: Exception) -> str:
    """What may be stored about a failure: the stage, the error type and an HTTP status.
    Never the message itself, which can carry model output or provider echoes of third-party
    data, and cases.error survives the retention purge (review 2026-10-01)."""
    status = re.search(r"\bHTTP (\d{3})\b", str(e))
    return f"{stage}: {type(e).__name__}" + (f" HTTP {status.group(1)}" if status else "")


# A shared link comes with a line of boilerplate ("Mira este anuncio en idealista"). Past a
# couple of other words ("¿es timo?") the user wrote something worth reading.
SHARE_WORDS = frozenset({
    "mira", "echa", "un", "vistazo", "he", "encontrado", "visto", "te", "paso", "comparto",
    "este", "esta", "anuncio", "inmueble", "piso", "casa", "vivienda", "habitacion", "habitación",
    "estudio", "ático", "atico", "en", "de", "del", "a", "al", "el", "la", "lo", "que", "y",
    "idealista", "fotocasa", "milanuncios", "habitaclia", "pisos", "com",
})  # fmt: skip
LINK_ONLY_MAX_WORDS = 2
UNKNOWN_LISTING = (
    "🔗 {what} no lo tengo en mi copia del mercado (solo guardo pisos y habitaciones en alquiler "
    "de idealista en la Comunidad de Madrid, de los barridos de cada semana), y el portal no lo "
    "abro desde aquí. Con solo el enlace no tengo nada que valorar.\n\n"
    "Mándame <b>capturas del anuncio</b> (precio, descripción, quién lo anuncia) y, sobre todo, "
    "<b>de la conversación con el anunciante</b>, y pulsa <b>Analizar</b>."
)
UNKNOWN_LISTING_EN = (
    "🔗 {what} is not in my copy of the market (I only keep flats and rooms for rent on "
    "idealista in the Comunidad de Madrid, from each week's sweeps), and I do not open the "
    "portal from here. With just the link I have nothing to assess.\n\n"
    "Send me <b>screenshots of the listing</b> (price, description, who advertises it) and, above "
    "all, <b>of the conversation with the advertiser</b>, and press <b>Analyse</b>."
)
FAILED = {
    "vision": {
        "es": "No he podido leer las capturas. Inténtalo de nuevo en un rato.",
        "en": "I could not read the screenshots. Please try again in a while.",
    },
    "empty": {
        "es": "No me has enviado nada que analizar: mándame capturas del chat, el texto del "
        "anuncio o el enlace.",
        "en": "You have not sent me anything to analyse: send me screenshots of the chat, the "
        "text of the listing or the link.",
    },
    "text": {
        "es": "No he podido completar el análisis ahora mismo. Inténtalo de nuevo en un rato.",
        "en": "I could not complete the analysis right now. Please try again in a while.",
    },
}


def _lang(lang: str) -> str:
    return "en" if lang == "en" else "es"


class AnalysisFailed(Exception):
    """Could not produce a trustworthy verdict. The message is user-facing, in the language the
    analysis was asked in."""


@dataclass
class Analysis:
    case: Case
    votes: list[Vote]
    verdict: Verdict
    report: str
    cost_usd: float


class Analyzer:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        router: LLMRouter,
        market: MarketStore | None = None,
        streets: StreetChecker | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.router = router
        self.market = MarketVoter(market)
        # Only the bot passes a checker: tests and evaluation/ never leave the house.
        self.address = AddressVoter(streets)
        self.vision = VisionExtractor(router, think=settings.think_vision)
        self.text_voter = TextVoter(router, think=settings.think_text)
        self.adversary = Adversary(
            router, think=settings.think_adversarial, effort=settings.effort_adversarial or None
        )

    def unknown_link_only(self, draft: Draft, lang: str = "es") -> str | None:
        """User-facing text when the draft is just a portal link we have no copy of.

        We never open the portal from here, so such a link alone leaves nothing to read: the
        bot says so before charging an analysis (05-10-2026: a shared idealista link outside
        the mirror came back as an amber "hay señales" with no signal at all).

        Only the share message itself may carry a few words of its own. Any other message, or
        anything added to a case already analysed, may be the advertiser's answer ("Sí", "Vale,
        el sábado"), and that is always analysed."""
        if draft.images() or draft.continues:
            return None
        if any(not tx.urls(t) for t in draft.texts()):
            return None
        text = "\n".join(draft.texts())
        case = enrich(Case(case_id=draft.case_id, pasted_text=text))
        if not case.listing_url or case.phones or case.ibans or case.emails:
            return None
        rest = tx.URL_RE.sub(" ", text)
        words = [w for w in re.findall(r"\w+", rest.lower()) if w not in SHARE_WORDS]
        if len(words) > LINK_ONLY_MAX_WORDS:
            return None
        if self.market.lookup(case) is not None:
            return None
        portal = (tx.listing_ref(case.listing_url) or "").partition(":")[0]
        if lang == "en":
            return UNKNOWN_LISTING_EN.format(
                what="That listing" if portal == "idealista" else f"{portal} listings"
            )
        return UNKNOWN_LISTING.format(
            what="Ese anuncio" if portal == "idealista" else f"Los anuncios de {portal}"
        )

    async def analyze_draft(self, draft: Draft, lang: str = "es") -> Analysis:
        try:
            case = await build_case(
                draft,
                vision=self.vision,
                max_images=self.settings.max_images,
                max_text_chars=self.settings.max_text_chars,
            )
        except LLMError as e:
            log.warning("case %s: vision failed: %s", draft.case_id, type(e).__name__)
            # Register what the texts already tell (the listing link, a phone) even though the
            # screenshots could not be read: the registry must not depend on vision working.
            text_only = enrich(
                Case(
                    case_id=draft.case_id,
                    pasted_text="\n\n".join(draft.texts())[: self.settings.max_text_chars],
                )
            )
            self._save_failed(text_only, draft.user_id, safe_error("vision", e), register=True)
            raise AnalysisFailed(FAILED["vision"][_lang(lang)]) from e
        return await self.analyze_case(case, tg_user_id=draft.user_id, lang=lang)

    async def analyze_case(
        self, case: Case, *, tg_user_id: int, persist: bool = True, lang: str = "es"
    ) -> Analysis:
        lang = _lang(lang)
        if not case.has_material():
            raise AnalysisFailed(FAILED["empty"][lang])
        listing = self.market.lookup(case)
        if listing is not None:
            presence = self.market.presence(listing)
            # Spanish for the text voter (its prompt is Spanish), English for the report.
            case.market_facts = facts(listing, presence=presence)
            case.market_facts_en = facts(listing, presence=presence, lang="en")
        reference = self.market.reference(case, listing)
        if reference is not None:
            case.market_reference, case.market_reference_en = reference[1], reference[2]
        indicators = case_indicators(case, self.settings.hmac_key.get_secret_value().encode())
        votes = [
            IbanVoter().vote_sync(case),
            PhoneVoter().vote_sync(case),
            PatternVoter().vote_sync(case),
            FeeVoter().vote_sync(case),
            RecidivismVoter(self.store, self.settings.allowed_users).vote_sync(
                case, indicators, tg_user_id
            ),
            self.market.vote_sync(case, listing, reference),
        ]
        # The street check (Nominatim, ~1 s per street) runs while the model reads the chat.
        address_vote = asyncio.create_task(self.address.vote(case))
        try:
            votes.append(await self.text_voter.vote(case))
        except LLMError as e:
            address_vote.cancel()
            # Without the text analysis a green would be unfounded: fail instead.
            log.warning("case %s: text voter failed: %s", case.case_id, e)
            if persist:
                self._save_failed(case, tg_user_id, safe_error("text", e))
            raise AnalysisFailed(FAILED["text"][lang]) from e
        votes.append(await address_vote)
        verdict = aggregate(votes, case)
        verdict = await self.adversary.review(case, verdict)
        en = lang == "en"
        report = render_report(
            verdict,
            channel=case.channel,
            market_facts=case.market_facts_en if en else case.market_facts,
            market_reference=case.market_reference_en if en else case.market_reference,
            lang=lang,
        )
        cost = sum(r["cost_usd"] for r in self.store.case_usage(case.case_id))
        if persist:
            self.store.save_case(
                case=redacted_case(case),
                tg_user_id=tg_user_id,
                retention_days=self.settings.retention_days,
                verdict=verdict,
                votes=votes,
                report_text=report,
                indicators=indicators,
                photo_hashes=case.photo_hashes,
            )
        if persist:
            feature_id = None
            try:
                feature_id = self.store.save_features(
                    features(
                        case,
                        verdict,
                        listing_known=listing is not None,
                        listing_removed=bool(listing and listing.removed_at),
                    )
                )
            except Exception:
                log.exception("could not record features of case %s", case.case_id)
            # Nobody to ask on the website: a follow-up there would never be answered.
            if not is_web_user(tg_user_id):
                try:
                    self.store.add_followup(case.case_id, feature_id, lang)
                except Exception:
                    log.exception("could not schedule the follow-up of case %s", case.case_id)
        log.info(
            "case %s: %s conf=%.2f score=%.2f cost=$%.5f signals=%s",
            case.case_id,
            verdict.level.key,
            verdict.confidence,
            verdict.score,
            cost,
            ",".join(s.code for s in verdict.signals),
        )
        return Analysis(case, votes, verdict, report, cost)

    def _save_failed(
        self, case: Case, tg_user_id: int, error: str, *, register: bool = False
    ) -> None:
        key = self.settings.hmac_key.get_secret_value().encode()
        try:
            self.store.save_case(
                case=redacted_case(case),
                tg_user_id=tg_user_id,
                retention_days=self.settings.retention_days,
                indicators=case_indicators(case, key) if register else (),
                error=error[:500],
            )
        except Exception:
            log.exception("could not record failed case %s", case.case_id)
