from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw, ImageFilter

from mikiwame.extract.address import find_addresses, street_of
from mikiwame.extract.build import Draft, DraftItem, enrich, photo_hashes
from mikiwame.extract.vision import VisionPage
from mikiwame.indicators import case_indicators
from mikiwame.llm.client import LLMClient, LLMError
from mikiwame.llm.router import LLMRouter
from mikiwame.models import Case, Level, Message, Verdict
from mikiwame.photos import distance, phash, same_photo
from mikiwame.pipeline import AnalysisFailed, Analyzer
from mikiwame.store.sql import Store
from mikiwame.voters.recidivism import RecidivismVoter
from tests.conftest import make_settings

KEY = b"k" * 32


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(f"sqlite:///{tmp_path / 'r.db'}")


def _room(seed: int) -> bytes:
    r = random.Random(seed)
    im = Image.new("RGB", (800, 600), tuple(r.randint(150, 240) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(8):
        x, y = r.randint(0, 700), r.randint(0, 500)
        d.rectangle([x, y, x + r.randint(60, 300), y + r.randint(40, 200)],
                    fill=tuple(r.randint(0, 255) for _ in range(3)))  # fmt: skip
    buf = io.BytesIO()
    im.filter(ImageFilter.GaussianBlur(2)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _resaved(data: bytes) -> bytes:
    im = Image.open(io.BytesIO(data))
    buf = io.BytesIO()
    im.resize((im.width // 2, im.height // 2)).save(buf, "JPEG", quality=60)
    return buf.getvalue()


def test_addresses_are_found_and_normalised():
    keys = [a.building_key for a in find_addresses("Calle de los Colegios 14, 2º B, Alcalá")]
    assert keys == ["calle colegios 14|alcala de henares"]
    assert [
        a.building_key for a in find_addresses("c/ Colegios nº 14 en Alcalá de Henares")
    ] == keys
    assert street_of(keys[0]) == "calle colegios|alcala de henares"
    assert find_addresses("Son 900 € al mes y quedamos a las 19:00") == []


def test_counts_and_towns_are_not_part_of_an_address():
    # Telegram test 2026-10-08: "calle Libreros en Alcalá, 2 habitaciones" became the street
    # "libreros en alcala" number 2, which OpenStreetMap could not find.
    assert find_addresses("el piso de la calle Libreros en Alcalá, 2 habitaciones a 550 €") == []
    assert find_addresses("Calle Mayor, 2 habitaciones") == []
    assert find_addresses("calle Madrid.\n85 m², tercero con ascensor") == []
    assert find_addresses("plaza de garaje 680 € al mes") == []
    keys = [a.building_key for a in find_addresses("calle Ramón y Cajal 12, Alcalá")]
    assert keys == ["calle ramon cajal 12|alcala de henares"]


def test_phash_matches_resaved_photo_not_another():
    a, b = _room(1), _room(2)
    assert same_photo(phash(a), phash(_resaved(a)))
    assert distance(phash(a), phash(b)) > 10
    assert phash(b"not an image") is None


def test_only_property_photos_are_hashed_in_order():
    imgs = [_room(1), _room(2), _room(3)]
    pages = [VisionPage(kind="chat"), VisionPage(kind="photo"), VisionPage(kind="photo")]
    assert photo_hashes(imgs, pages) == [phash(imgs[1]), phash(imgs[2])]


def _save(store, user, *, phone="+34600000001", address=None, level=Level.RED, photos=()):
    text = f"Llámame al {phone}." + (f" El piso está en {address}, Alcalá." if address else "")
    case = enrich(Case(chat=[Message(sender="advertiser", text=text)]))
    store.save_case(
        case=case,
        tg_user_id=user,
        retention_days=30,
        verdict=Verdict(level=level, confidence=0.8, score=0.7),
        indicators=case_indicators(case, KEY),
        photo_hashes=photos,
    )
    return case


def _vote(store, user, **kw):
    text = f"Llámame al {kw.get('phone', '+34600000001')}." + (
        f" El piso está en {kw['address']}, Alcalá." if kw.get("address") else ""
    )
    case = enrich(Case(chat=[Message(sender="advertiser", text=text)]))
    case.photo_hashes = list(kw.get("photos", []))
    roots = kw.get("roots", frozenset())
    vote = RecidivismVoter(store, roots).vote_sync(case, case_indicators(case, KEY), user)
    return {s.code for s in vote.signals}, vote


def test_seen_before_red_needs_two_other_users(store):
    _save(store, 1)
    assert _vote(store, 9)[0] == set()  # one user alone could poison the registry
    _save(store, 2)
    codes, vote = _vote(store, 9)
    assert codes == {"seen_before_red"} and "teléfono" in vote.signals[0].message
    # The user's own earlier cases never count.
    assert _vote(store, 1)[0] == set()


def test_green_history_is_not_seen_before_red(store):
    _save(store, 1, level=Level.GREEN)
    _save(store, 2, level=Level.GREEN)
    assert _vote(store, 9)[0] == set()


def test_same_address_with_another_advertiser_is_reused(store):
    _save(store, 1, phone="+34600000001", address="calle Libreros 20", level=Level.GREEN)
    codes, vote = _vote(store, 9, phone="+34600000002", address="calle Libreros 20")
    assert codes == {"reused_elsewhere"} and "la misma dirección" in vote.signals[0].message
    # Same address, same advertiser: just the same landlord again.
    assert _vote(store, 9, phone="+34600000001", address="calle Libreros 20")[0] == set()


def test_same_photo_with_another_advertiser_is_reused(store):
    photo = _room(7)
    _save(store, 1, phone="+34600000001", level=Level.GREEN, photos=[phash(photo)])
    codes, vote = _vote(store, 9, phone="+34600000002", photos=[phash(_resaved(photo))])
    assert codes == {"reused_elsewhere"} and "las mismas fotos" in vote.signals[0].message


async def test_listing_link_is_registered_even_if_vision_fails(tmp_path):
    settings = make_settings()
    store = Store(f"sqlite:///{tmp_path / 'p.db'}")
    router = LLMRouter(settings, store, LLMClient(env={}))
    analyzer = Analyzer(settings, store, router)

    async def broken_vision(images, *, case_id):
        raise LLMError("down")

    analyzer.vision = broken_vision
    draft = Draft(user_id=5)
    draft.items += [
        DraftItem("text", text="https://www.idealista.com/inmueble/98765432/"),
        DraftItem("image", image=_room(1)),
    ]
    with pytest.raises(AnalysisFailed):
        await analyzer.analyze_draft(draft)
    ref_case = enrich(Case(pasted_text="https://www.idealista.com/inmueble/98765432/"))
    key = settings.hmac_key.get_secret_value().encode()
    (kind, digest), *_ = [p for p in case_indicators(ref_case, key) if p[0] == "listing"]
    assert store.indicator_case_count(kind, digest) == 1


def test_features_are_anonymous_and_capture_the_modus_operandi(store):
    from mikiwame.patterns import features, summarize
    from mikiwame.voters.base import signal

    case = enrich(
        Case(
            channel="whatsapp",
            chat=[
                Message(sender="advertiser", text="Hola, estoy en Londres."),
                Message(sender="advertiser", text="Reserva de 500 € a GB82 WEST 1234 5698 7654 32"),
                Message(sender="advertiser", text="Llámame al +44 7700 900123"),
            ],
        )
    )
    v = Verdict(level=Level.RED, confidence=0.8, score=0.8,
                signals=[signal("pay_before_visit"), signal("owner_away")])  # fmt: skip
    f = features(case, v, listing_known=False, listing_removed=False)
    assert f["first_money_msg"] == 2 and f["amounts_eur"] == "[500]"
    assert f["iban_countries"] == "GB" and f["phone_countries"] == "GB"
    dumped = str(f)
    for secret in ("GB82", "7700", "Londres"):
        assert secret not in dumped
    store.save_features(f)
    text = summarize([f])
    assert "owner_away,pay_before_visit" in text and "mensaje nº 2" in text


def test_research_opt_in_copies_without_user(store):
    c = _save(store, 7)
    assert store.keep_for_research(c.case_id)
    from sqlalchemy import select

    from mikiwame.store import schema

    with store.engine.connect() as conn:
        rows = conn.execute(select(schema.research_cases)).all()
    assert len(rows) == 1 and "tg_user_id" not in rows[0]._mapping
    store.delete_user(7)
    with store.engine.connect() as conn:
        assert len(conn.execute(select(schema.research_cases)).all()) == 1


def _join(store, user: int, inviter: int) -> None:
    code = store.create_invite(inviter, ttl_days=7, unlimited=True)
    assert store.redeem_invite(code, user, invites_for_new=2)


def test_one_branch_of_the_invite_tree_counts_once(store):
    root = frozenset({100})
    _join(store, 1, 100)  # Ana, invited by Patricio
    _join(store, 2, 1)  # Ana's friend
    _save(store, 1)
    _save(store, 2)
    assert _vote(store, 9, roots=root)[0] == set()  # Ana and her friend are one branch
    _join(store, 3, 100)  # someone else Patricio invited
    _save(store, 3)
    assert _vote(store, 9, roots=root)[0] == {"seen_before_red"}


def test_revoked_members_and_disputed_identifiers_stop_counting(store):
    from mikiwame.indicators import digest, indicator_value

    root = frozenset({100})
    for u in (1, 2):
        _join(store, u, 100)
        _save(store, u)
    assert _vote(store, 9, roots=root)[0] == {"seen_before_red"}
    store.set_member(2, revoked=True)
    assert _vote(store, 9, roots=root)[0] == set()
    store.set_member(2, revoked=False)
    # A landlord writes in: the phone, typed by hand with spaces, matches the registry digest.
    value = indicator_value("phone", "600 00 00 01")
    store.dispute("phone", digest(KEY, "phone", value), "correo 02-10")
    assert _vote(store, 9, roots=root)[0] == set()


def test_invites_are_one_use_limited_and_expire(store):
    import datetime as dt

    from sqlalchemy import update

    from mikiwame.store import schema

    _join(store, 1, 100)
    a = store.create_invite(1, ttl_days=7, unlimited=False)
    b = store.create_invite(1, ttl_days=7, unlimited=False)
    assert a and b and store.create_invite(1, ttl_days=7, unlimited=False) is None
    assert store.redeem_invite(a, 2, invites_for_new=2)
    assert not store.redeem_invite(a, 3, invites_for_new=2)  # used
    assert not store.redeem_invite(b, 2, invites_for_new=2)  # already a member
    with store.engine.begin() as conn:
        conn.execute(
            update(schema.invites)
            .where(schema.invites.c.code == b)
            .values(expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
        )
    assert not store.redeem_invite(b, 3, invites_for_new=2)  # expired
    c = store.create_invite(2, ttl_days=7, unlimited=False)
    store.set_member(2, revoked=True)
    assert not store.redeem_invite(c, 4, invites_for_new=2)  # inviter revoked
    assert store.is_member(1) and not store.is_member(2) and not store.is_member(4)


def test_beta_cap_counts_members_and_pending_invites(store):
    _join(store, 1, 100)  # 1 member (its invite is used: no longer pending)
    a = store.create_invite(100, ttl_days=7, unlimited=True, max_members=3)  # 2 seats
    assert a and store.create_invite(100, ttl_days=7, unlimited=True, max_members=3)  # 3
    assert store.create_invite(100, ttl_days=7, unlimited=True, max_members=3) is None  # full
    assert store.redeem_invite(a, 2, invites_for_new=2)  # a used invite becomes a member: 3
    assert store.create_invite(2, ttl_days=7, unlimited=False, max_members=3) is None
    assert store.member(2)["invites_left"] == 2  # a refused invite costs nothing
    store.set_member(1, revoked=True)  # a revoked member frees a seat
    assert store.create_invite(2, ttl_days=7, unlimited=False, max_members=3)


def test_idle_members_free_their_seat_but_keep_access(store):
    import datetime as dt

    from sqlalchemy import update

    from mikiwame.store import schema

    _join(store, 1, 100)
    assert store.beta_seats_taken() == 1
    long_ago = dt.datetime.now(dt.UTC) - dt.timedelta(days=45)
    with store.engine.begin() as conn:
        conn.execute(update(schema.members).values(joined_at=long_ago))
    assert store.beta_seats_taken(idle_days=30) == 0 and store.is_member(1)
    store.record_usage(1)  # back in use: takes a seat again
    assert store.beta_seats_taken(idle_days=30) == 1


def test_campaign_link_has_n_uses_and_holds_seats(store):
    code = store.create_campaign(100, "alquilaenmadrid", uses=2, ttl_days=7, max_members=3)
    assert code and store.beta_seats_taken() == 2  # unused uses hold seats
    assert store.create_campaign(100, "alquilaenmadrid", uses=1, ttl_days=7) is None  # name
    assert store.create_campaign(100, "otro", uses=2, ttl_days=7, max_members=3) is None  # full
    assert store.redeem_invite(code, 1, invites_for_new=2)
    assert not store.redeem_invite(code, 1, invites_for_new=2)  # already in
    assert store.redeem_invite(code, 2, invites_for_new=2)
    assert not store.redeem_invite(code, 3, invites_for_new=2)  # no uses left
    assert store.beta_seats_taken() == 2  # held seats became members
    assert store.member(1)["invites_left"] == 0  # none until they use it
    store.record_usage(1, campaign_invites=2)
    assert store.member(1)["invites_left"] == 2
    store.record_usage(1, campaign_invites=2)  # only the first analysis grants them
    store.set_member(1, invites_left=0)
    store.record_usage(1, campaign_invites=2)
    assert store.member(1)["invites_left"] == 0
    [c] = store.campaigns_list()
    assert (c["uses"], c["max_uses"], c["analyses"]) == (2, 2, 3)


def test_closed_or_expired_campaign_lets_nobody_in_and_frees_seats(store):
    import datetime as dt

    from sqlalchemy import update

    from mikiwame.store import schema

    a = store.create_campaign(100, "a", uses=5, ttl_days=7)
    b = store.create_campaign(100, "b", uses=5, ttl_days=7)
    assert store.close_campaign("a") and not store.close_campaign("a")
    assert not store.redeem_invite(a, 1, invites_for_new=2)
    with store.engine.begin() as conn:
        conn.execute(
            update(schema.campaigns)
            .where(schema.campaigns.c.code == b)
            .values(expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
        )
    assert not store.redeem_invite(b, 1, invites_for_new=2)
    assert store.beta_seats_taken() == 0 and not store.is_member(1)


def test_one_campaign_is_one_branch_with_its_invitees(store):
    root = frozenset({100})
    code = store.create_campaign(100, "grupo", uses=10, ttl_days=7)
    for u in (1, 2):  # two strangers from the same group
        assert store.redeem_invite(code, u, invites_for_new=2)
        _save(store, u)
    store.record_usage(1, campaign_invites=2)
    _join(store, 3, 1)  # someone the first stranger invited
    _save(store, 3)
    assert _vote(store, 9, roots=root)[0] == set()
    other = store.create_campaign(100, "otro", uses=10, ttl_days=7)
    assert store.redeem_invite(other, 4, invites_for_new=2)
    _save(store, 4)
    assert _vote(store, 9, roots=root)[0] == {"seen_before_red"}


def test_public_members_are_one_branch(store):
    root = frozenset({100})
    for u in (1, 2):  # two strangers through open access
        assert store.join_public(u)
        _save(store, u)
    assert not store.join_public(1)
    assert _vote(store, 9, roots=root)[0] == set()
    _join(store, 3, 100)
    _save(store, 3)
    assert _vote(store, 9, roots=root)[0] == {"seen_before_red"}
