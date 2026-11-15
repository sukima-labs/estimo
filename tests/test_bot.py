from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mikiwame.bot import texts, texts_en
from mikiwame.bot.app import MikiwameBot
from mikiwame.extract.build import DraftItem
from mikiwame.pipeline import AnalysisFailed
from mikiwame.store.sql import Store, utcnow
from tests.conftest import make_settings


def fake_update(uid: int, text: str | None = None):
    message = MagicMock()
    message.text = text
    message.photo = []
    message.document = None
    message.caption = None
    message.reply_text = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
    progress = MagicMock(edit_text=AsyncMock())
    chat = SimpleNamespace(id=uid, send_message=AsyncMock(return_value=progress))
    upd = SimpleNamespace(
        effective_user=SimpleNamespace(id=uid, username="u"),
        effective_message=message,
        effective_chat=chat,
        callback_query=None,
        get_bot=lambda: MagicMock(send_chat_action=AsyncMock()),
    )
    return upd, progress


def _result(report: str, level: str, case_id: str = "c1"):
    return SimpleNamespace(
        report=report,
        verdict=SimpleNamespace(level=SimpleNamespace(key=level)),
        case=SimpleNamespace(case_id=case_id),
    )


@pytest.fixture
def bot(tmp_path):
    settings = make_settings(allowed_users="7", admin_chat_id=None)
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    analyzer = MagicMock()
    analyzer.analyze_draft = AsyncMock()
    analyzer.unknown_link_only = MagicMock(return_value=None)
    return MikiwameBot(settings, store, analyzer)


async def test_strangers_get_one_reply_and_no_work(bot):
    upd, _ = fake_update(99, "hola")
    await bot.on_text(upd, None)
    await bot.on_text(upd, None)
    upd.effective_message.reply_text.assert_awaited_once_with(texts.CLOSED)
    assert 99 not in bot.drafts


async def test_text_goes_to_draft_with_status(bot):
    upd, _ = fake_update(7, "https://www.idealista.com/inmueble/1/")
    await bot.on_text(upd, None)
    assert bot.drafts[7].texts() == ["https://www.idealista.com/inmueble/1/"]
    sent = upd.effective_message.reply_text.await_args
    assert "1 texto" in sent.args[0] and sent.kwargs["reply_markup"] is not None


async def test_analyze_empty_draft(bot):
    upd, _ = fake_update(7)
    await bot.analyze(upd)
    upd.effective_chat.send_message.assert_awaited_with(texts.EMPTY_DRAFT)


async def test_analyze_replies_with_report_and_clears_draft(bot):
    upd, progress = fake_update(7, "texto")
    bot.draft(7).items.append(DraftItem("text", text="Hazme un Bizum antes de verlo"))
    bot.analyzer.analyze_draft.return_value = _result("🔴 <b>Riesgo alto</b>", "red")
    await bot.analyze(upd)
    progress.edit_text.assert_awaited_once()
    assert progress.edit_text.await_args.args[0].startswith("🔴")
    assert 7 not in bot.drafts and 7 not in bot.busy


async def test_analysis_failure_is_explained(bot):
    upd, progress = fake_update(7)
    bot.draft(7).items.append(DraftItem("text", text="x"))
    bot.analyzer.analyze_draft.side_effect = AnalysisFailed("No he podido leer las capturas.")
    await bot.analyze(upd)
    assert progress.edit_text.await_args.args[0] == "No he podido leer las capturas."


async def test_limit_blocks_before_spending(bot):
    bot.settings = make_settings(allowed_users="7", limit_user_daily=0, admin_chat_id=None)
    bot.limiter.settings = bot.settings
    upd, _ = fake_update(7)
    bot.draft(7).items.append(DraftItem("text", text="x"))
    await bot.analyze(upd)
    bot.analyzer.analyze_draft.assert_not_awaited()
    assert "límite" in upd.effective_chat.send_message.await_args.args[0]


def test_texts_what():
    assert texts.what(1, 0) == "1 captura"
    assert texts.what(2, 1) == "2 capturas y 1 texto"


async def test_follow_up_joins_previous_case_until_new_case(bot):
    upd, progress = fake_update(7, "primera captura")
    bot.draft(7).items.append(DraftItem("text", text="chat inicial"))
    bot.analyzer.analyze_draft.return_value = _result("🟠 <b>Precaución</b>", "amber")
    await bot.analyze(upd)
    assert progress.edit_text.await_args.kwargs["reply_markup"] is not None
    upd2, _ = fake_update(7, "su respuesta")
    await bot.on_text(upd2, None)
    d = bot.drafts[7]
    assert d.continues and d.texts() == ["chat inicial", "su respuesta"]
    assert "Lo sumo al caso anterior" in upd2.effective_message.reply_text.await_args.args[0]
    bot.reset(7)
    assert 7 not in bot.previous and not bot.draft(7).items


async def test_failed_analysis_keeps_the_draft_for_retry(bot):
    upd, _ = fake_update(7)
    bot.draft(7).items.append(DraftItem("text", text="x"))
    bot.analyzer.analyze_draft.side_effect = AnalysisFailed("falló")
    await bot.analyze(upd)
    assert bot.drafts[7].texts() == ["x"] and 7 not in bot.previous


async def test_status_asks_for_the_listing_link_until_it_arrives(bot):
    upd, _ = fake_update(7, "Hola, me han pedido una señal")
    await bot.on_text(upd, None)
    assert "enlace del anuncio" in upd.effective_message.reply_text.await_args.args[0]
    upd2, _ = fake_update(7, "https://www.idealista.com/inmueble/98765432/")
    await bot.on_text(upd2, None)
    assert "enlace del anuncio" not in upd2.effective_message.reply_text.await_args.args[0]


async def test_red_and_amber_reports_offer_research_opt_in(bot):
    upd, progress = fake_update(7, "x")
    bot.draft(7).items.append(DraftItem("text", text="x"))
    bot.analyzer.analyze_draft.return_value = _result("🔴", "red", case_id="abc")
    await bot.analyze(upd)
    rows = progress.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert [r[0].callback_data for r in rows] == ["new_case", "keep:abc"]
    bot.draft(7).items.append(DraftItem("text", text="y"))
    bot.analyzer.analyze_draft.return_value = _result("🟢", "green")
    await bot.analyze(upd)
    rows = progress.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert [r[0].callback_data for r in rows] == ["new_case"]


async def test_invite_link_lets_a_stranger_in_and_members_invite_once_each(bot):
    ctx = SimpleNamespace(args=[], bot=SimpleNamespace(username="EsTimoBot"))
    root, _ = fake_update(7, "/invitar")
    await bot.on_invite(root, ctx)
    sent = root.effective_message.reply_text.await_args.args[0]
    assert "https://t.me/EsTimoBot?start=inv_" in sent and "Te quedan" not in sent
    code = sent.split("start=inv_")[1].split()[0]

    friend, _ = fake_update(42, "/start")
    await bot.on_start(friend, SimpleNamespace(args=[f"inv_{code}"], bot=ctx.bot))
    replies = [c.args[0] for c in friend.effective_message.reply_text.await_args_list]
    assert replies[0] == texts.INVITE_WELCOME and "Mikiwame" in replies[1]
    assert bot.limiter.is_allowed_user(42)

    again, _ = fake_update(43, "/start")
    await bot.on_start(again, SimpleNamespace(args=[f"inv_{code}"], bot=ctx.bot))
    again.effective_message.reply_text.assert_awaited_once_with(texts.INVITE_INVALID)
    assert not bot.limiter.is_allowed_user(43)

    for left in ("Te quedan 1", "Te quedan 0"):
        upd, _ = fake_update(42, "/invitar")
        await bot.on_invite(upd, ctx)
        assert left in upd.effective_message.reply_text.await_args.args[0]
    upd, _ = fake_update(42, "/invitar")
    await bot.on_invite(upd, ctx)
    upd.effective_message.reply_text.assert_awaited_once_with(texts.INVITE_NONE_LEFT)


async def test_invite_when_the_beta_is_full(tmp_path):
    settings = make_settings(allowed_users="7", admin_chat_id=None, max_members=0)
    bot = MikiwameBot(settings, Store(f"sqlite:///{tmp_path / 'f.db'}"), MagicMock())
    upd, _ = fake_update(7, "/invitar")
    await bot.on_invite(upd, SimpleNamespace(args=[], bot=SimpleNamespace(username="b")))
    upd.effective_message.reply_text.assert_awaited_once_with(texts.BETA_FULL)


def _analysed(store: Store, uid: int, level: str, days_ago: float) -> str:
    import datetime as dt

    from sqlalchemy import update

    from mikiwame.models import Case, Level, Verdict
    from mikiwame.store import schema

    case = Case(pasted_text="x")
    store.save_case(
        case=case,
        tg_user_id=uid,
        retention_days=30,
        verdict=Verdict(level=Level.from_key(level), confidence=0.8, score=0.5),
    )
    fid = store.save_features({"day": "2026-10-01", "level": level})
    store.add_followup(case.case_id, fid)
    when = dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago)
    with store.engine.begin() as conn:
        conn.execute(
            update(schema.cases).where(schema.cases.c.id == case.case_id).values(created_at=when)
        )
    return case.case_id


async def test_followup_asks_once_per_user_and_records_anonymously(bot):
    old = _analysed(bot.store, 7, "red", 6.5)
    latest = _analysed(bot.store, 7, "red", 5.5)
    _analysed(bot.store, 7, "amber", 2)  # too recent
    _analysed(bot.store, 99, "green", 6)  # not allowed any more: skipped
    tg = MagicMock(send_message=AsyncMock())
    night = dt.datetime.now().astimezone().replace(hour=1, minute=36)
    assert await bot.ask_followups(tg, now=night) == 0  # it would buzz the phone at night
    tg.send_message.assert_not_awaited()
    noon = night.replace(hour=12)
    assert await bot.ask_followups(tg, now=noon) == 1
    (uid, text), kw = tg.send_message.await_args
    assert uid == 7 and "riesgo alto" in text
    datas = [b.callback_data for row in kw["reply_markup"].inline_keyboard for b in row]
    assert datas == [f"out:{latest}:scam", f"out:{latest}:legit", f"out:{latest}:unknown"]
    assert await bot.ask_followups(tg, now=noon) == 0  # never twice
    bot.alert = MagicMock()

    async def press(uid: int, data: str):
        upd, _ = fake_update(uid)
        q = SimpleNamespace(
            data=data, answer=AsyncMock(), message=upd.effective_message,
            edit_message_reply_markup=AsyncMock(),
        )  # fmt: skip
        upd.callback_query = q
        await bot.on_button(upd, None)
        return upd.effective_message.reply_text.await_args.args[0]

    assert await press(7, f"out:{old}:legit") == texts.FOLLOWUP_DONE  # never asked
    assert await press(7, f"out:{latest}:legit") == texts.FOLLOWUP_THANKS_SORRY
    bot.alert.assert_called_once()  # a real landlord in red reaches Patricio
    assert await press(7, f"out:{latest}:scam") == texts.FOLLOWUP_DONE  # once
    assert bot.store.outcome_counts() == {("red", "legit"): 1}
    bot.store.delete_user(7)  # /borrar: the anonymous answer stays
    assert bot.store.outcome_counts() == {("red", "legit"): 1}


def test_member_quota_for_the_whole_beta(tmp_path):
    from mikiwame.limits import Limiter

    settings = make_settings(allowed_users="7", limit_user_daily=50, member_quota=2)
    store = Store(f"sqlite:///{tmp_path / 'q.db'}")
    store.redeem_invite(store.create_invite(7, ttl_days=7, unlimited=True), 8, invites_for_new=2)
    limiter = Limiter(settings, store)
    for _ in range(2):
        assert limiter.check(8).allowed
        store.record_usage(8)
    d = limiter.check(8)
    assert not d.allowed and d.code == "member_quota" and "info@patriciollorens.com" in d.reason
    store.set_member(8, quota=3)  # Patricio raises it for this person
    assert limiter.check(8).allowed
    for _ in range(5):
        store.record_usage(7)
    assert limiter.check(7).allowed  # roots have no beta quota


def test_member_list_shows_each_kind_of_quota(tmp_path):
    from mikiwame.limits import member_quota

    settings = make_settings(allowed_users="7", member_quota=30, public_quota=5)
    store = Store(f"sqlite:///{tmp_path / 'k.db'}")
    store.redeem_invite(store.create_invite(7, ttl_days=7, unlimited=True), 8, invites_for_new=2)
    store.join_public(9)
    quotas = {m["tg_user_id"]: member_quota(settings, m) for m in store.members_list()}
    assert quotas == {8: 30, 9: 5}
    store.set_member(9, quota=0)  # a quota of 0 is a quota, not "unset"
    assert {m["tg_user_id"]: member_quota(settings, m) for m in store.members_list()}[9] == 0


async def test_open_access_lets_strangers_in_with_a_small_quota(tmp_path):
    settings = make_settings(
        allowed_users="7", admin_chat_id=None, open_access=True, public_quota=5
    )
    store = Store(f"sqlite:///{tmp_path / 'o.db'}")
    analyzer = MagicMock()
    analyzer.analyze_draft = AsyncMock()
    analyzer.unknown_link_only = MagicMock(return_value=None)
    bot = MikiwameBot(settings, store, analyzer)
    ctx = SimpleNamespace(args=[], bot=SimpleNamespace(username="EsTimoBot"))
    upd, _ = fake_update(99, "/start")
    await bot.on_start(upd, ctx)
    replies = [c.args[0] for c in upd.effective_message.reply_text.await_args_list]
    assert replies[0] == texts.PUBLIC_WELCOME_QUOTA.format(n=5) and "Mikiwame" in replies[1]
    assert bot.limiter.is_allowed_user(99) and store.beta_seats_taken() == 0
    for _ in range(5):
        assert bot.limiter.check(99).allowed
        store.record_usage(99, campaign_invites=2)
    d = bot.limiter.check(99)
    assert not d.allowed and d.code == "public_quota"
    assert store.member(99)["invites_left"] == 0  # public members do not invite
    # ...but /invitar tells them the plain link is enough.
    share, _ = fake_update(99, "/invitar")
    await bot.on_invite(share, ctx)
    share.effective_message.reply_text.assert_awaited_once()
    sent = share.effective_message.reply_text.await_args.args[0]
    assert "https://t.me/EsTimoBot" in sent and "inv_" not in sent and "5 análisis" in sent

    # An invite upgrades them: member quota, a seat, usage kept.
    root, _ = fake_update(7, "/invitar")
    await bot.on_invite(root, ctx)
    code = root.effective_message.reply_text.await_args.args[0].split("start=inv_")[1].split()[0]
    again, _ = fake_update(99, "/start")
    await bot.on_start(again, SimpleNamespace(args=[f"inv_{code}"], bot=ctx.bot))
    assert again.effective_message.reply_text.await_args_list[0].args[0] == texts.INVITE_WELCOME
    m = store.member(99)
    assert m["campaign"] is None and m["invited_by"] == 7 and m["analyses_used"] == 5
    assert bot.limiter.check(99).code == "user_daily"  # past the quota: only today's brake
    assert store.beta_seats_taken() == 1

    # A revoked public member stays out.
    store.join_public(50)
    store.set_member(50, revoked=True)
    out, _ = fake_update(50, "hola")
    await bot.on_text(out, None)
    out.effective_message.reply_text.assert_awaited_once_with(texts.CLOSED)


async def test_open_access_is_free_with_only_the_daily_limit_by_default(tmp_path):
    from sqlalchemy import update

    from mikiwame.limits import member_quota
    from mikiwame.store import schema

    settings = make_settings(allowed_users="7", admin_chat_id=None, open_access=True)
    store = Store(f"sqlite:///{tmp_path / 'f.db'}")
    analyzer = MagicMock()
    analyzer.unknown_link_only = MagicMock(return_value=None)
    bot = MikiwameBot(settings, store, analyzer)
    ctx = SimpleNamespace(args=[], bot=SimpleNamespace(username="EsTimoBot"))
    upd, _ = fake_update(99, "/start")
    await bot.on_start(upd, ctx)
    replies = [c.args[0] for c in upd.effective_message.reply_text.await_args_list]
    assert replies[0] == texts.PUBLIC_WELCOME.format(daily=5)
    assert member_quota(settings, store.member(99)) is None
    for _ in range(5):
        assert bot.limiter.check(99).allowed
        store.record_usage(99)
    assert bot.limiter.check(99).code == "user_daily"  # the security limit stays
    with store.engine.begin() as conn:  # a day later: no total quota to run out of
        conn.execute(update(schema.usage).values(created_at=utcnow() - dt.timedelta(days=2)))
    assert bot.limiter.check(99).allowed and store.member(99)["analyses_used"] == 5
    store.set_member(99, quota=5)  # Patricio can still cap one person
    assert bot.limiter.check(99).code == "public_quota"
    share, _ = fake_update(99, "/invitar")
    await bot.on_invite(share, ctx)
    sent = share.effective_message.reply_text.await_args.args[0]
    assert "es gratis" in sent and "5 análisis al día" in sent


def test_long_admin_messages_are_split_between_lines():
    import json

    import httpx
    import respx

    from mikiwame.bot.alert import send_admin, split_text

    text = "\n".join(f"línea {n:03d} " + "x" * 50 for n in range(150))
    chunks = split_text(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks) == text  # nothing lost, cuts only at line ends
    assert split_text("a" * 2500, 1000) == ["a" * 1000, "a" * 1000, "a" * 500]
    assert split_text("") == [""]

    # The first weekly digest (05-10, 5.536 characters) was cut at 4.000: the tail never arrived.
    with respx.mock:
        route = respx.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        assert send_admin("t", 1, text, label="Mikiwame (pruebas)", icon="📬")
    bodies = [json.loads(c.request.content)["text"] for c in route.calls]
    assert len(bodies) == 3 and all(len(b) <= 4096 for b in bodies)
    assert bodies[0].startswith("📬 Mikiwame (pruebas) (1/3): línea 000")
    assert "línea 149" in bodies[-1]


def test_admin_alerts_go_to_kchat_once_its_webhook_is_installed(tmp_path):
    import json
    import stat

    import httpx
    import pytest
    import respx

    from mikiwame.bot import alert

    hook = "https://abc-1.kchat.infomaniak.com/hooks/AbC-123"
    for bad in (
        "http://abc.kchat.infomaniak.com/hooks/x",
        "https://evil.com/hooks/x",
        f"{hook}/../x",
        f"{hook}?a=1",
    ):
        with pytest.raises(ValueError):
            alert.save_kchat_webhook(bad)
    path = alert.save_kchat_webhook(f"  {hook}\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert alert.kchat_webhook() == hook

    text = "📊 Resumen\n    3× contract_before_payment\n# no es un título\nver https://x.es/a_b_c"
    with respx.mock:
        tg = respx.post(url__regex=r"https://api\.telegram\.org/.*")
        kc = respx.post(hook).mock(return_value=httpx.Response(201, text="ok"))  # kChat: 201
        assert alert.send_admin("t", 1, text, label="Mikiwame (pruebas)", icon="📬")
    assert kc.call_count == 1 and not tg.called  # once, and Telegram is paused for Patricio
    body = json.loads(kc.calls[0].request.content)["text"]
    assert body.startswith("📬 **Mikiwame (pruebas)**: 📊 Resumen\n")
    assert " " * 4 + "3× contract\\_before\\_payment" in body  # no code block
    assert "\n\\# no es un título" in body and "https://x.es/a_b_c" in body

    # Two failed tries: no Telegram fallback, the text is kept locally.
    with respx.mock:
        tg = respx.post(url__regex=r"https://api\.telegram\.org/.*")
        kc = respx.post(hook).mock(return_value=httpx.Response(500))
        assert not alert.send_admin("t", 1, "se cayó", key="k")
    assert kc.call_count == 2 and not tg.called
    assert "se cayó" in alert.UNDELIVERED_LOG.read_text()
    assert stat.S_IMODE(alert.UNDELIVERED_LOG.stat().st_mode) == 0o600


async def test_a_bare_unknown_link_is_neither_charged_nor_analysed(bot):
    bot.analyzer.unknown_link_only.return_value = "🔗 Ese anuncio no lo tengo"
    upd, _ = fake_update(7, "https://www.idealista.com/inmueble/1/")
    await bot.on_text(upd, None)
    assert "🔗 Ese anuncio no lo tengo" in upd.effective_message.reply_text.await_args.args[0]
    await bot.analyze(upd)
    sent = upd.effective_chat.send_message.await_args.args[0]
    assert sent == texts.UNKNOWN_LINK_AGAIN and texts.NOT_CHARGED in sent
    bot.analyzer.analyze_draft.assert_not_awaited()
    assert bot.store.count_usage_since(dt.datetime(2000, 1, 1, tzinfo=dt.UTC)) == 0
    assert bot.drafts[7].texts()  # kept, so screenshots can be added to it


async def test_events_are_logged_without_content(bot, caplog):
    caplog.set_level("INFO", logger="mikiwame.bot.app")
    bot.analyzer.analyze_draft.return_value = _result("🟠 <b>Precaución</b>", "amber")
    upd, _ = fake_update(7, "Me pide 300 € por Bizum, IBAN ES9121000418450200051332")
    await bot.on_text(upd, None)
    await bot.analyze(upd)
    logged = caplog.text
    assert "user 7: draft" in logged and "1 text(s)" in logged
    assert "analysis charged and started" in logged and "amber report, delivered" in logged
    assert "Bizum" not in logged and "ES91" not in logged and "Precaución" not in logged


async def test_a_start_that_never_got_going_is_reported_next_time(bot, tmp_path):
    bot.marker = tmp_path / "running-test"
    bot.alert = MagicMock()
    bot._housekeeping = AsyncMock()
    # First start: no marker, nothing to report; killed while waiting for Telegram.
    bot.claim_marker()
    await bot.post_shutdown(MagicMock())
    assert bot.marker.exists()
    # Next start gets through: it says it restarted after an unexpected stop.
    bot.claim_marker()
    await bot.post_init(MagicMock())
    bot.alert.assert_called_once()
    await bot.post_shutdown(MagicMock())
    assert not bot.marker.exists()
    # After a clean stop, the next start is quiet.
    bot.alert.reset_mock()
    bot.claim_marker()
    await bot.post_init(MagicMock())
    bot.alert.assert_not_called()
    await bot.post_shutdown(MagicMock())


async def test_a_lone_409_is_not_alerted_but_repeated_ones_are(bot):
    from telegram.error import Conflict

    bot.alert = MagicMock()
    ctx = MagicMock()
    ctx.error = Conflict("terminated by other getUpdates request")
    # One or two: a poll stranded by a change of uplink (2026-10-06), gone by itself.
    await bot.on_error(None, ctx)
    await bot.on_error(None, ctx)
    bot.alert.assert_not_called()
    # A real second poller keeps conflicting.
    await bot.on_error(None, ctx)
    bot.alert.assert_called_once()
    assert "409" in bot.alert.call_args.args[0]


def _english(upd):
    upd.effective_user.language_code = "en-GB"
    return upd


async def test_an_english_telegram_gets_english(bot):
    upd, _ = fake_update(7, "Hi, is this a scam?")
    await bot.on_text(_english(upd), None)
    sent = upd.effective_message.reply_text.await_args
    assert "I have 1 text" in sent.args[0]
    upd2, progress = fake_update(7)
    bot.analyzer.analyze_draft.return_value = _result("🔴 <b>High risk</b>", "red")
    await bot.analyze(_english(upd2))
    assert bot.analyzer.analyze_draft.await_args.args[1] == "en"


async def test_catalan_and_unknown_apps_stay_in_spanish(bot):
    from mikiwame.bot.app import app_lang

    assert app_lang("ca") == app_lang("es-ES") == app_lang(None) == "es"
    assert app_lang("en") == app_lang("fr") == app_lang("it") == "en"


async def test_a_chosen_language_wins_and_goes_with_borrar(bot):
    upd, _ = fake_update(7, "/english")
    await bot.on_lang(upd, None)
    upd.effective_message.reply_text.assert_awaited_with(texts_en.LANG_SET)
    assert bot.store.chosen_lang(7) == "en"
    # A Spanish app no longer decides; a fresh process reads the choice back.
    bot.chosen_lang.clear()
    assert bot.lang(7) == "en"
    upd, _ = fake_update(7, "/espanol")
    await bot.on_lang(upd, None)
    assert bot.lang(7) == "es"
    bot.store.delete_user(7)
    assert bot.store.chosen_lang(7) is None


def test_every_bot_text_has_its_english():
    names = {n for n in dir(texts) if n.isupper()}
    assert names <= {n for n in dir(texts_en) if n.isupper()}
    assert set(texts_en.FOLLOWUP_LEVEL) == set(texts.FOLLOWUP_LEVEL)


async def test_start_offers_the_example_and_it_costs_nothing(bot):
    upd, _ = fake_update(7, "/start")
    await bot.on_start(upd, SimpleNamespace(args=[], bot=MagicMock(username="EsTimoBot")))
    markup = upd.effective_message.reply_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "example"

    upd, _ = fake_update(7)
    upd.callback_query = SimpleNamespace(data="example", answer=AsyncMock())
    await bot.on_button(upd, None)
    sent = [c.args[0] for c in upd.effective_message.reply_text.await_args_list]
    assert len(sent) == 3
    assert sent[0].startswith("<b>Ejemplo inventado</b>") and "DE89 3704" in sent[0]
    assert sent[1].startswith("🔴") and "no afirma que nadie sea un estafador" in sent[1]
    assert sent[2] == texts.EXAMPLE_OUTRO
    bot.analyzer.analyze_draft.assert_not_awaited()
    assert bot.store.count_usage_since(dt.datetime(2000, 1, 1, tzinfo=dt.UTC), 7) == 0
    assert 7 not in bot.drafts


async def test_the_example_speaks_english_to_english_users(bot):
    bot.chosen_lang[7] = "en"
    upd, _ = fake_update(7, "/example")
    await bot.on_example(upd, None)
    sent = [c.args[0] for c in upd.effective_message.reply_text.await_args_list]
    assert sent[0].startswith("<b>Invented example</b>") and "Erasmus" in sent[0]
    assert sent[1].startswith("🔴 <b>High risk")
    assert sent[2] == texts_en.EXAMPLE_OUTRO


def test_admin_alerts_go_through_the_host_gate_first(tmp_path, monkeypatch):
    """08-11-2026 (Patricio): a host gate command first; rc 0 (WhatsApp) or 10
    (host's kChat) = done and no kChat post of our own; rc 1/2, no ssh or a timeout = the
    kChat webhook path, unchanged."""
    import json

    import httpx
    import respx

    from mikiwame.bot import alert

    hook = "https://abc-1.kchat.infomaniak.com/hooks/AbC-123"
    alert.save_kchat_webhook(hook)
    calls, rc_file = tmp_path / "calls", tmp_path / "rc"
    gate = tmp_path / "gate.sh"
    gate.write_text(
        f"#!/bin/sh\ncat >> {calls}\nprintf '\\036' >> {calls}\nexit $(cat {rc_file})\n"
    )
    monkeypatch.setenv("MIKIWAME_NOTIFY_GATE", f"sh {gate}")

    def bodies() -> list[str]:
        return calls.read_text().split("\x1e")[:-1] if calls.exists() else []

    text = "📊 Resumen <x> & y\n    3× contract_before_payment"
    for rc in (0, 10):
        calls.unlink(missing_ok=True)
        rc_file.write_text(str(rc))
        with respx.mock:
            kc = respx.post(hook)
            assert alert.send_admin("t", 1, text, label="Mikiwame (pruebas)", icon="📬")
        assert not kc.called  # delivered by the host: never also to kChat
        assert bodies() == [
            "📬 <b>Mikiwame (pruebas)</b>: 📊 Resumen &lt;x&gt; &amp; y\n"
            "    3× contract_before_payment"
        ]

    # rc 1 (not delivered) and rc 2 (usage error): the kChat webhook, as before.
    for rc in (1, 2):
        calls.unlink(missing_ok=True)
        rc_file.write_text(str(rc))
        with respx.mock:
            kc = respx.post(hook).mock(return_value=httpx.Response(201, text="ok"))
            assert alert.send_admin("t", 1, text, label="Mikiwame (pruebas)", icon="📬")
        assert len(bodies()) == 1 and kc.call_count == 1
        body = json.loads(kc.calls[0].request.content)["text"]
        assert body.startswith("📬 **Mikiwame (pruebas)**: 📊 Resumen <x> & y\n")

    # No ssh at all, or a hung gate: same fallback. Both leave the rate-limit key untouched
    # until something is really delivered.
    monkeypatch.setenv("MIKIWAME_NOTIFY_GATE", str(tmp_path / "no-such-gate"))
    with respx.mock:
        kc = respx.post(hook).mock(return_value=httpx.Response(201, text="ok"))
        assert alert.send_admin("t", 1, "se cayó", key="gate-k")
    assert kc.call_count == 1
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    monkeypatch.setenv("MIKIWAME_NOTIFY_GATE", f"sh {slow}")
    monkeypatch.setattr(alert, "GATE_TIMEOUT_S", 0.3)
    with respx.mock:
        kc = respx.post(hook).mock(return_value=httpx.Response(500))
        assert not alert.send_admin("t", 1, "se cayó otra vez")
    assert kc.call_count == 2 and "se cayó otra vez" in alert.UNDELIVERED_LOG.read_text()

    # Switched off: straight to kChat, no gate.
    monkeypatch.setenv("MIKIWAME_NOTIFY_GATE", "")
    assert alert.notify_gate() is None
    monkeypatch.setenv("MIKIWAME_NOTIFY_GATE", "off")
    assert alert.notify_gate() is None


def test_host_gate_bodies_stay_under_its_byte_limit(tmp_path, monkeypatch):
    from mikiwame.bot import alert

    calls = tmp_path / "calls"
    gate = tmp_path / "gate.sh"
    gate.write_text(f"#!/bin/sh\ncat >> {calls}\nprintf '\\036' >> {calls}\nexit 0\n")
    monkeypatch.setenv("MIKIWAME_NOTIFY_GATE", f"sh {gate}")
    # 300 lines of 60 chars with a 4-byte emoji each: ~24 kB, over the 16384-byte limit.
    text = "\n".join(f"línea {n:03d} 🏠 " + "x" * 48 for n in range(300))
    assert alert.send_admin("t", 1, text, label="Mikiwame (pruebas)", icon="📬")
    bodies = calls.read_text().split("\x1e")[:-1]
    assert len(bodies) >= 2
    assert all(len(b.encode()) <= alert.GATE_MAX_BYTES for b in bodies)
    assert bodies[0].startswith(f"📬 <b>Mikiwame (pruebas) (1/{len(bodies)})</b>: línea 000")
    assert "línea 299" in bodies[-1]
