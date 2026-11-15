"""Role-level model selection: primary vs fallback, daily budgets, usage logging."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from mikiwame.config import Role, Settings
from mikiwame.llm.client import CallRecord, LLMClient, LLMError, LLMRequest, LLMResult
from mikiwame.llm.registry import ModelRef
from mikiwame.store.sql import Store, utcnow

log = logging.getLogger(__name__)


def session_id_for(case_id: str) -> str:
    """Stable per case, as the provider asks (routing and prompt caching)."""
    return f"mikiwame-{case_id}"


class LLMRouter:
    def __init__(self, settings: Settings, store: Store, client: LLMClient) -> None:
        self.settings = settings
        self.store = store
        self.client = client

    def candidates(self, role: Role) -> list[ModelRef]:
        """Primary then fallback, skipping any the model policy refuses today (a ZDR
        agreement can lapse while the bot runs; startup validation is not enough) and any past
        its monthly budget (the plan's cap on that model is shared by the whole house)."""
        primary = self.settings.models[(role, False)]
        fallback = self.settings.models.get((role, True))
        month = utcnow() - dt.timedelta(days=30)
        allowed = []
        for r in (primary, fallback):
            if r is None or r.model.refusal() is not None:
                continue
            spent = self.store.spent_usd_since(month, r.label)
            if spent >= self.settings.budget_monthly_usd_per_model:
                log.warning("%s: %s over its monthly budget (%.4f USD)", role, r.label, spent)
                continue
            allowed.append(r)
        if primary not in allowed:
            return [r for r in allowed if r is not primary]
        if fallback is None or fallback not in allowed:
            return [primary]
        since = utcnow() - dt.timedelta(hours=24)
        spent = self.store.spent_usd_since(since, primary.label)
        if spent >= self.settings.role_budget_usd(role):
            log.info("%s: primary %s over daily budget (%.4f USD)", role, primary.label, spent)
            return [fallback]
        return [primary, fallback]

    async def call(
        self,
        role: Role,
        req: LLMRequest,
        *,
        case_id: str,
        parse: Callable[[str], Any] | None = None,
    ) -> LLMResult:
        """Call the role's model, falling back to the next candidate on errors, and also when
        `parse` rejects the answer (raises LLMError): a 200 with an unusable answer is a failure
        of that model, not a verdict. The parsed value is in `result.parsed`."""

        def record(rec: CallRecord) -> None:
            self.store.record_llm_call(case_id, role, rec)

        refs = self.candidates(role)
        if not refs:
            raise LLMError(f"{role}: no usable model (refused by the policy or over budget)")
        last: LLMError | None = None
        for ref in refs:
            try:
                res = await self.client.complete(
                    ref, req, session_id=session_id_for(case_id), recorder=record
                )
                if parse is not None:
                    res = replace(res, parsed=parse(res.text))
                return res
            except LLMError as e:
                log.warning("%s: %s failed (%s)", role, ref.label, e)
                last = e
            except (TypeError, ValueError, KeyError, AttributeError) as e:
                # A well-formed JSON of the wrong shape: treat like any unusable answer.
                log.warning(
                    "%s: %s answer had the wrong shape (%s)", role, ref.label, type(e).__name__
                )
                last = LLMError(f"wrong answer shape: {type(e).__name__}")
        assert last is not None
        raise last
