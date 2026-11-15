from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx

from mikiwame.llm.client import CallRecord, LLMClient, LLMRequest, Usage
from mikiwame.llm.registry import resolve
from mikiwame.llm.router import LLMRouter
from mikiwame.store import schema
from mikiwame.store.sql import Store, utcnow
from tests.conftest import make_settings, responses_ok

ENV = {"OPENCODE_BASE_URL": "https://llm.test/v1", "OPENCODE_API_KEY": "sk-test"}
OK = responses_ok()
URL = "https://llm.test/v1/responses"


@pytest.fixture
def router(tmp_path):
    settings = make_settings(
        model_adversarial="opencode-go:gpt-6-luna",
        model_adversarial_fallback="opencode-go:gpt-5.6-luna",
        budget_daily_usd_adversarial=0.05,
        budget_monthly_usd_per_model=0.50,
    )
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    return LLMRouter(settings, store, LLMClient(env=ENV, backoff_s=0, max_attempts=1))


def _models_called(route) -> list[str]:
    import json

    return [json.loads(c.request.content)["model"] for c in route.calls]


@respx.mock
async def test_falls_back_on_quota(router):
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(429, text="limit"), httpx.Response(200, json=OK)]
    )
    res = await router.call("adversarial", LLMRequest(system="S", user_text="U"), case_id="c1")
    assert res.ref.model.id == "gpt-5.6-luna"
    assert _models_called(route) == ["gpt-6-luna", "gpt-5.6-luna"]
    rows = router.store.case_usage("c1")
    assert [(r["model"], r["ok"], r["role"]) for r in rows] == [
        ("gpt-6-luna", False, "adversarial"),
        ("gpt-5.6-luna", True, "adversarial"),
    ]


@respx.mock
async def test_budget_switches_to_fallback(router):
    ref = resolve("opencode-go:gpt-6-luna")
    router.store.record_llm_call(None, "adversarial", CallRecord(ref, "s", Usage(), 0.06, 1, True))
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    await router.call("adversarial", LLMRequest(system="S", user_text="U"), case_id="c2")
    assert _models_called(route) == ["gpt-5.6-luna"]


@respx.mock
async def test_session_header_is_per_case(router):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    await router.call("adversarial", LLMRequest(system="S", user_text="U"), case_id="abc")
    assert route.calls.last.request.headers["x-opencode-session"] == "mikiwame-abc"


def _spend(router, model: str, usd: float, *, days_ago: float = 0) -> None:
    """One call on the text role; with days_ago, every recorded call is moved back in time."""
    ref = resolve(f"opencode-go:{model}")
    router.store.record_llm_call(None, "text", CallRecord(ref, "s", Usage(), usd, 1, True))
    if days_ago:
        with router.store.engine.begin() as conn:
            conn.execute(
                schema.llm_calls.update().values(created_at=utcnow() - dt.timedelta(days=days_ago))
            )


@respx.mock
async def test_monthly_budget_skips_a_model_across_roles(router):
    # Spent by another role over the month, not today: the shared plan cap is per model.
    _spend(router, "gpt-6-luna", 0.60, days_ago=10)
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=OK))
    await router.call("adversarial", LLMRequest(system="S", user_text="U"), case_id="m1")
    assert _models_called(route) == ["gpt-5.6-luna"]


def test_spend_older_than_30_days_does_not_count(router):
    _spend(router, "gpt-6-luna", 0.60, days_ago=31)
    assert [r.model.id for r in router.candidates("adversarial")] == ["gpt-6-luna", "gpt-5.6-luna"]


async def test_both_models_over_the_month_fail_without_calling(router):
    from mikiwame.llm.client import LLMError

    _spend(router, "gpt-6-luna", 0.60)
    _spend(router, "gpt-5.6-luna", 0.60)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(URL)
        with pytest.raises(LLMError, match="over budget"):
            await router.call("adversarial", LLMRequest(system="S", user_text="U"), case_id="m2")
        assert not route.called
