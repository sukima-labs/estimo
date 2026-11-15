"""Who may ask for an analysis, and how many. Checked before any LLM spend."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from mikiwame.config import Settings
from mikiwame.store.sql import PUBLIC, Store, utcnow

REASONS = {
    "not_allowed": {
        "es": "Mikiwame está en pruebas privadas por ahora.",
        "en": "Mikiwame is in a private test for now.",
    },
    "public_quota": {
        "es": "Ya has usado tus {n} análisis gratuitos. Si alguien que ya usa Mikiwame te "
        "invita, tendrás más; o escribe a info@patriciollorens.com.",
        "en": "You have used your {n} free analyses. If someone who already uses Mikiwame "
        "invites you, you get more; or write to info@patriciollorens.com.",
    },
    "member_quota": {
        "es": "Ya has usado tus {n} análisis de la beta. Si necesitas más para un caso concreto, "
        "escribe a info@patriciollorens.com.",
        "en": "You have used your {n} beta analyses. If you need more for a specific case, write "
        "to info@patriciollorens.com.",
    },
    "user_daily": {
        "es": "Has llegado al límite de {n} análisis en 24 horas. Vuelve a intentarlo más tarde.",
        "en": "You have reached the limit of {n} analyses in 24 hours. Please try again later.",
    },
    "web_visitor_daily": {
        "es": "Desde esta conexión ya se han hecho {n} análisis en 24 horas. Vuelve a intentarlo "
        "más tarde, o usa @EsTimoBot en Telegram.",
        "en": "{n} analyses have already been run from this connection in 24 hours. Please try "
        "again later, or use @EsTimoBot on Telegram.",
    },
    "web_daily": {
        "es": "La web ha llegado a su límite diario de análisis. Inténtalo mañana, o usa "
        "@EsTimoBot en Telegram.",
        "en": "The website has reached its daily limit of analyses. Please try tomorrow, or use "
        "@EsTimoBot on Telegram.",
    },
    "global_daily": {
        "es": "El servicio ha llegado a su límite diario de análisis. Inténtalo mañana.",
        "en": "The service has reached its daily limit of analyses. Please try tomorrow.",
    },
    "budget_total": {
        "es": "El servicio ha llegado a su límite diario de uso. Inténtalo mañana.",
        "en": "The service has reached its daily usage limit. Please try tomorrow.",
    },
}


def member_quota(settings: Settings, member) -> int | None:
    """Analyses this member may run in total: their own quota, else the default of their kind.
    None: no total, only the daily brakes."""
    if member["quota"] is not None:
        return member["quota"]
    return settings.public_quota if member["campaign"] == PUBLIC else settings.member_quota


@dataclass(frozen=True)
class Decision:
    allowed: bool
    # User-facing reason, in the language asked for.
    reason: str | None = None
    # Internal code, for logs and admin alerts.
    code: str | None = None


def _no(code: str, lang: str, n: int = 0) -> Decision:
    return Decision(False, REASONS[code]["en" if lang == "en" else "es"].format(n=n), code)


class Limiter:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store

    def is_root(self, tg_user_id: int) -> bool:
        return tg_user_id in self.settings.allowed_users

    def is_allowed_user(self, tg_user_id: int) -> bool:
        """Roots (env allowlist) and members who joined with an invite and were not revoked."""
        return self.is_root(tg_user_id) or self.store.is_member(tg_user_id)

    def check(self, tg_user_id: int, lang: str = "es") -> Decision:
        if not self.is_allowed_user(tg_user_id):
            return _no("not_allowed", lang)
        member = None if self.is_root(tg_user_id) else self.store.member(tg_user_id)
        if member is not None:
            public = member["campaign"] == PUBLIC
            quota = member_quota(self.settings, member)
            if quota is not None and member["analyses_used"] >= quota:
                return _no("public_quota" if public else "member_quota", lang, quota)
        since = utcnow() - dt.timedelta(hours=24)
        n = self.settings.limit_user_daily
        if self.store.count_usage_since(since, tg_user_id) >= n:
            return _no("user_daily", lang, n)
        return self._service(since, lang)

    def check_web(self, visitor_id: int, lang: str = "es") -> Decision:
        """A website visitor (negative id, one per IP): a daily brake per address, a daily cap
        for the whole website, then the same service-wide brakes as the bot."""
        since = utcnow() - dt.timedelta(hours=24)
        n = self.settings.web_limit_visitor_daily
        if self.store.count_usage_since(since, visitor_id) >= n:
            return _no("web_visitor_daily", lang, n)
        if self.store.count_usage_since(since, web=True) >= self.settings.web_limit_daily:
            return _no("web_daily", lang)
        return self._service(since, lang)

    def _service(self, since: dt.datetime, lang: str) -> Decision:
        if self.store.count_usage_since(since) >= self.settings.limit_global_daily:
            return _no("global_daily", lang)
        if self.store.spent_usd_since(since) >= self.settings.budget_daily_usd_total:
            return _no("budget_total", lang)
        return Decision(True)
