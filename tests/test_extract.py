from __future__ import annotations

import io
import json

import httpx
import pytest
import respx
from PIL import Image

from evaluation.fixtures import CASES_DIR, load, oracle_case
from mikiwame.extract import text as tx
from mikiwame.extract.build import Draft, DraftItem, apply_pages, build_case
from mikiwame.extract.redact import Redactor
from mikiwame.extract.vision import VisionExtractor, parse_pages, prepare_image
from mikiwame.indicators import case_indicators, digest, normalize_name
from mikiwame.llm.client import BadResponse, LLMClient
from mikiwame.llm.router import LLMRouter
from mikiwame.store.sql import Store
from tests.conftest import make_settings, responses_ok


def test_iban_spans_trim_and_validate():
    text = (
        "La cuenta: GB82 WEST 1234 5698 7654 32 a nombre de Airbnb. Otra: ES9121000418450200051333"
    )
    spans = tx.iban_spans(text)
    assert [s.value for s in spans] == ["GB82WEST12345698765432", "ES9121000418450200051333"]
    assert text[spans[0].start : spans[0].end] == "GB82 WEST 1234 5698 7654 32"
    assert tx.iban_info("GB82WEST12345698765432").valid
    bad = tx.iban_info("ES9121000418450200051333")
    assert not bad.valid and bad.country == "ES"
    es = tx.iban_info("ES9121000418450200051332")
    assert es.valid and es.bank_name and "CAIXA" in es.bank_name.upper()


def test_iban_wrapped_across_lines():
    spans = tx.iban_spans("Mi cuenta es IT60 X054 2811 1010 0000\n0123 456, a mi nombre")
    assert [s.value for s in spans] == ["IT60X0542811101000000123456"]


def test_iban_overmatch_into_uppercase_word():
    spans = tx.iban_spans("ES91 2100 0418 4502 0005 1332 A NOMBRE DE JUAN")
    assert spans[0].value == "ES9121000418450200051332"


def test_phones_do_not_eat_iban_digits():
    text = "Bizum al 600 000 104, cuenta ES91 2100 0418 4502 0005 1332, 1.500 € y +44 7700 900123"
    assert [s.value for s in tx.phone_spans(text)] == ["+34600000104", "+447700900123"]
    assert tx.phone_region("+447700900123") == "GB"


def test_price_type_and_listing_ref():
    assert tx.price_eur("Piso reformado. 1.050 €/mes. Sin honorarios") == 1050
    assert tx.price_eur("habitación 320 euros al mes") == 320
    assert tx.price_eur("fianza de 900 €") is None
    assert tx.looks_like_room("Habitación doble en piso compartido")
    assert not tx.looks_like_room("Piso de 3 habitaciones")
    assert tx.listing_ref("https://www.idealista.com/inmueble/98765432/") == "idealista:98765432"
    assert (
        tx.listing_ref("https://www.fotocasa.es/es/alquiler/vivienda/alcala-de-henares/181234567/d")
        == "fotocasa:181234567"
    )
    assert tx.url_domain("https://idealista-pagoseguro.com/reserva/1") == "idealista-pagoseguro.com"


def test_redactor_is_consistent_and_keeps_country():
    r = Redactor(advertiser_names=["María Ferrer", "Inmobiliaria Lumbre Complutense"])
    a = r.redact("Soy María Ferrer, llama al +44 7700 900123 o paga a GB82 WEST 1234 5698 7654 32")
    b = r.redact(
        "Otra vez: +44 7700 900123. Escribe a maria@example.com. Inmobiliaria Lumbre Complutense"
    )
    assert "[TEL_1 país=GB]" in a and "[TEL_1 país=GB]" in b
    assert "[IBAN_1 país=GB]" in a and "7700" not in a
    assert "[NOMBRE_ANUNCIANTE]" in a and "Ferrer" not in a
    assert "[EMAIL_1 dominio=example.com]" in b
    # Business names are not personal data and help the rubric.
    assert "Inmobiliaria Lumbre Complutense" in b


def test_indicators_are_stable_and_keyed():
    fx = load(CASES_DIR / "s01_airbnb_llaves.toml")
    case = oracle_case(fx)
    k1 = case_indicators(case, b"k" * 32)
    assert case_indicators(case, b"k" * 32) == k1
    kinds = {k for k, _ in k1}
    assert {"iban", "phone", "name", "listing"} <= kinds
    assert case_indicators(case, b"x" * 32) != k1
    assert normalize_name("  ~María  Ferrer ") == "maria ferrer"
    assert digest(b"k", "phone", "+34600000104") == digest(b"k", "phone", "+34600000104")


def test_oracle_case_from_fixture():
    case = oracle_case(load(CASES_DIR / "s07_llaves_mensajeria.toml"))
    assert case.ibans == ["DE89370400440532013000"]
    assert case.phones == ["+4915123456789"]
    assert case.price_eur == 800
    assert case.listing_url and case.chat[0].sender == "user"


def test_prepare_image_downscales_and_strips():
    im = Image.new("RGB", (2000, 5000), "white")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    part = prepare_image(buf.getvalue())
    out = Image.open(io.BytesIO(part.data))
    assert out.format == "JPEG" and out.width <= 1080 and out.height <= 2400


def test_parse_pages_is_tolerant():
    pages = parse_pages(
        {
            "images": [
                {
                    "index": 1,
                    "kind": "chat",
                    "contact_title": "+44 7700 900123",
                    "contact_subtitle": "~María",
                    "messages": [
                        {"from": "them", "text": "hola"},
                        {"from": "bot", "text": "x"},
                        {"text": ""},
                    ],
                },
                {"index": 2, "kind": "weird", "text": "algo"},
                {"index": 3, "kind": "id_document"},
                {"index": 4, "kind": "id_document", "sent_by": "them"},
                {"index": 5, "kind": "id_document", "sent_by": "me"},
                {"index": 6, "kind": "id_document", "sent_by": "anunciante"},
                "basura",
            ]
        }
    )
    assert [p.kind for p in pages] == ["chat", "other"] + ["id_document"] * 4
    assert [m.from_ for m in pages[0].messages] == ["them", "unknown"]
    from mikiwame.models import Case

    case = apply_pages(Case(), pages)
    assert case.phones == ["+447700900123"] and case.advertiser_names == ["~María"]
    # Only the advertiser's bubble counts; loose photos (and unknown values) stay unattributed;
    # the user's own ID says nothing about the advertiser.
    assert case.id_documents == 1 and case.id_documents_unattributed == 2
    assert case.pasted_text == "algo"


VISION_JSON = {
    "images": [
        {
            "index": 1,
            "kind": "chat",
            "contact_title": "+34 600 000 104",
            "contact_subtitle": "~Laura",
            "messages": [
                {"from": "me", "text": "¿Cuándo podría verla?"},
                {"from": "them", "text": "Bizum de 250 € al 600 000 104"},
            ],
            "text": "",
        }
    ]
}


@respx.mock
async def test_build_case_with_vision(tmp_path):
    settings = make_settings()
    store = Store(f"sqlite:///{tmp_path / 't.db'}")
    env = {"OPENCODE_BASE_URL": "https://llm.test/v1", "OPENCODE_API_KEY": "k"}
    router = LLMRouter(settings, store, LLMClient(env=env, backoff_s=0))
    route = respx.post("https://llm.test/v1/responses").mock(
        return_value=httpx.Response(
            200, json=responses_ok(json.dumps(VISION_JSON), input_tokens=1500, output_tokens=200)
        )
    )
    buf = io.BytesIO()
    Image.new("RGB", (720, 1200), "white").save(buf, "JPEG")
    draft = Draft(user_id=1)
    draft.items += [
        DraftItem("text", text="https://www.idealista.com/inmueble/96543210/"),
        DraftItem("image", image=buf.getvalue()),
    ]
    case = await build_case(
        draft, vision=VisionExtractor(router), max_images=6, max_text_chars=8000
    )
    assert route.called
    assert case.listing_url == "https://www.idealista.com/inmueble/96543210/"
    assert case.phones == ["+34600000104"]
    assert case.advertiser_names == ["Laura"]
    assert [m.sender for m in case.chat] == ["user", "advertiser"]
    assert store.case_usage(draft.case_id)[0]["role"] == "vision"


async def test_build_case_caps_images():
    calls = []

    async def fake_vision(images, *, case_id):
        calls.append(len(images))
        return []

    draft = Draft(user_id=1, items=[DraftItem("image", image=b"x") for _ in range(9)])
    case = await build_case(draft, vision=fake_vision, max_images=6, max_text_chars=10)
    assert calls == [6] and case.image_count == 6


def test_company_named_in_its_own_url_is_not_redacted():
    # Second real case (2026-10-01): "Spacest"-like platform, automatic reply from idealista.
    text = (
        "Explora los inmuebles de Casalto aquí: https://casalto.example/es?utm_source=idealistaES\n"
        "El equipo de Casalto. Te atiende María Ferrer."
    )
    r = Redactor(advertiser_names=["Casalto", "María Ferrer"], context=text)
    out = r.redact(text)
    assert "https://casalto.example/es?utm_source=idealistaES" in out
    assert "El equipo de Casalto" in out
    assert "María Ferrer" not in out and "[NOMBRE_ANUNCIANTE]" in out


def test_person_name_inside_url_is_left_alone():
    text = "Mira https://example.com/maria-ferrer y habla con Maria-Ferrer"
    out = Redactor(advertiser_names=["Maria-Ferrer"], context="").redact(text)
    assert "https://example.com/maria-ferrer" in out and out.endswith("[NOMBRE_ANUNCIANTE]")


def test_vision_channel_is_parsed_and_applied():
    from mikiwame.models import Case

    pages = parse_pages(
        {
            "images": [
                {"kind": "chat", "channel": "Idealista", "messages": []},
                {"kind": "chat", "channel": "telepatía"},
            ]
        }
    )
    assert [p.channel for p in pages] == ["idealista", "unknown"]
    assert apply_pages(Case(), pages).channel == "idealista"


def test_advertiser_phones_exclude_the_users_own():
    from mikiwame.extract.build import enrich
    from mikiwame.models import Case, Message

    case = enrich(
        Case(
            chat=[
                Message(sender="user", text="Mi número es +44 7700 900999"),
                Message(sender="advertiser", text="Llámame al +39 312 345 6789"),
            ]
        )
    )
    assert "+447700900999" in case.phones
    assert case.advertiser_phones == ["+393123456789"]


def test_parse_pages_survives_null_fields_and_rejects_empty_envelope():
    pages = parse_pages(
        {
            "images": [
                {
                    "kind": "chat",
                    "contact_title": None,
                    "text": None,
                    "messages": [{"from": "them", "text": "hola"}],
                },
                {"kind": "chat", "messages": "no es una lista"},
            ]
        }
    )
    assert len(pages) == 2 and pages[0].messages[0].text == "hola" and pages[1].messages == []
    with pytest.raises(BadResponse):
        parse_pages({"images": []}, expected=2)
    with pytest.raises(BadResponse):
        parse_pages({"imagenes": []})


def test_url_domain_never_raises_on_hostile_urls():
    assert tx.url_domain("http://[::1") is None
    assert tx.url_domain("https://www.idealista.com»") == "idealista.com"
    assert tx.urls("mira https://casalto.example/es…") == ["https://casalto.example/es"]
    assert not tx.iban_info("ES9١2100041845020005133").valid  # Arabic-Indic digit


def test_redacted_case_keeps_only_url_domains():
    from mikiwame.extract.build import enrich
    from mikiwame.extract.redact import redacted_case
    from mikiwame.models import Case, Message

    c = enrich(
        Case(
            chat=[
                Message(sender="advertiser", text="Escríbeme a https://wa.me/34600000104?text=hola")
            ]
        )
    )
    r = redacted_case(c)
    assert r.urls == ["wa.me"] and "600000104" not in r.model_dump_json()


def test_listing_ids_in_urls_are_not_phones_but_whatsapp_links_are():
    text = "Anuncio https://www.idealista.com/inmueble/600000104/ y mi WhatsApp https://wa.me/447700900123"
    assert [sp.value for sp in tx.phone_spans(text)] == ["+447700900123"]


def test_name_redaction_whole_words_surnames_and_hostile_names():
    r = Redactor(advertiser_names=["Ana Robles"], context="")
    out = r.redact("Abre la ventana, Ana. Robles te llama. ¿Es de Ana Robles?")
    # Full name and surname go; a lone 3-letter first name is too ambiguous to chase.
    assert "ventana" in out and "Robles" not in out and "Ana Robles" not in out
    hostile = Redactor(advertiser_names=["Transferencia"], context="")
    assert "transferencia" in hostile.redact("Hazme una transferencia hoy")
    assert Redactor(["Ana"], context="https://banana.example").person_names() == ["Ana"]


def test_iban_lowercase_and_dots():
    spans = tx.iban_spans("cuenta: es91.2100.0418.4502.0005.1332 gracias")
    assert [sp.value for sp in spans] == ["ES9121000418450200051332"]


def test_room_phrasings_and_status_lines():
    from mikiwame.extract.build import apply_pages
    from mikiwame.models import Case

    assert tx.looks_like_room("Se alquila una habitación en piso compartido")
    assert tx.looks_like_room("Alquilo habitación doble, luminosa")
    assert not tx.looks_like_room("Piso de 3 habitaciones con terraza")
    pages = parse_pages(
        {
            "images": [
                {
                    "kind": "chat",
                    "contact_title": "Ana Robles",
                    "contact_subtitle": "últ. vez hoy a las 12:30",
                }
            ]
        }
    )
    assert apply_pages(Case(), pages).advertiser_names == ["Ana Robles"]


def test_english_listings_give_price_rooms_and_room_ads():
    from mikiwame.extract import text as t

    assert t.price_eur("Room in shared flat, 280 €/month, bills included") == 280
    assert t.price_eur("€1,250 a month") == 1250
    assert t.price_eur("650 euros per month") == 650
    assert t.price_eur("studio, €700 pcm") == 700
    assert t.rooms_in("Bright 2-bedroom flat") == 2
    assert t.rooms_in("3 bedrooms, 2 bathrooms") == 3
    assert t.rooms_in("Cosy studio") == 1
    assert t.looks_like_room("Room in shared flat near the university")
    assert t.looks_like_room("Double room available in Lavapiés")
    assert not t.looks_like_room("2-bedroom flat with a big living room")
