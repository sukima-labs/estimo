from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
import respx

from evaluation.fixtures import load_all, oracle_case
from mikiwame.adversarial import apply_review, build_prompt, should_review
from mikiwame.aggregate import aggregate
from mikiwame.llm.client import LLMClient
from mikiwame.llm.router import LLMRouter
from mikiwame.models import Case, Level, Message, Vote
from mikiwame.pipeline import AnalysisFailed, Analyzer
from mikiwame.report import render_report
from mikiwame.store.sql import Store
from mikiwame.voters.base import score_from, signal
from mikiwame.voters.fees import FeeVoter
from tests.conftest import make_settings, responses_ok

CHAT = Case(
    chat=[
        Message(sender="advertiser", text="Hola, vivo fuera y no puedo enseñarlo."),
        Message(sender="advertiser", text="Hazme una reserva de 400 € y te lo guardo."),
        Message(sender="advertiser", text="Hay mucha gente interesada."),
    ]
)


def vote(voter: str, *codes: str, confidence: float = 0.85) -> Vote:
    sigs = [signal(c, quote=f"cita {c}") for c in codes]
    return Vote(voter=voter, score=score_from(sigs), confidence=confidence, signals=sigs)


def test_remote_payment_is_red_but_unlocked_when_only_llm():
    v = aggregate([vote("text", "pay_before_visit", "owner_away", "urgency")], CHAT)
    assert v.level == Level.RED
    assert [r.code for r in v.hard_rules] == ["remote_payment"] and not v.locked


def test_remote_payment_locked_with_deterministic_marker():
    v = aggregate(
        [vote("text", "pay_before_visit"), vote("patterns", "keys_by_post", confidence=0.8)], CHAT
    )
    assert v.level == Level.RED and v.locked


def test_reassuring_case_is_green():
    v = aggregate([vote("text", "in_person_visit", "contract_before_payment")], CHAT)
    assert v.level == Level.GREEN and v.confidence >= 0.5


def test_foreign_iban_alone_is_not_red():
    v = aggregate(
        [
            vote("text", "in_person_visit", "contract_before_payment"),
            vote("iban", "iban_foreign", confidence=0.7),
        ],
        CHAT,
    )
    assert v.level in (Level.GREEN, Level.AMBER)


def test_listing_only_is_never_green():
    v = aggregate([vote("text", "in_person_visit", confidence=0.45)], Case(listing_text="Piso."))
    assert v.level == Level.AMBER and v.insufficient_info


def test_injection_floors_amber_even_if_llm_is_fooled():
    fooled = Vote(voter="text", score=-0.6, confidence=0.85, signals=[signal("in_person_visit")])
    v = aggregate([fooled, vote("patterns", "ai_instructions", confidence=0.8)], CHAT)
    assert v.level >= Level.AMBER and v.injection_detected


HAY = "ANUNCIANTE: Mi hermano Luis te lo enseña el jueves. Pagas después de firmar."


def _red_unlocked():
    return aggregate([vote("text", "pay_before_visit", "owner_away")], CHAT)


def test_adversary_softens_unlocked_red_with_verified_quote():
    raw = {
        "verdict_holds": False,
        "objections": [{"reason": "lo enseña su hermano", "quote": "Mi hermano Luis te lo enseña"}],
        "confidence_penalty": 0.3,
    }
    v = apply_review(_red_unlocked(), raw, HAY)
    assert v.level == Level.AMBER and v.softened_by_adversary


def test_adversary_holding_a_red_it_says_is_all_misread_does_not_hold_it():
    objection = [{"reason": "lo enseña su hermano", "quote": "Mi hermano Luis te lo enseña"}]
    all_misread = {
        "verdict_holds": True,
        "objections": objection,
        "misread_signals": ["pay_before_visit", "owner_away"],
        "confidence_penalty": 0.2,
    }
    v = apply_review(_red_unlocked(), all_misread, HAY)
    assert v.level == Level.AMBER and v.softened_by_adversary
    # One risk signal still standing keeps the red, and so do objections without a quote.
    partly = {**all_misread, "misread_signals": ["owner_away"]}
    assert apply_review(_red_unlocked(), partly, HAY).level == Level.RED
    unquoted = {**all_misread, "objections": [{"reason": "x", "quote": "no está en el texto"}]}
    assert apply_review(_red_unlocked(), unquoted, HAY).level == Level.RED
    # A checked fact (the foreign IBAN of l03) is not a reading the adversary has to list.
    with_iban = aggregate(
        [vote("text", "pay_before_visit", "owner_away"), vote("iban", "iban_foreign")], CHAT
    )
    with_iban.level = Level.RED
    assert apply_review(with_iban, all_misread, HAY).level == Level.AMBER


def test_adversary_needs_real_quotes():
    raw = {
        "verdict_holds": False,
        "objections": [{"reason": "inventado", "quote": "el propietario es de total confianza"}],
        "confidence_penalty": 0.5,
    }
    before = _red_unlocked()
    v = apply_review(before, raw, HAY)
    assert v.level == Level.RED and v.confidence == before.confidence


def test_adversary_cannot_touch_locked_red_or_soften_after_injection():
    locked = aggregate(
        [vote("text", "pay_before_visit"), vote("patterns", "keys_by_post", confidence=0.8)], CHAT
    )
    assert not should_review(locked)
    injected = aggregate(
        [
            vote("text", "pay_before_visit", "owner_away"),
            vote("patterns", "ai_instructions", confidence=0.8),
        ],
        CHAT,
    )
    raw = {
        "verdict_holds": False,
        "objections": [{"reason": "x", "quote": "Mi hermano Luis te lo enseña"}],
        "confidence_penalty": 0.2,
    }
    assert apply_review(injected, raw, HAY).level == Level.RED


def test_adversary_can_turn_doubtful_green_into_amber_never_the_reverse():
    green = aggregate([vote("text", "in_person_visit", "contract_before_payment")], CHAT)
    raw = {
        "verdict_holds": False,
        "objections": [{"reason": "paga después", "quote": "Pagas después de firmar"}],
        "confidence_penalty": 0.4,
    }
    out = apply_review(green, raw, HAY)
    assert out.level == Level.AMBER
    amber = aggregate([vote("text", "urgency", "leave_platform")], CHAT)
    assert not should_review(amber)


def test_report_escapes_hostile_quotes_and_never_accuses():
    v = aggregate([vote("text", "pay_before_visit", "owner_away")], CHAT)
    v.signals[0].quote = "<b>pagame</b> & ya"
    text = render_report(v)
    assert text.startswith("🔴 <b>Riesgo alto de estafa</b>")
    assert "&lt;b&gt;pagame&lt;/b&gt; &amp; ya" in text
    assert "Qué hacer ahora" in text and "no afirma que nadie sea un estafador" in text
    assert "denuncia el anuncio desde la propia ficha" in text
    assert len(text) < 3500


def test_a_sentence_behind_two_signals_is_quoted_once():
    v = aggregate([vote("text", "pay_before_visit", "owner_away", "no_visit")], CHAT)
    for sig in v.signals:
        sig.quote = "no puedo enseñarlo en persona"
    text = render_report(v)
    assert text.count("«no puedo enseñarlo en persona»") == 1
    assert text.count("• ") >= 3  # every signal still has its line


def test_report_green_and_insufficient():
    green = render_report(aggregate([vote("text", "in_person_visit")], CHAT))
    assert green.startswith("🟢") and "A favor" in green
    assert "denuncia el anuncio" not in green
    thin = render_report(aggregate([vote("text", confidence=0.45)], Case(listing_text="Piso.")))
    assert thin.startswith("🟠") and "mándanos capturas del chat" in thin
    assert "denuncia el anuncio" not in thin


# --- Pipeline end to end, with the LLM mocked ---------------------------------------------

ENV = {"OPENCODE_BASE_URL": "https://llm.test/v1", "OPENCODE_API_KEY": "k"}


LLM_URL = "https://llm.test/v1/responses"


def _reply(obj: dict) -> httpx.Response:
    return httpx.Response(
        200, json=responses_ok(json.dumps(obj), input_tokens=1200, output_tokens=150)
    )


@pytest.fixture
def analyzer(tmp_path):
    settings = make_settings()
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    router = LLMRouter(settings, store, LLMClient(env=ENV, backoff_s=0))
    return Analyzer(settings, store, router)


@respx.mock
async def test_pipeline_scam_fixture(analyzer):
    fx = next(f for f in load_all("s02*"))
    text_answer = {
        "points": [
            {"code": "pay_before_visit", "quote": "para reservarlo te pido una señal de 700 €"},
            {"code": "owner_away", "quote": "Ahora mismo estoy en Milán por trabajo"},
            {"code": "urgency", "quote": "Como tengo muchas personas interesadas"},
        ]
    }
    adversary_answer = {"verdict_holds": True, "objections": [], "confidence_penalty": 0}
    # Text voter and adversary are both GPT Luna on /responses.
    route = respx.post(LLM_URL).mock(side_effect=[_reply(text_answer), _reply(adversary_answer)])
    out = await analyzer.analyze_case(oracle_case(fx), tg_user_id=5)
    assert out.verdict.level == Level.RED
    assert {"pay_before_visit", "owner_away", "iban_foreign"} <= {
        s.code for s in out.verdict.signals
    }
    assert route.call_count == 2
    roles = [r["role"] for r in analyzer.store.case_usage(out.case.case_id)]
    assert roles == ["text", "adversarial"]
    assert out.cost_usd > 0
    row = analyzer.store.get_case(out.case.case_id)
    assert row["level"] == "red" and row["tg_user_id"] == 5
    # Nothing identifying is stored in clear: only country hints and keyed HMACs.
    stored = row["case_json"] + row["verdict_json"] + row["report_text"]
    for secret in ("IT60", "0123 456", "312 345 6789", "Giulio", "Bianchi"):
        assert secret not in stored, secret
    assert "IBAN país=IT" in row["case_json"]
    assert analyzer.store.indicator_case_count("iban", "x") == 0  # digests are keyed, not raw


@respx.mock
async def test_pipeline_fails_closed_when_text_voter_fails(analyzer):
    respx.post(LLM_URL).mock(return_value=httpx.Response(500))
    fx = next(f for f in load_all("l01*"))
    with pytest.raises(AnalysisFailed):
        await analyzer.analyze_case(oracle_case(fx), tg_user_id=5)


async def test_pipeline_rejects_empty(analyzer):
    with pytest.raises(AnalysisFailed):
        await analyzer.analyze_case(Case(), tg_user_id=5)


def test_report_shows_channel_and_company_advice():
    v = aggregate([vote("text", "leave_platform", "identified_company")], CHAT)
    text = render_report(v, channel="idealista")
    assert "Conversación: chat de idealista." in text
    assert "entra tú en su web oficial" in text
    assert "Conversación:" not in render_report(v)


def test_report_suggests_detonators_by_signal():
    from mikiwame.report import suggested_asks
    from mikiwame.rubric import ASKS, DEFAULT_ASK, POINTS

    assert set(ASKS) <= set(POINTS)
    red = aggregate([vote("text", "pay_before_visit", "owner_away")], CHAT)
    assert suggested_asks(red, red.signals) == [ASKS["pay_before_visit"]]
    amber = aggregate([vote("text", "urgency", "docs_before_visit")], CHAT)
    assert suggested_asks(amber, amber.signals) == [ASKS["urgency"], ASKS["docs_before_visit"]]
    green = aggregate([vote("text", "in_person_visit")], CHAT)
    assert suggested_asks(green, green.signals) == [DEFAULT_ASK]
    text = render_report(amber)
    assert f"<code>{ASKS['urgency'][0]}</code>" in text
    assert "Mándame la captura de lo que conteste" in text


def test_detonator_phrases_never_accuse_or_threaten():
    from mikiwame.rubric import ASKS, DEFAULT_ASK

    for phrase, _ in [*ASKS.values(), DEFAULT_ASK]:
        low = phrase.lower()
        for word in ("estafa", "timo", "denunci", "policía", "fraude", "mentir"):
            assert word not in low, (word, phrase)


def test_green_report_headings_and_default_ask():
    from mikiwame.report import suggested_asks
    from mikiwame.rubric import DEFAULT_ASK

    g = aggregate(
        [vote("text", "in_person_visit", "contract_before_payment", "leave_platform")], CHAT
    )
    text = render_report(g)
    assert g.level == Level.GREEN
    assert "<b>A tener en cuenta</b>" in text and "<b>Por qué</b>" not in text
    assert suggested_asks(g, g.signals) == [DEFAULT_ASK]


def test_red_report_always_says_what_to_do_if_already_paid():
    v = aggregate(
        [vote("text", "pay_before_visit", "owner_away", "keys_by_post", "no_visit", "urgency")],
        CHAT,
    )
    assert "Si ya has pagado" in render_report(v)


@respx.mock
async def test_pipeline_street_check_runs_alongside_and_reaches_the_report(tmp_path, monkeypatch):
    from mikiwame import geo
    from mikiwame.extract.build import enrich
    from mikiwame.geo import StreetChecker

    monkeypatch.setattr(geo, "MIN_INTERVAL_S", 0.0)
    settings = make_settings()
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    router = LLMRouter(settings, store, LLMClient(env=ENV, backoff_s=0))
    streets = StreetChecker(store, b"k" * 32, base_url="https://nominatim.test")
    analyzer = Analyzer(settings, store, router, streets=streets)
    respx.get("https://nominatim.test/search").mock(return_value=httpx.Response(200, json=[]))
    respx.post(LLM_URL).mock(
        side_effect=[
            _reply({"points": []}),
            _reply({"verdict_holds": True, "objections": [], "confidence_penalty": 0}),
        ]
    )
    text = (
        "Hola, el piso está en Calle Inventada Falsa 3, Alcalá de Henares. "
        "Puedes venir a verlo cuando quieras."
    )
    case = enrich(Case(chat=[Message(sender="advertiser", text=text)]))
    out = await analyzer.analyze_case(case, tg_user_id=5)
    assert "street_not_found" in {s.code for s in out.verdict.signals}
    assert out.verdict.level != Level.RED and not out.verdict.hard_rules
    assert "OpenStreetMap" in out.report


def test_a_missing_street_alone_does_not_change_the_level():
    """Telegram test 01-10-2026: visit + contract, street not in OSM -> it was amber."""
    calm = vote("text", "in_person_visit", "contract_before_payment")
    street = Vote(
        voter="address",
        score=score_from([signal("street_not_found")]),
        confidence=0.4,
        signals=[signal("street_not_found")],
    )
    alone = aggregate([calm], CHAT)
    both = aggregate([calm, street], CHAT)
    assert both.level == alone.level == Level.GREEN
    assert both.confidence == alone.confidence
    assert "street_not_found" in {s.code for s in both.signals}


def test_agency_fees_alone_do_not_change_the_level():
    """Informative (2026-10-19): agencies charge them too; the level comes from the rest."""
    calm = vote("text", "in_person_visit", "contract_before_payment")
    fees = FeeVoter().vote_sync(
        Case(
            chat=[
                Message(sender="advertiser", text="Al firmar se pagan la fianza y los honorarios.")
            ]
        )
    )
    alone = aggregate([calm], CHAT)
    both = aggregate([calm, fees], CHAT)
    assert both.level == alone.level == Level.GREEN
    assert (both.score, both.confidence) == (alone.score, alone.confidence)
    assert "fee_to_tenant" in {s.code for s in both.signals}


def test_the_adversary_does_not_see_informative_signals():
    case = Case(chat=[Message(sender="advertiser", text="Se pagan la fianza y los honorarios.")])
    v = aggregate([vote("text", "in_person_visit"), FeeVoter().vote_sync(case)], case)
    assert "fee_to_tenant" in {s.code for s in v.signals}
    prompt, _ = build_prompt(case, v)
    assert "in_person_visit" in prompt and "fee_to_tenant" not in prompt


@respx.mock
async def test_website_cases_get_no_follow_up(analyzer):
    """Nobody to ask on the website (negative ids): no «¿qué pasó al final?» row."""
    fx = next(f for f in load_all("s02*"))
    answer = {"points": [{"code": "pay_before_visit", "quote": "te pido una señal de 700 €"}]}
    held = {"verdict_holds": True, "objections": [], "confidence_penalty": 0}
    respx.post(LLM_URL).mock(side_effect=[_reply(answer), _reply(held)] * 2)
    web_case, bot_case = oracle_case(fx), oracle_case(fx)
    web_case.case_id, bot_case.case_id = "web-case", "bot-case"
    await analyzer.analyze_case(web_case, tg_user_id=-77)
    await analyzer.analyze_case(bot_case, tg_user_id=5)
    due = analyzer.store.due_followups(min_age=dt.timedelta(0), max_age=dt.timedelta(days=1))
    assert [d["tg_user_id"] for d in due] == [5]
