from __future__ import annotations

import datetime as dt

import pytest

from mikiwame.limits import Limiter
from mikiwame.llm.client import CallRecord, Usage
from mikiwame.llm.registry import resolve
from mikiwame.models import Case, Level, Signal, Verdict, Vote
from mikiwame.store.sql import Store, utcnow
from tests.conftest import make_settings


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(f"sqlite:///{tmp_path / 'db' / 't.db'}")


def _verdict() -> Verdict:
    return Verdict(
        level=Level.RED,
        confidence=0.8,
        score=0.7,
        signals=[Signal(code="pay_before_visit", weight=0.6, message="Pago antes de visitar")],
    )


def _save(store: Store, user: int, **kw) -> Case:
    case = Case(pasted_text="hola", phones=["+44 7700 900123"])
    store.save_case(
        case=case,
        tg_user_id=user,
        retention_days=kw.pop("retention_days", 30),
        verdict=kw.pop("verdict", _verdict()),
        votes=[Vote(voter="iban", score=0.5, confidence=0.5)],
        report_text="🔴",
        indicators=[("phone", "a" * 64), ("iban", "b" * 64)],
        **kw,
    )
    return case


def test_save_and_counts(store):
    case = _save(store, 7)
    row = store.get_case(case.case_id)
    assert row["level"] == "red" and row["signal_codes"] == "pay_before_visit"
    assert row["created_at"].tzinfo is not None
    since = utcnow() - dt.timedelta(hours=1)
    assert store.count_cases_since(since, 7) == 1
    assert store.count_cases_since(since, 8) == 0


def test_indicators_link_across_cases(store):
    first = _save(store, 7)
    second = _save(store, 8)
    assert store.indicator_case_count("phone", "a" * 64) == 2
    assert store.indicator_case_count("phone", "a" * 64, exclude_case=second.case_id) == 1
    assert first.case_id != second.case_id


def test_purge_keeps_stats_drops_pii(store):
    case = _save(store, 7, retention_days=0)
    assert store.purge_expired(utcnow() + dt.timedelta(seconds=1)) == 1
    row = store.get_case(case.case_id)
    assert row["case_json"] is None and row["report_text"] is None
    assert row["level"] == "red"
    assert store.purge_expired(utcnow() + dt.timedelta(seconds=2)) == 0


def test_delete_user_removes_cases_and_orphans(store):
    _save(store, 7)
    kept = _save(store, 8)
    assert store.delete_user(7) == 1
    assert store.indicator_case_count("phone", "a" * 64) == 1
    assert store.get_case(kept.case_id) is not None
    store.delete_user(8)
    assert store.indicator_case_count("phone", "a" * 64) == 0


def test_llm_spend(store):
    ref = resolve("opencode-go:gpt-6-luna")
    rec = CallRecord(ref, "s", Usage(1000, 100), 0.0045, 1200, ok=True)
    store.record_llm_call("c1", "adversarial", rec)
    store.record_llm_call("c1", "adversarial", rec)
    since = utcnow() - dt.timedelta(hours=1)
    assert store.spent_usd_since(since) == pytest.approx(0.009)
    assert store.spent_usd_since(since, "opencode-go:gpt-6-luna") == pytest.approx(0.009)
    assert store.spent_usd_since(since, "opencode-go:gpt-5.6-luna") == 0
    assert len(store.case_usage("c1")) == 2


def test_limiter(store):
    settings = make_settings(allowed_users="7", limit_user_daily=2, limit_global_daily=3)
    lim = Limiter(settings, store)
    assert lim.check(99).code == "not_allowed"
    assert lim.check(7).allowed
    store.record_usage(7)
    store.record_usage(7)
    assert lim.check(7).code == "user_daily"
    settings2 = make_settings(allowed_users="7,8", limit_global_daily=2)
    assert Limiter(settings2, store).check(8).code == "global_daily"


def test_deleting_cases_does_not_reset_limits(store):
    settings = make_settings(allowed_users="7", limit_user_daily=1)
    _save(store, 7)
    store.record_usage(7)
    store.delete_user(7)
    assert Limiter(settings, store).check(7).code == "user_daily"


def test_limiter_budget(store):
    settings = make_settings(allowed_users="7", budget_daily_usd_total=0.01)
    ref = resolve("opencode-go:gpt-6-luna")
    store.record_llm_call(None, "text", CallRecord(ref, "s", Usage(), 0.02, 1, ok=True))
    assert Limiter(settings, store).check(7).code == "budget_total"


def test_digest_aggregates_without_case_content(tmp_path):
    from mikiwame.digest import build

    url = f"sqlite:///{tmp_path / 'db' / 'd.db'}"
    store = Store(url)
    settings = make_settings(database_url=url, market_database_url=f"sqlite:///{tmp_path / 'm.db'}")
    assert "Ningún análisis" in build(settings)

    case = _save(store, 7)
    _save(store, 8)
    ref = resolve("opencode-go:gpt-6-luna")
    store.record_llm_call(
        case.case_id, "text", CallRecord(ref, "s", Usage(1000, 100), 0.1, 9, ok=True)
    )
    store.save_features(
        {"day": dt.date.today().isoformat(), "level": "red", "signal_codes": "pay_before_visit"}
    )
    body = build(settings)
    assert "Análisis: 2 (rojo 2) de 2 usuario(s)" in body
    # 0.1 $ on one of two reports: the max is over the 0.05 € goal and says so.
    assert "por encima del objetivo" in body
    assert "text · gpt-6-luna: 1 llamada(s), 0,1000 $" in body
    assert "pay_before_visit" in body and "copia del mercado: sin sincronizaciones" in body
    # Nothing from the cases themselves.
    assert "hola" not in body and "+44" not in body


def test_digest_counts_cases_deleted_with_borrar(tmp_path):
    # 05-10: Patricio's only case was deleted with /borrar; the digest said "Ningún análisis"
    # and then "1 casos analizados" under Modus operandi, with an empty "señales:" line.
    from mikiwame.digest import build

    url = f"sqlite:///{tmp_path / 'db' / 'd.db'}"
    store = Store(url)
    settings = make_settings(database_url=url, market_database_url=f"sqlite:///{tmp_path / 'm.db'}")
    now = dt.datetime.now(dt.UTC)
    for days_ago in (0, 7):  # the second one is on the first, partial day: last week's
        day = (now - dt.timedelta(days=days_ago)).date().isoformat()
        store.save_features({"day": day, "level": "amber", "channel": "unknown"})
    body = build(settings, now=now)
    assert "Análisis: 1, ya borrados por sus usuarios" in body
    assert "Ningún análisis" not in body
    assert "señales: ninguna" in body and "combinaciones" not in body
